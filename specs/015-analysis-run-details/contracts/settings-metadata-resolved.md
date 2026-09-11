# Contract — `settings_metadata.resolved` (spec 015, T-01)

One workbench-owned key inside `irp_analysis.settings_metadata`, beside Risk
Modeler's `GET /platform/riskdata/v1/analyses/{id}` response, which stays at
top level unchanged. Written by `finalize_analysis` (own analyses and groups)
and `backfill_rdm_analyses` (broker analyses) in the same UPDATE that stores
the response. Read by one view builder in `analysis_service`, which serves the
expanded row and the Compare modal's metadata line. Evidence:
[research.md T-01…T-05](../research.md).

```jsonc
{
  // …Risk Modeler's response keys, verbatim…
  "resolved": {
    "partitions": [                     // one per region and peril the run resolved on
      {
        "region_code": "NA",            // RM regionCode ("YY" never appears here — a
        "peril_code": "EQ",             //   group's entries come from its per-partition list)
        "framework": "ELT",             // "ELT" | "PLT"
        "event_rate_scheme": {          // ELT only; null on a PLT partition
          "id": 163,
          "name": "RMS 17.0 NA   Stochastic Event Rates"   // null when the id did not resolve
        },
        "simulation_set": {             // PLT partition: the PET. ELT partition of a PLT
          "id": 15,                     //   group: the ELT-to-PLT conversion set. Else null.
          "name": "RMS V2.0 Stochastic Event Rates - Typhoon Events Only",  // null when unresolved
          "periods": 50000              // null when unknown
        }
      }
    ],
    "treaties": [                       // the treaties the analysis applied; [] when none
      {
        "id": 33833, "number": "PR1", "name": "PR1",
        "currency": "CAD",              // currency.code as the run applied it, not irp_treaty's
        "occurrence_limit": 10000000.0, // occurrenceLimit
        "risk_limit": 5000000.0,        // riskLimit
        "attachment_point": 5000000.0,  // attachmentPoint
        "retention_amount": 0.0         // retentionAmount; each term null when RM omits it
      }
    ],
    "captured_at": "2026-09-11T14:03:22Z"
  }
}
```

Rules:

- **Presence tells the read apart from its result.** `resolved` absent means
  the capture never ran or failed as a whole (FR-014, FR-015). `partitions`
  absent while `treaties` is present (or the reverse) means that half failed.
  `treaties: []` means the read succeeded and the analysis applied none
  (FR-012). `name: null` means the id was read but not named.
- **Names are what Risk Modeler reported when captured** (P-03). No reader
  re-resolves an id.
- **Order and duplicates are fixed at write time.** `partitions` is sorted by
  `region_code` then `peril_code`; `treaties` is sorted by `number` and holds
  each `id` once, however many group members applied it (P-06, P-07).
- **Source by origin** (all three origins produce this one shape):
  - own or broker analysis: `partitions` from the describe method's region
    facts ([irp-gateway.md](irp-gateway.md)) — one entry per distinct
    (`region_code`, `peril_code`, `framework`) after collapsing the
    per-sub-region rows; `treaties` from the same method.
  - group: `partitions` from the detail's `additionalProperties` entry keyed
    `eventRateSchemes` (ELT group) or `simulationSets` (PLT group), one entry
    per property value: `regionCode` → `region_code`, `perilCode` →
    `peril_code`, `framework`, `eventRateSchemeId`/`eventRateSchemeName` →
    `event_rate_scheme` (when the id is positive), `simulationSetId`/
    `simulationSetName`/`simulationPeriods` → `simulation_set` (when the id is
    positive); `treaties` from the describe method. A detail carrying either
    property is a group for this purpose, whatever `isGroup` says.
- **Display** (`analysis_results_inline.html`):
  - a single ELT partition → `Event rate scheme: <name>`;
  - a single PLT partition → `Simulation set: <name> (<periods:,> periods)`,
    `PET <id>` in place of a null `name`;
  - several partitions, group or not (P-08) → a wide list labelled
    **Run details**, one entry per partition,
    `<region_code> · <peril_code> — <scheme name> — <set name> (<periods> periods)`,
    omitting whichever half is null;
  - `treaties` non-empty → wide list `<number> · <name> · <currency>`; the
    stored limits, attachment point and retention are not rendered (P-02);
    `[]` → no Treaties entry at all (FR-012); absent (read failed) → a
    Treaties label reading *not returned* (P-04).
  - `partitions` absent (whole `resolved` absent, or that half failed) → a
    single **Run details** entry reading *not returned*, since the framework
    is unknown; never an Event rate scheme or Simulation set label, never an
    error.
  - The Compare modal's metadata line (`compare_modal.html` `row_meta`) shows
    the same summary: the single field's value, or the partition entries
    joined, in place of today's event-rate-scheme-only part; `run details
    not returned` when blank (P-05, FR-017).

Captured payloads the fixtures are built from: [captures/](captures/) —
`own_dlm`, `own_hd`, `broker_dlm`, `broker_dlm_no_treaties`,
`broker_group_ingp`, `group_mixed_rm_made`, `group_elt_workbench_made`,
`group_plt_workbench_made`, `reference_data`, `search_analyses_item`. Each
file holds the trimmed `get_analysis_by_id`, `get_regions` (one row per
distinct combination, with the live row count noted) and
`search_analysis_treaties_paginated` responses for one analysis.
