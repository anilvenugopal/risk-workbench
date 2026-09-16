# Research — Treaty-Level (TY) Loss Results Export

Evidence and rejected alternatives behind the decisions in [plan.md](plan.md).
Decisions themselves live there; this file says why.

## R1 — What Risk Modeler returns for a treaty-level export (T-01, T-10)

**Evidence**: the sample archive `26325601_CRE_HU_US_US_HU_wSS_wDS___PROP_Stochastic_Losses_all_financial_perspectives`
(CSV export of analysis 42336, job 26325601, run 2026-09-10, engine `DLM`,
model version `25.0`, currency `USD`):

| | Value |
|---|---|
| `metadata.csv` `PerspCodes` | `GU,GR,RL,RP` |
| `metadata.csv` `Granularities` | `Treaty` |
| Files | one: `ELT/Treaty/TY/26325601_…_ELT_Treaty_TY_1.csv`, 50,529 rows |
| Columns | `TreatyId`, `TreatyNum`, `TreatyName`, `EventId`, `Rate`, `Loss`, `StdDevI`, `StdDevC`, `ExpValue` |
| Treaties | `(33832, PR2, PR2)` 24,717 rows; `(33833, PR1, PR1)` 25,813 rows |
| Rows per (treaty, event) | one |

Four financial perspectives were requested at treaty output level and one
file came back, in a folder named `TY`, so the treaty output level supersedes
the financial perspective (note 28 §8.2, D16). Ben's live export with `GR`
alone and Cheryl's with `WX` both produced a populated TY file.

**Decisions**:

- The request sends `outputLevels ["Treaty"]` and one financial perspective.
  `GR` is the constant: it is one of the two perspectives the documented
  minimum output profile for treaty event loss tables already requires (note
  28 §2.3), so every analysis that can produce TY has it. The constant is
  `TY_REQUEST_PERSPECTIVE_CODE` in `app/workers/export_jobs.py` with P-08
  beside it, so the next reader does not take it for a bug.
- The stage worker stages `ELT/Treaty/TY/*.parquet`. Another perspective
  folder under `Treaty/` fails the analysis, mirroring 014's rule for
  `Portfolio/`.
- The Parquet columns are assumed to be the CSV columns (T-10). The 014
  Portfolio Parquet carried the CSV's `PortInfoId`/`PortInfoName`/`PortInfoNum`
  names, so the treaty file is expected to follow; a missing column fails the
  analysis naming it, as 014 already does. The sandbox check is in
  quickstart.md.
- The archive is chunked like a portfolio export can be (`_1`, `_2`, …). One
  treaty's events may span chunks, so every chunk is read before the
  per-treaty combination.

## R2 — Treaty grain widens the manifest instead of adding a child table (T-02)

Spec P-09 asks for one manifest row per treaty per analysis once the loss
table has been read, each with its own status, data ID, counts, Retry, and
Close. Two shapes were weighed.

| Shape | Why not |
|---|---|
| A child table `stage.rwb_loss_result_treaty` with its own `stage_status`, `load_status`, `data_id`, counts, `closed_at`, `error_message` | Every piece of status machinery exists twice: `derive_status`, the two counts the exports section filters on, the procedure's claim, Retry's decision tree, and both screens' read models would each need a second path. The procedure would take a treaty ID as well as a manifest ID |
| **Four nullable columns on the manifest** (`treaty_number`, `treaty_name`, `treaty_ids`, `aal`) with the pre-split analysis row becoming the first treaty's row and siblings copied from it | **Chosen.** A treaty row is a manifest row; everything downstream of the split (`usp_load_elt_result`, `derive_status`, the exports section, the detail table, Retry, Close, the failed/loaded filters) runs unchanged. The one cost is that Retry and Close can no longer be keyed by analysis (R5) |

`UNIQUE (export_id, irp_analysis_id)` becomes `UNIQUE (export_id,
irp_analysis_id, treaty_number, treaty_name)`. SQL Server treats `NULL` as a
value in a unique constraint, so an export still holds at most one pre-split
row per analysis; SQLite treats `NULL`s as distinct, which the unit tier
accepts. `treaty_ids` is a comma-separated list because a group's table can
carry one treaty under several analysis-time IDs (spec key entities) and the
value is traceability only, never a join key (non-negotiable 2).

The pre-split row is reused rather than deleted and replaced so that
`manifest_id` values referenced by a queued job, an `rwb_job.input_data`, or a
DBA's note stay valid.

## R3 — Combine in the stage worker, not in the procedure (T-03)

Spec P-11 combines one treaty's rows per event before classification. Two
places could do it.

| Place | Why not |
|---|---|
| The procedure: stage every raw treaty row with its `TreatyId`, then aggregate in T-SQL before classification | `stage.rwb_loss_result_elt_data` would need treaty columns, the procedure would need an aggregation step with the P-11 arithmetic and O-04's exposure rule, and a later change to O-04 would be a procedure release for CIC's DBA (014 O-12) |
| **The stage worker, with pandas, before `upload_parquet`** | **Chosen.** The worker already reads every Parquet file; a `groupby` on (`TreatyNum`, `TreatyName`, `EventId`) with `Loss` summed, `StdDevI` summed, `StdDevC` the root of the summed squares, `Rate` first, `ExpValue` max is a dozen lines. Each treaty's combined rows are written to one derived Parquet file with the nine ELT columns (`PortInfoId` empty, `PortInfoName` the treaty name, `PortInfoNum` the treaty number) and uploaded through the same `upload_parquet` call and column map as a portfolio file. The procedure does not know TY exists |

The sample's 50,529 rows sit in memory without effort; a treaty ELT is
bounded by the event set, not by exposure size, so this stays true for real
runs. The derived file is what `rwb_loss_result_file.result_file` names, with
`output_level = 'Treaty'`, so a DBA tracing a staged row reaches the file that
was uploaded; the archive on the share still holds the raw table.

O-04 (exposure value) is one line in `_combine_treaty_rows`. Cheryl says the
treaty's exposure is a maximum, not additive; Cheng's query is owed.

**Cross-analysis aggregation, rejected (P-01).** Note 28 D6–D7 and D10
recorded the workflow tool's behaviour: one treaty selected across several
analyses in one EDM aggregates into one loss set, and Ben's first position was
to do the same on export without a Risk Modeler group. Cheryl's owed test
(note 28 O28-9) settled it on 2026-09-11: Risk Modeler exports one archive per
analysis and does not aggregate a multi-analysis treaty export (note 29 §5.1).
Anil's argument for group-first (note 29 §5.2): grouping is one platform job
that already exists (spec 012), the aggregation then happens where the vendor
does it, and the Workbench's export path stays one archive in, N data sets
out. The alternative — the stage worker reading several analyses' tables and
combining by (`TreatyNum`, `TreatyName`, `EventId`) across them — would need
an export-level grain (which analyses belong to one treaty data set), a
different Retry model (one treaty's failure spans several archives), and its
own answer to occurrence caps, which the group already loses knowingly
("we'd lose caps … of course, but yes, you can", note 28 §9). Both paths stay
documented here at Anil's request; the built one is group-first.

## R4 — How the form knows an analysis ran with treaties (T-04, T-13)

Note 28 §2.3: the template's output profile does not predict TY; the treaty
does, and only when it took loss. Spec P-05 asks for a per-analysis fact read
from what the Workbench or Risk Modeler already records.

| Source | Why not |
|---|---|
| `irp_analysis.submitted_settings.treaty_names` | Own analyses only; broker rows (spec 004) and groups (spec 012) never carry it |
| `irp_analysis.loss_results.perspectives` | Holds the five viewing perspectives; TY is not one and cannot be read without a treaty (note 28 §3) |
| `analysis_group_member` for groups, plus one of the above for members | A second rule for one origin, and still nothing for broker rows |
| **`GET /platform/riskdata/v1/analyses/{id}/treaties`**, wrapped as `irp_gateway.list_analysis_treaties` and called by `retrieve_analysis_results` | **Chosen.** One call for every origin, made in the worker that already reads the analysis's results, stored beside them as `loss_results.treaties`. irp-integration 0.8.0 exposes it as `search_analysis_treaties_paginated(analysis_id)`, returning `treatyId`, `treatyNumber`, `treatyName`, `cedant`, `producer`, `treatyType` |

The list is stored (id, number, name), not a count, because it costs the same
and a DBA can read what Risk Modeler reported; nothing displays it, and the
export never uses it to name or match a treaty (non-negotiable 2).

**Assumed**: that the endpoint answers for a group analysis with the members'
treaties. Risk Modeler groups are analyses with an `engineType` of `GROUP`
(014 R6), so the same resource should exist; the sandbox test in quickstart.md
is the check. **Assumed**: that an analysis with no treaties answers with an
empty list rather than an error; an error fails the retrieval job like a
failed perspective read, which would surface at once in the analyses grid.

**No backfill (T-13).** An analysis whose results were retrieved before this
release has no `treaties` key and is offered no TY. The dev database is
rebuilt and results retrieved again; there is no production data (AGENTS.md
dev DB strategy, "no backwards compatibility").

## R5 — One job per analysis, acting on every eligible row (T-05, T-06)

`rwb_job` keys a job by `UNIQUE (requestor_type, requestor_id,
rwb_job_type)` with `requestor_id` a `UNIQUEIDENTIFIER`. The 014 load job is
keyed by the stage job's id, so one analysis can hold exactly one load job.

| Option | Why not |
|---|---|
| One load job per treaty row, keyed by a UUID derived from (stage job, manifest row) | Invents a requestor that no kind row names, and Retry's load branch would need to find or mint the same derived id |
| A new requestor type `manifest` with `requestor_id` … | `requestor_id` is a UUID; `manifest_id` is an `INT` in another database |
| **One stage job and one load job per analysis; each acts on every eligible manifest row of (`export_id`, `irp_analysis_id`)** | **Chosen.** Eligible means not staged, not loaded, not closed for stage; staged with `load_status` in (`pending`, `failed`) and not closed for load, which is the procedure's own claim rule. For a portfolio export the row set has one row and the 014 behaviour is unchanged; the load job's `input_data` drops `manifest_id`, which the row set now derives |

Consequence for Retry (T-06): a treaty row's Retry resets that row and
re-arms the analysis's job. A staged or loaded sibling is skipped by the
eligibility rule and a closed sibling is never touched; a failed, unclosed
sibling is retried alongside, which is the same archive and the same procedure
that would have to succeed for it anyway. Retry and Close therefore take
`manifest_id`, not `irp_analysis_id`; the two routes are renamed rather than
overloaded with a query parameter, and the analysis-keyed routes go.

## R6 — Which archive a re-run stages (T-05)

Sibling rows copy `irp_export_job_id` and `zip_file` from the pre-split row,
so every row of an analysis names the same archive. A Retry that falls to the
submit branch nulls one row's `irp_export_job_id`; the submit worker requests
a new export for that row alone and stamps it, and the poller enqueues a stage
job keyed by the new `irp_job`. The stage worker then prefers, among the rows
it will act on, one whose `zip_file` is present on the share; failing that it
downloads the job it was given into `{root}/{export_id}/{irp_analysis_id}/`,
where the filename carries the new job id, and stamps `zip_file` on every row
it stages. Rows already loaded keep the archive path they were loaded from.

## Clarifications

### Session 2026-09-16

- **Q**: Should the TY data name repeat the treaty name when it equals the
  treaty number (the sample's `PR1`/`PR1`)? → **A** (plan): no. The name is
  the number, then the name only when it differs (T-07). P-06 asks that two
  layers differ in the name, which the number alone gives.
- **Q**: Does the 2026-09-15 session's amendment of 014 (flat exports table,
  decimal model version, engine-version override) gate this plan? → **A**: it
  is not in the codebase or 014's documents. This plan changes nothing in
  those areas; a treaty row runs the same stage and load code as a portfolio
  row, so 014's later change applies to TY without a change here.
