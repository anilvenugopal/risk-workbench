# Contract: job monitoring routes

Server-rendered (Article 8) — HTMX partial swaps, not a JSON API. No client-side app touches these routes directly.

## `GET /workflows/rwb-jobs`

Fills an existing, previously-stubbed nav slot: `workflows.rwb_jobs` in `app/nav/manifest.py` (under the Workflows rail root, alongside Active/Review Queue/IRP Jobs/Exceptions), already routed and templated as a placeholder ("RWB jobs will appear here") in `app/routers/shell.py` and `app/templates/pages/workflows_rwb_jobs.html`. No new nav node — this contract replaces that stub's handler and template body.

Read-only monitoring page. Lists every `rwb_job` row, grouped by `rwb_job_type`, ordered within each group by status then most-recent `updated_at`.

Each row renders:

| Field | Source | Notes |
|---|---|---|
| Job type | `rwb_job.rwb_job_type` | |
| Status | `rwb_job.status_code` | `pending` rows render as a distinct "queued" marker, not a bar with a start time — `submitted_at` is null until claimed. |
| Elapsed | `submitted_at` → now (`running`) or `submitted_at` → `completed_at` (terminal) | `pending` rows show "queued since `inserted_at`" instead. |
| Failure detail | `error_detail` | Shown only when `status_code = 'failed'`. |
| Action | Cancel (pending only) / Resubmit (failed only) / none (running, succeeded, cancelled) | See below. |

No pagination requirement stated in spec.md — if the row count makes this impractical, add pagination as an implementation detail, not a contract change.

## `POST /workflows/rwb-jobs/{id}/cancel`

- Precondition: row `id` exists and `status_code = 'pending'`.
- Effect: `cancel_rwb_job(rwb_job_id=id)` — `UPDATE rwb_job SET status_code = 'cancelled' WHERE id = :id AND status_code = 'pending'`.
- Response:
  - Rowcount 1 (won the race): re-render that row's partial showing `cancelled`, swapped via `hx-target` on the row, `hx-swap="outerHTML"`.
  - Rowcount 0 (lost the race — a worker claimed it first, or it was already cancelled/terminal): re-render the row's partial showing its **current** actual status (re-read after the failed update), not an error. The UI reflects reality; it does not report a failure for a race that resolved the other way.
- Confirmation: `hx-confirm` before submitting — cancelling is irreversible (a cancelled job is not retried by anything).
- CSRF: same CSRF requirement as every other state-changing route (Article 13).

## `POST /workflows/rwb-jobs/{id}/resubmit`

- Precondition: row `id` exists and `status_code = 'failed'`.
- Effect: calls the existing `ensure_pending_rwb_job` with that row's own `requestor_type`/`requestor_id`/`rwb_job_type`/`input_data` — unchanged function, no new logic. Resets the same row: `status_code = 'pending'`, `attempt_count += 1`, `output_data`/`error_detail`/`completed_at`/`submitted_at` cleared.
- Response: re-render the row's partial showing `pending` ("queued"). The prior `error_detail` is gone from this point on — nothing in this contract preserves it (see `data-model.md`; this is a stated limitation, not a bug).
- Precondition failure (row is not `failed` — e.g. a concurrent resubmit already moved it to `pending`): re-render the row's current actual state, not an error.
- CSRF: same as Cancel.

## Explicitly not provided by this contract

- No route to stop or cancel a `running` job.
- No route to submit a new job of an arbitrary type.
- No route to drain a queue.
- No route to change worker process/thread counts.
- No route to reorder queued jobs.

(Matches spec.md's Out of scope section and `docs/CR/CR_04a__JOB_MONITORING_UI.md` §6 — listed here so a reviewer checking this contract doesn't have to cross-reference the CR to know these are deliberate, not missing.)
