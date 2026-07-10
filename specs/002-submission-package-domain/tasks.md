---
description: "Task list for Submission & Package Domain Model (Iteration 1)"
---

# Tasks: Submission & Package Domain Model (Iteration 1)

**Input**: Design documents from `/specs/002-submission-package-domain/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/data-access.md, contracts/http-routes.md, quickstart.md

**Tests**: INCLUDED. The spec mandates automated tests (FR-024 package invariant, FR-029 data-access tests) and the constitution requires test-first coverage (Article 12). Unit tier runs on SQLite via `db.register_engine`; the SQL-Server tier runs with `--run-sqlserver`.

**Organization**: Tasks are grouped by user story (US1–US6, in priority order). Setup and Foundational phases are shared prerequisites; the CR-003 cleanup and the single-revision schema are foundational blockers for every story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US6 (Setup/Foundational/Polish carry no story label)
- Exact file paths are included in every task

## Path Conventions

Single server-rendered web app extending the existing Iteration-0 tree (plan.md "Project Structure"): app code under `app/`, data-access under `db/`, schema in `alembic/versions/0001_initial.py`, tests under `tests/unit/` and `tests/sqlserver/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the Iteration-0 baseline and dev environment. No new dependencies (plan.md Technical Context — everything is inherited).

- [ ] T001 Verify the Iteration-0 baseline is present (auth, shell, nav, `db/` package, `0001_initial.py`) and the dev SQL Server is reachable (`make sqlserver-up`); select **Rebuild** for this schema-affecting iteration per CLAUDE.md "Dev DB Strategy".

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Retire the CR-003 scaffolding (FR-032/FR-033, research R8), fold the nine Iteration-1 tables + seeds into the single revision, and stand up the shared service errors. Nothing in Phase 3+ can run until the schema rebuilds clean and the unit suite is free of scope constructs.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### CR-003 cleanup sweep (FR-032/FR-033 — grep-verified surface, research R8)

- [X] T002 [P] Delete `db/scope.py` (removes `apply_scope`/`scoped_execute`).
- [X] T003 Remove the `from .scope import ...` import, the `__all__` entries, and the `scoped_execute`/`customer_ids` docstring example from `db/__init__.py` (after T002).
- [X] T004 [P] Reword the `db/execute.py` module docstring to drop the `apply_scope`/`customer_id` references (cosmetic, no dangling refs).
- [X] T005 [P] Update `db/README.md`: remove the `db.scope` module row, the RLS `scoped_execute` example, the `scope.py` file listing, and the "multi-tenant tables" line.
- [X] T006 [P] Delete `tests/unit/test_scope.py`.
- [X] T007 [P] Edit `tests/unit/test_db_package.py`: drop the `apply_scope, scoped_execute` import and the scope test block; remove the `customer_id` fixture column; keep the safe-path tests.
- [X] T008 [P] Edit `tests/unit/test_db_config.py`: remove the `apply_scope` test block (~lines 120–162).
- [X] T009 [P] Edit `app/routers/shell.py`: replace the `SELECT COUNT(*) FROM customer` in `home()` with a submission count (or drop the stat).
- [X] T010 [P] Edit `app/templates/pages/home.html`: update or remove the `customer_count` stat to match T009.
- [X] T011 [P] Edit `tests/unit/test_shell_routes.py`: retarget or remove `test_customer_count_in_page` to match T009.

### Schema + seeds (single revision — drop-create-seed, data-model §9)

- [X] T012 Edit `alembic/versions/0001_initial.py`: remove the `customer`, `program`, and `user_customer_access` `create_table` calls and their downgrade drops (FR-032).
- [ ] T013 Edit `alembic/versions/0001_initial.py`: add the nine Iteration-1 tables in FK order — `treaty_type_kind`, `submission_status_kind`, `package`, `submission`, `submission_crm_id`, `submission_status_event`, `submission_package` (composite PK), `irp_edm`, `irp_rdm` — with the self-renewal `CHECK (renews_from_submission_id IS NULL OR renews_from_submission_id <> id)`, indexes on `assigned_analyst_id`/`cedant_name`/`treaty_type_code`/`inception_date`, **no** `UNIQUE(name)`, **no** `customer_id`, nullable `package_id` on `irp_edm`/`irp_rdm`, and plain-`VARCHAR` `status` on the irp tables (data-model §2–§7); downgrade drops in reverse FK order (after T012).
- [ ] T014 Edit `alembic/versions/0001_initial.py`: add in-migration seeds mirroring the `role_kind` seed — `submission_status_kind` (ACTIVE 10 / COMPLETED 20 / CANCELLED 30) and `treaty_type_kind` (the six provisional codes) (data-model §1/§9, FR-010/FR-030) (after T013).
- [ ] T015 Edit `infra/scripts/seed_db.py`: add idempotent `MERGE` seeds for `submission_status_kind` and `treaty_type_kind` (same pattern as the existing `role_kind` MERGE) so a re-seed without a full rebuild stays correct (data-model §9).

### Shared service scaffolding + cleanup verification

- [ ] T016 [P] Create `app/services/errors.py` with the typed service errors `SubmissionClosed`, `ConcurrencyConflict`, `SelfRenewalError`, and `EmptyPackageError` (contracts/data-access.md).
- [X] T017 [P] Create `tests/unit/test_no_scope.py`: assert `db` exposes no `apply_scope`/`scoped_execute`, `import db.scope` fails, and no repository query/source references `customer_id` (SC-010 / FR-032) (after T002–T005).
- [ ] T018 Create `tests/sqlserver/test_submission_migration.py`: assert the migration builds all nine tables + FKs + the self-renewal CHECK and that the seeds are present (data-model §9). (Event-sourced atomicity is added in T032.)
- [ ] T019 Run `make db-rebuild`; verify the nine tables exist, `customer`/`program`/`user_customer_access` do not, and the seeds are present (quickstart §1) (after T012–T015).

**Checkpoint**: Schema rebuilds clean, no scope construct remains, typed errors available — user stories can now begin.

---

## Phase 3: User Story 1 - Register a deal as a submission (Priority: P1) 🎯 MVP

**Goal**: An authenticated analyst creates a deal (name, cedant, treaty type, inception, optional treaty year / directory / renewal link), becomes its owner, and views it on a real detail URL with status ACTIVE.

**Independent Test**: Create a submission with core attributes; confirm it persists, is retrievable by its own id, and shows all captured attributes on its detail view — with no other feature present.

### Tests for User Story 1 ⚠️ (write first, ensure they fail)

- [ ] T020 [P] [US1] In `tests/unit/test_submission_service.py`: `create_submission` writes the submission **and** the initial ACTIVE status event atomically, sets the owner, and yields status ACTIVE; `get_submission` returns full detail with no access restriction; `cedant_suggestions` returns DISTINCT prefix matches (SC-001; contracts test obligations).

### Implementation for User Story 1

- [ ] T021 [US1] Create `app/services/submission_service.py` with `create_submission` (app-side `uuid4()` id; write submission + ACTIVE event in one `db.get_connection("WORKBENCH")` + `conn.begin()` transaction; returns `CreateResult`), `get_submission`, and `cedant_suggestions` (`SELECT DISTINCT cedant_name … LIKE`) — research R2/R6/R11. Also define here (so US2–US5 reuse them, and no story "owns" a shared primitive): the `_require_active(status_code)` read-only gate helper (raises `SubmissionClosed`) and the result DTOs `CreateResult`/`UpdateResult`/`Submission`/`SubmissionRow`/`StatusEvent`/`CrmTag` (contracts/data-access.md).
- [ ] T022 [US1] Edit `app/nav/manifest.py`: add a parameterized `submissions.detail` node under the existing `submissions` rail (Article 1; http-routes Cross-cutting).
- [ ] T023 [US1] Create `app/routers/submissions.py` (`APIRouter`): GET `/submissions/new`, POST `/submissions` (CSRF; 303 → `/submissions/{id}` or detail partial), GET `/submissions/{id}` (404 on unknown id), GET `/submissions/cedant-suggest?q=`; reuse the `_render(request, template, nav_key, extra)` shell pattern (contracts/http-routes.md).
- [ ] T024 [US1] Edit `app/main.py`: register the router with `app.include_router(submissions.router)`.
- [ ] T025 [P] [US1] Create `app/templates/pages/submission_form.html` (create form: name/cedant/treaty-type/inception + optional treaty-year/directory/renewal; CSRF token; cedant autocomplete via HTMX suggest or `<datalist>`) and `app/templates/pages/submission_detail.html` (attributes, status chip, empty CRM/packages placeholders).
- [ ] T026 [P] [US1] Create `app/static/css/submissions.css`: list/detail/status-chip styling via existing ITCSS tokens — no hardcoded hex (Article 9).

**Checkpoint**: A deal can be created, is owned by its creator, is ACTIVE, and is viewable by its own URL. MVP slice functional.

---

## Phase 4: User Story 2 - Find and filter submissions (Priority: P1)

**Goal**: The analyst lands on "My Submissions" (default), toggles to "All", and narrows either view by cedant / treaty type / inception (filters combine). Any analyst can open any deal and reassign its owner.

**Independent Test**: Seed submissions owned by two analysts; confirm the default view shows only the current analyst's, "All" shows every deal regardless of owner, each filter narrows correctly, and reassigning moves a deal between My views without changing visibility.

### Tests for User Story 2 ⚠️

- [ ] T027 [P] [US2] In `tests/unit/test_submission_service.py`: `list_submissions(owner_id=A)` returns only A's; `owner_id=None` returns all; each filter (cedant/treaty_type/inception/treaty_year) and combinations narrow correctly; `reassign_owner` changes the owner and raises `ConcurrencyConflict` on a stale `updated_at` (SC-002/SC-003/SC-011/SC-009).

### Implementation for User Story 2

- [ ] T028 [US2] Add to `app/services/submission_service.py`: `list_submissions` (plain `assigned_analyst_id` predicate for My; no owner predicate for All; AND-combined bound filters — research R7/R10) and `reassign_owner` (reuse the `_require_active` gate helper defined in T021 + `updated_at` concurrency check — research R1/R3; FR-005a).
- [ ] T029 [US2] Add to `app/routers/submissions.py`: GET `/submissions` (All; filter query params), GET `/submissions/mine` (the default landing per FR-020), POST `/submissions/{id}/reassign` (CSRF; 409 on gate/concurrency).
- [ ] T030 [P] [US2] Replace the stub `app/templates/pages/submissions.html` with the master-detail list (My/All toggle; cedant/treaty-type/inception filter controls) and create `app/templates/partials/submission_row.html` (HTMX swap row).

**Checkpoint**: The master-detail list is usable at scale; ownership filters and reassignment work with no row-level access restriction.

---

## Phase 5: User Story 3 - Track a submission's status lifecycle (Priority: P2)

**Goal**: Set COMPLETED / CANCELLED with no precondition, reopen from either closed state, retain full history, enforce read-only on closed deals, and expose no delete.

**Independent Test**: Move a deal ACTIVE → COMPLETED → ACTIVE and another ACTIVE → CANCELLED → ACTIVE; confirm every transition is recorded, history is lossless, closed deals reject edits, and no delete action exists.

### Tests for User Story 3 ⚠️

- [ ] T031 [P] [US3] In `tests/unit/test_submission_service.py`: `set_status` records history for every transition; reopen works from COMPLETED **and** CANCELLED; same-status is a recorded no-op (never errors); `get_status_history` is newest-first and immutable; the read-only gate makes `reassign_owner` raise `SubmissionClosed` when status != ACTIVE; assert no delete function exists (SC-004/SC-005/SC-012).
- [ ] T032 [P] [US3] Extend `tests/sqlserver/test_submission_migration.py`: the event-sourced status transaction is atomic — the `submission_status_event` insert and the cached `submission.status_code` stamp commit and roll back together.

### Implementation for User Story 3

- [ ] T033 [US3] Add to `app/services/submission_service.py`: `set_status` (one `conn.begin()` transaction — INSERT event + UPDATE cached `status_code` + `updated_at` concurrency check; no precondition; same-status no-op — research R2) and `get_status_history` (FR-010–FR-014).
- [ ] T034 [US3] Add to `app/routers/submissions.py`: POST `/submissions/{id}/status` (body `to_status`/`reason`/`updated_at`; reopen from either closed state; returns status-chip + history partial; CSRF). Confirm **no** delete route exists anywhere (FR-014/SC-005).
- [ ] T035 [P] [US3] Edit `app/templates/pages/submission_detail.html`: add the status section + history list + Reopen control, and render read-only (hide edit/reassign/CRM affordances) when status != ACTIVE (FR-015/SC-012).

**Checkpoint**: Status is event-sourced and lossless; closed deals are read-only and reopenable; there is no delete.

---

## Phase 6: User Story 4 - Attach and manage CRM-ID tags (Priority: P2)

**Goal**: Add/edit/remove zero-or-more free-text CRM tags on an ACTIVE deal; zero tags is valid; no format validation; edits blocked once closed.

**Independent Test**: On an existing submission add two tags, edit one, remove one; create a deal with none; confirm the tag set reflects each change and zero tags is valid.

### Tests for User Story 4 ⚠️

- [ ] T036 [P] [US4] In `tests/unit/test_submission_service.py`: add/edit/remove/list CRM tags; zero tags valid; blank/whitespace rejected (not stored); no format validation; duplicate identical tags permitted; all three mutations raise `SubmissionClosed` unless ACTIVE (SC-007/SC-012).

### Implementation for User Story 4

- [ ] T037 [US4] Add to `app/services/submission_service.py`: `add_crm_id`, `edit_crm_id`, `remove_crm_id`, `list_crm_ids` (append-only inserts; reuse the `_require_active` gate; FR-016–FR-018).
- [ ] T038 [US4] Add to `app/routers/submissions.py`: POST `/submissions/{id}/crm-ids`, POST `/submissions/{id}/crm-ids/{tag_id}`, POST `/submissions/{id}/crm-ids/{tag_id}/delete` (CSRF; return the tag-set partial; 409 on gate).
- [ ] T039 [P] [US4] Create `app/templates/partials/crm_tags.html` (tag-set editor fragment; small Alpine sliver) and wire it into `app/templates/pages/submission_detail.html`.

**Checkpoint**: CRM tags are fully manageable on ACTIVE deals and correctly gated on closed deals.

---

## Phase 7: User Story 5 - Coexisting look-alike deals (non-unique identity) (Priority: P3)

**Goal**: Two deals may share every naming attribute; create/rename shows a non-blocking "a similar deal already exists" warning and never blocks. Adds field editing with concurrency + self-renewal guard.

**Independent Test**: Create a submission, then create a second with identical name/attributes; confirm the warning shows and the second deal is created with its own id; confirm a rename to a self-renewal link is prevented and a stale save conflicts.

### Tests for User Story 5 ⚠️

- [ ] T040 [P] [US5] In `tests/unit/test_submission_service.py`: `find_similar` warns on name-match and on attribute-match (cedant+type+inception), returns empty for a genuinely new deal, and `exclude_id` skips the renamed row; `create_submission` with an unconfirmed match returns `CreateResult(created=False, warnings=…)` without writing while `confirmed=True` writes; `update_submission` runs `find_similar` on rename, raises `SelfRenewalError` on a self-link, and raises `ConcurrencyConflict` on a stale `updated_at` (SC-006/SC-009).

### Implementation for User Story 5

- [ ] T041 [US5] Add to `app/services/submission_service.py`: `find_similar` (name OR cedant+type+inception; `exclude_id`); wire the duplicate check into `create_submission` (confirmed flag → CreateResult); `update_submission` (edit mutable fields; `_require_active` gate; `updated_at` concurrency; self-renewal guard; dup-warning on rename) — research R1/R3/R4/R9.
- [ ] T042 [US5] Add to `app/routers/submissions.py`: GET `/submissions/{id}/edit` (carries `updated_at`; 409 gate if not ACTIVE) and POST `/submissions/{id}` (update); non-blocking dup-warning two-step confirm (`confirmed=1`) on create **and** update; 409 conflict banner that preserves input (contracts/http-routes.md).
- [ ] T043 [P] [US5] Create `app/templates/partials/dup_warning.html` (non-blocking look-alike list + Create/Save-anyway control) and add edit mode + conflict banner + hidden `confirmed` field to `app/templates/pages/submission_form.html`.

**Checkpoint**: Look-alike deals coexist with a warning-not-block flow; field edits are concurrency-safe and self-renewal-proof.

---

## Phase 8: User Story 6 - Package bundle structure foundation (Priority: P3)

**Goal**: The package structure (bundle of ≥1 EDM/RDM members, sharable across submissions, soft-removed) exercised through the data-access layer and tests — no analyst-facing package UI (FR-028). Fully independent of US1–US5 (own files; irp tables already in the schema).

**Independent Test**: Through `package_service` and its tests, confirm a package holds multiple EDM/RDM members, a zero-member package is rejected, and one package attaches to two submissions.

### Tests for User Story 6 ⚠️

- [ ] T044 [P] [US6] Create `tests/unit/test_package_service.py`: `create_package([])` raises `EmptyPackageError` (SC-008); `create_package` with members writes the package and stamps `package_id` in one transaction; `package_member_count` counts across `irp_edm` + `irp_rdm` where `deleted_at IS NULL`; `add_member`/`remove_member` work and removing the last member soft-deletes the package; `attach_to_submission` is composite-PK idempotent and one package attaches to two submissions; `detach_from_submission` and `soft_delete_package` behave (FR-023–FR-027/SC-008).

### Implementation for User Story 6

- [ ] T045 [P] [US6] Create `app/services/package_service.py`: `create_package`, `package_member_count`, `add_member`, `remove_member`, `attach_to_submission`, `detach_from_submission`, `soft_delete_package`, `get_packages_for_submission` — the ≥1-member invariant enforced app-side across both child tables, raising `EmptyPackageError` (research R5; FR-024/FR-029). Define the `Package` DTO here (contracts/data-access.md).
- [ ] T046 [US6] Edit `app/templates/pages/submission_detail.html`: add a read-only placeholder list of attached packages via `get_packages_for_submission` — no create/sync/delete (FR-028). (Touches the detail template; sequence after T035/T039.)

**Checkpoint**: Package structure + data-access + tests complete; Iteration 2 can build package behavior on it.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Verify constitution gates and run the end-to-end validation.

- [ ] T047 [P] Confirm every state-changing route in `app/routers/submissions.py` carries a CSRF check via `app/auth/csrf.py` and inherits the Iteration-0 HTMX session-expiry `HX-Redirect` handling (Article 13; http-routes Cross-cutting).
- [ ] T048 [P] ITCSS/token audit of `app/static/css/submissions.css` — tokens only, no hardcoded hex, no flat append-sheets (Article 9).
- [ ] T049 Run `pytest tests/unit` and `pytest tests/sqlserver --run-sqlserver`; confirm all green (quickstart §2–§3).
- [ ] T050 Execute the quickstart.md manual walkthrough (SC-001…SC-012), confirming no delete action exists anywhere and no customer/program/scope construct remains in schema, `db/`, or tests (SC-005/SC-010).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup; **blocks all user stories**. Within it: cleanup (T002–T011) and schema (T012–T014) feed the rebuild (T019); errors (T016) and the regression/migration tests (T017–T018) can proceed alongside.
- **User Stories (Phase 3–8)**: all depend on Foundational (esp. T019 rebuild + T016 errors).
  - **US1 (P1)** — no story dependencies (creates `submission_service.py` — including the shared `_require_active` gate helper and the result DTOs — plus `submissions.py`, the detail template, and router registration).
  - **US2 (P1)** — extends US1's service/router/detail; reuses the `_require_active` gate helper defined in US1 (T021).
  - **US3 (P2)** — `set_status` is independent; its read-only-gate assertion (SC-012) exercises a gated mutation (`reassign_owner`/edit) from another story because `set_status` itself has no precondition (FR-012) — this cross-story test reference is inherent to SC-012, not a build dependency.
  - **US4 (P2)** — reuses the gate helper defined in US1 (T021); otherwise independent.
  - **US5 (P3)** — extends US1's `create_submission` and adds `update_submission`.
  - **US6 (P3)** — **fully independent** of US1–US5 (own service + test files; irp tables already migrated); only T046 touches the shared detail template.
- **Polish (Phase 9)**: depends on all targeted stories being complete.

### Within Each User Story

- The test task is written first and expected to fail before the implementation tasks land (Article 12).
- Service functions before routes; routes before/with templates.
- Tasks that edit the **same file** are sequential (not `[P]`): `submission_service.py` (T021→T028→T033→T037→T041), `submissions.py` (T023→T029→T034→T038→T042), `submission_detail.html` (T025→T035→T039→T046), and `tests/sqlserver/test_submission_migration.py` (T018→T032).

### Parallel Opportunities

- **Foundational**: T002, T004, T005, T006, T007, T008, T009, T010, T011 edit different files and can run together; T016, T017, T018 are independent files. T003 follows T002; T013/T014 follow T012; T019 follows the schema+seed tasks.
- **Per story**: the test task ([P]) and the CSS/template tasks ([P]) run alongside the service work on different files.
- **Cross-story**: **US6 can run in parallel with US1–US5** the moment Foundational completes (disjoint files). US3 and US4 can proceed in parallel once US2's gate helper exists.
- **Polish**: T047 and T048 are independent.

---

## Parallel Example: Foundational cleanup sweep

```bash
# After T001, launch the disjoint-file cleanup edits together:
Task: "Delete db/scope.py"                                   # T002
Task: "Reword db/execute.py docstring"                       # T004
Task: "Update db/README.md"                                  # T005
Task: "Delete tests/unit/test_scope.py"                      # T006
Task: "Edit tests/unit/test_db_package.py"                   # T007
Task: "Edit tests/unit/test_db_config.py"                    # T008
Task: "Edit app/routers/shell.py (drop customer count)"      # T009
Task: "Edit app/templates/pages/home.html"                   # T010
Task: "Edit tests/unit/test_shell_routes.py"                 # T011
```

## Parallel Example: after Foundational, run US1 and US6 concurrently

```bash
# Developer A — US1 (deal create + view):
Task: "Write test_submission_service.py create/get/cedant tests"   # T020
Task: "Implement create_submission/get_submission/cedant_suggestions"  # T021

# Developer B — US6 (package structure, disjoint files):
Task: "Write tests/unit/test_package_service.py"                   # T044
Task: "Implement app/services/package_service.py"                  # T045
```

---

## Implementation Strategy

### MVP First

1. Phase 1 Setup → Phase 2 Foundational (clean rebuild is the gate).
2. Phase 3 **US1** — create + view a deal by its own identity. **STOP and validate** (SC-001).
3. Phase 4 **US2** completes the master-detail list; together US1+US2 (both P1) are the usable-at-scale baseline.

### Incremental Delivery

Foundational → US1 (MVP) → US2 → US3 → US4 → US5 → US6, validating each at its checkpoint. US6 may be pulled forward and run in parallel since it shares no files with US1–US5.

### Parallel Team Strategy

After Foundational: one developer takes US1→US2→US5 (the submission service/router spine), a second takes US3→US4 (status + CRM, reusing the gate helper), and a third takes US6 (packages) independently.

---

## Notes

- `[P]` = different files, no dependency on an incomplete task; same-file tasks are sequential.
- `[Story]` labels map tasks to spec user stories for traceability; Setup/Foundational/Polish carry none.
- Tests are written before implementation and must fail first (Article 12); the unit tier is SQLite (`register_engine`), the migration/atomicity tier is `--run-sqlserver`.
- All SQL goes through the `db/` safe path; the create and status transactions use `db.get_connection("WORKBENCH")` + explicit `conn.begin()` (Articles 4, 7). Never `execute_command` for status.
- No row-level security anywhere; `assigned_analyst_id` is a soft filter only (Article 6). `reference/` (the UX mock — the constitution's Source-of-Truth list names this the `mock/` reference; same artifact, path name to be reconciled) is out of the cleanup scope (research R8).
- **FR-022 scope note:** function-level role gating has no new surface this iteration — all submission CRUD is available to any authenticated analyst; only FR-022's *negative* clause (roles never restrict rows) is exercised, via the no-scope regression (T017) and inherited Iteration-0 auth. Admin-only maintenance functions, if any, arrive in later iterations.
- **FR-021 filter note:** `treaty_year` is a first-class list filter in the data-access/route contracts and in T027/T028 as the treaty-year *grouping* facet of inception filtering (research R10); FR-021 enumerates cedant/treaty-type/inception, of which treaty-year is a sub-facet.
- Commit after each task or logical group; stop at any checkpoint to validate a story independently.
