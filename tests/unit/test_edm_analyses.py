"""Unit tests for the EDM-page analyses read (spec 004 US3, T035b — FR-037).

``analysis_service.list_edm_analyses(edm_id)`` returns the EDM's broker
analyses grouped by source ``rdm_id``, and ``edm_service.get_edm_detail``
carries them. No analysis is attributed to a portfolio (8/4 D8), even though
``exposure_resource_id`` is still captured.
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
    """One EDM, two source RDMs, portfolios 501/502; analyses: two carrying an
    exposure pointer, a group, and one with no pointer."""
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
                                   status_code="ready", **kw)
    mk_analysis(rdm_id=rdm1, irp_id="1", name="AEP", exposure_resource_id="501",
                is_group=0,
                settings_metadata=json.dumps({"analysisType": "EP"}))
    mk_analysis(rdm_id=rdm1, irp_id="2", name="OEP", exposure_resource_id="502",
                is_group=0)
    mk_analysis(rdm_id=rdm2, irp_id="3", name="Suite", is_group=1)
    mk_analysis(rdm_id=rdm2, irp_id="4", name="Rollup", is_group=0)
    return edm, rdm1, rdm2


def test_list_edm_analyses_groups_by_source_rdm(iteration2_db):
    edm, rdm1, rdm2 = _seed_edm_with_analyses()

    groups = analysis_service.list_edm_analyses(edm_id=edm)

    assert [g.rdm_name for g in groups] == [
        "meridian_q4_results", "retro_2025_view"]
    assert {a.irp_id for a in groups[0].analyses} == {"1", "2"}
    by_irp = {a.irp_id: a for a in groups[1].analyses}
    assert by_irp["3"].is_group is True
    assert by_irp["4"].is_group is False


def test_get_edm_detail_carries_the_rdm_grouped_analyses(iteration2_db):
    edm, _, _ = _seed_edm_with_analyses()

    detail = edm_service.get_edm_detail(edm)

    assert [g.rdm_name for g in detail.analyses] == [
        "meridian_q4_results", "retro_2025_view"]
