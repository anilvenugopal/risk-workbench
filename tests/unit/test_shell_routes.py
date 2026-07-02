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
    from app.routers import shell
    from app.auth.csrf import generate_csrf_token
    from app.config import settings

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates

    app.add_middleware(_InjectUser, user=user or _fake_user())
    app.include_router(shell.router)
    return app


class TestHomeRoute:
    def test_returns_200(self, monkeypatch):
        import app.routers.shell as shell_mod
        monkeypatch.setattr(shell_mod, "execute", lambda sql, params, connection=None: [{"n": 0}])
        resp = TestClient(_make_app()).get("/")
        assert resp.status_code == 200

    def test_html_response(self, monkeypatch):
        import app.routers.shell as shell_mod
        monkeypatch.setattr(shell_mod, "execute", lambda sql, params, connection=None: [{"n": 5}])
        resp = TestClient(_make_app()).get("/")
        assert "text/html" in resp.headers["content-type"]

    def test_customer_count_in_page(self, monkeypatch):
        import app.routers.shell as shell_mod
        monkeypatch.setattr(shell_mod, "execute", lambda sql, params, connection=None: [{"n": 42}])
        resp = TestClient(_make_app()).get("/")
        assert "42" in resp.text

    def test_zero_count_when_execute_returns_empty(self, monkeypatch):
        import app.routers.shell as shell_mod
        monkeypatch.setattr(shell_mod, "execute", lambda sql, params, connection=None: [])
        resp = TestClient(_make_app()).get("/")
        assert resp.status_code == 200


class TestSimpleShellRoutes:
    """Routes that need no DB calls — just render a template."""

    @pytest.fixture
    def client(self, monkeypatch):
        import app.routers.shell as shell_mod
        monkeypatch.setattr(shell_mod, "execute", lambda sql, params, connection=None: [])
        return TestClient(_make_app())

    def test_submissions(self, client):
        assert client.get("/submissions").status_code == 200

    def test_submissions_mine(self, client):
        assert client.get("/submissions/mine").status_code == 200

    def test_workflows(self, client):
        assert client.get("/workflows").status_code == 200

    def test_results(self, client):
        assert client.get("/results").status_code == 200

    def test_templates_page(self, client):
        assert client.get("/templates").status_code == 200

    def test_irp_page(self, client):
        assert client.get("/irp").status_code == 200

    def test_account_page(self, client):
        assert client.get("/account").status_code == 200

    def test_workflows_active(self, client):
        assert client.get("/workflows/active").status_code == 200

    def test_workflows_review(self, client):
        assert client.get("/workflows/review").status_code == 200

    def test_workflows_irp_jobs(self, client):
        assert client.get("/workflows/irp-jobs").status_code == 200

    def test_workflows_rwb_jobs(self, client):
        assert client.get("/workflows/rwb-jobs").status_code == 200

    def test_workflows_exceptions(self, client):
        assert client.get("/workflows/exceptions").status_code == 200


class TestShellNavContext:
    """Verify nav context is rendered into the shell."""

    def test_display_name_in_page(self, monkeypatch):
        import app.routers.shell as shell_mod
        monkeypatch.setattr(shell_mod, "execute", lambda sql, params, connection=None: [{"n": 0}])
        user = _fake_user(display_name="Alice Smith")
        resp = TestClient(_make_app(user=user)).get("/")
        assert "Alice Smith" in resp.text
