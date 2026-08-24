"""EDM routes — import, detail, recovery, and the blocking name check (US1, #17).

Server-rendered FastAPI + Jinja2 + HTMX (Article 8). Every state-changing route
validates a CSRF token (Article 13). Risk Modeler *submits* stay worker-side —
import only *enqueues* the upload work and returns; the one RM call on a request
path is the name-collision **read** (permitted by Article 11, cached per
``name_check``). No row scoping (Article 6): every analyst may load and act on
every EDM.

Route order matters: the literal ``/edms/import`` and ``/edms/name-check`` paths are
declared before ``/edms/{edm_id}`` so the parameter route never shadows them.
"""

from __future__ import annotations

import json
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.nav import get_nav_context
from app.routers._entity_notes import save_notes
from app.services import analysis_service, edm_service, geohaz_service
from app.services.errors import (
    ConcurrencyConflict,
    EdmCatalogUnavailable,
    GeohazLaunchConflict,
    InvalidGeohazLaunch,
    InvalidMemberName,
    InvalidSourceFile,
    NameCollisionError,
)

router = APIRouter()

_NAV_KEY = "irp.edm_library"  # list / import / detail all activate this node (T060)


def _templates(request: Request):
    return request.app.state.templates


def _render(request: Request, template: str, extra: dict, status_code: int = 200,
            nav_key: str = _NAV_KEY):
    current_user = request.state.user
    nav = get_nav_context(current_user, nav_key)
    return _templates(request).TemplateResponse(
        request, template,
        {"current_user": current_user, "nav": nav, **extra},
        status_code=status_code,
    )


def _partial(request: Request, template: str, ctx: dict, status_code: int = 200):
    return _templates(request).TemplateResponse(
        request, template, {"current_user": request.state.user, **ctx},
        status_code=status_code,
    )


# ── Library list (literal paths — declared before /edms/{edm_id}) ────────────────

def _library_context(request: Request) -> dict:
    """Shared context for the full library page and its polled table fragment."""
    q = (request.query_params.get("q") or "").strip() or None
    status = (request.query_params.get("status") or "").strip() or None
    rows = edm_service.list_edms(name=q, status=status)
    return {
        "rows": rows,
        "filter_values": {"q": request.query_params.get("q", ""),
                          "status": request.query_params.get("status", "")},
        "statuses": edm_service.STATUSES,
        # Any row a worker is still moving → the table keeps polling (see
        # partials/library_table.html); all-terminal → no trigger, polling stops.
        "live": any(r.status in edm_service.TRANSIENT_STATUSES for r in rows),
        # partials/library_table.html inputs — declared here so the page and the
        # polled fragment cannot drift apart.
        "list_route": "/edms", "detail_prefix": "/edms", "entity_label": "EDM",
    }


@router.get("/edms", response_class=HTMLResponse)
def library(request: Request):
    """Global EDM library — every EDM across all submissions, any analyst (no row
    scoping, FR-037/SC-009), narrowable by a name search + status filter (US7). GET,
    no CSRF."""
    return _render(request, "pages/edm_library.html", _library_context(request))


@router.get("/edms/table", response_class=HTMLResponse)
def library_table(request: Request):
    """Read-only table render for HTMX polling — the same filtered rows as ``/edms``
    without the shell, so in-flight imports advance on the list without a reload.
    The trigger is emitted only while a row is non-terminal, so the poll stops by
    itself. No writes, no Risk Modeler call (Article 11)."""
    return _partial(request, "partials/library_table.html",
                    _library_context(request))


# ── Sync existing Risk Modeler EDMs (literal path, before /edms/{edm_id}) ────────

def _sync_context(request: Request) -> dict:
    """The adoptable list, or the unavailable state when Risk Modeler does not
    answer (``page`` is None). The banner counts are read either way, so a Risk
    Modeler outage on the redirect cannot hide a sync that already happened."""
    q = (request.query_params.get("q") or "").strip() or None
    return {
        "page": edm_service.list_adoptable_edms(
            page=_int_param(request, "page", default=1), name=q),
        "page_size": edm_service.ADOPTABLE_PAGE_SIZE,
        "filter_values": {"q": request.query_params.get("q", "")},
        # Set by the POST's redirect, so the banner survives Post/Redirect/Get.
        "synced": _int_param(request, "synced"),
        "skipped": _int_param(request, "skipped"),
        "sync_error": request.query_params.get("sync_error") == "unavailable",
    }


def _int_param(request: Request, key: str, default: int = 0) -> int:
    """A mangled value falls back to ``default`` rather than 422 — the pager and the
    banner counts are navigation, not form input."""
    try:
        return max(0, int(request.query_params.get(key, default)))
    except ValueError:
        return default


@router.get("/edms/sync", response_class=HTMLResponse)
def sync_list(request: Request):
    """EDMs that exist in Risk Modeler with no ``irp_edm`` row. No polling: each
    render is another Risk Modeler call, so a self-poll would turn one page view
    into a call stream."""
    return _render(request, "pages/edm_sync.html", _sync_context(request),
                   nav_key="irp.sync_edms")


@router.post("/edms/sync")
def sync_adopt(request: Request, csrf_token: Annotated[str, Form()],
               irp_ids: Annotated[list[int] | None, Form()] = None):
    """Take in the ticked EDMs, then Post/Redirect/Get back to this page, whose
    re-read is what drops the newly synced rows from the list."""
    if not validate_csrf_token(csrf_token):
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse("/edms/sync", status_code=303)
    try:
        result = edm_service.adopt_edms(irp_ids=irp_ids or [],
                                        actor_id=request.state.user.id)
    except EdmCatalogUnavailable:
        params = {"sync_error": "unavailable"}
        q = (request.query_params.get("q") or "").strip()
        if q:
            params["q"] = q
        return RedirectResponse(f"/edms/sync?{urlencode(params)}", status_code=303)
    # The form action carries the search term, so the analyst lands back inside the
    # list they were working through. `page` is dropped: the rows just taken in are
    # gone and the page they were on has re-flowed.
    params = {"synced": len(result.adopted), "skipped": len(result.skipped)}
    q = (request.query_params.get("q") or "").strip()
    if q:
        params["q"] = q
    return RedirectResponse(f"/edms/sync?{urlencode(params)}", status_code=303)


# ── Import form + name check (literal paths first) ───────────────────────────────

@router.get("/edms/import", response_class=HTMLResponse)
def import_form(request: Request):
    return _render(request, "pages/edm_import.html",
                   {"form": {"name": ""}, "errors": [], "check": None})


@router.get("/edms/name-check", response_class=HTMLResponse)
def name_check(request: Request):
    name = request.query_params.get("name", "")
    return _partial(request, "partials/name_collision.html",
                    {"check": edm_service.check_name_collision(name),
                     "name": name, "kind": "EDM"})


@router.post("/edms/import")
def create_import(
    request: Request,
    name: str = Form(...),
    source_paths: list[str] = Form(default=[]),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/edms/import", status_code=303)

    source = source_paths[0] if source_paths else ""
    form = {"name": name}
    if not name.strip() or not source:
        return _render(request, "pages/edm_import.html", {
            "form": form,
            "errors": ["A name and a source file selection are required."],
            "check": None}, status_code=422)
    try:
        result = edm_service.import_edm(
            name=name.strip(), source_file_path=source, actor_id=request.state.user.id)
    except (InvalidSourceFile, InvalidMemberName, NameCollisionError) as exc:
        return _render(request, "pages/edm_import.html",
                       {"form": form, "errors": [str(exc)], "check": None},
                       status_code=422)
    # Fail-open marker (issue #17): the collision check couldn't reach Risk
    # Modeler — the detail page shows a warning banner once, via the query flag.
    suffix = "?nc=unchecked" if result.collision_unchecked else ""
    return RedirectResponse(f"/edms/{result.entity_id}{suffix}", status_code=303)


# ── Detail + recovery ────────────────────────────────────────────────────────────

def _detail(request: Request, edm_id: str, status_code: int = 200):
    # The full spec-004 read model: light header + per-portfolio snapshot table
    # (US2/US3/US4 extend the same payload). Reads STORED detail only — no Risk
    # Modeler call on the request path (Article 11).
    edm = edm_service.get_edm_detail(edm_id)
    if edm is None:
        return _render(request, "base/error.html",
                       {"status_code": 404, "title": "Not found",
                        "detail": "That EDM does not exist."}, status_code=404)
    # Rendered by the page shell (not the polled #edm-detail body, which would
    # wipe it on the first 3s swap): the import was saved fail-open because the
    # name-collision check couldn't reach Risk Modeler.
    return _render(request, "pages/edm_detail.html",
                   {"edm": edm,
                    "nc_unchecked": request.query_params.get("nc") == "unchecked",
                    "geohaz_queued": request.query_params.get("geohaz") == "queued",
                    "geohaz_error": request.query_params.get("geohaz_error")},
                   status_code=status_code)


def _contextual_not_found(request: Request):
    return _render(
        request, "base/error.html",
        {"status_code": 404, "title": "Not found",
         "detail": "That EDM is not related to the named submission."},
        status_code=404, nav_key="submissions.detail")


def _contextual_template_context(
    context: edm_service.ContextualEdmDetail,
) -> dict:
    base_url = (f"/submissions/{context.submission.id}/edms/"
                f"{context.edm.id}")
    return {
        "edm": context.edm,
        "source_submission": context.submission,
        "edm_choices": context.edm_choices,
        "submission_rdms": context.rdms,
        "detail_base_url": base_url,
        "detail_body_url": f"{base_url}/body",
        "detail_sync_url": f"{base_url}/sync",
        "detail_notes_url": f"{base_url}/notes",
    }


def _contextual_body_partial(
    request: Request, submission_id: str, edm_id: str, *, poll: bool = False,
):
    context = edm_service.get_contextual_edm_detail(
        submission_id=submission_id, edm_id=edm_id)
    if context is None:
        return HTMLResponse(
            '<div class="page-pad" id="edm-detail">'
            '<div class="state-box state-box--warn">'
            'This EDM is no longer related to the submission.</div></div>')
    if poll and context.edm.sync_running and context.edm.detail_state == "populated":
        return Response(status_code=204)
    return _partial(
        request, "partials/edm_detail_body.html",
        _contextual_template_context(context))


@router.get(
    "/submissions/{submission_id}/edms/{edm_id}",
    response_class=HTMLResponse,
)
def contextual_detail(request: Request, submission_id: str, edm_id: str):
    context = edm_service.get_contextual_edm_detail(
        submission_id=submission_id, edm_id=edm_id)
    if context is None:
        return _contextual_not_found(request)
    return _render(
        request, "pages/edm_detail.html",
        {
            **_contextual_template_context(context),
            "nc_unchecked": False,
            "geohaz_queued": request.query_params.get("geohaz") == "queued",
            "geohaz_error": request.query_params.get("geohaz_error"),
        },
        nav_key="submissions.detail")


@router.get(
    "/submissions/{submission_id}/edms/{edm_id}/body",
    response_class=HTMLResponse,
)
def contextual_detail_body(request: Request, submission_id: str, edm_id: str):
    return _contextual_body_partial(
        request, submission_id, edm_id, poll=True)


@router.post("/submissions/{submission_id}/edms/{edm_id}/sync")
def contextual_sync(
    request: Request, submission_id: str, edm_id: str,
    csrf_token: str = Form(...),
):
    url = f"/submissions/{submission_id}/edms/{edm_id}"
    is_htmx = request.headers.get("HX-Request") == "true"
    if not validate_csrf_token(csrf_token):
        if is_htmx:
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse(url, status_code=303)
    context_exists = edm_service.sync_contextual_detail(
        submission_id=submission_id, edm_id=edm_id,
        actor_id=request.state.user.id)
    if not context_exists:
        return _contextual_not_found(request)
    if is_htmx:
        return _contextual_body_partial(request, submission_id, edm_id)
    return RedirectResponse(url, status_code=303)


@router.post("/submissions/{submission_id}/edms/{edm_id}/notes")
def contextual_notes(
    request: Request, submission_id: str, edm_id: str,
    notes: str = Form(default=""), original_notes: str = Form(default=""),
    csrf_token: str = Form(...),
):
    url = f"/submissions/{submission_id}/edms/{edm_id}"
    if edm_service.get_contextual_edm_detail(
            submission_id=submission_id, edm_id=edm_id) is None:
        return _contextual_not_found(request)
    return save_notes(
        request, kind="edm", entity_id=edm_id, action=f"{url}/notes",
        return_url=url, notes=notes, original_notes=original_notes,
        csrf_token=csrf_token, get_entity=edm_service.get_edm_detail)


@router.get(
    "/submissions/{submission_id}/edms/{edm_id}/rdms/{rdm_id}/analyses",
    response_class=HTMLResponse,
)
def contextual_rdm_analyses(
    request: Request, submission_id: str, edm_id: str, rdm_id: str,
):
    context = edm_service.get_contextual_edm_detail(
        submission_id=submission_id, edm_id=edm_id)
    if context is None:
        return _contextual_not_found(request)
    analyses = analysis_service.list_submission_rdm_analyses(
        submission_id=submission_id, rdm_id=rdm_id)
    if analyses is None:
        return _contextual_not_found(request)
    rdm = next(
        (group for group in context.rdms if group.rdm_id == rdm_id.lower()), None)
    if rdm is None:
        return _contextual_not_found(request)
    return _partial(
        request, "partials/contextual_rdm_analyses.html",
        {"analyses": analyses, "rdm": rdm})


@router.get("/edms/{edm_id}", response_class=HTMLResponse)
def detail(request: Request, edm_id: str):
    return _detail(request, edm_id)


def _toast_header(message: str, kind: str) -> str:
    return json.dumps({"rwb:toast": {"message": message, "type": kind}})


@router.post("/edms/{edm_id}/geohaz")
def geohaz_launch(
    request: Request,
    edm_id: str,
    csrf_token: Annotated[str, Form()],
    portfolio_ids: Annotated[list[str] | None, Form()] = None,
    submission_id: Annotated[str | None, Form()] = None,
):
    is_htmx = request.headers.get("HX-Request") == "true"
    submission_id = submission_id or None
    return_url = (
        f"/submissions/{submission_id}/edms/{edm_id}"
        if submission_id else f"/edms/{edm_id}"
    )
    if not validate_csrf_token(csrf_token):
        if is_htmx:
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse(return_url, status_code=303)

    if submission_id and edm_service.get_contextual_edm_detail(
            submission_id=submission_id, edm_id=edm_id) is None:
        return _contextual_not_found(request)

    selected = portfolio_ids or []
    error: str | None = None
    try:
        result = geohaz_service.launch(
            edm_id=edm_id,
            portfolio_ids=selected,
            actor_id=request.state.user.id,
        )
    except (InvalidGeohazLaunch, GeohazLaunchConflict) as exc:
        error = str(exc)
        if not is_htmx:
            query = urlencode({"geohaz_error": error})
            return RedirectResponse(f"{return_url}?{query}", status_code=303)

    if not is_htmx:
        return RedirectResponse(f"{return_url}?geohaz=queued", status_code=303)

    response = (
        _contextual_body_partial(request, submission_id, edm_id)
        if submission_id else _body_partial(request, edm_id)
    )
    if error is not None:
        response.headers["HX-Trigger"] = _toast_header(error, "error")
    else:
        message = (
            f"Hazard lookup queued for {len(result.portfolio_ids)} "
            f"portfolio{'s' if len(result.portfolio_ids) != 1 else ''}."
        )
        response.headers["HX-Trigger"] = _toast_header(message, "success")
    return response


@router.get(
    "/edms/{edm_id}/portfolios/{portfolio_id}/geohaz-cell",
    response_class=HTMLResponse,
)
def geohaz_cell(request: Request, edm_id: str, portfolio_id: str):
    # 200 always: htmx never swaps a non-2xx response, so a 404 here would leave
    # a deleted portfolio's cell polling forever. The terminal fragment carries
    # no hx-* attributes, and the swap itself ends the poll — the same pattern
    # as _body_partial's EDM-gone terminal notice.
    state = geohaz_service.cell_state(portfolio_id, edm_id=edm_id)
    return _partial(
        request,
        "partials/geohaz_cell.html",
        {
            "edm_id": edm_id,
            "state": state,
            "latest": (
                geohaz_service.latest_lookup(portfolio_id) if state else None
            ),
            "refresh_details": True,
        },
    )


def _body_partial(request: Request, edm_id: str, *, poll: bool = False):
    """The shell-less #edm-detail wrapper — the HTMX swap/poll unit."""
    edm = edm_service.get_edm_detail(edm_id)
    if edm is None:
        # EDM hard-gone mid-poll: return a terminal notice with no trigger,
        # so the every-3s poll ends instead of returning a repeating 404.
        return HTMLResponse(
            '<div class="page-pad" id="edm-detail">'
            '<div class="state-box state-box--warn">This EDM no longer exists.'
            '</div></div>')
    if poll and edm.sync_running and edm.detail_state == "populated":
        # A populated page mid-sync: swapping the body every 3s would collapse
        # every <details> the analyst opened and — because #edm-detail is the
        # page's scrolling element — scroll them back to the top. 204 → htmx
        # swaps nothing and the poll keeps ticking; the first post-sync poll
        # returns the fresh body (whose trigger is gone), rendering the result
        # exactly once. A breakout episode is served by the Portfolios
        # section's own poll instead (T-11 — portfolios_section below).
        return Response(status_code=204)
    return _partial(request, "partials/edm_detail_body.html", {"edm": edm})


@router.get("/edms/{edm_id}/body", response_class=HTMLResponse)
def detail_body(request: Request, edm_id: str):
    """Read-only body render for HTMX polling. The template emits the ``every 3s``
    trigger only while the backfill head is in flight (``sync_running``) or the
    import itself still is, so the page updates on its own when the rwb job lands —
    and polling stops once the work is terminal. A populated page mid-sync gets a
    204 (poll continues, nothing swaps) so open rows aren't collapsed. No writes,
    no Risk Modeler call (Article 11)."""
    return _body_partial(request, edm_id, poll=True)


@router.get("/edms/{edm_id}/portfolios-section", response_class=HTMLResponse)
def portfolios_section(request: Request, edm_id: str):
    """The Portfolios section on its own, for the breakout-episode poll (T-11).

    A breakout changes only that section — the completion banner, the source
    row's ``N of M`` counter, the generated rows, the per-row failure lines —
    so the section polls this route every 3s instead of the whole body: the
    body wrapper ``#edm-detail`` is the page's scrolling element, and replacing
    it scrolled the analyst back to the top every cycle. The response also
    OOB-swaps the header meta line and the rollup strip, the two places outside
    the section that carry a portfolio count. The template emits the ``every 3s``
    trigger only while the breakout episode is live (the run itself, or its
    FR-013 follow-up backfill filling figures in), so polling self-terminates.
    Read-only — no writes, no Risk Modeler call (Article 11)."""
    edm = edm_service.get_edm_detail(edm_id)
    if edm is None:
        # EDM hard-gone mid-poll: a terminal notice with no trigger, so the
        # every-3s poll ends instead of 404-looping (the body-poll precedent).
        return HTMLResponse(
            '<details class="sec" open id="edm-portfolios">'
            '<summary><span class="sec__title">Portfolios</span></summary>'
            '<div class="state-box state-box--warn">This EDM no longer exists.'
            '</div></details>')
    return _partial(request, "partials/edm_portfolios_live.html", {"edm": edm})


@router.post("/edms/{edm_id}/retry")
def retry(request: Request, edm_id: str, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)
    edm_service.retry_import(edm_id=edm_id, actor_id=request.state.user.id)
    return _detail(request, edm_id)


@router.post("/edms/{edm_id}/sync")
def sync(request: Request, edm_id: str, csrf_token: str = Form(...)):
    # Manual detail re-sync (FR-003 as amended): enqueues the backfill_edm_detail
    # worker — the fetch itself never runs on this request path (Article 11).
    # HTMX path: swap the #edm-detail wrapper in place (it then self-polls until
    # the head lands). No-JS fallback: Post/Redirect/Get, so a refresh never
    # re-prompts a form re-submission.
    is_htmx = request.headers.get("HX-Request") == "true"
    if not validate_csrf_token(csrf_token):
        if is_htmx:
            # Never swap a redirect-followed full page into the wrapper — force
            # a clean reload (which also mints fresh tokens).
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)
    edm_service.sync_detail(edm_id=edm_id, actor_id=request.state.user.id)
    if is_htmx:
        return _body_partial(request, edm_id)
    return RedirectResponse(f"/edms/{edm_id}", status_code=303)


@router.post("/edms/{edm_id}/replace-file")
def replace_file(
    request: Request,
    edm_id: str,
    source_paths: list[str] = Form(default=[]),
    updated_at: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)
    source = source_paths[0] if source_paths else ""
    if not source:
        return _detail(request, edm_id, status_code=422)
    try:
        edm_service.replace_source_file(
            edm_id=edm_id, new_source_file_path=source,
            expected_updated_at=updated_at, actor_id=request.state.user.id)
    except InvalidSourceFile:
        return _detail(request, edm_id, status_code=422)
    except ConcurrencyConflict:
        return _detail(request, edm_id, status_code=409)
    return _detail(request, edm_id)


@router.post("/edms/{edm_id}/notes")
def notes(
    request: Request, edm_id: str, notes: str = Form(default=""),
    original_notes: str = Form(default=""), csrf_token: str = Form(...),
):
    return save_notes(
        request, kind="edm", entity_id=edm_id,
        action=f"/edms/{edm_id}/notes",
        return_url=f"/edms/{edm_id}", notes=notes,
        original_notes=original_notes, csrf_token=csrf_token,
        get_entity=edm_service.get_edm_detail)


__all__ = ["router"]
