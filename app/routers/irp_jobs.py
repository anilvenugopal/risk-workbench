"""IRP Jobs monitoring page — the read-only, filtered, paged ``irp_job`` list.

Display only (Article 11): no route here calls Risk Modeler. No row scoping
(Article 6): Submitted by narrows by ``irp_job.inserted_by`` as a plain
predicate and defaults to the current analyst, the way ``/workflows/rwb-jobs``
does.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.nav import get_nav_context
from app.routers._list_filters import submitted_by_filter
from app.services import auth_service, irp_job_service
from app.services._common import _parse_int

router = APIRouter()

_NAV_KEY = "workflows.irp_jobs"
# The table fragment's element id; a request naming it as its HTMX target gets
# the table alone, skipping the picker reads htmx would only discard.
_LIST_TARGET = "irp-jobs-live"


def _templates(request: Request):
    return request.app.state.templates


def _zone(name: str):
    """The browser's IANA zone, or UTC when it is missing or unknown."""
    try:
        return ZoneInfo(name)
    except (ValueError, OSError, ZoneInfoNotFoundError):
        return UTC


def _utc_midnight(day: date, zone) -> datetime:
    """Local midnight at the start of ``day`` as naive UTC, the form
    ``irp_job`` stores."""
    return (datetime.combine(day, time.min, tzinfo=zone)
            .astimezone(UTC).replace(tzinfo=None))


def _list_context(request: Request) -> dict:
    """Everything ``partials/irp_jobs_table.html`` reads. The page route adds the
    pickers on top; the poll does not."""
    params = request.query_params
    job_types = [v.strip() for v in params.getlist("job_type") if v.strip()]
    statuses = [v.strip() for v in params.getlist("status") if v.strip()]
    submitted_by, by_params = submitted_by_filter(params, request.state.user.id)
    texts = {key: (params.get(key) or "").strip()
             for key in ("submitted_from", "completed_by")}
    tz = (params.get("tz") or "").strip()
    zone = _zone(tz)
    page = max(1, _parse_int(params.get("page")) or 1)

    error = None
    from_day = by_day = from_bound = by_bound = None
    try:
        if texts["submitted_from"]:
            from_day = date.fromisoformat(texts["submitted_from"])
            from_bound = _utc_midnight(from_day, zone)
    except (ValueError, OverflowError):
        error = "Submitted from is not a valid date."
    try:
        if texts["completed_by"]:
            by_day = date.fromisoformat(texts["completed_by"])
            # Completed by is inclusive, so its bound is the next local midnight.
            by_bound = _utc_midnight(by_day + timedelta(days=1), zone)
    except (ValueError, OverflowError):
        error = error or "Completed by is not a valid date."
    if error is None and from_day and by_day and from_day > by_day:
        error = "Submitted from is later than Completed by."

    rows, has_next = [], False
    if error is None:
        rows, has_next = irp_job_service.list_jobs(
            job_types=job_types, statuses=statuses, submitted_by=submitted_by,
            submitted_from=from_bound, completed_before=by_bound, page=page)

    filter_values = {"job_type": job_types, "status": statuses,
                     "submitted_by": submitted_by or ["any"], **texts}
    query_values = [(key, v) for key in ("job_type", "status") for v in filter_values[key]]
    if by_params:
        query_values += [("submitted_by", v) for v in filter_values["submitted_by"]]
    query_values += [(key, text) for key, text in texts.items() if text]
    if tz and any(texts.values()):
        query_values.append(("tz", tz))
    filter_query = urlencode(query_values)
    return {
        "rows": rows,
        "page": page,
        "has_next": has_next,
        "date_error": error,
        "filter_values": filter_values,
        "status_chips": irp_job_service.STATUS_CHIPS,
        # Only the landing view (no query at all) gets the "you have no jobs" message.
        "is_default_view": not query_values,
        "filter_query": filter_query,
        # Filters plus the page, for the poll to re-render what the analyst sees.
        "list_query": urlencode(query_values + ([("page", page)] if page > 1 else [])),
        # Any row still moving at Risk Modeler keeps the poll trigger in the fragment.
        "live": any(r["status"] not in irp_job_service.TERMINAL for r in rows),
    }


def _partial(request: Request, ctx: dict):
    return _templates(request).TemplateResponse(
        request, "partials/irp_jobs_table.html",
        {"current_user": request.state.user, **ctx})


@router.get("/workflows/irp-jobs", response_class=HTMLResponse)
def irp_jobs_page(request: Request):
    list_ctx = _list_context(request)
    if request.headers.get("HX-Target") == _LIST_TARGET:
        response = _partial(request, list_ctx)
        query = list_ctx["list_query"]
        response.headers["HX-Push-Url"] = (
            "/workflows/irp-jobs" + (f"?{query}" if query else ""))
        return response
    current_user = request.state.user
    return _templates(request).TemplateResponse(
        request, "pages/workflows_irp_jobs.html", {
            "current_user": current_user,
            "nav": get_nav_context(current_user, _NAV_KEY),
            **list_ctx,
            "job_types": irp_job_service.job_type_kinds(),
            "statuses": [(s, s) for s in irp_job_service.STATUS_CHIPS],
            "analysts": [(a["id"], a["display_name"])
                         for a in auth_service.list_active_analysts()],
        })


@router.get("/workflows/irp-jobs/table", response_class=HTMLResponse)
def irp_jobs_table(request: Request):
    return _partial(request, _list_context(request))
