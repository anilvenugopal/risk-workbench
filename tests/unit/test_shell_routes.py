"""Unit tests for app/routers/shell.py.

Strategy: build a minimal FastAPI app with only the shell router mounted.
A lightweight middleware stamps request.state.user with a fake CurrentUser so
the session gate is bypassed. db.execute is monkeypatched to return stub data.
Real Jinja2 templates are used (they live on disk) so the render path is real.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services.auth_service import CurrentUser


def _fake_user(**overrides):
    defaults = dict(
        user_id="test-user-id",
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
    from app.config import settings
    from app.routers import shell
    from app.services import analysis_service

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    templates.env.globals["default_perspective"] = (
        analysis_service.DEFAULT_PERSPECTIVE)
    templates.env.globals["default_perspective_label"] = (
        analysis_service.DEFAULT_PERSPECTIVE_LABEL)
    app.state.templates = templates

    app.add_middleware(_InjectUser, user=user or _fake_user())
    app.include_router(shell.router)
    return app


class TestHomeRoute:
    def test_returns_200(self):
        resp = TestClient(_make_app()).get("/")
        assert resp.status_code == 200

    def test_html_response(self):
        resp = TestClient(_make_app()).get("/")
        assert "text/html" in resp.headers["content-type"]

    def test_greeting_in_page(self):
        resp = TestClient(_make_app()).get("/")
        assert "Test User" in resp.text


class TestSimpleShellRoutes:
    """Routes that need no DB calls — just render a template."""

    @pytest.fixture
    def client(self):
        return TestClient(_make_app())

    # NOTE: /submissions moved from the shell stub to the DB-backed submissions
    # router (Iteration 1, T030) and is no longer a shell route. Its coverage
    # lives in the submission service tests + CSRF audit.

    def test_workflows(self, client):
        assert client.get("/workflows").status_code == 200

    def test_results(self, client):
        assert client.get("/results").status_code == 200

    def test_account_page(self, client):
        assert client.get("/account").status_code == 200

    def test_workflows_active(self, client):
        assert client.get("/workflows/active").status_code == 200

    def test_workflows_review(self, client):
        assert client.get("/workflows/review").status_code == 200

    def test_workflows_rwb_jobs(self, client):
        assert client.get("/workflows/rwb-jobs").status_code == 200

    def test_workflows_exceptions(self, client):
        assert client.get("/workflows/exceptions").status_code == 200


class TestWorkflowsIrpJobs:
    """The job monitor (T-12) reads real irp_job rows — irp_job_service.list_recent
    is monkeypatched here so the route test stays DB-free."""

    def test_lists_rows(self, monkeypatch):
        from app.services import irp_job_service

        monkeypatch.setattr(irp_job_service, "list_recent", lambda: [{
            "id": "1", "irp_job_type": "analysis", "type_label": "Analysis",
            "status": "SUBMISSION FAILED", "attempts": 1,
            "submitted_at": "2026-08-21 10:00:00", "submitted_by": "Test User",
            "entity_name": "Portfolio A DLM",
        }])
        resp = TestClient(_make_app()).get("/workflows/irp-jobs")
        assert resp.status_code == 200
        assert "Portfolio A DLM" in resp.text

    def test_empty_state(self, monkeypatch):
        from app.services import irp_job_service

        monkeypatch.setattr(irp_job_service, "list_recent", lambda: [])
        resp = TestClient(_make_app()).get("/workflows/irp-jobs")
        assert resp.status_code == 200
        assert "No IRP jobs yet." in resp.text

    def test_table_fragment(self, monkeypatch):
        from app.services import irp_job_service

        monkeypatch.setattr(irp_job_service, "list_recent", lambda: [])
        resp = TestClient(_make_app()).get("/workflows/irp-jobs/table")
        assert resp.status_code == 200
        assert "No IRP jobs yet." in resp.text


class TestShellNavContext:
    """Verify nav context is rendered into the shell."""

    def test_display_name_in_page(self):
        user = _fake_user(display_name="Alice Smith")
        resp = TestClient(_make_app(user=user)).get("/")
        assert "Alice Smith" in resp.text


# ── spec 011 US4: the dedicated results page (T033/T034/T036) ─────────────────

SUB_ID = "11111111-1111-1111-1111-111111111111"
EDM_ID = "22222222-2222-2222-2222-222222222222"
AN_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
AN_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _perspectives():
    return [{"code": "GR", "label": "Gross"},
            {"code": "RL", "label": "Pre-Cat Net"},
            {"code": "WX", "label": "Working Excess"},
            {"code": "QS", "label": "Quota Share"},
            {"code": "GU", "label": "Ground Up"}]


def _column(analysis_id, name, *, produced=("RL",), state="ready", error=None):
    from app.services import analysis_service
    from app.services.analysis_service import PerspectiveResults, ResultsColumn

    labels = analysis_service.expanded_return_periods()
    results = []
    if state == "ready":
        for p in _perspectives():
            if p["code"] in produced:
                rows = [{"rp": rp, "oep": 1_000_000.0, "aep": 1_100_000.0,
                         "oep_display": "1.0M", "aep_display": "1.1M"}
                        for rp in labels]
                results.append(PerspectiveResults(
                    code=p["code"], label=p["label"], produced=True,
                    aal=4_100_000.0, std_dev=14_900_000.0, rows=rows))
            else:
                results.append(PerspectiveResults(
                    code=p["code"], label=p["label"], produced=False))
    return ResultsColumn(id=analysis_id, name=name, currency="USD",
                         results_state=state, results_error=error,
                         results=results)


class TestResultsAnalysesPage:
    """Route behavior only — the read model is faked; its own coverage lives
    in test_analysis_service.py."""

    def _client(self, monkeypatch, columns_by_id, *, submission_name=None,
                edm_name=None):
        from types import SimpleNamespace

        from app.services import analysis_service, edm_service, submission_service

        def fake_columns(*, analysis_ids):
            found = [columns_by_id[i] for i in analysis_ids if i in columns_by_id]
            return found, len(analysis_ids) - len(found)

        monkeypatch.setattr(analysis_service, "list_analysis_perspectives",
                            lambda: _perspectives())
        monkeypatch.setattr(analysis_service, "list_results_columns",
                            fake_columns)
        monkeypatch.setattr(
            submission_service, "get_submission",
            lambda sid: (SimpleNamespace(id=SUB_ID, name=submission_name)
                         if submission_name and str(sid) == SUB_ID else None))
        monkeypatch.setattr(
            edm_service, "get_edm",
            lambda eid: (SimpleNamespace(id=EDM_ID, name=edm_name)
                         if edm_name and str(eid) == EDM_ID else None))
        return TestClient(_make_app())

    def test_column_order_follows_ids_param(self, monkeypatch):
        client = self._client(monkeypatch, {
            AN_A: _column(AN_A, "Alpha Analysis"),
            AN_B: _column(AN_B, "Beta Analysis")})
        resp = client.get(f"/results/analyses?ids={AN_B},{AN_A}")
        assert resp.status_code == 200
        assert resp.text.index("Beta Analysis") < resp.text.index("Alpha Analysis")
        # reorder links rewrite the ids param with the neighbours swapped
        assert f"ids={AN_A}%2C{AN_B}" in resp.text

    def test_reorder_swaps_the_table_and_leaves_the_units_select(self, monkeypatch):
        """A move re-renders #results-view over HTMX instead of navigating, so
        #res-units keeps the picked unit; the two selects come back out of band
        because they carry the ids order in their own hx-get."""
        client = self._client(monkeypatch, {
            AN_A: _column(AN_A, "Alpha Analysis"),
            AN_B: _column(AN_B, "Beta Analysis")})
        text = client.get(f"/results/analyses?ids={AN_A},{AN_B}").text
        assert f'hx-get="/results/analyses?ids={AN_B}%2C{AN_A}' in text
        assert 'hx-target="#results-view" hx-select="#results-view"' in text
        assert 'hx-select-oob="#res-persp,#res-ep"' in text
        assert text.count('hx-push-url="true"') == 2
        # the units select sits ahead of the swapped region, so no move touches it
        assert text.index("data-units-select") < text.index('id="results-view"')

    def test_perspective_param_rerenders_every_column(self, monkeypatch):
        client = self._client(monkeypatch, {
            AN_A: _column(AN_A, "Alpha Analysis", produced=("GR",)),
            AN_B: _column(AN_B, "Beta Analysis", produced=("GR", "GU"))})
        resp = client.get(f"/results/analyses?ids={AN_A},{AN_B}&perspective=GU")
        assert resp.status_code == 200
        # screen-wide: the GR-only column reads not-produced while the GU
        # column shows numbers — one param drives both
        assert "did not produce this perspective" in resp.text
        assert 'value="GU" selected' in resp.text
        assert 'data-unit-value="1000000.0"' in resp.text

    def test_default_perspective_is_pre_cat_net(self, monkeypatch):
        """D9: the page opens on RL, not on the first row of the kind table."""
        client = self._client(monkeypatch, {AN_A: _column(AN_A, "Alpha Analysis")})
        resp = client.get(f"/results/analyses?ids={AN_A}")
        assert 'value="RL" selected' in resp.text
        assert 'data-unit-value="4100000.0"' in resp.text

    def test_ep_type_selects_one_curve(self, monkeypatch):
        client = self._client(monkeypatch, {AN_A: _column(AN_A, "Alpha Analysis")})
        oep = client.get(f"/results/analyses?ids={AN_A}").text
        assert 'value="OEP" selected' in oep
        assert 'data-unit-value="1000000.0"' in oep
        assert 'data-unit-value="1100000.0"' not in oep

        aep = client.get(f"/results/analyses?ids={AN_A}&ep_type=AEP").text
        assert 'value="AEP" selected' in aep
        assert 'data-unit-value="1100000.0"' in aep
        assert 'data-unit-value="1000000.0"' not in aep
        # AAL and Std dev sit outside the selection (FR-015)
        assert 'data-unit-value="4100000.0"' in oep
        assert 'data-unit-value="4100000.0"' in aep

    def test_no_cell_spans_columns(self, monkeypatch):
        client = self._client(monkeypatch, {
            AN_A: _column(AN_A, "Alpha Analysis"),
            AN_B: _column(AN_B, "Beta Analysis", produced=("GU",))})
        resp = client.get(f"/results/analyses?ids={AN_A},{AN_B}")
        assert "did not produce this perspective" in resp.text
        assert "colspan" not in resp.text

    def test_breadcrumbs_submission_only(self, monkeypatch):
        client = self._client(monkeypatch, {AN_A: _column(AN_A, "Alpha Analysis")},
                              submission_name="Coastal Re HO 2026")
        resp = client.get(f"/results/analyses?ids={AN_A}&submission={SUB_ID}")
        assert f'href="/submissions/{SUB_ID}"' in resp.text
        assert "Coastal Re HO 2026" in resp.text
        assert "<title>Coastal Re HO 2026 · Risk Workbench</title>" in resp.text

    def test_breadcrumbs_submission_then_edm(self, monkeypatch):
        client = self._client(monkeypatch, {AN_A: _column(AN_A, "Alpha Analysis")},
                              submission_name="Coastal Re HO 2026",
                              edm_name="Coastal HO 2026")
        resp = client.get(
            f"/results/analyses?ids={AN_A}&submission={SUB_ID}&edm={EDM_ID}")
        sub_pos = resp.text.index(f'href="/submissions/{SUB_ID}"')
        edm_pos = resp.text.index(f'href="/edms/{EDM_ID}"')
        assert sub_pos < edm_pos
        # the tab title carries the EDM name on entry from the EDM page
        assert "<title>Coastal HO 2026 · Risk Workbench</title>" in resp.text

    def test_absent_ids_render_a_notice_not_a_500(self, monkeypatch):
        client = self._client(monkeypatch, {AN_A: _column(AN_A, "Alpha Analysis")})
        resp = client.get(
            f"/results/analyses?ids={AN_A},99999999-9999-9999-9999-999999999999")
        assert resp.status_code == 200
        assert "no longer exist" in resp.text
        assert "Alpha Analysis" in resp.text

    def test_pending_column_renders_never_dropped(self, monkeypatch):
        client = self._client(monkeypatch, {
            AN_A: _column(AN_A, "Alpha Analysis"),
            AN_B: _column(AN_B, "Beta Analysis", state="pending")})
        resp = client.get(f"/results/analyses?ids={AN_A},{AN_B}")
        assert "Beta Analysis" in resp.text
        assert "Results pending" in resp.text

    def test_failed_column_carries_reason(self, monkeypatch):
        client = self._client(monkeypatch, {
            AN_A: _column(AN_A, "Alpha Analysis", state="failed",
                          error="RM returned 500 on EP curve (GR)")})
        resp = client.get(f"/results/analyses?ids={AN_A}")
        assert "Results retrieval failed" in resp.text
        assert "RM returned 500 on EP curve (GR)" in resp.text

    def test_no_ids_reads_empty_state(self, monkeypatch):
        client = self._client(monkeypatch, {})
        resp = client.get("/results/analyses")
        assert resp.status_code == 200
        assert "No analyses to display" in resp.text
