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
    DuplicatePortfolioNameError,
    EdmCatalogEntry,
    EntityHit,
    ExposureDetail,
    JobStatus,
    PortfolioHit,
    SubmitResult,
    SubPortfolioResult,
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
        # force search_analyses to fail (prune-safety tests)
        self.raise_on_search_analyses = False
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
        # ── spec-005 breakout knobs ──────────────────────────────────────────
        # confirm-time freshness read (FR-002a): recorded calls + failure knob
        self.stamp_reads: list[str] = []
        self.raise_on_fetch_stamp = False
        # selection: value → account ids. The selection read raising fails the
        # whole job — the real gateway's single DataBridge query is
        # all-or-nothing (R1, revised 2026-08-05)
        self.selection_by_value: dict[str, list[int]] = {}
        self.raise_on_selection_read = False
        self.selection_calls: list[dict] = []
        # Add-time match count (P-29): whatever the test says. The default is
        # non-zero because a zero refuses the Add, and most tests never mention
        # the count. Set 0 for the empty-intersection refusal, or make the read
        # raise (the caller fails open).
        self.match_count = 1
        self.raise_on_match_count = False
        self.match_count_calls: list[dict] = []
        # composition: names already taken in RM → create raises the DISTINCT
        # duplicate-name type (the adoption signal); per-name generic failures;
        # recorded create/populate calls; seedable adopt hits per number;
        # read-back count overrides (portfolio_irp_id → count; a count that
        # differs from the ids sent raises, as the real gateway does)
        self.taken_portfolio_names: set[str] = set()
        self.fail_create_for: dict[str, str] = {}
        self.created_sub_portfolios: list[dict] = []
        self.populate_calls: list[dict] = []
        self.hits_by_number: dict[str, list[PortfolioHit]] = {}
        self.readback_counts: dict[str, int] = {}
        self._next_sub_portfolio_id = 430
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
                      name: str, exposure: dict | None = None,
                      stamp: str | None = None) -> None:
        """Seed a portfolio enumerable by ``list_portfolios`` for an EDM (by its RM
        exposureId), with the canned exposure payload ``get_portfolio_exposure``
        returns (``DEFAULT_EXPOSURE`` when omitted). ``stamp`` seeds the RM
        stampDate the enumeration carries and ``fetch_portfolio_stamp`` returns
        (spec 005 FR-002a); update it via ``set_portfolio_stamp`` to simulate the
        portfolio changing in Risk Modeler after a backfill."""
        self._portfolios.setdefault(str(edm_exposure_id), []).append({
            "irp_id": str(irp_id), "name": name, "stamp": stamp,
            "exposure": (exposure if exposure is not None else dict(DEFAULT_EXPOSURE))})

    def set_portfolio_stamp(self, *, edm_exposure_id: str | int,
                            irp_id: str | int, stamp: str | None) -> None:
        """Move a seeded portfolio's RM stampDate — the portfolio changed in Risk
        Modeler (the FR-002a staleness case)."""
        for p in self._portfolios.get(str(edm_exposure_id), []):
            if p["irp_id"] == str(irp_id):
                p["stamp"] = stamp
                return
        raise KeyError(f"fake IRP: unknown portfolio {irp_id}")

    def set_exposure_summary(self, edm_name: str,
                             by_portfolio: dict[str, dict]) -> None:
        """Seed the per-EDM DataBridge aggregate ``get_edm_exposure_summary``
        returns — ``{portfolioId(str): {portfolio_name, countries, states,
        lines_of_business, currencies, account_total, breakout_values,
        breakout_coverage}}`` (the sql/databridge/ script set). Seed
        ``breakout_coverage`` per dimension as ``{"covered": n, "multi_value": n}``
        to exercise the measured FR-007 disclosure; omit it for the pre-2026-08-05
        summary that degrades to the qualitative one. Unseeded EDMs return
        ``{}``."""
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
        if self.raise_on_search_analyses:
            raise RuntimeError("fake IRP: forced search_analyses failure")
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
        return [PortfolioHit(irp_id=p["irp_id"], name=p["name"],
                             stamp=p.get("stamp"))
                for p in self._portfolios.get(str(edm_irp_id), [])]

    def fetch_portfolio_stamp(self, *, exposure_irp_id: str,
                              portfolio_irp_id: str) -> str | None:
        # The confirm-time freshness read (spec 005 FR-002a). Seed the stamp via
        # add_portfolio(stamp=...) / set_portfolio_stamp; force a gateway error
        # with raise_on_fetch_stamp (→ the confirm refuses, no job row).
        self.stamp_reads.append(str(portfolio_irp_id))
        if self.raise_on_fetch_stamp:
            raise RuntimeError("fake IRP: forced fetch_portfolio_stamp failure")
        for p in self._portfolios.get(str(exposure_irp_id), []):
            if p["irp_id"] == str(portfolio_irp_id):
                return p.get("stamp")
        return None

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

    # ── spec-005 breakout composition (mirrors the gateway) ─────────────────────

    def select_breakout_accounts(self, *, edm_name: str, exposure_irp_id: str,
                                 source_portfolio_irp_id: str, dimension: str,
                                 values) -> dict[str, list[int]]:
        # The selection read is the input to EVERY value — its failure raises
        # and the worker fails the job before anything is created.
        if self.raise_on_selection_read:
            raise RuntimeError("fake IRP: forced selection read failure")
        self.selection_calls.append({
            "edm_name": edm_name, "exposure_irp_id": str(exposure_irp_id),
            "source_portfolio_irp_id": str(source_portfolio_irp_id),
            "dimension": dimension, "values": list(values)})
        return {v: list(self.selection_by_value.get(v, [])) for v in values}

    def count_breakout_match(self, *, edm_name: str, exposure_irp_id: str,
                             source_portfolio_irp_id: str, filters) -> int:
        if self.raise_on_match_count:
            raise RuntimeError("fake IRP: forced match-count failure")
        self.match_count_calls.append({
            "edm_name": edm_name, "exposure_irp_id": str(exposure_irp_id),
            "source_portfolio_irp_id": str(source_portfolio_irp_id),
            "filters": {d: list(v) for d, v in filters.items()}})
        return self.match_count

    def create_sub_portfolio(self, *, edm_name: str, exposure_irp_id: str,
                             name: str, number: str, description: str,
                             account_ids) -> SubPortfolioResult:
        if name in self.fail_create_for:
            raise RuntimeError(self.fail_create_for[name])
        if name in self.taken_portfolio_names:
            raise DuplicatePortfolioNameError(
                f"portfolio name already exists in the EDM: {name}")
        self._next_sub_portfolio_id += 1
        pid = str(self._next_sub_portfolio_id)
        self.created_sub_portfolios.append({
            "edm_name": edm_name, "exposure_irp_id": str(exposure_irp_id),
            "name": name, "number": number, "description": description,
            "account_ids": list(account_ids), "portfolio_irp_id": pid})
        self.taken_portfolio_names.add(name)
        return self._read_back(pid, account_ids)

    def populate_sub_portfolio(self, *, edm_name: str, exposure_irp_id: str,
                               portfolio_irp_id: str,
                               account_ids) -> SubPortfolioResult:
        # adopt-then-populate heal (R7): re-adding members is safe (W-9)
        self.populate_calls.append({
            "portfolio_irp_id": str(portfolio_irp_id),
            "account_ids": list(account_ids)})
        return self._read_back(str(portfolio_irp_id), account_ids)

    def _read_back(self, pid: str, account_ids) -> SubPortfolioResult:
        # The gateway decides success by reading the portfolio back and
        # comparing against the ids sent; a mismatch raises (FR-008).
        selected = len(set(account_ids))
        count = self.readback_counts.get(pid, selected)
        if count != selected:
            raise ValueError(
                f"sub-portfolio {pid} holds {count} accounts after the add; "
                f"{selected} were selected")
        return SubPortfolioResult(portfolio_irp_id=pid, account_count=count)

    def find_portfolio_by_number(self, *, exposure_irp_id: str,
                                 number: str) -> list[PortfolioHit]:
        # EVERY hit — the worker refuses to adopt when there is more than one
        return list(self.hits_by_number.get(number, []))

    def find_portfolio_by_name(self, *, exposure_irp_id: str,
                               name: str) -> list[PortfolioHit]:
        # The group-name check's RM leg (spec 005 P-25): casefolded exact match
        # over the exposure's seeded + created portfolios; raise_on_search
        # forces the fail-open path, as for the EDM/RDM checks.
        self.search_calls.append(("portfolio", name))
        if self.raise_on_search:
            raise RuntimeError("fake IRP: forced search failure")
        wanted = name.casefold()
        hits = [PortfolioHit(irp_id=p["irp_id"], name=p["name"],
                             stamp=p.get("stamp"))
                for p in self._portfolios.get(str(exposure_irp_id), [])
                if p["name"].casefold() == wanted]
        hits += [PortfolioHit(irp_id=c["portfolio_irp_id"], name=c["name"],
                              stamp=None)
                 for c in self.created_sub_portfolios
                 if (c["exposure_irp_id"] == str(exposure_irp_id)
                     and c["name"].casefold() == wanted)]
        return hits

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
