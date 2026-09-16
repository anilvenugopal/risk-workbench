"""The spec-015 live captures as unit fixtures.

Each file in ``specs/015-analysis-run-details/contracts/captures/`` holds one
analysis's trimmed ``get_analysis_by_id``, ``get_regions`` and
``search_analysis_treaties_paginated`` responses, read from the sandbox tenant
on 2026-09-11. The region list is trimmed to one row per distinct combination;
the ``_note`` key on a row records how many rows the live response carried and
which ``subRegion`` codes they differed by. ``sub_region_rows`` rebuilds that
fan-out for a test that needs the collapse to have something to collapse.

``reference_data`` and ``search_analyses_item`` carry no analysis of their own
and are read through the named constants at the bottom.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.services.irp_gateway import GroupingRegionFact, collapse_run_description, resolved_payload

_CAPTURE_DIR = (Path(__file__).resolve().parents[2] / "specs"
                / "015-analysis-run-details" / "contracts" / "captures")

ANALYSIS_CAPTURES = (
    "own_dlm", "own_hd", "broker_dlm", "broker_dlm_no_treaties",
    "broker_group_ingp", "group_mixed_rm_made", "group_elt_workbench_made",
    "group_plt_workbench_made",
)
CAPTURE_NAMES = ANALYSIS_CAPTURES + ("reference_data", "search_analyses_item")


@dataclass(frozen=True)
class Capture:
    """One analysis's three captured responses."""
    name: str
    analysis_id: int | None
    detail: dict
    regions: list[dict]
    treaties: list[dict]


def _load(name: str) -> dict:
    return json.loads((_CAPTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _without_note(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "_note"}


def capture(name: str) -> Capture:
    raw = _load(name)
    return Capture(
        name=name,
        analysis_id=raw.get("_analysis_id"),
        detail=raw.get("get_analysis_by_id") or {},
        regions=[_without_note(r) for r in raw.get("get_regions") or []],
        treaties=list(raw.get("search_analysis_treaties_paginated") or []),
    )


def detail(name: str) -> dict:
    return capture(name).detail


def regions(name: str) -> list[dict]:
    return capture(name).regions


def treaties(name: str) -> list[dict]:
    return capture(name).treaties


def sub_region_rows(row: dict, count: int) -> list[dict]:
    """``count`` copies of a captured region row, one per sub-region — the live
    fan-out the capture trimmed to a single row. The captured row keeps its own
    ``subRegion``; the rest carry placeholder codes, since the collapse reads
    region, peril and framework and never the sub-region."""
    rows = [_without_note(row)]
    rows += [{**rows[0], "subRegion": f"S{n:02d}"} for n in range(1, count)]
    return rows


@dataclass(frozen=True)
class CapturedTreaty:
    """One applied treaty as the package's describe method returns it: the name
    ``GroupingTreaty`` drops, plus the terms it normalizes (``currency`` already
    collapsed to its code)."""
    treaty_id: int | None
    treaty_number: str
    treaty_name: str | None
    terms: dict


@dataclass(frozen=True)
class CapturedRun:
    """One capture as the package's ``describe_run`` returns it — region facts
    per region row, uncollapsed, plus the scheme names and the applied
    treaties. The package's own tests prove it builds this from the three
    responses; the workbench tier starts from it."""
    analysis_id: int | None
    is_group: bool
    regions: tuple[GroupingRegionFact, ...]
    event_rate_scheme_names: dict[int, str]
    treaties: tuple[CapturedTreaty, ...]


_GROUP_PROPERTY_KEYS = ("eventRateSchemes", "simulationSets")


def _is_group(detail: dict) -> bool:
    if detail.get("isGroup"):
        return True
    return any(p.get("key") in _GROUP_PROPERTY_KEYS
               for p in detail.get("additionalProperties") or [])


def _positive(value) -> int | None:
    return int(value) if isinstance(value, int) and value > 0 else None


def _region_fact(row: dict, detail: dict, *, pet_names: dict[int, str],
                 analysis_id: int) -> GroupingRegionFact:
    framework = row["framework"]
    # A region row's ``peril`` is the display name the detail also carries
    # beside its code; ``region`` is already a code.
    peril_code = (detail.get("perilCode") if row.get("peril") == detail.get("peril")
                  else row.get("peril"))
    region_code = row["region"]
    sub_region = row.get("subRegion") or ""
    engine_version = row.get("engineVersion") or detail.get("engineVersion")
    pet_id = _positive(row.get("petId"))
    return GroupingRegionFact(
        analysis_id=analysis_id, framework=framework, peril_code=peril_code,
        region_code=region_code,
        model_version=MODEL_VERSIONS.get(
            f"{engine_version}/{region_code}/{peril_code}", engine_version),
        engine_version=engine_version, sub_region=sub_region,
        model_region_code=f"{sub_region or region_code}{peril_code}",
        event_rate_scheme_id=(_positive(row.get("eventRateSchemeId"))
                              if framework == "ELT" else None),
        pet_id=pet_id, pet_name=(pet_names.get(pet_id) if pet_id else None),
        periods=(_positive(row.get("periods")) if framework == "PLT" else None),
        apply_contract_flag=bool(row.get("applyContractFlag")))


_TREATY_TERM_KEYS = ("occurrenceLimit", "riskLimit", "attachmentPoint",
                     "retentionAmount")


def _captured_treaty(row: dict) -> CapturedTreaty:
    terms = {key: row.get(key) for key in _TREATY_TERM_KEYS}
    terms["currency"] = (row.get("currency") or {}).get("code")
    return CapturedTreaty(treaty_id=row.get("treatyId"),
                          treaty_number=row["treatyNumber"],
                          treaty_name=row.get("treatyName"), terms=terms)


def captured_run(name: str, *, scheme_names: dict[int, str] | None = None,
                 pet_names: dict[int, str] | None = None,
                 fan_out: int = 1) -> CapturedRun:
    """One capture as ``describe_run`` returns it. ``fan_out`` repeats each
    captured region row that many times over distinct sub-regions, the way the
    live response fans out over states. ``scheme_names`` and ``pet_names``
    default to what reference data named for every id the captures carry; pass
    ``{}`` for the unnamed-id case."""
    c = capture(name)
    pet_names = PET_NAMES if pet_names is None else pet_names
    scheme_names = SCHEME_NAMES if scheme_names is None else scheme_names
    rows = [row for region in c.regions
            for row in sub_region_rows(region, fan_out)]
    return CapturedRun(
        analysis_id=c.analysis_id, is_group=_is_group(c.detail),
        regions=tuple(_region_fact(row, c.detail, pet_names=pet_names,
                                   analysis_id=c.analysis_id) for row in rows),
        event_rate_scheme_names=dict(scheme_names),
        treaties=tuple(_captured_treaty(t) for t in c.treaties))


# The names reference data returns for the scheme ids the captures carry, read
# live from get_event_rate_schemes on 2026-09-11 (the capture keeps 3 of its 151
# rows). The triple space in 163's name is Risk Modeler's own.
SCHEME_NAMES = {
    163: "RMS 17.0 NA   Stochastic Event Rates",
    577: "RMS 2023 Historical Event Rates",
    578: "RMS 2023 Stochastic Event Rates",
    738: "RMS 2025 Historical Event Rates",
    739: "RMS 2025 Stochastic Event Rates",
}

# The PETMetadata names for the PET ids the captures carry. 12 is the NZ EQ PET
# own_hd ran on, named through model version 3.0 — id 12 also exists for 2.0
# under a different name (T-04). 14 and 15 are the JP WS PETs the group captures
# name in their ``simulationSets`` property.
PET_NAMES = {
    12: "RMS 2020 Time-Dependent Rates",
    14: "RMS V2.0 Stochastic Event Rates - Typhoon and Non-Typhoon Flood Events",
    15: "RMS V2.0 Stochastic Event Rates - Typhoon Events Only",
}

_reference = _load("reference_data")
# get_event_rate_schemes().items and get_all_pet_metadata() rows, three of each.
EVENT_RATE_SCHEME_ROWS = _reference["get_event_rate_schemes.items (3 of 151)"]
PET_METADATA_ROWS = _reference[
    "get_all_pet_metadata (3 of 2844; id 12 occurs for modelVersionCode 2.0"
    " and 3.0 with different petName)"]
# Both PETMetadata rows for id 12 — the pair that makes the model-version
# qualifier necessary (T-04).
PET_12_ROWS = _reference["pet_12_rows"]
# "<engineVersion>/<regionCode>/<perilCode>" -> modelVersionCode
MODEL_VERSIONS = _reference["get_model_version_by_engine_region_peril"]

SEARCH_ANALYSES_ITEM = _load("search_analyses_item")[
    "search_analyses item (broker)"]


def settings_metadata(name: str, **kwargs) -> dict:
    """One capture's ``get_analysis_by_id`` response with the ``resolved`` key
    the workers write beside it — the reader tests start from the document the
    writer produces. ``kwargs`` reach ``captured_run``."""
    return {**detail(name),
            "resolved": resolved_payload(
                collapse_run_description(captured_run(name, **kwargs)))}
