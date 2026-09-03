"""Unit tests for the ``submit_grouping`` worker (spec 012, T018/T021/T035).

Covers the success path (claim → tenant-wide name check → one gateway submit
by Platform id → ``irp_job`` recorded with the exact request body → group row
still ``pending``), claim idempotency on redelivery (PK resume), the duplicate-name
``_n`` retry driven by the worker's own pre-check, and the uniform
``SUBMISSION FAILED`` recording with the cause in ``failure_reason`` — the
exception text for a generic failure, the analyst-readable mapping of the
package's structured problems otherwise (spec O-09).
"""

from __future__ import annotations

import json

from app.services import grouping_service as svc
from app.services.irp_gateway import GroupingPartitionKey, GroupingProblem
from app.workers import grouping_jobs
from db import execute, execute_command, execute_one
from tests.unit.analysis_rows import seed_currency, seed_edm
from tests.unit.grouping_rows import (
    link_submission_edm,
    seed_own_analysis,
    seed_submission,
)

_SELECTION = {"peril_code": "WS", "region_code": "NA", "model_version": "11.0",
              "event_rate_scheme_id": 738}


def _irp(analysis_id: str) -> int:
    return int(execute_one("SELECT irp_id FROM irp_analysis WHERE id = :id",
                           {"id": analysis_id}, connection="WORKBENCH")["irp_id"])


def _composed_grouping(iteration2_db, group_name: str = "CRE_Sub One_Group",
                       fingerprint: str | None = None) -> dict:
    """Drive one grouping through the real compose gate and return its plan.
    The fingerprint defaults to the one FakeIRP's default inspection carries."""
    seed_currency()
    submission_id = seed_submission("Sub One")
    edm_id = seed_edm("EDM One")
    link_submission_edm(submission_id, edm_id)
    a1 = seed_own_analysis(edm_id, "CRE_P1_T1")
    a2 = seed_own_analysis(edm_id, "CRE_P2_T1")
    ids = [_irp(a1), _irp(a2)]
    request_id = svc.request_grouping(
        submission_id=submission_id, submission_name="Sub One",
        member_ids=[a1, a2], group_name=group_name,
        currency_code="USD", currency_scheme="RMS", currency_vintage="RL25",
        propagate_detailed_output=True,
        num_of_simulations="50000",
        event_rate_selections=[json.dumps(_SELECTION)],
        expected_inspection_fingerprint=(
            fingerprint or f"v1:fake-{ids[0]},{ids[1]}"),
        inspected_analysis_ids=[str(i) for i in ids],
        actor_id=iteration2_db.user_a)
    job = execute_one(
        "SELECT id, input_data FROM rwb_job WHERE requestor_id = :r",
        {"r": request_id}, connection="WORKBENCH")
    return {"submission_id": submission_id, "a1": a1, "a2": a2, "irp_ids": ids,
            "rwb_job_id": job["id"], "plan": json.loads(job["input_data"])}


def _group_row(group_id: str) -> dict | None:
    return execute_one(
        "SELECT id, name, full_name, status_code, failure_reason, is_group, "
        "submission_id FROM irp_analysis WHERE id = :id",
        {"id": group_id}, connection="WORKBENCH")


def _irp_job(group_id: str) -> dict:
    return execute_one(
        "SELECT status, irp_job_type, requested_from_submission_id, irp_id, "
        "last_submission_payload, last_submission_response, request_params "
        "FROM irp_job WHERE irp_analysis_id = :a", {"a": group_id},
        connection="WORKBENCH")


def test_worker_claims_submits_and_records(iteration2_db, fake_irp):
    ctx = _composed_grouping(iteration2_db)

    assert grouping_jobs.run_pending(worker_id="w1") == 1

    group = _group_row(ctx["plan"]["group_analysis_id"])
    assert group["status_code"] == "pending"
    assert group["name"] == "CRE_Sub One_Group"
    assert bool(group["is_group"])
    assert group["submission_id"] == ctx["submission_id"]
    members = execute(
        "SELECT member_analysis_id FROM irp_analysis_group_member "
        "WHERE group_analysis_id = :g", {"g": group["id"]},
        connection="WORKBENCH")
    assert {m["member_analysis_id"] for m in members} == {ctx["a1"], ctx["a2"]}

    assert fake_irp.grouping_name_checks == ["CRE_Sub One_Group"]
    assert len(fake_irp.grouping_submits) == 1
    submit = fake_irp.grouping_submits[0]
    assert submit["group_name"] == "CRE_Sub One_Group"
    assert submit["analysis_ids"] == ctx["irp_ids"]
    assert submit["propagate_detailed_losses"] is True
    assert submit["num_of_simulations"] == 50000
    assert submit["event_rate_selections"] == [_SELECTION]
    assert submit["expected_inspection_fingerprint"] == (
        ctx["plan"]["expected_inspection_fingerprint"])

    irp_job = _irp_job(group["id"])
    assert irp_job["status"] == "QUEUED"
    assert irp_job["irp_job_type"] == "grouping"
    assert irp_job["requested_from_submission_id"] == ctx["submission_id"]
    assert fake_irp.jobs[irp_job["irp_id"]] == "QUEUED"
    payload = json.loads(irp_job["last_submission_payload"])
    assert payload["resourceUris"] == [
        f"/platform/riskdata/v1/analyses/{i}" for i in ctx["irp_ids"]]
    assert payload["settings"]["numOfSimulations"] == 50000
    assert payload["settings"]["simulateToPLT"] is False
    assert json.loads(irp_job["last_submission_response"]) == {
        "job_id": int(irp_job["irp_id"])}
    assert json.loads(irp_job["request_params"])["analysis_ids"] == ctx["irp_ids"]


def test_redelivery_resumes_the_claimed_row_by_pk(iteration2_db, fake_irp):
    ctx = _composed_grouping(iteration2_db)
    group_id = ctx["plan"]["group_analysis_id"]
    # A crash between claim and submit left the row behind under its claimed
    # name; the redelivered job must reuse it, not claim a second row.
    execute_command(
        "INSERT INTO irp_analysis (id, submission_id, is_group, name, "
        "full_name, status_code, inserted_at) "
        "VALUES (:id, :sub, 1, 'Claimed name', 'Claimed name', 'pending', "
        "'2026-08-27T00:00:00')",
        {"id": group_id, "sub": ctx["submission_id"]}, connection="WORKBENCH")

    assert grouping_jobs.run_pending(worker_id="w1") == 1

    rows = execute(
        "SELECT name FROM irp_analysis WHERE is_group = 1",
        {}, connection="WORKBENCH")
    assert len(rows) == 1
    assert fake_irp.grouping_submits[0]["group_name"] == "Claimed name"


def test_duplicate_name_retries_with_the_next_suffix(iteration2_db, fake_irp):
    fake_irp.duplicate_group_names.add("CRE_Sub One_Group")
    ctx = _composed_grouping(iteration2_db)

    assert grouping_jobs.run_pending(worker_id="w1") == 1

    group = _group_row(ctx["plan"]["group_analysis_id"])
    assert group["status_code"] == "pending"
    assert group["name"] == "CRE_Sub One_Group_2"
    assert group["full_name"] == "CRE_Sub One_Group_2"
    assert fake_irp.grouping_name_checks == [
        "CRE_Sub One_Group", "CRE_Sub One_Group_2"]
    assert [s["group_name"] for s in fake_irp.grouping_submits] == [
        "CRE_Sub One_Group_2"]


def test_submit_failure_records_submission_failed_without_retry(
        iteration2_db, fake_irp):
    fake_irp.raise_on_submit_grouping_for.add("CRE_Sub One_Group")
    ctx = _composed_grouping(iteration2_db)

    grouping_jobs.run_pending(worker_id="w1")

    group = _group_row(ctx["plan"]["group_analysis_id"])
    assert group["status_code"] == "error"
    assert "forced grouping submit failure" in group["failure_reason"]
    irp_job = _irp_job(group["id"])
    assert irp_job["status"] == "SUBMISSION FAILED"
    assert irp_job["irp_id"] is None
    assert json.loads(irp_job["request_params"])["group_name"] == "CRE_Sub One_Group"
    rwb = execute_one(
        "SELECT status_code FROM rwb_job WHERE id = :id",
        {"id": ctx["rwb_job_id"]}, connection="WORKBENCH")
    assert rwb["status_code"] == "failed"
    assert len(fake_irp.grouping_submits) == 1  # no automatic retry


def test_changed_inspection_fails_the_job_and_asks_for_a_new_inspection(
        iteration2_db, fake_irp):
    """spec O-09 / US-2 acceptance 4: the package re-inspects at submit and
    rejects a stale fingerprint before any POST."""
    ctx = _composed_grouping(iteration2_db, fingerprint="v1:" + "0" * 64)

    grouping_jobs.run_pending(worker_id="w1")

    group = _group_row(ctx["plan"]["group_analysis_id"])
    assert group["status_code"] == "error"
    assert group["failure_reason"] == grouping_jobs.INSPECTION_CHANGED_REASON
    assert _irp_job(group["id"])["status"] == "SUBMISSION FAILED"
    assert fake_irp.jobs == {}  # nothing was created platform-side


def test_structured_problem_names_the_partition_and_pet_ids(
        iteration2_db, fake_irp):
    ctx = _composed_grouping(iteration2_db)
    fake_irp.grouping_submit_problems = [GroupingProblem(
        code="differing_pet_ids_unsupported",
        message="Members use different PETs in one partition.",
        analysis_ids=tuple(ctx["irp_ids"]),
        partition=GroupingPartitionKey("WS", "NA", "11.0"),
        pet_ids=(900, 901))]

    grouping_jobs.run_pending(worker_id="w1")

    group = _group_row(ctx["plan"]["group_analysis_id"])
    assert group["status_code"] == "error"
    assert group["failure_reason"] == (
        "Members use different PETs in one partition. "
        "(partition WS · NA · 11.0) (PET IDs 900, 901)")
    assert _irp_job(group["id"])["status"] == "SUBMISSION FAILED"
    assert fake_irp.jobs == {}
