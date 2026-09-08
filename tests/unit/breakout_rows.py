"""Row makers for the spec-005 breakout tests — raw INSERTs against the SQLite
WORKBENCH the ``iteration2_db`` fixture registers, shared by the gate, plan,
page-state, worker, prune/re-run, group, and route modules.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from app.workers import portfolio_jobs
from db import execute, execute_command, execute_one

AS_OF = "2026-08-01 00:00:00"
RM_STAMP = "2026-07-31T09:15:00.000Z"

SUMMARY = {
    "portfolio_name": "usfl_commercial",
    "total_tiv": 3.0e10,
    "states": ["CA", "TX"],
    "lines_of_business": ["EQ Comm", "FLD Comm"],
    "currencies": ["USD"],
    "account_total": 1701,
    "breakout_values": {
        "state": [{"value": "TX", "label": "TEXAS", "accounts": 220},
                  {"value": "CA", "label": "CALIFORNIA", "accounts": 1481}],
        "lob": [{"value": "FLD Comm", "label": None, "accounts": 900},
                {"value": "EQ Comm", "label": None, "accounts": 801}],
    },
    # Measured per account by the coverage scripts (FR-007 as revised
    # 2026-08-05) — NOT derivable from the per-value counts above: every one of
    # the 1,701 accounts carries a state and none carries two, while the lob
    # counts sum to the same 1,701 with 60 accounts carrying two and 60
    # carrying none.
    "breakout_coverage": {
        "state": {"covered": 1701, "multi_value": 0},
        "lob": {"covered": 1641, "multi_value": 60},
    },
}

# A summary the spec-004 builder wrote: states hold the mixed name/code
# vocabulary and there is no breakout_values key at all.
PRE_ITERATION_SUMMARY = {
    "portfolio_name": "usfl_commercial",
    "total_tiv": 3.0e10,
    "states": ["CALIFORNIA", "TX"],
    "lines_of_business": ["FLD Comm"],
    "currencies": ["USD"],
}


def mk_edm(*, name: str | None = None, status: str = "ready",
           irp_id: int | None = 90001, deleted: bool = False,
           now: datetime | None = None) -> str:
    edm_id = str(uuid.uuid4())
    now = now or datetime.utcnow()
    execute_command(
        "INSERT INTO irp_edm (id, name, irp_id, status, deleted_at, "
        "inserted_at, updated_at) VALUES (:i, :n, :irp, :s, :d, :now, :now)",
        {"i": edm_id, "n": name or f"edm-{edm_id[:8]}", "irp": irp_id,
         "s": status, "d": (now if deleted else None), "now": now},
        connection="WORKBENCH")
    return edm_id


def mk_portfolio(edm_id: str, *, name: str = "usfl_commercial",
                 irp_id: str | None = "1", summary: dict | None = SUMMARY,
                 stamp: str | None = RM_STAMP, as_of: str | None = AS_OF,
                 detail: dict | str | None = "auto", deleted: bool = False,
                 now: datetime | None = None) -> str:
    if detail == "auto":
        detail = {"metrics": {"totalAccounts": 1701}, "summary": summary,
                  "stamp_date": stamp}
    pid = str(uuid.uuid4())
    now = now or datetime.utcnow()
    execute_command(
        "INSERT INTO irp_portfolio (id, edm_id, name, irp_id, exposure_detail, "
        "as_of, deleted_at, inserted_at, updated_at) "
        "VALUES (:i, :e, :n, :irp, :x, :a, :d, :now, :now)",
        {"i": pid, "e": edm_id, "n": name, "irp": irp_id,
         "x": (detail if isinstance(detail, str) or detail is None
               else json.dumps(detail)),
         "a": as_of, "d": (now if deleted else None), "now": now},
        connection="WORKBENCH")
    return pid


def mk_generated_portfolio(edm_id: str, source_id: str, *, dimension: str,
                           value: str, irp_id: str,
                           now: datetime | None = None) -> None:
    now = now or datetime.utcnow()
    execute_command(
        "INSERT INTO irp_portfolio (id, edm_id, name, irp_id, "
        "source_portfolio_id, breakout_dimension_code, breakout_value, "
        "inserted_at, updated_at) VALUES (:i, :e, :n, :irp, :s, :d, :v, "
        ":now, :now)",
        {"i": str(uuid.uuid4()), "e": edm_id, "n": f"generated - {value}",
         "irp": irp_id, "s": source_id, "d": dimension, "v": value,
         "now": now}, connection="WORKBENCH")


def mk_breakout_job(portfolio_id: str, *, dimension: str = "lob",
                    status: str = "pending", input_data: dict | None = None,
                    output: dict | str | None = None, error: str | None = None,
                    now: datetime | None = None,
                    updated: datetime | None = None) -> str:
    jid = str(uuid.uuid4())
    now = now or datetime.utcnow()
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, link_type, "
        "context_type, context_id, rwb_job_type, "
        "status_code, input_data, output_data, error_detail, attempt_count, "
        "inserted_at, updated_at) VALUES (:i, 'analyst_request', :r, "
        "'not_applicable', 'portfolio', :r, :t, :s, "
        ":in, :out, :err, 0, :now, :upd)",
        {"i": jid, "r": portfolio_id, "t": f"run_breakout_{dimension}",
         "s": status,
         "in": (None if input_data is None else json.dumps(input_data)),
         "out": (output if isinstance(output, str) or output is None
                 else json.dumps(output)),
         "err": error, "now": now, "upd": updated or now},
        connection="WORKBENCH")
    return jid


def mk_backfill_job(edm_id: str, *, status: str = "pending",
                    via_irp_job: bool = False) -> None:
    requestor_id = edm_id
    requestor_type = "analyst_request"
    if via_irp_job:
        irp_job_id = str(uuid.uuid4())
        execute_command(
            "INSERT INTO irp_job (id, irp_edm_id, irp_job_type, status, "
            "inserted_at, updated_at) "
            "VALUES (:i, :e, 'import_edm', 'FINISHED', :now, :now)",
            {"i": irp_job_id, "e": edm_id, "now": datetime.utcnow()},
            connection="WORKBENCH")
        requestor_id, requestor_type = irp_job_id, "irp_job"
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, link_type, "
        "link_id, context_type, context_id, rwb_job_type, "
        "status_code, attempt_count, inserted_at, updated_at) "
        "VALUES (:i, :rt, :r, 'edm', :edm, 'edm', :edm, "
        "'backfill_edm_detail', :s, 0, :now, :now)",
        {"i": str(uuid.uuid4()), "rt": requestor_type, "r": requestor_id,
         "edm": edm_id,
         "s": status, "now": datetime.utcnow()}, connection="WORKBENCH")


def breakout_jobs() -> list[dict]:
    return execute(
        "SELECT id, requestor_type, requestor_id, rwb_job_type, status_code, "
        "input_data FROM rwb_job WHERE rwb_job_type LIKE 'run_breakout_%'",
        {}, connection="WORKBENCH")


def run_breakout_job(jid: str, dimension: str = "lob") -> dict:
    assert portfolio_jobs.run_one(rwb_job_id=jid,
                                  rwb_job_type=f"run_breakout_{dimension}",
                                  worker_id="w1")
    return execute_one(
        "SELECT status_code, output_data, error_detail FROM rwb_job "
        "WHERE id = :i", {"i": jid}, connection="WORKBENCH")


def rerun_breakout_job(jid: str, dimension: str = "lob") -> dict:
    execute_command(
        "UPDATE rwb_job SET status_code = 'pending', claimed_by = NULL, "
        "output_data = NULL, error_detail = NULL WHERE id = :i",
        {"i": jid}, connection="WORKBENCH")
    return run_breakout_job(jid, dimension)
