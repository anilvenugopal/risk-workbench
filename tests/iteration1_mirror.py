"""Single source of the Iteration-1 SQLite mirror used by the unit tier.

The unit tests run against a portable SQLite mirror of the WORKBENCH schema (see
``conftest.iteration1_db``). Keeping the DDL and seeds here — rather than inline
in conftest — lets the SQL Server drift guard (``tests/sqlserver/test_schema_drift.py``)
introspect *exactly* the same mirror it validates against the real migration, so
the two can never silently diverge.

Types are collapsed to TEXT/INTEGER (SQLite affinity is loose and the services
bind ids/dates/timestamps as strings/ISO text) and FKs are omitted (SQLite does
not enforce them by default and the services never rely on that). Only the column
*shape* matters here.
"""

from __future__ import annotations

import sqlite3

ITERATION1_SCHEMA = [
    """CREATE TABLE app_user (
        id TEXT PRIMARY KEY, email TEXT, display_name TEXT
    )""",
    """CREATE TABLE treaty_type_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    """CREATE TABLE submission_status_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    """CREATE TABLE package (
        id TEXT PRIMARY KEY, name TEXT, deleted_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT
    )""",
    """CREATE TABLE submission (
        id TEXT PRIMARY KEY, assigned_analyst_id TEXT, name TEXT,
        cedant_name TEXT, treaty_type_code TEXT, inception_date TEXT,
        treaty_year INTEGER, renews_from_submission_id TEXT, directory_path TEXT,
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
    """CREATE TABLE submission_package (
        submission_id TEXT, package_id TEXT, inserted_at TEXT, inserted_by TEXT,
        PRIMARY KEY (submission_id, package_id)
    )""",
    """CREATE TABLE irp_edm (
        id TEXT PRIMARY KEY, package_id TEXT, name TEXT, deleted_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT
    )""",
    """CREATE TABLE irp_rdm (
        id TEXT PRIMARY KEY, package_id TEXT, name TEXT, deleted_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT
    )""",
]

STATUS_SEED = [("ACTIVE", "Active", 10), ("COMPLETED", "Completed", 20),
               ("CANCELLED", "Cancelled", 30)]
TREATY_SEED = [("cat_xol", "Cat XoL", 10), ("quota_share", "Quota Share", 20),
               ("surplus", "Surplus", 30), ("per_risk_xol", "Per-Risk XoL", 40),
               ("aggregate_xol", "Aggregate XoL", 50), ("stop_loss", "Stop Loss", 60)]

# ── Drift-guard contract (tests/sqlserver/test_schema_drift.py) ──────────────────
# Tables whose mirror must match the real migrated schema column-for-column. A new
# migration column here MUST be added to the mirror above or the guard fails.
EXACT_MATCH_TABLES = (
    "treaty_type_kind", "submission_status_kind", "package", "submission",
    "submission_crm_id", "submission_status_event", "submission_package",
)
# irp_edm/irp_rdm are intentionally trimmed to the structure-only columns the
# package service touches; the real tables carry extra Iteration-2 IRP columns
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
        for ddl in ITERATION1_SCHEMA:
            conn.execute(ddl)
        return {
            table: {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for table in (*EXACT_MATCH_TABLES, *SUBSET_TABLES)
        }
    finally:
        conn.close()
