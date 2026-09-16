# Quickstart — Verifying the TY Export

## Prerequisites

- Everything in [014 quickstart.md](../014-results-export/quickstart.md)
  "Prerequisites", plus `EXPORT_PERSPECTIVE_CODES=GU,GR,RL,RP,TY` in `infra/.env`.
- The manifest gained four columns: run `make bootstrap-loss-reset` (or
  `make wsl-bootstrap-loss-reset`) once. Every manifest, file, and staged loss
  row in `rwb_loss` is lost; the developer decides when.
- Analyses whose results were retrieved before this branch have no
  `loss_results.treaties` and are offered no TY. Rebuild (`make db-rebuild`)
  and run again, or retry results retrieval from the analyses grid, for the
  analyses you will export.
- A finished own analysis run with two per-risk treaties that take loss (the
  sandbox analysis 42336 `CRE_HU_US_US HU wSS wDS - PROP Stochastic` did,
  treaties `PR1` and `PR2`). Note 28 O22-14: a treaty that takes no loss
  yields no TY table.
- Unit tier from any host shell: `uv run pytest tests/unit`.

## Story 1 — Export an analysis's treaty losses

1. Submission → Results → **Export**. Tick the treaty analysis: the
   perspective list holds `TY` beside its other codes (acceptance 1). Also
   tick an analysis run without treaties: `TY` disappears (acceptance 2).
2. Untick it, pick `TY`: the line "TY writes one loss set per treaty, per
   analysis." shows under the select; the cart row shows no AAL (acceptance 3,
   story 2 acceptance 3). Pick a client, enter a vintage, **Export**.
3. The detail page shows one **queued** row for the analysis with an empty
   Treaty cell. When the stage runs, the row becomes treaty `PR1` and a second
   row `PR2` appears (P-09); both move to loaded.
4. In `rwb_loss`: two `dbo.Data` rows with `Perspective = 'TY'`,
   `AnalysisID = 42336`, `DataName` reading `<analysis name> PR1` and
   `<analysis name> PR2` (or your data name in place of the analysis name);
   each `DataID`'s `RMSELT` + `RMS_HistoricalRDS` rows equal that row's staged
   count; the sum of both rows' counts equals the distinct (treaty, event)
   pairs in the archive's TY file (acceptance 4, SC-002). Every `Loss` equals
   the file's value: the sample has one row per treaty and event, so nothing
   was combined. `stage.rwb_loss_result_manifest.treaty_ids` reads `33833` and
   `33832` (acceptance 5).
5. Export two treaty analyses at TY in one export: four data sets, none mixing
   analyses (acceptance 6, SC-004).
6. No treaty rows: in SQL, point a TY manifest row's `zip_file` at a copy of
   the archive whose `ELT/Treaty/TY` folder holds an empty Parquet file
   (`tests/unit/export_archive.py` `build_archive(output_level="Treaty",
   treaty_rows=[])` writes one), Retry → the row fails "Risk Modeler returned
   no treaty (TY) loss rows for this analysis", nothing is loaded, Retry is
   offered, other analyses in the export are untouched (acceptance 7).

## Story 2 — Follow treaty data sets

1. Exports section: the export's **Data sets** column reads 2 for the one
   analysis; the expanded row lists `… · PR1` and `… · PR2`, each with its own
   status, AAL, and Data ID (acceptance 1, 3).
2. Detail table: one row per treaty with the Treaty cell, last change time,
   Data ID, and the five counts; the AAL is the treaty's own
   (`SUM(Rate × Loss)` over its rows — check one against the file) (acceptance
   1, 3).
3. Fail one treaty's load (insert a duplicate `Lookup_RMS_HistoricalRDS` row
   for an event only that treaty carries, as 014 quickstart Story 3 step 1),
   Retry the export → that row fails, its sibling loads. The **Failed** filter
   lists the failed treaty row and not the loaded one (acceptance 4). Retry the
   failed row after deleting the duplicate: it loads; the sibling's `DataID`
   and `updated_at` are unchanged (FR-012, FR-014).
4. Request rejected: point a TY row's `irp_analysis_irp_id` at a non-existent
   analysis and export → one failed row for the analysis, no treaty rows
   (acceptance 2).
5. Open a loaded treaty's Data ID in the workflow tool: the data name carries
   the same treaty number the Workbench row shows (acceptance 5, SC-003).

## Story 3 — Export a group at TY

1. Create an analysis group (spec 012) from members run with the same
   treaties. Once its results are retrieved the group is offered on the export
   form with `TY` (acceptance 1). If `TY` is missing for the group while its
   members show it, plan T-04's group assumption failed: check
   `loss_results.treaties` on the group row.
2. Export the group at TY: one data set per distinct treaty in the group's
   table; the row's `treaty_ids` lists one ID per member, and a combined
   event's `Loss` equals the sum of the members' losses for that treaty and
   event in the archive (acceptance 2, P-11).
3. Export the members without grouping: one data set per member per treaty
   (acceptance 3).

## Sandbox checks of the two assumptions

- **T-04**: `make shell`, then
  `uv run pytest tests/irp --run-irp -k treaty` — `list_analysis_treaties`
  returns rows with `treaty_id`, `treaty_number`, `treaty_name` for a finished
  sandbox analysis run with treaties (set `IRP_TEST_TREATY_ANALYSIS_ID`). Run
  it once against a group analysis ID to close O-01.
- **T-10**: after Story 1 step 3, in `linux-box`:
  `uv run python -c "import pyarrow.parquet as pq, sys; print(pq.ParquetFile(sys.argv[1]).schema.names)" <archive>/…/ELT/Treaty/TY/*_1.parquet`
  — expect the nine TY columns. A mismatch already fails the row naming the
  missing columns; fix `TY_COLUMNS` in `app/workers/export_jobs.py`.

## Test commands

| Tier | Command | Covers |
|---|---|---|
| Unit | `uv run pytest tests/unit` | Treaties recorded at retrieval; TY intersection; submit body; split, combination, and failures; per-row load; Retry and Close by manifest row; both screens (plan.md Testing) |
| SQL Server | `make test-sql` | A TY manifest row loads with `Perspective TY` and the composed name; the widened unique key. Unverified until run |
| IRP sandbox | `make shell`, then `uv run pytest tests/irp --run-irp -k treaty` | The treaties endpoint (T-04) |
