# Implementation Plan: Treaty-Level (TY) Loss Results Export

**Branch**: `016-ty-perspective-export` | **Date**: 2026-09-16 | **Spec**: [spec.md](spec.md)

<!-- Technical only. User stories and scope → spec.md. Schema → data-model.md.
     Payloads → contracts/. Endpoint investigation → research.md. Everything
     above the `---` is what a reviewer reads to decide: ten minutes to read. -->

## Plan status

**Ready for tasks:** Yes, with two assumptions carried into the build (T-04 groups, T-10 Parquet column names) and spec O-04 built as "largest exposure value".
**Blocked by:** Nothing for development. Verification against Risk Modeler needs a sandbox analysis run with treaties that take loss (quickstart.md). 014's 2026-09-15 session has since landed: the export detail page is gone and the exports section is the one export status screen, and the model version is chosen on the export form instead of read from the archive. This plan carries the Treaty column into that section; every treaty data set still runs through the same stage and load code as a portfolio data set.

## Design summary

- **The results retrieval worker records the applied treaties.** `retrieve_analysis_results` (`app/workers/analysis_jobs.py`) calls the new gateway wrapper `irp_gateway.list_analysis_treaties` (`GET /platform/riskdata/v1/analyses/{id}/treaties`) after the five perspective reads and stores the list as `loss_results.treaties` (`treaty_id`, `treaty_number`, `treaty_name`). A failed call fails the job with `loss_results` untouched, like a failed perspective read. Nothing displays the list (spec out of scope); the export reads treaty identity from the loss table (T-04).
- **TY joins the perspective intersection.** `export_service.list_exportable_analyses` adds `TY` to an analysis's `perspectives` when `loss_results.treaties` is non-empty, and `EXPORT_PERSPECTIVE_CODES` defaults to `GU,GR,RL,RP,TY`. `perspective_choices`, the form column, and `create_export`'s validation need no other change: one selected analysis without treaties removes TY (FR-001, P-05).
- **The form** says "TY writes one loss set per treaty, per analysis." under the perspective select when TY is chosen and shows no AAL in the cart rows at TY (FR-002, FR-016). Nothing else on the form changes.
- **POST** inserts one manifest row per analysis at `perspective_code = 'TY'` exactly as for GR; the treaty columns are `NULL` until the loss table is read (P-09).
- **`submit_results_export`** sends `lossDetails: [{"metricType": "LOSS_TABLES", "outputLevels": ["Treaty"], "perspectiveCodes": ["GR"]}]` for a TY row. `GR` is the protocol constant P-08 asked for (T-01).
- **`stage_results_export`** now acts on every manifest row of (`export_id`, `irp_analysis_id`) that is not staged, loaded, or closed. At TY it downloads once, reads `ELT/Treaty/TY/*.parquet`, combines each treaty's rows per event with pandas (P-11: loss summed, independent standard deviation summed, correlated the root of the sum of squares, rate kept, exposure value the largest, O-04), and writes one derived Parquet file per treaty into the working directory. The pre-split analysis row becomes the first treaty's row; each further treaty gets a sibling row copied from it, with `treaty_number`, `treaty_name`, `treaty_ids`, the composed `data_name`, and the treaty's `aal`. Each treaty file is uploaded under its own `manifest_id` with `output_level = 'Treaty'`, and the row is stamped `staged`. A table with no treaty rows fails the analysis row with "Risk Modeler returned no treaty (TY) loss rows for this analysis" (FR-007). One load job per analysis is enqueued (T-03, T-05).
- **`load_results_export`** loads every manifest row of the analysis that is staged, not loaded, and not closed, calling `stage.usp_load_elt_result @manifest_id` once per row. Each call is its own transaction; a failed row is stamped alone and the job reports every failure. The procedure is unchanged: `Data.Perspective` takes `TY` and `Data.DataName` the composed name from the row (FR-008, FR-009).
- **Read models** carry `treaty_number`, `treaty_name`, `treaty_ids`, and, at TY, `aal` from the row. The exports section shows a Treaty column beside the analysis name, and its rows are keyed by `manifest_id` because an analysis at TY occupies several of them (FR-011, T-12).
- **Retry and Close** move to `POST …/exports/{export_id}/manifests/{manifest_id}/retry|close`, since treaty rows share an analysis. Retry re-arms the analysis's single stage or load job after resetting the clicked row; the worker then touches only eligible rows, so a loaded or closed sibling is never re-run (FR-012, T-05, T-06).
- **Repeat-export warning** counts distinct exports, not manifest rows, so a treaty export of two treaties reads as one earlier export (FR-013, T-11).
- **DDL**: `stage.rwb_loss_result_manifest` gains `treaty_number`, `treaty_name`, `treaty_ids`, `aal`; `UNIQUE (export_id, irp_analysis_id)` becomes `UNIQUE (export_id, irp_analysis_id, treaty_number, treaty_name)`. The change is edited into `db/bootstrap/loss_schema.sql` and `tests/loss_mirror.py` because CIC's repository holds no manifest yet (014 R18); the developer runs `make bootstrap-loss-reset` (T-09).
- **Tests**: a TY fixture archive in `tests/unit/export_archive.py`; unit cases for the retrieval, the intersection, the submit body, the split and combination, the per-row load, Retry and Close by manifest row, and the two screens; one SQL Server case loading a TY manifest row; one opt-in sandbox test for the treaties endpoint.

## Material changes

| Area | Change |
|---|---|
| Database (Workbench) | None. `loss_results.treaties` is a new key in an existing JSON column. |
| Database (loss repository) | Four nullable manifest columns and a widened unique constraint in `db/bootstrap/loss_schema.sql`; `rwb_loss_result_file.output_level` now also takes `Treaty`. Procedure unchanged. |
| Worker | `retrieve_analysis_results` records treaties; `submit_results_export` builds the treaty request; `stage_results_export` splits and combines; `load_results_export` loads every eligible row of the analysis. |
| Service | `export_service`: TY in `perspectives`, treaty fields on the read models, Retry and Close by `manifest_id`, distinct-export warning count. |
| UI | Form note and no AAL at TY; Treaty column on the exports section, whose row ids and Retry and Close forms are keyed by `manifest_id`. No new screen (UI_WORKFLOW: derivative change, no preview). |
| Gateway | `list_analysis_treaties` on the Protocol, `_RealGateway`, module functions, and `FakeIRP`. |
| Config | `EXPORT_PERSPECTIVE_CODES` default `GU,GR,RL,RP,TY`; `infra/.env.example` updated. |
| Docs | PRD §17.4 and §21 no longer list TY as out of scope; FUNCTIONAL_REQUIREMENTS §7 part B points at this spec; 014 plan O-02 closed. |

## High-risk technical decisions

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | A TY request is `outputLevels ["Treaty"]` with the fixed financial perspective `GR`; the archive's one `ELT/Treaty/TY` folder is what is staged | Approved | P-08; [research.md#R1](research.md#r1--what-risk-modeler-returns-for-a-treaty-level-export-t-01-t-10) |
| T-02 | Treaty grain is the manifest itself: four nullable columns, the pre-split analysis row becomes the first treaty's row, siblings are copied from it, and the unique key widens to include the treaty | Approved | P-09; [research.md#R2](research.md#r2--treaty-grain-widens-the-manifest-instead-of-adding-a-child-table-t-02) |
| T-03 | The per-event combination (P-11) runs in the stage worker with pandas and lands one derived Parquet file per treaty; the load procedure is unchanged | Approved | [research.md#R3](research.md#r3--combine-in-the-stage-worker-not-in-the-procedure-t-03) |
| T-04 | "Run with treaties" is `loss_results.treaties`, written by `retrieve_analysis_results` from `GET /analyses/{id}/treaties`; TY is offered when every selected analysis has a non-empty list. Own and broker analyses confirmed by the wheel's method; whether the endpoint reports a group's members' treaties is unverified | Assumed | [research.md#R4](research.md#r4--how-the-form-knows-an-analysis-ran-with-treaties-t-04-t-13) |
| T-05 | One stage job and one load job per analysis, each acting on every eligible manifest row of (`export_id`, `irp_analysis_id`): not staged / not loaded / not closed for stage, staged and pending-or-failed and not closed for load. The load job's `input_data` drops `manifest_id` | Approved | [research.md#R5](research.md#r5--one-job-per-analysis-acting-on-every-eligible-row-t-05-t-06) |
| T-06 | Retry and Close are keyed by `manifest_id` (`…/manifests/{manifest_id}/retry`); the analysis-keyed routes are removed | Approved | [research.md#R5](research.md#r5--one-job-per-analysis-acting-on-every-eligible-row-t-05-t-06) |
| T-07 | A treaty data set's `data_name` is the analyst's data name (the analysis name when blank), a space, the treaty number, and, when it differs from the number, a space and the treaty name; cut to 150 characters | Approved | P-06; [data-model.md §2](data-model.md#2-treaty-data-set-values) |
| T-08 | A treaty row's AAL is the sum over its combined rows of rate times loss, computed by the stage worker and stored as `manifest.aal`; portfolio rows keep reading `loss_results` at render time | Approved | P-10 |
| T-09 | The manifest DDL change is edited into `loss_schema.sql` (and the SQLite mirror) with no change script, because CIC's repository holds no manifest row (014 R18, O-05) | Approved | 014 [research.md#R18](../014-results-export/research.md#r18--how-a-stage-schema-change-reaches-cic-after-cutover-o-12) |
| T-10 | The TY Parquet columns are `TreatyId`, `TreatyNum`, `TreatyName`, `EventId`, `Rate`, `Loss`, `StdDevI`, `StdDevC`, `ExpValue`, as in the CSV sample; a file missing any of them fails the analysis naming the columns | Assumed | [research.md#R1](research.md#r1--what-risk-modeler-returns-for-a-treaty-level-export-t-01-t-10) |
| T-11 | The repeat-export warning counts distinct `export_id`s for the analysis and perspective, not manifest rows | Approved | FR-013 |
| T-12 | The exports section keeps the analysis name column and a Treaty column follows it; each row is one manifest row, so its DOM id and its Retry and Close forms carry `manifest_id` | Approved | FR-011 |
| T-13 | An analysis whose results were retrieved before this release has no `treaties` key and is offered no TY until its results are retrieved again; no backfill, the dev database is rebuilt | Approved | Pre-cutover rule (no backwards compatibility) |

## Open items carried from the design

| ID | Question | Status | What is built meanwhile |
|---|---|---|---|
| O-04 (spec) | Exposure value when a treaty's rows are combined | Open | The largest exposure value among the combined rows; one constant in `export_jobs._combine_treaty_rows` to change when Cheng's query is read |
| O-01 | Whether `GET /analyses/{id}/treaties` on a group analysis returns the members' treaties | Assumed (T-04) | The same call for every origin; the sandbox test in quickstart.md is the check. If it returns nothing for a group, TY is not offered for groups and story 3 waits on a fallback |

---

## Technical Context

<!-- Only what changed or constrains the design. The stack is documented in
     docs/PRD.md §3 (Technology stack & environment); architecture rules in
     .specify/memory/constitution.md. Do not restate either. -->

**New dependencies**: None. irp-integration 0.8.0 (active) has `search_analysis_treaties_paginated` and passes `loss_details` through to Risk Modeler unchanged; pandas and pyarrow are already runtime dependencies.

**Databases touched**:
- `rwb_workbench` (`WORKBENCH`): no schema change; `irp_analysis.loss_results` gains the `treaties` key.
- `rwb_loss` (`LOSS`): four manifest columns and the unique constraint; treaty rows in the manifest, file, and staged loss tables. `dbo.Data`, `dbo.RMSELT`, `dbo.RMS_HistoricalRDS` receive treaty data sets through the unchanged procedure.
- `rwb_exposure`, DATABRIDGE: untouched.

## Constitution Check

*GATE: before Phase 0 research, re-checked after Phase 1 design.*

Reviewed against all 13 articles in `.specify/memory/constitution.md`: no violations.

Material interactions — where an article actively shapes this design:

- **Article 11 (IRP work behind an interface)**: the treaties read runs in the `retrieve_analysis_results` worker, never on the request path; the form reads the stored list. The treaty request is built in the submit worker; the download and split in the stage worker.
- **Article 7 (one data-access package)**: sibling manifest rows are inserted and stamped through `db.execute_command` with bound parameters; the derived Parquet files go through `db.elt.upload_parquet`; the procedure through `db.execute_procedure`.
- **Article 10 (SQL table is the queue)**: no new job type. One stage job and one load job per analysis keep the queue's `UNIQUE (requestor_type, requestor_id, rwb_job_type)` idempotency; the treaty fan-out is rows in the manifest, not jobs in the queue (T-05).
- **Article 3 (kind tables)**: `perspective_code = 'TY'` and `output_level = 'Treaty'` are Risk Modeler vocabulary on client-owned tables with no `CHECK`, as 014 T-19 already set.
- **Article 2 (sequencing derived, not stored)**: which rows a stage or load job acts on is read off `stage_status`, `load_status`, and `closed_at` at run time.
- **Article 8 / 1 (server-rendered; nav manifest)**: no new page or nav node; two routes renamed.
- **Article 13 (CSRF)**: the renamed Retry and Close POSTs keep the token.
- **Article 12 (test-first)**: see Testing.

## Project Structure

### Documentation (this feature)

```text
specs/016-ty-perspective-export/
├── plan.md                  # This file
├── research.md              # R1–R6
├── data-model.md            # manifest columns, loss_results.treaties, treaty data set values, view models
├── quickstart.md            # Per-story verification, tier commands
├── contracts/
│   ├── routes.md            # Deltas over 014 contracts/routes.md
│   └── jobs.md              # Deltas over 014 contracts/jobs.md
└── tasks.md
```

### Source Code (changed directories only)

```text
db/bootstrap/loss_schema.sql               # manifest: treaty_number, treaty_name, treaty_ids, aal; unique key
app/
├── config.py                              # export_perspective_codes default adds TY
├── services/irp_gateway.py                # list_analysis_treaties
├── services/export_service.py             # TY in perspectives; treaty fields; manifest-keyed Retry/Close; distinct-export count
├── workers/analysis_jobs.py               # retrieval records loss_results.treaties
├── workers/export_jobs.py                 # treaty request; split + combine; per-row load
├── routers/submissions.py                 # …/manifests/{manifest_id}/retry|close
├── templates/partials/export_form_fields.html      # TY note; no AAL at TY
├── templates/partials/exports_section.html         # Treaty column; manifest-keyed row ids, Retry, and Close
infra/.env.example                         # EXPORT_PERSPECTIVE_CODES=GU,GR,RL,RP,TY
tests/
├── loss_mirror.py                         # manifest columns in lockstep
├── unit/fakes/fake_irp.py                 # set_analysis_treaties; list_analysis_treaties
├── unit/export_archive.py                 # build_archive(output_level="Treaty", treaty_rows=…)
├── unit/test_ty_export.py                 # NEW: every unit case of this spec; 014 modules rekeyed by manifest row
├── sqlserver/test_loss_export_procedure.py         # a TY manifest row loads with Perspective TY
└── irp/test_treaty_export.py              # opt-in: treaties endpoint on a sandbox analysis
docs/PRD.md, docs/FUNCTIONAL_REQUIREMENTS.md, specs/014-results-export/plan.md   # scope pointers
```

**Structure Decision**: no new module. The treaty work is a branch inside the three export actors and the export service, because every step after the split is the portfolio step run once per row.

## Complexity Tracking

| Violation | Why needed | Simpler alternative rejected because |
|---|---|---|
| None | | |

## Testing

<!-- Strategy by tier. Not a test-file inventory. -->

- **Unit** (`uv run pytest tests/unit`): the retrieval worker stores `treaties` and fails without a partial write when the call raises; TY in `perspectives` only with a non-empty list and the intersection dropping it for one analysis without treaties; `create_export` refusing TY for such an analysis; the submit body at TY; the stage worker against a TY fixture archive (two treaties split into two rows with the composed data name, treaty IDs, and AAL; a treaty under two treaty IDs combined per event with the P-11 arithmetic; a table with no treaty rows failing with a message naming TY; a re-run leaving loaded and closed siblings untouched; missing columns named); the load worker calling the procedure once per eligible row and stamping one failure without touching the others; Retry and Close by `manifest_id` with the 404 and 409 paths; the fragment's TY note and missing AAL; the exports section's Treaty column and per-row AAL; the repeat-export warning counting one export for two treaty rows.
- **SQL Server integration** (`make test-sql`; unverified until someone runs it): a manifest row with `perspective_code = 'TY'`, `treaty_number`, `treaty_name`, and a composed `data_name` loads through the unchanged procedure with `Data.Perspective = 'TY'` and `Data.DataName` as composed; the widened unique constraint accepts two treaty rows of one analysis and refuses a third with the same treaty.
- **IRP sandbox** (`uv run pytest tests/irp --run-irp -k treaty` inside `linux-box`): `list_analysis_treaties` on a finished sandbox analysis returns rows with `treaty_id`, `treaty_number`, `treaty_name`. The T-04 group half and the T-10 column names are checked by hand per quickstart.md, since a live TY export needs an analysis with treaties that take loss.
