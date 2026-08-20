"""Single source of the Iteration-1 SQLite mirror used by the unit tier.

The unit tests run against a portable SQLite mirror of the WORKBENCH schema (see
``conftest.iteration1_db``). Keeping the DDL and seeds here — rather than inline
in conftest — lets the SQL Server drift guard (``tests/sqlserver/test_schema_drift.py``)
introspect *exactly* the same mirror it validates against the real migration, so
the two can never silently diverge.

Types are collapsed to TEXT/INTEGER (SQLite affinity is loose and the services
bind ids/dates/timestamps as strings/ISO text). Association-table foreign keys
are retained because detach behavior depends on them. Only the column shape
matters elsewhere.
"""

from __future__ import annotations

import sqlite3

ITERATION1_SCHEMA = [
    """CREATE TABLE app_user (
        id TEXT PRIMARY KEY, email TEXT, display_name TEXT,
        is_active INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE treaty_type_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    """CREATE TABLE submission_status_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    """CREATE TABLE submission (
        id TEXT PRIMARY KEY, assigned_analyst_id TEXT, name TEXT,
        cedant_name TEXT, treaty_type_code TEXT, inception_date TEXT,
        treaty_year INTEGER, links_to_submission_id TEXT, directory_path TEXT,
        status_code TEXT, inserted_at TEXT, updated_at TEXT,
        inserted_by TEXT, updated_by TEXT
    )""",
    """CREATE TABLE submission_crm_id (
        id TEXT PRIMARY KEY, submission_id TEXT, crm_id TEXT,
        inserted_at TEXT, inserted_by TEXT
    )""",
    """CREATE TABLE submission_status_event (
        id TEXT PRIMARY KEY, submission_id TEXT, status_code TEXT, reason TEXT,
        at TEXT, inserted_by TEXT
    )""",
    # irp_edm / irp_rdm carry their full §5 shape from Iteration 1; this iteration
    # EXERCISES the previously-inert columns (§6). The mirror is a subset of the
    # real table (SUBSET_TABLES), so it lists every column a service now touches.
    """CREATE TABLE irp_edm (
        id TEXT PRIMARY KEY, source_file_path TEXT, name TEXT,
        irp_id INTEGER, created_by_irp_job_irp_id TEXT, as_of TEXT,
        server_name TEXT, notes NVARCHAR(250), status TEXT, deleted_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT
    )""",
    """CREATE TABLE irp_rdm (
        id TEXT PRIMARY KEY, source_file_path TEXT, name TEXT,
        irp_id INTEGER, created_by_irp_job_irp_id TEXT, as_of TEXT,
        notes NVARCHAR(250), status TEXT, deleted_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT
    )""",
    """CREATE TABLE submission_edm (
        submission_id TEXT, edm_id TEXT, inserted_at TEXT, inserted_by TEXT,
        PRIMARY KEY (submission_id, edm_id),
        FOREIGN KEY (submission_id) REFERENCES submission(id),
        FOREIGN KEY (edm_id) REFERENCES irp_edm(id),
        FOREIGN KEY (inserted_by) REFERENCES app_user(id)
    )""",
    """CREATE TABLE submission_rdm (
        submission_id TEXT, rdm_id TEXT, inserted_at TEXT, inserted_by TEXT,
        PRIMARY KEY (submission_id, rdm_id),
        FOREIGN KEY (submission_id) REFERENCES submission(id),
        FOREIGN KEY (rdm_id) REFERENCES irp_rdm(id),
        FOREIGN KEY (inserted_by) REFERENCES app_user(id)
    )""",
]

STATUS_SEED = [("ACTIVE", "Active", 10), ("COMPLETED", "Completed", 20),
               ("CANCELLED", "Cancelled", 30)]
TREATY_SEED = [("cat_xol", "Cat XoL", 10), ("quota_share", "Quota Share", 20),
               ("surplus", "Surplus", 30), ("per_risk_xol", "Per-Risk XoL", 40),
               ("aggregate_xol", "Aggregate XoL", 50), ("stop_loss", "Stop Loss", 60)]

# ── Iteration-2 mirror: irp_job / rwb_job families (data-model §1–§5) ──────────
# Column shapes match the migration exactly (types collapsed, FKs omitted). The
# rwb_job UNIQUE(requestor_type, requestor_id, rwb_job_type) IS kept — the queue's
# idempotency backbone is exercised on the unit tier.
ITERATION2_SCHEMA = [
    """CREATE TABLE irp_job_type_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    """CREATE TABLE irp_job_resource_type_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    """CREATE TABLE rwb_job_type_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    """CREATE TABLE rwb_job_requestor_type_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    """CREATE TABLE rwb_job_status_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    """CREATE TABLE irp_analysis_status_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    """CREATE TABLE irp_job (
        id TEXT PRIMARY KEY, requested_from_submission_id TEXT,
        irp_edm_id TEXT, irp_rdm_id TEXT,
        irp_job_type TEXT, irp_id TEXT, status TEXT, correlation_id TEXT,
        last_submission_payload TEXT, last_submission_response TEXT,
        last_completion_result TEXT, submission_attempt_count INTEGER,
        submitted_at TEXT, completed_at TEXT, last_tracked_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT
    )""",
    """CREATE TABLE irp_job_resource (
        id TEXT PRIMARY KEY, irp_job_id TEXT, resource_type TEXT,
        resource_uri TEXT, inserted_at TEXT
    )""",
    """CREATE TABLE rwb_job (
        id TEXT PRIMARY KEY, requestor_type TEXT, requestor_id TEXT,
        rwb_job_type TEXT, status_code TEXT, input_data TEXT, output_data TEXT,
        error_detail TEXT, attempt_count INTEGER, claimed_by TEXT,
        correlation_id TEXT,
        submitted_at TEXT, completed_at TEXT, inserted_at TEXT, updated_at TEXT,
        inserted_by TEXT, updated_by TEXT,
        UNIQUE (requestor_type, requestor_id, rwb_job_type)
    )""",
    """CREATE TABLE rwb_job_heartbeat (
        rwb_job_id TEXT PRIMARY KEY, worker_id TEXT, heartbeat_at TEXT
    )""",
    # irp_analysis (D2) — captured broker analyses for delete-enumeration (§6a).
    # UNIQUE(rdm_id, edm_id, irp_id) is kept — the backfill idempotency backbone is
    # exercised on the unit tier. Iteration 3 (spec 004): settings_metadata /
    # is_group / exposure_resource_id detail columns (data-model §4).
    """CREATE TABLE irp_analysis (
        id TEXT PRIMARY KEY, rdm_id TEXT, edm_id TEXT,
        irp_id TEXT, name TEXT, source_rdm_name TEXT, status_code TEXT,
        created_by_irp_job_irp_id TEXT,
        settings_metadata TEXT, is_group INTEGER, exposure_resource_id TEXT,
        deleted_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT,
        UNIQUE (rdm_id, irp_id)
    )""",
]

# ── Iteration-3 mirror: irp_portfolio / irp_treaty (spec 004, data-model §2/§3) ──
# Thin identity/lineage records + a JSON snapshot column each (R2). The
# UNIQUE(edm_id, irp_id) keys ARE kept — the idempotent-upsert backbone is
# exercised on the unit tier.
ITERATION3_SCHEMA = [
    """CREATE TABLE irp_portfolio (
        id TEXT PRIMARY KEY, edm_id TEXT, name TEXT, irp_id TEXT,
        exposure_detail TEXT, as_of TEXT, deleted_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT,
        UNIQUE (edm_id, irp_id)
    )""",
    """CREATE TABLE irp_treaty (
        id TEXT PRIMARY KEY, edm_id TEXT, name TEXT, irp_id TEXT,
        attributes TEXT, as_of TEXT, deleted_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT,
        UNIQUE (edm_id, irp_id)
    )""",
]

IRP_JOB_TYPE_SEED = [("import_edm", "Import EDM", 10), ("import_rdm", "Import RDM", 20),
                     ("geohaz", "Geohazard", 40),
                     ("analysis", "Analysis", 50), ("grouping", "Grouping", 60),
                     ("export", "Export", 70)]
IRP_JOB_RESOURCE_TYPE_SEED = [("portfolio", "Portfolio", 10)]
RWB_JOB_TYPE_SEED = [("upload_edm", "Upload EDM", 10), ("upload_rdm", "Upload RDM", 20),
                     ("backfill_rdm_analyses", "Backfill RDM Analyses", 25),  # D2
                     ("backfill_edm_detail", "Backfill EDM Detail", 27),  # spec 004
                     ("retrieve_analysis_results", "Retrieve Analysis Results", 30),
                     ("download_export_file", "Download Export File", 40),
                     ("push_results_to_loss_repo", "Push Results to Loss Repo", 50),
                     ("notify_analyst", "Notify Analyst", 60)]
RWB_JOB_REQUESTOR_TYPE_SEED = [("irp_job", "IRP Job", 10),
                               ("analyst_request", "Analyst Request", 20),
                               ("rwb_job", "RWB Job", 30)]
RWB_JOB_STATUS_SEED = [("pending", "Pending", 10), ("running", "Running", 20),
                       ("succeeded", "Succeeded", 30), ("failed", "Failed", 40)]
IRP_ANALYSIS_STATUS_SEED = [("pending", "Pending", 10), ("running", "Running", 20),
                            ("ready", "Ready", 30), ("error", "Error", 40)]

# ── Drift-guard contract (tests/sqlserver/test_schema_drift.py) ──────────────────
# Tables whose mirror must match the real migrated schema column-for-column. A new
# migration column here MUST be added to the mirror above or the guard fails.
EXACT_MATCH_TABLES = (
    "treaty_type_kind", "submission_status_kind", "submission",
    "submission_crm_id", "submission_status_event", "submission_edm",
    "submission_rdm",
    # Iteration 2 — irp_job / rwb_job families (full mirrors, exact match).
    "irp_job_type_kind", "irp_job_resource_type_kind", "rwb_job_type_kind",
    "rwb_job_requestor_type_kind", "rwb_job_status_kind", "irp_analysis_status_kind",
    "irp_job", "irp_job_resource", "rwb_job", "rwb_job_heartbeat", "irp_analysis",
    # Iteration 3 — EDM detail entities (spec 004; full mirrors, exact match).
    "irp_portfolio", "irp_treaty",
)
# irp_edm/irp_rdm are intentionally trimmed to the structure-only columns the
# unit services touch; the real tables carry extra Iteration-2 IRP columns
# (source_file_path, irp_id, as_of, status, server_name, ...). For these the
# invariant is mirror ⊆ real: every mirrored column must exist, extras are fine.
SUBSET_TABLES = ("irp_edm", "irp_rdm")

# app_user is deliberately NOT guarded: its mirror is a 3-column stub for the FK
# target, while the real auth table has many more columns (Iteration 0).


def mirror_columns() -> dict[str, set[str]]:
    """Return ``{table: {column, ...}}`` for the mirror, read straight from a
    scratch in-memory SQLite built from ITERATION1_SCHEMA — SQLite itself is the
    parser, so this never drifts from the DDL the unit tier actually runs."""
    conn = sqlite3.connect(":memory:")
    try:
        for ddl in (*ITERATION1_SCHEMA, *ITERATION2_SCHEMA, *ITERATION3_SCHEMA):
            conn.execute(ddl)
        return {
            table: {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for table in (*EXACT_MATCH_TABLES, *SUBSET_TABLES)
        }
    finally:
        conn.close()
