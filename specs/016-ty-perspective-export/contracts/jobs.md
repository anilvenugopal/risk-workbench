# Contract — Jobs and gateway (TY export)

Deltas over [014 contracts/jobs.md](../../014-results-export/contracts/jobs.md).
Everything not named here is unchanged.

## 1. `rwb_job` rows

No new job type. One `submit_results_export` per export, and one
`stage_results_export` and one `load_results_export` per analysis, keyed as in
014 §1. All three now act on **every eligible manifest row of (`export_id`,
`irp_analysis_id`)**; a portfolio export has one such row, a TY analysis one
per ticked treaty.

| | Change |
|---|---|
| `load_results_export` `input_data` | `{"export_id", "irp_analysis_id"}` — `manifest_id` dropped; the row set is read at run time |
| `retrieve_analysis_results` (spec 011) | Body gains the treaties read and one TY stats read per treaty (§2) |

## 2. `retrieve_analysis_results` body (spec 011 §, amended)

After the five perspective reads and before the `UPDATE`:

```python
treaties = irp_gateway.list_analysis_treaties(analysis_id=int(row["irp_id"]))
for treaty in treaties:
    rows = irp_gateway.get_analysis_stats(
        analysis_id=int(row["irp_id"]), perspective_code=TY,
        exposure_resource_id=int(treaty["treaty_id"]),
        exposure_resource_type="TREATY")
    treaty["has_loss"] = bool(rows)
```

A raised treaties call → `JobResult.fail(f"treaties read failed: {exc}")`; a
raised stats call → `JobResult.fail(f"treaty loss read failed for
{treaty_number}: {exc}")`; both with `loss_results` untouched (no partial
write, spec 011 T-04). The stored document gains `"treaties": [{"treaty_id",
"treaty_number", "treaty_name", "treaty_type", "attachment_point",
"occurrence_limit", "risk_limit", "has_loss"}, …]`
([data-model.md §3](../data-model.md#3-workbench-irp_analysislosss_results)).

## 3. `submit_results_export` body

Rows are still selected by `stage_status = 'pending' AND irp_export_job_id IS
NULL`, then **grouped by `irp_analysis_id`**: one Risk Modeler request per
analysis, its id stamped on every row of the group, because an analysis's
ticked treaties share one loss table. A rejection (`IRPAPIError`) fails every
row of the group; the `submitted` and `failed` counters count requests. The
poller enqueues one `stage_results_export` per terminal `irp_job`, so the
shared job triggers one stage job.

The request for a group whose `perspective_code` is `TY`:

```python
loss_details=[{"metricType": "LOSS_TABLES", "outputLevels": ["Treaty"],
               "perspectiveCodes": [TY_REQUEST_PERSPECTIVE_CODE]}]   # "GR", P-08
```

Every other group keeps `outputLevels ["Portfolio"]` and its own code.

`irp_job_service.find_export_job(export_id, irp_analysis_id)` answers only a
job with `completed_at IS NULL`, so a crashed run's job is reused and a
terminal one never is (T-14).

## 4. `stage_results_export` body

Entry: read every manifest row `WHERE export_id = :e AND irp_analysis_id = :a
ORDER BY manifest_id`.

| State | Action |
|---|---|
| No rows | `JobResult.fail` |
| Every row `load_status = loaded` | `JobResult.ok(skipped="loaded")` |
| No row eligible (eligible = `stage_status <> 'staged'`, `load_status <> 'loaded'`, `closed_at IS NULL`) | Step 8 for the staged, unloaded, unclosed rows only |
| Otherwise | Steps 1–8 for the eligible rows |

Every exit but success stamps each eligible row that is not yet staged
`stage_status = 'failed'` with the reason.

1–5. As 014 §4, run once for the analysis. The archive is the `zip_file` of
   an eligible row when that file exists under the root (research R6), else
   the download of the job in `input_data`; `zip_file`, `loss_table_type`,
   `engine_type`, and `data_model_version` are stamped on every eligible row.
6. **Portfolio codes**: as 014 §4 steps 6–7 (one row).
   **TY**: list `ELT/Treaty/TY/*.parquet` (fail when the folder is missing or
   another folder sits under `Treaty/`); read every file; require the
   `TY_COLUMNS` (`TreatyId`, `TreatyNum`, `TreatyName`, `EventId`, `Rate`,
   `Loss`, `StdDevI`, `StdDevC`, `ExpValue`), failing with the missing names;
   zero rows → fail "Risk Modeler returned no treaty (TY) loss rows for this
   analysis" (FR-007). Combine per (`TreatyNum`, `TreatyName`, `EventId`) as
   [data-model.md §2](../data-model.md#2-treaty-data-set-values). Then, per
   combined treaty in (`TreatyNum`, `TreatyName`) order, `n` its 1-based
   position in that order:
   - the row is the eligible row whose `treaty_number` and `treaty_name` equal
     the treaty's; no such row (the analyst did not tick it, or it is already
     staged, loaded, or closed) → count it as skipped and move on;
   - the treaty's combined rows are written to
     `{top}/ELT/Treaty/TY/{source stem}__{n}.parquet` with the nine ELT
     columns (`PortInfoId` null, `PortInfoName` the treaty name, `PortInfoNum`
     the treaty number) and uploaded as 014 §4 step 7 under the row's
     `manifest_id` with `output_level = 'Treaty'`;
   - the row is stamped `stage_status = 'staged'`, `staged_at`,
     `staged_row_count`, `treaty_ids`, `aal`, `error_message = NULL`.
   Matching no eligible row at all fails the whole analysis with "no ticked
   treaty matches the loss table Risk Modeler returned, which holds {number}
   {name}; …", listing every combined treaty: the match is exact (FR-005) and
   T-10 is still Assumed, so the first live run says what the table held.
   Otherwise one `INFO` line reports the skipped count when it is not zero, and
   an eligible row whose treaty is not in the table is stamped failed: "treaty
   {number} {name} is not in the loss table Risk Modeler returned".
7. Remove the working directory.
8. `ensure_pending_rwb_job` for the analysis's one `load_results_export`
   (§1) and `dispatch.dispatch`. A failure stamps `load_status = 'failed'`
   on the rows staged in this run.

## 5. `load_results_export` body

1. Read every manifest row of (`export_id`, `irp_analysis_id`).
2. No rows → `JobResult.fail`. Every row loaded → `JobResult.ok(skipped="loaded")`.
3. Eligible rows: `stage_status = 'staged'`, `load_status IN ('pending', 'failed')`,
   `closed_at IS NULL`. None eligible and some row not staged →
   `JobResult.fail("analysis is not staged")` without touching the manifest
   (014 behaviour for the one-row case).
4. Per eligible row, in `manifest_id` order:
   `execute_procedure("stage.usp_load_elt_result", {"manifest_id": …}, connection="LOSS")`;
   on exception the 014 §5 step 5 stamp for that row only, and the reason is
   kept.
5. Any failure → `JobResult.fail("; ".join(reasons), loaded=[data_ids])`;
   else `JobResult.ok(data_ids=[…])`.

## 6. Gateway wrapper (`app/services/irp_gateway.py`)

| Function | Wraps | Notes |
|---|---|---|
| `list_analysis_treaties(*, analysis_id: int) -> list[dict]` | `client.analysis.search_analysis_treaties_paginated(analysis_id)` | Worker only. Returns `[{"treaty_id": str, "treaty_number", "treaty_name", "treaty_type", "attachment_point", "occurrence_limit", "risk_limit"}]`, the last four verbatim from `treatyType`, `attachmentPoint`, `occurrenceLimit`, `riskLimit` (T-16); the wheel's `cedant` and `producer` are dropped |
| `get_analysis_stats(*, analysis_id: int, perspective_code: str, exposure_resource_id: int, exposure_resource_type: str = "PORTFOLIO") -> list[dict]` | `client.analysis.get_stats(analysis_id, perspective_code, exposure_resource_id, exposure_resource_type=…)` | Worker only (spec 011). `exposure_resource_type="TREATY"` with a treaty id from `list_analysis_treaties` answers that treaty's own rows at the perspective, `[]` when it took none (T-18). Needs irp-integration ≥ 0.10.0rc1 |

`FakeIRP`: `set_analysis_treaties(analysis_id, [{"treatyId", "treatyNumber",
"treatyName", "treatyType", "attachmentPoint", "occurrenceLimit",
"riskLimit"}])` seeds the answer (default `[]`); `raise_on_analysis_treaties`
makes the call raise; `treaty_calls` records the analysis IDs asked.
`set_treaty_stats(analysis_id, treaty_id, rows)` seeds the `TREATY`-scoped
stats answer (default `[]`, no loss; any non-empty list means loss);
`raise_on_treaty_stats_for` is the set of treaty ids whose read raises;
`result_calls` records each read with its `exposure_resource_type`.
