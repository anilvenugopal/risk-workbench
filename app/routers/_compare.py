"""The Compare modal fragment, shared by the submission, contextual-EDM, and
EDM scope routes."""

from __future__ import annotations

from fastapi import Request

from app.services import analysis_service


def compare_modal_response(request: Request, *,
                           submission_id: str | None = None,
                           edm_id: str | None = None):
    """The Compare modal fragment (spec 013 contracts §1) — one handler behind
    the three scope routes, fetched into ``#compare-modal-mount``. Read-only,
    no Risk Modeler call (Article 11)."""
    rows = analysis_service.list_comparable_analyses(
        submission_id=submission_id, edm_id=edm_id)
    if submission_id is not None and edm_id is not None:
        gone_message = "This EDM is no longer related to the submission."
    elif submission_id is not None:
        gone_message = "This submission no longer exists."
    else:
        gone_message = "This EDM no longer exists."
    return request.app.state.templates.TemplateResponse(
        request, "partials/compare_modal.html", {
            "current_user": request.state.user,
            "rows": rows,
            "gone": rows is None,
            "gone_message": gone_message,
            "submission_id": submission_id,
            "edm_id": edm_id,
            "max_pairs": analysis_service.MAX_COMPARISON_PAIRS,
        })
