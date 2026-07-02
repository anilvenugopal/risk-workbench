"""Authentication service — session lifecycle, user lookup, login audit.

All DB access goes through db.execute() / db.get_connection() (safe path).
No raw SQL strings with user data; all parameters are bound.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

from db import execute, execute_one, execute_command, get_connection

# Session timeouts (can be overridden via env vars)
SESSION_IDLE_TIMEOUT_HOURS = int(os.getenv("SESSION_IDLE_TIMEOUT_HOURS", "8"))
SESSION_ABSOLUTE_TIMEOUT_HOURS = int(os.getenv("SESSION_ABSOLUTE_TIMEOUT_HOURS", "24"))


class CurrentUser:
    """Lightweight value object attached to request.state.user."""

    __slots__ = (
        "id", "email", "display_name", "is_active",
        "must_change_password", "role_codes", "is_admin",
        "session_id", "entra_oid",
    )

    def __init__(self, **kwargs):
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))

    @property
    def is_password_account(self) -> bool:
        return self.entra_oid is None


# ── Session management ────────────────────────────────────────────────────────

def create_session(user_id: str, ip: str | None, user_agent: str | None) -> str:
    """Insert a user_session row and return the 64-char hex session ID."""
    session_id = secrets.token_hex(32)  # 32 bytes = 64 hex chars
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_ABSOLUTE_TIMEOUT_HOURS)
    execute_command(
        """
        INSERT INTO user_session (id, user_id, expires_at, ip_address, user_agent)
        VALUES (:id, :user_id, :expires_at, :ip, :ua)
        """,
        {
            "id": session_id,
            "user_id": user_id,
            "expires_at": expires_at,
            "ip": (ip or "")[:45],
            "ua": (user_agent or "")[:512],
        },
        connection="WORKBENCH",
    )
    return session_id


def validate_session(session_id: str) -> CurrentUser | None:
    """Check session validity, update last_active_at, return CurrentUser or None."""
    idle_secs = SESSION_IDLE_TIMEOUT_HOURS * 3600
    rows = execute(
        """
        SELECT
            u.id          AS user_id,
            u.email,
            u.display_name,
            u.entra_oid,
            u.is_active,
            u.must_change_password,
            STRING_AGG(rk.code, ',') WITHIN GROUP (ORDER BY rk.sort_order) AS role_codes,
            MAX(CAST(rk.is_admin AS INT)) AS is_admin
        FROM user_session s
        JOIN app_user u ON s.user_id = u.id
        LEFT JOIN user_role ur ON ur.user_id = u.id
        LEFT JOIN role_kind rk ON rk.code = ur.role_code
        WHERE s.id = :id
          AND s.invalidated_at IS NULL
          AND s.last_active_at > DATEADD(SECOND, -:idle, GETUTCDATE())
          AND s.expires_at > GETUTCDATE()
          AND u.is_active = 1
        GROUP BY u.id, u.email, u.display_name, u.entra_oid, u.is_active, u.must_change_password
        """,
        {"id": session_id, "idle": idle_secs},
        connection="WORKBENCH",
    )
    if not rows:
        return None

    row = rows[0]
    # Update last_active_at (sliding idle window)
    execute_command(
        "UPDATE user_session SET last_active_at = GETUTCDATE() WHERE id = :id",
        {"id": session_id},
        connection="WORKBENCH",
    )
    role_codes = [r for r in (row["role_codes"] or "").split(",") if r]
    return CurrentUser(
        id=str(row["user_id"]),
        email=row["email"],
        display_name=row["display_name"],
        entra_oid=row["entra_oid"],
        is_active=bool(row["is_active"]),
        must_change_password=bool(row["must_change_password"]),
        role_codes=role_codes,
        is_admin=bool(row["is_admin"] or 0),
        session_id=session_id,
    )


def invalidate_session(session_id: str) -> None:
    """Set invalidated_at on a session (logout / admin force-logout)."""
    execute_command(
        "UPDATE user_session SET invalidated_at = GETUTCDATE() WHERE id = :id",
        {"id": session_id},
        connection="WORKBENCH",
    )


def invalidate_all_sessions(user_id: str) -> None:
    """Invalidate all active sessions for a user (admin force-logout)."""
    execute_command(
        """
        UPDATE user_session
        SET invalidated_at = GETUTCDATE()
        WHERE user_id = :uid AND invalidated_at IS NULL
        """,
        {"uid": user_id},
        connection="WORKBENCH",
    )


# ── User lookup ───────────────────────────────────────────────────────────────

def get_user_by_email(email: str) -> dict | None:
    """Case-insensitive email lookup. Returns raw row dict or None."""
    rows = execute(
        "SELECT id, email, display_name, password_hash, must_change_password, "
        "is_active, entra_oid FROM app_user WHERE LOWER(email) = LOWER(:email)",
        {"email": email},
        connection="WORKBENCH",
    )
    return rows[0] if rows else None


def get_user_by_id(user_id: str) -> dict | None:
    rows = execute(
        "SELECT id, email, display_name, password_hash, must_change_password, "
        "is_active, entra_oid FROM app_user WHERE id = :id",
        {"id": user_id},
        connection="WORKBENCH",
    )
    return rows[0] if rows else None


def get_user_by_oid(oid: str) -> dict | None:
    """Lookup by Entra OID."""
    rows = execute(
        "SELECT id, email, display_name, is_active, must_change_password, entra_oid "
        "FROM app_user WHERE entra_oid = :oid",
        {"oid": oid},
        connection="WORKBENCH",
    )
    return rows[0] if rows else None


def update_last_login(user_id: str) -> None:
    execute_command(
        "UPDATE app_user SET last_login_at = GETUTCDATE(), updated_at = GETUTCDATE() "
        "WHERE id = :id",
        {"id": user_id},
        connection="WORKBENCH",
    )


def link_entra_oid(user_id: str, oid: str) -> None:
    """Attach an Entra OID to an admin-pre-provisioned account on first OIDC sign-in."""
    execute_command(
        "UPDATE app_user SET entra_oid = :oid, updated_at = GETUTCDATE() WHERE id = :id",
        {"oid": oid, "id": user_id},
        connection="WORKBENCH",
    )


# ── Login audit ───────────────────────────────────────────────────────────────

def log_attempt(
    email: str,
    auth_mode: str,
    success: bool,
    reason: str | None,
    ip: str | None,
    user_agent: str | None,
) -> None:
    execute_command(
        """
        INSERT INTO login_attempt (email, auth_mode, success, failure_reason, ip_address, user_agent)
        VALUES (:email, :auth_mode, :success, :reason, :ip, :ua)
        """,
        {
            "email": email,
            "auth_mode": auth_mode,
            "success": 1 if success else 0,
            "reason": reason,
            "ip": (ip or "")[:45],
            "ua": (user_agent or "")[:512],
        },
        connection="WORKBENCH",
    )
