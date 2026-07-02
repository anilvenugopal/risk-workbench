"""Unit tests for session middleware helpers."""

from __future__ import annotations

from unittest.mock import MagicMock
from starlette.requests import Request


def _mock_request(headers: dict) -> MagicMock:
    r = MagicMock(spec=Request)
    r.headers = headers
    return r


class TestIsHtmx:
    def test_htmx_header_present(self):
        from app.auth.middleware import _is_htmx
        assert _is_htmx(_mock_request({"HX-Request": "true"})) is True

    def test_htmx_header_absent(self):
        from app.auth.middleware import _is_htmx
        assert _is_htmx(_mock_request({})) is False

    def test_htmx_header_wrong_value(self):
        from app.auth.middleware import _is_htmx
        assert _is_htmx(_mock_request({"HX-Request": "false"})) is False


class TestRedirectResponse:
    def test_htmx_returns_200_with_hx_redirect(self):
        from app.auth.middleware import _redirect_response
        r = _redirect_response(_mock_request({"HX-Request": "true"}), "/auth/login")
        assert r.status_code == 200
        assert r.headers["HX-Redirect"] == "/auth/login"

    def test_standard_returns_302(self):
        from app.auth.middleware import _redirect_response
        r = _redirect_response(_mock_request({}), "/auth/login")
        assert r.status_code == 302

    def test_redirect_location_in_302(self):
        from app.auth.middleware import _redirect_response
        r = _redirect_response(_mock_request({}), "/some/path")
        assert "/some/path" in r.headers["location"]


class TestPublicPaths:
    def test_login_is_public(self):
        from app.auth.middleware import _PUBLIC_PATHS
        assert "/auth/login" in _PUBLIC_PATHS

    def test_callback_is_public(self):
        from app.auth.middleware import _PUBLIC_PATHS
        assert "/auth/callback" in _PUBLIC_PATHS

    def test_health_is_public(self):
        from app.auth.middleware import _PUBLIC_PATHS
        assert "/api/health" in _PUBLIC_PATHS

    def test_home_is_not_public(self):
        from app.auth.middleware import _PUBLIC_PATHS
        assert "/" not in _PUBLIC_PATHS


class TestGateSets:
    def test_change_password_exempt_from_pwd_gate(self):
        from app.auth.middleware import _CHANGE_PWD_EXEMPT
        assert "/auth/change-password" in _CHANGE_PWD_EXEMPT

    def test_access_pending_exempt_from_role_gate(self):
        from app.auth.middleware import _ROLE_GATE_EXEMPT
        assert "/auth/access-pending" in _ROLE_GATE_EXEMPT

    def test_logout_exempt_from_role_gate(self):
        from app.auth.middleware import _ROLE_GATE_EXEMPT
        assert "/auth/logout" in _ROLE_GATE_EXEMPT
