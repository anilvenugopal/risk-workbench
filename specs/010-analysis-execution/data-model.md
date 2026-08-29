# Data Model: Analysis Execution (spec 010)

All changes are edits to the single revision `alembic/versions/0001_initial.py`
(drop-create-seed, T-06). Every change below must land in four places together:
the migration, `infra/scripts/seed_db.py` (kind seeds), `tests/iteration1_mirror.py`
(SQLite DDL + seeds + `EXACT_MATCH_TABLES`), and `docs/DATA_MODEL.md` (§6/§8/§9).

## 1. `irp_analysis` — reshaped for own (executed) analyses

Today the table is broker-shaped. Changed/added columns:

| Column | Change | Notes |
|---|---|---|
| `rdm_id` | NOT NULL → **NULL** | Executed analyses have no RDM |
| `source_rdm_name` | NOT NULL → **NULL** | Broker-only |
| `irp_id` | NOT NULL → **NULL** | RM analysisId exists only after the job finishes; backfilled by `finalize_analysis` |
| `irp_app_analysis_id` | **new** NVARCHAR(64) NULL | RM `appAnalysisId` from the analysis-details payload — the id the RM web UI route takes; feeds the grid's Risk Modeler link (FR-022). NULL until backfilled |
| `name` | unchanged (NVARCHAR(256) NULL) | For executed rows: the exact ≤64-char name sent to RM (suffix included) |
| `full_name` | **new** NVARCHAR(512) NULL | Untruncated `CRE_{portfolio}_{template}` (+ suffix); set for executed rows, NULL for broker rows (P-05/P-10). 512 fits `CRE_` + a 256-char portfolio name + `_` + a 200-char template name — only `name` is truncated |
| `irp_portfolio_id` | **new** Uuid NULL FK → `irp_portfolio.id` | The portfolio the analysis ran against (trustworthy — workbench submitted it) |
| `analysis_template_id` | **new** Uuid NULL FK → `analysis_template.id` | The template it came from; survives template soft-delete |
| `execution_id` | **new** Uuid NULL | The run's UUID — equals the `execute_analysis_batch` row's `requestor_id`; the "originating submission context" of FR-008 together with `inserted_by` |
| `execution_item_no` | **new** INT NULL | The plan item's ordinal within the run. With cross-suite dedup dropped (P-02 as amended), `(execution_id, portfolio, template)` can repeat — `(execution_id, irp_portfolio_id, execution_item_no)` is the worker's exact resume key, enforced by `uq_irp_analysis_execution_item`. NULL for broker rows |
| `failure_reason` | **new** NVARCHAR(MAX) NULL | RM's run-failure message (poller, T-08) or the submit exception message (worker) |

Constraints:

- **New CHECK** `ck_irp_analysis_origin`: `edm_id IS NOT NULL OR rdm_id IS NOT NULL`
  (DATA_MODEL §6's stated rule, now enforced). Own = `edm_id` set / `rdm_id` NULL;
  broker = the reverse. Origin stays derived from `rdm_id`, no stored column.
- `uq_irp_analysis_rdm_irp` `UNIQUE(rdm_id, irp_id)` → **filtered unique index**
  `WHERE rdm_id IS NOT NULL AND irp_id IS NOT NULL` (plain UNIQUE would treat the many
  executed rows' NULLs as equal).
- **New filtered unique index** `uq_irp_analysis_live_edm_name` on `(edm_id, name)`
  `WHERE edm_id IS NOT NULL AND deleted_at IS NULL` — backs the P-10 suffix rule (T-05).
- **New filtered unique index** `uq_irp_analysis_execution_item` on
  `(execution_id, irp_portfolio_id, execution_item_no)` `WHERE execution_id IS NOT NULL`
  — the worker reads the resume key as a scalar subquery, which raises on a duplicate,
  and `uq_irp_analysis_live_edm_name` does not prevent one (a rerun landing on a
  different `_n` suffix passes it).
- New index `ix_irp_analysis_edm_id` on `(edm_id)` (the user-executed section's read).

`irp_analysis_status_kind` drops `running` and keeps three codes (T-07): `pending` (the
only non-terminal value — written at claim, held through submit and the whole run) →
`ready` (FINISHED + backfilled) / `error` (FAILED, CANCELLED, terminal SUBMISSION
FAILED, or a failed backfill). Progress while an analysis runs is `irp_job.status`,
which the poller keeps current; every write that leaves `pending` is terminal, so
`is_live` is the single test `status_code == 'pending'`.

`exposure_resource_id` stays what §6 of DATA_MODEL says it is — RM's numeric
`exposureResourceId` for broker rows (R9/FR-036). Executed rows leave it NULL; the
portfolio `resourceUri` they were submitted against lives in `irp_job_resource`.

## 2. `irp_job` — analysis linkage and retry inputs

| Column | Change | Notes |
|---|---|---|
| `irp_portfolio_id` | **new** Uuid NULL FK → `irp_portfolio.id` | Reconciles DATA_MODEL §8 (ER diagram already lists it); flip the negative assertion in `tests/sqlserver/test_job_tables_migration.py:53` |
| `irp_analysis_id` | **new** Uuid NULL FK → `irp_analysis.id` | Joins the user-executed section to job status; also the retry batch's per-entity key (T-09) |
| `request_params` | **new** NVARCHAR(MAX) NULL | JSON snapshot of the submit kwargs, written at first attempt; the retry batch resubmits from it verbatim (approved-plans rule) |
| `completed_at` | unchanged | For `SUBMISSION FAILED` it doubles as the retry backoff clock, stamped at insert and on each failed attempt. A successful resubmit clears it — the job is back in flight |

New index `ix_irp_job_irp_analysis_id` on `(irp_analysis_id)`.
`resource_uri` continues to live in `irp_job_resource` (`resource_type='portfolio'`),
written at submit from `request_body["resourceUri"]`.

## 3. `analysis_result_meta` — new (loss phase only)

One row per (analysis, perspective), immutable once written (no `as_of`). Hybrid per
DATA_MODEL §9: this row is the list-view index; row-level data is Parquet.

| Column | Type | Notes |
|---|---|---|
| `id` | Uuid PK NEWID | |
| `analysis_id` | Uuid NULL FK → `irp_analysis.id` | Own results (this spec) |
| `rdm_id` | Uuid NULL FK → `irp_rdm.id` | Broker dedup — column reserved, never written here (P-12) |
| `analysis_name` | NVARCHAR(256) NOT NULL | Snapshot |
| `perspective_code` | NVARCHAR(10) NOT NULL FK → `analysis_perspective_kind.code` | |
| `aal` | FLOAT NULL | From `get_stats` |
| `std_dev` | FLOAT NULL | From `get_stats` |
| `max_event_loss` | FLOAT NULL | Max `positionValue` over the ELT |
| `elt_record_count` | INT NULL | |
| `has_plt` | BIT NOT NULL DEFAULT 0 | True only for HD |
| `elt_file_path` / `ep_file_path` / `stats_file_path` | NVARCHAR(1024) NULL | Relative to `OUTPUTS_BASE_DIR` |
| `plt_file_path` | NVARCHAR(1024) NULL | HD only |
| `retrieved_at` | DATETIME2 NOT NULL | |
| audit 4 (`inserted_at/by`, `updated_at/by`) | | |

Constraints: CHECK `ck_analysis_result_meta_origin` — exactly one of `analysis_id` /
`rdm_id` set; filtered unique `uq_analysis_result_meta_analysis_perspective` on
`(analysis_id, perspective_code)` `WHERE analysis_id IS NOT NULL` (the retrieval
worker's idempotency key).

Parquet layout (T-14): `{OUTPUTS_BASE_DIR}/analyses/{analysis_id}/{perspective_code}/{result_type}.parquet`,
`result_type ∈ elt | ep | plt | stats`. Return-period losses and OEP/AEP points are read
from the `ep` file at view time (T-13).

## 4. Kind-table seeds

| Table | New rows |
|---|---|
| `rwb_job_type_kind` | `execute_analysis_batch` (29), `finalize_analysis` (31) — in the migration itself, not only `seed_db.py`: `rwb_job.rwb_job_type` has an FK here, so a freshly migrated database rejects every spec-010 enqueue without them (`retrieve_analysis_results` already seeded) |
| `analysis_perspective_kind` | **new table**, standard kind shape: `GR` (Gross), `GU` (Ground-Up), `RL` (Reinsurance Layer) — loss phase |

No `irp_job_type_kind` change (`analysis` already seeded). `irp_analysis_status_kind`
drops `running` (§1).

## 5. Persisted plan (not a table)

The approved run is JSON in the `execute_analysis_batch` row's `rwb_job.input_data`
(T-01) — there is no run/batch table (spec Key Entities). Shape in
[contracts/worker-poller.md](contracts/worker-poller.md). `rwb_job` keys:
`requestor_type='analyst_request'`, `requestor_id=execution_id` (fresh UUID per
execution), `rwb_job_type='execute_analysis_batch'`.

## 6. Config

| Setting | Change |
|---|---|
| `IRP_SUBMISSION_MAX_RETRIES` | default `None` → `3` (PRD §14.3; T-09) |
| `IRP_SUBMISSION_RETRY_BASE_SECS` | **new**, default 60 — backoff base for the retry batch |
| `OUTPUTS_BASE_DIR` | existing; gains the `analyses/` subtree (loss phase) |
| `DEFAULT_ANALYSIS_CURRENCY_CODE` | **new**, default `USD` — pre-fills the modal's currency picker (P-16, T-19) |
| `DEFAULT_ANALYSIS_CURRENCY_SCHEME` | **new**, default `RMS` — pre-fills the scheme picker |
| `DEFAULT_ANALYSIS_CURRENCY_VINTAGE` | **new**, default empty — pre-fills the vintage picker; empty or cache-absent ⇒ no pre-selection |

The currency defaults are pinned configuration, not a table (note 17 D6/D7/O17-3):
ops edits `.env` on the VM; the system never advances them when a newer vintage syncs.

Dependency: add `pyarrow` (Parquet writes; loss phase, T-13).
