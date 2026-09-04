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
    """CREATE TABLE rwb_job_link_type_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    """CREATE TABLE rwb_job_context_type_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    """CREATE TABLE rwb_job_status_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    """CREATE TABLE irp_analysis_status_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
    )""",
    # irp_portfolio_id / irp_analysis_id / request_params (spec 010, data-model §2).
    """CREATE TABLE irp_job (
        id TEXT PRIMARY KEY, requested_from_submission_id TEXT,
        irp_edm_id TEXT, irp_rdm_id TEXT, irp_portfolio_id TEXT, irp_analysis_id TEXT,
        irp_job_type TEXT, irp_id TEXT, status TEXT, correlation_id TEXT,
        request_params TEXT, completion_summary TEXT,
        last_submission_payload TEXT, last_submission_response TEXT,
        last_completion_result TEXT,
        submission_attempt_count INTEGER,
        submitted_at TEXT, completed_at TEXT, last_tracked_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT
    )""",
    """CREATE TABLE irp_job_resource (
        id TEXT PRIMARY KEY, irp_job_id TEXT, resource_type TEXT,
        resource_uri TEXT, inserted_at TEXT
    )""",
    """CREATE TABLE rwb_job (
        id TEXT PRIMARY KEY, requestor_type TEXT, requestor_id TEXT,
        link_type TEXT NOT NULL, link_id TEXT,
        context_type TEXT, context_id TEXT,
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
    # exercised on the unit tier (both rdm_id/irp_id are NULL for own-executed
    # rows, which SQLite — like SQL Server's filtered index — never treats as
    # colliding). Iteration 3 (spec 004): settings_metadata / is_group /
    # exposure_resource_id detail columns (data-model §4). Spec 010: rdm_id/irp_id/
    # source_rdm_name are no longer required (own-executed rows have none);
    # full_name/irp_portfolio_id/analysis_template_id/execution_id/
    # execution_item_no/failure_reason are new (data-model §1). Spec 011:
    # loss_results (the retrieval extract) and submitted_settings (the approved
    # plan item snapshot, T-09).
    """CREATE TABLE irp_analysis (
        id TEXT PRIMARY KEY, rdm_id TEXT, edm_id TEXT,
        irp_id TEXT, irp_app_analysis_id TEXT, name TEXT, full_name TEXT,
        source_rdm_name TEXT,
        status_code TEXT, created_by_irp_job_irp_id TEXT,
        settings_metadata TEXT, is_group INTEGER, exposure_resource_id TEXT,
        irp_portfolio_id TEXT, analysis_template_id TEXT, execution_id TEXT,
        execution_item_no INTEGER, failure_reason TEXT,
        loss_results TEXT, submitted_settings TEXT, deleted_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT,
        UNIQUE (rdm_id, irp_id)
    )""",
    # The worker's resume key (spec 010) — _submit_one reads it as a scalar
    # subquery, which raises on a duplicate. Filtered: all three columns are
    # NULL for broker rows.
    """CREATE UNIQUE INDEX uq_irp_analysis_execution_item
        ON irp_analysis (execution_id, irp_portfolio_id, execution_item_no)
        WHERE execution_id IS NOT NULL""",
]

# ── Iteration-3 mirror: irp_portfolio / irp_treaty (spec 004, data-model §2/§3) ──
# Thin identity/lineage records + a JSON snapshot column each (R2). The
# UNIQUE(edm_id, irp_id) keys ARE kept — the idempotent-upsert backbone is
# exercised on the unit tier. Iteration 4 (spec 005): breakout_dimension_kind,
# the three lineage columns, and the filtered unique idempotency index (SQLite
# partial indexes are supported since 3.8 — the same WHERE the migration emits).
ITERATION3_SCHEMA = [
    """CREATE TABLE breakout_dimension_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER
    )""",
    """CREATE TABLE irp_portfolio (
        id TEXT PRIMARY KEY, edm_id TEXT, name TEXT, irp_id TEXT,
        exposure_detail TEXT, as_of TEXT,
        source_portfolio_id TEXT, breakout_dimension_code TEXT,
        breakout_value TEXT, breakout_group_id TEXT,
        deleted_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT,
        UNIQUE (edm_id, irp_id)
    )""",
    """CREATE UNIQUE INDEX uq_irp_portfolio_breakout
        ON irp_portfolio (source_portfolio_id, breakout_dimension_code,
                          breakout_value)
        WHERE source_portfolio_id IS NOT NULL AND deleted_at IS NULL""",
    # spec 005 follow-on (T-12): one row per custom group; the row's UUID is
    # the group job's rwb_job.requestor_id. UNIQUE(source, group_key) IS kept —
    # re-confirming the same member set reuses the row, which dedups the job
    # through rwb_job's uniqueness key.
    """CREATE TABLE breakout_group (
        id TEXT PRIMARY KEY, source_portfolio_id TEXT, group_key TEXT,
        label TEXT, filters TEXT, name TEXT, number TEXT, cart_id TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT,
        UNIQUE (source_portfolio_id, group_key)
    )""",
    """CREATE TABLE irp_treaty (
        id TEXT PRIMARY KEY, edm_id TEXT, name TEXT, irp_id TEXT,
        attributes TEXT, as_of TEXT, deleted_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT,
        UNIQUE (edm_id, irp_id)
    )""",
]

ITERATION4_SCHEMA = [
    """CREATE TABLE irp_model_profile (
        id TEXT PRIMARY KEY, irp_id INTEGER, name TEXT,
        is_accumulation INTEGER NOT NULL DEFAULT 0,
        software_version_code TEXT, peril_code TEXT, model_region_code TEXT,
        peril TEXT, region TEXT, analysis_type TEXT,
        inserted_at TEXT, updated_at TEXT,
        UNIQUE (irp_id)
    )""",
    """CREATE TABLE irp_output_profile (
        id TEXT PRIMARY KEY, irp_id INTEGER, name TEXT,
        rms_default INTEGER NOT NULL DEFAULT 0,
        inserted_at TEXT, updated_at TEXT,
        UNIQUE (irp_id)
    )""",
    """CREATE TABLE irp_event_rate_scheme (
        id TEXT PRIMARY KEY, irp_id INTEGER, name TEXT, peril_code TEXT,
        model_region_code TEXT, model_version_code TEXT,
        is_hd INTEGER NOT NULL DEFAULT 0,
        workbench_is_active INTEGER NOT NULL DEFAULT 1,
        inserted_at TEXT, updated_at TEXT,
        UNIQUE (irp_id)
    )""",
    """CREATE TABLE irp_currency (
        id TEXT PRIMARY KEY, code TEXT, name TEXT, country_name TEXT, symbol TEXT,
        inserted_at TEXT, updated_at TEXT,
        UNIQUE (code)
    )""",
    """CREATE TABLE irp_currency_scheme (
        id TEXT PRIMARY KEY, irp_id INTEGER, name TEXT, code TEXT,
        anchor_currency_code TEXT, update_interval_days INTEGER,
        inserted_at TEXT, updated_at TEXT,
        UNIQUE (irp_id)
    )""",
    # No irp_id/unique key — raw snapshot, delete-all + insert per sync (R13).
    """CREATE TABLE irp_currency_scheme_vintage (
        id TEXT PRIMARY KEY, vintage TEXT, currency_scheme_code TEXT,
        effective_date TEXT,
        inserted_at TEXT, updated_at TEXT
    )""",
    """CREATE TABLE analysis_template (
        id TEXT PRIMARY KEY, name TEXT, analysis_profile_name TEXT,
        output_profile_name TEXT, event_rate_scheme_name TEXT,
        min_loss_threshold NUMERIC NOT NULL DEFAULT 1.00,
        num_max_loss_event INTEGER NOT NULL DEFAULT 1,
        franchise_deductible INTEGER NOT NULL DEFAULT 0,
        treat_construction_occupancy_as_unknown INTEGER NOT NULL DEFAULT 1,
        deleted_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT
    )""",
    """CREATE UNIQUE INDEX uq_analysis_template_live_name
        ON analysis_template (name) WHERE deleted_at IS NULL""",
    """CREATE TABLE analysis_template_tag (
        template_id TEXT, tag_name TEXT, inserted_at TEXT, inserted_by TEXT,
        PRIMARY KEY (template_id, tag_name)
    )""",
    """CREATE TABLE template_suite (
        id TEXT PRIMARY KEY, name TEXT, deleted_at TEXT,
        inserted_at TEXT, updated_at TEXT, inserted_by TEXT, updated_by TEXT
    )""",
    """CREATE UNIQUE INDEX uq_template_suite_live_name
        ON template_suite (name) WHERE deleted_at IS NULL""",
    """CREATE TABLE template_suite_item (
        id TEXT PRIMARY KEY, suite_id TEXT, template_id TEXT,
        inserted_at TEXT, inserted_by TEXT,
        UNIQUE (suite_id, template_id)
    )""",
    # Iteration 8 (spec 011 T-06): the perspectives the retrieval worker
    # requests and every perspective toggle offers.
    """CREATE TABLE analysis_perspective_kind (
        code TEXT PRIMARY KEY, label TEXT, sort_order INTEGER, inserted_at TEXT
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
                     ("run_geohaz", "Run GeoHaz", 28),
                     ("execute_analysis_batch", "Execute Analysis Batch", 29),  # spec 010
                     ("retrieve_analysis_results", "Retrieve Analysis Results", 30),
                     ("finalize_analysis", "Finalize Analysis", 31),  # spec 010
                     ("download_export_file", "Download Export File", 40),
                     ("push_results_to_loss_repo", "Push Results to Loss Repo", 50),
                     ("notify_analyst", "Notify Analyst", 60),
                     ("run_breakout_lob", "Portfolio breakout by line of business", 90),  # spec 005
                     ("run_breakout_state", "Portfolio breakout by geography (state)", 100),
                     ("run_breakout_country", "Portfolio breakout by country", 105),
                     ("run_breakout_peril", "Portfolio breakout by peril", 107),
                     ("run_breakout_custom", "Portfolio breakout by custom group", 110),  # T-12
                     ("sync_irp_metadata", "Sync IRP metadata", 120),  # spec 009
                     ("dummy_wait", "Dummy: wait (dev/test only)", 900),
                     ("dummy_fail", "Dummy: fail (dev/test only)", 910)]
RWB_JOB_REQUESTOR_TYPE_SEED = [("irp_job", "IRP Job", 10),
                               ("analyst_request", "Analyst Request", 20),
                               ("rwb_job", "RWB Job", 30),
                               ("breakout_group", "Breakout Group", 40),  # T-13
                               ("irp_analysis", "IRP Analysis", 50)]  # spec 011 T-01
RWB_JOB_LINK_TYPE_SEED = [("edm", "EDM", 10), ("rdm", "RDM", 20),
                          ("not_applicable", "Not applicable", 900)]  # CR-04c
RWB_JOB_CONTEXT_TYPE_SEED = [("edm", "EDM", 10), ("rdm", "RDM", 20),
                             ("irp_analysis", "IRP Analysis", 30),
                             ("portfolio", "Portfolio", 40),
                             ("breakout_group", "Breakout Group", 50),
                             ("execution", "Execution", 60)]  # CR-04c
RWB_JOB_STATUS_SEED = [("pending", "Pending", 10), ("running", "Running", 20),
                       ("succeeded", "Succeeded", 30), ("failed", "Failed", 40),
                       ("cancelled", "Cancelled", 50)]
IRP_ANALYSIS_STATUS_SEED = [("pending", "Pending", 10), ("ready", "Ready", 30),
                            ("error", "Error", 40)]
BREAKOUT_DIMENSION_SEED = [("lob", "Line of business", 10),  # spec 005 data-model §2
                           ("state", "Geography - State", 20),
                           ("country", "Geography - Country", 25),
                           ("peril", "Peril", 30),
                           ("custom", "Custom group", 40)]  # lineage code (T-12)
# sort_order is dropdown order; the screen-wide default is
# analysis_service.DEFAULT_PERSPECTIVE (RL).
ANALYSIS_PERSPECTIVE_SEED = [("GR", "Gross", 10),
                             ("RL", "Pre-Cat Net", 20),
                             ("WX", "Working Excess", 30),
                             ("QS", "Quota Share", 40),
                             ("GU", "Ground Up", 50)]

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
    "rwb_job_link_type_kind", "rwb_job_context_type_kind",
    "irp_job", "irp_job_resource", "rwb_job", "rwb_job_heartbeat", "irp_analysis",
    # Iteration 3 — EDM detail entities (spec 004; full mirrors, exact match).
    "irp_portfolio", "irp_treaty",
    # Iteration 4 — breakout dimension kind table (spec 005) + the custom
    # group entity (follow-on T-12).
    "breakout_dimension_kind", "breakout_group",
    # Iteration 4 — IRP reference data and the template/suite entities (spec 009).
    "irp_model_profile", "irp_output_profile", "irp_event_rate_scheme",
    "irp_currency", "irp_currency_scheme", "irp_currency_scheme_vintage",
    "analysis_template", "analysis_template_tag",
    "template_suite", "template_suite_item",
    # Iteration 8 — the perspective kind table (spec 011).
    "analysis_perspective_kind",
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
        for ddl in (*ITERATION1_SCHEMA, *ITERATION2_SCHEMA, *ITERATION3_SCHEMA,
                    *ITERATION4_SCHEMA):
            conn.execute(ddl)
        return {
            table: {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for table in (*EXACT_MATCH_TABLES, *SUBSET_TABLES)
        }
    finally:
        conn.close()
