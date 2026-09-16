# Data Model: Analysis Run Details (spec 015)

**No schema change.** No migration, no new column, no new table. Two existing
`irp_analysis` JSON columns carry new content. Decision references:
[plan.md](plan.md) T-01, T-05; column audit: [research.md](research.md#audit--how-analysis-facts-are-sourced-today-86).

## 1. `irp_analysis.settings_metadata` — gains `resolved` (T-01)

The column stays Risk Modeler's `GET /platform/riskdata/v1/analyses/{id}`
response, stored as returned, for all three origins. It gains one
workbench-owned top-level key, `resolved`, written by `finalize_analysis` (own
analyses and groups) and `backfill_rdm_analyses` (broker analyses) in the same
UPDATE as the response. Keys, per-field semantics and absence rules:
[contracts/settings-metadata-resolved.md](contracts/settings-metadata-resolved.md).

## 2. `irp_analysis.submitted_settings` — the plan item gains `treaty_names` (T-05)

Two shapes live under this column by origin, both written once at claim and
never updated:

| Origin | Shape | Writer |
|---|---|---|
| Own analysis | the execution plan **item**: `item_no`, `template_id`, `template_name`, `analysis_profile_name`, `output_profile_name`, `event_rate_scheme_name`, `currency {code, scheme, vintage, asOfDate}`, `min_loss_threshold`, `num_max_loss_event`, `franchise_deductible`, `treat_construction_occupancy_as_unknown`, `tag_names`, **`treaty_names`** (new — copied from the batch plan) | `_claim_analysis` |
| Group | the compose **plan**: `group_analysis_id`, `submission_id`, `group_full_name`, `actor_id`, `currency`, `event_rate_selections`, `simulation_set_selections`, `members[]` | `_claim_group` |
| Broker analysis | NULL | — |

`_submitted_view` reads `treat_construction_occupancy_as_unknown` (item),
`currency.code` (both), `members` (plan). Nothing in this feature reads
`treaty_names` back; it is the row's record of what was requested.

## 3. `irp_analysis.loss_results` — unchanged

One shape for every origin, written whole by `retrieve_analysis_results`
(spec 011 contracts/loss-results.md). The audit found no reader mismatch.

## 4. Entities (spec Key Entities)

| Spec term | Stored as |
|---|---|
| Partition (analysis region) — one region-and-peril combination a run resolved on | one element of `settings_metadata.resolved.partitions` |
| Applied treaty — a treaty as one analysis applied it | one element of `settings_metadata.resolved.treaties` |
