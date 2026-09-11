# Tasks: Analysis Run Details in the Expanded Row

**Input**: Design documents from `specs/015-analysis-run-details/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md,
contracts/settings-metadata-resolved.md, contracts/irp-gateway.md,
contracts/captures/*.json, quickstart.md

**Tests**: plan.md § Testing names the unit, SQL Server and IRP-tier coverage
the feature ships with, so test tasks are included. Unit tests run with
`uv run pytest tests/unit` from any host shell. The SQL Server and IRP tiers
need `linux-box` up; an agent reports and stops if it is down (AGENTS.md
§ Testing).

**Organization**: Phase 2 is the irp-integration release and the gateway
wrapper every story reads through. Stories 1 and 2 share the non-group
capture path, so story 1 builds the whole `resolved` pipeline for own and
broker analyses and story 2 adds the PLT half of the display. Stories 3 and 4
follow. Implement one story per pass and stop at each checkpoint for the
approver to click the running feature (docs/UI_WORKFLOW.md).

## Format: `[ID] [P?] [Story] [Ref] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1–US4 from spec.md
- **[Ref]**: `FR-nnn` from spec.md, `T-nn`/`P-nn` from the decision tables.
  Omitted only on setup and cleanup tasks.
- File paths are repo-relative. `../irp-integration` is the sibling checkout
  the plan names for T-06.

## Path Conventions

Single project: `app/` (services, workers, templates), `tests/unit`,
`tests/irp`, `docs/`. Fixture payloads come from
`specs/015-analysis-run-details/contracts/captures/`.

---

## Phase 1: Setup

**Purpose**: Turn the captured payloads into fixtures every later phase reads.

- [ ] T001 Create `tests/unit/run_details_fixtures.py` that loads each file in `specs/015-analysis-run-details/contracts/captures/` (`own_dlm`, `own_hd`, `broker_dlm`, `broker_dlm_no_treaties`, `broker_group_ingp`, `group_mixed_rm_made`, `group_elt_workbench_made`, `group_plt_workbench_made`, `reference_data`, `search_analyses_item`) and exposes, per capture, its `get_analysis_by_id`, `get_regions` and `search_analysis_treaties_paginated` payloads by name
  - Proof: `uv run pytest tests/unit -k run_details_fixtures` imports every capture without error
- [ ] T002 [P] Replace the knowledge-base fixtures `SETTINGS_FULL` and `SETTINGS_PARTIAL` in `tests/unit/test_broker_analyses.py` with the `broker_dlm` and `broker_dlm_no_treaties` detail payloads from T001; keep `SETTINGS_LIVE`; existing assertions on flat `currencyCode`, `subperil` and `lineOfBusiness` move to the live keys (`currency.currencyCode`, `subPeril`) or are deleted where the field no longer exists; replace the second `SETTINGS_FULL` at `tests/unit/test_analysis_service.py:30` (flat `currencyCode`, `peril`, `region`) with the `own_dlm` detail payload the same way
  - Proof: `uv run pytest tests/unit/test_broker_analyses.py tests/unit/test_analysis_service.py` passes with no knowledge-base shape left in either module

---

## Phase 2: Foundational

**Purpose**: The package describe method, its TestPyPI release, and the
workbench gateway wrapper. Every capture path in Phases 3–6 calls
`irp_gateway.describe_analysis_run`; nothing in a story can be verified end
to end until this phase is done.

**⚠️ CRITICAL**: T003–T006 change `../irp-integration`, not this repo. The
method's name and signature are Assumed until the wheel exists; T008
re-confirms them.

- [ ] T003 [T-06] Add a single-analysis describe method (working name `describe_run(analysis_id)`) to `../irp-integration/irp_integration/analysis.py` (or `grouping.py`, the approver's call) that reuses `GroupingManager._inspect`'s region, PET and scheme-naming code (`../irp-integration/irp_integration/grouping.py:686–960`) and returns a frozen `RunDescription(analysis_id, is_group, regions: tuple[GroupingRegionFact, ...], event_rate_scheme_names: Mapping[int, str], treaties: tuple[AppliedTreaty, ...])` per `contracts/irp-gateway.md`; `AppliedTreaty` carries `treaty_id`, `treaty_number`, `treaty_name` plus the terms `GroupingTreaty` normalizes; `is_group` is true when `isGroup` is true or the detail carries an `eventRateSchemes` / `simulationSets` property; a 404 on the analysis raises, a missing region list yields empty `regions`, treaty and reference failures raise `IRPAPIError`; the method's name and placement are closed by T006
  - Proof: package unit tests in T004
- [ ] T004 [T-06] [T-02] [T-04] Add package tests for the describe method in `../irp-integration/tests/` using the trimmed captures (`own_dlm` → 23 region rows all scheme 739, name resolved from `reference_data`; `own_hd` → one PLT row, `pet_id` 12 named "RMS 2020 Time-Dependent Rates" through model version 3.0 + `get_pet_metadata_exact`, `periods` 1,978,459; `broker_dlm` → scheme 577 and two treaties with `treaty_name` "XPR_1_100_Fld"; an unnamed PET id yields `pet_name` None and no `SimulationSet` row); run with the workbench venv and `PYTHONPATH=.` per memory note `irp-integration-local-checkout-testing`
  - Proof: package tests pass under the workbench venv
- [ ] T005 [T-06] Regenerate `../irp-integration/docs/api.md` (`uv run --no-project`), tag the release and publish to TestPyPI
  - Proof: the new version appears on TestPyPI
- [ ] T006 [T-06] Pin the release here with `make irp-testpypi`; confirm `make irp-status` names it and the describe method's name and signature match `contracts/irp-gateway.md`; if they differ, update `contracts/irp-gateway.md` and plan.md T-06's status from Assumed to Approved
  - Proof: `uv run python -c "from irp_integration import IRPClient; help(IRPClient().analysis.describe_run)"` (or the released name) prints the signature; `uv.lock` records the TestPyPI version
- [ ] T007 [T-06] [P-06] [P-07] Add `ResolvedPartition`, `AppliedTreaty` and `ResolvedRun` frozen dataclasses, a `describe_analysis_run(*, analysis_id: int) -> ResolvedRun` method on the `IRPGateway` Protocol (`app/services/irp_gateway.py:315`) and `_RealGateway` (`:437`), and the module function beside `get_analysis_metadata` (`:1342`); the wrapper calls the package method once and collapses `regions` to distinct (`region_code`, `peril_code`, `framework`), names each ELT partition from `event_rate_scheme_names` and each PLT partition from `pet_name`/`periods`, sorts partitions by `region_code` then `peril_code`, and reduces treaties to one per `treaty_id` sorted by `number`; `AppliedTreaty` carries `treaty_id`, `number`, `name`, `currency` (the code), `occurrence_limit`, `risk_limit`, `attachment_point`, `retention_amount` (T-05)
  - Proof: T009 gateway tests
- [ ] T008 [P] [T-06] [FR-013] Add `describe_analysis_run` to `FakeIRP` in `tests/unit/fakes/fake_irp.py`: `add_analysis(...)` gains `regions=`, `treaties=`, `scheme_names=`, `pet_names=` seeds shaped like the captures and the method returns the collapsed `ResolvedRun` from them; add `raise_on_describe_run: set[str]` keyed by analysis id for the blank-and-continue tests; record each call in the fake's call log so a render test can assert zero gateway calls
  - Proof: T009 asserts the fake and the real wrapper collapse the same seeds to the same `ResolvedRun`
- [ ] T009 [T-06] [T-02] [T-04] [P-06] [P-07] Add `tests/unit/test_irp_gateway.py` cases for the collapse: `own_dlm`'s 23 sub-region rows → one ELT partition (NA · WS, scheme 739, named); `own_hd` → one PLT partition with `simulation_set_id` 12, its PET name and 1,978,459 periods; an unnamed scheme or PET id → `*_name` None, partition kept; `broker_dlm`'s two treaty rows → sorted by `number`, each carrying currency USD, `occurrence_limit`, `risk_limit`, `attachment_point` and `retention_amount` from the capture; duplicate `treaty_id` rows → one entry; partitions from two regions → sorted by `region_code` then `peril_code`
  - Proof: `uv run pytest tests/unit/test_irp_gateway.py`
- [ ] T010 [P] [T-06] Add the opt-in IRP-tier probe `tests/irp/test_describe_run.py` calling `irp_gateway.describe_analysis_run` for 5741781 (own DLM → scheme 739), 5733173 (own HD → PET 12 "RMS 2020 Time-Dependent Rates", 1,978,459 periods) and 5689560 (broker DLM → two treaties), gated by `--run-irp` like `tests/irp/test_grouping.py`
  - Proof: `make shell` then `uv run pytest tests/irp -k describe --run-irp` — this tier does not run in CI; report it as not run when `linux-box` is down

**Checkpoint**: `uv run pytest tests/unit` green; `make irp-status` names the
TestPyPI release. Story work may begin.

---

## Phase 3: User Story 1 — The broker analysis names its event rate scheme (Priority: P1) 🎯 MVP

**Goal**: A broker-imported DLM analysis's expanded row names its event rate
scheme, read from `settings_metadata.resolved` written by
`backfill_rdm_analyses`. This phase also builds the `resolved` writer for own
analyses in `finalize_analysis` and the single reader, because the reader's
ELT branch is the same for both origins (FR-016).

**Independent Test**: Manual-sync an RDM; expand a DLM analysis in the
submission's RDM analyses section; Event rate scheme names the scheme, and the
Group dialog's inspect shows the same scheme. A forced describe failure in the
unit tier leaves that analysis's field reading *not returned* while the other
analyses in the RDM are captured.

### Implementation for User Story 1

- [ ] T011 [US1] [T-01] [T-07] [FR-013] [FR-014] Add a module-level builder in `app/workers/analysis_jobs.py` (working name `_resolved_payload(run: ResolvedRun, *, partitions: list[dict] | None = None) -> dict`) that turns a `ResolvedRun` into the `resolved` dict from `contracts/settings-metadata-resolved.md` — `partitions[]` with `region_code`, `peril_code`, `framework`, `event_rate_scheme {id, name}` (ELT) / `simulation_set {id, name, periods}` (PLT), `treaties[]` with `id`, `number`, `name`, `currency`, `occurrence_limit`, `risk_limit`, `attachment_point`, `retention_amount`, and `captured_at` as UTC ISO-8601 — importable by `app/workers/entity_jobs.py`
  - Proof: T014 asserts the exact key set for `own_dlm`
- [ ] T012 [US1] [T-07] [FR-013] [FR-014] In `_finalize_analysis_body` (`app/workers/analysis_jobs.py:220–300`), after `get_analysis_metadata` succeeds and before the UPDATE at `:280`, call `irp_gateway.describe_analysis_run(analysis_id=int(rm_id))` for a non-group row inside `try/except`; on success set `meta.payload["resolved"]` from T011 and store it in the same `settings_metadata` UPDATE; on failure log a warning naming the analysis and leave `resolved` absent while the UPDATE, `status_code = 'ready'` and the results-retrieval chain proceed unchanged
  - Proof: T014 success and failure cases
- [ ] T013 [US1] [T-07] [FR-001] [FR-014] In `_backfill_rdm_analyses_body` (`app/workers/entity_jobs.py:215–275`), inside the existing per-hit metadata loop, call `irp_gateway.describe_analysis_run(analysis_id=int(hit.analysis_id))` for each hit whose metadata read succeeded and whose detail carries neither `eventRateSchemes` nor `simulationSets` property; write `resolved` into that hit's payload before `_UPDATE_ANALYSIS_DETAIL` runs; a describe failure logs, leaves that hit's `resolved` absent, increments a counter beside `metadata_failures`, and never fails the job; all gateway calls stay before `get_connection("WORKBENCH")` opens (Article 11)
  - Proof: T015
- [ ] T014 [P] [US1] [T-07] [FR-013] [FR-014] Add `tests/unit/test_analysis_jobs_worker.py` cases: `finalize_analysis` on an own DLM seeded from `own_dlm` writes `settings_metadata.resolved.partitions` with one NA · WS ELT entry naming scheme 739 and `treaties` with the seeded rows; with `raise_on_describe_run` set, the row still reaches `ready`, `settings_metadata` holds the detail without `resolved`, and `retrieve_analysis_results` is still enqueued
  - Proof: `uv run pytest tests/unit/test_analysis_jobs_worker.py -k resolved`
- [ ] T015 [P] [US1] [T-07] [FR-001] [FR-014] Add `tests/unit/test_rdm_sync.py` cases: a backfill over two broker hits seeded from `broker_dlm` and `broker_dlm_no_treaties` writes `resolved` on both (scheme 577 on each, two treaties vs `treaties: []`); with `raise_on_describe_run` for one hit, that hit's `settings_metadata` has no `resolved`, the other hit's does, the job result is success, and the prune/insert transaction ran
  - Proof: `uv run pytest tests/unit/test_rdm_sync.py -k resolved`
- [ ] T016 [US1] [T-08] [FR-016] Add to `app/services/analysis_service.py` a `ResolvedDetails` dataclass (`partitions: list[PartitionDisplay]`, `treaties: list[TreatyDisplay] | None`, `partitions_read: bool`, `single_scheme: str | None`, `single_simulation_set: str | None`, `summary: str | None`) and one reader `_resolved_view(settings: dict | None) -> ResolvedDetails` over `settings_metadata["resolved"]`: `partitions` absent → `partitions_read` False and both single fields None; exactly one partition → `single_scheme` = its scheme name (ELT) or `single_simulation_set` = `"<name> (<periods:,> periods)"` (PLT); two or more → `partitions` list entries of `"<region_code> · <peril_code> — <scheme> — <set> (<periods> periods)"` omitting a null half; `treaties` None when absent, `[]` when empty, else `"<number> · <name> · <currency>"` entries in stored order; `summary` is the single field's value or the partition entries joined with `"; "`, for the Compare modal; attach the result as `a.resolved` wherever `a.display` is built from `_to_display` (the RDM analyses section, own analyses section, and `list_comparable_analyses`)
  - Proof: T018
- [ ] T017 [US1] [FR-001] [FR-006] [P-08] In `app/templates/partials/analysis_results_inline.html` replace the `('Event rate scheme', d.event_rate_scheme)` entry in the `settings-grid` loop (`:21`) with: when `a.resolved.partitions_read` is false, a `Run details` entry reading `not returned` (the framework is unknown, so neither field label is used); else when `a.resolved.partitions|length >= 2`, no single entry; else when `a.resolved.single_simulation_set`, a `Simulation set` entry; else an `Event rate scheme` entry reading `a.resolved.single_scheme`; add after the Members block a `settings-grid__wide` list `<dt>Run details</dt>` of `a.resolved.partitions` rendered only when two or more partitions exist (P-08)
  - Proof: T018 template renders
- [ ] T018 [US1] [FR-001] [FR-002] [FR-006] [FR-013] [FR-015] [FR-016] Add `tests/unit/test_analysis_service.py` cases over the T001 fixtures with `resolved` attached: `broker_dlm` row renders `Event rate scheme` naming scheme 577 and no `Simulation set` label; a row whose `settings_metadata` lacks `resolved` renders a `Run details` entry reading `not returned` and neither an `Event rate scheme` nor a `Simulation set` label; a row whose `resolved` has `treaties` but no `partitions` renders the same and the rest of the grid unchanged; rendering the RDM analyses section for a submission makes zero `FakeIRP` calls (FR-013); the scheme name the reader returns for `broker_dlm` equals the name `grouping_view` shows for the same `GroupingRegionFact` seeded in `tests/unit/grouping_inspections.py` (FR-002)
  - Proof: `uv run pytest tests/unit/test_analysis_service.py -k resolved`
- [ ] T019 [US1] [T-08] Delete `_event_rate_scheme` (`app/services/analysis_service.py:359–376`) and the `event_rate_scheme` field of `AnalysisSettings` (`:56`); `ComparableAnalysis.event_rate_scheme` (`:1020`) becomes `run_details: str | None` set from `a.resolved.summary` at `:1063` and `:1074`; update `compare_modal.html`'s `row_meta` macro (`:15`) to read `a.run_details or 'run details not returned'` with title "Run details"
  - Proof: `grep -rn "_event_rate_scheme\|scheme not returned" app tests` returns nothing; T020
- [ ] T020 [P] [US1] [FR-017] [P-05] Update `tests/unit/test_results_comparison.py` (and `test_comparison_service.py` where it asserts the metadata line): a `broker_dlm` row's Compare line names scheme 577, the same string the expanded row's `Event rate scheme` shows; a row without `resolved` reads `run details not returned`
  - Proof: `uv run pytest tests/unit/test_results_comparison.py tests/unit/test_comparison_service.py`
- [ ] T021 [US1] Run `uv run pytest tests/unit` and fix any test still importing `SETTINGS_FULL`, `SETTINGS_PARTIAL`, `_event_rate_scheme` or `AnalysisSettings.event_rate_scheme`
  - Proof: unit tier green, count reported

**Checkpoint**: Manual-sync an RDM, expand a broker DLM row: Event rate scheme
names the scheme; Group dialog inspect shows the same name. **STOP** for the
approver to click before story 2.

---

## Phase 4: User Story 2 — The HD analysis names its simulation set (Priority: P1)

**Goal**: A finished HD analysis's expanded row shows `Simulation set:
<PET name> (<N> periods)` and no Event rate scheme; a DLM row shows the
reverse; the Compare modal's line follows the same rule.

**Independent Test**: Run the HD and a DLM template on one portfolio, wait for
`ready`, expand each row and open the Compare modal.

### Implementation for User Story 2

- [ ] T022 [US2] [FR-003] [FR-004] [FR-005] [T-04] Confirm in `app/services/irp_gateway.py` `describe_analysis_run` that a PLT partition's `simulation_set_id` is the region row's `pet_id` and `simulation_set_name` is the PET name the package resolved through model version + `get_pet_metadata_exact` (never a `SimulationSet` row id); `periods` comes from the region row's `periods`; an unnamed PET yields `simulation_set_name` None with the id and periods kept
  - Proof: T009's `own_hd` case, extended to assert `simulation_set_id == 12` and `periods == 1_978_459`
- [ ] T023 [US2] [FR-003] [FR-004] [FR-006] Confirm the `_resolved_view` PLT branch in `app/services/analysis_service.py` formats `single_simulation_set` as `"<name> (<periods:,> periods)"`, falls back to `"PET <id>"` when the name is None (the label `grouping_view.py` already uses), and leaves `single_scheme` None for a PLT partition; confirm `analysis_results_inline.html` renders `Simulation set` and no `Event rate scheme` for that row
  - Proof: T024
- [ ] T024 [P] [US2] [FR-003] [FR-004] [FR-006] [FR-017] Add `tests/unit/test_analysis_service.py` cases: `own_hd` row renders `Simulation set` reading `RMS 2020 Time-Dependent Rates (1,978,459 periods)` and no `Event rate scheme` label; `own_dlm` row renders `Event rate scheme` and no `Simulation set` label; a PLT partition with `name: null` renders `PET 12 (1,978,459 periods)`; an HD row whose `resolved` lacks `partitions` renders a `Run details` entry reading `not returned` with settings and condensed results unchanged; the Compare line for `own_hd` equals the expanded row's Simulation set string and the `own_dlm` line equals its scheme
  - Proof: `uv run pytest tests/unit/test_analysis_service.py -k "hd or simulation_set"`
- [ ] T025 [P] [US2] [FR-003] [T-07] Add a `tests/unit/test_analysis_jobs_worker.py` case: `finalize_analysis` on an own HD seeded from `own_hd` writes one PLT partition with `simulation_set {id: 12, name, periods: 1978459}` and `event_rate_scheme: null`
  - Proof: `uv run pytest tests/unit/test_analysis_jobs_worker.py -k hd`

**Checkpoint**: Expand the HD row and the DLM row on the same portfolio; each
shows only its own field; the Compare modal names the same values. **STOP**
for the approver before story 3.

---

## Phase 5: User Story 3 — The mixed group names both (Priority: P2)

**Goal**: A finished group's row lists one entry per region and peril from the
detail's `eventRateSchemes` or `simulationSets` property, each naming its
scheme and its simulation set, sorted by region code then peril code; a
one-partition group renders the single field.

**Independent Test**: Group an HD and a DLM analysis, choose a simulation set
for the DLM partition, Finish, wait for `ready`, expand the group.

### Implementation for User Story 3

- [ ] T026 [US3] [T-03] [FR-007] [FR-008] [FR-009] [P-06] Add `_group_partitions(detail: dict) -> list[dict] | None` to `app/workers/analysis_jobs.py`: find the `additionalProperties` entry keyed `eventRateSchemes` or `simulationSets`; for each property value emit a partition with `regionCode` → `region_code`, `perilCode` → `peril_code`, `framework`, `event_rate_scheme {id, name}` from `eventRateSchemeId`/`eventRateSchemeName` when the id is positive (else null), `simulation_set {id, name, periods}` from `simulationSetId`/`simulationSetName`/`simulationPeriods` when the id is positive (else null); sort by `region_code` then `peril_code`; return None when neither property is present
  - Proof: T029
- [ ] T027 [US3] [T-03] [T-07] [FR-007] [FR-011a] [P-07] In `_finalize_analysis_body` (`app/workers/analysis_jobs.py`), when `meta.payload` carries either property (a group, whatever `isGroup` says), build `partitions` from T026 and call `irp_gateway.describe_analysis_run` for the treaties, ignoring the `partitions` it returns, and pass both into T011's builder; a describe failure writes `resolved` with `partitions` and no `treaties`; the treaty list holds each `treaty_id` once
  - Proof: T030
- [ ] T028 [US3] [T-03] [FR-007] In `_backfill_rdm_analyses_body` (`app/workers/entity_jobs.py`), apply the same property check: a broker hit whose detail carries `eventRateSchemes` or `simulationSets` (the INGP group 5723350 shape in `broker_group_ingp`) gets `partitions` from T026 and treaties from the describe call, mirroring T027
  - Proof: T030's broker INGP case
- [ ] T029 [P] [US3] [T-03] [FR-007] [FR-009] [P-06] Add `tests/unit/test_analysis_jobs_worker.py` cases for `_group_partitions` over each capture: `group_mixed_rm_made` (`simulationSets`) → three partitions JP · WS (PLT, set 15 "RMS V2.0 Stochastic Event Rates - Typhoon Events Only", 50,000, no scheme), NA · EQ (ELT, scheme 163 + set 87), NA · WS (ELT, scheme 738 + set 146), in that order; `group_elt_workbench_made` (`eventRateSchemes`) → NA · EQ 163, NA · WS 739 with `simulation_set` null; `group_plt_workbench_made` → one PLT partition set 14, 50,000; `broker_group_ingp` → its property list; `own_dlm` detail → None
  - Proof: `uv run pytest tests/unit/test_analysis_jobs_worker.py -k group_partitions`
- [ ] T030 [P] [US3] [T-07] [FR-007] [FR-011a] [P-07] Add `tests/unit/test_grouping_jobs_worker.py` (or `test_analysis_jobs_worker.py`) cases: `finalize_analysis` with `is_group` 1 seeded from `group_mixed_rm_made` writes `resolved.partitions` from the detail and `treaties` from the fake with two members' duplicate treaty ids reduced to one entry; with `raise_on_describe_run`, `resolved` holds `partitions` and no `treaties` key and the row reaches `ready`; a backfill over `broker_group_ingp` writes `partitions` from its property
  - Proof: `uv run pytest tests/unit -k "group and resolved"`
- [ ] T031 [US3] [FR-007] [FR-009] [FR-006a] [P-08] Confirm `_resolved_view` and `analysis_results_inline.html` render a group of two or more partitions as the wide per-partition list (`NA · EQ — RMS 17.0 NA Stochastic Event Rates — North America Earthquake, RMS 17.0 NA Stochastic Event Rates (50,000 periods)`), an ELT-only group's entries with no set half, a one-partition group as the single field, and an own analysis with two partitions as the list
  - Proof: T032
- [ ] T032 [P] [US3] [FR-006a] [FR-007] [FR-008] [FR-009] [FR-017] Add `tests/unit/test_analysis_service.py` cases: `group_mixed_rm_made` row renders three list entries in JP · WS, NA · EQ, NA · WS order with both halves where present; `group_elt_workbench_made` renders two entries with scheme names and no "periods" text; `group_plt_workbench_made` renders the single `Simulation set` field, no list; an own row seeded with two partitions renders the list; the Compare line for the mixed group equals the three entries joined; the set named for NA · EQ equals `simulation_set_selections` in a compose plan the test seeds into that row's `submitted_settings` (the capture is a Risk Modeler-made group and carries none) (FR-008)
  - Proof: `uv run pytest tests/unit/test_analysis_service.py -k group`

**Checkpoint**: Expand the finished mixed group: one entry per region and
peril naming scheme and the compose-screen set; a two-DLM group lists schemes
only. **STOP** for the approver before story 4.

---

## Phase 6: User Story 4 — The row names the treaties the run applied (Priority: P2)

**Goal**: The expanded row lists `number · name` per applied treaty, sorted by
number, one per treaty id; no entry when none applied; *not returned* when the
read failed; the plan item records the requested treaty names.

**Independent Test**: Run a template with two treaties selected; expand the
finished row; expand broker 5689560; expand a no-treaty run.

### Implementation for User Story 4

- [ ] T033 [US4] [T-05] In `_submit_one` (`app/workers/analysis_jobs.py:123`) pass `treaty_names` into `_claim_analysis` and in `_claim_analysis` (`:78`) store `{**item, "treaty_names": list(treaty_names)}` as `submitted_settings` instead of `item` alone; the resumed-row branch is unchanged
  - Proof: T034
- [ ] T034 [P] [US4] [T-05] Add a `tests/unit/test_analysis_jobs_worker.py` case: after `_submit_one` for a batch plan with `treaty_names ["PR1", "PR2"]`, the claimed row's `submitted_settings` JSON carries `treaty_names: ["PR1", "PR2"]` beside the item keys, and `_submitted_view` still reads `treat_construction_occupancy_as_unknown` and `currency.code`
  - Proof: `uv run pytest tests/unit/test_analysis_jobs_worker.py -k treaty_names`
- [ ] T035 [US4] [FR-010] [FR-012] [P-02] [P-04] In `app/templates/partials/analysis_results_inline.html` add after the Run details block: when `a.resolved.treaties is none` a `<div><dt>Treaties</dt><dd class="blank">not returned</dd></div>`; when `a.resolved.treaties` is non-empty a `settings-grid__wide` `<dt>Treaties</dt>` list of its entries; when `[]`, nothing
  - Proof: T036
- [ ] T036 [P] [US4] [FR-010] [FR-011] [FR-011a] [FR-012] [P-06] [P-07] Add `tests/unit/test_analysis_service.py` cases: `broker_dlm` row lists its two treaties as `<number> · <name> · <currency>` in number order and renders no limit, attachment or retention text; `broker_dlm_no_treaties` (`treaties: []`) renders no "Treaties" text at all; a row whose `resolved` has `partitions` but no `treaties` key renders `Treaties` reading `not returned` with the scheme field intact; a group `resolved` holding one treaty id lists it once; an own row seeded with treaties in CAD reports the stored `number`/`name` and `CAD`, never a re-read from `irp_treaty`
  - Proof: `uv run pytest tests/unit/test_analysis_service.py -k treaties`
- [ ] T037 [P] [US4] [FR-010] [FR-014] Add a `tests/unit/test_analysis_jobs_worker.py` case: `finalize_analysis` on an own DLM seeded with two treaties writes `resolved.treaties` sorted by `number`, each with `currency`, `occurrence_limit`, `risk_limit`, `attachment_point` and `retention_amount`; seeded with none writes `treaties: []`
  - Proof: `uv run pytest tests/unit/test_analysis_jobs_worker.py -k treaties`

**Checkpoint**: Expand a two-treaty run, broker 5689560, and a no-treaty run;
the first two list treaties, the third shows no Treaties entry. **STOP** for
the approver.

---

## Phase 7: Polish & Cross-Cutting

- [ ] T038 [T-08] In `app/services/analysis_service.py` delete `AnalysisSettings` fields `analysis_mode`, `construction`, `line_of_business`, `term`, `pla`, `rate_vintage` and their `_to_display` lines; drop the dead key alternates `type`, `mode`, `modelVersion`, `subperil`, `secondaryPeril`, `currencyCode`/`currencyName` (read the `currency` object only) per research.md § Readers
  - Proof: `uv run pytest tests/unit`; `grep -n "rate_vintage\|line_of_business\|analysis_mode" app` returns nothing
- [ ] T039 [P] [FR-016] Update `docs/DATA_MODEL.md` §6: the `settings_metadata` column note at `:318` reads "JSON: Risk Modeler's GET-analysis response as returned, plus the workbench key `resolved` (spec 015)"; the `submitted_settings` bullet at `:358` gains the group shape (compose plan, `_claim_group`) and `treaty_names`; add one sentence stating the origin → source table from `data-model.md` §2
  - Proof: the three columns' per-origin content appears once in §6
- [ ] T040 [P] Update `docs/agents` / `CONTEXT.md` only if a term introduced here (Partition, Applied treaty) is absent; otherwise no change
- [ ] T041 Run `make test-sql` (or `make wsl-test-sql`) once to confirm the existing SQL Server tier still passes with the larger `settings_metadata` JSON; if `linux-box` is down, report the tier as not run and stop
  - Proof: SQL Server tier count reported, or "not run (`linux-box` down)"
- [ ] T042 Run quickstart.md §4 click-through for all four stories and the FR-016 Compare check on the running stack; record any mismatch as a GitHub issue
- [ ] T043 Review the diff for subtraction per AGENTS.md § Code Quality: remove comments that narrate, inline single-use helpers that do not name behavior, confirm no `research.md` rationale was copied into source comments

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: none. T001 before T002.
- **Phase 2 (Foundational)**: T003 → T004 → T005 → T006 → T007 → T009; T008
  and T010 in parallel with T007. Blocks every story.
- **Phase 3 (US1)**: after Phase 2. T011 → T012, T013 → T014, T015; T016 →
  T017 → T018; T019 → T020; T021 last.
- **Phase 4 (US2)**: after US1 (shares the reader and worker path).
- **Phase 5 (US3)**: after US2 (names simulation sets the same way, spec
  story 3).
- **Phase 6 (US4)**: after US1 (needs the `resolved` writer and reader); can
  run before US2 and US3 if the approver prefers, but the checkpoint order
  above follows spec priority.
- **Phase 7 (Polish)**: after all stories. T041 needs `linux-box`.

### Parallel Opportunities

- Phase 2: T008 (fake) and T010 (IRP probe) alongside T007 (real wrapper).
- Phase 3: T014 and T015 (worker tests) alongside T016–T018 (reader and
  template); T020 alongside T019.
- Phase 4: T024 and T025 together.
- Phase 5: T029, T030 and T032 together once T026–T028 and T031 land.
- Phase 6: T034, T036 and T037 together once T033 and T035 land.
- Phase 7: T039 and T040 alongside T038.

## Parallel Example: User Story 1

```bash
# After T011–T013 land:
Task: "T014 finalize_analysis writes resolved / blank-and-continue in tests/unit/test_analysis_jobs_worker.py"
Task: "T015 backfill writes resolved per hit / one failure isolated in tests/unit/test_rdm_sync.py"
# After T016–T017 land:
Task: "T018 reader and template cases in tests/unit/test_analysis_service.py"
Task: "T020 Compare line cases in tests/unit/test_results_comparison.py"
```

## Implementation Strategy

1. Phase 1 + Phase 2: fixtures, package release, gateway wrapper, fake. Stop
   when `make irp-status` names the TestPyPI release and the unit tier is
   green.
2. Phase 3 (US1) — the MVP: broker scheme visible, `resolved` pipeline
   complete for own and broker analyses, `_event_rate_scheme` gone. Approver
   clicks.
3. Phase 4 (US2): PLT display. Approver clicks.
4. Phase 5 (US3): group partitions. Approver clicks.
5. Phase 6 (US4): treaties and `treaty_names`. Approver clicks.
6. Phase 7: dead fields, docs, SQL Server tier, quickstart click-through.

## Notes

- Expanding a row calls nothing (FR-013): T018 asserts zero `FakeIRP` calls on
  render; keep that test as the guard for every later template change.
- A failed describe read never fails `finalize_analysis` or
  `backfill_rdm_analyses` (FR-014); the metadata fetch keeps its current
  failure behavior.
- Dev DB choice: **Refresh**. No migration. Rows captured before the change
  show the new fields blank until recaptured (FR-015).
- Report test tiers by name and count; the IRP tier (T010) and SQL Server
  tier (T041) are unverified until run inside `linux-box`.
