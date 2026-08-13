"""In-memory fake Risk Modeler for the unit tier (Article 12).

Implements the ``app.services.irp_gateway.IRPGateway`` protocol without touching
``irp-integration`` or the network, so the poller and workers can be exercised
deterministically. Tests drive job outcomes explicitly:

    fake = FakeIRP()
    irp_gateway.configure(fake)
    res = fake.submit_edm_import(name="A", source_file_path="/x.bak")
    assert fake.get_import_job(res.irp_id).status == "QUEUED"
    fake.run(res.irp_id)                    # → RUNNING
    fake.finish(res.irp_id)                 # → FINISHED
    fake.fail(res.irp_id)                   # → FAILED

Name-collision hits are seeded via ``add_edm_name`` / ``add_rdm_name``.
"""

from __future__ import annotations

from app.services.irp_gateway import (
    AnalysisHit,
    AnalysisMetadata,
    EdmCatalogEntry,
    EntityHit,
    ExposureDetail,
    JobStatus,
    PortfolioHit,
    SubmitResult,
    TreatyDetail,
)

# The real RM /metrics payload shape (confirmed in sandbox 2026-07-23, data-model §2)
# used when a seeded portfolio doesn't specify its own. Counts + a perilsExposed
# STRING — RM returns no geography/LOB/currency here (those come from the
# DataBridge exposure summary).
DEFAULT_EXPOSURE = {
    "totalAccounts": 10, "totalLocations": 100, "totalPolicies": 12,
    "perilsExposed": "EQ",
    "name": "portfolio", "number": "portfolio",
    "geocodeVersion": "23.0", "hazardVersion": "23.0",
}


class FakeIRP:
    def __init__(self) -> None:
        self._seq = 0
        # irp_id -> current status string
        self.jobs: dict[str, str] = {}
        # irp_id -> terminal result body (set on finish/fail)
        self.results: dict[str, dict] = {}
        # recorded calls for assertions
        self.submits: list[dict] = []
        # seeded name-collision universe
        self._edm_names: set[str] = set()
        self._rdm_names: set[str] = set()
        # EDM name -> fake RM exposureId (the durable entity id; distinct from job ids)
        self._edm_exposure_ids: dict[str, str] = {}
        # seeded analyses for search_analyses (D2): list of dicts with the pair keys
        self._analyses: list[dict] = []
        # optionally force the next submit to fail (returns no irp_id)
        self.raise_on_submit = False
        # force name-collision searches to fail (fail-open tests, issue #17)
        self.raise_on_search = False
        # recorded (kind, name) collision searches — cache assertions (issue #11)
        self.search_calls: list[tuple[str, str]] = []
        # ── spec-004 detail-read universe (worker backfill reads) ────────────
        # EDM exposureId (str) -> [{irp_id, name, exposure}] seeded portfolios
        self._portfolios: dict[str, list[dict]] = {}
        # EDM exposureId (str) -> [TreatyDetail-shaped dicts] seeded treaties
        self._treaties: dict[str, list[dict]] = {}
        # failure knobs: whole-enumeration failure / per-portfolio exposure failure
        self.raise_on_list_portfolios = False
        self.raise_on_search_treaties = False
        self.fail_exposure_for: set[str] = set()
        # recorded detail-read calls for assertions
        self.exposure_reads: list[str] = []
        # ── DataBridge exposure summary (Addendum A T057) ────────────────────
        # EDM name -> {portfolioId(str): summary dict} — the per-EDM aggregate
        self._summaries: dict[str, dict[str, dict]] = {}
        self.raise_on_exposure_summary = False
        self.summary_reads: list[str] = []
        # per-analysis metadata failure knob (US3 — blank, never error)
        self.raise_on_analysis_metadata = False
        # ── unfiltered EDM catalog (the "sync existing EDMs" page) ───────────
        # explicitly seeded catalog entries (a list, not a name-keyed dict —
        # EDM names are not unique in RM and the diff must cope with that)
        self._catalog: list[dict] = []
        self.raise_on_list_edms = False

    # ── control surface (test-only) ────────────────────────────────────────────

    def add_edm_name(self, name: str) -> None:
        self._edm_names.add(name)

    def add_rdm_name(self, name: str) -> None:
        self._rdm_names.add(name)

    def add_catalog_edm(self, *, name: str, irp_id: str | int | None = None,
                        **display) -> str:
        """Seed an EDM that ``list_edms`` returns — one that exists in Risk Modeler
        whether or not the workbench created it. ``irp_id`` defaults to the same
        exposureId ``search_edms`` would resolve for the name; pass it explicitly to
        seed two EDMs sharing a name (RM allows that). ``display`` sets any other
        ``EdmCatalogEntry`` field. Returns the exposureId."""
        exposure_id = (str(irp_id) if irp_id is not None
                       else self._exposure_id_for(name))
        self._edm_names.add(name)
        self._catalog.append({"irp_id": exposure_id, "name": name, **display})
        return exposure_id

    def _exposure_id_for(self, name: str) -> str:
        """Stable fake RM exposureId for an EDM name — deliberately in a different
        range from job ids (which start at 1) so tests can tell the durable *entity*
        id apart from the import *job* id."""
        if name not in self._edm_exposure_ids:
            self._edm_exposure_ids[name] = str(90001 + len(self._edm_exposure_ids))
        return self._edm_exposure_ids[name]

    def edm_exposure_id(self, name: str) -> str | None:
        """The exposureId assigned to a known/imported EDM (test assertion helper)."""
        return self._edm_exposure_ids.get(name)

    def add_analysis(self, *, source_rdm_name: str, exposure_name: str,
                     analysis_id: str, name: str | None = None,
                     exposure_resource_id: str | None = None,
                     exposure_resource_type: str | None = None,
                     is_group: bool = False,
                     metadata: dict | None = None) -> None:
        """Seed an analysis discoverable by ``search_analyses`` for this (RDM, EDM)
        pair — the backfill worker captures it as an ``irp_analysis`` row (D2).

        Spec 004 (R9): optionally carries RM's exposure pointer — seed
        ``exposure_resource_type="PORTFOLIO"`` for a linkable analysis, ``GROUP``/
        another type or no pointer for the group / non-portfolio / unresolvable
        paths — plus ``is_group`` and a ``metadata`` settings payload."""
        self._analyses.append({
            "analysis_id": str(analysis_id), "name": name,
            "source_rdm_name": source_rdm_name, "exposure_name": exposure_name,
            "exposure_resource_id": (str(exposure_resource_id)
                                     if exposure_resource_id is not None else None),
            "exposure_resource_type": exposure_resource_type,
            "is_group": is_group, "metadata": metadata})

    def add_portfolio(self, *, edm_exposure_id: str | int, irp_id: str | int,
                      name: str, exposure: dict | None = None) -> None:
        """Seed a portfolio enumerable by ``list_portfolios`` for an EDM (by its RM
        exposureId), with the canned exposure payload ``get_portfolio_exposure``
        returns (``DEFAULT_EXPOSURE`` when omitted)."""
        self._portfolios.setdefault(str(edm_exposure_id), []).append({
            "irp_id": str(irp_id), "name": name,
            "exposure": (exposure if exposure is not None else dict(DEFAULT_EXPOSURE))})

    def set_exposure_summary(self, edm_name: str,
                             by_portfolio: dict[str, dict]) -> None:
        """Seed the per-EDM DataBridge aggregate ``get_edm_exposure_summary``
        returns — ``{portfolioId(str): {portfolio_name, countries, states,
        lines_of_business, currencies}}`` (the sql/databridge/ script set).
        Unseeded EDMs return ``{}``."""
        self._summaries[edm_name] = {str(k): dict(v)
                                     for k, v in by_portfolio.items()}

    def add_treaty(self, *, edm_exposure_id: str | int, irp_id: str | int | None,
                   name: str, attributes: dict | None = None) -> None:
        """Seed a treaty returned by ``search_treaties`` for an EDM, with its full
        attribute map (stored verbatim by the backfill worker)."""
        self._treaties.setdefault(str(edm_exposure_id), []).append({
            "irp_id": (str(irp_id) if irp_id is not None else None),
            "name": name, "attributes": (attributes or {"treatyName": name})})

    def run(self, irp_id: str) -> None:
        self.jobs[irp_id] = "RUNNING"

    def finish(self, irp_id: str, result: dict | None = None) -> None:
        self.jobs[irp_id] = "FINISHED"
        self.results[irp_id] = result or {}

    def fail(self, irp_id: str, result: dict | None = None) -> None:
        self.jobs[irp_id] = "FAILED"
        self.results[irp_id] = result or {}

    def _next_id(self) -> str:
        self._seq += 1
        return str(self._seq)

    def _submit(self, kind: str, **meta) -> SubmitResult:
        if self.raise_on_submit:
            raise RuntimeError("fake IRP: forced submit failure")
        irp_id = self._next_id()
        self.jobs[irp_id] = "QUEUED"
        self.submits.append({"irp_id": irp_id, "kind": kind, **meta})
        return SubmitResult(
            irp_id=irp_id,
            resource_uri=f"/irp/{kind}/{irp_id}",
            payload={"kind": kind, **meta},
            response={"jobId": irp_id, "resourceUri": f"/irp/{kind}/{irp_id}"},
        )

    # ── IRPGateway protocol ─────────────────────────────────────────────────────

    def submit_edm_import(self, *, name: str, source_file_path: str) -> SubmitResult:
        # A successful import makes the EDM discoverable by name and assigns it a
        # durable exposureId — distinct from the returned job id — so the poller's
        # by-name exposureId resolution is exercised (entity id != job id). A FAILED
        # submit creates nothing (mirrors real RM), so an errored member's name
        # doesn't phantom-collide on re-sync (issue #17).
        result = self._submit("import_edm", name=name, source_file_path=source_file_path)
        self._edm_names.add(name)
        self._exposure_id_for(name)
        return result

    def submit_rdm_import(self, *, name: str,
                          source_file_path: str) -> SubmitResult:
        return self._submit("import_rdm", name=name,
                            source_file_path=source_file_path,
                            exposure_set_name=name)

    def search_analyses(self, *, source_rdm_name: str,
                        exposure_name: str | None = None) -> list[AnalysisHit]:
        # Return every seeded analysis matching this (RDM, EDM) pair. The gateway now
        # builds the filter string internally (safe json.dumps quoting), so the fake
        # matches on the pair args directly rather than parsing a filter string.
        hits: list[AnalysisHit] = []
        for a in self._analyses:
            if (a["source_rdm_name"] == source_rdm_name
                    and (exposure_name is None
                         or a["exposure_name"] == exposure_name)):
                hits.append(AnalysisHit(
                    analysis_id=a["analysis_id"], name=a["name"],
                    source_rdm_name=a["source_rdm_name"],
                    exposure_name=a["exposure_name"],
                    exposure_resource_id=a.get("exposure_resource_id"),
                    exposure_resource_type=a.get("exposure_resource_type")))
        return hits

    # ── spec-004 detail reads (mirrors the extended IRPGateway surface) ─────────

    def list_portfolios(self, *, edm_irp_id: int) -> list[PortfolioHit]:
        if self.raise_on_list_portfolios:
            raise RuntimeError("fake IRP: forced list_portfolios failure")
        return [PortfolioHit(irp_id=p["irp_id"], name=p["name"])
                for p in self._portfolios.get(str(edm_irp_id), [])]

    def get_portfolio_exposure(self, *, edm_irp_id: int,
                               portfolio_irp_id: int) -> ExposureDetail:
        self.exposure_reads.append(str(portfolio_irp_id))
        if str(portfolio_irp_id) in self.fail_exposure_for:
            raise RuntimeError(
                f"fake IRP: forced exposure failure for portfolio {portfolio_irp_id}")
        for p in self._portfolios.get(str(edm_irp_id), []):
            if p["irp_id"] == str(portfolio_irp_id):
                return ExposureDetail(payload=p["exposure"])
        raise RuntimeError(f"fake IRP: unknown portfolio {portfolio_irp_id}")

    def get_edm_exposure_summary(self, *, edm_name: str,
                                 edm_irp_id: int) -> dict[str, dict]:
        self.summary_reads.append(edm_name)
        if self.raise_on_exposure_summary:
            raise RuntimeError("fake IRP: forced exposure-summary failure")
        return self._summaries.get(edm_name, {})

    def search_treaties(self, *, edm_irp_id: int) -> list[TreatyDetail]:
        if self.raise_on_search_treaties:
            raise RuntimeError("fake IRP: forced search_treaties failure")
        return [TreatyDetail(irp_id=t["irp_id"], name=t["name"],
                             attributes=t["attributes"])
                for t in self._treaties.get(str(edm_irp_id), [])]

    def get_analysis_metadata(self, *, analysis_id: int) -> AnalysisMetadata:
        if self.raise_on_analysis_metadata:
            raise RuntimeError("fake IRP: forced analysis-metadata failure")
        for a in self._analyses:
            if a["analysis_id"] == str(analysis_id):
                return AnalysisMetadata(
                    payload=(a.get("metadata") or {}),
                    exposure_resource_id=a.get("exposure_resource_id"),
                    exposure_resource_type=a.get("exposure_resource_type"),
                    is_group=bool(a.get("is_group")))
        return AnalysisMetadata()

    def get_import_job(self, irp_id: str) -> JobStatus:
        return JobStatus(status=self.jobs.get(irp_id, "QUEUED"),
                         result=self.results.get(irp_id))

    def search_edms(self, name: str) -> list[EntityHit]:
        self.search_calls.append(("edm", name))
        if self.raise_on_search:
            raise RuntimeError("fake IRP: forced search failure")
        # A known EDM (collision-seeded or imported) resolves to its fake exposureId —
        # the durable entity id the poller stores as irp_edm.irp_id.
        return ([EntityHit(irp_id=self._exposure_id_for(name), name=name)]
                if name in self._edm_names else [])

    def list_edms(self) -> list[EdmCatalogEntry]:
        if self.raise_on_list_edms:
            raise RuntimeError("fake IRP: forced list_edms failure")
        entries = [EdmCatalogEntry(**c) for c in self._catalog]
        # Every other known EDM — collision-seeded or imported through the fake —
        # is in RM too, so it belongs in the catalog even without add_catalog_edm.
        # A name add_catalog_edm already described is left to those entries (it may
        # legitimately have several, since RM names are not unique).
        described = {e.name for e in entries}
        for name in sorted(self._edm_names - described):
            entries.append(EdmCatalogEntry(
                irp_id=self._exposure_id_for(name), name=name,
                status="READY", server_name="databridge-1"))
        return entries

    def search_rdms(self, name: str) -> list[EntityHit]:
        self.search_calls.append(("rdm", name))
        if self.raise_on_search:
            raise RuntimeError("fake IRP: forced search failure")
        return ([EntityHit(irp_id=f"rdm-{name}", name=name)]
                if name in self._rdm_names else [])
