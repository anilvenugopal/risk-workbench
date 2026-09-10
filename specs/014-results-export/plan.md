# Implementation Plan: Loss Results Export (Iteration 11)

**Branch**: `014-results-export` | **Date**: 2026-09-09 | **Spec**: [spec.md](spec.md)

<!-- Technical only. User stories and scope → spec.md. Schema → data-model.md.
     Payloads → contracts/. Endpoint investigation → research.md. Everything
     above the `---` is what a reviewer reads to decide: ten minutes to read. -->

## Plan status

**Ready for tasks:** Yes. Tasks generated 2026-09-10; analyze remediations applied the same day.
**Blocked by:** O-05 and O-10, for verification against CIC's repository only. O-05: CIC's load of the five tables into `CRE_Trial_ELT_Repository` on the shared server failed, so the repository is empty until they load it again, and the Workbench login and grants are not yet created. O-10: the share mount for `EXPORT_ARCHIVE_DIR`. Development is not blocked (note 25 D9): the dev mirror holds the same five tables and archives go under the `rwb-data` volume. T-23 (the export→download path) is unvalidated until the IRP sandbox spike in quickstart.md runs.

## Design summary

- **Export** joins Compare and View in the analyses section's summary bar and opens the page `/submissions/{submission_id}/exports/new`. The form lists the submission's finished analyses from `analysis_service.list_comparable_analyses(submission_id=…)` (own, group, and broker rows; groups reach a submission through `submission_edm` like own rows, T-24/R13). Ticking analyses re-renders, over HTMX, the perspective select (codes in `EXPORT_PERSPECTIVE_CODES` that every ticked analysis has in `loss_results`), one data-name field per ticked analysis, and the exported marks read from the manifest over `LOSS` (T-26).
- Client comes from `dbo.Client` over `LOSS`; treaty inception and CRM ID default from `submission.inception_date` and `submission_crm_id`. The form refuses an analysis whose `irp_app_analysis_id` is not an integer (FR-005).
- **POST** re-validates, generates `export_id`, inserts one `stage.rwb_loss_result_manifest` row per analysis, each carrying `requested_from_submission_id` (T-32), in one `LOSS` transaction (the unique index on `irp_app_analysis_id, perspective_code` rejects a concurrent duplicate and rolls the whole export back, T-09), then enqueues one `submit_results_export` `rwb_job` (`requestor_type analyst_request`, `requestor_id export_id`) through `rwb_job_service.enqueue_rwb_job`, dispatches it, and redirects to the export detail page. The manifest rows are the approved plan; nothing is recomputed later (T-03, T-12).
- **`submit_results_export`** (new actor, `app/workers/export_jobs.py`) reads the manifest rows with `stage_status = pending` and no `irp_export_job_id`, calls `irp_gateway.submit_analysis_export_job(irp_id, [LOSS_TABLES / Portfolio / one perspective])` per analysis, inserts one `irp_job` of type `export` carrying the new `irp_job.export_id`, `irp_analysis_id`, `requested_from_submission_id`, and the request body, and writes the Risk Modeler job ID back to the manifest. A per-analysis rejection marks that row `failed` and continues (T-23).
- **Poller** gains `_GETTERS["export"] = irp_gateway.get_export_job` (single status check) and `_TERMINAL_HANDLERS["export"]`, which enqueues `stage_results_export` on any terminal status inside the poller's `WORKBENCH` transaction (T-14).
- **`stage_results_export`** resumes from the manifest (loaded → nothing; staged → enqueue the load only; else discard partial stage rows and restart). It fails the analysis when the `export` job is not `FINISHED` or `EXPORT_ARCHIVE_DIR` is missing, downloads through `irp_gateway.download_export_results` into `{EXPORT_ARCHIVE_DIR}/{export_id}/{irp_analysis_id}/` unless `zip_file` is already there, unzips into `{submission_outputs_base}/exports/{export_id}/{irp_analysis_id}/`, checks `metadata.csv` `AnlsId` and `AnalysisCurrency` against the manifest, records `loss_table_type`, `engine_type`, `data_model_version`, writes one `stage.rwb_loss_result_file` row per Parquet file under the export's perspective folder, streams each with `db.elt.upload_parquet(schema="stage", connection="LOSS")` into `stage.rwb_loss_result_elt_data`, marks `staged`, deletes the working directory, and enqueues `load_results_export` with `ensure_pending_rwb_job` + `dispatch.dispatch` (T-15, T-07).
- **`load_results_export`** does nothing when `load_status = loaded`, fails when `stage_status ≠ staged`, otherwise calls `stage.usp_load_elt_result @manifest_id` through the new `db.execute_procedure` on an autocommit `LOSS` connection and stamps `load_status = failed` on the manifest when the call raises (T-18).
- **The procedure** (in `db/bootstrap/loss_schema.sql`) refuses to run inside a caller's transaction, claims the manifest row as the first statement of its own transaction, asserts the lookup holds the model version, asserts every event matches at most one lookup row, classifies `event_type` by the lookup join on event ID and model version, applies the two corrections with counts, inserts `dbo.Data` capturing `DataID`, inserts `dbo.RMSELT` and `dbo.RMS_HistoricalRDS`, sets `loaded` and commits; `CATCH` rolls back, writes `failed`, rethrows (T-05, T-17, T-04).
- **Submission page** gains an exports section below the analyses: the manifest rows requested from that submission grouped by `export_id`, joined to `dbo.Client`, newest first. Another submission that reaches an exported analysis shows it as exported on its form, linked to the export under its own submission (T-32). **Export detail page** `/submissions/{submission_id}/exports/{export_id}` shows the export's header values and, per analysis, a status derived from the manifest and the `export` job (data-model.md §7), last change time, archive path, `data_id`, the five counts, the error, and Retry. Manifest reads on the request path carry the dialect-aware `READUNCOMMITTED` hint because RCSI is off on the repository (T-25).
- **Retry** (`POST …/exports/{export_id}/analyses/{irp_analysis_id}/retry`) re-arms exactly one job by the manifest state: staged → load; archive present or `export` job `FINISHED` under seven days → stage; else reset the row and re-arm submit (T-28). Crash recovery is the existing heartbeat reconciler; every job resumes from the manifest.
- **DDL and bootstrap, built first (T-31)**: `db/bootstrap/loss_schema.sql` (stage schema, tables, procedure; what the CIC DBA installs) and `db/bootstrap/loss_dev_mirror.sql` (CIC's five tables, `dbo.Lookup_RMS_HistoricalRDS` included, dev only); new `infra/scripts/bootstrap_loss.py` and make target `bootstrap-loss` apply both files to `rwb_loss`, then seed `dbo.Client` with made-up rows and `dbo.Lookup_RMS_HistoricalRDS` from `db/bootstrap/seed/lookup_rms_historical_rds.csv`, the 2,754 historical events extracted from Moody's `EVENT.csv.gz` (T-22, T-21, T-30). The procedure reads the lookup by its two-part name `dbo.Lookup_RMS_HistoricalRDS`; nothing in the file names a database (T-29).
- **Seeds and config**: `rwb_job_type_kind` adds the three job types and drops `download_export_file` and `push_results_to_loss_repo`; `rwb_job_context_type_kind` adds `result_export`; `irp_job.export_id` column; settings `export_perspective_codes` and `export_archive_dir`.
- **Docs**: PRD §17.4 rewritten (broker export in scope, P-10); DATA_MODEL §1 (`LOSS` is CIC-owned, mirrored), §8 (job types), §9 (replace `analysis_result_meta`/`result_export` with a pointer to data-model.md).

## Material changes

| Area | Change |
|---|---|
| Database (Workbench) | `irp_job.export_id` (Uuid, nullable, indexed); seed rows added and removed in `alembic/versions/0001_initial.py`; no new table. |
| Database (loss repository) | New `stage` schema: `rwb_loss_result_manifest`, `rwb_loss_result_file`, `rwb_loss_result_elt_data`, procedure `usp_load_elt_result`. `rwb_loss_result_plt_data` designed, not built. Dev mirror of CIC's five tables. |
| Worker | Three actors in `app/workers/export_jobs.py`; poller getter and terminal handler for `export`; gateway wrappers for submit, status, download; `FakeIRP` equivalents. |
| Service | New `app/services/export_service.py`: exportable-analysis list, perspective intersection, duplicate check, manifest insert, exports list and detail read models, status derivation, Retry decision. |
| UI | Export button on the analyses section; export form page; exports section on the submission page; export detail page; two hidden nav nodes; small `details.css`/`components.css` extensions. Rendered preview first (UI_WORKFLOW). |
| Library (`db/`) | `db.execute_procedure` (autocommit `EXEC` with bound parameters) and `db.read_uncommitted_hint` (dialect-aware). |
| Dependencies | None new. irp-integration 0.7.2 (active `irp-testpypi` group) already has the three export methods; pyarrow and pandas are already runtime dependencies. |
| Config | `EXPORT_PERSPECTIVE_CODES` (default `GU,GR,RL,RP`), `EXPORT_ARCHIVE_DIR`; `infra/.env.example` and `infra/scripts/wsl-env.sh` updated. |
| Docs | PRD §17.4; DATA_MODEL §1, §8, §9. |

## High-risk technical decisions

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | The Workbench writes the loss repository from the application VM over the `LOSS` connection | Approved | Note 25 D1 |
| T-02 | Stage tables live in `CRE_Trial_ELT_Repository` under the `stage` schema | Approved | Note 25 D2 |
| T-03 | The manifest rows in the loss repository are the export record; the Workbench keeps `irp_job`/`rwb_job` rows only and no export table | Approved | [research.md#R1](research.md#r1--the-manifest-in-the-loss-repository-is-the-export-record-the-workbench-keeps-no-export-table-t-03-t-08-t-12) |
| T-04 | Classification is a SQL join to `dbo.Lookup_RMS_HistoricalRDS` in the repository on event ID and model version; no peril term (Moody's EVENT export shows no event ID under two perils within one model version, R4); absence means stochastic; one match per event asserted first | Approved | [research.md#R4](research.md#r4--historical-classification-is-a-join-to-cics-lookup-with-a-one-match-assertion-first-t-04) |
| T-05 | Enrich, correct, and load run as `stage.usp_load_elt_result @manifest_id`, authored in `loss_schema.sql`, installed by the CIC DBA, callable without the Workbench | Approved | [research.md#R3](research.md#r3--the-load-is-a-stored-procedure-that-claims-the-manifest-row-inside-its-own-transaction-t-05-t-17-t-18) |
| T-07 | Three stage grains: manifest, file, loss row; several Parquet files per perspective is confirmed | Approved | [research.md#R6](research.md#r6--archive-layout-metadata-and-the-parquet-schema-t-07-t-11-t-13-t-20) |
| T-08 | `export_id` (UUID from the route) is the batch ID on every manifest row, beside `requested_by_email` and `requested_at` | Approved | [research.md#R1](research.md#r1--the-manifest-in-the-loss-repository-is-the-export-record-the-workbench-keeps-no-export-table-t-03-t-08-t-12) |
| T-09 | One `Data` row per analysis per perspective, ever: form check, route check, unique index on (`irp_app_analysis_id`, `perspective_code`); failures fixed by Retry; no override | Approved | [research.md#R2](research.md#r2--one-data-set-per-analysis-per-perspective-enforced-by-a-unique-index-t-09) |
| T-10 | Exportable perspective codes come from `EXPORT_PERSPECTIVE_CODES` | Approved | User 2026-09-03 |
| T-11 | `loss_table_type` and `engine_type` are stored on the manifest from the first build | Approved | [research.md#R6](research.md#r6--archive-layout-metadata-and-the-parquet-schema-t-07-t-11-t-13-t-20) |
| T-12 | Manifest rows are inserted when the analyst submits, before any job runs; workers only update them | Approved | [research.md#R1](research.md#r1--the-manifest-in-the-loss-repository-is-the-export-record-the-workbench-keeps-no-export-table-t-03-t-08-t-12) |
| T-13 | PLT rows stage in their own table with a mapping chosen by `loss_table_type` | Deferred | Designed in data-model.md §4.4; built with the HD path (O-08) |
| T-14 | Two `rwb_job`s per analysis: `stage_results_export` then `load_results_export`, each resuming from `stage_status`/`load_status`; `submit_results_export` once per export | Approved | User 2026-09-07; [contracts/jobs.md](contracts/jobs.md) |
| T-15 | Archives are kept permanently under `EXPORT_ARCHIVE_DIR/{export_id}/{irp_analysis_id}/` and never deleted; only the local extraction directory is removed | Approved | User 2026-09-04 |
| T-17 | The procedure claims the manifest row (`loading`, from `staged` + `pending`/`failed`) as its first statement and raises on zero rows; the claim, not a reserved `DataID`, prevents a duplicate `Data` row | Approved | [research.md#R3](research.md#r3--the-load-is-a-stored-procedure-that-claims-the-manifest-row-inside-its-own-transaction-t-05-t-17-t-18) |
| T-18 | `db.execute_procedure` runs the call on an autocommit connection; the procedure guards `@@TRANCOUNT`, rolls back on `XACT_STATE() <> 0`, writes `failed` after rollback; the worker stamps `failed` when the call raises early | Approved | [research.md#R3](research.md#r3--the-load-is-a-stored-procedure-that-claims-the-manifest-row-inside-its-own-transaction-t-05-t-17-t-18) |
| T-19 | Stage categoricals use `CHECK` constraints, not kind tables (Article 3 deviation) | Approved | [research.md#R9](research.md#r9--check-constraints-instead-of-kind-tables-on-the-stage-schema-t-19) |
| T-20 | Conversions happen once at a named place: `irp_app_analysis_id` → `INT` on submit; `TreatyYear` via `CONVERT(varchar(4))`; `DataInforce` as ISO `yyyy-mm-dd`; `PCS`/`Peril` copied without truncation so overflow fails the load | Approved | [research.md#R6](research.md#r6--archive-layout-metadata-and-the-parquet-schema-t-07-t-11-t-13-t-20) |
| T-21 | `loss_schema.sql` declares `CREATE SCHEMA stage AUTHORIZATION dbo`; the procedure reads `dbo.Lookup_RMS_HistoricalRDS` in the same database by two-part name, so ownership chaining covers the lookup and no synonym, cross-database grant, or second database exists. The lookup lives in `CRE_Trial_ELT_Repository` with the other four CIC tables (user, 2026-09-09) | Approved | [research.md#R4](research.md#r4--historical-classification-is-a-join-to-cics-lookup-with-a-one-match-assertion-first-t-04) |
| T-22 | Two DDL files: `loss_schema.sql` (installed at CIC unchanged) and `loss_dev_mirror.sql` (CIC's five tables, dev only); new `bootstrap_loss.py` + make target apply both to `rwb_loss` via `execute_script_file` | Approved | [research.md#R8](research.md#r8--loss-repository-ddl-lives-in-two-files-bootstrap-loss-is-new-t-22) |
| T-23 | irp-integration ≥ 0.7.2; the submit worker passes `irp_analysis.irp_id`; the download path (content-type and zip checks) is unvalidated live until the sandbox spike | Assumed | [research.md#R7](research.md#r7--irp-integration-export-methods-on-the-active-wheel-t-23) |
| T-24 | Exportable analyses are `list_comparable_analyses(submission_id=…)` rows with results; `Data.Name` ← `irp_analysis.name`, `Data.Description` ← `irp_analysis.full_name` | Approved | [research.md#R13](research.md#r13--which-analyses-a-submission-can-export-and-where-dataname-and-datadescription-come-from-t-24) |
| T-25 | Request-path manifest reads append `db.read_uncommitted_hint(connection)` (`WITH (READUNCOMMITTED)` on SQL Server, empty on SQLite). RCSI is off on the repository (checked 2026-09-09), so the hint stays | Approved | [research.md#R10](research.md#r10--page-reads-of-the-manifest-use-a-dialect-aware-readuncommitted-hint-because-rcsi-is-off-t-25) |
| T-26 | Export form and export detail are pages under `/submissions/{id}/exports/…`; the exports table is a submission-page section; Export sits beside Compare and View | Approved | [research.md#R11](research.md#r11--export-form-and-export-detail-are-pages-the-exports-table-is-a-section-t-26) |
| T-27 | Unit tier registers a `LOSS` SQLite engine with an attached `stage` database and fakes `execute_procedure`; the procedure is tested in the SQL Server tier against the mirror; one opt-in IRP sandbox test is the T-23 spike | Approved | [research.md#R14](research.md#r14--test-tiers-sqlite-for-everything-but-the-procedure-the-procedure-on-sql-server-t-27) |
| T-28 | Retry re-arms one job by manifest state (staged → load; archive on share or `FINISHED` job under 7 days → stage; else reset and submit) through `ensure_pending_rwb_job`; actors keep `max_retries=0` | Approved | [research.md#R12](research.md#r12--retry-resumes-from-the-manifest-the-decision-tree-is-the-routes-t-28) |
| T-29 | One SQL Server at CIC hosts `CRE_Trial_ELT_Repository` and the Workbench application database (name to be chosen by CIC). `WORKBENCH` and `LOSS` stay separate named connections that differ only in `MSSQL_*_DATABASE`; no SQL joins or transactions cross the two databases, and no file names a database other than in a `USE` for installation | Approved | [research.md#R15](research.md#r15--one-sql-server-two-databases-the-lookup-is-in-the-repository-t-21-t-29) |
| T-30 | Dev seeds: `dbo.Client` gets made-up rows; `dbo.Lookup_RMS_HistoricalRDS` gets the historical subset of Moody's `EVENT` reference export (`EVENTTYPECODE = HIST`, 2,754 rows), extracted by `infra/scripts/extract_historical_events.py` from the user's `EVENT.csv.gz` into the committed `db/bootstrap/seed/lookup_rms_historical_rds.csv` and loaded by `bootstrap_loss.py`; `CatYear`/`[PCS#]` mapping Assumed | Approved | User 2026-09-10; [research.md#R16](research.md#r16--the-dev-lookup-seed-is-the-historical-subset-of-moodys-event-reference-export-t-30) |
| T-31 | Database setup is the first implementation phase: `loss_dev_mirror.sql` with both seeds, `loss_schema.sql`, `bootstrap_loss.py` + `bootstrap-loss`, and the SQL Server tier test of `stage.usp_load_elt_result` precede every route, worker, and template | Approved | User 2026-09-10 |
| T-32 | The manifest carries `requested_from_submission_id`; the exports section, detail page, and Retry filter on it, and the form's exported mark links to `/submissions/{requested_from_submission_id}/exports/{export_id}`. `irp_job.requested_from_submission_id` alone cannot list an export whose Risk Modeler jobs were never created | Approved | Spec P-16, 2026-09-10; [research.md#clarifications](research.md#clarifications) |

## Open items carried from the design

| ID | Question | Status | What is built meanwhile |
|---|---|---|---|
| O-01 | Whether CIC ever reloads an analysis and perspective, and who clears the manifest row | Deferred | The block with no override (P-09) |
| O-02 | TY treaty-level export: what Risk Modeler produces, what `Data` row it maps to | Deferred | Not offered; `TY` never appears in `EXPORT_PERSPECTIVE_CODES` |
| O-03 | Purge schedule for staged loss rows, defined and run by CIC (Nagi). Decided 2026-09-09: the Workbench deletes nothing after a stage completes; manifest and file rows are permanent (the manifest is the duplicate block, T-09); any purge touches `rwb_loss_result_elt_data` only | Deferred | Nothing is deleted (FR-024) |
| O-04 | Live lookup `Peril`/`[PCS#]` widths vs `varchar(5)` targets; `Data.Perspective` holds the code; the workflow tool's `DataInforce` form | Deferred | Copy without truncation (overflow fails the load); ISO `yyyy-mm-dd`; queries in quickstart.md |
| O-05 | When CIC's load of `dbo.Client`, `dbo.Data`, `dbo.RMSELT`, `dbo.RMS_HistoricalRDS`, and `dbo.Lookup_RMS_HistoricalRDS` into `CRE_Trial_ELT_Repository` completes (the 2026-09-09 check found the database empty: 5 MB data, no `dbo.Data`, RCSI off, because that load failed); the `LOSS` login and its grants; the Workbench database name on the same server | Open | Local mirror; blocks CIC verification only |
| O-08 | HD/PLT: destination table, `HDv2.1` as model version, classification for HD, ELT vs PLT choice | Deferred | `loss_table_type`/`engine_type` recorded; PLT table designed only; an HD archive fails at stage with "loss table type PLT not supported" |
| O-10 | Which share, its mount point on the VM, and the service account's write grant (Ross, Randy). `Data.ArchiveFile` stays unpopulated (decided 2026-09-09); the detail page shows the path | Open | `EXPORT_ARCHIVE_DIR` under the `rwb-data` volume in dev; production value is the mount. Blocks CIC verification only |
| O-11 | Repeated `EventID` within one `ModelVersion` in CIC's lookup (the peril-code half closed 2026-09-10: the join has no peril term, R4) | Deferred | The one-match assertion fails the load (50003) instead of misclassifying |
| O-12 | Which CIC DBA runs `loss_schema.sql` (a `db_owner` login) and reviews it. Mechanism decided 2026-09-09: the idempotent repo file is installed by the DBA before each Workbench release that changes it; the Workbench never runs DDL against CIC | Deferred | File authored for unchanged install; grants listed in contracts/load-procedure.md |

---

## Technical Context

<!-- Only what changed or constrains the design. The stack is documented in
     docs/PRD.md §3 (Technology stack & environment); architecture rules in
     .specify/memory/constitution.md. Do not restate either. -->

**New dependencies**: None. irp-integration 0.7.2 (active) supplies
`submit_analysis_export_job`, `get_export_job`, `download_export_results`;
pyarrow and pandas are already runtime dependencies for `db.elt.upload_parquet`.

**Databases touched**:
- `rwb_workbench` (`WORKBENCH`; at CIC a database on the repository's server whose name CIC has not chosen yet, T-29): one new column (`irp_job.export_id`), seed changes, new `rwb_job`/`irp_job` rows. Migration edits `0001_initial.py` (single revision until cutover; developer chooses Rebuild).
- `rwb_loss` (`LOSS`; dev mirror of `CRE_Trial_ELT_Repository`): new `stage` schema, tables, procedure; the request path reads `dbo.Client` and reads/writes the manifest; workers write stage rows; the procedure reads `dbo.Lookup_RMS_HistoricalRDS` and writes `dbo.Data`, `dbo.RMSELT`, `dbo.RMS_HistoricalRDS`. All five CIC tables are in this one database.
- `rwb_exposure`, DATABRIDGE: untouched.

**Filesystem**: `EXPORT_ARCHIVE_DIR` (permanent archives; a share mount in production, a directory under the `rwb-data` volume in development) and `{submission_outputs_base}/exports/…` (transient extraction).

## Constitution Check

*GATE: before Phase 0 research, re-checked after Phase 1 design.*

Reviewed against all 13 articles in `.specify/memory/constitution.md`: no
violations (re-checked after Phase 1 design and after `/speckit-analyze` on
2026-09-10).

- **Article 3 (kind tables) — client-owned database carve-out**:
  `stage_status`, `load_status`, `loss_table_type`, `engine_type`,
  `event_type` on the stage tables are `CHECK`-constrained `VARCHAR` columns
  under the Article 3 carve-out for tables the Workbench installs in a
  client-owned database (constitution v4.1.0, T-19). `perspective_code`
  (manifest and file table) and `output_level` (file table) are also
  `VARCHAR` on those tables, with no `CHECK`: both hold Risk Modeler's
  vocabulary (perspective codes chosen from `EXPORT_PERSPECTIVE_CODES`, the
  output level read from the archive path), so a `CHECK` would need editing
  whenever the configured codes change. Workbench-side categoricals follow
  the default: the three job types and the `result_export` context type are
  seed rows.

Material interactions — where an article actively shapes this design:

- **Article 7 (one data-access package)**: every request-path and worker read
  or write of the manifest, `dbo.Client`, and the Workbench goes through
  `db.execute`/`db.execute_command` with bound parameters. `db.execute_procedure`
  is the safe path's `EXEC` form (bound parameters, autocommit); it is not a
  third path. The trusted-script path (`execute_script_file`) is used only by
  the dev bootstrap script to apply DDL to the external repository, never by
  app or web code. `upload_parquet` is worker-side only.
- **Article 11 (IRP work behind an interface)**: export submission runs in
  the `submit_results_export` worker rather than on the request path (the
  article permits either); status checks run in the poller through
  `irp_gateway.get_export_job`, a single `get_*`; the download runs in the
  stage worker. No route calls `get_*`, `download_*`, or `poll_*`.
- **Article 10 (SQL table is the queue, per-queue concurrency)**: three new
  `rwb_job_type`s, each its own Dramatiq queue via `rwb_actor`,
  `max_retries=0`, one process per queue. The heartbeat reconciler is
  unchanged and is the crash-recovery path (FR-020); every job resumes from
  the manifest state. The queue's `UNIQUE (requestor_type, requestor_id,
  rwb_job_type)` makes each enqueue idempotent.
- **Article 5 (mechanical follow-up auto-fires; judgment waits)**: Export and
  Retry are analyst clicks; submit → export job → stage → load chain
  automatically (no judgment between steps, spec "no review step").
- **Article 2 (sequencing derived, not stored)**: "what runs next" is read
  off `stage_status`/`load_status`/`irp_export_job_id` on the manifest row
  and the `export` `irp_job` status; no stored stage machine.
- **Article 4 (status)**: manifest statuses are updated in place (they are
  repository rows, not `submission.status_code`); `irp_job.status` mirrors
  Risk Modeler's export job vocabulary under the existing carve-out.
- **Article 8 / Article 1 (server-rendered; nav manifest)**: two real-URL
  pages, two hidden `submissions.*` nav nodes, HTMX fragments for the
  selection-dependent re-render and Retry, no Alpine beyond the existing
  section slivers.
- **Article 6 (no RLS) / spec P-11**: any authenticated analyst can export
  and retry; `requested_by_email` is recorded for traceability, not access.
- **Article 13 (CSRF)**: the export POST and the Retry POST carry the CSRF
  token like every other state-changing route.
- **Article 12 (test-first)**: the form validator (point-of-action), the
  duplicate check, the Retry decision, and the poller handler get unit
  coverage; the procedure gets SQL Server coverage; the download path gets
  an opt-in sandbox test — see Testing.

## Project Structure

### Documentation (this feature)

```text
specs/014-results-export/
├── plan.md                  # This file
├── research.md              # R1–R16
├── data-model.md            # Workbench column + seeds; stage schema; CIC targets; view models
├── quickstart.md            # Per-story verification, DBA checks, tier commands
├── contracts/
│   ├── routes.md            # Pages, fragments, POSTs, Retry
│   ├── jobs.md              # rwb_job / irp_job payloads, poller entries, gateway wrappers
│   └── load-procedure.md    # usp_load_elt_result interface, install, grants
├── 23_loss_result_download_staging_refinement.md   # Working design (source)
├── cic-reference/           # CIC DDL
└── tasks.md                 # /speckit-tasks output (not created by /speckit-plan)
```

### Source Code (changed directories only)

```text
alembic/versions/0001_initial.py       # irp_job.export_id; rwb_job_type_kind ± ; rwb_job_context_type_kind + result_export
db/
├── __init__.py                        # export execute_procedure, read_uncommitted_hint
├── execute.py                         # execute_procedure, read_uncommitted_hint
└── bootstrap/                         # NEW directory
    ├── loss_schema.sql                # stage schema, tables, procedure (CIC install)
    ├── loss_dev_mirror.sql            # dbo.Client/Data/RMSELT/RMS_HistoricalRDS/Lookup_RMS_HistoricalRDS (dev)
    └── seed/lookup_rms_historical_rds.csv   # NEW: 2,754 historical events from Moody's EVENT export (T-30)
infra/
├── scripts/bootstrap_loss.py          # NEW: apply mirror + schema over LOSS, then seed Client and the lookup
├── scripts/extract_historical_events.py   # NEW: EVENT.csv.gz → seed CSV (HIST rows only)
├── scripts/wsl-env.sh, .env.example   # EXPORT_PERSPECTIVE_CODES, EXPORT_ARCHIVE_DIR
Makefile                               # bootstrap-loss; db-rebuild calls it
app/
├── config.py                          # export_perspective_codes, export_archive_dir
├── services/irp_gateway.py            # submit_analysis_export_job, get_export_job, download_export_results
├── services/export_service.py         # NEW: form model, duplicate check, manifest insert, list/detail, retry
├── workers/export_jobs.py             # NEW: submit_results_export, stage_results_export, load_results_export
├── poller/run.py                      # _GETTERS/_TERMINAL_HANDLERS "export"
├── routers/submissions.py             # exports/new GET+POST, perspective fragment, exports section, detail, retry
├── nav/manifest.py                    # submissions.export_new, submissions.export_detail (hidden)
├── templates/partials/analyses_merged_section.html   # Export button
├── templates/partials/exports_section.html           # NEW: exports section
├── templates/partials/export_form_fields.html        # NEW: perspective + data-name + exported marks fragment
├── templates/pages/submission_export_new.html        # NEW
├── templates/pages/submission_export_detail.html     # NEW
└── static/css/details.css, components.css            # export rows, status chips
tests/
├── unit/fakes/fake_irp.py             # export job fakes
├── loss_mirror.py                     # NEW: SQLite stage + mirror DDL for unit tier
├── unit/test_export_*.py              # see Testing
├── sqlserver/test_loss_export_procedure.py   # NEW
└── irp/test_export_download.py        # NEW, opt-in
docs/
├── PRD.md                             # §17.4
├── DATA_MODEL.md                      # §1, §8, §9
└── ui_previews/export_form.html, export_detail.html   # NEW previews (UI_WORKFLOW)
```

**Structure Decision**: existing single-app layout. New: one worker module,
one service module, one `db/bootstrap/` directory, two page templates, two
partials, one SQLite mirror module for tests.

## Complexity Tracking

| Violation | Why needed | Simpler alternative rejected because |
|---|---|---|
| None. The Article 3 `CHECK` constraints on the stage schema (T-19) fall under the client-owned database carve-out added to the constitution on 2026-09-10 (v4.1.0); the rejected alternatives are recorded in research.md R9 | | |

## Testing

<!-- Strategy by tier. Not a test-file inventory. -->

- **Unit** (`uv run pytest tests/unit`, SQLite for `WORKBENCH` and a second
  SQLite engine registered as `LOSS` with an attached `stage` database, T-27):
  the exportable-analysis list (own, group, broker; results-ready only;
  non-integer `irp_app_analysis_id` refused);
  perspective intersection against `EXPORT_PERSPECTIVE_CODES`; the duplicate
  check at render and on submit, including the unique-index rollback path
  (simulated `IntegrityError`); manifest insert values (FR-006) and the
  `submit_results_export` enqueue; the submit worker with `fake_irp`
  (per-analysis rejection, unreachable Risk Modeler, skip rows with a job
  ID); the poller getter/handler for `export`; the stage worker against a
  fixture archive built in the DLM layout (entry table, missing root, reuse
  of an existing archive, `AnlsId`/currency mismatch, missing
  `metadata.csv`, unknown loss-table folder, extra perspective folder,
  `staged` then load enqueue); the load worker's entry checks and failure
  stamp with `execute_procedure` faked; status derivation for the detail
  page; the Retry decision tree; route renders for the form, fragment,
  exports section, detail page, and Retry; nav nodes and breadcrumbs.
- **SQL Server integration** (`make test-sql`; unverified until someone runs
  it): `stage.usp_load_elt_result` against the mirror — lookup missing for
  the model version, one event matching two lookup rows, exposure raised
  and standard deviation zeroed with counts, historical-only no std-dev
  correction, `RMS_HistoricalRDS` conversions (`TreatyYear`,
  `DataInforce`, `TreatyIncept`), `Data` row captured to `data_id`, claim
  raising on a second call and on a `pending` row, `@@TRANCOUNT` guard,
  `CATCH` writing `failed` and rolling back all target rows, `loaded` only
  on commit; `db.execute_procedure` autocommit behavior; `upload_parquet`
  into `stage.rwb_loss_result_elt_data` with `extra_columns`; migration
  shape for `irp_job.export_id` and the seed changes.
- **IRP sandbox** (`uv run pytest tests/irp --run-irp` inside `linux-box`):
  submit an export for a finished sandbox analysis, bounded status polling,
  download through the gateway, assert the archive layout and `metadata.csv`
  columns of research R6. This is the T-23 spike; T-23 stays Assumed until
  it passes.
