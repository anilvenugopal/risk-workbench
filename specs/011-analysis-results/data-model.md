# Data Model — Analysis Results Sync & Viewing (spec 011)

Deltas only. Canonical definitions: [docs/DATA_MODEL.md](../../docs/DATA_MODEL.md)
§6 (`irp_analysis.loss_results`), §8 (`retrieve_analysis_results` chains), §9
(export-only machinery, untouched). All changes land in
`alembic/versions/0001_initial.py`, `infra/scripts/seed_db.py`, and
`tests/iteration1_mirror.py` (drop-create-seed, single revision).

## 1. `irp_analysis.loss_results` — new column

```text
loss_results  NVARCHAR(MAX)  NULL
```

- The bounded per-perspective viewing extract, written whole by the
  `retrieve_analysis_results` worker in one UPDATE. JSON contract:
  [contracts/loss-results.md](contracts/loss-results.md).
- `NULL` = not fetched yet (views show results-pending, FR-008). A perspective
  the analysis did not produce is present with value `null` inside the JSON —
  "fetched, nothing there" (FR-004).
- Broker rows are keyed (`rdm_id`, `irp_id`) and shared by every EDM copy, so
  once-per-RDM storage (spec non-negotiable 3) needs no further machinery.
- No index: always read via the row's PK / existing list queries.

## 1b. `irp_analysis.submitted_settings` — new column (T-09)

```text
submitted_settings  NVARCHAR(MAX)  NULL
```

- The approved plan item this analysis was submitted with, stored verbatim by
  `_claim_analysis` in the same INSERT that claims the row
  (`app/workers/analysis_jobs.py`): `currency` (code, scheme, vintage,
  `asOfDate`), `event_rate_scheme_name`, `min_loss_threshold`,
  `num_max_loss_event`, `franchise_deductible`,
  `treat_construction_occupancy_as_unknown`, `template_name`, and the profile
  names. The expanded row's **Analysis settings** group reads it (FR-022).
- Own analyses only. `NULL` on broker rows, which is what the expanded row
  renders as *not returned* — Risk Modeler returns none of these fields, and
  currency scheme and vintage exist nowhere else (they are chosen at submit
  time per suite, spec 009 P-11).
- Written once at claim time and never updated: a template edited after the run
  must not change what a finished analysis reports (Article 8).
- No index: read via the row's PK on expansion.

## 2. `analysis_perspective_kind` — new kind table (T-06, Article 3)

```text
analysis_perspective_kind
  code         NVARCHAR(10)   PK
  label        NVARCHAR(100)  NOT NULL
  sort_order   INT            NOT NULL
  inserted_at  DATETIME2      NOT NULL DEFAULT GETUTCDATE()
```

Seeds (spec O-07; first sort_order = the screen-wide default, FR-012):

| code | label | sort_order |
|---|---|---|
| `GR` | Gross | 10 |
| `RL` | Reinsurance Layer | 20 |
| `WX` | Working Excess | 30 |
| `QS` | Quota Share | 40 |
| `GU` | Ground Up | 50 |

- The retrieval worker's request list and every perspective toggle read codes,
  labels, and order from this table — the five-code set changes in one seed.
- Labels for WX/QS are the workbench's display wording; confirm exact wording
  with CIC at the next demo (display-only, not schema-blocking).
- No FK from `loss_results` (JSON keys mirror RM's `perspectiveCode`
  vocabulary verbatim); the kind table owns the app-side vocabulary the same
  way `breakout_dimension_kind` does.

## 3. `rwb_job_requestor_type_kind` — new seed row (T-01)

| code | label |
|---|---|
| `irp_analysis` | IRP Analysis |

Retrieval jobs are keyed `(requestor_type='irp_analysis', requestor_id=<irp_analysis.id>,
rwb_job_type='retrieve_analysis_results')`:

- The queue's `UNIQUE(requestor_type, requestor_id, rwb_job_type)` is the
  FR-006 dedup — any re-fired trigger is a no-op insert.
- Views join this key to surface a failed retrieval's `error_detail` (SC-005)
  and the pending/failed/ready results state.

`retrieve_analysis_results` already exists in `rwb_job_type_kind` (seeded by
spec 010) — no job-type change.

## 4. Return-period sets (constants, not schema)

Fixed by spec O-03; owned by the extract-builder module as named business-rule
constants (they define the stored shape, so they live worker-side, not in the
UI):

- **Stored / expanded**: 5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000
- **Condensed display subset**: 50, 100, 250, 500, 1000, 10000

## 5. Read-model fields (no schema)

Computed in `analysis_service` from existing columns:

| Field | Source |
|---|---|
| origin (own / broker) | `rdm_id IS NULL` (existing §6 rule; no stored column) |
| currency | `settings_metadata ->> 'currencyCode'` (T-05); NULL → `—` |
| AAL (per selected perspective) | `loss_results` JSON |
| results state | `loss_results IS NOT NULL` → ready; else retrieval job row `failed` → failed + `error_detail`; else pending |
| condensed / expanded extract | `loss_results` JSON filtered to the §4 sets |
| Metadata group (engine version, analysis type, peril, subperil, framework, event rate scheme) | `settings_metadata` via `AnalysisSettings`, which gains a `framework` field — `_to_display` folds `analysisFramework` into `analysis_mode` today |
| analysis template | `analysis_template_id` join (own rows); broker rows blank |
| Analysis settings group | `submitted_settings` JSON (§1b); broker rows blank |
| broker Risk Modeler link / Submitted | `BrokerAnalysis` gains `rm_url` (built like the own-row link) and `created_at` from the payload's `createDate` |
