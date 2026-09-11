# Implementation Plan: Analysis Run Details in the Expanded Row

**Branch**: `015-analysis-run-details` | **Date**: 2026-09-11 | **Spec**: [spec.md](spec.md)

<!-- Technical only. User stories and scope → spec.md. Schema → data-model.md.
     Payloads → contracts/. Endpoint investigation → research.md. Everything
     above the `---` is what a reviewer reads to decide: ten minutes to read. -->

## Plan status

**Ready for tasks:** Yes. T-06's package method is a prerequisite task, not a
blocker: its placement is approved, its signature stays Assumed until the
TestPyPI wheel exists and is re-confirmed.
**Blocked by:** Nothing.

## Design summary

- **No schema change.** `irp_analysis.settings_metadata` gains one
  workbench-owned key, `resolved`, beside Risk Modeler's `GET /analyses/{id}`
  response: the collapsed scheme or simulation set per region and peril
  (`partitions`) and the applied treaties (`treaties`) —
  [contracts/settings-metadata-resolved.md](contracts/settings-metadata-resolved.md)
  (T-01). Risk Modeler's keys and every reader of them are untouched.
- **irp-integration grows a single-analysis describe method** reusing the
  grouping inspect's region, PET and scheme-naming code and returning the
  treaty name inspect drops (T-06). It is released to TestPyPI and pinned
  with `make irp-testpypi` before the worker work starts. The workbench
  gateway wraps it as `describe_analysis_run`, worker-only
  ([contracts/irp-gateway.md](contracts/irp-gateway.md)).
- **Own and broker analyses** get `partitions` by collapsing the describe
  method's region facts to distinct (region, peril, framework), each named
  from reference data (T-02, T-04). **Groups** get `partitions` from the
  detail's `additionalProperties` entry keyed `eventRateSchemes` or
  `simulationSets`, which Risk Modeler writes per region and peril with names
  (T-03); the describe call a group makes for its treaties has its region
  facts ignored. Every origin gets `treaties` from the describe method, stored
  with currency, limits, attachment point and retention (T-05).
- `finalize_analysis` (own analyses and groups) writes `resolved` in the
  same UPDATE that stores the metadata. `backfill_rdm_analyses` writes it per
  broker analysis in its existing per-analysis loop. A failed describe read
  logs, leaves that analysis's `resolved` (or the failed half) absent, and
  never fails the job (T-07). The metadata fetch keeps its current failure
  behavior.
- `_claim_analysis` writes `treaty_names` into the plan item it stores as
  `submitted_settings`, copied from the batch plan (T-05). Approved plans stay
  immutable; nothing recomputes at execution.
- One reader in `analysis_service` turns `settings_metadata.resolved` into
  the display and serves both the expanded row and the Compare modal's
  metadata line (FR-016). `_event_rate_scheme` is deleted; `AnalysisSettings`
  loses its six unrendered fields and `_to_display` its dead key alternates
  (T-08, research audit).
- The expanded row's settings grid shows Event rate scheme for an ELT row,
  Simulation set `name (N periods)` for a PLT row (`PET <id>` when the name
  did not resolve), a wide Run details list when a run has two or more
  partitions or a Run details entry reading *not returned* when partitions
  were not captured (P-08), and a wide Treaties list `number · name ·
  currency` — omitted when the read returned none, *not returned* when the
  read failed (P-04). Partitions sort by region then peril, treaties by
  number with one entry per treaty id (P-06, P-07). The Compare modal's
  metadata line shows the same summary, `run details not returned` when
  blank (P-05). No preview: derivative additions to a styled component
  (T-09).
- Unit fixtures for own DLM, own HD, broker DLM, broker group and three
  group shapes come from the live captures in
  [contracts/captures/](contracts/captures/); the knowledge-base fixtures
  `SETTINGS_FULL` / `SETTINGS_PARTIAL` are replaced.
- `docs/DATA_MODEL.md` §6 states what each of the three JSON columns holds
  per origin, including the two `submitted_settings` shapes (item vs compose
  plan) and the `resolved` key.

## Material changes

| Area | Change |
|---|---|
| Database | None. `settings_metadata` JSON gains the `resolved` key; the plan item in `submitted_settings` gains `treaty_names`. Dev DB choice: **Refresh** — no migration; analyses captured before the change show the new fields blank until recaptured (FR-015). |
| Worker | `finalize_analysis` and `backfill_rdm_analyses` call `describe_analysis_run` and write `resolved`; `_claim_analysis` stores `treaty_names`. |
| UI | `analysis_results_inline.html` settings grid: conditional Event rate scheme / Simulation set entry, group partition list, Treaties list. Compare modal metadata line reads the same field. |
| Library | irp-integration: new `describe_run` (name Assumed) on `AnalysisManager`, TestPyPI release; workbench `irp_gateway` + `FakeIRP` gain `describe_analysis_run`. |
| Docs | `docs/DATA_MODEL.md` §6 column semantics. |

## High-risk technical decisions

<!-- Status: Approved | Proposed | Assumed | Open | Deferred | Blocked. -->

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | Resolved facts live under `settings_metadata.resolved`, one workbench key beside Risk Modeler's response; no new column, no envelope | Approved | [research](research.md#t-01--resolved-facts-live-under-settings_metadataresolved-no-new-column) |
| T-02 | Own and broker rows collapse region rows to one scheme or PET per region and peril, named from reference data, the way `_inspect` does; the detail's `eventRateSchemeNames` is retired as a source | Approved | [research](research.md#t-02--non-group-rows-the-scheme-and-simulation-set-collapse-from-region-rows-the-way-irp-integration-already-does-it) |
| T-03 | Group rows read the detail's `eventRateSchemes` / `simulationSets` property; `get_regions` is not used for groups; source selection is by payload content | Approved | [research](research.md#t-03--group-rows-the-details-additionalproperties-are-the-source) |
| T-04 | Simulation set label is PET name plus simulation periods; PET named through model version + `get_pet_metadata_exact` | Approved | [research](research.md#t-04--simulation-set-label-pet-name-and-simulation-periods-spec-wording-amended) |
| T-05 | Applied treaties from `search_analysis_treaties_paginated` stored as id, number, name, currency, occurrence limit, risk limit, attachment point and retention; the row shows number, name and currency; the plan item gains `treaty_names` | Approved | [research](research.md#t-05--treaties-applied-treaties-from-the-analysis-treaty-search-the-plan-item-records-the-requested-names) |
| T-06 | The single-analysis collapse lives in irp-integration as a describe method; the gateway wraps one call | Approved (placement) · method signature **Assumed** until the wheel exists | [research](research.md#t-06--the-single-analysis-collapse-lives-in-irp-integration) |
| T-07 | Capture in `finalize_analysis` and `backfill_rdm_analyses`; a failed describe read blanks and continues, never fails the job | Approved | [research](research.md#t-07--capture-points-and-the-failure-rule) |
| T-08 | One reader for `resolved` serves the expanded row and the Compare line; `_event_rate_scheme` and six dead display fields deleted | Approved | [research](research.md#t-08--one-reader-dead-fields-deleted) |
| T-09 | Expanded-row additions ship without a rendered preview | Assumed | [research](research.md#t-09--expanded-row-layout-no-preview) |

---

## Technical Context

**New dependencies**: irp-integration release carrying the describe method
(TestPyPI, pinned via `make irp-testpypi`); no new Python packages.
**Databases touched**: `rwb_workbench` only — JSON content of two existing
`irp_analysis` columns. DATABRIDGE untouched.

## Constitution Check

*GATE: before Phase 0 research, re-checked after Phase 1 design.*

Reviewed against all 13 articles in `.specify/memory/constitution.md`: no
violations.

Material interactions — where an article actively shapes this design:

- **Article 11 (IRP polling and result work behind an interface)**: every
  Risk Modeler read this feature adds runs in `finalize_analysis` or
  `backfill_rdm_analyses`, behind `irp_gateway`. Expanding a row reads
  `settings_metadata` only (FR-013); the unit tier asserts zero gateway calls
  on render.
- **Article 7 (one data-access package)**: the two writers reuse their
  existing UPDATE statements through `db`; the reader parses the column
  through `_parse_json_dict`. No new SQL path.
- **Article 12 (three test tiers)**: fixtures are the live captures, not
  knowledge-base shapes; the IRP tier gains one probe that reads the describe
  method against the sandbox for an own DLM, an own HD and a broker analysis.
- **Article 8 (server-rendered)**: the additions are Jinja entries in the
  existing `settings-grid`; no client code.
- **AGENTS.md rule 8 (approved plans are immutable)** shaped T-05: the plan
  item records `treaty_names` at claim so the row never re-derives what was
  requested.

Re-check after Phase 1: unchanged.

## Project Structure

```text
specs/015-analysis-run-details/
├── plan.md · research.md · data-model.md · quickstart.md
└── contracts/
    ├── settings-metadata-resolved.md   # the resolved key
    ├── irp-gateway.md                  # package describe method + gateway wrapper + FakeIRP
    └── captures/*.json                 # trimmed live payloads → unit fixtures

../irp-integration/irp_integration/analysis.py (or grouping.py)   # describe method (T-06), released to TestPyPI

app/services/irp_gateway.py            # describe_analysis_run + ResolvedRun types
app/services/analysis_service.py       # resolved reader; _event_rate_scheme and dead fields removed
app/workers/analysis_jobs.py           # finalize writes resolved; _claim_analysis stores treaty_names
app/workers/entity_jobs.py             # backfill writes resolved per broker analysis
app/templates/partials/analysis_results_inline.html   # grid entries
tests/unit/fakes/fake_irp.py           # describe_analysis_run fake + seeds
tests/unit/                            # fixtures from captures; worker, reader, template tests
tests/irp/                             # describe probe
docs/DATA_MODEL.md                     # §6 column semantics
```

## Complexity Tracking

Not needed — no violation to justify.

## Testing

- **Unit**: the collapse from describe output to `partitions` (23 sub-region
  rows → one entry; HD → one PLT entry with periods; unnamed id → `name`
  null); the group branch over each captured property shape (ELT group, PLT
  group, mixed RM-made group, broker INGP group); both writers' success and
  blank-and-continue paths against `FakeIRP`, including a describe failure
  that leaves the metadata write intact and the job successful; the plan item
  carrying `treaty_names`; the reader over every capture (ELT row shows
  scheme and no simulation set, PLT row the reverse, group lists both, no
  treaties → no Treaties entry, absent `resolved` → a Run details entry
  reading *not returned*, unnamed PET → `PET <id>`); the Compare modal
  line reading the same value as the expanded row; zero gateway calls on
  render.
- **SQL Server integration**: none added — no schema or SQL change. The
  existing tier is run once to confirm the JSON columns round-trip the new
  content on `NVARCHAR(MAX)`.
- **IRP sandbox**: one opt-in probe calling `describe_analysis_run` for
  analyses 5741781 (own DLM), 5733173 (own HD) and 5689560 (broker DLM),
  asserting scheme 739, PET 12 "RMS 2020 Time-Dependent Rates" with 1,978,459
  periods, and two treaties respectively — the fixtures' claims re-checked
  live. This tier does not run in CI; the plan says so when reporting.
