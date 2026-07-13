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
(``make irp-pypi | irp-testpypi | irp-local``, research R1). Because this is the
only importer, re-confirming a method signature against the active wheel is a
one-file edit, and the CI fake (``tests/unit/fakes/fake_irp.py``) implements the
same ``IRPGateway`` protocol, so a signature change never scatters across services.

Injection: tests call ``configure(FakeIRP())``; production code calls the module
free functions (``submit_edm_import(...)`` etc.), which delegate to the active
implementation — the real, ``IRPClient``-backed one by default.
"""

from __future__ import annotations

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


# ── The interface the poller/workers depend on (fake implements it in CI) ────────

@runtime_checkable
class IRPGateway(Protocol):
    def submit_edm_import(self, *, name: str, source_file_path: str) -> SubmitResult: ...

    def submit_rdm_import(self, *, name: str, source_file_path: str,
                          edm_name: str | None) -> SubmitResult: ...

    def submit_delete_edm(self, *, edm_irp_id: int) -> SubmitResult: ...

    def delete_rdm_analyses(self, *, rdm_name: str) -> None: ...

    def get_import_job(self, irp_id: str) -> JobStatus: ...

    def get_delete_edm_job(self, irp_id: str) -> JobStatus: ...

    def search_edms(self, name: str) -> list[EntityHit]: ...

    def search_rdms(self, name: str) -> list[EntityHit]: ...


# ── The real implementation — imports irp-integration lazily ─────────────────────

class _RealGateway:
    """Thin wrapper over ``irp-integration``. Every method name/signature MUST be
    re-confirmed against the ACTIVE wheel before its operation is first exercised
    (R1). ``IRPClient()`` reads all config from env vars — no constructor args.

    The library is imported lazily (inside ``_client``) so that importing this
    module never requires the wheel to be installed — unit tests inject a fake and
    never touch the real client.
    """

    def __init__(self) -> None:
        self._irp = None

    def _client(self):
        if self._irp is None:
            from irp_integration import IRPClient  # noqa: PLC0415 — lazy by design
            self._irp = IRPClient()
        return self._irp

    def submit_edm_import(self, *, name: str, source_file_path: str) -> SubmitResult:
        raise NotImplementedError(
            "Real EDM-import submit is wired in US1 (T021) against the active wheel.")

    def submit_rdm_import(self, *, name: str, source_file_path: str,
                          edm_name: str | None) -> SubmitResult:
        raise NotImplementedError(
            "Real RDM-import submit is wired in US2 (T027) against the active wheel.")

    def submit_delete_edm(self, *, edm_irp_id: int) -> SubmitResult:
        raise NotImplementedError(
            "Real EDM-delete submit is wired in US4 (T039) against the active wheel.")

    def delete_rdm_analyses(self, *, rdm_name: str) -> None:
        raise NotImplementedError(
            "Real synchronous RDM-analysis delete is wired in US4 (T039).")

    def get_import_job(self, irp_id: str) -> JobStatus:
        raise NotImplementedError(
            "Real single-status import check is wired in US1 (T022).")

    def get_delete_edm_job(self, irp_id: str) -> JobStatus:
        raise NotImplementedError(
            "Real single-status delete-EDM check is wired in US4 (T040).")

    def search_edms(self, name: str) -> list[EntityHit]:
        raise NotImplementedError(
            "Real EDM name search is wired in US1 (T020) against the active wheel.")

    def search_rdms(self, name: str) -> list[EntityHit]:
        raise NotImplementedError(
            "Real RDM name search is wired in US2 (T026) against the active wheel.")


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


def delete_rdm_analyses(*, rdm_name: str) -> None:
    return _active().delete_rdm_analyses(rdm_name=rdm_name)


def get_import_job(irp_id: str) -> JobStatus:
    return _active().get_import_job(irp_id)


def get_delete_edm_job(irp_id: str) -> JobStatus:
    return _active().get_delete_edm_job(irp_id)


def search_edms(name: str) -> list[EntityHit]:
    return _active().search_edms(name)


def search_rdms(name: str) -> list[EntityHit]:
    return _active().search_rdms(name)


__all__ = [
    "SubmitResult", "JobStatus", "EntityHit", "EdmHit", "RdmHit", "IRPGateway",
    "configure", "reset",
    "submit_edm_import", "submit_rdm_import", "submit_delete_edm",
    "delete_rdm_analyses", "get_import_job", "get_delete_edm_job",
    "search_edms", "search_rdms",
]
