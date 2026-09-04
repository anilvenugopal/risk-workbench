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
from fastapi.responses import HTMLResponse

from app.auth.csrf import validate_csrf_token
from app.nav import get_nav_context
from app.services import rwb_job_service, submission_service
from app.services._common import _utcnow
from db import execute

router = APIRouter()

_NAV_KEY = "workflows.rwb_jobs"
_SORT_COLUMNS = ("rwb_job_type", "entity_name", "submission", "status_code",
                 "submitted_at", "elapsed")
_DEFAULT_SORT = "status_code"


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


def _active_analysts() -> list[dict]:
    """Every active user, for the Owner filter — mirrors
    ``submissions.py``'s own ``_active_analysts``; not shared cross-module
    since each is a one-line query private to its own router."""
    return execute(
        "SELECT id, display_name FROM app_user WHERE is_active = 1 "
        "ORDER BY display_name",
        {}, connection="WORKBENCH",
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


def _context(request: Request) -> dict:
    """Shared context for the full page and its polled/filtered table fragment."""
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
    links = [(r["link_type"], r["link_id"]) for r in rows if r["link_id"] is not None]
    submissions_by_link = rwb_job_service.list_submissions_for_rwb_jobs(links)
    type_labels = dict(rwb_job_service.job_type_kinds())
    status_labels = dict(rwb_job_service.status_kinds())
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

    sort = request.query_params.get("sort", "")
    if sort not in _SORT_COLUMNS:
        sort = _DEFAULT_SORT
    descending = request.query_params.get("dir", "") == "desc"
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
    return {
        "rows": rows,
        "filter_values": filter_values,
        "filter_query": urlencode(query_values),
        "submission_statuses": submission_service.status_kinds(),
        "job_types": rwb_job_service.job_type_kinds(),
        "job_statuses": rwb_job_service.status_kinds(),
        "owner_options": [(a["id"], a["display_name"]) for a in _active_analysts()],
        "sort": sort,
        "descending": descending,
        # Any row still running (dead or alive) → keep polling; once every
        # row is pending/terminal the fragment stops emitting the trigger.
        "live": any(r["status_code"] == "running" for r in rows),
    }


@router.get("/workflows/rwb-jobs", response_class=HTMLResponse)
def rwb_jobs_page(request: Request):
    return _render(request, "pages/workflows_rwb_jobs.html", _context(request))


@router.get("/workflows/rwb-jobs/table", response_class=HTMLResponse)
def rwb_jobs_table(request: Request):
    return _partial(request, "partials/rwb_jobs_table.html", _context(request))


@router.post("/workflows/rwb-jobs/{rwb_job_id}/cancel", response_class=HTMLResponse)
def rwb_jobs_cancel(
    request: Request, rwb_job_id: str, csrf_token: Annotated[str, Form()],
):
    if validate_csrf_token(csrf_token):
        rwb_job_service.cancel_rwb_job(rwb_job_id=rwb_job_id)
    return _partial(request, "partials/rwb_jobs_table.html", _context(request))


@router.post("/workflows/rwb-jobs/{rwb_job_id}/resubmit", response_class=HTMLResponse)
def rwb_jobs_resubmit(
    request: Request, rwb_job_id: str, csrf_token: Annotated[str, Form()],
):
    if validate_csrf_token(csrf_token):
        rwb_job_service.resubmit_rwb_job(rwb_job_id=rwb_job_id)
    return _partial(request, "partials/rwb_jobs_table.html", _context(request))
