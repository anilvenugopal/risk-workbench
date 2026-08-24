"""The ``_snapshot_upsert`` race-recovery branch (_common.py) against SQL Server.

The UNIQUE-violation branch is reachable only via a concurrent insert between
the pre-check UPDATEs and the INSERT, so these tests simulate the
race deterministically: a connection proxy makes the pre-check UPDATEs report
rowcount 0 (as if the racing writer's row weren't there yet) while the
conflicting row already exists, forcing the INSERT to violate the constraint.

Two contracts are pinned:
  • the winner's row is overwritten in place (a dedup hit, not an error);
  • if NEITHER recovery update matches, the violation is re-raised — a loud,
    recoverable job failure, never a silently dropped row (the SQL Server
    NULL-irp_id case).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.services import portfolio_service
from app.services._common import _json, _snapshot_upsert, _utcnow
from db import execute, execute_command, get_connection


def _edm() -> str:
    """A real irp_edm row — irp_portfolio.edm_id is a foreign key."""
    edm_id = str(uuid.uuid4())
    execute_command("INSERT INTO irp_edm (id, name) VALUES (:id, 'SnapEDM')",
                    {"id": edm_id}, connection="WORKBENCH")
    return edm_id


class _FakeMissConn:
    """Delegate everything to the real connection, but report rowcount 0 for
    the first ``miss`` UPDATE statements — the deterministic stand-in for 'the
    racing writer's row landed after our pre-checks ran'."""

    def __init__(self, conn, miss: int):
        self._conn = conn
        self._miss = miss

    def begin_nested(self):
        return self._conn.begin_nested()

    def execute(self, stmt, params=None):
        if str(stmt).lstrip().upper().startswith("UPDATE") and self._miss > 0:
            self._miss -= 1
            return SimpleNamespace(rowcount=0)
        return self._conn.execute(stmt, params)


def _params(*, edm_id: str, irp: str | None, name: str) -> dict:
    return {"id": str(uuid.uuid4()), "edm": edm_id, "irp": irp, "name": name,
            "snap": _json({"metrics": {"totalLocations": 7}}),
            "asof": _utcnow(), "now": _utcnow()}


def _seed_row(conn, *, edm_id: str, irp: str | None, name: str) -> None:
    conn.execute(text(portfolio_service._INSERT),
                 _params(edm_id=edm_id, irp=irp, name=name))


def _rows(edm_id: str) -> list[dict]:
    return execute(
        "SELECT irp_id, name, exposure_detail FROM irp_portfolio "
        "WHERE edm_id=:e ORDER BY name",
        {"e": edm_id}, connection="WORKBENCH")


def test_lost_insert_race_recovers_by_overwriting_in_place(workbench_db):
    edm_id = _edm()
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            _seed_row(conn, edm_id=edm_id, irp="501", name="Old Name")
            # Both pre-checks "miss" (rowcount 0); the INSERT then violates
            # UNIQUE(edm_id, irp_id); the recovery update_by_irp must win.
            _snapshot_upsert(
                _FakeMissConn(conn, miss=2),
                _params(edm_id=edm_id, irp="501", name="New Name"),
                update_by_irp=portfolio_service._UPDATE_BY_IRP,
                update_by_name=portfolio_service._UPDATE_BY_NAME,
                insert=portfolio_service._INSERT)
    rows = _rows(edm_id)
    assert len(rows) == 1                    # dedup hit — never a duplicate
    assert rows[0]["name"] == "New Name"     # overwritten in place


def test_snapshot_upsert_preserves_lineage_and_the_list_reads_it(
        workbench_db):
    # US3 (T054/FR-014): the backfill's in-place overwrite touches only the
    # snapshot columns — the three lineage columns and inserted_by survive —
    # and the lineage-aware list joins the immediate source's name and the
    # dimension label onto the generated row.
    edm_id = _edm()
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            _seed_row(conn, edm_id=edm_id, irp="1", name="usfl_commercial")
    source = portfolio_service.list_portfolios(edm_id=edm_id)[0]
    portfolio_service.insert_generated(
        edm_id, name="usfl_commercial - TX", irp_id="431",
        source_portfolio_id=source.id, dimension_code="state", value="TX",
        actor_id=None)

    # the backfill later enumerates the generated portfolio and overwrites
    # its snapshot in place (same edm_id + irp_id)
    portfolio_service.upsert_portfolio_detail(
        edm_id=edm_id, irp_id="431", name="usfl_commercial - TX",
        exposure_detail={"metrics": {"totalAccounts": 220}}, as_of=_utcnow())

    rows = portfolio_service.list_portfolios(edm_id=edm_id)
    generated = next(r for r in rows if r.name == "usfl_commercial - TX")
    assert generated.exposure_detail == {"metrics": {"totalAccounts": 220}}
    assert generated.source_portfolio_id == source.id
    assert generated.source_name == "usfl_commercial"
    assert generated.breakout_dimension_code == "state"
    assert generated.breakout_dimension_label == "Geography - State"
    assert generated.breakout_value == "TX"
    # the source snapshot carries no summary → no display label, and the
    # template falls back to the code (P-12 as revised 2026-08-05)
    assert generated.breakout_value_label is None
    # the broker-arrived source row carries no lineage
    assert next(r for r in rows if r.id == source.id).source_name is None


def test_list_resolves_the_display_label_from_the_source_summary(
        workbench_db):
    # P-12 as revised 2026-08-05: the generated row's breakout_value stays the
    # Admin1Code ("200"), and the list resolves its display label ("Puerto
    # Rico") from the label stored beside that value in the SOURCE portfolio's
    # summary — read-time only, nothing stored on the generated row.
    edm_id = _edm()
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            _seed_row(conn, edm_id=edm_id, irp="1", name="cbhu")
    source = portfolio_service.list_portfolios(edm_id=edm_id)[0]
    portfolio_service.upsert_portfolio_detail(
        edm_id=edm_id, irp_id="1", name="cbhu",
        exposure_detail={"summary": {"breakout_values": {"state": [
            {"value": "010", "label": "St Croix", "accounts": 74},
            {"value": "200", "label": "Puerto Rico", "accounts": 2437},
        ]}}}, as_of=_utcnow())
    portfolio_service.insert_generated(
        edm_id, name="cbhu - Puerto Rico", irp_id="431",
        source_portfolio_id=source.id, dimension_code="state", value="200",
        actor_id=None)

    rows = portfolio_service.list_portfolios(edm_id=edm_id)
    generated = next(r for r in rows if r.breakout_value == "200")
    assert generated.breakout_value_label == "Puerto Rico"
    # a value the rewritten summary no longer carries resolves to None
    portfolio_service.insert_generated(
        edm_id, name="cbhu - 030", irp_id="432",
        source_portfolio_id=source.id, dimension_code="state", value="030",
        actor_id=None)
    rows = portfolio_service.list_portfolios(edm_id=edm_id)
    assert next(r for r in rows
                if r.breakout_value == "030").breakout_value_label is None


def test_unrecoverable_violation_raises_instead_of_dropping_the_row(
        workbench_db):
    # The SQL Server shape: the INSERT violates but neither recovery update
    # matches (NULL irp_id ⇒ update_by_irp skipped; a different name ⇒
    # update_by_name misses). Silently returning here would drop the row and
    # report success — the contract is a loud re-raise instead.
    edm_id = _edm()
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            _seed_row(conn, edm_id=edm_id, irp="501", name="T1")
            with pytest.raises(IntegrityError):
                _snapshot_upsert(
                    _FakeMissConn(conn, miss=4),  # pre-checks AND recovery miss
                    _params(edm_id=edm_id, irp="501", name="T2"),
                    update_by_irp=portfolio_service._UPDATE_BY_IRP,
                    update_by_name=portfolio_service._UPDATE_BY_NAME,
                    insert=portfolio_service._INSERT)
    assert [r["name"] for r in _rows(edm_id)] == ["T1"]  # original row intact
