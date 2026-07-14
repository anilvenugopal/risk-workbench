# Contract — Poller, Workers & the A21 Chaining Mechanism (Iteration 2)

The out-of-process half of the iteration, where every Risk Modeler call actually happens (Article 11). This contract makes the **A21 resolution** (spec Clarifications; DATA_MODEL §8 → "Package sync/delete chaining") concrete: what each `rwb_job` worker does, how the poller bridges the `irp_job`→`rwb_job` boundary, and how fan-in stays idempotent.

**Process boundaries (Article 11 / Article 10):**
- **Poller** — `app/poller/run.py`, standalone; never imported by a route handler. Runs one `poll_once` pass every `POLL_INTERVAL_SECS` (default ~15 s, SC-001). Single-status-check `get_*_job` only; `poll_*_to_completion` forbidden.
- **Dramatiq worker** — `app/workers/`; consumes `rwb_job` rows; performs every Risk Modeler submit + the synchronous RDM delete + notifications. Single worker by default.
- **Web layer** — enqueues `rwb_job` rows and returns; calls no `get_*`/result method.
- All three reach Risk Modeler only via `app/services/irp_gateway.py` (fake in CI).

---

## IRP gateway — confirmed method surface (`irp-integration` 0.2.0)

Confirmed against the committed PyPI wheel on 2026-07-14. The library is **manager-based** —
`client.edm` / `.rdm` / `.import_job` / `.risk_data_job` / `.analysis` — not the flat names earlier
drafts assumed (`submit_edm_import_job()` at top level, `get_analysis_job()`, `search_rdms()`).
`app/services/irp_gateway.py` is the ONLY module that imports it, wraps exactly these calls, and the
CI fake mirrors this surface. **This table supersedes the provisional list in research R1.**

| Op | Gateway wraps (0.2.0) | Returns | Single-status getter |
|---|---|---|---|
| EDM import | `client.edm.submit_edm_import_job(edm_name, edm_file_path, server_name="databridge-1")` | `(job_id:int, request_body:dict)` | `client.import_job.get_import_job(job_id:int)` → dict |
| RDM import (apply) | `client.rdm.submit_rdm_import_job(rdm_name, edm_name, rdm_file_path)` | `(job_id:int, request_body:dict)` | **same** `get_import_job` |
| EDM delete | `client.edm.submit_delete_edm_job(exposure_id:int)` | `job_id:int` | `client.risk_data_job.get_risk_data_job(job_id:int)` → dict |
| RDM delete | `client.analysis.delete_analysis(analysis_id:int)` per analysis | `None` — **synchronous** | — (no job) |
| Analysis backfill / delete-enumeration | `client.analysis.search_analyses(filter='sourceRdmName="<rdm>" AND exposureName="<edm>"')` | `List[dict]` | — |
| Name collision | `client.edm.search_edms(filter)` · `client.rdm.search_imported_rdms(filter)` | `List[dict]` | — |

- **Getters collapse to two:** `get_import_job` (both `import_edm` and `import_rdm` — one shared
  endpoint) and `get_risk_data_job` (`delete_edm`). There is **no** per-type import getter.
- **Terminal statuses** (`WORKFLOW_COMPLETED_STATUSES`): `FINISHED` (only success), `FAILED`,
  `CANCELLED` (double-L). In-progress: `QUEUED/PENDING/RUNNING/CANCEL_REQUESTED/CANCELLING`.
  `SUBMISSION FAILED` is app-local only (never from IRP).
- **Gateway discipline:** expose ONLY `submit_*` + the single `get_*` + `search_*`/`delete_analysis`.
  NEVER `poll_*_to_completion` (on every manager). NEVER the poll-inside convenience methods
  `edm.delete_edm()`, `rdm.export_analyses_to_rdm()`, `import_job.submit_job()` — they block for minutes.
- **Request body / S3:** import methods return `request_body["resourceUri"]` (store on
  `irp_job_resource`). `submit_edm_import_job` performs folder-create + S3 upload internally using
  **temporary S3 creds from the RM response** — no ambient AWS credentials; the worker host needs S3
  egress only.
- **Config/env:** `IRPClient()` reads `RISK_MODELER_BASE_URL` / `RISK_MODELER_API_KEY` /
  `RISK_MODELER_RESOURCE_GROUP_ID`. `server_name` defaults to `"databridge-1"` (workbench config; only
  EDM import takes it).
- **Deferred (library change, NOT on the Iteration-2 path):** review-only / no-EDM RDM import —
  `submit_rdm_import_job` requires `edm_name` — so **RDM-only packages are follow-up work** (spec 003 D3).

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
| `upload_edm` | `gateway.submit_edm_import_job(edm_name, source_file_path, server_name="databridge-1")` → `(irp_id, request_body)`; write `irp_job(import_edm, QUEUED, irp_id)` + `irp_job_resource(request_body["resourceUri"])`; unit of work is the **submit**, not the remote finish | *(poller-mediated)* `upload_rdm` — enqueued when this `import_edm` reaches `FINISHED` |
| `upload_rdm` | for each RDM in the package, `gateway.submit_rdm_import_job(rdm_name, edm_name=<the just-finished EDM>, source_file_path)` (EDM name-resolved, Article 2); write one `irp_job(import_rdm, …)` per apply. **Every apply has a target EDM — review-only/RDM-only is deferred (D3).** | *(poller-mediated)* `backfill_rdm_analyses` on `import_rdm` FINISHED |
| `backfill_rdm_analyses` | **(new this iteration)** `gateway.search_analyses('sourceRdmName="<rdm>" AND exposureName="<edm>"')`; write this pair's `irp_analysis` rows (Moody's `analysisId` + metadata) for delete-enumeration; roll up `irp_rdm.status='ready'` once all its applies are FINISHED | — *(Iteration 6 adds `retrieve_analysis_results` here)* |
| `delete_rdm` | **SYNCHRONOUS**: read this pair's `irp_analysis` rows and loop `gateway.delete_analysis(analysis_id)` (**no `irp_job`, no polling**); mark those rows deleted + set `irp_rdm.status='deleted'` once the deletes return | *(app-side fan-in)* `delete_edm` — when **all** the package's RDM removals have succeeded (§3) |
| `delete_edm` | under the atomic guard `UPDATE irp_edm SET status='delete_pending' WHERE id=:e AND status NOT IN ('delete_pending','deleted')` (rowcount 0 → already handled), `gateway.submit_delete_edm_job(exposure_id=irp_edm.irp_id)` → `irp_id`; write `irp_job(delete_edm, QUEUED, irp_id)` | *(poller-mediated)* package finalize — when this `delete_edm` reaches `FINISHED` and no live members remain |
| `notify_analyst` | `notification_service.notify(...)` — dispatch an **action-completion** message (a standalone import / package sync / package delete whose member set is fully terminal) **or** a **member-failure** message, on the configured channel(s) (R10); never one per successful member (FR-030) | — |

> On submit failure (never reached Risk Modeler) the worker writes `irp_job.status='SUBMISSION FAILED'`, `irp_id=null` — retried by the poller's `submission_retry` batch (§4), distinct from an RM-side `FAILED` (FR-029).

---

## 3. Poller pass (`poll_once`) — bridges the async boundary

```
# A. Track in-flight IRP jobs (single-status-check, batched by type)
non_terminal = SELECT irp_job WHERE status NOT IN (FINISHED,FAILED,CANCELLED,SUBMISSION FAILED)
group by irp_job_type:
  for each job: st = gateway getter by type:                 # NEVER poll_*_to_completion
                     import_edm / import_rdm → get_import_job(irp_id)
                     delete_edm             → get_risk_data_job(irp_id)
    UPDATE irp_job SET status=st, last_tracked_at=now (+ completed_at/last_completion_result if terminal)
    on terminal:
      backfill entity irp_id, then flip entity status BY TYPE:
          import_edm → irp_edm.status = ready (FINISHED) / error (other terminal)
          import_rdm → irp_rdm.status = error on a non-FINISHED terminal ONLY; on FINISHED do NOT
                       set ready here — backfill_rdm_analyses rolls irp_rdm.status up to ready once ALL applies FINISH (§2)
          delete_edm → no entity flip here (irp_edm already 'delete_pending'; finalize soft-deletes the members)
      if FINISHED: idempotently enqueue the dependent head rwb_job
        (requestor_type='irp_job', requestor_id=<this irp_job.id>):
          import_edm  FINISHED → one upload_rdm      (fans out to an apply per RDM)
          import_rdm  FINISHED → backfill_rdm_analyses (capture irp_analysis rows; the worker does the irp_rdm.status rollup)
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

IRP tier (`--run-irp`, opt-in): real submit + single-status getters (`get_import_job` for `import_edm`/`import_rdm`, `get_risk_data_job` for `delete_edm`); `search_analyses` backfill; the synchronous `delete_analysis` calls. `poll_*_to_completion` and the poll-inside convenience methods (`edm.delete_edm`, `import_job.submit_job`) are asserted **absent** from poller/worker code.
