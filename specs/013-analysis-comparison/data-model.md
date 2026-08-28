# Data Model — Analysis Comparison (Iteration 10)

**No schema change.** No migration, no seeds, no new columns, no new tables.
Every read is over columns spec 011 already writes:

| Column | Written by | Read here for |
|---|---|---|
| `irp_analysis.loss_results` | `retrieve_analysis_results` worker (spec 011 T-04) | The numbers, plus the `engine_type`/`engine_version` header snapshot (T-04) |
| `irp_analysis.submitted_settings` | `_claim_analysis` at submit (spec 011 T-09) | Own-row run currency — `currency.code` (T-03, FR-005) |
| `irp_analysis.settings_metadata` | Both backfills (spec 004/011) | Broker-row run currency — `currencyCode`, or the live payload's `currency` object collapsed to its code (T-03, FR-005) |
| `rwb_job` (failed retrieval join) | Spec 011 SC-005 machinery | "retrieval failed" modal state (FR-002) |

Comparison pairs and the cart are deliberately **not persisted** (spec P-06,
Key Entities): the cart lives in the modal's Alpine state, the opened page's
pairs live in its `pairs` query param.

## View models (analysis_service)

### ResultsColumn — extended

The spec-011 dedicated-page column model gains two fields; existing callers
are unaffected.

| Field | Type | Source |
|---|---|---|
| `engine` (new) | `str \| None` | `loss_results.engine_type` + `engine_version`, joined as `AnalysisSettings.engine` joins them (e.g. "RL 23.0") |
| `run_currency` (new) | `str \| None` | Own: `submitted_settings.currency.code`; broker: the `settings_metadata` currency (`currencyCode` / `currencyName` / `currency` object — the chain the table displays) |
| existing fields | — | unchanged (`id`, `name`, `currency`, `results_state`, `results_error`, `results`) |

### ComparisonPair

One rendered pair — built by `list_comparison_pairs`, never stored.

| Field | Type | Rule |
|---|---|---|
| `base` | `ResultsColumn` | First-picked analysis (FR-003) — first column |
| `second` | `ResultsColumn` | Second column |
| `pct` | per-row `float \| None` | (second − base) / base per displayed row; `None` when either side's perspective is absent (FR-014) or base is zero/missing (T-06) |

Validation at build time (T-01) — a failing pair is dropped whole and counted
for the FR-015 notice, never partially rendered:

1. Both ids parse as UUIDs and resolve to undeleted `irp_analysis` rows.
2. The two ids differ (P-04).
3. Both run currencies are recorded (P-05) and equal (FR-005).
4. At most 5 pairs render; the rest are dropped (P-02).

### ComparableAnalysis

One modal row — built by `list_comparable_analyses` for the table at hand
(FR-002), in table order: own rows newest first, then broker rows grouped by
RDM.

| Field | Type | Rule |
|---|---|---|
| `id` | `str` | `irp_analysis.id` (broker: the representative handle, `_dedup_handles`) |
| `name` | `str \| None` | Own: `full_name or name`; broker: the broker's own analysis name |
| `rdm_name` | `str \| None` | Broker rows only — the group label |
| `run_currency` | `str \| None` | As ResultsColumn; `None` renders the row unpairable (P-05) |
| `results_state` | `str` | `pending \| failed \| ready` — only `ready` rows are tickable (FR-002) |
