"""RWB Jobs monitoring page — list, search, cancel, resubmit (CR-04a).

Server-rendered FastAPI + Jinja2 + HTMX (Article 8). No row scoping (Article
6): every analyst may see and act on every job; Submitted by narrows by
``rwb_job.inserted_by`` as a plain predicate and defaults to the current
analyst, the way ``/workflows/irp-jobs`` does.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from app.auth.csrf import validate_csrf_token
from app.nav import get_nav_context
from app.services import auth_service, rwb_job_service
from app.services._common import _as_datetime, _format_duration, _utcnow
from app.services.submission_filters import _as_uuid

router = APIRouter()

_NAV_KEY = "workflows.rwb_jobs"
# The table fragment's element id; a request naming it as its HTMX target gets
# the table alone, skipping the picker reads htmx would only discard.
_LIST_TARGET = "rwb-jobs-live"
# Direction a column starts in on its first click: text up, time down.
_SORT_STARTS_DESCENDING = {
    "rwb_job_type": False, "entity_name": False, "submitted_by": False,
    "status_code": False, "submitted_at": True,
}


def _elapsed_seconds(row: dict, *, now: datetime) -> float | None:
    """Seconds elapsed for display/sort — ``None`` when there's nothing to
    show (a terminal row with no ``submitted_at``, i.e. it failed/succeeded
    before ever being claimed, which the dummy/metadata job types can do).
    ``pending``: since ``inserted_at`` (queued). ``running``/dead: since
    ``submitted_at`` (claimed). Terminal: ``submitted_at`` → ``completed_at``,
    a fixed span rather than one that keeps growing."""
    if row["status_code"] == "pending":
        inserted_at = _as_datetime(row["inserted_at"])
        return (now - inserted_at).total_seconds() if inserted_at else None
    submitted_at = _as_datetime(row["submitted_at"])
    if row["status_code"] == "running":
        return (now - submitted_at).total_seconds() if submitted_at else None
    completed_at = _as_datetime(row["completed_at"])
    if submitted_at and completed_at:
        return (completed_at - submitted_at).total_seconds()
    return None


def _templates(request: Request):
    return request.app.state.templates


def _render(request: Request, template: str, extra: dict, status_code: int = 200):
    current_user = request.state.user
    nav = get_nav_context(current_user, _NAV_KEY)
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


def _sort_links(filter_query: str, sort: str, descending: bool) -> dict[str, dict]:
    """One link per sortable header cell (D15). Clicking the sorted column flips
    its direction; each link carries the filters, so sorting never drops them."""
    stem = "/workflows/rwb-jobs?" + (f"{filter_query}&" if filter_query else "")
    links = {}
    for key in _SORT_STARTS_DESCENDING:
        active = key == sort
        next_descending = (not descending if active
                           else _SORT_STARTS_DESCENDING[key])
        links[key] = {
            "href": f"{stem}sort={key}&dir={'desc' if next_descending else 'asc'}",
            "active": active,
            "aria": ("descending" if descending else "ascending") if active else "none",
            "caret": ("▼" if descending else "▲") if active else "",
        }
    return links


def _decorate(rows: list[dict], *, type_labels: dict, status_labels: dict) -> None:
    """Add the display columns ``partials/rwb_jobs_row.html`` reads. Elapsed time
    is computed here rather than in SQL because it moves on every render."""
    now = _utcnow()
    for row in rows:
        row["type_label"] = type_labels.get(row["rwb_job_type"], row["rwb_job_type"])
        row["status_label"] = (
            status_labels.get("dead") if row["is_dead"]
            else status_labels.get(row["status_code"], row["status_code"]))
        row["elapsed_seconds"] = _elapsed_seconds(row, now=now)
        row["elapsed"] = (
            _format_duration(row["elapsed_seconds"])
            if row["elapsed_seconds"] is not None else None)


def _list_context(request: Request) -> dict:
    """Everything ``partials/rwb_jobs_table.html`` reads. The page route adds the
    Submitted by picker on top; the poll does not."""
    params = request.query_params
    rwb_job_types = [v.strip() for v in params.getlist("job_type") if v.strip()]
    status_codes = [v.strip() for v in params.getlist("job_status") if v.strip()]
    by_params = [v.strip() for v in params.getlist("submitted_by")
                 if v.strip() == "any" or _as_uuid(v) == v.strip().lower()]
    submitted_by = ([str(request.state.user.id)] if not by_params
                    else [] if "any" in by_params else by_params)
    # A URL without a sort param is newest first, Submitted at's own order.
    sort = params.get("sort", "")
    explicit_sort = sort in _SORT_STARTS_DESCENDING
    if not explicit_sort:
        sort = "submitted_at"
    descending = {"asc": False, "desc": True}.get(
        params.get("dir", ""), _SORT_STARTS_DESCENDING[sort])

    rows = rwb_job_service.list_rwb_jobs_for_monitoring(
        submitted_by=submitted_by or None,
        rwb_job_types=rwb_job_types or None,
        status_codes=status_codes or None,
        sort=sort, descending=descending,
    )
    # Read for the row labels; the job-type and job-status pickers reuse them.
    job_types = rwb_job_service.job_type_kinds()
    job_statuses = rwb_job_service.status_kinds()
    _decorate(rows, type_labels=dict(job_types), status_labels=dict(job_statuses))

    filter_values = {
        "submitted_by": submitted_by or ["any"],
        "job_type": rwb_job_types,
        "job_status": status_codes,
    }
    # The filters alone (never sort/dir) — each sortable header appends its
    # own sort=/dir= to this, so clicking a header never drops a filter.
    query_values = [(key, v) for key in ("job_type", "job_status")
                    for v in filter_values[key]]
    if by_params:
        query_values += [("submitted_by", v) for v in filter_values["submitted_by"]]
    order_values = ([("sort", sort), ("dir", "desc" if descending else "asc")]
                    if explicit_sort else [])
    filter_query = urlencode(query_values)
    return {
        "rows": rows,
        "filter_values": filter_values,
        "job_types": job_types,
        "job_statuses": job_statuses,
        "sort_links": _sort_links(filter_query, sort, descending),
        # Only the landing view (no query at all) gets the "you have no jobs" message.
        "is_default_view": not query_values,
        # Filters plus the sort in force, for the poll to re-render what the
        # analyst is actually looking at.
        "list_query": urlencode(query_values + order_values),
        # Any row not yet terminal keeps the poll trigger in the fragment.
        "live": any(r["status_code"] in ("pending", "running") for r in rows),
    }


def _row_response(request: Request, rwb_job_id: str):
    """The changed row's partial. A guarded update that matched nothing re-reads
    the row as it now stands rather than reporting an error."""
    rows = rwb_job_service.list_rwb_jobs_for_monitoring(rwb_job_ids=[rwb_job_id])
    if not rows:
        return Response(status_code=404)
    _decorate(rows, type_labels=dict(rwb_job_service.job_type_kinds()),
              status_labels=dict(rwb_job_service.status_kinds()))
    return _partial(request, "partials/rwb_jobs_row.html", {"row": rows[0]})


@router.get("/workflows/rwb-jobs", response_class=HTMLResponse)
def rwb_jobs_page(request: Request):
    list_ctx = _list_context(request)
    if request.headers.get("HX-Target") == _LIST_TARGET:
        response = _partial(request, "partials/rwb_jobs_table.html", list_ctx)
        query = list_ctx["list_query"]
        response.headers["HX-Push-Url"] = (
            "/workflows/rwb-jobs" + (f"?{query}" if query else ""))
        return response
    return _render(request, "pages/workflows_rwb_jobs.html", {
        **list_ctx,
        "analysts": [(a["id"], a["display_name"])
                     for a in auth_service.list_active_analysts()],
    })


@router.get("/workflows/rwb-jobs/table", response_class=HTMLResponse)
def rwb_jobs_table(request: Request):
    return _partial(request, "partials/rwb_jobs_table.html", _list_context(request))


@router.post("/workflows/rwb-jobs/{rwb_job_id}/cancel", response_class=HTMLResponse)
def rwb_jobs_cancel(
    request: Request, rwb_job_id: str, csrf_token: Annotated[str, Form()],
):
    if not validate_csrf_token(csrf_token):
        return Response(status_code=204, headers={"HX-Refresh": "true"})
    rwb_job_service.cancel_rwb_job(rwb_job_id=rwb_job_id)
    return _row_response(request, rwb_job_id)


@router.post("/workflows/rwb-jobs/{rwb_job_id}/resubmit", response_class=HTMLResponse)
def rwb_jobs_resubmit(
    request: Request, rwb_job_id: str, csrf_token: Annotated[str, Form()],
):
    if not validate_csrf_token(csrf_token):
        return Response(status_code=204, headers={"HX-Refresh": "true"})
    rwb_job_service.resubmit_rwb_job(rwb_job_id=rwb_job_id)
    return _row_response(request, rwb_job_id)
