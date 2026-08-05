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
           dimension: str | None = None, *, status_code: int = 200,
           error: str | None = None, error_kind: str | None = None):
    """Render ``partials/breakout_modal.html`` for the CURRENT stored summary.
    Missing/deleted EDM or portfolio → the graceful 404 fragment (never an
    error page — the modal mounts over a page that may be mid-poll)."""
    modal = breakout_service.modal_context(edm_id, portfolio_id, dimension)
    ctx = {
        "edm_id": edm_id, "portfolio_id": portfolio_id, "modal": modal,
        "missing": modal is None, "error": error, "error_kind": error_kind,
        "large_fanout_threshold": breakout_service.LARGE_FANOUT_THRESHOLD,
        "missing_summary_reason": breakout_service.MISSING_SUMMARY_REASON,
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
    stored summary. ``?dimension=`` selects the chooser tab; fetched into
    ``#breakout-modal-mount`` — OUTSIDE the self-polling ``#edm-detail``
    wrapper, so the 3-second poll never removes an open modal. GET, no CSRF,
    no writes, no Risk Modeler call (Article 11)."""
    return _modal(request, edm_id, portfolio_id,
                  request.query_params.get("dimension"))


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


__all__ = ["router"]
