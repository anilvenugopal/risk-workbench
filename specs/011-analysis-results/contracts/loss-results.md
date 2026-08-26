# Contract — `irp_analysis.loss_results` extract (spec 011, T-04)

Written whole, once, by the `retrieve_analysis_results` worker; read by every
results view. Source evidence: the live captures in
[research.md#R3](../research.md#r3--retrieval-model-decided-rest-endpoints-bounded-extract-stored-per-analysis-closes-o-01-for-viewing).

```jsonc
{
  "engine_type": "RL",              // settings payload engineType (FR-021)
  "engine_version": "23.0",         // settings payload engineVersion (FR-021)
  "retrieved_at": "2026-08-25T14:03:22Z",
  "perspectives": {
    "GR": {
      "aal": 38270.59,              // stats purePremium, verbatim
      "std_dev": 2645726.19,        // stats totalStdDev, verbatim
      "oep": { "5": 1234.5, "10": 2345.6, "25": 0.0, "50": 0.0,
               "100": 0.0, "250": 0.0, "500": 0.0, "1000": 0.0,
               "2000": 0.0, "5000": 0.0, "10000": 0.0 },
      "aep": { "5": 0.0, "10": 0.0, "25": 0.0, "50": 0.0, "100": 0.0,
               "250": 0.0, "500": 0.0, "1000": 0.0, "2000": 0.0,
               "5000": 0.0, "10000": 0.0 }
    },
    "RL": null,                     // fetched, analysis did not produce it (FR-004)
    "WX": null,
    "QS": null,
    "GU": null
  }
}
```

Rules:

- **All five perspective keys are always present** (`analysis_perspective_kind`
  codes). `null` = explicitly empty — the endpoints returned no rows for the
  perspective. Absence of the whole column (`loss_results IS NULL`) = not
  fetched yet. The two states are never conflated (spec non-negotiable 6).
- **Values are RM's numbers verbatim** — no rounding, no unit scaling, no
  interpolation (spec non-negotiable 5). Return-period keys are the 11 stored
  points as strings (JSON object keys), from the exact-match array lookup in
  the EP curve response (`value.returnPeriods` / `value.positionValues`).
- **EP types**: `oep` from the `epType == "OEP"` element, `aep` from
  `"AEP"`; `TCE-OEP` / `TCE-AEP` elements are discarded (spec O-04).
- **`aal`** = stats `purePremium`; **`std_dev`** = stats `totalStdDev`, both from
  the stats row whose `epType` is `OEP`. No such row → both are `null` and the
  perspective reads as explicitly empty (FR-004), the same as an empty response.
  The `OEP` match is named rather than positional because `purePremium` is the
  same mean annual loss in any row while `totalStdDev` describes the loss
  distribution, which differs between occurrence and aggregate — reading
  whichever row came first would change the standard deviation and leave AAL
  looking correct.
  Every capture so far (`ep_stats-aal_response`) is a single-element array with
  `epType: "OEP"`. A response carrying more than one stats row is **unobserved
  and undecided**: the worker takes the `OEP` row and nothing else, and the
  job's `output_data` records how many stats rows arrived (T011) so the first
  real occurrence is a queryable fact in `rwb_job` instead of an assumption
  made here.
- **`engine_type` / `engine_version`** come from the analysis metadata payload
  (`settings_metadata`, or the T-03 re-read when that is NULL); absent fields
  are stored as `null`, never omitted.
- **Write-whole**: the worker builds the complete document, then one UPDATE.
  A partially-fetched analysis is never persisted — any perspective-call
  failure fails the job and leaves `loss_results` untouched.
- Sizing: ≤ ~3 KB per analysis (5 perspectives × 22 points + stats).
