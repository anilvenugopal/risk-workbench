# Data Model: GeoHaz Execution (Iteration 5)

WORKBENCH only. All changes are edits to the single revision
`alembic/versions/0001_initial.py` (drop-create-seed), mirrored in
`infra/scripts/seed_db.py` and `tests/iteration1_mirror.py` (the
`EXACT_MATCH_TABLES` drift guard enforces column-for-column parity).

## 1. `irp_job` — three new columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `irp_portfolio_id` | Uuid, FK → `irp_portfolio.id` | yes | Set on every geohaz row (submitted and `SUBMISSION FAILED`); NULL for every other job type. Index `ix_irp_job_irp_portfolio_id`. `irp_portfolio` is created after `irp_job` in the revision — add the FK with `op.create_foreign_key` after both tables exist. Fulfils `docs/DATA_MODEL.md:470`. |
| `request_params` | NVARCHAR(MAX) JSON | yes | The analyst-level parameter set (§3). Written by both `record_submitted_irp_job` and `record_submission_failure` (new optional arguments, alongside the existing `actor_id`). Distinct from `last_submission_payload` (the raw Risk Modeler request body). |
| `completion_summary` | NVARCHAR(MAX) | yes | The `tasks[].output.summary` string copied from the terminal Risk Modeler response. NULL when Risk Modeler supplies no task summary. |

Existing columns carrying the rest of the P-05 record — no change:

| P-05 field | Column |
|---|---|
| Launching analyst | `inserted_by` (Uuid FK → `app_user.id`, passed as `actor_id` by the worker from the rwb_job's input) |
| Submitted timestamp | `submitted_at` |
| Completed timestamp | `completed_at` (stamped by `update_tracking` on terminal) |
| Terminal status | `status` (external-mirror VARCHAR, Article 3 carve-out; `SUBMISSION FAILED` for a submit that never reached Risk Modeler) |
| Completion summary | `completion_summary` |
| Parameter set | `request_params` (§3) |

The submit also writes the existing `irp_job_resource` row
(`resource_type='portfolio'`, `resource_uri` from the request body — the seeded
kind row already fits geohaz).

## 2. `rwb_job` — one new type, no schema change

New `rwb_job_type_kind` seed row: `('run_geohaz', 'Run GeoHaz', 28)` — added in
all three seed locations (`0001_initial.py`, `seed_db.py` MERGE,
`iteration1_mirror.py` `RWB_JOB_TYPE_SEED`).

One `run_geohaz` rwb_job per selected portfolio:

- `requestor_type = 'analyst_request'`, `requestor_id = irp_portfolio.id` — the
  `UNIQUE(requestor_type, requestor_id, rwb_job_type)` head plus
  `ensure_pending_rwb_job` (revive-or-noop) prevents double-enqueue per portfolio
  and lets a relaunch revive the terminal head (FR-007).
- `input_data` JSON:

```json
{
  "irp_portfolio_id": "<uuid>",
  "irp_edm_id": "<uuid>",
  "edm_name": "…",
  "portfolio_name": "…",
  "requested_by_user_id": "<uuid>",
  "params": { …the request_params document, §3… }
}
```

Names are captured at launch so the worker submits by name (Article 2) without a
request-path Risk Modeler read; the worker re-reads nothing from RM before submit.

## 3. `request_params` document

One JSON document per lookup, identical for every portfolio in a launch (FR-003):

```json
{
  "data_version": "25.0",
  "model_family": "DLM",
  "perils": ["earthquake", "windstorm"],
  "missing_locations": "overwrite"
}
```

- `data_version` — one of `GEOHAZ_DATA_VERSIONS` (config, research R6); maps to the wheel's `version`.
- `model_family` — always `"DLM"` this iteration (record-only; no wire representation, research R5).
- `perils` — non-empty subset of `["earthquake", "windstorm"]`; each becomes one hazard layer (the layer `name`).
- `missing_locations` — `"overwrite"` (default) or `"skip"`; maps to `layerOptions.skipPrevHazard` False/True on every hazard layer.

This is a snapshot record for display (like `irp_portfolio.exposure_detail`) — no
internal code path dispatches on its values, so no kind tables are minted for them
(Article 3 rationale).

## 4. Derived state — never stored

### "Hazard looked up?" column (FR-011, P-07)

Computed per portfolio from geohaz `irp_job` rows (`irp_portfolio_id = :pid AND
irp_job_type = 'geohaz'`) plus the pending/running `run_geohaz` rwb_job head,
first match wins:

1. A pending/claimed `run_geohaz` rwb_job with no `irp_job` yet → in-line state **Queued**.
2. A non-terminal `irp_job` exists → its **status** in-line (QUEUED/PENDING/RUNNING/…).
3. Any `irp_job` with `status = 'FINISHED'` → **Yes** (a later failure leaves this at Yes).
4. Any geohaz `irp_job` rows at all → **Failed**.
5. Otherwise → **No** (normal state, never a warning).

The table render computes states for all portfolios in one grouped query; the
per-cell poll fragment computes one.

### P-06 launch eligibility

A portfolio is selectable iff it has no non-terminal geohaz `irp_job` and no
pending/claimed `run_geohaz` rwb_job head. Enforced in the form (checkbox
disabled) and re-validated in the launch POST; the unique rwb_job head is the
race backstop.

### Lookup history (FR-022)

`SELECT … FROM irp_job LEFT JOIN app_user ON app_user.id = irp_job.inserted_by
WHERE irp_portfolio_id = :pid AND irp_job_type = 'geohaz' ORDER BY inserted_at DESC`
— each row renders parameters (`request_params`), analyst, submitted/completed
timestamps, status, and `completion_summary` (or "unavailable", FR-023).

## 5. Unchanged

`irp_portfolio` (a lookup never alters the portfolio's own state), `irp_edm`,
`submission.*`, all statuses (no new event-sourced status — Article 4).
`irp_job_type_kind` already seeds `('geohaz', 'Geohazard', 40)`.
EXPOSURE/LOSS untouched; DATABRIDGE never in schema scope.

**DB lifecycle choice: Rebuild** (`make db-rebuild`) — required by the three new
columns and the kind row.
