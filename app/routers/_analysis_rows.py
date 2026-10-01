"""The Analyses section's rows POST, shared by the EDM, contextual-EDM, and
submission scope routes. It writes nothing, so it takes no CSRF token."""

from __future__ import annotations

from fastapi import Request, Response

from app.services import analysis_service


def retarget_section(response: Response, section_id: str) -> Response:
    """Makes the poller's response replace the whole section."""
    response.headers["HX-Retarget"] = f"#{section_id}"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


def analysis_rows_response(request: Request, ctx: dict, analyses_hash: str,
                           live: str) -> Response:
    """The tracked rows, each swapped in place, while the section's set of rows
    is unchanged; the whole section once it has changed."""
    ctx = {"current_user": request.state.user, **ctx}
    templates = request.app.state.templates
    if analyses_hash != analysis_service.analyses_hash(ctx["analyses"],
                                                       ctx["groups"]):
        return retarget_section(
            templates.TemplateResponse(
                request, "partials/analyses_merged_section.html", ctx),
            ctx["section_id"])
    tracked = set(live.split(","))
    rows = [a for a in ctx["analyses"] if str(a.id) in tracked]
    return templates.TemplateResponse(
        request, "partials/analyses_rows_poll.html", {**ctx, "rows": rows})
