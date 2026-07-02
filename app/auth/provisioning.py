"""JIT provisioning for OIDC users."""

from __future__ import annotations

from db import execute_command, execute_one


def jit_provision_oidc_user(oid: str, email: str, display_name: str) -> str:
    """Insert a new app_user for an OIDC account; return the new user ID.

    Idempotent: if the OID already exists (race/retry), returns the existing ID.
    """
    from app.services.auth_service import get_user_by_oid
    existing = get_user_by_oid(oid)
    if existing:
        return str(existing["id"])

    execute_command(
        """
        INSERT INTO app_user (entra_oid, email, display_name, must_change_password, is_active)
        VALUES (:oid, :email, :display_name, 0, 1)
        """,
        {"oid": oid, "email": email, "display_name": display_name},
        connection="WORKBENCH",
    )
    row = execute_one(
        "SELECT id FROM app_user WHERE entra_oid = :oid",
        {"oid": oid},
        connection="WORKBENCH",
    )
    return str(row["id"])
