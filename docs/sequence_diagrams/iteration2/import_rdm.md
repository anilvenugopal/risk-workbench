# Execution Flow — Import an RDM (US2)

The analyst imports one results file as an `irp_rdm`, optionally applying it to one or more
EDMs. Same request-path discipline as [import EDM](import_edm.md), with one shape
difference: **one `rwb_job` fans out in the worker to one `irp_job` apply per (RDM × EDM)
pair**, and the poller's status write is a **combined rollup** — the RDM is `ready` only
once *every* apply is `FINISHED`.

Code: `rdm_service.import_rdm` → one `upload_rdm` `rwb_job` → `package_jobs._upload_rdm_body`
(fan-out) → `poller.run._handle_import_rdm_terminal` → `rdm_service.rollup_on_terminal`.

**Classification:** request path = **sync** (+ one collision **search**); the applies =
**async (worker)**, fanned out; the finishes = **async (poller)**, rolled up. With no
applied EDMs it degrades to a single **review-only** apply (FR-002/FR-016).

## Records written (in order)

| # | Table | Row / change | Written by | Process |
|---|---|---|---|---|
| 1 | `irp_rdm` | INSERT — `status='pending_import'`, `irp_id=NULL` | `import_rdm` | 🟦 request |
| 2 | `rwb_job` | INSERT — `rwb_job_type='upload_rdm'`, `pending`, `requestor_id=irp_rdm.id`, `input={rdm_ids:[rdm], edm_ids:[…], package_id}` | `enqueue_rwb_job` | 🟦 request |
| 3 | `rwb_job` | UPDATE — `pending → running` (atomic claim) + heartbeat upsert | `claim_rwb_job` | 🟩 worker |
| 4 | `irp_job` | INSERT **× one per (RDM, EDM) pair** — `import_rdm`, `QUEUED`, `irp_id`, `irp_edm_id` set (NULL for review-only) | `record_submitted_irp_job` | 🟩 worker |
| 5 | `irp_job_resource` | INSERT — one per apply (`resourceUri`) | `record_submitted_irp_job` | 🟩 worker |
| 6 | `irp_rdm` | UPDATE — `pending_import → importing` | `mark_importing` | 🟩 worker |
| 7 | `rwb_job` | UPDATE — `running → succeeded` (the whole fan-out is one unit of work) | `complete_rwb_job` | 🟩 worker |
| 8 | `irp_job` | UPDATE — status mirror per apply, every pass | `update_tracking` | 🟪 poller |
| 9 | `irp_rdm` | UPDATE — **rollup**: `→ ready` only when NO `import_rdm` apply is non-terminal AND none failed (backfill `irp_id` from the first finished); `→ error` if any apply FAILED/CANCELED | `rollup_on_terminal` | 🟪 poller |

*Per-pair idempotency:* before each submit the worker checks `_apply_exists` — if an
`import_rdm` row already exists for that `(rdm_id, edm_id)` pair (and is not
`SUBMISSION FAILED`), it is skipped. So a redelivery re-runs the loop safely.

## Sequence

```mermaid
sequenceDiagram
    actor User
    participant App as App (route)
    participant DB as WORKBENCH DB
    participant RM as Risk Modeler
    participant W as Worker (Dramatiq)
    participant P as Poller

    rect rgb(238,244,255)
        Note over User,DB: REQUEST PATH — synchronous
        User->>App: POST /rdms/import (name, path, applied_edm_ids)
        App->>App: validate_selection(path)
        App->>RM: search_rdms(name) — collision check (READ, non-blocking)
        RM-->>App: colliding names (warning only)
        App->>DB: INSERT irp_rdm (pending_import)
        App->>DB: INSERT rwb_job (upload_rdm, pending) — input carries rdm_id + edm_ids
        App-->>W: dispatch(upload_rdm)
        App-->>User: 200 — RDM card (chip: pending_import)
    end

    rect rgb(238,255,244)
        Note over W,RM: WORKER — ONE rwb_job fans out to one apply per (RDM × EDM) pair
        W->>DB: UPDATE rwb_job (pending→running) + heartbeat
        W->>DB: SELECT irp_rdm
        loop for each applied EDM (or once, review-only with edm_id = NULL)
            W->>DB: SELECT irp_job — _apply_exists (RDM,EDM)? skip if so
            W->>RM: submit_rdm_import (rdm name, edm resolved BY NAME)
            alt submit reached RM
                RM-->>W: irp_id + resourceUri
                W->>DB: INSERT irp_job (import_rdm, QUEUED, irp_edm_id)
                W->>DB: INSERT irp_job_resource (resourceUri)
                W->>DB: UPDATE irp_rdm (pending_import→importing)
            else submit never reached RM
                W->>DB: INSERT irp_job (SUBMISSION FAILED, irp_id=NULL)
            end
        end
        W->>DB: UPDATE rwb_job (running→succeeded)
    end

    rect rgb(245,238,255)
        Note over P,RM: POLLER — tracks EACH apply. RDM status is the COMBINED rollup
        loop each pass — one status check per non-terminal apply
            P->>DB: SELECT irp_job (non-terminal import_rdm applies)
            P->>RM: get_import_job(irp_id)
            RM-->>P: status
            Note over P,DB: one transaction per apply
            P->>DB: UPDATE irp_job (status mirror, last_tracked_at)
            alt this apply FINISHED
                P->>DB: SELECT COUNT(*) applies for this RDM still non-terminal
                alt none remain AND none failed
                    P->>DB: UPDATE irp_rdm (→ready, backfill irp_id)
                else some still in flight
                    Note over P: leave irp_rdm importing — not ready yet
                end
            else this apply FAILED / CANCELED
                P->>DB: UPDATE irp_rdm (→error)
            end
        end
        P->>DB: reconcile_stale_rwb_jobs
    end
```

---

**Boundaries worth noting**

- **One `rwb_job`, M `irp_job` rows.** The fan-out lives *inside* the worker body, not in
  the enqueue. That keeps the unit of app-side work coarse (one claim, one heartbeat, one
  complete) while each RM apply is tracked independently.
- **The RDM's status is a rollup, not a mirror.** No single apply's terminal status sets
  the RDM `ready`; the poller re-derives it each time an apply finishes and only promotes
  the RDM when the whole set is `FINISHED`. One failed apply makes the RDM `error`.
- **The EDM is resolved by *name* at submit** (Article 2 — name-first resolution), inside
  the worker, so the apply doesn't depend on the EDM's `irp_id` being backfilled yet.
- **Review-only is the `edm_ids = []` degenerate case** — a single apply with
  `irp_edm_id = NULL`. The rollup logic is identical (a set of one).
- **This is the standalone-import flow.** When the RDM belongs to a package being synced,
  the `upload_rdm` head is not enqueued here on the request path — it is enqueued by the
  **poller** once the EDM finishes importing (see [save & sync](save_and_sync_package.md)).
