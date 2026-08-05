"""Unit tests for ``_RealGateway``'s spec-004 detail readers.

The whole value of these methods is defensive field-mapping over a pre-release
wheel (R1): ``id`` vs ``portfolioId``, ``treatyId`` vs ``id``, skip-on-missing,
and — crucially — treating a NON-DICT single-item response as a FAILED read
(raise), never as an empty success that would overwrite a prior good snapshot.

Group-marker confirmation (2026-07-24, first real sync against the RM tenant):
``GET /analyses/{analysisId}`` carries a first-class ``isGroup`` boolean, and a
plain analysis comes back with ``groupType='ANLS'`` — NOT a ``'GROUP'`` literal
— so the defensive literal-equality derivation alone would misread a real group
whose marker fields never spell "GROUP". ``isGroup`` is now the authoritative
marker; the literal spellings stay as fallback for payloads that omit it.
``exposureResourceId``/``exposureResourceType`` are confirmed RESPONSE
properties (IRP_INTEGRATION_FOLLOWUPS.md §8 resolved).

``_RealGateway`` is constructed directly with a stub client injected into
``_irp`` — no wheel import, no env, no HTTP.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import irp_gateway


class _StubClient:
    class _Analysis:
        def __init__(self, payload):
            self._payload = payload

        def get_analysis_by_id(self, analysis_id):
            return self._payload

    def __init__(self, payload):
        self.analysis = self._Analysis(payload)


def _gw(**managers) -> irp_gateway._RealGateway:
    gw = irp_gateway._RealGateway()
    gw._irp = SimpleNamespace(**managers)
    return gw


def _meta(payload) -> irp_gateway.AnalysisMetadata:
    gw = irp_gateway._RealGateway()
    gw._irp = _StubClient(payload)
    return gw.get_analysis_metadata(analysis_id=1)


def test_first_class_isgroup_boolean_is_authoritative():
    m = _meta({"isGroup": True, "groupType": "GRP"})
    assert m.is_group is True


def test_plain_live_analysis_is_not_group_and_pointer_promotable():
    # The confirmed live shape: isGroup=False, groupType='ANLS', pointer present.
    m = _meta({"isGroup": False, "groupType": "ANLS",
               "exposureResourceId": 3, "exposureResourceType": "PORTFOLIO"})
    assert m.is_group is False
    assert m.exposure_resource_id == "3"
    assert m.exposure_resource_type == "PORTFOLIO"


def test_group_literal_fallback_when_isgroup_absent():
    # Older/other payload shapes without isGroup: the defensive literals hold.
    assert _meta({"analysisType": "GROUP"}).is_group is True
    assert _meta({"analysisType": "EP"}).is_group is False


# ── non-dict single-item responses are FAILED reads, never empty successes ────────
# The worker's except path counts these as metadata/exposure failures and leaves
# the stored snapshot alone; coercing to {} would wipe a prior good snapshot.

def test_analysis_metadata_non_dict_response_raises():
    with pytest.raises(ValueError):
        _meta(None)
    with pytest.raises(ValueError):
        _meta(["not", "a", "dict"])


def test_portfolio_exposure_non_dict_response_raises():
    def _exposure(payload):
        gw = _gw(portfolio=SimpleNamespace(
            get_portfolio_metadata=lambda e, p: payload))
        return gw.get_portfolio_exposure(edm_irp_id=1, portfolio_irp_id=2)

    assert _exposure({"totalLocations": 5}).payload == {"totalLocations": 5}
    with pytest.raises(ValueError):
        _exposure(None)
    with pytest.raises(ValueError):
        _exposure("oops")


# ── defensive field-mapping of the paginated enumerations ─────────────────────────

def test_list_portfolios_maps_both_id_spellings_and_skips_incomplete_rows():
    rows = [
        {"id": 1, "name": "A"},
        {"portfolioId": 2, "portfolioName": "B"},   # alternate spellings
        {"name": "no-id — skipped"},
        {"id": 3},                                   # no name — skipped
    ]
    gw = _gw(portfolio=SimpleNamespace(
        search_portfolios_paginated=lambda edm_irp_id: rows))
    hits = gw.list_portfolios(edm_irp_id=9)
    assert [(h.irp_id, h.name) for h in hits] == [("1", "A"), ("2", "B")]


def test_search_treaties_keeps_idless_rows_and_stores_the_row_verbatim():
    rows = [
        {"treatyId": 1042, "treatyName": "Cat XoL", "treatyType": "CATA"},
        {"name": "Legacy no-id treaty"},   # kept (irp_id None) — display fidelity
        {"treatyId": 9},                   # no name — skipped
    ]
    gw = _gw(treaty=SimpleNamespace(
        search_treaties_paginated=lambda edm_irp_id: rows))
    hits = gw.search_treaties(edm_irp_id=9)
    assert [(t.irp_id, t.name) for t in hits] == [
        ("1042", "Cat XoL"), (None, "Legacy no-id treaty")]
    assert hits[0].attributes == rows[0]   # the whole row IS the attribute map


# ── the DataBridge exposure summary — script-based interim implementation ─────────
# get_edm_exposure_summary resolves the EDM's physical databaseName from RM's
# exposures search (matched on exposureId — names collide in RM) and runs the
# four set-based sql/databridge/ scripts through the wheel's generic executor,
# assembling {portfolioId(str): {portfolio_name, total_tiv, states,
# lines_of_business, currencies}}.

class _Frame:
    """A minimal DataFrame stand-in — the gateway only calls to_dict('records')."""

    def __init__(self, records):
        self._records = records

    def to_dict(self, orient):
        assert orient == "records"
        return list(self._records)


def _summary_gw(hits, results_by_script, calls=None):
    def execute_query_from_file(file_path, database):
        if calls is not None:
            calls.append((file_path, database))
        script = file_path.replace("\\", "/").rsplit("/", 1)[-1]
        return [_Frame(results_by_script.get(script, []))]

    return _gw(
        edm=SimpleNamespace(search_edms=lambda filter: hits),
        databridge=SimpleNamespace(
            execute_query_from_file=execute_query_from_file))


def test_edm_exposure_summary_assembles_per_portfolio_from_the_scripts():
    hits = [
        {"exposureId": 111, "exposureName": "EDM", "databaseName": "other_db"},
        {"exposureId": 42, "exposureName": "EDM", "databaseName": "edm_db"},
    ]
    results = {
        "portfolio_total_tiv.sql": [
            {"PortfolioId": 1, "PortfolioName": "A", "TotalTIV": 2.8e9},
            {"PortfolioId": 2, "PortfolioName": "B", "TotalTIV": 0},
        ],
        "portfolio_account_total.sql": [
            {"PortfolioId": 1, "PortfolioName": "A", "AccountTotal": 1701},
        ],
        "portfolio_states.sql": [
            {"PortfolioId": 1, "PortfolioName": "A", "State": "TX"},
            {"PortfolioId": 1, "PortfolioName": "A", "State": "FL"},
        ],
        "portfolio_lines_of_business.sql": [
            {"PortfolioId": 1, "PortfolioName": "A",
             "LineOfBusiness": "Commercial", "AccountCount": 812},
            {"PortfolioId": 1, "PortfolioName": "A",
             "LineOfBusiness": "Auto", "AccountCount": 900},
        ],
        "portfolio_currencies.sql": [
            {"PortfolioId": 1, "PortfolioName": "A", "Currency": "USD"},
        ],
    }
    calls: list = []
    gw = _summary_gw(hits, results, calls)

    summary = gw.get_edm_exposure_summary(edm_name="EDM", edm_irp_id=42)

    # keys stringified; lists sorted; portfolio 2 (no locations/policies) still
    # gets an entry from the TIV seed with empty lists. account_total and the
    # breakout_values container are the spec-005 additions (R11) — the
    # container's PRESENCE is what marks a post-005 summary; the lob entries
    # (value = its own label → null; accounts = the FR-007 numerator) are
    # sorted by value.
    assert summary == {
        "1": {"portfolio_name": "A", "total_tiv": 2.8e9,
              "states": ["FL", "TX"],
              "lines_of_business": ["Auto", "Commercial"],
              "currencies": ["USD"],
              "account_total": 1701, "breakout_values": {"lob": [
                  {"value": "Auto", "label": None, "accounts": 900},
                  {"value": "Commercial", "label": None, "accounts": 812}]}},
        "2": {"portfolio_name": "B", "total_tiv": 0.0,
              "states": [], "lines_of_business": [], "currencies": [],
              "account_total": None, "breakout_values": {}},
    }
    # every script ran against the databaseName of the exposureId-matched hit
    assert [db for _, db in calls] == ["edm_db"] * 5


def test_edm_exposure_summary_raises_when_database_name_unresolvable():
    gw = _summary_gw([{"exposureId": 1, "exposureName": "EDM"}], {})
    with pytest.raises(ValueError):
        # no hit matches the exposureId
        gw.get_edm_exposure_summary(edm_name="EDM", edm_irp_id=42)
    with pytest.raises(ValueError):
        # the matched hit carries no databaseName
        gw.get_edm_exposure_summary(edm_name="EDM", edm_irp_id=1)


# ── spec 005 (T031): breakout selection & composition response-shape parsing ──────
# The account id is nested DIFFERENTLY in each read, and a wrong key returns a
# plausible empty result rather than an error (W-15) — every wrong-key mistake
# in the probe run was silent. These tests pin the parsers to the RECORDED
# response bodies of the probe run.

from irp_integration.exceptions import IRPAPIError, IRPValidationError  # noqa: E402

from app.services.irp_gateway import (  # noqa: E402
    MAX_COMPOSED_FILTER_CHARS,
    DuplicatePortfolioNameError,
    _chunk_ids_by_filter_length,
)

# Recorded shapes (probe-findings W-15): searchAccounts rows carry a top-level
# accountId; searchPolicies rows carry accountId + lob.lobName.
ACCOUNT_ROWS = [{"accountId": 101, "accountName": "Acme"},
                {"accountId": 102, "accountName": "Bmee"},
                {"accountId": 103, "accountName": "Cmee"}]
POLICY_ROWS = [
    {"policyId": 1, "accountId": 101, "lob": {"lobId": 5, "lobName": "FLD Comm"}},
    {"policyId": 2, "accountId": 102, "lob": {"lobId": 5, "lobName": "FLD Comm"}},
    {"policyId": 3, "accountId": 102, "lob": {"lobId": 7, "lobName": "EQ Comm"}},
    {"policyId": 4, "accountId": 103, "lob": {"lobId": 9, "lobName": "Unplanned"}},
    {"policyId": 5},                       # no accountId/lob — skipped, not an error
]


def _selection_gw(policies=POLICY_ROWS, accounts=ACCOUNT_ROWS, filters=None):
    def search_policies_paginated(exposure_id, filter=""):
        if filters is not None:
            filters.append(filter)
        if isinstance(policies, Exception):
            raise policies
        return policies

    return _gw(portfolio=SimpleNamespace(
        search_accounts_by_portfolio_paginated=lambda e, p: accounts,
        search_policies_paginated=search_policies_paginated))


def test_select_lob_groups_client_side_on_the_recorded_shape():
    filters: list = []
    gw = _selection_gw(filters=filters)
    selection = gw.select_breakout_accounts(
        exposure_irp_id="42", source_portfolio_irp_id="1", dimension="lob",
        values=["FLD Comm", "EQ Comm", "No Match"])
    assert selection.accounts_by_value == {
        "FLD Comm": [101, 102],
        "EQ Comm": [102],       # a multi-LOB account lands in BOTH values (W-11)
        "No Match": [],         # empty, not an error — the worker zero-match-fails it
    }
    assert selection.errors_by_value == {}
    # scope always comes from an account-id list — no portfolio predicate (W-6)
    assert filters == ["accountId IN (101,102,103)"]


def test_select_lob_pagination_failure_fails_every_value_not_the_run():
    gw = _selection_gw(policies=IRPAPIError("page fingerprint repeated"))
    selection = gw.select_breakout_accounts(
        exposure_irp_id="42", source_portfolio_irp_id="1", dimension="lob",
        values=["FLD Comm", "EQ Comm"])
    # never proceed on a possibly-short id list (W-14)
    assert set(selection.errors_by_value) == {"FLD Comm", "EQ Comm"}
    assert selection.accounts_by_value == {}


def test_select_source_account_read_failure_raises():
    def boom(e, p):
        raise IRPAPIError("account enumeration incomplete")
    gw = _gw(portfolio=SimpleNamespace(
        search_accounts_by_portfolio_paginated=boom))
    with pytest.raises(IRPAPIError):
        gw.select_breakout_accounts(
            exposure_irp_id="42", source_portfolio_irp_id="1",
            dimension="lob", values=["FLD Comm"])


def test_select_chunks_by_composed_filter_length_not_id_count():
    # 7-digit ids fit roughly half as many per request as 1-4 digit ids — the
    # ceiling is characters, not ids (W-6).
    ids = list(range(1_000_000, 1_003_000))
    filters: list = []
    accounts = [{"accountId": i} for i in ids]
    gw = _selection_gw(policies=[], accounts=accounts, filters=filters)
    gw.select_breakout_accounts(exposure_irp_id="42",
                                source_portfolio_irp_id="1", dimension="lob",
                                values=["X"])
    assert len(filters) > 1                      # forced into several chunks
    assert all(len(f) <= MAX_COMPOSED_FILTER_CHARS for f in filters)
    seen = [int(t) for f in filters
            for t in f[len("accountId IN ("):-1].split(",")]
    assert seen == ids                           # nothing dropped, nothing reordered


def test_chunk_helper_respects_extra_clause_budget():
    suffix = ' AND admin1Code = "TX"'
    chunks = _chunk_ids_by_filter_length(list(range(10_000_000, 10_000_500)),
                                         suffix)
    for chunk in chunks:
        composed = f"accountId IN ({','.join(chunk)}){suffix}"
        assert len(composed) <= MAX_COMPOSED_FILTER_CHARS


# ── create → add → read-back composition ─────────────────────────────────────────

def _compose_gw(*, create_exc=None, name_taken=False, member_rows=None,
                manage_calls=None, search_hits=None):
    def create_portfolio(edm_name, portfolio_name, portfolio_number,
                         description):
        if create_exc is not None:
            raise create_exc
        return 431, {"portfolioName": portfolio_name}

    def manage_portfolio_accounts(e, p, *, accounts_to_add=None,
                                  accounts_to_remove=None):
        if manage_calls is not None:
            manage_calls.append(list(accounts_to_add))
        # `completed` counts ids NEWLY added — 0 on a healthy re-run (W-9)
        return {"addAccounts": {"completed": 0, "total": len(accounts_to_add)}}

    def search_portfolios(exposure_id, filter=""):
        return [{"portfolioId": 900}] if name_taken else []

    def search_portfolios_paginated(exposure_id, filter=""):
        return search_hits or []

    return _gw(portfolio=SimpleNamespace(
        create_portfolio=create_portfolio,
        manage_portfolio_accounts=manage_portfolio_accounts,
        search_portfolios=search_portfolios,
        search_portfolios_paginated=search_portfolios_paginated,
        search_accounts_by_portfolio_paginated=lambda e, p: member_rows or []))


def test_create_sub_portfolio_success_is_the_read_back_never_completed():
    manage_calls: list = []
    gw = _compose_gw(member_rows=[{"accountId": 101}, {"accountId": 102}],
                     manage_calls=manage_calls)
    result = gw.create_sub_portfolio(
        edm_name="EDM", exposure_irp_id="42", name="src - TX",
        number="P1-S-TX", description="Breakout of portfolio src by "
        "Geography (state): TX", account_ids=[101, 102])
    assert result.portfolio_irp_id == "431"
    # the add reported completed 0 — success comes from the read-back (W-9)
    assert result.account_count == 2
    assert manage_calls == [[101, 102]]


def test_duplicate_name_surfaces_as_the_distinct_error_type():
    # IRPValidationError + the name IS taken in RM → the adoption signal
    gw = _compose_gw(create_exc=IRPValidationError(
        "1 portfolios found with name src - TX, please use a unique name"),
        name_taken=True)
    with pytest.raises(DuplicatePortfolioNameError):
        gw.create_sub_portfolio(edm_name="EDM", exposure_irp_id="42",
                                name="src - TX", number="P1-S-TX",
                                description="d", account_ids=[101])


def test_length_violation_is_not_misread_as_duplicate_name():
    # IRPValidationError also covers an over-long name/number (W-10): when the
    # name is NOT taken, the original error propagates untouched.
    gw = _compose_gw(create_exc=IRPValidationError(
        "portfolio_name is 41 characters and exceeds the 40-character limit"),
        name_taken=False)
    with pytest.raises(IRPValidationError):
        gw.create_sub_portfolio(edm_name="EDM", exposure_irp_id="42",
                                name="x" * 41, number="P1-S-TX",
                                description="d", account_ids=[101])


def test_find_portfolio_by_number_returns_every_hit():
    gw = _compose_gw(search_hits=[
        {"portfolioId": 900, "portfolioName": "src - TX"},
        {"id": 901, "name": "src - TX (2)", "stampDate": "2026-08-01"},
    ])
    hits = gw.find_portfolio_by_number(exposure_irp_id="42", number="P1-S-TX")
    assert [(h.irp_id, h.name) for h in hits] == [
        ("900", "src - TX"), ("901", "src - TX (2)")]


def test_fetch_portfolio_stamp_matches_on_portfolio_id():
    rows = [{"portfolioId": 1, "portfolioName": "A",
             "stampDate": "2026-07-31T09:15:00.000Z"},
            {"id": 2, "name": "B"}]
    gw = _gw(portfolio=SimpleNamespace(
        search_portfolios_paginated=lambda e: rows))
    assert gw.fetch_portfolio_stamp(
        exposure_irp_id=42, portfolio_irp_id="1") == "2026-07-31T09:15:00.000Z"
    assert gw.fetch_portfolio_stamp(
        exposure_irp_id=42, portfolio_irp_id="2") is None   # no stampDate field
    assert gw.fetch_portfolio_stamp(
        exposure_irp_id=42, portfolio_irp_id="99") is None  # portfolio gone
