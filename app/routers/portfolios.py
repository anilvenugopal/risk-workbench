"""Portfolio routes — the breakout modal and its confirm (spec 005 US1/US2).

Nav key ``irp.edm_library`` — the modal belongs to the EDM detail page, no new
nav node (Article 1). Conventions inherited from ``edms.py``/``packages.py``:
``_partial``, ``validate_csrf_token`` on POST (HTMX CSRF failure → 204 +
HX-Refresh), refusals → **409 + re-rendered modal fragment**, toasts via
``HX-Trigger: {"rwb:toast": …}``.

The GET renders one snapshot of the STORED summary — zero Risk Modeler or
DataBridge calls. The POST's only RM call happens inside
``breakout_service.request_breakout`` (the FR-002a freshness read — the
Article 2 submit-time pattern); everything else is enqueue-only (Article 11).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.services import breakout_service, edm_service
from app.services.breakout_service import (
    GateRefused, StaleSummary, SummaryRewritten)
from db import execute_one

router = APIRouter()

_NAV_KEY = "irp.edm_library"  # documented owner; the modal renders no nav


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
        "missing_summary_reason": breakout_service.MISSING_SUMMARY_REASON,
        "name_max": breakout_service.PORTFOLIO_NAME_MAX,
    }
    if modal is None:
        return _partial(request, "partials/breakout_modal.html", ctx,
                        status_code=404)
    return _partial(request, "partials/breakout_modal.html", ctx,
                    status_code=status_code)


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


def _planned_count(job_id: str) -> int:
    row = execute_one("SELECT input_data FROM rwb_job WHERE id = :i",
                      {"i": str(job_id)}, connection="WORKBENCH")
    if row is None or not row["input_data"]:
        return 0
    plan = json.loads(row["input_data"]).get("plan")
    return len(plan) if isinstance(plan, list) else 0


@router.post("/edms/{edm_id}/portfolios/{portfolio_id}/breakout")
def breakout_confirm(
    request: Request,
    edm_id: str,
    portfolio_id: str,
    dimension: str = Form(...),
    summary_as_of: str = Form(default=""),
    csrf_token: str = Form(...),
):
    """Confirm (FR-002a/FR-002b/FR-006a): ``request_breakout`` runs the five
    ordered steps — gate re-check, summary-unchanged check, freshness read,
    plan persistence, idempotent enqueue. Success returns the EDM body partial
    (retargeted at ``#edm-detail`` — the modal closes itself) with the
    "Breakout started" toast; every refusal returns **409 + the re-rendered
    modal** and writes no job row. No-JS fallback is PRG."""
    is_htmx = request.headers.get("HX-Request") == "true"
    if not validate_csrf_token(csrf_token):
        if is_htmx:
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)

    try:
        job_id = breakout_service.request_breakout(
            edm_id, portfolio_id, dimension, summary_as_of or None,
            request.state.user.id)
    except SummaryRewritten as exc:
        if not is_htmx:
            return RedirectResponse(f"/edms/{edm_id}", status_code=303)
        return _modal(request, edm_id, portfolio_id, dimension,
                      status_code=409, error=exc.reason, error_kind="rewritten")
    except StaleSummary as exc:
        if not is_htmx:
            return RedirectResponse(f"/edms/{edm_id}", status_code=303)
        return _modal(request, edm_id, portfolio_id, dimension,
                      status_code=409, error=exc.reason, error_kind="stale")
    except GateRefused as exc:
        if not is_htmx:
            return RedirectResponse(f"/edms/{edm_id}", status_code=303)
        return _modal(request, edm_id, portfolio_id, dimension,
                      status_code=409, error=exc.reason, error_kind="gate")

    if job_id is None:
        # Idempotent enqueue found a live run — the re-rendered modal shows
        # its in-flight state (409, no new job row).
        if not is_htmx:
            return RedirectResponse(f"/edms/{edm_id}", status_code=303)
        return _modal(request, edm_id, portfolio_id, dimension,
                      status_code=409, error_kind="running")

    if not is_htmx:
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)
    # Success: swap the whole #edm-detail wrapper (its self-poll then shows
    # sub-portfolios as the worker creates them). The form targets the modal
    # mount, so the response retargets — and the modal closes on 2xx.
    edm = edm_service.get_edm_detail(edm_id)
    response = _partial(request, "partials/edm_detail_body.html", {"edm": edm})
    response.headers["HX-Retarget"] = "#edm-detail"
    response.headers["HX-Reswap"] = "outerHTML"
    n = _planned_count(job_id)
    response.headers["HX-Trigger"] = json.dumps({"rwb:toast": {
        "message": f"Breakout started — {n} sub-portfolio"
                   f"{'' if n == 1 else 's'}",
        "type": "success"}})
    return response


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
    name-suffixed/overlap-checked against the already-carted group JSONs
    posted along. No writes, no Risk Modeler or DataBridge call — the same
    read path as the modal GET. Returns the cart-row fragment
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
    gate = breakout_service.evaluate_gate(edm_id, portfolio_id)
    try:
        plans = breakout_service.compose_group_cart(
            gate, edm_id=edm_id, portfolio_id=portfolio_id,
            groups=[*_carted_groups(form), new_group])
        plan = plans[-1]
        # The Add is where a duplicate name blocks (P-25): compose covered the
        # workbench rows and the cart; this is the Risk Modeler leg, fail-open
        # (an unreachable RM never blocks the Add). An adopted member set
        # keeps its approved name — that name IS its own portfolio.
        if not plan.adopted and breakout_service.check_group_name(
                edm_id, plan.name).collides:
            raise GateRefused(f"a portfolio named {plan.name!r} already "
                              "exists in this EDM — choose a different name")
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
    per group. Success returns the EDM body partial exactly like the quick
    confirm."""
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
    edm = edm_service.get_edm_detail(edm_id)
    response = _partial(request, "partials/edm_detail_body.html", {"edm": edm})
    response.headers["HX-Retarget"] = "#edm-detail"
    response.headers["HX-Reswap"] = "outerHTML"
    n = len(job_ids)
    response.headers["HX-Trigger"] = json.dumps({"rwb:toast": {
        "message": f"Breakout started — {n} sub-portfolio"
                   f"{'' if n == 1 else 's'}",
        "type": "success"}})
    return response


__all__ = ["router"]
