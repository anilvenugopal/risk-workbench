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


def open_rdm_ids(request: Request, groups: list, submission_id,
                 sort: str, descending: bool) -> set[str]:
    """The ids of ``groups`` the browser's ``X-Open-Rdms`` header names as open,
    each group's ``analyses`` filled in that sort. The section renders those
    groups open with their rows: a group rendered closed and reopened after the
    swap would collapse, then refetch its rows."""
    named = {rdm_id.strip().lower()
             for rdm_id in request.headers.get("X-Open-Rdms", "").split(",")}
    opened = set()
    for group in groups:
        if group.rdm_id not in named:
            continue
        analyses = analysis_service.list_submission_rdm_analyses(
            submission_id=submission_id, rdm_id=group.rdm_id)
        if analyses is None:
            continue
        group.analyses = analysis_service.sort_broker_analyses(
            analyses, sort, descending)
        opened.add(group.rdm_id)
    return opened


def analysis_rows_response(request: Request, ctx: dict, analyses_hash: str,
                           live: str) -> Response:
    """The tracked rows, each swapped in place, while the section's set of rows
    is unchanged; the whole section once it has changed."""
    ctx = {"current_user": request.state.user, **ctx}
    templates = request.app.state.templates
    if analyses_hash != analysis_service.analyses_hash(ctx["analyses"],
                                                       ctx["groups"]):
        if ctx["source_submission"]:
            ctx["open_rdm_ids"] = open_rdm_ids(
                request, ctx["groups"], ctx["source_submission"].id,
                ctx["sort"], ctx["sort_desc"])
        return retarget_section(
            templates.TemplateResponse(
                request, "partials/analyses_merged_section.html", ctx),
            ctx["section_id"])
    tracked = set(live.split(","))
    rows = [a for a in ctx["analyses"] if str(a.id) in tracked]
    return templates.TemplateResponse(
        request, "partials/analyses_rows_poll.html", {**ctx, "rows": rows})
