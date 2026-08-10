"""Unit tests for the breakout prerequisite gate and confirm path (spec 005
T018/T025 — FR-002/FR-002a/FR-002b/FR-003/FR-006a/FR-006b).

The gate is the Article 12 named must-test: a truth table over EDM status ×
deleted × ``breakout_values`` present/absent/malformed × 0/1/2+ values ×
in-flight ``run_breakout_*`` × in-flight ``backfill_edm_detail`` — including a
pre-iteration summary (has ``states``, no ``breakout_values``) reading as
ABSENT, never falling back to the mixed-vocabulary ``states`` list (P-12/R11).

The confirm path (``request_breakout``) refuses — with NO ``rwb_job`` row —
on gate failure, on a rewritten summary (``as_of`` mismatch, FR-002b, even when
the RM stamp still matches), and on a stale/unverifiable RM ``stampDate``
(FR-002a). On pass it persists the approved plan into ``input_data`` and
enqueues exactly one job per (portfolio, dimension).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest

from app.services import breakout_service
from app.services.breakout_service import (
    MISSING_SUMMARY_REASON,
    GateRefused,
    StaleSummary,
    SummaryRewritten,
    compose_plan,
    evaluate_gate,
    load_approved_plan,
    request_breakout,
)
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


def _mk_edm(status: str = "ready", *, deleted: bool = False,
            irp_id: int | None = 90001) -> str:
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, irp_id, status, deleted_at, "
        "inserted_at, updated_at) VALUES (:i, :n, :irp, :s, :d, :now, :now)",
        {"i": edm_id, "n": f"edm-{edm_id[:8]}", "irp": irp_id, "s": status,
         "d": (datetime.utcnow() if deleted else None),
         "now": datetime.utcnow()}, connection="WORKBENCH")
    return edm_id


def _mk_portfolio(edm_id: str, *, name: str = "usfl_commercial",
                  irp_id: str | None = "1", summary: dict | None = SUMMARY,
                  stamp: str | None = RM_STAMP, as_of: str | None = AS_OF,
                  detail: dict | str | None = "auto",
                  deleted: bool = False) -> str:
    if detail == "auto":
        detail = {"metrics": {"totalAccounts": 1701}, "summary": summary,
                  "stamp_date": stamp}
    pid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_portfolio (id, edm_id, name, irp_id, exposure_detail, "
        "as_of, deleted_at, inserted_at, updated_at) "
        "VALUES (:i, :e, :n, :irp, :x, :a, :d, :now, :now)",
        {"i": pid, "e": edm_id, "n": name, "irp": irp_id,
         "x": (detail if isinstance(detail, str) or detail is None
               else json.dumps(detail)),
         "a": as_of, "d": (datetime.utcnow() if deleted else None),
         "now": datetime.utcnow()}, connection="WORKBENCH")
    return pid


def _eligible_pair(fake_irp) -> tuple[str, str]:
    """A ready EDM + live portfolio whose stored summary and RM stamp agree —
    the confirm path's happy input."""
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id)
    fake_irp.add_portfolio(edm_exposure_id="90001", irp_id="1",
                           name="usfl_commercial", stamp=RM_STAMP)
    return edm_id, pid


def _dim(gate, code: str):
    return next(d for d in gate.dimensions if d.dimension == code)


def _breakout_jobs() -> list[dict]:
    return execute(
        "SELECT id, requestor_type, requestor_id, rwb_job_type, status_code, "
        "input_data FROM rwb_job WHERE rwb_job_type LIKE 'run_breakout_%'",
        {}, connection="WORKBENCH")


def _mk_breakout_job(portfolio_id: str, dimension: str = "lob",
                     status: str = "pending") -> str:
    jid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, "
        "status_code, attempt_count, inserted_at, updated_at) "
        "VALUES (:i, 'analyst_request', :r, :t, :s, 0, :now, :now)",
        {"i": jid, "r": portfolio_id, "t": f"run_breakout_{dimension}",
         "s": status, "now": datetime.utcnow()}, connection="WORKBENCH")
    return jid


def _mk_backfill_job(edm_id: str, *, status: str = "pending",
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
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, "
        "status_code, attempt_count, inserted_at, updated_at) "
        "VALUES (:i, :rt, :r, 'backfill_edm_detail', :s, 0, :now, :now)",
        {"i": str(uuid.uuid4()), "rt": requestor_type, "r": requestor_id,
         "s": status, "now": datetime.utcnow()}, connection="WORKBENCH")


# ── the gate truth table (T018) ───────────────────────────────────────────────────

def test_gate_eligible_happy_path(iteration2_db):
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id)
    gate = evaluate_gate(edm_id, pid)
    assert gate.portfolio_eligible is True
    assert gate.reason is None
    assert gate.in_flight is None
    assert gate.refresh_in_flight is False
    assert gate.summary_as_of == AS_OF
    assert _dim(gate, "lob").eligible and _dim(gate, "state").eligible
    # values come sorted from the stored summary, with labels and counts
    assert [(v.value, v.label, v.accounts)
            for v in _dim(gate, "state").values] == [
        ("CA", "CALIFORNIA", 1481), ("TX", "TEXAS", 220)]


@pytest.mark.parametrize("status", ["pending_import", "importing", "error",
                                    "delete_pending"])
def test_gate_requires_ready_edm(iteration2_db, status):
    edm_id = _mk_edm(status)
    pid = _mk_portfolio(edm_id)
    gate = evaluate_gate(edm_id, pid)
    assert gate.portfolio_eligible is False
    assert gate.reason == "the EDM is not ready"


def test_gate_deleted_edm_and_missing_portfolio(iteration2_db):
    edm_id = _mk_edm(deleted=True)
    pid = _mk_portfolio(edm_id)
    assert evaluate_gate(edm_id, pid).reason == "EDM not found"

    edm_id = _mk_edm()
    gate = evaluate_gate(edm_id, str(uuid.uuid4()))
    assert gate.portfolio_eligible is False
    assert gate.reason == "portfolio not found"

    pid = _mk_portfolio(edm_id, deleted=True)
    assert evaluate_gate(edm_id, pid).reason == "portfolio not found"


def test_gate_no_snapshot_reads_as_missing_summary(iteration2_db):
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id, detail=None, as_of=None)
    gate = evaluate_gate(edm_id, pid)
    assert gate.portfolio_eligible is True  # the EDM half passes
    assert gate.summary_as_of is None
    for code in ("lob", "state"):
        assert _dim(gate, code).eligible is False
        assert _dim(gate, code).reason == MISSING_SUMMARY_REASON


def test_gate_pre_iteration_summary_reads_as_absent_never_states_fallback(
        iteration2_db):
    # Every pre-005 summary lacks breakout_values, and its states list is a
    # mixed name/code vocabulary that MUST NOT be offered as filter values.
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id, summary=PRE_ITERATION_SUMMARY)
    gate = evaluate_gate(edm_id, pid)
    state = _dim(gate, "state")
    assert state.eligible is False
    assert state.values == []              # no fallback to summary["states"]
    assert state.reason == MISSING_SUMMARY_REASON


@pytest.mark.parametrize("container", [
    ["not", "a", "dict"],                              # container malformed
    {"state": "TX,CA", "lob": "x"},                    # dimension not a list
    {"state": [{"label": "TEXAS"}], "lob": []},        # entry missing value
    {"state": [["TX"]], "lob": []},                    # entry not an object
])
def test_gate_malformed_breakout_values_reads_as_absent(iteration2_db, container):
    edm_id = _mk_edm()
    summary = dict(SUMMARY, breakout_values=container)
    pid = _mk_portfolio(edm_id, summary=summary)
    state = _dim(evaluate_gate(edm_id, pid), "state")
    assert state.eligible is False
    assert state.reason == MISSING_SUMMARY_REASON


def test_gate_zero_and_one_value_dimensions_disable_with_reason(iteration2_db):
    edm_id = _mk_edm()
    summary = dict(SUMMARY, breakout_values={
        "lob": [{"value": "FLD Comm", "label": None, "accounts": 1701}]})
    pid = _mk_portfolio(edm_id, summary=summary)
    gate = evaluate_gate(edm_id, pid)
    lob = _dim(gate, "lob")
    assert lob.eligible is False
    assert lob.reason == "only one line of business present"
    state = _dim(gate, "state")  # key absent from a PRESENT container → 0 values
    assert state.eligible is False
    assert state.reason == "no state values present"


def test_peril_is_grouping_only_never_quick(iteration2_db, fake_irp):
    # P-19: peril appears in the gate (the custom-grouping pane reads its
    # eligibility) but never runs in quick mode — quick=False, modal_context
    # never selects it, and a hand-crafted confirm refuses with no job row.
    edm_id = _mk_edm()
    summary = dict(SUMMARY, breakout_values=dict(
        SUMMARY["breakout_values"],
        peril=[{"value": "1", "label": None, "accounts": 517},
               {"value": "2", "label": None, "accounts": 1701}]))
    pid = _mk_portfolio(edm_id, summary=summary)

    gate = evaluate_gate(edm_id, pid)
    peril = _dim(gate, "peril")
    assert (peril.quick, peril.eligible) == (False, True)
    assert peril.noun == "peril"
    assert _dim(gate, "lob").quick is True
    assert _dim(gate, "state").quick is True

    modal = breakout_service.modal_context(edm_id, pid)
    assert modal.dimension == "lob"          # first QUICK eligible wins

    with pytest.raises(GateRefused, match="one-per-value"):
        request_breakout(edm_id, pid, "peril", AS_OF, iteration2_db.user_a)
    assert _breakout_jobs() == []


def test_modal_selects_nothing_when_only_peril_is_eligible(iteration2_db):
    # lob/state each carry one value; peril carries two: no quick-mode
    # dimension is selectable and no per-value plan is composed.
    edm_id = _mk_edm()
    summary = dict(SUMMARY, breakout_values={
        "lob": [{"value": "FLD Comm", "label": None, "accounts": 1701}],
        "state": [{"value": "FL", "label": None, "accounts": 1701}],
        "peril": [{"value": "1", "label": None, "accounts": 517},
                  {"value": "2", "label": None, "accounts": 1701}]})
    pid = _mk_portfolio(edm_id, summary=summary)
    modal = breakout_service.modal_context(edm_id, pid)
    assert modal.dimension is None
    assert modal.plan == []


def test_gate_reports_in_flight_breakout_dimension(iteration2_db):
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id)
    _mk_breakout_job(pid, "lob", status="running")
    gate = evaluate_gate(edm_id, pid)
    assert gate.in_flight == "lob"
    # a TERMINAL breakout job does not read as in-flight
    other = _mk_portfolio(edm_id, name="other", irp_id="2")
    _mk_breakout_job(other, "lob", status="succeeded")
    assert evaluate_gate(edm_id, other).in_flight is None


@pytest.mark.parametrize("via_irp_job", [False, True])
def test_gate_disables_while_detail_refresh_in_flight(iteration2_db, via_irp_job):
    # P-16: a pending|running backfill_edm_detail rewrites the summary the
    # preview reads — disabled-with-reason under EITHER enqueue key.
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id)
    _mk_backfill_job(edm_id, via_irp_job=via_irp_job)
    gate = evaluate_gate(edm_id, pid)
    assert gate.refresh_in_flight is True
    assert gate.portfolio_eligible is False
    assert gate.reason == breakout_service.REFRESH_IN_FLIGHT_REASON


def test_gate_terminal_backfill_does_not_disable(iteration2_db):
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id)
    _mk_backfill_job(edm_id, status="succeeded")
    gate = evaluate_gate(edm_id, pid)
    assert gate.refresh_in_flight is False
    assert gate.portfolio_eligible is True


# ── the confirm path (T025) ───────────────────────────────────────────────────────

def test_confirm_happy_path_persists_plan_and_enqueues_one_job(
        iteration2_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    job_id = request_breakout(edm_id, pid, "lob", AS_OF,
                              iteration2_db.user_a)
    assert job_id is not None
    jobs = _breakout_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["rwb_job_type"] == "run_breakout_lob"
    assert job["requestor_type"] == "analyst_request"
    assert job["requestor_id"] == pid          # the SOURCE portfolio (FR-015)
    data = json.loads(job["input_data"])
    assert data["edm_id"] == edm_id
    assert data["portfolio_id"] == pid
    assert data["dimension"] == "lob"
    assert data["actor_id"] == iteration2_db.user_a
    # the persisted plan is the approved list: value, label, name, number, AND
    # the previewed account count (FR-006a/FR-006b)
    assert [{k: v for k, v in e.items() if k != "number"}
            for e in data["plan"]] == [
        {"value": "EQ Comm", "label": None,
         "name": "usfl_commercial - EQ Comm", "accounts": 801},
        {"value": "FLD Comm", "label": None,
         "name": "usfl_commercial - FLD Comm", "accounts": 900},
    ]
    # both values carry a space, so both numbers are hash-tailed (R4) — the
    # shape and the per-value uniqueness are what matter, not the digits
    numbers = [e["number"] for e in data["plan"]]
    assert [n[:11] for n in numbers] == ["P1-L-EQCOMM", "P1-L-FLDCOM"]
    assert len(set(numbers)) == 2


def test_confirm_double_post_yields_one_job(iteration2_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    first = request_breakout(edm_id, pid, "lob", AS_OF, iteration2_db.user_a)
    second = request_breakout(edm_id, pid, "lob", AS_OF, iteration2_db.user_a)
    assert first is not None
    assert second is None                      # already running (in_flight gate)
    assert len(_breakout_jobs()) == 1


def test_confirm_each_dimension_gets_its_own_job_slot(iteration2_db, fake_irp):
    # Two job types — a LOB and a state breakout on the same portfolio don't
    # collide on UNIQUE(requestor_type, requestor_id, rwb_job_type)... but a
    # LIVE run of either dimension blocks a new confirm (the modal's in-flight
    # state covers the whole action).
    edm_id, pid = _eligible_pair(fake_irp)
    assert request_breakout(edm_id, pid, "lob", AS_OF,
                            iteration2_db.user_a) is not None
    assert request_breakout(edm_id, pid, "state", AS_OF,
                            iteration2_db.user_a) is None
    # once the LOB run is terminal, the state dimension enqueues its own row
    execute_command(
        "UPDATE rwb_job SET status_code = 'succeeded' "
        "WHERE rwb_job_type = 'run_breakout_lob'", {}, connection="WORKBENCH")
    assert request_breakout(edm_id, pid, "state", AS_OF,
                            iteration2_db.user_a) is not None
    assert {j["rwb_job_type"] for j in _breakout_jobs()} == {
        "run_breakout_lob", "run_breakout_state"}


def test_confirm_stale_stamp_refuses_with_no_job_row(iteration2_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    fake_irp.set_portfolio_stamp(edm_exposure_id="90001", irp_id="1",
                                 stamp="2026-08-04T08:00:00.000Z")  # RM moved
    with pytest.raises(StaleSummary, match="Sync the EDM, then retry"):
        request_breakout(edm_id, pid, "lob", AS_OF, iteration2_db.user_a)
    assert _breakout_jobs() == []


def test_confirm_missing_stored_stamp_refuses_with_no_job_row(
        iteration2_db, fake_irp):
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id, stamp=None)    # backfilled before spec 005
    fake_irp.add_portfolio(edm_exposure_id="90001", irp_id="1",
                           name="usfl_commercial", stamp=RM_STAMP)
    with pytest.raises(StaleSummary):
        request_breakout(edm_id, pid, "lob", AS_OF, iteration2_db.user_a)
    assert _breakout_jobs() == []


def test_confirm_gateway_error_refuses_with_no_job_row(iteration2_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    fake_irp.raise_on_fetch_stamp = True
    with pytest.raises(StaleSummary, match="couldn't verify freshness"):
        request_breakout(edm_id, pid, "lob", AS_OF, iteration2_db.user_a)
    assert _breakout_jobs() == []


def test_confirm_without_a_risk_modeler_id_refuses_with_no_job_row(
        iteration2_db, fake_irp):
    # No portfolioId means the stamp cannot be read and no portfolio_number can
    # be composed — the freshness check refuses rather than proceeding.
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id, irp_id=None)
    with pytest.raises(StaleSummary, match="couldn't verify freshness"):
        request_breakout(edm_id, pid, "lob", AS_OF, iteration2_db.user_a)
    assert _breakout_jobs() == []
    assert fake_irp.stamp_reads == []


def test_confirm_rewritten_summary_refuses_even_when_stamp_matches(
        iteration2_db, fake_irp):
    # FR-002b — the case FR-002a cannot see: a re-backfill that left the RM
    # portfolio untouched wrote back an EQUAL stamp but a NEW summary. The
    # confirm carries the preview's as_of; a mismatch refuses before the stamp
    # is even read, and no job row exists.
    edm_id, pid = _eligible_pair(fake_irp)
    with pytest.raises(SummaryRewritten, match="synced while you were reviewing"):
        request_breakout(edm_id, pid, "lob", "2026-08-02 09:00:00",
                         iteration2_db.user_a)
    assert _breakout_jobs() == []
    assert fake_irp.stamp_reads == []          # refused before the RM read


def test_confirm_gate_refusal_writes_no_job_row(iteration2_db, fake_irp):
    edm_id = _mk_edm()
    summary = dict(SUMMARY, breakout_values={
        "lob": [{"value": "FLD Comm", "label": None, "accounts": 1701}],
        "state": SUMMARY["breakout_values"]["state"]})
    pid = _mk_portfolio(edm_id, summary=summary)
    with pytest.raises(GateRefused, match="only one line of business"):
        request_breakout(edm_id, pid, "lob", AS_OF, iteration2_db.user_a)
    with pytest.raises(GateRefused, match="unknown breakout dimension"):
        request_breakout(edm_id, pid, "zip", AS_OF, iteration2_db.user_a)
    assert _breakout_jobs() == []


def test_confirm_plan_matches_preview_except_collision_suffix(
        iteration2_db, fake_irp):
    # FR-006b/P-14: a portfolio created in the EDM between preview and confirm
    # may move a collision suffix; the value, label, account count, number, and
    # the set of entries MUST NOT differ — the number, not the name, is the
    # identity.
    edm_id, pid = _eligible_pair(fake_irp)
    gate = evaluate_gate(edm_id, pid)
    preview = compose_plan(gate, edm_id=edm_id, portfolio_id=pid,
                           source_name="usfl_commercial",
                           source_portfolio_irp_id="1", dimension="lob")
    # someone creates a portfolio named like the first previewed entry
    _mk_portfolio(edm_id, name=preview[0].name, irp_id="77", summary=None)

    request_breakout(edm_id, pid, "lob", AS_OF, iteration2_db.user_a)
    persisted = json.loads(_breakout_jobs()[0]["input_data"])["plan"]

    assert [(e["value"], e["label"], e["accounts"], e["number"])
            for e in persisted] == [
        (p.value, p.label, p.accounts, p.number) for p in preview]
    assert persisted[0]["name"] == f"{preview[0].name} (2)"  # only the suffix moved
    assert persisted[1]["name"] == preview[1].name


# ── worker-side plan load (T-10/R10) ──────────────────────────────────────────────

def test_load_approved_plan_runs_verbatim_never_recomputes():
    # A stored plan whose names no longer match what a recompute would produce
    # still parses to exactly what was persisted — no re-suffixing, no reads.
    input_data = {"plan": [
        {"value": "TX", "label": "TEXAS", "name": "old name (4)",
         "number": "P1-S-TX", "accounts": 220},
    ]}
    plan = load_approved_plan(input_data)
    assert len(plan) == 1
    assert plan[0].name == "old name (4)"
    assert plan[0].number == "P1-S-TX"
    assert plan[0].accounts == 220


@pytest.mark.parametrize("input_data", [
    {},                                        # no plan at all
    {"plan": []},                              # empty plan
    {"plan": "not-a-list"},
    {"plan": [{"value": "TX", "name": "x"}]},  # entry missing number
    {"plan": [{"value": "", "name": "x", "number": "y"}]},
    {"plan": ["TX"]},                          # entry not an object
])
def test_load_approved_plan_rejects_empty_or_unparseable(input_data):
    with pytest.raises(ValueError):
        load_approved_plan(input_data)
