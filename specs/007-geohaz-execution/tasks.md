# Tasks: GeoHaz Execution (Iteration 5)

**Input**: Design documents from `specs/007-geohaz-execution/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — the plan's Article 12 testing section names the unit-tier coverage each story must carry. The SQL Server tier and the IRP sandbox tier need a running stack / credentials and are the developer's call; agents report and stop when they cannot run.

**Organization**: Tasks are grouped by user story. One story per implement pass — stop at each checkpoint for the approver to click the running feature (docs/UI_WORKFLOW.md).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 (launch), US2 (status & history), US3 (completion summary)

## Path Conventions

Existing single-project `app/` tree at the repository root (plan.md Project Structure). No new process, no new queue, no new nav node.

---

## Phase 1: Setup

**Purpose**: The one new configuration value everything else reads.

- [X] T001 Add `geohaz_data_versions: list[str]` to `app/config.py` (parsed from `GEOHAZ_DATA_VERSIONS`, comma-separated, first entry is the form default — research R6) and document `GEOHAZ_DATA_VERSIONS` in `infra/.env.example` (e.g. `25.0,24.0`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, gateway, and writer changes every story depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 Edit `alembic/versions/0001_initial.py`: add `irp_job.irp_portfolio_id` (Uuid, nullable) with index `ix_irp_job_irp_portfolio_id` and FK → `irp_portfolio.id` via `op.create_foreign_key` after `irp_portfolio` is created (it is created after `irp_job` — research R3 migration note); add `irp_job.request_params` (NVARCHAR(MAX) JSON, nullable); add `rwb_job_type_kind` seed row `('run_geohaz', 'Run GeoHaz', 28)` (data-model §1–2)
- [X] T003 [P] Add the `run_geohaz` row to the `rwb_job_type_kind` MERGE in `infra/scripts/seed_db.py`
- [X] T004 [P] Mirror the two new `irp_job` columns and the `run_geohaz` seed row in `tests/iteration1_mirror.py` (`EXACT_MATCH_TABLES` drift guard + `RWB_JOB_TYPE_SEED`)
- [X] T005 Add `submit_geohaz(*, edm_name, portfolio_name, version, perils, skip_prev_hazard) -> SubmitResult` and `get_geohaz_job(irp_id) -> JobStatus` to `app/services/irp_gateway.py` (Protocol, `_RealGateway`, module free functions, `__all__`): build one hazard layer per peril — `{"type": "hazard", "name": <peril>, "engineType": "RL", "version": <version>, "layerOptions": {"overrideUserDef": False, "skipPrevHazard": <bool>}}` — hazard-only, no geocode layer ever built (FR-005, research R4/R5); wrap `client.portfolio.submit_geohaz_job(portfolio_name, edm_name, layers)`; `resource_uri` comes from the returned request body, not the completion response (contracts/worker-poller.md)
- [X] T006 [P] Add optional `irp_portfolio_id` and `request_params` arguments to `record_submitted_irp_job` and `record_submission_failure` in `app/services/irp_job_service.py`, threaded into `_insert_irp_job`; `update_tracking`, `list_non_terminal`, and `TERMINAL` unchanged
- [X] T007 Extend `tests/unit/fakes/fake_irp.py` `FakeIRP` with `submit_geohaz`/`get_geohaz_job` so it keeps implementing the whole gateway protocol (depends on T005)
- [X] T008 Unit tests for the foundation in `tests/unit/test_geohaz_gateway.py` (new): parameter mapping builds one hazard layer per selected peril with the given version and `skipPrevHazard`, never a geocode layer; `resource_uri` taken from the request body; `record_submitted_irp_job`/`record_submission_failure` persist `irp_portfolio_id` and `request_params`

**Checkpoint**: Foundation ready. The developer runs `make db-rebuild` (destructive — developer's call, never an agent's) before any end-to-end check.

---

## Phase 3: User Story 1 — Launch hazard lookup on selected portfolios (Priority: P1) 🎯 MVP

**Goal**: Select portfolios on the EDM summary page, submit one pre-populated parameter set, and get one `run_geohaz` rwb_job per portfolio submitted worker-side to Risk Modeler — with per-portfolio failure isolation and P-06 exclusion.

**Independent Test**: On an EDM with two or more portfolios, select two, open the launch form, confirm the four defaults are pre-populated and editable, submit, and confirm two geohaz jobs were created — one per portfolio, same parameter set — with immediate confirmation and no Risk Modeler interaction on the request path.

### UI Preview for User Story 1 🎨

- [X] T009 [US1] Rendered HTML preview of the launch modal in `docs/ui_previews/geohaz_launch.html` (from `_scaffold.html`, reusing the `package_modal.html` classes): defaults pre-populated (data version = first configured, model family = DLM with HD disabled, earthquake + windstorm checked, missing locations = overwritten), no geocoding option, error variants (no selection; P-06-ineligible selection listing which; gate not met) — **approved before T012**

### Implementation for User Story 1

- [X] T010 [US1] Create `app/services/geohaz_service.py`: `eligible(portfolio_id)` (P-06 — no non-terminal geohaz `irp_job`, no pending/claimed `run_geohaz` rwb_job head) and `launch(*, edm_id, portfolio_ids, data_version, perils, missing_locations, actor_id) -> LaunchResult` — validate the gate (FR-004), portfolio membership + eligibility, ≥1 peril (FR-002), `data_version` ∈ `settings.geohaz_data_versions`; reject the launch whole on any failure; build the single `request_params` document (data-model §3, FR-003); per portfolio `ensure_pending_rwb_job(requestor_type='analyst_request', requestor_id=portfolio_id, rwb_job_type='run_geohaz', input_data=…)` + dispatch (contracts/data-access.md); `input_data` carries ids, names, analyst, and params (data-model §2)
- [X] T011 [P] [US1] Create `app/workers/geohaz_jobs.py`: `_run_geohaz_body(rwb_job_id)` reads `input_data`, calls `irp_gateway.submit_geohaz(...)`, on success `record_submitted_irp_job(irp_job_type='geohaz', irp_edm_id=…, irp_portfolio_id=…, irp_id=…, resource_uri=…, payload=…, request_params=…, actor_id=…)`, on exception `record_submission_failure(...)` then `JobResult.fail(...)`; module-level `_BODIES = {"run_geohaz": _run_geohaz_body}` for loader auto-discovery and the unit-tier drain; no portfolio/EDM/submission state change (contracts/worker-poller.md, FR-006/FR-014)
- [X] T012 [US1] Create `app/templates/partials/geohaz_modal.html` per the approved T009 preview: pre-populated defaults, every parameter editable, ≥1 peril required to submit, no geocoding option, error variants rendered in the modal (contracts/http-routes.md)
- [X] T013 [P] [US1] Register the geohaz modal Alpine component in `app/static/js/app.js` (the `package_modal.html` pattern — registered there, not inline)
- [X] T014 [US1] Add routes to `app/routers/edms.py`: `GET /edms/{edm_id}/geohaz/new` (modal fragment from the checked `portfolio_ids`, rendered into `#geohaz-modal-mount`) and `POST /edms/{edm_id}/geohaz` (CSRF-validated; calls `geohaz_service.launch`; on success re-render the portfolios section fragment / PRG for no-JS; on validation failure 422/409 re-render of the modal, nothing enqueued) — depends on T010, T012
- [X] T015 [US1] Add the selection form and launch button to `app/templates/partials/edm_detail_body.html`: button disabled until ≥1 eligible portfolio is checked, absent when the gate fails (no portfolios — FR-004)
- [X] T016 [US1] Add the selection checkbox cell to `app/templates/partials/portfolio_row.html`, disabled when the portfolio is P-06-ineligible
- [X] T017 [P] [US1] Unit tests in `tests/unit/test_geohaz_service.py`: gate/membership/peril/data-version validation each reject the launch whole with nothing enqueued; P-06-ineligible selection rejected; a valid launch enqueues one `run_geohaz` rwb_job per portfolio, all carrying the same `request_params` document
- [X] T018 [P] [US1] Unit tests in `tests/unit/test_run_geohaz_worker.py` (FakeIRP + synchronous drain): submit success writes a `QUEUED` geohaz `irp_job` with `irp_portfolio_id`, `request_params`, `inserted_by`, and the `irp_job_resource` row; a submit exception writes a terminal `SUBMISSION FAILED` row and fails the rwb_job; one portfolio's failure leaves its siblings' jobs untouched (FR-006)

**Checkpoint**: User Story 1 works end-to-end (launch → jobs submitted). **STOP** — the approver clicks the running feature before User Story 2 begins.

---

## Phase 4: User Story 2 — See lookup status and history per portfolio (Priority: P2)

**Goal**: The portfolios table carries the four-state "Hazard looked up?" column, refreshed by a self-terminating per-cell poll, with the per-lookup history in the expanded portfolio row. The poller tracks geohaz jobs to terminal.

**Independent Test**: Launch a lookup on one portfolio; confirm its "Hazard looked up?" column shows the job's status, updates without a manual reload, and flips to Yes on completion; expand the row and confirm the lookup history is there — while a never-looked-up portfolio shows No, no warning, and no version stamp.

### Implementation for User Story 2

- [X] T019 [P] [US2] Add `"geohaz": irp_gateway.get_geohaz_job` to `_GETTERS` in `app/poller/run.py` — single-status check only; no `_TERMINAL_HANDLERS` or `_TERMINAL_RESOLVERS` entry (nothing auto-fires on completion — Article 5; `update_tracking` already stores the terminal body, FR-020)
- [X] T020 [US2] Add the read models to `app/services/geohaz_service.py`: `lookup_states(edm_id)` (one grouped query over geohaz `irp_job` rows + pending/claimed `run_geohaz` heads → the four P-07 states per data-model §4, first match wins: Queued / in-line status / Yes / Failed / No), `cell_state(portfolio_id)` (single-portfolio variant carrying `live: bool`), and `lookup_history(portfolio_id)` (`irp_job` LEFT JOIN `app_user`, newest first: parsed `request_params`, analyst display name, `submitted_at`, `completed_at`, `status`)
- [X] T021 [US2] Add `GET /edms/{edm_id}/portfolios/{portfolio_id}/geohaz-cell` to `app/routers/edms.py` (missing portfolio → terminal empty cell, never an error page), and attach the per-portfolio geohaz cell state (`lookup_states`) and `lookup_history` to the `get_edm_detail` read model in `app/services/edm_service.py` — both detail routes render from it (contracts/data-access.md) — depends on T020
- [X] T022 [US2] Create `app/templates/partials/geohaz_cell.html`: the four-state cell; emits `hx-get … hx-trigger="every 3s" hx-target="this" hx-swap="outerHTML"` **only while** `live` — attributes omitted on terminal render so polling stops (research R8, FR-012); style via existing tokens
- [X] T023 [US2] Add the "Hazard looked up?" column header to `app/templates/partials/edm_detail_body.html`, updating `--cols` and the table `min-width` together
- [X] T024 [US2] Edit `app/templates/partials/portfolio_row.html`: include `geohaz_cell.html` in the row and add the lookup-history list to the expanded `<details>` — one record per lookup with parameters, analyst, submitted/completed timestamps, and status; empty history renders as a normal state (FR-022, US2 scenario 3); style via existing tokens
- [X] T025 [P] [US2] Unit tests: four-state derivation in `tests/unit/test_geohaz_service.py` (No with zero rows; Queued from a pending head; in-line status while non-terminal; Yes on any `FINISHED`; Failed when rows exist but none succeeded; failure-after-success stays Yes — FR-011/FR-014), poller routing (`_GETTERS["geohaz"]` resolves, no terminal handler), and cell-fragment trigger emission (poll attributes present only while live)

**Checkpoint**: User Stories 1 and 2 work together (launch → in-line status → Yes/Failed → history). **STOP** for the approver.

---

## Phase 5: User Story 3 — Read the per-layer completion summary (Priority: P3)

**Goal**: A completed lookup's record shows per-layer locations-looked-up counts parsed from the stored terminal body — zero as a value, missing detail as an unavailable state — finalized against a real sandbox capture.

**Independent Test**: Let a lookup complete; expand the portfolio row and confirm its per-layer locations-looked-up counts display in the lookup's record, that a zero layer renders as zero, and that the record shows the parameter set, launching analyst, and timestamps.

### Implementation for User Story 3

- [X] T026 [US3] Add `parse_layer_counts(last_completion_result: str | None) -> dict[str, int] | None` to `app/services/geohaz_service.py` — pure function; `None` → "unavailable" (FR-023); zero is a value, never a failure; include the parsed counts on `lookup_history` records (research R7)
- [X] T027 [US3] Render the per-layer counts in the history record in `app/templates/partials/portfolio_row.html`: count per layer, zero rendered as 0, missing/partial detail as a graceful unavailable state — never an error (FR-023, US3 scenario 3)
- [X] T028 [P] [US3] Unit tests in `tests/unit/test_geohaz_parser.py`: counts parsed, zero layer → 0, missing/partial/None body → unavailable (`None`), malformed JSON → unavailable
- [ ] T029 [US3] Create the opt-in sandbox test in `tests/irp/` (run via `make shell` + `uv run pytest tests/irp --run-irp -k geohaz`): submit one real lookup on a small sandbox portfolio with the hazard-only layer list, poll within the test, save the terminal `get_geohaz_job` body, and confirm Risk Modeler accepts the geocode-free submit (plan risk 2); then finalize the `parse_layer_counts` keys in T026 against the captured body (quickstart step 5 — the feature is unverified until this runs)

**Checkpoint**: All three stories functional. **STOP** for the approver.

---

## Phase 6: Polish & Verification

- [ ] T030 Run `uv run pytest tests/unit` and report the tier and count; the SQL Server tier (`make test-sql` — drift guard for the two columns + kind row) and the sandbox capture (T029) need the developer's stack/credentials — name what ran and what did not (AGENTS.md Reporting results)
- [ ] T031 Review the diff for subtraction per AGENTS.md: remove comments and tests that restate the implementation, inline helpers that only rename a call, remove speculative branches (e.g. the R7 `fetch_geohaz_summary` contingency stays unbuilt unless the capture demands it)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: after Setup — blocks all user stories; T007 depends on T005; T008 depends on T005–T007
- **US1 (Phase 3)**: after Foundational; T012 after T009 approval; T014 after T010 + T012
- **US2 (Phase 4)**: after US1's checkpoint (the cell and history hang off US1's launched jobs); T021 after T020
- **US3 (Phase 5)**: after US2 (counts render inside the US2 history list); T029 informs T026's final parser keys
- **Polish (Phase 6)**: after the stories being delivered

### Parallel Opportunities

- Phase 2: T003, T004, T006 in parallel after T002/alongside T005
- US1: T011, T013, T017, T018 in parallel with the template/route chain
- US2: T019 and T025 in parallel with the read-model/template chain
- US3: T028 in parallel with T027

## Parallel Example: User Story 1

```bash
# After T010 lands, these touch different files and can run together:
Task: "T011 Create app/workers/geohaz_jobs.py"
Task: "T013 Register the geohaz modal Alpine component in app/static/js/app.js"
Task: "T017 Unit tests in tests/unit/test_geohaz_service.py"
Task: "T018 Unit tests in tests/unit/test_run_geohaz_worker.py"
```

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + User Story 1**: after T018 the analyst can launch lookups and the jobs reach Risk Modeler — verifiable in the worker logs and `irp_job` rows even before the column exists. Stop at the checkpoint; the approver clicks the launch before US2 starts. Each later story adds one increment (status column, then completion counts) without changing US1's behavior. The feature is **unverified** until the developer runs `make test-sql` and the T029 sandbox capture (quickstart Reporting).
