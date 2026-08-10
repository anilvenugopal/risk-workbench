"""Unit tests for breakout lineage on the EDM detail read (spec 005 US3, T054).

``edm_service.get_edm_detail`` returns each generated portfolio with its
IMMEDIATE source portfolio's name and the dimension label (FR-014) — a chained
breakout is never rendered as a chain. A generated row carries no
``exposure_detail`` until the follow-up ``backfill_edm_detail`` fills it in, and
that pending state renders gracefully, never as an error.

Runs on the SQLite unit mirror (``iteration2_db``).
"""

from __future__ import annotations

import uuid

from app.services import edm_service, portfolio_service
from app.services._common import _utcnow
from db import execute_command

SNAP_A = {
    "metrics": {"totalLocations": 8240, "totalAccounts": 1120,
                "totalPolicies": 1180, "perilsExposed": "WS, EQ"},
    "summary": {"portfolio_name": "A", "currencies": ["USD"],
                "states": ["FL", "TX"], "lines_of_business": ["Commercial"]},
}


def test_get_edm_detail_lineage_rows_pending_state_and_immediate_source(
        iteration2_db):
    edm_id = str(uuid.uuid4())
    now = _utcnow()
    execute_command(
        "INSERT INTO irp_edm (id, name, status, inserted_at, updated_at) "
        "VALUES (:id, 'EDM', 'ready', :now, :now)",
        {"id": edm_id, "now": now}, connection="WORKBENCH")
    portfolio_service.upsert_portfolio_detail(
        edm_id=edm_id, irp_id="1", name="A", exposure_detail=SNAP_A, as_of=now)
    a = portfolio_service.list_portfolios(edm_id=edm_id)[0]
    b = portfolio_service.insert_generated(
        edm_id, name="A - X", irp_id="11", source_portfolio_id=a.id,
        dimension_code="lob", value="X", actor_id=None)
    portfolio_service.insert_generated(
        edm_id, name="A - X - TX", irp_id="12",
        source_portfolio_id=b.portfolio_id, dimension_code="state",
        value="TX", actor_id=None)

    detail = edm_service.get_edm_detail(edm_id)
    by_name = {p.name: p for p in detail.portfolios}
    assert by_name["A"].source_name is None          # broker-arrived: unchanged
    generated = by_name["A - X"]
    assert generated.exposure_detail is None         # pending until backfill
    assert generated.source_name == "A"
    assert generated.breakout_dimension_label == "Line of business"
    assert generated.breakout_value == "X"
    chained = by_name["A - X - TX"]
    assert chained.source_name == "A - X"            # immediate source only
    assert chained.breakout_dimension_label == "Geography (state)"
