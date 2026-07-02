"""Unit tests for app/routers/admin.py.

Key invariant: every admin route must reject non-admin users by redirecting to /.
We test that gate first, then the happy path and error branches for each action.

Strategy: same minimal-app pattern as test_auth_routes and test_shell_routes.
db.execute and db.execute_command are patched on the admin module so no DB
is needed. Real Jinja2 templates are used for HTML routes.
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
        id="admin-user-id", email="admin@example.com", display_name="Admin",
        session_id="sess-admin", role_codes=["admin"], is_admin=True,
        must_change_password=False, entra_oid=None, is_active=True,
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
    from app.routers.admin import router
    from app.config import settings

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates

    app.add_middleware(_InjectUser, user=user or _make_user())
    app.include_router(router)
    return app


def _noop_execute(sql, params, connection=None):
    return []


def _noop_command(sql, params, connection=None):
    return 1


# ── Admin gate ────────────────────────────────────────────────────────────────

class TestAdminGate:
    """Every route must block non-admin users."""

    @pytest.fixture
    def non_admin_client(self):
        return TestClient(
            _make_app(user=_make_user(is_admin=False, role_codes=["analyst"])),
            follow_redirects=False,
        )

    def test_user_list_blocked(self, non_admin_client):
        assert non_admin_client.get("/admin/users").status_code == 302

    def test_new_user_form_blocked(self, non_admin_client):
        assert non_admin_client.get("/admin/users/new").status_code == 302

    def test_create_user_blocked(self, non_admin_client):
        assert non_admin_client.post(
            "/admin/users/new",
            data={"display_name": "X", "email": "x@x.com",
                  "password": "P", "csrf_token": "t"},
        ).status_code == 302

    def test_user_detail_blocked(self, non_admin_client):
        assert non_admin_client.get("/admin/users/some-id").status_code == 302

    def test_reset_password_blocked(self, non_admin_client):
        assert non_admin_client.post(
            "/admin/users/some-id/reset-password",
            data={"new_password": "x", "csrf_token": "t"},
        ).status_code == 302

    def test_assign_role_blocked(self, non_admin_client):
        assert non_admin_client.post(
            "/admin/users/some-id/assign-role",
            data={"role_code": "analyst", "csrf_token": "t"},
        ).status_code == 302

    def test_force_logout_blocked(self, non_admin_client):
        assert non_admin_client.post(
            "/admin/users/some-id/force-logout",
            data={"csrf_token": "t"},
        ).status_code == 302


# ── GET /admin/users ──────────────────────────────────────────────────────────

class TestUserList:
    def test_renders_user_list(self, monkeypatch):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "execute", lambda sql, p, connection=None: [
            {"id": "u1", "email": "a@b.com", "display_name": "A",
             "is_active": True, "must_change_password": False,
             "last_login_at": None, "entra_oid": None, "roles": "analyst"},
        ])
        resp = TestClient(_make_app()).get("/admin/users")
        assert resp.status_code == 200
        assert "a@b.com" in resp.text

    def test_empty_user_list_renders(self, monkeypatch):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "execute", _noop_execute)
        resp = TestClient(_make_app()).get("/admin/users")
        assert resp.status_code == 200


# ── GET /admin/users/new ──────────────────────────────────────────────────────

class TestNewUserForm:
    def test_renders_empty_form(self, monkeypatch):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "execute", lambda *a, **kw: [])
        resp = TestClient(_make_app()).get("/admin/users/new")
        assert resp.status_code == 200


# ── POST /admin/users/new ─────────────────────────────────────────────────────

class TestCreateUser:
    def test_invalid_csrf_redirects(self, monkeypatch):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "validate_csrf_token", lambda t: False)
        monkeypatch.setattr(admin_mod, "execute_command", _noop_command)
        resp = TestClient(_make_app(), follow_redirects=False).post(
            "/admin/users/new",
            data={"display_name": "X", "email": "x@x.com",
                  "password": "P", "csrf_token": "bad"},
        )
        assert resp.status_code == 302
        assert "/admin/users" in resp.headers["location"]

    def test_weak_password_returns_form_with_errors(self, monkeypatch):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "validate_csrf_token", lambda t: True)
        monkeypatch.setattr(admin_mod, "execute_command", _noop_command)
        resp = TestClient(_make_app()).post(
            "/admin/users/new",
            data={"display_name": "X", "email": "x@x.com",
                  "password": "weak", "csrf_token": "t"},
        )
        assert resp.status_code == 200

    def test_valid_user_created_and_redirects(self, monkeypatch):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "validate_csrf_token", lambda t: True)
        commands = []
        monkeypatch.setattr(admin_mod, "execute_command",
                            lambda sql, p, connection=None: commands.append(p) or 1)
        resp = TestClient(_make_app(), follow_redirects=False).post(
            "/admin/users/new",
            data={"display_name": "New User", "email": "new@x.com",
                  "password": "ValidPassword1!", "csrf_token": "t"},
        )
        assert resp.status_code == 302
        assert "/admin/users" in resp.headers["location"]
        assert any(p.get("email") == "new@x.com" for p in commands)


# ── GET /admin/users/{id} ─────────────────────────────────────────────────────

class TestUserDetail:
    def test_not_found_redirects_to_list(self, monkeypatch):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "execute", _noop_execute)
        resp = TestClient(_make_app(), follow_redirects=False).get("/admin/users/no-such-id")
        assert resp.status_code == 302
        assert "/admin/users" in resp.headers["location"]

    def test_found_renders_detail(self, monkeypatch):
        import app.routers.admin as admin_mod
        fake_user = {"id": "u1", "email": "u@x.com", "display_name": "U",
                     "is_active": True, "must_change_password": False,
                     "last_login_at": None, "entra_oid": None, "roles": None}
        call_count = [0]
        def _execute(sql, p, connection=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return [fake_user]
            return [{"code": "analyst", "label": "Analyst"}]
        monkeypatch.setattr(admin_mod, "execute", _execute)
        resp = TestClient(_make_app()).get("/admin/users/u1")
        assert resp.status_code == 200
        assert "u@x.com" in resp.text


# ── POST /admin/users/{id}/reset-password ─────────────────────────────────────

class TestResetPassword:
    def test_invalid_csrf_redirects_back(self, monkeypatch):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "validate_csrf_token", lambda t: False)
        monkeypatch.setattr(admin_mod, "execute_command", _noop_command)
        resp = TestClient(_make_app(), follow_redirects=False).post(
            "/admin/users/u1/reset-password",
            data={"new_password": "x", "csrf_token": "bad"},
        )
        assert resp.status_code == 302
        assert "/admin/users/u1" in resp.headers["location"]

    def test_valid_reset_updates_db_and_redirects(self, monkeypatch):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "validate_csrf_token", lambda t: True)
        commands = []
        monkeypatch.setattr(admin_mod, "execute_command",
                            lambda sql, p, connection=None: commands.append(p) or 1)
        resp = TestClient(_make_app(), follow_redirects=False).post(
            "/admin/users/u1/reset-password",
            data={"new_password": "NewPass1!", "csrf_token": "t"},
        )
        assert resp.status_code == 302
        assert len(commands) == 1
        assert commands[0]["id"] == "u1"


# ── POST /admin/users/{id}/assign-role ────────────────────────────────────────

class TestAssignRole:
    def test_invalid_csrf_redirects_back(self, monkeypatch):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "validate_csrf_token", lambda t: False)
        monkeypatch.setattr(admin_mod, "execute_command", _noop_command)
        resp = TestClient(_make_app(), follow_redirects=False).post(
            "/admin/users/u1/assign-role",
            data={"role_code": "analyst", "csrf_token": "bad"},
        )
        assert resp.status_code == 302
        assert "/admin/users/u1" in resp.headers["location"]

    def test_valid_assign_writes_to_db_and_redirects(self, monkeypatch):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "validate_csrf_token", lambda t: True)
        commands = []
        monkeypatch.setattr(admin_mod, "execute_command",
                            lambda sql, p, connection=None: commands.append(p) or 1)
        resp = TestClient(_make_app(), follow_redirects=False).post(
            "/admin/users/u1/assign-role",
            data={"role_code": "analyst", "csrf_token": "t"},
        )
        assert resp.status_code == 302
        assert commands[0]["uid"] == "u1"
        assert commands[0]["role"] == "analyst"

    def test_assigning_role_records_who_made_change(self, monkeypatch):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "validate_csrf_token", lambda t: True)
        commands = []
        monkeypatch.setattr(admin_mod, "execute_command",
                            lambda sql, p, connection=None: commands.append(p) or 1)
        TestClient(_make_app(), follow_redirects=False).post(
            "/admin/users/u1/assign-role",
            data={"role_code": "analyst", "csrf_token": "t"},
        )
        assert commands[0]["by"] == "admin-user-id"


# ── POST /admin/users/{id}/force-logout ───────────────────────────────────────

class TestForceLogout:
    def test_invalid_csrf_redirects_back(self, monkeypatch):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "validate_csrf_token", lambda t: False)
        resp = TestClient(_make_app(), follow_redirects=False).post(
            "/admin/users/u1/force-logout",
            data={"csrf_token": "bad"},
        )
        assert resp.status_code == 302
        assert "/admin/users/u1" in resp.headers["location"]

    def test_valid_force_logout_invalidates_all_sessions(self, monkeypatch):
        import app.routers.admin as admin_mod
        from app.services import auth_service
        monkeypatch.setattr(admin_mod, "validate_csrf_token", lambda t: True)
        invalidated = []
        monkeypatch.setattr(auth_service, "invalidate_all_sessions",
                            lambda uid: invalidated.append(uid))
        resp = TestClient(_make_app(), follow_redirects=False).post(
            "/admin/users/u1/force-logout",
            data={"csrf_token": "t"},
        )
        assert resp.status_code == 302
        assert "u1" in invalidated


# ── POST /admin/users/provision-oidc ─────────────────────────────────────────

class TestProvisionOidcUser:
    def _post(self, monkeypatch, execute_side_effect=None, **form):
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "validate_csrf_token", lambda t: True)

        call_count = [0]
        seq = execute_side_effect or []

        def _execute(sql, params, connection=None):
            idx = call_count[0]
            call_count[0] += 1
            if idx < len(seq):
                return seq[idx]
            return []

        monkeypatch.setattr(admin_mod, "execute", _execute)
        monkeypatch.setattr(admin_mod, "execute_command", _noop_command)

        defaults = {
            "display_name": "Alice",
            "email": "alice@example.com",
            "role_code": "analyst",
            "csrf_token": "tok",
        }
        defaults.update(form)
        return TestClient(_make_app(), follow_redirects=False).post(
            "/admin/users/provision-oidc", data=defaults
        )

    def test_invalid_csrf_redirects(self, monkeypatch):
        """Line 252-253: bad CSRF redirects to /admin/users."""
        import app.routers.admin as admin_mod
        monkeypatch.setattr(admin_mod, "validate_csrf_token", lambda t: False)
        monkeypatch.setattr(admin_mod, "execute", _noop_execute)
        monkeypatch.setattr(admin_mod, "execute_command", _noop_command)
        resp = TestClient(_make_app(), follow_redirects=False).post(
            "/admin/users/provision-oidc",
            data={"display_name": "X", "email": "x@x.com",
                  "role_code": "analyst", "csrf_token": "bad"},
        )
        assert resp.status_code == 302
        assert "/admin/users" in resp.headers["location"]

    def test_existing_user_assigns_role_and_redirects(self, monkeypatch):
        """Lines 263-264: email already exists — skips INSERT, assigns role."""
        resp = self._post(
            monkeypatch,
            execute_side_effect=[[{"id": "existing-id"}]],
        )
        assert resp.status_code == 302
        assert "/admin/users/existing-id" in resp.headers["location"]

    def test_new_user_inserted_then_role_assigned(self, monkeypatch):
        """Lines 265-279: no existing row — INSERT then SELECT id, then assign role."""
        resp = self._post(
            monkeypatch,
            execute_side_effect=[[], [{"id": "new-id"}]],
        )
        assert resp.status_code == 302
        assert "/admin/users/new-id" in resp.headers["location"]
