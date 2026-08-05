"""Unit tests for the breakout lineage writes (spec 005 T012 — FR-009/R3/R7).

``portfolio_service.insert_generated`` / ``adopt_generated`` are the ONE write
path for generated portfolios: the three lineage columns are set together, the
source portfolio must be live in the SAME EDM, ``inserted_by`` carries the
confirming analyst, and a race duplicate on the filtered unique index
``uq_irp_portfolio_breakout`` is absorbed as ``created=False``
(→ ``skipped_existing``), never raised.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from app.services import portfolio_service
from db import execute, execute_command, execute_one


def _mk_edm(status: str = "ready") -> str:
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, irp_id, status, inserted_at, updated_at) "
        "VALUES (:i, :n, 90001, :s, :now, :now)",
        {"i": edm_id, "n": f"edm-{edm_id[:8]}", "s": status,
         "now": datetime.utcnow()}, connection="WORKBENCH")
    return edm_id


def _mk_portfolio(edm_id: str, *, name: str = "usfl_commercial",
                  irp_id: str | None = "1") -> str:
    pid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_portfolio (id, edm_id, name, irp_id, inserted_at, "
        "updated_at) VALUES (:i, :e, :n, :irp, :now, :now)",
        {"i": pid, "e": edm_id, "n": name, "irp": irp_id,
         "now": datetime.utcnow()}, connection="WORKBENCH")
    return pid


def _live_rows(edm_id: str) -> list[dict]:
    return execute(
        "SELECT id, name, irp_id, source_portfolio_id, breakout_dimension_code, "
        "breakout_value, inserted_by FROM irp_portfolio "
        "WHERE edm_id = :e AND deleted_at IS NULL ORDER BY name",
        {"e": edm_id}, connection="WORKBENCH")


def test_insert_generated_writes_lineage_together_with_inserted_by(iteration2_db):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    actor = iteration2_db.user_a

    result = portfolio_service.insert_generated(
        edm_id, name="usfl_commercial - TX", irp_id="431",
        source_portfolio_id=source_id, dimension_code="state", value="TX",
        actor_id=actor)

    assert result.created is True
    row = portfolio_service.find_generated(source_id, "state", "TX")
    assert row is not None
    assert row["id"] == result.portfolio_id
    assert row["name"] == "usfl_commercial - TX"
    assert row["irp_id"] == "431"
    stored = execute_one(
        "SELECT source_portfolio_id, breakout_dimension_code, breakout_value, "
        "inserted_by FROM irp_portfolio WHERE id = :i",
        {"i": result.portfolio_id}, connection="WORKBENCH")
    assert stored["source_portfolio_id"] == source_id
    assert stored["breakout_dimension_code"] == "state"
    assert stored["breakout_value"] == "TX"
    assert stored["inserted_by"] == actor  # FR-015 — the confirming analyst


@pytest.mark.parametrize("dimension,value", [("", "TX"), ("state", "")])
def test_lineage_columns_must_be_set_together(iteration2_db, dimension, value):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    with pytest.raises(ValueError, match="set together"):
        portfolio_service.insert_generated(
            edm_id, name="x", irp_id="431", source_portfolio_id=source_id,
            dimension_code=dimension, value=value,
            actor_id=iteration2_db.user_a)


def test_source_portfolio_must_be_in_the_same_edm(iteration2_db):
    edm_a, edm_b = _mk_edm(), _mk_edm()
    source_in_b = _mk_portfolio(edm_b)
    with pytest.raises(ValueError, match="same EDM"):
        portfolio_service.insert_generated(
            edm_a, name="x", irp_id="431", source_portfolio_id=source_in_b,
            dimension_code="state", value="TX", actor_id=iteration2_db.user_a)


def test_deleted_source_portfolio_is_refused(iteration2_db):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    execute_command(
        "UPDATE irp_portfolio SET deleted_at = :now WHERE id = :i",
        {"now": datetime.utcnow(), "i": source_id}, connection="WORKBENCH")
    with pytest.raises(ValueError, match="missing or deleted"):
        portfolio_service.insert_generated(
            edm_id, name="x", irp_id="431", source_portfolio_id=source_id,
            dimension_code="state", value="TX", actor_id=iteration2_db.user_a)


def test_race_duplicate_on_lineage_key_reports_skipped_not_raised(iteration2_db):
    # Two writers (redelivered job / concurrent identical breakout) insert the
    # same (source, dimension, value): the loser hits uq_irp_portfolio_breakout
    # and gets the winner's row back with created=False (→ skipped_existing).
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    first = portfolio_service.insert_generated(
        edm_id, name="usfl_commercial - TX", irp_id="431",
        source_portfolio_id=source_id, dimension_code="state", value="TX",
        actor_id=iteration2_db.user_a)
    second = portfolio_service.insert_generated(
        edm_id, name="usfl_commercial - TX (2)", irp_id="432",
        source_portfolio_id=source_id, dimension_code="state", value="TX",
        actor_id=iteration2_db.user_b)

    assert second.created is False
    assert second.portfolio_id == first.portfolio_id
    generated = [r for r in _live_rows(edm_id) if r["source_portfolio_id"]]
    assert len(generated) == 1  # the constraint, not the pre-check, is the guarantee


def test_same_key_different_dimension_or_value_both_insert(iteration2_db):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    a = portfolio_service.insert_generated(
        edm_id, name="usfl_commercial - TX", irp_id="431",
        source_portfolio_id=source_id, dimension_code="state", value="TX",
        actor_id=iteration2_db.user_a)
    b = portfolio_service.insert_generated(
        edm_id, name="usfl_commercial - CA", irp_id="432",
        source_portfolio_id=source_id, dimension_code="state", value="CA",
        actor_id=iteration2_db.user_a)
    c = portfolio_service.insert_generated(
        edm_id, name="usfl_commercial - FLD Comm", irp_id="433",
        source_portfolio_id=source_id, dimension_code="lob", value="FLD Comm",
        actor_id=iteration2_db.user_a)
    assert a.created and b.created and c.created


def test_soft_deleted_generated_row_does_not_block_recreation(iteration2_db):
    # The unique index is filtered on deleted_at IS NULL: a pruned generated
    # portfolio never blocks re-creation (data-model §1).
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    first = portfolio_service.insert_generated(
        edm_id, name="usfl_commercial - TX", irp_id="431",
        source_portfolio_id=source_id, dimension_code="state", value="TX",
        actor_id=iteration2_db.user_a)
    execute_command(
        "UPDATE irp_portfolio SET deleted_at = :now WHERE id = :i",
        {"now": datetime.utcnow(), "i": first.portfolio_id},
        connection="WORKBENCH")
    assert portfolio_service.find_generated(source_id, "state", "TX") is None

    second = portfolio_service.insert_generated(
        edm_id, name="usfl_commercial - TX", irp_id="440",
        source_portfolio_id=source_id, dimension_code="state", value="TX",
        actor_id=iteration2_db.user_a)
    assert second.created is True
    assert second.portfolio_id != first.portfolio_id


def test_adopt_claims_backfill_created_row_in_place(iteration2_db):
    # A crash between the RM create and the row write leaves an RM portfolio a
    # later backfill enumerates as a PLAIN row (no lineage). Adoption stamps the
    # lineage on that row rather than violating UNIQUE(edm_id, irp_id).
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    portfolio_service.upsert_portfolio_detail(
        edm_id=edm_id, irp_id="431", name="usfl_commercial - TX",
        exposure_detail={"metrics": {}, "summary": None, "stamp_date": None},
        as_of=datetime.utcnow())

    result = portfolio_service.adopt_generated(
        edm_id, name="usfl_commercial - TX", irp_id="431",
        source_portfolio_id=source_id, dimension_code="state", value="TX",
        actor_id=iteration2_db.user_a)

    assert result.created is True
    generated = [r for r in _live_rows(edm_id) if r["source_portfolio_id"]]
    assert len(generated) == 1  # claimed in place — no second row for irp_id 431
    assert generated[0]["irp_id"] == "431"
    assert generated[0]["breakout_value"] == "TX"
    assert generated[0]["inserted_by"] == iteration2_db.user_a
