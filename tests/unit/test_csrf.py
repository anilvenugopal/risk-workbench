"""Unit tests for CSRF token generation and validation."""

from __future__ import annotations

from app.auth.csrf import generate_csrf_token, validate_csrf_token


class TestCSRFToken:
    def test_generated_token_validates(self):
        token = generate_csrf_token()
        assert validate_csrf_token(token) is True

    def test_none_token_returns_false(self):
        assert validate_csrf_token(None) is False

    def test_empty_token_returns_false(self):
        assert validate_csrf_token("") is False

    def test_tampered_token_returns_false(self):
        token = generate_csrf_token()
        tampered = token[:-4] + "XXXX"
        assert validate_csrf_token(tampered) is False

    def test_garbage_string_returns_false(self):
        assert validate_csrf_token("not.a.real.token") is False
