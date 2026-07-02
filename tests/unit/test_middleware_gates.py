"""Unit tests for SessionMiddleware gate logic.

Tests the three security gates in dispatch():
  1. No session cookie → redirect to /auth/login?next=<path>
  2. Invalid/expired session → redirect to /auth/login
  3. must_change_password gate → redirect to /auth/change-password
  4. No roles assigned gate → redirect to /auth/access-pending

Also tests HTMX vs standard redirect format, and public path bypass.

Strategy: mount a minimal FastAPI app with only SessionMiddleware and a
trivial /protected echo endpoint. Monkeypatch validate_session so no DB
is needed.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from starlette.testclient import TestClient

from app.services.auth_service import CurrentUser


def _make_app():
    from app.auth.middleware import SessionMiddleware
    app = FastAPI()
    app.add_middleware(SessionMiddleware)

    @app.get("/protected")
    def protected(request: Request):
        return PlainTextResponse("ok")

    @app.get("/auth/login")
    def login():
        return PlainTextResponse("login page")

    @app.get("/auth/change-password")
    def change_pwd():
        return PlainTextResponse("change password page")

    @app.get("/auth/access-pending")
    def access_pending():
        return PlainTextResponse("access pending page")

    @app.get("/api/health")
    def health():
        return PlainTextResponse("ok")

    return app


def _make_user(**overrides):
    defaults = dict(
        id="u1", email="u@x.com", display_name="U", session_id="sess-1",
        role_codes=["analyst"], is_admin=False,
        must_change_password=False, entra_oid=None, is_active=True,
    )
    defaults.update(overrides)
    return CurrentUser(**defaults)


class TestPublicPathsBypass:
    def test_health_endpoint_bypasses_session_check(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_login_page_bypasses_session_check(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        # No session cookie, hitting /auth/login — should not redirect loop
        resp = client.get("/auth/login")
        assert resp.status_code == 200

    def test_static_assets_bypass_session_check(self, monkeypatch):
        import app.auth.middleware as mw
        monkeypatch.setattr(mw, "validate_session", lambda sid: None)
        client = TestClient(_make_app(), raise_server_exceptions=False)
        # static path — should never hit validate_session (would return None → redirect otherwise)
        resp = client.get("/static/css/app.css", follow_redirects=False)
        # The file doesn't exist so 404, but it should NOT redirect to /auth/login
        assert "/auth/login" not in resp.headers.get("location", "")


class TestNoSessionCookie:
    def test_redirects_to_login(self, monkeypatch):
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/protected")
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/auth/login")

    def test_next_param_included_in_redirect(self, monkeypatch):
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/protected")
        assert "next=/protected" in resp.headers["location"]

    def test_htmx_request_returns_hx_redirect_header(self):
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/protected", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "HX-Redirect" in resp.headers
        assert "/auth/login" in resp.headers["HX-Redirect"]


class TestInvalidSession:
    def test_invalid_session_redirects_to_login(self, monkeypatch):
        import app.auth.middleware as mw
        monkeypatch.setattr(mw, "validate_session", lambda sid: None)
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/protected", cookies={"rwb_session": "bad-session-id"})
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]
        # No ?next= because session was present but invalid (already lost context)
        assert "next=" not in resp.headers["location"]

    def test_htmx_invalid_session_returns_hx_redirect(self, monkeypatch):
        import app.auth.middleware as mw
        monkeypatch.setattr(mw, "validate_session", lambda sid: None)
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/protected",
                          cookies={"rwb_session": "bad"},
                          headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "HX-Redirect" in resp.headers


class TestValidSession:
    def test_valid_session_passes_through(self, monkeypatch):
        import app.auth.middleware as mw
        monkeypatch.setattr(mw, "validate_session", lambda sid: _make_user())
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/protected", cookies={"rwb_session": "good"})
        assert resp.status_code == 200
        assert resp.text == "ok"


class TestMustChangePasswordGate:
    def test_must_change_password_redirects_to_change_page(self, monkeypatch):
        import app.auth.middleware as mw
        user = _make_user(must_change_password=True)
        monkeypatch.setattr(mw, "validate_session", lambda sid: user)
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/protected", cookies={"rwb_session": "s"})
        assert resp.status_code == 302
        assert "/auth/change-password" in resp.headers["location"]

    def test_change_password_path_is_exempt(self, monkeypatch):
        import app.auth.middleware as mw
        user = _make_user(must_change_password=True)
        monkeypatch.setattr(mw, "validate_session", lambda sid: user)
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/auth/change-password", cookies={"rwb_session": "s"})
        assert resp.status_code == 200

    def test_logout_exempt_even_when_must_change(self, monkeypatch):
        from app.auth.middleware import _CHANGE_PWD_EXEMPT
        assert "/auth/logout" in _CHANGE_PWD_EXEMPT


class TestRoleGate:
    def test_no_roles_redirects_to_access_pending(self, monkeypatch):
        import app.auth.middleware as mw
        user = _make_user(role_codes=[])
        monkeypatch.setattr(mw, "validate_session", lambda sid: user)
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/protected", cookies={"rwb_session": "s"})
        assert resp.status_code == 302
        assert "/auth/access-pending" in resp.headers["location"]

    def test_access_pending_path_is_exempt(self, monkeypatch):
        import app.auth.middleware as mw
        user = _make_user(role_codes=[])
        monkeypatch.setattr(mw, "validate_session", lambda sid: user)
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/auth/access-pending", cookies={"rwb_session": "s"})
        assert resp.status_code == 200

    def test_user_with_roles_passes_role_gate(self, monkeypatch):
        import app.auth.middleware as mw
        user = _make_user(role_codes=["analyst"])
        monkeypatch.setattr(mw, "validate_session", lambda sid: user)
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/protected", cookies={"rwb_session": "s"})
        assert resp.status_code == 200
