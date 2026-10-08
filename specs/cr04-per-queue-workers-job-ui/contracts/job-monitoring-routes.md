# Contract: job monitoring routes

Server-rendered (Article 8) — HTMX partial swaps, not a JSON API. No client-side app touches these routes directly.

## `GET /workflows/rwb-jobs`

Fills an existing, previously-stubbed nav slot: `workflows.rwb_jobs` in `app/nav/manifest.py` (under the Workflows rail root, alongside Active/Review Queue/IRP Jobs/Exceptions), now served by `app/routers/rwb_jobs.py` (moved off the placeholder stub previously in `app/routers/shell.py` / `app/templates/pages/workflows_rwb_jobs.html`). No new nav node.

Monitoring page. Lists `rwb_job` rows 50 to a page (`rwb_job_service.PAGE_SIZE`, applied with `db.row_limit`) in the order picked below, newest first by default, narrowed by the filters below (all optional, AND-combined):

| Filter | Matches on | Default |
|---|---|---|
| Job type | `rwb_job.rwb_job_type` | off |
| Job status | `rwb_job.status_code`, plus the computed "dead" | off |
| Submitted by | `rwb_job.inserted_by` | **current user** (no `submitted_by` param at all); `submitted_by=any` clears it; an explicit list narrows to those analysts |

A job with no `inserted_by` (a `dummy_*` job from the CLI) is listed only under Anyone. With no query at all and no rows, the table says the analyst has submitted no RWB jobs and links to `submitted_by=any`.

Each row renders:

| Field | Source | Notes |
|---|---|---|
| Job type | `rwb_job.rwb_job_type` | Sortable. |
| Entity | kind · name per `docs/DATA_MODEL.md` §8, kind label from `rwb_job_context_type_kind` (link kind for `execution`/`result_export`) | Sortable by name. "—" for a job with no context. |
| Submitted by | `rwb_job.inserted_by` → `app_user.display_name` | Sortable. "—" when null. |
| Status | `rwb_job.status_code`, plus the computed `is_dead` flag | Sortable. `pending` rows render as a distinct "queued" marker. A `running` row with `is_dead = 1` (heartbeat missing or older than `settings.rwb_heartbeat_stale_secs`) renders as a "Dead" chip, not "Running" — `status_code` is still `running` underneath. |
| Submitted at | `submitted_at` | Sortable by `COALESCE(submitted_at, inserted_at)`, so a queued job sorts by when it was queued. Shows "—" for `pending` (null until claimed). |
| Elapsed | a computed duration, not a timestamp | Not sortable. `pending`: now minus `inserted_at`, prefixed "queued". `running`/dead: now minus `submitted_at`. Terminal: `completed_at` minus `submitted_at` (a fixed span). "—" when there's nothing to compute (a terminal row that never got a `submitted_at`, e.g. `dummy_wait`/`sync_irp_metadata` failing before being claimed). |
| Failure detail | `error_detail` | Shown only when `status_code = 'failed'`. |
| Action | Cancel (`pending`, `failed`, or dead `running`) / Resubmit (`failed` only) / none (live `running`, `succeeded`, `cancelled`) | See below. A `failed` row shows both Cancel and Resubmit. |

Every sortable column is a clickable header (same click-to-sort convention as `pages/submissions.html`, D15): clicking flips direction; clicking a different column starts it in that column's own default direction (text ascending, Submitted at descending). The sort runs in SQL before the page is cut, so paging walks every matching job in that order. A header click returns to page 1. With no `sort` param the page renders Submitted at descending and marks that header as sorted.

Pager: `page` (default 1). Prev and Next links appear once there is a second page, carry the filters and sort, and are real hrefs, as on `/workflows/irp-jobs`. The service reads one row past the page to know a next page exists, without a `COUNT`. Changing a filter returns to page 1; a page past the last shows a link back to page 1. The 3-second poll re-renders the same page.

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
