# Execution Flow — Import an RDM (US2)

The analyst imports one results file as an `irp_rdm`, applying it to one or more EDMs. Same
request-path discipline as [import EDM](import_edm.md) — including the **blocking**
name-collision check with its `?nc=unchecked` fail-open — with one shape difference: **one
`rwb_job` fans out in the worker to one `irp_job` apply per (RDM × EDM) pair**, and the
RDM's status is a **combined rollup** — `ready` only once *every* apply is `FINISHED`.

Code: `rdm_service.import_rdm` → one `upload_rdm` `rwb_job` → `package_jobs._upload_rdm_body`
(fan-out) → `poller.run._handle_import_rdm_terminal` → `backfill_rdm_analyses` →
`rdm_service.rollup_on_terminal`.

**Classification:** request path = **sync** (+ one collision **search**); the applies =
**async (worker)**, fanned out; the finishes = **async (poller)**, and the `ready` rollup
is **async (worker)** again — see below.

**Where the rollup happens depends on which way the apply went** — this is the one place
the RDM flow inverts the usual division of labour:

| Apply's terminal status | Who writes `irp_rdm.status` |
|---|---|
| `FINISHED` | the poller enqueues `backfill_rdm_analyses`, and the **worker** runs the rollup after capturing the analyses — so `ready` and the analyses land in one transaction |
| `FAILED` / `CANCELLED` | the **poller**, in place, via `rollup_on_terminal` → `error` |

**At least one applied EDM is required** (`POST /rdms/import` returns 422 without one). The
worker still supports a review-only apply with `irp_edm_id = NULL`, and the rollup treats it
as a set of one — but no UI path reaches it (deferred, 003 D3).

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
| 9a | `rwb_job` | INSERT — `backfill_rdm_analyses`, keyed `('irp_job', <this apply's irp_job.id>)`, input carries `rdm_id` + **`edm_id`** + `apply_irp_id` — *same transaction as 8*, **FINISHED only** | `enqueue_rwb_job` | 🟪 poller |
| 9b | `irp_rdm` | UPDATE — `→ error` (FAILED/CANCELLED path only) | `rollup_on_terminal` | 🟪 poller |
| 10 | `irp_rdm` | UPDATE — **the `ready` rollup**: only when NO `import_rdm` apply is non-terminal AND none failed; backfills `irp_id` + `created_by_irp_job_irp_id` *only when currently NULL* | `rollup_on_terminal` | 🟩 worker |

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
        App->>App: validate_selection(path) — 422 if no EDM selected
        App->>RM: search_rdms(name) — collision check (READ, cached)
        RM-->>App: colliding names
        alt name collides
            App-->>User: 422 — form re-renders, NOTHING written
        else check unavailable
            Note over App: fail open — save, then ?nc=unchecked banner
        end
        App->>DB: INSERT irp_rdm (pending_import)
        App->>DB: INSERT rwb_job (upload_rdm, pending) — input carries rdm_id + edm_ids
        App-->>W: dispatch(upload_rdm)
        App-->>User: 200 — RDM card (chip: pending_import)
    end

    rect rgb(238,255,244)
        Note over W,RM: WORKER — ONE rwb_job fans out to one apply per (RDM × EDM) pair
        W->>DB: UPDATE rwb_job (pending→running) + heartbeat
        W->>DB: SELECT irp_rdm
        loop for each applied EDM
            W->>DB: SELECT irp_job — _apply_exists (RDM,EDM)? skip if so
            W->>RM: submit_rdm_import (rdm name, edm resolved BY NAME, HEAVY S3 upload inside)
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
        Note over P,RM: POLLER — tracks EACH apply. It does NOT write the ready status
        loop each pass — one status check per non-terminal apply
            P->>DB: SELECT irp_job (non-terminal import_rdm applies)
            P->>RM: get_import_job(irp_id)
            RM-->>P: status
            Note over P,DB: one transaction per apply
            P->>DB: UPDATE irp_job (status mirror, last_tracked_at)
            alt this apply FINISHED
                P->>DB: INSERT rwb_job (backfill_rdm_analyses — rdm_id + edm_id + apply_irp_id)
                P-->>W: dispatch(backfill_rdm_analyses)
                Note over P: the RDM stays importing — the WORKER promotes it
            else this apply FAILED / CANCELLED
                P->>DB: UPDATE irp_rdm (→error)
            end
        end
        P->>DB: reconcile_stale_rwb_jobs
    end

    rect rgb(238,255,244)
        Note over W,DB: WORKER — captures the analyses, THEN rolls the RDM up
        W->>DB: claim rwb_job (backfill_rdm_analyses) + heartbeat
        W->>RM: search_analyses + per-analysis metadata (all reads, pre-transaction)
        Note over W,DB: ONE transaction — analyses + rollup + as_of
        W->>DB: prune + INSERT irp_analysis rows for this (RDM, EDM) pair
        W->>DB: SELECT COUNT applies for this RDM still non-terminal / failed
        alt none remain AND none failed
            W->>DB: UPDATE irp_rdm (→ready, backfill irp_id if NULL)
        else more applies in flight, or one failed
            Note over W: →error if any failed, otherwise no status write at all
        end
        W->>DB: UPDATE irp_rdm (as_of) + UPDATE rwb_job (→succeeded)
    end
```

The worker half is only sketched here — see
[backfill the RDM's analyses](../backfill/backfill_rdm_analyses.md) for the full flow,
including the metadata-failure branches and the portfolio-linkage rule.

---

**Boundaries worth noting**

- **One `rwb_job`, M `irp_job` rows.** The fan-out lives *inside* the worker body, not in
  the enqueue. That keeps the unit of app-side work coarse (one claim, one heartbeat, one
  complete) while each RM apply is tracked independently.
- **The RDM's status is a rollup, not a mirror.** No single apply's terminal status sets
  the RDM `ready`; the rollup is re-derived from *all* of that RDM's applies each time one
  finishes, and only promotes the RDM when the whole set is `FINISHED`. One failed apply
  makes the RDM `error`. Because the rollup is a pure re-derivation, running it again is
  harmless — which is what lets a manual Sync reuse it.
- **`ready` is deferred one extra hop, deliberately.** The poller *could* promote the RDM
  the moment an apply finishes, but then the RDM would read `ready` with no analyses under
  it. Enqueueing `backfill_rdm_analyses` and letting the worker promote it inside the same
  transaction as the analysis capture means the analyst never sees that empty window.
- **The EDM is resolved by *name* at submit** (Article 2 — name-first resolution), inside
  the worker, so the apply doesn't depend on the EDM's `irp_id` being backfilled yet.
- **The submit is HEAVY, same as the EDM's**, and for the same reason — the file bytes go
  to S3 *inside* the synchronous library call, after it resolves the target EDM's resource
  URI by name. An RDM cannot be applied to an EDM that has not *finished* importing, which
  is exactly why the package flow chains on the EDM's terminal status rather than its
  submit.
- **This is the standalone-import flow.** When the RDM belongs to a package being synced,
  the `upload_rdm` head is not enqueued here on the request path — it is enqueued by the
  **poller** once the EDM finishes importing (see
  [save & sync](../packages/save_and_sync_package.md)).
