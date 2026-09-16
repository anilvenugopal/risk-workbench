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
new layout, only a column and a note on already-styled components.

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
  - Proof: `tests/unit/test_ty_export.py::test_fragment_at_ty_shows_the_note_and_no_aal`.

### Workers

- [x] T008 [US1] [FR-004] [T-01] `app/workers/export_jobs.py` `_submit_results_export_body`: `TY_REQUEST_PERSPECTIVE_CODE = "GR"` (P-08 comment) and `outputLevels ["Treaty"]` for a TY row (contracts/jobs.md §3).
  - Proof: `tests/unit/test_ty_export.py::test_a_ty_row_requests_the_treaty_output_level_with_the_fixed_perspective`.
- [x] T009 [US1] [FR-005] [FR-006] [FR-007] [FR-009] [FR-017] [T-02] [T-03] [T-05] [T-07] [T-08] [O-04] `app/workers/export_jobs.py` `_stage_results_export_body` and `_stage`: the row set of (`export_id`, `irp_analysis_id`), the eligibility rule, the archive choice (research R6), `_treaty_files`, `_read_treaty_table` (pandas, `TY_COLUMNS` check), `_combine_treaty_rows` (P-11 arithmetic, exposure max as O-04), `_treaty_row` (claim the pre-split row, else `INSERT … SELECT` a sibling), the derived per-treaty Parquet through `_stage_file` with `output_level 'Treaty'`, the composed `data_name`, `treaty_ids`, `aal`, the "no treaty (TY) loss rows" failure, the "treaty … is not in the loss table" failure, one load job per analysis (contracts/jobs.md §4).
  - Proof: `tests/unit/test_ty_export.py::test_ty_archive_splits_into_one_staged_row_per_treaty`, `::test_ty_rows_of_one_treaty_under_two_treaty_ids_are_combined_per_event`, `::test_ty_archive_with_no_treaty_rows_fails_naming_ty`, `::test_retry_on_one_treaty_row_leaves_its_loaded_sibling_alone`, `::test_a_closed_treaty_row_is_never_re_staged`, `::test_ty_file_missing_columns_fails_naming_them`.
- [x] T010 [US1] [FR-008] [FR-014] [T-05] `app/workers/export_jobs.py` `_load_results_export_body`: every eligible row of the analysis, one procedure call each, per-row failure stamp, `input_data` without `manifest_id`; `_enqueue_load` and `apply_retry` stop passing `manifest_id` (contracts/jobs.md §5).
  - Proof: `tests/unit/test_ty_export.py::test_every_staged_treaty_row_is_loaded_once_and_a_failure_stamps_only_its_row`, `::test_loaded_and_closed_rows_are_skipped_by_the_load`.

### Read models and screens

- [x] T011 [US2] [FR-011] [FR-013] [FR-016] [T-08] [T-11] [T-12] `app/services/export_service.py`: `ExportAnalysisDetail.treaty_number`/`treaty_name`/`treaty_ids`/`treaty_label`, `aal` from the row at TY; `ExportSummary.data_set_count`; ordering by treaty (data-model.md §6); `find_exported` counting distinct exports.
  - Proof: `tests/unit/test_ty_export.py::test_detail_rows_carry_the_treaty_and_its_own_aal_at_ty`, `::test_find_exported_counts_one_export_for_two_treaty_rows`.
- [x] T012 [US2] [FR-011] [T-12] Templates: `export_analyses_table.html` Treaty column and `--cols`; `export_analysis_row.html` treaty cell with `treaty_ids` in `title`; `exports_section.html` "Data sets" heading, `data_set_count`, treaty appended to the analysis name in the expanded row; `submission_export_detail.html` "data set(s)" badge (contracts/routes.md §2, §3).
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

## Dependencies and execution order

- **Phase 1** first; T002–T004 parallel after T001.
- **Phase 2**: T005 → T006 → T007 (form); T008, T009, T010 in that order (workers, T009 depends on T004); T011 → T012 (screens). The two chains are independent of each other.
- **Phase 3** after Phase 2 (its tests replace the analysis-keyed ones).
- **Phase 4** is verification only, after Phase 3.
- **Phase 5** any time after Phase 1; T017 last.

## What stays open after all tasks

- Spec O-04 (exposure value when rows are combined): built as the largest value; one line to change.
- Plan O-01 (the treaties endpoint on a group): closed by the Phase 4 checkpoint.
- The 014 amendments of 2026-09-15 (FR-010): 014's to build; treaty rows inherit them.
