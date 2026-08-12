"""Unit tests for the EDM-page analyses read (spec 004 US3, rescoped 8/5 D15).

``analysis_service.list_edm_analyses(edm_id)`` returns every RDM in the EDM's
package, each grouped with its broker analyses — including RDMs with none.
Package membership is the whole association: an analysis carries no ``edm_id``
(the RDM is imported standalone), so a packageless EDM lists nothing.
``edm_service.get_edm_detail`` carries the groups. No analysis is attributed
to a portfolio (8/4 D8), even though ``exposure_resource_id`` is still
captured.
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
    """One package holding an EDM and two source RDMs, portfolios 501/502;
    analyses: two carrying an exposure pointer, a group, and one with none."""
    pkg = _mk("package", name="Pkg")
    edm = _mk("irp_edm", name="meridian_edm_2026", status="ready", package_id=pkg)
    rdm1 = _mk("irp_rdm", name="meridian_q4_results", status="ready", irp_id=88,
               package_id=pkg)
    rdm2 = _mk("irp_rdm", name="retro_2025_view", status="ready", irp_id=71,
               package_id=pkg)
    now = _utcnow()
    portfolio_service.upsert_portfolio_detail(
        edm_id=edm, irp_id="501", name="Primary 2026",
        exposure_detail={"metrics": {}}, as_of=now)
    portfolio_service.upsert_portfolio_detail(
        edm_id=edm, irp_id="502", name="Excess 2026",
        exposure_detail={"metrics": {}}, as_of=now)
    mk_analysis = lambda **kw: _mk("irp_analysis",  # noqa: E731
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


# ── 8/5 D15: the read is package-scoped ────────────────────────────────────────


def _seed_package_pair():
    """Wendy's paired book: one package, two EDMs (in-force + projected), two
    RDMs — analyses applied against EDM 1 only; a third, packageless RDM's
    analysis also targets EDM 1."""
    pkg = _mk("package", name="Pkg")
    edm1 = _mk("irp_edm", name="edm_in_force", status="ready", package_id=pkg)
    edm2 = _mk("irp_edm", name="edm_projected", status="ready", package_id=pkg)
    rdm1 = _mk("irp_rdm", name="rdm_in_force", status="ready", irp_id=88,
               package_id=pkg)
    rdm2 = _mk("irp_rdm", name="rdm_projected", status="ready", irp_id=71,
               package_id=pkg)
    stray = _mk("irp_rdm", name="stray_rdm", status="ready", irp_id=99)
    mk = lambda **kw: _mk("irp_analysis", status_code="ready", **kw)  # noqa: E731
    mk(rdm_id=rdm1, irp_id="1", name="AEP", is_group=0)
    mk(rdm_id=rdm2, irp_id="2", name="OEP", is_group=0)
    mk(rdm_id=stray, irp_id="3", name="Stray", is_group=0)
    return pkg, edm1, edm2


def test_every_rdm_in_the_package_is_listed_on_every_edm_page(iteration2_db):
    _, edm1, edm2 = _seed_package_pair()
    # both EDM pages list both package RDMs identically — "if you have 12
    # analyses in one, you have 12 analyses in the other"
    for edm in (edm1, edm2):
        groups = analysis_service.list_edm_analyses(edm_id=edm)
        assert [g.rdm_name for g in groups] == ["rdm_in_force", "rdm_projected"]
        assert [len(g.analyses) for g in groups] == [1, 1]


def test_rdm_with_no_analyses_still_gets_an_empty_group(iteration2_db):
    pkg, edm1, _ = _seed_package_pair()
    _mk("irp_rdm", name="rdm_empty", status="ready", irp_id=12, package_id=pkg)

    groups = analysis_service.list_edm_analyses(edm_id=edm1)
    assert [g.rdm_name for g in groups] == [
        "rdm_in_force", "rdm_projected", "rdm_empty"]
    assert len(groups[2].analyses) == 0


def test_packageless_edm_lists_nothing(iteration2_db):
    # Package membership is the only EDM↔RDM association — an EDM in no package
    # has no RDMs, so there is nothing to group.
    edm = _mk("irp_edm", name="lone_edm", status="ready")
    assert analysis_service.list_edm_analyses(edm_id=edm) == []
