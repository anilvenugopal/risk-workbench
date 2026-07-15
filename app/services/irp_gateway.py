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

**Caller caveat — EDM delete identifier (open defect).** ``submit_delete_edm``
forwards its ``edm_irp_id`` to the wheel as the Risk Modeler *exposureId*
(``DELETE /exposures/{exposureId}``) — NOT the import *job id*. Callers therefore
MUST pass the EDM's exposureId, i.e. ``irp_edm.irp_id`` must be backfilled with the
exposureId at import-FINISHED (name lookup via ``search_edms`` or from the import
completion body), not with the import job id the submit returned. Until the poller
does that, ``delete_edm`` targets the wrong resource.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


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
    """A name-search hit used for the non-blocking collision warning (R8)."""
    irp_id: str
    name: str


EdmHit = EntityHit
RdmHit = EntityHit


@dataclass(frozen=True)
class AnalysisHit:
    """One broker analysis returned by ``search_analyses`` (D2). ``analysis_id`` is
    Moody's ``analysisId`` as a string — the ``delete_analysis`` key. The pair names
    are echoed back so the backfill worker can persist lineage on ``irp_analysis``."""
    analysis_id: str
    name: str | None = None
    source_rdm_name: str | None = None
    exposure_name: str | None = None


# ── The interface the poller/workers depend on (fake implements it in CI) ────────

@runtime_checkable
class IRPGateway(Protocol):
    def submit_edm_import(self, *, name: str, source_file_path: str) -> SubmitResult: ...

    def submit_rdm_import(self, *, name: str, source_file_path: str,
                          edm_name: str | None) -> SubmitResult: ...

    def submit_delete_edm(self, *, edm_irp_id: int) -> SubmitResult: ...

    def delete_analysis(self, *, analysis_id: int) -> None: ...

    def search_analyses(self, *, source_rdm_name: str,
                        exposure_name: str) -> list[AnalysisHit]: ...

    def get_import_job(self, irp_id: str) -> JobStatus: ...

    def get_delete_edm_job(self, irp_id: str) -> JobStatus: ...

    def search_edms(self, name: str) -> list[EntityHit]: ...

    def search_rdms(self, name: str) -> list[EntityHit]: ...


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

    def submit_rdm_import(self, *, name: str, source_file_path: str,
                          edm_name: str | None) -> SubmitResult:
        # D3: every RDM apply targets an EDM. The wheel requires edm_name — a
        # no-EDM apply is a programming error, not a runtime IRP failure.
        if not edm_name:
            raise ValueError("submit_rdm_import requires an edm_name (D3).")
        job_id, body = self._client().rdm.submit_rdm_import_job(
            rdm_name=name, edm_name=edm_name, rdm_file_path=source_file_path)
        return SubmitResult(irp_id=str(job_id),
                            resource_uri=body.get("resourceUri"), payload=body)

    def submit_delete_edm(self, *, edm_irp_id: int) -> SubmitResult:
        # edm_irp_id is the RM exposureId (see the module docstring caveat), not the
        # import job id. Returns only a job id (no request body / resource_uri).
        job_id = self._client().edm.submit_delete_edm_job(exposure_id=edm_irp_id)
        return SubmitResult(irp_id=str(job_id), payload={"exposure_id": edm_irp_id})

    # ── synchronous analysis delete + search (no irp_job — R6 / D2) ───────────────

    def delete_analysis(self, *, analysis_id: int) -> None:
        self._client().analysis.delete_analysis(analysis_id)

    def search_analyses(self, *, source_rdm_name: str,
                        exposure_name: str) -> list[AnalysisHit]:
        # Build the pair filter here with json.dumps quoting (mirrors search_edms /
        # search_rdms) so a name with a quote/space can never malform the filter —
        # the callers pass raw names, never a pre-built filter string.
        filter = (f"sourceRdmName={json.dumps(source_rdm_name)} "
                  f"AND exposureName={json.dumps(exposure_name)}")
        # Paginated so delete-enumeration captures every analysis for the pair (D2).
        rows = self._client().analysis.search_analyses_paginated(filter=filter)
        return [
            AnalysisHit(
                analysis_id=str(r["analysisId"]),
                name=r.get("analysisName"),
                source_rdm_name=r.get("sourceRdmName"),
                exposure_name=r.get("exposureName"))
            for r in rows if r.get("analysisId") is not None
        ]

    # ── single-status checks (Article 11 — never poll_*_to_completion) ────────────

    def get_import_job(self, irp_id: str) -> JobStatus:
        data = self._client().import_job.get_import_job(int(irp_id))
        return JobStatus(status=str(data["status"]), result=data)

    def get_delete_edm_job(self, irp_id: str) -> JobStatus:
        # EDM delete is a platform risk-data job (DELETE /exposures/{id} → jobs/{id}),
        # tracked via the unified risk-data job endpoint — same WORKFLOW vocabulary.
        data = self._client().risk_data_job.get_risk_data_job(int(irp_id))
        return JobStatus(status=str(data["status"]), result=data)

    # ── name searches for the non-blocking collision warning (R8) ─────────────────

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


def submit_rdm_import(*, name: str, source_file_path: str,
                      edm_name: str | None) -> SubmitResult:
    return _active().submit_rdm_import(
        name=name, source_file_path=source_file_path, edm_name=edm_name)


def submit_delete_edm(*, edm_irp_id: int) -> SubmitResult:
    return _active().submit_delete_edm(edm_irp_id=edm_irp_id)


def delete_analysis(*, analysis_id: int) -> None:
    return _active().delete_analysis(analysis_id=analysis_id)


def search_analyses(*, source_rdm_name: str,
                    exposure_name: str) -> list[AnalysisHit]:
    return _active().search_analyses(source_rdm_name=source_rdm_name,
                                     exposure_name=exposure_name)


def get_import_job(irp_id: str) -> JobStatus:
    return _active().get_import_job(irp_id)


def get_delete_edm_job(irp_id: str) -> JobStatus:
    return _active().get_delete_edm_job(irp_id)


def search_edms(name: str) -> list[EntityHit]:
    return _active().search_edms(name)


def search_rdms(name: str) -> list[EntityHit]:
    return _active().search_rdms(name)


__all__ = [
    "SubmitResult", "JobStatus", "EntityHit", "EdmHit", "RdmHit", "AnalysisHit",
    "IRPGateway", "configure", "reset",
    "submit_edm_import", "submit_rdm_import", "submit_delete_edm",
    "delete_analysis", "search_analyses", "get_import_job", "get_delete_edm_job",
    "search_edms", "search_rdms",
]
