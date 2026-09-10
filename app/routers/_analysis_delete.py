"""The analyses-delete POST body, shared by the EDM, contextual-EDM, and
submission scope routes."""

from __future__ import annotations

import json
from html import escape

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from app.services import analysis_service


def delete_analyses_response(request: Request, form, *,
                             submission_id: str | None = None,
                             edm_id: str | None = None) -> Response:
    """Synchronous request-path cascade (P-19): Risk Modeler delete first, local
    soft delete on success. Validation failures return 422 whose banner text
    app.js surfaces as a toast (htmx:responseError)."""
    analysis_ids = form.getlist("analysis_ids")
    actor_id = request.state.user.id
    try:
        if edm_id is not None:
            outcome = analysis_service.delete_executed_analyses(
                edm_id=edm_id, analysis_ids=analysis_ids, actor_id=actor_id)
        else:
            outcome = analysis_service.delete_submission_analyses(
                submission_id=submission_id, analysis_ids=analysis_ids,
                actor_id=actor_id)
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
