# Tasks: Treaty-Level (TY) Loss Results Export

**Input**: Design documents from `specs/016-ty-perspective-export/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (routes.md, jobs.md), quickstart.md

**Tests**: Included. plan.md "Testing" and constitution Article 12 name the
unit, SQL Server, and IRP sandbox coverage; each test task below implements one
of those named cases. Unit tier runs from any host shell with `uv run pytest
tests/unit`; the SQL Server tier is unverified until someone runs
`make test-sql` inside `linux-box`.

**Organization**: Phase 1 is the manifest DDL and the gateway wrapper every
story needs. Phases 2–4 are the three user stories from spec.md in priority
order. Stories 1 and 2 are bundled into one implement pass: story 2's screens
are the only way to see story 1's treaty rows (UI_WORKFLOW rule 2, "bundle two
small related stories if splitting is silly"). No UI preview: no screen gains
new layout, only a column and a note on already-styled components. Phase 6 is
the 2026-09-17 amendment (note 31 D23–D26): it does gain layout, and its
preview was approved before the build. Phase 7 is the 2026-09-21 amendment
(note 32 D8–D11): the cart lists only treaties that took loss; no layout
changes.

## Format: `[ID] [P?] [Story] [Ref] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1, US2, US3 (spec.md stories 1–3)
- **[Ref]**: `FR-nnn` from spec.md, `T-nn`/`P-nn`/`O-nn` from the decision tables
- `- Proof:` names the test or observation that closes the task when it is not obvious

---

## Phase 1: Foundational

**Purpose**: the manifest columns, the mirror, the config default, and the
gateway wrapper. Nothing user-visible yet.

- [x] T001 [T-02] [T-09] In `db/bootstrap/loss_schema.sql` add `treaty_number NVARCHAR(64) NULL`, `treaty_name NVARCHAR(256) NULL`, `treaty_ids NVARCHAR(400) NULL`, `aal FLOAT NULL` after `region_code`, and change `uq_rwb_loss_result_manifest_export_analysis` to `UNIQUE (export_id, irp_analysis_id, treaty_number, treaty_name)`. Same columns and key in `tests/loss_mirror.py`. The header comment's "ships its own ALTER TABLE block" sentence already yields to 014 R18; leave it.
  - Proof: `tests/sqlserver/test_loss_export_procedure.py::test_two_treaty_rows_of_one_analysis_are_allowed_and_a_repeat_is_not` (T016); unit tier green over the mirror.
- [x] T002 [P] [T-04] `app/services/irp_gateway.py`: `list_analysis_treaties(*, analysis_id: int) -> list[dict]` on the `IRPGateway` Protocol, `_RealGateway` (wrapping `search_analysis_treaties_paginated`, mapping `treatyId`/`treatyNumber`/`treatyName` to `treaty_id`/`treaty_number`/`treaty_name`), the module function, and `__all__`. `tests/unit/fakes/fake_irp.py`: `set_analysis_treaties`, `raise_on_analysis_treaties`, `treaty_calls`, and the method (contracts/jobs.md §6).
- [x] T003 [P] [FR-001] `app/config.py` `export_perspective_codes` default `["GU", "GR", "RL", "RP", "TY"]`; `infra/.env.example` `EXPORT_PERSPECTIVE_CODES=GU,GR,RL,RP,TY`.
- [x] T004 [P] [T-01] [T-10] `tests/unit/export_archive.py`: `build_archive(output_level="Treaty", treaty_rows=[…])` writes `ELT/Treaty/TY/{job}_{name}_ELT_Treaty_TY_{n}.parquet` with the `TreatyId`, `TreatyNum`, `TreatyName`, `EventId`, `Rate`, `Loss`, `StdDevI`, `StdDevC`, `ExpValue` columns and `Granularities` `Treaty` in `metadata.csv`; `TY_ROWS` default of two treaties (`PR1`, `PR2`) sharing events, plus one treaty under two treaty IDs for the combination case.

**Checkpoint**: `uv run pytest tests/unit` green; developer runs `make bootstrap-loss-reset`.

---

## Phase 2: User Stories 1 and 2 — Export treaty losses and follow the treaty data sets (Priority: P1) 🎯 MVP

**Goal**: an analyst exports a treaty analysis at TY and sees one loaded data set per treaty on both screens.

**Independent Test**: quickstart.md Story 1 steps 1–5 and Story 2 steps 1–2.

### Retrieval and form

- [x] T005 [US1] [FR-001] [T-04] [T-13] `app/workers/analysis_jobs.py`: `_retrieve_analysis_results_body` calls `irp_gateway.list_analysis_treaties` after the perspective loop, fails with "treaties read failed: …" and no write when it raises, and `build_loss_results_extract(…, treaties=…)` stores `"treaties": [{"treaty_id", "treaty_number", "treaty_name"}]`.
  - Proof: `tests/unit/test_ty_export.py::test_retrieval_stores_the_applied_treaties`, `::test_retrieval_fails_when_the_treaties_read_raises`.
- [x] T006 [US1] [FR-001] [P-05] `app/services/export_service.py` `list_exportable_analyses`: `TY` appended to `perspectives` when `loss_results.treaties` is non-empty; `create_export`'s per-analysis message at TY reads "{name} was not run with treaties."
  - Proof: `tests/unit/test_ty_export.py::test_ty_is_offered_only_when_every_selected_analysis_ran_with_treaties`, `::test_create_export_refuses_ty_for_an_analysis_without_treaties`.
- [x] T007 [US1] [FR-002] [FR-016] `app/templates/partials/export_form_fields.html`: the hint "TY writes one loss set per treaty, per analysis." under the select when `perspective == 'TY'`; the AAL line hidden at TY.
  - Proof: `tests/unit/test_ty_export.py::test_fragment_at_ty_lists_the_treaties_with_their_terms_and_no_aal` (renamed by T022).

### Workers

- [x] T008 [US1] [FR-004] [T-01] `app/workers/export_jobs.py` `_submit_results_export_body`: `TY_REQUEST_PERSPECTIVE_CODE = "GR"` (P-08 comment) and `outputLevels ["Treaty"]` for a TY row (contracts/jobs.md §3).
  - Proof: `tests/unit/test_ty_export.py::test_a_ty_row_requests_the_treaty_output_level_with_the_fixed_perspective`.
- [x] T009 [US1] [FR-005] [FR-006] [FR-007] [FR-009] [FR-017] [T-02] [T-03] [T-05] [T-07] [T-08] [O-04] `app/workers/export_jobs.py` `_stage_results_export_body` and `_stage`: the row set of (`export_id`, `irp_analysis_id`), the eligibility rule, the archive choice (research R6), `_treaty_files`, `_read_treaty_table` (pandas, `TY_COLUMNS` check), `_combine_treaty_rows` (P-11 arithmetic, exposure max as O-04), `_treaty_row` (claim the pre-split row, else `INSERT … SELECT` a sibling), the derived per-treaty Parquet through `_stage_file` with `output_level 'Treaty'`, the composed `data_name`, `treaty_ids`, `aal`, the "no treaty (TY) loss rows" failure, the "treaty … is not in the loss table" failure, one load job per analysis (contracts/jobs.md §4).
  - Proof: `tests/unit/test_ty_export.py::test_each_ticked_treaty_row_is_staged_from_the_one_loss_table`, `::test_ty_rows_of_one_treaty_under_two_treaty_ids_are_combined_per_event`, `::test_ty_archive_with_no_treaty_rows_fails_every_row_naming_ty`, `::test_retry_on_one_treaty_row_leaves_its_loaded_sibling_alone`, `::test_a_closed_treaty_row_is_never_re_staged`, `::test_ty_file_missing_columns_fails_every_row_naming_them` (renamed by T024).
- [x] T010 [US1] [FR-008] [FR-014] [T-05] `app/workers/export_jobs.py` `_load_results_export_body`: every eligible row of the analysis, one procedure call each, per-row failure stamp, `input_data` without `manifest_id`; `_enqueue_load` and `apply_retry` stop passing `manifest_id` (contracts/jobs.md §5).
  - Proof: `tests/unit/test_ty_export.py::test_every_staged_treaty_row_is_loaded_once_and_a_failure_stamps_only_its_row`, `::test_loaded_and_closed_rows_are_skipped_by_the_load`.

### Read models and screens

- [x] T011 [US2] [FR-011] [FR-013] [FR-016] [T-08] [T-11] [T-12] `app/services/export_service.py`: `ExportAnalysisDetail.treaty_number`/`treaty_name`/`treaty_ids`/`treaty_label`, `aal` from the row at TY; ordering by treaty (data-model.md §6); `find_exported` counting distinct exports.
  - Proof: `tests/unit/test_ty_export.py::test_detail_rows_carry_the_treaty_and_its_own_aal_at_ty`, `::test_find_exported_counts_one_export_for_two_treaty_rows`.
- [x] T012 [US2] [FR-011] [T-12] `app/templates/partials/exports_section.html`: Treaty column and `--cols`, the treaty cell with `treaty_ids` in `title`, and row ids and Retry and Close forms keyed by `manifest_id` (contracts/routes.md §2).
  - Proof: `tests/unit/test_ty_export.py::test_detail_rows_show_the_treaty_and_the_section_counts_data_sets`.

**Checkpoint**: `uv run pytest tests/unit` green. **STOP.** The approver runs quickstart.md Story 1 steps 1–5 and Story 2 steps 1–2 on the running stack (needs `bootstrap-loss-reset`, results retrieved again, and a treaty analysis in the sandbox). The T-10 column check and the T-04 sandbox test run here.

---

## Phase 3: User Story 2 (rest) — Retry and Close per treaty row (Priority: P1)

**Goal**: a failed treaty row is retried or closed on its own.

**Independent Test**: quickstart.md Story 2 steps 3–4.

- [x] T013 [US2] [FR-012] [T-06] `app/services/export_service.py`: `_failed_manifest`, `apply_retry`, `apply_close` take `manifest_id`; `app/routers/submissions.py`: the two routes become `…/manifests/{manifest_id}/retry|close`, the analysis-keyed routes removed; `export_analysis_row.html` and `exports_section.html` post to them (contracts/routes.md §4).
  - Proof: `tests/unit/test_export_retry.py`, `tests/unit/test_export_close.py`, and `tests/unit/test_export_routes.py` Retry cases pass keyed by manifest row; `tests/unit/test_ty_export.py::test_retry_on_one_treaty_row_leaves_its_loaded_sibling_alone`, `::test_close_posts_by_manifest_row`.

**Checkpoint**: `uv run pytest tests/unit` green. **STOP.** The approver runs quickstart.md Story 2 steps 3–5.

---

## Phase 4: User Story 3 — Export a group at TY (Priority: P2)

**Goal**: a group exported at TY yields one data set per treaty with each event combined across the members' treaty IDs.

**Independent Test**: quickstart.md Story 3.

- [x] T014 [US3] [FR-003] [FR-017] [P-01] [T-04] No new code: T005 records a group's treaties by the same call and T009 combines rows under several treaty IDs. This task is the sandbox check of the group half of T-04 (quickstart.md "Sandbox checks") and closes plan O-01 one way or the other.
  - Proof: quickstart.md Story 3 step 2 observed on the running stack; `tests/unit/test_ty_export.py::test_ty_rows_of_one_treaty_under_two_treaty_ids_are_combined_per_event` is the unit half.

**Checkpoint**: **STOP.** The approver runs quickstart.md Story 3.

---

## Phase 5: Other tiers and docs

- [x] T015 [P] [T-04] `tests/irp/test_treaty_export.py` (opt-in, `--run-irp`): `list_analysis_treaties` on `IRP_TEST_TREATY_ANALYSIS_ID` returns rows carrying the three keys; skipped without the variable.
- [x] T016 [P] [T-02] [FR-009] `tests/sqlserver/test_loss_export_procedure.py`: a manifest row with `perspective_code 'TY'`, `treaty_number`, `treaty_name`, `treaty_ids`, and a composed `data_name` loads with `Data.Perspective = 'TY'` and `Data.DataName` unchanged; two treaty rows of one analysis insert and a third with the same treaty raises. Unverified until `make test-sql` runs.
- [x] T017 [P] Docs: `docs/PRD.md` §17.4 / §21 stop listing TY as out of scope and point at spec 016; `docs/FUNCTIONAL_REQUIREMENTS.md` §7 part B paragraph gains the pointer; `specs/014-results-export/plan.md` O-02 closed by spec 016 (`TY` is in `EXPORT_PERSPECTIVE_CODES`).

---

## Phase 6: Treaty selection (note 31 D23–D26)

**Purpose**: the analyst picks the treaties to export and names each one on the
form, beside its terms. Reverses P-03 and the treaty-terms exclusion, moves the
data name to the analysis-treaty grain, and deletes the stage worker's
pre-split row, sibling insert, and name composition. Preview approved
2026-09-17: `docs/ui_previews/export_form_ty_treaties.html`.

**Independent Test**: quickstart.md Story 1 steps 2–6.

- [x] T018 [US1] [P-12] [T-16] `app/services/irp_gateway.py` `list_analysis_treaties`: keep `treaty_type`, `attachment_point`, `occurrence_limit`, `risk_limit` beside identity; same keys in `tests/unit/fakes/fake_irp.py`; `tests/irp/test_treaty_export.py` asserts the seven keys.
  - Proof: `tests/unit/test_ty_export.py::test_retrieval_stores_the_applied_treaties`.
- [x] T019 [US1] [FR-002] [P-12] `app/services/export_service.py`: `ExportableAnalysis.treaties` (deduped by number and name) and `treaty_choices` → `TreatyChoice`, the type through `treaty_service.display_value` and the amounts through `analysis_service.fmt_loss`. No new formatting helper.
  - Proof: `tests/unit/test_ty_export.py::test_the_cart_lists_each_treaty_once_with_its_terms`.
- [x] T020 [US1] [FR-002] [FR-005] [FR-009] [P-03] [P-06] `app/services/export_service.py` `create_export`: the `treaty_picks` keyword, the three TY validation messages, and one manifest row per ticked treaty with `treaty_number`, `treaty_name`, and the typed or default `data_name`; `_MANIFEST_INSERT` gains the two treaty columns.
  - Proof: `tests/unit/test_ty_export.py::test_create_export_refuses_an_analysis_with_no_treaty_ticked`, `::test_create_export_refuses_a_treaty_the_analysis_did_not_run_with`, `::test_one_manifest_row_per_ticked_treaty_with_the_typed_or_default_name`, `::test_an_untouched_treaty_is_not_exported`.
- [x] T021 [US1] [FR-002] `app/routers/submissions.py`: `_treaty_picks` over `treaty[<analysis_id>]` and `treaty_data_name[<analysis_id>][<treaty_number>]`, in `_export_fields_context`, the fragment route, and the POST (contracts/routes.md §1).
  - Proof: `tests/unit/test_export_routes.py::test_post_at_ty_writes_one_row_per_ticked_treaty`, `::test_post_at_ty_without_a_tick_rerenders_with_the_ticks_it_had`.
- [x] T022 [US1] [FR-002] [P-12] [T-15] `app/templates/partials/export_form_fields.html`: the treaty list per cart row at TY (head with the ticked count and all / none, one row per treaty with number, name, type, terms, and the per-treaty Data name input), the reworded hint, and the per-analysis Data name field kept for every other perspective. `submission_export_new.html` and the fields partial add the two `hx-include` selectors and gate Export on `treatiesOk`; `app/static/js/app.js` `analysisPicks` gains `treatiesOk` and `tickTreaties`; `app/static/css/details.css` gains `.treaty-pick` / `.treaty-row`, the name field revealed by `:has(input:checked)`.
  - Proof: `tests/unit/test_ty_export.py::test_fragment_at_ty_lists_the_treaties_with_their_terms_and_no_aal`, `::test_fragment_keeps_the_ticks_and_the_treaty_names_typed_before_the_next_change`.
- [x] T023 [US1] [T-17] `app/workers/export_jobs.py` `_submit_results_export_body`: group the pending rows by analysis, one Risk Modeler request per group, its job id stamped on every row, a rejection failing the group (contracts/jobs.md §3).
  - Proof: `tests/unit/test_ty_export.py::test_the_ticked_treaty_rows_share_one_export_request`, `::test_a_rejected_request_fails_every_treaty_row_of_the_analysis`, `::test_a_job_recorded_by_a_crashed_run_is_stamped_on_every_treaty_row`.
- [x] T024 [US1] [FR-005] [FR-007] `app/workers/export_jobs.py` `_stage_treaties`: match each combined treaty to an eligible row by (number, name), skip and log the treaties no row claims, fail a row whose treaty the table lacks. Delete `_treaty_data_name`, `_TREATY_ROW_COPIED_COLUMNS`, `_insert_treaty_row`, the `rows` parameter of `_stage_treaties` and `_stage`, and the `DATA_NAME_MAX_LEN` import (contracts/jobs.md §4 step 6).
  - Proof: `tests/unit/test_ty_export.py::test_each_ticked_treaty_row_is_staged_from_the_one_loss_table`, `::test_a_treaty_the_analyst_left_unticked_is_skipped_and_logged`, `::test_a_ticked_treaty_the_table_does_not_hold_fails_its_row_alone`, `::test_ty_archive_with_no_treaty_rows_fails_every_row_naming_ty`.
- [x] T025 [US2] [T-14] `app/services/irp_job_service.py` `find_export_job`: `AND completed_at IS NULL`, so Retry → submit asks Risk Modeler again instead of re-stamping a terminal job id. Defect found while grouping the submit worker; user approved the fix 2026-09-17.
  - Proof: `tests/unit/test_export_submit_worker.py::test_a_completed_job_is_never_reused_after_retry`.
- [x] T026 [P] Documents: spec.md (Status, scope, P-03, P-06, P-09, new P-12 and O-05, FR-002, FR-005, FR-007, FR-009, FR-011, FR-013, stories 1–3, SC-001, SC-002), plan.md (design summary, T-02, T-07 deleted, T-14 to T-17, Testing), contracts/routes.md §1–§2, contracts/jobs.md §1–§4 and §6, data-model.md §1–§3 and §5, research.md R2, R6, new R7, quickstart.md stories 1–3, `docs/PRD.md` §17.4.

**Checkpoint**: `uv run pytest tests/unit` green. **STOP.** The approver runs
quickstart.md Story 1 steps 2–6 and Story 2 step 3 on the running stack.

---

## Phase 7: Zero-loss treaties filtered (note 32 D8–D11)

**Purpose**: the cart lists only the treaties that took TY loss, filtered out
rather than shown as zero, and the Workbench does not say why a treaty took
none (spec P-13). Reaching FR-007 for a treaty the analyst could tick was the
defect. The flag is computed in the retrieval worker (constitution Article 11)
and stored on each `loss_results.treaties` entry (plan T-18, research R8).

**Independent Test**: quickstart.md Story 1 step 8 and Story 3 step 1.

- [x] T027 [T-18] irp-integration branch `feature/exposure-resource-type`: `EXPOSURE_RESOURCE_TYPES = ['PORTFOLIO', 'TREATY']` in `constants.py`; `_validate_exposure_resource_type` and a keyword-only `exposure_resource_type='PORTFOLIO'` on `get_elt`, `get_ep`, `get_stats`, `get_plt` in `analysis.py`; `tests/test_exposure_resource_types.py`; `docs/api.md` regenerated. Release: Ben tags `v0.10.0rc1` and dispatches `publish-test.yml`; `v0.10.0` via a GitHub Release once Phase 7 lands.
  - Proof: the wheel suite green; `test_result_getters_send_treaty_when_asked` for each getter.
- [x] T028 [T-18] `pyproject.toml` `irp-testpypi = ["irp-integration[databridge]==0.10.0rc1"]` and `uv.lock` re-resolved (`uv lock --upgrade-package irp-integration`; `make` is not on the Windows host). `default-groups` stays `["dev", "irp-pypi"]`. Inside `linux-box`, `make irp-testpypi` installs the rc.
- [x] T029 [T-18] `app/services/irp_gateway.py` `get_analysis_stats` on the Protocol, `_RealGateway`, and the module function: `exposure_resource_type: str = "PORTFOLIO"`, passed to the wheel as `exposure_resource_type=`. `tests/unit/fakes/fake_irp.py`: the parameter, `_treaty_stats`, `set_treaty_stats`, `raise_on_treaty_stats_for`, and the type on every `result_calls` entry (contracts/jobs.md §6).
- [x] T030 [T-18] [T-13] `app/workers/analysis_jobs.py` `_retrieve_analysis_results_body`: after the treaties read, one `get_analysis_stats` at `TY` scoped `TREATY` per treaty, `has_loss = bool(rows)`; a raised read fails the job "treaty loss read failed for {number}: …" with no write (contracts/jobs.md §2).
  - Proof: `tests/unit/test_ty_export.py::test_retrieval_stores_the_applied_treaties`, `::test_retrieval_fails_when_a_treaty_loss_read_raises`.
- [x] T031 [FR-001] [FR-002] [P-05] [P-13] `app/services/export_service.py` `list_exportable_analyses`: the cart's treaties are the entries with `has_loss` true, filtered before the dedupe by (number, name); TY offered only when one remains. `create_export`'s TY message reads "{name} has no treaty with TY loss."
  - Proof: `tests/unit/test_ty_export.py::test_the_cart_hides_a_treaty_that_took_no_loss`, `::test_ty_is_not_offered_when_no_treaty_took_loss`, `::test_create_export_refuses_ty_for_an_analysis_without_treaties`.
- [x] T032 [T-18] `tests/irp/test_treaty_export.py::test_treaty_scoped_ty_stats_answer_per_treaty` (opt-in, `--run-irp`): each applied treaty of `IRP_TEST_TREATY_ANALYSIS_ID` answers a list at `TY`/`TREATY`, at least one populated. Unverified until run inside `linux-box`.
- [x] T033 [P] Documents: spec.md (Status, scope, P-05, new P-13, O-05 closed, story 1 acceptance 1–3 and 8, FR-001, FR-002, FR-007), plan.md (Status, design summary, Material changes, T-04 Approved, T-13, new T-18, O-01 closed, New dependencies, Constitution Check, structure, Testing), research.md R4 and new R8, data-model.md §3 and §5, contracts/jobs.md §1, §2, §6, quickstart.md (prerequisites, Story 1 step 8, Story 3 step 1, sandbox checks, test commands).

**Checkpoint**: T028 done and `uv run pytest tests/unit` green. **STOP.** The
approver runs the T-18 sandbox test, then quickstart.md Story 1 step 8 and
Story 3 step 1 on the running stack.

---

## Dependencies and execution order

- **Phase 1** first; T002–T004 parallel after T001.
- **Phase 2**: T005 → T006 → T007 (form); T008, T009, T010 in that order (workers, T009 depends on T004); T011 → T012 (screens). The two chains are independent of each other.
- **Phase 3** after Phase 2 (its tests replace the analysis-keyed ones).
- **Phase 4** is verification only, after Phase 3.
- **Phase 5** any time after Phase 1; T017 last.
- **Phase 6** after Phase 5: T018 → T019 → T020 → T021 → T022 (the form, in
  that order); T023 → T024 (the workers); T025 alone. T026 last.
- **Phase 7** after Phase 6: T027 → T028 (the wheel, then its pin); T029 →
  T030 → T031 (unit tier green without T028, since `FakeIRP` stands in for
  the wheel); T032 needs T028 to run. T033 last.

## What stays open after all tasks

- Spec O-04 (exposure value when rows are combined): built as the largest value; one line to change.
- The 014 amendments of 2026-09-15 (FR-010): 014's to build; treaty rows inherit them.
