"""Tests that ``edm_service.get_edm_detail`` surfaces the derived
EDM-aggregate rollup (spec 004 US4, FR-040), and that an EDM with no
backfilled snapshot carries ``aggregate is None`` so the caller renders the
pending state (FR-043).
"""

from __future__ import annotations

import uuid

from app.services import edm_service, portfolio_service
from app.services._common import _utcnow
from db import execute_command
from tests.unit.test_edm_detail_rollup import SNAP_A, SNAP_B


def test_get_edm_detail_surfaces_the_derived_aggregate(iteration2_db):
    edm_id = str(uuid.uuid4())
    now = _utcnow()
    execute_command(
        "INSERT INTO irp_edm (id, name, status, inserted_at, updated_at) "
        "VALUES (:id, 'EDM', 'ready', :now, :now)",
        {"id": edm_id, "now": now}, connection="WORKBENCH")
    portfolio_service.upsert_portfolio_detail(
        edm_id=edm_id, irp_id="1", name="A", exposure_detail=SNAP_A, as_of=now)
    portfolio_service.upsert_portfolio_detail(
        edm_id=edm_id, irp_id="2", name="B", exposure_detail=SNAP_B, as_of=now)

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.aggregate is not None
    assert detail.aggregate.locations == 12140
    assert detail.aggregate.portfolio_count == 2


def test_get_edm_detail_aggregate_none_renders_pending_state(iteration2_db):
    # an EDM with no backfilled snapshot — aggregate is None, never an error
    edm_id = str(uuid.uuid4())
    now = _utcnow()
    execute_command(
        "INSERT INTO irp_edm (id, name, status, inserted_at, updated_at) "
        "VALUES (:id, 'EDM', 'ready', :now, :now)",
        {"id": edm_id, "now": now}, connection="WORKBENCH")

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.aggregate is None
    assert detail.detail_state == "unavailable"  # the pending/unavailable box
