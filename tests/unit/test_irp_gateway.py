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
        # US2 (T044/P-12): the rewritten script returns Admin1Code as the
        # value, a nullable Admin1Name label (absent until geocoding), and an
        # account count. "BY" (Bayern) enumerates through the same field as
        # the US states — no separate mode for non-US divisions.
        "portfolio_states.sql": [
            {"PortfolioId": 1, "PortfolioName": "A", "Admin1Code": "TX",
             "Admin1Name": None, "AccountCount": 412},
            {"PortfolioId": 1, "PortfolioName": "A", "Admin1Code": "FL",
             "Admin1Name": "FLORIDA", "AccountCount": 1241},
            {"PortfolioId": 1, "PortfolioName": "A", "Admin1Code": "BY",
             "Admin1Name": "BAYERN", "AccountCount": 9},
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
    # container's PRESENCE is what marks a post-005 summary; entries are
    # sorted by value. states holds Admin1Code (P-12); a state's label is
    # Admin1Name where geocoded and None otherwise; a lob value is its own
    # label → None.
    assert summary == {
        "1": {"portfolio_name": "A", "total_tiv": 2.8e9,
              "states": ["BY", "FL", "TX"],
              "lines_of_business": ["Auto", "Commercial"],
              "currencies": ["USD"],
              "account_total": 1701, "breakout_values": {
                  "lob": [
                      {"value": "Auto", "label": None, "accounts": 900},
                      {"value": "Commercial", "label": None, "accounts": 812}],
                  "state": [
                      {"value": "BY", "label": "BAYERN", "accounts": 9},
                      {"value": "FL", "label": "FLORIDA", "accounts": 1241},
                      {"value": "TX", "label": None, "accounts": 412}]}},
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


# ── spec 005 (T031): breakout selection — the DataBridge read ─────────────────────
# One parameterized, portfolio-scoped script per dimension resolves every value
# at once (R1, revised 2026-08-05): the REST selection could not complete on a
# 248,000-account portfolio (the wheel's 1,000-page completeness ceiling, W-20).
# ACCGRPID from the script IS the accountId RM's account operations accept.

from irp_integration.exceptions import IRPValidationError  # noqa: E402

from app.services.irp_gateway import DuplicatePortfolioNameError  # noqa: E402

SELECTION_ROWS = [
    {"Value": "FLD Comm", "AccountId": 101},
    {"Value": "FLD Comm", "AccountId": 102},
    {"Value": "EQ Comm", "AccountId": 102},     # multi-LOB account (W-11)
    {"Value": "Unplanned", "AccountId": 103},   # present in the EDM, not requested
    {"Value": None, "AccountId": 104},          # incomplete rows skipped
    {"Value": "FLD Comm", "AccountId": None},
]

_EDM_HITS = [{"exposureId": 111, "exposureName": "EDM", "databaseName": "other_db"},
             {"exposureId": 42, "exposureName": "EDM", "databaseName": "edm_db"}]


def _selection_gw(records=SELECTION_ROWS, calls=None, edm_searches=None):
    def execute_query_from_file(file_path, params=None, database=None):
        if calls is not None:
            script = file_path.replace("\\", "/").rsplit("/", 1)[-1]
            calls.append((script, params, database))
        if isinstance(records, Exception):
            raise records
        return [_Frame(records)]

    def search_edms(filter):
        if edm_searches is not None:
            edm_searches.append(filter)
        return _EDM_HITS

    return _gw(
        edm=SimpleNamespace(search_edms=search_edms),
        databridge=SimpleNamespace(
            execute_query_from_file=execute_query_from_file))


def test_select_lob_maps_the_script_rows_per_requested_value():
    calls: list = []
    gw = _selection_gw(calls=calls)
    selection = gw.select_breakout_accounts(
        edm_name="EDM", exposure_irp_id="42", source_portfolio_irp_id="1",
        dimension="lob", values=["FLD Comm", "EQ Comm", "No Match"])
    assert selection.accounts_by_value == {
        "FLD Comm": [101, 102],
        "EQ Comm": [102],       # a multi-LOB account lands in BOTH values (W-11)
        "No Match": [],         # empty, not an error — the worker zero-match-fails it
    }
    assert selection.errors_by_value == {}
    # one portfolio-scoped script run against the exposureId-matched database
    assert calls == [("breakout_lob_accounts.sql", {"portfolio_id": 1}, "edm_db")]


def test_select_state_maps_the_script_rows_per_requested_value():
    # US2 (T045/P-12): the state dimension runs breakout_state_accounts.sql —
    # Value is Admin1Code, mirroring the rewritten portfolio_states.sql joins,
    # so the filter vocabulary matches the stored summary. Admin1Name is never
    # a filter input.
    calls: list = []
    state_rows = [
        {"Value": "TX", "AccountId": 101},
        {"Value": "TX", "AccountId": 102},
        {"Value": "BY", "AccountId": 103},   # non-US division, same field
        {"Value": "CA", "AccountId": 102},   # multi-state account (W-3/W-11)
    ]
    gw = _selection_gw(records=state_rows, calls=calls)
    selection = gw.select_breakout_accounts(
        edm_name="EDM", exposure_irp_id="42", source_portfolio_irp_id="1",
        dimension="state", values=["TX", "CA", "BY", "MT"])
    assert selection.accounts_by_value == {
        "TX": [101, 102],
        "CA": [102],        # the multi-state account lands in BOTH values
        "BY": [103],
        "MT": [],           # empty, not an error — zero-match fails downstream
    }
    assert selection.errors_by_value == {}
    assert calls == [("breakout_state_accounts.sql", {"portfolio_id": 1},
                      "edm_db")]


def test_select_databridge_failure_raises_and_fails_the_job():
    # the single set-based read is all-or-nothing: never proceed on a result
    # that cannot be shown complete (the W-14 rule, now enforced by raising)
    gw = _selection_gw(records=RuntimeError("DataBridge connection refused"))
    with pytest.raises(RuntimeError):
        gw.select_breakout_accounts(
            edm_name="EDM", exposure_irp_id="42", source_portfolio_irp_id="1",
            dimension="lob", values=["FLD Comm"])


def test_select_unknown_dimension_raises():
    gw = _selection_gw()
    with pytest.raises(ValueError):
        gw.select_breakout_accounts(
            edm_name="EDM", exposure_irp_id="42", source_portfolio_irp_id="1",
            dimension="region", values=["X"])


def test_selection_database_name_is_resolved_once_and_cached():
    # the breakout loop's per-entry reads share one exposures-search resolution
    edm_searches: list = []
    gw = _selection_gw(edm_searches=edm_searches)
    for _ in range(3):
        gw.select_breakout_accounts(
            edm_name="EDM", exposure_irp_id="42", source_portfolio_irp_id="1",
            dimension="lob", values=["FLD Comm"])
    assert len(edm_searches) == 1


# ── create → add → read-back composition ─────────────────────────────────────────

def _compose_gw(*, create_exc=None, name_taken=False, member_count=0,
                manage_calls=None, search_hits=None, count_calls=None):
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

    def execute_query_from_file(file_path, params=None, database=None):
        # the read-back count (R1, revised 2026-08-05): one DataBridge scalar
        if count_calls is not None:
            script = file_path.replace("\\", "/").rsplit("/", 1)[-1]
            count_calls.append((script, params, database))
        return [_Frame([{"AccountCount": member_count}])]

    return _gw(
        portfolio=SimpleNamespace(
            create_portfolio=create_portfolio,
            manage_portfolio_accounts=manage_portfolio_accounts,
            search_portfolios=search_portfolios,
            search_portfolios_paginated=search_portfolios_paginated),
        edm=SimpleNamespace(search_edms=lambda filter: _EDM_HITS),
        databridge=SimpleNamespace(
            execute_query_from_file=execute_query_from_file))


def test_create_sub_portfolio_success_is_the_read_back_never_completed():
    manage_calls: list = []
    count_calls: list = []
    gw = _compose_gw(member_count=2, manage_calls=manage_calls,
                     count_calls=count_calls)
    result = gw.create_sub_portfolio(
        edm_name="EDM", exposure_irp_id="42", name="src - TX",
        number="P1-S-TX", description="Breakout of portfolio src by "
        "Geography (state): TX", account_ids=[101, 102])
    assert result.portfolio_irp_id == "431"
    # the add reported completed 0 — success comes from the read-back (W-9),
    # counted via DataBridge against the CREATED portfolio's id
    assert result.account_count == 2
    assert manage_calls == [[101, 102]]
    assert count_calls == [("portfolio_member_count.sql",
                            {"portfolio_id": 431}, "edm_db")]


def test_populate_chunks_the_add_and_returns_the_databridge_count():
    # no single PATCH carries more than 1,000 ids; the returned count is the
    # DataBridge read-back, not arithmetic over the chunks
    manage_calls: list = []
    gw = _compose_gw(member_count=2500, manage_calls=manage_calls)
    result = gw.populate_sub_portfolio(
        edm_name="EDM", exposure_irp_id="42", portfolio_irp_id="431",
        account_ids=list(range(1, 2501)))
    assert [len(c) for c in manage_calls] == [1000, 1000, 500]
    assert result.account_count == 2500


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
