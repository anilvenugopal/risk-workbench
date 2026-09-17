from __future__ import annotations

import pytest

from app.services import user_admin_service


def test_create_user_rejects_duplicate_email(monkeypatch):
    monkeypatch.setattr(user_admin_service, "execute_one", lambda *a, **k: {"id": "u1"})

    with pytest.raises(ValueError, match="already exists"):
        user_admin_service.create_user("USER@example.com", "User", "admin", "Password1234")


def test_create_password_user_sets_forced_change_and_role(monkeypatch):
    statements = []

    class Result:
        def scalar_one(self):
            return "new-user"

    class Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Connection:
        def begin(self):
            return Transaction()

        def execute(self, statement, params):
            statements.append((str(statement), params))
            return Result()

    class ConnectionContext:
        def __enter__(self):
            return Connection()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(user_admin_service, "execute_one", lambda *a, **k: None)
    monkeypatch.setattr(user_admin_service, "hash_password", lambda password: "hashed")
    monkeypatch.setattr(user_admin_service, "get_connection", lambda name: ConnectionContext())

    user_id = user_admin_service.create_user(
        " USER@example.com ", " User Name ", "admin", "Password1234"
    )

    assert user_id == "new-user"
    assert statements[0][1] == {
        "email": "user@example.com",
        "name": "User Name",
        "password_hash": "hashed",
        "must_change": 1,
    }
    assert statements[1][1] == {"user_id": "new-user", "role_code": "admin"}


def test_reset_password_hashes_and_requires_change(monkeypatch):
    calls = []
    monkeypatch.setattr(user_admin_service, "hash_password", lambda password: "hashed")
    monkeypatch.setattr(
        user_admin_service,
        "execute_command",
        lambda sql, params, connection: calls.append((sql, params, connection)),
    )

    user_admin_service.reset_password("u1", "Password1234")

    assert calls[0][1] == {"password_hash": "hashed", "user_id": "u1"}
    assert "must_change_password = 1" in calls[0][0]
    assert calls[0][2] == "WORKBENCH"
