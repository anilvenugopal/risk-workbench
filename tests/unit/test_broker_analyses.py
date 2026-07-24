"""Unit tests for ``analysis_service.list_broker_analyses`` (spec 004 US3, T035).

The RDM page's read model (FR-030/FR-031/FR-035/FR-036, R8/R9): broker
analyses grouped by ``rdm_id`` (an analysis applied across M EDMs shown ONCE),
parsed ``settings_metadata`` (missing/partial → blank, never error),
``is_group`` surfaced, and the **portfolio linkage resolved at read time** —
``exposure_resource_id`` matched against ``irp_portfolio.irp_id`` within the
same ``edm_id``; order-independent (portfolio backfilled before OR after the
analysis).
"""

from __future__ import annotations

import json
import uuid

from app.services import analysis_service, portfolio_service
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
              exposure_resource_id: str | None = None) -> str:
    return _mk("irp_analysis", rdm_id=rdm_id, edm_id=edm_id, irp_id=irp_id,
               name=name, status_code="ready",
               settings_metadata=(json.dumps(settings) if settings else None),
               is_group=(1 if is_group else 0),
               exposure_resource_id=exposure_resource_id)


def _portfolio(edm_id: str, irp_id: str, name: str) -> None:
    portfolio_service.upsert_portfolio_detail(
        edm_id=edm_id, irp_id=irp_id, name=name,
        exposure_detail={"metrics": {}}, as_of=_utcnow())


def test_analysis_across_m_edms_shown_once_grouped_by_rdm(iteration2_db):
    rdm = _rdm("meridian_q4_results", irp_id=88)
    e1, e2 = _edm("edm_a"), _edm("edm_b")
    # DATA_MODEL §6: M handle rows share ONE irp_id, one per (RDM×EDM) pair
    _analysis(rdm_id=rdm, edm_id=e1, irp_id="5521", name="AEP")
    _analysis(rdm_id=rdm, edm_id=e2, irp_id="5521", name="AEP")
    _analysis(rdm_id=rdm, edm_id=e1, irp_id="5522", name="OEP")

    groups = analysis_service.list_broker_analyses(rdm_id=rdm)

    assert len(groups) == 1
    g = groups[0]
    assert g.rdm_name == "meridian_q4_results"
    assert str(g.rdm_irp_id) == "88"
    assert {a.irp_id for a in g.analyses} == {"5521", "5522"}  # shown once
    aep = next(a for a in g.analyses if a.irp_id == "5521")
    assert sorted(aep.edm_names) == ["edm_a", "edm_b"]  # spans both EDMs
    assert g.edm_count == 2


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


def test_group_analysis_surfaced_as_group_never_resolved(iteration2_db):
    rdm, edm = _rdm("R"), _edm("E")
    _portfolio(edm, "501", "Primary 2026")
    # a group row even WITH a pointer must render Group, not a portfolio link
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="9", is_group=True,
              exposure_resource_id="501")

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    a = g.analyses[0]
    assert a.is_group is True
    assert a.portfolio is None  # ui.md §4 precedence: is_group wins


def test_portfolio_linkage_resolves_within_same_edm_only(iteration2_db):
    rdm = _rdm("R")
    e1, e2 = _edm("E1"), _edm("E2")
    _portfolio(e1, "501", "Primary 2026")
    # same pointer value exists as a portfolio only in E1
    _analysis(rdm_id=rdm, edm_id=e1, irp_id="1", exposure_resource_id="501")
    _analysis(rdm_id=rdm, edm_id=e2, irp_id="2", exposure_resource_id="501")
    _analysis(rdm_id=rdm, edm_id=e1, irp_id="3", exposure_resource_id=None)
    _analysis(rdm_id=rdm, edm_id=e1, irp_id="4", exposure_resource_id="9999")

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    by_irp = {a.irp_id: a for a in g.analyses}
    assert by_irp["1"].portfolio is not None
    assert by_irp["1"].portfolio.name == "Primary 2026"
    assert by_irp["2"].portfolio is None   # matching irp_id but WRONG edm
    assert by_irp["3"].portfolio is None   # null pointer (non-portfolio) → not linked
    assert by_irp["4"].portfolio is None   # unmatched pointer → not linked


def test_resolution_is_order_independent(iteration2_db):
    # analysis captured BEFORE the portfolio backfilled — read-time join
    rdm, edm = _rdm("R"), _edm("E")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="1", exposure_resource_id="501")
    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    assert g.analyses[0].portfolio is None  # portfolio not there yet — not linked

    _portfolio(edm, "501", "Primary 2026")  # backfill lands AFTER the analysis
    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    assert g.analyses[0].portfolio.name == "Primary 2026"  # self-heals on read


def test_only_broker_rows_of_this_rdm_and_no_deleted(iteration2_db):
    rdm, other, edm = _rdm("R"), _rdm("R2"), _edm("E")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="1")
    _analysis(rdm_id=other, edm_id=edm, irp_id="2")
    deleted = _analysis(rdm_id=rdm, edm_id=edm, irp_id="3")
    execute_command("UPDATE irp_analysis SET deleted_at=:n WHERE id=:i",
                    {"n": _utcnow(), "i": deleted}, connection="WORKBENCH")

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    assert {a.irp_id for a in g.analyses} == {"1"}


def test_analysis_counts_populated(iteration2_db):
    rdm, rdm2, edm = _rdm("R"), _rdm("R2"), _edm("E")
    _portfolio(edm, "501", "Primary 2026")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="1", exposure_resource_id="501")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="2")
    _analysis(rdm_id=rdm2, edm_id=edm, irp_id="3", is_group=True)

    counts = analysis_service.analysis_counts(edm_id=edm)
    assert counts.total == 3       # FR-050 — no longer empty
    assert counts.rdm_count == 2
    assert counts.linked == 1      # only the resolved, non-group row
