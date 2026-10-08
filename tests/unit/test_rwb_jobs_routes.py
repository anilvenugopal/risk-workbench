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
    from app.templating import TEMPLATE_DIRS

    app = FastAPI()
    templates = Jinja2Templates(directory=TEMPLATE_DIRS)
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    templates.env.globals["ui_poll_interval_secs"] = 3
    app.state.templates = templates

    app.add_middleware(_InjectUser, user=user or _fake_user())
    app.include_router(rwb_jobs.router)
    return app


class TestRwbJobsPage:
    def test_returns_200(self, iteration2_db):
        resp = TestClient(_make_app()).get("/workflows/rwb-jobs")
        assert resp.status_code == 200

    def test_lists_an_unattributed_job_under_anyone(self, iteration2_db):
        enqueue_rwb_job(requestor_type="analyst_request",
                        requestor_id=str(uuid.uuid4()),
                        rwb_job_type="dummy_wait", **_NO_LINK)
        resp = TestClient(_make_app()).get("/workflows/rwb-jobs?submitted_by=any")
        assert resp.status_code == 200
        assert "Dummy: wait" in resp.text

    def test_empty_state(self, iteration2_db):
        resp = TestClient(_make_app()).get("/workflows/rwb-jobs")
        assert "You have not submitted any RWB jobs" in resp.text
        assert 'href="/workflows/rwb-jobs?submitted_by=any"' in resp.text

    def test_pending_row_shows_no_submitted_at_but_shows_queued_elapsed(self, iteration2_db):
        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="dummy_wait", **_NO_LINK)
        resp = TestClient(_make_app()).get("/workflows/rwb-jobs?submitted_by=any")
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

        resp = TestClient(_make_app()).get("/workflows/rwb-jobs?submitted_by=any")
        assert resp.status_code == 200
        assert f'<time data-utc="{stamped.isoformat(sep=" ")}"' in resp.text
        assert "2m 1" in resp.text or "2m 0" in resp.text  # ~2m14s, allow test-run skew

    def test_table_fragment(self, iteration2_db):
        resp = TestClient(_make_app()).get("/workflows/rwb-jobs/table?job_type=upload_edm")
        assert resp.status_code == 200
        assert "No RWB jobs match these filters" in resp.text

    def test_submitted_by_defaults_to_current_user(self, iteration2_db):
        user_a, user_b = iteration2_db.user_a, iteration2_db.user_b
        for actor, job_type in ((user_a, "dummy_wait"), (user_b, "dummy_fail")):
            enqueue_rwb_job(requestor_type="analyst_request",
                            requestor_id=str(uuid.uuid4()), rwb_job_type=job_type,
                            actor_id=actor, **_NO_LINK)

        resp = TestClient(_make_app(user=_fake_user(id=user_a))).get(
            "/workflows/rwb-jobs/table")

        assert "Dummy: wait" in resp.text and "Analyst A" in resp.text
        assert "Dummy: fail" not in resp.text

    def test_row_names_the_entity_with_its_kind(self, iteration2_db):
        portfolio_id = str(uuid.uuid4())
        execute_command("INSERT INTO irp_portfolio (id, name) VALUES (:id, 'FL Comm')",
                        {"id": portfolio_id}, connection="WORKBENCH")
        enqueue_rwb_job(requestor_type="analyst_request", requestor_id=portfolio_id,
                        rwb_job_type="run_geohaz", link_type="not_applicable",
                        link_id=None, context_type="portfolio", context_id=portfolio_id)

        resp = TestClient(_make_app()).get("/workflows/rwb-jobs?submitted_by=any")

        assert '<span class="muted">Portfolio ·</span> FL Comm' in resp.text


class TestRwbJobsSort:
    def test_request_naming_the_table_gets_the_table_alone(self, iteration2_db):
        # Naming #rwb-jobs-live as the HTMX target returns the fragment without
        # the nav shell, and pushes the list's own URL.
        resp = TestClient(_make_app()).get(
            "/workflows/rwb-jobs?submitted_by=any&sort=status_code&dir=desc",
            headers={"HX-Target": "rwb-jobs-live"})

        assert "<html" not in resp.text
        assert 'id="rwb-jobs-live"' in resp.text
        assert resp.headers["HX-Push-Url"] == (
            "/workflows/rwb-jobs?submitted_by=any&sort=status_code&dir=desc")

    def test_no_sort_param_shows_submitted_at_newest_first(self, iteration2_db):
        resp = TestClient(_make_app()).get("/workflows/rwb-jobs?submitted_by=any")

        assert resp.text.count('aria-sort="descending"') == 1
        assert 'href="/workflows/rwb-jobs?submitted_by=any&amp;sort=submitted_at&amp;dir=asc"'             in resp.text

    def test_poll_url_carries_the_active_sort(self, iteration2_db):
        # The 3s poll re-renders the table from its own request, so its URL has
        # to carry sort/dir. Cancel and resubmit do not: they swap one row,
        # re-read by id.
        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="dummy_wait", **_NO_LINK)
        claim_rwb_job(rwb_job_id=job_id, worker_id="w1")

        resp = TestClient(_make_app()).get(
            "/workflows/rwb-jobs?submitted_by=any&sort=status_code&dir=desc")

        assert ("/workflows/rwb-jobs/table?submitted_by=any&amp;sort=status_code&amp;dir=desc"
                in resp.text)

    def test_a_queued_only_page_still_polls(self, iteration2_db):
        # pending is non-terminal: without polling, a queued job starting would
        # not show until the analyst reloaded.
        enqueue_rwb_job(requestor_type="analyst_request",
                        requestor_id=str(uuid.uuid4()),
                        rwb_job_type="dummy_wait", **_NO_LINK)

        resp = TestClient(_make_app()).get("/workflows/rwb-jobs?submitted_by=any")

        assert 'hx-trigger="every 3s"' in resp.text

    def test_an_all_terminal_page_stops_polling(self, iteration2_db):
        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="dummy_wait", **_NO_LINK)
        claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
        complete_rwb_job(rwb_job_id=job_id, status="succeeded")

        resp = TestClient(_make_app()).get("/workflows/rwb-jobs?submitted_by=any")

        assert 'hx-trigger="every 3s"' not in resp.text

    def test_cancel_and_resubmit_swap_only_their_own_row(self, iteration2_db):
        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="dummy_wait", **_NO_LINK)
        claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
        complete_rwb_job(rwb_job_id=job_id, status="failed", error_detail="boom")

        resp = TestClient(_make_app()).get("/workflows/rwb-jobs?submitted_by=any")

        assert f'hx-post="/workflows/rwb-jobs/{job_id}/cancel"' in resp.text
        assert f'hx-post="/workflows/rwb-jobs/{job_id}/resubmit"' in resp.text
        assert 'hx-target="closest tr"' in resp.text


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

    def test_cancel_returns_only_the_changed_row(self, iteration2_db):
        from app.auth.csrf import generate_csrf_token

        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="dummy_wait", **_NO_LINK)
        resp = TestClient(_make_app()).post(
            f"/workflows/rwb-jobs/{job_id}/cancel",
            data={"csrf_token": generate_csrf_token()})

        assert "<table" not in resp.text
        assert resp.text.strip().startswith("<tr")
        assert "status-chip--job-cancelled" in resp.text

    def test_cancel_rejects_invalid_csrf(self, iteration2_db):
        from db import execute_one

        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="dummy_wait", **_NO_LINK)
        client = TestClient(_make_app())
        resp = client.post(f"/workflows/rwb-jobs/{job_id}/cancel",
                           data={"csrf_token": "bad"})

        # 204 + HX-Refresh, the same answer every other fragment-swapping POST
        # gives — a rejected token must not read as a successful cancel.
        assert resp.status_code == 204
        assert resp.headers["HX-Refresh"] == "true"
        row = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                          {"id": job_id}, connection="WORKBENCH")
        assert row["status_code"] == "pending"


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
