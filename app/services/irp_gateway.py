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
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

logger = logging.getLogger(__name__)

# Repo-owned, read-only DataBridge scripts — the per-EDM summary aggregates
# (get_edm_exposure_summary) and the per-portfolio breakout selection and
# member-count reads (select_breakout_accounts / populate_sub_portfolio) —
# executed through the wheel's generic DataBridge executor.
_DATABRIDGE_SQL_DIR = Path(__file__).resolve().parents[2] / "sql" / "databridge"

# manage_portfolio_accounts takes the ids in the request body (no URL ceiling);
# adds are still chunked so no single PATCH carries an unbounded id list.
_ADD_CHUNK_SIZE = 1000

# The breakout selection read per dimension — parameterized, one-portfolio
# DataBridge scripts ({{ portfolio_id }}), executed by select_breakout_accounts
# below. Each script mirrors its summary script's joins, so the selection
# vocabulary matches the stored breakout_values the plan was approved from
# (LOBNAME for lob, Admin1Code for state — or the island's ISO3A CountryCode
# where the country is CB, D5 — P-12).
_SELECTION_SCRIPTS = {
    "lob": "breakout_lob_accounts.sql",
    "state": "breakout_state_accounts.sql",
    "country": "breakout_country_accounts.sql",
    "peril": "breakout_peril_accounts.sql",
}

# The custom-breakout emptiness check (P-29): one row, one integer, and the
# only DataBridge read permitted on the request path (Article 11 request-path
# exception, v3.2.0). Its value expressions mirror the selection scripts above,
# so a group that counts zero here is the same group the run would find empty.
_MATCH_COUNT_SCRIPT = "breakout_match_count.sql"

# Its per-dimension parameter names. A dimension missing from this map has no
# clause in the script, and dropping its filter silently would count accounts the
# breakout excludes — count_breakout_match raises instead.
_MATCH_COUNT_PARAMS = {"lob": "lob_values", "state": "state_values",
                       "country": "country_values", "peril": "peril_values"}

# The delimiter joining a dimension's selected values into one scalar parameter.
# ASCII unit separator: no EDM descriptor carries it, so no value can split
# wrong and turn a breakout that has accounts into a refused Add.
_MATCH_VALUE_SEPARATOR = "\x1f"

# The overlap coverage read per dimension — whole-EDM aggregates run by
# get_edm_exposure_summary, one row per portfolio: how many of its accounts
# carry at least one value of the dimension, and how many carry more than one
# (FR-007 as revised 2026-08-05). Keyed by breakout_dimension_kind.code so the
# dimension vocabulary stays in Python, never in the SQL.
_COVERAGE_SCRIPTS = {
    "lob": "portfolio_lob_coverage.sql",
    "state": "portfolio_state_coverage.sql",
    "country": "portfolio_country_coverage.sql",
    "peril": "portfolio_peril_coverage.sql",
}

# A free-text descriptor with more distinct values than this is not saved into
# the stored summary (8/4 D15 — lines of business is the known case).
_FREE_TEXT_STORAGE_CAP = 500


def _peril_code(value: Any) -> str:
    """The one owner of peril value stringification. ``loccvg.PERIL`` is a
    smallint, but pandas may hand it back as a float (3 → "3.0") — the
    canonical form everywhere (stored summary, selection keys, match-count
    filters) is the integer string."""
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value)


class DuplicatePortfolioNameError(Exception):
    """``create_portfolio`` refused because the name is already taken in the
    EDM — the adopt-an-existing-sub-portfolio signal (FR-011). A DISTINCT type
    because ``IRPValidationError`` alone also covers an over-long name and an
    over-long number (W-10); the gateway verifies the name is actually taken
    before raising this."""


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
    string; the exposure figures come separately via ``get_portfolio_exposure``.
    ``stamp`` is RM's ``stampDate`` from the enumeration row — the closest thing
    RM has to an updated-at; the backfill stores it as the FR-002a freshness
    anchor (spec 005). Read at enumeration time, BEFORE the DataBridge summary
    read, so the stored stamp is conservative."""
    irp_id: str
    name: str
    stamp: str | None = None


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


# ── Breakout value objects (spec 005 — contracts/data-access.md §3) ─────────────

@dataclass(frozen=True)
class SubPortfolioResult:
    """One composed sub-portfolio: RM's portfolioId from the create step, and
    the member count READ BACK from Risk Modeler — never the ``completed``
    figure from the add call, which counts ids newly added and is legitimately
    0 on a re-run (W-9)."""
    portfolio_irp_id: str
    account_count: int


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

    # ── spec-005 breakout reads (fetch_portfolio_stamp is request-path-legal) ────

    def fetch_portfolio_stamp(self, *, exposure_irp_id: str,
                              portfolio_irp_id: str) -> str | None: ...

    # ── spec-005 breakout composition (worker-only — the one RM write seam) ──────

    def select_breakout_accounts(
            self, *, edm_name: str, exposure_irp_id: str,
            source_portfolio_irp_id: str, dimension: str,
            values: Sequence[str]) -> dict[str, list[int]]: ...

    def count_breakout_match(self, *, edm_name: str, exposure_irp_id: str,
                             source_portfolio_irp_id: str,
                             filters: dict[str, Sequence[str]]) -> int: ...

    def create_sub_portfolio(self, *, edm_name: str, exposure_irp_id: str,
                             name: str, number: str, description: str,
                             account_ids: Sequence[int]) -> SubPortfolioResult: ...

    def populate_sub_portfolio(self, *, edm_name: str, exposure_irp_id: str,
                               portfolio_irp_id: str,
                               account_ids: Sequence[int]) -> SubPortfolioResult: ...

    def find_portfolio_by_number(self, *, exposure_irp_id: str,
                                 number: str) -> list[PortfolioHit]: ...

    def find_portfolio_by_name(self, *, exposure_irp_id: str,
                               name: str) -> list[PortfolioHit]: ...


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
        # databaseName per (edm_name, exposureId) — stable for the EDM's
        # lifetime (a re-import gets a new exposureId), so the breakout loop's
        # per-entry DataBridge reads don't repeat the RM exposures search.
        self._database_names: dict[tuple[str, str], str] = {}

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
            stamp = r.get("stampDate")
            hits.append(PortfolioHit(
                irp_id=str(pid), name=str(name),
                stamp=(str(stamp) if stamp is not None else None)))
        return hits

    def fetch_portfolio_stamp(self, *, exposure_irp_id: str,
                              portfolio_irp_id: str) -> str | None:
        # The confirm-time freshness read (spec 005 FR-002a): the portfolio's
        # current stampDate via the exposure-scoped portfolio search — the one
        # RM call permitted on the request path (Article 2's submit-time
        # pattern). Deliberately NOT named get_* — the architecture guard greps
        # for web-layer get_* IRP calls, and this one is request-path-legal.
        # Matched on portfolioId client-side: the filterable fields are
        # portfolioName/portfolioNumber (W-17) and neither is stable enough to
        # anchor a freshness check on.
        rows = self._client().portfolio.search_portfolios_paginated(
            int(exposure_irp_id))
        for r in rows:
            pid = r.get("id") if r.get("id") is not None else r.get("portfolioId")
            if pid is not None and str(pid) == str(portfolio_irp_id):
                stamp = r.get("stampDate")
                return str(stamp) if stamp is not None else None
        return None

    # ── spec-005 breakout composition (select → create → add, worker-only) ───────

    def _cached_database_name(self, *, edm_name: str,
                              exposure_irp_id: str) -> str:
        key = (edm_name, str(exposure_irp_id))
        if key not in self._database_names:
            self._database_names[key] = self._edm_database_name(
                edm_name=edm_name, edm_irp_id=int(exposure_irp_id))
        return self._database_names[key]

    def select_breakout_accounts(
            self, *, edm_name: str, exposure_irp_id: str,
            source_portfolio_irp_id: str, dimension: str,
            values: Sequence[str]) -> dict[str, list[int]]:
        # One set-based DataBridge query resolves EVERY value at once (R1,
        # revised 2026-08-05): the REST selection — paginated account
        # enumeration plus a chunked accountId-IN policy scan — cannot complete
        # on a large book. The wheel's account search refuses past 100,000
        # records because it can no longer prove the page sequence is complete
        # (observed at 248,000 accounts, W-20), and the policy scan multiplies
        # that into thousands of round trips. The script mirrors the summary
        # script's joins, so the values filtered here are byte-identical to
        # the stored summary the plan was approved from; ACCGRPID is the id
        # RM's account operations accept as accountId. Any failure RAISES —
        # the worker fails the job before anything is created (W-14: never
        # proceed on an id list the query cannot prove complete). A value the
        # query returned no rows for maps to an EMPTY list, which the worker
        # turns into a zero-match failure with no create call (FR-008).
        script = _SELECTION_SCRIPTS.get(dimension)
        if script is None:
            raise ValueError(
                f"no selection read implemented for dimension {dimension!r}")
        database = self._cached_database_name(edm_name=edm_name,
                                              exposure_irp_id=exposure_irp_id)
        frames = self._client().databridge.execute_query_from_file(
            str(_DATABRIDGE_SQL_DIR / script),
            params={"portfolio_id": int(source_portfolio_irp_id)},
            database=database)
        rows = frames[0].to_dict("records") if frames else []
        coerce = _peril_code if dimension == "peril" else str
        by_value: dict[str, set[int]] = {}
        for row in rows:
            value, account_id = row.get("Value"), row.get("AccountId")
            if value is None or account_id is None:
                continue
            by_value.setdefault(coerce(value), set()).add(int(account_id))
        return {v: sorted(by_value.get(v, set())) for v in values}

    def count_breakout_match(self, *, edm_name: str, exposure_irp_id: str,
                             source_portfolio_irp_id: str,
                             filters: dict[str, Sequence[str]]) -> int:
        # The Add-time emptiness check (P-29): how many accounts carry at least
        # one selected value in EVERY filtered dimension. One row, one integer —
        # the shape Article 11's request-path exception admits. Any failure
        # raises; the caller fails open, so an unreachable DataBridge never
        # blocks an Add.
        unknown = sorted(set(filters) - set(_MATCH_COUNT_PARAMS))
        if unknown:
            raise ValueError(
                f"breakout_match_count.sql carries no clause for dimension(s) "
                f"{', '.join(unknown)} — their filters would be dropped and the "
                "count would include accounts the breakout excludes")
        params: dict[str, Any] = {
            "portfolio_id": int(source_portfolio_irp_id)}
        for dimension, param in _MATCH_COUNT_PARAMS.items():
            selected = filters.get(dimension)
            params[param] = (_MATCH_VALUE_SEPARATOR.join(selected) if selected
                             else None)
        frames = self._client().databridge.execute_query_from_file(
            str(_DATABRIDGE_SQL_DIR / _MATCH_COUNT_SCRIPT), params=params,
            database=self._cached_database_name(
                edm_name=edm_name, exposure_irp_id=exposure_irp_id))
        rows = frames[0].to_dict("records") if frames else []
        if not rows:
            # A COUNT query always returns one row (the _member_count
            # reasoning): no rows means the read itself failed, and reporting
            # that as zero would refuse a breakout that has accounts.
            raise ValueError(
                f"match count for portfolio {source_portfolio_irp_id} returned "
                "no rows — the count could not be verified")
        count = rows[0].get("AccountCount")
        return int(count) if count is not None else 0

    def _member_count(self, *, edm_name: str, exposure_irp_id: str,
                      portfolio_irp_id: str) -> int:
        # A COUNT query always returns one row, so no rows at all means the read
        # itself came back empty — raise rather than report a zero-member
        # portfolio, which the caller's comparison would blame on the add.
        frames = self._client().databridge.execute_query_from_file(
            str(_DATABRIDGE_SQL_DIR / "portfolio_member_count.sql"),
            params={"portfolio_id": int(portfolio_irp_id)},
            database=self._cached_database_name(
                edm_name=edm_name, exposure_irp_id=exposure_irp_id))
        rows = frames[0].to_dict("records") if frames else []
        if not rows:
            raise ValueError(
                f"member-count read for portfolio {portfolio_irp_id} returned "
                "no rows — the count could not be verified")
        count = rows[0].get("AccountCount")
        return int(count) if count is not None else 0

    def _portfolio_name_taken(self, exposure_irp_id: str, name: str) -> bool:
        # W-10: IRPValidationError alone is not "the name is taken" — it also
        # covers the two length violations. Verify against RM before treating
        # the failure as the adoption signal. Paginated, like every other
        # portfolio lookup here: a name held by a portfolio past the first page
        # would otherwise read as free and the entry would fail instead of
        # adopting.
        try:
            hits = self._client().portfolio.search_portfolios_paginated(
                int(exposure_irp_id), filter=f"portfolioName={json.dumps(name)}")
        except Exception:  # noqa: BLE001 — unverifiable → let the original error stand
            return False
        return len(hits) > 0

    def create_sub_portfolio(self, *, edm_name: str, exposure_irp_id: str,
                             name: str, number: str, description: str,
                             account_ids: Sequence[int]) -> SubPortfolioResult:
        # create (synchronous 201) → add → read back and compare (T-01). The
        # caller's description passes through UNTOUCHED — Risk Modeler's
        # description is where the untruncated lineage lives (FR-010), so the
        # gateway never shortens it. portfolio_number is always passed
        # explicitly: omitted, RM defaults it to the name, which then overruns
        # the number's own 20-character cap (W-13).
        from irp_integration.exceptions import IRPValidationError  # noqa: PLC0415 — lazy by design

        try:
            portfolio_irp_id, _body = self._client().portfolio.create_portfolio(
                edm_name, name, number, description)
        except IRPValidationError as exc:
            if self._portfolio_name_taken(exposure_irp_id, name):
                raise DuplicatePortfolioNameError(
                    f"portfolio name already exists in the EDM: {name}") from exc
            raise
        return self.populate_sub_portfolio(
            edm_name=edm_name, exposure_irp_id=exposure_irp_id,
            portfolio_irp_id=str(portfolio_irp_id), account_ids=account_ids)

    def populate_sub_portfolio(self, *, edm_name: str, exposure_irp_id: str,
                               portfolio_irp_id: str,
                               account_ids: Sequence[int]) -> SubPortfolioResult:
        # The add step + read-back verification, shared by the create path and
        # the adopt-then-populate heal (R7). Re-adding already-member accounts
        # is safe and returns completed 0 (W-9), so the heal runs
        # unconditionally; success is decided by reading the portfolio back,
        # never by the add call's completed/total counts. The read-back is a
        # DataBridge count (R1, revised 2026-08-05) — the paginated REST
        # enumeration cannot verify a portfolio past 100,000 accounts (W-20).
        # A count that does not equal the ids sent RAISES: FR-008 asks for
        # exactly the selected accounts, so an under- or over-populated
        # portfolio fails that sub-portfolio and writes no lineage row. The RM
        # portfolio stays (P-07 — nothing is deleted); the re-run adopts it on
        # its number and re-adds, which heals a partial add. It cannot heal an
        # OVER-populated one: re-adding never removes a member, so an adopted
        # portfolio holding accounts outside the selection fails on every run
        # until someone removes them in Risk Modeler — which is what the message
        # says, since the app deletes nothing.
        pm = self._client().portfolio
        ids = [int(i) for i in account_ids]
        chunks = range(0, len(ids), _ADD_CHUNK_SIZE)
        logger.info("adding %d accounts to portfolio %s in %d chunk(s)",
                    len(ids), portfolio_irp_id, len(chunks))
        for start in chunks:
            pm.manage_portfolio_accounts(
                int(exposure_irp_id), int(portfolio_irp_id),
                accounts_to_add=ids[start:start + _ADD_CHUNK_SIZE])
        count = self._member_count(edm_name=edm_name,
                                   exposure_irp_id=exposure_irp_id,
                                   portfolio_irp_id=portfolio_irp_id)
        selected = len(set(ids))
        if count != selected:
            raise ValueError(
                f"sub-portfolio {portfolio_irp_id} holds {count} accounts "
                f"after the add; {selected} were selected"
                + (" — remove the extra accounts in Risk Modeler, then re-run"
                   if count > selected else " — re-run to complete the add"))
        return SubPortfolioResult(portfolio_irp_id=str(portfolio_irp_id),
                                  account_count=count)

    def find_portfolio_by_number(self, *, exposure_irp_id: str,
                                 number: str) -> list[PortfolioHit]:
        # EVERY hit, not the first — more than one portfolio carrying the
        # number fails that sub-portfolio rather than adopting an arbitrary
        # one (FR-011). Numbers are unique only within an exposure; the
        # exposure-scoped search covers that (W-17).
        rows = self._client().portfolio.search_portfolios_paginated(
            int(exposure_irp_id), filter=f"portfolioNumber={json.dumps(number)}")
        hits: list[PortfolioHit] = []
        for r in rows:
            pid = r.get("id") if r.get("id") is not None else r.get("portfolioId")
            if pid is None:
                continue
            stamp = r.get("stampDate")
            hits.append(PortfolioHit(
                irp_id=str(pid), name=str(r.get("name") or
                                          r.get("portfolioName") or ""),
                stamp=(str(stamp) if stamp is not None else None)))
        return hits

    def find_portfolio_by_name(self, *, exposure_irp_id: str,
                               name: str) -> list[PortfolioHit]:
        # The group-name check (spec 005 P-25): the same exposure-scoped
        # portfolioName search the duplicate-name verification trusts (W-10).
        # Every hit counts — the check blocks, it never adopts, so ambiguity
        # is fine here. Request-path-legal: the submit-time pattern of
        # constitution Art. 2, like fetch_portfolio_stamp above.
        rows = self._client().portfolio.search_portfolios_paginated(
            int(exposure_irp_id), filter=f"portfolioName={json.dumps(name)}")
        hits: list[PortfolioHit] = []
        for r in rows:
            pid = r.get("id") if r.get("id") is not None else r.get("portfolioId")
            if pid is None:
                continue
            stamp = r.get("stampDate")
            hits.append(PortfolioHit(
                irp_id=str(pid),
                name=str(r.get("name") or r.get("portfolioName") or name),
                stamp=(str(stamp) if stamp is not None else None)))
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
            if not frames:
                return []
            # A DataFrame turns SQL NULL into NaN, and NaN is truthy: an
            # un-geocoded Admin1Name reached the summary as the label "nan".
            # `v != v` is true only for NaN/NaT and needs no pandas import.
            return [{k: (None if v != v else v) for k, v in record.items()}
                    for record in frames[0].to_dict("records")]

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
                    # spec 005 (R11): the overlap denominator and the breakout
                    # enumeration source, keyed by breakout_dimension_kind.code.
                    # Their PRESENCE marks a post-005 summary — the gate reads a
                    # summary without breakout_values as absent (FR-002).
                    "account_total": None, "breakout_values": {},
                    # spec 005 FR-007 as revised 2026-08-05: the measured
                    # overlap, also keyed by dimension code. Absent on a summary
                    # written before that revision, which the preview degrades
                    # to the qualitative disclosure alone (data-model §6).
                    "breakout_coverage": {},
                }
            return summary[key]

        for row in rows("portfolio_list.sql"):
            entry(row)
        for row in rows("portfolio_account_total.sql"):
            total = row.get("AccountTotal")
            entry(row)["account_total"] = (int(total) if total is not None
                                           else None)
        for row in rows("portfolio_countries.sql"):
            # dbo.Address carries country only as codes, so the code is its
            # own display and the label is never synthesized (P-12).
            e = entry(row)
            value = str(row["Country"])
            e["countries"].append(value)
            count = row.get("AccountCount")
            e["breakout_values"].setdefault("country", []).append({
                "value": value, "label": None,
                "accounts": (int(count) if count is not None else 0)})
        for row in rows("portfolio_states.sql"):
            # spec 005 (FR-005/P-12): the value is Admin1Code — the summary's
            # states list now holds codes, not the old COALESCE(name, code)
            # mix — or the island's ISO3A country code for Caribbean addresses,
            # which the script returns in the same column (D5). Admin1Name rides
            # along as a nullable display label (absent until the EDM is
            # geocoded, null for the Caribbean, never synthesized).
            e = entry(row)
            value = str(row["Admin1Code"])
            e["states"].append(value)
            label = row.get("Admin1Name")
            count = row.get("AccountCount")
            e["breakout_values"].setdefault("state", []).append({
                "value": value,
                "label": (str(label) if label else None),
                "accounts": (int(count) if count is not None else 0)})
        for row in rows("portfolio_lines_of_business.sql"):
            e = entry(row)
            value = str(row["LineOfBusiness"])
            e["lines_of_business"].append(value)
            # spec 005 (FR-005): LOB breakout values — the value is its own
            # label (label null); accounts is the FR-007 overlap numerator.
            count = row.get("AccountCount")
            e["breakout_values"].setdefault("lob", []).append({
                "value": value, "label": None,
                "accounts": (int(count) if count is not None else 0)})
        for row in rows("portfolio_perils.sql"):
            # spec 005 (P-19 rev. 2026-08-12): peril breakout values. The value
            # is loccvg.PERIL — a numeric RMS peril code with no in-EDM
            # code→name lookup (W-21) — so the code is its own display and the
            # label is never synthesized (P-12); _peril_code canonicalizes it.
            count = row.get("AccountCount")
            entry(row)["breakout_values"].setdefault("peril", []).append({
                "value": _peril_code(row["Peril"]), "label": None,
                "accounts": (int(count) if count is not None else 0)})
        for dimension, script in _COVERAGE_SCRIPTS.items():
            # spec 005 FR-007 as revised 2026-08-05: `covered` is the account
            # count carrying at least one value of the dimension (AccountTotal
            # minus it is the SC-002 coverage shortfall), `multi_value` the
            # count carrying more than one — the accounts that land in several
            # sub-portfolios. Summing breakout_values[].accounts cannot produce
            # either: it counts memberships, and an account with three values
            # adds three.
            for row in rows(script):
                covered = row.get("CoveredAccounts")
                multi = row.get("MultiValueAccounts")
                entry(row)["breakout_coverage"][dimension] = {
                    "covered": (int(covered) if covered is not None else 0),
                    "multi_value": (int(multi) if multi is not None else 0)}
        for row in rows("portfolio_currencies.sql"):
            entry(row)["currencies"].append(str(row["Currency"]))
        for values in summary.values():
            for key in ("countries", "states", "lines_of_business", "currencies"):
                values[key] = sorted(set(values[key]))
            for dim, entries in values["breakout_values"].items():
                values["breakout_values"][dim] = sorted(
                    entries, key=lambda e: e["value"])
            # 8/4 D15/CR19: line of business is user-defined free text that
            # cedants fill with account numbers or underwriter names — "if
            # it's over 500 values, we're not going to save it out." The lob
            # breakout values go with it: spec 005 enumerates the breakout from
            # the stored summary, so a dropped list must not leave them behind.
            if len(values["lines_of_business"]) > _FREE_TEXT_STORAGE_CAP:
                values["lines_of_business"] = []
                values["breakout_values"].pop("lob", None)
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


def fetch_portfolio_stamp(*, exposure_irp_id: str,
                          portfolio_irp_id: str) -> str | None:
    return _active().fetch_portfolio_stamp(exposure_irp_id=exposure_irp_id,
                                           portfolio_irp_id=portfolio_irp_id)


def select_breakout_accounts(*, edm_name: str, exposure_irp_id: str,
                             source_portfolio_irp_id: str, dimension: str,
                             values: Sequence[str]) -> dict[str, list[int]]:
    return _active().select_breakout_accounts(
        edm_name=edm_name, exposure_irp_id=exposure_irp_id,
        source_portfolio_irp_id=source_portfolio_irp_id,
        dimension=dimension, values=values)


def count_breakout_match(*, edm_name: str, exposure_irp_id: str,
                         source_portfolio_irp_id: str,
                         filters: dict[str, Sequence[str]]) -> int:
    return _active().count_breakout_match(
        edm_name=edm_name, exposure_irp_id=exposure_irp_id,
        source_portfolio_irp_id=source_portfolio_irp_id, filters=filters)


def create_sub_portfolio(*, edm_name: str, exposure_irp_id: str, name: str,
                         number: str, description: str,
                         account_ids: Sequence[int]) -> SubPortfolioResult:
    return _active().create_sub_portfolio(
        edm_name=edm_name, exposure_irp_id=exposure_irp_id, name=name,
        number=number, description=description, account_ids=account_ids)


def populate_sub_portfolio(*, edm_name: str, exposure_irp_id: str,
                           portfolio_irp_id: str,
                           account_ids: Sequence[int]) -> SubPortfolioResult:
    return _active().populate_sub_portfolio(
        edm_name=edm_name, exposure_irp_id=exposure_irp_id,
        portfolio_irp_id=portfolio_irp_id, account_ids=account_ids)


def find_portfolio_by_number(*, exposure_irp_id: str,
                             number: str) -> list[PortfolioHit]:
    return _active().find_portfolio_by_number(
        exposure_irp_id=exposure_irp_id, number=number)


def find_portfolio_by_name(*, exposure_irp_id: str,
                           name: str) -> list[PortfolioHit]:
    return _active().find_portfolio_by_name(
        exposure_irp_id=exposure_irp_id, name=name)


__all__ = [
    "SubmitResult", "JobStatus", "EntityHit", "EdmHit", "RdmHit", "AnalysisHit",
    "PortfolioHit", "ExposureDetail", "TreatyDetail", "AnalysisMetadata",
    "SubPortfolioResult", "DuplicatePortfolioNameError",
    "IRPGateway", "configure", "reset",
    "submit_edm_import", "submit_rdm_import", "search_analyses", "get_import_job",
    "search_edms", "search_rdms",
    "list_portfolios", "get_portfolio_exposure", "get_edm_exposure_summary",
    "search_treaties", "get_analysis_metadata", "fetch_portfolio_stamp",
    "select_breakout_accounts", "count_breakout_match", "create_sub_portfolio",
    "populate_sub_portfolio", "find_portfolio_by_number",
    "find_portfolio_by_name",
]
