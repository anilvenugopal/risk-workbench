# Contract — Jobs, poller, and gateway (Loss Results Export)

## 1. `rwb_job` rows

Three types, all `@rwb_actor(max_retries=0)` in `app/workers/export_jobs.py`,
each its own queue named after the function (Article 10). Every actor runs
its body through `runtime.run_job` and returns `JobResult.ok(...)` or
`JobResult.fail(...)`.

| | `submit_results_export` | `stage_results_export` | `load_results_export` |
|---|---|---|---|
| Enqueued by | Export route (§4 of routes.md) via `enqueue_rwb_job` | Poller terminal handler for `export` via `enqueue_rwb_job(conn=conn)` | Stage worker via `ensure_pending_rwb_job` |
| Re-armed by | Retry branch 3 | Retry branch 2; stage worker re-run | Retry branch 1 |
| `requestor_type` / `requestor_id` | `analyst_request` / `export_id` | `irp_job` / the `export` `irp_job.id` | `rwb_job` / the stage `rwb_job.id` |
| `link_type` / `link_id` | `not_applicable` / null | `edm` / `irp_analysis.edm_id`, or `rdm` / `irp_analysis.rdm_id` | same as stage |
| `context_type` / `context_id` | `result_export` / `export_id` | `irp_analysis` / `irp_analysis.id` | `irp_analysis` / `irp_analysis.id` |
| `input_data` | `{"export_id", "submission_id"}` | `{"export_id", "irp_analysis_id", "irp_job_id"}` | `{"export_id", "irp_analysis_id", "manifest_id"}` |
| Connections | `WORKBENCH`, Risk Modeler (submit), `LOSS` (manifest) | Risk Modeler (download), `LOSS`, `WORKBENCH` (load enqueue) | `LOSS` only |
| `time_limit` | default | 6 hours (large downloads and stages) | 6 hours |

`UNIQUE (requestor_type, requestor_id, rwb_job_type)` makes each enqueue
idempotent; `ensure_pending_rwb_job` revives a terminal row instead of
inserting a second one.

## 2. `submit_results_export` body

1. Read manifest rows `WHERE export_id = :export_id AND stage_status = 'pending' AND irp_export_job_id IS NULL`.
2. For each row: when an `export` `irp_job` already exists for
   (`export_id`, `irp_analysis_id`) — a previous run died between recording
   it and stamping the row — `UPDATE` the manifest row `irp_export_job_id`
   from it and continue, so Risk Modeler is never asked twice. Otherwise
   `irp_gateway.submit_analysis_export_job(analysis_id=int(irp_analysis_irp_id), loss_details=[{"metricType": "LOSS_TABLES", "outputLevels": ["Portfolio"], "perspectiveCodes": [perspective_code]}])`.
   - Success `(job_id, request_body)`: insert `irp_job` (`irp_job_type
     'export'`, `irp_id = job_id`, `status` from the submit response or
     `QUEUED`, `irp_analysis_id`, `export_id`, `requested_from_submission_id`
     from `input_data`, `irp_edm_id`/`irp_rdm_id` from the analysis,
     `request_params = request_body`, `submitted_at = now`); then `UPDATE`
     the manifest row `irp_export_job_id = job_id`.
   - `IRPAPIError` naming this analysis (not found, rejected): `UPDATE` the
     manifest row `stage_status = 'failed', error_message = str(e)`; continue.
   - Any other exception (auth, connection): stop; `JobResult.fail` with
     the error. Rows already submitted keep their job ID; a re-run picks up
     the rest.
3. `JobResult.ok(submitted=n, failed=m)`.

Request body Risk Modeler receives:

```json
{
  "exportType": "RESULTS",
  "resourceUris": ["/platform/riskdata/v1/analyses/<analysisId>"],
  "resourceType": "analyses",
  "settings": {
    "fileExtension": "PARQUET",
    "lossDetails": [{"metricType": "LOSS_TABLES", "outputLevels": ["Portfolio"], "perspectiveCodes": ["<code>"]}]
  }
}
```

## 3. Poller (`app/poller/run.py`)

| Map | Key | Value |
|---|---|---|
| `_GETTERS` | `export` | `irp_gateway.get_export_job` |
| `_TERMINAL_HANDLERS` | `export` | `_handle_export_terminal(conn, job, status, resolved)` |

`_handle_export_terminal` runs on any terminal status (`FINISHED`, `FAILED`,
`CANCELLED`) and only touches `WORKBENCH`:

```python
rwb_job_service.enqueue_rwb_job(
    requestor_type="irp_job", requestor_id=job["id"],
    rwb_job_type="stage_results_export",
    link_type=link_type, link_id=link_id,  # rwb_job_service.analysis_link(edm, rdm):
                                           # edm, else rdm, else not_applicable
    context_type="irp_analysis", context_id=job["irp_analysis_id"],
    input_data={"export_id": str(job["export_id"]),
                "irp_analysis_id": str(job["irp_analysis_id"]),
                "irp_job_id": str(job["id"])},
    conn=conn)
```

The poller's job select gains `export_id`. Dispatch of the enqueued job
follows the existing post-commit pattern for other handlers.

## 4. `stage_results_export` body

Entry, after reading the manifest row by (`export_id`, `irp_analysis_id`):

| State | Action |
|---|---|
| `load_status = loaded` | `JobResult.ok(skipped="loaded")` |
| `stage_status = staged` | Step 8 only |
| otherwise | `DELETE` this manifest's `rwb_loss_result_elt_data` and `rwb_loss_result_file` rows; remove the local working directory; steps 1–8 |

Every exit but success stamps the row, because Retry is offered from the
manifest alone: any failure in 1–7, an error while clearing the partial
stage, or the actor time limit → `UPDATE` manifest `stage_status = 'failed',
error_message = <reason>`; `JobResult.fail(reason)` (the time limit
re-raises after stamping).

1. Read the `export` `irp_job`; if `status <> 'FINISHED'` fail with the
   job's failure text (from `last_completion_result`) or "Risk Modeler export
   job {irp_id} ended {status}".
2. `settings.export_archive_dir` must be a directory, else fail "Archive
   root {path} is not available". If `zip_file` is set and
   `{root}/{zip_file}` exists, skip the download. Else
   `irp_gateway.download_export_results(job_id, output_dir=f"{root}/{export_id}/{irp_analysis_id}")`
   and `UPDATE` `zip_file` to the returned path relative to the root.
3. Unzip into `{settings.export_staging_dir}/{export_id}/{irp_analysis_id}/`.
   A file that is not a zip archive fails the analysis and clears `zip_file`,
   so Retry downloads it again instead of reusing it.
4. Locate exactly one top-level folder containing one loss-table folder
   (`ELT` or `PLT`, else fail "unknown loss table type {name}"); `PLT` fails
   "loss table type PLT not supported" until O-08. Read
   `{type}/metadata.csv` (fail when absent); require `AnlsId ==
   irp_app_analysis_id` and `AnalysisCurrency == data_currency`, naming
   both values on mismatch.
5. `UPDATE` manifest `loss_table_type`, `engine_type` (`Engine Type`),
   `data_model_version` (`ModelVersion`).
6. List `{type}/Portfolio/{perspective_code}/*.parquet`; fail when the
   folder is missing or another perspective folder is present. Insert one
   `rwb_loss_result_file` row per file (`output_level`, `perspective_code`,
   `chunk_index` parsed from the name).
7. Per file, in `chunk_index` order:
   `db.elt.upload_parquet(path, "rwb_loss_result_elt_data", schema="stage",
   extra_columns={"manifest_id": …, "result_file_id": …},
   column_mapping=ELT_COLUMN_MAP, drop_unmapped_columns=True, connection="LOSS")`;
   `UPDATE` the file row's `row_count`. When `SUM(row_count)` is 0, fail
   "Risk Modeler returned no {perspective_code} loss rows for this analysis"
   (P-12) without marking the row staged. Otherwise `UPDATE` manifest
   `stage_status = 'staged', staged_at = now, staged_row_count = SUM(row_count)`;
   remove the working directory (log and ignore a removal error).
8. `ensure_pending_rwb_job` for `load_results_export` (§1) and
   `dispatch.dispatch`. A failure here stamps `load_status = 'failed',
   error_message = 'could not queue the load: …'` and fails the job; the
   staged rows stay, and Retry's load branch repeats step 8.

`ELT_COLUMN_MAP`: `PortInfoId→port_info_id`, `PortInfoName→port_info_name`,
`PortInfoNum→port_info_num`, `EventId→event_id`, `Rate→rate`, `Loss→loss`,
`StdDevI→std_dev_i`, `StdDevC→std_dev_c`, `ExpValue→exp_value`. A Parquet
file whose columns do not match fails the analysis with the missing names.

## 5. `load_results_export` body

1. Read the manifest row by `manifest_id`.
2. `load_status = loaded` → `JobResult.ok(skipped="loaded")`.
3. `stage_status <> staged` → `JobResult.fail("analysis is not staged")`
   without touching the manifest.
4. `db.execute_procedure("stage.usp_load_elt_result", {"manifest_id": manifest_id}, connection="LOSS")`.
5. On exception: `db.execute_command("UPDATE stage.rwb_loss_result_manifest SET load_status = 'failed', error_message = :e, updated_at = :now WHERE manifest_id = :id AND load_status NOT IN ('loaded', 'loading')", …, connection="LOSS")` (log and continue if this raises; a row another session holds in `loading` is left alone), then `JobResult.fail` with the SQL Server message, the ODBC wrapper stripped.
6. Success → `JobResult.ok(data_id=<re-read manifest.data_id>)`.

## 6. Gateway wrappers (`app/services/irp_gateway.py`)

Added to the `IRPGateway` Protocol, `_RealGateway`, the module-level
functions, and `tests/unit/fakes/fake_irp.py`:

| Function | Wraps | Notes |
|---|---|---|
| `submit_analysis_export_job(*, analysis_id: int, loss_details: list[dict]) -> tuple[int, dict]` | `client.analysis.submit_analysis_export_job(analysis_id, loss_details, "PARQUET")` | Request path forbidden; worker only |
| `get_export_job(irp_id: str) -> JobStatus` | `client.export_job.get_export_job(int(irp_id))` | Poller only; takes `irp_job.irp_id` and answers like every other `_GETTERS` entry |
| `download_export_results(*, job_id: int, output_dir: str) -> str` | `client.export_job.download_export_results(job_id, output_dir)` | Stage worker only |

`FakeIRP` records submitted export jobs, lets a test set each job's status
and a path to a fixture archive that `download_export_results` copies into
`output_dir`, and can raise `IRPAPIError` per analysis or for the whole call.
