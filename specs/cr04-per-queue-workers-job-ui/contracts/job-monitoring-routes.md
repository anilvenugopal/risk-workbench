# Contract: job monitoring routes

Server-rendered (Article 8) — HTMX partial swaps, not a JSON API. No client-side app touches these routes directly.

**Updated per CR-04a's rewrite (see `docs/CR/CR_04a__JOB_MONITORING_UI.md`):**
search reaches submission through `rwb_job.link_type`/`link_id` →
`submission_edm`/`submission_rdm` → `submission` (CR-04c's columns), never
through `requestor_type`/`requestor_id`. This contract's route shapes are
otherwise as originally specified.

## `GET /workflows/rwb-jobs`

Fills an existing, previously-stubbed nav slot: `workflows.rwb_jobs` in `app/nav/manifest.py` (under the Workflows rail root, alongside Active/Review Queue/IRP Jobs/Exceptions), now served by `app/routers/rwb_jobs.py` (moved off the placeholder stub previously in `app/routers/shell.py` / `app/templates/pages/workflows_rwb_jobs.html`). No new nav node.

Monitoring + search page. Lists `rwb_job` rows, grouped by `rwb_job_type`, ordered within each group by status then most-recent `updated_at`, narrowed by the filters below (all optional, AND-combined):

| Filter | Matches on | Default |
|---|---|---|
| Submission name / cedant | word-and match, same as `submission_service.list_submissions`'s `name`/`cedant_name` | off |
| Submission status | `submission.status_code`, from `submission_status_kind` | off |
| Owner | `submission.assigned_analyst_id`, reached via the job's linked EDM/RDM | **current user** (no `owner` param at all); `owner=any` clears it; an explicit list narrows to those analysts |
| Job type | `rwb_job.rwb_job_type` | off |
| Job status | `rwb_job.status_code` | off |

A job whose `link_type = 'not_applicable'`, or whose EDM/RDM belongs to no submission, is excluded by any submission-name/status/owner filter but still listed when none of those three are set. A job's EDM/RDM belonging to more than one submission does not fan the job out into multiple rows — the submission filters match "at least one."

Each row renders:

| Field | Source | Notes |
|---|---|---|
| Job type | `rwb_job.rwb_job_type` | Sortable. |
| EDM/RDM | `rwb_job.link_type`/`link_id` → `irp_edm`/`irp_rdm` | Sortable. "—" for `not_applicable`. |
| Submission(s) | linked EDM/RDM → `submission_edm`/`submission_rdm` → `submission` | Sortable. Each name links to `/submissions/{id}`, opening in a new tab. First name + "+N more" for multiple, "—" for none — same display convention as `partials/library_table.html`. |
| Status | `rwb_job.status_code`, plus the computed `is_dead` flag | Sortable. `pending` rows render as a distinct "queued" marker. A `running` row with `is_dead = 1` (heartbeat missing or older than `settings.rwb_heartbeat_stale_secs`) renders as a "Dead" chip, not "Running" — `status_code` is still `running` underneath. |
| Submitted at | `submitted_at` | Sortable. The raw timestamp; "—" for `pending` (null until claimed). |
| Elapsed | a computed duration, not a timestamp | Sortable (by the underlying seconds, not the formatted string). `pending`: now minus `inserted_at`, prefixed "queued". `running`/dead: now minus `submitted_at`. Terminal: `completed_at` minus `submitted_at` (a fixed span). "—" when there's nothing to compute (a terminal row that never got a `submitted_at`, e.g. `dummy_wait`/`sync_irp_metadata` failing before being claimed). |
| Failure detail | `error_detail` | Shown only when `status_code = 'failed'`. |
| Action | Cancel (`pending`, `failed`, or dead `running`) / Resubmit (`failed` only) / none (live `running`, `succeeded`, `cancelled`) | See below. A `failed` row shows both Cancel and Resubmit. |

Every sortable column is a clickable header (same click-to-sort convention as `pages/submissions.html`, D15): clicking flips direction; clicking a different column starts it in that column's own default direction. Sorting orders the already-filtered rows; it is independent of the filters above.

No pagination requirement stated in spec.md — if the row count makes this impractical, add pagination as an implementation detail, not a contract change.

## `GET /workflows/rwb-jobs/table`

The table fragment alone (filter values carried as query params), for the filter form's HTMX target and for self-polling while any listed row is non-terminal — same shape as `partials/irp_jobs_table.html`.

## `POST /workflows/rwb-jobs/{id}/cancel`

- Precondition: row `id` exists and is one of `pending`, `failed`, or a `running` row whose heartbeat is missing or older than `settings.rwb_heartbeat_stale_secs` ("dead").
- Effect: `cancel_rwb_job(rwb_job_id=id)` — one guarded `UPDATE rwb_job SET status_code = 'cancelled' WHERE id = :id AND (status_code = 'pending' OR status_code = 'failed' OR (status_code = 'running' AND <heartbeat missing or stale>))`.
- Response:
  - Rowcount 1 (won the race, or the row was `failed`): re-render that row's partial showing `cancelled`, swapped via `hx-target` on the row, `hx-swap="outerHTML"`.
  - Rowcount 0 (lost the race — a worker claimed a `pending` row first, the poller's reconciler reclaimed a dead row to `pending` first, or the row was already `succeeded`/`cancelled`/a live `running` row): re-render the row's partial showing its **current** actual status (re-read after the failed update), not an error. The UI reflects reality; it does not report a failure for a race that resolved the other way.
- Confirmation: `hx-confirm` before submitting — cancelling is irreversible (a cancelled job is not retried by anything, and a `failed` row cancelled this way is no longer eligible for Resubmit either).
- CSRF: same CSRF requirement as every other state-changing route (Article 13).

## `POST /workflows/rwb-jobs/{id}/resubmit`

- Precondition: row `id` exists and `status_code = 'failed'`.
- Effect: calls the existing `ensure_pending_rwb_job` with that row's own `requestor_type`/`requestor_id`/`rwb_job_type`/`input_data` — unchanged function, no new logic. Resets the same row: `status_code = 'pending'`, `attempt_count += 1`, `output_data`/`error_detail`/`completed_at`/`submitted_at` cleared.
- Response: re-render the row's partial showing `pending` ("queued"). The prior `error_detail` is gone from this point on — nothing in this contract preserves it (see `data-model.md`; this is a stated limitation, not a bug).
- Precondition failure (row is not `failed` — e.g. a concurrent resubmit already moved it to `pending`): re-render the row's current actual state, not an error.
- CSRF: same as Cancel.

## Explicitly not provided by this contract

- No route to stop or cancel a `running` job with a **live** heartbeat.
- No route to submit a new job of an arbitrary type.
- No route to drain a queue.
- No route to change worker process/thread counts.
- No route to reorder queued jobs.

(Matches spec.md's Out of scope section and `docs/CR/CR_04a__JOB_MONITORING_UI.md` §6 — listed here so a reviewer checking this contract doesn't have to cross-reference the CR to know these are deliberate, not missing.)
