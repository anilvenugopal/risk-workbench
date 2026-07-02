"""Extended unit tests for auth_service — validate_session, lookups, audit."""

from __future__ import annotations


class TestValidateSession:
    def test_returns_none_when_no_rows(self, monkeypatch):
        from app.services import auth_service

        monkeypatch.setattr(auth_service, "execute", lambda sql, params, connection=None: [])
        monkeypatch.setattr(auth_service, "execute_command", lambda sql, params, connection=None: None)

        assert auth_service.validate_session("deadbeef") is None

    def test_returns_current_user_on_valid_session(self, monkeypatch):
        from app.services import auth_service

        fake_row = {
            "user_id": "uuid-123",
            "email": "test@example.com",
            "display_name": "Test User",
            "entra_oid": None,
            "is_active": True,
            "must_change_password": False,
            "role_codes": "analyst",
            "is_admin": 0,
        }
        monkeypatch.setattr(auth_service, "execute", lambda sql, params, connection=None: [fake_row])
        monkeypatch.setattr(auth_service, "execute_command", lambda sql, params, connection=None: None)

        user = auth_service.validate_session("abc123")
        assert user is not None
        assert user.email == "test@example.com"
        assert user.role_codes == ["analyst"]
        assert user.is_admin is False
        assert user.session_id == "abc123"

    def test_role_codes_split_correctly(self, monkeypatch):
        from app.services import auth_service

        fake_row = {
            "user_id": "uuid-456",
            "email": "admin@example.com",
            "display_name": "Admin",
            "entra_oid": "oid-abc",
            "is_active": True,
            "must_change_password": False,
            "role_codes": "analyst,admin",
            "is_admin": 1,
        }
        monkeypatch.setattr(auth_service, "execute", lambda sql, params, connection=None: [fake_row])
        monkeypatch.setattr(auth_service, "execute_command", lambda sql, params, connection=None: None)

        user = auth_service.validate_session("sess-xyz")
        assert set(user.role_codes) == {"analyst", "admin"}
        assert user.is_admin is True

    def test_null_role_codes_gives_empty_list(self, monkeypatch):
        from app.services import auth_service

        fake_row = {
            "user_id": "uuid-789",
            "email": "pending@example.com",
            "display_name": "Pending",
            "entra_oid": "oid-xyz",
            "is_active": True,
            "must_change_password": False,
            "role_codes": None,
            "is_admin": None,
        }
        monkeypatch.setattr(auth_service, "execute", lambda sql, params, connection=None: [fake_row])
        monkeypatch.setattr(auth_service, "execute_command", lambda sql, params, connection=None: None)

        user = auth_service.validate_session("sess-000")
        assert user.role_codes == []
        assert user.is_admin is False

    def test_updates_last_active(self, monkeypatch):
        from app.services import auth_service

        fake_row = {
            "user_id": "uuid-111",
            "email": "x@x.com",
            "display_name": "X",
            "entra_oid": None,
            "is_active": True,
            "must_change_password": False,
            "role_codes": "analyst",
            "is_admin": 0,
        }
        monkeypatch.setattr(auth_service, "execute", lambda sql, params, connection=None: [fake_row])
        commands = []
        monkeypatch.setattr(auth_service, "execute_command", lambda sql, params, connection=None: commands.append(sql))

        auth_service.validate_session("sess-upd")
        assert any("last_active_at" in s for s in commands)


class TestInvalidateAllSessions:
    def test_passes_user_id(self, monkeypatch):
        from app.services import auth_service

        captured = []
        monkeypatch.setattr(auth_service, "execute_command",
                            lambda sql, params, connection=None: captured.append(params))

        auth_service.invalidate_all_sessions("user-abc")
        assert captured[0]["uid"] == "user-abc"


class TestGetUserByEmail:
    def test_returns_row_when_found(self, monkeypatch):
        from app.services import auth_service

        fake = {"id": "u1", "email": "a@b.com"}
        monkeypatch.setattr(auth_service, "execute", lambda sql, params, connection=None: [fake])

        result = auth_service.get_user_by_email("a@b.com")
        assert result["id"] == "u1"

    def test_returns_none_when_not_found(self, monkeypatch):
        from app.services import auth_service

        monkeypatch.setattr(auth_service, "execute", lambda sql, params, connection=None: [])

        assert auth_service.get_user_by_email("missing@x.com") is None

    def test_passes_email_param(self, monkeypatch):
        from app.services import auth_service

        captured = []
        monkeypatch.setattr(auth_service, "execute",
                            lambda sql, params, connection=None: captured.append(params) or [])

        auth_service.get_user_by_email("Test@Example.COM")
        assert captured[0]["email"] == "Test@Example.COM"


class TestGetUserById:
    def test_returns_row_when_found(self, monkeypatch):
        from app.services import auth_service

        fake = {"id": "u2", "email": "b@b.com"}
        monkeypatch.setattr(auth_service, "execute", lambda sql, params, connection=None: [fake])

        assert auth_service.get_user_by_id("u2")["email"] == "b@b.com"

    def test_returns_none_when_not_found(self, monkeypatch):
        from app.services import auth_service

        monkeypatch.setattr(auth_service, "execute", lambda sql, params, connection=None: [])

        assert auth_service.get_user_by_id("nope") is None


class TestGetUserByOid:
    def test_returns_row_when_found(self, monkeypatch):
        from app.services import auth_service

        fake = {"id": "u3", "entra_oid": "oid-1"}
        monkeypatch.setattr(auth_service, "execute", lambda sql, params, connection=None: [fake])

        assert auth_service.get_user_by_oid("oid-1")["id"] == "u3"

    def test_returns_none_when_not_found(self, monkeypatch):
        from app.services import auth_service

        monkeypatch.setattr(auth_service, "execute", lambda sql, params, connection=None: [])

        assert auth_service.get_user_by_oid("no-such-oid") is None


class TestUpdateLastLogin:
    def test_passes_user_id(self, monkeypatch):
        from app.services import auth_service

        captured = []
        monkeypatch.setattr(auth_service, "execute_command",
                            lambda sql, params, connection=None: captured.append(params))

        auth_service.update_last_login("user-xyz")
        assert captured[0]["id"] == "user-xyz"


class TestLogAttempt:
    def test_success_flag_true(self, monkeypatch):
        from app.services import auth_service

        captured = []
        monkeypatch.setattr(auth_service, "execute_command",
                            lambda sql, params, connection=None: captured.append(params))

        auth_service.log_attempt("a@b.com", "oidc", True, None, "1.2.3.4", "UA")
        assert captured[0]["success"] == 1

    def test_success_flag_false(self, monkeypatch):
        from app.services import auth_service

        captured = []
        monkeypatch.setattr(auth_service, "execute_command",
                            lambda sql, params, connection=None: captured.append(params))

        auth_service.log_attempt("a@b.com", "password", False, "bad_pw", "1.2.3.4", "UA")
        assert captured[0]["success"] == 0
        assert captured[0]["reason"] == "bad_pw"

    def test_none_ip_stored_as_empty(self, monkeypatch):
        from app.services import auth_service

        captured = []
        monkeypatch.setattr(auth_service, "execute_command",
                            lambda sql, params, connection=None: captured.append(params))

        auth_service.log_attempt("a@b.com", "password", True, None, None, None)
        assert captured[0]["ip"] == ""
        assert captured[0]["ua"] == ""

    def test_ip_truncated_to_45_chars(self, monkeypatch):
        from app.services import auth_service

        captured = []
        monkeypatch.setattr(auth_service, "execute_command",
                            lambda sql, params, connection=None: captured.append(params))

        auth_service.log_attempt("a@b.com", "password", True, None, "x" * 100, "UA")
        assert len(captured[0]["ip"]) == 45
