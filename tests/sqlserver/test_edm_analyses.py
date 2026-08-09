"""Unit tests for the EDM-page analyses read (spec 004 US3, T035b — FR-037).

``analysis_service.list_edm_analyses(edm_id)`` returns the EDM's broker
analyses grouped by source ``rdm_id`` with resolved portfolios; the per-
portfolio bucketing keeps ONLY clearly-linked analyses inside a portfolio
(group / unresolved rows stay standalone-only — ui.md §4), and
``edm_service.get_edm_detail`` carries both.
"""

from __future__ import annotations

import json
import uuid

from app.services import analysis_service, edm_service, portfolio_service
from app.services._common import _utcnow
from db import execute_command


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


def _seed_edm_with_analyses():
    """One EDM, two source RDMs, portfolios 501/502; analyses: linked to 501,
    linked to 502, a group, and an unresolved one."""
    edm = _mk("irp_edm", name="meridian_edm_2026", status="ready")
    rdm1 = _mk("irp_rdm", name="meridian_q4_results", status="ready", irp_id=88)
    rdm2 = _mk("irp_rdm", name="retro_2025_view", status="ready", irp_id=71)
    now = _utcnow()
    portfolio_service.upsert_portfolio_detail(
        edm_id=edm, irp_id="501", name="Primary 2026",
        exposure_detail={"metrics": {}}, as_of=now)
    portfolio_service.upsert_portfolio_detail(
        edm_id=edm, irp_id="502", name="Excess 2026",
        exposure_detail={"metrics": {}}, as_of=now)
    mk_analysis = lambda **kw: _mk("irp_analysis", edm_id=edm,  # noqa: E731
                                   source_rdm_name="src", status_code="ready",
                                   **kw)
    mk_analysis(rdm_id=rdm1, irp_id="1", name="AEP", exposure_resource_id="501",
                is_group=0,
                settings_metadata=json.dumps({"analysisType": "EP"}))
    mk_analysis(rdm_id=rdm1, irp_id="2", name="OEP", exposure_resource_id="502",
                is_group=0)
    mk_analysis(rdm_id=rdm2, irp_id="3", name="Suite", is_group=1)
    mk_analysis(rdm_id=rdm2, irp_id="4", name="Rollup", is_group=0)  # unresolved
    return edm, rdm1, rdm2


def test_list_edm_analyses_groups_by_source_rdm_with_resolution(iteration2_db):
    edm, rdm1, rdm2 = _seed_edm_with_analyses()

    groups = analysis_service.list_edm_analyses(edm_id=edm)

    assert [g.rdm_name for g in groups] == [
        "meridian_q4_results", "retro_2025_view"]
    g1 = groups[0]
    assert {a.irp_id for a in g1.analyses} == {"1", "2"}
    assert {a.portfolio.name for a in g1.analyses} == {
        "Primary 2026", "Excess 2026"}
    g2 = groups[1]
    by_irp = {a.irp_id: a for a in g2.analyses}
    assert by_irp["3"].is_group is True and by_irp["3"].portfolio is None
    assert by_irp["4"].is_group is False and by_irp["4"].portfolio is None


def test_bucketing_keeps_group_and_unresolved_standalone_only(iteration2_db):
    edm, _, _ = _seed_edm_with_analyses()
    groups = analysis_service.list_edm_analyses(edm_id=edm)

    buckets = analysis_service.bucket_by_portfolio(groups)

    linked_ids = {a.irp_id for rows in buckets.values() for a in rows}
    assert linked_ids == {"1", "2"}          # group + unresolved NEVER bucketed
    # each bucket keys on the workbench portfolio id
    by_name = {rows[0].portfolio.name: [a.irp_id for a in rows]
               for rows in buckets.values()}
    assert by_name == {"Primary 2026": ["1"], "Excess 2026": ["2"]}


def test_get_edm_detail_carries_analyses_and_portfolio_buckets(iteration2_db):
    edm, _, _ = _seed_edm_with_analyses()

    detail = edm_service.get_edm_detail(edm)

    # the standalone RDM-grouped list rides the payload (FR-037)
    assert [g.rdm_name for g in detail.analyses] == [
        "meridian_q4_results", "retro_2025_view"]
    # each portfolio carries ONLY its linked analyses, inline (US3/FR-037)
    by_name = {p.name: p for p in detail.portfolios}
    assert [a.irp_id for a in by_name["Primary 2026"].analyses] == ["1"]
    assert [a.irp_id for a in by_name["Excess 2026"].analyses] == ["2"]


def test_resolution_order_independent_on_the_edm_page(iteration2_db):
    # the portfolio lands AFTER the analysis was captured — same read heals
    edm = _mk("irp_edm", name="E", status="ready")
    rdm = _mk("irp_rdm", name="R", status="ready")
    _mk("irp_analysis", edm_id=edm, rdm_id=rdm, irp_id="1", is_group=0,
        source_rdm_name="R", status_code="ready", exposure_resource_id="777")
    [g] = analysis_service.list_edm_analyses(edm_id=edm)
    assert g.analyses[0].portfolio is None

    portfolio_service.upsert_portfolio_detail(
        edm_id=edm, irp_id="777", name="Late Portfolio",
        exposure_detail={"metrics": {}}, as_of=_utcnow())
    [g] = analysis_service.list_edm_analyses(edm_id=edm)
    assert g.analyses[0].portfolio.name == "Late Portfolio"
