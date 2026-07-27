# Phase 0 — Research: EDM & RDM Entity Management (incl. Packages) (Iteration 2)

**No Technical Context unknowns.** The stack, dev workflow, `db/` layer, test tiers, and project structure are inherited unchanged from Iterations 0–1 and the constitution; the one genuinely open design question (**A21 — cross-boundary job chaining**) was resolved in the spec (Clarifications → A21 resolution). There were therefore **zero `NEEDS CLARIFICATION` markers** to resolve here. This document records the concrete decisions the implementation hangs on — the pieces the spec and DATA_MODEL deliberately leave to planning — each in Decision / Rationale / Alternatives form.

The canonical schema and the A21 mechanism live in **DATA_MODEL.md §5, §6, §8, §13**; this research turns them into implementation choices and reconciles them against the *installed* `irp-integration`.

---

## R1 — `irp-integration`: version reconciliation + the exact methods used

**Decision**: Reach `irp-integration` **only** through a thin `app/services/irp_gateway.py` interface. `IRPClient()` reads all config from env vars (no constructor args). **The exact methods, signatures, and request bodies are now confirmed against the 0.2.0 wheel and recorded authoritatively in [contracts/worker-poller.md → "IRP gateway — confirmed method surface"](contracts/worker-poller.md); that table supersedes this one.** Confirmed 2026-07-14 — the library is **manager-based** (`client.edm` / `.rdm` / `.import_job` / `.risk_data_job` / `.analysis`), not the flat names this doc originally drafted. What changed from the original draft:

- EDM/RDM import and EDM delete names hold (`submit_edm_import_job`, `submit_rdm_import_job`, `submit_delete_edm_job`) but are **manager-scoped**, not top-level.
- **RDM delete = `analysis.delete_analysis(id)` per analysis** (synchronous, no `irp_job`), enumerated from **local `irp_analysis` rows** captured at import via `search_analyses('sourceRdmName="…" AND exposureName="…"')` (R6/R13, D2). The earlier "resolve analyses by `rdmName`" named the wrong field.
- **Poll getters collapse to two**: `import_job.get_import_job` (both imports — one shared endpoint) and `risk_data_job.get_risk_data_job` (EDM delete). No `get_edm_import_job`/`get_analysis_job`. Terminal set is `FINISHED/FAILED/CANCELLED` (double-L).
- **RDM name search** is `rdm.search_imported_rdms` (there is no `search_rdms`).
- **No `irp-integration` code change is required for Iteration 2** — every needed call exists in 0.2.0 behind the gateway. Deferred/nice-to-have library items are tracked in `docs/IRP_INTEGRATION_FOLLOWUPS.md`.

**Version sourcing — RESOLVED 2026-07-13.** `irp-integration` is a pre-release library whose signatures still move, and it lives across three places at different versions: **PyPI `0.2.0`** (production target), **TestPyPI `0.2.1`/`…dev26`** (ahead-of-stable dev builds), and a **local editable checkout** at `../../IRP/irp-integration` (setuptools-scm dynamic; team-owned, sometimes patched). The old exact `==0.2.1.dev23` pin hard-wired the project to TestPyPI. It is replaced by three mutually-exclusive uv **dependency groups** — `irp-pypi` (`>=0.2,<1`), `irp-testpypi` (`>=0.2.1.dev0`, pre-release-allowing), `irp-local` (unpinned path source) — selected via `[tool.uv] default-groups` and switched with `make irp-pypi | irp-testpypi | irp-local`. `uv lock` holds all three simultaneously (a universal lock), so switching just installs the active group's variant; PyPI is the committed default (production-safe). Because the gateway is the **only** module that imports the library, **every method name + signature above MUST still be re-confirmed against the active wheel before its operation is implemented** (spec Assumptions; Dependencies); the CI fake (R2/Article 12) implements the same interface, so a signature change is a one-file edit plus a fake update — not a scatter across services.

**Rationale**: Article 11 requires IRP behind an interface; a single gateway makes the poller/worker unit-testable against a fake and quarantines the version churn. Preferring single-item calls (per user memory) over fail-fast batch helpers keeps one member's failure from aborting a whole package sync.

**Alternatives considered**: (a) Call `irp-integration` directly from services — rejected: unfakeable in CI, and spreads version risk. (b) Pin down signatures now from docs — rejected: the installed version differs from the pin; confirm against the actual wheel at implementation.

---

## R2 — Poller: batch single-status poll → terminal enqueue

**Decision**: `poll_once()` (in the standalone `app/poller/run.py`) runs one pass: `SELECT` non-terminal `irp_job` rows (status not in the terminal set `FINISHED`/`FAILED`/`CANCELLED`/`SUBMISSION FAILED`), **group by `irp_job_type`**, and for each call the matching single-status-check getter once (`get_import_job` for imports, `get_risk_data_job` for `delete_edm`). Mirror the returned status onto `irp_job.status` (plain in-place `UPDATE`), stamp `last_tracked_at`, and on a **terminal** status: (a) backfill the produced entity's `irp_id` (`irp_edm`/`irp_rdm`) and flip its `status` (`ready`/`error`); (b) if `FINISHED`, **idempotently insert the dependent head `rwb_job`** via the composite key (R4). The pass never calls `poll_*_to_completion`.

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

**Decision**: Save-and-Sync enqueues **one `upload_edm` head per EDM** (not a single global "EDM head"). When an EDM's `import_edm` reaches FINISHED, the poller enqueues **one `upload_rdm`** keyed to that finished job; the `upload_rdm` worker submits an `import_rdm` **apply per RDM in the package onto that specific EDM** (resolved by name via `search_edms`, Article 2). Thus each RDM apply waits only for *its* target EDM's upload; independent EDMs and their applies proceed in parallel. A **review-only / RDM-only** package (an RDM with no EDM) is **deferred to follow-up** (D3, 2026-07-14): `submit_rdm_import_job` requires `edm_name` in 0.2.0, so every package this iteration has ≥1 EDM, every `import_rdm` apply has a target EDM, and Save-and-Sync rejects an RDM-only package.

**Rationale**: FR-015 / SC-006 / spec Edge Cases ("per-pair sync ordering… no single global EDM head job"). DATA_MODEL §8 sets `irp_job` grain per (RDM × EDM) pair. Name-based coupling at submit time is Article 2.

**Alternatives considered**: (a) One head job gating all applies — rejected: serializes independent EDMs, violates the per-pair rule. (b) Pre-computing the full pair grid as stored rows up front — rejected: Article 2 (derive, don't store); the grid is derived from `package_id` membership.

---

## R6 — Asymmetric delete: async EDM job vs synchronous RDM analysis-entity delete

**Decision**: **EDM delete is asynchronous** — the `delete_rdm`-gated `delete_edm` worker calls `client.edm.submit_delete_edm_job(exposure_id=irp_edm.irp_id)`, writes an `irp_job(irp_job_type='delete_edm')`, and the poller follows it to `FINISHED` via **`client.risk_data_job.get_risk_data_job`** (confirmed 2026-07-14). **RDM delete is synchronous** — an RDM import produces **analysis entities**, not a first-class Risk Modeler RDM object, so removal is an inline per-analysis delete: the `delete_rdm` worker reads the RDM's `irp_analysis` rows (captured at import, D2/R13) and loops **`client.analysis.delete_analysis(analysis_id)`**, writes **no `irp_job`**, and marks `irp_rdm.status='deleted'` only once the deletes complete. Delete order stays RDM-before-EDM (an EDM removal waits for all its RDM removals — R4 fan-in), because the RDM's analyses reference the EDM's exposure.

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

## R8 — Name-collision blocks the save (amended 2026-07-27 — issue #17)

**Decision**: Setting or renaming an EDM/RDM name runs `search_edms()` / `search_imported_rdms()` (cached ~30s in-process — issue #11); a hit **blocks Save and Save-and-Sync** with an error naming the member — nothing is persisted or enqueued. The same check renders as-you-type as a blocking-error fragment that disables the submit buttons. When the check cannot reach Risk Modeler it **fails open**: the save proceeds with a visible warning and the worker-side submit validation (irp-integration ≥ 0.2.1 `validate_unique_edms`) is the backstop, whose specific message is surfaced on the entity page and package-card member row.

**Rationale**: irp-integration 0.2.1 validates uniqueness at submit time — an overridden warning can no longer produce the duplicate the analyst asked for; it fails minutes later in the worker with a graceless generic error (issue #17). Duplicate names also break the poller's by-name exposureId resolution. Surfacing the collision at save time is strictly better; failing open preserves availability during a Risk Modeler outage.

**Alternatives considered**: (a) **Non-blocking warning with a "use anyway" override** — the original R8 decision, **superseded 2026-07-27**: it predates 0.2.1's submit-side uniqueness validation, which turned the override into a delayed failure rather than a real choice. (b) Auto-suffix — still rejected: mangles the analyst's chosen name. (c) Fail closed when Risk Modeler is unreachable — rejected: would block every save during an outage; the worker backstop already catches the rare real collision.

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

**Decision (revised 2026-07-14, D2)**: RDM import **creates broker analyses in Risk Modeler** (FR-002); the workbench now **does** create local `irp_analysis` rows this iteration — populated when an `import_rdm` job reaches FINISHED, by the `backfill_rdm_analyses` worker calling `search_analyses('sourceRdmName="<rdm>" AND exposureName="<edm>"')` and storing each Moody's `analysisId` + metadata, keyed per (RDM × EDM) pair. RDM delete then enumerates from that local table and loops `delete_analysis` (R6) — no live query at delete time. **`irp_analysis` (+ `irp_analysis_status_kind`) ARE created in the migration this iteration**; `irp_portfolio` / `irp_treaty` remain out. The package card's portfolio summary and analysis **counts still render empty** (FR-023 / D5) — the rows exist for delete-enumeration only, not surfaced yet.

**Rationale**: A reliable synchronous RDM delete needs the exact `analysisId`s; capturing them into `irp_analysis` at import time makes delete a local-table read + `delete_analysis` loop, independent of the RM search still resolving at delete time. Full analysis *tracking* (portfolios, results, treaties) stays a later iteration — only the minimal `irp_analysis` rows needed for delete-enumeration are added now, and counts stay empty (D5) so no card work is pulled in.

**Alternatives considered**: (a) Live-query analyses by `sourceRdmName` at delete time (no local table) — rejected: depends on the RM search resolving correctly at delete time and re-derives ids on every delete; the local `irp_analysis` capture is more robust and idempotent (D2). (b) Full analysis tracking now (portfolios / results / treaties) — rejected: out of scope; only the minimal delete-enumeration subset is added this iteration.

---

## Summary of decisions

| # | Area | Decision |
|---|---|---|
| R1 | IRP library | One `irp_gateway` interface; **methods confirmed vs 0.2.0 (manager-based) — authoritative matrix in worker-poller.md**; source switchable PyPI(0.2.0)/TestPyPI/local via `make irp-*` (default PyPI); prefer single-item calls; fake in CI; **no library change needed this iteration** |
| R2 | Poller | Batch non-terminal `irp_job` by type; single-status `get_*_job`; on terminal backfill + idempotent enqueue; owns reconciler + `submission_retry`; never `poll_*_to_completion` |
| R3 | Queue | `rwb_job` SQL table is the queue of record; Dramatiq wakes the single worker; atomic claim `WHERE status_code='pending'`; heartbeat daemon; reconciler reclaim |
| R4 | Chaining / fan-in | Completion-chaining keyed by `(requestor_type, requestor_id, rwb_job_type)` UNIQUE; idempotent inserts; fan-in via "all siblings terminal?" query under atomic guard — **no counter** |
| R5 | Sync fan-out | One `upload_edm` per EDM; `import_edm` FINISHED → one `upload_rdm` → apply per RDM onto that EDM; per-pair, parallel; **review-only/RDM-only deferred (D3)** — every package has ≥1 EDM |
| R6 | Delete asymmetry | EDM delete async (`submit_delete_edm_job`; getter `get_risk_data_job`); RDM delete **synchronous** (`delete_analysis` per analysis from local `irp_analysis`, no `irp_job`); RDM-before-EDM; RDM→EDM fan-in app-side |
| R7 | Recovery | Idempotent re-sync + per-member retry + source-file replacement; `SUBMISSION FAILED` auto-retried by the batch |
| R8 | Name collision *(amended 2026-07-27 — issue #17)* | `search_edms/rdms` (cached ~30s) → **blocks** Save/Save-and-Sync on a hit; fails open with a warning when RM unreachable (worker submit is the backstop) |
| R9 | Live status | SSE (`sse-starlette`) pushes server-rendered `job_row` fragments; filter = pure URL function; JS-off degrades |
| R10 | Notifications | `notify_analyst` worker actor; configured channel(s) (Teams/email/desktop); per-action completion + per-member failure (not per successful member) |
| R11 | Shared drive | Live read-only listing under `SHARED_DRIVE_ROOT` (traversal-guarded); path stored on the member; no inventory; never mutates broker files |
| R12 | Nav | `irp.edm_library` / `irp.rdm_library` new nodes; Jobs list reuses `workflows.irp_jobs`/`rwb_jobs`, made filterable; shared `submission/package/status/job_type` vocabulary |
| R13 | Scope | `irp_analysis` **tracked locally (D2)** — captured at import via `search_analyses(sourceRdmName+exposureName)` for delete-enumeration; card counts still empty (D5); `irp_portfolio`/`irp_treaty` still out |
