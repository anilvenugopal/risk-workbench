# Loss Result Export — Design Overview

Working design for spec 014. Sources: design session notes 21–25
(`docs/design_session_notes/`), CIC DDL in `cic-reference/`, a DLM export
example in `../../../example_requests_responses/25437617_*_Stochastic_Losses/`,
and an HD export example in `../../../25827751_*_Typhoon_Only_Losses/`.
`Loss_Result_Loading.docx` in this folder is the earlier draft and is superseded
by this document.

## 1. Summary

An analyst selects one or more finished analyses, one financial perspective,
and a client, then clicks **Export**. The Workbench requests a Parquet export of
the loss table from Risk Modeler for each analysis, downloads each archive when
Risk Modeler finishes it, stages the rows in the loss repository database,
classifies each event as stochastic or historical by joining to CIC's
historical event lookup table, applies two automatic corrections, and copies
the rows into CIC's three target tables: `dbo.Data` (one header row per
analysis), `dbo.RMSELT` (stochastic events), and `dbo.RMS_HistoricalRDS`
(historical events). The analyst then sees a per-analysis summary with row
counts, including how many rows each correction touched.

No analyst review step exists between export and load. The downloaded
archive is kept on a shared drive; the extracted Parquet files are deleted once
staged. Each analysis is processed, committed, and recovered on its own; one
failing analysis does not hold up the others in the same export.

Sizing from CIC: tens of thousands of exports per year, millions of loss rows.

## 2. Scope

Built and validated in this iteration:

- Event loss table (ELT) export from DLM analyses, own and broker, including
  group analyses.
- A configured set of financial perspectives (§4.1). The first set is GU, GR,
  RL, and RP. Adding a perspective later is a configuration change plus
  validation against the loss repository, not a design change.
- Enrichment from `Lookup_RMS_HistoricalRDS`; the two corrections in §4.4.
- Load into `Data`, `RMSELT`, and `RMS_HistoricalRDS`. The historical write is
  always on.
- Post-load summary with row counts.
- A block when an analysis and perspective already has a manifest row, in
  any status. A failed export is fixed with Retry, never exported again.

Designed for, built later:

- **HD analyses and period loss tables (PLT).** An HD export example has been
  captured (analysis 42044, job 25827751). Its archive has the same layout as
  the DLM example with `PLT` in place of `ELT`, and the PLT Parquet columns
  are known (§4.3). The loss repository has no PLT destination table, and
  `Data` has no column that says which loss table type a `DataID` holds. The
  manifest carries the loss table type and engine type from the first build,
  so the HD path adds the PLT stage table in §4.3 and a load procedure rather
  than changing the ELT tables. Remaining questions are in
  O-08.
- **The TY perspective.** Note 21 describes TY as treaty selection that
  aggregates one treaty across several analyses within one EDM. How Risk
  Modeler exports it has not been investigated and no example exists. It is
  its own user story (O-02).

Not in scope:

- Editing `Data` rows after load. The workflow tool's existing edit process
  covers `DataName` and `DataVintage` (note 23 O23-7).
- Pre-fetching loss tables before the analyst clicks Export (note 24 D12/D14).
- Any analyst review or approval step between export and load.

## 3. What exists today

| Capability | State |
|---|---|
| AAL and EP retrieval for viewing | Built. `retrieve_analysis_results` writes `irp_analysis.loss_results`. No loss table involved. |
| Risk Modeler export job: submit, status, download | Available in irp-integration: `analysis.submit_analysis_export_job(analysis_id, loss_details, file_extension)`, `export_job.get_export_job(job_id)`, and `export_job.download_export_results(job_id, output_dir)`. The Workbench calls none of them: no route or worker submits an export, the poller has no branch for the `export` job type, and `irp_job_type_kind` has the `export` seed row and nothing else. |
| Unzip, manifest, staging | Not built. `db/elt.py` has `upload_parquet` (stream a Parquet file into a SQL Server table) and `enrich` (set-based UPDATE through a session temp table), both unused. |
| Stored procedure call | Not built. `db/execute.py` has `execute` (connection with an implicit transaction, rolled back at close) and `execute_command` (`engine.begin()`, committed at close); neither runs a statement with autocommit, which the load procedure needs (§4.4). |
| `download_export_file`, `push_results_to_loss_repo` job types | Seed rows only. No worker, no dispatch entry. Replaced by the three job types in §4.8. |
| Stage schema and tables | Do not exist. `db/bootstrap/loss_schema.sql` has no `stage` schema. |
| `analysis_result_meta`, `result_export` (DATA_MODEL §9) | Documented, not built. §9 describes a Parquet-on-disk design with `*_file_path` columns. Removed; the Workbench keeps no export table (§4.1). |
| Access to the loss repository | Approved 9/2 by Nagi and Ross. Logins and the server host name are still pending (note 25 O25-7). |

## 4. Export process

Each step below says what happens, then lists the tables that step writes.
Every column table has a Source column (where the value comes from) and a
Read-by column (what reads it). "Traceability" in Read-by means no target
column reads it; it exists so a person can tie a loaded row back to its
export, analysis, and file.

Tables live in two databases:

- **Workbench** (`rwb_workbench`): no new table. One new column on
  `irp_job` (§4.2) and `rwb_job` rows of three new types (§4.6). DDL goes
  into `alembic/versions/0001_initial.py` (single revision until cutover).
  Existing tables are named as in `docs/DATA_MODEL.md` §6 and §8.
- **Loss repository** (`CRE_Trial_ELT_Repository`, approved 9/2, note 25
  D2): tables under the `stage` schema at three grains. The manifest (one
  row per analysis per export, §4.1), the result file (one row per Parquet
  file, §4.3), and the ELT data table (one row per loss row, §4.3) are built
  in this iteration; the PLT data table (§4.3) is designed and built with the
  HD path. The grains differ, so one table cannot hold them without
  repeating analysis-level facts on every file row. DDL is authored in
  `db/bootstrap/loss_schema.sql`, which `bootstrap-loss` applies locally and
  the CIC DBA applies in CIC environments. CIC's own tables `dbo.Data`,
  `dbo.RMSELT`, and `dbo.RMS_HistoricalRDS` are the targets (§4.4).

### 4.1 Export request

The export form opens from a submission's analyses page, so the route knows
the submission. Own analyses relate to a submission through `submission_edm`,
group analyses through `irp_analysis.submission_id`, broker analyses through
the submission's RDM. The submission supplies the form defaults below and is
recorded on the `irp_job` rows (§4.2).

The export form takes:

| Field | Rule |
|---|---|
| Analyses | One or more finished analyses. Own, broker, and group analyses all qualify. An analysis that already has a manifest row for the chosen perspective is shown as exported, with the existing export's date, requester, and status, and cannot be selected (rule below). |
| Perspective | One code. The codes the Workbench can export are listed in the env var `EXPORT_PERSPECTIVE_CODES` (first value `GU,GR,RL,RP`). The form offers the codes in that list that every selected analysis has data for, read from `irp_analysis.loss_results`. |
| Client | Selected from the read-only `dbo.Client` table in the loss repository (`ClientID`, `ClientName`, `ActiveFlag`). Required. |
| Treaty inception | Defaults to `submission.inception_date`. Editable. |
| CRM ID | Defaults from `submission_crm_id`. Editable. |
| Data name | Optional, one text field per selected analysis, never required (note 23 D12; Cheryl and Wendy agreed). A blank field leaves `Data.DataName` null for the analyst to fill in later through the workflow tool. |
| Data vintage | Optional, blank by default. Entered later through the workflow tool if left blank. |

One manifest row exists per analysis per perspective, ever. The duplicate
key is (`irp_app_analysis_id`, `perspective_code`): the Risk Modeler
application analysis ID and the perspective code, which are what land in
`Data.AnalysisID` and `Data.Perspective`, so the rule is stated in the
columns CIC can see. The Workbench UUID `irp_analysis_id` is not the key,
because the same Risk Modeler analysis registered twice in the Workbench
would otherwise load twice. The status of the existing row does not matter:
`pending`, `staged`, `loading`, `loaded`, and `failed` all block. A failed
export is fixed with Retry on the export detail page (§4.7), never by
exporting the analysis again, so no analysis ever has two rows for one
perspective and a second `Data` row cannot arise from the form.

The rule is enforced in three places, each catching what the one before it
cannot:

1. **Form render.** When the analyst picks a perspective, the form reads
   the manifest rows whose `irp_app_analysis_id` is among the selected
   analyses and whose `perspective_code` matches, and marks each such
   analysis as exported, showing `requested_at`, `requested_by_email`,
   `stage_status`, `load_status`, and `data_id`. A marked analysis cannot
   be submitted.
2. **Route on submit.** The route runs the same query again before it
   inserts anything and rejects the whole submission, naming the analysis
   and the existing export, if any selected analysis now has a row. This
   catches an export another analyst submitted while the form was open.
3. **Unique index.** `stage.rwb_loss_result_manifest` is unique on
   (`irp_app_analysis_id`, `perspective_code`). The route inserts all of an
   export's manifest rows in one transaction, so when two analysts submit
   the same analysis in the same second, the second insert fails on the
   index, the transaction rolls back with none of that export's rows
   written, and the route reports the analysis by name. The index is the
   guarantee; the two checks before it exist so the analyst learns about
   the block before, not after, filling in the form.

There is no override. Whether CIC ever needs the same analysis and
perspective loaded a second time, and who would clear the manifest row to
allow it, is O-01.

Every page read of the manifest table (the duplicate check, the export
list, the export detail page) uses `WITH (READUNCOMMITTED)` until O-05
confirms that `READ_COMMITTED_SNAPSHOT` is on for the repository. The load
procedure holds an exclusive lock on the manifest row from its claim to its
commit (§4.4), which can be minutes for a large analysis; under plain
`READ COMMITTED` a page that reads that row waits for the commit. A dirty
read of one status row costs nothing here and lets the detail page show
`loading` while the load runs; the duplicate check can afford it because the
unique index, not the read, is what stops a second row. If the DBA confirms
snapshot isolation, the hint is dropped.

Submitting generates an `export_id` (a new UUID, the batch ID) and writes:

1. One manifest row per selected analysis in the loss repository, all
   carrying the `export_id`, with `stage_status = pending` and that
   analysis's data name.
2. One `rwb_job` of type `submit_results_export` in the Workbench, with
   `requestor_id = export_id` and the submission ID in `input_data` (§4.6).

The manifest rows are the export. They are the persisted plan the workers
execute, and the export list and detail pages read them, grouped by
`export_id`, joining `dbo.Client` in the same database for the client name.
The Workbench keeps no table of its own for exports; `export_id` is a plain
UUID carried by the `rwb_job` and `irp_job` rows, the same way
`execution_id` ties an analysis batch together without a table.

The two writes hit two databases, so they are not one transaction. The
manifest rows go first. If the `rwb_job` insert then fails, the route reports
the error; the manifest rows stay `pending` with no job, which the export
detail page shows.

#### `stage.rwb_loss_result_manifest` — loss repository, one row per analysis per perspective, grouped by export

| Column | Source | Read by |
|---|---|---|
| `manifest_id` | `INT IDENTITY`. | Primary key; `rwb_loss_result_file.manifest_id`, `rwb_loss_result_elt_data.manifest_id`, the load job's `input_data` and the load procedure's `@manifest_id` parameter. |
| `export_id` | UUID generated by the route on submit. `UNIQUEIDENTIFIER`. | The batch ID to track across CIC and Workbench tables (note 25 D7). Export list and detail pages group on it; `rwb_job.requestor_id` of the submit job; `irp_job.export_id`. |
| `requested_by_email` | `app_user.email` of the session user. | Export list page; traceability for a CIC DBA without Workbench access, who cannot resolve a Workbench user ID. |
| `requested_at` | Submit time. | Export list page; same. |
| `irp_analysis_id` | `irp_analysis.id` (Workbench). `UNIQUEIDENTIFIER`. | Export detail page; with `export_id`, how the workers find this row. |
| `irp_analysis_irp_id` | `irp_analysis.irp_id` (Risk Modeler API analysis ID). | Traceability to Risk Modeler. |
| `irp_app_analysis_id` | `irp_analysis.irp_app_analysis_id`, converted to `INT` by the route on submit; the form rejects an analysis whose ID is not an integer. Checked against `metadata.csv` `AnlsId` at stage time (§4.3). | `Data.AnalysisID`, which is `INT`; the procedure copies without a cast. Duplicate block (§4.1), unique with `perspective_code`. |
| `analysis_name` | `irp_analysis.name` at export time. | `Data.Name`. |
| `analysis_description` | `irp_analysis.description` at export time. | `Data.Description`. |
| `perspective_code` | Export form. | `Data.Perspective`, `RMS_HistoricalRDS.Perspective`; duplicate block (§4.1), unique with `irp_app_analysis_id`. |
| `client_id` | Export form, from `dbo.Client`. | `Data.ClientID`, `RMS_HistoricalRDS.ClientID`. |
| `treaty_incept` | Export form, default `submission.inception_date`. `DATE`. | `Data.TreatyIncept` (`date`); `RMS_HistoricalRDS.TreatyIncept` (`datetime`, midnight). |
| `treaty_year` | `submission.treaty_year`. `INT`. | `RMS_HistoricalRDS.TreatyYear` (`varchar(4)`, via `CONVERT`). |
| `crm_id` | Export form, default `submission_crm_id`. | `Data.CRMID`. |
| `data_name` | Export form, the field for this analysis; nullable. | `Data.DataName`. |
| `data_vintage` | Export form; nullable. `DATE`. | `Data.DataVintage` (`date`); `RMS_HistoricalRDS.DataInforce` (`varchar(15)`, as `yyyy-mm-dd`, §4.4). |
| `data_currency` | `irp_analysis.settings_metadata` `currency.currencyCode` (the Risk Modeler analysis payload, present on own and broker rows; `submitted_settings` is own-only), checked against `metadata.csv` `AnalysisCurrency` at stage time. | `Data.DataCurrency`. |
| `data_model_vendor` | Constant `RMS`. | `Data.DataModelVendor`. |
| `server` | Risk Modeler base URL from app config. | `Data.Server`. |
| `irp_export_job_id` | Risk Modeler export job ID, written by the submit worker (§4.2). Null until then. | Traceability; re-download while the URL is valid. |
| `loss_table_type` | Archive folder name, written by the stage worker (§4.3): `ELT` (DLM example) or `PLT` (HD example). Null until staged. | Selects the stage data table and the load procedure. |
| `engine_type` | `metadata.csv` `Engine Type`, written by the stage worker: `DLM` or `HD`. Null until staged. | Export detail page; whether the historical classification step runs is O-08. |
| `data_model_version` | `metadata.csv` `ModelVersion`, written by the stage worker: `25.0` (DLM example) or `HDv2.1` (HD example). Null until staged. `NVARCHAR(10)`, the type of both `Data.DataModelVersion` and the lookup's `ModelVersion`, so the join compares like with like. | `Data.DataModelVersion`; lookup join and lookup-loaded assertion (§4.4 steps 1–2). |
| `peril_code` | `irp_analysis.settings_metadata` `perilCode` (`EQ` in the captured payload; `YY` for a multi-peril group), written by the route on submit. The form rejects an analysis whose payload has no `perilCode`. `NVARCHAR(10)`, the lookup's `Peril` type. | Lookup join on `Peril` (§4.4 step 2) for single-peril analyses, once O-11 confirms the lookup uses the same codes. |
| `region_code` | `irp_analysis.settings_metadata` `regionCode` (`NA` in the captured payload), written by the route on submit. | Export detail page; traceability. The lookup has no region column, so nothing joins on it. |
| `zip_file` | Path of the downloaded archive relative to `EXPORT_ARCHIVE_DIR` (§4.8): `{export_id}/{irp_analysis.id}/{filename}`. Written by the stage worker as soon as the download completes. Null until then. | Export detail page, so an analyst can open the archive on the share; the stage worker, which skips the download when the file is already there (§4.3 step 2); Retry (§4.7). Whether it also goes into `Data.ArchiveFile` is O-10. |
| `stage_status` | Route on submit and Retry (§4.7): `pending`; submit worker: `failed` (§4.2); stage worker: `staged`, `failed`. | Export detail page; the submit worker's row selection (§4.2); the stage worker's resume check (§4.3); the load worker's entry check (§4.4). |
| `staged_at` | Stage worker. | Export detail page. |
| `load_status` | Route on submit: `pending`; load procedure: `loading` when it claims the row as the first statement of its transaction, `loaded` at commit, `failed` from the `CATCH` block; load worker: `failed` when the call raises before the procedure could write (§4.4). A rollback returns the row from `loading` to the value it had before the claim. | Export detail page; the load procedure's claim (§4.4); the load worker's entry check (§4.4); the stage worker's resume check (§4.3). |
| `loaded_at` | Load procedure. | Export detail page. |
| `error_message` | Worker or load procedure; nullable. | Export detail page. |
| `data_id` | `Data.DataID` returned by the header insert (§4.4 step 4). | `RMSELT.DataID`, `RMS_HistoricalRDS.DataID`; export detail page. |
| `staged_row_count` | Sum of `rwb_loss_result_file.row_count`. | Summary (§4.5). |
| `stochastic_row_count` | Load procedure. | Summary. |
| `historical_row_count` | Load procedure. | Summary. |
| `exp_value_raised_count` | Load procedure. | Summary (note 24 D13). |
| `std_dev_zeroed_count` | Load procedure. | Summary (note 24 D15). |
| `inserted_at` | Route on submit. | Audit. |
| `updated_at` | Worker or load procedure. | Audit. |

Unique on (`irp_app_analysis_id`, `perspective_code`): the duplicate block
in §4.1, and the reason one analysis can never have two `Data` rows for one
perspective. Also unique on (`export_id`, `irp_analysis_id`), so the workers'
lookup by export and analysis finds one row.

`stage_status` (`pending`, `failed`, `staged`), `load_status` (`pending`,
`loading`, `loaded`, `failed`), `loss_table_type` (`ELT`, `PLT`), and
`engine_type` (`DLM`, `HD`) each carry a `CHECK` constraint listing those
values, as does `event_type` on the stage data tables (§4.3). The
constitution asks for a kind table for an internal categorical; these
columns get a `CHECK` instead because the tables live in the client's
repository, the values are written only by the three workers and the load
procedure, and a kind table there would be five more tables the CIC DBA
seeds on every install and the procedure joins on every load (T-19). The
plan's constitution check lists the deviation.

### 4.2 Export job submission

The `submit_results_export` worker reads the manifest rows for its
`export_id` and calls `submit_analysis_export_job` once per analysis.
The request Risk Modeler receives:

```json
{
  "exportType": "RESULTS",
  "resourceType": "analyses",
  "resourceUris": ["/platform/riskdata/v1/analyses/<analysisId>"],
  "settings": {
    "fileExtension": "PARQUET",
    "lossDetails": [{
      "metricType": "LOSS_TABLES",
      "outputLevels": ["Portfolio"],
      "perspectiveCodes": ["<one code>"]
    }]
  }
}
```

The worker only submits manifest rows with `stage_status = pending` and a
null `irp_export_job_id`, so a re-run never submits an analysis twice and
never resubmits a rejected analysis the analyst has not retried. For each analysis it inserts one
`irp_job` of type `export` and writes the Risk Modeler job ID to
`manifest.irp_export_job_id`. When Risk Modeler rejects one analysis, the
worker marks that manifest row `stage_status = failed` with the error and
continues with the next; the job itself succeeds. When the call fails for a
reason that is not about the analysis (Risk Modeler unreachable, token
expired), the job fails and a re-run picks up the rows still without a job
ID (§4.7).

#### `irp_job` — Workbench, one new column

| Column | Source | Read by |
|---|---|---|
| `export_id` | `UNIQUEIDENTIFIER`, nullable, no foreign key (the ID has no Workbench table, like `irp_analysis.execution_id`). Set by the `submit_results_export` worker on each `export` job it creates. | Poller terminal handler for `export` jobs, to pass into the `stage_results_export` job; export detail page, to list the Risk Modeler jobs of one export. |

Existing `irp_job` columns the export job uses:

| Column | Value for an export job |
|---|---|
| `irp_job_type` | `export` (seed row already exists in `irp_job_type_kind`). |
| `irp_id` | Risk Modeler export job ID from `submit_analysis_export_job`. Also written to `manifest.irp_export_job_id`. |
| `irp_analysis_id` | The analysis being exported. With `export_id` it identifies the manifest row (`UNIQUE (export_id, irp_analysis_id)`). |
| `requested_from_submission_id` | The submission whose analyses page the form was opened from, passed through the submit job's `input_data`. Not read from `irp_analysis.submission_id`, which only group analyses carry. |
| `request_params` | The request body returned by `submit_analysis_export_job`. |
| `status` | Risk Modeler job status mirror, as for other job types. |

#### Poller

`app/poller/run.py` gains two entries:

| Map | Key | Value |
|---|---|---|
| `_GETTERS` | `export` | `irp_gateway.get_export_job`, a single-status check (Article 11). `irp_gateway` gains that method, wrapping `export_job.get_export_job`. |
| `_TERMINAL_HANDLERS` | `export` | Enqueue `stage_results_export` (§4.6) on any terminal status, in the same connection as the `irp_job` status write. The handler does not touch the loss repository; the stage worker reads the `irp_job` status and marks the manifest failed itself, so the poller needs only the `WORKBENCH` connection. |

`irp_gateway` also gains `submit_analysis_export_job` and
`download_export_results` wrappers for the submit and stage workers.

The completed job response carries one download URL per analysis with an
expiration seven days after completion. Download must happen inside that
window; after it, the analysis needs a new export job.

### 4.3 Download, unzip, stage

The `stage_results_export` worker runs one analysis from download through
`stage_status = staged`, then hands off to the `load_results_export` worker
(§4.4). On entry it reads the manifest row and picks up where the last attempt
stopped:

| Manifest state on entry | Worker does |
|---|---|
| `load_status = loaded` | Nothing; the job succeeds. |
| `stage_status = staged` | Skips staging and runs step 8 only, so a load job exists for the analysis. |
| Anything else | Deletes the analysis's result file rows, stage data rows, and local working directory, then runs the steps below from step 1. Staging is all-or-nothing per analysis; a partial stage from a failed or killed attempt is discarded, not resumed. The archive on the share is never deleted. |

The steps:

1. Reads the `export` `irp_job`. If its status is not `FINISHED`, marks the
   manifest row `stage_status = failed` with the job's error message and
   stops; no other analysis is affected.
2. Checks that `EXPORT_ARCHIVE_DIR` exists and is a directory, and fails the
   analysis if not. The worker never creates the root: if the share is not
   mounted, creating it would write the archive to the VM's local disk under
   the mount point, where nobody would look for it. If `zip_file` is already
   set and the file exists under the root, the worker skips the download and
   goes to step 3. Otherwise it downloads the archive with
   `download_export_results` into
   `{EXPORT_ARCHIVE_DIR}/{export_id}/{irp_analysis.id}/` and writes
   `zip_file`. The archive is kept there permanently; it is the record of
   what Risk Modeler produced and what the repository was loaded from.
3. Unzips it into a local working directory,
   `{submission_outputs_base}/exports/{export_id}/{irp_analysis.id}/`, so the
   Parquet reads in step 6 hit local disk rather than the share. The archive
   layout is
   `{JobId}_{AnalysisName}_Losses/{LossTableType}/metadata.csv` plus
   `{LossTableType}/{OutputLevel}/{PerspCode}/{JobId}_{AnalysisName}_{LossTableType}_{OutputLevel}_{PerspCode}_{n}.parquet`.
   `{LossTableType}` is `ELT` in the DLM example and `PLT` in the HD example;
   `{OutputLevel}` is `Portfolio` in both; `{PerspCode}` is GU, GR, or RL in
   the DLM example and GU, GR, RL, or RP in the HD example. One perspective
   folder can hold several Parquet files; `{n}` is the chunk index starting
   at 0. The HD example has five files per perspective, each covering 10,000
   simulation periods.
4. Reads `metadata.csv`. Both examples supply `AnlsId` (the user-facing
   analysis ID), `ModelVersion`, `AnalysisCurrency`, `EDMName`, `RunDate`,
   `PerspCodes`, `Region`, `Peril`, and `Engine Type`. The worker reads
   `AnlsId`, `ModelVersion`, `AnalysisCurrency`, and `Engine Type`. `Peril`
   and `Region` are display names and model region codes (`Earthquake`,
   `NAEQ`); the manifest already holds `perilCode` and `regionCode` from the
   analysis payload, so the worker does not read them. `ModelVersion` is
   read from the archive because the analysis payload's `engineVersion` is
   `RL25`, the form `Data.DataModelVersion` must not take. The DLM example has `ModelVersion` `25.0`
   and `Engine Type` `DLM`; the HD example has `HDv2.1` and `HD`, plus five
   columns the DLM file lacks: `SubPerils`, `SimulationSet`,
   `NumberOfSimulations`, `NumberOfSamples`, and `Shuffled`. The worker
   checks `AnlsId` against `manifest.irp_app_analysis_id` and
   `AnalysisCurrency` against `manifest.data_currency`, and fails the
   analysis on a mismatch.
5. Updates the manifest row: `loss_table_type` from the folder name,
   `engine_type` and `data_model_version` from `metadata.csv`. Inserts one
   result file row per Parquet file.
6. Streams each Parquet file into the stage data table for the loss table
   type (`stage.rwb_loss_result_elt_data` for `ELT`,
   `stage.rwb_loss_result_plt_data` for `PLT`) with `db.elt.upload_parquet`,
   recording the row count on the result file row. The Parquet column names
   differ between loss table types (`PortInfoId` in the ELT, `PortfolioId`
   in the PLT), so the column mapping is chosen by `loss_table_type`, not
   shared.
7. Marks the manifest `stage_status = staged` and deletes the local working
   directory. The archive on the share stays.
8. Enqueues the `load_results_export` job for the analysis (§4.6) with
   `ensure_pending_rwb_job` and dispatches it, the same way the portfolio
   worker enqueues `backfill_edm_detail`. `ensure_pending_rwb_job` revives a
   `failed` or `succeeded` load job for the same analysis rather than
   inserting a second one, so a stage job that re-runs after a load failure
   (entering at `staged`, step 8 only) or after Retry re-staged the analysis
   from scratch gives it one new load attempt.

Step 7 writes the loss repository and step 8 writes the Workbench, so the
two are not one transaction. If step 8 fails, the manifest says `staged`
with no load job, the stage job fails, and a re-run enters at
`stage_status = staged` and runs step 8 alone (§4.7).

A failure at any of steps 1–7 marks `stage_status = failed` with the error
and the job fails. `upload_parquet` commits once per file and rolls back on error, so
a file is either fully staged or absent; files staged before the failure stay
until the next attempt's cleanup deletes them. Recovery is in §4.7.

#### `stage.rwb_loss_result_file` — loss repository, one row per Parquet file

| Column | Source | Read by |
|---|---|---|
| `result_file_id` | `INT IDENTITY`. | Primary key; `rwb_loss_result_elt_data.result_file_id`, `rwb_loss_result_plt_data.result_file_id`. |
| `manifest_id` | Foreign key to `rwb_loss_result_manifest.manifest_id`. | Groups a manifest row's files. |
| `result_file` | Path of the Parquet file inside the archive. | Traceability: locates the file inside the archive on the share (`manifest.zip_file`). No target column reads it. |
| `output_level` | Parsed from the path (`Portfolio`). | Traceability; reserved for other output levels. |
| `perspective_code` | Parsed from the path. | Checked against the manifest's `perspective_code`. |
| `chunk_index` | Parsed from the `_{n}` file suffix. | Ordering; traceability. |
| `row_count` | `upload_parquet` result. | `manifest.staged_row_count`. |
| `staged_at` | Worker. | Export detail page. |

Unique on (`manifest_id`, `result_file`).

#### `stage.rwb_loss_result_elt_data` — loss repository, one row per ELT event

| Column | Source | Read by |
|---|---|---|
| `manifest_id` | Foreign key to `rwb_loss_result_manifest.manifest_id`. | Every load procedure statement filters on it. |
| `result_file_id` | Foreign key to `rwb_loss_result_file.result_file_id`. | Traceability to the file. |
| `port_info_id` | Parquet `PortInfoId`. | Nothing downstream. |
| `port_info_name` | Parquet `PortInfoName`. | Nothing downstream. |
| `port_info_num` | Parquet `PortInfoNum`. | Nothing downstream. |
| `event_id` | Parquet `EventId`. | Lookup join; `RMSELT.EventID`, `RMS_HistoricalRDS.EventID`. |
| `rate` | Parquet `Rate`. | Nothing downstream. |
| `loss` | Parquet `Loss`. Never altered. | `RMSELT.Loss`, `RMS_HistoricalRDS.Loss`; exposure correction. |
| `std_dev_i` | Parquet `StdDevI`; zeroed if negative (§4.4 step 3). | `RMSELT.StdDevI`. |
| `std_dev_c` | Parquet `StdDevC`; zeroed if negative. | `RMSELT.StdDevC`. |
| `exp_value` | Parquet `ExpValue`; raised to `loss` if lower. | `RMSELT.ExpValue`. |
| `event_type` | Load procedure; null until classified, then `stochastic` or `historical`. | Selects the target table. |
| `exp_value_raised` | Load procedure; `BIT`, default 0. | `manifest.exp_value_raised_count`. |
| `std_dev_zeroed` | Load procedure; `BIT`, default 0. | `manifest.std_dev_zeroed_count`. |
| `inserted_at` | Worker. | Audit. |

Clustered index on (`manifest_id`, `event_id`). Rows are kept after load
until O-03 settles retention. The four Parquet columns nothing downstream
reads are staged so the stage row is a complete copy of the source row.

#### `stage.rwb_loss_result_plt_data` — loss repository, one row per PLT period event (designed, not built)

Columns are taken from the HD example's Parquet schema. No loss repository
table reads them yet (O-08).

| Column | Source | Read by |
|---|---|---|
| `manifest_id` | Foreign key to `rwb_loss_result_manifest.manifest_id`. | Every load procedure statement filters on it. |
| `result_file_id` | Foreign key to `rwb_loss_result_file.result_file_id`. | Traceability to the file. |
| `portfolio_id` | Parquet `PortfolioId` (string). | Nothing downstream. |
| `portfolio_name` | Parquet `PortfolioName`. | Nothing downstream. |
| `portfolio_num` | Parquet `PortfolioNum`. | Nothing downstream. |
| `period_id` | Parquet `PeriodId` (int64, 1 to `NumberOfSimulations`). | PLT destination, once one exists. |
| `event_id` | Parquet `EventId` (int64). | PLT destination; historical lookup join if O-08 keeps classification for HD. |
| `event_date` | Parquet `EventDate` (timestamp). | PLT destination. |
| `loss_date` | Parquet `LossDate` (timestamp). | PLT destination. |
| `loss` | Parquet `Loss` (float). Never altered. | PLT destination. |
| `region` | Parquet `Region` (`JP` in the example). | Nothing downstream. |
| `peril` | Parquet `Peril` (`TY` in the example). | Nothing downstream. |
| `weight` | Parquet `Weight` (float, `1 / NumberOfSimulations`). | PLT destination. |
| `event_type` | Load procedure; null until classified. | Selects the target table, if O-08 keeps classification for HD. |
| `inserted_at` | Worker. | Audit. |

The PLT has no `ExpValue`, `StdDevI`, `StdDevC`, or `Rate` column, so neither
correction in §4.4 step 3 has anything to act on. The PLT load procedure
skips both and records zero for both counts.

### 4.4 Enrich, correct, load

The `load_results_export` worker runs one analysis. On entry it reads the
manifest row: when `load_status = loaded` it does nothing and succeeds, so a
re-run after a completed load never writes a second `Data` row; when
`stage_status` is not `staged` it fails with that message and writes nothing,
because the stage job is the one to re-run. Otherwise it calls the stored
procedure `stage.usp_load_elt_result @manifest_id` through a new
`db.execute_procedure(name, params, connection)` on the `LOSS` connection.
The procedure body is one transaction: it claims the manifest row, then runs
the five steps below.

`execute_procedure` opens the connection with
`execution_options(isolation_level="AUTOCOMMIT")` and runs `EXEC` with bound
parameters. Autocommit is required, not a convenience. `db.execute` and
`db.execute_command` both hold a driver-side transaction for the life of the
connection: `execute` rolls it back at close, and `execute_command` commits
on success but rolls back on an exception. Under either, the procedure's
`BEGIN TRANSACTION` nests inside the driver's (`@@TRANCOUNT` goes 1 to 2)
and its `COMMIT` only decrements the count, so with `execute` the whole load
would be rolled back after the procedure reported success, and with
`execute_command` the `CATCH` block's `failed` write would be rolled back
with the rethrown error. With autocommit the procedure owns the only
transaction. `db.execute_procedure` exists so the rule lives in `db/` with
the other execution paths; the PLT procedure uses the same function.
`MSSQL_TIMEOUT` is the connect timeout only, and pyodbc sets no statement
timeout by default, so a load of several million rows is not cut off.

The worker fails the job when the call raises. Before it does, it stamps the
manifest row through `db.execute_command`:

```sql
UPDATE stage.rwb_loss_result_manifest
SET load_status = 'failed', error_message = :error
WHERE manifest_id = :manifest_id AND load_status <> 'loaded';
```

When the procedure ran and its `CATCH` block already wrote `failed`, the
worker's write repeats the same error text. When the procedure never ran
(not installed, no EXECUTE grant, connection refused, login failed), the
worker's write is the only one, so the export detail page shows the failure
and Retry behaves the same as for any other load failure. When the stamp
itself fails (the server is unreachable), the worker logs that and fails the
job with the original error; the `rwb_job` row still holds it.

The procedure refuses to run inside a caller's transaction. Its first
statement is:

```sql
IF @@TRANCOUNT > 0
    THROW 50000, 'usp_load_elt_result must be called outside a transaction', 1;
```

A person who wraps the call in `BEGIN TRAN` in SQL Server Management Studio,
or a future caller that forgets autocommit, gets that error and nothing else
happens. Without the check the load would nest silently, `loaded` would
depend on the caller committing, and a failed load's `CATCH` write would be
lost on the caller's rollback. The design promises that a failed load
records its error on the manifest row; the check is what makes that promise
hold for every caller.

The claim is the first statement inside the transaction:

```sql
UPDATE stage.rwb_loss_result_manifest
SET load_status = 'loading', error_message = NULL
WHERE manifest_id = @manifest_id
  AND stage_status = 'staged'
  AND load_status IN ('pending', 'failed');

IF @@ROWCOUNT = 0
    THROW 50001, @reason, 1;  -- built from the row: missing, not staged,
                              -- loading, or loaded with data_id N
```

The claim is what makes the procedure safe to call from anywhere. The
`UPDATE` locks the manifest row for the rest of the transaction, so a second
caller on the same `@manifest_id`, whether a second worker or a person in
SQL Server Management Studio, waits on the lock, then finds `load_status =
loaded` and raises. It also raises when `@manifest_id` names no row, so a
mistyped ID cannot run the five steps against nothing and report success;
without the claim, every step is an `INSERT ... SELECT` or `UPDATE` filtered
on `@manifest_id`, so a wrong ID would touch zero rows and exit cleanly. The
error message for an already loaded row names the existing `data_id`. A
rollback undoes the claim along with everything after it, so no row is ever
left in `loading` by a killed worker or a dropped connection.

The procedure is part of the `stage` schema DDL in
`db/bootstrap/loss_schema.sql`, so it reaches CIC the same way the stage
tables do: the CIC DBA installs it (T-05). Because it lives on the server and
takes only `@manifest_id`, the client team can run a load themselves from
SQL Server Management Studio against any staged manifest row, without the
Workbench. A load run that way writes the same manifest columns, so a later
`load_results_export` re-run sees `load_status = loaded` and does nothing.
The worker's own `loaded` check stays even though the procedure would raise
on that row: a reconciler re-run after a committed load must succeed, not
fail, and only the worker knows the difference between a re-run and a
mistaken call.
The PLT procedure, `stage.usp_load_plt_result`, is designed with the HD path
(O-08); the worker chooses the procedure by `manifest.loss_table_type`.

1. **Assert the lookup is loaded for this version.** Fail the analysis when
   `Lookup_RMS_HistoricalRDS` has no row with `ModelVersion` equal to the
   manifest's `data_model_version` (note 23 D19). Without this check, an
   analysis run against a model version the DBA has not yet loaded into the
   lookup would classify every event as stochastic.
2. **Classify.** Update each stage row's `event_type`: `historical` when a
   lookup row matches on `EventID = event_id`, `ModelVersion =
   manifest.data_model_version`, and `Peril = manifest.peril_code`,
   otherwise `stochastic`. The peril term is held back until O-11 confirms
   the lookup's codes, and is skipped when `peril_code` is `YY`: Risk
   Modeler gives a multi-peril group that code, its ELT rows carry no
   peril, so a group joins on event ID and model version only (T-16). The lookup holds
   historical events only, so absence means stochastic (note 23 D23,
   confirmed by Cheng). Event IDs repeat across model versions and may
   repeat across perils and regions within one version, so `EventID` alone
   is not a key. The lookup has no region column, and a group's join has
   no peril term, so before the classification `UPDATE` the procedure
   asserts that every stage `event_id` matches at most one lookup row:

   ```sql
   IF EXISTS (
       SELECT d.event_id
       FROM stage.rwb_loss_result_elt_data d
       JOIN stage.Lookup_RMS_HistoricalRDS l
         ON l.EventID = d.event_id
        AND l.ModelVersion = @data_model_version
        AND (@peril_code = 'YY' OR l.Peril = @peril_code)
       WHERE d.manifest_id = @manifest_id
       GROUP BY d.event_id
       HAVING COUNT(*) > 1)
       THROW 50002, @reason, 1;  -- names the first offending event_id
   ```

   The order matters. `UPDATE ... FROM` with a join that matches several
   rows does not raise; SQL Server picks one match and moves on, so a check
   that ran after the update would find nothing wrong while step 5, which
   joins the lookup again for the historical columns, would insert two
   `RMS_HistoricalRDS` rows for one loss. The assertion runs first and
   fails the analysis with the event ID named. For a group, the assertion
   is the only protection: a group event that the lookup lists under two
   perils in one model version cannot be classified from the ELT. The
   lookup lives in the repository database with the four other CIC
   tables; the procedure reads `dbo.Lookup_RMS_HistoricalRDS` by two-part
   name (plan T-21, T-29).
   The lookup is a heap with no index and every column nullable
   (`cic-reference/Lookup_RMSHistoricalRDS_v25DDL.sql`), so each load
   hashes the whole table once for the assertion, once for the
   classification, and once for the step 5 join. That is acceptable at the
   lookup's size; if a load ever shows the join as the slow step, an index
   on (`ModelVersion`, `EventID`) is a request to the DBA through O-05, not
   a change to the procedure.
3. **Correct.** Two set-based updates, each recording its row count on the
   manifest:
   - Where `loss > exp_value`, set `exp_value = loss` and `exp_value_raised = 1`.
     Model losses are never altered (note 24 D12).
   - Where `std_dev_i < 0` or `std_dev_c < 0` on a stochastic row, set the
     negative value to 0 and `std_dev_zeroed = 1` (note 24 D15). Historical
     rows are not corrected.
4. **Insert the header.** Insert one `dbo.Data` row from the manifest,
   capture `DataID` with `OUTPUT ... INTO`, and write it to `manifest.data_id`
   (SQL below).
5. **Copy.** Insert stochastic rows into `dbo.RMSELT` and historical rows
   into `dbo.RMS_HistoricalRDS`, joining the lookup for the historical
   columns. Record `stochastic_row_count` and `historical_row_count` on the
   manifest, set `load_status = loaded`, and commit.

The stage rows remain after the commit. How long they and the manifest rows
are kept is O-03.

`load_status = loaded` and `loaded_at` are set inside the transaction, so
they commit with the target rows or not at all.

The procedure wraps the claim and the five steps in `TRY ... CATCH`:

```sql
BEGIN TRY
    BEGIN TRANSACTION;
    -- claim, steps 1-5, COMMIT
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    UPDATE stage.rwb_loss_result_manifest
    SET load_status = 'failed', error_message = ERROR_MESSAGE()
    WHERE manifest_id = @manifest_id AND load_status <> 'loaded';
    THROW;
END CATCH;
```

The rollback undoes the claim, the classification and corrections on the
stage rows, and any target rows. `XACT_STATE()` is checked rather than
`@@TRANCOUNT` because a transaction doomed by a severe error (a deadlock
victim, a conversion failure under `XACT_ABORT`) is still open but cannot be
committed, and an `UPDATE` inside it would raise a second error and hide the
first. The `failed` write runs after the rollback, in its own autocommitted
statement, so it commits even though the load did not; the `load_status <>
'loaded'` guard means a failure raised after the claim can never overwrite a
row that another caller has since loaded. The rethrow fails the worker's job
and returns the error to a person running the procedure by hand. Recovery is
in §4.7.

#### `dbo.Data` — CIC target, one row per analysis per perspective

DDL in `cic-reference/DataTableDDL.sql`.

| Column | Value |
|---|---|
| `DataID` | `IDENTITY`; captured at insert and written to `manifest.data_id`. |
| `ClientID` | `manifest.client_id`. |
| `TreatyIncept` | `manifest.treaty_incept`. |
| `DataVintage` | `manifest.data_vintage`; usually blank, entered later by the analyst. |
| `DataName` | `manifest.data_name`; optional. |
| `DataModelVendor` | `RMS`. |
| `DataModelVersion` | `manifest.data_model_version`: `25.0` for the DLM example. Decimal form, never `RL 25`. The HD example carries `HDv2.1`, which fits `nvarchar(10)` but is not decimal form; whether CIC accepts it is in O-08. |
| `DataCurrency` | `manifest.data_currency`. |
| `Server` | `manifest.server`. |
| `Database` | Not populated. |
| `AnalysisID` | `manifest.irp_app_analysis_id`; both `INT`, converted on submit (§4.1). |
| `Name` | `manifest.analysis_name`. |
| `Description` | `manifest.analysis_description`. |
| `Perspective` | `manifest.perspective_code`. |
| `ArchiveFile` | Not populated until O-10 decides whether it holds the archive path on the share. |
| `AReLossSet` | Not populated. |
| `LOB` | Not populated. |
| `Geography` | Not populated. |
| `CRMID` | `manifest.crm_id`. |

Nothing reserves a `DataID` ahead of the load. `Data.DataID` is
`IDENTITY(1,1)`; SQL Server generates the value when the header row is
inserted in step 4, inside the load transaction, and the `OUTPUT` clause
returns it in the same statement. Design notes 21 and 23 and the earlier
`Loss_Result_Loading.docx` speak of reserving a data ID from a `SEQUENCE`;
the repository has no `SEQUENCE`, and none is needed, because the manifest
row, not the ID, is what the procedure claims (above). The value is
unknown until step 4 runs and is never handed out to anything outside the
transaction before commit.

```sql
DECLARE @inserted TABLE (data_id INT);

INSERT INTO dbo.Data (ClientID, TreatyIncept, DataVintage, DataName,
    DataModelVendor, DataModelVersion, DataCurrency, [Server], AnalysisID,
    Name, Description, Perspective, CRMID)
OUTPUT INSERTED.DataID INTO @inserted (data_id)
SELECT m.client_id, m.treaty_incept, m.data_vintage, m.data_name,
       m.data_model_vendor, m.data_model_version, m.data_currency, m.server,
       m.irp_app_analysis_id, m.analysis_name, m.analysis_description,
       m.perspective_code, m.crm_id
FROM stage.rwb_loss_result_manifest m
WHERE m.manifest_id = @manifest_id;

UPDATE stage.rwb_loss_result_manifest
SET data_id = (SELECT data_id FROM @inserted)
WHERE manifest_id = @manifest_id;
```

The procedure runs per analysis, so one row is inserted and no natural-key join
back is needed. The `RMSELT` and `RMS_HistoricalRDS` inserts that follow read
`data_id` from the manifest row in the same transaction.

The database enforces nothing about `DataID`. `dbo.Data` has no primary key
and no unique constraint on `DataID` (`cic-reference/DataTableDDL.sql`);
`RMSELT.DataID` and `RMS_HistoricalRDS.DataID` are nullable `INT` columns
with no foreign key and no index. A second header row for the same analysis,
or loss rows pointing at a `DataID` with no header, would insert without
error. The manifest claim and the single transaction are therefore the only
guarantee that one load produces exactly one `Data` row and that every loss
row it writes carries that row's `DataID`. The design does not ask CIC to
add a key to `Data`: the table is the client's and the workflow tool writes
it too.

Identity values are consumed by a rolled-back insert. A load that fails after
step 4 leaves a gap in `DataID`, which is normal for an `IDENTITY` column and
means nothing was loaded under that number. The client team should expect
gaps and not read one as a lost load.

#### `dbo.RMSELT` — CIC target, stochastic rows

| Column | Value |
|---|---|
| `DataID` | `manifest.data_id`. |
| `EventID` | `elt_data.event_id`. |
| `Loss` | `elt_data.loss`. |
| `StdDevI` | `elt_data.std_dev_i` after correction. |
| `StdDevC` | `elt_data.std_dev_c` after correction. |
| `ExpValue` | `elt_data.exp_value` after correction. |

#### `dbo.RMS_HistoricalRDS` — CIC target, historical rows

| Column | Value |
|---|---|
| `DataID` | `manifest.data_id`. |
| `ClientID` | `manifest.client_id`. |
| `Peril` | Lookup `Peril`. |
| `ModelVersion` | Lookup `ModelVersion`. |
| `TreatyYear` | `CONVERT(varchar(4), manifest.treaty_year)`; the target is `varchar(4)`, the manifest `INT`. |
| `TreatyIncept` | `manifest.treaty_incept`; `date` widens to the target's `datetime` at midnight. |
| `DataInforce` | `CONVERT(varchar(15), manifest.data_vintage, 23)`, the ISO form `yyyy-mm-dd` (same value as `DataVintage`, note 23 D20); null when the vintage is blank. The target is `varchar(15)`, so the value is text; O-04 confirms the workflow tool writes the same form. |
| `EventID` | `elt_data.event_id`. |
| `Type` | Lookup `Type`. |
| `Event_Name` | Lookup `Name`. |
| `Loss` | `elt_data.loss`. |
| `PCS` | Lookup `[PCS#]`. |
| `Perspective` | `manifest.perspective_code`. |
| `AReLossSet` | Not populated. |

Column widths differ between the lookup and the target: `[PCS#]` is
`nvarchar(225)` and `PCS` is `varchar(5)`; lookup `Peril` is `nvarchar(10)`
and target `Peril` is `varchar(5)`. Lookup `ModelVersion` is `nvarchar(10)`
and the target's is `varchar(10)`, the same width. `Perspective` is
`varchar(50)` on `Data` and `varchar(5)` here; perspective codes are two
characters. The procedure copies `PCS` and `Peril` without truncation, so a
lookup value wider than the target raises a string truncation error and the
load fails rather than storing a cut value. O-04 checks the live values
before the load is trusted.

### 4.5 Post-load summary

The export detail page in the Workbench lists each analysis in the export with
its status and, once loaded, `data_id`, rows staged, stochastic rows,
historical rows, rows with exposure raised to loss, and rows with a standard
deviation zeroed. A failed analysis shows its error message and a **Retry**
action (§4.7). The counts come from the manifest row. Cheryl's condition
for accepting automatic correction was "I would want to know that it's
happening"; Wendy's was that the summary carry row counts (note 24 D13).

### 4.6 The job chain

Three `rwb_job` types carry the process from §4.1 to §4.4: one per export,
then two per analysis. All are reachable from `export_id`: the submit job by
`requestor_id`, the stage job by the `irp_job` it follows, the load job by
the stage job it follows.

| | `submit_results_export` (§4.2) | `stage_results_export` (§4.3) | `load_results_export` (§4.4) |
|---|---|---|---|
| Enqueued by | The export route. | Poller, when the `export` `irp_job` reaches a terminal status. | The stage worker, after `stage_status = staged` (§4.3 step 8). |
| `requestor_type` / `requestor_id` | `analyst_request` / `export_id`. | `irp_job` / the `export` `irp_job.id`. | `rwb_job` / the stage `rwb_job.id`. |
| `link_type` / `link_id` | `not_applicable` / null (several analyses). | `edm` / `irp_analysis.edm_id`, or `rdm` / `irp_analysis.rdm_id` for a broker analysis. | Same as the stage job. |
| `context_type` / `context_id` | `result_export` / `export_id`. | `irp_analysis` / `irp_analysis.id`. | `irp_analysis` / `irp_analysis.id`. |
| `input_data` | `{"export_id", "submission_id"}`. The analysis list is read from the manifest rows. | `{"export_id", "irp_analysis_id", "irp_job_id"}`. | `{"export_id", "irp_analysis_id", "manifest_id"}`. |
| Runs on | `WORKBENCH`, Risk Modeler, `LOSS` (manifest update). | Risk Modeler download, `LOSS`, then `WORKBENCH` (load job enqueue). | `LOSS` only. |

The queue's `UNIQUE (requestor_type, requestor_id, rwb_job_type)` makes each
enqueue idempotent: a re-fired poller tick cannot create a second stage job
for the same analysis, and a re-run stage job cannot create a second load
job. A retry re-runs the existing job, which resumes from the manifest
statuses.

### 4.7 Failure and recovery

The manifest row is the checkpoint. `stage_status`, `load_status`, and
`irp_export_job_id` say how far an analysis got, and every stage row carries
`manifest_id`, so cleanup is one `DELETE` per table. The `rwb_job` row records
the attempt: the runtime marks it `failed` with the exception text, and a
re-run resets the same row to `pending` with `attempt_count + 1` (the
existing `ensure_pending_rwb_job` path used by the monitoring page's
resubmit). No automatic retries are configured on the actors; a failed job
waits for a person or the reconciler.

Three ways an attempt is re-run:

| Trigger | When | What it does |
|---|---|---|
| Reconciler (automatic) | A worker process dies mid-job. The heartbeat goes stale and the poller resets the `running` row to `pending`. | The same job re-runs and resumes from the manifest state (§4.3 entry table). |
| Monitoring page resubmit (existing) | An operator re-runs a failed `rwb_job` by ID. | Same as above. |
| Export detail page, **Retry** per analysis (new) | An analyst retries one failed analysis. | If `stage_status = staged`, re-arms the `load_results_export` job. Else if the archive is already on the share (`zip_file` set and the file present), or the analysis has a `FINISHED` export job whose download URL is still valid, re-arms the `stage_results_export` job. Otherwise clears `irp_export_job_id`, resets `stage_status = pending` and `error_message`, and re-arms the `submit_results_export` job, which submits only the rows without a job ID. The URL is valid for seven days after `irp_job.completed_at`. |

What each failure leaves behind and how it is recovered:

| Failure | State left | Recovery |
|---|---|---|
| Risk Modeler rejects the export request for one analysis | Manifest `stage_status = failed`, error message, no `irp_export_job_id`. Other analyses unaffected. Submit job succeeded. | Retry on the detail page re-arms the submit job; it resubmits only this analysis. |
| Risk Modeler unreachable during submission | Rows already submitted have a job ID; the rest are `pending` without one. Submit job failed. | Re-run the submit job (monitoring page or detail page). It skips rows with a job ID. |
| Risk Modeler export job ends `FAILED` | Poller enqueues the stage job; the stage job marks the manifest `failed` with the Risk Modeler error. No load job is created. | Retry on the detail page: no usable export job, so it resets the row and re-arms the submit job. |
| Archive share not mounted or not writable | `stage_status = failed` at step 2, before any download. Nothing written anywhere. | Fix the mount, re-run the stage job. |
| Download fails (network, share full) | `stage_status = failed`. Nothing staged. `zip_file` null. `download_export_results` streams to a temporary name and renames only after validating the zip, so no partial file carries the archive name. | Re-run the stage job; the download runs again. Same for a download that completes but fails zip validation. |
| Download URL expired | Download returns an error after seven days. `stage_status = failed`, `zip_file` null (step 2 only downloads when the archive is not already on the share). | Retry finds no usable export job, resets the row, and re-arms the submit job for a new export job. |
| Unzip fails (corrupt archive) | `stage_status = failed`. `zip_file` names the bad archive on the share. | Retry re-arms the stage job, which reuses the archive and fails again. The operator deletes the bad archive from the share by hand, then Retry downloads a fresh copy while the URL is valid, or submits a new export job after that. A second corrupt download points at Risk Modeler, not the Workbench. |
| Archive does not match: `AnlsId` differs from `irp_app_analysis_id`, currency differs, no `metadata.csv`, unknown loss table type folder, Parquet columns do not match the mapping | `stage_status = failed` with the specific mismatch. Nothing staged. | Deterministic: a re-run fails the same way. The error names the mismatch so the analyst can raise it. Nothing reaches the target tables. |
| Result file or stage data insert fails part-way (constraint, disk, connection drop) | `stage_status = failed`. Files before the failing one are committed; the failing file is rolled back. | Re-run the stage job. Cleanup deletes all file and data rows for the manifest and re-stages every file. |
| Worker killed during staging | `rwb_job` left `running`; manifest still `pending`; partial rows and a local working directory. The archive, if the download finished, is on the share with `zip_file` set. | Reconciler resets the job to `pending`. The next attempt's cleanup discards the partial rows and the working directory, reuses the archive, and re-stages. |
| Stage worker killed, or the `rwb_job` insert fails, after `stage_status = staged` and before the load job is enqueued | Stage job `running` or `failed`; `stage_status = staged`; `load_status = pending`; no load job. | Reconciler or a re-run resets the stage job. The next attempt enters at `staged` and runs step 8 alone: it enqueues the load job and succeeds. |
| Load worker killed during the load procedure | Load job `running`; `stage_status = staged`; `load_status` back to `pending` or `failed`, whichever it was before the claim. SQL Server rolls the transaction back when the connection drops, which undoes the `loading` claim; the `CATCH` block does not run. | Reconciler resets the load job. The next attempt claims the row and calls the procedure again. |
| Load procedure not installed on the server, no EXECUTE grant, or an older version than the Workbench expects (a parameter or manifest column it does not know) | Call raises. `load_status = failed` with the driver error, written by the worker when the procedure never ran and by the `CATCH` block when it ran and hit the mismatch. | The DBA installs the current `loss_schema.sql` or adds the grant (O-05, O-12), then re-run the load job. |
| Load procedure called inside an open transaction (`BEGIN TRAN` in SQL Server Management Studio, or a caller without autocommit) | The `@@TRANCOUNT` check raises before the claim. Nothing written. | Call it again outside a transaction. |
| Load procedure called on a row it must not load: `@manifest_id` names no row, `stage_status` is not `staged`, another load holds the row, or `load_status = loaded` (a second by-hand run, or a worker and a person at once) | The claim raises before any step runs. Nothing written; the error names the existing `data_id` when the row is already loaded. A worker re-run never reaches the procedure on a `loaded` row because of its own entry check. | None needed. The row was either loaded once already or was never ready to load. |
| Load procedure fails (lookup not loaded for the model version, one event matching two lookup rows, missing grant, `PCS` or `Peril` width overflow, deadlock) | Transaction rolled back: no `Data`, `RMSELT`, or `RMS_HistoricalRDS` rows; stage rows back to their staged values. `load_status = failed` with the SQL error. | Re-run the load job. Lookup assertions and width overflow are deterministic and need the lookup or the mapping fixed first (O-04, O-11). |
| Load procedure commits, load worker dies before the job completes | `load_status = loaded` (set inside the transaction). Load job `running`. | Reconciler resets the load job; the next attempt sees `loaded` and succeeds without writing. |
| Local working directory deletion fails after staging | Extracted files left on local disk. | Logged and ignored; the extracted files duplicate the archive and the next attempt's cleanup removes them. |

Two rules make the table hold: the stage job never resumes a partial stage,
it discards and restarts; and the load procedure is one transaction that
claims the manifest row as its first statement and sets `load_status =
loaded` inside it. Staging an analysis again costs a stream of rows, and a
download only when the archive is not already on the share; loading it twice
would cost a duplicate `Data` row that nothing in CIC's tables would reject
(§4.4), which is why the second rule is the one that is never relaxed. Two
constraints on the manifest back the rule: the unique index on
(`irp_app_analysis_id`, `perspective_code`) means one row per analysis per
perspective (§4.1), and the procedure's claim means one load per row
(§4.4).

### 4.8 Kind table seeds, configuration, grants

`rwb_job_type_kind`:

| Code | Change |
|---|---|
| `submit_results_export` | Add, label `Submit Results Export`, sort 40. |
| `stage_results_export` | Add, label `Stage Results Export`, sort 41. |
| `load_results_export` | Add, label `Load Results Export`, sort 42. |
| `download_export_file` | Remove. Never had a worker. |
| `push_results_to_loss_repo` | Remove. Never had a worker. |

`rwb_job_context_type_kind`: add `result_export`, label `Result Export`,
sort 70. Like the existing `execution` code, it names an ID with no table.
`rwb_job_requestor_type_kind`, `rwb_job_link_type_kind`, and
`irp_job_type_kind` need no new rows.

Configuration:

| Setting | Purpose |
|---|---|
| `LOSS` named connection | Points at `CRE_Trial_ELT_Repository`. Used by the export form and pages (manifest insert and reads, duplicate block, `dbo.Client` read) and by all three workers. |
| `EXPORT_PERSPECTIVE_CODES` | New. Perspective codes the export form may offer (§4.1). First value `GU,GR,RL,RP`. |
| `EXPORT_ARCHIVE_DIR` | New. Root directory the downloaded archives are kept under (§4.3 step 2). In production, the mount point of a shared drive on the VM, writable by the worker's service account; the same pattern as the read-only `SHARED_DRIVE_ROOT` the broker file browser uses. In development, a directory under the `rwb-data` volume. The worker fails if the root is missing rather than creating it. |
| `bootstrap-loss` | New make target. Applies the dev mirror of CIC's five tables (`Client`, `Data`, `RMSELT`, `RMS_HistoricalRDS`, `Lookup_RMS_HistoricalRDS`) and then `loss_schema.sql` (schema, tables, `CHECK` constraints, procedure) to the local repository database `rwb_loss`, so the procedure call is exercised in development. |

Grants the `LOSS` login needs, requested through O-05, all in the repository
database: SELECT on `Client`, full rights on the `stage` schema, and EXECUTE
on `stage.usp_load_elt_result`. The procedure's read of
`Lookup_RMS_HistoricalRDS` and its inserts into `Data`, `RMSELT`, and
`RMS_HistoricalRDS` run under ownership chaining, so the login needs no
grant on them. Chaining holds only when the procedure and the tables
it touches have the same owner, so `loss_schema.sql` declares `CREATE SCHEMA
stage AUTHORIZATION dbo`, and the file must be run by a login in the
`db_owner` role (the CIC DBA, O-12), never by the `LOSS` login: a schema
created by that login would be owned by it, the chain to `dbo.Data` would
break, and the procedure would fail with a permission error on its first
insert. The procedure body uses no dynamic SQL (`EXEC` of a string or
`sp_executesql`), because statements run that way are checked against the
caller, not the owner, and the chain does not cover them. The client team's
own accounts need EXECUTE on the procedure to run a load by hand.

Whether `READ_COMMITTED_SNAPSHOT` is on for the repository decides whether
the page reads in §4.1 keep their `READUNCOMMITTED` hint. Anyone with a
login on the server can check it:

```sql
SELECT name, is_read_committed_snapshot_on, snapshot_isolation_state_desc
FROM sys.databases
WHERE name = 'CRE_Trial_ELT_Repository';
```

`is_read_committed_snapshot_on = 1` means readers see the last committed
row and never wait on the load; `0` means they do (O-05).

## 5. Decisions

| ID | Decision | Status | Source |
|---|---|---|---|
| P-01 | The Workbench owns the whole export process; the workflow tool is not reused. | Approved | Note 21 D1 |
| P-02 | One perspective per export, offered as the intersection across selected analyses. | Approved | Note 21 |
| P-03 | Loss greater than exposure: set exposure equal to loss, automatically, reported with row counts. | Approved | Note 24 D12–D14 |
| P-04 | Negative independent or correlated standard deviation: set to 0, stochastic rows only, reported. | Approved | Note 24 D15 |
| P-05 | `Data` field mapping in §4.4. | Approved | Note 23 D9–D18 |
| P-06 | `DataName` is one optional field per analysis on the export form, never required, and stays editable afterwards through the workflow tool. | Approved | Note 23 D12, O23-7 |
| P-07 | Historical write is always on; no checkbox. | Approved | User, 9/3 |
| P-08 | Build and validate ELT for DLM analyses first; HD and PLT are designed for and built later. TY is its own user story. | Approved | User, 9/3 |
| T-01 | Workbench writes to the loss repository from the application VM over the `LOSS` connection. | Approved | Note 25 D1 |
| T-02 | Stage tables live in `CRE_Trial_ELT_Repository` under `stage`. | Approved | Note 25 D2 |
| T-03 | The manifest rows in the loss repository are the record of an export; the Workbench holds only the `irp_job` and `rwb_job` rows and no export table. | Proposed | §4.1, §4.6 |
| T-04 | Enrichment is a SQL join to `dbo.Lookup_RMS_HistoricalRDS` in the repository database, on event ID, model version, and peril code (peril term pending O-11); classification by absence; one match per event asserted. | Approved | Note 23 D23, note 24 D16, user 9/3, 9/4, 9/9 |
| T-05 | Enrich, correct, and load run as the stored procedure `stage.usp_load_elt_result @manifest_id`, authored in `db/bootstrap/loss_schema.sql` and installed on the loss repository server by the CIC DBA, so the client team can run a load without the Workbench. Replaces the 9/3 decision for a repo-owned SQL script run by the worker. | Approved | User, 9/7 |
| T-07 | Stage tables at three grains (manifest, file, loss row); several Parquet files per perspective is confirmed. | Approved | User, 9/3 |
| T-08 | `export_id`, a UUID generated on submit, is the batch ID on every manifest row, with `requested_by_email` and `requested_at` beside it. | Proposed | Note 25 D7 |
| T-09 | One `Data` row per analysis per perspective, ever. The form and the route block an export of an analysis that has a manifest row for the chosen perspective in any status, and a unique index on (`irp_app_analysis_id`, `perspective_code`) enforces it; a failed export is fixed with Retry. No override. Replaces the 9/3 assumption that a duplicate warns and a repeat export creates a new `Data` row. | Approved | User, 9/9 |
| T-10 | Exportable perspective codes come from `EXPORT_PERSPECTIVE_CODES`. | Approved | User, 9/3 |
| T-11 | `loss_table_type` and `engine_type` are stored on the manifest from the first build so an HD path adds tables without altering existing ones. | Proposed | §2, O-08 |
| T-12 | The manifest row is inserted when the analyst submits the form, before any job runs; workers update it. | Proposed | §4.1, AGENTS.md rule 8 |
| T-13 | PLT rows stage in their own table (§4.3); the column mapping is chosen by `loss_table_type`. | Proposed | HD example |
| T-14 | Staging and loading are two `rwb_job`s per analysis: `stage_results_export` downloads and stages, marks the manifest `staged`, and enqueues `load_results_export`, which calls the enrich, correct, load procedure. Each resumes from `stage_status` and `load_status`, so a retry never re-downloads an archive already on the share. Replaces the 9/4 decision that one job did both. | Approved | User, 9/7 |
| T-17 | The load procedure claims the manifest row (`load_status = loading`, only from `staged` and `pending`/`failed`) as the first statement of its transaction and raises when no row is claimed; the claim, not a reserved `DataID`, is what prevents a duplicate `Data` row, since `Data` has no key and no key is requested from CIC. The worker keeps its own `loaded` entry check so a re-run after a committed load succeeds. | Approved | User, 9/9 |
| T-18 | The worker calls the load procedure through a new `db.execute_procedure`, which opens the `LOSS` connection with autocommit so the procedure owns the only transaction. The procedure raises when `@@TRANCOUNT > 0` on entry, rolls back on `XACT_STATE() <> 0` in `CATCH`, and writes `failed` after the rollback. The worker also stamps `failed` on the manifest when the call raises before the procedure could write. | Approved | User, 9/9 |
| T-19 | Stage table categoricals (`stage_status`, `load_status`, `loss_table_type`, `engine_type`, `event_type`) are constrained by `CHECK` constraints in `loss_schema.sql`, not kind tables, because the tables live in the client's repository. Listed as a deviation in the plan's constitution check. | Approved | User, 9/9 |
| T-20 | Type conversions happen once, at a named place: `irp_app_analysis_id` becomes `INT` on submit (the form rejects a non-integer); the procedure writes `TreatyYear` with `CONVERT(varchar(4))` and `DataInforce` as ISO `yyyy-mm-dd` (`CONVERT(varchar(15), ..., 23)`); `data_model_version` and `peril_code` are `NVARCHAR(10)` to match the lookup. `PCS` and `Peril` are copied without truncation so an overflow fails the load. | Approved | User, 9/9 |
| T-21 | `loss_schema.sql` declares `CREATE SCHEMA stage AUTHORIZATION dbo` and names no database; the procedure reads `dbo.Lookup_RMS_HistoricalRDS` in the repository by two-part name, so one file is installed unchanged everywhere and ownership chaining covers the lookup and `dbo.Data`. The one-match lookup assertion runs before the classification `UPDATE`. Replaces the 9/9 morning synonym to `CRE_Cat_Workflow`. | Approved | User, 9/9 |
| T-29 | CIC runs one SQL Server holding `CRE_Trial_ELT_Repository` (all five CIC tables) and the Workbench application database (name to be chosen). `WORKBENCH` and `LOSS` stay separate named connections; nothing joins or transacts across the two databases. | Approved | User, 9/9 |
| T-16 | A multi-peril group (`perilCode` `YY`) joins the lookup on event ID and model version only; the one-match assertion is its protection. Fetching the member peril set from `get_regions` and joining with `Peril IN (...)` is not built unless O-11 finds repeated event IDs within one model version. | Approved | User, 9/4 |
| T-15 | Downloaded archives are kept permanently on a shared drive mounted to the VM, under `EXPORT_ARCHIVE_DIR/{export_id}/{irp_analysis.id}/`, and never deleted by the Workbench. Only the local extraction directory, and the Parquet files in it, is removed after staging. A re-run reuses an archive already on the share. Replaces note 25 D6, which deleted everything after staging. | Approved | User, 9/4; note 25 D6 |

## 6. Open decisions

| ID | Question | Owner |
|---|---|---|
| O-01 | Whether CIC ever needs the same analysis and perspective loaded a second time (a `Data` row deleted or corrected in the workflow tool), and if so who clears the manifest row so the block in T-09 lifts. Until decided, a repeat export is blocked with no override. | Ben, Cheryl |
| O-02 | TY: how Risk Modeler exports treaty-level results, what the archive contains, and what `Data` row it maps to. Separate user story. | Ben, Cheryl |
| O-03 | Retention of manifest, result file, and stage data rows after load. Nagi offered database-side retention rules. Until decided, nothing is deleted. | Ben, Nagi |
| O-04 | Check live `Lookup_RMS_HistoricalRDS` values for `Peril` and `[PCS#]` against the `RMS_HistoricalRDS` column widths, confirm `Data.Perspective` holds the perspective code, and confirm the form the workflow tool writes into `RMS_HistoricalRDS.DataInforce` (the procedure writes ISO `yyyy-mm-dd`, §4.4). | Ben |
| O-05 | When CIC re-runs the load of the five tables into `CRE_Trial_ELT_Repository` (the 9/9 load failed; the database is empty), the `LOSS` login and its grants, EXECUTE on the load procedure (note 25 O25-7), and the Workbench database name on the same server. `READ_COMMITTED_SNAPSHOT` checked 9/9: off, so the `READUNCOMMITTED` hint in §4.1 stays. | Ross, Randy, Ben |
| O-06 | Whether the analysis grid also shows the post-load summary, in addition to the export detail page (§4.5). | Ben, Cheryl |
| O-07 | Whether the per-analysis `DataName` field (P-06) is built in the first user story or deferred, leaving `DataName` null and relying on the workflow tool edit path. Deferring needs Cheryl's agreement, since she asked for the field. | Ben, Cheryl |
| O-08 | HD readiness, still open after the HD example: (a) which loss repository table PLT rows load into, and whether `Data` gains a loss table type column; (b) whether CIC accepts `HDv2.1` as `Data.DataModelVersion`; (c) whether the historical classification runs for HD events, which the v25 DLM lookup will not match; (d) whether an HD analysis also produces an ELT, and whether the analyst or `engine_type` chooses ELT versus PLT. | Ben, Cheryl, Nagi |
| O-09 | The DLM example's `metadata.csv` lists `PerspCodes` `GU,GR,RL,TY`, so analysis 41958 can be used to try a TY export for O-02. `TY` is also the HD example's `Peril` code (typhoon); the two uses must not be confused when parsing. | Ben |
| O-12 | Installing the load procedure at CIC: who runs `loss_schema.sql` on the repository server (must be a `db_owner` login, never the `LOSS` login, so the `stage` schema and procedure are owned by `dbo` and ownership chaining to `dbo.Data` holds, §4.8), how a procedure change is deployed alongside the Workbench release that needs it, and whether the CIC DBA reviews the procedure text before the first install. | Ben, Nagi |
| O-11 | Lookup join on peril. Confirm the lookup's `Peril` values are Risk Modeler peril codes (`EQ`, `WS`, `TY`, matching the analysis payload's `perilCode`), and check the live lookup for `EventID` values that repeat within one `ModelVersion`. A repeat is what would make multi-peril group exports fail under T-16. | Ben, Cheng |
| O-10 | The archive share (T-15): which share, its mount point on the VM, the service account's write grant, and who else can read it. Also whether `Data.ArchiveFile` (`varchar(100)`) should now hold the archive path, which note 23 left unpopulated when archives were not kept; `{export_id}/{irp_analysis.id}/{filename}` can exceed 100 characters, so the value would need to be the share path CIC uses or a shorter form. | Ben, Ross, Randy |
