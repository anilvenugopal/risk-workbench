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

import pytest

from app.services import edm_service, portfolio_service
from app.services._common import _utcnow
from db import execute, execute_command, execute_one

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


def _setup_source(edm_name: str = "EDM") -> tuple[str, object]:
    edm_id = str(uuid.uuid4())
    now = _utcnow()
    execute_command(
        "INSERT INTO irp_edm (id, name, status, inserted_at, updated_at) "
        "VALUES (:id, :n, 'ready', :now, :now)",
        {"id": edm_id, "n": edm_name, "now": now}, connection="WORKBENCH")
    portfolio_service.upsert_portfolio_detail(
        edm_id=edm_id, irp_id="1", name="A", exposure_detail=SNAP_A, as_of=now)
    return edm_id, portfolio_service.list_portfolios(edm_id=edm_id)[0]


def test_insert_generated_reclaims_soft_deleted_lineage_row(iteration2_db):
    # T-16 (demo bug): breakout → sub-portfolio deleted in RM → sync prunes →
    # re-breakout writes the same (source, dimension, value) under a NEW RM
    # identity. The write must reuse the soft-deleted row — cleared deleted_at,
    # new irp_id/name — never insert a second row for the triple.
    edm_id, a = _setup_source()
    first = portfolio_service.insert_generated(
        edm_id, name="A - FL", irp_id="11", source_portfolio_id=a.id,
        dimension_code="state", value="FL", actor_id=iteration2_db.user_a)
    execute_command(
        "UPDATE irp_portfolio SET deleted_at = :now WHERE id = :i",
        {"now": _utcnow(), "i": first.portfolio_id}, connection="WORKBENCH")

    write = portfolio_service.insert_generated(
        edm_id, name="A - FL", irp_id="21", source_portfolio_id=a.id,
        dimension_code="state", value="FL", actor_id=iteration2_db.user_b)

    assert write.created is True
    assert write.portfolio_id == first.portfolio_id
    rows = execute(
        "SELECT id, name, irp_id, deleted_at, inserted_by, updated_by "
        "FROM irp_portfolio WHERE source_portfolio_id = :s "
        "AND breakout_dimension_code = 'state' AND breakout_value = 'FL'",
        {"s": a.id}, connection="WORKBENCH")
    assert len(rows) == 1                            # no ghost twin
    row = rows[0]
    assert row["deleted_at"] is None
    assert (row["irp_id"], row["name"]) == ("21", "A - FL")
    assert row["inserted_by"] == iteration2_db.user_a   # first confirmer kept
    assert row["updated_by"] == iteration2_db.user_b


def test_dead_row_with_conflicting_lineage_still_refuses(iteration2_db):
    # A soft-deleted row holding RM portfolio 11 as the state=FL breakout is
    # still that breakout's record — adopting 11 under another value refuses
    # rather than silently moving the portfolio between breakout keys.
    edm_id, a = _setup_source()
    first = portfolio_service.insert_generated(
        edm_id, name="A - FL", irp_id="11", source_portfolio_id=a.id,
        dimension_code="state", value="FL", actor_id=None)
    execute_command(
        "UPDATE irp_portfolio SET deleted_at = :now WHERE id = :i",
        {"now": _utcnow(), "i": first.portfolio_id}, connection="WORKBENCH")

    with pytest.raises(ValueError, match="already the state=FL breakout"):
        portfolio_service.adopt_generated(
            edm_id, name="A - TX", irp_id="11", source_portfolio_id=a.id,
            dimension_code="state", value="TX", actor_id=None)
    row = execute_one(
        "SELECT deleted_at FROM irp_portfolio WHERE id = :i",
        {"i": first.portfolio_id}, connection="WORKBENCH")
    assert row["deleted_at"] is not None             # left exactly as it was


def test_adopt_generated_revives_soft_deleted_rm_id_match(iteration2_db):
    # The (edm_id, irp_id) pre-check sees soft-deleted rows: adopting an RM
    # portfolio whose row the prune killed revives that row and stamps the
    # lineage, instead of violating uq_irp_portfolio_edm_irp on insert.
    edm_id, a = _setup_source()
    first = portfolio_service.insert_generated(
        edm_id, name="A - FL", irp_id="11", source_portfolio_id=a.id,
        dimension_code="state", value="FL", actor_id=None)
    execute_command(
        "UPDATE irp_portfolio SET deleted_at = :now WHERE id = :i",
        {"now": _utcnow(), "i": first.portfolio_id}, connection="WORKBENCH")

    write = portfolio_service.adopt_generated(
        edm_id, name="A - FL", irp_id="11", source_portfolio_id=a.id,
        dimension_code="state", value="FL", actor_id=None)

    assert write.created is True
    assert write.portfolio_id == first.portfolio_id
    row = execute_one(
        "SELECT deleted_at FROM irp_portfolio WHERE id = :i",
        {"i": first.portfolio_id}, connection="WORKBENCH")
    assert row["deleted_at"] is None
