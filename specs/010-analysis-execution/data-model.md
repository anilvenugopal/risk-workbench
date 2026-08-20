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
| `irp_id` | NOT NULL → **NULL** | RM analysisId exists only after the job finishes; backfilled by `backfill_analysis_detail` |
| `name` | unchanged (NVARCHAR(256) NULL) | For executed rows: the exact ≤64-char name sent to RM (suffix included) |
| `full_name` | **new** NVARCHAR(256) NULL | Untruncated `portfolio + " " + template` (+ suffix); set for executed rows, NULL for broker rows (P-05/P-10) |
| `irp_portfolio_id` | **new** Uuid NULL FK → `irp_portfolio.id` | The portfolio the analysis ran against (trustworthy — workbench submitted it) |
| `analysis_template_id` | **new** Uuid NULL FK → `analysis_template.id` | The template it came from; survives template soft-delete |
| `execution_id` | **new** Uuid NULL | The run's UUID — equals the `execute_analysis_batch` row's `requestor_id`; the "originating submission context" of FR-008 together with `inserted_by` |
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
- New index `ix_irp_analysis_edm_id` on `(edm_id)` (the user-executed section's read).

`status_code` keeps the existing `irp_analysis_status_kind` codes (T-07):
`pending` (row written, submit not yet confirmed) → `running` (job submitted,
non-terminal) → `ready` (FINISHED + backfilled) / `error` (FAILED, CANCELLED, or
terminal SUBMISSION FAILED). Live detail comes from the joined `irp_job`.

## 2. `irp_job` — analysis linkage and retry inputs

| Column | Change | Notes |
|---|---|---|
| `irp_portfolio_id` | **new** Uuid NULL FK → `irp_portfolio.id` | Reconciles DATA_MODEL §8 (ER diagram already lists it); flip the negative assertion in `tests/sqlserver/test_job_tables_migration.py:53` |
| `irp_analysis_id` | **new** Uuid NULL FK → `irp_analysis.id` | Joins the user-executed section to job status; also the retry batch's per-entity key (T-09) |
| `request_params` | **new** NVARCHAR(MAX) NULL | JSON snapshot of the submit kwargs, written at first attempt; the retry batch resubmits from it verbatim (approved-plans rule) |

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
| `rwb_job_type_kind` | `execute_analysis_batch`, `backfill_analysis_detail` (`retrieve_analysis_results` already seeded) |
| `analysis_perspective_kind` | **new table**, standard kind shape: `GR` (Gross), `GU` (Ground-Up), `RL` (Reinsurance Layer) — loss phase |

No `irp_job_type_kind` change (`analysis` already seeded). No `irp_analysis_status_kind`
change (T-07).

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

Dependency: add `pyarrow` (Parquet writes; loss phase, T-13).
