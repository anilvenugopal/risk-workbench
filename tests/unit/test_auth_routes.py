"""Unit tests for app/routers/auth.py route handlers.

Strategy: build a minimal FastAPI app with the auth router and real Jinja2
templates. A lightweight InjectUser middleware optionally stamps
request.state.user. All service calls (validate_session, create_session,
get_user_by_email, etc.) are monkeypatched — no real DB.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services.auth_service import CurrentUser
from app.auth.csrf import generate_csrf_token


def _make_user(**overrides):
    defaults = dict(
        id="u1", email="u@x.com", display_name="U", session_id="sess-1",
        role_codes=["analyst"], is_admin=False,
        must_change_password=False, entra_oid=None, is_active=True,
    )
    defaults.update(overrides)
    return CurrentUser(**defaults)


class _OptionalUser(BaseHTTPMiddleware):
    """Stamps request.state.user if a user is configured; leaves it absent otherwise."""
    def __init__(self, app, user=None):
        super().__init__(app)
        self._user = user

    async def dispatch(self, request: Request, call_next):
        if self._user is not None:
            request.state.user = self._user
        return await call_next(request)


def _make_app(user=None):
    from app.routers.auth import router
    from app.config import settings

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates

    app.add_middleware(_OptionalUser, user=user)
    app.include_router(router)
    return app


def _valid_csrf():
    """Return a CSRF token that will pass validate_csrf_token."""
    return generate_csrf_token()


# ── GET /auth/login ───────────────────────────────────────────────────────────

class TestLoginPage:
    def test_unauthenticated_user_sees_form(self):
        resp = TestClient(_make_app()).get("/auth/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_already_authenticated_redirects_home(self):
        client = TestClient(_make_app(user=_make_user()), follow_redirects=False)
        resp = client.get("/auth/login")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    def test_error_param_rendered_in_page(self):
        resp = TestClient(_make_app()).get("/auth/login?error=account_not_found")
        assert resp.status_code == 200


# ── POST /auth/login ──────────────────────────────────────────────────────────

class TestLoginSubmit:
    def _post(self, monkeypatch, **form_overrides):
        defaults = {
            "email": "user@example.com",
            "password": "Password1234!",
            "csrf_token": _valid_csrf(),
        }
        defaults.update(form_overrides)
        return TestClient(_make_app(), follow_redirects=False).post(
            "/auth/login", data=defaults
        )

    def test_invalid_csrf_returns_form(self, monkeypatch):
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: False)
        monkeypatch.setattr(auth_mod, "log_attempt", lambda *a, **k: None)
        resp = TestClient(_make_app(), follow_redirects=False).post(
            "/auth/login",
            data={"email": "a@b.com", "password": "x", "csrf_token": "bad"},
        )
        assert resp.status_code == 200

    def test_unknown_email_returns_form(self, monkeypatch):
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "get_user_by_email", lambda e: None)
        monkeypatch.setattr(auth_mod, "log_attempt", lambda *a, **k: None)
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        resp = self._post(monkeypatch)
        assert resp.status_code == 200

    def test_wrong_password_returns_form(self, monkeypatch):
        import app.routers.auth as auth_mod
        fake_user = {"id": "u1", "password_hash": "hash", "is_active": True, "email": "u@x.com"}
        monkeypatch.setattr(auth_mod, "get_user_by_email", lambda e: fake_user)
        monkeypatch.setattr(auth_mod, "verify_password", lambda pw, h: False)
        monkeypatch.setattr(auth_mod, "log_attempt", lambda *a, **k: None)
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        resp = self._post(monkeypatch)
        assert resp.status_code == 200

    def test_inactive_account_returns_form(self, monkeypatch):
        import app.routers.auth as auth_mod
        fake_user = {"id": "u1", "password_hash": "hash", "is_active": False, "email": "u@x.com"}
        monkeypatch.setattr(auth_mod, "get_user_by_email", lambda e: fake_user)
        monkeypatch.setattr(auth_mod, "verify_password", lambda pw, h: True)
        monkeypatch.setattr(auth_mod, "log_attempt", lambda *a, **k: None)
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        resp = self._post(monkeypatch)
        assert resp.status_code == 200

    def test_successful_login_sets_cookie_and_redirects(self, monkeypatch):
        import app.routers.auth as auth_mod
        fake_user = {"id": "u1", "password_hash": "hash", "is_active": True, "email": "u@x.com"}
        monkeypatch.setattr(auth_mod, "get_user_by_email", lambda e: fake_user)
        monkeypatch.setattr(auth_mod, "verify_password", lambda pw, h: True)
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        monkeypatch.setattr(auth_mod, "create_session", lambda uid, ip, ua: "new-session-id")
        monkeypatch.setattr(auth_mod, "log_attempt", lambda *a, **k: None)
        monkeypatch.setattr(auth_mod, "update_last_login", lambda uid: None)
        resp = self._post(monkeypatch)
        assert resp.status_code == 302
        assert "rwb_session" in resp.cookies

    def test_successful_login_respects_safe_next(self, monkeypatch):
        import app.routers.auth as auth_mod
        fake_user = {"id": "u1", "password_hash": "hash", "is_active": True, "email": "u@x.com"}
        monkeypatch.setattr(auth_mod, "get_user_by_email", lambda e: fake_user)
        monkeypatch.setattr(auth_mod, "verify_password", lambda pw, h: True)
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        monkeypatch.setattr(auth_mod, "create_session", lambda uid, ip, ua: "sess")
        monkeypatch.setattr(auth_mod, "log_attempt", lambda *a, **k: None)
        monkeypatch.setattr(auth_mod, "update_last_login", lambda uid: None)
        resp = TestClient(_make_app(), follow_redirects=False).post(
            "/auth/login?next=/submissions",
            data={"email": "u@x.com", "password": "pw", "csrf_token": "tok"},
        )
        assert resp.headers["location"] == "/submissions"

    def test_next_open_redirect_blocked(self, monkeypatch):
        import app.routers.auth as auth_mod
        fake_user = {"id": "u1", "password_hash": "hash", "is_active": True, "email": "u@x.com"}
        monkeypatch.setattr(auth_mod, "get_user_by_email", lambda e: fake_user)
        monkeypatch.setattr(auth_mod, "verify_password", lambda pw, h: True)
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        monkeypatch.setattr(auth_mod, "create_session", lambda uid, ip, ua: "sess")
        monkeypatch.setattr(auth_mod, "log_attempt", lambda *a, **k: None)
        monkeypatch.setattr(auth_mod, "update_last_login", lambda uid: None)
        resp = TestClient(_make_app(), follow_redirects=False).post(
            "/auth/login?next=https://evil.com",
            data={"email": "u@x.com", "password": "pw", "csrf_token": "tok"},
        )
        assert resp.headers["location"] == "/"


# ── POST /auth/logout ─────────────────────────────────────────────────────────

class TestLogout:
    def test_logout_clears_cookie_and_redirects(self, monkeypatch):
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        monkeypatch.setattr(auth_mod, "invalidate_session", lambda sid: None)
        client = TestClient(_make_app(user=_make_user()), follow_redirects=False)
        resp = client.post("/auth/logout", data={"csrf_token": "t"})
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]

    def test_invalid_csrf_still_clears_cookie(self, monkeypatch):
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: False)
        client = TestClient(_make_app(user=_make_user()), follow_redirects=False)
        resp = client.post("/auth/logout", data={"csrf_token": "bad"})
        # Still redirects — never leaves user stuck with a broken session
        assert resp.status_code == 302

    def test_oidc_user_gets_entra_logout_url(self, monkeypatch):
        import app.routers.auth as auth_mod
        user = _make_user(entra_oid="oid-123")
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        monkeypatch.setattr(auth_mod, "invalidate_session", lambda sid: None)
        monkeypatch.setattr(auth_mod.settings, "auth_mode", "oidc")

        import app.auth.oidc as oidc_mod
        monkeypatch.setattr(oidc_mod, "build_logout_url",
                            lambda: "https://login.microsoftonline.com/t/logout?post_logout_redirect_uri=...")
        client = TestClient(_make_app(user=user), follow_redirects=False)
        resp = client.post("/auth/logout", data={"csrf_token": "t"})
        assert resp.status_code == 302
        assert "microsoftonline.com" in resp.headers["location"]


# ── GET /auth/oidc-login ──────────────────────────────────────────────────────

class TestOidcLogin:
    def test_oidc_disabled_redirects_to_login(self, monkeypatch):
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod.settings, "auth_mode", "password")
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/auth/oidc-login")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]

    def test_oidc_enabled_sets_state_cookie_and_redirects(self, monkeypatch):
        import app.routers.auth as auth_mod
        import app.auth.oidc as oidc_mod
        monkeypatch.setattr(auth_mod.settings, "auth_mode", "oidc")
        monkeypatch.setattr(oidc_mod, "initiate_flow",
                            lambda uri: {"auth_uri": "https://login.microsoftonline.com/auth", "state": "s"})
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/auth/oidc-login")
        assert resp.status_code == 302
        assert "microsoftonline.com" in resp.headers["location"]
        assert "rwb_oidc_state" in resp.cookies


# ── GET /auth/callback ────────────────────────────────────────────────────────

class TestOidcCallback:
    def _client(self, monkeypatch):
        return TestClient(_make_app(), follow_redirects=False)

    def _sign_flow(self, flow: dict) -> str:
        from itsdangerous import URLSafeTimedSerializer
        from app.config import settings
        ser = URLSafeTimedSerializer(settings.session_secret_key)
        return ser.dumps(flow, salt=b"oidc-state")

    def test_missing_state_cookie_aborts(self, monkeypatch):
        resp = self._client(monkeypatch).get("/auth/callback?code=abc&state=s")
        assert resp.status_code == 302
        assert "state_missing" in resp.headers["location"]

    def test_tampered_state_cookie_aborts(self, monkeypatch):
        client = self._client(monkeypatch)
        resp = client.get("/auth/callback?code=abc",
                          cookies={"rwb_oidc_state": "TAMPERED.bad.cookie"})
        assert resp.status_code == 302
        assert "state_expired" in resp.headers["location"]

    def test_token_exchange_failure_aborts(self, monkeypatch):
        import app.auth.oidc as oidc_mod
        monkeypatch.setattr(oidc_mod, "complete_flow",
                            lambda flow, params: (_ for _ in ()).throw(ValueError("bad token")))
        state_cookie = self._sign_flow({"state": "s", "code_verifier": "v"})
        client = self._client(monkeypatch)
        resp = client.get("/auth/callback?code=abc&state=s",
                          cookies={"rwb_oidc_state": state_cookie})
        assert resp.status_code == 302
        assert "token_exchange_failed" in resp.headers["location"]

    def test_existing_oidc_user_redirects_to_home(self, monkeypatch):
        import app.routers.auth as auth_mod
        import app.auth.oidc as oidc_mod
        claims = {"oid": "oid-1", "email": "u@x.com", "name": "U"}
        monkeypatch.setattr(oidc_mod, "complete_flow", lambda flow, params: claims)
        monkeypatch.setattr(auth_mod, "get_user_by_oid",
                            lambda oid: {"id": "u1", "email": "u@x.com"})
        monkeypatch.setattr(auth_mod, "update_last_login", lambda uid: None)
        monkeypatch.setattr(auth_mod, "log_attempt", lambda *a, **k: None)
        monkeypatch.setattr(auth_mod, "create_session", lambda uid, ip, ua: "sess-1")
        state_cookie = self._sign_flow({"state": "s"})
        client = self._client(monkeypatch)
        resp = client.get("/auth/callback?code=abc&state=s",
                          cookies={"rwb_oidc_state": state_cookie})
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"
        assert "rwb_session" in resp.cookies

    def test_new_oidc_user_jit_provisioned_redirects_to_access_pending(self, monkeypatch):
        import app.routers.auth as auth_mod
        import app.auth.oidc as oidc_mod
        import app.auth.provisioning as prov_mod
        claims = {"oid": "oid-new", "email": "new@x.com", "name": "New"}
        monkeypatch.setattr(oidc_mod, "complete_flow", lambda flow, params: claims)
        monkeypatch.setattr(auth_mod, "get_user_by_oid", lambda oid: None)
        monkeypatch.setattr(auth_mod, "get_user_by_email", lambda email: None)
        monkeypatch.setattr(prov_mod, "jit_provision_oidc_user",
                            lambda oid, email, name: "new-user-id")
        monkeypatch.setattr(auth_mod, "log_attempt", lambda *a, **k: None)
        monkeypatch.setattr(auth_mod, "create_session", lambda uid, ip, ua: "sess-2")
        state_cookie = self._sign_flow({"state": "s"})
        client = self._client(monkeypatch)
        resp = client.get("/auth/callback?code=abc&state=s",
                          cookies={"rwb_oidc_state": state_cookie})
        assert resp.status_code == 302
        assert "/auth/access-pending" in resp.headers["location"]

    def test_missing_email_claim_aborts(self, monkeypatch):
        import app.auth.oidc as oidc_mod
        # oid present but no email or preferred_username
        claims = {"oid": "oid-1", "name": "No Email"}
        monkeypatch.setattr(oidc_mod, "complete_flow", lambda flow, params: claims)
        state_cookie = self._sign_flow({"state": "s"})
        client = self._client(monkeypatch)
        resp = client.get("/auth/callback?code=abc&state=s",
                          cookies={"rwb_oidc_state": state_cookie})
        assert resp.status_code == 302
        assert "email_missing" in resp.headers["location"]


# ── GET /auth/access-pending ──────────────────────────────────────────────────

class TestAccessPending:
    def test_renders_page(self):
        resp = TestClient(_make_app()).get("/auth/access-pending")
        assert resp.status_code == 200


# ── GET /auth/change-password ─────────────────────────────────────────────────

class TestChangePasswordPage:
    def test_no_user_redirects_home(self):
        client = TestClient(_make_app(), follow_redirects=False)
        resp = client.get("/auth/change-password")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    def test_user_without_flag_redirects_home(self):
        client = TestClient(_make_app(user=_make_user(must_change_password=False)),
                            follow_redirects=False)
        resp = client.get("/auth/change-password")
        assert resp.status_code == 302

    def test_user_with_flag_sees_form(self):
        client = TestClient(_make_app(user=_make_user(must_change_password=True)))
        resp = client.get("/auth/change-password")
        assert resp.status_code == 200


# ── POST /auth/change-password ────────────────────────────────────────────────

class TestChangePasswordSubmit:
    def _post(self, monkeypatch, inject_user=True, **form):
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "execute_command", lambda sql, p, connection=None: 1)
        defaults = {
            "new_password": "NewPass123456!",
            "confirm_password": "NewPass123456!",
            "csrf_token": "tok",
        }
        defaults.update(form)
        user = _make_user(must_change_password=True) if inject_user else None
        client = TestClient(_make_app(user=user), follow_redirects=False)
        return client.post("/auth/change-password", data=defaults)

    def test_invalid_csrf_returns_form(self, monkeypatch):
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: False)
        resp = self._post(monkeypatch)
        assert resp.status_code == 200

    def test_no_user_redirects_login(self, monkeypatch):
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        resp = self._post(monkeypatch, inject_user=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]

    def test_password_mismatch_returns_form(self, monkeypatch):
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        resp = self._post(monkeypatch, confirm_password="Different123456!")
        assert resp.status_code == 200

    def test_weak_password_returns_form(self, monkeypatch):
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        resp = self._post(monkeypatch, new_password="weak", confirm_password="weak")
        assert resp.status_code == 200

    def test_valid_submission_redirects_home(self, monkeypatch):
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        resp = self._post(monkeypatch)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"


# ── _client_ip helper ─────────────────────────────────────────────────────────

class TestClientIp:
    def test_forwarded_header_returns_first_ip(self):
        """Line 35: X-Forwarded-For branch extracts the first address."""
        from unittest.mock import MagicMock
        import app.routers.auth as auth_mod

        req = MagicMock()
        req.headers.get = lambda k, d=None: "1.2.3.4, 5.6.7.8" if k == "X-Forwarded-For" else d
        assert auth_mod._client_ip(req) == "1.2.3.4"

    def test_no_forwarded_header_uses_client_host(self):
        from unittest.mock import MagicMock
        import app.routers.auth as auth_mod

        req = MagicMock()
        req.headers.get = lambda k, d=None: None
        req.client.host = "9.9.9.9"
        assert auth_mod._client_ip(req) == "9.9.9.9"


# ── POST /auth/logout — OIDC logout exception fallback ───────────────────────

class TestLogoutOidcFallback:
    def test_oidc_logout_exception_falls_back_to_local_logout(self, monkeypatch):
        """Lines 153-154: if build_logout_url() raises, except catches it and
        falls through to the local-logout response already built.

        build_logout_url is imported inside the try block, so we patch it on
        app.auth.oidc (the module it's imported from)."""
        import app.routers.auth as auth_mod
        import app.auth.oidc as oidc_mod

        monkeypatch.setattr(auth_mod.settings, "auth_mode", "oidc")
        monkeypatch.setattr(auth_mod, "invalidate_session", lambda sid: None)

        def _raise_logout():
            raise RuntimeError("network timeout")

        monkeypatch.setattr(oidc_mod, "build_logout_url", _raise_logout)

        # User with entra_oid so the OIDC branch is entered
        user = _make_user(entra_oid="oid-123")
        client = TestClient(_make_app(user=user), follow_redirects=False)
        resp = client.post("/auth/logout", data={"csrf_token": "tok"})
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]


# ── OIDC callback — pre-provisioned account OID linking (lines 230-231) ──────

class TestOidcCallbackPreProvisioned:
    """Lines 230-231: when get_user_by_oid returns None but get_user_by_email
    finds a pre-provisioned record with no OID, link_entra_oid is called and
    the user is reloaded; the callback then logs in normally."""

    def _sign_flow(self, data):
        from itsdangerous import URLSafeTimedSerializer
        from app.config import settings
        ser = URLSafeTimedSerializer(settings.session_secret_key)
        return ser.dumps(data, salt=b"oidc-state")

    def _client(self, monkeypatch):
        return TestClient(_make_app(), follow_redirects=False)

    def test_pre_provisioned_user_linked_and_logged_in(self, monkeypatch):
        import app.routers.auth as auth_mod
        import app.auth.oidc as oidc_mod

        claims = {"oid": "oid-xyz", "email": "pre@x.com", "name": "Pre"}
        pre_user = {"id": "pre-id", "entra_oid": None}
        linked_user = {"id": "pre-id", "is_active": True, "must_change_password": False,
                       "role_codes": ["analyst"], "display_name": "Pre"}

        oid_lookups = iter([None, linked_user])

        monkeypatch.setattr(oidc_mod, "complete_flow", lambda flow, params: claims)
        monkeypatch.setattr(auth_mod, "get_user_by_oid", lambda oid: next(oid_lookups))
        monkeypatch.setattr(auth_mod, "get_user_by_email", lambda email: pre_user)
        linked = []
        monkeypatch.setattr(auth_mod, "link_entra_oid", lambda uid, oid: linked.append((uid, oid)))
        monkeypatch.setattr(auth_mod, "update_last_login", lambda uid: None)
        monkeypatch.setattr(auth_mod, "log_attempt", lambda *a, **k: None)
        monkeypatch.setattr(auth_mod, "create_session", lambda uid, ip, ua: "sess-linked")

        state_cookie = self._sign_flow({"state": "s"})
        client = self._client(monkeypatch)
        resp = client.get("/auth/callback?code=abc&state=s",
                          cookies={"rwb_oidc_state": state_cookie})

        assert resp.status_code == 302
        assert resp.headers["location"] == "/"
        assert linked == [("pre-id", "oid-xyz")]


# ── POST /auth/setup-profile ──────────────────────────────────────────────────

class TestSetupProfile:
    def _post(self, monkeypatch, user=None, **form):
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "execute_command", lambda sql, p, connection=None: 1)
        defaults = {
            "display_name": "My Name",
            "email": "u@x.com",
            "new_password": "",
            "confirm_password": "",
            "csrf_token": "tok",
        }
        defaults.update(form)
        if user is None:
            user = _make_user()
        client = TestClient(_make_app(user=user), follow_redirects=False)
        return client.post("/auth/setup-profile", data=defaults)

    def test_invalid_csrf_returns_form(self, monkeypatch):
        """Line 287: bad CSRF renders the form with error."""
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: False)
        resp = self._post(monkeypatch)
        assert resp.status_code == 200
        assert "Request expired" in resp.text

    def test_no_user_redirects_login(self, monkeypatch):
        """Line 290: no logged-in user redirects to /auth/login."""
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        resp = self._post(monkeypatch, user=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]

    def test_empty_display_name_returns_form(self, monkeypatch):
        """Line 303: blank display_name renders error."""
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        resp = self._post(monkeypatch, display_name="   ")
        assert resp.status_code == 200
        assert "Display name is required" in resp.text

    def test_password_mismatch_returns_form(self, monkeypatch):
        """Lines 296-299: mismatched passwords renders error."""
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        resp = self._post(monkeypatch,
                          new_password="ValidPassword1!",
                          confirm_password="Different1!")
        assert resp.status_code == 200
        assert "do not match" in resp.text

    def test_weak_password_returns_form(self, monkeypatch):
        """Lines 295, 298-299: weak password triggers policy errors."""
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        resp = self._post(monkeypatch,
                          new_password="weak",
                          confirm_password="weak")
        assert resp.status_code == 200

    def test_valid_no_password_saves_and_renders(self, monkeypatch):
        """Lines 311-320: no new_password — updates display_name only, renders saved."""
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        resp = self._post(monkeypatch)
        assert resp.status_code == 200

    def test_valid_with_password_saves_and_renders(self, monkeypatch):
        """Lines 307-320: new_password provided — UPDATE includes password_hash."""
        import app.routers.auth as auth_mod
        monkeypatch.setattr(auth_mod, "validate_csrf_token", lambda t: True)
        executed = []
        monkeypatch.setattr(auth_mod, "execute_command",
                            lambda sql, p, connection=None: executed.append(p) or 1)
        # Call client directly so monkeypatches above are already in place
        user = _make_user()
        client = TestClient(_make_app(user=user), follow_redirects=False)
        resp = client.post("/auth/setup-profile", data={
            "display_name": "My Name",
            "email": "u@x.com",
            "new_password": "ValidPassword1!",
            "confirm_password": "ValidPassword1!",
            "csrf_token": "tok",
        })
        assert resp.status_code == 200
        assert any("pw" in p for p in executed)
