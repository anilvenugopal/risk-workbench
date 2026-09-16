# Data Model — Treaty-Level (TY) Loss Results Export

Deltas over [014 data-model.md](../014-results-export/data-model.md). Rationale
in [research.md](research.md); payloads in [contracts/](contracts/).

## 1. Loss repository: `stage.rwb_loss_result_manifest`

Four new nullable columns (`db/bootstrap/loss_schema.sql`, mirrored in
`tests/loss_mirror.py`). `NULL` on every row of a portfolio-level export and
on a TY analysis row until its loss table has been read (P-09).

| Column | Type | Source | Read by |
|---|---|---|---|
| `treaty_number` | `NVARCHAR(64)` NULL | Stage worker, `TreatyNum` as written in the loss table | Composed `data_name`; both screens; the unique key |
| `treaty_name` | `NVARCHAR(256)` NULL | Stage worker, `TreatyName` as written | Composed `data_name`; both screens; the unique key |
| `treaty_ids` | `NVARCHAR(400)` NULL | Stage worker: the distinct `TreatyId` values found for the treaty, ascending, comma-separated (one for a single analysis, one per member for a group) | Traceability only (FR-006, non-negotiable 2); exports table `title` |
| `aal` | `FLOAT` NULL | Stage worker: `SUM(rate × loss)` over the treaty's combined rows (P-10) | The exports table at TY, in place of the `loss_results` AAL |

Constraint change: `uq_rwb_loss_result_manifest_export_analysis` is now
`UNIQUE (export_id, irp_analysis_id, treaty_number, treaty_name)`. Every other
column, index, and the procedure are unchanged. `perspective_code` takes `TY`
beside the four portfolio codes (no `CHECK`, 014 T-19).

`stage.rwb_loss_result_file.output_level` takes `Treaty` for a treaty data
set's file; `result_file` then names the derived per-treaty Parquet file the
stage worker wrote under the working directory
(`{top}/ELT/Treaty/TY/{source stem}__{n}.parquet`, `n` the treaty's 1-based
position in the table), not a file inside the archive.

`stage.rwb_loss_result_elt_data` is unchanged. For a treaty data set the
worker fills `port_info_id` with `NULL`, `port_info_name` with the treaty name,
and `port_info_num` with the treaty number, so a staged row can be read
without its manifest; the loss columns hold the combined values of §2.

## 2. Treaty data set values

Written by the stage worker onto the treaty's manifest row when the loss table
is read. The pre-split analysis row takes the first treaty (table order:
`TreatyNum`, then `TreatyName`); each further treaty gets a sibling row that
copies every header column of the pre-split row (`export_id` through
`region_code`, `irp_export_job_id`, `loss_table_type`, `engine_type`,
`data_model_version`, `zip_file`) with `stage_status` and `load_status`
`pending`.

| Value | Rule |
|---|---|
| `data_name` | `{base} {treaty_number}` where `base` is the analyst's data name for the analysis, or `analysis_name` when blank; when `treaty_name` differs from `treaty_number`, ` {treaty_name}` follows; cut to 150 characters (T-07, P-06) |
| Combined loss row, per (`treaty_number`, `treaty_name`, `EventId`) | `Loss` summed; `StdDevI` summed; `StdDevC` = √(Σ `StdDevC`²); `Rate` the first row's; `ExpValue` the largest (spec O-04); a treaty and event appearing once are unchanged (P-11) |
| `treaty_ids` | distinct `TreatyId` of the treaty's rows, ascending |
| `aal` | Σ `Rate × Loss` over the combined rows |
| `staged_row_count` | the treaty's combined rows |

Everything the procedure writes (`dbo.Data`, `dbo.RMSELT`,
`dbo.RMS_HistoricalRDS`) follows 014 §5 unchanged: `Data.Perspective` ←
`perspective_code` (`TY`), `Data.DataName` ← the composed `data_name`,
`Data.AnalysisID`, `Name`, `Description` ← the analysis's values on the row.
The repository's header table has no treaty column (P-07).

## 3. Workbench: `irp_analysis.loss_results`

One new key in the existing JSON document (spec 011
`contracts/loss-results.md`), written by `retrieve_analysis_results` in the
same `UPDATE` as `perspectives`:

```json
"treaties": [
  {"treaty_id": "33833", "treaty_number": "PR1", "treaty_name": "PR1"}
]
```

Source: `irp_gateway.list_analysis_treaties(analysis_id)` →
`GET /platform/riskdata/v1/analyses/{id}/treaties` (`treatyId`,
`treatyNumber`, `treatyName`; `cedant`, `producer`, `treatyType` dropped).
Empty list when Risk Modeler reports none. Read by
`export_service.list_exportable_analyses` only: a non-empty list adds `TY` to
the analysis's `perspectives`. No screen shows it. A document without the key
(retrieved before this release) reads as no treaties (T-13).

## 4. Settings

| Setting | Env var | Default | Change |
|---|---|---|---|
| `export_perspective_codes` | `EXPORT_PERSPECTIVE_CODES` | `GU,GR,RL,RP,TY` | `TY` added; `infra/.env.example` updated |

## 5. View models (`app/services/export_service.py`)

### ExportableAnalysis

`perspectives` includes `TY` when `loss_results.treaties` is non-empty.
`aal_display("TY")` is never shown: the cart row omits the AAL line at TY
(P-10).

### ExportAnalysisDetail — one exports-table row, now one per manifest row

New fields `treaty_number`, `treaty_name`, `treaty_ids` (list of strings), and
a `treaty_label` property: `treaty_number`, then ` · treaty_name` when it
differs; empty for a portfolio row or a pre-split TY row. `aal` is the row's
`aal` column when `perspective_code = 'TY'`, else the `loss_results` value at
render time as before. Status derivation (014 §7) is unchanged; the manifest
row decides alone. `manifest_id` is what Retry and Close post to.

The status filter counts treaty rows like analysis rows.

### ExportedMark

`earlier_count` counts distinct `export_id`s for the analysis and perspective
(T-11), so one earlier TY export of two treaties reads as one.

## 6. Row ordering

`list_export_rows` orders manifest rows within an export by
`analysis_description`, `analysis_name`, `treaty_number`, `treaty_name`,
`manifest_id`, so an analysis's treaty rows sit together under it in treaty
order.
