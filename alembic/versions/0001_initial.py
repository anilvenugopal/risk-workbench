"""Initial schema — WORKBENCH database.

Revision ID: 0001
Revises: (none)
Create Date: 2026-07-01

Dev strategy: drop-create-seed. This is the ONLY revision until production
cutover. Run `make db-rebuild` to drop and recreate from scratch rather than
accumulating incremental migrations.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mssql import DATETIME2

revision: str = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── role_kind ─────────────────────────────────────────────────────────────
    op.create_table(
        "role_kind",
        sa.Column("code", sa.NVARCHAR(50), primary_key=True),
        sa.Column("label", sa.NVARCHAR(255), nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
    )

    # ── customer (shell — FK target for user_customer_access) ─────────────────
    op.create_table(
        "customer",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("name", sa.NVARCHAR(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
    )

    # ── program (shell — depends on customer) ─────────────────────────────────
    op.create_table(
        "program",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("customer_id", sa.Uuid, nullable=False),
        sa.Column("name", sa.NVARCHAR(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"]),
    )

    # ── app_user ──────────────────────────────────────────────────────────────
    op.create_table(
        "app_user",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("entra_oid", sa.NVARCHAR(255), nullable=True),
        sa.Column("email", sa.NVARCHAR(255), nullable=False),
        sa.Column("display_name", sa.NVARCHAR(255), nullable=False),
        sa.Column("password_hash", sa.NVARCHAR(255), nullable=True),
        sa.Column("must_change_password", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("last_login_at", DATETIME2, nullable=True),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.UniqueConstraint("email"),
    )
    # Partial unique index for entra_oid (NULL allowed, but unique when set)
    op.create_index(
        "ix_app_user_entra_oid",
        "app_user",
        ["entra_oid"],
        unique=True,
        mssql_where=sa.text("entra_oid IS NOT NULL"),
    )

    # ── user_session ──────────────────────────────────────────────────────────
    op.create_table(
        "user_session",
        sa.Column("id", sa.CHAR(64), primary_key=True),
        sa.Column("user_id", sa.Uuid, nullable=False),
        sa.Column("created_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("last_active_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("expires_at", DATETIME2, nullable=False),
        sa.Column("invalidated_at", DATETIME2, nullable=True),
        sa.Column("ip_address", sa.NVARCHAR(45), nullable=True),
        sa.Column("user_agent", sa.NVARCHAR(512), nullable=True),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
    )

    # ── login_attempt ─────────────────────────────────────────────────────────
    op.create_table(
        "login_attempt",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("email", sa.NVARCHAR(255), nullable=False),
        sa.Column("auth_mode", sa.NVARCHAR(16), nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("failure_reason", sa.NVARCHAR(255), nullable=True),
        sa.Column("ip_address", sa.NVARCHAR(45), nullable=True),
        sa.Column("user_agent", sa.NVARCHAR(512), nullable=True),
        sa.Column("at", DATETIME2, nullable=False, server_default=sa.text("GETUTCDATE()")),
    )

    # ── user_role ─────────────────────────────────────────────────────────────
    op.create_table(
        "user_role",
        sa.Column("user_id", sa.Uuid, nullable=False),
        sa.Column("role_code", sa.NVARCHAR(50), nullable=False),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.PrimaryKeyConstraint("user_id", "role_code"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["role_code"], ["role_kind.code"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
    )

    # ── user_customer_access ──────────────────────────────────────────────────
    op.create_table(
        "user_customer_access",
        sa.Column("user_id", sa.Uuid, nullable=False),
        sa.Column("customer_id", sa.Uuid, nullable=False),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.PrimaryKeyConstraint("user_id", "customer_id"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
    )

    # ── Seeds ─────────────────────────────────────────────────────────────────
    op.execute(sa.text(
        "INSERT INTO role_kind (code, label, sort_order, is_admin) VALUES "
        "('analyst', 'Analyst', 10, 0), "
        "('admin', 'Administrator', 20, 1)"
    ))


def downgrade() -> None:
    op.drop_table("user_customer_access")
    op.drop_table("user_role")
    op.drop_table("login_attempt")
    op.drop_table("user_session")
    op.drop_index("ix_app_user_entra_oid", table_name="app_user")
    op.drop_table("app_user")
    op.drop_table("program")
    op.drop_table("customer")
    op.drop_table("role_kind")
