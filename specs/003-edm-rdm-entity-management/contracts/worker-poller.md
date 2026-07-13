# Contract — Poller, Workers & the A21 Chaining Mechanism (Iteration 2)

The out-of-process half of the iteration, where every Risk Modeler call actually happens (Article 11). This contract makes the **A21 resolution** (spec Clarifications; DATA_MODEL §8 → "Package sync/delete chaining") concrete: what each `rwb_job` worker does, how the poller bridges the `irp_job`→`rwb_job` boundary, and how fan-in stays idempotent.

**Process boundaries (Article 11 / Article 10):**
- **Poller** — `app/poller/run.py`, standalone; never imported by a route handler. Runs one `poll_once` pass every `POLL_INTERVAL_SECS` (default ~15 s, SC-001). Single-status-check `get_*_job` only; `poll_*_to_completion` forbidden.
- **Dramatiq worker** — `app/workers/`; consumes `rwb_job` rows; performs every Risk Modeler submit + the synchronous RDM delete + notifications. Single worker by default.
- **Web layer** — enqueues `rwb_job` rows and returns; calls no `get_*`/result method.
- All three reach Risk Modeler only via `app/services/irp_gateway.py` (fake in CI).

---

## 1. `rwb_job` worker lifecycle (every actor, Article 10)

```
receive Dramatiq message for rwb_job_id
  → claim: UPDATE rwb_job SET status_code='running', claimed_by=:wid
           WHERE id=:id AND status_code='pending'      # rowcount 0 → exit (already claimed)
  → start heartbeat daemon thread (upsert rwb_job_heartbeat every RWB_HEARTBEAT_INTERVAL_SECS)
  → run the type-specific body (below)
  → on success: complete_rwb_job(succeeded) + idempotently enqueue chained tail row(s)
  → on failure: complete_rwb_job(failed, error_detail); idempotently enqueue a member-failure notify_analyst (keyed to this member)
  → stop heartbeat
```
Stale `running` rows (heartbeat older than `RWB_HEARTBEAT_STALE_SECS`) are reset to `pending` by the poller's reconciler and re-dispatched. Every chained enqueue is idempotent on `UNIQUE(requestor_type, requestor_id, rwb_job_type)`.

---

## 2. Worker bodies (the `rwb_job_type` table)

| `rwb_job_type` | Body (this iteration) | On success, chains to |
|---|---|---|
| `upload_edm` | `irp_gateway.submit_edm_import(name, source_file_path)`; write `irp_job(import_edm, QUEUED, irp_id)` + `irp_job_resource(resource_uri)`; the worker's unit of work is the **submit**, not the remote finish | *(poller-mediated)* `upload_rdm` — enqueued by the poller when this `import_edm` reaches `FINISHED` |
| `upload_rdm` | for each RDM in the package, `submit_rdm_import(name, source_file_path, edm_name=<the just-finished EDM>)` (name-resolved via `search_edms`, Article 2); write one `irp_job(import_rdm, …)` per apply; review-only → single apply, no EDM | *(poller-mediated, Iteration 6)* `retrieve_analysis_results` on `import_rdm` FINISHED |
| `delete_rdm` | **SYNCHRONOUS**: `irp_gateway.delete_rdm_analyses(rdm_name)` (deletes the RDM's analysis entities inline; **no `irp_job`**); set `irp_rdm.status='deleted'` only once the delete returns | *(app-side fan-in)* `delete_edm` — when **all** the package's RDM removals have succeeded (§3) |
| `delete_edm` | under the atomic guard `UPDATE irp_edm SET status='delete_pending' WHERE id=:e AND status NOT IN ('delete_pending','deleted')` (rowcount 0 → already handled), `submit_delete_edm(edm_irp_id)`; write `irp_job(delete_edm, QUEUED, irp_id)` | *(poller-mediated)* package finalize — when this `delete_edm` reaches `FINISHED` and no live members remain |
| `notify_analyst` | `notification_service.notify(...)` — dispatch an **action-completion** message (a standalone import / package sync / package delete whose member set is fully terminal) **or** a **member-failure** message, on the configured channel(s) (R10); never one per successful member (FR-030) | — |

> On submit failure (never reached Risk Modeler) the worker writes `irp_job.status='SUBMISSION FAILED'`, `irp_id=null` — retried by the poller's `submission_retry` batch (§4), distinct from an RM-side `FAILED` (FR-029).

---

## 3. Poller pass (`poll_once`) — bridges the async boundary

```
# A. Track in-flight IRP jobs (single-status-check, batched by type)
non_terminal = SELECT irp_job WHERE status NOT IN (FINISHED,FAILED,CANCELED,SUBMISSION FAILED)
group by irp_job_type:
  for each job: st = irp_gateway.get_<type>_job(irp_id)      # NEVER poll_*_to_completion
    UPDATE irp_job SET status=st, last_tracked_at=now (+ completed_at/last_completion_result if terminal)
    on terminal:
      backfill entity irp_id + flip irp_edm/irp_rdm.status (ready on FINISHED, error otherwise)
      if FINISHED: idempotently enqueue the dependent head rwb_job
        (requestor_type='irp_job', requestor_id=<this irp_job.id>):
          import_edm  FINISHED → one upload_rdm      (fans out to an apply per RDM)
          import_rdm  FINISHED → (Iteration 6) retrieve_analysis_results; set irp_rdm.status rollup
          delete_edm  FINISHED → package finalize     (§3 fan-in tail)
      # NOTIFICATIONS — per-action + per-failure, NEVER per successful member (FR-030 / Q1 2026-07-13):
      if terminal is a failure (FAILED, or SUBMISSION FAILED after retries exhausted):
        idempotently enqueue a member-failure notify_analyst (requestor_type='irp_job', requestor_id=<this job>)
      action-completion fan-in: NOT EXISTS a non-terminal sibling in this analyst action
        (package sync, grouped by the originating analyst request); if satisfied,
        idempotently enqueue ONE action-completion notify_analyst
        (requestor_type='analyst_request', requestor_id=<action anchor: the package for a sync/delete;
         a STANDALONE import is anchored on the imported entity's own id — per-import this iteration,
         one notification per entity; multi-file batch grouping is deferred, no batch id persisted>)

# B. RDM→EDM fan-in is NOT here — it is app-side (delete_rdm worker on success), because
#    RDM delete has no irp_job to observe (R6). The poller only bridges async ops.

# C. Reconciler: reset stale 'running' rwb_job rows (heartbeat < now - STALE) to 'pending'
# D. submission_retry batch (§4)
```

**Fan-in (idempotent, never counted):**
- **RDM → EDM** (app-side, in the `delete_rdm` worker): on success run
  `NOT EXISTS (SELECT 1 FROM irp_rdm WHERE package_id=:p AND status <> 'deleted')`; if satisfied, idempotently enqueue the `delete_edm` head rows.
- **Package soft-delete** (poller-mediated, on `delete_edm` FINISHED): idempotent
  `UPDATE package SET deleted_at=now WHERE id=:p AND deleted_at IS NULL AND NOT EXISTS (live members)`; soft-delete the members too (FR-021). On that transition, idempotently enqueue the **delete action-completion** `notify_analyst` (requestor_type='analyst_request', requestor_id=the package).
- **Sync rollup**: `irp_rdm.status='ready'` once all its `import_rdm` applies are FINISHED.

A re-poll, worker redelivery, or reconciler re-enqueue cannot double-submit or advance a fan-in early (FR-043 / SC-014).

---

## 4. `submission_retry` (single-threaded batch, not a Dramatiq actor)

`SELECT irp_job WHERE status='SUBMISSION FAILED' AND submission_attempt_count < IRP_SUBMISSION_MAX_RETRIES` (a deployment config value with **no fixed default**, FR-029), re-attempt the submit with backoff, increment `submission_attempt_count`. When the count reaches the configured max the row stays terminal `SUBMISSION FAILED` (parked) for analyst-driven recovery and enqueues a member-failure `notify_analyst`. Runs in/alongside the poller loop (Article 11 — submission retry is a batch, CR-002). It retries **submit-side** failures only; RM-side `FAILED` is an analyst-driven recovery (replace file / retry / re-sync, FR-044–FR-046).

---

## 5. The auto-fire vs click-gated line (Article 5, explicit per op)

| Step | Trigger | Kind |
|---|---|---|
| Import a file / assemble a package / choose Save-and-Sync / choose Delete | analyst click | **judgment — waits for a click** |
| `import_edm` FINISHED → `upload_rdm` | poller (mechanical) | **auto-fires** |
| all RDM removals done → `delete_edm` | `delete_rdm` worker (mechanical) | **auto-fires** |
| `delete_edm` FINISHED → package soft-delete | poller (mechanical) | **auto-fires** |
| member op `FAILED` / exhausted `SUBMISSION FAILED` → member-failure `notify_analyst` | poller / worker (mechanical) | **auto-fires** |
| analyst action fully terminal (standalone import / sync / delete) → action-completion `notify_analyst` | poller (mechanical, idempotent fan-in) | **auto-fires** |
| `SUBMISSION FAILED` → re-submit | `submission_retry` batch | **auto-fires** |

Everything downstream of one analyst intent is a direct mechanical consequence and auto-fires; the intents themselves always wait for a click.

---

## 6. Stub-first build (FR-048)

The package UI MAY be built first against short **heartbeat stubs**: the `rwb_job_type`s and the whole chaining/fan-in shape are identical; only the worker *body* differs (a stub sleeps/heartbeats and marks succeeded instead of calling the gateway). Wiring real Risk Modeler is a change to the worker body + the `irp_gateway` implementation alone — no orchestration change (research R1).

---

## 7. Test obligations (Article 12)

Unit tier (SQLite + **fake IRP** implementing `irp_gateway`):
- **Claim/heartbeat/reconciler** (Article 10 mandate): atomic claim rowcount 1→0; heartbeat upsert; reconciler reclaims a stale `running` row (`test_rwb_job_queue`).
- **Prerequisite gate / chaining** (Article 2 mandate): `import_edm` FINISHED → exactly one `upload_rdm`; fan-out to one apply per RDM (`test_job_chaining`).
- **Fan-in idempotency**: `delete_edm` enqueued only when all RDMs `deleted`; a duplicate `delete_rdm` success does not double-enqueue; package soft-delete fires once (`test_delete_ordering`).
- **Delete asymmetry**: `delete_rdm` writes no `irp_job` and completes synchronously; `delete_edm` writes an `irp_job` polled to FINISHED (`test_delete_ordering`).
- **Poller** (`test_poller`): terminal FINISHED backfills `irp_id` + flips entity status + enqueues the dependent head; `SUBMISSION FAILED` ≠ `FAILED`; `submission_retry` respects the configured max (no fixed default) and parks the row when reached.
- **Notification granularity** (`test_notifications`): a multi-member sync where every member succeeds enqueues exactly ONE action-completion `notify_analyst` (not one per member); a failed member enqueues one member-failure `notify_analyst`; a repeated terminal trigger duplicates neither (idempotent on the action / member key) — FR-030 / Q1.

IRP tier (`--run-irp`, opt-in): real submit + single-status `get_*_job` for `import_edm`/`import_rdm`/`delete_edm`; the synchronous `delete_rdm_analyses` call. `poll_*_to_completion` is asserted **absent** from poller code.
