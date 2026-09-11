# Data Model — Loss Results Export (Iteration 11)

Schema owned by this feature. Rationale lives in [research.md](research.md);
payloads in [contracts/](contracts/). Column tables carry a **Source** (where
the value comes from) and **Read by** (what reads it); "traceability" means no
target column reads it.

## 1. Databases

| Database | Change |
|---|---|
| `rwb_workbench` (`WORKBENCH`; at CIC a database on the same server as the repository, name not yet chosen, T-29) | `irp_job.export_id`; seed rows in `rwb_job_type_kind` and `rwb_job_context_type_kind`. DDL in `alembic/versions/0001_initial.py`. No new table. |
| `CRE_Trial_ELT_Repository` (`LOSS`; dev mirror `rwb_loss`) | New `stage` schema: three tables, one procedure (`db/bootstrap/loss_schema.sql`). CIC's `dbo.Client` (read by the form), `dbo.Lookup_RMS_HistoricalRDS` (read by the procedure), `dbo.Data`, `dbo.RMSELT`, `dbo.RMS_HistoricalRDS` (written by the procedure). All five CIC tables are in this database. |

## 2. Workbench changes

### 2.1 `irp_job` — one new column

| Column | Type | Source | Read by |
|---|---|---|---|
| `export_id` | `Uuid`, nullable, no FK, index `ix_irp_job_export_id` | `submit_results_export` worker, on each `export` job it inserts | Poller terminal handler (passes it into the stage job's `input_data`); the submit worker, to reuse a job a crashed run already recorded; a DBA tracing an export's Risk Modeler jobs (FR-023; the detail page does not list them) |

Existing `irp_job` columns an `export` job uses: `irp_job_type = 'export'`
(seed exists), `irp_id` (Risk Modeler export job ID), `irp_analysis_id`,
`requested_from_submission_id` (from the submit job's `input_data`, not from
the analysis), `irp_edm_id` or `irp_rdm_id` (from the analysis row),
`request_params` (the request body returned by `submit_analysis_export_job`),
`status` (Risk Modeler mirror, Article 3 carve-out), `completed_at` (the Retry
seven-day window).

### 2.2 Seed changes

`rwb_job_type_kind`:

| Code | Label | Sort | Change |
|---|---|---|---|
| `submit_results_export` | Submit Results Export | 40 | Add |
| `stage_results_export` | Stage Results Export | 41 | Add |
| `load_results_export` | Load Results Export | 42 | Add |
| `download_export_file` | — | 40 | Remove (never had a worker) |
| `push_results_to_loss_repo` | — | 50 | Remove (never had a worker) |

`rwb_job_context_type_kind`: add `result_export`, label `Result Export`, sort
70. Like `execution`, it names an ID with no table.

`rwb_job_requestor_type_kind`, `rwb_job_link_type_kind`, `irp_job_type_kind`:
no change.

### 2.3 `rwb_job` rows

Column values per job type are in [contracts/jobs.md](contracts/jobs.md) §1.
No schema change.

## 3. Settings

| Setting (`app/config.py`) | Env var | Default | Purpose |
|---|---|---|---|
| `export_perspective_codes` | `EXPORT_PERSPECTIVE_CODES` | `GU,GR,RL,RP` | Codes the form may offer (T-10) |
| `export_archive_dir` | `EXPORT_ARCHIVE_DIR` | `""` (stage worker fails when empty or missing) | Root for permanent archives (T-15). Production: the share mount; dev: `/workspace/data/export_archive` under the `rwb-data` volume |
| `EXPORT_STAGING_DIR` | `export_staging_dir` | — (unset) | Transient extraction under `{export_id}/{irp_analysis_id}/`; the stage worker fails the analysis when it is not a directory and never creates it |
| `risk_modeler_base_url` (existing) | `RISK_MODELER_BASE_URL` | — | `manifest.server` → `Data.Server` |

## 4. Loss repository `stage` schema

All DDL in `db/bootstrap/loss_schema.sql`, idempotent, `CREATE SCHEMA stage
AUTHORIZATION dbo`. Categorical columns carry `CHECK` constraints (T-19).

### 4.1 `stage.rwb_loss_result_manifest` — one row per analysis per perspective, grouped by export

| Column | Type | Source | Read by |
|---|---|---|---|
| `manifest_id` | `INT IDENTITY` PK | — | Child tables; load job `input_data`; procedure parameter |
| `export_id` | `UNIQUEIDENTIFIER` NOT NULL | Route on submit | Exports section and detail page (group key); `rwb_job.requestor_id` of the submit job; `irp_job.export_id` |
| `requested_by_email` | `NVARCHAR(255)` NOT NULL | `app_user.email` of the session user | Exports section; DBA traceability |
| `requested_at` | `DATETIME2` NOT NULL | Submit time | Exports section |
| `requested_from_submission_id` | `UNIQUEIDENTIFIER` NOT NULL, no FK (the submission lives in `WORKBENCH`, T-29) | Route: the submission whose export form was submitted (spec P-16) | Exports section filter; detail page and Retry (404 when the export was not requested from the page's submission); the exported mark's link on another submission's form |
| `irp_analysis_id` | `UNIQUEIDENTIFIER` NOT NULL | `irp_analysis.id` | Detail page; worker row lookup with `export_id` |
| `irp_analysis_irp_id` | `NVARCHAR(64)` | `irp_analysis.irp_id` | Traceability to Risk Modeler; the submit worker's `analysis_id` argument |
| `irp_app_analysis_id` | `INT` NOT NULL | `irp_analysis.irp_app_analysis_id` cast on submit (form rejects non-integers) | `Data.AnalysisID`; duplicate key; checked against `metadata.csv` `AnlsId` |
| `analysis_name` | `NVARCHAR(256)` | `irp_analysis.name` | `Data.Name` |
| `analysis_description` | `NVARCHAR(512)` NULL | `irp_analysis.full_name` (T-24) | `Data.Description` |
| `perspective_code` | `VARCHAR(5)` NOT NULL | Form | `Data.Perspective`, `RMS_HistoricalRDS.Perspective`; duplicate key; stage folder selection |
| `client_id` | `INT` NOT NULL | Form, from `dbo.Client` | `Data.ClientID`, `RMS_HistoricalRDS.ClientID`; exports section joins `dbo.Client` for the name |
| `treaty_incept` | `DATE` NOT NULL | Form, default `submission.inception_date` | `Data.TreatyIncept`; `RMS_HistoricalRDS.TreatyIncept` (widened to `datetime`) |
| `treaty_year` | `INT` NULL | `submission.treaty_year` | `RMS_HistoricalRDS.TreatyYear` via `CONVERT(varchar(4))` |
| `crm_id` | `VARCHAR(30)` NULL | Form, default first `submission_crm_id.crm_id` | `Data.CRMID` |
| `data_name` | `NVARCHAR(150)` NULL | Form, per analysis | `Data.DataName` |
| `data_vintage` | `DATE` NULL | Form | `Data.DataVintage`; `RMS_HistoricalRDS.DataInforce` as `CONVERT(varchar(15), …, 23)` |
| `data_currency` | `NVARCHAR(5)` NOT NULL | `settings_metadata` currency code (`_parse_settings`) | `Data.DataCurrency`; checked against `metadata.csv` `AnalysisCurrency` |
| `data_model_vendor` | `NVARCHAR(10)` NOT NULL | Constant `RMS` | `Data.DataModelVendor` |
| `server` | `VARCHAR(255)` | `settings.risk_modeler_base_url` | `Data.Server` |
| `irp_export_job_id` | `NVARCHAR(64)` NULL | Submit worker | Retry decision; traceability |
| `loss_table_type` | `VARCHAR(3)` NULL, CHECK `('ELT','PLT')` | Stage worker, archive folder name | Stage table and procedure selection |
| `engine_type` | `VARCHAR(5)` NULL, CHECK `('DLM','HD','GROUP')` | Stage worker, `metadata.csv` `Engine Type` | Detail page; O-08 |
| `data_model_version` | `NVARCHAR(10)` NULL | Stage worker, `metadata.csv` `ModelVersion`, reduced to the decimal form when Risk Modeler writes a build number (`23.0.2250.1` → `23.0`) | `Data.DataModelVersion`; lookup join and assertion |
| `peril_code` | `NVARCHAR(10)` NULL | `settings_metadata` `perilCode` on submit | Detail page; traceability (not part of the lookup join, R4) |
| `region_code` | `NVARCHAR(10)` NULL | `settings_metadata` `regionCode` | Detail page; traceability |
| `zip_file` | `NVARCHAR(1024)` NULL | Stage worker after download: `{export_id}/{irp_analysis_id}/{filename}` relative to `EXPORT_ARCHIVE_DIR` | Detail page; stage worker reuse; Retry |
| `stage_status` | `VARCHAR(10)` NOT NULL, CHECK `('pending','failed','staged')` | Route (`pending`), submit worker (`failed`), stage worker (`staged`/`failed`), Retry (`pending`) | Detail page; submit worker row selection; stage/load entry checks |
| `staged_at` | `DATETIME2` NULL | Stage worker | Detail page |
| `load_status` | `VARCHAR(10)` NOT NULL, CHECK `('pending','loading','loaded','failed')` | Route (`pending`); procedure (`loading` claim, `loaded` at commit, `failed` in CATCH); load worker (`failed` when the call raises early) | Detail page; procedure claim; load entry check |
| `loaded_at` | `DATETIME2` NULL | Procedure | Detail page |
| `error_message` | `NVARCHAR(MAX)` NULL | Worker or procedure | Detail page |
| `data_id` | `INT` NULL | Procedure, `OUTPUT INSERTED.DataID` | `RMSELT.DataID`, `RMS_HistoricalRDS.DataID`; detail page |
| `staged_row_count` | `INT` NULL | Stage worker, sum of file `row_count` | Detail page |
| `stochastic_row_count` | `INT` NULL | Procedure | Detail page |
| `historical_row_count` | `INT` NULL | Procedure | Detail page |
| `exp_value_raised_count` | `INT` NULL | Procedure | Detail page |
| `std_dev_zeroed_count` | `INT` NULL | Procedure | Detail page |
| `inserted_at`, `updated_at` | `DATETIME2` | Route; worker or procedure | Detail page "last change" reads `updated_at` |

Constraints: `UNIQUE (irp_app_analysis_id, perspective_code)` (T-09);
`UNIQUE (export_id, irp_analysis_id)`; index on `export_id`; index on
`requested_from_submission_id` (the exports section's filter, T-32).

### 4.2 `stage.rwb_loss_result_file` — one row per Parquet file

| Column | Type | Source | Read by |
|---|---|---|---|
| `result_file_id` | `INT IDENTITY` PK | — | Data tables |
| `manifest_id` | `INT` NOT NULL FK → manifest | Stage worker | Grouping |
| `result_file` | `NVARCHAR(1024)` NOT NULL | Path inside the archive | Traceability |
| `output_level` | `VARCHAR(20)` | Parsed from path (`Portfolio`) | Traceability |
| `perspective_code` | `VARCHAR(5)` | Parsed from path; must equal the manifest's | Check |
| `chunk_index` | `INT` | Parsed `_{n}` suffix | Ordering |
| `row_count` | `INT` | `upload_parquet` return | `manifest.staged_row_count` |
| `staged_at` | `DATETIME2` | Stage worker | Detail page |

`UNIQUE (manifest_id, result_file)`.

### 4.3 `stage.rwb_loss_result_elt_data` — one row per ELT event

| Column | Type | Source (Parquet column) | Read by |
|---|---|---|---|
| `manifest_id` | `INT` NOT NULL FK | `upload_parquet` `extra_columns` | Every procedure statement filters on it |
| `result_file_id` | `INT` NOT NULL FK | `extra_columns` | Traceability |
| `port_info_id` | `INT` | `PortInfoId` | — |
| `port_info_name` | `NVARCHAR(256)` | `PortInfoName` | — |
| `port_info_num` | `NVARCHAR(64)` | `PortInfoNum` | — |
| `event_id` | `INT` NOT NULL | `EventId` | Lookup join; `RMSELT.EventID`, `RMS_HistoricalRDS.EventID` |
| `rate` | `FLOAT` | `Rate` | — |
| `loss` | `FLOAT` | `Loss` (never altered) | Both targets; exposure correction |
| `std_dev_i` | `FLOAT` | `StdDevI` (zeroed if negative, stochastic only) | `RMSELT.StdDevI` |
| `std_dev_c` | `FLOAT` | `StdDevC` (same) | `RMSELT.StdDevC` |
| `exp_value` | `FLOAT` | `ExpValue` (raised to `loss` if lower) | `RMSELT.ExpValue` |
| `event_type` | `VARCHAR(10)` NULL, CHECK `('stochastic','historical')` | Procedure | Target selection |
| `exp_value_raised` | `BIT` NOT NULL DEFAULT 0 | Procedure | `manifest.exp_value_raised_count` |
| `std_dev_zeroed` | `BIT` NOT NULL DEFAULT 0 | Procedure | `manifest.std_dev_zeroed_count` |
| `inserted_at` | `DATETIME2` DEFAULT `SYSUTCDATETIME()` | — | Audit |

Clustered index on (`manifest_id`, `event_id`). Rows stay after load; a CIC-side purge (O-03) may delete from this table only, never from the manifest or file tables. The one Workbench-side deletion is the stage worker's restart branch (contracts/jobs.md §4), which removes the file and loss rows of an interrupted stage before staging again (spec FR-024).

### 4.4 `stage.rwb_loss_result_plt_data` — designed, not built (T-13, O-08)

Columns from the HD example's Parquet schema: `manifest_id`, `result_file_id`,
`portfolio_id` (`NVARCHAR(64)`, Parquet `PortfolioId` string),
`portfolio_name`, `portfolio_num`, `period_id` (`BIGINT`), `event_id`
(`BIGINT`), `event_date`, `loss_date` (`DATETIME2`), `loss` (`FLOAT`), `region`
(`VARCHAR(10)`), `peril` (`VARCHAR(10)`), `weight` (`FLOAT`), `event_type`,
`inserted_at`. No `ExpValue`/`StdDev*`/`Rate`, so neither correction applies.
Until built, the stage worker fails an archive whose loss-table folder is
`PLT` with "loss table type PLT not supported".

### 4.5 Procedure

`stage.usp_load_elt_result @manifest_id INT` — contract in
[contracts/load-procedure.md](contracts/load-procedure.md). Reads
`dbo.Lookup_RMS_HistoricalRDS` by two-part name (T-21).

## 5. CIC target mapping (written by the procedure only)

### 5.1 `dbo.Data` — one row per loaded analysis

| Column | Value |
|---|---|
| `DataID` | `IDENTITY`; captured via `OUTPUT INSERTED.DataID INTO @inserted` → `manifest.data_id` |
| `ClientID` | `manifest.client_id` |
| `TreatyIncept` | `manifest.treaty_incept` |
| `DataVintage` | `manifest.data_vintage` |
| `DataName` | `manifest.data_name` |
| `DataModelVendor` | `RMS` |
| `DataModelVersion` | `manifest.data_model_version` (`25.0`) |
| `DataCurrency` | `manifest.data_currency` |
| `Server` | `manifest.server` |
| `AnalysisID` | `manifest.irp_app_analysis_id` |
| `Name` | `manifest.analysis_name` |
| `Description` | `manifest.analysis_description` |
| `Perspective` | `manifest.perspective_code` |
| `CRMID` | `manifest.crm_id` |
| `Database`, `ArchiveFile` (O-10), `AReLossSet`, `LOB`, `Geography` | Not populated |

`Name` and `Description` (and §5.3 `Event_Name`) are `VARCHAR(MAX)` at CIC
while the manifest and lookup columns are `NVARCHAR`: a character outside the
server's code page arrives as `?` with no error. CIC owns those columns.

### 5.2 `dbo.RMSELT` — stochastic rows

`DataID` ← `manifest.data_id`; `EventID`, `Loss`, `StdDevI`, `StdDevC`,
`ExpValue` ← the stage row after corrections.

### 5.3 `dbo.RMS_HistoricalRDS` — historical rows

| Column | Value |
|---|---|
| `DataID` | `manifest.data_id` |
| `ClientID` | `manifest.client_id` |
| `Peril` | lookup `Peril` (no truncation; overflow fails the load, O-04) |
| `ModelVersion` | lookup `ModelVersion` |
| `TreatyYear` | `CONVERT(varchar(4), manifest.treaty_year)` |
| `TreatyIncept` | `manifest.treaty_incept` |
| `DataInforce` | `CONVERT(varchar(15), manifest.data_vintage, 23)`; null when blank |
| `EventID` | stage `event_id` |
| `Type` | lookup `Type` |
| `Event_Name` | lookup `Name` |
| `Loss` | stage `loss` |
| `PCS` | lookup `[PCS#]` (no truncation) |
| `Perspective` | `manifest.perspective_code` |
| `AReLossSet` | Not populated |

## 6. Dev mirror (`db/bootstrap/loss_dev_mirror.sql`)

`dbo.Client`, `dbo.Data`, `dbo.RMSELT`, `dbo.RMS_HistoricalRDS`, and
`dbo.Lookup_RMS_HistoricalRDS` copied from `cic-reference/` into `rwb_loss`.
`bootstrap_loss.py` then seeds `dbo.Client` with made-up rows and
`dbo.Lookup_RMS_HistoricalRDS` from
`db/bootstrap/seed/lookup_rms_historical_rds.csv`: the 2,754 historical
events (`EVENTTYPECODE = HIST`) of Moody's `EVENT` reference export, 13
model versions, `Peril` in `WS`/`EQ`/`WT` (T-30, research R16). Column map:
`EventID` ← `EVENTID`, `Peril` ← `PERILCODE`, `Type` ← `'HIST'`, `Name` ←
`EVENTNAME`, `ModelVersion` ← `MODELVERSIONCODE`, `CatYear` ← year in
`EVENTNAME` or `NULL`, `[PCS#]` ← `NULL`. Never run against production; the
script refuses when `MSSQL_LOSS_DATABASE` is not `rwb_loss`.

## 7. View models (`app/services/export_service.py`)

### ExportableAnalysis — one form row

| Field | Source | Rule |
|---|---|---|
| `id`, `name`, `origin` | `list_comparable_analyses(submission_id=…)` | Only rows whose results state is ready |
| `irp_id`, `irp_app_analysis_id` | `irp_analysis` | `irp_app_analysis_id` must parse as `int`, else the row is listed disabled with the reason (FR-005) |
| `perspectives` | `loss_results.perspectives` keys | Intersection input |
| `peril_code`, `region_code`, `currency` | `_parse_settings(settings_metadata)` | Recorded on the manifest; `currency` is checked against the archive at stage |
| `exported` | Manifest row for (`irp_app_analysis_id`, chosen perspective), from any submission | When set: `requested_at`, `requested_by_email`, derived status, and `export_id` + `requested_from_submission_id` for the link to that export's detail page; row not tickable |

### ExportSummary — one exports-section row

`export_id`, `perspective_code`, `requested_by_email`, `requested_at`,
`client_name` (join `dbo.Client`), `analysis_count`, `loaded_count`,
`failed_count` — grouped from the manifest rows whose
`requested_from_submission_id` is the page's submission (spec P-16), newest
first.

### ExportAnalysisDetail — one detail-page row

Manifest columns, plus `origin` (own, broker, or group) read from `irp_analysis` over `WORKBENCH` by `manifest.irp_analysis_id`, plus a derived `status`, evaluated top-down:

| Condition | Displayed status |
|---|---|
| `load_status = loaded` | loaded |
| `stage_status = failed` or `load_status = failed` | failed |
| `load_status = loading` | loading |
| `stage_status = staged` | staged |
| `irp_export_job_id IS NULL` | pending |
| `export` `irp_job.status` terminal, any outcome | downloading and staging (the poller has handed the row to the stage worker, which stamps `stage_status = failed` itself when the job did not finish) |
| otherwise | requested from Risk Modeler |

Last change time is `manifest.updated_at`. Retry is offered when the status
is failed, which the manifest row alone decides. Comparison pairs and the export form selection are not persisted.
