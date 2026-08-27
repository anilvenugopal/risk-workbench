# Data Model: Grouping Execution (spec 012)

Deltas to the merged 010+011 schema, all in `rwb_workbench`
(`alembic/versions/0001_initial.py`, single-revision strategy — edit in place,
then Rebuild). Decision references: [plan.md](plan.md) T-04, T-05.

## 1. `irp_analysis` — group rows (T-04)

New column:

| Column | Type | Notes |
|---|---|---|
| `submission_id` | UNIQUEIDENTIFIER NULL, FK `submission.id` | Set on group rows only. Own analyses keep `edm_id`, broker analyses keep `rdm_id`; neither sets `submission_id`. Index `ix_irp_analysis_submission_id`. |

Changed constraint:

- `ck_irp_analysis_origin` becomes
  `edm_id IS NOT NULL OR rdm_id IS NOT NULL OR submission_id IS NOT NULL`.

New index:

- `uq_irp_analysis_live_submission_name (submission_id, name)`
  filtered `WHERE submission_id IS NOT NULL AND deleted_at IS NULL` — one live
  group per (submission, name), the mirror of `uq_irp_analysis_live_edm_name`.

A group row's shape:

| Column | Value |
|---|---|
| `is_group` | 1 |
| `submission_id` | the owning submission |
| `edm_id`, `rdm_id`, `irp_portfolio_id`, `analysis_template_id`, `execution_id`, `execution_item_no` | NULL |
| `name` / `full_name` | submitted (≤64) / untruncated group name (T-09) |
| `status_code` | `pending` at claim → `running` after submit → `ready` after backfill, or `error` + `failure_reason` |
| `submitted_settings` | the approved compose plan verbatim (members, currency, flags) |
| `irp_id`, `settings_metadata`, `exposure_resource_id`, `loss_results` | populated by the existing backfill / retrieve chain |

The row `id` is minted at compose time and carried in the plan
(`group_analysis_id`), so the worker's claim INSERT is idempotent by PK on
redelivery.

`group_parent_id` remains deferred (it cannot model an analysis belonging to
several groups — see `irp_analysis_group_member`).

## 2. `irp_analysis_group_member` — new table (T-05)

| Column | Type | Notes |
|---|---|---|
| `group_analysis_id` | UNIQUEIDENTIFIER NOT NULL, FK `irp_analysis.id` | the group row |
| `member_analysis_id` | UNIQUEIDENTIFIER NOT NULL, FK `irp_analysis.id` | an own analysis, broker analysis, or another group (nesting, FR-018) |
| `inserted_at` | DATETIME2 NOT NULL | |

- PK `(group_analysis_id, member_analysis_id)`.
- Written once by the `submit_grouping` worker at claim, from the approved
  plan. Never updated; deleted only via the group's soft-delete path (rows are
  retained — the group row's `deleted_at` is the visibility gate, matching how
  member analyses keep their own lifecycle).
- An analysis may appear as `member_analysis_id` under many groups.

## 3. Kind-table seeds

| Table | New row | Where |
|---|---|---|
| `rwb_job_type_kind` | `submit_grouping` (label "Submit grouping", sort 33) | `alembic/versions/0001_initial.py`, `infra/scripts/seed_db.py`, `tests/iteration1_mirror.py` — all three, matching the CR-04 three-place convention |
| `irp_job_type_kind` | none — `grouping` (sort 60) is already seeded and unused | — |

No new status vocabularies: group rows use `irp_analysis_status_kind`
(`pending/running/ready/error`), the grouping `irp_job` uses the plain-VARCHAR
external-status mirror (Article 3 carve-out), and the `rwb_job` uses
`rwb_job_status_kind` unchanged.

## 4. `rwb_job` usage (no schema change)

The compose POST enqueues exactly one row:

| Field | Value |
|---|---|
| `requestor_type` | `analyst_request` |
| `requestor_id` | minted `grouping_request_id` (UUID) |
| `rwb_job_type` | `submit_grouping` |
| `input_data` | the approved plan — see [contracts/grouping-worker.md](contracts/grouping-worker.md) |

`uq_rwb_job_requestor_type` gives resubmit-idempotency per request as on every
other op.

## 5. `irp_job` usage (no schema change)

One row per grouping submitted to the platform, written by the worker exactly
as the analysis worker writes it:

| Field | Value |
|---|---|
| `irp_job_type` | `grouping` |
| `irp_analysis_id` | the group row |
| `requested_from_submission_id` | the submission |
| `irp_id` | job id from the `Location` header |
| `last_submission_payload` / `last_submission_response` | request body / wheel result incl. `included_items` |
| `status` | `QUEUED` → poller-tracked to `FINISHED` / `FAILED` / `CANCELLED`; `SUBMISSION FAILED` on a submit exception (no automatic retry for grouping — T-11) |

No `irp_job_resource` rows: the member URIs are already in
`last_submission_payload`, and no reconciliation reads them.
