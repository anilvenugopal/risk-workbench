"""Unit tests for the prune → re-breakout → sync sequence (T-16 — the Aug 6
demo bug).

The failure: break out → delete the sub-portfolios in Risk Modeler → sync
(``prune_missing`` soft-deletes their rows) → break out again. The re-run
regenerated the identical names (the collision universe is live-only) and
inserted second rows for the same lineage triples; the NEXT sync's
resurrect-by-name revived the dead ghosts, violating
``uq_irp_portfolio_breakout`` inside ``prune_missing`` and failing every
subsequent sync of the EDM.

The fix has two halves, both exercised here: ``save_generated_portfolio`` reclaims the
soft-deleted lineage row in place, and ``prune_missing``'s resurrect-by-name
skips generated rows (irp_id match still resurrects them).

Runs on the SQLite unit mirror — its ``uq_irp_portfolio_breakout`` partial
unique index enforces the same live-rows-only key as SQL Server's.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from app.services import portfolio_service
from app.services._common import _utcnow
from db import execute, execute_command, execute_one
from tests.unit.breakout_rows import (
    mk_edm,
    mk_portfolio,
    rerun_breakout_job,
    run_breakout_job,
)

SOURCE_SEEN = ("1", "usfl_commercial")   # the source portfolio survives in RM


def _mk_job(edm_id: str, portfolio_id: str, actor_id) -> str:
    jid = str(uuid.uuid4())
    plan = [{"value": v, "label": None, "name": f"usfl_commercial - {v}",
             "number": f"P1-S-{v}", "accounts": 1} for v in ("FL", "GA")]
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, "
        "status_code, input_data, attempt_count, inserted_at, updated_at) "
        "VALUES (:i, 'analyst_request', :r, 'run_breakout_state', 'pending', "
        ":d, 0, :now, :now)",
        {"i": jid, "r": portfolio_id,
         "d": json.dumps({"edm_id": edm_id, "portfolio_id": portfolio_id,
                          "dimension": "state", "actor_id": str(actor_id),
                          "plan": plan}),
         "now": datetime.utcnow()}, connection="WORKBENCH")
    return jid


def _generated_rows(source_id: str) -> list[dict]:
    """Every row of the source's lineage keys — INCLUDING soft-deleted."""
    return execute(
        "SELECT id, name, irp_id, breakout_value, deleted_at "
        "FROM irp_portfolio WHERE source_portfolio_id = :s "
        "ORDER BY breakout_value",
        {"s": source_id}, connection="WORKBENCH")


def test_demo_sequence_reclaims_rows_and_next_sync_stays_healthy(
        iteration2_db, fake_irp):
    edm_id = mk_edm(name="EDM")
    source_id = mk_portfolio(edm_id)
    fake_irp.selection_by_value = {"FL": [1], "GA": [2]}
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a)

    # 1. breakout — two generated rows, RM ids 431/432
    assert run_breakout_job(jid, "state")["status_code"] == "succeeded"
    first = _generated_rows(source_id)
    assert [(r["irp_id"], r["deleted_at"] is None) for r in first] == [
        ("431", True), ("432", True)]

    # 2. the analyst deletes both sub-portfolios in Risk Modeler; sync prunes
    fake_irp.taken_portfolio_names.clear()       # the names are free in RM again
    assert portfolio_service.prune_missing(
        edm_id=edm_id, seen=[SOURCE_SEEN], now=_utcnow()) == 2
    assert all(r["deleted_at"] is not None for r in _generated_rows(source_id))

    # 3. re-breakout — same plan, same names, NEW RM portfolios (433/434);
    #    each write reclaims its soft-deleted row: one row per triple
    job = rerun_breakout_job(jid, "state")
    assert job["status_code"] == "succeeded"
    out = json.loads(job["output_data"])
    assert (out["created"], out["failed"]) == (2, 0)
    rows = _generated_rows(source_id)
    assert [(r["irp_id"], r["deleted_at"]) for r in rows] == [
        ("433", None), ("434", None)]
    assert {r["id"] for r in rows} == {r["id"] for r in first}  # reused rows

    # 4. the next sync enumerates the new sub-portfolios — no unique violation,
    #    nothing pruned, nothing wrongly resurrected
    pruned = portfolio_service.prune_missing(
        edm_id=edm_id,
        seen=[SOURCE_SEEN, ("433", "usfl_commercial - FL"),
              ("434", "usfl_commercial - GA")],
        now=_utcnow())
    assert pruned == 0
    assert [r["deleted_at"] for r in _generated_rows(source_id)] == [None, None]


def test_resurrect_by_name_skips_generated_rows(iteration2_db):
    # A NEW RM portfolio reusing a dead sub-portfolio's name must not revive
    # the dead row: name-match resurrection is for broker-arrived portfolios
    # only (T-16). The upsert then inserts a fresh live row for the new
    # portfolio instead of stamping the dead one (the _UPDATE_BY_NAME live
    # filter), so the enumeration lands somewhere visible.
    edm_id = mk_edm(name="EDM")
    source_id = mk_portfolio(edm_id)
    write = portfolio_service.save_generated_portfolio(
        edm_id, name="usfl_commercial - FL", irp_id="431",
        source_portfolio_id=source_id, dimension_code="state", value="FL",
        actor_id=None)
    portfolio_service.prune_missing(edm_id=edm_id, seen=[SOURCE_SEEN],
                                    now=_utcnow())

    seen = [SOURCE_SEEN, ("999", "usfl_commercial - FL")]
    portfolio_service.prune_missing(edm_id=edm_id, seen=seen, now=_utcnow())
    dead = execute_one(
        "SELECT deleted_at FROM irp_portfolio WHERE id = :i",
        {"i": write.portfolio_id}, connection="WORKBENCH")
    assert dead["deleted_at"] is not None        # name match did NOT revive it

    portfolio_service.upsert_portfolio_detail(
        edm_id=edm_id, irp_id="999", name="usfl_commercial - FL",
        exposure_detail={"metrics": {}}, as_of=_utcnow())
    rows = execute(
        "SELECT irp_id, deleted_at FROM irp_portfolio "
        "WHERE edm_id = :e AND name = 'usfl_commercial - FL' "
        "ORDER BY irp_id",
        {"e": edm_id}, connection="WORKBENCH")
    assert [(r["irp_id"], r["deleted_at"] is None) for r in rows] == [
        ("431", False), ("999", True)]           # dead row untouched, new row live


def test_resurrect_by_irp_id_still_revives_generated_rows(iteration2_db):
    # Only the NAME leg is restricted: a generated row whose RM portfolio
    # reappears under its own id (enumeration transient healed) resurrects.
    edm_id = mk_edm(name="EDM")
    source_id = mk_portfolio(edm_id)
    write = portfolio_service.save_generated_portfolio(
        edm_id, name="usfl_commercial - FL", irp_id="431",
        source_portfolio_id=source_id, dimension_code="state", value="FL",
        actor_id=None)
    portfolio_service.prune_missing(edm_id=edm_id, seen=[SOURCE_SEEN],
                                    now=_utcnow())

    portfolio_service.prune_missing(
        edm_id=edm_id, seen=[SOURCE_SEEN, ("431", "renamed in RM")],
        now=_utcnow())
    row = execute_one(
        "SELECT deleted_at FROM irp_portfolio WHERE id = :i",
        {"i": write.portfolio_id}, connection="WORKBENCH")
    assert row["deleted_at"] is None
