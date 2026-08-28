"""Unit tests for the ``submit_grouping`` worker (spec 012, T018/T021).

Covers the success path (claim → one gateway submit → ``irp_job`` recorded →
group row ``running``), claim idempotency on redelivery (PK resume), the
duplicate-name ``_n`` retry, and the uniform ``SUBMISSION FAILED`` recording
with the cause in ``failure_reason`` and no automatic retry (spec O-09).
"""

from __future__ import annotations

import json

from app.services import grouping_service as svc
from app.workers import grouping_jobs
from db import execute, execute_command, execute_one
from tests.unit.analysis_rows import seed_currency, seed_edm
from tests.unit.grouping_rows import (
    link_submission_edm,
    seed_own_analysis,
    seed_submission,
)


def _composed_grouping(iteration2_db, group_name: str = "CRE_Sub One_Group") -> dict:
    """Drive one grouping through the real compose gate and return its plan."""
    seed_currency()
    submission_id = seed_submission("Sub One")
    edm_id = seed_edm("EDM One")
    link_submission_edm(submission_id, edm_id)
    a1 = seed_own_analysis(edm_id, "CRE_P1_T1")
    a2 = seed_own_analysis(edm_id, "CRE_P2_T1")
    request_id = svc.request_grouping(
        submission_id=submission_id, submission_name="Sub One",
        member_ids=[a1, a2], group_name=group_name,
        currency_code="USD", currency_scheme="RMS", currency_vintage="RL25",
        propagate_detailed_output=True, actor_id=iteration2_db.user_a)
    job = execute_one(
        "SELECT id, input_data FROM rwb_job WHERE requestor_id = :r",
        {"r": request_id}, connection="WORKBENCH")
    return {"submission_id": submission_id, "a1": a1, "a2": a2,
            "rwb_job_id": job["id"], "plan": json.loads(job["input_data"])}


def _group_row(group_id: str) -> dict | None:
    return execute_one(
        "SELECT id, name, full_name, status_code, failure_reason, is_group, "
        "submission_id FROM irp_analysis WHERE id = :id",
        {"id": group_id}, connection="WORKBENCH")


def test_worker_claims_submits_and_records(iteration2_db, fake_irp):
    ctx = _composed_grouping(iteration2_db)

    assert grouping_jobs.run_pending(worker_id="w1") == 1

    group = _group_row(ctx["plan"]["group_analysis_id"])
    assert group["status_code"] == "running"
    assert group["name"] == "CRE_Sub One_Group"
    assert bool(group["is_group"])
    assert group["submission_id"] == ctx["submission_id"]
    members = execute(
        "SELECT member_analysis_id FROM irp_analysis_group_member "
        "WHERE group_analysis_id = :g", {"g": group["id"]},
        connection="WORKBENCH")
    assert {m["member_analysis_id"] for m in members} == {ctx["a1"], ctx["a2"]}

    assert len(fake_irp.grouping_submits) == 1
    submit = fake_irp.grouping_submits[0]
    assert submit["group_name"] == "CRE_Sub One_Group"
    assert submit["analysis_names"] == ["CRE_P1_T1", "CRE_P2_T1"]
    assert submit["analysis_edm_map"] == {"CRE_P1_T1": "EDM One",
                                          "CRE_P2_T1": "EDM One"}
    assert submit["group_names"] == set()
    assert submit["propagate_detailed_losses"] is True

    irp_job = execute_one(
        "SELECT status, irp_job_type, requested_from_submission_id, irp_id "
        "FROM irp_job WHERE irp_analysis_id = :a", {"a": group["id"]},
        connection="WORKBENCH")
    assert irp_job["status"] == "QUEUED"
    assert irp_job["irp_job_type"] == "grouping"
    assert irp_job["requested_from_submission_id"] == ctx["submission_id"]
    assert fake_irp.jobs[irp_job["irp_id"]] == "QUEUED"


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
    assert group["status_code"] == "running"
    assert group["name"] == "CRE_Sub One_Group_2"
    assert group["full_name"] == "CRE_Sub One_Group_2"
    assert [s["group_name"] for s in fake_irp.grouping_submits] == [
        "CRE_Sub One_Group", "CRE_Sub One_Group_2"]


def test_submit_failure_records_submission_failed_without_retry(
        iteration2_db, fake_irp):
    fake_irp.raise_on_submit_grouping_for.add("CRE_Sub One_Group")
    ctx = _composed_grouping(iteration2_db)

    grouping_jobs.run_pending(worker_id="w1")

    group = _group_row(ctx["plan"]["group_analysis_id"])
    assert group["status_code"] == "error"
    assert "forced grouping submit failure" in group["failure_reason"]
    irp_job = execute_one(
        "SELECT status, irp_id FROM irp_job WHERE irp_analysis_id = :a",
        {"a": group["id"]}, connection="WORKBENCH")
    assert irp_job["status"] == "SUBMISSION FAILED"
    assert irp_job["irp_id"] is None
    rwb = execute_one(
        "SELECT status_code FROM rwb_job WHERE id = :id",
        {"id": ctx["rwb_job_id"]}, connection="WORKBENCH")
    assert rwb["status_code"] == "failed"
    assert len(fake_irp.grouping_submits) == 1  # no automatic retry


def test_missing_member_failure_names_the_cause(iteration2_db, fake_irp):
    """spec O-09 / T021: the wheel raises before the POST — nothing reached the
    platform, and the cause lands verbatim in ``failure_reason``."""
    fake_irp.missing_group_members.add("CRE_P2_T1")
    ctx = _composed_grouping(iteration2_db)

    grouping_jobs.run_pending(worker_id="w1")

    group = _group_row(ctx["plan"]["group_analysis_id"])
    assert group["status_code"] == "error"
    assert "CRE_P2_T1" in group["failure_reason"]
    irp_job = execute_one(
        "SELECT status FROM irp_job WHERE irp_analysis_id = :a",
        {"a": group["id"]}, connection="WORKBENCH")
    assert irp_job["status"] == "SUBMISSION FAILED"
    assert fake_irp.jobs == {}  # nothing was created platform-side
