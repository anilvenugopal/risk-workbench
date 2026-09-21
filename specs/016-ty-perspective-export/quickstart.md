# Quickstart — Verifying the TY Export

## Prerequisites

- Everything in [014 quickstart.md](../014-results-export/quickstart.md)
  "Prerequisites", plus `EXPORT_PERSPECTIVE_CODES=GU,GR,RL,RP,TY` in `infra/.env`.
- irp-integration 0.10.0rc1: `make irp-testpypi` with the `irp-testpypi` pin at
  `==0.10.0rc1` (plan.md "New dependencies"). With 0.9.0 installed every
  retrieval of an analysis with treaties fails "treaty loss read failed".
- The manifest gained four columns: run `make bootstrap-loss-reset` (or
  `make wsl-bootstrap-loss-reset`) once. Every manifest, file, and staged loss
  row in `rwb_loss` is lost; the developer decides when.
- Analyses whose results were retrieved before this branch have no
  `loss_results.treaties`, those retrieved before 2026-09-17 have it without
  the term values, and those retrieved before 2026-09-21 have entries without
  `has_loss`, so they are offered no TY or an empty terms line. Rebuild
  (`make db-rebuild`) and run again, or retry results retrieval from the
  analyses grid, for the analyses you will export.
- A finished own analysis run with two per-risk treaties that take loss (the
  sandbox analysis 42336 `CRE_HU_US_US HU wSS wDS - PROP Stochastic` did,
  treaties `PR1` and `PR2`), and for Story 1 step 8 one run with a treaty that
  takes none: an auto portfolio under a per-risk attachment too high for auto
  to reach (Cheryl's case, note 32 D11). A layer attaching at zero always
  takes loss (Wendy, note 32 §5.4). Note 28 O22-14: a treaty that takes no
  loss yields no TY rows.
- Unit tier from any host shell: `uv run pytest tests/unit`.

## Story 1 — Export an analysis's treaty losses

1. Submission → Results → **Export**. Tick the treaty analysis: the
   perspective list holds `TY` beside its other codes (acceptance 1). Also
   tick an analysis run without treaties: `TY` disappears (acceptance 2).
2. Untick it, pick `TY`: the line "TY writes one loss set per ticked treaty,
   per analysis." shows under the select, the cart row lists `PR1` and `PR2`
   with their type and `risk · att · occ` and nothing ticked, the cart row
   shows no AAL, and **Export** is disabled (acceptance 3, story 2
   acceptance 3).
3. Tick `PR1`: its Data name field appears and the head reads 1 of 2 on the
   next re-render. Type `3x2 2026` there, tick `PR2`, leave its name blank
   (the placeholder shows what it will be). Pick a client, enter a vintage,
   **Export**.
4. The exports section shows two **queued** rows straight away, Treaty `PR1`
   and `PR2` (P-09); both move to loaded. In `rwb_loss`: two `dbo.Data` rows
   with `Perspective = 'TY'`, `AnalysisID = 42336`, `DataName` reading
   `3x2 2026` and `<analysis name> PR2`; each `DataID`'s `RMSELT` +
   `RMS_HistoricalRDS` rows equal that row's staged count; the sum of both
   rows' counts equals the distinct (treaty, event) pairs in the archive's TY
   file (acceptance 4, SC-002). Every `Loss` equals the file's value: the
   sample has one row per treaty and event, so nothing was combined.
   `stage.rwb_loss_result_manifest.treaty_ids` reads `33833` and `33832`
   (acceptance 5).
5. Export the same analysis again with only `PR2` ticked: one data set, and
   the worker log carries one line reading "1 treaties in the loss table have
   no row to stage" (FR-005). Export two treaty analyses at TY in one export,
   ticking both treaties in each: four data sets, none mixing analyses
   (acceptance 6, SC-004).
6. Tick nothing in one of two selected analyses: **Export** stays disabled
   with the hint under it. Submit anyway (re-enable the button in devtools):
   the form comes back with "Tick at least one treaty for {name}." and the
   ticks you did make still ticked, and nothing is written (FR-002).
7. No treaty rows: in SQL, point a TY manifest row's `zip_file` at a copy of
   the archive whose `ELT/Treaty/TY` folder holds an empty Parquet file
   (`tests/unit/export_archive.py` `build_archive(output_level="Treaty",
   treaty_rows=[])` writes one), Retry → every treaty row of that analysis
   fails "Risk Modeler returned no treaty (TY) loss rows for this analysis",
   nothing is loaded, Retry is offered, other analyses in the export are
   untouched (acceptance 7).
8. Zero-loss treaty filtered: retrieve results for the analysis run with the
   treaty that takes none (Retry results retrieval from the analyses grid if
   they were retrieved before 2026-09-21). Pick `TY`: the cart lists the
   treaties that took loss and not that one, with no zero and no note (P-13,
   acceptance 8). Export one listed treaty: the exports section shows one row
   and it loads. In `irp_analysis.loss_results` the hidden treaty's entry
   reads `"has_loss": false`.

## Story 2 — Follow treaty data sets

1. Exports section: the one analysis occupies two rows, Treaty `PR1` and
   `PR2`, each with its own status, last change time, Data ID, and the five
   counts; the AAL is the treaty's own (`SUM(Rate × Loss)` over its rows —
   check one against the file) (acceptance 1, 3).
2. Fail one treaty's load (insert a duplicate `Lookup_RMS_HistoricalRDS` row
   for an event only that treaty carries, as 014 quickstart Story 3 step 1),
   Retry the export → that row fails, its sibling loads. The **Failed** filter
   lists the failed treaty row and not the loaded one (acceptance 4). Retry the
   failed row after deleting the duplicate: it loads; the sibling's `DataID`
   and `updated_at` are unchanged (FR-012, FR-014).
3. Request rejected: point a TY row's `irp_analysis_irp_id` at a non-existent
   analysis and export → every treaty row of that analysis fails with the
   Risk Modeler message (acceptance 2). Retry one of them: a fresh Risk
   Modeler export is requested and the row leaves "in progress" (T-14).
4. Open a loaded treaty's Data ID in the workflow tool: the data name carries
   the same treaty number the Workbench row shows (acceptance 5, SC-003).

## Story 3 — Export a group at TY

1. Create an analysis group (spec 012) from members run with the same
   treaties. Once its results are retrieved the group is offered on the export
   form with `TY` and its cart row lists each treaty once, whatever member
   treaty IDs it carries (acceptance 1). If `TY` is missing for the group
   while its members show it, check `loss_results.treaties` on the group row:
   either every entry has `has_loss` false or the results were retrieved
   before 2026-09-21 (T-13).
2. Tick both treaties and export the group at TY: one data set per ticked
   treaty; the row's `treaty_ids` lists one ID per member, and a combined
   event's `Loss` equals the sum of the members' losses for that treaty and
   event in the archive (acceptance 2, P-11).
2. Export the members without grouping: one data set per member per treaty
   (acceptance 3).

## Sandbox checks

- **T-04**: `make shell`, then
  `uv run pytest tests/irp --run-irp -k treaty` — `list_analysis_treaties`
  returns rows with `treaty_id`, `treaty_number`, `treaty_name` and the four
  term keys `treaty_type`, `risk_limit`, `attachment_point`,
  `occurrence_limit` for a finished sandbox analysis run with treaties (set
  `IRP_TEST_TREATY_ANALYSIS_ID`).
- **T-18**: the same command runs
  `test_treaty_scoped_ty_stats_answer_per_treaty`: `get_analysis_stats` at
  `TY` scoped `TREATY` answers a list for each applied treaty and at least one
  is populated. Run it before the Story 1 click-through: an empty answer for a
  treaty the TY loss table does hold would make the cart hide an exportable
  treaty, and FR-007 would never show it.
- **T-10**: after Story 1 step 4, in `linux-box`:
  `uv run python -c "import pyarrow.parquet as pq, sys; print(pq.ParquetFile(sys.argv[1]).schema.names)" <archive>/…/ELT/Treaty/TY/*_1.parquet`
  — expect the nine TY columns. A mismatch already fails the row naming the
  missing columns; fix `TY_COLUMNS` in `app/workers/export_jobs.py`.

## Test commands

| Tier | Command | Covers |
|---|---|---|
| Unit | `uv run pytest tests/unit` | Treaties recorded at retrieval; TY intersection; the treaty selection accepted and refused; the shared submit request; staging, combination, and failures; per-row load; Retry and Close by manifest row; both screens (plan.md Testing) |
| SQL Server | `make test-sql` | A TY manifest row loads with `Perspective TY` and the data name unchanged; the widened unique key. Unverified until run |
| IRP sandbox | `make shell`, then `uv run pytest tests/irp --run-irp -k treaty` | The treaties endpoint (T-04) and the treaty-scoped TY stats read (T-18). Unverified until run |
