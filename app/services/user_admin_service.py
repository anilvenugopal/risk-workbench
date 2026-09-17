"""User provisioning operations shared by administrative entry points."""

from __future__ import annotations

from sqlalchemy import text

from app.auth.password import hash_password
from db import execute, execute_command, execute_one, get_connection


def list_roles() -> list[dict]:
    return execute(
        "SELECT code, label FROM role_kind ORDER BY sort_order",
        connection="WORKBENCH",
    )


def list_users() -> list[dict]:
    return execute(
        """
        SELECT u.id, u.email, u.display_name, u.entra_oid, u.password_hash, u.is_active,
               STRING_AGG(rk.code, ', ') WITHIN GROUP (ORDER BY rk.sort_order) AS roles
        FROM app_user u
        LEFT JOIN user_role ur ON ur.user_id = u.id
        LEFT JOIN role_kind rk ON rk.code = ur.role_code
        GROUP BY u.id, u.email, u.display_name, u.entra_oid, u.password_hash, u.is_active
        ORDER BY u.email
        """,
        connection="WORKBENCH",
    )


def list_pending_oidc_users() -> list[dict]:
    return execute(
        """
        SELECT u.id, u.email, u.display_name
        FROM app_user u
        WHERE u.is_active = 1
          AND u.entra_oid IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM user_role ur WHERE ur.user_id = u.id)
        ORDER BY u.email
        """,
        connection="WORKBENCH",
    )


def list_password_users() -> list[dict]:
    return execute(
        """
        SELECT id, email, display_name
        FROM app_user
        WHERE password_hash IS NOT NULL AND is_active = 1
        ORDER BY email
        """,
        connection="WORKBENCH",
    )


def create_user(
    email: str,
    display_name: str,
    role_code: str | None,
    password: str | None = None,
) -> str:
    normalized_email = email.strip().lower()
    if execute_one(
        "SELECT id FROM app_user WHERE LOWER(email) = :email",
        {"email": normalized_email},
        connection="WORKBENCH",
    ):
        raise ValueError(f"A user with email {normalized_email} already exists.")

    password_hash = hash_password(password) if password is not None else None
    with get_connection("WORKBENCH") as conn, conn.begin():
        user_id = conn.execute(
            text(
                """
                INSERT INTO app_user
                    (email, display_name, password_hash, must_change_password, is_active)
                OUTPUT INSERTED.id
                VALUES (:email, :name, :password_hash, :must_change, 1)
                """
            ),
            {
                "email": normalized_email,
                "name": display_name.strip(),
                "password_hash": password_hash,
                "must_change": 1 if password is not None else 0,
            },
        ).scalar_one()
        if role_code:
            conn.execute(
                text(
                    """
                    INSERT INTO user_role (user_id, role_code, inserted_by)
                    VALUES (:user_id, :role_code, :user_id)
                    """
                ),
                {"user_id": user_id, "role_code": role_code},
            )
    return str(user_id)


def assign_role(user_id: str, role_code: str) -> None:
    execute_command(
        """
        IF NOT EXISTS (
            SELECT 1 FROM user_role WHERE user_id = :user_id AND role_code = :role_code
        )
        INSERT INTO user_role (user_id, role_code, inserted_by)
        VALUES (:user_id, :role_code, :user_id)
        """,
        {"user_id": user_id, "role_code": role_code},
        connection="WORKBENCH",
    )


def reset_password(user_id: str, password: str) -> None:
    execute_command(
        """
        UPDATE app_user
        SET password_hash = :password_hash,
            must_change_password = 1,
            updated_at = GETUTCDATE()
        WHERE id = :user_id
        """,
        {"password_hash": hash_password(password), "user_id": user_id},
        connection="WORKBENCH",
    )
