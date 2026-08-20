# Implementation Plan: EDM & RDM Entity Management (incl. Packages) (Iteration 2)

> **Superseded in part:** `specs/006-package-retirement/` owns the replacement
> schema, routes, workers, and standalone RDM import design.

**Branch**: `003-edm-rdm-entity-management` | **Date**: 2026-07-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-edm-rdm-entity-management/spec.md`

---

## Summary

Iteration 2 makes the workbench **do real work against Moody's Risk Modeler (IRP)** for the first time. On the package *structure* Iteration 1 delivered, this iteration adds the behavior that fills it: an analyst imports **EDMs** (exposure) and **RDMs** (broker results) from the read-only shared drive into Risk Modeler, assembles them into **packages**, and **syncs** or **deletes** those packages against Risk Modeler with real operations. Because those operations run for minutes on an external platform, the iteration also delivers the machinery that makes them trustworthy: a background **poller** that mirrors each `irp_job` status (single-status-check only), a SQL-backed **`rwb_job`** work queue driven by a single Dramatiq worker, completion **notifications**, a URL-query-string-filtered **Jobs list**, per-package **cards** on the submission detail, and the global **EDM/RDM libraries**.

The design question that gated the real sync/delete paths — **A21: how a completed Risk Modeler job triggers the next queued app-side job across the `irp_job`/`rwb_job` boundary** — was resolved in the spec (Clarifications → A21) and is made concrete here: member operations are queued as `rwb_job` rows; workers perform every Risk Modeler call; the poller writes the dependent head `rwb_job` when it observes an async `irp_job` reach `FINISHED`; fan-in is an idempotent "are all siblings terminal?" query guarded by an atomic status transition — never a counter. Delete is **asymmetric**: EDM delete is an async, polled `irp_job` (`delete_edm`); **RDM delete is synchronous** (the `delete_rdm` worker removes the RDM's analysis entities inline, with no `irp_job`), so the RDM→EDM fan-in is detected app-side on worker success rather than by the poller.

Technical approach: extend the existing FastAPI + Jinja2 + HTMX stack with import/package/jobs routers and services; light up the poller stub (`app/poller/run.py`) and the empty worker package (`app/workers/`); add the `irp_job` / `rwb_job` family of tables to the single `0001_initial.py` revision (drop-create-seed). Risk Modeler is reached only through `irp-integration` — submit on the request path is deferred to workers (Article 11 permits this), and polling/result work runs only in the poller and workers, never the web layer. The package UI MAY first be built against short heartbeat stubs; the stub and the real worker share the same `rwb_job_type`s, so wiring real Risk Modeler is a change to the worker body alone (FR-048).

> **Scope update — US6 descoped (2026-07-15).** User Story 6 (the URL-query-string-filtered Jobs list + completion/failure notifications) is deferred out of this iteration; see spec.md → Clarifications → "US6 (Jobs list + notifications) descoped". The async spine (poller, `irp_job`/`rwb_job` queue, completion-chaining/fan-in, package-card job counts) is unchanged and still built — what defers is the *observability surface* on top of it. **Not built this iteration:** `app/services/notification_service.py`, `app/routers/jobs.py` (+ SSE stream), the `notify_analyst` actor, `job_query.list_jobs`, the `workflows.*` Jobs-list nav elevation, the jobs templates/CSS (`jobs.html`, `job_row.html`, `filter_chips.html`, `jobs.css`), and `tests/unit/test_jobs_filter.py` / `test_notifications.py`. **FR-030–FR-036, SC-003, and SC-008 defer with it.** FR-029/FR-047 (automatic submit-side retry + park as `SUBMISSION FAILED`) stay in scope as foundational reliability; only the notify-on-park step defers. The file tree, testing bullets, and constitution notes below describe the full Iteration-2 design — treat the US6 entries listed here as deferred, not removed.

---

## Technical Context

**Language/Version**: Python 3.12 (inherited from Iterations 0–1)

**Primary Dependencies** (all already declared in `pyproject.toml` — no new deps):
- `fastapi` + `uvicorn[standard]` — web server; `jinja2` + HTMX — server-rendered partials
- `sse-starlette` — the live-status transport (SSE) that pushes Jobs-list status changes (Iteration 0 scaffold, first *used* here)
- `dramatiq[redis]` + `redis` — worker process + broker for `rwb_job` execution (SQL table remains the queue of record, Article 10)
- `sqlalchemy>=2.0` (Core only) + `pyodbc` (ODBC Driver 18) — engine/pool via the `db/` package
- `alembic` — WORKBENCH schema (single `0001_initial.py` revision)
- **`irp-integration[databridge]`** — the sole path to Risk Modeler; source-switchable across PyPI `0.2.0` (production default), TestPyPI (`0.2.1`/`…dev`), and a local editable checkout via uv dependency groups (`make irp-pypi | irp-testpypi | irp-local`). `IRPClient()` reads all config from env vars. **Confirmed against 0.2.0 on 2026-07-14 — the library is manager-based (`client.edm/.rdm/.import_job/.risk_data_job/.analysis`); the method/request-body matrix is in `contracts/worker-poller.md` (research R1 points to it). No library change is on the Iteration-2 critical path.**
- `itsdangerous`, `bcrypt`, `msal`, `python-multipart` — auth (reused, unchanged); Alpine.js — package-modal / browse / filter-chip client slivers only

**Storage**: SQL Server 2022 (`rwb_workbench`) — every Iteration-2 table lives in the `WORKBENCH` connection. **No EXPOSURE/LOSS/DATABRIDGE access this iteration** (results retrieval + repositories are Iteration 6+; DataBridge is never touched by this app). Risk Modeler holds the real EDM/RDM/analysis entities; the workbench stores only tracking rows.

**Testing**:
- `pytest tests/unit` — SQLite via `db.register_engine`: the service layer, the **prerequisite gate** and **completion-chaining/fan-in idempotency** (Article 12 mandate), the **`rwb_job` claim / heartbeat / reconciler** state machine (Article 12 mandate), the per-pair sync sequencing, idempotent re-sync / per-member retry / source-file replacement, jobs-list filter parsing, and a **fake IRP** implementing the `irp-integration` interface for the poller/worker paths.
- `pytest tests/sqlserver --run-sqlserver` — real driver: the extended migration builds the `irp_job`/`rwb_job` families with FKs and kind seeds; the atomic `rwb_job` claim (`UPDATE … WHERE status_code='pending'`) and idempotent chained-insert on `UNIQUE(requestor_type, requestor_id, rwb_job_type)`.
- `pytest tests/irp --run-irp` — opt-in sandbox IRP: real submit + single-status-check `get_*` round-trips for import and EDM delete, and the synchronous RDM-delete call.

**Target Platform**: Linux server (WSL2 native dev: uvicorn + poller + Dramatiq worker + Redis + SQL Server container; mirrors the `linux-box` / `sqlserver` split).

**Project Type**: Server-rendered web application (FastAPI + Jinja2 + HTMX) with two out-of-process background components (poller, Dramatiq worker). Single project; extends the existing `app/` tree.

**Performance Goals**:
- Every Risk Modeler **submit is deferred to a worker**; no web request blocks on Risk Modeler I/O (SC-002). Save-and-Sync / Delete request path only inserts `rwb_job` head rows and returns — target < 300 ms p95 for a 50-member package.
- Poller pass: one single-status-check `get_*` per non-terminal `irp_job`, batched by type, one pass per `POLL_INTERVAL_SECS` (default ~15 s); a pass MUST NOT block on any `poll_*_to_completion` (Article 11 / FR-027).
- Jobs-list render (filtered, with counts): < 300 ms p95; live status via SSE within one poll interval (~15 s, SC-001).

**Constraints**:
- **IRP discipline (Article 11):** the web layer never calls `get_*` / `poll_*_to_completion` / result-retrieval; the poller uses single-status-check `get_*_job` only; `poll_*_to_completion` is forbidden everywhere. Submit is permitted on the request path but is deliberately deferred to workers here.
- **Queue discipline (Article 10):** `rwb_job` is the SQL-backed queue with a single worker by default; atomic claim; heartbeat + reconciler for stale `running` rows retained regardless of concurrency.
- **Status writes:** `submission.status_code` stays the *only* event-sourced status (Article 4); `irp_job.status`, `rwb_job.status_code`, `irp_edm.status`, `irp_rdm.status` are **updated in place**. `irp_job.status`/`irp_edm.status`/`irp_rdm.status` are plain VARCHAR external-status mirrors (Article 3 carve-out); every `*_type`/`*_status_code` on `rwb_job`/`irp_job` is a kind table.
- **No row-level security (Article 6):** no `customer_id`, no `apply_scope`, no scope on EDM/RDM/package/job; libraries show every entity to every analyst; ownership reaches a submission only transitively through the package.
- All SQL via the `db/` safe bound-parameter path (Article 7); the trusted-script path is **not** used this iteration (it is for EXPOSURE/LOSS external sources, Iteration 6+). CSRF on every state-changing route; function-level role gating server-side (Article 13).
- Shared drive is **read-only** — browsing is a live directory listing; the app never writes/moves/deletes broker files (FR-008/FR-009). UUID PKs generated app-side (`uuid4()`) so the same code runs on SQLite and SQL Server.

**Scale/Scope**: ~10–30 internal analysts; a package can hold 50+ members; a worldwide sync fans out to one `upload_edm` per EDM and one apply per (EDM × RDM) pair. ~11 new tables (5 entity incl. `irp_analysis` + 6 kind), ~9 kind seeds, ~6 new services, ~4 new routers, the poller + worker bodies, the EDM/RDM libraries + Jobs list + package cards, folded into the single Alembic revision.

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Article | Title | Status | Notes |
|---------|-------|--------|-------|
| 1 | Navigation Manifest Is the One Versioned Source of Truth | ✅ | New destinations (EDM library, RDM library) and the now-real Jobs list are added as manifest nodes + handler + template. The Jobs list reuses the existing `workflows.irp_jobs` / `workflows.rwb_jobs` nodes (made real + filterable); EDM/RDM libraries are new nodes under the `irp` rail root (research R12). Rail/sidebar/breadcrumb/active-state/RBAC/search inherited — no scattered config. |
| 2 | Sequencing Is Derived, Not Stored | ✅ | **Central to A21.** No stored DAG/stage machine. "What's next" is the prerequisite gate computed in code — the idempotent "are all siblings terminal?" query (fan-in) + per-pair "target EDM upload FINISHED?" check (fan-out). Coupling is name-based: `upload_rdm` resolves its target EDM via `search_edms` at submit time; `delete_rdm` resolves analyses from the local `irp_analysis` rows captured at import (`sourceRdmName`+`exposureName`). `rwb_job.(requestor_type, requestor_id)` records a *trigger*, and `created_by_irp_job_irp_id` records lineage — neither is a persisted topology. |
| 3 | Categoricals Are Kind Tables, Never Enums — Except External-Status Mirrors | ✅ | Kind tables: `irp_job_type_kind`, `irp_job_resource_type_kind`, `rwb_job_type_kind`, `rwb_job_requestor_type_kind`, `rwb_job_status_kind`, `irp_analysis_status_kind`. Plain VARCHAR (carve-out): `irp_job.status`, `irp_edm.status`, `irp_rdm.status` — they mirror IRP-controlled vocabularies that can drift; an unknown value must not crash the poller. `irp_job_type` / `rwb_job_type` are **kind tables** (closed, app-defined), per the Article 3 note. |
| 4 | Status Is Event-Sourced with Cached Current | ✅ | `submission.status_code` remains the sole event-sourced status (unchanged from Iteration 1). `irp_job.status`, `rwb_job.status_code`, `irp_edm.status`, `irp_rdm.status` are plain in-place updates; per-transition audit is the deferred general-auditing capability (CR-002), not built now. There is no stored `ERROR` status — failure is `FAILED` / `SUBMISSION FAILED`. `irp_job.last_tracked_at` records active tracking. |
| 5 | Mechanical Follow-up Auto-fires; Judgment Waits for a Click | ✅ | **First iteration this applies.** Mechanical follow-ups auto-fire: import FINISHED → backfill `irp_id` + enqueue the dependent op (`import_edm` FINISHED → `upload_rdm`); all-RDM-removed → enqueue `delete_edm`; last-member-gone → package soft-delete. The judgment steps — which files to import, assembling a package, choosing Save-and-Sync vs Delete — always wait for an explicit analyst click. The auto-vs-click line is explicit per op in the worker/poller contract. |
| 6 | No Row-Level Security; All Authenticated Analysts See All Deals | ✅ | FR-041: no `customer_id`, no `apply_scope`, no scope column on `irp_edm`/`irp_rdm`/`package`/`irp_job`/`rwb_job`. The EDM/RDM libraries list every entity to every analyst (FR-037/SC-009). Ownership reaches a submission only transitively via the package. Roles gate functions, not rows. |
| 7 | One Data-Access Package, Two Paths (`/db`) | ✅ | All app-table SQL (services, poller, worker) goes through the `db.execute*` safe path; the transactional writes (`rwb_job` claim, idempotent chained insert, event-sourced submission status) use `db.get_connection("WORKBENCH")` + explicit `conn.begin()`. The trusted-script path (`db.scripts`) is **not** imported this iteration (no EXPOSURE/LOSS writes yet). |
| 8 | Server-Rendered; No SPA | ✅ | Jinja2 + HTMX master-detail; `hx-boost` top-level nav; real URLs for libraries, Jobs list, submission detail. Alpine.js only for the package modal, shared-drive browse multi-select, and filter chips. The Jobs-list live update is server-pushed **SSE** (`sse-starlette`) swapping server-rendered rows — a live-status transport, not a client-side app (research R9). |
| 9 | Styling Extends ITCSS via Tokens | ✅ | Package cards, EDM/RDM status chips, job-status pills, filter chips, and progress indicators styled via named design tokens layered into the ITCSS layers; no hardcoded hex, no flat append-sheets. |
| 10 | The SQL Table Is the Queue; Single Worker by Default | ✅ | **First iteration this applies.** `rwb_job` is the SQL-backed queue; a single Dramatiq worker by default with plain dequeue; atomic claim (`UPDATE … SET status_code='running' WHERE id=:id AND status_code='pending'`, rowcount 0 → already claimed); heartbeat via daemon thread (`rwb_job_heartbeat`); the stale-`running` reclaim (reconciler in the poller) is retained regardless of concurrency. Concurrency-safe claim + idempotent IRP submission are documented upgrade paths, not default complexity. |
| 11 | IRP Polling and Result Work Behind Interface; Submission on Request Path Permitted | ✅ | **Central to this iteration.** The poller (`app/poller/run.py`) is a standalone process, never imported by a route handler; it uses single-status-check `get_*_job` only — `poll_*_to_completion` is forbidden everywhere. Result/notification work runs in Dramatiq workers, never the web process; `submission_retry` is a single-threaded batch, not a Dramatiq actor. The web layer never calls `get_*` / result-retrieval. Submit is permitted on the request path but is deliberately deferred to workers (FR-042) — within Article 11, which permits but does not require request-path submit. |
| 12 | Test-First, Three Connected Strategies | ✅ | Unit (SQLite) covers the mandated prerequisite gate (Article 2) and `rwb_job` claim/heartbeat/reconciler state machine (Article 10), plus chaining/fan-in idempotency, per-pair sequencing, and recovery. A **fake IRP** implementing the interface backs the poller/worker in default CI; an opt-in `irp`-marked suite hits the sandbox. SQL-Server tier covers the extended migration + atomic claim + idempotent chained insert. |
| 13 | Authentication & Secrets | ✅ | Reuses Iteration-0 auth; CSRF on every state-changing route (import, save, save-and-sync, delete, per-member retry, source-file replace); roles read from DB per request; role gating server-side. Risk Modeler credentials come from env via `IRPClient()` — `RISK_MODELER_BASE_URL` / `RISK_MODELER_API_KEY` / `RISK_MODELER_RESOURCE_GROUP_ID` (no constructor args, no secrets in code/VCS); EDM-import `server_name` defaults to `databridge-1` (workbench config). **S3 upload uses temporary creds returned by Risk Modeler — no ambient AWS credentials; the worker host needs outbound S3 egress only.** Other new config (`RWB_HEARTBEAT_*`, `IRP_SUBMISSION_MAX_RETRIES`, poll interval, notification channel, shared-drive root) is env-sourced. |

**Constitution Check: PASSED — no violations. No Complexity Tracking entries required.**

> Re-check after Phase 1 design: **still PASSED** (see end of plan). The design adds no scoping key and no stored sequence; job status is updated in place (only `submission.status_code` stays event-sourced); chaining/fan-in is a computed, idempotent, name-coupled gate; every Risk Modeler poll/result path stays out of the web layer.

---

## Project Structure

### Documentation (this feature)

```text
specs/003-edm-rdm-entity-management/
├── plan.md              ← this file (/speckit-plan output)
├── research.md          ← Phase 0 output (the concrete design decisions A21 leaves to planning)
├── data-model.md        ← Phase 1 output (irp_job / rwb_job families from DATA_MODEL §5, §8, §13)
├── quickstart.md        ← Phase 1 output (rebuild + test + manual end-to-end walkthrough)
├── contracts/           ← Phase 1 output
│   ├── data-access.md    ← service/repository function contract (edm/rdm/package-sync/job/notify/shared-drive)
│   ├── http-routes.md    ← import / package-modal / sync / delete / jobs-list / libraries route contract
│   └── worker-poller.md  ← the A21 mechanism made concrete: rwb_job worker bodies, poller chaining, IRP interface
├── checklists/
│   └── requirements.md   ← spec quality checklist (created by /speckit-specify; all pass)
└── tasks.md             ← Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root — extends the existing Iteration-0/1 tree)

```text
app/
├── main.py                          # EDIT: include edms / rdms / packages / jobs routers
├── config.py                        # EDIT: RWB_HEARTBEAT_INTERVAL_SECS / _STALE_SECS,
│                                    #       IRP_SUBMISSION_MAX_RETRIES, POLL_INTERVAL_SECS,
│                                    #       SHARED_DRIVE_ROOT, notification channel settings
├── services/
│   ├── errors.py                    # EDIT: add import/sync/browse errors (NameCollision is a
│   │                                #       warning, not an error; add InvalidSourceFile, JobSubmitError)
│   ├── edm_service.py               # NEW: import EDM, name-collision check, list/get,
│   │                                #      replace-source-file + retry, lifecycle status reads
│   ├── rdm_service.py               # NEW: import RDM (applied to ≥1 EDM; RDM-only deferred D3), list/get, retry
│   ├── package_sync_service.py      # NEW: save, save-and-sync (enqueue head rwb_jobs), delete
│   │                                #      (enqueue delete rwb_jobs), per-member retry, idempotent
│   │                                #      re-sync; builds on Iteration-1 package_service
│   ├── rwb_job_service.py           # NEW: the Article-10 queue — enqueue (idempotent composite-key
│   │                                #      insert) / atomic claim / in-place complete of rwb_job
│   ├── irp_job_service.py           # NEW: the Article-11 bridge — record submitted irp_job (+ resource)
│   │                                #      and the poller's in-place irp_job status transitions
│   ├── job_query.py                 # NEW: read-only views spanning BOTH tables — jobs-list query +
│   │                                #      filters; per-package counts (all/active/failed)
│   ├── shared_drive.py              # NEW: live read-only directory listing (no cached inventory)
│   ├── notification_service.py      # NEW: dispatch configured channel(s) (Teams/email/desktop);
│   │                                #      per-action completion + per-member failure, not per success (R10)
│   └── irp_gateway.py               # NEW: thin interface over irp-integration 0.2.0 — submit_edm/rdm_import,
│                                    #      submit_delete_edm, delete_analysis (sync), search_analyses,
│                                    #      get_import_job, get_risk_data_job, search_edms/imported_rdms; fake in CI
├── poller/
│   └── run.py                       # EDIT: implement poll_once — batch non-terminal irp_job by type,
│                                    #       single-status get_*_job, mirror status, on terminal backfill
│                                    #       irp_id + idempotently enqueue dependent rwb_job; reconciler
│                                    #       (stale rwb_job running reclaim) + submission_retry batch
├── workers/
│   ├── broker.py                    # NEW: Dramatiq broker (redis_url from config)
│   ├── package_jobs.py              # NEW: rwb_job actors — upload_edm, upload_rdm, backfill_rdm_analyses,
│   │                                #      delete_rdm, delete_edm (+ app-side fan-in), notify_analyst
│   └── runtime.py                   # NEW: claim/heartbeat/complete helpers + stub↔real worker-body switch
├── routers/
│   ├── edms.py                      # NEW: EDM library + import (browse/name/submit/track) + retry
│   ├── rdms.py                      # NEW: RDM library + import (applied to ≥1 EDM; RDM-only deferred D3) + retry
│   ├── packages.py                  # NEW: package modal, save, save-and-sync, delete, per-member retry,
│   │                                #      source-file replace, package-card partials
│   ├── jobs.py                      # NEW: Jobs list (query-string filters + chips), counts, SSE stream
│   ├── shared_drive.py             # NEW: HTMX directory-browse endpoint (read-only)
│   └── submissions.py               # EDIT: submission detail renders real package cards (not placeholder)
├── nav/
│   └── manifest.py                  # EDIT: add EDM/RDM library nodes; wire the real Jobs list nodes
├── templates/
│   ├── pages/
│   │   ├── edm_library.html         # NEW
│   │   ├── rdm_library.html         # NEW
│   │   ├── jobs.html                # NEW (or elevate workflows_irp_jobs/_rwb_jobs stubs)
│   │   └── submission_detail.html   # EDIT: package cards region
│   └── partials/
│       ├── package_modal.html       # NEW: browse + multi-select + per-member name + actions
│       ├── package_card.html        # NEW: upload progress, EDM/RDM chips, source paths, job counts
│       ├── shared_drive_browse.html # NEW: live directory listing fragment (multi-select)
│       ├── name_collision.html      # NEW: non-blocking collision warning fragment
│       ├── job_row.html             # NEW: one Jobs-list row (SSE swap target)
│       ├── filter_chips.html        # NEW: clearable active-filter chips
│       └── member_row.html          # NEW: package member with per-member retry / replace-file control
├── static/css/
│   ├── packages.css                 # NEW: cards / chips / progress via tokens
│   └── jobs.css                     # NEW: job-status pills / filter chips via tokens

alembic/versions/
└── 0001_initial.py                  # EDIT: add irp_job, irp_job_resource, rwb_job, rwb_job_heartbeat,
                                     #       irp_analysis + 6 kind tables; seed the kind rows (data-model §13)

infra/scripts/
└── seed_db.py                       # EDIT: idempotent MERGE seeds for the new kind tables

infra/
└── .env.example                     # EDIT: new RWB_/IRP_/SHARED_DRIVE_/notification vars

tests/
├── unit/
│   ├── test_edm_service.py          # NEW: import, collision warning, replace-file+retry
│   ├── test_rdm_service.py          # NEW: applied import + RDM-only rejection (D3), retry
│   ├── test_package_sync_service.py # NEW: per-pair fan-out set, idempotent re-sync, empty-package reject
│   ├── test_job_chaining.py         # NEW: completion-chaining + fan-in idempotency (Article 2 gate)
│   ├── test_rwb_job_queue.py        # NEW: atomic claim / heartbeat / reconciler (Article 10 mandate)
│   ├── test_poller.py               # NEW: batch single-status poll + terminal enqueue (fake IRP)
│   ├── test_delete_ordering.py      # NEW: RDM-before-EDM; sync RDM delete; async EDM delete
│   └── test_jobs_filter.py          # NEW: URL-query-string filter parsing + shared vocabulary
└── sqlserver/
    └── test_job_tables_migration.py # NEW: irp_job/rwb_job families build; atomic claim; idempotent
                                     #       chained insert on UNIQUE(requestor_type,requestor_id,type)
```

**Structure Decision**: Single server-rendered web app, extending the `app/` package. Business logic stays in `app/services/` as functions (not classes), plus a thin `irp_gateway` interface so the poller/worker can be tested against a fake IRP (Article 12). The job layer is split by which table it writes — `rwb_job_service` (the Article-10 queue) and `irp_job_service` (the Article-11 async-op bridge) — with the cross-table read views in `job_query`; chaining that touches both tables runs in one worker/poller-owned transaction (see contracts/data-access.md). The two background components live where the constitution requires: the poller in `app/poller/`, the Dramatiq actors in `app/workers/` — never imported by the web layer (Article 11). All new schema folds into the single `0001_initial.py` revision (drop-create-seed); no incremental migration (FR-040).

---

## Complexity Tracking

*No Constitution violations — no entries required.*

---

## Phase 0 — Research

See [research.md](research.md). No `NEEDS CLARIFICATION` unknowns remained after the spec (A21 was resolved there). Research records the concrete decisions the spec/DATA_MODEL leave to planning: the installed-vs-pinned `irp-integration` version reconciliation and the exact submit/get/delete method names (R1); the poller batch + terminal-enqueue shape and forbidden `poll_*_to_completion` (R2); the `rwb_job` claim/heartbeat/reconciler + Dramatiq-vs-SQL-queue reconciliation (R3); completion-chaining + idempotent fan-in without a counter (R4); per-pair fan-out sequencing (R5); the asymmetric delete (async EDM job vs synchronous RDM analysis-entity delete) and the exact synchronous call (R6); idempotent re-sync / per-member retry / source-file replacement (R7); name-collision as a non-blocking warning (R8); SSE live-status transport within "no SPA" (R9); notification channel resolution (R10); shared-drive read-only browse (R11); nav placement of libraries + Jobs list (R12); analyses-not-tracked scope boundary (R13).

## Phase 1 — Design & Contracts

- [data-model.md](data-model.md) — the `irp_job`, `irp_job_resource`, `rwb_job`, `rwb_job_heartbeat`, `irp_analysis` tables and their six kind tables, derived from DATA_MODEL §5 (EDM/RDM lifecycle status now exercised), §6/§6a (`irp_analysis` — D2), and §8, with the status vocabularies, the `UNIQUE(requestor_type, requestor_id, rwb_job_type)` dedup key, the kind-table seeds (§13), and the migration/seed impact folded into `0001_initial.py`.
- [contracts/data-access.md](contracts/data-access.md) — the service function contract (edm / rdm / package-sync / job / notification / shared-drive).
- [contracts/http-routes.md](contracts/http-routes.md) — import, package-modal, save/save-and-sync/delete, per-member retry, source-file replace, Jobs list + filters + SSE, and library routes; CSRF + roles + HTMX conventions.
- [contracts/worker-poller.md](contracts/worker-poller.md) — the A21 mechanism made concrete: each `rwb_job` worker body, the poller's batch/mirror/enqueue loop, the idempotent fan-in, the reconciler + submission-retry, and the `irp_gateway` interface (fake for CI).
- [quickstart.md](quickstart.md) — rebuild + test + end-to-end manual walkthrough (import → assemble → sync → cards → jobs/notify → delete → libraries).
- Agent context updated: the `<!-- SPECKIT START/END -->` block in `CLAUDE.md` now points at this plan.

**Post-Design Constitution Re-check: PASSED.** The data model adds no scoping key and no stored sequence; only `submission.status_code` is event-sourced (job status is in-place); `irp_job`/`irp_edm`/`irp_rdm` `status` honor the Article 3 carve-out while every type/status discriminator is a kind table; the queue is the `rwb_job` SQL table with single-worker + heartbeat/reconciler; and all IRP polling/result work is confined to the poller and workers. No violations introduced.
