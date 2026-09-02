"""Unit tests for the grouping compose gate + plan (spec 012, T017).

Covers member eligibility (FR-003 / FR-018), the gate's collected failures
with nothing persisted (SC-005), group naming with the ``_n`` collision suffix
and 64-char truncation (T-09), and the plan carried verbatim into
``rwb_job.input_data`` (AGENTS.md rule 8).
"""

from __future__ import annotations

import json

import pytest

from app.services import grouping_service as svc
from app.services.analysis_execution_service import ExecutionGateError
from db import execute, execute_command
from tests.unit.analysis_rows import seed_currency, seed_edm
from tests.unit.grouping_rows import (
    link_submission_edm,
    seed_broker_analysis,
    seed_group,
    seed_own_analysis,
    seed_submission,
)


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


def _request(ctx, iteration2_db, member_ids=None, **overrides) -> str:
    kwargs = {
        "submission_id": ctx["submission_id"], "submission_name": "Sub One",
        "member_ids": member_ids or [ctx["a1"], ctx["a2"]],
        "group_name": "CRE_Sub One_Group",
        "currency_code": "USD", "currency_scheme": "RMS",
        "currency_vintage": "RL25", "propagate_detailed_output": True,
        "actor_id": iteration2_db.user_a,
    }
    kwargs.update(overrides)
    return svc.request_grouping(**kwargs)


def _no_rwb_job() -> None:
    rows = execute("SELECT 1 FROM rwb_job WHERE rwb_job_type = 'submit_grouping'",
                   {}, connection="WORKBENCH")
    assert rows == []


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
    assert own.edm_name == "EDM One"
    assert own.engine == "DLM"
    group = next(m for m in members if m.kind == "group")
    assert group.engine == "Group"
    assert group.edm_name is None


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

    assert len(exc.value.errors) == 4  # ineligible, <2, name, currency
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
                          member_ids=[ctx["a1"], broker, nested])

    job = execute(
        "SELECT input_data FROM rwb_job WHERE requestor_type = 'analyst_request' "
        "AND requestor_id = :r AND rwb_job_type = 'submit_grouping'",
        {"r": request_id}, connection="WORKBENCH")
    plan = json.loads(job[0]["input_data"])
    assert plan["grouping_request_id"] == request_id
    assert plan["submission_id"] == ctx["submission_id"]
    assert plan["group_full_name"] == "CRE_Sub One_Group"
    assert plan["currency"] == {"code": "USD", "scheme": "RMS",
                                "vintage": "RL25", "asOfDate": "2025-05-28"}
    assert plan["propagate_detailed_losses"] is True
    assert plan["members"] == [
        {"analysis_id": ctx["a1"], "name": "CRE_P1_T1",
         "display_name": "CRE_P1_T1", "kind": "own", "edm_name": "EDM One"},
        {"analysis_id": broker, "name": "Broker EU Wind",
         "display_name": "Broker EU Wind", "kind": "broker",
         "edm_name": None},
        {"analysis_id": nested, "name": "Existing group",
         "display_name": "Existing group", "kind": "group",
         "edm_name": None},
    ]


def test_posted_name_collision_takes_the_suffix_not_an_error(iteration2_db):
    ctx = _submission_with_two_ready(iteration2_db)
    seed_group(ctx["submission_id"], "CRE_Sub One_Group")

    request_id = _request(ctx, iteration2_db)

    job = execute(
        "SELECT input_data FROM rwb_job WHERE requestor_id = :r",
        {"r": request_id}, connection="WORKBENCH")
    assert json.loads(job[0]["input_data"])["group_full_name"] == (
        "CRE_Sub One_Group_2")
