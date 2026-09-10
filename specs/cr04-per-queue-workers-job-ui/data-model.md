# Data model: Per-queue Dramatiq workers and job monitoring UI

## Schema change

One value added to an existing kind table. No new table, no new column.

**`rwb_job_status_kind`** — add `cancelled`, sort order after `failed`:

```sql
INSERT INTO rwb_job_status_kind (code, label, sort_order) VALUES
    ('cancelled', 'Cancelled', 50);
```

Per this repo's Dev DB Strategy (drop-create-seed, single revision `0001_initial.py` amended in place until production cutover), this is a direct edit to the existing seed block in `alembic/versions/0001_initial.py` (the block that currently seeds `pending`/`running`/`succeeded`/`failed` at sort orders 10/20/30/40) — not a new migration file.

No change to `rwb_job`'s columns, indexes, or `UNIQUE(requestor_type, requestor_id, rwb_job_type)` constraint. `cancelled` is referenced the same way the four existing values are — as an FK target from `rwb_job.status_code` — and requires no new index: the existing `ix_rwb_job_status_code` already covers filtering by any status value, including `cancelled`.

## State transitions

`rwb_job.status_code` transitions, extended by this feature (existing transitions per CR-001/the current codebase; new transition marked):

| From | To | Trigger | Guard |
|---|---|---|---|
| *(insert)* | `pending` | `enqueue_rwb_job` / `ensure_pending_rwb_job` | `UNIQUE(requestor_type, requestor_id, rwb_job_type)` |
| `pending` | `running` | `claim_rwb_job` | `WHERE status_code = 'pending'` |
| `running` | `succeeded` / `failed` | `complete_rwb_job` | none (worker-owned, post-claim) |
| `running` | `pending` | `reconcile_stale_rwb_jobs` (reconciler) | stale heartbeat |
| `succeeded` / `failed` | `pending` | `ensure_pending_rwb_job` (resubmit) | terminal status only |
| **`pending`** | **`cancelled`** *(new)* | **`cancel_rwb_job`** | **`WHERE status_code = 'pending'`** |
| **`failed`** | **`cancelled`** *(new, CR-04a extension)* | **`cancel_rwb_job`** | **`WHERE status_code = 'failed'`** |
| **`running` (dead)** | **`cancelled`** *(new, CR-04a extension)* | **`cancel_rwb_job`** | **`WHERE status_code = 'running' AND (heartbeat missing OR older than settings.rwb_heartbeat_stale_secs)`** |

`cancelled` is terminal — nothing transitions out of it. It is not in the resubmit path: `ensure_pending_rwb_job`'s existing logic only revives `succeeded`/`failed` rows (confirmed by reading the function — see `research.md#R5`), so a `failed` row cancelled this way is simply no longer eligible for resubmit — cancelling and resubmitting a `failed` row are now mutually exclusive next steps, not sequential ones.

`cancel_rwb_job` is one guarded `UPDATE` covering all three transitions above, not three separate statements — its `WHERE` ORs the three guards together. Each still resolves races by rowcount exactly like the existing `pending → running` guard (`claim_rwb_job`): whichever of `claim_rwb_job`/`cancel_rwb_job` runs first against a `pending` row wins, and whichever of `reconcile_stale_rwb_jobs`/`cancel_rwb_job` runs first against a dead `running` row wins (the loser's `UPDATE` matches zero rows — a no-op, never an error). No new locking, no new coordination.

**Dead-job detection is computed at read time, not stored.** `list_rwb_jobs_for_monitoring` LEFT JOINs `rwb_job_heartbeat` and derives `is_dead` per row using the same staleness threshold (`settings.rwb_heartbeat_stale_secs`) `reconcile_stale_rwb_jobs` already uses — this feature adds no new column and no new kind-table value; a dead row's `status_code` reads `running` right up until something (this feature's Cancel, or the poller's reconciler) changes it.

## Entities (from spec.md)

- **Job (`rwb_job`)** — one unit of background work of a specific job type. Already exists; this feature adds one new terminal status and one new service function operating on it. No new fields.
- **Job type (`rwb_job_type`, an existing kind table)** — unchanged by this feature. The queue each job type's actor is bound to is a Dramatiq-side property (`queue_name`), not a database column — `rwb_job_type` already identifies the job type; `queue_name` is derived from it 1:1 at the code level (`rwb_actor`), not stored.
