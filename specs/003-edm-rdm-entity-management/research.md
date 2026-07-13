# Phase 0 — Research: EDM & RDM Entity Management (incl. Packages) (Iteration 2)

**No Technical Context unknowns.** The stack, dev workflow, `db/` layer, test tiers, and project structure are inherited unchanged from Iterations 0–1 and the constitution; the one genuinely open design question (**A21 — cross-boundary job chaining**) was resolved in the spec (Clarifications → A21 resolution). There were therefore **zero `NEEDS CLARIFICATION` markers** to resolve here. This document records the concrete decisions the implementation hangs on — the pieces the spec and DATA_MODEL deliberately leave to planning — each in Decision / Rationale / Alternatives form.

The canonical schema and the A21 mechanism live in **DATA_MODEL.md §5, §6, §8, §13**; this research turns them into implementation choices and reconciles them against the *installed* `irp-integration`.

---

## R1 — `irp-integration`: version reconciliation + the exact methods used

**Decision**: Treat `irp-integration` as reachable **only** through a thin `app/services/irp_gateway.py` interface that names every method the app calls. `IRPClient()` reads all config from env vars (no constructor args). The methods the gateway wraps this iteration:

| Operation | `irp-integration` call | Sync/async | Notes |
|---|---|---|---|
| EDM import | `submit_edm_import_job()` → job id (+ `request_body`) | async | store `request_body["resourceUri"]` on `irp_job_resource` at submit time |
| RDM import/apply | `submit_rdm_import_job()` (per EDM, or review-only) → job id | async | one job per (EDM × RDM) pair; review-only omits the EDM |
| EDM delete | `submit_delete_edm_job()` → pollable job id | async | polled like an import (R6) |
| RDM delete | synchronous analysis-entity delete call (R6) | **sync** | no `irp_job`; resolves analyses by `rdmName` |
| Name collision | `search_edms()` / `search_rdms()` | sync | non-blocking warning (R8) |
| Poll status | `get_edm_import_job()` / `get_analysis_job()` / `get_delete_*` single-status getters | sync | **single-status-check only**; `poll_*_to_completion` forbidden |

**Version sourcing — RESOLVED 2026-07-13.** `irp-integration` is a pre-release library whose signatures still move, and it lives across three places at different versions: **PyPI `0.2.0`** (production target), **TestPyPI `0.2.1`/`…dev26`** (ahead-of-stable dev builds), and a **local editable checkout** at `../../IRP/irp-integration` (setuptools-scm dynamic; team-owned, sometimes patched). The old exact `==0.2.1.dev23` pin hard-wired the project to TestPyPI. It is replaced by three mutually-exclusive uv **dependency groups** — `irp-pypi` (`>=0.2,<1`), `irp-testpypi` (`>=0.2.1.dev0`, pre-release-allowing), `irp-local` (unpinned path source) — selected via `[tool.uv] default-groups` and switched with `make irp-pypi | irp-testpypi | irp-local`. `uv lock` holds all three simultaneously (a universal lock), so switching just installs the active group's variant; PyPI is the committed default (production-safe). Because the gateway is the **only** module that imports the library, **every method name + signature above MUST still be re-confirmed against the active wheel before its operation is implemented** (spec Assumptions; Dependencies); the CI fake (R2/Article 12) implements the same interface, so a signature change is a one-file edit plus a fake update — not a scatter across services.

**Rationale**: Article 11 requires IRP behind an interface; a single gateway makes the poller/worker unit-testable against a fake and quarantines the version churn. Preferring single-item calls (per user memory) over fail-fast batch helpers keeps one member's failure from aborting a whole package sync.

**Alternatives considered**: (a) Call `irp-integration` directly from services — rejected: unfakeable in CI, and spreads version risk. (b) Pin down signatures now from docs — rejected: the installed version differs from the pin; confirm against the actual wheel at implementation.

---

## R2 — Poller: batch single-status poll → terminal enqueue

**Decision**: `poll_once()` (in the standalone `app/poller/run.py`) runs one pass: `SELECT` non-terminal `irp_job` rows (status not in the terminal set `FINISHED`/`FAILED`/`CANCELED`/`SUBMISSION FAILED`), **group by `irp_job_type`**, and for each call the matching single-status-check `get_*_job` once. Mirror the returned status onto `irp_job.status` (plain in-place `UPDATE`), stamp `last_tracked_at`, and on a **terminal** status: (a) backfill the produced entity's `irp_id` (`irp_edm`/`irp_rdm`) and flip its `status` (`ready`/`error`); (b) if `FINISHED`, **idempotently insert the dependent head `rwb_job`** via the composite key (R4). The pass never calls `poll_*_to_completion`.

**Poller also owns** two batches that are not per-`irp_job`: the **reconciler** (reclaim stale `rwb_job` rows whose `rwb_job_heartbeat` is older than `RWB_HEARTBEAT_STALE_SECS`, back to `pending`) and the single-threaded **`submission_retry`** batch (re-attempt `SUBMISSION FAILED` rows with `submission_attempt_count < IRP_SUBMISSION_MAX_RETRIES` — a deployment config value with no fixed default, with backoff; a row that reaches the max stays parked as terminal `SUBMISSION FAILED` for analyst-driven recovery).

**Rationale**: Article 11 (single-status-check, standalone process) + DATA_MODEL §8 ("query non-terminal jobs grouped by `irp_job_type`, poll each via the single-status-check `get_*_job`… On terminal status, backfill entity `irp_id`s and create head `rwb_job` row(s)"). Batching by type matches how the library exposes getters. The reconciler-in-poller placement is mandated by Article 10.

**Alternatives considered**: (a) Per-job `poll_to_completion` — forbidden (blocks minutes). (b) A separate reconciler process — rejected: DATA_MODEL/Article 10 put the reclaim in the poller. (c) Event-sourcing `irp_job.status` — rejected: Article 4 says job status is updated in place.

---

## R3 — `rwb_job`: SQL table is the queue; Dramatiq is the executor

**Decision**: The **`rwb_job` table is the queue of record** (Article 10). Dramatiq (redis broker) is the *execution* mechanism: enqueuing a work item is `INSERT rwb_job (status_code='pending', …)` **and** sending a Dramatiq message that wakes the single worker; the worker's actor then **claims the row atomically** — `UPDATE rwb_job SET status_code='running', claimed_by=:wid WHERE id=:id AND status_code='pending'`; `rowcount==0` ⇒ already claimed ⇒ the actor exits cleanly. While running, a daemon thread upserts `rwb_job_heartbeat(worker_id, heartbeat_at)` every `RWB_HEARTBEAT_INTERVAL_SECS`. On completion the actor sets `succeeded`/`failed`, writes `output_data`/`error_detail`, and — on success — inserts any chained tail `rwb_job` (R4). A redis outage cannot lose work: the row is already `pending`, and a lightweight "sweep pending" path (or the reconciler) re-dispatches it.

**Rationale**: DATA_MODEL §8 states `rwb_job` is executed by a Dramatiq worker *and* claimed via the atomic `UPDATE … WHERE status_code='pending'`; Article 10 requires the SQL table be the queue with single worker + heartbeat + reconciler. Making the row (not the redis message) authoritative reconciles "SQL table is the queue" with the `dramatiq[redis]` dependency: redis is a wake-up/dispatch signal, not the source of truth.

**Alternatives considered**: (a) Pure redis queue — rejected: violates Article 10 (SQL table must be the queue). (b) Pure DB-poll worker, no Dramatiq — viable and simpler, but the dependency is already declared and Dramatiq gives lower-latency wake-ups; the atomic claim means the two can't double-execute. (c) Multi-worker by default — rejected: Article 10 is single-worker by default; concurrency-safe claim is a documented upgrade.

---

## R4 — Completion-chaining + idempotent fan-in (no counter) — the A21 core

**Decision**: Drive the member sequence by **completion-chaining**, keyed by the trigger, with fan-in detected by an idempotent query under an atomic guard — never a dependency counter.

- **Trigger key**: every chained `rwb_job` carries `(requestor_type, requestor_id, rwb_job_type)` with a `UNIQUE` constraint. Head rows from the request path use `requestor_type='analyst_request', requestor_id=package.id`; rows the poller enqueues on `FINISHED` use `requestor_type='irp_job', requestor_id=<finished irp_job.id>`; app-side fan-in rows use `requestor_type='rwb_job'`. Every enqueue is an **idempotent insert** (`INSERT … WHERE NOT EXISTS` / swallow unique-violation), so a re-poll, worker redelivery, or reconciler re-enqueue can't double-submit.
- **Fan-out** (natural): `import_edm` FINISHED → one `upload_rdm` (which itself fans out to an apply per RDM in the package).
- **Fan-in** (idempotent query, not counted): the `delete_edm` heads are enqueued only once **all** the package's RDM removals are done — each `delete_rdm` worker, on success, runs `NOT EXISTS (SELECT 1 FROM irp_rdm WHERE package_id=:p AND status <> 'deleted')` and, if satisfied, enqueues the `delete_edm` rows. Each `delete_edm` worker then submits under an atomic guard `UPDATE irp_edm SET status='delete_pending' WHERE id=:e AND status NOT IN ('delete_pending','deleted')` (rowcount 0 ⇒ already handled). `package.deleted_at` is stamped by an idempotent `UPDATE … WHERE deleted_at IS NULL AND NOT EXISTS (live members)`. Sync-side rollup is the same shape: `irp_rdm.status='ready'` once all its `import_rdm` applies are FINISHED.

**Rationale**: DATA_MODEL §8 → "Package sync/delete chaining" prescribes exactly this (lineage chaining; idempotent "are all siblings terminal?" query guarded by an atomic status transition; no counter column). It satisfies Article 2 (sequencing derived, not stored) and FR-043/SC-014 (idempotent, no double-submit, no premature advance). This is the mandated **prerequisite-gate** test target (Article 12).

**Alternatives considered**: (a) A `pending_children` counter decremented on each completion — rejected explicitly by DATA_MODEL §8 (races on redelivery; not idempotent). (b) A stored DAG of dependencies — rejected: Article 2 forbids stored topology. (c) Poller-driven fan-in for RDM→EDM — impossible: RDM delete is synchronous with no `irp_job` to observe (R6), so fan-in is app-side on worker success.

---

## R5 — Per-pair sync fan-out sequencing

**Decision**: Save-and-Sync enqueues **one `upload_edm` head per EDM** (not a single global "EDM head"). When an EDM's `import_edm` reaches FINISHED, the poller enqueues **one `upload_rdm`** keyed to that finished job; the `upload_rdm` worker submits an `import_rdm` **apply per RDM in the package onto that specific EDM** (resolved by name via `search_edms`, Article 2). Thus each RDM apply waits only for *its* target EDM's upload; independent EDMs and their applies proceed in parallel. A **review-only** package (RDM, no EDM) inserts one `upload_rdm` head directly on the request path and submits a single apply with no EDM (FR-016).

**Rationale**: FR-015 / SC-006 / spec Edge Cases ("per-pair sync ordering… no single global EDM head job"). DATA_MODEL §8 sets `irp_job` grain per (RDM × EDM) pair. Name-based coupling at submit time is Article 2.

**Alternatives considered**: (a) One head job gating all applies — rejected: serializes independent EDMs, violates the per-pair rule. (b) Pre-computing the full pair grid as stored rows up front — rejected: Article 2 (derive, don't store); the grid is derived from `package_id` membership.

---

## R6 — Asymmetric delete: async EDM job vs synchronous RDM analysis-entity delete

**Decision**: **EDM delete is asynchronous** — `delete_rdm`-gated `delete_edm` worker calls `submit_delete_edm_job()`, writes an `irp_job(irp_job_type='delete_edm')`, and the poller follows it to `FINISHED` with a single-status getter (the import/risk-data job getter — confirm the exact getter against the installed wheel, R1). **RDM delete is synchronous** — an RDM import produces **analysis entities**, not a first-class Risk Modeler RDM object, so removal is an inline delete of those entities: the `delete_rdm` worker resolves the RDM's analyses by `rdmName` and deletes them, writes **no `irp_job`**, and marks `irp_rdm.status='deleted'` only once the delete has completed. Delete order stays RDM-before-EDM (an EDM removal waits for all its RDM removals — R4 fan-in), because the RDM's analyses reference the EDM's exposure.

**Rationale**: The user's 2026-07-13 clarification and DATA_MODEL §8 step 5 / §13 note. Because there is no RDM `irp_job`, the RDM→EDM fan-in must be app-side on worker success (R4), not poller-mediated. Only `delete_edm` is added to `irp_job_type_kind`; there is no `delete_rdm` job-type kind.

**Alternatives considered**: (a) Model RDM delete as a polled job — rejected: it maps to no Risk Modeler job (the earlier A21 draft's error, now corrected). (b) Delete EDM before RDM — rejected: the RDM's analyses hang off the EDM's exposure; reverse-of-sync order is required (FR-019).

---

## R7 — Recovery: idempotent re-sync + per-member retry + source-file replacement

**Decision**: Provide all three recovery paths (FR-044–FR-047):
1. **Idempotent Save-and-Sync** — re-running it re-inserts head rows only for members not already `ready`/in-flight (re-submitting `error`/unstarted ones); the `UNIQUE(requestor_type, requestor_id, rwb_job_type)` key makes repeats safe (R4).
2. **Per-member retry** — a control on the package card re-inserts a single member's head `rwb_job` without touching the rest.
3. **Replace source file** — the analyst re-browses the read-only shared drive, picks a replacement for the failed member, the service updates `irp_edm/irp_rdm.source_file_path`, and retries the import against the new file (the expected primary remedy for a bad broker `.bak`).

Submit-side failures (`SUBMISSION FAILED`, no `irp_id`) are retried automatically by the single-threaded `submission_retry` batch (R2), independent of these analyst-driven paths.

**Rationale**: FR-044–FR-047 / SC-013 / DATA_MODEL §8 step 5. Idempotency reuses the R4 dedup key, so recovery needs no new machinery beyond a "skip if already ready/in-flight" predicate and a source-file update.

**Alternatives considered**: (a) Rebuild-the-package-to-recover — rejected: SC-013 requires recovery without rebuilding. (b) Auto-retry Risk-Modeler-side failures (`FAILED`) — rejected: a `FAILED` import usually means a bad file; the analyst chooses replace-or-retry, only `SUBMISSION FAILED` auto-retries.

---

## R8 — Name-collision as a non-blocking warning

**Decision**: Setting or renaming an EDM/RDM name runs `search_edms()` / `search_rdms()`; if the name already exists in Risk Modeler, the response carries a **non-blocking** warning fragment that highlights the name field and offers "rename" or "use anyway" — it never blocks Save or Save-and-Sync (FR-012). Same two-step HTMX shape as the Iteration-1 duplicate-warning (a `confirmed`/override marker skips re-warning on the second submit).

**Rationale**: FR-012 / SC-005 (warn in 100% of collisions, block in 0%); DATA_MODEL §5 ("a non-blocking warning if the name already exists in IRP"). Reuses the Iteration-1 warning UX so the pattern is consistent.

**Alternatives considered**: (a) Hard-block on collision — rejected: Risk Modeler allows duplicate names and analysts sometimes want them (M&A/test variants); FR-012 forbids blocking. (b) Auto-suffix — rejected: mangles the analyst's chosen name.

---

## R9 — Live Jobs-list status via SSE (within "no SPA")

**Decision**: The Jobs list updates live via **Server-Sent Events** (`sse-starlette`, already a dependency): the browser opens an `EventSource` to a jobs-stream endpoint; when the poller advances a job the server pushes the **server-rendered** `job_row.html` fragment, swapped in place via HTMX's SSE extension. Filters remain a pure function of the URL query string (R12); the stream is scoped to the same filter. JS-off degrades to a normal filtered page that reflects state on each load. **Cross-process signal:** the poller and web run in separate processes, so the stream generator learns of advances by re-querying the filtered `job_query` each ~`POLL_INTERVAL_SECS` and emitting changed `job_row`s (a Redis pub/sub bridge is a documented upgrade); latency is bounded by SC-001.

**Rationale**: FR-036 / SC-001 ("reflect status changes live… without a manual refresh"). Article 8 permits server-rendered live transports — SSE pushes HTML fragments, not a client-side app; it's the Iteration-0 "live-status transport" scaffold (`sse-starlette`) first used here. No SPA, no client state store.

**Alternatives considered**: (a) HTMX `hx-trigger="every Ns"` polling — acceptable fallback, but re-renders the whole list and lags the poller; SSE is push and cheaper. (b) WebSockets — rejected: bidirectional overkill; status flow is server→client only. (c) Client-side polling of a JSON API — rejected: reintroduces client state / SPA drift.

---

## R10 — Notification channel resolution

**Decision**: `notify_analyst` is an `rwb_job` actor that dispatches to the **configured** channel(s) — Teams webhook and/or email and/or desktop toast — resolved from config (`NOTIFY_CHANNEL`, `TEAMS_WEBHOOK_URL`, SMTP settings). At least one channel is delivered per config; enabling a specific channel is a config edit, not a code change. It fires from the worker (never the web/poller) at **two granularities** (Q1, 2026-07-13): one **action-completion** message when an analyst action (a standalone import / package sync / package delete) reaches a fully-terminal state, and one **member-failure** message per failed member operation — **never one per successfully-completed member job**. The poller/worker enqueues the `notify_analyst` row via an idempotent action/member key, so repeated terminal triggers do not duplicate a notification.

**Rationale**: FR-030 / SC-003 (notify on 100% of terminal *actions* and 100% of *failures*, from a background worker — one-per-successful-member would flood a 50-member sync); spec Assumptions (channel is configurable). Keeping channel selection in config satisfies "not a scope question."

**Alternatives considered**: (a) Hardcode one channel — rejected: the deployment picks the channel. (b) Notify from the poller — rejected: Article 11 keeps result/side-effect work in workers; the poller only enqueues the `notify_analyst` `rwb_job`.

---

## R11 — Shared-drive read-only browse

**Decision**: Browsing is a **live directory listing** of the mounted read-only shared drive via a server endpoint that lists entries under a path constrained to `SHARED_DRIVE_ROOT` (path-traversal-guarded; symlinks/`..` rejected). The listing feeds an HTMX/Alpine multi-select; the chosen path string is stored verbatim on `irp_edm/irp_rdm.source_file_path`. There is **no cached/scanned inventory** to reconcile. The browse start location is seeded from the submission's `directory_path` when present. The app never writes/moves/deletes on the drive; the optional "delete after transfer" affordance (FR-008) only removes an **app-created temporary** file, never a broker file.

**Rationale**: FR-008/FR-009/FR-011, spec Edge Cases; DATA_MODEL §5 (`source_file_path` is the whole file model — no `file_artifact`, no versioning).

**Alternatives considered**: (a) A scanned file inventory table — rejected explicitly (FR-009 "no cached/scanned inventory"). (b) Client-side file picker — impossible for a server-mounted drive; the listing must be server-side.

---

## R12 — Nav placement: EDM/RDM libraries + the Jobs list

**Decision**: Add **`irp.edm_library`** and **`irp.rdm_library`** sidebar nodes under the existing `irp` ("Moody's IRP") rail root — global, cross-submission destinations with no row-scoping (FR-037). The **Jobs list** reuses the existing `workflows.irp_jobs` / `workflows.rwb_jobs` sidebar nodes (Iteration-0 stubs), now made real and **URL-query-string filterable** (FR-032–FR-035); package-card job-count links deep-link into them with the filter in the query string. Filters share one fixed vocabulary — `submission`, `package`, `status`, `job_type` — and each list accepts the subset it understands (FR-033).

**Rationale**: Article 1 (manifest is the one source of truth — a page = one node + handler + template). The `irp` rail already exists for Moody's-facing destinations; the `workflows.*_jobs` nodes already exist for job monitoring. Reusing them avoids inventing a parallel nav tree.

**Alternatives considered**: (a) A new top-level "Libraries" rail root — rejected: heavier than needed; the `irp` root is the natural home. (b) A single unified Jobs page merging irp+rwb — reasonable, but the existing two nodes already split them and the shared filter vocabulary keeps them coherent; leave the split and make both filterable.

---

## R13 — Scope boundary: analyses are created in IRP but not tracked locally

**Decision**: RDM import **creates broker analyses in Risk Modeler** (FR-002) and RDM delete **removes those analysis entities** (FR-020, via R6), but the workbench does **not** create local `irp_analysis` rows this iteration. The package card's "portfolio summary and analysis counts" render **empty** (FR-023), and the `delete_rdm` worker resolves analyses to delete by `rdmName` against Risk Modeler (DATA_MODEL §6) rather than from a local table. `irp_analysis` + `irp_analysis_status_kind` + `irp_portfolio`/`irp_treaty` are **not** created in the migration this iteration.

**Rationale**: Spec Assumptions ("Analysis, grouping, results, repositories, and treaties are out… analysis counts render empty this iteration"). Tracking analyses is a later iteration; the synchronous RDM delete needs only the Risk Modeler-side query-by-`rdmName` (confirmed enumerable, DATA_MODEL §6/§14), not local rows. Keeps the migration surface to the `irp_job`/`rwb_job` families only.

**Alternatives considered**: (a) Create `irp_analysis` now — rejected: out of scope, and unused until the analysis iteration. (b) Store a local analysis-id list for RDM delete — rejected: DATA_MODEL §6 confirms analyses are enumerable by `rdmName`, so no local mirror is needed.

---

## Summary of decisions

| # | Area | Decision |
|---|---|---|
| R1 | IRP library | One `irp_gateway` interface; source switchable across PyPI(0.2.0)/TestPyPI(0.2.1.dev)/local via uv dependency groups + `make irp-*` (default PyPI); re-confirm every method/signature vs the active wheel; prefer single-item calls; fake in CI |
| R2 | Poller | Batch non-terminal `irp_job` by type; single-status `get_*_job`; on terminal backfill + idempotent enqueue; owns reconciler + `submission_retry`; never `poll_*_to_completion` |
| R3 | Queue | `rwb_job` SQL table is the queue of record; Dramatiq wakes the single worker; atomic claim `WHERE status_code='pending'`; heartbeat daemon; reconciler reclaim |
| R4 | Chaining / fan-in | Completion-chaining keyed by `(requestor_type, requestor_id, rwb_job_type)` UNIQUE; idempotent inserts; fan-in via "all siblings terminal?" query under atomic guard — **no counter** |
| R5 | Sync fan-out | One `upload_edm` per EDM; `import_edm` FINISHED → one `upload_rdm` → apply per RDM onto that EDM; per-pair, parallel; review-only = single apply, no EDM |
| R6 | Delete asymmetry | EDM delete async (`delete_edm` `irp_job`, polled); RDM delete **synchronous** (delete analyses by `rdmName`, no `irp_job`); RDM-before-EDM; RDM→EDM fan-in app-side |
| R7 | Recovery | Idempotent re-sync + per-member retry + source-file replacement; `SUBMISSION FAILED` auto-retried by the batch |
| R8 | Name collision | `search_edms/rdms` → non-blocking warning; never blocks Save/Sync; two-step HTMX confirm |
| R9 | Live status | SSE (`sse-starlette`) pushes server-rendered `job_row` fragments; filter = pure URL function; JS-off degrades |
| R10 | Notifications | `notify_analyst` worker actor; configured channel(s) (Teams/email/desktop); per-action completion + per-member failure (not per successful member) |
| R11 | Shared drive | Live read-only listing under `SHARED_DRIVE_ROOT` (traversal-guarded); path stored on the member; no inventory; never mutates broker files |
| R12 | Nav | `irp.edm_library` / `irp.rdm_library` new nodes; Jobs list reuses `workflows.irp_jobs`/`rwb_jobs`, made filterable; shared `submission/package/status/job_type` vocabulary |
| R13 | Scope | Analyses created in IRP but **not** tracked locally; card analysis counts empty; no `irp_analysis`/`irp_portfolio`/`irp_treaty` tables this iteration |
