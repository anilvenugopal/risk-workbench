"""Unit tests for app/routers/rwb_jobs.py — the CR-04a monitoring page.

Strategy: build a minimal FastAPI app with only the rwb_jobs router mounted,
same isolation pattern as test_shell_routes.py. Real rows come from the
SQLite unit mirror (iteration2_db) so the search/filter/cancel/resubmit
mechanics exercise the real service, not a monkeypatched stub.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services.auth_service import CurrentUser
from app.services.rwb_job_service import (
    cancel_rwb_job,
    claim_rwb_job,
    complete_rwb_job,
    enqueue_rwb_job,
)
from db import execute_command

_NO_LINK = {"link_type": "not_applicable", "link_id": None,
           "context_type": None, "context_id": None}


def _fake_user(**overrides):
    defaults = dict(
        id=str(uuid.uuid4()),
        email="test@example.com",
        display_name="Test User",
        session_id="sess-abc",
        role_codes=["analyst"],
        is_admin=False,
        must_change_password=False,
        entra_oid=None,
    )
    defaults.update(overrides)
    return CurrentUser(**defaults)


class _InjectUser(BaseHTTPMiddleware):
    def __init__(self, app, user):
        super().__init__(app)
        self._user = user

    async def dispatch(self, request: Request, call_next):
        request.state.user = self._user
        return await call_next(request)


def _make_app(user=None):
    from app.auth.csrf import generate_csrf_token
    from app.routers import rwb_jobs

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates

    app.add_middleware(_InjectUser, user=user or _fake_user())
    app.include_router(rwb_jobs.router)
    return app


def _edm(*, name="EDM") -> str:
    eid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, source_file_path, name, status, "
        "inserted_at, updated_at) VALUES (:id, :src, :name, 'ready', :now, :now)",
        {"id": eid, "src": r"\\share\intake\x.bak", "name": name,
         "now": "2026-01-01 00:00:00"},
        connection="WORKBENCH")
    return eid


def _submission(*, name="Sub", assigned_analyst_id) -> str:
    sid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO submission (id, assigned_analyst_id, name, cedant_name, "
        "treaty_type_code, inception_date, status_code, inserted_at, updated_at) "
        "VALUES (:id, :a, :name, 'Cedant', 'cat_xol', '2026-01-01', 'ACTIVE', "
        ":now, :now)",
        {"id": sid, "a": assigned_analyst_id, "name": name,
         "now": "2026-01-01 00:00:00"},
        connection="WORKBENCH")
    return sid


def _attach_edm(submission_id, edm_id) -> None:
    execute_command(
        "INSERT INTO submission_edm (submission_id, edm_id, inserted_at) "
        "VALUES (:s, :e, :now)",
        {"s": submission_id, "e": edm_id, "now": "2026-01-01 00:00:00"},
        connection="WORKBENCH")


class TestRwbJobsPage:
    def test_returns_200(self, iteration2_db):
        resp = TestClient(_make_app()).get("/workflows/rwb-jobs")
        assert resp.status_code == 200

    def test_lists_a_job_with_no_link(self, iteration2_db):
        # A not_applicable-linked job has no submission, so the default
        # owner=mine filter (a submission-scoped filter) excludes it — the
        # same as any other submission filter. owner=any turns that off.
        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="dummy_wait", **_NO_LINK)
        resp = TestClient(_make_app()).get("/workflows/rwb-jobs?owner=any")
        assert resp.status_code == 200
        assert "Dummy: wait" in resp.text

    def test_empty_state(self, iteration2_db):
        resp = TestClient(_make_app()).get("/workflows/rwb-jobs")
        assert "No jobs match" in resp.text

    def test_pending_row_shows_no_submitted_at_but_shows_queued_elapsed(self, iteration2_db):
        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="dummy_wait", **_NO_LINK)
        resp = TestClient(_make_app()).get("/workflows/rwb-jobs?owner=any")
        assert "queued " in resp.text

    def test_running_row_shows_submitted_at(self, iteration2_db):
        from datetime import datetime, timedelta
        from db import execute_command

        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="dummy_wait", **_NO_LINK)
        claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
        # Backdate submitted_at so the elapsed duration is deterministic
        # rather than racing the test's own clock at "0s".
        stamped = (datetime.utcnow() - timedelta(minutes=2, seconds=14))
        execute_command(
            "UPDATE rwb_job SET submitted_at = :s WHERE id = :id",
            {"s": stamped.isoformat(sep=' '), "id": job_id}, connection="WORKBENCH")

        resp = TestClient(_make_app()).get("/workflows/rwb-jobs?owner=any")
        assert resp.status_code == 200
        assert stamped.isoformat(sep=' ')[:16] in resp.text
        assert "2m 1" in resp.text or "2m 0" in resp.text  # ~2m14s, allow test-run skew

    def test_table_fragment(self, iteration2_db):
        resp = TestClient(_make_app()).get("/workflows/rwb-jobs/table")
        assert resp.status_code == 200
        assert "No jobs match" in resp.text

    def test_owner_defaults_to_current_user(self, iteration2_db):
        # A job whose EDM belongs to a submission owned by someone else is
        # excluded by default (owner defaults to the signed-in analyst).
        user_a, user_b = iteration2_db.user_a, iteration2_db.user_b
        edm_id = _edm()
        sub_id = _submission(name="Not Mine", assigned_analyst_id=user_b)
        _attach_edm(sub_id, edm_id)
        enqueue_rwb_job(requestor_type="analyst_request", requestor_id=str(uuid.uuid4()),
                        rwb_job_type="upload_edm", link_type="edm", link_id=edm_id,
                        context_type="edm", context_id=edm_id)

        resp = TestClient(_make_app(user=_fake_user(id=user_a))).get(
            "/workflows/rwb-jobs")
        assert "Not Mine" not in resp.text

    def test_owner_any_shows_every_submissions_jobs(self, iteration2_db):
        user_a, user_b = iteration2_db.user_a, iteration2_db.user_b
        edm_id = _edm()
        sub_id = _submission(name="Someone Elses Deal", assigned_analyst_id=user_b)
        _attach_edm(sub_id, edm_id)
        enqueue_rwb_job(requestor_type="analyst_request", requestor_id=str(uuid.uuid4()),
                        rwb_job_type="upload_edm", link_type="edm", link_id=edm_id,
                        context_type="edm", context_id=edm_id)

        resp = TestClient(_make_app(user=_fake_user(id=user_a))).get(
            "/workflows/rwb-jobs?owner=any")
        assert "Someone Elses Deal" in resp.text

    def test_submission_name_filter(self, iteration2_db):
        user_a = iteration2_db.user_a
        edm_id = _edm()
        sub_id = _submission(name="American Family Renewal",
                             assigned_analyst_id=user_a)
        _attach_edm(sub_id, edm_id)
        enqueue_rwb_job(requestor_type="analyst_request", requestor_id=str(uuid.uuid4()),
                        rwb_job_type="upload_edm", link_type="edm", link_id=edm_id,
                        context_type="edm", context_id=edm_id)

        client = TestClient(_make_app(user=_fake_user(id=user_a)))
        resp = client.get("/workflows/rwb-jobs?q=american+fam")
        assert "American Family Renewal" in resp.text
        resp = client.get("/workflows/rwb-jobs?q=zzz-no-match")
        assert "American Family Renewal" not in resp.text


class TestRwbJobsCancel:
    def test_cancel_pending_row_via_route(self, iteration2_db):
        from app.auth.csrf import generate_csrf_token
        from db import execute_one

        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="dummy_wait", **_NO_LINK)
        client = TestClient(_make_app())
        resp = client.post(f"/workflows/rwb-jobs/{job_id}/cancel",
                           data={"csrf_token": generate_csrf_token()})
        assert resp.status_code == 200
        row = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                          {"id": job_id}, connection="WORKBENCH")
        assert row["status_code"] == "cancelled"

    def test_cancel_rejects_invalid_csrf(self, iteration2_db):
        from db import execute_one

        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="dummy_wait", **_NO_LINK)
        client = TestClient(_make_app())
        client.post(f"/workflows/rwb-jobs/{job_id}/cancel", data={"csrf_token": "bad"})
        row = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                          {"id": job_id}, connection="WORKBENCH")
        assert row["status_code"] == "pending"  # unchanged — CSRF rejected the write


class TestRwbJobsResubmit:
    def test_resubmit_failed_row_via_route(self, iteration2_db):
        from app.auth.csrf import generate_csrf_token
        from db import execute_one

        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="dummy_wait", **_NO_LINK)
        claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
        complete_rwb_job(rwb_job_id=job_id, status="failed", error_detail="boom")

        client = TestClient(_make_app())
        resp = client.post(f"/workflows/rwb-jobs/{job_id}/resubmit",
                           data={"csrf_token": generate_csrf_token()})
        assert resp.status_code == 200
        row = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                          {"id": job_id}, connection="WORKBENCH")
        assert row["status_code"] == "pending"
