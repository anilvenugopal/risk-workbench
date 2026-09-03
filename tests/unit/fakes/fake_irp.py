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
    CurrencyEntry,
    CurrencySchemeEntry,
    CurrencySchemeVintageEntry,
    DuplicatePortfolioNameError,
    EdmCatalogEntry,
    EntityHit,
    EventRateSchemeEntry,
    EventRateSchemeOption,
    ExposureDetail,
    GroupingInspection,
    GroupingMember,
    GroupingPartition,
    GroupingPartitionKey,
    GroupingProblem,
    GroupingRegionFact,
    IRPGroupingValidationError,
    IRPIntegrationError,
    JobStatus,
    ModelProfileEntry,
    OutputProfileEntry,
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

# ── spec-011 result fixtures (shaped like the live captures, research R3) ──────
# The 11 stored return periods (data-model §4) with two points around them the
# extract never keeps, so the exact-match lookup runs against a wider curve than
# the target set — as it does against RM's real 10,004-point response.
FIXTURE_RETURN_PERIODS = [1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0,
                          1000.0, 2000.0, 5000.0, 10000.0, 50000.0]

# One multiplier per epType, so a test can tell which element a stored number
# came from. TCE-OEP/TCE-AEP are discarded by the builder (O-04) and their
# multipliers are far enough away that a leaked TCE value is unmistakable.
_EP_TYPE_FACTOR = {"AEP": 2, "OEP": 1, "TCE-AEP": 180, "TCE-OEP": 90}


def stats_rows(*, analysis_id, perspective_code, exposure_resource_id,
               pure_premium: float, total_std_dev: float,
               ep_type: str = "OEP") -> list[dict]:
    """One ``ep_stats-aal_response``-shaped row, including the -1.0-filled
    treaty fields RM sends for a portfolio analysis."""
    return [{
        "analysisId": int(analysis_id),
        "exposureResourceId": int(exposure_resource_id),
        "exposureResourceType": "PORTFOLIO",
        "perspectiveCode": perspective_code,
        "epType": ep_type,
        "purePremium": pure_premium,
        "totalStdDev": total_std_dev,
        "cv": 69.13209737370671,
        "netPurePremium": -1.0, "activation": -1.0, "exhaustion": -1.0,
        "totalLossRatio": -1.0, "limit": -1.0, "premium": -1.0,
        "netStdDev": -1.0, "exhaustAllReinstatements": -1.0,
        "exposureResourceNumber": "FF_US",
    }]


def ep_elements(*, analysis_id, perspective_code, exposure_resource_id,
                base: float) -> list[dict]:
    """The four ``ep_curve_response``-shaped elements — AEP, OEP, TCE-AEP,
    TCE-OEP. A point's loss is ``base * return_period * the epType factor``, so
    every stored number identifies the perspective, the EP type and the return
    period it came from."""
    periods = list(FIXTURE_RETURN_PERIODS)
    return [{
        "jobId": int(analysis_id),
        "epType": ep_type,
        "perspectiveCode": perspective_code,
        "exposureResourceId": int(exposure_resource_id),
        "exposureResourceType": "PORTFOLIO",
        "exposureResourceNumber": "FF_US",
        "value": {
            "returnPeriods": periods,
            "positionValues": [base * period * factor for period in periods],
        },
    } for ep_type, factor in _EP_TYPE_FACTOR.items()]


# Unseeded perspectives outside these two return empty lists — the FR-004
# "fetched, nothing there" path every test gets for free.
_DEFAULT_RESULT_PERSPECTIVES = {
    "GR": {"pure_premium": 38270.5904752427, "total_std_dev": 2645726.187283731,
           "base": 1.0},
    "GU": {"pure_premium": 55000.25, "total_std_dev": 3100500.75, "base": 3.0},
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
        self.model_profiles = [
            ModelProfileEntry(1, "RMS Default RL25", "RL25", "WS", "NAWS",
                              "Windstorm", "North America", "Exceedance Probability"),
            ModelProfileEntry(2, "RMS Default HD", "HDv3.0", "WS", "NAWS",
                              "Windstorm", "North America", "Exceedance Probability"),
            ModelProfileEntry(3, "Open profile", "Open", "EQ", "NAEQ",
                              "Earthquake", "North America", "User Defined"),
        ]
        self.output_profiles = [OutputProfileEntry(10, "RMS Default Output", True)]
        self.event_rate_schemes = [
            EventRateSchemeEntry(20, "RMS WS", "WS", "NAWS", "25.0", False)]
        self.currencies = [CurrencyEntry("USD", "US Dollar", "United States", "$")]
        self.currency_schemes = [
            CurrencySchemeEntry(30, "RMS Scheme", "RMS", "USD", 30),
            CurrencySchemeEntry(31, "Deterministic Scheme", "DT", "EUR", 365),
        ]
        # RMS carries two vintages (latest RL25) so pre-fill-latest logic has
        # something to choose between; DT carries one, under a vintage code
        # RMS doesn't share — so a vintage resolving under the wrong scheme
        # is directly exercisable against these defaults.
        self.currency_scheme_vintages = [
            CurrencySchemeVintageEntry("RL25", "RMS", "2025-05-28T00:00:00.000Z"),
            CurrencySchemeVintageEntry("RL23", "RMS", "2023-05-28T00:00:00.000Z"),
            CurrencySchemeVintageEntry("RL24", "DT", "2024-05-28T00:00:00.000Z"),
        ]
        self.raise_on_reference_data = False
        # ── spec-010 analysis execution (worker-only) ────────────────────────
        # recorded submit_portfolio_analysis calls, in order
        self.analysis_submits: list[dict] = []
        # job_name -> forced IRPIntegrationError on the next submit for that name
        self.raise_on_submit_analysis_for: set[str] = set()
        # recorded delete_analysis calls, in order
        self.deleted_analyses: list[str] = []
        # irp_id -> forced IRPIntegrationError on delete_analysis (per-id,
        # mirrors raise_on_submit_analysis_for)
        self.raise_on_delete_analysis: set[str] = set()
        # ── spec-011 result reads (worker-only) ──────────────────────────────
        # (analysis_id, perspective_code) -> {"stats": [...], "ep": [...]};
        # unseeded pairs fall back to _DEFAULT_RESULT_PERSPECTIVES
        self._analysis_results: dict[tuple[str, str], dict] = {}
        # perspective codes whose stats/EP read raises — the retrieval-failure
        # path (the job fails, loss_results is left untouched)
        self.raise_on_analysis_results_for: set[str] = set()
        # recorded result reads: {"call", "analysis_id", "perspective_code",
        # "exposure_resource_id"} — the idempotency assertions count these
        self.result_calls: list[dict] = []
        # ── spec-012 grouping (contracts/grouping-worker.md) ─────────────────
        # recorded inspect_grouping id lists and submit_grouping kwargs, in order
        self.grouping_inspects: list[list[int]] = []
        self.grouping_submits: list[dict] = []
        # the inspection inspect_grouping returns (seed_grouping_inspection);
        # None → a pure-ELT, non-conflicting inspection built for the ids
        self.grouping_inspection: GroupingInspection | None = None
        # makes inspect_grouping raise IRPIntegrationError with this message
        self.grouping_inspect_error: str | None = None
        # names count_analyses_named reports as taken, besides those in _analyses
        self.duplicate_group_names: set[str] = set()
        # recorded count_analyses_named names, in order
        self.grouping_name_checks: list[str] = []
        # group names whose submit raises a generic failure
        self.raise_on_submit_grouping_for: set[str] = set()
        # structured problems submit_grouping raises as IRPGroupingValidationError
        self.grouping_submit_problems: list[GroupingProblem] = []
        # force the inspection_changed rejection even when fingerprints match
        self.grouping_fingerprints_change = False

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

    def set_analysis_results(self, *, analysis_id: str | int,
                             perspective_code: str,
                             stats: list[dict] | None = None,
                             ep: list[dict] | None = None) -> None:
        """Seed what ``get_analysis_stats``/``get_analysis_ep`` return for one
        (analysis, perspective) — build the rows with ``stats_rows`` /
        ``ep_elements``, or pass ``[]`` for a perspective the analysis did not
        produce. Overrides the GR/GU defaults for that pair only."""
        self._analysis_results[(str(analysis_id), perspective_code)] = {
            "stats": [] if stats is None else list(stats),
            "ep": [] if ep is None else list(ep)}

    def run(self, irp_id: str) -> None:
        self.jobs[irp_id] = "RUNNING"

    def finish(self, irp_id: str, result: dict | None = None) -> None:
        self.jobs[irp_id] = "FINISHED"
        self.results[irp_id] = result or {}

    def fail(self, irp_id: str, result: dict | None = None) -> None:
        self.jobs[irp_id] = "FAILED"
        self.results[irp_id] = result or {}

    def cancel(self, irp_id: str, result: dict | None = None) -> None:
        self.jobs[irp_id] = "CANCELLED"
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

    def submit_geohaz(self, *, edm_name: str, portfolio_name: str,
                      version: str, perils: list[str],
                      skip_prev_hazard: bool,
                      override_user_def: bool) -> SubmitResult:
        return self._submit(
            "geohaz",
            edm_name=edm_name,
            portfolio_name=portfolio_name,
            version=version,
            perils=list(perils),
            skip_prev_hazard=skip_prev_hazard,
            override_user_def=override_user_def,
        )

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
        # EVERY hit — the worker refuses to adopt when there is more than one.
        # Unseeded numbers resolve against the sub-portfolios this fake created
        # and still holds, so two entries composing one number read as
        # ambiguous here exactly as they would in Risk Modeler.
        if number in self.hits_by_number:
            return list(self.hits_by_number[number])
        return [PortfolioHit(irp_id=p["portfolio_irp_id"], name=p["name"])
                for p in self.created_sub_portfolios
                if p["number"] == number
                and p["name"] in self.taken_portfolio_names]

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

    # ── spec-010 analysis execution (worker-only) ────────────────────────────

    def submit_portfolio_analysis(
        self, *, edm_name: str, portfolio_name: str, job_name: str,
        analysis_profile_name: str, output_profile_name: str,
        event_rate_scheme_name: str | None, treaty_names: list[str],
        tag_names: list[str], currency: dict,
        min_loss_threshold: float, num_max_loss_event: int,
        franchise_deductible: bool, treat_construction_occupancy_as_unknown: bool,
    ) -> tuple[str, dict]:
        self.analysis_submits.append({
            "edm_name": edm_name, "portfolio_name": portfolio_name,
            "job_name": job_name,
            "analysis_profile_name": analysis_profile_name,
            "output_profile_name": output_profile_name,
            "event_rate_scheme_name": event_rate_scheme_name,
            "treaty_names": list(treaty_names), "tag_names": list(tag_names),
            "currency": dict(currency),
            "min_loss_threshold": min_loss_threshold,
            "num_max_loss_event": num_max_loss_event,
            "franchise_deductible": franchise_deductible,
            "treat_construction_occupancy_as_unknown": (
                treat_construction_occupancy_as_unknown),
        })
        if job_name in self.raise_on_submit_analysis_for:
            raise IRPIntegrationError(
                f"fake IRP: forced analysis submit failure for '{job_name}'")
        irp_id = self._next_id()
        self.jobs[irp_id] = "QUEUED"
        request_body = {
            "resourceUri": f"/irp/analysis/{irp_id}",
            "resourceType": "portfolio",
            "type": "DLM" if event_rate_scheme_name else "HD",
            "settings": {
                "name": job_name,
                "currency": currency,
                "minLossThreshold": min_loss_threshold,
                "numMaxLossEvent": num_max_loss_event,
                "franchiseDeductible": franchise_deductible,
                "treatConstructionOccupancyAsUnknown": (
                    treat_construction_occupancy_as_unknown),
            },
        }
        return irp_id, request_body

    def get_analysis_job(self, irp_id: str) -> JobStatus:
        return JobStatus(status=self.jobs.get(irp_id, "QUEUED"),
                         result=self.results.get(irp_id))

    # ── spec-012 grouping (mirrors contracts/grouping-worker.md) ─────────────

    def seed_grouping_inspection(
        self, analysis_ids: list[int], *, output_loss_table: str = "ELT",
        conflicting: list[int] | None = None,
        periods: dict[int, int] | None = None,
        blocking: tuple[GroupingProblem, ...] = (),
        warnings: tuple[GroupingProblem, ...] = (),
    ) -> GroupingInspection:
        """Seed what ``inspect_grouping`` returns. Every member sits in one
        WS · NA · 11.0 partition; ``conflicting`` lists the scheme ids on offer
        and marks the partition as requiring a selection; ``periods`` makes the
        named members PLT (PET ``900+n``) with that many periods; ``blocking``
        and ``warnings`` seed the problems verbatim."""
        self.grouping_inspection = self._build_inspection(
            list(analysis_ids), output_loss_table=output_loss_table,
            conflicting=conflicting, periods=periods, blocking=blocking,
            warnings=warnings)
        return self.grouping_inspection

    @staticmethod
    def _build_inspection(ids: list[int], *, output_loss_table: str = "ELT",
                          conflicting: list[int] | None = None,
                          periods: dict[int, int] | None = None,
                          blocking: tuple[GroupingProblem, ...] = (),
                          warnings: tuple[GroupingProblem, ...] = (),
                          ) -> GroupingInspection:
        periods = periods or {}
        key = GroupingPartitionKey(peril_code="WS", region_code="NA",
                                   model_version="11.0")
        members = []
        for n, analysis_id in enumerate(ids):
            plt = analysis_id in periods
            scheme = conflicting[n % len(conflicting)] if conflicting else 101
            region = GroupingRegionFact(
                analysis_id=analysis_id, framework="PLT" if plt else "ELT",
                peril_code="WS", region_code="NA", model_version="11.0",
                engine_version="RL25", sub_region="NA", model_region_code="NA_WS",
                event_rate_scheme_id=None if plt else scheme,
                pet_id=(900 + n) if plt else None,
                periods=periods.get(analysis_id), apply_contract_flag=False)
            members.append(GroupingMember(
                analysis_id=analysis_id, exists=True, is_group=False,
                analysis_framework="PLT" if plt else "ELT",
                engine_type="HD" if plt else "DLM", engine_version="RL25",
                peril_code="WS", region_code="NA", model_version="11.0",
                regions=(region,)))
        options = tuple(
            EventRateSchemeOption(event_rate_scheme_id=s, label=f"Scheme {s}")
            for s in (conflicting or [101]))
        partition = GroupingPartition(
            key=key, analysis_ids=tuple(ids), event_rate_scheme_options=options,
            observed_pet_ids=tuple(900 + n for n, a in enumerate(ids)
                                   if a in periods),
            event_rate_selection_required=bool(conflicting))
        required = ["analysis_name", "currency", "propagate_detailed_losses",
                    "num_of_simulations"]
        if conflicting:
            required.append("event_rate_selections")
        return GroupingInspection(
            analysis_ids=tuple(ids),
            resource_uris=tuple(f"/platform/riskdata/v1/analyses/{i}" for i in ids),
            inspected_at="2026-09-02T00:00:00+00:00",
            fingerprint=f"v1:fake-{','.join(str(i) for i in ids)}",
            members=tuple(members), output_loss_table=output_loss_table,
            simulate_to_plt=(output_loss_table == "PLT"),
            partitions=(partition,), simulation_mappings=(),
            required_caller_inputs=tuple(required), warnings=tuple(warnings),
            blocking_problems=tuple(blocking))

    def _inspection_for(self, ids: list[int]) -> GroupingInspection:
        return self.grouping_inspection or self._build_inspection(ids)

    def inspect_grouping(self, *, analysis_ids: list[int]) -> GroupingInspection:
        self.grouping_inspects.append(list(analysis_ids))
        if self.grouping_inspect_error:
            raise IRPIntegrationError(self.grouping_inspect_error)
        return self._inspection_for(list(analysis_ids))

    def submit_grouping(
        self, *, analysis_ids: list[int], group_name: str, currency: dict,
        propagate_detailed_losses: bool, num_of_simulations: int,
        event_rate_selections: list[dict], simulation_set_selections: list[dict],
        expected_inspection_fingerprint: str,
    ) -> tuple[str, dict]:
        self.grouping_submits.append({
            "analysis_ids": list(analysis_ids),
            "group_name": group_name,
            "currency": dict(currency),
            "propagate_detailed_losses": propagate_detailed_losses,
            "num_of_simulations": num_of_simulations,
            "event_rate_selections": [dict(s) for s in event_rate_selections],
            "simulation_set_selections": [dict(s) for s in simulation_set_selections],
            "expected_inspection_fingerprint": expected_inspection_fingerprint,
        })
        if group_name in self.raise_on_submit_grouping_for:
            raise IRPIntegrationError(
                f"fake IRP: forced grouping submit failure for '{group_name}'")
        inspection = self._inspection_for(list(analysis_ids))
        if (self.grouping_fingerprints_change
                or expected_inspection_fingerprint != inspection.fingerprint):
            raise IRPGroupingValidationError((GroupingProblem(
                code="inspection_changed",
                message="Grouping facts changed after inspection; inspect the analyses again.",
                analysis_ids=tuple(analysis_ids)),))
        if self.grouping_submit_problems:
            raise IRPGroupingValidationError(tuple(self.grouping_submit_problems))
        irp_id = self._next_id()
        self.jobs[irp_id] = "QUEUED"
        request_body = {
            "resourceType": "analyses",
            "resourceUris": list(inspection.resource_uris),
            "settings": {
                "analysisName": group_name,
                "currency": dict(currency),
                "simulateToPLT": inspection.simulate_to_plt,
                "propagateDetailedLosses": propagate_detailed_losses,
                "numOfSimulations": num_of_simulations,
                "regionPerilSimulationSet": [],
            },
        }
        return irp_id, request_body

    def get_grouping_job(self, irp_id: str) -> JobStatus:
        return JobStatus(status=self.jobs.get(irp_id, "QUEUED"),
                         result=self.results.get(irp_id))

    def count_analyses_named(self, name: str) -> int:
        self.grouping_name_checks.append(name)
        return (len([a for a in self._analyses if a["name"] == name])
                + (1 if name in self.duplicate_group_names else 0))

    def get_analysis_by_name_only(self, name: str) -> AnalysisHit:
        hits = [a for a in self._analyses if a["name"] == name]
        if len(hits) != 1:
            raise LookupError(
                f"expected exactly one analysis named {name!r}, "
                f"found {len(hits)}")
        a = hits[0]
        return AnalysisHit(
            analysis_id=a["analysis_id"], name=a["name"],
            source_rdm_name=a["source_rdm_name"],
            exposure_name=a["exposure_name"],
            exposure_resource_id=a.get("exposure_resource_id"),
            exposure_resource_type=a.get("exposure_resource_type"))

    # ── spec-011 result reads (worker-only) ──────────────────────────────────

    def get_analysis_stats(self, *, analysis_id: int, perspective_code: str,
                           exposure_resource_id: int) -> list[dict]:
        return self._results("stats", analysis_id, perspective_code,
                             exposure_resource_id)

    def get_analysis_ep(self, *, analysis_id: int, perspective_code: str,
                        exposure_resource_id: int) -> list[dict]:
        return self._results("ep", analysis_id, perspective_code,
                             exposure_resource_id)

    def _results(self, call: str, analysis_id, perspective_code,
                 exposure_resource_id) -> list[dict]:
        self.result_calls.append({
            "call": call, "analysis_id": str(analysis_id),
            "perspective_code": perspective_code,
            "exposure_resource_id": str(exposure_resource_id)})
        if perspective_code in self.raise_on_analysis_results_for:
            raise IRPIntegrationError(
                f"fake IRP: forced {call} failure for perspective "
                f"{perspective_code}")
        seeded = self._analysis_results.get((str(analysis_id), perspective_code))
        if seeded is not None:
            return list(seeded[call])
        default = _DEFAULT_RESULT_PERSPECTIVES.get(perspective_code)
        if default is None:
            return []
        if call == "stats":
            return stats_rows(analysis_id=analysis_id,
                              perspective_code=perspective_code,
                              exposure_resource_id=exposure_resource_id,
                              pure_premium=default["pure_premium"],
                              total_std_dev=default["total_std_dev"])
        return ep_elements(analysis_id=analysis_id,
                           perspective_code=perspective_code,
                           exposure_resource_id=exposure_resource_id,
                           base=default["base"])

    def delete_analysis(self, irp_id: str) -> None:
        if str(irp_id) in self.raise_on_delete_analysis:
            raise IRPIntegrationError(
                f"fake IRP: forced analysis delete failure for '{irp_id}'")
        self.deleted_analyses.append(str(irp_id))

    def get_geohaz_job(self, irp_id: str) -> JobStatus:
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

    def _reference_data(self, rows):
        if self.raise_on_reference_data:
            raise RuntimeError("fake IRP: forced reference-data failure")
        return list(rows)

    def list_model_profiles(self) -> list[ModelProfileEntry]:
        return self._reference_data(self.model_profiles)

    def list_output_profiles(self) -> list[OutputProfileEntry]:
        return self._reference_data(self.output_profiles)

    def list_event_rate_schemes(self) -> list[EventRateSchemeEntry]:
        return self._reference_data(self.event_rate_schemes)

    def list_currencies(self) -> list[CurrencyEntry]:
        return self._reference_data(self.currencies)

    def list_currency_schemes(self) -> list[CurrencySchemeEntry]:
        return self._reference_data(self.currency_schemes)

    def list_currency_scheme_vintages(self) -> list[CurrencySchemeVintageEntry]:
        return self._reference_data(self.currency_scheme_vintages)
