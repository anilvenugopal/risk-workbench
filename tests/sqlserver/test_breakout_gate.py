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
from db import execute_command
from tests.sqlserver.breakout_rows import (
    AS_OF,
    PRE_ITERATION_SUMMARY,
    RM_STAMP,
    SUMMARY,
    breakout_jobs,
    mk_backfill_job,
    mk_breakout_job,
    mk_edm,
    mk_portfolio,
)


def _eligible_pair(fake_irp) -> tuple[str, str]:
    """A ready EDM + live portfolio whose stored summary and RM stamp agree —
    the confirm path's happy input."""
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id)
    fake_irp.add_portfolio(edm_exposure_id="90001", irp_id="1",
                           name="usfl_commercial", stamp=RM_STAMP)
    return edm_id, pid


def _dim(gate, code: str):
    return next(d for d in gate.dimensions if d.dimension == code)


def test_gate_eligible_happy_path(workbench_db):
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id)
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
def test_gate_requires_ready_edm(workbench_db, status):
    edm_id = mk_edm(status=status)
    pid = mk_portfolio(edm_id)
    gate = evaluate_gate(edm_id, pid)
    assert gate.portfolio_eligible is False
    assert gate.reason == "the EDM is not ready"


def test_gate_deleted_edm_and_missing_portfolio(workbench_db):
    edm_id = mk_edm(deleted=True)
    pid = mk_portfolio(edm_id)
    assert evaluate_gate(edm_id, pid).reason == "EDM not found"

    edm_id = mk_edm()
    gate = evaluate_gate(edm_id, str(uuid.uuid4()))
    assert gate.portfolio_eligible is False
    assert gate.reason == "portfolio not found"

    pid = mk_portfolio(edm_id, deleted=True)
    assert evaluate_gate(edm_id, pid).reason == "portfolio not found"


def test_gate_no_snapshot_reads_as_missing_summary(workbench_db):
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id, detail=None, as_of=None)
    gate = evaluate_gate(edm_id, pid)
    assert gate.portfolio_eligible is True  # the EDM half passes
    assert gate.summary_as_of is None
    for code in ("lob", "state", "country"):
        assert _dim(gate, code).eligible is False
        assert _dim(gate, code).reason == MISSING_SUMMARY_REASON


def test_gate_pre_iteration_summary_reads_as_absent_never_states_fallback(
        workbench_db):
    # Every pre-005 summary lacks breakout_values, and its states list is a
    # mixed name/code vocabulary that MUST NOT be offered as filter values.
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id, summary=PRE_ITERATION_SUMMARY)
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
def test_gate_malformed_breakout_values_reads_as_absent(workbench_db, container):
    edm_id = mk_edm()
    summary = dict(SUMMARY, breakout_values=container)
    pid = mk_portfolio(edm_id, summary=summary)
    state = _dim(evaluate_gate(edm_id, pid), "state")
    assert state.eligible is False
    assert state.reason == MISSING_SUMMARY_REASON


def test_gate_zero_and_one_value_dimensions_disable_with_reason(workbench_db):
    edm_id = mk_edm()
    summary = dict(SUMMARY, breakout_values={
        "lob": [{"value": "FLD Comm", "label": None, "accounts": 1701}]})
    pid = mk_portfolio(edm_id, summary=summary)
    gate = evaluate_gate(edm_id, pid)
    lob = _dim(gate, "lob")
    assert lob.eligible is False
    assert lob.reason == "only one line of business present"
    state = _dim(gate, "state")  # key absent from a PRESENT container → 0 values
    assert state.eligible is False
    assert state.reason == "no state values present"


def test_peril_breaks_out_one_sub_portfolio_per_code(workbench_db, fake_irp):
    # D3 (replacing P-19): peril runs in quick mode like the other value
    # dimensions — the plan names by mnemonic (P-30) while the stored plan
    # value and the number token stay the numeric code.
    edm_id = mk_edm()
    summary = dict(SUMMARY, breakout_values=dict(
        SUMMARY["breakout_values"],
        peril=[{"value": "1", "label": None, "accounts": 517},
               {"value": "2", "label": None, "accounts": 1701}]))
    pid = mk_portfolio(edm_id, summary=summary)
    fake_irp.add_portfolio(edm_exposure_id="90001", irp_id="1",
                           name="usfl_commercial", stamp=RM_STAMP)

    peril = _dim(evaluate_gate(edm_id, pid), "peril")
    assert peril.eligible is True
    assert peril.noun == "peril"

    job_id = request_breakout(edm_id, pid, "peril", AS_OF, workbench_db.user_a)
    assert job_id is not None
    job = breakout_jobs()[0]
    assert job["rwb_job_type"] == "run_breakout_peril"
    assert [(e["value"], e["label"], e["name"], e["number"])
            for e in json.loads(job["input_data"])["plan"]] == [
        ("1", "EQ", "usfl_commercial - EQ", "P1-P-1"),
        ("2", "WS", "usfl_commercial - WS", "P1-P-2")]


def test_country_is_eligible_when_the_summary_carries_values(workbench_db):
    edm_id = mk_edm()
    summary = dict(SUMMARY, breakout_values=dict(
        SUMMARY["breakout_values"],
        country=[{"value": "US", "label": None, "accounts": 1650},
                 {"value": "CA", "label": None, "accounts": 51}]))
    pid = mk_portfolio(edm_id, summary=summary)
    country = _dim(evaluate_gate(edm_id, pid), "country")
    assert country.eligible is True
    assert country.noun == "country"
    assert [v.value for v in country.values] == ["CA", "US"]


def test_modal_selects_peril_when_it_is_the_only_eligible_dimension(
        workbench_db):
    # lob/state each carry one value; peril carries two.
    edm_id = mk_edm()
    summary = dict(SUMMARY, breakout_values={
        "lob": [{"value": "FLD Comm", "label": None, "accounts": 1701}],
        "state": [{"value": "FL", "label": None, "accounts": 1701}],
        "peril": [{"value": "1", "label": None, "accounts": 517},
                  {"value": "2", "label": None, "accounts": 1701}]})
    pid = mk_portfolio(edm_id, summary=summary)
    modal = breakout_service.modal_context(edm_id, pid)
    assert modal.dimension == "peril"
    assert [p.name for p in modal.plan] == ["usfl_commercial - EQ",
                                            "usfl_commercial - WS"]


def test_gate_reports_in_flight_breakout_dimension(workbench_db):
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id)
    mk_breakout_job(pid, dimension="lob", status="running")
    gate = evaluate_gate(edm_id, pid)
    assert gate.in_flight == "lob"
    # a TERMINAL breakout job does not read as in-flight
    other = mk_portfolio(edm_id, name="other", irp_id="2")
    mk_breakout_job(other, dimension="lob", status="succeeded")
    assert evaluate_gate(edm_id, other).in_flight is None


@pytest.mark.parametrize("via_irp_job", [False, True])
def test_gate_disables_while_detail_refresh_in_flight(workbench_db, via_irp_job):
    # P-16: a pending|running backfill_edm_detail rewrites the summary the
    # preview reads — disabled-with-reason under EITHER enqueue key.
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id)
    mk_backfill_job(edm_id, via_irp_job=via_irp_job)
    gate = evaluate_gate(edm_id, pid)
    assert gate.refresh_in_flight is True
    assert gate.portfolio_eligible is False
    assert gate.reason == breakout_service.REFRESH_IN_FLIGHT_REASON


def test_gate_terminal_backfill_does_not_disable(workbench_db):
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id)
    mk_backfill_job(edm_id, status="succeeded")
    gate = evaluate_gate(edm_id, pid)
    assert gate.refresh_in_flight is False
    assert gate.portfolio_eligible is True


# ── the confirm path (T025) ───────────────────────────────────────────────────────

def test_confirm_happy_path_persists_plan_and_enqueues_one_job(
        workbench_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    job_id = request_breakout(edm_id, pid, "lob", AS_OF,
                              workbench_db.user_a)
    assert job_id is not None
    jobs = breakout_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["rwb_job_type"] == "run_breakout_lob"
    assert job["requestor_type"] == "analyst_request"
    assert str(job["requestor_id"]).lower() == pid   # the SOURCE portfolio (FR-015)
    data = json.loads(job["input_data"])
    assert data["edm_id"] == edm_id
    assert data["portfolio_id"] == pid
    assert data["dimension"] == "lob"
    assert data["actor_id"] == workbench_db.user_a
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


def test_confirm_double_post_yields_one_job(workbench_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    first = request_breakout(edm_id, pid, "lob", AS_OF, workbench_db.user_a)
    second = request_breakout(edm_id, pid, "lob", AS_OF, workbench_db.user_a)
    assert first is not None
    assert second is None                      # already running (in_flight gate)
    assert len(breakout_jobs()) == 1


def test_confirm_each_dimension_gets_its_own_job_slot(workbench_db, fake_irp):
    # Two job types — a LOB and a state breakout on the same portfolio don't
    # collide on UNIQUE(requestor_type, requestor_id, rwb_job_type)... but a
    # LIVE run of either dimension blocks a new confirm (the modal's in-flight
    # state covers the whole action).
    edm_id, pid = _eligible_pair(fake_irp)
    assert request_breakout(edm_id, pid, "lob", AS_OF,
                            workbench_db.user_a) is not None
    assert request_breakout(edm_id, pid, "state", AS_OF,
                            workbench_db.user_a) is None
    # once the LOB run is terminal, the state dimension enqueues its own row
    execute_command(
        "UPDATE rwb_job SET status_code = 'succeeded' "
        "WHERE rwb_job_type = 'run_breakout_lob'", {}, connection="WORKBENCH")
    assert request_breakout(edm_id, pid, "state", AS_OF,
                            workbench_db.user_a) is not None
    assert {j["rwb_job_type"] for j in breakout_jobs()} == {
        "run_breakout_lob", "run_breakout_state"}


def test_confirm_stale_stamp_refuses_with_no_job_row(workbench_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    fake_irp.set_portfolio_stamp(edm_exposure_id="90001", irp_id="1",
                                 stamp="2026-08-04T08:00:00.000Z")  # RM moved
    with pytest.raises(StaleSummary, match="Sync the EDM, then retry"):
        request_breakout(edm_id, pid, "lob", AS_OF, workbench_db.user_a)
    assert breakout_jobs() == []


def test_confirm_missing_stored_stamp_refuses_with_no_job_row(
        workbench_db, fake_irp):
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id, stamp=None)    # backfilled before spec 005
    fake_irp.add_portfolio(edm_exposure_id="90001", irp_id="1",
                           name="usfl_commercial", stamp=RM_STAMP)
    with pytest.raises(StaleSummary):
        request_breakout(edm_id, pid, "lob", AS_OF, workbench_db.user_a)
    assert breakout_jobs() == []


def test_confirm_gateway_error_refuses_with_no_job_row(workbench_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    fake_irp.raise_on_fetch_stamp = True
    with pytest.raises(StaleSummary, match="couldn't verify freshness"):
        request_breakout(edm_id, pid, "lob", AS_OF, workbench_db.user_a)
    assert breakout_jobs() == []


def test_confirm_without_a_risk_modeler_id_refuses_with_no_job_row(
        workbench_db, fake_irp):
    # No portfolioId means the stamp cannot be read and no portfolio_number can
    # be composed — the freshness check refuses rather than proceeding.
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id, irp_id=None)
    with pytest.raises(StaleSummary, match="couldn't verify freshness"):
        request_breakout(edm_id, pid, "lob", AS_OF, workbench_db.user_a)
    assert breakout_jobs() == []
    assert fake_irp.stamp_reads == []


def test_confirm_rewritten_summary_refuses_even_when_stamp_matches(
        workbench_db, fake_irp):
    # FR-002b — the case FR-002a cannot see: a re-backfill that left the RM
    # portfolio untouched wrote back an EQUAL stamp but a NEW summary. The
    # confirm carries the preview's as_of; a mismatch refuses before the stamp
    # is even read, and no job row exists.
    edm_id, pid = _eligible_pair(fake_irp)
    with pytest.raises(SummaryRewritten, match="synced while you were reviewing"):
        request_breakout(edm_id, pid, "lob", "2026-08-02 09:00:00",
                         workbench_db.user_a)
    assert breakout_jobs() == []
    assert fake_irp.stamp_reads == []          # refused before the RM read


def test_confirm_gate_refusal_writes_no_job_row(workbench_db, fake_irp):
    edm_id = mk_edm()
    summary = dict(SUMMARY, breakout_values={
        "lob": [{"value": "FLD Comm", "label": None, "accounts": 1701}],
        "state": SUMMARY["breakout_values"]["state"]})
    pid = mk_portfolio(edm_id, summary=summary)
    with pytest.raises(GateRefused, match="only one line of business"):
        request_breakout(edm_id, pid, "lob", AS_OF, workbench_db.user_a)
    with pytest.raises(GateRefused, match="unknown breakout dimension"):
        request_breakout(edm_id, pid, "zip", AS_OF, workbench_db.user_a)
    assert breakout_jobs() == []


def test_confirm_plan_matches_preview_except_collision_suffix(
        workbench_db, fake_irp):
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
    mk_portfolio(edm_id, name=preview[0].name, irp_id="77", summary=None)

    request_breakout(edm_id, pid, "lob", AS_OF, workbench_db.user_a)
    persisted = json.loads(breakout_jobs()[0]["input_data"])["plan"]

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
