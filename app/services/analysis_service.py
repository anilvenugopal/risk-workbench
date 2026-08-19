"""Analysis service — the broker-analysis read models (spec 004 US3).

Surfaces the ``irp_analysis`` rows captured by ``backfill_rdm_analyses``:

  • ``list_broker_analyses(rdm_id)`` — the RDM page (FR-030/FR-031, R8):
    grouped by ``rdm_id``; each with its parsed
    ``settings_metadata`` (missing/partial → blank, never error) and
    ``is_group`` (FR-035).
  • ``list_edm_analyses(edm_id)`` — the context-free EDM page, which has no
    submission RDM context and therefore returns no groups.

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
from db import execute


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
    rdm_name: str | None         # source-RDM name
    edm_name: str | None         # representative handle's EDM
    edm_names: list[str] = field(default_factory=list)  # every EDM it spans
    is_group: bool = False
    settings: dict | None = None            # parsed raw snapshot (R2)
    display: AnalysisSettings = field(default_factory=AnalysisSettings)


@dataclass
class BrokerAnalysisGroup:
    """Analyses under one source-RDM divider (both pages render per-RDM groups)."""
    rdm_id: str
    rdm_name: str | None
    rdm_irp_id: Any
    status: str | None = None
    analysis_count: int = 0
    analyses: list[BrokerAnalysis] = field(default_factory=list)
    # A backfill_rdm_analyses head is pending/running for this RDM — the EDM
    # detail page's contextual broker-analyses section polls while any group
    # carries this, so an RDM's own capture finishing after the EDM's own
    # backfill still lands without a manual refresh.
    sync_running: bool = False

    @property
    def edm_count(self) -> int:
        return len({n for a in self.analyses for n in (a.edm_names or []) if n})



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


# One row per (RDM×EDM) handle.
_HANDLE_SELECT = """
    SELECT a.id, a.rdm_id, a.irp_id, a.name, a.is_group, a.settings_metadata,
           e.name AS edm_name,
           r.name AS rdm_name, r.irp_id AS rdm_irp_id
    FROM irp_analysis a
    LEFT JOIN irp_edm e ON e.id = a.edm_id
    LEFT JOIN irp_rdm r ON r.id = a.rdm_id
    WHERE a.deleted_at IS NULL AND a.rdm_id IS NOT NULL
"""


def _dedup_handles(rows: list[dict]) -> list[BrokerAnalysis]:
    """Collapse the M (RDM×EDM) handle rows sharing one ``irp_id`` into ONE
    display row (R8): the representative handle is the first seen;
    ``edm_names`` collects every EDM spanned; settings come from any handle that
    has them (the snapshot is per-analysis)."""
    out: list[BrokerAnalysis] = []
    by_key: dict[tuple, BrokerAnalysis] = {}
    for r in rows:
        key = (_uid(r["rdm_id"]), str(r["irp_id"]))
        existing = by_key.get(key)
        if existing is None:
            settings = _parse_settings(r["settings_metadata"])
            entry = BrokerAnalysis(
                id=_uid(r["id"]), irp_id=str(r["irp_id"]), name=r["name"],
                rdm_id=_uid(r["rdm_id"]), rdm_name=r["rdm_name"],
                edm_name=r["edm_name"],
                edm_names=[r["edm_name"]] if r["edm_name"] else [],
                is_group=bool(r["is_group"]), settings=settings,
                display=_to_display(settings))
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
    deduped across their M EDM handles (shown once), each with parsed settings
    and ``is_group``. No scoping (Article 6)."""
    rows = execute(
        f"{_HANDLE_SELECT} AND a.rdm_id = :r ORDER BY a.name, a.irp_id, a.id",
        {"r": str(rdm_id)}, connection="WORKBENCH")
    return _group_by_rdm([dict(r) for r in rows])


def list_edm_analyses(*, edm_id: Any) -> list[BrokerAnalysisGroup]:
    """Direct library EDM pages have no submission context and show no RDM list."""
    return []


def list_submission_rdms(*, submission_id: Any) -> list[BrokerAnalysisGroup]:
    """List one submission's RDMs and stored counts without loading analyses."""
    rows = execute(
        "SELECT r.id, r.name, r.irp_id, r.status, COUNT(a.id) AS analysis_count "
        "FROM submission_rdm sr JOIN irp_rdm r ON r.id = sr.rdm_id "
        "LEFT JOIN irp_analysis a ON a.rdm_id = r.id AND a.deleted_at IS NULL "
        "WHERE sr.submission_id = :submission_id AND r.deleted_at IS NULL "
        "GROUP BY r.id, r.name, r.irp_id, r.status, r.inserted_at "
        "ORDER BY r.inserted_at, r.name",
        {"submission_id": str(submission_id)}, connection="WORKBENCH")
    return [
        BrokerAnalysisGroup(
            rdm_id=_uid(row["id"]), rdm_name=row["name"],
            rdm_irp_id=row["irp_id"], status=row["status"],
            analysis_count=int(row["analysis_count"] or 0))
        for row in rows
    ]


def list_submission_rdm_analyses(
    *, submission_id: Any, rdm_id: Any,
) -> list[BrokerAnalysis] | None:
    """Return stored analyses when the RDM belongs to the named submission.

    ``None`` distinguishes an invalid association from an associated RDM with no
    captured analyses. The query never calls Risk Modeler.
    """
    associated = execute(
        "SELECT r.id FROM submission_rdm sr "
        "JOIN irp_rdm r ON r.id = sr.rdm_id "
        "WHERE sr.submission_id = :submission_id AND sr.rdm_id = :rdm_id "
        "AND r.deleted_at IS NULL",
        {"submission_id": str(submission_id), "rdm_id": str(rdm_id)},
        connection="WORKBENCH")
    if not associated:
        return None
    rows = execute(
        f"{_HANDLE_SELECT} AND a.rdm_id = :rdm_id "
        "ORDER BY a.name, a.irp_id, a.id",
        {"rdm_id": str(rdm_id)}, connection="WORKBENCH")
    return _dedup_handles([dict(row) for row in rows])


__all__ = [
    "AnalysisSettings", "BrokerAnalysis", "BrokerAnalysisGroup",
    "list_broker_analyses", "list_edm_analyses", "list_submission_rdms",
    "list_submission_rdm_analyses",
]
