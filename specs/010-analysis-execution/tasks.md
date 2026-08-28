# Tasks: Analysis Execution — Suite & Single-Template Runs (Iteration 7)

**Input**: Design documents from `/specs/010-analysis-execution/`

**Prerequisites**: plan.md, spec.md, research.md (T-01…T-20), data-model.md, contracts/, quickstart.md

**Tests**: Included — the constitution requires all three tiers, and quickstart.md names
what each tier covers. Unit tests run after every change (`uv run pytest tests/unit`);
SQL Server and IRP tiers need the developer-started stack — report tiers by name and
count, and say plainly when a tier did not run.

**Organization**: Tasks are grouped by user story. Delivery follows P-09: US1 + US2 are
phase 1 (suite execution + tracking), US3 is phase 2 (single templates), US4 is phase 3
(loss retrieval), and the job-monitor listing (T-12) is the final phase. Each story stops
at a checkpoint for the approver to click the running feature before the next begins.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1–US4 from spec.md; setup/foundational/polish tasks carry no story label

---

## Phase 1: Setup

**Purpose**: Config defaults the retry batch and workers read.

- [X] T001 In `app/config.py`, change `IRP_SUBMISSION_MAX_RETRIES` default `None` → `3` and add `IRP_SUBMISSION_RETRY_BASE_SECS` (default 60) per data-model.md §6
- [X] T049 In `app/config.py`, add the pinned currency-default settings per data-model.md §6 (T-19): `default_analysis_currency_code` (`USD`), `default_analysis_currency_scheme` (`RMS`), `default_analysis_currency_vintage` (empty); document them in `infra/.env.example` *(added 2026-08-20, note 17 D6/P-16)*

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema reshape, kind seeds, mirror, and the gateway/job-service functions every story calls. No user story work can begin until this phase is complete.

- [X] T002 Reshape `irp_analysis` in `alembic/versions/0001_initial.py` per data-model.md §1: `rdm_id`/`source_rdm_name`/`irp_id` → NULL; new `full_name`, `irp_portfolio_id` FK, `analysis_template_id` FK, `execution_id`, `failure_reason`; CHECK `ck_irp_analysis_origin`; `uq_irp_analysis_rdm_irp` → filtered unique; new filtered unique `uq_irp_analysis_live_edm_name` on `(edm_id, name)`; new `ix_irp_analysis_edm_id`
- [X] T003 Extend `irp_job` in `alembic/versions/0001_initial.py` per data-model.md §2: new `irp_portfolio_id` FK, `irp_analysis_id` FK, `request_params` NVARCHAR(MAX); new `ix_irp_job_irp_analysis_id` (same file as T002 — sequential)
- [X] T004 [P] Seed `rwb_job_type_kind` rows `execute_analysis_batch` and `finalize_analysis` in `infra/scripts/seed_db.py` (`retrieve_analysis_results` and `irp_job_type_kind` `analysis` already seeded)
- [X] T005 Mirror T002/T003 in `tests/iteration1_mirror.py`: SQLite DDL, seeds, `EXACT_MATCH_TABLES` (depends on T002–T004)
- [X] T006 [P] Flip the `irp_portfolio_id`-absent assertion at `tests/sqlserver/test_job_tables_migration.py:53`; add SQL Server tier assertions for the reshaped `irp_analysis` (origin CHECK, both filtered uniques) and the new `irp_job` columns in `tests/sqlserver/`
- [X] T007 [P] Reconcile `docs/DATA_MODEL.md` §6 (own-analysis shape now enforced) and §8 (`irp_job.irp_portfolio_id` landed)
- [X] T008 [P] Add submission/backfill gateway functions to `app/services/irp_gateway.py` per contracts/irp-gateway.md: `submit_portfolio_analysis` (explicit `currency`, `skip_duplicate_check=True`, returns `(job_id, request_body)`), `get_analysis_job` — protocol + `_RealGateway`; confirm signatures against the active wheel first (`make irp-status`, TestPyPI `0.6.0`)
- [X] T009 [P] Add FakeIRP counterparts in `tests/unit/fakes/fake_irp.py`: per-name programmable submit success (job id + body with `resourceUri`) and `IRPIntegrationError`, job-status sequences ending FINISHED / FAILED-with-reason / CANCELLED
- [X] T010 Extend `app/services/irp_job_service.py`: `record_submitted_irp_job` / `record_submission_failure` accept `irp_portfolio_id`, `irp_analysis_id`, `request_params`; `resource_uri` written to `irp_job_resource` from `request_body["resourceUri"]` (depends on T003)
- [X] T050 Add `irp_analysis.execution_item_no` (INT NULL) to `alembic/versions/0001_initial.py` per data-model.md §1, mirror it in `tests/iteration1_mirror.py`, and add its column assertion to the T006 `irp_analysis` checks in `tests/sqlserver/` — the exact resume key now that a template can appear once per chosen suite *(added 2026-08-20, P-02 amended)*

**Checkpoint**: Unit + SQL Server tiers green on the new schema — user story work can begin.

---

## Phase 3: User Story 1 — Run template suites against selected portfolios (Priority: P1) 🎯 MVP

**Goal**: Multi-select portfolios → Execute Suite modal (search, expandable suites, per-execution template deselection, per-suite currency with pinned defaults, treaty picker) → background submit of one analysis per portfolio × selected template of each chosen suite, fixed naming with the 64-char cap and rerun suffix, failures visible and submission failures retried.

**Independent Test**: With an imported EDM holding several portfolios and the spec-009 suites, run one suite against two portfolios and confirm one submitted analysis per portfolio × template, correctly named, each with its own tracked job (quickstart Phase 1).

### UI Preview for User Story 1 🎨

- [X] T011 [US1] Rendered HTML preview of the execution modal in `docs/ui_previews/execute_modal.html` (from `_scaffold.html`): suite list with search, one suite expanded with two templates deselected, per-suite currency block (pre-filled defaults, one suite overridden, one with an empty vintage picker — FR-019/FR-020), treaty picker, disabled/enabled Submit, and the blocking-message state — approved before building the template and route

### Implementation for User Story 1

- [X] T012 [US1] Portfolio multi-select: checkbox (`name="portfolio_ids"`, `syncPicks()` pattern) in `app/templates/partials/portfolio_row.html`; **Execute Suite** / **Execute Template** buttons in the Portfolios header of `app/templates/partials/edm_detail_body.html`, disabled until ≥1 checked, offered only when the EDM is `ready` and ≥1 portfolio exists (FR-001); picks JS in `app/static/js/app.js`
- [X] T013 [US1] New `app/services/analysis_execution_service.py`: gate validation (EDM `ready`, portfolios belong, posted templates live and belonging to their suite, `kind=suite` ⇒ ≥1 suite with ≥1 template selected, every currency block complete and cache-valid — `code` in `irp_currency`, `scheme` in `irp_currency_scheme`, `(scheme, vintage)` in `irp_currency_scheme_vintage` (FR-019/FR-020), treaty names exist); plan composition per contracts/worker-poller.md §1 — one `item_no`-ordinaled item per suite×template selection carrying its suite's currency block with `asOfDate` from the chosen vintage's `effective_date` (T-03, P-15), `tag_names` extended with the submission name when a submission context exists (FR-021, T-20); `request_execution` persists the plan via `enqueue_rwb_job(requestor_type='analyst_request', requestor_id=execution_id, rwb_job_type='execute_analysis_batch', input_data=plan)` + dispatch (FR-012)
- [X] T014 [US1] Modal fragment `app/templates/partials/execute_analysis_modal.html` (`submission_entity_add_modal` pattern): search (300ms debounce re-GET with `q`), suite checkbox rows expanding via `<details>` into template lists checked by default (FR-003), per-suite currency block (code/scheme/vintage selects, vintage options scoped to the chosen scheme, pre-filled from the T049 defaults, blank when a default is unset/cache-absent — FR-019/FR-020), treaty picker from `irp_treaty` (zero valid — FR-004), read-only selected portfolios as hidden inputs, Alpine Submit-disable until ≥1 suite chosen, ≥1 template remains, and every currency block is complete (FR-002/FR-020)
- [X] T015 [US1] `GET`/`POST .../edms/{edm_id}/execute` in `app/routers/edms.py`, standalone + submission-contextual variants per contracts/routes.md: GET renders the fragment or the blocking message; POST validates CSRF, runs the T013 gate (422 re-renders the modal), composes + persists the plan, responds 204 with `HX-Trigger: execution-submitted` — no IRP call on the request path
- [X] T016 [US1] New `app/workers/analysis_jobs.py` with `execute_analysis_batch` actor (`_BODIES` registration, `max_retries=0`, `time_limit=60*60*1000` — T-17) per contracts/worker-poller.md §2: iterate portfolios × plan items with per-item isolation; resume check on `(execution_id, portfolio, item_no)` via `irp_analysis.execution_item_no` (T050); transaction A inserts the `irp_analysis` row claiming the name (`status_code='pending'`, FR-008); submit outside any transaction via T008 gateway with exactly the plan item's values including its currency block (FR-006); transaction B records the `irp_job` (`request_params`, `resource_uri`) — `status_code` stays `pending`, `irp_job.status` carries the progress — or on `IRPIntegrationError` calls `record_submission_failure` and writes `irp_analysis.failure_reason` (FR-010); `output_data = {"submitted": n, "submission_failed": m}`
- [X] T017 [US1] Port the `TimeLimitExceeded` handling fix into `app/workers/runtime.py` (from `origin/007-geohaz-execution`, plan.md source list)
- [X] T018 [US1] Poller `analysis` job type in `app/poller/run.py`: `_GETTERS["analysis"] = irp_gateway.get_analysis_job`; terminal handler — FINISHED enqueues head `rwb_job` `finalize_analysis` (`requestor_type='irp_job'`, `requestor_id=job.id`); FAILED/CANCELLED set `irp_analysis.status_code='error'` + `failure_reason` from the completion body (FR-011, T-08)
- [X] T019 [US1] `finalize_analysis` worker in `app/workers/analysis_jobs.py` per contracts/worker-poller.md §4 (standard actor pattern, `max_retries=0`): write `irp_id`, `settings_metadata`, `status_code='ready'`; failure → `rwb_job` `failed` with `error_detail` (FR-009). Resolution is by `analysisId` — see T056
- [X] T020 [US1] Implement the `_submission_retry` scaffold in `app/poller/run.py` per contracts/worker-poller.md §6 (T-09): newest `SUBMISSION FAILED` row per `irp_analysis_id`, eligible when `now > completed_at + IRP_SUBMISSION_RETRY_BASE_SECS * 2^attempts` and attempts < `IRP_SUBMISSION_MAX_RETRIES`; resubmit verbatim from `irp_job.request_params`; success updates the row in place (`irp_id`, `QUEUED`, attempts+1) and clears `failure_reason`; at the maximum `irp_analysis.status_code` flips to `error` — entity imports keep insert-per-failure

### Tests for User Story 1

- [X] T021 [P] [US1] Unit tests for naming (truncation, suffix re-clipping, full-name suffix), plan composition + immutability (per-suite currency onto items, `item_no` ordinals, submission tag appended, `asOfDate` from the chosen vintage), and the gate (incomplete or cache-invalid currency block → 422) in `tests/unit/test_analysis_execution_service.py`
- [X] T022 [P] [US1] Unit tests for the worker bodies against FakeIRP in `tests/unit/test_analysis_jobs_worker.py`: per-item isolation (one failure never stops the loop), a template shared by two suites submitting once per suite with each suite's currency and a suffixed name, resume skip after reclaim keyed on `execution_item_no`, submission-failure recording, backfill resolution
- [X] T023 [P] [US1] Unit tests for the poller handler (FINISHED enqueue, FAILED/CANCELLED reason extraction) and the retry batch (backoff eligibility, in-place update, exhaustion → `error`) in `tests/unit/`
- [X] T024 [US1] IRP sandbox test in `tests/irp/`: one real submit → single-status poll → backfill round-trip (`make shell` → `uv run pytest tests/irp --run-irp`)

**Checkpoint**: Suite execution works end-to-end (verifiable in DB / via jobs even before the US2 section exists). **STOP** — approver clicks the running feature before US2.

---

## Phase 4: User Story 2 — Track executed analyses on the EDM detail page (Priority: P2)

**Goal**: A user-executed analyses section on the EDM detail page — full name, portfolio, live status via the existing 3s body self-poll, settings/metadata on completion, failure reason on failure. (The `/workflows/irp-jobs` listing half of FR-014 ships in Phase 7.)

**Independent Test**: Submit a small run, watch the section populate immediately, statuses change without refresh, settings appear on completion and a reason on failure (quickstart Phase 1 steps 3–8).

### UI Preview for User Story 2 🎨

- [X] T025 [US2] Rendered HTML preview of the user-executed section in `docs/ui_previews/executed_analyses_section.html` (from `_scaffold.html`, modeled on the broker-analysis sections, no RDM grouping, portfolio column, status chips including "Failed to submit · attempt n/max", one expanded row with settings grid) — approved before building the partials

### Implementation for User Story 2

- [X] T026 [US2] User-executed read model in `app/services/analysis_service.py`: per-EDM list of executed `irp_analysis` rows joined to the latest `irp_job` per analysis (T-07), status-chip derivation (`SUBMISSION FAILED` → "Failed to submit · attempt n/max"), failure reason, settings availability
- [X] T027 [US2] New `app/templates/partials/executed_analysis_row.html` (modeled on `broker_analysis_row.html`): `full_name`, portfolio name, status chip, `failure_reason` when failed; expanded settings grid once `settings_metadata` is backfilled (FR-013)
- [X] T028 [US2] User-executed section in `app/templates/partials/edm_detail_body.html` + section context in the body routes of `app/routers/edms.py`; extend the server-computed `live` flag with "any executed analysis non-terminal or `pending`/`running`" so the 3s self-poll keeps running (FR-014, T-11)
- [X] T029 [P] [US2] Unit tests for the read model, status-chip derivation, and the extended `live` flag in `tests/unit/test_analysis_service.py`

**Checkpoint**: Delivery phase 1 (US1 + US2) complete — quickstart Phase 1 runs end-to-end. **STOP** for approver click-through.

---

## Phase 5: User Story 3 — Run individual templates (Priority: P3)

**Goal**: **Execute Template** opens the same modal listing templates instead of suites; several templates, same treaty picking, same submit machinery; suites and templates never mixed.

**Independent Test**: Select one portfolio, Execute Template, pick two templates, submit, confirm two correctly-named analyses (quickstart Phase 2).

### Implementation for User Story 3

- [X] T030 [US3] `kind=template` variant in `app/templates/partials/execute_analysis_modal.html` and the `GET`/`POST .../execute` routes in `app/routers/edms.py`: template checkbox rows (no suites offered, no expansion), same search and treaty picker, one currency block for the whole execution (FR-019), Submit disabled until ≥1 template and the currency block is complete (FR-002/FR-020); POST gate accepts `kind=template` with flat `template_ids` + a single currency block
- [X] T031 [P] [US3] Unit tests in `tests/unit/test_analysis_execution_service.py`: template-kind gate (no suites required, suites rejected), identical naming/plan/failure behavior, kinds never mixed

**Checkpoint**: Both execution kinds work. **STOP** for approver click-through.

---

## Phase 6: User Story 4 — View loss numbers for executed analyses (Priority: P4)

> **Deferred — re-task before building.** T032–T044 below assume the stored-Parquet
> design: `retrieve_analysis_results` materializes ELT/EP/PLT/stats and
> `analysis_result_meta` feeds the views. Design session note 19
> (`docs/design_session_notes/19_loss_results_viewing_no_elt_live_fetch_view_vs_compare_merged_grid.md`)
> replaced that for the **view** path: ELTs are export-only (D5), loss results are not
> stored for viewing (D6 — live fetch of EP stats + the EP curve), condensed results
> render inline in the expanded analysis row (D9), and the broker and user-executed
> tables merge into one (D11). §9's Parquet machinery survives but narrows to export,
> and its trigger — eager on completion vs lazy on export — is undecided. **O19-1,
> O19-2 and O19-4 stay open**; nothing here is ready for tasks until they close.
> FR-016 and FR-017 (`spec.md`) are deferred with this phase.

**Goal**: Automatic background retrieval of loss results per perspective (GR/GU/RL) for FINISHED analyses — Parquet row data + `analysis_result_meta` summary — shown on the analysis detail views with perspective tabs, PLT for HD only, results-pending until numbers arrive. Retrieval failure follows the standard job handling (`failed` + `error_detail`); backoff retry is deferred (P-14).

**Independent Test**: Run one DLM and one HD analysis to FINISHED, open the detail views, read AAL, return-period losses, OEP/AEP per perspective; PLT only on the HD analysis (quickstart Phase 3).

### Implementation for User Story 4

- [ ] T032 [US4] Add `analysis_result_meta` and `analysis_perspective_kind` to `alembic/versions/0001_initial.py` per data-model.md §3/§4: summary columns, `ck_analysis_result_meta_origin`, filtered unique `uq_analysis_result_meta_analysis_perspective`, kind-table shape
- [ ] T033 [P] [US4] Seed `analysis_perspective_kind` (`GR`, `GU`, `RL`) in `infra/scripts/seed_db.py`
- [ ] T034 [US4] Mirror T032/T033 in `tests/iteration1_mirror.py` (DDL, seeds, `EXACT_MATCH_TABLES`)
- [ ] T035 [P] [US4] Add `pyarrow` dependency (`uv add pyarrow`; T-13)
- [ ] T036 [P] [US4] Result getters in `app/services/irp_gateway.py` per contracts/irp-gateway.md: `get_analysis_stats` / `get_analysis_elt` / `get_analysis_ep` / `get_analysis_plt` (raw `list[dict]`, empty = perspective absent, worker-side only); FakeIRP per-perspective payloads in `tests/unit/fakes/fake_irp.py` including empty-perspective and HD/PLT cases
- [ ] T037 [US4] `retrieve_analysis_results` worker in `app/workers/analysis_jobs.py` per contracts/worker-poller.md §5 (standard actor pattern, `max_retries=0` — failure lands the `rwb_job` in `failed` with `error_detail`, P-14): per-perspective skip on existing meta row (idempotency/resume), Parquet writes to `{OUTPUTS_BASE_DIR}/analyses/{analysis_id}/{code}/{type}.parquet` (T-14), meta row insert in one transaction, all-empty → no row no error (T-15), HD ⇒ PLT; chain from `finalize_analysis` via `ensure_pending_rwb_job` + dispatch — never fired for `rdm_id` rows (P-12)
- [ ] T038 [US4] Rendered HTML preview of the loss-numbers fragment in `docs/ui_previews/analysis_losses.html` (from `_scaffold.html`: perspective tabs, ELT summary, std dev, return-period table, OEP/AEP tables, PLT block, results-pending state) — approved before building the fragment
- [ ] T039 [US4] Loss read model in `app/services/analysis_service.py`: `analysis_result_meta` rows + EP Parquet read at view time for return-period/OEP/AEP numbers (T-13)
- [ ] T040 [US4] `GET /analyses/{analysis_id}/losses?perspective=` in new `app/routers/analyses.py` + `app/templates/partials/analysis_losses.html` per contracts/routes.md: lazy-loaded into the expanded row (`hx-trigger="toggle[...] once"` in `executed_analysis_row.html`), perspective tabs re-GET, absent perspectives render as absent tabs, PLT only when `has_plt`, results-pending until the numbers arrive — numbers only, no chart (FR-017)
- [ ] T041 [P] [US4] Update `docs/DATA_MODEL.md` §9 to the analysis-id-keyed Parquet path rule (research T-14 deviation)
- [ ] T042 [P] [US4] Unit tests in `tests/unit/`: retrieval worker (idempotent skip, empty perspective, HD/PLT, path layout, failure → `rwb_job` `failed`) and loss read model
- [ ] T043 [US4] SQL Server tier assertions for `analysis_result_meta` (origin CHECK, filtered unique) and `analysis_perspective_kind` seeds in `tests/sqlserver/`
- [ ] T044 [US4] Extend the IRP sandbox round-trip in `tests/irp/` to results retrieval (stats/ELT/EP per perspective)

**Checkpoint**: All four stories functional — quickstart Phase 3 verifiable. **STOP** for approver click-through.

---

## Phase 7: Job Monitor Listing (T-12, FR-014 — final delivery phase)

**Purpose**: The `/workflows/irp-jobs` stub becomes a minimal read-only listing so analysis jobs are visible. Deliberately last per the amended P-09 phasing.

- [X] T045 [US2] `/workflows/irp-jobs` listing: read-only `irp_job` table (type, entity/analysis name, status chip, submitted-by, submitted at, attempts; newest first, capped) in `app/routers/shell.py`, `app/templates/pages/workflows_irp_jobs.html`, `app/templates/partials/irp_jobs_table.html`, 3s fragment self-poll; verify per quickstart Phase 4

---

## Phase 8: Treaty Pass-Through (FR-018, P-08 — any delivery phase)

**Purpose**: Story-independent requirement; no tracked job, no job-monitor entry.

- [ ] T046 **Deferred** — "Add / edit in Risk Modeler ↗" link (`_rm_datasource_url(edm.name, "treaties")`, `target="_blank"`, offered for create as well) in the treaty section of `app/templates/partials/edm_detail_body.html`, plus an Alpine sliver in `app/static/js/app.js` that marks the page on link click and POSTs the existing `.../sync` route once on the next `window` focus (T-16); verify per quickstart "Treaty pass-through"

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T047 Run the full quickstart.md verification (Phases 1–4 + treaty pass-through) against the running stack; report tiers by name and count, naming any tier that did not run
- [ ] T048 Diff subtraction review per AGENTS.md: remove comments/tests restating the implementation, inline single-use helpers, remove speculative branches, compare diff size with requirement size

## Phase 10: Design-18 extensions (P-18/P-19, FR-007/FR-022–FR-024 — added 2026-08-24)

- [X] T051 [FR-007] Naming format change in `app/workers/analysis_jobs.py`: `build_full_name` → `CRE_{portfolio}_{template}`, rerun suffix `_2`, `_3`… (P-05/P-10 as amended); update the hard-coded name literals in `tests/unit/test_analysis_execution_service.py`, `test_analysis_jobs_worker.py`, `test_analysis_poller.py`
- [X] T052 [FR-011] `_analysis_failure_reason` in `app/poller/run.py` descends `tasks[] → output → errors[] → message` (first non-empty message in task order — task 1 is the engine root cause) before the top-level key scan; unit tests with the real two-task FAILED body shape
- [X] T053 [P-18/FR-022] Analyses grid: `analysis_service` read model gains `template_name`/`inserted_at`/`irp_id`/`rm_url`/`group_key`/`is_deletable`; `executed_analyses_section.html` groups Failed / In progress / Ready with a `?status=` filter baked into the poll URL; `executed_analysis_row.html` gains checkbox, RM ↗ link, Template and Submitted cells; `analysisPicks()` + tick/reopen restore in `app/static/js/app.js`
- [X] T054 [P-19/FR-023/FR-024] Multi-select delete: `irp_gateway.delete_analysis` (+ FakeIRP), `analysis_service.delete_executed_analyses` (validate batch up front, RM-first cascade, per-row failure isolation, local soft delete), `POST .../analyses/delete` routes (both page variants, `analyses-changed` HX-Trigger), retry-batch guard in `app/poller/run.py` (`deleted_at IS NULL` join); unit tests for the service and the poller guard

## Phase 11: ID-based completion backfill (T-10 as amended, FR-022 — added 2026-08-26)

- [X] T055 [T-10] Poller passes RM's `analysisId`: `_analysis_created_id` in `app/poller/run.py` extracts `tasks[].output.log.analysisId` from the FINISHED completion body; `_handle_analysis_terminal` adds it to the backfill's `input_data` as `rm_analysis_id`; unit tests for the present and absent cases
- [X] T056 [T-10] `finalize_analysis` resolves by id in `app/workers/analysis_jobs.py`: drop the name/edm_name lookup for a row-exists guard, fail the `rwb_job` when `rm_analysis_id` is missing, fetch `get_analysis_metadata(int(rm_analysis_id))`, write `irp_id` + `irp_app_analysis_id` + `settings_metadata`; delete `get_analysis_by_name` from `app/services/irp_gateway.py` and FakeIRP; rewrite the worker unit tests
- [X] T057 [FR-022] `irp_analysis.irp_app_analysis_id` (NVARCHAR(64) NULL) in `alembic/versions/0001_initial.py` + `tests/iteration1_mirror.py` + the `tests/sqlserver/test_job_tables_migration.py` column set; `analysis_service` selects it and `_rm_analysis_url` takes `appAnalysisId` (the RM web UI id) instead of `irp_id`; unit tests updated; `tests/irp/test_analysis_execution_roundtrip.py` extracts the id from the job body and asserts `appAnalysisId` is present

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none. T049 (currency defaults) added 2026-08-20 — feeds T013/T014
- **Foundational (Phase 2)**: after Setup — blocks all stories. T002 → T003 (same migration file); T005 after T002–T004; T010 after T003; T050 (`execution_item_no`, added 2026-08-20) before T016
- **US1 (Phase 3)**: after Foundational. T011 preview approved before T014/T015; T013 before T015/T016; T016 before T018–T020; T008–T010, T049, T050 feed T016
- **US2 (Phase 4)**: after US1 (renders what US1 writes). T025 preview approved before T027/T028; T026 before T027/T028
- **US3 (Phase 5)**: after US1 (parameterizes the same modal, service, and worker)
- **US4 (Phase 6)**: after US1 (chains off `finalize_analysis`); T032 → T034; T032/T035/T036 before T037; T038 preview approved before T040; T039 before T040
- **Job monitor listing (Phase 7)**: after Foundational; scheduled last per the amended P-09
- **Treaty pass-through (Phase 8)**: independent — any time after Setup
- **Polish (Phase 9)**: after all desired phases

### Story order

Sequential per P-09 and the one-story-at-a-time rule: US1 → US2 → US3 → US4 → job-monitor listing. US1+US2 together form delivery phase 1.

### Parallel Opportunities

- Foundational: T004, T006, T007, T008, T009 in parallel once T002/T003 land
- US1 tests: T021, T022, T023 in parallel after T013–T020
- US4: T033, T035, T036, T041 in parallel after T032; T042 parallel after T037/T039

## Parallel Example: Foundational

```text
After T002+T003 (migration edits):
  T004 seed_db.py kind rows
  T006 tests/sqlserver assertions
  T007 docs/DATA_MODEL.md §6/§8
  T008 irp_gateway.py submission functions
  T009 fake_irp.py counterparts
```

---

## Implementation Strategy

**MVP = US1** (suite execution): Setup + Foundational + Phase 3. Verifiable through the
database and IRP sandbox even before the US2 section renders; the modal, background
submit, naming, retry, poller mirroring, and backfill all land here.

**Incremental delivery**: US1 → approver click → US2 (delivery phase 1 complete) →
US3 (delivery phase 2) → US4 (delivery phase 3) → job-monitor listing (final phase).
Treaty pass-through (T046) slots into any pause. Commit after each task or logical group.

**Notes**:

- Retrieval/backfill failure handling is the standard rwb_job actor pattern
  (`max_retries=0`, failure → `failed` + `error_detail`, reconciler recovers
  interruption). The P-14 backoff retry, its config settings, and a retrieval-failed
  display are deferred — do not build them here (research.md "P-14 amended").
- Gateway signatures (T008, T036) are against wheel `0.6.0` — re-confirm with
  `make irp-status` before implementing; the IRP sandbox tier is the proof.
- Never start/stop containers to run a tier; if `linux-box` is down, report which tiers
  ran and stop.
