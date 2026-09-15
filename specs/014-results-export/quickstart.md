# Quickstart — Verifying Loss Results Export

## Prerequisites

- Stack up (`make dev-up`; the developer starts it, not an agent), then
  `make db-rebuild` (migration, seeds, and `bootstrap-loss`, which applies
  the dev mirror of CIC's five tables and the `stage` schema to `rwb_loss`).
- `infra/.env`: `EXPORT_PERSPECTIVE_CODES=GU,GR,RL,RP`,
  `EXPORT_ARCHIVE_DIR=/workspace/data/export_archive` (create the directory
  inside `linux-box`; the stage worker refuses to create it).
- A submission with at least two finished own analyses whose results are
  retrieved (`loss_results` set), run in the sandbox Risk Modeler so a real
  export job can be submitted. For the historical path, add lookup rows in
  `rwb_loss.dbo.Lookup_RMS_HistoricalRDS` for `ModelVersion = '25.0'`
  with event IDs that appear in the analysis's ELT (the dev mirror seeds a
  few; adjust to your analysis).
- Poller and the three export queues running (`make dev-up` starts them;
  check `queue_names()` lists `submit_results_export`, `stage_results_export`,
  `load_results_export`).
- Unit tier from any host shell: `uv run pytest tests/unit`.

## Story 1 — Export finished analyses

1. Open the submission → Results section. **Export** sits beside Compare and
   View (routes.md §1). It opens `/submissions/{id}/exports/new`.
2. Tick two finished analyses. The perspective select now lists only the
   configured codes both analyses have (FR-003); one data-name field appears
   per ticked analysis (O-07). Treaty inception and CRM ID are pre-filled from
   the submission; client and data vintage are required and the vintage is
   blank (story 1 acceptance 2). Pick GR: each cart row shows that analysis's
   AAL (P-20). Clear the vintage and submit: the form comes back with "Data
   vintage is required."
   The client list includes a retired client (`ActiveFlag = 'N'`, seeded by
   `bootstrap-loss`) — P-18.
3. Pick GR and a client, click **Export**. You land on the export detail page
   with both analyses **queued** (acceptance 1).
4. Watch the statuses move: in progress → loaded. Expect several minutes per
   analysis for a real ELT.
5. In `rwb_loss`: one `dbo.Data` row per analysis with `AnalysisID`,
   `Perspective = 'GR'`, `DataModelVersion = '25.0'`, `Name`, `Description`,
   `CRMID`, `Server` reading `https://<tenant>.<domain>` (the Risk Modeler web
   UI, not `api-…`), and `Database` reading the Workbench database name
   (P-23); `dbo.RMSELT` rows plus `dbo.RMS_HistoricalRDS` rows
   equal to `staged_row_count`; every `Loss` equals the Parquet value
   (acceptance 6; SC-002, SC-003). The archive is under
   `EXPORT_ARCHIVE_DIR/{export_id}/{irp_analysis_id}/`.
6. Reopen the form, tick the same analysis, pick GR: the cart row names the
   earlier export — date, requester, status, an underlined link, and how many
   there are — and the Export button stays on (acceptance 4, P-17).
   Export again: a second `dbo.Data` row lands under a new data ID.
7. Lookup miss: export an analysis whose `ModelVersion` has no lookup rows
   (delete the seeded rows first). It loads: every event is stochastic, the
   detail page's historical count reads 0, and `dbo.RMS_HistoricalRDS` gets
   no rows (acceptance 7, non-negotiable 3, P-24).

## Story 2 — Follow an export

1. Submission page: the exports section below the analyses lists every
   export newest first with perspective, requester, time, client, analysis
   count, loaded / failed counts; a row opens the detail page (acceptance 5).
   An analysis exported for GR and RL shows two rows; the analyses grid is
   unchanged (acceptance 6). A group analysis exported from another
   submission is not listed here, but this submission's export form shows it
   as exported with a link to that export (story 1 acceptance 4, P-16).
2. Detail page, loaded row: data ID, AAL, rows staged, stochastic, historical,
   exposure raised, standard deviation zeroed; stochastic + historical = staged
   (acceptance 2, 6).
3. Failed row: the error message and **Retry**; sibling rows show their own
   status (acceptance 3).
4. Every row shows its last change time (acceptance 1).
5. Pick **Failed** in the exports section: only the exports holding a failed
   analysis stay listed, and expanding one still shows all of its analyses.
   The same choice on the detail page keeps only the failed analyses; Retry
   from there comes back under the filter (acceptance 7).

## Story 3 — Retry

1. Load failure: stop the load queue's worker, export, wait for `staged`,
   then in SQL insert a second `Lookup_RMS_HistoricalRDS` row for an event
   the analysis carries, under the same `ModelVersion`, and start the worker
   → the load fails "event {id} matches 2 historical lookup rows". Delete the
   duplicate, click **Retry**: the load runs again with no new download (the
   archive's mtime is unchanged) and the row reads loaded (acceptance 1).
2. Rejected or expired export job: set `EXPORT_ARCHIVE_DIR` to a missing
   path, export → the stage step fails "Archive root … is not available".
   Fix the path, Retry → the stage job re-runs and downloads (the job is
   still `FINISHED` and under seven days). To exercise branch 3, set the
   manifest row's `irp_export_job_id` to a bogus value and the `irp_job` to
   `FAILED`, Retry → a new Risk Modeler export job is submitted for that
   analysis only (acceptance 2).
3. A loaded row offers no Retry. Re-run its load job from the monitoring
   page: the job succeeds and writes nothing (`Data` row count unchanged)
   (acceptance 3; FR-021).
4. Archive mismatch: replace the archive on the share with the DLM example
   archive for a different analysis, delete the stage rows, Retry → fails
   naming the `AnlsId` mismatch; nothing reaches the targets (acceptance 4).
5. Close: on a failed row click **Close** → the row reads closed with your
   email and the time, offers neither Retry nor Close, keeps its error
   message, and leaves the export's failed count. Close the same analysis
   from the exports section's expanded row instead: the section comes back
   with the filter still applied. Retry a closed row (POST by hand) → 409
   "the analysis is closed, not failed" (acceptance 6).

## Crash recovery (FR-020)

Kill the stage worker mid-download (`docker kill` the worker container while
the row reads in progress). After `RWB_HEARTBEAT_STALE_SECS` the
poller's reconciler resets the job; the next attempt reuses the archive if
the download completed, else downloads again; no duplicate stage rows.

## By hand from SQL Server Management Studio (FR-022)

```sql
EXEC stage.usp_load_elt_result @manifest_id = <id>;
```

Against a `staged` row: loads and stamps `loaded`; the detail page shows it
without any Workbench job. Against a `loaded` row: raises 50001 naming the
data ID. Inside `BEGIN TRAN`: raises 50000, nothing written.

## Server checks (O-04, O-05, O-11) — run on the CIC server

Open [`cic-reference/validate_loss_repo_server.sql`](cic-reference/validate_loss_repo_server.sql)
in SQL Server Management Studio and run it section by section. Each section
states the good result and the ask to send CIC when the result is not good;
section 9 collects the asks. Section 7 (lookup widths, repeated event IDs)
and section 8 (how the workflow tool writes `DataInforce` and
`Perspective`) run only once CIC has loaded the five tables.

Status 2026-09-09: `CRE_Trial_ELT_Repository` on the CIC server is empty
because CIC's load of the five tables failed (plan O-05); RCSI is off there
(T-25 closed). Re-run sections 4, 7, and 8 once the load succeeds.

## Test commands

| Tier | Command | Covers |
|---|---|---|
| Unit | `uv run pytest tests/unit` | Form validation, intersection, duplicate check, manifest insert, three workers with `fake_irp` and a fixture archive, poller handler, status derivation, Retry, route renders (plan.md Testing) |
| SQL Server | `make test-sql` | `usp_load_elt_result` behavior, `execute_procedure` autocommit, `upload_parquet` into `stage`, migration shape. Unverified until run |
| IRP sandbox | `make shell`, then `uv run pytest tests/irp --run-irp -k export` | Submit, status, download, archive layout (the T-23 spike) |
