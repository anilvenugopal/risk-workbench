"""The Risk Modeler (IRP) interface — the ONLY module that imports irp-integration.

Article 11: every Risk Modeler call goes through this thin gateway so the poller
and workers can be unit-tested against a fake (Article 12). The web layer only
ever reaches the *submit* / *search* methods indirectly, via services that enqueue
workers — it never calls the ``get_*`` status checks or any result retrieval.

**Single-status-check only.** ``get_*_job`` maps to one status read; the blocking
``poll_*_to_completion`` helpers are NEVER wrapped here (they run for minutes and
are forbidden everywhere — Article 11).

**Version churn is quarantined here.** ``irp-integration`` is pre-release and its
signatures move; it is source-switchable across PyPI / TestPyPI / a local checkout
(``make irp-pypi | irp-testpypi | irp-local``, research R1). Re-confirming a method
signature against the active wheel is a one-file edit, and the CI fake
(``tests/unit/fakes/fake_irp.py``) implements the same ``IRPGateway`` protocol, so a
signature change never scatters across services.

Injection: tests call ``configure(FakeIRP())``; production code calls the module
free functions (``submit_edm_import(...)`` etc.), which delegate to the active
implementation — the real, ``IRPClient``-backed one by default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

# Repo-owned, read-only DataBridge aggregate scripts (set-based, one row set
# covering every portfolio in the EDM) — executed through the wheel's generic
# DataBridge executor by get_edm_exposure_summary below.
_DATABRIDGE_SQL_DIR = Path(__file__).resolve().parents[2] / "sql" / "databridge"

# A free-text descriptor with more distinct values than this is not saved into
# the stored summary (8/4 D15 — lines of business is the known case).
_FREE_TEXT_STORAGE_CAP = 500


# ── Result value objects (gateway-owned; independent of the wheel's shapes) ──────

@dataclass(frozen=True)
class SubmitResult:
    """The outcome of a submit_* call. ``irp_id`` is the Risk Modeler job id as a
    string; ``resource_uri`` is captured at submit time because the completion
    response omits it (R1). ``payload``/``response`` are stored for audit."""
    irp_id: str
    resource_uri: str | None = None
    payload: dict = field(default_factory=dict)
    response: dict = field(default_factory=dict)


@dataclass(frozen=True)
class JobStatus:
    """A single-status-check result. ``status`` mirrors the Risk Modeler vocabulary
    verbatim (plain string — Article 3 carve-out); ``result`` carries the terminal
    completion body when present."""
    status: str
    result: dict | None = None


@dataclass(frozen=True)
class EntityHit:
    """A name-search hit used for the blocking collision check (R8 as amended
    2026-07-27 — issue #17)."""
    irp_id: str
    name: str


EdmHit = EntityHit
RdmHit = EntityHit


@dataclass(frozen=True)
class EdmCatalogEntry:
    """One EDM as Risk Modeler lists it, for the "sync existing EDMs" page.
    ``irp_id`` is the exposureId and ``server_name`` is the DataBridge server the
    adopt writes to ``irp_edm``; the rest is display-only and read defensively
    (they are RM's own names)."""
    irp_id: str
    name: str
    status: str | None = None
    server_name: str | None = None
    portfolio_count: int | None = None
    treaty_count: int | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class AnalysisHit:
    """One broker analysis returned by ``search_analyses`` (D2). ``analysis_id`` is
    Moody's ``analysisId`` as a string. The source names are echoed back so the
    backfill worker can persist lineage on ``irp_analysis``.

    Spec 004 (R9/FR-036): the hit now carries RM's exposure pointer —
    ``exposure_resource_id`` + ``exposure_resource_type`` (previously dropped) — so
    the backfill worker can promote the portfolio pointer to
    ``irp_analysis.exposure_resource_id`` when the type is ``PORTFOLIO``."""
    analysis_id: str
    name: str | None = None
    source_rdm_name: str | None = None
    exposure_name: str | None = None
    exposure_resource_id: str | None = None
    exposure_resource_type: str | None = None


# ── Detail-read value objects (spec 004 — R1/R2; typed hand-off, not a model) ────

@dataclass(frozen=True)
class PortfolioHit:
    """One portfolio enumerated within an EDM. ``irp_id`` is RM's portfolioId as a
    string; the exposure figures come separately via ``get_portfolio_exposure``."""
    irp_id: str
    name: str


@dataclass(frozen=True)
class ExposureDetail:
    """One portfolio's exposure figures — RM's ``/portfolios/{id}/metrics`` payload
    **verbatim** (stored as the ``irp_portfolio.exposure_detail`` JSON snapshot, R2;
    read defensively — field names are RM's own)."""
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TreatyDetail:
    """One treaty on an EDM: identity + the full attribute map **verbatim** (stored
    as the ``irp_treaty.attributes`` JSON snapshot, R2)."""
    irp_id: str | None
    name: str
    attributes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisMetadata:
    """One analysis's settings/metadata payload **verbatim** (stored as the
    ``irp_analysis.settings_metadata`` JSON snapshot), plus the typed exposure
    pointer the R9 linkage promotes when the type is ``PORTFOLIO`` and the
    group marker (FR-035). ``is_group`` is derived HERE from RM's payload so
    the how-to-detect-a-group question lives in one file — the exact marker
    field is unconfirmed against the sandbox (IRP_INTEGRATION_FOLLOWUPS.md)."""
    payload: dict = field(default_factory=dict)
    exposure_resource_id: str | None = None
    exposure_resource_type: str | None = None
    is_group: bool = False


# ── The interface the poller/workers depend on (fake implements it in CI) ────────

@runtime_checkable
class IRPGateway(Protocol):
    def submit_edm_import(self, *, name: str, source_file_path: str) -> SubmitResult: ...

    def submit_rdm_import(self, *, name: str,
                          source_file_path: str) -> SubmitResult: ...

    def search_analyses(self, *, source_rdm_name: str,
                        exposure_name: str | None = None) -> list[AnalysisHit]: ...

    def get_import_job(self, irp_id: str) -> JobStatus: ...

    def search_edms(self, name: str) -> list[EntityHit]: ...

    def search_rdms(self, name: str) -> list[EntityHit]: ...

    def list_edms(self) -> list[EdmCatalogEntry]: ...

    # ── spec-004 detail reads (worker-only; single-item, loop app-side) ──────────

    def list_portfolios(self, *, edm_irp_id: int) -> list[PortfolioHit]: ...

    def get_portfolio_exposure(self, *, edm_irp_id: int,
                               portfolio_irp_id: int) -> ExposureDetail: ...

    def get_edm_exposure_summary(self, *, edm_name: str,
                                 edm_irp_id: int) -> dict[str, dict]: ...

    def search_treaties(self, *, edm_irp_id: int) -> list[TreatyDetail]: ...

    def get_analysis_metadata(self, *, analysis_id: int) -> AnalysisMetadata: ...


# ── The real implementation — imports irp-integration lazily ─────────────────────

class _RealGateway:
    """Thin wrapper over ``irp-integration`` 0.2.0 (manager-based). ``IRPClient()``
    reads all config from env vars — no constructor args. The library is imported
    lazily (inside ``_client``) so importing this module never requires the wheel;
    unit tests inject a fake and never construct this class.

    Every call maps to exactly one manager method — all single-status-check;
    ``poll_*_to_completion`` is never wrapped (Article 11). Method signatures were
    re-confirmed against the active 0.2.0 wheel; re-confirm before trusting a new
    source (``make irp-status``) since the wheel is pre-release (R1).
    """

    def __init__(self) -> None:
        self._irp = None

    def _client(self):
        if self._irp is None:
            from irp_integration import IRPClient  # noqa: PLC0415 — lazy by design
            self._irp = IRPClient()
        return self._irp

    # ── submits — unit of work is the submit; irp_id is the RM job id (R1) ────────

    def submit_edm_import(self, *, name: str, source_file_path: str) -> SubmitResult:
        from app.config import settings  # noqa: PLC0415 — lazy: keep imports minimal
        job_id, body = self._client().edm.submit_edm_import_job(
            edm_name=name, edm_file_path=source_file_path,
            server_name=settings.irp_edm_import_server)
        return SubmitResult(irp_id=str(job_id),
                            resource_uri=body.get("resourceUri"), payload=body)

    def submit_rdm_import(self, *, name: str,
                          source_file_path: str) -> SubmitResult:
        job_id, body = self._client().rdm.submit_rdm_import_job(
            rdm_name=name,
            rdm_file_path=source_file_path,
            exposure_set_name=name,
        )
        return SubmitResult(irp_id=str(job_id),
                            resource_uri=body.get("resourceUri"), payload=body)

    def search_analyses(self, *, source_rdm_name: str,
                        exposure_name: str | None = None) -> list[AnalysisHit]:
        # Build the pair filter here with json.dumps quoting (mirrors search_edms /
        # search_rdms) so a name with a quote/space can never malform the filter —
        # the callers pass raw names, never a pre-built filter string.
        filter = f"sourceRdmName={json.dumps(source_rdm_name)}"
        if exposure_name is not None:
            filter += f" AND exposureName={json.dumps(exposure_name)}"
        # Paginated so the capture includes every analysis for the RDM.
        rows = self._client().analysis.search_analyses_paginated(filter=filter)
        return [
            AnalysisHit(
                analysis_id=str(r["analysisId"]),
                name=r.get("analysisName"),
                source_rdm_name=r.get("sourceRdmName"),
                exposure_name=r.get("exposureName"),
                # R9: carry RM's exposure pointer (previously dropped) so the
                # backfill can promote it when the type is PORTFOLIO.
                exposure_resource_id=(
                    str(r["exposureResourceId"])
                    if r.get("exposureResourceId") is not None else None),
                exposure_resource_type=r.get("exposureResourceType"))
            for r in rows if r.get("analysisId") is not None
        ]

    # ── spec-004 detail reads (worker-only; single-item, loop app-side — R1) ──────

    def list_portfolios(self, *, edm_irp_id: int) -> list[PortfolioHit]:
        # GET /platform/riskdata/v1/exposures/{exposureId}/portfolios (paginated so a
        # 25-portfolio EDM enumerates completely). Field names read defensively —
        # the wheel is pre-release (R1).
        rows = self._client().portfolio.search_portfolios_paginated(edm_irp_id)
        hits: list[PortfolioHit] = []
        for r in rows:
            pid = r.get("id") if r.get("id") is not None else r.get("portfolioId")
            name = r.get("name") or r.get("portfolioName")
            if pid is None or not name:
                continue
            hits.append(PortfolioHit(irp_id=str(pid), name=str(name)))
        return hits

    def get_portfolio_exposure(self, *, edm_irp_id: int,
                               portfolio_irp_id: int) -> ExposureDetail:
        # GET /platform/riskdata/v1/exposures/{exposureId}/portfolios/{id}/metrics —
        # needs BOTH ids (confirmed vs wheel 0.2.1; IRP_INTEGRATION_FOLLOWUPS.md).
        # Payload stored verbatim as the JSON snapshot (R2). A non-dict response
        # is a FAILED read, never an empty success — the worker's per-portfolio
        # except must skip it rather than overwrite a prior good snapshot.
        data = self._client().portfolio.get_portfolio_metadata(
            edm_irp_id, portfolio_irp_id)
        if not isinstance(data, dict):
            raise ValueError(
                f"unexpected portfolio metrics shape for {portfolio_irp_id}: "
                f"{type(data).__name__}")
        return ExposureDetail(payload=data)

    def _edm_database_name(self, *, edm_name: str, edm_irp_id: int) -> str:
        # The EDM's physical database name on Data Bridge comes from RM's
        # exposures search (`databaseName` per hit) — the workbench name is the
        # exposureName given at import, which RM may not use verbatim as the
        # database name. Matching the hit on exposureId picks OUR edm when
        # names collide (EDM names are not unique in RM).
        rows = self._client().edm.search_edms(
            filter=f"exposureName={json.dumps(edm_name)}")
        hit = next((r for r in rows
                    if str(r.get("exposureId")) == str(edm_irp_id)), None)
        database_name = (hit or {}).get("databaseName")
        if not database_name:
            raise ValueError(
                f"no databaseName resolvable for EDM '{edm_name}' "
                f"(exposureId {edm_irp_id}) from {len(rows)} search hit(s)")
        return str(database_name)

    def get_edm_exposure_summary(self, *, edm_name: str,
                                 edm_irp_id: int) -> dict[str, dict]:
        # Per-EDM DataBridge SQL aggregate (geography/LOB/currency — none
        # of which any RM REST endpoint returns; IRP_INTEGRATION_FOLLOWUPS §6).
        # Interim implementation: the requested wheel method
        # (get_portfolio_exposure_summary) doesn't exist yet, so the gateway
        # runs the repo-owned set-based scripts (sql/databridge/) through the
        # wheel's generic DataBridge executor — still read-only, still
        # worker-side, still via irp-integration (constitution Art. 11). One
        # call set per EDM: a deliberate exception to the single-item-loop
        # rule — these are SQL aggregates, where N per-portfolio queries would
        # just be N ODBC round-trips. Raises on ANY failure (databaseName
        # resolution / databridge extra / env / SQL) — graceful degradation
        # lives in one place, the worker's try/except.
        database = self._edm_database_name(edm_name=edm_name,
                                           edm_irp_id=edm_irp_id)
        databridge = self._client().databridge

        def rows(script: str) -> list[dict]:
            frames = databridge.execute_query_from_file(
                str(_DATABRIDGE_SQL_DIR / script), database=database)
            return frames[0].to_dict("records") if frames else []

        # Seed every portfolio from the portinfo enumeration (it covers
        # portfolios with no accounts/locations too); the DISTINCT list
        # scripts then only add to existing entries.
        summary: dict[str, dict] = {}

        def entry(row: dict) -> dict:
            key = str(row["PortfolioId"])
            if key not in summary:
                name = row.get("PortfolioName")
                summary[key] = {
                    "portfolio_name": (str(name) if name is not None else None),
                    "countries": [], "states": [],
                    "lines_of_business": [], "currencies": [],
                }
            return summary[key]

        for row in rows("portfolio_list.sql"):
            entry(row)
        for row in rows("portfolio_countries.sql"):
            entry(row)["countries"].append(str(row["Country"]))
        for row in rows("portfolio_states.sql"):
            entry(row)["states"].append(str(row["State"]))
        for row in rows("portfolio_lines_of_business.sql"):
            entry(row)["lines_of_business"].append(str(row["LineOfBusiness"]))
        for row in rows("portfolio_currencies.sql"):
            entry(row)["currencies"].append(str(row["Currency"]))
        for values in summary.values():
            for key in ("countries", "states", "lines_of_business", "currencies"):
                values[key] = sorted(set(values[key]))
            # 8/4 D15/CR19: line of business is user-defined free text that
            # cedants fill with account numbers or underwriter names — "if
            # it's over 500 values, we're not going to save it out."
            if len(values["lines_of_business"]) > _FREE_TEXT_STORAGE_CAP:
                values["lines_of_business"] = []
        return summary

    def search_treaties(self, *, edm_irp_id: int) -> list[TreatyDetail]:
        # GET /platform/riskdata/v1/exposures/{exposureId}/treaties (paginated).
        # The whole row IS the attribute map — stored verbatim (R2).
        rows = self._client().treaty.search_treaties_paginated(edm_irp_id)
        hits: list[TreatyDetail] = []
        for r in rows:
            tid = (r.get("treatyId") if r.get("treatyId") is not None
                   else r.get("id"))
            name = r.get("treatyName") or r.get("name")
            if not name:
                continue
            hits.append(TreatyDetail(
                irp_id=(str(tid) if tid is not None else None),
                name=str(name), attributes=r))
        return hits

    def get_analysis_metadata(self, *, analysis_id: int) -> AnalysisMetadata:
        # GET /platform/riskdata/v1/analyses/{analysisId} — the settings/metadata
        # payload verbatim, plus the typed exposure pointer (R9) and the group
        # marker (FR-035). The live payload (first real sync, 2026-07-24) carries
        # a first-class ``isGroup`` boolean — authoritative when present; a plain
        # analysis says groupType='ANLS', so the 'GROUP'-literal spellings below
        # stay only as fallback for payloads that omit isGroup
        # (IRP_INTEGRATION_FOLLOWUPS.md §7).
        data = self._client().analysis.get_analysis_by_id(analysis_id)
        if not isinstance(data, dict):
            # A failed read, never an empty success — the worker counts it as a
            # metadata failure and leaves the stored snapshot alone.
            raise ValueError(f"unexpected analysis metadata shape for "
                             f"{analysis_id}: {type(data).__name__}")
        payload = data
        rid = payload.get("exposureResourceId")
        is_group = payload.get("isGroup")
        if not isinstance(is_group, bool):
            group_markers = (payload.get("groupType"),
                             payload.get("analysisFramework"),
                             payload.get("analysisType"),
                             payload.get("exposureResourceType"))
            is_group = any(str(m).upper() == "GROUP"
                           for m in group_markers if m is not None)
        return AnalysisMetadata(
            payload=payload,
            exposure_resource_id=(str(rid) if rid is not None else None),
            exposure_resource_type=payload.get("exposureResourceType"),
            is_group=is_group)

    # ── single-status checks (Article 11 — never poll_*_to_completion) ────────────

    def get_import_job(self, irp_id: str) -> JobStatus:
        data = self._client().import_job.get_import_job(int(irp_id))
        return JobStatus(status=str(data["status"]), result=data)

    # ── name searches for the blocking collision check (R8, amended #17) ──────────

    def search_edms(self, name: str) -> list[EntityHit]:
        rows = self._client().edm.search_edms(
            filter=f"exposureName={json.dumps(name)}")
        return [EntityHit(irp_id=str(r.get("exposureId")),
                          name=(r.get("exposureName") or name)) for r in rows]

    def search_rdms(self, name: str) -> list[EntityHit]:
        # imported-rdms field names are less settled than the exposures ones; keep
        # this defensive — the caller treats collision search as best-effort (R8).
        rows = self._client().rdm.search_imported_rdms(
            filter=f"rdmName={json.dumps(name)}")
        return [EntityHit(
            irp_id=str(r.get("rdmId") or r.get("databaseId") or ""),
            name=(r.get("rdmName") or r.get("name") or r.get("sourceRdmName")
                  or name)) for r in rows]

    # ── unfiltered EDM catalog (the "sync existing EDMs" page) ────────────────────

    def list_edms(self) -> list[EdmCatalogEntry]:
        # No filter: every exposure the tenant can see. An IRPAPIError from the
        # paginated walk propagates — the caller degrades the whole page rather
        # than render a truncated list that reads as complete.
        rows = self._client().edm.search_edms_paginated(filter="")
        entries: list[EdmCatalogEntry] = []
        for r in rows:
            exposure_id = r.get("exposureId")
            name = r.get("exposureName")
            if exposure_id is None or not name:
                continue
            metrics = r.get("metrics") or {}
            entries.append(EdmCatalogEntry(
                irp_id=str(exposure_id),
                name=str(name),
                status=r.get("status"),
                server_name=r.get("serverName"),
                portfolio_count=metrics.get("portfolioCount"),
                treaty_count=metrics.get("treatyCount"),
                updated_at=r.get("updatedAt")))
        return entries


# ── Active-implementation registry (the injection seam) ──────────────────────────

_impl: IRPGateway | None = None


def configure(impl: IRPGateway) -> None:
    """Install the active gateway implementation (tests inject a fake here)."""
    global _impl
    _impl = impl


def reset() -> None:
    """Drop the active implementation (test teardown)."""
    global _impl
    _impl = None


def _active() -> IRPGateway:
    global _impl
    if _impl is None:
        _impl = _RealGateway()
    return _impl


# ── Module free functions — the call surface used everywhere else ────────────────

def submit_edm_import(*, name: str, source_file_path: str) -> SubmitResult:
    return _active().submit_edm_import(name=name, source_file_path=source_file_path)


def submit_rdm_import(*, name: str, source_file_path: str) -> SubmitResult:
    return _active().submit_rdm_import(
        name=name, source_file_path=source_file_path)


def search_analyses(*, source_rdm_name: str,
                    exposure_name: str | None = None) -> list[AnalysisHit]:
    return _active().search_analyses(source_rdm_name=source_rdm_name,
                                     exposure_name=exposure_name)


def get_import_job(irp_id: str) -> JobStatus:
    return _active().get_import_job(irp_id)


def search_edms(name: str) -> list[EntityHit]:
    return _active().search_edms(name)


def search_rdms(name: str) -> list[EntityHit]:
    return _active().search_rdms(name)


def list_edms() -> list[EdmCatalogEntry]:
    return _active().list_edms()


def list_portfolios(*, edm_irp_id: int) -> list[PortfolioHit]:
    return _active().list_portfolios(edm_irp_id=edm_irp_id)


def get_portfolio_exposure(*, edm_irp_id: int,
                           portfolio_irp_id: int) -> ExposureDetail:
    return _active().get_portfolio_exposure(edm_irp_id=edm_irp_id,
                                            portfolio_irp_id=portfolio_irp_id)


def get_edm_exposure_summary(*, edm_name: str,
                             edm_irp_id: int) -> dict[str, dict]:
    return _active().get_edm_exposure_summary(edm_name=edm_name,
                                              edm_irp_id=edm_irp_id)


def search_treaties(*, edm_irp_id: int) -> list[TreatyDetail]:
    return _active().search_treaties(edm_irp_id=edm_irp_id)


def get_analysis_metadata(*, analysis_id: int) -> AnalysisMetadata:
    return _active().get_analysis_metadata(analysis_id=analysis_id)


__all__ = [
    "SubmitResult", "JobStatus", "EntityHit", "EdmHit", "RdmHit", "AnalysisHit",
    "PortfolioHit", "ExposureDetail", "TreatyDetail", "AnalysisMetadata",
    "IRPGateway", "configure", "reset",
    "submit_edm_import", "submit_rdm_import", "search_analyses", "get_import_job",
    "search_edms", "search_rdms",
    "list_portfolios", "get_portfolio_exposure", "get_edm_exposure_summary",
    "search_treaties", "get_analysis_metadata",
]
