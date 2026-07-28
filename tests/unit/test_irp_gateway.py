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
        "portfolio_states.sql": [
            {"PortfolioId": 1, "PortfolioName": "A", "State": "TX"},
            {"PortfolioId": 1, "PortfolioName": "A", "State": "FL"},
        ],
        "portfolio_lines_of_business.sql": [
            {"PortfolioId": 1, "PortfolioName": "A",
             "LineOfBusiness": "Commercial"},
        ],
        "portfolio_currencies.sql": [
            {"PortfolioId": 1, "PortfolioName": "A", "Currency": "USD"},
        ],
    }
    calls: list = []
    gw = _summary_gw(hits, results, calls)

    summary = gw.get_edm_exposure_summary(edm_name="EDM", edm_irp_id=42)

    # keys stringified; lists sorted; portfolio 2 (no locations/policies) still
    # gets an entry from the TIV seed with empty lists
    assert summary == {
        "1": {"portfolio_name": "A", "total_tiv": 2.8e9,
              "states": ["FL", "TX"], "lines_of_business": ["Commercial"],
              "currencies": ["USD"]},
        "2": {"portfolio_name": "B", "total_tiv": 0.0,
              "states": [], "lines_of_business": [], "currencies": []},
    }
    # every script ran against the databaseName of the exposureId-matched hit
    assert [db for _, db in calls] == ["edm_db"] * 4


def test_edm_exposure_summary_raises_when_database_name_unresolvable():
    gw = _summary_gw([{"exposureId": 1, "exposureName": "EDM"}], {})
    with pytest.raises(ValueError):
        # no hit matches the exposureId
        gw.get_edm_exposure_summary(edm_name="EDM", edm_irp_id=42)
    with pytest.raises(ValueError):
        # the matched hit carries no databaseName
        gw.get_edm_exposure_summary(edm_name="EDM", edm_irp_id=1)
