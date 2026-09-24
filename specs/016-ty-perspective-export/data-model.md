# Data Model — Treaty-Level (TY) Loss Results Export

Deltas over [014 data-model.md](../014-results-export/data-model.md). Rationale
in [research.md](research.md); payloads in [contracts/](contracts/).

## 1. Loss repository: `stage.rwb_loss_result_manifest`

Four new nullable columns (`db/bootstrap/loss_schema.sql`, mirrored in
`tests/loss_mirror.py`). `NULL` on every row of a portfolio-level export; at TY
`treaty_number` and `treaty_name` are written when the export is requested and
`treaty_ids` and `aal` when the row is staged (P-09).

| Column | Type | Source | Read by |
|---|---|---|---|
| `treaty_number` | `NVARCHAR(64)` NULL | `create_export`, the number the analyst ticked | Both screens; the stage worker's match; the unique key |
| `treaty_name` | `NVARCHAR(256)` NULL | `create_export`, the name recorded beside that number in `loss_results.treaties` | Both screens; the stage worker's match; the unique key |
| `treaty_ids` | `NVARCHAR(400)` NULL | Stage worker: the distinct `TreatyId` values found for the treaty, ascending, comma-separated | Traceability only (FR-006, non-negotiable 2); exports table `title` |
| `aal` | `FLOAT` NULL | Stage worker: `SUM(rate × loss)` over the treaty's rows (P-10) | The exports table at TY, in place of the `loss_results` AAL |

Constraint change: `uq_rwb_loss_result_manifest_export_analysis` is now
`UNIQUE (export_id, irp_analysis_id, treaty_number, treaty_name)`. Every other
column, index, and the procedure are unchanged. `perspective_code` takes `TY`
beside the four portfolio codes (no `CHECK`, 014 T-19).

`stage.rwb_loss_schema_version` stays at 1 and so does
`export_jobs.REQUIRED_LOSS_SCHEMA_VERSION`. `loss_schema.sql` installs and never
alters, so a development `rwb_loss` still carrying the spec-014 manifest is reset,
not upgraded: `make bootstrap-loss-reset` (T-09). The version number starts
tracking the table's shape at cutover, when CIC's repository holds manifests and
the change scripts of 014 O-12 come back.

`stage.rwb_loss_result_file.output_level` takes `Treaty` for a treaty data
set's file; `result_file` then names the derived per-treaty Parquet file the
stage worker wrote under the working directory
(`{top}/ELT/Treaty/TY/{source stem}__{n}.parquet`, `n` the treaty's 1-based
position in the table), not a file inside the archive.

`stage.rwb_loss_result_elt_data` is unchanged. For a treaty data set the
worker fills `port_info_id` with `NULL`, `port_info_name` with the treaty name,
and `port_info_num` with the treaty number, so a staged row can be read
without its manifest; the loss columns hold Risk Modeler's values unchanged.

## 2. Treaty data set values

`create_export` writes one row per ticked treaty, all header columns as for a
portfolio-level row; the stage worker fills in what only the loss table knows.

| Value | Written by | Rule |
|---|---|---|
| `data_name` | `create_export` | what the analyst typed for that treaty, or `{analysis_name} {treaty_number}` when they left it blank, cut to 150 characters (P-06) |
| Loss rows | Stage worker | the table's rows whose `TreatyNum` and `TreatyName` equal the manifest row's, unchanged (P-11) |
| `treaty_ids` | Stage worker | distinct `TreatyId` of the treaty's rows, ascending; a null `TreatyId` is skipped |
| `aal` | Stage worker | Σ `Rate × Loss` over the treaty's rows |
| `staged_row_count` | Stage worker | the treaty's rows |

Everything the procedure writes (`dbo.Data`, `dbo.RMSELT`,
`dbo.RMS_HistoricalRDS`) follows 014 §5 unchanged: `Data.Perspective` ←
`perspective_code` (`TY`), `Data.DataName` ← the row's `data_name`,
`Data.AnalysisID`, `Name`, `Description` ← the analysis's values on the row.
The repository's header table has no treaty column (P-07).

## 3. Workbench: `irp_analysis.loss_results`

One new key in the existing JSON document (spec 011
`contracts/loss-results.md`), written by `retrieve_analysis_results` in the
same `UPDATE` as `perspectives`:

```json
"treaties": [
  {"treaty_id": "33833", "treaty_number": "PR1", "treaty_name": "PR1",
   "treaty_type": "WORK", "attachment_point": 2000000.0,
   "occurrence_limit": 9000000.0, "risk_limit": 3000000.0,
   "has_loss": true}
]
```

Source: `irp_gateway.list_analysis_treaties(analysis_id)` →
`GET /platform/riskdata/v1/analyses/{id}/treaties` (`treatyId`,
`treatyNumber`, `treatyName`, `treatyType`, `attachmentPoint`,
`occurrenceLimit`, `riskLimit`; `cedant` and `producer` dropped). A term Risk
Modeler omits is stored as `null`. Empty list when it reports no treaties.
`has_loss` is `true` when
`irp_gateway.get_analysis_stats(analysis_id, "TY", treaty_id, exposure_resource_type="TREATY")`
answered at least one row for that treaty, else `false` (T-18); the whole
document is written only when every read succeeded.
Read by `export_service.list_exportable_analyses` only: an entry with
`has_loss` true adds `TY` to the analysis's `perspectives`, and those entries
are what the cart lists to tick (P-13). A document without the key, or with
entries lacking `has_loss` (retrieved before 2026-09-21), reads as no treaty
with loss and offers no TY (T-13).

## 4. Settings

| Setting | Env var | Default | Change |
|---|---|---|---|
| `export_perspective_codes` | `EXPORT_PERSPECTIVE_CODES` | `GU,GR,RL,RP,TY` | `TY` added; `infra/.env.example` updated |

## 5. View models (`app/services/export_service.py`)

### ExportableAnalysis

`perspectives` includes `TY` when `loss_results.treaties` holds an entry with
`has_loss` true. `treaties` is those entries deduped by (number, name), in
recorded order — a group repeats a treaty once per member and keeps the treaty
when any copy took loss. `treaty_choices` is the cart's view of it,
one `TreatyChoice(number, name, type_label, risk_limit, attachment_point,
occurrence_limit)` per treaty with the amounts already formatted (P-12).
`aal_display("TY")` is never shown: the cart row omits the AAL line at TY
(P-10).

### ExportAnalysisDetail — one exports-table row, now one per manifest row

New fields `treaty_number`, `treaty_name`, `treaty_ids` (list of strings), and
a `treaty_label` property: `treaty_number`, then ` · treaty_name` when it
differs; empty for a portfolio row. `aal` is the row's
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
