"""SQL Server integration tests for auth migration and schema.

Run with: make wsl-test-sql  (requires live SQL Server)
"""

from __future__ import annotations

import pytest

from db import execute, execute_command


pytestmark = pytest.mark.sqlserver


class TestAuthMigration:
    def test_role_kind_seeds_present(self):
        rows = execute(
            "SELECT code FROM role_kind ORDER BY sort_order",
            {},
            connection="WORKBENCH",
        )
        codes = [r["code"] for r in rows]
        assert "analyst" in codes
        assert "admin" in codes

    def test_app_user_table_exists(self):
        rows = execute(
            "SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_NAME = 'app_user' AND TABLE_SCHEMA = 'dbo'",
            {},
            connection="WORKBENCH",
        )
        assert rows[0]["n"] == 1

    def test_user_session_table_exists(self):
        rows = execute(
            "SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_NAME = 'user_session' AND TABLE_SCHEMA = 'dbo'",
            {},
            connection="WORKBENCH",
        )
        assert rows[0]["n"] == 1

    def test_role_kind_table_exists(self):
        rows = execute(
            "SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_NAME = 'role_kind' AND TABLE_SCHEMA = 'dbo'",
            {},
            connection="WORKBENCH",
        )
        assert rows[0]["n"] == 1

    def test_login_attempt_table_exists(self):
        rows = execute(
            "SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_NAME = 'login_attempt' AND TABLE_SCHEMA = 'dbo'",
            {},
            connection="WORKBENCH",
        )
        assert rows[0]["n"] == 1

    def test_user_role_table_exists(self):
        rows = execute(
            "SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_NAME = 'user_role' AND TABLE_SCHEMA = 'dbo'",
            {},
            connection="WORKBENCH",
        )
        assert rows[0]["n"] == 1


class TestSessionLifecycle:
    """End-to-end session insert + invalidate."""

    def test_session_insert_and_invalidate(self):
        import secrets
        from datetime import datetime, timedelta, timezone

        session_id = secrets.token_hex(32)
        # First insert a test user
        execute_command(
            "INSERT INTO app_user (email, display_name, must_change_password, is_active) "
            "VALUES (:email, :name, 0, 1)",
            {"email": f"test_{session_id[:8]}@example.com", "name": "Test User"},
            connection="WORKBENCH",
        )
        user_row = execute(
            "SELECT id FROM app_user WHERE email = :email",
            {"email": f"test_{session_id[:8]}@example.com"},
            connection="WORKBENCH",
        )
        user_id = user_row[0]["id"]

        expires = datetime.now(timezone.utc) + timedelta(hours=24)
        execute_command(
            "INSERT INTO user_session (id, user_id, expires_at) VALUES (:id, :uid, :exp)",
            {"id": session_id, "uid": user_id, "exp": expires},
            connection="WORKBENCH",
        )

        # Verify session exists
        rows = execute(
            "SELECT id FROM user_session WHERE id = :id AND invalidated_at IS NULL",
            {"id": session_id},
            connection="WORKBENCH",
        )
        assert len(rows) == 1

        # Invalidate
        execute_command(
            "UPDATE user_session SET invalidated_at = GETUTCDATE() WHERE id = :id",
            {"id": session_id},
            connection="WORKBENCH",
        )

        # Verify invalidated
        rows = execute(
            "SELECT id FROM user_session WHERE id = :id AND invalidated_at IS NULL",
            {"id": session_id},
            connection="WORKBENCH",
        )
        assert len(rows) == 0

        # Cleanup
        execute_command(
            "DELETE FROM user_session WHERE id = :id", {"id": session_id},
            connection="WORKBENCH",
        )
        execute_command(
            "DELETE FROM app_user WHERE id = :uid", {"uid": user_id},
            connection="WORKBENCH",
        )
