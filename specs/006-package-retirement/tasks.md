# Tasks: Package Retirement

**Input**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`, and
`quickstart.md` in this directory.

**Delivery rule**: Complete one user story, run its verification, and stop for the
approver to click the running feature before starting the next user story.

## Phase 1: Source documents and approved UI

- [x] T001 [P-09] Approve `docs/ui_previews/package_retirement_submission.html`
  and `docs/ui_previews/package_retirement_contextual_edm.html`; preserve the current
  shell, submission controls, EDM tables, disclosure markup, and caret behavior.
- [x] T002 [P] [FR-001] Replace current Package requirements with direct submission
  associations in `docs/PRD.md` and `docs/FUNCTIONAL_REQUIREMENTS.md`; do not edit
  historical design notes.
- [x] T003 [P] [FR-002] [FR-003] Update `docs/DATA_MODEL.md` with
  `submission_edm`, `submission_rdm`, entity-scoped jobs, and RDM-wide broker analyses.
- [x] T004 [P] [FR-020] Add concise supersession pointers to
  `specs/002-submission-package-domain/`, `specs/003-edm-rdm-entity-management/`,
  and `specs/004-edm-rdm-details-backfill/` files that own Package behavior.
- [x] T005 [P] [T-06] Record TestPyPI `irp-integration` 0.4.0 and the confirmed
  `exposure_set_name` signature in `docs/IRP_INTEGRATION_FOLLOWUPS.md`.

## Phase 2: Package removal foundation

**Purpose**: Replace Package persistence and execution assumptions before any user
story route reads the new associations.

### Tests first

- [x] T006 [P] [FR-001] [FR-002] [FR-003] Update
  `tests/sqlserver/test_submission_migration.py`,
  `tests/sqlserver/test_detail_tables_migration.py`,
  `tests/sqlserver/test_job_tables_migration.py`, and
  `tests/sqlserver/test_schema_drift.py` for the removed Package tables/columns,
  new association keys/indexes, `requested_from_submission_id`, and broker-analysis
  identity; confirm the focused tests fail before editing the migration.
- [x] T007 [P] [FR-002] [FR-003] Update `tests/iteration1_mirror.py` fixtures and
  add unit assertions for association foreign keys, duplicate rejection, and detach
  isolation; confirm the focused tests fail before editing the mirror.
- [x] T008 [P] [FR-018] [FR-019] Update `tests/unit/test_irp_gateway.py`,
  `tests/unit/test_rdm_service.py`, `tests/unit/test_rdm_sync.py`,
  `tests/unit/test_poller.py`, and `tests/unit/fakes/fake_irp.py` for one standalone
  RDM import using `exposure_set_name` and RDM-wide analysis capture; confirm the
  focused tests fail first.

### Schema and execution

- [x] T009 [FR-001] [FR-002] [FR-003] [FR-019] Edit
  `alembic/versions/0001_initial.py` per `data-model.md`: remove Package schema and
  `package_id`, add both association tables, add job provenance, change broker-analysis
  identity, and update downgrade order.
- [x] T010 [FR-002] [FR-003] Mirror T009 in `tests/iteration1_mirror.py` and update
  shared unit fixtures that insert EDMs, RDMs, jobs, or analyses.
- [x] T011 [FR-018] Change `app/services/irp_gateway.py` to submit RDM imports once
  with `exposure_set_name=rdm_name`; confirm the call against TestPyPI 0.4.0.
- [x] T012 [FR-018] [FR-019] Replace pair-based RDM inputs and capture in
  `app/services/rdm_service.py` and `app/workers/package_jobs.py`; rename the worker
  module to `app/workers/entity_jobs.py`, target one RDM, and write broker analyses
  with `edm_id` null.
- [x] T013 [FR-018] Remove EDM-completion-to-RDM-upload chaining and Package
  finalization from `app/poller/run.py`; retain EDM detail backfill and one RDM
  analysis backfill after an RDM import finishes.
- [x] T014 [FR-001] Remove Package router registration from `app/main.py` and delete
  `app/routers/packages.py`, `app/services/package_service.py`, and Package-only
  operations in `app/services/package_sync_service.py` and `app/services/job_query.py`.
- [x] T015 [FR-001] Delete Package-only templates, JavaScript, and CSS; remove the
  `packages.css` import from `app/static/css/app.css` while retaining styles still
  used by status chips or moving those exact rules to their owning stylesheet.
- [x] T016 [FR-020] Delete or replace Package-only unit tests and update imports for
  `entity_jobs.py`; run the focused foundation unit tests.

**Database gate**: Ask the developer to choose Rebuild / Refresh / Skip. Do not run
the destructive WORKBENCH rebuild. SQL Server tests remain unverified until the
developer has prepared the database and `make test-sql` runs.

## Phase 3: User Story 1 - Submission EDM and RDM tables

**Goal**: Show direct EDM/RDM associations on submission detail with no Package UI.

**Independent Test**: Open a submission containing two EDMs and two RDMs and verify
the fixed tables, counts, Risk Modeler links, empty states, and absence of Package.

### Tests first

- [x] T017 [P] [US1] [FR-002] [FR-003] Add association read tests to
  `tests/unit/test_submission_service.py`, including one EDM/RDM related to two
  submissions and no duplicated entity row; confirm they fail first.
- [x] T018 [P] [US1] [FR-005] [FR-006] Replace Package-card expectations in
  `tests/unit/test_submission_routes.py` with EDM/RDM table, count, Risk Modeler
  link, long-list, and independent empty-state assertions; confirm they fail first.

### Implementation

- [x] T019 [US1] [FR-002] [FR-003] Add association reads and submission table
  payloads to `app/services/submission_service.py`; derive portfolio and analysis
  counts without Risk Modeler calls.
- [x] T020 [US1] [FR-005] Pass EDM/RDM table payloads from
  `app/routers/submissions.py` and remove Package-card reads.
- [x] T021 [P] [US1] [FR-005] [FR-006] Create fixed EDM and RDM table partials in
  `app/templates/partials/` and add only their required token-based rules to
  `app/static/css/submissions.css`.
- [x] T022 [US1] [FR-001] [FR-005] Replace the Package section in
  `app/templates/pages/submission_detail.html` with the approved tables while
  preserving the existing header, owner, CRM, status controls, and history markup.
- [x] T023 [US1] [FR-004] Run the US1-focused unit tests, then stop for the approver
  to click the running submission page before starting US2.

## Phase 4: User Story 2 - Add and remove submission data

**Goal**: Import or relate EDMs/RDMs from an active submission and detach only the
selected association.

**Independent Test**: Import an EDM and RDM into one submission, relate both to a
second submission without another import, then detach them from the first submission.

### Tests first

- [x] T024 [P] [US2] [FR-007] [FR-008] [FR-009] Add service tests for import with
  association, all-live add-existing candidates, duplicate/stale selection, and
  detach-only behavior in `tests/unit/test_submission_service.py`; confirm they fail.
- [x] T025 [P] [US2] [FR-007] [FR-008] [FR-010] [FR-021] Add route tests for EDM
  and RDM add/import/attach/detach paths, CSRF, and closed-submission rejection in
  `tests/unit/test_submission_routes.py`; confirm they fail.

### Implementation

- [x] T026 [US2] [FR-007] [FR-008] [FR-009] Implement association writes,
  candidate search/pagination, stale predicates, and detach in
  `app/services/submission_service.py`, using explicit WORKBENCH transactions where
  the entity and association must be inserted together.
- [x] T027 [US2] [FR-007] Update `app/services/edm_service.py` and
  `app/services/rdm_service.py` to accept optional submission provenance and create
  the association before dispatching the entity-scoped upload head.
- [x] T028 [US2] [FR-007] [FR-008] [FR-009] [FR-010] [FR-021] Implement the EDM
  and RDM add/import/attach/detach routes in `app/routers/submissions.py` per
  `contracts/http-routes.md`.
- [x] T029 [P] [US2] [FR-007] [FR-008] Build the add-new/add-existing modal and
  candidate partials in `app/templates/partials/`, reusing the shared `.modal-*`
  convention and existing shared-drive/name-collision components.
- [x] T030 [US2] [FR-009] Add remove controls to the submission table partials;
  label the action as removal from the submission and never as Risk Modeler deletion.
- [ ] T031 [US2] [FR-004] [FR-018] Run the US2-focused unit tests and observe one
  standalone RDM import through the running worker/poller when the developer's stack
  is available; stop for the approver to click the running feature before US3.
- [x] T031a [US2] [FR-006] Add Status to both submission entity tables and refresh
  each table while any listed import or subsequent backfill is non-terminal; cover
  polling start and stop in the unit tier before the US2 click review.

## Phase 5: User Story 3 - Contextual EDM detail

**Goal**: Preserve the EDM detail page while making submission context explicit and
loading one submission-related RDM's stored analyses on demand.

**Independent Test**: Open the same EDM from two submissions with different RDMs and
verify the breadcrumb, EDM picker, RDM rows, lazy analysis request, and direct-library
behavior for each URL.

### Tests first

- [x] T032 [P] [US3] [FR-011] [FR-012] [FR-013] Add contextual EDM service and
  404 tests to `tests/unit/test_edm_service.py`; confirm they fail first.
- [x] T033 [P] [US3] [FR-014] [FR-015] [FR-016] Add submission-scoped RDM list,
  per-RDM analysis query, and no-Risk-Modeler-read tests to
  `tests/unit/test_edm_analyses.py`; confirm they fail first.
- [x] T034 [P] [US3] [FR-011] [FR-017] Add contextual and direct-library route
  assertions to `tests/unit/test_edm_detail_header.py` and
  `tests/unit/test_edm_sync_routes.py`; confirm they fail first.

### Implementation

- [x] T035 [US3] [FR-011] [FR-012] [FR-013] Add contextual association validation,
  submission EDM choices, and contextual sync inputs to `app/services/edm_service.py`.
- [x] T036 [US3] [FR-014] [FR-015] [FR-016] Replace Package-based broker analysis
  reads in `app/services/analysis_service.py` with submission RDM reads and a stored
  per-RDM analysis query.
- [x] T037 [US3] [FR-011] [FR-016] [FR-017] Add contextual EDM, body, sync, and lazy
  analysis routes to `app/routers/edms.py`; keep `/edms/{edm_id}` context-free.
- [x] T038 [US3] [FR-012] [FR-013] Update the navigation manifest only as needed
  for the contextual route, then add the source-submission breadcrumb and EDM picker
  to `app/templates/partials/edm_detail_body.html`.
- [x] T039 [US3] [FR-014] [FR-016] Replace the Package-scoped broker analyses
  section with submission-scoped collapsed RDM rows and a lazy analysis partial.
  Reuse the existing `.sec`, `.dtable__rdm`, `.grp-caret`, and related markup and CSS
  exactly; do not redesign portfolios, treaties, or row disclosures.
- [x] T040 [US3] [FR-011] [FR-017] Run the US3-focused unit tests, then stop for the
  approver to click contextual and direct-library EDM URLs.

## Phase 6: Current documentation and final verification

- [x] T041 [P] [FR-020] Replace current Package execution diagrams under
  `docs/sequence_diagrams/` with direct association, standalone import, contextual
  detail, and detach behavior; remove the `packages/` diagrams.
- [x] T042 [P] [FR-020] Update `specs/006-package-retirement/quickstart.md` with any
  route or label corrections found during implementation; do not duplicate design
  rationale.
- [x] T043 [FR-020] Search live schema, application code, tests, and current
  execution documents for `package`, `packages`, `package_id`, and
  `submission_package`; retain only historical evidence, supersession notes, and
  Python packaging references.
- [x] T044 Review the full diff for unnecessary helpers, compatibility branches,
  repeated comments, and documentation outside the file that owns each changed fact.
- [x] T045 Run `uv run pytest tests/unit` and report the unit count. Run
  `make test-sql` only if `linux-box` is already running and report its count; do not
  start or rebuild containers.
- [ ] T046 [T-06] Run the opt-in IRP tier against TestPyPI `irp-integration` 0.4.0
  only when credentials and the developer's stack are available; report standalone
  RDM import as unverified otherwise.

## Phase 7: EDM and RDM notes

- [x] T047 [FR-023] Add nullable `notes NVARCHAR(250)` to `irp_edm`, `irp_rdm`, and the SQLite mirror.
- [x] T048 [FR-023] [FR-028] Add EDM/RDM note reads, normalization, updates, and note-only concurrency checks.
- [x] T049 [FR-024] [FR-026] Add CSRF-protected direct and contextual note routes with 422 and 409 responses.
- [x] T050 [FR-024] [FR-027] Add the inline note editor and suspend detail polling while it is open.
- [x] T051 [FR-025] Add wrapped Notes columns to submission EDM and RDM tables.
- [x] T052 [FR-023] [FR-026] [FR-028] Add unit and SQL Server schema tests for notes.
- [x] T056 [FR-024] [FR-025] Add in-place note editing to submission EDM and RDM table cells.

## Phase 8: Submission table sorting and Risk Modeler links

- [x] T053 [FR-006] Add independent Name, Status, and count sorting to the submission EDM and RDM tables and retain both orders during polling.
- [x] T054 [FR-006] Show Risk Modeler links only for EDMs/RDMs whose status is `ready`.
- [x] T055 [FR-006] Add service and route tests for table ordering, URL state, and ready-only Risk Modeler links.

## Phase 9: Contextual RDM navigation

- [x] T057 [FR-030] [FR-031] Add the contextual RDM association read and submission RDM choices.
- [x] T058 [FR-030] [FR-031] Add contextual RDM detail, body, sync, and note routes; preserve the submission in RDM links and the selector.
- [x] T059 [FR-030] [FR-031] [FR-032] Add service and route tests for contextual association validation, breadcrumbs, selector URLs, and direct-library compatibility.

## Phase 10: Submission table column alignment

- [x] T060 [FR-006] Cap the Name column width in `submission_edm_table.html` and
  `submission_rdm_table.html` so the EDM and RDM tables render identical column
  widths regardless of name length; Notes remains the only variable-width column.

## Dependencies

- Phase 1 finishes before implementation so the source documents describe the target.
- Phase 2 blocks every user story.
- US1, US2, and US3 run in order. Each checkpoint requires the running feature click.
- T041-T046 run after all three user stories.
- Tests for each phase are written and observed failing before their implementation tasks.

## Delivery order

1. Remove Package persistence and pair-based RDM execution.
2. Deliver submission EDM/RDM tables and stop for review.
3. Deliver import/add-existing/detach and stop for review.
4. Deliver contextual EDM navigation and lazy RDM analyses and stop for review.
5. Update current execution documents and run available verification tiers.
