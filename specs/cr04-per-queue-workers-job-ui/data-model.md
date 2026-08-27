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

`cancelled` is terminal — nothing transitions out of it. It is not in the resubmit path: `ensure_pending_rwb_job`'s existing logic only revives `succeeded`/`failed` rows (confirmed by reading the function — see `research.md#R5`), so this feature does not need to decide whether a cancelled job can be resubmitted; by construction, it cannot, unless that function is separately extended (out of scope here, not requested).

The `pending → cancelled` guard is the same shape as the existing `pending → running` guard (`claim_rwb_job`): both are a single `UPDATE ... WHERE status_code = 'pending'`, both resolve races by rowcount. Whichever of `claim_rwb_job` or `cancel_rwb_job` runs first against a given row wins; the other's `UPDATE` matches zero rows and is a no-op. No new locking, no new coordination.

## Entities (from spec.md)

- **Job (`rwb_job`)** — one unit of background work of a specific job type. Already exists; this feature adds one new terminal status and one new service function operating on it. No new fields.
- **Job type (`rwb_job_type`, an existing kind table)** — unchanged by this feature. The queue each job type's actor is bound to is a Dramatiq-side property (`queue_name`), not a database column — `rwb_job_type` already identifies the job type; `queue_name` is derived from it 1:1 at the code level (`rwb_actor`), not stored.
