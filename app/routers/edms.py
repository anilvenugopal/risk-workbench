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
from html import escape
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.nav import get_nav_context
from app.routers._compare import compare_modal_response
from app.routers._entity_notes import save_notes
from app.services import (
    analysis_execution_service,
    analysis_service,
    edm_service,
    geohaz_service,
    grouping_service,
    portfolio_service,
    template_service,
    treaty_service,
)
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

# Fired on the execute modal's successful POST: app.js clears the submitted
# portfolio picks and fetches the Analyses section with the execution id.
def _execution_submitted_headers(execution_id: str) -> dict[str, str]:
    return {
        "HX-Trigger": json.dumps({
            "execution-submitted": {"execution_id": execution_id},
            "rwb:toast": {
                "message": "Analysis submission started.", "type": "success"},
        }),
    }


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


@router.get("/edms/execute/vintage-options", response_class=HTMLResponse)
def execute_vintage_options(request: Request):
    """The scheme→vintage cascade for the execute-analysis modal's currency
    block (FR-019): the vintage select re-GETs its ``<option>`` list scoped to
    whichever scheme was just chosen."""
    scheme = (request.query_params.get("scheme") or "").strip()
    options = analysis_execution_service.vintage_options(scheme) if scheme else []
    return _partial(request, "partials/currency_vintage_options.html",
                    {"vintage_options": options})


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
        "analyses_table_url": f"{base_url}/analyses",
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


@router.get(
    "/submissions/{submission_id}/edms/{edm_id}/analyses",
    response_class=HTMLResponse,
)
def contextual_detail_analyses(request: Request, submission_id: str, edm_id: str):
    """Contextual variant of ``detail_analyses`` — the Analyses section's own
    polling fragment. No writes, no Risk Modeler call (Article 11)."""
    return _analyses_section_partial(request, edm_id,
                                     submission_id=submission_id)


@router.post("/submissions/{submission_id}/edms/{edm_id}/analyses/delete")
async def contextual_delete_analyses(
    request: Request, submission_id: str, edm_id: str,
):
    form = await request.form()
    if not validate_csrf_token(form.get("csrf_token")):
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse(f"/submissions/{submission_id}/edms/{edm_id}",
                                status_code=303)
    if edm_service.get_contextual_edm_detail(
            submission_id=submission_id, edm_id=edm_id) is None:
        return _contextual_not_found(request)
    return _delete_analyses_response(request, edm_id, form)


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


@router.get(
    "/submissions/{submission_id}/edms/{edm_id}/execute",
    response_class=HTMLResponse,
)
def contextual_execute_modal(request: Request, submission_id: str, edm_id: str):
    if edm_service.get_contextual_edm_detail(
            submission_id=submission_id, edm_id=edm_id) is None:
        return _contextual_not_found(request)
    return _execute_modal_get(
        request, edm_id=edm_id,
        action_url=f"/submissions/{submission_id}/edms/{edm_id}/execute")


@router.post("/submissions/{submission_id}/edms/{edm_id}/execute")
async def contextual_execute_submit(request: Request, submission_id: str, edm_id: str):
    form = await request.form()
    url = f"/submissions/{submission_id}/edms/{edm_id}"
    if not validate_csrf_token(form.get("csrf_token")):
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse(url, status_code=303)
    context = edm_service.get_contextual_edm_detail(
        submission_id=submission_id, edm_id=edm_id)
    if context is None:
        return _contextual_not_found(request)
    return _execute_submit_response(
        request, edm_id=edm_id, action_url=f"{url}/execute", form=form,
        submission_id=submission_id, submission_name=context.submission.name)


@router.get("/edms/{edm_id}", response_class=HTMLResponse)
def detail(request: Request, edm_id: str):
    return _detail(request, edm_id)


# ── Execute Suite / Execute Template modal (spec 010) ────────────────────────────

def _execute_context(
    *, edm, portfolios_all: list, kind: str, portfolio_ids: list[str],
) -> dict:
    """Shared GET/POST-error context: the blocking message, or every reference
    list the modal fragment needs (contracts/routes.md — GET .../execute)."""
    kind = kind if kind in ("suite", "template") else "suite"
    picked_ids = set(portfolio_ids)
    picked = [p for p in portfolios_all if p.id in picked_ids]
    blocking = None
    if edm is None or edm.status != edm_service.READY:
        blocking = "This EDM is not ready for execution."
    elif not picked:
        blocking = "Select at least one portfolio before executing an analysis."
    elif kind == "suite" and not template_service.list_suites():
        blocking = ("No template suites exist yet. Create one under Templates & "
                    "Suites before running an execution.")
    elif kind == "template" and not template_service.list_templates():
        blocking = ("No analysis templates exist yet. Create one under Templates "
                    "& Suites before running an execution.")
    ctx: dict = {"edm": edm, "kind": kind, "portfolios": picked, "blocking": blocking}
    if blocking:
        return ctx
    defaults = analysis_execution_service.currency_defaults()
    ctx.update({
        "treaties": treaty_service.list_treaties(edm_id=edm.id),
        "currency_defaults": defaults,
        "currency_options": analysis_execution_service.currency_options(),
        "scheme_options": analysis_execution_service.currency_scheme_options(),
        "default_vintage_options": analysis_execution_service.vintage_options(
            defaults["scheme"]),
    })
    if kind == "suite":
        ctx["suites"] = template_service.list_suites()
    else:
        ctx["templates"] = template_service.list_templates()
    return ctx


def _parse_execute_form(form) -> dict:
    """Dynamic per-suite field names (``templates_{suite_id}``,
    ``currency_*_{suite_id}``) can't be declared as static ``Form(...)``
    parameters — the route reads the raw form and this parses it."""
    kind = form.get("kind", "suite")
    parsed: dict = {
        "kind": kind,
        "portfolio_ids": form.getlist("portfolio_ids"),
        "treaty_names": form.getlist("treaty_names"),
        "suite_picks": None, "template_ids": None,
        "currency_code": "", "currency_scheme": "", "currency_vintage": "",
    }
    if kind == "suite":
        parsed["suite_picks"] = [
            analysis_execution_service.SuitePick(
                suite_id=suite_id,
                template_ids=form.getlist(f"templates_{suite_id}"),
                currency_code=form.get(f"currency_code_{suite_id}", ""),
                currency_scheme=form.get(f"currency_scheme_{suite_id}", ""),
                currency_vintage=form.get(f"currency_vintage_{suite_id}", ""))
            for suite_id in form.getlist("chosen_suite_ids")
        ]
    else:
        parsed["template_ids"] = form.getlist("template_ids")
        parsed["currency_code"] = form.get("currency_code", "")
        parsed["currency_scheme"] = form.get("currency_scheme", "")
        parsed["currency_vintage"] = form.get("currency_vintage", "")
    return parsed


def _execute_modal_response(request: Request, *, edm_id: str, action_url: str,
                            kind: str, portfolio_ids: list[str],
                            errors: list[str], status_code: int = 200):
    """The modal fragment, shared by both GETs and the gate's 422 re-render.
    ``action_url`` keeps the modal's POST on the path the analyst came in on."""
    edm = edm_service.get_edm(edm_id)
    portfolios = portfolio_service.list_portfolios(edm_id=edm_id) if edm else []
    ctx = _execute_context(edm=edm, portfolios_all=portfolios, kind=kind,
                           portfolio_ids=portfolio_ids)
    ctx["action_url"] = action_url
    return _partial(request, "partials/execute_analysis_modal.html",
                    {**ctx, "errors": errors}, status_code=status_code)


def _execute_modal_get(request: Request, *, edm_id: str, action_url: str):
    return _execute_modal_response(
        request, edm_id=edm_id, action_url=action_url,
        kind=request.query_params.get("kind", "suite"),
        portfolio_ids=request.query_params.getlist("portfolio_ids"), errors=[])


def _execute_error_response(request: Request, *, edm_id: str, action_url: str,
                            kind: str, portfolio_ids: list[str],
                            errors: list[str]):
    # Retargeted at the mount because htmx drops a non-2xx body at the
    # triggering element's own target by default.
    response = _execute_modal_response(
        request, edm_id=edm_id, action_url=action_url, kind=kind,
        portfolio_ids=portfolio_ids, errors=errors, status_code=422)
    if request.headers.get("HX-Request") == "true":
        response.headers["HX-Retarget"] = "#execute-modal"
        response.headers["HX-Reswap"] = "innerHTML"
    return response


def _execute_submit_response(request: Request, *, edm_id: str, action_url: str,
                             form, submission_id: str | None = None,
                             submission_name: str | None = None):
    """Shared body of both execute POSTs. ``submission_name`` is None outside a
    submission — it becomes the extra analysis tag on every plan item (FR-021)."""
    parsed = _parse_execute_form(form)
    try:
        execution_id = analysis_execution_service.request_execution(
            edm_id=edm_id, actor_id=request.state.user.id,
            submission_id=submission_id, submission_name=submission_name,
            **parsed)
    except analysis_execution_service.ExecutionGateError as exc:
        return _execute_error_response(
            request, edm_id=edm_id, action_url=action_url,
            kind=parsed["kind"], portfolio_ids=parsed["portfolio_ids"],
            errors=exc.errors)
    return Response(status_code=204,
                    headers=_execution_submitted_headers(execution_id))


@router.get("/edms/{edm_id}/execute", response_class=HTMLResponse)
def execute_modal(request: Request, edm_id: str):
    return _execute_modal_get(request, edm_id=edm_id,
                              action_url=f"/edms/{edm_id}/execute")


@router.post("/edms/{edm_id}/execute")
async def execute_submit(request: Request, edm_id: str):
    form = await request.form()
    if not validate_csrf_token(form.get("csrf_token")):
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)
    return _execute_submit_response(
        request, edm_id=edm_id, action_url=f"/edms/{edm_id}/execute", form=form)


_ANALYSES_STATUS_FILTERS = ("failed", "in_progress", "ready")


def _analyses_status_filter(request: Request) -> str:
    """The section's ``?status=`` filter, clamped to the three group keys —
    anything else reads as no filter."""
    status = (request.query_params.get("status") or "").strip()
    return status if status in _ANALYSES_STATUS_FILTERS else ""


def _analyses_gone_notice(message: str) -> HTMLResponse:
    """The Analyses section with nothing left to poll. Not
    ``analyses_merged_section.html``: that template always emits the ``hx-get``
    and ``hx-trigger`` the 3s poll runs on, and this notice must omit them so the
    poll stops instead of refetching a section that no longer resolves."""
    return HTMLResponse(
        '<details class="sec" open id="edm-executed-analyses">'
        '<summary><span class="sec__title">Analyses</span></summary>'
        f'<div class="state-box state-box--warn">{escape(message)}'
        '</div></details>')


def _analyses_section_partial(request: Request, edm_id: str,
                              *, submission_id: str | None = None):
    """The merged Analyses section's own fragment (analyses_merged_section.html)
    — its polling unit, separate from the rest of the detail body (T-11
    refinement) so an in-flight execution never re-swaps rows the analyst has
    expanded elsewhere on the page. With ``submission_id`` the fragment polls and
    deletes against its submission-scoped URL and renders the submission's RDM
    group rows; the plain library page has neither."""
    section = edm_service.get_edm_analyses(edm_id=edm_id,
                                           submission_id=submission_id)
    if section is None:
        return _analyses_gone_notice(
            "This EDM is no longer related to the submission." if submission_id
            else "This EDM no longer exists.")
    execution_id = (request.query_params.get("execution_id") or "").strip() or None
    grouping_request_id = (request.query_params.get("grouping_request_id")
                           or "").strip() or None
    base = f"/edms/{edm_id}/analyses" if submission_id is None else (
        f"/submissions/{submission_id}/edms/{edm_id}/analyses")
    return _partial(request, "partials/analyses_merged_section.html",
                    {"edm": section, "groups": section.rdms,
                     "source_submission": section.submission,
                     "status_filter": _analyses_status_filter(request),
                     "execution_id": execution_id,
                     "grouping_request_id": grouping_request_id or "",
                     "execution_live": (
                         analysis_service.execution_batch_is_live(execution_id)
                         or grouping_service.grouping_request_is_live(
                             grouping_request_id)),
                     "analyses_table_url": base})


@router.get("/edms/{edm_id}/analyses", response_class=HTMLResponse)
def detail_analyses(request: Request, edm_id: str):
    """Read-only Analyses-table fragment for HTMX polling. No writes, no Risk
    Modeler call (Article 11)."""
    return _analyses_section_partial(request, edm_id)


@router.get("/edms/{edm_id}/analyses/compare", response_class=HTMLResponse)
def detail_analyses_compare(request: Request, edm_id: str):
    return compare_modal_response(request, edm_id=edm_id)


def _delete_analyses_response(request: Request, edm_id: str, form) -> Response:
    """Shared body of the two analyses-delete POSTs (P-19): synchronous
    request-path cascade — Risk Modeler delete first, local soft delete on
    success. Validation failures return 422 whose banner text app.js surfaces
    as a toast (htmx:responseError)."""
    analysis_ids = form.getlist("analysis_ids")
    try:
        outcome = analysis_service.delete_executed_analyses(
            edm_id=edm_id, analysis_ids=analysis_ids,
            actor_id=request.state.user.id)
    except ValueError as exc:
        return HTMLResponse(
            f'<div class="form-banner--error">{escape(str(exc))}</div>',
            status_code=422)
    message = f"Deleted {outcome.deleted} analysis(es)."
    toast_type = "success"
    if outcome.failed:
        message += (f" {len(outcome.failed)} could not be deleted in "
                    "Risk Modeler.")
        toast_type = "warning"
    if outcome.retrying:
        message += (f" {len(outcome.retrying)} could not be deleted — a "
                    "submission retry is in progress.")
        toast_type = "warning"
    return Response(status_code=204, headers={
        "HX-Trigger": json.dumps({
            "analyses-changed": True,
            "rwb:toast": {"message": message, "type": toast_type},
        }),
    })


@router.post("/edms/{edm_id}/analyses/delete")
async def delete_analyses(request: Request, edm_id: str):
    form = await request.form()
    if not validate_csrf_token(form.get("csrf_token")):
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)
    return _delete_analyses_response(request, edm_id, form)


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
        launched = geohaz_service.launch(
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
        response.headers["HX-Trigger"] = json.dumps(
            {"rwb:toast": {"message": error, "type": "error"}})
    else:
        message = (
            f"Hazard lookup queued for {len(launched)} "
            f"portfolio{'s' if len(launched) != 1 else ''}."
        )
        response.headers["HX-Trigger"] = json.dumps(
            {"rwb:toast": {"message": message, "type": "success"}})
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
    # Scoped to one portfolio, so the read returns at most one entry.
    entry = next(iter(
        geohaz_service.read(edm_id=edm_id, portfolio_id=portfolio_id).values()), None)
    return _partial(
        request,
        "partials/geohaz_cell.html",
        {
            "edm_id": edm_id,
            "state": entry.state if entry else None,
            "latest": entry.latest if entry else None,
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
def portfolios_section(request: Request, edm_id: str,
                       submission_id: str | None = None):
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

    ``submission_id`` is the submission the analyst came through, carried on the
    poll URL and handed straight back to the GeoHaz form's hidden input so a
    launch after a swap still returns to the submission-scoped page. It is not
    validated here — nothing is read with it; POST /edms/{id}/geohaz rejects a
    submission the EDM does not belong to.

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
    return _partial(request, "partials/edm_portfolios_live.html",
                    {"edm": edm, "gh_sub": submission_id})


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
