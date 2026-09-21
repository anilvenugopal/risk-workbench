# Implementation Plan: Treaty-Level (TY) Loss Results Export

**Branch**: `016-ty-perspective-export` | **Date**: 2026-09-16, amended 2026-09-17 (treaty selector, note 31) and 2026-09-21 (zero-loss treaties filtered, note 32) | **Spec**: [spec.md](spec.md)

<!-- Technical only. User stories and scope → spec.md. Schema → data-model.md.
     Payloads → contracts/. Endpoint investigation → research.md. Everything
     above the `---` is what a reviewer reads to decide: ten minutes to read. -->

## Plan status

**Ready for tasks:** Yes, with one assumption carried into the build (T-10 Parquet column names) and spec O-04 built as "largest exposure value". Amended 2026-09-17 for note 31's treaty selector (T-15 to T-17, tasks.md Phase 6); T-07 is deleted with the name composition it described. Amended 2026-09-21 for note 32's zero-loss filter (T-18, tasks.md Phase 7), which needs irp-integration 0.10.0rc1.
**Blocked by:** Nothing for development. Verification against Risk Modeler needs a sandbox analysis run with treaties that take loss (quickstart.md). 014's 2026-09-15 session has since landed: the export detail page is gone and the exports section is the one export status screen, and the model version is chosen on the export form instead of read from the archive. This plan carries the Treaty column into that section; every treaty data set still runs through the same stage and load code as a portfolio data set.

## Design summary

- **The results retrieval worker records the applied treaties and which took loss.** `retrieve_analysis_results` (`app/workers/analysis_jobs.py`) calls the gateway wrapper `irp_gateway.list_analysis_treaties` (`GET /platform/riskdata/v1/analyses/{id}/treaties`) after the five perspective reads, then for each treaty one `irp_gateway.get_analysis_stats` at `TY` scoped `exposure_resource_type="TREATY"` to the treaty's id, and stores the list as `loss_results.treaties` (`treaty_id`, `treaty_number`, `treaty_name`, `treaty_type`, `risk_limit`, `attachment_point`, `occurrence_limit`, `has_loss`). A failed call fails the job with `loss_results` untouched, like a failed perspective read. The export form's cart is the one reader: it lists the treaties with `has_loss` true to tick and their terms (T-04, T-16, T-18).
- **TY joins the perspective intersection.** `export_service.list_exportable_analyses` adds `TY` to an analysis's `perspectives` when `loss_results.treaties` holds an entry with `has_loss` true, and the cart lists those entries alone, deduped by (number, name), so a group's treaty is offered when any member copy took loss. `EXPORT_PERSPECTIVE_CODES` defaults to `GU,GR,RL,RP,TY`. `perspective_choices`, the form column, and `create_export`'s validation need no other change: one selected analysis without a treaty that took loss removes TY (FR-001, P-05, P-13).
- **The form** says "TY writes one loss set per ticked treaty, per analysis." under the perspective select when TY is chosen and shows no AAL in the cart rows at TY (FR-002, FR-016). At TY each cart row replaces its Data name field with the analysis's treaty list: one tick box per treaty carrying the number, name, type label, and the three amounts, an all / none link per analysis, and a per-treaty Data name input revealed by the tick itself (CSS `:has()`). Ticks and typed names ride every fragment re-render through `hx-include`, as the per-analysis data names already do. Export is disabled until every selected analysis has a tick (T-15).
- **POST** inserts one manifest row per analysis — at TY one per ticked treaty, with `treaty_number`, `treaty_name`, and `data_name` filled from the form and `treaty_ids` and `aal` `NULL` until the row is staged (P-09, T-16).
- **`submit_results_export`** groups the export's pending rows by analysis and sends one Risk Modeler request per group, stamping its job id on every row of the group; a rejection fails the whole group. For a TY row the request is `lossDetails: [{"metricType": "LOSS_TABLES", "outputLevels": ["Treaty"], "perspectiveCodes": ["GR"]}]`; `GR` is the protocol constant P-08 asked for (T-01, T-17).
- **`stage_results_export`** now acts on every manifest row of (`export_id`, `irp_analysis_id`) that is not staged, loaded, or closed. At TY it downloads once, reads `ELT/Treaty/TY/*.parquet`, combines each treaty's rows per event with pandas (P-11: loss summed, independent standard deviation summed, correlated the root of the sum of squares, rate kept, exposure value the largest, O-04), and writes one derived Parquet file per treaty into the working directory. Each combined treaty is matched to the analysis's eligible manifest rows by (`treaty_number`, `treaty_name`): a match is staged and stamped with `treaty_ids` and `aal`; a treaty no row claims is skipped and counted in one log line; a row whose treaty the table does not hold fails alone. Each treaty file is uploaded under its own `manifest_id` with `output_level = 'Treaty'`. A table with no treaty rows fails every treaty row of the analysis with "Risk Modeler returned no treaty (TY) loss rows for this analysis" (FR-007). One load job per analysis is enqueued (T-03, T-05).
- **`load_results_export`** loads every manifest row of the analysis that is staged, not loaded, and not closed, calling `stage.usp_load_elt_result @manifest_id` once per row. Each call is its own transaction; a failed row is stamped alone and the job reports every failure. The procedure is unchanged: `Data.Perspective` takes `TY` and `Data.DataName` the name the analyst gave that treaty on the form (FR-008, FR-009).
- **Read models** carry `treaty_number`, `treaty_name`, `treaty_ids`, and, at TY, `aal` from the row. The exports section shows a Treaty column beside the analysis name, and its rows are keyed by `manifest_id` because an analysis at TY occupies several of them (FR-011, T-12).
- **Retry and Close** move to `POST …/exports/{export_id}/manifests/{manifest_id}/retry|close`, since treaty rows share an analysis. Retry re-arms the analysis's single stage or load job after resetting the clicked row; the worker then touches only eligible rows, so a loaded or closed sibling is never re-run (FR-012, T-05, T-06).
- **Repeat-export warning** counts distinct exports, not manifest rows, so a treaty export of two treaties reads as one earlier export (FR-013, T-11).
- **DDL**: `stage.rwb_loss_result_manifest` gains `treaty_number`, `treaty_name`, `treaty_ids`, `aal`; `UNIQUE (export_id, irp_analysis_id)` becomes `UNIQUE (export_id, irp_analysis_id, treaty_number, treaty_name)`. The change is edited into `db/bootstrap/loss_schema.sql` and `tests/loss_mirror.py` because CIC's repository holds no manifest yet (014 R18); the developer runs `make bootstrap-loss-reset` (T-09).
- **Tests**: a TY fixture archive in `tests/unit/export_archive.py`; unit cases for the retrieval, the intersection, the treaty selection `create_export` accepts and refuses, the shared submit request, the staging and combination of the ticked treaties, the per-row load, Retry and Close by manifest row, and the two screens; one SQL Server case loading a TY manifest row; two opt-in sandbox tests, the treaties endpoint and the treaty-scoped TY stats read.

## Material changes

| Area | Change |
|---|---|
| Database (Workbench) | None. `loss_results.treaties` is a new key in an existing JSON column. |
| Database (loss repository) | Four nullable manifest columns and a widened unique constraint in `db/bootstrap/loss_schema.sql`; `rwb_loss_result_file.output_level` now also takes `Treaty`. Procedure unchanged. |
| Worker | `retrieve_analysis_results` records treaties and, per treaty, whether its TY stats read answered rows; `submit_results_export` builds the treaty request; `stage_results_export` splits and combines; `load_results_export` loads every eligible row of the analysis. |
| Service | `export_service`: TY in `perspectives`, the analysis's treaties and their display view, one manifest row per ticked treaty in `create_export`, treaty fields on the read models, Retry and Close by `manifest_id`, distinct-export warning count. `irp_job_service.find_export_job` ignores completed jobs (T-14). |
| UI | Form note and no AAL at TY; the treaty list, its terms, and the per-treaty Data name field in the cart at TY (preview `docs/ui_previews/export_form_ty_treaties.html`, approved 2026-09-17); Treaty column on the exports section, whose row ids and Retry and Close forms are keyed by `manifest_id`. No new screen. |
| Gateway | `list_analysis_treaties` on the Protocol, `_RealGateway`, module functions, and `FakeIRP`, keeping the four term values beside identity (T-16); `get_analysis_stats` takes `exposure_resource_type` (T-18). |
| Config | `EXPORT_PERSPECTIVE_CODES` default `GU,GR,RL,RP,TY`; `infra/.env.example` updated. |
| Docs | PRD §17.4 and §21 no longer list TY as out of scope; FUNCTIONAL_REQUIREMENTS §7 part B points at this spec; 014 plan O-02 closed. |

## High-risk technical decisions

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | A TY request is `outputLevels ["Treaty"]` with the fixed financial perspective `GR`; the archive's one `ELT/Treaty/TY` folder is what is staged | Approved | P-08; [research.md#R1](research.md#r1--what-risk-modeler-returns-for-a-treaty-level-export-t-01-t-10) |
| T-02 | Treaty grain is the manifest itself: four nullable columns and a unique key widened to include the treaty. `create_export` writes the treaty rows; the stage worker only fills in what the loss table tells it | Approved | P-09; [research.md#R2](research.md#r2--treaty-grain-widens-the-manifest-instead-of-adding-a-child-table-t-02) |
| T-03 | The per-event combination (P-11) runs in the stage worker with pandas and lands one derived Parquet file per treaty; the load procedure is unchanged | Approved | [research.md#R3](research.md#r3--combine-in-the-stage-worker-not-in-the-procedure-t-03) |
| T-04 | "Run with treaties" is `loss_results.treaties`, written by `retrieve_analysis_results` from `GET /analyses/{id}/treaties`; TY is offered when every selected analysis has an entry with `has_loss` true. Own and broker analyses confirmed by the wheel's method; the group half by hand on 2026-09-21 (research R8) | Approved | [research.md#R4](research.md#r4--how-the-form-knows-an-analysis-ran-with-treaties-t-04-t-13) |
| T-05 | One stage job and one load job per analysis, each acting on every eligible manifest row of (`export_id`, `irp_analysis_id`): not staged / not loaded / not closed for stage, staged and pending-or-failed and not closed for load. The load job's `input_data` drops `manifest_id` | Approved | [research.md#R5](research.md#r5--one-job-per-analysis-acting-on-every-eligible-row-t-05-t-06) |
| T-06 | Retry and Close are keyed by `manifest_id` (`…/manifests/{manifest_id}/retry`); the analysis-keyed routes are removed | Approved | [research.md#R5](research.md#r5--one-job-per-analysis-acting-on-every-eligible-row-t-05-t-06) |
| T-08 | A treaty row's AAL is the sum over its combined rows of rate times loss, computed by the stage worker and stored as `manifest.aal`; portfolio rows keep reading `loss_results` at render time | Approved | P-10 |
| T-09 | The manifest DDL change is edited into `loss_schema.sql` (and the SQLite mirror) with no change script, because CIC's repository holds no manifest row (014 R18, O-05) | Approved | 014 [research.md#R18](../014-results-export/research.md#r18--how-a-stage-schema-change-reaches-cic-after-cutover-o-12) |
| T-10 | The TY Parquet columns are `TreatyId`, `TreatyNum`, `TreatyName`, `EventId`, `Rate`, `Loss`, `StdDevI`, `StdDevC`, `ExpValue`, as in the CSV sample; a file missing any of them fails the analysis naming the columns | Assumed | [research.md#R1](research.md#r1--what-risk-modeler-returns-for-a-treaty-level-export-t-01-t-10) |
| T-11 | The repeat-export warning counts distinct `export_id`s for the analysis and perspective, not manifest rows | Approved | FR-013 |
| T-12 | The exports section keeps the analysis name column and a Treaty column follows it; each row is one manifest row, so its DOM id and its Retry and Close forms carry `manifest_id` | Approved | FR-011 |
| T-13 | An analysis whose results were retrieved before this release has no `treaties` key, or entries without `has_loss` (retrieved before 2026-09-21), and is offered no TY until its results are retrieved again: an entry without the key reads as no loss. No backfill, the dev database is rebuilt | Approved | Pre-cutover rule (no backwards compatibility) |
| T-14 | `irp_job_service.find_export_job` ignores a job with a `completed_at`, so Retry on the submit branch asks Risk Modeler for a fresh export instead of re-stamping the terminal job id and leaving the row reading in progress for good | Approved | Defect found 2026-09-17 while grouping the submit worker; user approved the fix |
| T-15 | The tick state gates Export in Alpine (`analysisPicks.treatiesOk`) and is re-checked server-side in `create_export`; the ticked count in each list's head is rendered server-side and catches up with the next fragment swap, so no live counter is kept in the browser | Approved | P-03; note 31 D26 (build it cheap) |
| T-16 | The gateway keeps `treatyType`, `riskLimit`, `attachmentPoint`, and `occurrenceLimit` beside treaty identity, and `loss_results.treaties` stores them. The cart renders the type through `treaty_service.display_value` and the amounts through `analysis_service.fmt_loss`; no new formatting helper and no EDM join | Approved | P-12; [research.md#R7](research.md#r7--the-treaties-endpoint-already-carries-the-terms-t-16) |
| T-17 | One Risk Modeler export request per analysis, not per manifest row: the treaty rows of one analysis share a loss table, so they share a job id and a poller-enqueued stage job | Approved | P-09; contracts/jobs.md §3 |
| T-18 | Which treaties took loss is one `GET /analyses/{id}/stats` at `TY` scoped `exposureResourceType=TREATY` per applied treaty, made in `retrieve_analysis_results` after the treaties read and stored as `loss_results.treaties[].has_loss` (`true` when the read answers rows). A failed read fails the job with `loss_results` untouched; no partial or unknown flag is stored. The cart reads the stored flag and never calls stats (Article 11) | Approved | P-13; [research.md#R8](research.md#r8--which-treaties-took-loss-t-18) |

## Open items carried from the design

| ID | Question | Status | What is built meanwhile |
|---|---|---|---|
| O-04 (spec) | Exposure value when a treaty's rows are combined | Open | The largest exposure value among the combined rows; one constant in `export_jobs._combine_treaty_rows` to change when Cheng's query is read |
| O-01 | Whether `GET /analyses/{id}/treaties` on a group analysis returns the members' treaties | Closed 2026-09-21 | It does, and the treaty-scoped stats read answers a group the same way (research R8); T-04 is Approved and spec O-05 closed |

---

## Technical Context

<!-- Only what changed or constrains the design. The stack is documented in
     docs/PRD.md §3 (Technology stack & environment); architecture rules in
     .specify/memory/constitution.md. Do not restate either. -->

**New dependencies**: irp-integration 0.10.0rc1 (TestPyPI pre-release, `make irp-testpypi`): `get_stats` and the other three result getters take a keyword-only `exposure_resource_type`, which the treaty-scoped TY read needs (T-18); 0.9.0 hard-codes `PORTFOLIO`. `search_analysis_treaties_paginated` and the `loss_details` pass-through are unchanged; pandas and pyarrow are already runtime dependencies.

**Databases touched**:
- `rwb_workbench` (`WORKBENCH`): no schema change; `irp_analysis.loss_results` gains the `treaties` key.
- `rwb_loss` (`LOSS`): four manifest columns and the unique constraint; treaty rows in the manifest, file, and staged loss tables. `dbo.Data`, `dbo.RMSELT`, `dbo.RMS_HistoricalRDS` receive treaty data sets through the unchanged procedure.
- `rwb_exposure`, DATABRIDGE: untouched.

## Constitution Check

*GATE: before Phase 0 research, re-checked after Phase 1 design.*

Reviewed against all 13 articles in `.specify/memory/constitution.md`: no violations.

Material interactions — where an article actively shapes this design:

- **Article 11 (IRP work behind an interface)**: the treaties read and the per-treaty TY stats reads run in the `retrieve_analysis_results` worker, never on the request path; the form reads the stored list and its `has_loss` flags. Reading the flag live from the form was rejected (note 32 §5.3): result-retrieval `get_*` calls are barred from the web layer, which `tests/unit/test_architecture_guards.py` enforces. The treaty request is built in the submit worker; the download and split in the stage worker.
- **Article 7 (one data-access package)**: the treaty rows are inserted through `db.get_connection("LOSS")` in one transaction and stamped through `db.execute_command`, both with bound parameters; the derived Parquet files go through `db.elt.upload_parquet`; the procedure through `db.execute_procedure`.
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
├── services/irp_gateway.py                # list_analysis_treaties, identity + the four terms; get_analysis_stats exposure_resource_type
├── services/export_service.py             # TY in perspectives; the cart's treaty list; one row per ticked treaty; treaty fields; manifest-keyed Retry/Close; distinct-export count
├── services/irp_job_service.py            # find_export_job ignores a completed job (T-14)
├── workers/analysis_jobs.py               # retrieval records loss_results.treaties, has_loss per treaty
├── workers/export_jobs.py                 # one request per analysis; match + combine; per-row load
├── routers/submissions.py                 # treaty ticks and per-treaty names; …/manifests/{manifest_id}/retry|close
├── templates/partials/export_form_fields.html      # TY note; no AAL at TY; the treaty list
├── templates/pages/submission_export_new.html      # hx-include for the ticks; Export gated on them
├── templates/partials/exports_section.html         # Treaty column; manifest-keyed row ids, Retry, and Close
├── static/js/app.js                       # analysisPicks: treatiesOk, tickTreaties
├── static/css/details.css                 # .treaty-pick / .treaty-row
docs/ui_previews/export_form_ty_treaties.html       # the approved cart at TY
infra/.env.example                         # EXPORT_PERSPECTIVE_CODES=GU,GR,RL,RP,TY
tests/
├── loss_mirror.py                         # manifest columns in lockstep
├── unit/fakes/fake_irp.py                 # set_analysis_treaties; list_analysis_treaties; set_treaty_stats
├── unit/export_archive.py                 # build_archive(output_level="Treaty", treaty_rows=…)
├── unit/test_ty_export.py                 # NEW: every unit case of this spec; 014 modules rekeyed by manifest row
├── sqlserver/test_loss_export_procedure.py         # a TY manifest row loads with Perspective TY
└── irp/test_treaty_export.py              # opt-in: treaties endpoint and treaty-scoped TY stats on a sandbox analysis
docs/PRD.md, docs/FUNCTIONAL_REQUIREMENTS.md, specs/014-results-export/plan.md   # scope pointers
```

**Structure Decision**: no new module. The treaty work is a branch inside the three export actors and the export service, because every step after the split is the portfolio step run once per row.

## Complexity Tracking

| Violation | Why needed | Simpler alternative rejected because |
|---|---|---|
| None | | |

## Testing

<!-- Strategy by tier. Not a test-file inventory. -->

- **Unit** (`uv run pytest tests/unit`): the retrieval worker stores `treaties` with `has_loss` per treaty and fails without a partial write when the treaties read or one treaty's stats read raises; TY in `perspectives` only when a treaty has loss, the cart hiding a treaty that took none and keeping a group's treaty when any copy took loss, and the intersection dropping TY for one analysis without such a treaty; the cart listing each treaty once with its terms; `create_export` refusing TY for an analysis without treaties, refusing an analysis with no tick and a treaty the analysis did not run with, and writing one row per ticked treaty with the typed or default data name; the submit body at TY and one request shared by an analysis's treaty rows, with a rejection failing all of them and a completed job never reused (T-14); the stage worker against a TY fixture archive (both ticked treaties staged with treaty IDs and AAL; a treaty under two treaty IDs combined per event with the P-11 arithmetic; an unticked treaty skipped and logged; a ticked treaty the table lacks failing alone; a table with no treaty rows failing every row with a message naming TY; a re-run leaving loaded and closed siblings untouched; missing columns named); the load worker calling the procedure once per eligible row and stamping one failure without touching the others; Retry and Close by `manifest_id` with the 404 and 409 paths; the fragment's TY note, treaty tick boxes, terms, placeholder, and missing AAL, and the ticks and typed names it echoes on re-render; the POST at TY writing the rows and re-rendering 422 with the ticks kept; the exports section's Treaty column and per-row AAL; the repeat-export warning counting one export for two treaty rows.
- **SQL Server integration** (`make test-sql`; unverified until someone runs it): a manifest row with `perspective_code = 'TY'`, `treaty_number`, `treaty_name`, and the analyst's `data_name` loads through the unchanged procedure with `Data.Perspective = 'TY'` and `Data.DataName` unchanged; the widened unique constraint accepts two treaty rows of one analysis and refuses a third with the same treaty.
- **IRP sandbox** (`uv run pytest tests/irp --run-irp -k treaty` inside `linux-box`): `list_analysis_treaties` on a finished sandbox analysis returns rows carrying `treaty_id`, `treaty_number`, `treaty_name` and the four term keys (T-16); `get_analysis_stats` at `TY` scoped `TREATY` answers a list for each of those treaties, at least one populated (T-18). The T-10 column names are checked by hand per quickstart.md, since a live TY export needs an analysis with treaties that take loss.
