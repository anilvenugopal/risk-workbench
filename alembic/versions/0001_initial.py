"""Initial schema — WORKBENCH database.

Revision ID: 0001
Revises: (none)
Create Date: 2026-07-01

Dev strategy: drop-create-seed. This is the ONLY revision until production
cutover. Run `make db-rebuild` to drop and recreate from scratch rather than
accumulating incremental migrations.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.mssql import DATETIME2

from alembic import op

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
        sa.Column("links_to_submission_id", sa.Uuid, nullable=True),  # self-ref
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
        sa.ForeignKeyConstraint(["links_to_submission_id"], ["submission.id"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["app_user.id"]),
        # A submission cannot link to itself (FR-007 / R9).
        sa.CheckConstraint(
            "links_to_submission_id IS NULL OR links_to_submission_id <> id",
            name="ck_submission_no_self_link",
        ),
        # No UNIQUE(name) (FR-003); no customer_id/scope column (Article 6).
    )
    op.create_index("ix_submission_assigned_analyst_id", "submission",
                    ["assigned_analyst_id"])
    op.create_index("ix_submission_cedant_name", "submission", ["cedant_name"])
    op.create_index("ix_submission_treaty_type_code", "submission",
                    ["treaty_type_code"])
    # The master list's own index: keyed in its ORDER BY (inception_date DESC,
    # name) and covering every submission column the list SELECTs, so a page is
    # read from here and stops at PAGE_SIZE + 1 rows instead of sorting the table.
    # The DESC matters — an ascending index cannot be scanned backwards to satisfy
    # a mixed "inception_date DESC, name ASC". The clustered PK puts `id` in the
    # index without including it.
    op.create_index(
        "ix_submission_list_order", "submission",
        [sa.text("inception_date DESC"), "name"],
        mssql_include=["cedant_name", "treaty_type_code", "treaty_year",
                       "status_code", "assigned_analyst_id", "updated_at"],
    )

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
    live_irp_edm = sa.text("irp_id IS NOT NULL AND deleted_at IS NULL")
    op.create_index(
        "uq_irp_edm_live_irp_id",
        "irp_edm",
        ["irp_id"],
        unique=True,
        mssql_where=live_irp_edm,
        sqlite_where=live_irp_edm,
    )

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

    # ══════════════════════════════════════════════════════════════════════════
    #  Iteration 2 — irp_job / rwb_job families (data-model §1–§5, §8, §13)
    #  Created after the Iteration-1 tables, in FK order: kinds → irp_job →
    #  irp_job_resource → rwb_job → rwb_job_heartbeat. No ALTER on irp_edm/irp_rdm
    #  (their full shape already exists above — §6).
    # ══════════════════════════════════════════════════════════════════════════

    # ── kind tables (Article 3) ─────────────────────────────────────────────────
    for kind in (
        "irp_job_type_kind",
        "irp_job_resource_type_kind",
        "rwb_job_type_kind",
        "rwb_job_requestor_type_kind",
        "rwb_job_status_kind",
        "irp_analysis_status_kind",  # captured-analysis lifecycle (D2, data-model §6)
    ):
        op.create_table(
            kind,
            sa.Column("code", sa.NVARCHAR(50), primary_key=True),
            sa.Column("label", sa.NVARCHAR(255), nullable=False),
            sa.Column("sort_order", sa.Integer, nullable=False),
            sa.Column("inserted_at", DATETIME2, nullable=False,
                      server_default=sa.text("GETUTCDATE()")),
        )

    # ── irp_job (one tracked IRP async op; grain = package; data-model §2) ───────
    op.create_table(
        "irp_job",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("package_id", sa.Uuid, nullable=True),
        sa.Column("irp_edm_id", sa.Uuid, nullable=True),
        sa.Column("irp_portfolio_id", sa.Uuid, nullable=True),
        sa.Column("irp_rdm_id", sa.Uuid, nullable=True),
        sa.Column("irp_job_type", sa.NVARCHAR(50), nullable=False),
        sa.Column("irp_id", sa.NVARCHAR(64), nullable=True),  # IRP int id as string
        # plain VARCHAR — external-status mirror (Article 3 carve-out).
        sa.Column("status", sa.NVARCHAR(50), nullable=False,
                  server_default=sa.text("'UNSUBMITTED'")),
        # Operational log-trace id inherited from the rwb_job whose worker
        # submitted this op (issue #28). Provenance only — never a predicate.
        sa.Column("correlation_id", sa.NVARCHAR(64), nullable=True),
        sa.Column("request_params", sa.NVARCHAR(None), nullable=True),
        sa.Column("last_submission_payload", sa.NVARCHAR(None), nullable=True),
        sa.Column("last_submission_response", sa.NVARCHAR(None), nullable=True),
        sa.Column("last_completion_result", sa.NVARCHAR(None), nullable=True),
        sa.Column("submission_attempt_count", sa.Integer, nullable=False,
                  server_default="0"),
        sa.Column("submitted_at", DATETIME2, nullable=True),
        sa.Column("completed_at", DATETIME2, nullable=True),
        sa.Column("last_tracked_at", DATETIME2, nullable=True),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.Column("updated_by", sa.Uuid, nullable=True),
        sa.ForeignKeyConstraint(["package_id"], ["package.id"]),
        sa.ForeignKeyConstraint(["irp_edm_id"], ["irp_edm.id"]),
        sa.ForeignKeyConstraint(["irp_rdm_id"], ["irp_rdm.id"]),
        sa.ForeignKeyConstraint(["irp_job_type"], ["irp_job_type_kind.code"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["app_user.id"]),
        # No scope/customer column (Article 6).
    )
    op.create_index("ix_irp_job_type_status", "irp_job", ["irp_job_type", "status"])
    op.create_index("ix_irp_job_status", "irp_job", ["status"])
    op.create_index("ix_irp_job_package_id", "irp_job", ["package_id"])
    op.create_index("ix_irp_job_irp_portfolio_id", "irp_job", ["irp_portfolio_id"])

    # ── irp_job_resource (typed submit payload — the resource URI; §3) ──────────
    op.create_table(
        "irp_job_resource",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("irp_job_id", sa.Uuid, nullable=False),
        sa.Column("resource_type", sa.NVARCHAR(50), nullable=False),
        sa.Column("resource_uri", sa.NVARCHAR(1024), nullable=False),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.ForeignKeyConstraint(["irp_job_id"], ["irp_job.id"]),
        sa.ForeignKeyConstraint(["resource_type"],
                                ["irp_job_resource_type_kind.code"]),
    )
    op.create_index("ix_irp_job_resource_irp_job_id", "irp_job_resource",
                    ["irp_job_id"])

    # ── rwb_job (the SQL-backed work queue — Article 10; §4) ─────────────────────
    op.create_table(
        "rwb_job",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("requestor_type", sa.NVARCHAR(50), nullable=False),
        # requestor_id has NO DB FK — its target varies by requestor_type
        # (package / irp_job / rwb_job), data-model §4.
        sa.Column("requestor_id", sa.Uuid, nullable=False),
        sa.Column("rwb_job_type", sa.NVARCHAR(50), nullable=False),
        sa.Column("status_code", sa.NVARCHAR(50), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column("input_data", sa.NVARCHAR(None), nullable=True),
        sa.Column("output_data", sa.NVARCHAR(None), nullable=True),
        sa.Column("error_detail", sa.NVARCHAR(None), nullable=True),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("claimed_by", sa.NVARCHAR(128), nullable=True),
        # Operational log-trace id: the HTTP request (or chain) that caused this
        # job (issue #28). Provenance only — never a predicate.
        sa.Column("correlation_id", sa.NVARCHAR(64), nullable=True),
        sa.Column("submitted_at", DATETIME2, nullable=True),
        sa.Column("completed_at", DATETIME2, nullable=True),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.Column("updated_by", sa.Uuid, nullable=True),
        sa.ForeignKeyConstraint(["requestor_type"],
                                ["rwb_job_requestor_type_kind.code"]),
        sa.ForeignKeyConstraint(["rwb_job_type"], ["rwb_job_type_kind.code"]),
        sa.ForeignKeyConstraint(["status_code"], ["rwb_job_status_kind.code"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["app_user.id"]),
        # The A21 idempotency backbone — every chained enqueue is idempotent on it.
        sa.UniqueConstraint("requestor_type", "requestor_id", "rwb_job_type",
                            name="uq_rwb_job_requestor_type"),
    )
    op.create_index("ix_rwb_job_status_code", "rwb_job", ["status_code"])
    op.create_index("ix_rwb_job_requestor", "rwb_job",
                    ["requestor_type", "requestor_id"])

    # ── rwb_job_heartbeat (per-job liveness — one row per job; §5) ───────────────
    op.create_table(
        "rwb_job_heartbeat",
        sa.Column("rwb_job_id", sa.Uuid, primary_key=True),  # UNIQUE — one per job
        sa.Column("worker_id", sa.NVARCHAR(128), nullable=False),
        sa.Column("heartbeat_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.ForeignKeyConstraint(["rwb_job_id"], ["rwb_job.id"]),
    )

    # ── irp_analysis (captured broker analyses; §6a + spec-004 detail cols) ──────
    # Iteration 2 (D2): a minimal subset of DATA_MODEL §6, populated by the
    # backfill_rdm_analyses worker on import_rdm FINISHED so package delete can
    # enumerate the exact Moody's analyses an RDM produced (per RDM×EDM pair).
    # Iteration 3 (spec 004, data-model §4): settings_metadata (JSON snapshot, R2),
    # is_group (FR-035), exposure_resource_id (the RM portfolio pointer, R9 —
    # resolved to irp_portfolio at READ time, never a stored FK). group_parent_id
    # stays DEFERRED — RM does not expose group membership, nothing populates it.
    op.create_table(
        "irp_analysis",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("rdm_id", sa.Uuid, nullable=False),
        sa.Column("edm_id", sa.Uuid, nullable=True),
        sa.Column("package_id", sa.Uuid, nullable=True),
        sa.Column("irp_id", sa.NVARCHAR(64), nullable=False),  # Moody's analysisId
        sa.Column("name", sa.NVARCHAR(256), nullable=True),
        sa.Column("source_rdm_name", sa.NVARCHAR(256), nullable=False),
        # plain VARCHAR FK → irp_analysis_status_kind (written 'ready' on capture).
        sa.Column("status_code", sa.NVARCHAR(50), nullable=False),
        sa.Column("created_by_irp_job_irp_id", sa.NVARCHAR(64), nullable=True),
        # JSON snapshot of the analysis settings (R2); null ⇒ graceful blank.
        sa.Column("settings_metadata", sa.NVARCHAR(None), nullable=True),
        sa.Column("is_group", sa.Boolean, nullable=False, server_default="0"),
        # RM exposureResourceId as string — set ONLY when exposureResourceType ==
        # 'PORTFOLIO' (R9/FR-036); no index — the resolve join keys on edm_id.
        sa.Column("exposure_resource_id", sa.NVARCHAR(64), nullable=True),
        # Soft-delete: stamped by delete_analysis AND by the backfill's stale-row
        # prune (RM search no longer returns the analysis).
        sa.Column("deleted_at", DATETIME2, nullable=True),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.Column("updated_by", sa.Uuid, nullable=True),
        sa.ForeignKeyConstraint(["rdm_id"], ["irp_rdm.id"]),
        sa.ForeignKeyConstraint(["edm_id"], ["irp_edm.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["package.id"]),
        sa.ForeignKeyConstraint(["status_code"], ["irp_analysis_status_kind.code"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["app_user.id"]),
        # Idempotent backfill backbone — a duplicate search never double-inserts.
        sa.UniqueConstraint("rdm_id", "edm_id", "irp_id",
                            name="uq_irp_analysis_pair"),
        # No scope/customer column (Article 6).
    )
    op.create_index("ix_irp_analysis_rdm_edm", "irp_analysis", ["rdm_id", "edm_id"])
    op.create_index("ix_irp_analysis_package_id", "irp_analysis", ["package_id"])

    # ══════════════════════════════════════════════════════════════════════════
    #  Iteration 3 — EDM detail entities (spec 004, data-model §2/§3)
    #  irp_portfolio / irp_treaty: thin §5 identity/lineage records + a JSON
    #  snapshot cache column each (R2 — nullable; null ⇒ graceful empty state).
    #  Backfilled by the backfill_edm_detail worker; UNIQUE(edm_id, irp_id) is the
    #  idempotent-upsert backbone (service falls back to (edm_id, name) matching).
    #  No status column (Article 4), no scope column (Article 6).
    # ══════════════════════════════════════════════════════════════════════════

    # ── irp_portfolio (a portfolio within an EDM — the detail page's primary unit) ─
    op.create_table(
        "irp_portfolio",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("edm_id", sa.Uuid, nullable=False),
        sa.Column("name", sa.NVARCHAR(256), nullable=False),
        sa.Column("irp_id", sa.NVARCHAR(64), nullable=True),  # RM portfolioId as string
        # JSON snapshot — per-portfolio exposure figures, stored verbatim (R2).
        sa.Column("exposure_detail", sa.NVARCHAR(None), nullable=True),
        sa.Column("as_of", DATETIME2, nullable=True),  # trust signal (FR-052)
        # Soft-delete: the backfill's stale-row prune (RM no longer returns it).
        sa.Column("deleted_at", DATETIME2, nullable=True),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.Column("updated_by", sa.Uuid, nullable=True),
        sa.ForeignKeyConstraint(["edm_id"], ["irp_edm.id"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["app_user.id"]),
        sa.UniqueConstraint("edm_id", "irp_id", name="uq_irp_portfolio_edm_irp"),
        # No scope/customer column (Article 6).
    )
    op.create_index("ix_irp_portfolio_edm_id", "irp_portfolio", ["edm_id"])
    op.create_foreign_key(
        "fk_irp_job_irp_portfolio_id",
        "irp_job",
        "irp_portfolio",
        ["irp_portfolio_id"],
        ["id"],
    )

    # ── irp_treaty (reinsurance coded on an EDM — read/cache record) ─────────────
    op.create_table(
        "irp_treaty",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("edm_id", sa.Uuid, nullable=False),
        sa.Column("name", sa.NVARCHAR(256), nullable=False),
        sa.Column("irp_id", sa.NVARCHAR(64), nullable=True),  # RM treatyId as string
        # JSON snapshot — the full attribute set for the treaty view + .xlsx export.
        sa.Column("attributes", sa.NVARCHAR(None), nullable=True),
        sa.Column("as_of", DATETIME2, nullable=True),  # trust signal (FR-052)
        # Soft-delete: the backfill's stale-row prune (RM no longer returns it).
        sa.Column("deleted_at", DATETIME2, nullable=True),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.Column("updated_by", sa.Uuid, nullable=True),
        sa.ForeignKeyConstraint(["edm_id"], ["irp_edm.id"]),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["app_user.id"]),
        sa.UniqueConstraint("edm_id", "irp_id", name="uq_irp_treaty_edm_irp"),
        # No scope/customer column (Article 6).
    )
    op.create_index("ix_irp_treaty_edm_id", "irp_treaty", ["edm_id"])

    # ── Iteration-2 kind seeds (inline; data-model §13) ─────────────────────────
    # irp_job_type_kind — NOTE: there is NO delete_rdm type (RDM delete is
    # synchronous and creates no irp_job — A21 / research R6).
    op.execute(sa.text(
        "INSERT INTO irp_job_type_kind (code, label, sort_order) VALUES "
        "('import_edm', 'Import EDM', 10), "
        "('import_rdm', 'Import RDM', 20), "
        "('delete_edm', 'Delete EDM', 30), "
        "('geohaz', 'Geohazard', 40), "
        "('analysis', 'Analysis', 50), "
        "('grouping', 'Grouping', 60), "
        "('export', 'Export', 70)"
    ))
    op.execute(sa.text(
        "INSERT INTO irp_job_resource_type_kind (code, label, sort_order) VALUES "
        "('portfolio', 'Portfolio', 10)"
    ))
    op.execute(sa.text(
        "INSERT INTO rwb_job_type_kind (code, label, sort_order) VALUES "
        "('upload_edm', 'Upload EDM', 10), "
        "('upload_rdm', 'Upload RDM', 20), "
        "('backfill_rdm_analyses', 'Backfill RDM Analyses', 25), "
        "('backfill_edm_detail', 'Backfill EDM Detail', 27), "
        "('run_geohaz', 'Run GeoHaz', 28), "
        "('retrieve_analysis_results', 'Retrieve Analysis Results', 30), "
        "('download_export_file', 'Download Export File', 40), "
        "('push_results_to_loss_repo', 'Push Results to Loss Repo', 50), "
        "('notify_analyst', 'Notify Analyst', 60), "
        "('delete_rdm', 'Delete RDM', 70), "
        "('delete_edm', 'Delete EDM', 80)"
    ))
    op.execute(sa.text(
        "INSERT INTO rwb_job_requestor_type_kind (code, label, sort_order) VALUES "
        "('irp_job', 'IRP Job', 10), "
        "('analyst_request', 'Analyst Request', 20), "
        "('rwb_job', 'RWB Job', 30)"
    ))
    op.execute(sa.text(
        "INSERT INTO rwb_job_status_kind (code, label, sort_order) VALUES "
        "('pending', 'Pending', 10), "
        "('running', 'Running', 20), "
        "('succeeded', 'Succeeded', 30), "
        "('failed', 'Failed', 40)"
    ))
    # irp_analysis_status_kind — captured-analysis lifecycle (D2, data-model §6).
    op.execute(sa.text(
        "INSERT INTO irp_analysis_status_kind (code, label, sort_order) VALUES "
        "('pending', 'Pending', 10), "
        "('running', 'Running', 20), "
        "('ready', 'Ready', 30), "
        "('error', 'Error', 40)"
    ))

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
    # Iteration-3 tables — reverse FK order (irp_treaty → irp_portfolio), ahead of
    # the Iteration-2 drops. The irp_analysis detail columns are inherent to its
    # create (no separate drop).
    op.drop_index("ix_irp_treaty_edm_id", table_name="irp_treaty")
    op.drop_table("irp_treaty")
    op.drop_constraint("fk_irp_job_irp_portfolio_id", "irp_job", type_="foreignkey")
    op.drop_index("ix_irp_portfolio_edm_id", table_name="irp_portfolio")
    op.drop_table("irp_portfolio")

    # Iteration-2 tables — reverse FK order (irp_analysis → heartbeat → rwb_job →
    # irp_job_resource → irp_job → the six kind tables), ahead of Iteration-1.
    op.drop_index("ix_irp_analysis_package_id", table_name="irp_analysis")
    op.drop_index("ix_irp_analysis_rdm_edm", table_name="irp_analysis")
    op.drop_table("irp_analysis")
    op.drop_table("rwb_job_heartbeat")
    op.drop_index("ix_rwb_job_requestor", table_name="rwb_job")
    op.drop_index("ix_rwb_job_status_code", table_name="rwb_job")
    op.drop_table("rwb_job")
    op.drop_index("ix_irp_job_resource_irp_job_id", table_name="irp_job_resource")
    op.drop_table("irp_job_resource")
    op.drop_index("ix_irp_job_irp_portfolio_id", table_name="irp_job")
    op.drop_index("ix_irp_job_package_id", table_name="irp_job")
    op.drop_index("ix_irp_job_status", table_name="irp_job")
    op.drop_index("ix_irp_job_type_status", table_name="irp_job")
    op.drop_table("irp_job")
    for kind in (
        "irp_analysis_status_kind",
        "rwb_job_status_kind",
        "rwb_job_requestor_type_kind",
        "rwb_job_type_kind",
        "irp_job_resource_type_kind",
        "irp_job_type_kind",
    ):
        op.drop_table(kind)

    # Iteration-1 tables — reverse FK order.
    op.drop_index("ix_irp_rdm_package_id", table_name="irp_rdm")
    op.drop_table("irp_rdm")
    op.drop_index("uq_irp_edm_live_irp_id", table_name="irp_edm")
    op.drop_index("ix_irp_edm_package_id", table_name="irp_edm")
    op.drop_table("irp_edm")
    op.drop_table("submission_package")
    op.drop_index("ix_submission_status_event_submission_id",
                  table_name="submission_status_event")
    op.drop_table("submission_status_event")
    op.drop_index("ix_submission_crm_id_submission_id",
                  table_name="submission_crm_id")
    op.drop_table("submission_crm_id")
    op.drop_index("ix_submission_list_order", table_name="submission")
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
