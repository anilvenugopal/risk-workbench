# Contract — package and gateway additions (spec 015, T-06)

## irp-integration: `analysis.describe_run(analysis_id)` — Assumed until released

A single-analysis read that reuses `GroupingManager._inspect`'s region, PET
and scheme-naming code (`irp_integration/grouping.py:686–960`) and adds the
treaty name `GroupingTreaty` drops. Name and placement are the approver's
call at package time; the shape below is what the workbench needs.

Calls, all reads: `analysis.get_analysis_by_id`, `analysis.get_regions`,
`analysis.search_analysis_treaties_paginated`, and for naming
`reference_data.get_event_rate_schemes`,
`reference_data.get_model_version_by_engine_region_peril`,
`reference_data.get_pet_metadata_exact` — the same calls `_inspect` makes per
member, with the same per-call caches.

Returns one frozen dataclass:

```python
RunDescription(
    analysis_id: int,
    is_group: bool,                       # isGroup, or a detail carrying eventRateSchemes / simulationSets
    regions: tuple[GroupingRegionFact, ...],   # the existing type: framework, peril_code, region_code,
                                          #   model_version, engine_version, event_rate_scheme_id,
                                          #   pet_id, pet_name, periods — one per region row, NOT collapsed
    event_rate_scheme_names: Mapping[int, str],  # id → name for every scheme id in regions
    treaties: tuple[AppliedTreaty, ...],  # treaty_id, treaty_number, treaty_name, plus the
                                          #   loss-affecting terms GroupingTreaty already normalizes
)
```

Rules the package keeps: a region row's `peril` is a display name and the
detail's `perilCode`/`regionCode` supply the codes; a PLT `petId` is named
only through `get_pet_metadata_exact` with the model version (PET 12 exists
for 2.0 and 3.0 with different names); an unnamed id yields `pet_name`
`None`, never a `SimulationSet` row. A 404 on the analysis raises; a missing
region list returns empty `regions`; treaty and reference failures raise
`IRPAPIError` so the caller applies its own blank-and-continue rule.

Release path: change in `../irp-integration`, tests with the workbench venv
(`PYTHONPATH=.`), regenerate `docs/api.md`, tag, TestPyPI, then
`make irp-testpypi` here and re-confirm the signature against the wheel.

## Workbench gateway: `describe_analysis_run(*, analysis_id: int) -> ResolvedRun`

Worker-only (Article 11). Protocol + `_RealGateway` + module function in
`app/services/irp_gateway.py`, mirrored in `tests/unit/fakes/fake_irp.py`.
Wraps `describe_run` and performs the collapse the package leaves to the
caller:

```python
@dataclass(frozen=True)
class ResolvedPartition:
    region_code: str
    peril_code: str
    framework: str                 # ELT | PLT
    event_rate_scheme_id: int | None
    event_rate_scheme_name: str | None
    simulation_set_id: int | None  # the PET id on a PLT partition
    simulation_set_name: str | None
    periods: int | None

@dataclass(frozen=True)
class AppliedTreaty:
    treaty_id: int | None
    number: str
    name: str | None

@dataclass(frozen=True)
class ResolvedRun:
    partitions: tuple[ResolvedPartition, ...]   # distinct (region, peril, framework), sorted by region_code, peril_code
    treaties: tuple[AppliedTreaty, ...]         # one per treaty_id, sorted by number (P-06, P-07)
```

The group branch does not call the gateway for partitions: the worker builds
them from the detail's property
([settings-metadata-resolved.md](settings-metadata-resolved.md)) and calls
`describe_analysis_run` for the treaties only. If the package method proves
expensive for that use, a treaty-only wrapper over
`search_analysis_treaties_paginated` is the fallback — decided at
implementation, not here.

## FakeIRP

- `add_analysis(...)` gains `regions=`, `treaties=`, `scheme_names=`,
  `pet_names=` seeds shaped like the captures; `describe_analysis_run`
  returns the collapsed `ResolvedRun` from them.
- `raise_on_describe_run` forces the failure path for the
  blank-and-continue tests (FR-014).
- Records calls for the "expanding a row calls nothing" assertion (FR-013): a
  page render makes zero gateway calls.
