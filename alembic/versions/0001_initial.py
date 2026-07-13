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

    # ══════════════════════════════════════════════════════════════════════════
    #  Iteration 1 — Submission & Package domain (data-model §1–§7)
    #  Created in FK dependency order: kinds → package → submission →
    #  children/join → irp_edm/irp_rdm.
    # ══════════════════════════════════════════════════════════════════════════

    # ── treaty_type_kind (kind) ─────────────────────────────────────────────────
    op.create_table(
        "treaty_type_kind",
        sa.Column("code", sa.NVARCHAR(50), primary_key=True),
        sa.Column("label", sa.NVARCHAR(255), nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
    )

    # ── submission_status_kind (kind) ───────────────────────────────────────────
    op.create_table(
        "submission_status_kind",
        sa.Column("code", sa.NVARCHAR(50), primary_key=True),
        sa.Column("label", sa.NVARCHAR(255), nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
    )

    # ── package (bundle — structure only this iteration) ────────────────────────
    op.create_table(
        "package",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("name", sa.NVARCHAR(255), nullable=True),
        sa.Column("deleted_at", DATETIME2, nullable=True),  # soft delete (FR-027)
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.Column("updated_by", sa.Uuid, nullable=True),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["app_user.id"]),
    )

    # ── submission (the deal — top-level entity) ────────────────────────────────
    op.create_table(
        "submission",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("assigned_analyst_id", sa.Uuid, nullable=False),  # soft owner (Article 6)
        sa.Column("name", sa.NVARCHAR(255), nullable=False),        # NOT unique (FR-003)
        sa.Column("cedant_name", sa.NVARCHAR(255), nullable=False),
        sa.Column("treaty_type_code", sa.NVARCHAR(50), nullable=False),
        sa.Column("inception_date", sa.Date, nullable=False),
        sa.Column("treaty_year", sa.Integer, nullable=True),
        sa.Column("renews_from_submission_id", sa.Uuid, nullable=True),  # self-ref
        sa.Column("directory_path", sa.NVARCHAR(1024), nullable=True),
        sa.Column("status_code", sa.NVARCHAR(50), nullable=False,
                  server_default=sa.text("'ACTIVE'")),  # cached current (Article 4)
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),  # concurrency marker (R1)
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.Column("updated_by", sa.Uuid, nullable=True),
        sa.ForeignKeyConstraint(["assigned_analyst_id"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["treaty_type_code"], ["treaty_type_kind.code"]),
        sa.ForeignKeyConstraint(["status_code"], ["submission_status_kind.code"]),
        sa.ForeignKeyConstraint(["renews_from_submission_id"], ["submission.id"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["app_user.id"]),
        # No self-renewal (FR-007 / R9).
        sa.CheckConstraint(
            "renews_from_submission_id IS NULL OR renews_from_submission_id <> id",
            name="ck_submission_no_self_renewal",
        ),
        # No UNIQUE(name) (FR-003); no customer_id/scope column (Article 6).
    )
    op.create_index("ix_submission_assigned_analyst_id", "submission",
                    ["assigned_analyst_id"])
    op.create_index("ix_submission_cedant_name", "submission", ["cedant_name"])
    op.create_index("ix_submission_treaty_type_code", "submission",
                    ["treaty_type_code"])
    op.create_index("ix_submission_inception_date", "submission", ["inception_date"])

    # ── submission_crm_id (0..N CRM tags) ───────────────────────────────────────
    op.create_table(
        "submission_crm_id",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("submission_id", sa.Uuid, nullable=False),
        sa.Column("crm_id", sa.NVARCHAR(255), nullable=False),  # unvalidated (FR-018)
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.ForeignKeyConstraint(["submission_id"], ["submission.id"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
    )
    op.create_index("ix_submission_crm_id_submission_id", "submission_crm_id",
                    ["submission_id"])

    # ── submission_status_event (append-only status history, Article 4) ─────────
    op.create_table(
        "submission_status_event",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("submission_id", sa.Uuid, nullable=False),
        sa.Column("status_code", sa.NVARCHAR(50), nullable=False),  # transitioned TO
        sa.Column("reason", sa.NVARCHAR(1024), nullable=True),
        sa.Column("at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.ForeignKeyConstraint(["submission_id"], ["submission.id"]),
        sa.ForeignKeyConstraint(["status_code"], ["submission_status_kind.code"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
    )
    op.create_index("ix_submission_status_event_submission_id",
                    "submission_status_event", ["submission_id"])

    # ── submission_package (deal ↔ package M:N — composite PK) ───────────────────
    op.create_table(
        "submission_package",
        sa.Column("submission_id", sa.Uuid, nullable=False),
        sa.Column("package_id", sa.Uuid, nullable=False),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.PrimaryKeyConstraint("submission_id", "package_id"),
        sa.ForeignKeyConstraint(["submission_id"], ["submission.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["package.id"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
    )

    # ── irp_edm (member table — full shape, schema only; data-model §7) ──────────
    op.create_table(
        "irp_edm",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("package_id", sa.Uuid, nullable=True),  # bundle membership (FR-023)
        sa.Column("source_file_path", sa.NVARCHAR(1024), nullable=True),
        sa.Column("name", sa.NVARCHAR(255), nullable=False),
        sa.Column("irp_id", sa.Integer, nullable=True),
        sa.Column("created_by_irp_job_irp_id", sa.NVARCHAR(64), nullable=True),
        sa.Column("as_of", DATETIME2, nullable=True),
        sa.Column("server_name", sa.NVARCHAR(255), nullable=True),
        # plain VARCHAR — external-status mirror (Article 3 carve-out); inert here.
        sa.Column("status", sa.NVARCHAR(50), nullable=True),
        sa.Column("deleted_at", DATETIME2, nullable=True),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.Column("updated_by", sa.Uuid, nullable=True),
        sa.ForeignKeyConstraint(["package_id"], ["package.id"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["app_user.id"]),
    )
    op.create_index("ix_irp_edm_package_id", "irp_edm", ["package_id"])

    # ── irp_rdm (member table — full shape, schema only; data-model §7) ──────────
    op.create_table(
        "irp_rdm",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("package_id", sa.Uuid, nullable=True),  # bundle membership (FR-023)
        sa.Column("source_file_path", sa.NVARCHAR(1024), nullable=True),
        sa.Column("name", sa.NVARCHAR(255), nullable=False),
        sa.Column("irp_id", sa.Integer, nullable=True),
        sa.Column("created_by_irp_job_irp_id", sa.NVARCHAR(64), nullable=True),
        sa.Column("as_of", DATETIME2, nullable=True),
        # plain VARCHAR — external-status mirror (Article 3 carve-out); inert here.
        sa.Column("status", sa.NVARCHAR(50), nullable=True),  # no edm_id (data-model §5)
        sa.Column("deleted_at", DATETIME2, nullable=True),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.Column("updated_by", sa.Uuid, nullable=True),
        sa.ForeignKeyConstraint(["package_id"], ["package.id"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["app_user.id"]),
    )
    op.create_index("ix_irp_rdm_package_id", "irp_rdm", ["package_id"])

    # ── Seeds ─────────────────────────────────────────────────────────────────
    op.execute(sa.text(
        "INSERT INTO role_kind (code, label, sort_order, is_admin) VALUES "
        "('analyst', 'Analyst', 10, 0), "
        "('admin', 'Administrator', 20, 1)"
    ))
    # submission_status_kind — exactly these three (FR-010 / data-model §1).
    op.execute(sa.text(
        "INSERT INTO submission_status_kind (code, label, sort_order) VALUES "
        "('ACTIVE', 'Active', 10), "
        "('COMPLETED', 'Completed', 20), "
        "('CANCELLED', 'Cancelled', 30)"
    ))
    # treaty_type_kind — six provisional codes (FR-030, pending CIC confirmation).
    op.execute(sa.text(
        "INSERT INTO treaty_type_kind (code, label, sort_order) VALUES "
        "('cat_xol', 'Cat XoL', 10), "
        "('quota_share', 'Quota Share', 20), "
        "('surplus', 'Surplus', 30), "
        "('per_risk_xol', 'Per-Risk XoL', 40), "
        "('aggregate_xol', 'Aggregate XoL', 50), "
        "('stop_loss', 'Stop Loss', 60)"
    ))


def downgrade() -> None:
    # Iteration-1 tables — reverse FK order.
    op.drop_index("ix_irp_rdm_package_id", table_name="irp_rdm")
    op.drop_table("irp_rdm")
    op.drop_index("ix_irp_edm_package_id", table_name="irp_edm")
    op.drop_table("irp_edm")
    op.drop_table("submission_package")
    op.drop_index("ix_submission_status_event_submission_id",
                  table_name="submission_status_event")
    op.drop_table("submission_status_event")
    op.drop_index("ix_submission_crm_id_submission_id",
                  table_name="submission_crm_id")
    op.drop_table("submission_crm_id")
    op.drop_index("ix_submission_inception_date", table_name="submission")
    op.drop_index("ix_submission_treaty_type_code", table_name="submission")
    op.drop_index("ix_submission_cedant_name", table_name="submission")
    op.drop_index("ix_submission_assigned_analyst_id", table_name="submission")
    op.drop_table("submission")
    op.drop_table("package")
    op.drop_table("submission_status_kind")
    op.drop_table("treaty_type_kind")

    # Iteration-0 auth tables.
    op.drop_table("user_role")
    op.drop_table("login_attempt")
    op.drop_table("user_session")
    op.drop_index("ix_app_user_entra_oid", table_name="app_user")
    op.drop_table("app_user")
    op.drop_table("role_kind")
