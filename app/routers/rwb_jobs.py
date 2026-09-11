"""RWB Jobs monitoring page — list, search, cancel, resubmit (CR-04a).

Server-rendered FastAPI + Jinja2 + HTMX (Article 8). No row scoping (Article
6): every analyst may see and act on every job; the owner filter narrows by
submission ownership as a plain predicate, not an access gate, and defaults to
the current analyst the same way ``/submissions`` does.

Search reaches submission through each job's own ``link_type``/``link_id``
(CR-04c), never ``requestor_type``/``requestor_id`` — see
``rwb_job_service.list_rwb_jobs_for_monitoring``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from app.auth.csrf import validate_csrf_token
from app.nav import get_nav_context
from app.services import auth_service, rwb_job_service, submission_service
from app.services._common import _utcnow

router = APIRouter()

_NAV_KEY = "workflows.rwb_jobs"
# The table fragment's element id; a request naming it as its HTMX target gets
# the table alone, skipping the picker reads htmx would only discard.
_LIST_TARGET = "rwb-jobs-live"
_SORT_COLUMNS = ("rwb_job_type", "entity_name", "submission", "status_code",
                 "submitted_at", "elapsed")
# Direction a column starts in on its first click: text up, time/duration down.
_SORT_STARTS_DESCENDING = {
    "rwb_job_type": False, "entity_name": False, "submission": False,
    "status_code": False, "submitted_at": True, "elapsed": True,
}


def _as_datetime(value) -> datetime | None:
    """Normalize a timestamp column to a real ``datetime`` for arithmetic.
    SQL Server's driver returns native ``datetime`` objects; the SQLite unit
    tier only registers a write-side adapter (``tests/conftest.py``), so a
    read comes back as its ISO string verbatim — parse it here rather than
    doing date math on two different types depending on which tier is live."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _format_duration(seconds: float) -> str:
    """``"2m 14s"`` / ``"41s"`` — the smallest two units that matter; a job
    still queued or running never needs day/hour precision to be useful."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


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


def _sort_key(sort: str):
    """A key function over rows from ``list_rwb_jobs_for_monitoring`` for the
    display-only column sort (D15) — applied in Python after the SQL query,
    since the sort columns include computed/joined values (``entity_name``,
    the submissions list, elapsed time) the SQL ``ORDER BY`` doesn't carry."""
    def key(row: dict):
        if sort == "elapsed":
            # Sorts by the underlying duration in seconds, not the formatted
            # string — "2m 14s" vs "41s" would otherwise sort alphabetically.
            return row.get("elapsed_seconds") or 0
        if sort == "submitted_at":
            return _as_datetime(row.get("submitted_at")) or datetime.min
        if sort == "entity_name":
            return (row.get("entity_name") or "").lower()
        if sort == "submission":
            names = row.get("submissions") or []
            return names[0]["name"].lower() if names else ""
        return row.get(sort) or ""
    return key


def _sort_links(filter_query: str, sort: str, descending: bool) -> dict[str, dict]:
    """One link per sortable header cell (D15). Clicking the sorted column flips
    its direction; each link carries the filters, so sorting never drops them."""
    stem = "/workflows/rwb-jobs?" + (f"{filter_query}&" if filter_query else "")
    links = {}
    for key in _SORT_COLUMNS:
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
    links = [(r["link_type"], r["link_id"]) for r in rows if r["link_id"] is not None]
    submissions_by_link = rwb_job_service.list_submissions_for_rwb_jobs(links)
    now = _utcnow()
    for row in rows:
        key = (row["link_type"], str(row["link_id"])) if row["link_id"] is not None else None
        row["submissions"] = submissions_by_link.get(key, []) if key else []
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
    owner and submission-status pickers on top; the 3-second poll does not."""
    current_user = request.state.user
    submission_name = (request.query_params.get("q") or "").strip() or None
    submission_status_codes = [
        v.strip() for v in request.query_params.getlist("submission_status") if v.strip()]
    rwb_job_types = [
        v.strip() for v in request.query_params.getlist("job_type") if v.strip()]
    status_codes = [
        v.strip() for v in request.query_params.getlist("job_status") if v.strip()]
    owner_params = request.query_params.getlist("owner")
    owner_ids = ([str(current_user.id)] if not owner_params
                 else [] if "any" in owner_params else owner_params)

    rows = rwb_job_service.list_rwb_jobs_for_monitoring(
        submission_name=submission_name,
        submission_status_codes=submission_status_codes or None,
        owner_ids=owner_ids or None,
        rwb_job_types=rwb_job_types or None,
        status_codes=status_codes or None,
    )
    # Read for the row labels; the job-type and job-status pickers reuse them.
    job_types = rwb_job_service.job_type_kinds()
    job_statuses = rwb_job_service.status_kinds()
    _decorate(rows, type_labels=dict(job_types), status_labels=dict(job_statuses))

    sort = request.query_params.get("sort", "")
    if sort not in _SORT_COLUMNS:
        sort = ""
    descending = {"asc": False, "desc": True}.get(
        request.query_params.get("dir", ""),
        _SORT_STARTS_DESCENDING[sort] if sort else False)
    # No explicit sort leaves the query's own ORDER BY in place: rows grouped by
    # job type, then status, then most recent (contracts/job-monitoring-routes.md).
    if sort:
        rows.sort(key=_sort_key(sort), reverse=descending)

    filter_values = {
        "q": request.query_params.get("q", ""),
        "submission_status": submission_status_codes,
        "owner": owner_ids or ["any"],
        "job_type": rwb_job_types,
        "job_status": status_codes,
    }
    # The filters alone (never sort/dir) — each sortable header appends its
    # own sort=/dir= to this, so clicking a header never drops what's typed.
    query_values: list[tuple[str, str]] = []
    if filter_values["q"]:
        query_values.append(("q", filter_values["q"]))
    for key in ("submission_status", "owner", "job_type", "job_status"):
        query_values += [(key, v) for v in filter_values[key]]
    order_values = ([("sort", sort), ("dir", "desc" if descending else "asc")]
                    if sort else [])
    filter_query = urlencode(query_values)
    return {
        "rows": rows,
        "filter_values": filter_values,
        "job_types": job_types,
        "job_statuses": job_statuses,
        "sort_links": _sort_links(filter_query, sort, descending),
        # Filters plus the sort in force, for the poll to re-render what the
        # analyst is actually looking at.
        "list_query": urlencode(query_values + order_values),
        # Any row not yet terminal keeps the 3s trigger in the fragment.
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
        "submission_statuses": submission_service.status_kinds(),
        "owner_options": [(a["id"], a["display_name"])
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
