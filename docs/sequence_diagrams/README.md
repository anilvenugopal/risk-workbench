# Execution Flows

One document per **user action the workbench actually implements today**, at the
implementation/execution altitude — **metamodel-full**. Each flow shows exactly which
`rwb_job` / `irp_job` / entity rows get written, **when**, and **by which process** (the HTTP
request, the Dramatiq worker, or the poller), and where the **sync → async** hand-offs are.

Read these to answer: *"When the analyst clicks this, what lands in the database, what runs on
the request vs. off it, and how does the worker/poller carry it the rest of the way?"*

Design-ahead flows for the parts of the MVP spine that are **not built yet** — analyses, GeoHaz,
grouping, Loss-Repo export, subportfolio breakouts — live in **[`planned/`](planned/README.md)**.
Those are deliberately *metamodel-free* and written at a different altitude; don't read them as
descriptions of running code.

---

## The two metamodel tables (the spine of every flow)

| Table | Article | One row = | Status vocabulary | Written by |
|---|---|---|---|---|
| **`rwb_job`** | 10 | one unit of **app-side** work (the queue of record) | `pending → running → succeeded`/`failed` | request path + poller (enqueue); worker (claim/complete) |
| **`irp_job`** | 11 | one **in-flight Risk Modeler op** | `QUEUED / RUNNING / FINISHED / FAILED / CANCELLED` + app-local `SUBMISSION FAILED` | worker (at submit); poller (mirror in place) |

Supporting rows the flows touch:

| Table | Role |
|---|---|
| `irp_edm`, `irp_rdm` | the entities. Plain-string status lifecycle (Article 3 carve-out): `pending_import → importing → ready`/`error → delete_pending → deleted`. Carry `package_id`, backfilled `irp_id`, and an `as_of` freshness stamp |
| `irp_portfolio`, `irp_treaty`, `irp_analysis` | the **snapshot cache** — RM detail stored verbatim as JSON (`exposure_detail`, `attributes`, `settings_metadata`), overwritten in place, **no status column** |
| `package`, `submission_package` | the package shell and its M:N attachment to a submission (soft-delete via `deleted_at`) |
| `submission`, `submission_status_event` | the deal, and the **only event-sourced status in the app** (Article 4) |
| `irp_job_resource` | the `resourceUri` captured at submit time (the completion response omits it — R1) |
| `rwb_job_heartbeat` | one row per running job; the poller's reconciler reads it to reclaim dead-worker rows |

## The one boundary everything turns on

A Risk Modeler op takes **minutes**, and `poll_*_to_completion` is **forbidden** (Article 11)
because it would block a worker for that whole time. So **no single process both starts an RM op
and sees it finish**:

- the **request path** only ever does fast, synchronous things (validate, a cached RM *search*,
  insert the `pending` rows) and returns;
- the **worker** does the fast half of the async op — the *submit* — records an
  `irp_job(QUEUED)`, and exits. It is also where **every** RM and Data Bridge *read* lives;
- the **poller** does one **single-status check** per pass, mirrors the status, and on a
  *terminal* status backfills the entity and enqueues the next `rwb_job` — atomically.

## The three processes (participants)

| Participant | Process | Colour in diagrams |
|---|---|---|
| **App (route)** | FastAPI request handler — synchronous, on the HTTP request | 🟦 `rgb(238,244,255)` |
| **Worker** | Dramatiq actor — off-request; claims a `rwb_job`, does the submit or the read | 🟩 `rgb(238,255,244)` |
| **Poller** | standalone process — off-request; one pass every `POLL_INTERVAL_SECS` | 🟪 `rgb(245,238,255)` |
| **WORKBENCH DB** | the `rwb_workbench` database (all metamodel writes) | — |
| **Risk Modeler** | RM REST via `irp-integration`, in-process to whoever calls it | — |
| **Data Bridge** | Moody's cloud SQL — **read-only, worker-side only** (Article 11) | — |

**How a worker gets kicked off** (three mechanisms, only the first is load-bearing):

1. the `pending` `rwb_job` row **is** the truth — a worker will claim it eventually;
2. `dispatch()` sends a Dramatiq message so an idle worker grabs it **now** (a latency
   optimisation, behind an injection seam; unset in the unit tier so tests need no Redis);
3. the poller's **reconciler** reclaims rows a dead worker abandoned.

A missed dispatch is therefore never a correctness problem — the reconciler recovers it.

## Facts that hold across every flow

Stated once here so the individual documents don't repeat them:

- **Every workbench write lands in `rwb_workbench`**, via the `WORKBENCH` connection.
  `rwb_exposure` and `rwb_loss` are referenced only by the health probe today. **DATABRIDGE is
  read-only and worker-side only**, reached through the wheel's own executor with repo-owned
  scripts under `sql/databridge/` — never through the `db/` package, never written to.
- **`rwb_job` is written by all three processes.** The request path and the poller insert or
  revive; the worker claims, heartbeats and completes; and the poller's **reconciler** resets
  `running → pending` (clearing `claimed_by`) when a heartbeat goes stale.
- **`enqueue_rwb_job` vs `ensure_pending_rwb_job` is the mechanical/human split.** The poller's
  version **never revives a terminal row** — that is the idempotency backbone of every automatic
  chain. The analyst-triggered version **does**. The unique key is
  `(requestor_type, requestor_id, rwb_job_type)`, which is why the same work enqueued by the
  poller (`'irp_job'` key) and by a manual Sync (`'analyst_request'` key) can coexist — and why
  read paths that ask "is a backfill running?" have to union both keys.
- **`correlation_id` threads the chain** — bound per `irp_job` in the poller and per `rwb_job` in
  the worker, so chained enqueues inherit it. A revived row is re-stamped with the *retrying*
  request's id: a retry is a new causal chain.
- **The poller's RM lookups run before its transaction opens.** The transaction then covers
  `update_tracking` + the entity write + the chained enqueue **atomically**.
- **Optimistic concurrency is always the WHERE clause, never a prior SELECT** —
  `… WHERE updated_at = :expected`, `rowcount == 0` → 409.
- **No row-level security anywhere** (Article 6). Every authenticated analyst sees every deal,
  every EDM, every package. Roles gate *functions*; read-only gates like `_package_actionable`
  govern whether **buttons** render, not whether data is visible.

---

## Flows

### Shell & auth

| Flow | User action (spec) | Writes | Async work |
|---|---|---|---|
| [Navigate the shell & check health](shell/navigate_and_health.md) | 001 US6/US7/US8 | none | none |
| [Sign in, stay in, sign out](auth/login_and_session.md) | 001 US1/US2/US5 | `user_session`, `login_attempt`, `app_user.last_login_at` | none |
| [Provision & administer users](auth/user_administration.md) | 001 US3/US4 | `app_user`, `user_role`, `user_session` | none |

### Submissions

| Flow | User action (spec) | Writes | Async work |
|---|---|---|---|
| [Register a submission](submissions/create_submission.md) | 002 US1 + US5 | `submission` + `submission_status_event` (one txn) | none |
| [Find & filter submissions](submissions/find_submissions.md) | 002 US2 | none — read-only | none |
| [View a submission](submissions/view_submission.md) | 002 detail + 003 US5 | none — read-only | none (reads what the others drive) |
| [Manage a submission](submissions/manage_submission.md) | 002 US3 + US4 | `submission` (optimistic), `submission_status_event`, `submission_crm_id` | none |

### EDM & RDM entities

| Flow | User action (spec) | Request-path writes | Worker | Poller | Chaining |
|---|---|---|---|---|---|
| [Browse the libraries](entities/browse_libraries.md) | 003 US7 | **none — read-only** | — | — | — |
| [Import an EDM](entities/import_edm.md) | 003 US1 | `irp_edm`(pending) + `rwb_job`(upload_edm) | submit → `irp_job`(import_edm) | mirror → `irp_edm` ready/error | → `backfill_edm_detail` |
| [Import an RDM](entities/import_rdm.md) | 003 US2 | `irp_rdm`(pending) + `rwb_job`(upload_rdm) | fan-out → 1 `irp_job`(import_rdm) **per (RDM×EDM) pair** | mirror; `error` in place | → `backfill_rdm_analyses` (which owns the `ready` rollup) |
| [View an EDM's detail](entities/view_edm_detail.md) | 004 US1 + US4 | **none — read-only** | — | — | — |
| [View an RDM's analyses](entities/view_rdm_detail.md) | 004 US3 | **none — read-only** | — | — | — |
| [Review & export treaties](entities/review_treaties.md) | 004 US2 | **none — read-only** (returns a file) | — | — | — |
| [Recover a failed import](entities/recover_import.md) | 003 recovery | entity `→ pending_import` + revive the `rwb_job` | re-runs the import | as the import | as the import |

### Packages

| Flow | User action (spec) | Request-path writes | Worker | Poller | Chaining |
|---|---|---|---|---|---|
| [Assemble & sync a package](packages/save_and_sync_package.md) | 003 US3 | `package` + member entities; then N `rwb_job`(upload_edm) + M `rwb_job`(upload_rdm) | submit every member, no ordering | on each FINISHED, enqueue that member's backfill | **N EDMs + M RDMs → N+M heads → N+M imports** |
| [Delete a package](packages/delete_package.md) | 003 US4 | N `rwb_job`(delete_rdm) *or* delete_edm | RDM delete **synchronous (no `irp_job`)**; EDM delete async | on delete_edm FINISHED, mark deleted + finalize | RDM→EDM fan-in; idempotent finalize |

### Detail backfill

| Flow | User action (spec) | Request-path writes | Worker | Poller | Chaining |
|---|---|---|---|---|---|
| [Backfill an EDM's detail](backfill/backfill_edm_detail.md) | 004 US1/US4 — **no click** | none | RM portfolios + `/metrics` + treaties, **Data Bridge** summary → `irp_portfolio` / `irp_treaty` snapshots | enqueues it on `import_edm` FINISHED | runs **parallel** to the `upload_rdm` chain |
| [Backfill an RDM's analyses](backfill/backfill_rdm_analyses.md) | 004 US3 — **no click** | none | `search_analyses` + metadata → `irp_analysis`; **and the `ready` rollup** | enqueues it on `import_rdm` FINISHED | fan-in across every apply of that RDM |
| [Sync an entity on demand](backfill/manual_sync.md) | 004 T056 | **only** a `rwb_job` (insert or revive) | the two flows above, unchanged | — | EDM Sync also fans out one job per applied RDM |

---

## Not covered (and why)

Explicit, so the gaps don't read as oversights:

- **003 US6 — the jobs list + notifications.** Descoped 2026-07-15 (T048–T056 removed): no
  `job_query.list_jobs` filter, no `notification_service`, no `notify_analyst` actor, no jobs
  router or SSE. `job_query` survives with only `package_job_counts`. The package card's
  all/active/failed numbers are real, but their deep-links land on empty stubs.
- **003 T017a — automatic submit retry.** `_submission_retry` in the poller is a **no-op
  scaffold**. A `SUBMISSION FAILED` row (submit never reached RM, so no `irp_id`, so nothing
  polls it) is recoverable only by the analyst clicking Retry.
- **The stub pages** — all of `/workflows/*`, `/results`, `/templates`. Nav nodes exist; the
  pages are one-line placeholders.
- **`GET /api/search` does not exist**, though the shell's global search box posts to it on every
  keystroke. The manifest already has `searchable` flags and `searchable_nodes()`.
- **`POST /packages/{package_id}`** (the package *edit* route) is unreachable — no template posts
  to it, and it carries a documented member-duplication hazard if ever wired.
- **Four seeded `rwb_job_type`s have no actor**: `retrieve_analysis_results`,
  `download_export_file`, `push_results_to_loss_repo`, `notify_analyst`.
- **Four seeded `irp_job_type`s have no poller getter**: `geohaz`, `analysis`, `grouping`,
  `export` — the poller logs "No getter for irp_job_type" and skips them. There is no
  analysis-run user action yet; see [`planned/`](planned/README.md).
- **No RDM delete and no standalone EDM delete.** Deletion exists only through
  [delete a package](packages/delete_package.md).
- **`irp_analysis.group_parent_id`** exists and nothing populates it (004 T005), so group
  membership is unknown.

## Conventions in the mermaid

- Coloured `rect` blocks mark which **process** owns the steps (legend above).
- `INSERT` / `UPDATE` arrows to `WORKBENCH DB` are the **actual metamodel writes**, in order.
- A `loop each pass` around poller steps = the interval loop; inside it is **one** single-status
  check (never a blocking poll-to-completion).
- `alt` blocks show the terminal branches (`FINISHED` vs `FAILED`/`CANCELLED`) and the two
  failure modes (`SUBMISSION FAILED` at submit vs. an RM-side failure later).
- Read-only flows carry a **Records read (none written)** table in place of the usual
  **Records written (in order)** table.
