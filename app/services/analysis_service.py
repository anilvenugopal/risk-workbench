"""Analysis service — the broker-analysis read models (spec 004 US3).

Surfaces the ``irp_analysis`` rows captured by ``backfill_rdm_analyses``
(broker ⇔ ``rdm_id`` set — DATA_MODEL §6; no stored origin column):

  • ``list_broker_analyses(rdm_id)`` — the RDM page (FR-030/FR-031, R8):
    grouped by ``rdm_id``, each with its parsed ``settings_metadata``
    (missing/partial → blank, never error) and ``is_group`` (FR-035).
  • ``list_edm_analyses(edm_id)`` — the EDM page (8/5 D15): every RDM in the
    EDM's package, each grouped with its analyses — including RDMs with none.
  • ``analysis_counts`` — un-empties the package-card / EDM counts (FR-050).

An analysis carries no ``edm_id``: the RDM is imported standalone, so no EDM is
named in Risk Modeler. Both EDM-facing reads therefore key on **package
membership** — the RDMs and EDMs that share a package.

**No analysis is attributed to a portfolio** (8/4 D8): there is no trustworthy
way to tie an RDM analysis to an EDM portfolio, and every analysis here is
broker-provided (``rdm_id`` NOT NULL). ``irp_analysis.exposure_resource_id`` is
still captured by the worker — it is defensible only for analyses CIC runs
itself — but nothing reads or displays it.

The curated ``AnalysisSettings`` view model reads the documented RM payload
fields defensively (``analysisType``/``engineType``/``engineVersion``/
``peril``/``subperil``/``region``/``currencyCode``/… — IRP knowledge base
2026-07-24); term / PLA / event-rate fields have NO documented source and stay
blank until the sandbox confirms their spelling (IRP_INTEGRATION_FOLLOWUPS.md).

Read-only; no loss numbers (FR-033); no row scoping (Article 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services._common import _parse_json_dict, _uid
from db import execute, execute_one


@dataclass
class AnalysisSettings:
    """The curated FR-031 settings view — every field blank-on-missing."""
    analysis_type: str | None = None
    analysis_mode: str | None = None
    engine_type: str | None = None
    engine_version: str | None = None
    peril: str | None = None
    peril_secondary: str | None = None
    region: str | None = None
    currency: str | None = None
    construction: str | None = None
    line_of_business: str | None = None
    term: str | None = None
    pla: str | None = None
    event_rate_scheme: str | None = None
    rate_vintage: str | None = None

    @property
    def engine(self) -> str | None:
        """The compact Engine column: ``DLM · 23.0`` (either half optional)."""
        parts = [p for p in (self.engine_type, self.engine_version) if p]
        return " · ".join(parts) if parts else None

    @property
    def has_rate_detail(self) -> bool:
        return bool(self.event_rate_scheme or self.rate_vintage or self.term)


@dataclass
class BrokerAnalysis:
    """One broker analysis."""
    id: str                      # workbench row id
    irp_id: str                  # Moody's analysisId
    name: str | None
    rdm_id: str
    rdm_name: str | None         # source-RDM name
    is_group: bool = False
    settings: dict | None = None            # parsed raw snapshot (R2)
    display: AnalysisSettings = field(default_factory=AnalysisSettings)


@dataclass
class BrokerAnalysisGroup:
    """Analyses under one source-RDM divider (both pages render per-RDM groups)."""
    rdm_id: str
    rdm_name: str | None
    rdm_irp_id: Any
    analyses: list[BrokerAnalysis] = field(default_factory=list)


@dataclass
class AnalysisCounts:
    """FR-050 — the populated counts (spec 003 D5 rendered these empty)."""
    total: int = 0
    rdm_count: int = 0


def _parse_settings(raw: Any) -> dict | None:
    return _parse_json_dict(raw, "settings_metadata")


def _first(payload: dict, *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return None


def _text(value: Any) -> str | None:
    """A display string from a defensive read: dicts collapse to their code/
    name (the live payload's ``currency`` object is keyed ``currencyCode``/
    ``currencyName`` — confirmed 2026-07-24); lists join, empty → blank
    (``eventRateSchemeNames``); bools to On/Off."""
    if value is None:
        return None
    if isinstance(value, dict):
        return (value.get("code") or value.get("name")
                or value.get("currencyCode") or value.get("currencyName")
                or None)
    if isinstance(value, (list, tuple)):
        parts = [t for t in (_text(v) for v in value) if t]
        return ", ".join(parts) or None
    if isinstance(value, bool):
        return "On" if value else "Off"
    return str(value)


def _to_display(settings: dict | None) -> AnalysisSettings:
    """The curated FR-031 view from the raw RM payload — documented camelCase
    fields first, plausible fallbacks second, blank when absent (US3 acc. 3)."""
    p = settings or {}
    return AnalysisSettings(
        analysis_type=_text(_first(p, "analysisType", "type")),
        analysis_mode=_text(_first(p, "analysisMode", "mode", "analysisFramework")),
        engine_type=_text(_first(p, "engineType")),
        engine_version=_text(_first(p, "engineVersion", "modelVersion")),
        peril=_text(_first(p, "peril", "perilCode")),
        peril_secondary=_text(_first(p, "subperil", "subPeril", "secondaryPeril")),
        region=_text(_first(p, "region", "regionCode")),
        currency=_text(_first(p, "currencyCode", "currencyName", "currency")),
        construction=_text(_first(p, "construction")),
        line_of_business=_text(_first(p, "lineOfBusiness", "lob")),
        term=_text(_first(p, "term", "timeDependency", "rateTimeDependency")),
        pla=_text(_first(p, "lossAmplification", "pla", "plaEnabled")),
        # eventRateSchemeNames (a LIST) is the live spelling (2026-07-24);
        # the scalar guesses stay first so a truthy scalar wins if both appear.
        event_rate_scheme=_text(_first(p, "eventRateScheme", "rateScheme",
                                       "eventRateSchemeNames")),
        rate_vintage=_text(_first(p, "rateVintage", "eventRateSchemeVersion")),
    )


# One row per captured analysis (UNIQUE(rdm_id, edm_id, irp_id), edm_id null).
_ANALYSIS_SELECT = """
    SELECT a.id, a.rdm_id, a.irp_id, a.name, a.is_group, a.settings_metadata,
           r.name AS rdm_name, r.irp_id AS rdm_irp_id
    FROM irp_analysis a
    LEFT JOIN irp_rdm r ON r.id = a.rdm_id
    WHERE a.deleted_at IS NULL AND a.rdm_id IS NOT NULL
"""


def _to_analysis(r: dict) -> BrokerAnalysis:
    settings = _parse_settings(r["settings_metadata"])
    return BrokerAnalysis(
        id=_uid(r["id"]), irp_id=str(r["irp_id"]), name=r["name"],
        rdm_id=_uid(r["rdm_id"]), rdm_name=r["rdm_name"],
        is_group=bool(r["is_group"]), settings=settings,
        display=_to_display(settings))


def _group_by_rdm(rows: list[dict]) -> list[BrokerAnalysisGroup]:
    analyses = [_to_analysis(r) for r in rows]
    rdm_meta = {_uid(r["rdm_id"]): (r["rdm_name"], r["rdm_irp_id"])
                for r in rows}
    groups: dict[str, BrokerAnalysisGroup] = {}
    ordered: list[BrokerAnalysisGroup] = []
    for a in analyses:
        g = groups.get(a.rdm_id)
        if g is None:
            name, irp = rdm_meta.get(a.rdm_id, (None, None))
            g = BrokerAnalysisGroup(rdm_id=a.rdm_id, rdm_name=name,
                                    rdm_irp_id=irp)
            groups[a.rdm_id] = g
            ordered.append(g)
        g.analyses.append(a)
    return ordered


def list_broker_analyses(*, rdm_id: Any) -> list[BrokerAnalysisGroup]:
    """The RDM page's read (FR-030/FR-031/R8): this RDM's broker analyses, each
    with parsed settings and ``is_group``. No scoping (Article 6)."""
    rows = execute(
        f"{_ANALYSIS_SELECT} AND a.rdm_id = :r ORDER BY a.name, a.irp_id, a.id",
        {"r": str(rdm_id)}, connection="WORKBENCH")
    return _group_by_rdm([dict(r) for r in rows])


def list_edm_analyses(*, edm_id: Any) -> list[BrokerAnalysisGroup]:
    """The EDM page's read (8/5 D15): every RDM in the EDM's package with its
    broker analyses — two EDMs and two RDMs means each EDM page lists both
    RDMs, and an RDM with no analyses still gets an (empty) group, because the
    point is Wendy's paired-book check: "were the same analyses run… if you
    have 12 analyses in one, you have 12 analyses in the other." Listing
    asserts no EDM↔RDM link (the RDMs merely share the package). A packageless
    EDM has no RDMs to list. Never attributed to a portfolio (8/4 D8). No
    scoping (Article 6)."""
    edm = execute_one("SELECT package_id FROM irp_edm WHERE id = :id",
                      {"id": str(edm_id)}, connection="WORKBENCH")
    package_id = edm["package_id"] if edm else None
    if not package_id:
        return []

    rows = execute(
        f"{_ANALYSIS_SELECT} AND r.package_id = :p AND r.deleted_at IS NULL "
        "ORDER BY r.inserted_at, a.rdm_id, a.name, a.irp_id",
        {"p": str(package_id)}, connection="WORKBENCH")
    by_rdm = {g.rdm_id: g for g in _group_by_rdm([dict(r) for r in rows])}
    rdms = execute(
        "SELECT id, name, irp_id FROM irp_rdm "
        "WHERE package_id = :p AND deleted_at IS NULL ORDER BY inserted_at",
        {"p": str(package_id)}, connection="WORKBENCH")
    ordered = [by_rdm.pop(_uid(r["id"]),
                          BrokerAnalysisGroup(rdm_id=_uid(r["id"]),
                                              rdm_name=r["name"],
                                              rdm_irp_id=r["irp_id"]))
               for r in rdms]
    ordered.extend(by_rdm.values())   # defensive — should be empty
    return ordered


def analysis_counts(*, edm_id: Any) -> AnalysisCounts:
    """FR-050: populated counts for one EDM (the package card renders these
    per member, the EDM detail directly). Counts the analyses of every RDM in
    the EDM's package — the same membership ``list_edm_analyses`` renders, so
    the badge and the list can never disagree. A packageless EDM counts zero."""
    rows = execute(
        """
        SELECT a.rdm_id, a.irp_id
        FROM irp_analysis a
        JOIN irp_rdm r ON r.id = a.rdm_id AND r.deleted_at IS NULL
        JOIN irp_edm e ON e.package_id = r.package_id
        WHERE a.deleted_at IS NULL AND e.id = :e
        """,
        {"e": str(edm_id)}, connection="WORKBENCH")
    # Counted app-side (no dialect string-concat in aggregates); the per-view
    # row count is small.
    keys = {(_uid(r["rdm_id"]), str(r["irp_id"])) for r in rows}
    return AnalysisCounts(total=len(keys),
                          rdm_count=len({k[0] for k in keys}))


__all__ = [
    "AnalysisSettings", "BrokerAnalysis", "BrokerAnalysisGroup",
    "AnalysisCounts", "list_broker_analyses", "list_edm_analyses",
    "analysis_counts",
]
