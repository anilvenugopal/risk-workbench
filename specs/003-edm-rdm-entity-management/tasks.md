---
description: "Task list for EDM & RDM Entity Management (incl. Packages) — Iteration 2"
---

# Tasks: EDM & RDM Entity Management (incl. Packages) (Iteration 2)

**Input**: Design documents from `/specs/003-edm-rdm-entity-management/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (data-access.md, http-routes.md, worker-poller.md), quickstart.md

**Tests**: INCLUDED. Article 12 (Test-First, Three Connected Strategies) is a constitutional mandate, and the design docs enumerate specific obligations (plan §Testing, data-model §9, contracts/data-access.md + worker-poller.md "Test obligations"). Test tasks are therefore first-class, written before the implementation they cover.

**Organization**: Tasks are grouped by user story (US1…US7 from spec.md) so each story is an independently testable increment. Shared machinery this iteration is heavy (the SQL-backed queue, the poller, the IRP gateway), so it lives in **Phase 2 Foundational** and blocks all stories.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1…US7 (setup / foundational / polish carry no story label)
- Every task names its exact file path(s)

## Path Conventions

Single server-rendered web app extending the existing `app/` tree (plan §Project Structure). Source under `app/`; tests under `tests/unit`, `tests/sqlserver`, `tests/irp`; schema in `alembic/versions/0001_initial.py`; seeds in `infra/scripts/seed_db.py`.

## Shared-file serialization (read before parallelizing)

These files are touched by several tasks across phases; edits to them are **sequential**, never `[P]` against each other:

- `app/main.py` — router includes (T015, T023, T029, T035, T054)
- `app/poller/run.py` — T013 → T022 → T028 → T034 → T040 → T053
- `app/workers/package_jobs.py` — T021 → T027 → T027a → T039 → T052 → T053
- `app/services/package_sync_service.py` — T033 → T038 → T045
- `app/services/job_query.py` — T044 → T050
- `app/nav/manifest.py` — T055 → T060
- `app/routers/edms.py` — T023 → T058; `app/routers/rdms.py` — T029 → T058
- `app/templates/partials/package_card.html` — T036 → T042 → T047
- `alembic/versions/0001_initial.py` — T004 → T005

> **Re-run ordering (D2/D3 revision, 2026-07-14) — DONE:** T028 and T033 were reset to `[ ]` for the D2/D3 revision while later tasks in their files (T034/T040 in `run.py`; T038/T045 in `package_sync_service.py`) remained `[X]`. Both have now been re-run as a **targeted replacement** — the `import_rdm` poller branch (T028, now enqueues `backfill_rdm_analyses` instead of rolling `irp_rdm.status` up directly) and the `save_and_sync` body (T033, now rejects RDM-only with `EmptyPackageError`) — preserving the later `[X]` additions. See the ⚠️ Re-run notes on T028/T033.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Configuration and error surface the rest of the iteration builds on. No new runtime dependencies (plan §Technical Context — all already in `pyproject.toml`).

- [X] T001 Add Iteration-2 settings to `app/config.py`: `RWB_HEARTBEAT_INTERVAL_SECS`, `RWB_HEARTBEAT_STALE_SECS`, `IRP_SUBMISSION_MAX_RETRIES` (no fixed default — deployment value, FR-029), `POLL_INTERVAL_SECS` (default ~15, FR-027/SC-001), `SHARED_DRIVE_ROOT`, and the notification-channel settings (Teams/email/desktop, R10).
- [X] T002 [P] Add the new Iteration-2 vars to `infra/.env.example`: the `RWB_HEARTBEAT_*`, `IRP_SUBMISSION_MAX_RETRIES`, `POLL_INTERVAL_SECS`, `SHARED_DRIVE_ROOT`, notification-channel, and `IRPClient()` env vars (quickstart §Prerequisites).
- [X] T003 [P] Add Iteration-2 service errors `InvalidSourceFile` (→422) and `JobSubmitError` to `app/services/errors.py` (contracts/data-access.md §errors); name-collision remains a non-blocking warning payload, **not** an error.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, the IRP gateway + fake, and the Article-10 SQL queue / heartbeat / reconciler + Article-11 bridge — the machinery every user story depends on.

**⚠️ CRITICAL**: No user-story work may begin until this phase is complete.

### Schema & seeds (single revision, drop-create-seed — data-model §8)

- [X] T004 Add the six kind tables with inline seeds to `alembic/versions/0001_initial.py`: `irp_job_type_kind`, `irp_job_resource_type_kind`, `rwb_job_type_kind`, `rwb_job_requestor_type_kind`, `rwb_job_status_kind`, `irp_analysis_status_kind` (seed rows per data-model §1/§13 — note **no `delete_rdm` irp_job_type**; `rwb_job_type_kind` includes `backfill_rdm_analyses` (D2); `rwb_job_requestor_type_kind` = `irp_job`/`analyst_request`/`rwb_job`; `irp_analysis_status_kind` = `pending`/`running`/`ready`/`error`).
- [X] T005 Add the entity tables to `alembic/versions/0001_initial.py` in FK order (depends T004): `irp_job` (**without** `irp_portfolio_id`, §2 note), `irp_job_resource`, `rwb_job` (+ `UNIQUE(requestor_type, requestor_id, rwb_job_type)`), `rwb_job_heartbeat`, `irp_analysis` (FKs → `irp_rdm`/`irp_edm`/`package`/`irp_analysis_status_kind`; + `UNIQUE(rdm_id, edm_id, irp_id)` and indexes `(rdm_id, edm_id)`/`(package_id)`, §6a — D2); add the indexes (data-model §8) and the reverse-order downgrade drops. **No `ALTER`** on `irp_edm`/`irp_rdm` (§6). Keep the SQLite unit mirror in sync: add `irp_analysis` + `irp_analysis_status_kind` to `tests/iteration1_mirror.py` (schema + seeds + `EXACT_MATCH_TABLES`) and seed them in the `iteration2_db` fixture, or the schema-drift guard fails.
- [X] T006 [P] Add idempotent `MERGE` seeds for the six new kind tables to `infra/scripts/seed_db.py` (same pattern as the existing kind-seed MERGEs), including `backfill_rdm_analyses` in `rwb_job_type_kind` and the new `irp_analysis_status_kind` (D2).

### IRP gateway + fake (Article 11 / Article 12)

- [X] T007 [P] Define the `irp_gateway` interface in `app/services/irp_gateway.py` — `submit_edm_import`, `submit_rdm_import`, `submit_delete_edm`, `delete_analysis` (synchronous), `search_analyses`, `get_import_job`, `get_risk_data_job` (single-status-check only), `search_edms`, `search_imported_rdms` — thin wrapper over `irp-integration` 0.2.0 (manager-based; confirmed matrix in `contracts/worker-poller.md`); the ONLY module importing it. **No `poll_*_to_completion` (or poll-inside convenience methods) ever wrapped.**
- [X] T008 Implement a fake IRP conforming to `irp_gateway` for the unit tier in `tests/unit/fakes/fake_irp.py` (+ a `conftest.py` fixture that injects it) (depends T007) — Article 12.

### The SQL queue, heartbeat, bridge, poller skeleton (Article 10 / 11)

- [X] T009 [P] Create the Dramatiq broker (redis_url from config) in `app/workers/broker.py`.
- [X] T010 Implement the Article-10 queue in `app/services/rwb_job_service.py` (depends T005): `enqueue_rwb_job` (idempotent insert on the UNIQUE composite key → `None` on dedup hit), `claim_rwb_job` (atomic `UPDATE … WHERE status_code='pending'` → bool), `complete_rwb_job` (in-place succeeded/failed + payload + `completed_at`). All via the `db/` safe path.
- [X] T011 Implement worker runtime helpers in `app/workers/runtime.py` (depends T010): claim wrapper, heartbeat daemon thread upserting `rwb_job_heartbeat` every `RWB_HEARTBEAT_INTERVAL_SECS`, complete wrapper, and the **stub↔real worker-body switch** (FR-048).
- [X] T012 Implement the Article-11 bridge in `app/services/irp_job_service.py` (depends T005): `record_submitted_irp_job` (write `irp_job` status `QUEUED` + `irp_id` + any `irp_job_resource` with `resource_uri` captured at submit; on submit failure write `SUBMISSION FAILED`, `irp_id=null`, FR-029).
- [X] T013 Create the poller skeleton in `app/poller/run.py` (depends T010, T012): the `poll_once` loop shell running one pass per `POLL_INTERVAL_SECS`, the **reconciler** (reset `running` `rwb_job` rows with stale heartbeat to `pending`), and the `submission_retry` batch scaffold (`SUBMISSION FAILED` rows under `IRP_SUBMISSION_MAX_RETRIES`). No `poll_*_to_completion` (Article 11).

### Shared-drive browse (used by US1/US2/US3)

- [X] T014 [P] Implement `app/services/shared_drive.py`: `browse(path)` (live read-only listing under `SHARED_DRIVE_ROOT`, no cached inventory) and `validate_selection(path)` (resolve + confirm within root and is a file, else `InvalidSourceFile`) — FR-008/FR-009/R11.
- [X] T015 Add the browse router `GET /browse` (HTMX fragment, multi-select) in `app/routers/shared_drive.py` + template `app/templates/partials/shared_drive_browse.html`; include the router in `app/main.py` (depends T014).

### Foundational mandate tests (Article 10 / 12)

- [X] T016 [P] Unit-test the `rwb_job` state machine in `tests/unit/test_rwb_job_queue.py` (depends T010, T011, T013): atomic claim returns rowcount 1 then 0; heartbeat upsert (one row per job); reconciler reclaims a stale `running` row to `pending`.
- [X] T017 [P] SQL-Server test in `tests/sqlserver/test_job_tables_migration.py` (depends T005, T006): the extended migration builds the `irp_job`/`rwb_job`/`irp_analysis` families with all FKs + the `rwb_job` UNIQUE key + the `irp_analysis` `UNIQUE(rdm_id, edm_id, irp_id)` + seeds (incl. `backfill_rdm_analyses` in `rwb_job_type_kind` and the `irp_analysis_status_kind` rows — D2); atomic claim returns rowcount 1 then 0 under contention; the idempotent chained insert absorbs a duplicate exactly once.

**Checkpoint**: Schema rebuilds, the queue + heartbeat + reconciler + gateway/fake are in place. User stories can begin.

---

## Phase 3: User Story 1 - Import an EDM from a broker file (Priority: P1) 🎯 MVP

**Goal**: An analyst browses the shared drive, names an exposure file, and imports it as an EDM; the import runs async, the poller mirrors its status, and the EDM reaches *ready* with a Risk Modeler id — no web request ever blocks.

**Independent Test**: Select one `.bak`/`.mdf`/`.csv`, name and import it; confirm a tracked `import_edm` job is created with **no** Risk Modeler call on the request path, the poller mirrors status without blocking, and on success the EDM shows *ready* with `irp_id` recorded — no package or RDM involved.

### Tests for User Story 1 ⚠️ (write first, ensure they fail)

- [X] T018 [P] [US1] Unit-test `edm_service` in `tests/unit/test_edm_service.py`: `import_edm` creates the `irp_edm` (`pending_import`) + enqueues one `upload_edm` with **no** gateway call on the request path (FR-042); `check_name_collision` returns colliding names and never raises/blocks (SC-005); `replace_source_file` updates `source_file_path` + re-enqueues (FR-046); `retry_import` is idempotent (FR-045); `list_edms` applies no row scoping (SC-009); a stale `expected_updated_at` on `replace_source_file` raises `ConcurrencyConflict` (FR-039/SC-010).
- [X] T019 [P] [US1] Unit-test the poller import path (fake IRP) in `tests/unit/test_poller.py`: terminal `FINISHED` backfills `irp_id` + `completed_at` and flips `irp_edm.status` → `ready`; a non-`FINISHED` terminal flips → `error`; `SUBMISSION FAILED` is distinct from `FAILED`; `last_tracked_at` is stamped.

### Implementation for User Story 1

- [X] T020 [US1] Implement `app/services/edm_service.py` (depends T010, T012, T014): `import_edm` (standalone → enqueue `upload_edm` head `requestor_type='analyst_request'`, `requestor_id=irp_edm.id`; validates source via `shared_drive`), `check_name_collision`, `list_edms` (no scoping), `get_edm`, `replace_source_file` (optimistic concurrency, FR-039), `retry_import`.
- [X] T021 [US1] Implement the `upload_edm` Dramatiq actor in `app/workers/package_jobs.py` (depends T011, T012): body calls `irp_gateway.submit_edm_import`, records the `irp_job` (`import_edm`, QUEUED) + `irp_job_resource`; on successful submit flip `irp_edm.status` → `importing` (FR-004); on submit failure writes `SUBMISSION FAILED`. The unit of work is the **submit**, not the remote finish.
- [X] T022 [US1] Implement the import-tracking body of `poll_once` in `app/poller/run.py` (depends T013, T012): batch non-terminal `irp_job` by type, single-status `get_import_job`, mirror `status` in place, and on terminal backfill `irp_id` + `completed_at` + flip `irp_edm.status` (`ready` on `FINISHED`, else `error`).
- [X] T023 [US1] Implement EDM import/detail/recovery routes in `app/routers/edms.py` (depends T020, T015): `GET`/`POST /edms/import`, `GET /edms/{id}`, `POST /edms/{id}/retry`, `POST /edms/{id}/replace-file`, `GET /edms/name-check` — CSRF on every POST (Article 13), returns partials, no Risk Modeler call; include the router in `app/main.py`.
- [X] T024 [P] [US1] Create the EDM import form + detail templates (`app/templates/pages/edm_import.html`, `edm_detail.html`) and the non-blocking `app/templates/partials/name_collision.html` fragment (FR-012/SC-005).

**Checkpoint**: MVP — an analyst can import an EDM and watch it reach *ready*, fully background-tracked.

---

## Phase 4: User Story 2 - Import an RDM (broker results) from a file (Priority: P1)

**Goal**: Import a broker RDM applied to one or more ready EDMs — every apply targets an EDM (RDM-only/review-only is deferred — D3); each import is tracked to a terminal state, and on `import_rdm` FINISHED a `backfill_rdm_analyses` worker captures the broker analyses (`irp_analysis`) for later delete-enumeration (D2).

**Independent Test**: Import an RDM applied to a ready EDM; confirm it produces a tracked import reaching terminal, one apply per applied EDM, broker results treated as one logical source across those EDMs, and (on FINISHED) captured `irp_analysis` rows; an RDM import with no target EDM is rejected (`EmptyPackageError`, FR-016).

### Tests for User Story 2 ⚠️

- [X] T025 [P] [US2] Unit-test `rdm_service` in `tests/unit/test_rdm_service.py`: applied import enqueues one apply per EDM (every apply targets an EDM — D3); an RDM import with no target EDM (`applied_edm_ids=[]`) is rejected with `EmptyPackageError` (FR-016; RDM-only/review-only deferred); collision warning non-blocking; `retry_import` idempotent; `list_rdms` no scoping.

### Implementation for User Story 2

- [X] T026 [US2] Implement `app/services/rdm_service.py` (depends T010, T012, T014): `import_rdm` (enqueue one `upload_rdm` head; the worker fans out to one apply per applied EDM; **≥1 target EDM required — reject a no-EDM import with `EmptyPackageError`; RDM-only/review-only deferred, D3/FR-016**), `check_name_collision`, `list_rdms`, `get_rdm`, `replace_source_file`, `retry_import` (mirrors `edm_service`).
- [X] T027 [US2] Add the `upload_rdm` actor to `app/workers/package_jobs.py` (depends T021): for each applied EDM call `irp_gateway.submit_rdm_import(edm_name=…)` (name-resolved via `search_edms`, Article 2), writing one `irp_job(import_rdm)` per apply; **every apply targets an EDM — no no-EDM/review-only apply path (deferred, D3)**; on successful submit flip `irp_rdm.status` → `importing` (FR-004).
- [X] T027a [US2] Add the `backfill_rdm_analyses` actor to `app/workers/package_jobs.py` (depends T027, T005): on a head keyed to a FINISHED `import_rdm` apply, call `irp_gateway.search_analyses('sourceRdmName="<rdm>" AND exposureName="<edm>"')` and write this pair's `irp_analysis` rows (Moody's `analysisId` + metadata) for delete-enumeration (D2, data-model §6a) — idempotent on `UNIQUE(rdm_id, edm_id, irp_id)`; roll `irp_rdm.status` up to `ready` once all of the RDM's applies are `FINISHED` (combined rollup, worker-poller.md §2). Register it in the actor map + `_BODIES`. Not surfaced on the card — analysis counts stay empty (D5). Add a unit test (data-model §9): FINISHED `import_rdm` → backfill enqueued; the worker writes `irp_analysis` from a fake `search_analyses`; a duplicate backfill is idempotent.
- [X] T028 [US2] Extend `poll_once` in `app/poller/run.py` (depends T022, T027a) to track terminal `import_rdm`: on `FINISHED`, idempotently enqueue a `backfill_rdm_analyses` head (`requestor_type='irp_job'`, `requestor_id=<finished import_rdm irp_job.id>`, carrying `rdm_id`/`edm_id`/`package_id`) — the worker captures `irp_analysis` rows and rolls `irp_rdm.status` up to `ready` once all applies are FINISHED (D2; worker-poller.md §2/§3); on a non-`FINISHED` terminal, flip `irp_rdm.status` → `error`.
  - **⚠️ Re-run note (D2/D3):** `poll_once` already holds the T034 (`import_edm`→`upload_rdm`) and T040 (`delete_edm`→finalize) branches (`[X]`) plus a prior `import_rdm` handler that rolled `irp_rdm.status` up in the poller directly. REPLACE that prior handler with the backfill-enqueue branch above (the poller must NOT flip `irp_rdm.status` → `ready` — that rollup is the worker's job); leave the T034/T040 branches intact.
- [X] T029 [US2] Implement RDM import/detail/recovery routes in `app/routers/rdms.py` (depends T026, T015): `GET`/`POST /rdms/import` (body carries `applied_edm_ids`, **non-empty required — reject an empty selection, review-only deferred, D3**), `GET /rdms/{id}`, `POST /rdms/{id}/retry`, `/rdms/{id}/replace-file`, `GET /rdms/name-check` — CSRF; include in `app/main.py`.
- [X] T030 [P] [US2] Create the RDM import form + detail templates (`app/templates/pages/rdm_import.html`, `rdm_detail.html`), reusing the shared `name_collision` fragment; the form **requires ≥1 applied EDM** (no review-only option — deferred, D3).

**Checkpoint**: Both import shapes work and are tracked; the import/poller machinery is shared and proven.

---

## Phase 5: User Story 3 - Assemble a package and sync it to Risk Modeler (Priority: P1)

**Goal**: An analyst assembles a package (any mix of EDM/RDM members) and chooses Save-and-Sync, which queues one upload per EDM and one apply per (EDM × RDM) pair — each apply waiting only for its target EDM's upload — with the whole sync running off the request path and safe to re-run.

**Independent Test**: Build EDM-only / RDM-only / both packages by browsing + multi-select; confirm the ≥1-member rule, the non-blocking collision warning, and that Save-and-Sync on a both-package queues exactly one `upload_edm` per EDM + one apply per pair (each apply gated on its EDM's upload) — verified against the queued job set; re-running skips ready members.

### Tests for User Story 3 ⚠️

- [X] T031 [P] [US3] Unit-test `package_sync_service` in `tests/unit/test_package_sync_service.py`: Save persists names + runs the collision check + submits nothing; Save-and-Sync enqueues one `upload_edm` per EDM and (via chaining) one apply per (EDM × RDM) pair; idempotent re-sync skips ready/in-flight and re-enqueues only unstarted/errored (SC-013); empty package → `EmptyPackageError` (SC-012); an RDM-only package (no EDM) → `EmptyPackageError` (FR-016; every apply targets an EDM, RDM-only/review-only deferred D3); a stale `expected_updated_at` on `save_package` raises `ConcurrencyConflict` (FR-039/SC-010).
- [X] T032 [P] [US3] Unit-test completion-chaining + fan-in idempotency in `tests/unit/test_job_chaining.py` (Article 2 mandate): `import_edm` `FINISHED` enqueues exactly one `upload_rdm` fanning out to one apply per RDM; a duplicate/repeated trigger never double-enqueues (SC-014); per-pair fan-out (an apply gated only on its target EDM's upload, not a global head).

### Implementation for User Story 3

- [X] T033 [US3] Implement `app/services/package_sync_service.py` (depends T010, T020, T026): `save_package` (≥1-member invariant, per-member collision, optimistic concurrency), `save_and_sync` (record pending work + return immediately; enqueue `upload_edm` heads `requestor_type='analyst_request'`, `requestor_id=package_id`; **reject an RDM-only package (no EDM) with `EmptyPackageError` — every apply targets an EDM, RDM-only/review-only deferred D3/FR-016**; idempotent on the dedup key, FR-044), `retry_member` (FR-045).
  - **⚠️ Re-run note (D2/D3):** `package_sync_service.py` already holds `delete_package` (T038) and `get_package_cards` (T045) (`[X]`). This task REPLACES the `save_and_sync` body — drop the old review-only `[None]`-target branch, add the RDM-only `EmptyPackageError` guard — and revises `save_package`/`retry_member`; do NOT remove the T038/T045 functions.
- [X] T034 [US3] Extend `poll_once` in `app/poller/run.py` (depends T028, T027): on `import_edm` `FINISHED` idempotently enqueue the `upload_rdm` head (`requestor_type='irp_job'`, `requestor_id=<finished irp_job.id>`), which fans out per-pair — each RDM apply gated only on its target EDM's upload (FR-015/FR-043).
- [X] T035 [US3] Implement package routes in `app/routers/packages.py` (depends T033, T015): `GET /submissions/{id}/packages/new` (modal), `POST /submissions/{id}/packages` (Save), `POST /packages/{pid}` (edit), `POST /packages/{pid}/sync` (Save-and-Sync — enqueue + return queued card), `POST /packages/{pid}/members/{mid}/retry` — CSRF + read-only submission gate (`SubmissionClosed`→409, FR-025); include in `app/main.py`.
- [X] T036 [P] [US3] Create `app/templates/partials/package_modal.html` (browse + multi-select + per-member name + actions, Alpine.js), `member_row.html` (per-member retry / replace-file control), and a **basic** `package_card.html` (queued/syncing state) — the card is enriched in US5.

**Checkpoint**: The headline capability — real package sync — works end-to-end against the gateway/fake, off the request path, idempotently.

---

## Phase 6: User Story 4 - Delete a package and its Risk Modeler members (Priority: P2)

**Goal**: Delete removes members RDM-before-EDM — RDM removals synchronous (no `irp_job`), EDM removals async `delete_edm` jobs — and, once no live members remain, soft-deletes the members and the package (never hard-delete).

**Independent Test**: Sync a both-package then delete it; confirm removals run RDM-before-EDM, `delete_edm` is enqueued only when all RDMs are `deleted`, a duplicate `delete_rdm` success never double-enqueues, and the package + members soft-delete exactly once with zero hard-deletes.

### Tests for User Story 4 ⚠️

- [X] T037 [P] [US4] Unit-test delete ordering + fan-in in `tests/unit/test_delete_ordering.py`: `delete_rdm` reads the RDM's `irp_analysis` rows and loops `irp_gateway.delete_analysis` synchronously (stamping `deleted_at`; a re-run skips already-`deleted_at` rows — D2), writing **no** `irp_job`; `delete_edm` writes a pollable `irp_job`; `delete_edm` enqueued only when all package RDMs `deleted`; duplicate `delete_rdm` success does not double-enqueue; package soft-delete fires once (SC-007). (Seed `irp_analysis` rows for the RDM in the fixture, since backfill does not run in this test.)

### Implementation for User Story 4

- [X] T038 [US4] Add `delete_package` to `app/services/package_sync_service.py` (depends T033): enqueue reverse-order removals — one `delete_rdm` head per RDM (or one `delete_edm` head per EDM when the package has no RDMs); return immediately; no hard-delete path anywhere (FR-019/FR-021).
- [X] T039 [US4] Add the `delete_rdm` + `delete_edm` actors to `app/workers/package_jobs.py` (depends T027a, T028, T012 — `delete_rdm` reads the `irp_analysis` rows backfill populates, D2): `delete_rdm` = **synchronous** loop of `irp_gateway.delete_analysis(analysis_id)` over the RDM's `irp_analysis` rows (`WHERE rdm_id=:r AND deleted_at IS NULL`), stamp their `deleted_at` + set `irp_rdm.status='deleted'`, then app-side RDM→EDM fan-in (when all package RDMs `deleted`, idempotently enqueue `delete_edm` heads); `delete_edm` = atomic `delete_pending` guard → `submit_delete_edm(exposure_id)` → write `irp_job(delete_edm)`.
- [X] T040 [US4] Extend `poll_once` in `app/poller/run.py` (depends T034): on `delete_edm` `FINISHED`, run the idempotent package-finalize fan-in — soft-delete the package + its members when no live members remain (FR-021/SC-014).
- [X] T041 [US4] Add `POST /packages/{pid}/delete` to `app/routers/packages.py` (depends T035, T038): CSRF + read-only gate; enqueue removals; return the deleting-state card.
- [X] T042 [P] [US4] Extend `app/templates/partials/package_card.html` (depends T036) with the deleting-state rendering.

**Checkpoint**: Delete works with the correct asymmetric ordering and idempotent soft-delete finalize.

---

## Phase 7: User Story 5 - See packages on the submission via package cards (Priority: P2)

**Goal**: The submission detail shows one full-width card per package — upload progress, EDM + RDM status chips, source file path(s), and all/active/failed job counts that link to a pre-filtered Jobs list — with portfolio/analysis areas empty for now.

**Independent Test**: On a submission with a synced package, confirm the card shows progress, both chips, source paths, correct all/active/failed counts scoped to that package's members, empty portfolio/analysis areas, and that a count links to the package-filtered Jobs list; on a closed submission the actions are read-only.

### Tests for User Story 5 ⚠️

- [X] T043 [P] [US5] Unit-test package-card data in `tests/unit/test_package_cards.py`: `package_job_counts` returns all/active/failed scoped to the package's members; `get_package_cards` exposes both status chips + source paths, renders portfolio/analysis empty (no error), carries no rolled-up package status (FR-018); create/sync/delete blocked on a COMPLETED/CANCELLED submission (SC-011).

### Implementation for User Story 5

- [X] T044 [P] [US5] Create `app/services/job_query.py` with `package_job_counts(package_id)` — all/active/failed over `irp_job` + `rwb_job` scoped to the package's members (FR-023/FR-024) (depends T005).
- [X] T045 [US5] Add `get_package_cards(submission_id)` to `app/services/package_sync_service.py` (depends T044, T038): per-package card data — upload progress, member EDM + RDM status chips, source path(s), job counts; portfolio/analysis empty (R13); no rolled-up package status (FR-018).
- [X] T046 [US5] Edit `app/routers/submissions.py` + `app/templates/pages/submission_detail.html` (depends T045, T036) to render one full-width card per package (replacing the Iteration-1 placeholder), inheriting the read-only status gate.
- [X] T047 [P] [US5] Build the full `app/templates/partials/package_card.html` (depends T042) — upload progress, EDM + RDM status chips, source path(s), all/active/failed counts each deep-linking to the package-filtered Jobs list (FR-024) — and add `app/static/css/packages.css` (cards/chips/progress via ITCSS tokens, Article 9).

**Checkpoint**: The analyst's day-to-day window into each package is legible on the submission.

---

## Phase 8: User Story 6 - Monitor and filter jobs, and be notified on completion (Priority: P2)

**Goal**: A URL-query-string-filtered Jobs list with clearable chips and live (SSE) status, plus notifications — one per terminal analyst action (standalone import / sync / delete) and one per member failure, never one per successful member.

**Independent Test**: Filter the Jobs list via the URL (submission/package/status/job_type); confirm exactly the matching jobs + chips, that refresh/bookmark/back-forward preserve the filter, that a job advances live without refresh, and that a multi-member sync yields exactly one action-completion notification (plus one per failed member).

### Tests for User Story 6 ⚠️

- [ ] T048 [P] [US6] Unit-test filter parsing in `tests/unit/test_jobs_filter.py`: the shared `submission/package/status/job_type` vocabulary parses from the query string as bound predicates; unknown params ignored; no row scoping (SC-008/SC-009).
- [ ] T049 [P] [US6] Unit-test notification granularity in `tests/unit/test_notifications.py`: a multi-member sync where every member succeeds enqueues exactly ONE `action_complete` `notify_analyst` (not one per member); a failed member enqueues one `member_failure`; a repeated terminal trigger duplicates neither (idempotent on the action / member key) — FR-030/SC-003.

### Implementation for User Story 6

- [ ] T050 [US6] Add `list_jobs(submission/package/status/job_type)` to `app/services/job_query.py` (depends T044): the filtered union of `irp_job` + `rwb_job`, unknown params ignored, no scoping (FR-032/FR-033).
- [ ] T051 [US6] Implement `app/services/notification_service.py` — `notify(notice_kind, ref, outcome, actor_id)` dispatching `action_complete` (one per terminal analyst action) or `member_failure` (one per failed member) to the configured channel(s) (R10); never one per successful member.
- [ ] T052 [US6] Add the `notify_analyst` actor to `app/workers/package_jobs.py` (depends T051): claim the `notify_analyst` `rwb_job` and dispatch via `notification_service.notify`.
- [ ] T053 [US6] Wire notification enqueue points across `app/poller/run.py` + `app/workers/package_jobs.py` (depends T040, T052): member-failure `notify_analyst` (`requestor_type='irp_job'`) on `FAILED`/exhausted `SUBMISSION FAILED`; action-completion fan-in (`requestor_type='analyst_request'`, `requestor_id=`the action anchor — package for sync/delete; the imported entity's own id for a standalone import (per-import this iteration; multi-file batch grouping deferred, no batch id)) when the action's member set is fully terminal; the delete action-completion on package soft-delete; and the `submission_retry` batch parking a row at the configured max + enqueuing a member-failure notify (FR-029/FR-047).
- [ ] T054 [US6] Implement `app/routers/jobs.py` (depends T050): `GET /workflows/irp-jobs` + `/workflows/rwb-jobs` (query-string filters + clearable chips, same code path full-page or partial), and `GET /workflows/*-jobs/stream` SSE pushing server-rendered `job_row` fragments as the poller advances (FR-036/R9); include in `app/main.py`. The SSE generator learns of poller-driven changes by re-querying the filtered `job_query` each ~`POLL_INTERVAL_SECS` and emitting changed rows (cross-process — no shared in-process state; Redis pub/sub is a documented upgrade), bounded by SC-001 (R9).
- [ ] T055 [US6] Wire the real `workflows.irp_jobs` / `workflows.rwb_jobs` nav nodes (elevate the Iteration-0 stubs to the filterable lists) in `app/nav/manifest.py` (Article 1).
- [ ] T056 [P] [US6] Create `app/templates/pages/jobs.html`, `app/templates/partials/job_row.html` (SSE swap target), `filter_chips.html`, and `app/static/css/jobs.css` (job-status pills / filter chips via tokens).

**Checkpoint**: All async work is observable, shareable via URL, live, and pushed on completion/failure at the right granularity.

---

## Phase 9: User Story 7 - Browse the global EDM and RDM libraries (Priority: P3)

**Goal**: EDM and RDM libraries list every entity across all submissions to every analyst (no scoping), offer the same import entry point, and show each entity's import status.

**Independent Test**: Seed EDMs/RDMs under submissions owned by different analysts; confirm both libraries list all of them for any analyst, expose import, and show import status.

### Tests for User Story 7 ⚠️

- [ ] T057 [P] [US7] Unit-test the libraries in `tests/unit/test_libraries.py`: `list_edms()`/`list_rdms()` with no filter return every entity regardless of owning submission/analyst (SC-009).

### Implementation for User Story 7

- [ ] T058 [US7] Add the library routes `GET /edms` and `GET /rdms` to `app/routers/edms.py` and `app/routers/rdms.py` (depends T023, T029): list all entities (no scoping), import entry point, per-entity import status (FR-037/FR-038).
- [ ] T059 [P] [US7] Create `app/templates/pages/edm_library.html` and `rdm_library.html` (list + import affordance + status column).
- [ ] T060 [US7] Add the `irp.edm_library` + `irp.rdm_library` nodes under the `irp` rail root in `app/nav/manifest.py` (depends T055) — breadcrumb/active-state derive from position (Article 1/R12).

**Checkpoint**: All seven stories independently functional.

---

## Phase 10: Polish & Cross-Cutting Concerns

- [ ] T061 Run `make db-rebuild` and confirm a clean drop-create-seed with all new tables + seeds present (quickstart §1).
- [ ] T062 Run `pytest tests/unit` and confirm the full unit tier (fake IRP) is green (quickstart §2).
- [ ] T063 Run `pytest tests/sqlserver --run-sqlserver` and confirm the migration + atomic-claim + idempotent-insert tier is green (quickstart §3).
- [ ] T064 [P] Guard-test that `poll_*_to_completion` appears nowhere in `app/poller/` and no `customer`/scope construct exists on any EDM/RDM/package/job (Article 11 / Article 6 / FR-041) — add to `tests/unit/test_architecture_guards.py`.
- [ ] T065 [P] ITCSS token audit of `app/static/css/packages.css` + `jobs.css` — no hardcoded hex, tokens layered correctly (Article 9).
- [ ] T066 Run the quickstart §5 manual walkthrough (import → assemble → sync → cards → jobs/notify → delete → libraries → closed-submission gate) and confirm SC-001…SC-014.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup; **blocks all user stories**.
- **User stories (Phases 3–9)**: all depend on Foundational. Priority order P1 (US1→US2→US3) → P2 (US4→US5→US6) → P3 (US7). US3 depends on US1+US2 services; US4/US5 depend on US3; US6 depends on US5 (`job_query`); US7 depends on US1/US2 routes.
- **Polish (Phase 10)**: depends on all targeted stories.

### Critical cross-story dependencies (beyond "after Foundational")

- US2 analysis backfill (D2): the `backfill_rdm_analyses` actor (T027a) and the poller branch (T028) populate `irp_analysis` on `import_rdm` FINISHED; US4 `delete_rdm` (T039) reads those rows to enumerate analyses to delete.
- US3 (T033) uses `edm_service`/`rdm_service` (T020/T026); its chaining (T034) extends the US2 poller (T028) and uses the US2 `upload_rdm` actor (T027).
- US4 extends US3's service (T038←T033), worker (T039←T027, plus T027a/T028 — reads the backfilled `irp_analysis` rows), poller (T040←T034), routes (T041←T035), card (T042←T036).
- US5 `get_package_cards` (T045) extends US4's service (T038); the full card (T047) extends US4's card (T042).
- US6 `list_jobs` (T050) extends US5's `job_query` (T044); notifications (T053) extend US4's poller (T040) + the notify actor (T052).
- US7 library routes (T058) extend US1/US2 routers; the nav nodes (T060) extend US6's manifest edit (T055).

### Within each user story

Tests (listed first) are written and failing before implementation; services before workers/poller before routers before templates; shared-file edits serialized per the "Shared-file serialization" list above.

---

## Parallel Opportunities

- **Setup**: T002, T003 in parallel (T001 first — others reference it).
- **Foundational**: T006, T007, T009, T014 in parallel; T008 after T007; T010→T011, T012, T013 form the queue chain; T016, T017 in parallel once their deps land.
- **Within a story**: the `[P]` test tasks and the `[P]` template/CSS tasks run alongside the service/worker/poller work (different files). E.g. US1: T018, T019, T024 in parallel with T020/T021/T022.
- **Across stories**: once Foundational is done, the two P1 import stories US1 and US2 can be built in parallel by two developers (disjoint service/router/template files); they converge at US3.

## Parallel Example: User Story 1

```bash
# Tests + presentation (different files) alongside the service/worker/poller build:
Task: "T018 Unit-test edm_service in tests/unit/test_edm_service.py"
Task: "T019 Unit-test poller import path in tests/unit/test_poller.py"
Task: "T024 EDM import/detail templates + name_collision fragment"
# Then the sequential core (shared poller/worker files):
Task: "T020 edm_service.py" → "T021 upload_edm actor" / "T022 poller body" → "T023 edms router + main.py"
```

---

## Implementation Strategy

### MVP first

1. Phase 1 Setup → Phase 2 Foundational (the queue/poller/gateway spine — **blocks everything**).
2. Phase 3 US1 — import an EDM to *ready*, fully background-tracked. **Stop and validate** (the irreducible MVP slice per spec).
3. Demo the import + poller loop against the fake IRP.

### Incremental delivery (by priority)

- **P1 core**: US1 → US2 → US3 (import both entity types, then real package sync — the headline). Deliverable: an analyst can import and sync a real package.
- **P2**: US4 (delete) → US5 (cards) → US6 (jobs list + notifications). Deliverable: full lifecycle + observability.
- **P3**: US7 (libraries). Deliverable: cross-submission browse.
- **Polish**: rebuild + all test tiers green + architecture guards + quickstart walkthrough.

### Stub-first option (FR-048)

Per worker-poller.md §6, the package UI (US3–US5) MAY be built first against heartbeat stubs (T011's stub↔real switch): the `rwb_job_type`s and the chaining/fan-in shape are identical; wiring real Risk Modeler is a change to the worker bodies (T021/T027/T039) + `irp_gateway` (T007) alone — no orchestration change.

---

## Notes

- `[P]` = different files, no incomplete-task dependency; consult the **Shared-file serialization** list before parallelizing.
- Every state-changing route carries CSRF (Article 13); no route applies row-level scoping (Article 6).
- All SQL goes through the `db/` safe path; cross-table chaining writes run in one worker/poller-owned `conn.begin()` (contracts/data-access.md).
- `poll_*_to_completion` is forbidden everywhere; the poller uses single-status `get_*_job` only (Article 11) — guarded by T064.
- Commit after each task or logical group; stop at any checkpoint to validate a story independently.
