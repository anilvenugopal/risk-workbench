# Iteration 2 — Execution Flows (metamodel-full)

Sequence diagrams for the **user actions built in spec `003-edm-rdm-entity-management`**
(EDM & RDM entity management incl. packages).

These are a **third altitude**, distinct from the sibling folders:

- `../granular/` and `../composite/` are **deliberately metamodel-free** — they show
  only the real interactions with Risk Modeler / S3, *not* where the workbench writes
  its own rows. That was intentional (read the interactions objectively, then decide
  where the metamodel goes).
- **This folder is the opposite: metamodel-full.** The metamodel decisions are made
  (constitution v3.0.0, Articles 10 & 11), so these flows show exactly **what rows get
  written, when, and by which process** — the request path, the Dramatiq worker, and
  the poller — and where the **sync → async** hand-offs are.

Read these to answer: *"When the analyst clicks this, what lands in the database, what
runs on the request vs. off it, and how does the worker/poller carry it the rest of the
way?"*

---

## The two metamodel tables (the spine of every flow)

| Table | Article | One row = | Status vocabulary | Written by |
|---|---|---|---|---|
| **`rwb_job`** | 10 | one unit of **app-side** work (the queue of record) | `pending → running → succeeded`/`failed` | request path + poller (enqueue); worker (claim/complete) |
| **`irp_job`** | 11 | one **in-flight Risk Modeler op** | `QUEUED / RUNNING / FINISHED / FAILED / CANCELED` + app-local `SUBMISSION FAILED` | worker (at submit); poller (mirror in place) |

Supporting rows the flows touch:

| Table | Role |
|---|---|
| `irp_edm`, `irp_rdm` | the entities. Plain-string status lifecycle (Article 3 carve-out): `pending_import → importing → ready`/`error → delete_pending → deleted`. Carry `package_id`, backfilled `irp_id` |
| `package`, `submission_package` | the package shell and its M:N attachment to a submission (soft-delete via `deleted_at`) |
| `irp_job_resource` | the `resourceUri` captured at submit time (the completion response omits it — R1) |
| `rwb_job_heartbeat` | one row per running job; the poller's reconciler reads it to reclaim dead-worker rows |

## The one boundary everything turns on

A Risk Modeler op takes **minutes**, and `poll_*_to_completion` is **forbidden**
(Article 11) because it would block a worker for that whole time. So **no single
process both starts an RM op and sees it finish**:

- the **request path** only ever does fast, synchronous things (validate, a lightweight
  RM *search*, insert the `pending` rows) and returns;
- the **worker** does the fast half of the async op — the *submit* — records an
  `irp_job(QUEUED)`, and exits;
- the **poller** does one **single-status check** per pass, mirrors the status, and on a
  *terminal* status backfills the entity and enqueues the next `rwb_job` — atomically.

## The three processes (participants)

| Participant | Process | Colour in diagrams |
|---|---|---|
| **App (route)** | FastAPI request handler — synchronous, on the HTTP request | 🟦 `rgb(238,244,255)` |
| **Worker** | Dramatiq actor — off-request; claims a `rwb_job`, does the submit | 🟩 `rgb(238,255,244)` |
| **Poller** | standalone process — off-request; one pass every `POLL_INTERVAL_SECS` | 🟪 `rgb(245,238,255)` |
| **WORKBENCH DB** | the `rwb_workbench` database (all metamodel writes) | — |
| **Risk Modeler** | RM REST via `irp-integration`, in-process to whoever calls it | — |

**How a worker gets kicked off** (three mechanisms, only the first is load-bearing):

1. the `pending` `rwb_job` row **is** the truth — a worker will claim it eventually;
2. `dispatch()` sends a Dramatiq message so an idle worker grabs it **now** (a latency
   optimisation, behind an injection seam; unset in the unit tier so tests need no Redis);
3. the poller's **reconciler** reclaims rows a dead worker abandoned.

A missed dispatch is therefore never a correctness problem — the reconciler recovers it.

---

## Flows

| Flow | User action (spec) | Request-path writes | Worker | Poller | Chaining |
|---|---|---|---|---|---|
| [Import an EDM](import_edm.md) | US1 | `irp_edm`(pending) + `rwb_job`(upload_edm) | submit → `irp_job`(import_edm) | mirror → `irp_edm` ready/error | none (single entity) |
| [Import an RDM](import_rdm.md) | US2 | `irp_rdm`(pending) + `rwb_job`(upload_rdm) | fan-out → 1 `irp_job`(import_rdm) **per (RDM×EDM) pair** | combined rollup → `irp_rdm` ready/error | 1 head → M applies |
| [Save & sync a package](save_and_sync_package.md) | US3 | `package` + member entities; then N `rwb_job`(upload_edm) | submit each EDM; then apply each RDM | on each EDM FINISHED, **enqueue an `upload_rdm` head** → then rollup | **N EDMs → N heads → N×M applies** |
| [Delete a package](delete_package.md) | US4 | N `rwb_job`(delete_rdm) *or* delete_edm | RDM delete **synchronous (no `irp_job`)**; EDM delete async | on delete_edm FINISHED, mark deleted + finalize | RDM→EDM fan-in; idempotent finalize |
| [Package cards on the submission](package_cards.md) | US5 | **none — read-only** | — | — | — (surfaces the progress the others drive) |

**Conventions in the mermaid**

- Coloured `rect` blocks mark which **process** owns the steps (legend above).
- `INSERT` / `UPDATE` arrows to `WORKBENCH DB` are the **actual metamodel writes**, in order.
- A `loop each pass` around poller steps = the interval loop; inside it is **one**
  single-status check (never a blocking poll-to-completion).
- `alt` blocks show the terminal branches (`FINISHED` vs `FAILED`/`CANCELED`) and the two
  failure modes (`SUBMISSION FAILED` at submit vs. an RM-side failure later).
