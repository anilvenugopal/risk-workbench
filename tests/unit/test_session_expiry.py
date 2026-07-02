"""Unit tests for session expiry and HTMX-aware redirect logic."""

from __future__ import annotations

import pytest


class TestSessionExpiry:
    """validate_session behavior is tested via monkeypatch."""

    def test_htmx_expired_returns_hx_redirect(self):
        """HTMX requests to expired sessions get HX-Redirect header, HTTP 200."""
        from unittest.mock import MagicMock
        from starlette.testclient import TestClient
        from starlette.requests import Request

        from app.auth.middleware import _is_htmx, _redirect_response

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"HX-Request": "true"}

        response = _redirect_response(mock_request, "/auth/login")
        assert response.status_code == 200
        assert response.headers.get("HX-Redirect") == "/auth/login"

    def test_standard_expired_returns_302(self):
        """Non-HTMX requests to expired sessions get a 302 redirect."""
        from unittest.mock import MagicMock
        from starlette.requests import Request

        from app.auth.middleware import _redirect_response

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {}

        response = _redirect_response(mock_request, "/auth/login")
        assert response.status_code == 302

    def test_is_htmx_detects_header(self):
        from unittest.mock import MagicMock
        from starlette.requests import Request
        from app.auth.middleware import _is_htmx

        mock_htmx = MagicMock(spec=Request)
        mock_htmx.headers = {"HX-Request": "true"}
        assert _is_htmx(mock_htmx) is True

        mock_plain = MagicMock(spec=Request)
        mock_plain.headers = {}
        assert _is_htmx(mock_plain) is False


class TestPasswordRequirementsInDetail:
    """Additional edge-case coverage for validate_password_requirements."""

    def test_exactly_12_chars_passes(self):
        from app.auth.password import validate_password_requirements
        assert validate_password_requirements("Abcdefghij1!") == []

    def test_11_chars_fails(self):
        from app.auth.password import validate_password_requirements
        errors = validate_password_requirements("Abcdefghi1!")
        assert any("12" in e for e in errors)
