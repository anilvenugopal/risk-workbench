"""Unit tests for app/routers/irp_jobs.py — the filtered, paged IRP jobs page.

Real rows come from the SQLite unit mirror (iteration2_db), so the filters and
the pager run through the real ``irp_job_service.list_jobs``.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import irp_job_service
from app.services.auth_service import CurrentUser
from db import execute_command


def _fake_user(user_id):
    return CurrentUser(
        id=user_id, email="analyst.a@example.com", display_name="Analyst A",
        session_id="sess-abc", role_codes=["analyst"], is_admin=False,
        must_change_password=False, entra_oid=None)


class _InjectUser(BaseHTTPMiddleware):
    def __init__(self, app, user):
        super().__init__(app)
        self._user = user

    async def dispatch(self, request: Request, call_next):
        request.state.user = self._user
        return await call_next(request)


def _client(user_id) -> TestClient:
    from app.routers import irp_jobs
    from app.templating import TEMPLATE_DIRS

    app = FastAPI()
    templates = Jinja2Templates(directory=TEMPLATE_DIRS)
    templates.env.globals["ui_poll_interval_secs"] = 3
    app.state.templates = templates
    app.add_middleware(_InjectUser, user=_fake_user(user_id))
    app.include_router(irp_jobs.router)
    return TestClient(app)


def _job(name, *, by, job_type="import_edm", status="FINISHED", progress=None,
         submitted_at="2026-09-15 12:00:00", completed_at=None) -> None:
    """One irp_job linked to an EDM named ``name``, so the row shows ``name``."""
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, source_file_path, name, status, inserted_at, updated_at) "
        "VALUES (:id, 'x.bak', :name, 'ready', :at, :at)",
        {"id": edm_id, "name": name, "at": submitted_at}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO irp_job (id, irp_edm_id, irp_job_type, status, progress, "
        "submission_attempt_count, submitted_at, completed_at, inserted_at, "
        "updated_at, inserted_by) VALUES (:id, :edm, :jt, :st, :pr, 1, :sub, :done, "
        ":sub, :sub, :by)",
        {"id": str(uuid.uuid4()), "edm": edm_id, "jt": job_type, "st": status,
         "pr": progress, "sub": submitted_at, "done": completed_at, "by": by},
        connection="WORKBENCH")


def test_defaults_to_the_current_users_jobs(iteration2_db):
    _job("Mine", by=iteration2_db.user_a)
    _job("Theirs", by=iteration2_db.user_b)
    client = _client(iteration2_db.user_a)

    resp = client.get("/workflows/irp-jobs")
    assert "Mine" in resp.text
    assert "Theirs" not in resp.text

    resp = client.get("/workflows/irp-jobs?submitted_by=any")
    assert "Mine" in resp.text
    assert "Theirs" in resp.text
    assert "Analyst B" in resp.text


def test_job_type_and_status_filters(iteration2_db):
    _job("EdmDone", by=iteration2_db.user_a)
    _job("EdmFailed", by=iteration2_db.user_a, status="FAILED")
    _job("ExportDone", by=iteration2_db.user_a, job_type="export")
    client = _client(iteration2_db.user_a)

    resp = client.get("/workflows/irp-jobs?job_type=import_edm&status=FINISHED")
    assert "EdmDone" in resp.text
    assert "EdmFailed" not in resp.text
    assert "ExportDone" not in resp.text

    resp = client.get("/workflows/irp-jobs?status=FINISHED&status=FAILED")
    assert "EdmDone" in resp.text
    assert "EdmFailed" in resp.text


def test_dates_are_local_days_in_the_browsers_zone(iteration2_db):
    # America/Chicago is UTC-5 in September: Sep 10 starts at 05:00 UTC, and
    # Completed by Sep 20 runs up to Sep 21 05:00 UTC.
    by = iteration2_db.user_a
    _job("SubmittedSep9Local", by=by, submitted_at="2026-09-10 04:30:00",
         completed_at="2026-09-12 00:00:00")
    _job("CompletedSep20Local", by=by, submitted_at="2026-09-10 05:30:00",
         completed_at="2026-09-21 04:30:00")
    _job("CompletedSep21Local", by=by, submitted_at="2026-09-10 05:30:00",
         completed_at="2026-09-21 05:30:00")
    _job("StillRunning", by=by, status="RUNNING", submitted_at="2026-09-11 00:00:00")

    resp = _client(by).get("/workflows/irp-jobs?submitted_from=2026-09-10"
                           "&completed_by=2026-09-20&tz=America/Chicago")
    assert "SubmittedSep9Local" not in resp.text
    assert "CompletedSep20Local" in resp.text
    assert "CompletedSep21Local" not in resp.text
    assert "StillRunning" not in resp.text


@pytest.mark.parametrize("tz", ["", "&tz=Nowhere/Atlantis"])
def test_dates_fall_back_to_utc_days(iteration2_db, tz):
    _job("EarlyUtc", by=iteration2_db.user_a, submitted_at="2026-09-10 04:30:00")
    _job("DayBefore", by=iteration2_db.user_a, submitted_at="2026-09-09 23:30:00")

    resp = _client(iteration2_db.user_a).get(
        f"/workflows/irp-jobs?submitted_from=2026-09-10{tz}")
    assert "EarlyUtc" in resp.text
    assert "DayBefore" not in resp.text


@pytest.mark.parametrize("query, message", [
    ("submitted_from=2026-13-01", "Submitted from is not a valid date."),
    ("completed_by=yesterday", "Completed by is not a valid date."),
    ("submitted_from=2026-09-30&completed_by=2026-09-01",
     "Submitted from is later than Completed by."),
])
def test_date_errors_show_a_message_and_no_rows(iteration2_db, query, message):
    _job("AnyJob", by=iteration2_db.user_a, completed_at="2026-09-15 13:00:00")

    resp = _client(iteration2_db.user_a).get(f"/workflows/irp-jobs?{query}")
    assert resp.status_code == 200
    assert message in resp.text
    assert "AnyJob" not in resp.text
    assert "every 3s" not in resp.text


def test_pager_carries_the_filters(iteration2_db, monkeypatch):
    monkeypatch.setattr(irp_job_service, "PAGE_SIZE", 2)
    for day in (11, 12, 13):
        _job(f"Job{day}", by=iteration2_db.user_a, submitted_at=f"2026-09-{day} 12:00:00")
    client = _client(iteration2_db.user_a)

    resp = client.get("/workflows/irp-jobs?status=FINISHED")
    assert "Job13" in resp.text and "Job12" in resp.text
    assert "Job11" not in resp.text
    assert 'href="/workflows/irp-jobs?status=FINISHED&amp;page=2"' in resp.text

    resp = client.get("/workflows/irp-jobs?status=FINISHED&page=2")
    assert "Job11" in resp.text
    assert "Job13" not in resp.text
    assert 'href="/workflows/irp-jobs?status=FINISHED&amp;page=1"' in resp.text
    assert "Next ›" not in resp.text


def test_poll_keeps_the_filters_and_the_page(iteration2_db, monkeypatch):
    monkeypatch.setattr(irp_job_service, "PAGE_SIZE", 1)
    _job("Newer", by=iteration2_db.user_a, status="RUNNING", submitted_at="2026-09-12 12:00:00")
    _job("Older", by=iteration2_db.user_a, status="RUNNING", submitted_at="2026-09-11 12:00:00")

    resp = _client(iteration2_db.user_a).get(
        "/workflows/irp-jobs/table?status=RUNNING&submitted_from=2026-09-01"
        "&tz=America/Chicago&page=2")
    assert "Older" in resp.text
    assert ('hx-get="/workflows/irp-jobs/table?status=RUNNING&amp;submitted_from=2026-09-01'
            '&amp;tz=America%2FChicago&amp;page=2"') in resp.text


def test_tz_is_left_out_of_the_query_without_a_date(iteration2_db):
    _job("Live", by=iteration2_db.user_a, status="QUEUED")

    resp = _client(iteration2_db.user_a).get(
        "/workflows/irp-jobs/table?status=QUEUED&tz=America/Chicago")
    assert 'hx-get="/workflows/irp-jobs/table?status=QUEUED"' in resp.text


def test_polling_stops_once_every_row_on_the_page_is_terminal(iteration2_db):
    _job("Done", by=iteration2_db.user_a, status="FINISHED")
    _job("Gone", by=iteration2_db.user_a, status="SUBMISSION FAILED")

    resp = _client(iteration2_db.user_a).get("/workflows/irp-jobs/table")
    assert "Done" in resp.text
    assert "every 3s" not in resp.text


def test_filtering_returns_the_table_and_pushes_the_url(iteration2_db):
    _job("Mine", by=iteration2_db.user_a)

    resp = _client(iteration2_db.user_a).get(
        "/workflows/irp-jobs?job_type=import_edm&submitted_by=any",
        headers={"HX-Request": "true", "HX-Target": "irp-jobs-live"})
    assert resp.headers["HX-Push-Url"] == (
        "/workflows/irp-jobs?job_type=import_edm&submitted_by=any")
    assert "Mine" in resp.text
    assert "<form" not in resp.text


def test_completed_column_shows_local_time_stamps(iteration2_db):
    _job("Done", by=iteration2_db.user_a, submitted_at="2026-09-15 12:00:00",
         completed_at="2026-09-15 12:07:00")

    resp = _client(iteration2_db.user_a).get("/workflows/irp-jobs")
    assert '<time data-utc="2026-09-15 12:00:00"' in resp.text
    assert '<time data-utc="2026-09-15 12:07:00"' in resp.text


@pytest.mark.parametrize("status, progress, chip", [
    ("RUNNING", 45, ">RUNNING 45%<"),
    ("RUNNING", None, ">RUNNING<"),
    ("QUEUED", 10, ">QUEUED<"),
])
def test_status_chip_shows_progress_only_while_running(iteration2_db, status, progress, chip):
    _job("Job", by=iteration2_db.user_a, status=status, progress=progress)

    resp = _client(iteration2_db.user_a).get("/workflows/irp-jobs/table")
    assert chip in resp.text


@pytest.mark.parametrize("query, message", [
    ("", "You have not submitted any IRP jobs."),
    ("?status=FAILED", "No IRP jobs match these filters."),
    ("?page=3", "Page 3 is past the last page."),
])
def test_empty_states(iteration2_db, query, message):
    _job("Theirs", by=iteration2_db.user_b)

    resp = _client(iteration2_db.user_a).get(f"/workflows/irp-jobs{query}")
    assert message in resp.text
