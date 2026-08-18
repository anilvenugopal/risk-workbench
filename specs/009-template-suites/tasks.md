# Tasks: Analysis Templates & Template Suites — Definition & Administration

**Input**: Design documents from `specs/009-template-suites/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R11), data-model.md, contracts/routes.md, contracts/transfer-workbook.md, quickstart.md

**Tests**: Included — the plan mandates the three-tier test strategy (Article 12): unit tests for worker/routes/validation/workbook/gating, sqlserver migration assertions, and an `--run-irp` shape test.

**Organization**: Tasks are grouped by user story. One story is implemented end-to-end per pass (docs/UI_WORKFLOW.md); each story phase ends at a checkpoint for the approver to click the running slice.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 / US2 / US3 (user story phases only)

---

## Phase 1: Cross-repo prerequisite (irp-integration — built externally)

**Purpose**: Track the irp-integration work this feature consumes. The T-06 classification/validation utility **landed 2026-08-18** in the `0.6.0rc1` TestPyPI pre-release; the accumulation-profile read is **tabled** (old T001/T002 — moved to *Deferred: accumulation* below).

- [x] T003 The T-06 utility landed in `irp-integration==0.6.0rc1`; consumed via the pinned TestPyPI build (`make irp-testpypi`, pin in the `irp-testpypi` dependency group) rather than `make irp-local`. Validated 2026-08-18 (research.md R2): `irp_integration.analysis_validation` exposes `classify_model_profile` + `validate_analysis_settings`, pure (no `IRPClient`), and the wheel's submit path is refactored onto it. T017, T021, T032 are unblocked

**Checkpoint**: the T-06 validation utility is importable locally. (Accumulation reads deferred; `irp_model_profile.is_accumulation` stays in the schema, defaulting 0, so resuming accumulation is additive — no migration churn.)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, seed row, nav wiring, and router skeleton that every user story builds on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T004 Add the 4 reference-cache tables (`irp_model_profile`, `irp_output_profile`, `irp_event_rate_scheme`, `irp_currency`) to `alembic/versions/0001_initial.py` per data-model.md — `sa.Uuid` PK/`NEWID()`, `DATETIME2`, `GETUTCDATE()` audit defaults, plain unique index on the natural key (`irp_id`; `code` for currencies), no `deleted_at` (cache rows hard-delete)
- [x] T005 Add the 4 template tables (`analysis_template`, `analysis_template_tag`, `template_suite`, `template_suite_item`) to `alembic/versions/0001_initial.py` — filtered unique live-name indexes on `analysis_template.name` and `template_suite.name` (pattern `uq_irp_edm_live_irp_id`), `UNIQUE(suite_id, template_id)` on suite items, composite PK `(template_id, tag_name)` on tags, R8 column defaults (1.00 / 1 / 0 / 1) — same file as T004, sequential
- [x] T006 Seed the `('sync_irp_metadata', 'Sync IRP metadata', …)` row into `rwb_job_type_kind` in both the `alembic/versions/0001_initial.py` inline seed and `infra/scripts/seed_db.py`
- [x] T007 [P] Add the `ITERATION4_SCHEMA` block (SQLite DDL for all 8 tables) to `tests/iteration1_mirror.py` and register the tables in the drift-guard lists consumed by `tests/sqlserver/test_schema_drift.py`
- [x] T008 [P] Add SQL Server migration assertions for the 8 new tables, their unique/filtered indexes, and the new kind row in `tests/sqlserver/` (new test module, e.g. `tests/sqlserver/test_template_suite_schema.py`)
- [x] T009 Add `templates.suites` (label "Template Suites", route `/templates`, roles `[]`) and `templates.metadata` (label "Analysis Metadata", route `/templates/metadata`, roles `[]`) children under the existing `templates` root in `app/nav/manifest.py`
- [x] T010 Create `app/routers/templates.py` skeleton (literal sub-paths before parameterized, EDM-router precedent), register it before `shell.router` in the app factory, and remove the `/templates` stub handler from `app/routers/shell.py:49` — skeleton serves a minimal `GET /templates` placeholder so the nav route keeps resolving until US2 reworks the page

**Checkpoint**: `make db-rebuild` migrates the 8 tables; nav shows both children; unit + sqlserver schema tests green.

---

## Phase 3: User Story 1 — Sync and view analysis metadata (Priority: P1) 🎯 MVP

**Goal**: On-demand `sync_irp_metadata` worker refreshes the four cache tables from Risk Modeler; a four-tab read-only metadata page lists them filterable with DLM/HD/Accumulation markers and last-synced time.

**Independent Test**: Run the sync against the IRP sandbox; the four tabs match Risk Modeler's lists, filtering narrows ~3,500 profiles to a UD profile without a reload, no create/edit control exists, a second sync while one runs is refused with "sync already in progress", and a failed sync leaves the cache and last-synced time intact (quickstart US1).

### Implementation for User Story 1

- [x] T011 [US1] Add 4 frozen dataclasses (`ModelProfileEntry`, `OutputProfileEntry`, `EventRateSchemeEntry`, `CurrencyEntry`) and the 4 `list_*` Protocol methods + real implementation via `client.reference_data` in `app/services/irp_gateway.py`, field lists per contracts/routes.md (`list_accumulation_profiles` deferred with the tabled accumulation read — see *Deferred: accumulation*)
- [x] T012 [P] [US1] Mirror the 4 list methods in `tests/unit/fakes/fake_irp.py` with configurable sample data covering DLM (`RL25`), HD (`HDv3.0`), and `Open` rows
- [x] T013 [US1] Implement the `sync_irp_metadata` Dramatiq actor in `app/workers/metadata_jobs.py` (name-based dispatch, body via `runtime.run_job`): fetch all four sets, truncate currency names to Risk Modeler's 16-character creation limit, then one WORKBENCH transaction — snapshot upsert keyed on `irp_id` (currencies: `code`), hard delete of rows the fetch no longer returned; return `JobResult.ok(synced counts)` / `.fail(reason)`; a gateway failure aborts before any write (FR-002, P-06); depends on T011 (accumulation ingestion deferred — `is_accumulation` defaults 0 on all synced rows)
- [x] T014 [P] [US1] Unit tests for the worker in `tests/unit/test_metadata_sync_worker.py` (fake IRP): initial populate, re-sync removes vanished rows and updates changed names, legacy currency names are truncated to 16 characters (P-06), fetch failure leaves prior cache rows intact and fails the job
- [x] T015 [US1] UI preview `docs/ui_previews/templates_metadata.html` (from `docs/ui_previews/_scaffold.html`, reuse the existing `.tabs` CSS component): four tabs, per-tab filter input, DLM/HD/Accumulation marker + raw software version column, last-synced line, sync button, empty state — approved before wiring
- [x] T016 [US1] `GET /templates/metadata` page route in `app/routers/templates.py` + `app/templates/pages/templates_metadata.html`: four tabs (`?tab=model-profiles` default, `output-profiles`, `event-rate-schemes`, `currencies`), tab links `hx-get` the fragment with `hx-push-url`, last-synced time and status/failure reason from the latest `sync_irp_metadata` rwb_job, `?sync=` banner messages; context built by a builder shared with the fragment route
- [x] T017 [US1] `GET /templates/metadata/table` HTMX fragment + `app/templates/partials/metadata_table.html`: one tab's read-only table with filter input (`hx-trigger="input delay:300ms"`, edm_library pattern); model-profile tab derives the marker (is_accumulation → Accumulation, else the T-06 irp-integration classification utility — never a re-implemented rule) and shows the raw version; depends on T016 (shared context builder) and T003 (utility available — `irp-integration==0.6.0rc1`, done)
- [x] T018 [US1] `POST /templates/metadata/sync` in `app/routers/templates.py`: CSRF-validated, open to every analyst; `ensure_pending_rwb_job` with the fixed sentinel requestor + dispatch, PRG to `?sync=queued`; when a sync job is already pending/running nothing is enqueued and PRG lands on `?sync=already-running` rendered as "sync already in progress" (FR-002)
- [x] T019 [P] [US1] Unit tests for the metadata routes in `tests/unit/test_templates_metadata_routes.py`: page renders four tabs, fragment filters, marker derivation shown, sync enqueues once, second request refused with the message, failed-job reason displayed, no create/edit control in any tab's markup
- [ ] T020 [P] [US1] IRP-tier shape test `tests/irp/test_reference_data_shapes.py` (opt-in `--run-irp`): all four gateway reads return the R1-documented fields against the sandbox

**Checkpoint**: quickstart US1 passes end-to-end against the sandbox. **STOP** — approver clicks the running slice before US2 begins.

---

## Phase 4: User Story 2 — Create and administer templates and suites (Priority: P2)

**Goal**: Admin builds analysis templates (cached pick lists, DLM-requires-scheme, R8 defaults, tags, treaty pattern) and composes ordered suites (reorder, per-item override, no duplicates); four starter suites seed on rebuild; everything global, mutations admin-gated, unresolved references flagged.

**Independent Test**: As admin: create a DLM template (save blocked without a scheme), an HD template (saves without one), a mixed suite with reorder + override; deleting a referenced template is blocked naming the suite; as non-admin: everything visible, no mutation controls, direct POSTs rejected; after `make db-rebuild` the four starter suites exist and survive a re-seed after an edit (quickstart US2).

### Implementation for User Story 2

- [ ] T021 [US2] Classification + template CRUD/validation in new `app/services/template_service.py`: classify a profile name via the cache (`is_accumulation` → Accumulation, else the T-06 irp-integration classification utility — R2 revised, no app-side rule); create/update/soft-delete templates with tag-row replacement; save-time validation via the T-06 utility — DLM-requires-`event_rate_scheme_name` and scheme-peril/region-must-match-profile (reject naming the rule; each check skipped when its cache row is absent — unresolved, never a save-blocker); live-name uniqueness (`is_unique_violation` absorbed into the form error); delete guard returning referencing live suite names (FR-010); read-time unresolved flags via LEFT JOIN from saved names to cache rows (R9); depends on T003 (T-06 utility)
- [ ] T022 [US2] Suite CRUD + item composition in `app/services/template_service.py`: create/update/soft-delete suites, items hard-rewritten on save with positions renumbered 1..n, per-item `portfolio_name_override`, duplicate template per suite rejected (`UNIQUE(suite_id, template_id)`), no DLM/HD/accumulation mixing restriction — same file as T021, sequential
- [ ] T023 [US2] Scheme filter/pre-fill query in `app/services/template_service.py` (T-03): live schemes matching the chosen profile's `(peril_code, model_region_code)`; pre-select only when exactly one active scheme matches; zero → empty, multiple → unselected list
- [ ] T024 [P] [US2] Unit tests for the service in `tests/unit/test_template_service.py`: DLM rejection message, HD/Accumulation optional (HD *with* scheme allowed), peril/region-mismatched scheme rejected at save (and allowed when either side is absent from the cache), unresolved profile skips the rule, duplicate template/suite names rejected, delete guard names suites, unresolved flag appears/disappears across simulated re-syncs, scheme pre-fill exactly-one / zero / multiple cases
- [ ] T025 [US2] UI preview `docs/ui_previews/templates_admin.html` (from `_scaffold.html`): administration page (suite list with name/item count/author/unresolved badge + filterable template list), template builder form (pick lists — model-profile options carry their DLM/HD/Accumulation marker, FR-004 — analysis settings with R8 defaults, tags, treaty pattern), suite builder (ordered item picker, override, empty-state marker) — approved before wiring
- [ ] T026 [US2] `GET /templates` administration page: rework `app/templates/pages/templates.html` + handler in `app/routers/templates.py` — suite list and filterable template list visible to all; an export control (links `GET /templates/export.xlsx`, always exports everything) visible to all; create/edit/delete/import controls rendered only for `is_admin`
- [ ] T027 [US2] Template builder routes in `app/routers/templates.py` + form templates under `app/templates/pages/`: `GET /templates/analysis-templates/new`, `POST /templates/analysis-templates` (create), `GET /templates/analysis-templates/{id}` (detail, all analysts, unresolved flags inline), `POST /templates/analysis-templates/{id}` (update), `POST /templates/analysis-templates/{id}/delete` — form-banner validation errors, R8 defaults pre-filled, pick lists from live cache with the DLM/HD/Accumulation marker on model-profile options (FR-004), tags entered as names with autocomplete over names already used on templates (FR-006)
- [ ] T028 [US2] `GET /templates/analysis-templates/scheme-options` HTMX fragment + `app/templates/partials/scheme_options.html`: `<option>` list for `?profile=<name>` via the T023 query, pre-selected when exactly one; triggered on profile change in the builder
- [ ] T029 [US2] Suite builder routes in `app/routers/templates.py` + templates: `GET /templates/suites/new`, `POST /templates/suites` (create with items/order/overrides), `GET /templates/suites/{id}` (detail, ordered items, empty-state marker), `POST /templates/suites/{id}` (update — items rewritten, renumbered), `POST /templates/suites/{id}/delete`; ordered item rows partial in `app/templates/partials/suite_item_rows.html`
- [ ] T030 [US2] Admin gating pass over `app/routers/templates.py`: `_require_admin` (pattern `app/routers/admin.py:19`) on every mutating route, CSRF on every POST, mutation controls hidden from non-admins in all templates (P-01)
- [ ] T031 [P] [US2] Unit tests for routes + gating in `tests/unit/test_templates_routes.py`: create/edit/delete flows, DLM rejection re-renders with the rule named, duplicate-name form error, delete-guard message, reorder + override round-trip, same-template-twice blocked, non-admin sees no controls and direct POSTs are rejected, unresolved badge renders
- [ ] T032 [US2] Workbook parse + import-apply in `app/services/template_service.py` per `contracts/transfer-workbook.md` (needed by the starter seed): openpyxl parse of `Templates`/`Suites` sheets, whole-file validation collecting `(sheet, row, message)` — missing required value, wrong type, duplicate `Name` / `(Suite Name, Position)` / `(Suite Name, Template Name)` in file, DLM without scheme and scheme/profile peril-region mismatch when both resolve (both via the T-06 utility, same rules as T021 save), unknown header or sheet — then all-or-nothing one-transaction apply: match-by-name update (tags replaced) or create; matched suites' item lists replaced wholesale; empty-suite row handling; values absent from cache import fine (FR-019); depends on T003 (T-06 utility)
- [ ] T033 [US2] Author the seed workbook `infra/scripts/starter_suites.xlsx` in the transfer-workbook format: US, Canada, US+Canada, Global suites, ~10 templates each with indicative settings built from `RMS Default *` profile names observed in the sandbox (P-02)
- [ ] T034 [US2] Wire the starter seed into `infra/scripts/seed_db.py`: import `starter_suites.xlsx` through the T032 import service, skipping entirely when any live suite exists (one EXISTS check, R10; re-creating the starter four after all suites were deleted is acceptable — decided 2026-08-18); works with an empty metadata cache (every profile unresolved, DLM rule skipped)
- [ ] T035 [P] [US2] Unit tests for the seed in `tests/unit/test_starter_seed.py`: fresh DB seeds the four suites with their templates; re-seed after an edit skips and the edit survives

**Checkpoint**: quickstart US2 passes; starter suites present after `make db-rebuild`. **STOP** — approver clicks the running slice before US3 begins.

---

## Phase 5: User Story 3 — Move suites between environments (Priority: P3)

**Goal**: Export suites (selected or all) with every referenced template — plus suite-less templates on export-all — to one `.xlsx` workbook; import applies all-or-nothing with every error reported by sheet/row; round-trip is field-identical.

**Independent Test**: Export all, `make db-rebuild`, import, export again — the two workbooks are field-identical (row order normalized); re-import updates in place with no duplicates; a broken cell yields the full error list and applies nothing (quickstart US3).

### Implementation for User Story 3

- [ ] T036 [US3] Workbook export builder in `app/services/template_service.py`: `Templates` sheet (full field set, tags semicolon-joined, R8 occupancy label mapping, TRUE/FALSE booleans) + `Suites` sheet (suite name, position, template name, override); export always writes everything — every live suite and every live template including those in no suite (FR-016, no per-suite selection); empty suite exports as one row with `Position`/`Template Name` blank
- [ ] T037 [US3] `GET /templates/export.xlsx` in `app/routers/templates.py`: open to every analyst, no parameters (always everything), plain `Response` with `Content-Disposition` (treaty-export precedent)
- [ ] T038 [US3] `POST /templates/import` in `app/routers/templates.py` + import controls on `app/templates/pages/templates.html`: admin-only, CSRF, multipart `UploadFile` through the T032 parse/apply; on errors re-render with the full `(sheet, row, message)` list; on success redirect with created/updated counts
- [ ] T039 [P] [US3] Round-trip unit test in `tests/unit/test_transfer_workbook.py`: `export(all)` → import into an empty environment → `export(all)` produces field-identical sheets with row order normalized by name/position (FR-020, SC-004); re-import of the same file updates in place, no duplicates
- [ ] T040 [P] [US3] Import validation-matrix unit tests in `tests/unit/test_import_validation.py`: each FR-018 error class reported with sheet + row and nothing applied (missing required field, wrong type, in-file duplicate name, duplicate position, DLM without scheme, unknown column, missing sheet, peril-region mismatch); unsynced-cache values import and flag unresolved (FR-019); wholesale replace removes locally present items absent from the file (FR-017)

**Checkpoint**: quickstart US3 round-trip passes. All user stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T041 [P] Reconcile `docs/DATA_MODEL.md`: §7 deltas (`analysis_template_tag.tag_name` replaces `irp_tag_id`; `treat_construction_occupancy_as_unknown` added; `auto_name_pattern`/`region_label`/`peril_code` dropped; authorship = `inserted_by`, no separate `created_by`) and §10 cache-table columns per data-model.md
- [ ] T042 Run the full three-tier suite and fix any drift: `uv run pytest tests/unit`, `make test-sql` (or `make wsl-test-sql`), and `make shell` → `uv run pytest tests/irp --run-irp`
- [ ] T043 Run the complete `specs/009-template-suites/quickstart.md` walkthrough (US1–US3) including the `make db-rebuild` starter-suite check (SC-005) and the sync-refusal double-click check

---

## Deferred: accumulation profiles (tabled 2026-08-18)

Accumulation ingestion is postponed; `irp_model_profile.is_accumulation` (default 0) and every
marker/validation branch on it ship anyway, so resuming is purely additive. Until it resumes,
sync ships four sets and no synced profile shows the Accumulation marker (the Accumulation branch
is exercised only by tests). If accumulation slips past this iteration entirely, revisit spec
FR-001/FR-004's three-way promise. Deferred work, in order:

- [ ] T001 [DEFERRED] Sandbox spike in `../../IRP/irp-integration`: probe the Risk Modeler accumulation-profile endpoint against the CIC sandbox, pin the URL and response shape (fields, id/name keys, whether `software_version_code` exists on accumulation rows); record findings and reconcile the provisional accumulation columns in `specs/009-template-suites/data-model.md` (`irp_model_profile` notes) and `specs/009-template-suites/contracts/routes.md` (`AccumulationProfileEntry` field list) if the shape differs
- [ ] T002 [DEFERRED] Implement the accumulation-profile read in `../../IRP/irp-integration` (new `reference_data` manager method per the T001 shape, with that repo's tests), depends on T001
- [ ] T044 [DEFERRED] Wire accumulation into this repo: `AccumulationProfileEntry` dataclass + `list_accumulation_profiles` in `app/services/irp_gateway.py`, FakeIRP mirror with accumulation sample rows, fifth fetch in the `sync_irp_metadata` worker upserting rows with `is_accumulation=1`, worker/route test cases, and the fifth read in `tests/irp/test_reference_data_shapes.py`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (external)**: **complete 2026-08-18** — the T-06 utility shipped in `irp-integration==0.6.0rc1` (T003 done); T017/T021/T032 are no longer gated. T001/T002 are deferred with accumulation.
- **Foundational (Phase 2)**: independent of Phase 1 — **start here now**; T004 → T005 → T006 share `0001_initial.py`; T007/T008 after T005; T010 after T009. BLOCKS all user stories.
- **US1 (Phase 3)**: needs Phase 2 only (accumulation deferred); T017 additionally needs T003 (T-06 utility)
- **US2 (Phase 4)**: needs Phase 2; T021/T032 additionally need T003; consumes US1's cache for pick lists and classification (buildable against fake/synced data, but the story is verified after US1)
- **US3 (Phase 5)**: needs US2 (T036–T038 build on T032's parse/apply and US2's entities)
- **Polish (Phase 6)**: after all desired stories

### Within-story ordering

- US1: T011 → {T012, T013} → T014; T015 (preview approved) → T016 → T017/T018 → T019 (T017 also needs T003); T020 anytime after T011
- US2: T021 → T022 → T023 → T024; T025 (preview approved) → T026 → T027 → T028 → T029 → T030 → T031; T032 → T033 → T034 → T035
- US3: T036 → T037; T038 after T032/T036; T039/T040 after T036/T038

### Parallel Opportunities

- T007 and T008 (different test files) in parallel after the migration tasks
- US1: T012 (fake) alongside T013 (worker); T014, T019, T020 in parallel once their subjects exist
- US2: T024, T031, T035 (separate test files) in parallel with later implementation tasks; T033 (workbook authoring) in parallel with route work once T032 exists
- US3: T039 and T040 in parallel

---

## Parallel Example: User Story 1

```bash
# After T011 (gateway) lands, run together:
Task: "Mirror the 5 list methods in tests/unit/fakes/fake_irp.py"          # T012
Task: "IRP-tier shape test tests/irp/test_reference_data_shapes.py"        # T020

# After T013 (worker) + T018 (routes) land, run together:
Task: "Worker unit tests in tests/unit/test_metadata_sync_worker.py"       # T014
Task: "Route unit tests in tests/unit/test_templates_metadata_routes.py"   # T019
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 2 (schema/nav/router) — starts immediately; the T-06 utility is already available (T003 done, `0.6.0rc1`)
2. Phase 3: US1 — sync worker + metadata page (four reads; accumulation deferred)
3. **STOP and VALIDATE**: quickstart US1 against the sandbox; approver clicks the slice
4. This alone has standalone value: analysts stop opening Risk Modeler to check what profiles exist (SC-001)

### Incremental Delivery

1. Setup + Foundational → schema migrated, nav live
2. US1 → metadata sync + four-tab screen → validate → demo (MVP)
3. US2 → templates, suites, starter seed → validate → demo
4. US3 → export/import round-trip → validate → demo
5. Polish → DATA_MODEL reconciliation + full three-tier run + quickstart sweep

### Notes

- One story per pass; stop at each checkpoint (docs/UI_WORKFLOW.md)
- UI previews (T015, T025) are approved informally (show → 👍) before any wiring
- T032 (import parse/apply) sits in US2 because the starter seed (FR-015, US2 acceptance scenario 6) imports through it; US3 adds the export half and the routes
- Commit after each task or logical group; never add AI attribution trailers
