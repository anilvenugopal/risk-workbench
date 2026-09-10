# Tasks: Loss Results Export (Iteration 11)

**Input**: Design documents from `specs/014-results-export/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (routes.md, jobs.md, load-procedure.md), quickstart.md

**Tests**: Included. plan.md "Testing" and constitution Article 12 name the unit,
SQL Server, and IRP sandbox coverage; each test task below implements one of
those named cases. Unit tier runs from any host shell with `uv run pytest
tests/unit`; the SQL Server tier is unverified until someone runs
`make test-sql` inside `linux-box`.

**Organization**: Phase 1 is the loss-repository database setup, which plan
T-31 orders before every route, worker, and template. Phase 2 is the Workbench
side every story needs. Phases 3–5 are the three user stories from spec.md in
priority order. One story per implement pass; stop at each checkpoint for the
approver to click the running feature (docs/UI_WORKFLOW.md).

## Format: `[ID] [P?] [Story] [Ref] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1, US2, US3 (spec.md stories 1–3)
- **[Ref]**: `FR-nnn` from spec.md, `T-nn`/`P-nn`/`O-nn` from the decision tables
- `- Proof:` names the test or observation that closes the task when it is not obvious

---

## Phase 1: Loss repository database setup (T-31)

**Purpose**: The `stage` schema, the load procedure, the dev mirror of CIC's five
tables, both seeds, and the bootstrap that applies them. Built and tested on SQL
Server before any Workbench code.

- [x] T001 [T-30] Create `infra/scripts/extract_historical_events.py`: takes the path to `EVENT.csv.gz`, reads the `~`-delimited CSV inside the gzip, keeps rows where `UPPER(TRIM(EVENTTYPECODE)) = 'HIST'`, and writes `db/bootstrap/seed/lookup_rms_historical_rds.csv` with columns `EventID` ← `EVENTID`, `Peril` ← `PERILCODE`, `Type` ← `HIST`, `Name` ← trimmed `EVENTNAME`, `ModelVersion` ← `MODELVERSIONCODE`, `CatYear` ← the 4-digit year in `EVENTNAME` or empty, `PCS` ← empty (research R16). Inactive rows are kept.
  - Proof: running it against `../EVENT.csv.gz` prints 2,754 rows written, 13 distinct model versions
- [x] T002 [T-30] Run `uv run python infra/scripts/extract_historical_events.py ../EVENT.csv.gz` and commit the resulting `db/bootstrap/seed/lookup_rms_historical_rds.csv` (2,754 rows). Do not commit `EVENT.csv.gz`.
- [x] T003 [P] [T-22] [T-29] Create `db/bootstrap/loss_dev_mirror.sql`: idempotent `IF OBJECT_ID(...) IS NULL CREATE TABLE` for `dbo.Client`, `dbo.Data`, `dbo.RMSELT`, `dbo.RMS_HistoricalRDS`, `dbo.Lookup_RMS_HistoricalRDS`, copying column names, types, and identities from the DDL in `specs/014-results-export/cic-reference/`. No `USE`, no database name, `GO` between batches.
- [x] T004 [P] [T-02] [T-19] [T-21] Create `db/bootstrap/loss_schema.sql` (tables part): `IF SCHEMA_ID('stage') IS NULL EXEC('CREATE SCHEMA stage AUTHORIZATION dbo')`; idempotent `CREATE TABLE` for `stage.rwb_loss_result_manifest`, `stage.rwb_loss_result_file`, `stage.rwb_loss_result_elt_data` exactly as data-model.md §4.1–§4.3 (column types, `CHECK` constraints on `stage_status`, `load_status`, `loss_table_type`, `engine_type`, `event_type`, `UNIQUE (irp_app_analysis_id, perspective_code)`, `UNIQUE (export_id, irp_analysis_id)`, `UNIQUE (manifest_id, result_file)`, indexes on `export_id` and `requested_from_submission_id`, clustered index on `(manifest_id, event_id)`). No `rwb_loss_result_plt_data` (T-13 deferred).
- [x] T005 [T-05] [T-17] [T-18] [T-04] [T-20] [FR-011] [FR-012] [FR-013] [FR-014] [FR-015] [FR-021] Add `CREATE OR ALTER PROCEDURE stage.usp_load_elt_result @manifest_id INT` to `db/bootstrap/loss_schema.sql` per contracts/load-procedure.md §1: `@@TRANCOUNT` guard (50000), claim `UPDATE` to `loading` raising 50001 with the row-state reason on zero rows, lookup model-version assertion (50002), one-match assertion (50003; join on `EventID` and `ModelVersion` only), classification `UPDATE`, exposure correction with `@@ROWCOUNT` → `exp_value_raised_count`, standard-deviation correction on stochastic rows → `std_dev_zeroed_count`, `INSERT dbo.Data ... OUTPUT INSERTED.DataID INTO @inserted`, `INSERT dbo.RMSELT`, `INSERT dbo.RMS_HistoricalRDS` with the data-model.md §5.3 conversions (`CONVERT(varchar(4), treaty_year)`, `CONVERT(varchar(15), data_vintage, 23)`, `Peril` and `[PCS#]` copied without truncation), `loaded` + `COMMIT`; `CATCH` rolls back on `XACT_STATE() <> 0`, writes `failed` + `ERROR_MESSAGE()` where `load_status <> 'loaded'`, `THROW`. No dynamic SQL.
- [x] T006 [P] [T-18] [T-25] Add `execute_procedure(name, params, connection="WORKBENCH", database=None)` and `read_uncommitted_hint(connection="WORKBENCH", database=None)` to `db/execute.py` per contracts/load-procedure.md §2–§3 (autocommit `EXEC {name} @p = :p, ...` with bound parameters; hint returns `WITH (READUNCOMMITTED)` for the `mssql` dialect, `""` otherwise); export both from `db/__init__.py`; unit tests in `tests/unit/test_db_package.py` for the hint's dialect switch and the `EXEC` statement text.
- [x] T007 [T-22] [T-30] Create `infra/scripts/bootstrap_loss.py`: exits with an error unless `MSSQL_LOSS_DATABASE == "rwb_loss"`; applies `db/bootstrap/loss_dev_mirror.sql` then `db/bootstrap/loss_schema.sql` via `from db.scripts import execute_script_file` (`connection="LOSS"`); seeds `dbo.Client` with three made-up rows, two `ActiveFlag = 'Y'` and one `'N'` (`MERGE`, idempotent) and `dbo.Lookup_RMS_HistoricalRDS` from `db/bootstrap/seed/lookup_rms_historical_rds.csv` (truncate-and-insert or `MERGE` on `EventID, ModelVersion, Peril`).
  - Proof: second run leaves row counts unchanged (`dbo.Client` = 3, `dbo.Lookup_RMS_HistoricalRDS` = 2,754)
- [x] T008 [T-22] Add `bootstrap-loss` and `wsl-bootstrap-loss` targets to `Makefile`; call them from `db-rebuild` and `wsl-db-rebuild` after `alembic upgrade head`. Document the target in `docs/SCAFFOLDING.md` next to `db-rebuild`.
- [x] T009 [T-27] [FR-011] [FR-012] [FR-013] [FR-014] [FR-021] [FR-022] Create `tests/sqlserver/test_loss_export_procedure.py`: module docstring names the truncation; at module start applies both bootstrap files to `LOSS` (idempotent) and truncates the stage and mirror tables; seeds lookup rows; stages rows through `db.elt.upload_parquet(..., schema="stage", extra_columns={"manifest_id", "result_file_id"}, connection="LOSS")`; calls the procedure through `db.execute_procedure`. Cases: lookup missing for the model version (50002, nothing written); one event matching two lookup rows (50003); exposure raised and standard deviation zeroed with counts; historical-only rows get no standard-deviation change; `RMS_HistoricalRDS` conversions (`TreatyYear`, `DataInforce`, `TreatyIncept`, `Perspective`); `Data` row captured to `data_id`; second call raises 50001 naming the data ID; `pending` row raises 50001; call inside `BEGIN TRAN` raises 50000; `CATCH` path leaves `failed` and zero target rows; `loaded` visible only after commit.
  - Proof: `make test-sql -k loss_export_procedure` green inside `linux-box` (unverified until run)

**Checkpoint**: the developer runs `make bootstrap-loss` then `make test-sql`. `EXEC stage.usp_load_elt_result @manifest_id = <id>` by hand from SSMS behaves as quickstart.md "By hand" describes (FR-022).

---

## Phase 2: Foundational (Workbench side)

**Purpose**: The Workbench column, seeds, settings, gateway wrappers, fakes, and
the unit-tier `LOSS` fixture every story's code and tests depend on.

- [x] T010 [P] [T-14] [FR-023] Edit `alembic/versions/0001_initial.py`: add `irp_job.export_id` (`Uuid`, nullable, index `ix_irp_job_export_id`); `rwb_job_type_kind` seed adds `submit_results_export` (40), `stage_results_export` (41), `load_results_export` (42) and removes `download_export_file` and `push_results_to_loss_repo`; `rwb_job_context_type_kind` adds `result_export` / `Result Export` / 70 (data-model.md §2). Make the same three-for-two replacement in the `rwb_job_type_kind` `MERGE` in `infra/scripts/seed_db.py` (lines 139–140 hold the two old rows) and add `result_export` to its context-type seed. Extend `tests/sqlserver/test_job_tables_migration.py` to assert the column, index, and the five seed changes.
- [x] T011 [P] [T-10] [T-15] Add `export_perspective_codes` (parsed from `EXPORT_PERSPECTIVE_CODES`, default `GU,GR,RL,RP`, ordered list) and `export_archive_dir` (`EXPORT_ARCHIVE_DIR`, default `""`) to `app/config.py`; add both to `infra/.env.example` and `infra/scripts/wsl-env.sh` (dev value `/workspace/data/export_archive`); tests in `tests/unit/test_config.py` for parsing and defaults.
- [x] T012 [P] [T-23] Add `submit_analysis_export_job(*, analysis_id: int, loss_details: list[dict]) -> tuple[int, dict]`, `get_export_job(job_id: int) -> dict`, `download_export_results(*, job_id: int, output_dir: str) -> str` to the `IRPGateway` Protocol, `_RealGateway`, and the module-level functions in `app/services/irp_gateway.py` (contracts/jobs.md §6), confirming the three signatures against the active irp-integration 0.7.2 wheel; tests in `tests/unit/test_irp_gateway.py`.
- [x] T013 [P] [T-23] [T-27] Extend `tests/unit/fakes/fake_irp.py`: record submitted export jobs and their request bodies, return `(job_id, request_body)`, let a test set each job's status for `get_export_job`, let a test set a fixture-archive path that `download_export_results` copies into `output_dir` and returns, and raise `IRPAPIError` per analysis ID or for the whole call.
- [x] T014 [P] [T-27] Create `tests/loss_mirror.py` (SQLite DDL for `stage.rwb_loss_result_manifest`, `stage.rwb_loss_result_file`, `stage.rwb_loss_result_elt_data`, and mirrors of `Client`, `Data`, `RMSELT`, `RMS_HistoricalRDS`, `Lookup_RMS_HistoricalRDS` reachable as `dbo.<name>` via a second attached database) and a `loss_db` fixture in `tests/conftest.py` that builds a SQLite engine, `ATTACH DATABASE ':memory:' AS stage` and `AS dbo`, creates the tables, and calls `register_engine("LOSS", engine)` beside the existing `WORKBENCH` registration.
  - Proof: `db.execute("SELECT 1 FROM stage.rwb_loss_result_manifest", connection="LOSS")` runs in a unit test
- [x] T015 [P] [T-07] [T-27] Create `tests/unit/export_archive.py`: a helper that writes a zip in the DLM layout (one top-level folder → `ELT/metadata.csv` with `AnlsId`, `AnalysisCurrency`, `Engine Type`, `ModelVersion` columns → `ELT/Portfolio/{perspective}/{name}_{chunk}.parquet` written with pyarrow using the research R6 column names `PortInfoId`, `PortInfoName`, `PortInfoNum`, `EventId`, `Rate`, `Loss`, `StdDevI`, `StdDevC`, `ExpValue`), with knobs for a missing `metadata.csv`, a `PLT` folder, an unknown folder, an extra perspective folder, zero rows, and mismatched columns.

**Checkpoint**: `uv run pytest tests/unit` green; developer chooses Rebuild for the `irp_job.export_id` column (`make db-rebuild`, which now also runs `bootstrap-loss`).

---

## Phase 3: User Story 1 — Export finished analyses to the loss repository (Priority: P1) 🎯 MVP

**Goal**: From a submission's analyses page the analyst selects finished analyses, a perspective, a client, confirms inception and CRM ID, adds optional data names, clicks Export, and lands on the export detail page. The Workbench then requests, downloads, stages, classifies, corrects, and loads each analysis without further input.

**Independent Test**: quickstart.md "Story 1" steps 1–8: two analyses exported at GR land as pending on the detail page, move through the statuses to loaded, produce one `dbo.Data` row each with matching `RMSELT` + `RMS_HistoricalRDS` counts, the re-opened form marks them exported for GR and tickable for RL, a lookup miss fails one analysis while the other loads, and a concurrent submit creates exactly one export.

### UI preview for User Story 1 🎨

- [x] T016 [US1] [T-26] [O-06] Build `docs/ui_previews/export_form.html` and `docs/ui_previews/export_detail.html` from `docs/ui_previews/_scaffold.html`, reusing existing classes: the form with no selection (perspective disabled), a selection with a live intersection and one data-name field per ticked analysis, an empty intersection message, an exported mark with its link, and a disabled row with its FR-005 reason; the detail page header and rows in pending, requested from Risk Modeler, downloading and staging, staged, loading, loaded (with the five counts, data ID, archive path), and failed (message + Retry). Get informal approval before T024–T028.

### Service (`app/services/export_service.py`, new)

- [x] T017 [US1] [FR-001] [FR-005] [T-24] Create `app/services/export_service.py` with `ExportableAnalysis` (data-model.md §7) and `list_exportable_analyses(submission_id)`: `analysis_service.list_comparable_analyses(submission_id=...)` rows whose results are ready, extended with `irp_id`, `irp_app_analysis_id`, `loss_results.perspectives` keys, and `_parse_settings`-derived `peril_code`, `region_code`, `currency`; rows with a non-integer `irp_app_analysis_id` or no results are returned disabled with the reason. Own and group rows first, then broker rows grouped by RDM name. Tests in `tests/unit/test_export_service.py` using `tests/unit/analysis_rows.py`.
- [x] T018 [US1] [FR-003] [T-10] Add `perspective_choices(analyses)` to `app/services/export_service.py`: the codes in `settings.export_perspective_codes`, in that order, present in every selected analysis's perspectives; empty list when no selection or empty intersection. Tests in `tests/unit/test_export_service.py`.
- [x] T019 [US1] [FR-004] [T-09] [T-25] [T-32] Add `find_exported(irp_app_analysis_ids, perspective_code)` to `app/services/export_service.py`: reads `stage.rwb_loss_result_manifest` over `LOSS` with `db.read_uncommitted_hint("LOSS")` for any submission, returning per analysis `export_id`, `requested_from_submission_id`, `requested_at`, `requested_by_email`, and the derived status (data-model.md §7 table). Tests over the `loss_db` fixture.
- [x] T020 [US1] [FR-002] Add `list_clients()` (`dbo.Client` over `LOSS`, `ActiveFlag = 'Y'` only, ordered by name) and `form_defaults(submission_id)` (`submission.inception_date`, first `submission_crm_id.crm_id`) to `app/services/export_service.py`. Tests over both fixtures.
- [x] T021 [US1] [FR-004] [FR-006] [T-03] [T-08] [T-12] [T-20] [T-32] [P-15] Add `create_export(submission_id, user_email, analysis_ids, perspective_code, client_id, treaty_incept, crm_id, data_vintage, data_names)` to `app/services/export_service.py`: validation in the contracts/routes.md §4 order raising a typed error that names the analysis or field; `export_id = uuid4()`; one `LOSS` transaction (`get_connection("LOSS")` + `conn.begin()`) inserting every manifest row with the data-model.md §4.1 values (`irp_app_analysis_id` cast to `int`, `analysis_description` from `full_name`, `data_model_vendor = 'RMS'`, `server = settings.risk_modeler_base_url`, `requested_from_submission_id = submission_id`, both statuses `pending`); `IntegrityError` → rollback, re-run `find_exported`, raise the duplicate error naming each blocked analysis with its `requested_at` and `requested_by_email`; then `rwb_job_service.enqueue_rwb_job` for `submit_results_export` (`analyst_request` / `export_id`, `result_export` / `export_id`, `input_data {"export_id", "submission_id"}`) and `dispatch.dispatch`. Never writes `submission.inception_date` or `submission_crm_id`. Tests: values written (FR-006), enqueue row, simulated `IntegrityError` path, submission untouched after an edited inception (P-15).
- [x] T022 [US1] [FR-017] Add `ExportAnalysisDetail`, `derive_status(manifest_row, export_job_row)` (data-model.md §7 table, evaluated top-down), and `get_export_detail(submission_id, export_id)` (header values from the first manifest row joined to `dbo.Client`; one row per analysis with `origin` from `irp_analysis` over `WORKBENCH` and `updated_at` as last change; `None` when no manifest row has this `requested_from_submission_id`) to `app/services/export_service.py`. Tests cover every status branch.

### Routes, nav, templates

- [x] T023 [US1] [T-26] Add hidden nav nodes `submissions.export_new` (crumb "Export") and `submissions.export_detail` (crumb "Export {perspective} · {requested_at}") under the submission in `app/nav/manifest.py`; extend `tests/unit/test_nav_manifest.py` and `tests/unit/test_nav_context.py`.
- [x] T024 [US1] [FR-001] Add the **Export** link beside Compare and View in the `<summary>` bar of `app/templates/partials/analyses_merged_section.html`, rendered only when `analyses_base` starts with `/submissions/`, pointing to `/submissions/{submission_id}/exports/new`.
- [x] T025 [US1] [FR-002] [FR-005] Add `GET /submissions/{submission_id}/exports/new` to `app/routers/submissions.py` and create `app/templates/pages/submission_export_new.html` per contracts/routes.md §2: analysis checkboxes (disabled rows show their reason), the §3 fragment rendered once with no selection, client select, treaty inception, CRM ID, data vintage, Export button disabled until an analysis, a perspective, and a client are chosen; gone-notice partial when the submission does not resolve.
- [x] T026 [US1] [FR-003] [FR-004] [O-07] Add `GET /submissions/{submission_id}/exports/new/fields` to `app/routers/submissions.py` and create `app/templates/partials/export_form_fields.html` per contracts/routes.md §3: perspective select from `perspective_choices` keeping a surviving choice, "The selected analyses share no exportable perspective" when empty, one `data_name[<analysis_id>]` input (max 150) per selected analysis, and when a perspective is chosen the exported marks "Exported {requested_at} by {requested_by_email} · {status}" linking to `/submissions/{requested_from_submission_id}/exports/{export_id}` with `hx-swap-oob` unticking and disabling the row's checkbox. Wire the `hx-get` / `hx-trigger="change"` / `hx-include` / `hx-target="#export-fields"` attributes on the form page.
- [x] T027 [US1] [FR-004] [FR-006] [P-14] Add `POST /submissions/{submission_id}/exports` to `app/routers/submissions.py` per contracts/routes.md §4: parse `analysis_ids[]`, `perspective`, `client_id`, `treaty_incept`, `crm_id`, `data_vintage`, `data_name[<uuid>]`; call `create_export`; a validation or duplicate error re-renders the form with the message and the analyst's values at HTTP 422; an enqueue failure after the manifest commit answers 500; success answers `303` to the detail page (`HX-Redirect` for HTMX). CSRF token on the form. No confirmation step.
- [x] T028 [US1] [FR-017] [T-26] Add `GET /submissions/{submission_id}/exports/{export_id}` to `app/routers/submissions.py` and create `app/templates/pages/submission_export_detail.html` with the header (perspective, client name, treaty inception, CRM ID, data vintage, requester, request time, `export_id`) and one row per analysis showing name, origin, derived status chip, and last change; 404 page when `get_export_detail` returns `None`. Counts, archive path, error message, polling, and Retry are added in US2 and US3.
- [x] T029 [US1] [T-26] Add the export status chip variants and export row styles to `app/static/css/components.css` and `app/static/css/details.css`, extending the existing chip and table classes rather than carrying preview-only classes.
- [x] T030 [US1] [FR-001] [FR-002] [FR-003] [FR-004] [FR-005] Create `tests/unit/test_export_routes.py`: Export link present on the submission-scoped section and absent elsewhere; form renders with defaults and disabled rows; fragment intersection, empty intersection, data-name fields, exported marks with the cross-submission link; POST happy path writes manifest rows and redirects; POST with a blocked analysis answers 422 naming it and the earlier export; POST after a simulated `IntegrityError` answers 422 the same way; detail page renders pending rows and 404s for another submission's export.

### Workers and poller

- [x] T031 [US1] [FR-007] [FR-016] [FR-023] [T-14] [T-23] Create `app/workers/export_jobs.py` with `@rwb_actor(max_retries=0) submit_results_export` per contracts/jobs.md §2: select manifest rows `stage_status = 'pending' AND irp_export_job_id IS NULL` for the export; per row `irp_gateway.submit_analysis_export_job(analysis_id=int(irp_analysis_irp_id), loss_details=[{"metricType": "LOSS_TABLES", "outputLevels": ["Portfolio"], "perspectiveCodes": [perspective_code]}])`; insert the `export` `irp_job` (`export_id`, `irp_analysis_id`, `requested_from_submission_id` from `input_data`, `irp_edm_id`/`irp_rdm_id` from the analysis, `request_params`, `submitted_at`) and write `irp_export_job_id` and `updated_at = now` back, so the detail page's last-change time is the request time while the row waits on Risk Modeler (story 3 acceptance 5); `IRPAPIError` for one analysis marks that row `failed` and continues; any other exception stops with `JobResult.fail`. Tests in `tests/unit/test_export_submit_worker.py` with `fake_irp`: two rows submitted with `updated_at` stamped, one rejected, unreachable Risk Modeler stops early, rows with a job ID are skipped.
  - Proof: `queue_names()` lists `submit_results_export`, `stage_results_export`, and `load_results_export` once the module exists (the loader imports every `app/workers/*_jobs.py`; no registration step)
- [x] T032 [US1] [FR-007] [T-14] In `app/poller/run.py` add `_GETTERS["export"] = irp_gateway.get_export_job` and `_TERMINAL_HANDLERS["export"] = _handle_export_terminal`, which on any terminal status enqueues `stage_results_export` inside the poller's `WORKBENCH` transaction with the contracts/jobs.md §3 arguments (`requestor_type "irp_job"`, `link_type` by `irp_edm_id`/`irp_rdm_id`, `context_type "irp_analysis"`, `input_data {"export_id", "irp_analysis_id", "irp_job_id"}`) and dispatches after commit like the other handlers; the poller's job select gains `export_id`. Tests in `tests/unit/test_export_poller.py`: `FINISHED`, `FAILED`, and `CANCELLED` each enqueue exactly one stage job; a second tick does not enqueue a second one.
- [x] T033 [US1] [FR-007] [FR-008] [FR-009] [FR-010] [FR-016] [FR-020] [FR-024] [T-07] [T-11] [T-15] [T-20] [P-12] [O-08] Add `@rwb_actor(max_retries=0, time_limit=6 * 60 * 60 * 1000) stage_results_export` to `app/workers/export_jobs.py` per contracts/jobs.md §4: entry table (`loaded` → skipped; `staged` → step 8 only; else delete this manifest's `rwb_loss_result_elt_data` and `rwb_loss_result_file` rows and the working directory); fail unless the `export` `irp_job` is `FINISHED`; fail "Archive root {path} is not available" unless `settings.export_archive_dir` is a directory; reuse `{root}/{zip_file}` when present else `irp_gateway.download_export_results(job_id, output_dir=f"{root}/{export_id}/{irp_analysis_id}")` and record `zip_file` relative to the root; unzip into `{submission_outputs_base}/exports/{export_id}/{irp_analysis_id}/`; locate the loss-table folder (`PLT` → "loss table type PLT not supported", other → "unknown loss table type {name}"); read `metadata.csv` and require `AnlsId == irp_app_analysis_id` and `AnalysisCurrency == data_currency` naming both values; record `loss_table_type`, `engine_type`, `data_model_version`; insert one `rwb_loss_result_file` row per `Portfolio/{perspective_code}/*.parquet` (fail on a missing folder or an extra perspective folder); per file in `chunk_index` order `db.elt.upload_parquet(path, "rwb_loss_result_elt_data", schema="stage", extra_columns={...}, column_mapping=ELT_COLUMN_MAP, drop_unmapped_columns=True, connection="LOSS")` and write `row_count`; `SUM(row_count) = 0` → fail "Risk Modeler returned no {perspective_code} loss rows for this analysis"; else `staged`, `staged_at`, `staged_row_count`, remove the working directory; `ensure_pending_rwb_job` for `load_results_export` (`rwb_job` / stage job id, `irp_analysis` context, `input_data {"export_id", "irp_analysis_id", "manifest_id"}`) and `dispatch.dispatch`. Every failure in steps 1–7 stamps `stage_status = 'failed'` and `error_message`. Tests in `tests/unit/test_export_stage_worker.py` with the T015 archive builder and `fake_irp`: happy path (files, rows, `staged`, load job enqueued), export job not `FINISHED`, missing archive root, archive reuse (no download call), `AnlsId` mismatch, currency mismatch, missing `metadata.csv`, `PLT` folder, unknown folder, extra perspective folder, zero rows (P-12), mismatched Parquet columns, re-run on `staged` enqueues load only, re-run after a partial stage removes the partial rows first.
- [x] T034 [US1] [FR-014] [FR-016] [FR-021] [T-18] Add `@rwb_actor(max_retries=0, time_limit=6 * 60 * 60 * 1000) load_results_export` to `app/workers/export_jobs.py` per contracts/jobs.md §5: `loaded` → `JobResult.ok(skipped="loaded")`; `stage_status <> 'staged'` → `JobResult.fail("analysis is not staged")` without touching the manifest; else `db.execute_procedure("stage.usp_load_elt_result", {"manifest_id": manifest_id}, connection="LOSS")`; on exception `UPDATE ... SET load_status = 'failed', error_message = :e, updated_at = :now WHERE manifest_id = :id AND load_status <> 'loaded'` (log and continue if it raises) then `JobResult.fail`; success re-reads `data_id` into `JobResult.ok`. Tests in `tests/unit/test_export_load_worker.py` with `db.execute_procedure` patched: skipped when loaded, fail when not staged, failure stamp on a raised call, success returns `data_id`.

**Checkpoint**: `uv run pytest tests/unit` green. **STOP.** The approver runs quickstart.md Story 1 steps 1–8 on the running stack (needs `EXPORT_ARCHIVE_DIR` created inside `linux-box`, the poller, and the three export queues) before User Story 2 begins. The manifest values on the detail page confirm or correct the `Data.Name` / `Data.Description` sourcing (research R13, Assumed).

---

## Phase 4: User Story 2 — Follow an export and read the post-load summary (Priority: P1)

**Goal**: The submission page lists that submission's exports newest first, and the export detail page shows each analysis's status, last change, archive path, data ID, the five counts, and a failed analysis's message, refreshing while work is in progress.

**Independent Test**: quickstart.md "Story 2" steps 1–4: the exports section lists only this submission's exports with perspective, requester, time, client, analysis count, loaded / failed counts and opens the detail page; an analysis exported at GR and RL shows two rows while the analyses grid is unchanged; a loaded row shows data ID, the five counts (stochastic + historical = staged), and the archive path; a failed row shows its message; every row shows its last change time.

- [x] T035 [US2] [FR-017] [O-06] [T-32] Add `ExportSummary` and `list_exports(submission_id)` to `app/services/export_service.py`: manifest rows with `requested_from_submission_id = submission_id` (with `read_uncommitted_hint("LOSS")`) grouped by `export_id`, joined to `dbo.Client` for the name, with `analysis_count`, `loaded_count` (`load_status = 'loaded'`), `failed_count` (`stage_status = 'failed' OR load_status = 'failed'`), newest `requested_at` first. Tests in `tests/unit/test_export_service.py`: another submission's export is excluded; GR and RL exports of one analysis are two rows.
- [x] T036 [US2] [FR-017] [O-06] Add `GET /submissions/{submission_id}/exports` to `app/routers/submissions.py`, create `app/templates/partials/exports_section.html` (one row per export linking to the detail page; empty state "No exports yet"; the wrapper carries `hx-trigger="every 10s"` only while some export has an analysis not yet loaded or failed), and include it in `app/templates/pages/submission_detail.html` below `analyses_merged_section.html` with `hx-get` on load. The analyses grid, its status vocabulary, and `analysis_row_macros.html` are unchanged.
- [x] T037 [US2] [FR-017] [FR-018] Extend `app/templates/pages/submission_export_detail.html` and `get_export_detail`: per analysis add archive path (`settings.export_archive_dir` joined with `zip_file` when set), `data_id`, rows staged, stochastic rows, historical rows, exposure raised, standard deviation zeroed, and the error message when failed. Move the analysis table into `app/templates/partials/export_analyses_table.html`, served by a new `GET /submissions/{submission_id}/exports/{export_id}/analyses` in `app/routers/submissions.py`, polling every 5 seconds only while a row is not loaded or failed.
- [x] T038 [US2] [FR-017] [FR-018] Extend `tests/unit/test_export_routes.py`: exports section lists this submission's exports newest first with all seven columns and excludes another submission's export; empty state; polling attribute present only while in progress; detail rows show the counts, data ID, archive path, and error; the analyses fragment renders and stops polling when every row is terminal; the submission detail page still renders the analyses grid unchanged.

**Checkpoint**: `uv run pytest tests/unit` green. **STOP.** The approver runs quickstart.md Story 2 steps 1–4 before User Story 3 begins.

---

## Phase 5: User Story 3 — Retry a failed analysis (Priority: P2)

**Goal**: Retry on a failed analysis re-arms exactly one job from the manifest state: load for a staged row, stage when the archive or a usable Risk Modeler export exists, otherwise a new Risk Modeler export for that analysis only. A loaded analysis offers no Retry and re-runs write nothing.

**Independent Test**: quickstart.md "Story 3" steps 1–4 and "Crash recovery": a load failure retried after the lookup is restored loads with no new download (archive mtime unchanged); a missing archive root fixed then retried re-runs the stage job; a bogus job ID plus `FAILED` job retried submits one new Risk Modeler export; a loaded row shows no Retry and a manual re-run of its load job leaves `Data` unchanged; an archive for a different analysis fails again naming the `AnlsId` mismatch; a killed stage worker is reset by the reconciler and the next attempt reuses the archive.

- [x] T039 [US3] [FR-019] [T-28] Add `retry_decision(manifest_row, export_job_row, archive_root, now) -> Literal["load", "stage", "submit"]` to `app/services/export_service.py`: `stage_status = 'staged'` → `load`; `zip_file` set and `{archive_root}/{zip_file}` exists, or the `export` job is `FINISHED` with `completed_at` within 7 days → `stage`; else `submit`. Add `apply_retry(submission_id, export_id, irp_analysis_id)`: 404 when the manifest row is not this submission's, 409 with the reason when the derived status is not failed ("already loaded as data ID {data_id}" for a loaded row), otherwise run the branch (`submit` first resets `irp_export_job_id = NULL`, `stage_status = 'pending'`, `error_message = NULL`), `rwb_job_service.ensure_pending_rwb_job` with the contracts/jobs.md §1 identity for that job type, then `dispatch.dispatch`. Tests in `tests/unit/test_export_retry.py`: every branch of the tree (archive present, archive absent but job `FINISHED` 6 days ago, job `FINISHED` 8 days ago, job `FAILED`), the reset values, the 404 and 409 preconditions, and the idempotent re-arm (a second Retry does not insert a second `rwb_job`).
- [x] T040 [US3] [FR-019] [FR-021] [P-13] Add `POST /submissions/{submission_id}/exports/{export_id}/analyses/{irp_analysis_id}/retry` to `app/routers/submissions.py` (CSRF token; HTMX request re-renders that analysis row from `export_analyses_table.html`, otherwise redirects to the detail page) and the **Retry** button in `app/templates/partials/export_analyses_table.html`, rendered only when the derived status is failed (never for pending, requested from Risk Modeler, staged, loading, or loaded).
- [x] T041 [US3] [FR-019] [FR-020] [FR-021] Extend `tests/unit/test_export_routes.py` and `tests/unit/test_export_stage_worker.py`: Retry route answers per branch and re-renders the row; no Retry button on a loaded or waiting row; a staged row retried enqueues load only; a re-run of `submit_results_export` after branch 3 submits only the reset row (sibling rows with job IDs untouched); a re-run of `load_results_export` on a loaded row returns skipped without calling `execute_procedure`; the stage worker re-entered after a partial stage (crash simulation) deletes the partial rows and reuses the archive without a download call.

**Checkpoint**: `uv run pytest tests/unit` green. **STOP.** The approver runs quickstart.md Story 3 steps 1–4 and the crash-recovery check.

---

## Phase 6: Polish and cross-cutting

- [ ] T042 [P] [P-10] Rewrite `docs/PRD.md` §17.4: broker and group analyses export the same way as own analyses; point to `specs/014-results-export/spec.md` for scope.
- [ ] T043 [P] [T-02] [T-03] [T-14] Update `docs/DATA_MODEL.md`: §1 (`LOSS` is CIC's `CRE_Trial_ELT_Repository`, mirrored in dev as `rwb_loss`; the `stage` schema is the Workbench's), §8 (the three export job types and the `result_export` context type), §9 (replace `analysis_result_meta` / `result_export` with a pointer to `specs/014-results-export/data-model.md`).
- [ ] T044 [P] [T-23] Create `tests/irp/test_export_download.py` (opt-in, `--run-irp`): submit an export for a finished sandbox analysis through `irp_gateway.submit_analysis_export_job`, poll `get_export_job` with bounded sleeps, download through `download_export_results`, and assert the research R6 archive layout and `metadata.csv` columns. Record the result in `plan.md` T-23 (Assumed → Approved) and `research.md` R7.
  - Proof: `make shell`, then `uv run pytest tests/irp --run-irp -k export` passes (unverified until run)
- [ ] T045 [P] Extend `tests/unit/test_architecture_guards.py`: `app/routers/` never imports or calls `download_export_results`, `get_export_job`, or `submit_analysis_export_job`; `app/poller/` never calls a `poll_*` method; `app/services/export_service.py` and `app/workers/export_jobs.py` reach SQL only through `db.execute`, `db.execute_command`, `db.execute_procedure`, `get_connection`, and `db.elt.upload_parquet`.
- [ ] T046 Review the whole diff for subtraction per AGENTS.md "Code Quality": remove comments and tests that restate the implementation, inline single-use helpers in `app/services/export_service.py` and `app/workers/export_jobs.py`, drop speculative branches and configurability, and confirm docs changes live only in the files that own the fact.
- [ ] T047 Run the full quickstart.md on the developer's stack (stories 1–3, crash recovery, by-hand load) and report by tier: unit count, SQL Server tier count, IRP sandbox result. Update `plan.md` "Plan status" with what remains blocked on O-05 (CIC's repository load, `LOSS` login and grants) and O-10 (share mount).

---

## Dependencies and execution order

### Phase dependencies

- **Phase 1 (loss repository setup)**: no dependencies; T001 → T002; T003, T004, T006 parallel; T005 after T004; T007 after T002, T003, T005; T008 after T007; T009 after T005, T006, T007. T009 needs `linux-box` up (the developer's call).
- **Phase 2 (foundational)**: after Phase 1's DDL exists (T014 mirrors T003–T004). T010–T015 all parallel.
- **Phase 3 (US1)**: after Phase 2. T016 (preview) before T024–T028. T017–T022 service tasks before T025–T028 routes. T031–T034 workers can proceed in parallel with the route work once T012–T015 exist. T030 after T025–T028.
- **Phase 4 (US2)**: after US1 is clicked. T035 → T036; T037 → T038.
- **Phase 5 (US3)**: after US2 is clicked. T039 → T040 → T041.
- **Phase 6 (polish)**: after US3. T042–T045 parallel; T046 then T047 last.

### User story dependencies

- **US1** depends only on Phases 1–2. It creates the export detail page in its basic form because story 1 acceptance 1 lands the analyst on it.
- **US2** extends US1's detail page and adds the exports section. It reads manifest rows written by US1's route and workers but can be built and unit-tested against fixture rows alone.
- **US3** adds the Retry decision and route on top of US1's workers, which already resume from the manifest (FR-020 is proven in T033–T034 and re-checked in T041).

### Parallel opportunities

```text
Phase 1:  T003 | T004 | T006          (three files)
Phase 2:  T010 | T011 | T012 | T013 | T014 | T015
Phase 3:  {T017..T022 service} with {T031 submit worker, T032 poller} once T012–T015 land
          T033 stage worker with T034 load worker
          T023 nav | T024 Export link | T029 CSS
Phase 6:  T042 | T043 | T044 | T045
```

---

## Implementation strategy

### MVP first (User Story 1)

1. Phase 1: DDL, procedure, seeds, bootstrap, SQL Server tier test (`make bootstrap-loss`, `make test-sql`).
2. Phase 2: Workbench column and seeds, settings, gateway, fakes, `LOSS` fixture.
3. Phase 3: preview approval, service, routes, workers, poller.
4. **STOP**: the approver runs quickstart.md Story 1 on the stack.

### Incremental delivery

1. US1 → export accepted, processed, loaded; detail page shows statuses.
2. US2 → exports section and the full post-load summary.
3. US3 → Retry.
4. Polish → docs, guards, sandbox spike, subtraction review, full quickstart run.

### What stays blocked after all tasks

End-to-end verification against CIC's `CRE_Trial_ELT_Repository` waits on O-05
(CIC's load of the five tables, the `LOSS` login, and the grants in
contracts/load-procedure.md §4) and O-10 (the share mount). Everything above
is verifiable against the dev mirror and the Risk Modeler sandbox.
