# Change Request — Job monitoring, cancel, and resubmit UI

**ID:** CR-004a
**Status:** Ready to apply
**Depends on:** CR-004 (per-queue workers) is not a hard prerequisite for this UI, but the "kill a stuck job" operational step this UI's monitoring view surfaces is safer once CR-004 lands (killing one queue's process no longer takes down the other three job types).
**Applies to:** `app/services/rwb_job_service.py`, new routes/templates, `alembic/versions/`, tests.

## 1. Summary

A read-only monitoring page for `rwb_job`, plus two actions:

- Cancel a `pending` job before it's claimed.
- Resubmit a `failed` job — resets the same row in place.

No mechanism is added to stop a `running` job, submit an arbitrary new job, or drain a queue from the UI. See §6.

## 2. Why

Analysts and operators currently have no visibility into `rwb_job` beyond whatever each entity's detail page already surfaces (e.g. `latest_backfill_status` on the EDM page). There's no single place to see every job across types, no way to tell a queued job not to run, and no way to retry a failed one without going through whatever entity-specific flow originally created it — if one exists at all.

## 3. Decisions

1. **Monitoring view is read-only against `rwb_job`.** No new table for display state; the view queries `rwb_job` (and the join patterns already used in `edm_service.py`/`rdm_service.py`) directly.
2. **Queued jobs are shown even though they have no start time.** A `pending` row is rendered as a distinct marker (not a zero-width bar), since a Gantt bar needs a start time and `pending` rows don't have one (`submitted_at` is set only on claim, in `claim_rwb_job`).
3. **Cancel only applies to `pending` rows.** A new terminal status (`cancelled`) is added to `rwb_job_status_kind`. The cancel action is a guarded update: `UPDATE rwb_job SET status_code = 'cancelled' WHERE id = :id AND status_code = 'pending'` — same shape as `claim_rwb_job`'s atomic claim, so a race against a worker claiming the same row resolves safely (rowcount 0 means the worker won; the job runs).
4. **No mechanism to stop a `running` job.** Cooperative cancellation was considered and rejected (CR-004 §6). A `running` job's only remedy is killing its queue's worker process — an operational step, not a UI feature.
5. **Resubmit calls the existing `ensure_pending_rwb_job`, unchanged.** It resets the same row (same `id`, `attempt_count + 1`, `output_data`/`error_detail`/`completed_at` cleared). No new row, no dedup-key scheme, no schema change beyond decision 3's new status. This was decided explicitly: separate rows per attempt were considered and rejected — see §7 risk 1 for what that decision costs the monitoring view.
6. **No bespoke job-submission form.** Constructing a new job of an arbitrary type from a generic form was considered and rejected — job inputs aren't defined as a reusable schema today (each body's `_load_input` just reads whatever JSON the specific caller wrote), and a generic form would bypass whatever business-state checks the real submission path enforces (e.g. `_upload_edm_body` only proceeds if the EDM is `pending_import`).
7. **No drain trigger in the UI.** Draining a queue is an operator action from a terminal (CR-004 §5.3), not a button in the app.
8. **No worker scale-up/down in the UI.** Changing worker process/thread counts means restarting a systemd unit with different environment values — a privileged host operation the web process does not and should not have rights to perform. Treated as a separate, later decision with its own security review, not part of this CR.
9. **No priority reordering for queued jobs.** Dramatiq's Redis broker delivers messages in the order they were enqueued; a `priority` column on `rwb_job` would not change delivery order, because the worker is handed a specific job id by the already-delivered message before it ever queries SQL. Real priority needs either multiple Redis queues per priority tier or a worker that polls `rwb_job` directly instead of being handed an id — both bigger than this CR. Deferred.

## 4. What changes, by area

### 4.1 Migration: `rwb_job_status_kind`

Add `cancelled` to the seed values. No other schema change.

### 4.2 `app/services/rwb_job_service.py`

Add `cancel_rwb_job(*, rwb_job_id) -> bool`:

```sql
UPDATE rwb_job SET status_code = 'cancelled', updated_at = :now
WHERE id = :id AND status_code = 'pending'
```

Returns whether the update matched a row (mirrors `claim_rwb_job`'s rowcount contract).

No change to `ensure_pending_rwb_job`, `enqueue_rwb_job`, `claim_rwb_job`, or `complete_rwb_job`.

### 4.3 Monitoring route + template (new)

- One page listing `rwb_job` rows grouped by `rwb_job_type`, each row showing `status_code`, `submitted_at`/`completed_at` (or "queued" for `pending` rows with no `submitted_at`), and `error_detail` when `failed`.
- A per-row "Cancel" action, shown only for `pending` rows, calling `cancel_rwb_job`. Confirm before submitting (irreversible: a cancelled job is not retried by anything).
- A per-row "Resubmit" action, shown only for `failed` rows, calling `ensure_pending_rwb_job` with the same `requestor_type`/`requestor_id`/`rwb_job_type`/`input_data` the failed row had.
- No action shown for `running` rows beyond viewing `submitted_at` (how long it's been running).

Per AGENTS.md's UI workflow: this has real new layout, so it needs a rendered HTML preview and approval before the template/route are built.

### 4.4 Tests

- Unit: `cancel_rwb_job` on a `pending` row succeeds; on a `running`/`succeeded`/`failed`/`cancelled` row is a no-op (rowcount 0).
- Unit: a claim racing a cancel — whichever update wins, the other is a no-op; no double-claim, no crash.
- Unit: resubmitting a `failed` row via the existing `ensure_pending_rwb_job` path still behaves as documented (same `id`, `attempt_count` incremented, prior `error_detail` cleared) — this CR doesn't change that function, so this is a regression check, not new behavior to verify.

## 5. Design detail

### 5.1 Why cancel is safe against a concurrent claim

`claim_rwb_job` and `cancel_rwb_job` both gate on `WHERE status_code = 'pending'` and both check rowcount. Whichever runs first wins; the other's update matches zero rows and is a no-op. No lock, no new coordination — the same pattern the codebase already uses for the pending → running transition.

### 5.2 What the monitoring view can and cannot show

The view can show: every job's current status, current attempt's timestamps, and (for `failed`) the current `error_detail`. It cannot show: a history of prior attempts for a job that's been resubmitted, because resubmit overwrites the same row (decision 5). If attempt-history becomes a real requirement later, it needs its own mechanism (e.g. a history table written on resubmit, before the reset) — out of scope here.

## 6. Out of scope

- Stopping a `running` job (decision 4).
- A generic form to submit a new job of any type (decision 6).
- Triggering a queue drain from the UI (decision 7).
- Scaling worker processes/threads from the UI (decision 8).
- Priority reordering of queued jobs (decision 9).
- Preserving failed-attempt history across a resubmit (§5.2) — would require a schema change beyond this CR's scope.

## 7. Residual risks

1. **Resubmit erases the failed attempt's `error_detail`.** An operator who wants to know why attempt 1 failed must look before clicking resubmit — the monitoring view cannot show it afterward. Accepted per the decision to keep one row per job (§3.5).
2. **A `running` job with no cancellation path can only be stopped by killing its worker process.** This is an operational action outside the app, requiring host/systemd access. The monitoring view should say this plainly in the UI (e.g. "contact an operator to stop a running job") rather than implying a cancel exists where it doesn't.
3. **Cancel and resubmit both act on a single row by `id`; neither is queue-aware.** No interaction with CR-004's per-queue split — confirmed no change needed to either function for that reason.

## 8. Acceptance criteria

- `rwb_job_status_kind` includes `cancelled`.
- `cancel_rwb_job` exists, only affects `pending` rows, and is race-safe against a concurrent claim (tested).
- The monitoring page lists jobs by type and status, including `pending` jobs with no start time, with working Cancel (`pending` only) and Resubmit (`failed` only) actions.
- No code was added for: stopping a `running` job, a generic job-submission form, a UI drain trigger, worker scaling, or priority queues.
- The monitoring UI was previewed and approved before the template/route were built (AGENTS.md UI workflow).

## 9. Grep checklist

- `rwb_job_status_kind` (seed values, migration)
- `ensure_pending_rwb_job`, `enqueue_rwb_job`, `claim_rwb_job` (confirm no accidental change)
- `latest_backfill_status`, `latest_backfill_statuses` (existing per-entity job status reads — for consistency with the new monitoring view's own read pattern)
