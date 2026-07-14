# Execution Flow — Assemble & Sync a Package (US3)

The headline action. The analyst assembles a package (EDMs + RDMs) on the submission
detail, **Saves** it (persists the shell + members, submits nothing), then **Save & Syncs**
(records the pending work and returns). All the Risk Modeler work is carried off-request by
the worker and poller, and the **EDM→RDM chaining is poller-mediated**: each EDM that
finishes importing triggers one `upload_rdm` head that fans out to one apply per RDM of
*that* EDM. For **N EDMs × M RDMs** that is **N heads → N×M applies**.

Code: `package_sync_service.save_package` / `save_and_sync` →
`package_jobs._upload_edm_body` / `_upload_rdm_body` →
`poller.run._handle_import_edm_terminal` (the chain link) → `_handle_import_rdm_terminal`.

**Two request-path operations, both non-blocking (FR-042):**

- **Save** — `INSERT package` + one member entity per EDM/RDM (each `pending_import`) +
  per-member collision **search** + `INSERT submission_package`. **No `rwb_job`, no RM
  submit.**
- **Save & Sync** — one `upload_edm` `rwb_job` per pending EDM (idempotent). Returns
  immediately. Everything after is worker + poller.

---

## Phase A — Save (synchronous, no jobs)

### Records written

| # | Table | Row / change | Written by |
|---|---|---|---|
| 1 | `package` | INSERT (create) — or UPDATE `name` with optimistic-concurrency on `updated_at` (edit) | `save_package` |
| 2 | `irp_edm` / `irp_rdm` | INSERT one per member — `status='pending_import'`, `package_id` set | `save_package` |
| 3 | `submission_package` | INSERT `(submission_id, package_id)` — idempotent | `attach_to_submission` |

```mermaid
sequenceDiagram
    actor User
    participant App as App (route)
    participant DB as WORKBENCH DB
    participant RM as Risk Modeler

    rect rgb(238,244,255)
        Note over User,RM: SAVE — synchronous. Persists structure, submits NOTHING
        User->>App: POST /submissions/{id}/packages (name, members[])
        App->>App: validate_selection(path) for each member
        Note over App,DB: one transaction
        App->>DB: INSERT package
        loop each member
            App->>DB: INSERT irp_edm / irp_rdm (pending_import, package_id)
        end
        App->>DB: SELECT member_count ≥ 1 (else EmptyPackageError)
        loop each member (outside txn)
            App->>RM: search_edms / search_rdms (collision — READ, non-blocking)
            RM-->>App: colliding names (warnings)
        end
        App->>DB: INSERT submission_package (attach, idempotent)
        App-->>User: 200 — package card (all chips: pending_import)
    end
```

---

## Phase B — Save & Sync + the chaining (async)

This is the core. Follow **one EDM** through; the counts in the notes show the fan-out.

### Records written (per EDM, then per pair)

| # | Table | Row / change | Written by | Process |
|---|---|---|---|---|
| 1 | `rwb_job` | INSERT/ensure — `upload_edm`, `pending`, `requestor_id=edm.id` (one per pending EDM) | `ensure_pending_rwb_job` | 🟦 request |
| 2 | `rwb_job` | claim `pending→running` + heartbeat | worker | 🟩 worker |
| 3 | `irp_job` | INSERT — `import_edm`, `QUEUED`, `irp_id` | `record_submitted_irp_job` | 🟩 worker |
| 4 | `irp_edm` | UPDATE `pending_import→importing` | `mark_importing` | 🟩 worker |
| 5 | `rwb_job` | UPDATE `running→succeeded` | `complete_rwb_job` | 🟩 worker |
| 6 | `irp_job` | UPDATE terminal (FINISHED) — *in the same txn as ↓* | `update_tracking` | 🟪 poller |
| 7 | `irp_edm` | UPDATE `importing→ready` + backfill `irp_id` | `backfill_on_terminal` | 🟪 poller |
| 8 | **`rwb_job`** | **INSERT — `upload_rdm` head, `requestor_type='irp_job'`, `requestor_id=<this import_edm irp_job.id>`, `input={rdm_ids:[all package RDMs], edm_ids:[this EDM]}`** — *the chain link, same txn* | `enqueue_rwb_job(conn=…)` | 🟪 poller |
| 9 | `rwb_job` | claim the head `pending→running` + heartbeat | worker | 🟩 worker |
| 10 | `irp_job` | INSERT one per (RDM, this EDM) pair — `import_rdm`, `QUEUED` | `record_submitted_irp_job` | 🟩 worker |
| 11 | `irp_rdm` | UPDATE `pending_import→importing` | `mark_importing` | 🟩 worker |
| 12 | `irp_rdm` | UPDATE **rollup** `→ready` once every apply across every EDM is FINISHED | `rollup_on_terminal` | 🟪 poller |

```mermaid
sequenceDiagram
    actor User
    participant App as App (route)
    participant DB as WORKBENCH DB
    participant RM as Risk Modeler
    participant W as Worker (Dramatiq)
    participant P as Poller

    rect rgb(238,244,255)
        Note over User,DB: SAVE & SYNC — record pending work, return immediately
        User->>App: POST /packages/{id}/sync
        loop each EDM not already ready/importing
            App->>DB: ensure_pending rwb_job (upload_edm, requestor_id = edm.id)
            App-->>W: dispatch(upload_edm)
        end
        Note over App: RDM-only package ⇒ one upload_rdm head (requestor_id = package_id)
        App-->>User: 200 — package card (queued)
    end

    rect rgb(238,255,244)
        Note over W,RM: WORKER — submit each EDM import (fans out across N EDMs)
        W->>DB: claim rwb_job (upload_edm) + heartbeat
        W->>RM: submit_edm_import (HEAVY)
        RM-->>W: irp_id + resourceUri
        W->>DB: INSERT irp_job (import_edm, QUEUED) + resource
        W->>DB: UPDATE irp_edm (→importing)
        W->>DB: UPDATE rwb_job (→succeeded)
    end

    rect rgb(245,238,255)
        Note over P,RM: POLLER — the EDM finish is the CHAIN LINK
        loop each pass
            P->>RM: get_import_job(import_edm.irp_id)
            RM-->>P: FINISHED
            Note over P,DB: ONE transaction — terminal + backfill + chained head are atomic
            P->>DB: UPDATE irp_job (import_edm → FINISHED)
            P->>DB: UPDATE irp_edm (importing→ready, irp_id)
            P->>DB: INSERT rwb_job (upload_rdm head, requestor_type='irp_job', requestor_id = this irp_job.id)
            P-->>W: dispatch(upload_rdm)
        end
        Note over P: keyed on the finished irp_job.id ⇒ exactly ONE head per EDM,<br/>idempotent if the poller re-processes (enqueue dedups, never revives)
    end

    rect rgb(238,255,244)
        Note over W,RM: WORKER — the head fans out to one apply per (RDM × this EDM)
        W->>DB: claim rwb_job (upload_rdm head) + heartbeat
        loop each RDM of the package
            W->>DB: _apply_exists (RDM, this EDM)? skip if so
            W->>RM: submit_rdm_import (rdm, edm resolved by name)
            RM-->>W: irp_id
            W->>DB: INSERT irp_job (import_rdm, QUEUED, irp_edm_id = this EDM)
            W->>DB: UPDATE irp_rdm (→importing)
        end
        W->>DB: UPDATE rwb_job (→succeeded)
    end

    rect rgb(245,238,255)
        Note over P,RM: POLLER — combined rollup per RDM across ALL its applies
        loop each pass
            P->>RM: get_import_job(import_rdm.irp_id)
            RM-->>P: FINISHED
            P->>DB: UPDATE irp_job (import_rdm → FINISHED)
            P->>DB: SELECT COUNT applies for this RDM still non-terminal
            alt none remain AND none failed
                P->>DB: UPDATE irp_rdm (→ready)
            else more applies in flight (other EDMs not done)
                Note over P: RDM stays importing
            end
        end
    end
```

**Fan-out summary:** 1 Save & Sync click → **N** `upload_edm` `rwb_job`s → (poller, one per
EDM finish) **N** `upload_rdm` heads → (worker) **N × M** `import_rdm` applies → (poller)
**M** RDM rollups, each promoted only once its applies across *all* N EDMs are `FINISHED`.
The N EDMs proceed **independently** — there is no barrier waiting for all EDMs before the
first RDM applies start; each EDM's completion drives its own head.

---

**Boundaries worth noting**

- **Save vs. Save & Sync is the deliberate submit boundary.** Save is pure structure —
  the package can sit half-built indefinitely with every member `pending_import` and *zero*
  jobs. Nothing reaches Risk Modeler until Sync.
- **The chain link is one atomic poller transaction** (diagram steps 6–8): "mark the
  `import_edm` FINISHED", "flip the EDM to `ready`", and "enqueue the `upload_rdm` head" all
  commit together. A crash leaves either the whole step done or none of it — on retry
  `update_tracking` is an in-place no-op and `enqueue_rwb_job` dedups.
- **The head is keyed on the finished `import_edm` `irp_job.id`, not the package.** That is
  what makes N EDMs each spawn exactly one fan-out head, and makes re-processing idempotent
  (the unique key `(requestor_type, requestor_id, rwb_job_type)` collapses a duplicate).
- **`enqueue` (poller, mechanical) never revives a terminal row; `ensure_pending` (request
  path, human) does.** Re-clicking Sync resets a *failed* member's head to `pending` and
  skips ready/in-flight ones — a mechanical re-poll can never do that.
- **Two RDM-attach shapes exist.** The normal path applies RDMs when their EDM finishes
  (poller). The `save_and_sync` request path also has a direct branch: if an EDM is
  *already* `ready` (e.g. RDMs added after the fact), it enqueues the `upload_rdm` apply
  immediately rather than waiting for a finish that already happened.
- **A review-only package (RDMs, no EDMs)** skips the EDM phase entirely: Save & Sync
  enqueues a single `upload_rdm` head keyed on the package id, whose applies carry
  `irp_edm_id = NULL`.
