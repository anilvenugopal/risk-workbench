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


def test_lost_insert_race_recovers_by_overwriting_in_place(iteration2_db):
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


def test_unrecoverable_violation_raises_instead_of_dropping_the_row(
        iteration2_db):
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
