# Tasks: Grouping

**Input**: Design documents from `/specs/012-grouping-execution/`

**Prerequisites**: plan.md, spec.md, research.md (T-01…T-12), data-model.md, contracts/, quickstart.md

**Tests**: Included — plan.md names what each tier covers. Unit tests run after every
change (`uv run pytest tests/unit`); SQL Server and IRP tiers need the developer-started
stack — report tiers by name and count, and say plainly when a tier did not run.

**Organization**: Tasks are grouped by user story. US1 (compose and run a group) carries
almost all of the code — the compose dialog, the `submit_grouping` worker, the poller,
and the `finalize_analysis` chain. US2 proves the event-rate-scheme resolution the US1 worker ships;
US3 covers the results views; US4 is verification-only (T-06 requires no code change).
Each story stops at a checkpoint for the approver to click the running feature before the
next begins.

## Format: `[ID] [P?] [Story] [Ref] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1–US4 from spec.md; setup/foundational/polish tasks carry no story label
- **[Ref]**: the `FR-nnn` / `T-nn` / `O-nn` the task closes

---

## Phase 1: Setup

**Purpose**: The sequencing prerequisite — this feature extends 011's merged analyses
grid and results views, which are not on `main` yet (as of 2026-08-27 `main` ends at the
010 merge; `analyses_merged_section.html` does not exist in this worktree).

- [X] T001 [T-01] Wait for `011-analysis-results` to merge to `main`, then merge `main` into `012-grouping-execution` and confirm the extension points exist: `app/templates/partials/analyses_merged_section.html`, the `currency_block` macro in `app/templates/partials/execute_analysis_modal.html`, the CR-04 `rwb_actor` framework, and the id-based `finalize_analysis` (010 T056 shape)
  - Proof: `git merge-base --is-ancestor` of the 011 merge commit; the named files exist; `uv run pytest tests/unit` green before any 012 change

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, seeds, mirror, and the gateway/FakeIRP functions every story calls.
No user story work can begin until this phase is complete.

- [X] T002 [T-04] [T-05] Edit `alembic/versions/0001_initial.py` per data-model.md §1–3: `irp_analysis.submission_id` (UNIQUEIDENTIFIER NULL, FK `submission.id`, index `ix_irp_analysis_submission_id`); `ck_irp_analysis_origin` gains the third leg (`edm_id IS NOT NULL OR rdm_id IS NOT NULL OR submission_id IS NOT NULL`); new filtered unique `uq_irp_analysis_live_submission_name (submission_id, name) WHERE submission_id IS NOT NULL AND deleted_at IS NULL`; new table `irp_analysis_group_member` (PK `(group_analysis_id, member_analysis_id)`, both FK `irp_analysis.id`, `inserted_at`); seed `rwb_job_type_kind` row `submit_grouping` (label "Submit grouping", sort 33)
- [X] T003 [P] [T-04] Mirror the `submit_grouping` kind seed in `infra/scripts/seed_db.py` (three-place convention with T002 and T004)
- [X] T004 [T-04] [T-05] Mirror T002/T003 in `tests/iteration1_mirror.py`: SQLite DDL for `submission_id` + `irp_analysis_group_member`, the seed row, `EXACT_MATCH_TABLES` (depends on T002, T003)
- [X] T005 [P] [T-04] [T-05] SQL Server tier assertions in `tests/sqlserver/`: `irp_analysis.submission_id` column + index, the three-leg origin CHECK, `uq_irp_analysis_live_submission_name`, the `irp_analysis_group_member` table shape, and the `submit_grouping` kind row
  - Proof: `make test-sql` green after a Rebuild (developer-started stack; report plainly if not run)
- [X] T006 [P] [T-10] Gateway additions in `app/services/irp_gateway.py` per contracts/grouping-worker.md — Protocol, `_RealGateway`, module functions: `submit_analysis_grouping(group_name, analysis_names, analysis_edm_map, group_names, currency, propagate_detailed_losses) -> (job_id, request_body)` wrapping `submit_analysis_grouping_job(..., skip_missing=False)`; `get_grouping_job(job_id) -> JobStatus` wrapping the single-status `get_analysis_grouping_job` (never the `poll_*_to_completion` variants); `get_analysis_by_name_only(name)` via `search_analyses` name filter, raising unless exactly one hit. Confirm signatures against the active wheel first (`make irp-status`; 0.6.2 already ships all three)
- [X] T007 [T-03] FakeIRP counterparts in `tests/unit/fakes/fake_irp.py` (depends on T006 signatures): grouping submit programmable per group name — success (job id + request body), duplicate-name `IRPAPIError` with the wheel's message prefix (`Analysis Group with this name already exists`), a missing-member `IRPAPIError`, and a generic submit failure; grouping job-status sequences ending FINISHED / FAILED-with-reason / CANCELLED; name-only search returning one, zero, or many hits

**Checkpoint**: Unit + SQL Server tiers green on the new schema — user story work can begin.

---

## Phase 3: User Story 1 — Compose and run a group (Priority: P1) 🎯 MVP

**Goal**: Tick ≥2 finished rows in the merged analyses grid → Group compose dialog
(submission-scoped pick-list, prefilled editable `CRE_<submission>_Group` name,
currency/scheme/vintage with env defaults, Propagate detailed output ON) →
background `submit_grouping` job → poller tracks it → the
finished group appears in the grid as a `ready` analysis, Engine column "Group",
selectable as a member of the next group (nesting).

**Independent Test**: quickstart §3 steps 1–3 and 7 — compose from two finished
analyses, watch Submitting… → Queued → Running → Ready, Engine reads "Group", the finished group is
offered in the next compose pick-list.

### UI Preview for User Story 1 🎨

- [X] T008 [US1] [FR-001] [FR-004] [FR-005] Rendered HTML preview of the compose dialog in `docs/ui_previews/group_compose_modal.html` (from `_scaffold.html`): submission-scoped pick-list mixing own/broker/group members with two rows pre-checked, prefilled editable group name, the currency block with env defaults, Propagate detailed output ON (the only setting besides currency — FR-006), and the fewer-than-two-eligible-members blocking state — approved before the Jinja2 template is built

### Implementation for User Story 1

- [X] T009 [US1] [FR-001] [FR-002] [FR-003] [FR-010] [FR-018] [T-03] [T-09] New `app/services/grouping_service.py`: eligible-member query (submission-scoped — own analyses `status_code='ready'`, broker analyses, finished groups; running/failed rows excluded); `build_group_name(submission_name)` → `CRE_{submission_name}_Group` with `name_attempt` collision handling against live group names of the submission; compose gate per contracts/routes.md (members exist, not deleted, belong to the submission, finished, ≥2; name non-empty; currency triple cache-valid — the `_validate_currency` rules; failures collected for a 422 re-render, nothing persisted); plan composition per contracts/grouping-worker.md (minted `group_analysis_id`, member entries with `kind`/`edm_name`); enqueue exactly one `rwb_job` (`requestor_type='analyst_request'`, `requestor_id=grouping_request_id`, `rwb_job_type='submit_grouping'`, `input_data`=plan) + dispatch
- [X] T010 [US1] [FR-001] [FR-004] [O-03] `GET`/`POST /submissions/{submission_id}/analyses/group` in `app/routers/submissions.py` per contracts/routes.md: GET renders the dialog into `#group-modal` with `analysis_ids` pre-checked, or the blocking message when fewer than two eligible members; POST validates CSRF, runs the T009 gate (422 re-renders with `HX-Retarget: #group-modal`), persists the plan, responds 204 with `HX-Trigger: {"grouping-submitted": true, "rwb:toast": …}` — no IRP call on the request path (T-02)
- [X] T011 [US1] [FR-004] [FR-005] [FR-006] New `app/templates/partials/group_compose_modal.html` per contracts/routes.md and the approved T008 preview: pick-list, editable name, the `currency_block` macro reused from `execute_analysis_modal.html` with `currency_defaults()` and the existing `/edms/execute/vintage-options` cascade, Propagate detailed output checked — no other setting (FR-006)
- [X] T012 [US1] [FR-001] [T-12] **Group** button (`data-group-analyses`) in the summary bar of `app/templates/partials/analyses_merged_section.html`, rendered only with submission context (submission page and submission-contextual EDM page), enabled at ≥2 ticked rows, opening the T010 GET with the ticked ids; picks JS in `app/static/js/app.js`
- [X] T013 [US1] [FR-011] [T-02] [T-03] [T-09] New `app/workers/grouping_jobs.py` with the `submit_grouping` actor (CR-04 `rwb_actor`, own queue `submit_grouping`, `max_retries=0`) per contracts/grouping-worker.md: (1) claim — idempotent INSERT of the group `irp_analysis` row by `group_analysis_id` PK (`is_group=1`, `submission_id`, `name`/`full_name` via `name_attempt`, `status_code='pending'`, `submitted_settings`=plan) + the `irp_analysis_group_member` rows, local-collision attempt loop; (2) submit via `irp_gateway.submit_analysis_grouping` with the plan's explicit currency and propagate flag — one call, the wheel resolves members and builds the simulation set internally (T-03, O-09); duplicate-name `IRPAPIError` (message prefix `Analysis Group with this name already exists`) → bounded `_n` retry updating `name`/`full_name`; every other exception → `record_submission_failure` (`irp_job_type='grouping'`, `SUBMISSION FAILED`) + group row `error` + `failure_reason`, no automatic retry; (3) one transaction — `record_submitted_irp_job` (`irp_job_type='grouping'`, `irp_analysis_id`, `requested_from_submission_id`, `irp_id`, payload, response); group row stays `pending` (spec 010 T-07)
- [X] T014 [US1] [FR-011] [T-11] Poller in `app/poller/run.py`: `_GETTERS["grouping"] = irp_gateway.get_grouping_job`; `_TERMINAL_HANDLERS["grouping"] = _handle_grouping_terminal` — FINISHED enqueues `finalize_analysis` (`requestor_type='irp_job'`, `requestor_id=job.id`, `input_data={"analysis_id": <group row id>}`), FAILED/CANCELLED set the group row `error` + `failure_reason` via `_analysis_failure_reason`; `_submission_retry` stays filtered to `irp_job_type='analysis'`
- [X] T015 [US1] [FR-012] [FR-013] [T-11] Group branch in `finalize_analysis` (`app/workers/analysis_jobs.py`): when the target row has `is_group=1` and no `edm_id`, resolve the platform id via `get_analysis_by_name_only(analysis.name)` (the 010 T056 id-based path has no `rm_analysis_id` for groups); everything after is unchanged — `get_analysis_metadata`, stamp `irp_id`/`settings_metadata`/`status_code='ready'`, chain `retrieve_analysis_results`
- [X] T016 [US1] [FR-012] [FR-014] [T-12] Group rows in the submission read model of `app/services/analysis_service.py` (`submission_id = :sid AND is_group = 1`; the EDM detail grid unchanged) rendered via `executed_analysis_row.html`: Portfolio/Template/EDM cells empty, **Engine cell "Group"**, Currency/AAL/Status/Submitted/Risk Modeler as for any analysis, selectable for View and further grouping, deletable when `is_deletable`

### Tests for User Story 1

- [X] T017 [P] [US1] [FR-003] [FR-009] [T-03] [T-09] Unit tests in `tests/unit/test_grouping_service.py`: eligibility (running/failed excluded, broker included, finished group included — nesting), gate failures (unfinished member, foreign member, deleted member, <2 members, invalid currency triple → collected errors, nothing persisted), `build_group_name` + `_n` collision suffix + 64-char truncation, plan composition carried verbatim into `rwb_job.input_data`
- [X] T018 [P] [US1] [FR-011] [T-09] Unit tests in `tests/unit/test_grouping_jobs_worker.py` against FakeIRP: success path (claim → submit → `irp_job` recorded → group row still `pending`), claim idempotency on redelivery (PK resume), duplicate-name retry updates `name`/`full_name`, submission failure recorded (`SUBMISSION FAILED` + `failure_reason`, no automatic retry)
- [X] T019 [P] [US1] [FR-011] [FR-012] [T-11] Unit tests in `tests/unit/`: poller `grouping` routing and terminal handling (FINISHED → `finalize_analysis` enqueue, FAILED reason extraction); `finalize_analysis` group branch (name-only resolution success, ambiguous/zero hits fail the job)
- [ ] T020 [US1] [T-11] IRP sandbox test in `tests/irp/test_grouping.py` (quickstart §4): inspect the `IRP_TEST_GROUP_ELT_IDS` members (no blocking problems, ELT, no selection required), submit with `num_of_simulations=1`, assert the request body, poll `get_grouping_job` single-status to FINISHED, assert `get_analysis_stats`/`get_analysis_ep` return data for the group's `analysisId` — until this passes T-11 is an assumption, not a validated claim
  - Proof: `make shell` → `uv run pytest tests/irp --run-irp -k grouping` green

**Checkpoint**: quickstart §3 steps 1–3 and 7 run end-to-end. **STOP** — approver clicks the running feature before US2.

---

## Phase 4: User Story 2 — Mixed members group without a manual pre-step (Priority: P2)

**Goal**: Prove the automated event-rate-scheme resolution the T013 worker ships:
mixed-scheme DLM pairs and DLM+HD mixes group with zero pre-steps; an invalid
member set fails with the named cause in `failure_reason` (spec O-09). No new
code — the mechanism is the wheel's auto-built `regionPerilSimulationSet` plus the
worker's uniform `SUBMISSION FAILED` recording.

**Independent Test**: quickstart §3 steps 4–5 — group two DLM analyses run under
different rate schemes with no scheme choice anywhere in the dialog; force a
member-resolution failure and see the cause in job monitoring with no grouping job in
Risk Modeler.

### Tests for User Story 2

- [X] T021 [P] [US2] [FR-007] [FR-009] [T-03] Unit test in `tests/unit/test_grouping_jobs_worker.py`: FakeIRP missing-member failure mode → `SUBMISSION FAILED` `irp_job` recorded, group row `error` + `failure_reason` = the exception text, no automatic retry (SC-005: cause named; the wheel raises before the POST, so nothing reached the platform — O-09)
- [ ] T022 [US2] [FR-007] [FR-009] IRP sandbox cases in `tests/irp/test_grouping.py`: the `IRP_TEST_GROUP_CONFLICTING_ELT_IDS` members inspect to exactly one partition requiring a selection with ≥2 options, submit once per offered scheme and each finishes with the chosen `eventRateSchemeId` in `regionPerilSimulationSet` (SC-002); a fabricated fingerprint raises `IRPGroupingValidationError` with `inspection_changed` and creates no job; Propagate detailed output verification stops at the `propagateDetailedLosses` payload flag until O-02 defines what it retains
  - Proof: `uv run pytest tests/irp --run-irp -k grouping` green; quickstart §3 step 5 walkthrough for the failure case

**Checkpoint**: US1 + US2 acceptance verifiable per quickstart. **STOP** for approver
click-through, including the CIC walkthrough: CIC validates the resolved groups against
manual Risk Modeler grouping (spec O-06) and defines what Propagate detailed output
retains (spec O-02). US2 is not accepted until both are signed off; implementation of
US3/US4 may proceed in the meantime.

---

## Phase 5: User Story 3 — Groups in the results views (Priority: P3)

**Goal**: A finished group behaves like any analysis in the results views: listed on the
submission-level results page, results open the same way, Engine column is the
disclosure, and the existing ◀/▶ neighbour-swap ordering moves group columns.

**Independent Test**: quickstart §3 step 6 — tick the finished group plus an analysis →
View; both open on `/results/analyses`; the ◀/▶ controls move the group column to
either end.

### Implementation for User Story 3

- [X] T023 [US3] [FR-013] [FR-015] [FR-016] [T-12] Verify `list_results_columns` in `app/services/analysis_service.py` resolves group ids (selects by id, no EDM filter) and adjust if it does not; group columns on `/results/analyses` render Currency/AAL/EP like analysis columns with the `ids` param order as column order — the existing neighbour-swap arrows need no change (O-04: drag-and-drop rework and O20-10 stay out of scope)

### Tests for User Story 3

- [X] T024 [P] [US3] [FR-014] [FR-015] [FR-016] Unit tests in `tests/unit/test_analysis_service.py`: the merged-grid group row carries Engine "Group" with empty Portfolio/Template/EDM cells; `list_results_columns` returns a group column for a group id mixed with analysis ids, in `ids` order

**Checkpoint**: quickstart §3 step 6 verifiable. **STOP** for approver click-through.

---

## Phase 6: User Story 4 — Workbench analyses findable outside the Workbench (Priority: P3)

**Goal**: Every Workbench-submitted individual analysis carries the bare-submission-name
tag in the platform. T-06 confirmed this is 011's existing `tag_names` path — no code
change; this phase pins the behavior and verifies it platform-side.

**Independent Test**: quickstart §3 step 8 — in Risk Modeler, filter analyses by the
submission-name tag; every Workbench-submitted analysis of the submission appears.

- [X] T025 [US4] [FR-017] [T-06] [O-05] Confirm the analysis-submit path applies the bare submission name via `tag_names` and that a unit test in `tests/unit/test_analysis_execution_service.py` asserts it (extend the test if 011 left it uncovered); confirm groups submit untagged — the grouping request body has no tag field (O-07)
  - Proof: the named unit assertion green; quickstart §3 step 8 platform filter shows every Workbench-submitted analysis of the submission

**Checkpoint**: All four stories verifiable. **STOP** for approver click-through.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T026 [P] Reconcile `docs/DATA_MODEL.md` with the landed schema: `irp_analysis.submission_id` + the three-leg origin CHECK + `uq_irp_analysis_live_submission_name`, the `irp_analysis_group_member` table, the `submit_grouping` job type
- [ ] T027 Run the full quickstart.md verification (§1–§4) against the running stack; report tiers by name and count, naming any tier that did not run (the stack is the developer's call — report and stop if it is down)
- [ ] T028 Diff subtraction review per AGENTS.md: remove comments/tests restating the implementation, inline single-use helpers, remove speculative branches, compare diff size with requirement size

---

## Phase 8: irp-integration 0.8.0rc1 migration (inspect-then-submit)

**Purpose**: The package replaced the name-based automatic grouping API with
`client.grouping.inspect()` / `submit()` / `get_job()`. Members are Platform ids,
event-rate schemes are the analyst's explicit choice, and the simulation count is a
caller input. Decisions: research.md Clarifications 2026-09-02.

- [X] T029 [O-06] [O-09] [FR-007] [FR-019] [FR-020] Revise spec 012 documents for the inspect-then-submit contract: spec.md (outcome, scope, non-negotiables 1 and 6, O-06/O-08/O-09, US-2, FR-006/007/009/019/020, SC-002/005), plan.md (design summary, T-02/T-03/T-10/T-11, Article 2 deviation, rule-8 note), research.md (T-02/T-03/T-10/T-11, assumptions, clarifications), contracts, data-model.md §4–5, quickstart.md; PRD §13.3/§14.3/§14.4/§16.4, FUNCTIONAL_REQUIREMENTS §6, and the planned grouping sequence diagrams lose the removed method names and the "never user-picked" rule
- [X] T030 [T-10] [T-11] Gateway in `app/services/irp_gateway.py`: replace `submit_analysis_grouping` with `inspect_grouping(analysis_ids)`, `submit_grouping(analysis_ids, group_name, currency, propagate_detailed_losses, num_of_simulations, event_rate_selections, expected_inspection_fingerprint)`, `get_grouping_job` over `client.grouping.get_job`, and `count_analyses_named`; re-export the package grouping types and `IRPGroupingValidationError`; Protocol, `_RealGateway`, module functions, `__all__`
- [X] T031 [T-03] [T-10] `grouping_service.py`: `GroupMember.irp_id`, `inspect_grouping` → `GroupingInspectionView` (suggested simulation count), `request_grouping` gate rules for inspected ids, fingerprint, simulation count, selections; plan carries `irp_id`, `num_of_simulations`, `event_rate_selections`, `expected_inspection_fingerprint`; `edm_name` removed
- [X] T032 [T-02] [FR-007] [FR-019] [FR-020] Routes and templates: `POST …/analyses/group/inspect` in `app/routers/submissions.py`; `group_compose_modal.html` gains Inspect members, the `#group-inspection` div, the treaty notice, the source-guarded close handler; new `partials/group_inspection.html` (errors, blocked, choices, ready); `#group-modal` mounts allow the 422 swap; `groupComposeModal` in `app.js` gains `canInspect`, `clearInspection`, and the inspection-aware `canSubmit`
- [X] T033 [T-11] [O-09] Worker `app/workers/grouping_jobs.py`: tenant-wide `count_analyses_named` pre-check with `_n` retry replaces the `DUPLICATE_NAME_PREFIX` match; submit by Platform id via `irp_gateway.submit_grouping`; `IRPGroupingValidationError.problems` → `failure_reason`; `irp_job.last_submission_payload` = exact request body, `last_submission_response` = `{"job_id"}`
- [X] T034 [T-03] FakeIRP: `inspect_grouping` (seeded or default pure-ELT inspection), `submit_grouping` (typed kwargs recorded; generic, structured, and `inspection_changed` failure knobs), `count_analyses_named`; `seed_grouping_inspection` helper over the gateway re-exports
- [X] T035 [FR-009] [FR-019] Unit tests: `test_grouping_service.py` (new gate rules, plan keys, inspection view with no writes), `test_grouping_routes.py` (dialog, inspect fragment states, submit with and without fingerprint), `test_grouping_jobs_worker.py` (typed submit kwargs, request body recorded, pre-check retry, structured failure reasons), `test_grouping_poller.py` (seeds gain `irp_id`)
- [X] T036 [T-11] Rewrite `tests/irp/test_grouping.py` per quickstart §4 (T020/T022 above) — unverified until run inside `linux-box` with `--run-irp`
- [X] T037 Pin irp-integration `0.8.0rc1` from TestPyPI (`pyproject.toml`, `uv.lock`); production `irp-pypi` unchanged
- [X] T038 Verification: unit tier green for every grouping module with no new failures against the icon-symlink baseline; `ruff check` on changed files; no reference to `submit_analysis_grouping`, `get_analysis_grouping_job`, `build_region_peril_simulation_set`, `analysis_edm_map`, `group_names`, `skip_missing`, `missing_group_members`, or `DUPLICATE_NAME_PREFIX` under `app/`, `tests/`, `specs/012-grouping-execution/`, PRD, FUNCTIONAL_REQUIREMENTS, or the planned sequence diagrams

---

## Phase 9: three-screen compose dialog

**Purpose**: Replace the single-scroll compose dialog with the approved
three-screen flow (Members → Inspection → Settings) from the rendered preview
`docs/ui_previews/group_compose_modal.html`. Decisions: research.md
Clarifications, Session 2026-09-02 (compose flow). The plan the dialog emits
and the worker do not change.

- [X] T039 [FR-019] Revise spec 012 documents for the compose flow: contracts/routes.md (three screens, oob targets, submit 422 retarget, hidden simulation count for ELT, `required` dropped), plan.md (design bullets 1–2, UI row, project structure), spec.md FR-019, research.md (compose-flow session superseding the "no preview" answer), quickstart.md §1 and §3 steps 2–5
- [X] T040 View model `app/services/grouping_view.py`: `build_inspection_screen(view) -> InspectionScreen` with `PartitionRow` (key, engine versions from the region facts, member display names, `mode` choose → resolved → none), `SchemeOption` (`label or "Scheme <id>"`, member count per scheme, the posted JSON value), `ProblemText` (wheel message + member names)
- [X] T041 [FR-019] Templates, JS, CSS: `group_compose_modal.html` shell + three `x-show` panes + step footer; `group_inspection.html` error/blocked/ready branches with the `#group-summary` and `#group-sims` oob divs; new `group_submit_errors.html`; `groupComposeModal` in `app.js` (`step`, `filter`, `inspect`, `back`, `toSettings`, `clearInspection`, `recompute`); `.modal-card--group` and `.steps*` in `components.css`, `.insp-*`, `.spin`, `.sum-*`, `.check-row` in `submissions.css`
- [X] T042 Routes in `app/routers/submissions.py`: GET renders the dialog only; inspect passes `screen=build_inspection_screen(view)`; submit 422 renders `group_submit_errors.html` with `HX-Retarget: #group-submit-errors`
- [X] T043 Unit tests: `test_grouping_view.py` (row modes, option labels and counts, engine versions, problem member names); `test_grouping_routes.py` assertions for the three screens (Next, facts strip, table cells, hidden ELT count, PLT hint, blocked notice, Retry, 422 retarget)
- [ ] T044 Click-through on the developer's stack per quickstart.md §3 steps 2–5, then the diff subtraction review
- [X] T045 [FR-020] Treaty term mismatches from `inspection.warnings` (rc4 `inconsistent_treaty_terms`): `TreatyMismatch` in `grouping_view.py` (treaty number, differing terms through `treaty_service.humanize_key`, member names, treaty ids — unpaired), the amber notices and the treaty mismatch count on screen 2, the Treaties row in `#group-summary`; `FakeIRP.seed_grouping_inspection(warnings=)`; unit tests; spec.md non-negotiable 6, FR-020, O-09, research.md 2026-09-03 session, contracts/routes.md, plan.md, quickstart.md §3
- [X] T047 [FR-020] Risk Modeler's treaty mismatch table on rc5 `GroupingProblem.treaties`: `TREATY_COLUMNS`, `TreatyMismatchRow` and the reshaped `TreatyMismatch` (`differing_keys`, `rows`, `analysis_count`) in `grouping_view.py`; `treaty_service.display_value` made public; the eleven-column table in `group_inspection.html` in place of the amber notices; the three macros extracted to `partials/treaty_macros.html`; `.insp-table--treaty`, `.insp-diff` and `.insp-treaty*` in `submissions.css` and the dead `.insp-notice--warn` removed; `GroupingTreaty` re-exported from `irp_gateway.py`; unit tests; the treaty stages in `docs/ui_previews/group_compose_modal.html`; spec.md FR-020, contracts/routes.md, research.md 2026-09-03 session, plan.md, quickstart.md §3
- [ ] T046 Plain-language blocking-problem texts by `GroupingProblem.code` in `grouping_view.py` in place of the package `message`; verification grep for `Inspect members` and `group-inspect-indicator`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: T001 blocks everything — the extension points live in 011.
- **Foundational (Phase 2)**: after T001. T002 → T004 (mirror follows the migration); T003 [P]; T005 after T002; T007 after T006 (FakeIRP mirrors the Protocol).
- **US1 (Phase 3)**: after Phase 2. T008 approval before T011; T009 before T010; T013 after T006/T007/T009; T014 after T013; T015 after T006; T016 independent of T013–T015; T017–T019 [P] after their targets; T020 after T013–T015.
- **US2 (Phase 4)**: after US1 — it tests the T013 worker. T021 needs T007's resolution-failure mode; T022 extends T020's sandbox suite.
- **US3 (Phase 5)**: after US1 (needs finished group rows). T023 → T024.
- **US4 (Phase 6)**: independent of US1–US3 code; only T001 gates it. Can run any time after Phase 1.
- **Polish (Phase 7)**: after the desired stories.

### Parallel Opportunities

- Phase 2: T003, T005, T006 in parallel after T002; T007 follows T006.
- US1: T011 + T012 + T016 touch different files and can run in parallel once T009/T010 exist; T017, T018, T019 in parallel.
- US4 (T025) can run in parallel with any story phase.

### Parallel Example: after T009/T010 land

```bash
Task: "Compose dialog template in app/templates/partials/group_compose_modal.html"   # T011
Task: "Group button + picks JS in analyses_merged_section.html / app.js"             # T012
Task: "Group rows in the submission read model in app/services/analysis_service.py"  # T016
```

---

## Implementation Strategy

1. T001 first — nothing proceeds until 011 is on `main` and merged in.
2. Phase 2, then **Rebuild** the dev DB (`make db-rebuild` is the developer's call).
3. US1 is the MVP: compose → submit → track → finished group in the grid. Stop at the checkpoint.
4. US2 and US3 are thin proofs/extensions on top of US1; US4 is verification-only.
5. The IRP sandbox tasks (T020, T022) close the one remaining Assumed decision (T-11, group results retrieval) — not a validated claim until the sandbox run passes.
