"""Unit tests for auth service, password, and CSRF helpers."""

from __future__ import annotations

import pytest


# ── Password helpers ──────────────────────────────────────────────────────────

class TestVerifyPassword:
    def test_correct_password(self):
        from app.auth.password import hash_password, verify_password
        h = hash_password("TestPass1234!")
        assert verify_password("TestPass1234!", h) is True

    def test_wrong_password(self):
        from app.auth.password import hash_password, verify_password
        h = hash_password("TestPass1234!")
        assert verify_password("WrongPass1234!", h) is False

    def test_null_hash_returns_false(self):
        from app.auth.password import verify_password
        # OIDC accounts have password_hash=None; must not be bypassed
        assert verify_password("anything", None) is False

    def test_empty_hash_returns_false(self):
        from app.auth.password import verify_password
        assert verify_password("anything", "") is False


class TestValidatePasswordRequirements:
    def test_valid_password(self):
        from app.auth.password import validate_password_requirements
        assert validate_password_requirements("SecurePass123!") == []

    def test_too_short(self):
        from app.auth.password import validate_password_requirements
        errors = validate_password_requirements("Short1A")
        assert any("12" in e for e in errors)

    def test_no_uppercase(self):
        from app.auth.password import validate_password_requirements
        errors = validate_password_requirements("securepas1234s!")
        assert any("uppercase" in e.lower() for e in errors)

    def test_no_lowercase(self):
        from app.auth.password import validate_password_requirements
        errors = validate_password_requirements("SECUREPAS1234!")
        assert any("lowercase" in e.lower() for e in errors)

    def test_no_digit(self):
        from app.auth.password import validate_password_requirements
        errors = validate_password_requirements("SecurePassWord!")
        assert any("digit" in e.lower() for e in errors)

    def test_all_failures(self):
        from app.auth.password import validate_password_requirements
        errors = validate_password_requirements("abc")
        assert len(errors) >= 3  # short + no upper + no digit


class TestHashPassword:
    def test_produces_bcrypt_hash(self):
        from app.auth.password import hash_password
        h = hash_password("TestPass1234!")
        assert h.startswith("$2b$") or h.startswith("$2a$")

    def test_different_salts(self):
        from app.auth.password import hash_password
        h1 = hash_password("TestPass1234!")
        h2 = hash_password("TestPass1234!")
        assert h1 != h2  # bcrypt uses random salt each time


# ── Session creation ──────────────────────────────────────────────────────────

class TestCreateSession:
    def test_returns_64_char_hex(self, monkeypatch):
        from app.services import auth_service

        calls = []

        def mock_execute_command(sql, params, connection=None):
            calls.append((sql, params))

        monkeypatch.setattr(auth_service, "execute_command", mock_execute_command)

        session_id = auth_service.create_session("user-id-123", "127.0.0.1", "test-agent")
        assert len(session_id) == 64
        assert all(c in "0123456789abcdef" for c in session_id)
        assert len(calls) == 1


# ── Invalidate session ────────────────────────────────────────────────────────

class TestInvalidateSession:
    def test_calls_update(self, monkeypatch):
        from app.services import auth_service

        calls = []

        def mock_execute_command(sql, params, connection=None):
            calls.append(params)

        monkeypatch.setattr(auth_service, "execute_command", mock_execute_command)
        auth_service.invalidate_session("abc123")
        assert len(calls) == 1
        assert calls[0]["id"] == "abc123"


# ── CurrentUser ───────────────────────────────────────────────────────────────

class TestCurrentUser:
    def test_is_password_account(self):
        from app.services.auth_service import CurrentUser
        u = CurrentUser(entra_oid=None, role_codes=["analyst"])
        assert u.is_password_account is True

    def test_is_oidc_account(self):
        from app.services.auth_service import CurrentUser
        u = CurrentUser(entra_oid="some-oid", role_codes=["analyst"])
        assert u.is_password_account is False
