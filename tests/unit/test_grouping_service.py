"""Unit tests for the grouping compose gate, inspection view, and plan (spec
012, T017/T035).

Covers member eligibility (FR-003 / FR-018), the gate's collected failures
with nothing persisted (SC-005), the inspect-then-submit rules (inspected ids,
fingerprint, simulation count, event-rate selections — FR-007/FR-019), group
naming with the ``_n`` collision suffix and 64-char truncation (T-09), the
inspection view (Platform ids, no writes), and the plan carried verbatim into
``rwb_job.input_data`` (AGENTS.md rule 8).
"""

from __future__ import annotations

import json

import pytest

from app.services import grouping_service as svc
from app.services.analysis_execution_service import ExecutionGateError
from db import execute, execute_command, execute_one
from tests.unit.analysis_rows import seed_currency, seed_edm
from tests.unit.grouping_rows import (
    link_submission_edm,
    seed_broker_analysis,
    seed_group,
    seed_own_analysis,
    seed_submission,
)

_FINGERPRINT = "v1:" + "a" * 64
_SELECTION = json.dumps({"peril_code": "WS", "region_code": "NA",
                         "model_version": "11.0", "event_rate_scheme_id": 738})
_SIMULATION_SET = json.dumps({"peril_code": "WS", "region_code": "NA",
                              "model_version": "11.0", "simulation_set_id": 147})


def _submission_with_two_ready(iteration2_db) -> dict:
    seed_currency()
    submission_id = seed_submission("Sub One")
    edm_id = seed_edm("EDM One")
    link_submission_edm(submission_id, edm_id)
    a1 = seed_own_analysis(edm_id, "CRE_P1_T1",
                           settings={"engineType": "DLM"})
    a2 = seed_own_analysis(edm_id, "CRE_P2_T1")
    return {"submission_id": submission_id, "edm_id": edm_id,
            "a1": a1, "a2": a2}


def _irp(analysis_id: str) -> int | None:
    row = execute_one("SELECT irp_id FROM irp_analysis WHERE id = :id",
                      {"id": analysis_id}, connection="WORKBENCH")
    return int(row["irp_id"]) if row["irp_id"] is not None else None


def _inspected(member_ids: list[str]) -> list[str]:
    return [str(_irp(m)) for m in member_ids if _irp(m) is not None]


def _request(ctx, iteration2_db, member_ids=None, **overrides) -> str:
    member_ids = member_ids or [ctx["a1"], ctx["a2"]]
    kwargs = {
        "submission_id": ctx["submission_id"], "submission_name": "Sub One",
        "member_ids": member_ids,
        "group_name": "CRE_Sub One_Group",
        "currency_code": "USD", "currency_scheme": "RMS",
        "currency_vintage": "RL25", "propagate_detailed_output": True,
        "num_of_simulations": "1", "event_rate_selections": [],
        "simulation_set_selections": [],
        "expected_inspection_fingerprint": _FINGERPRINT,
        "inspected_analysis_ids": _inspected(member_ids),
        "actor_id": iteration2_db.user_a,
    }
    kwargs.update(overrides)
    return svc.request_grouping(**kwargs)


def _no_rwb_job() -> None:
    rows = execute("SELECT 1 FROM rwb_job WHERE rwb_job_type = 'submit_grouping'",
                   {}, connection="WORKBENCH")
    assert rows == []


def _plan(request_id: str) -> dict:
    job = execute(
        "SELECT input_data FROM rwb_job WHERE requestor_type = 'analyst_request' "
        "AND requestor_id = :r AND rwb_job_type = 'submit_grouping'",
        {"r": request_id}, connection="WORKBENCH")
    return json.loads(job[0]["input_data"])


# ── eligibility (FR-003 / FR-018) ────────────────────────────────────────────────

def test_eligible_members_mix_own_broker_and_group(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)
    seed_broker_analysis(ctx["submission_id"], "Broker EU Wind")
    seed_group(ctx["submission_id"], "CRE_Sub One_Group")

    members = svc.list_eligible_members(ctx["submission_id"])

    by_kind = {m.kind for m in members}
    assert by_kind == {"own", "broker", "group"}
    assert len(members) == 4
    own = next(m for m in members if m.id == ctx["a1"])
    assert own.irp_id == _irp(ctx["a1"])
    assert own.engine == "DLM"
    broker = next(m for m in members if m.kind == "broker")
    assert broker.irp_id == 70001
    group = next(m for m in members if m.kind == "group")
    assert group.engine == "Group"


def test_running_failed_and_deleted_rows_are_not_eligible(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)
    seed_own_analysis(ctx["edm_id"], "CRE_P3_T1", status="pending")
    seed_own_analysis(ctx["edm_id"], "CRE_P4_T1", status="error")
    seed_group(ctx["submission_id"], "Pending group", status="pending")
    deleted = seed_own_analysis(ctx["edm_id"], "CRE_P5_T1")
    execute_command(
        "UPDATE irp_analysis SET deleted_at = '2026-08-27' WHERE id = :id",
        {"id": deleted}, connection="WORKBENCH")

    members = svc.list_eligible_members(ctx["submission_id"])

    assert {m.id for m in members} == {ctx["a1"], ctx["a2"]}


def test_broker_handles_dedupe_by_rm_analysis_id(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)
    seed_broker_analysis(ctx["submission_id"], "Broker EU Wind",
                         irp_id="70001", rdm_name="RDM One")
    seed_broker_analysis(ctx["submission_id"], "Broker EU Wind",
                         irp_id="70001", rdm_name="RDM Two")

    members = svc.list_eligible_members(ctx["submission_id"])

    assert len([m for m in members if m.kind == "broker"]) == 1


# ── the compose gate (SC-005) ────────────────────────────────────────────────────

def test_gate_collects_failures_and_persists_nothing(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)
    pending = seed_own_analysis(ctx["edm_id"], "CRE_P3_T1", status="pending")

    with pytest.raises(ExecutionGateError) as exc:
        _request(ctx, iteration2_db, member_ids=[pending],
                 group_name="  ", currency_vintage="")

    # ineligible, <2, members changed since inspection, name, currency
    assert len(exc.value.errors) == 5
    _no_rwb_job()


def test_gate_rejects_a_foreign_member(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)
    other_submission = seed_submission("Sub Two")
    other_edm = seed_edm("EDM Two")
    link_submission_edm(other_submission, other_edm)
    foreign = seed_own_analysis(other_edm, "CRE_Other_T1")

    with pytest.raises(ExecutionGateError):
        _request(ctx, iteration2_db, member_ids=[ctx["a1"], foreign])
    _no_rwb_job()


def test_gate_rejects_a_deleted_member(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)
    execute_command(
        "UPDATE irp_analysis SET deleted_at = '2026-08-27' WHERE id = :id",
        {"id": ctx["a2"]}, connection="WORKBENCH")

    with pytest.raises(ExecutionGateError):
        _request(ctx, iteration2_db)
    _no_rwb_job()


def test_gate_rejects_an_invalid_currency_triple(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)

    with pytest.raises(ExecutionGateError) as exc:
        _request(ctx, iteration2_db, currency_vintage="RL99")

    assert any("RL99" in e for e in exc.value.errors)
    _no_rwb_job()


def test_gate_rejects_a_member_without_a_platform_id(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)
    no_id = seed_own_analysis(ctx["edm_id"], "CRE_P3_T1", irp_id=None)

    with pytest.raises(ExecutionGateError) as exc:
        _request(ctx, iteration2_db, member_ids=[ctx["a1"], no_id])

    assert "CRE_P3_T1 has no Risk Modeler analysis id yet." in exc.value.errors
    _no_rwb_job()


def test_gate_rejects_members_changed_since_inspection(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)

    with pytest.raises(ExecutionGateError) as exc:
        _request(ctx, iteration2_db,
                 inspected_analysis_ids=[str(_irp(ctx["a1"]))])

    assert exc.value.errors == ["Members changed since inspection. Inspect again."]
    _no_rwb_job()


def test_gate_rejects_a_missing_fingerprint(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)

    with pytest.raises(ExecutionGateError) as exc:
        _request(ctx, iteration2_db, expected_inspection_fingerprint="  ")

    assert exc.value.errors == ["Inspect the members before grouping."]
    _no_rwb_job()


@pytest.mark.parametrize("value", ["0", "-5", "x", ""])
def test_gate_rejects_a_non_positive_simulation_count(iteration2_db, value):
    ctx = _submission_with_two_ready(iteration2_db)

    with pytest.raises(ExecutionGateError) as exc:
        _request(ctx, iteration2_db, num_of_simulations=value)

    assert exc.value.errors == ["Enter a simulation count greater than zero."]
    _no_rwb_job()


@pytest.mark.parametrize("selections", [
    ["not json"],
    [""],
    ['{"peril_code": "WS"}'],
    [json.dumps({"peril_code": "WS", "region_code": "NA",
                 "model_version": "11.0", "event_rate_scheme_id": "738"})],
    [_SELECTION, _SELECTION],
])
def test_gate_rejects_malformed_or_duplicate_selections(iteration2_db, selections):
    ctx = _submission_with_two_ready(iteration2_db)

    with pytest.raises(ExecutionGateError) as exc:
        _request(ctx, iteration2_db, event_rate_selections=selections)

    assert exc.value.errors == [
        "Choose an event-rate scheme for every conflicting partition."]
    _no_rwb_job()


@pytest.mark.parametrize("selections", [
    ['{"peril_code": "WS", "simulation_set_id": 147}'],
    [_SIMULATION_SET, _SIMULATION_SET],
])
def test_gate_rejects_malformed_or_duplicate_simulation_set_selections(
        iteration2_db, selections):
    ctx = _submission_with_two_ready(iteration2_db)

    with pytest.raises(ExecutionGateError) as exc:
        _request(ctx, iteration2_db, simulation_set_selections=selections)

    assert exc.value.errors == [
        "Choose a simulation set for every partition converted from ELT to PLT."]
    _no_rwb_job()


# ── naming (T-09) ────────────────────────────────────────────────────────────────

def test_build_group_name_defaults_from_the_submission(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)
    assert svc.build_group_name(ctx["submission_id"],
                                "Sub One") == "CRE_Sub One_Group"


def test_build_group_name_suffixes_on_a_live_collision(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)
    seed_group(ctx["submission_id"], "CRE_Sub One_Group")
    assert svc.build_group_name(ctx["submission_id"],
                                "Sub One") == "CRE_Sub One_Group_2"


def test_build_group_name_truncates_at_64(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)
    name = svc.build_group_name(ctx["submission_id"], "x" * 80)
    assert len(name) == 64


# ── the plan (rule 8) ────────────────────────────────────────────────────────────

def test_plan_is_persisted_verbatim_on_the_rwb_job(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)
    broker = seed_broker_analysis(ctx["submission_id"], "Broker EU Wind")
    nested = seed_group(ctx["submission_id"], "Existing group")

    request_id = _request(ctx, iteration2_db,
                          member_ids=[ctx["a1"], broker, nested],
                          num_of_simulations="50000",
                          event_rate_selections=[_SELECTION],
                          simulation_set_selections=[_SIMULATION_SET])

    plan = _plan(request_id)
    assert plan["grouping_request_id"] == request_id
    assert plan["submission_id"] == ctx["submission_id"]
    assert plan["group_full_name"] == "CRE_Sub One_Group"
    assert plan["currency"] == {"code": "USD", "scheme": "RMS",
                                "vintage": "RL25", "asOfDate": "2025-05-28"}
    assert plan["propagate_detailed_losses"] is True
    assert plan["num_of_simulations"] == 50000
    assert plan["event_rate_selections"] == [
        {"peril_code": "WS", "region_code": "NA", "model_version": "11.0",
         "event_rate_scheme_id": 738}]
    assert plan["simulation_set_selections"] == [
        {"peril_code": "WS", "region_code": "NA", "model_version": "11.0",
         "simulation_set_id": 147}]
    assert plan["expected_inspection_fingerprint"] == _FINGERPRINT
    assert plan["members"] == [
        {"analysis_id": ctx["a1"], "irp_id": _irp(ctx["a1"]),
         "name": "CRE_P1_T1", "display_name": "CRE_P1_T1", "kind": "own"},
        {"analysis_id": broker, "irp_id": 70001, "name": "Broker EU Wind",
         "display_name": "Broker EU Wind", "kind": "broker"},
        {"analysis_id": nested, "irp_id": _irp(nested),
         "name": "Existing group", "display_name": "Existing group",
         "kind": "group"},
    ]


def test_posted_name_collision_takes_the_suffix_not_an_error(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)
    seed_group(ctx["submission_id"], "CRE_Sub One_Group")

    request_id = _request(ctx, iteration2_db)

    assert _plan(request_id)["group_full_name"] == "CRE_Sub One_Group_2"


# ── inspection (T-02 / FR-019) ───────────────────────────────────────────────────

def test_inspect_grouping_reads_by_platform_id_and_writes_nothing(
        iteration2_db, fake_irp):
    ctx = _submission_with_two_ready(iteration2_db)

    view = svc.inspect_grouping(submission_id=ctx["submission_id"],
                                member_ids=[ctx["a1"], ctx["a2"]])

    ids = [_irp(ctx["a1"]), _irp(ctx["a2"])]
    assert fake_irp.grouping_inspects == [ids]
    assert view.inspection.output_loss_table == "ELT"
    assert view.suggested_num_of_simulations == 1
    assert set(view.members) == set(ids)
    assert view.members[ids[0]].display_name == "CRE_P1_T1"
    _no_rwb_job()
    rows = execute("SELECT COUNT(*) AS n FROM irp_analysis", {},
                   connection="WORKBENCH")
    assert rows[0]["n"] == 2


def test_inspect_grouping_suggests_the_largest_member_plt_length(
        iteration2_db, fake_irp):
    ctx = _submission_with_two_ready(iteration2_db)
    ids = [_irp(ctx["a1"]), _irp(ctx["a2"])]
    fake_irp.seed_grouping_inspection(
        ids, output_loss_table="PLT", periods={ids[0]: 10000, ids[1]: 50000})

    view = svc.inspect_grouping(submission_id=ctx["submission_id"],
                                member_ids=[ctx["a1"], ctx["a2"]])

    assert view.suggested_num_of_simulations == 50000


def test_inspect_grouping_gate_failure_never_reaches_the_platform(
        iteration2_db, fake_irp):
    ctx = _submission_with_two_ready(iteration2_db)

    with pytest.raises(ExecutionGateError) as exc:
        svc.inspect_grouping(submission_id=ctx["submission_id"],
                             member_ids=[ctx["a1"]])

    assert exc.value.errors == ["Pick at least two analyses to group."]
    assert fake_irp.grouping_inspects == []


def test_inspect_grouping_wraps_a_platform_read_failure(iteration2_db, fake_irp):
    ctx = _submission_with_two_ready(iteration2_db)
    fake_irp.grouping_inspect_error = "region lookup timed out"

    with pytest.raises(ExecutionGateError) as exc:
        svc.inspect_grouping(submission_id=ctx["submission_id"],
                             member_ids=[ctx["a1"], ctx["a2"]])

    assert exc.value.errors == ["Inspection failed: region lookup timed out"]
