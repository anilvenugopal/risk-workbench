"""Analysis service — the broker-analysis read models (spec 004 US3).

Surfaces the ``irp_analysis`` rows captured by ``backfill_rdm_analyses``
(broker ⇔ ``rdm_id`` set — DATA_MODEL §6; no stored origin column):

  • ``list_broker_analyses(rdm_id)`` — the RDM page (FR-030/FR-031, R8):
    grouped by ``rdm_id`` so an analysis applied across M EDMs is shown ONCE
    (the M pair-rows are handles sharing one ``irp_id``); each with its parsed
    ``settings_metadata`` (missing/partial → blank, never error), ``is_group``
    (FR-035), and the read-time-resolved portfolio (FR-036/R9).
  • ``list_edm_analyses(edm_id)`` — the EDM page (FR-037): the same rows
    scoped to one EDM, grouped by source RDM; ``bucket_by_portfolio`` feeds
    the per-portfolio inline panels (group/unresolved stay standalone-only).
  • ``analysis_counts`` — un-empties the package-card / EDM counts (FR-050).

**Portfolio linkage is derived at read time** (R9): a ``LEFT JOIN
irp_portfolio ON edm_id + exposure_resource_id ↔ irp_id`` — never a stored FK,
so resolution is import-order safe and self-heals on re-import. Display
precedence (ui.md §4): ``is_group`` → "Group"; resolved → portfolio link;
else → "— not linked".

The curated ``AnalysisSettings`` view model reads the documented RM payload
fields defensively (``analysisType``/``engineType``/``engineVersion``/
``peril``/``subperil``/``region``/``currencyCode``/… — IRP knowledge base
2026-07-24); term / PLA / event-rate fields have NO documented source and stay
blank until the sandbox confirms their spelling (IRP_INTEGRATION_FOLLOWUPS.md).

Read-only; no loss numbers (FR-033); no row scoping (Article 6).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.services._common import _uid
from db import execute

logger = logging.getLogger(__name__)


@dataclass
class PortfolioRef:
    """The resolved owning portfolio (id + name) — a link target, not a model."""
    id: str
    name: str


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
    """One broker analysis (deduped across its M (RDM×EDM) handle rows)."""
    id: str                      # workbench row id of the representative handle
    irp_id: str                  # Moody's analysisId
    name: str | None
    rdm_id: str
    rdm_name: str | None         # source-RDM name (the mini panel's RDM cell)
    edm_id: str | None           # representative handle's EDM
    edm_name: str | None
    edm_names: list[str] = field(default_factory=list)  # every EDM it spans
    is_group: bool = False
    settings: dict | None = None            # parsed raw snapshot (R2)
    display: AnalysisSettings = field(default_factory=AnalysisSettings)
    exposure_resource_id: str | None = None
    portfolio: PortfolioRef | None = None   # resolved (R9); None ⇒ Group / not linked


@dataclass
class BrokerAnalysisGroup:
    """Analyses under one source-RDM divider (both pages render per-RDM groups)."""
    rdm_id: str
    rdm_name: str | None
    rdm_irp_id: Any
    analyses: list[BrokerAnalysis] = field(default_factory=list)

    @property
    def edm_count(self) -> int:
        return len({n for a in self.analyses for n in (a.edm_names or []) if n})


@dataclass
class AnalysisCounts:
    """FR-050 — the populated counts (spec 003 D5 rendered these empty)."""
    total: int = 0
    rdm_count: int = 0
    linked: int = 0


def _parse_settings(raw: Any) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("unparseable settings_metadata snapshot — rendering blank")
        return None
    return parsed if isinstance(parsed, dict) else None


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


# One row per (RDM×EDM) handle + its read-time-resolved portfolio (R9): the
# LEFT JOIN keys on the SAME edm_id + the captured RM pointer — never a stored
# FK, so it is import-order safe and self-heals on re-import.
_HANDLE_SELECT = """
    SELECT a.id, a.rdm_id, a.edm_id, a.irp_id, a.name, a.is_group,
           a.settings_metadata, a.exposure_resource_id,
           e.name AS edm_name,
           r.name AS rdm_name, r.irp_id AS rdm_irp_id,
           pf.id AS portfolio_id, pf.name AS portfolio_name
    FROM irp_analysis a
    LEFT JOIN irp_edm e ON e.id = a.edm_id
    LEFT JOIN irp_rdm r ON r.id = a.rdm_id
    LEFT JOIN irp_portfolio pf
           ON pf.edm_id = a.edm_id
          AND pf.irp_id = a.exposure_resource_id
          AND pf.deleted_at IS NULL
    WHERE a.deleted_at IS NULL AND a.rdm_id IS NOT NULL
"""


def _dedup_handles(rows: list[dict]) -> list[BrokerAnalysis]:
    """Collapse the M (RDM×EDM) handle rows sharing one ``irp_id`` into ONE
    display row (R8): the representative handle is the first that resolves a
    portfolio (else the first seen); ``edm_names`` collects every EDM spanned;
    settings come from any handle that has them (the snapshot is per-analysis)."""
    out: list[BrokerAnalysis] = []
    by_key: dict[tuple, BrokerAnalysis] = {}
    for r in rows:
        key = (_uid(r["rdm_id"]), str(r["irp_id"]))
        resolved = (PortfolioRef(id=_uid(r["portfolio_id"]),
                                 name=r["portfolio_name"])
                    if r["portfolio_id"] is not None and not r["is_group"]
                    else None)
        existing = by_key.get(key)
        if existing is None:
            settings = _parse_settings(r["settings_metadata"])
            entry = BrokerAnalysis(
                id=_uid(r["id"]), irp_id=str(r["irp_id"]), name=r["name"],
                rdm_id=_uid(r["rdm_id"]), rdm_name=r["rdm_name"],
                edm_id=_uid(r["edm_id"]),
                edm_name=r["edm_name"],
                edm_names=[r["edm_name"]] if r["edm_name"] else [],
                is_group=bool(r["is_group"]), settings=settings,
                display=_to_display(settings),
                exposure_resource_id=r["exposure_resource_id"],
                portfolio=resolved)
            by_key[key] = entry
            out.append(entry)
            continue
        if r["edm_name"] and r["edm_name"] not in existing.edm_names:
            existing.edm_names.append(r["edm_name"])
        if existing.settings is None:
            settings = _parse_settings(r["settings_metadata"])
            if settings is not None:
                existing.settings = settings
                existing.display = _to_display(settings)
        if existing.portfolio is None and resolved is not None:
            # prefer the handle that actually resolves (ui.md §4 link cell)
            existing.portfolio = resolved
            existing.edm_id = _uid(r["edm_id"])
            existing.edm_name = r["edm_name"]
    return out


def _group_by_rdm(rows: list[dict]) -> list[BrokerAnalysisGroup]:
    analyses = _dedup_handles(rows)
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
    """The RDM page's read (FR-030/FR-031/R8): this RDM's broker analyses,
    deduped across their M EDM handles (shown once), each with parsed settings,
    ``is_group``, and the resolved portfolio. No scoping (Article 6)."""
    rows = execute(
        f"{_HANDLE_SELECT} AND a.rdm_id = :r ORDER BY a.name, a.irp_id, a.id",
        {"r": str(rdm_id)}, connection="WORKBENCH")
    return _group_by_rdm([dict(r) for r in rows])


def list_edm_analyses(*, edm_id: Any) -> list[BrokerAnalysisGroup]:
    """The EDM page's read (FR-037): this EDM's broker analyses grouped by
    source RDM (divider rows), each with its resolved portfolio. Feeds both the
    standalone section and — via ``bucket_by_portfolio`` — the per-portfolio
    inline panels. No scoping (Article 6)."""
    rows = execute(
        f"{_HANDLE_SELECT} AND a.edm_id = :e "
        "ORDER BY r.inserted_at, a.rdm_id, a.name, a.irp_id",
        {"e": str(edm_id)}, connection="WORKBENCH")
    return _group_by_rdm([dict(r) for r in rows])


def bucket_by_portfolio(
        groups: list[BrokerAnalysisGroup]) -> dict[str, list[BrokerAnalysis]]:
    """The R9 bucketing for the inline panels (ui.md §4): ONLY clearly-linked
    analyses land in a portfolio bucket — ``is_group`` and unresolved rows stay
    standalone-only. Keyed by the workbench ``irp_portfolio.id``."""
    buckets: dict[str, list[BrokerAnalysis]] = {}
    for g in groups:
        for a in g.analyses:
            if a.portfolio is not None and not a.is_group:
                buckets.setdefault(a.portfolio.id, []).append(a)
    return buckets


def analysis_counts(*, package_id: Any | None = None,
                    edm_id: Any | None = None) -> AnalysisCounts:
    """FR-050: populated counts for the package card / EDM detail. ``total``
    dedups on (rdm_id, irp_id) — one per broker analysis, not per handle;
    ``linked`` counts distinct analyses whose pointer resolves (non-group)."""
    where = "a.deleted_at IS NULL AND a.rdm_id IS NOT NULL"
    params: dict[str, Any] = {}
    if package_id is not None:
        where += " AND a.package_id = :p"
        params["p"] = str(package_id)
    if edm_id is not None:
        where += " AND a.edm_id = :e"
        params["e"] = str(edm_id)
    rows = execute(
        f"""
        SELECT a.rdm_id, a.irp_id, a.is_group,
               CASE WHEN pf.id IS NOT NULL THEN 1 ELSE 0 END AS resolved
        FROM irp_analysis a
        LEFT JOIN irp_portfolio pf
               ON pf.edm_id = a.edm_id
              AND pf.irp_id = a.exposure_resource_id
              AND pf.deleted_at IS NULL
        WHERE {where}
        """,
        params, connection="WORKBENCH")
    # Dedup app-side on (rdm_id, irp_id) — the handle → analysis collapse (R8);
    # an analysis is 'linked' when ANY handle resolves. Portable (no dialect
    # string-concat in aggregates); the per-view row count is small.
    linked_by_key: dict[tuple, bool] = {}
    for r in rows:
        key = (_uid(r["rdm_id"]), str(r["irp_id"]))
        is_linked = bool(r["resolved"]) and not bool(r["is_group"])
        linked_by_key[key] = linked_by_key.get(key, False) or is_linked
    return AnalysisCounts(
        total=len(linked_by_key),
        rdm_count=len({k[0] for k in linked_by_key}),
        linked=sum(1 for v in linked_by_key.values() if v))


__all__ = [
    "PortfolioRef", "AnalysisSettings", "BrokerAnalysis", "BrokerAnalysisGroup",
    "AnalysisCounts", "list_broker_analyses", "list_edm_analyses",
    "bucket_by_portfolio", "analysis_counts",
]
