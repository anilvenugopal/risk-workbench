"""Unit tests for ``analysis_service.list_broker_analyses`` (spec 004 US3, T035).

The RDM page's read model (FR-030/FR-031/FR-035, R8): broker analyses grouped
by ``rdm_id`` (an analysis applied across M EDMs shown ONCE), parsed
``settings_metadata`` (missing/partial → blank, never error), and ``is_group``
surfaced. No analysis is attributed to a portfolio (8/4 D8).
"""

from __future__ import annotations

import json
import uuid

from app.services import analysis_service
from app.services._common import _utcnow
from db import execute_command

# The documented RM analysis metadata shape (search-analyses / get-analysis —
# IRP knowledge base 2026-07-23): flat camelCase fields.
SETTINGS_FULL = {
    "analysisId": 5521, "analysisName": "Meridian AEP — All Perils",
    "analysisType": "EP", "engineType": "DLM", "engineVersion": "23.0",
    "peril": "Earthquake", "subperil": "Fire Following",
    "region": "North America", "currencyCode": "USD",
    "lineOfBusiness": "Commercial",
    "exposureResourceId": 501, "exposureResourceType": "PORTFOLIO",
}
SETTINGS_PARTIAL = {"analysisType": "EP", "peril": "Wind"}

# The LIVE payload shape confirmed 2026-07-24 (first real sync against the RM
# tenant): currency arrives as an OBJECT keyed currencyCode/currencyName, the
# event-rate scheme as the eventRateSchemeNames LIST, and PLA as the
# lossAmplification label. The curated view must read all three.
SETTINGS_LIVE = {
    "analysisType": "Exceedance Probability", "analysisFramework": "ELT",
    "engineType": "DLM", "engineVersion": "RL23",
    "peril": "Windstorm", "subPeril": "Surge Only",
    "region": "North Atlantic (including Hawaii)",
    "currency": {"currencyName": "US Dollar", "currencyCode": "USD"},
    "lossAmplification": "Building, Contents, BI",
    "eventRateSchemeNames": ["LT 2026"],
    "analysisMode": "Distributed",
    "exposureResourceId": 3, "exposureResourceType": "PORTFOLIO",
}


def _mk(table: str, **cols) -> str:
    row_id = cols.pop("id", str(uuid.uuid4()))
    now = _utcnow()
    keys = ["id", *cols.keys(), "inserted_at", "updated_at"]
    execute_command(
        f"INSERT INTO {table} ({', '.join(keys)}) "
        f"VALUES ({', '.join(':' + k for k in keys)})",
        {"id": row_id, **cols, "inserted_at": now, "updated_at": now},
        connection="WORKBENCH")
    return row_id


def _edm(name: str) -> str:
    return _mk("irp_edm", name=name, status="ready")


def _rdm(name: str, irp_id: int | None = None) -> str:
    return _mk("irp_rdm", name=name, status="ready", irp_id=irp_id)


def _analysis(*, rdm_id: str, edm_id: str, irp_id: str, name: str = "A",
              settings: dict | None = None, is_group: bool = False,
              row_id: str | None = None) -> str:
    cols: dict = dict(rdm_id=rdm_id, edm_id=edm_id, irp_id=irp_id,
                      name=name, status_code="ready",
                      settings_metadata=(json.dumps(settings) if settings
                                         else None),
                      is_group=(1 if is_group else 0))
    if row_id is not None:
        cols["id"] = row_id  # pin ORDER BY a.id ties for deterministic tests
    return _mk("irp_analysis", **cols)


def test_settings_metadata_parsed_and_missing_fields_blank_not_error(
        iteration2_db):
    rdm, edm = _rdm("R"), _edm("E")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="1", settings=SETTINGS_FULL)
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="2", settings=SETTINGS_PARTIAL)
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="3", settings=None)  # never backfilled

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    by_irp = {a.irp_id: a for a in g.analyses}

    full = by_irp["1"]
    assert full.settings["engineType"] == "DLM"     # raw snapshot parsed
    assert full.display.analysis_type == "EP"       # curated view model
    assert full.display.engine_type == "DLM"
    assert full.display.engine_version == "23.0"
    assert full.display.peril == "Earthquake"
    assert full.display.peril_secondary == "Fire Following"
    assert full.display.region == "North America"
    assert full.display.currency == "USD"
    assert full.display.line_of_business == "Commercial"

    partial = by_irp["2"]                            # missing fields → blank
    assert partial.display.analysis_type == "EP"
    assert partial.display.engine_type is None
    assert partial.display.currency is None
    assert partial.display.rate_vintage is None

    empty = by_irp["3"]                              # no snapshot → still renders
    assert empty.settings is None
    assert empty.display.analysis_type is None


def test_live_payload_shape_currency_object_rate_list_pla_label(iteration2_db):
    rdm, edm = _rdm("R"), _edm("E")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="1", settings=SETTINGS_LIVE)
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="2",
              settings=dict(SETTINGS_LIVE, eventRateSchemeNames=[]))

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    by_irp = {a.irp_id: a for a in g.analyses}

    a = by_irp["1"]
    assert a.display.currency == "USD"                # object → its code
    assert a.display.pla == "Building, Contents, BI"  # the real label field
    assert a.display.event_rate_scheme == "LT 2026"   # list → joined
    assert a.display.peril_secondary == "Surge Only"
    assert a.display.engine == "DLM · RL23"
    assert a.display.analysis_mode == "Distributed"
    assert by_irp["2"].display.event_rate_scheme is None  # empty list → blank


def test_group_analysis_surfaced_as_group(iteration2_db):
    rdm, edm = _rdm("R"), _edm("E")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="9", is_group=True)

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    assert g.analyses[0].is_group is True


def test_only_broker_rows_of_this_rdm_and_no_deleted(iteration2_db):
    rdm, other, edm = _rdm("R"), _rdm("R2"), _edm("E")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="1")
    _analysis(rdm_id=other, edm_id=edm, irp_id="2")
    deleted = _analysis(rdm_id=rdm, edm_id=edm, irp_id="3")
    execute_command("UPDATE irp_analysis SET deleted_at=:n WHERE id=:i",
                    {"n": _utcnow(), "i": deleted}, connection="WORKBENCH")

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    assert {a.irp_id for a in g.analyses} == {"1"}
