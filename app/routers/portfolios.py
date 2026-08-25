"""Portfolio routes — the breakout modal and its confirm (spec 005 US1/US2).

Nav key ``irp.edm_library`` — the modal belongs to the EDM detail page, no new
nav node (Article 1). Conventions inherited from ``edms.py``/``packages.py``:
``_partial``, ``validate_csrf_token`` on POST (HTMX CSRF failure → 204 +
HX-Refresh), refusals → **409 + re-rendered modal fragment**, toasts via
``HX-Trigger: {"rwb:toast": …}``.

The GET renders one snapshot of the STORED summary — zero Risk Modeler or
DataBridge calls. Two request-path reads exist, both in services: the confirm's
FR-002a freshness read (the Article 2 submit-time pattern) and the Add's
name/emptiness checks (the Article 11 request-path exception, v3.2.0 — one
cached RM name search and one single-row DataBridge count, both fail-open).
Everything else is enqueue-only.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.auth.csrf import validate_csrf_token
from app.services import breakout_service, edm_service
from app.services.breakout_service import (
    GateRefused, StaleSummary, SummaryRewritten)

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _partial(request: Request, template: str, ctx: dict, status_code: int = 200):
    return _templates(request).TemplateResponse(
        request, template, {"current_user": request.state.user, **ctx},
        status_code=status_code,
    )


def _modal(request: Request, edm_id: str, portfolio_id: str,
           dimension: str | None = None, *, mode: str = "quick",
           status_code: int = 200, error: str | None = None,
           error_kind: str | None = None):
    """Render ``partials/breakout_modal.html`` for the CURRENT stored summary.
    ``mode`` picks the pane — quick (default) or custom (the grouping cart,
    FR-018). Missing/deleted EDM or portfolio → the graceful 404 fragment
    (never an error page — the modal mounts over a page that may be
    mid-poll)."""
    modal = breakout_service.modal_context(edm_id, portfolio_id, dimension)
    ctx = {
        "edm_id": edm_id, "portfolio_id": portfolio_id, "modal": modal,
        "missing": modal is None, "error": error, "error_kind": error_kind,
        "mode": ("custom" if mode == "custom" else "quick"),
        "large_fanout_threshold": breakout_service.LARGE_FANOUT_THRESHOLD,
        "name_max": breakout_service.PORTFOLIO_NAME_MAX,
    }
    return _partial(request, "partials/breakout_modal.html", ctx,
                    status_code=404 if modal is None else status_code)


def _breakout_started(request: Request, edm_id: str, count: int):
    """The success response both confirms share: the Portfolios section
    retargeted at ``#edm-portfolios`` (the form targets the modal mount, so the
    modal closes on 2xx) plus the "Breakout started" toast.

    The section, not the whole ``#edm-detail`` body: this route carries no
    submission id, so a body render drops ``source_submission`` and erases the
    submission breadcrumbs, the EDM picker, and the Broker analyses section
    from the contextual page. The section is also all a breakout changes
    (T-11), and it comes back with its own ``every 3s`` trigger live because
    the enqueue just made ``breakout_running`` true."""
    edm = edm_service.get_edm_detail(edm_id)
    response = _partial(request, "partials/edm_portfolios_live.html",
                        {"edm": edm})
    response.headers["HX-Retarget"] = "#edm-portfolios"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps({"rwb:toast": {
        "message": f"Breakout started — {count} sub-portfolio"
                   f"{'' if count == 1 else 's'}",
        "type": "success"}})
    return response


@router.get("/edms/{edm_id}/portfolios/{portfolio_id}/breakout",
            response_class=HTMLResponse)
def breakout_modal(request: Request, edm_id: str, portfolio_id: str):
    """The preview modal (FR-001/FR-006/FR-007): gate + plan + overlap from the
    stored summary. ``?dimension=`` selects the chooser tab; ``?mode=custom``
    opens the grouping pane (FR-018). Fetched into ``#breakout-modal-mount`` —
    OUTSIDE the self-polling ``#edm-detail`` wrapper, so the 3-second poll
    never removes an open modal. GET, no CSRF, no writes, no Risk Modeler call
    (Article 11)."""
    return _modal(request, edm_id, portfolio_id,
                  request.query_params.get("dimension"),
                  mode=request.query_params.get("mode") or "quick")


@router.get("/edms/{edm_id}/portfolios/{portfolio_id}/breakout/name-check",
            response_class=HTMLResponse)
def breakout_name_check(request: Request, edm_id: str, portfolio_id: str):
    """As-you-type group-name check (P-25 — the EDM import pattern): renders
    ``partials/name_collision.html`` with the verdict for the typed name
    against this EDM's portfolios. GET, no writes; the Risk Modeler leg fails
    open."""
    name = request.query_params.get("group_label", "")
    return _partial(request, "partials/name_collision.html",
                    {"check": breakout_service.check_group_name(edm_id, name),
                     "name": name, "kind": "portfolio", "scope": "this EDM",
                     "action": "Adding"})


@router.post("/edms/{edm_id}/portfolios/{portfolio_id}/breakout")
def breakout_confirm(
    request: Request,
    edm_id: str,
    portfolio_id: str,
    dimension: str = Form(...),
    summary_as_of: str = Form(default=""),
    csrf_token: str = Form(...),
):
    """Confirm (FR-002a/FR-002b/FR-006a): ``request_breakout`` runs the seven
    ordered steps — gate re-check, in-flight check, dimension-eligibility
    check, summary-unchanged check, freshness read, plan persistence,
    idempotent enqueue. Success returns the Portfolios section (retargeted at
    ``#edm-portfolios`` — the modal closes itself) with the "Breakout started"
    toast; every refusal returns **409 + the re-rendered modal** and writes no
    job row. No-JS fallback is PRG."""
    is_htmx = request.headers.get("HX-Request") == "true"
    if not validate_csrf_token(csrf_token):
        if is_htmx:
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)

    def refused(error: str | None = None, error_kind: str | None = None):
        if not is_htmx:
            return RedirectResponse(f"/edms/{edm_id}", status_code=303)
        return _modal(request, edm_id, portfolio_id, dimension,
                      status_code=409, error=error, error_kind=error_kind)

    try:
        requested = breakout_service.request_breakout(
            edm_id, portfolio_id, dimension, summary_as_of or None,
            request.state.user.id)
    except SummaryRewritten as exc:
        return refused(exc.reason, "rewritten")
    except StaleSummary as exc:
        return refused(exc.reason, "stale")
    except GateRefused as exc:
        return refused(exc.reason, "gate")
    if requested is None:
        return refused(error_kind="running")

    if not is_htmx:
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)
    return _breakout_started(request, edm_id, requested.planned)


def _carted_groups(form) -> list[dict]:
    """The cart's hidden ``group`` JSON inputs → ``[{"label", "filters"}]``.
    Client state is never trusted: a blob that does not parse to an object
    refuses, and the service re-validates every dimension and value against
    the stored summary (FR-018)."""
    groups: list[dict] = []
    for raw in form.getlist("group"):
        try:
            parsed = json.loads(raw)
        except ValueError:
            raise GateRefused("a cart row is malformed — remove it and add "
                              "the breakout again") from None
        if not isinstance(parsed, dict):
            raise GateRefused("a cart row is malformed — remove it and add "
                              "the breakout again")
        groups.append({"label": parsed.get("label"),
                       "filters": parsed.get("filters")})
    return groups


@router.post("/edms/{edm_id}/portfolios/{portfolio_id}/breakout/group-preview")
async def breakout_group_preview(request: Request, edm_id: str,
                                 portfolio_id: str):
    """Compose ONE cart row server-side (FR-018/P-23): the posted checkbox
    selections + label, validated against the stored summary and
    overlap-checked against the already-carted group JSONs
    posted along, then refused when the name is taken (P-25) or when no account
    matches every filter (P-29). No writes. Returns the cart-row fragment
    (``hx-swap beforeend``); a refusal returns 409 retargeted at
    ``#bo-cart-error``."""
    form = await request.form()
    is_htmx = request.headers.get("HX-Request") == "true"
    if not validate_csrf_token(str(form.get("csrf_token") or "")):
        if is_htmx:
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)
    filters = {key.split(":", 1)[1]: [str(v) for v in form.getlist(key)]
               for key in form if key.startswith("values:")}
    new_group = {"label": str(form.get("group_label") or ""),
                 "filters": filters}
    try:
        # The whole Add decision lives in the service; the threadpool covers
        # its DataBridge match count, which must not hold the event loop.
        plan = await run_in_threadpool(
            breakout_service.compose_group_preview, edm_id, portfolio_id,
            carted=_carted_groups(form), new_group=new_group)
    except GateRefused as exc:
        response = _partial(request, "partials/breakout_cart_row.html",
                            {"error": exc.reason}, status_code=409)
        response.headers["HX-Retarget"] = "#bo-cart-error"
        response.headers["HX-Reswap"] = "innerHTML"
        return response
    return _partial(request, "partials/breakout_cart_row.html", {
        "plan": plan,
        "group_json": json.dumps({"label": plan.label,
                                  "filters": plan.filters}),
    })


@router.post("/edms/{edm_id}/portfolios/{portfolio_id}/breakout/groups")
async def breakout_groups_confirm(request: Request, edm_id: str,
                                  portfolio_id: str):
    """The cart confirm (FR-018/FR-020): ``request_group_breakout`` re-validates
    every posted group and applies the same ordered refusals as the quick
    confirm — **409 + the re-rendered modal** on each, with no job row; on
    pass, one ``breakout_group`` upsert and one ``run_breakout_custom`` job
    per group. Success returns the Portfolios section retargeted at
    ``#edm-portfolios``, exactly like the quick confirm."""
    form = await request.form()
    is_htmx = request.headers.get("HX-Request") == "true"
    if not validate_csrf_token(str(form.get("csrf_token") or "")):
        if is_htmx:
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)

    def refused(error: str | None = None, error_kind: str | None = None):
        if not is_htmx:
            return RedirectResponse(f"/edms/{edm_id}", status_code=303)
        return _modal(request, edm_id, portfolio_id, mode="custom",
                      status_code=409, error=error, error_kind=error_kind)

    try:
        job_ids = breakout_service.request_group_breakout(
            edm_id, portfolio_id, _carted_groups(form),
            str(form.get("summary_as_of") or "") or None,
            request.state.user.id)
    except SummaryRewritten as exc:
        return refused(exc.reason, "rewritten")
    except StaleSummary as exc:
        return refused(exc.reason, "stale")
    except GateRefused as exc:
        return refused(exc.reason, "gate")
    if job_ids is None:
        return refused(error_kind="running")

    if not is_htmx:
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)
    return _breakout_started(request, edm_id, len(job_ids))


__all__ = ["router"]
