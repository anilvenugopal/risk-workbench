# Research: Analysis Run Details in the Expanded Row (spec 015)

Evidence for the `T-nn` rows in [plan.md](plan.md), the #86 audit of how
analysis facts are sourced today, and the clarification log. Every payload
fact below was read live from the sandbox tenant on 2026-09-11 through
irp-integration 0.8.0; the trimmed captures are in
[contracts/captures/](contracts/captures/). Knowledge-base shapes were wrong
before (#86: flat `currencyCode`; #106: scheme ids of 0), so nothing here is
claimed from documentation alone.

## T-01 — Resolved facts live under `settings_metadata.resolved`; no new column

**Decision.** `irp_analysis.settings_metadata` keeps Risk Modeler's
`GET /platform/riskdata/v1/analyses/{id}` response at top level and gains one
workbench-owned key, `resolved`, holding the collapsed scheme or simulation set
per region and peril and the applied treaties
([contracts/settings-metadata-resolved.md](contracts/settings-metadata-resolved.md)).

**Rationale.** The three JSON columns already divide the run's facts by when
they arise: `submitted_settings` is the request the workbench built,
`settings_metadata` is Risk Modeler's description of the finished analysis,
`loss_results` is its losses. The scheme, simulation set and treaties a run
applied are facts about the finished analysis, so they belong with
`settings_metadata`. One key the workbench owns keeps them apart from Risk
Modeler's keys, so readers stop guessing inside a payload we do not control
(#86) while every existing reader of Risk Modeler's keys is untouched.

**Alternatives.** A new `run_details` column: rejected by the approver, who
wants the three-column model kept. Child tables `irp_analysis_region` and
`irp_analysis_treaty`: nothing queries by region or treaty, and framework
ELT/PLT would need an Article 3 decision for a display-only list. An envelope
`{analysis: <RM response>, resolved: {...}}`: cleaner separation, but every
existing reader, fixture and test moves and dev databases need a rebuild.
Loose top-level keys beside Risk Modeler's: the mixing #86 exists to end.

## T-02 — Non-group rows: the scheme and simulation set collapse from region rows, the way irp-integration already does it

**Decision.** For an own or broker analysis, the event rate scheme (ELT) or
PET (PLT) is read from `analysis.get_regions(analysis_id)`, collapsed to one
entry per region and peril, and named from reference data: the scheme by
`eventRateSchemeId` against `reference_data.get_event_rate_schemes()`, the
PET by `petId` qualified with the model version
(`get_model_version_by_engine_region_peril(engineVersion, regionCode,
perilCode)`) against `get_pet_metadata_exact`. This is the procedure
`GroupingManager._inspect` runs today. The detail's `eventRateSchemeNames`
list is no longer a source. Region rows themselves are never stored.

**Evidence.**

| Origin | `get_analysis_by_id` | `get_regions` |
|---|---|---|
| Own DLM 5741781 | `eventRateSchemeNames[0].name` = "RMS 2025 Stochastic Event Rates", `additionalProperties[eventRateSchemeId].properties[0].id` = 739 | 23 rows (one per `subRegion` state), every row ELT · NA · Windstorm · `eventRateSchemeId` 739 · RL25 |
| Own DLM 5613566 | name present, id 167 | 51 rows, all `eventRateSchemeId` 167 |
| Own HD 5733173 | `eventRateSchemeNames` `[]`, `simulationSetId` 0, no scheme or PET property | 1 row: PLT · NZ · Earthquake · `petId` 12 · `numSamples` 1 · `periods` 1,978,459 · HDv3.0 |
| Own HD 5728838 | same | 1 row: PLT · JP · Windstorm · `petId` 14 · `periods` 50,000 · HDv2.1 |
| Broker DLM 5689560 | `eventRateSchemeNames` `[]`, no `eventRateSchemeId` property | 23 rows, all `eventRateSchemeId` 577 |
| Broker DLM 5723351 | same | 51 rows, all `eventRateSchemeId` 163 |

Region rows carry the peril display name (`peril: "Earthquake"`), not the
code; the detail carries both `peril` and `perilCode`, `region` and
`regionCode`. `get_event_rate_schemes()` returns `{count, totalCount, items}`
with 151 items keyed `eventRateSchemeId` / `eventRateSchemeName` (all
active, 6 `isHD`). Ids 163, 167, 577, 739 all resolve.

**Rationale.** One path serves own and broker rows and is the package's own
procedure. The own detail's name list would let own rows skip a call, but it
gives broker rows nothing and would leave two paths to keep consistent, which
is the #86 pattern.

## T-03 — Group rows: the detail's `additionalProperties` are the source

**Decision.** A group's scheme and simulation set per region and peril are
read from its own detail: the `eventRateSchemes` property on an ELT group,
the `simulationSets` property on a PLT group. Each property lists one entry
per region and peril with `regionCode`, `perilCode`, `framework`,
`eventRateSchemeId` + `eventRateSchemeName`, `simulationSetId` +
`simulationSetName`, `simulationPeriods`. `get_regions` is not called for a
group. Source selection is by payload content: a detail carrying either
property is read as a group, whatever `isGroup` says (see the INGP finding).

**Evidence.**

| Group | Detail property | `get_regions` |
|---|---|---|
| 5684003, RM-made, mixed HD + DLM (`PLTGroup1.0`) | `simulationSets`: JP·WS PLT set 15 "RMS V2.0 Stochastic Event Rates - Typhoon Events Only" 50,000; NA·EQ ELT scheme 163 "RMS 17.0 NA Stochastic Event Rates" + set 87 "North America Earthquake, RMS 17.0 NA Stochastic Event Rates" 50,000; NA·WS ELT scheme 738 "RMS 2025 Historical Event Rates" + set 146 | 98 rows: members' rows stamped `analysisId` 5684003 — NA·WS with scheme 739 (23), NA·WS with scheme 738 (23), NA·EQ 163 (51), JP·WS PET 15 (1). Nothing marks 738 as the group's choice over 739. |
| 5745731, workbench-made, ELT | `eventRateSchemes`: NA·EQ 163, NA·WS 739 | 99 rows: members' rows plus one `modelProfileId` 0 row per chosen scheme |
| 5733165, workbench-made, PLT | `simulationSets`: JP·WS PLT set 14 50,000 | 2 rows, PET 14 and PET 15 (the two HD members) |

Spec 012 research (session 2026-09-03) established that `eventRateSchemes`
records what the group's own EP used. On a PLT entry `simulationSetId` is a
PET id and `simulationSetName` its PET name (PET 15 is JPWS 2.0 "RMS V2.0
Stochastic Event Rates - Typhoon Events Only" in `PETMetadata`); on an ELT
entry it is a `SimulationSet` row, the ELT-to-PLT conversion set chosen on the
compose screen. That is the pair story 3 asks for.

**Alternatives.** `get_regions` on the group id, as #108 proposed: the rows
are the members' rows with the group's id stamped on them, and only
workbench-made groups add a `modelProfileId` 0 row per chosen scheme. An
RM-made mixed group would list every member scheme with no way to mark the
chosen one. Issue #108's claim that the property scrape need not be extended
is withdrawn by this evidence.

## T-04 — Simulation set label: PET name and simulation periods; spec wording amended

**Decision.** An HD partition reads `<petName> (<periods:,> periods)`, the
label the compose screen's Simulation set column already renders
(`grouping_view.py`: `fact.pet_name or f"PET {fact.pet_id}"` plus
`fact.periods`). Spec P-01, FR-004 and story 2 said "sample count"; they now
say simulation periods.

**Evidence.** Both HD region rows report `numSamples` 1. `periods` is
1,978,459 for PET 12 (NZ EQ) and 50,000 for PET 14 (JP WS), equal to the PET
row's `numberOfPeriods`. PET id 12 occurs twice in `get_all_pet_metadata`
(2,844 rows, 783 duplicated ids): `modelVersionCode` 3.0 "RMS 2020
Time-Dependent Rates" and 2.0 "RMS V2.0 Time-Dependent Rates". The model
version qualifier is therefore required; `get_pet_metadata_by_id(12)` raises.
`get_model_version_by_engine_region_peril("HDv3.0", "NZ", "EQ")` returns
"3.0" and `get_pet_metadata_exact(pet_id=12, model_version="3.0",
model_region_code="NZEQ")` returns the 3.0 row.

## T-05 — Treaties: applied treaties from the analysis treaty search; the plan item records the requested names

**Decision.** `analysis.search_analysis_treaties_paginated(analysis_id)`
supplies the applied treaties for own, broker and group rows; the workbench
stores `treatyId`, `treatyNumber`, `treatyName`, `currency.code`,
`occurrenceLimit`, `riskLimit`, `attachmentPoint` and `retentionAmount` as
the run applied them. The row shows number, name and currency; the other
terms are stored now so that showing them later needs no recapture
(re-decided 2026-09-11, `/speckit-analyze`). The execution plan item
written to `submitted_settings` by `_claim_analysis` gains `treaty_names`
(today `treaty_names` sits on the batch plan only,
`analysis_execution_service.py:279`). The expanded row shows the applied
list as `<number> · <name> · <currency>` (P-02).

**Evidence.** Every own analysis read carried treaties (PR1/PR2 in USD or
CAD, QS_JP in JPY); broker 5689560 carried two (XPR_1_100_Fld), broker
5723351 none; groups carried their members' treaties with `analysisId` 0 and
`lobs` `[]`. Row keys include `treatyId`, `treatyNumber`, `treatyName`,
`treatyType`, `currency {id, code, name}`, attachment and limit terms
(captures). `GroupingTreaty` keeps id and number and drops the name, so the
package's inspect output cannot supply the name; the describe method (T-06)
returns it.

## T-06 — The single-analysis collapse lives in irp-integration

**Decision.** irp-integration gains a single-analysis describe method that
reuses `_inspect`'s region, PET and scheme-naming code and returns region
facts plus applied treaties with names
([contracts/irp-gateway.md](contracts/irp-gateway.md)). The workbench gateway
wraps that one method; the workers call the gateway. Risk Modeler shape
knowledge stays in the package.

**Evidence.** `GroupingManager.inspect` rejects fewer than two ids
(`_validate_analysis_ids`: "analysis_ids must contain at least two analysis
IDs"), so the existing entry point cannot serve one analysis. The local
checkout at `../irp-integration` is at the PR 33 merge that released 0.8.0.

**Alternatives.** A workbench module reimplementing the collapse over thin
gateway reads: no package release needed, but the same shape logic in two
repos drifts. Calling `inspect` with a dummy second id: the second analysis's
reads and problems pollute the result. A treaty-only gateway wrapper over
`search_analysis_treaties_paginated` for groups: one fewer region read per
group, but a second gateway method and fake to keep in step; rejected
2026-09-11 — groups call the describe method and ignore its region facts.

**Cost.** A package change, tag and TestPyPI release precede the worker task;
`make irp-testpypi` then pins it. The method name and signature in the
contract are Assumed until the wheel exists.

## T-07 — Capture points and the failure rule

**Decision.** `finalize_analysis` captures for own and group rows in the same
UPDATE as the metadata write; `backfill_rdm_analyses` captures per broker analysis
alongside the existing per-analysis metadata read. A failed describe read
leaves `resolved` absent (or its failed half absent) for that analysis, logs a
warning, and never fails the job — the rule the metadata read in the backfill
already follows (FR-014). The metadata fetch itself keeps its current
behavior: a failed fetch still fails `finalize_analysis` and still blanks the
broker row.

**Evidence.** `_finalize_analysis_body` (`analysis_jobs.py:270`) writes
`settings_metadata` in one UPDATE after `get_analysis_metadata`;
`_backfill_rdm_analyses_body` (`entity_jobs.py:246`) fetches every analysis's
metadata before opening the transaction, and `_UPDATE_ANALYSIS_DETAIL` writes
the snapshot only when the read succeeded. Groups reach `finalize_analysis`
through `_handle_grouping_terminal` (`poller/run.py:186`) with `is_group` 1.
A group's `resolved.partitions` come from the detail already in hand; the
group still makes one describe call for its treaties and ignores the region
facts it returns; an own analysis costs one describe call; an RDM
capture costs one describe call per analysis plus reference lists fetched
once per job inside the package method.

## T-08 — One reader; dead fields deleted

**Decision.** One reader builds the display of `settings_metadata.resolved`
and serves the expanded row (own, broker, group) and the Compare modal's
metadata line (`ComparableAnalysis.event_rate_scheme`, the second view of the
same field — FR-016). `_event_rate_scheme`, with its two shape branches, is
deleted. `AnalysisSettings` loses the six fields no template or service
reads — `analysis_mode`, `construction`, `line_of_business`, `term`, `pla`,
`rate_vintage` — and `_to_display` loses the key alternates no live payload
carries (audit below).

## T-09 — Expanded row layout: no preview

**Decision.** The additions are entries in the existing `settings-grid`
(`analysis_results_inline.html`, `details.css:120`): Event rate scheme for an
ELT row, Simulation set for a PLT row, a `settings-grid__wide` list of
partitions for a group (`NA · EQ — scheme — set (periods)`), and a
`settings-grid__wide` Treaties list of `number · name`, rendered only when
non-empty (non-negotiable 3). Adding fields to an already-styled component is
the derivative case docs/UI_WORKFLOW.md exempts from a preview.

## Audit — how analysis facts are sourced today (#86)

### Writers and shapes per column and origin

| Column | Own analysis | Broker analysis | Group |
|---|---|---|---|
| `settings_metadata` | `finalize_analysis` → `get_analysis_by_id` response | `backfill_rdm_analyses` → `get_analysis_by_id` response, per hit of `search_analyses_paginated(sourceRdmName=…)` | `finalize_analysis` (name-only resolve, then `get_analysis_by_id`) |
| `submitted_settings` | `_claim_analysis` → the execution plan **item** (`analysis_execution_service.py:256`): `item_no`, `template_id`, `template_name`, `analysis_profile_name`, `output_profile_name`, `event_rate_scheme_name`, `currency`, `min_loss_threshold`, `num_max_loss_event`, `franchise_deductible`, `treat_construction_occupancy_as_unknown`, `tag_names` | NULL | `_claim_group` → the compose **plan** (`grouping_service.py:415`): `group_analysis_id`, `submission_id`, `group_full_name`, `actor_id`, `currency`, `event_rate_selections`, `simulation_set_selections`, `members` |
| `loss_results` | `retrieve_analysis_results` → `build_loss_results_extract` | same | same |

Two shapes under `submitted_settings` by origin (item vs plan); one shape
under `loss_results`, normalized at write time
(spec 011 contracts/loss-results.md). `settings_metadata` is one Risk Modeler
shape for all three origins: the own, broker and group responses read today
have identical top-level key sets (60 keys). What differs is content:

| Key | Own | Broker | Group |
|---|---|---|---|
| `eventRateSchemeNames` | `[{id: 0, code: "0", name}]` | `[]` | `[]` |
| `additionalProperties` keys | `exposure`, `minimumLossThreshold`, `franchiseDeductible`, `unrecognizedConstructionOccupancyTypes`, `dataVersion`, `eventRateSchemeId` (DLM only), `analysisInfo` | same minus `eventRateSchemeId` | `groupedAnalysisIds`, `propagateDetailedOutput`, `eventRateSchemes` or `simulationSets`, `groupSimulationPeriod`, `analysisInfo` (RM-made and workbench-made alike; broker INGP groups lack `groupedAnalysisIds`) |
| `currency` | `{currencyName, currencyCode, currencyScheme, currencyAsOfDate, currencyVintage}` | `{currencyName, currencyCode}` | as own |
| `isGroup` / `groupType` | `false` / `"ANLS"` (DLM), `null` (HD) | `false` / `"ANLS"`; broker groups `false` / `"INGP"` | `true` / `"CDGP"` (workbench-made), `null` (RM-made) |
| `perilCode` / `regionCode` | codes (`WS`, `NA`) | codes | `YY` / `YY` on a multi-peril, multi-region group |
| `simulationSetId`, `simulationPeriods` | 0 / 0 for every row read, HD included | 0 / 0 | 0 / 0 |

The `search_analyses` list item (the backfill's enumeration hit) carries
`currency`, `eventRateSchemeNames`, `simulationSetId`, `isGroup`,
`exposureResourceId/Type` but no `peril`, `region` or `perilCode`; only its
ids, name and exposure pointer are used
(`irp_gateway.search_analyses`).

### Readers and their key chains

| Reader | Keys tried | Live key, all origins | Verdict |
|---|---|---|---|
| `_to_display.analysis_type` | `analysisType`, `type` | `analysisType` | drop `type` |
| `_to_display.analysis_mode` | `analysisMode`, `mode` | `analysisMode` | **delete field** — rendered nowhere |
| `_to_display.framework` | `analysisFramework` | same | keep |
| `_to_display.engine_type` | `engineType` | same | keep |
| `_to_display.engine_version` | `engineVersion`, `modelVersion` | `engineVersion` | drop `modelVersion` |
| `_to_display.peril` | `perilCode`, `peril` | both present; `perilCode` wins, so the grid shows `WS` | keep; finding below |
| `_to_display.peril_secondary` | `subperil`, `subPeril`, `secondaryPeril` | `subPeril` | drop the other two |
| `_to_display.region` | `regionCode`, `region` | both; `regionCode` wins | keep |
| `_to_display.currency` | `currencyCode`, `currencyName`, `currency` | `currency` object | read the object only |
| `_to_display.construction`, `.line_of_business`, `.term`, `.pla`, `.rate_vintage` | assorted | `lossAmplification` exists; the rest do not | **delete fields** — rendered nowhere |
| `_event_rate_scheme` | `eventRateSchemeNames`, then `additionalProperties[eventRateSchemes]` | own only / group only; broker blank | **delete** — replaced by `resolved` (T-08) |
| `_submitted_view` | `treat_construction_occupancy_as_unknown`, `currency.code`, `members[].display_name` | item / item + plan / plan | keep; document the two shapes |
| `_broker_run_currency`, `list_results_columns`, `list_eligible_members` | the `_to_display` currency chain or `_submitted_view.currency` | as above | keep; consistent since the #86 fix |
| `build_loss_results_extract` | `engineType`, `engineVersion` off the settings payload | present | keep |
| `_retrieve_analysis_results_body` | `exposureResourceId` via a metadata re-read when the pointer is NULL | present | keep |
| `irp_gateway.get_analysis_metadata.is_group` | `isGroup` when boolean, else `groupType` / `analysisFramework` / `analysisType` / `exposureResourceType` == "GROUP" | `isGroup` always boolean | see INGP finding |

Fixtures: `tests/unit/test_broker_analyses.py` `SETTINGS_FULL` and
`SETTINGS_PARTIAL` are knowledge-base shapes (flat `currencyCode`,
`subperil`, `lineOfBusiness`); `SETTINGS_LIVE` is the 2026-08-26 capture.
Implementation replaces the first two with the captures in
`contracts/captures/` and adds own, HD, group and broker fixtures from them.

### Findings outside this spec's scope

- **Broker groups read as non-groups.** An RDM's group analyses arrive with
  `engineType` "Group", `groupType` "INGP" and `isGroup` **false**
  (5723350). The gateway trusts the boolean, so `irp_analysis.is_group` is 0
  for them. The grid still reads "Group" because the Engine cell uses
  `engineType`; the resolved-facts reader selects its source by payload
  content (T-03), so the row's scheme list renders. Whether `is_group` should
  be `isGroup or engineType == "Group"` is a grouping-eligibility question
  for a follow-up issue.
- **Multi-peril groups show `YY`.** `perilCode` and `regionCode` are `YY` on
  a group spanning perils or regions while `peril` reads "Multi-Peril" and
  `region` "Multiple regions". `_to_display` prefers the code, so the grid's
  Peril and Region cells read `YY`. The collapsed grid is out of scope here;
  follow-up issue.
- `simulationSetId` and `simulationPeriods` on the detail are 0 for every
  analysis read, HD included; they are not a source for anything.

## Clarifications

### Session 2026-09-11

- Q: Store the region rows? → A: No. Follow irp-integration's inference from
  region rows and store only the collapsed scheme (T-02).
- Q: New column for the run facts? → A: No. `settings_metadata` holds Risk
  Modeler's description of the finished analysis, `submitted_settings` the
  request the workbench built, `loss_results` the losses; the resolved facts
  belong in `settings_metadata` under one workbench key, and the requested
  treaty names in `submitted_settings` (T-01, T-05).
- Q: Is `settings_metadata` the request payload? → A: No — it is the
  `GET /analyses/{id}` response. The approver's three-column reading is the
  one the code implements.
- Q: Group source? → A: The detail's `additionalProperties` (T-03).
- Q: "Sample count"? → A: Simulation periods, as the compose screen shows
  (T-04).
- Q: Where does the single-analysis collapse live? → A: irp-integration, as a
  new describe method (T-06).
- Q: When the treaty read fails, does the row show a blank Treaties label or
  no entry? → A: A Treaties label reading *not returned*; only a successful
  read that returned no treaties shows no entry (P-04).
- Q: What does the Compare modal's metadata line show for an HD analysis?
  → A: What the run resolved on, by the expanded row's rule: scheme for DLM,
  simulation set for HD, one entry per partition for a group (P-05).
- Q: In what order are treaties and a group's partitions listed? → A: Sorted:
  treaties by treaty number, partitions by region code then peril code; not
  Risk Modeler's return order (P-06).
- Q: When several group members applied the same treaty, is it listed once or
  per member? → A: Once per distinct treaty id, no count (P-07).
- Q: Does a one-partition group render as a single field or a per-partition
  list? → A: By partition count, not origin: one partition is a single field,
  two or more are the list, group or not (P-08).
- Q: When a PLT partition's PET name did not resolve, blank or the id?
  → A: `PET <id> (<periods> periods)`, the compose screen's fallback label
  (T-04); *not returned* is reserved for partitions that were not captured.
- Q: Which label does a row show when `resolved.partitions` is absent, since
  the framework is then unknown? → A: A neutral **Run details** entry reading
  *not returned*; the same label heads the per-partition list (P-08).
- Q: Store treaty terms, or number and name only? → A: Store currency,
  occurrence limit, risk limit, attachment point and retention with each
  applied treaty; show number, name and currency (P-02, T-05).
- Q: For a group's treaties, the describe method or a treaty-only wrapper?
  → A: The describe method, one gateway method for every origin; its region
  facts are ignored for a group (T-06, T-07).
