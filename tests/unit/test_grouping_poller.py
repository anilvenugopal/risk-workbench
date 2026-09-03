"""Unit tests for the poller's ``grouping`` job type and the group branch of
``finalize_analysis`` (spec 012, T019).

FINISHED enqueues ``finalize_analysis`` (which resolves the platform id by
name-only search — a grouping completion body carries no ``analysisId``);
FAILED extracts the failure reason onto the group row; an ambiguous or missing
name-only hit fails the job loudly.
"""

from __future__ import annotations

import json

from app.poller import run as poller
from app.services import grouping_service as svc
from app.workers import analysis_jobs, grouping_jobs
from db import execute_one
from tests.unit.analysis_rows import seed_currency, seed_edm
from tests.unit.grouping_rows import (
    link_submission_edm,
    seed_own_analysis,
    seed_submission,
)


def _submitted_group(iteration2_db, fake_irp) -> dict:
    """Drive one grouping through compose + worker so its irp_job/irp_analysis
    rows are realistic, and return the group row + its RM job id."""
    seed_currency()
    submission_id = seed_submission("Sub One")
    edm_id = seed_edm("EDM One")
    link_submission_edm(submission_id, edm_id)
    a1 = seed_own_analysis(edm_id, "CRE_P1_T1")
    a2 = seed_own_analysis(edm_id, "CRE_P2_T1")
    ids = [execute_one("SELECT irp_id FROM irp_analysis WHERE id = :id",
                       {"id": a}, connection="WORKBENCH")["irp_id"]
           for a in (a1, a2)]
    svc.request_grouping(
        submission_id=submission_id, submission_name="Sub One",
        member_ids=[a1, a2], group_name="CRE_Sub One_Group",
        currency_code="USD", currency_scheme="RMS", currency_vintage="RL25",
        num_of_simulations="1", event_rate_selections=[],
        simulation_set_selections=[],
        expected_inspection_fingerprint=f"v1:fake-{ids[0]},{ids[1]}",
        inspected_analysis_ids=[str(i) for i in ids],
        actor_id=iteration2_db.user_a)
    grouping_jobs.run_pending(worker_id="w1")
    group = execute_one(
        "SELECT id, name FROM irp_analysis WHERE is_group = 1",
        {}, connection="WORKBENCH")
    irp_job = execute_one(
        "SELECT id, irp_id FROM irp_job WHERE irp_analysis_id = :a",
        {"a": group["id"]}, connection="WORKBENCH")
    return {"group_id": group["id"], "group_name": group["name"],
            "irp_id": irp_job["irp_id"], "irp_job_id": irp_job["id"]}


def _finalize_head() -> dict | None:
    return execute_one(
        "SELECT id, input_data FROM rwb_job "
        "WHERE rwb_job_type = 'finalize_analysis'",
        {}, connection="WORKBENCH")


# ── poller routing + terminal handling ───────────────────────────────────────────

def test_finished_grouping_enqueues_finalize(iteration2_db, fake_irp):
    g = _submitted_group(iteration2_db, fake_irp)
    fake_irp.finish(g["irp_id"])

    poller.poll_once()

    head = _finalize_head()
    assert head is not None
    assert json.loads(head["input_data"]) == {"analysis_id": g["group_id"]}
    job = execute_one("SELECT status FROM irp_job WHERE id = :id",
                      {"id": g["irp_job_id"]}, connection="WORKBENCH")
    assert job["status"] == "FINISHED"


def test_failed_grouping_moves_the_group_row_to_error(iteration2_db, fake_irp):
    g = _submitted_group(iteration2_db, fake_irp)
    fake_irp.fail(g["irp_id"], result={
        "tasks": [{"output": {"errors": [{"message": "ENGINE-400: bad member"}]}}]})

    poller.poll_once()

    row = execute_one(
        "SELECT status_code, failure_reason FROM irp_analysis WHERE id = :id",
        {"id": g["group_id"]}, connection="WORKBENCH")
    assert row["status_code"] == "error"
    assert row["failure_reason"] == "ENGINE-400: bad member"
    assert _finalize_head() is None


# ── finalize_analysis group branch (name-only resolution) ───────────────────────

def test_finalize_resolves_a_group_by_name_only(iteration2_db, fake_irp):
    g = _submitted_group(iteration2_db, fake_irp)
    fake_irp.add_analysis(source_rdm_name="", exposure_name="",
                          analysis_id="9001", name=g["group_name"],
                          is_group=True, metadata={"engineType": "DLM"})
    fake_irp.finish(g["irp_id"])
    poller.poll_once()
    head = _finalize_head()

    assert analysis_jobs.run_one(rwb_job_id=head["id"],
                                 rwb_job_type="finalize_analysis")

    row = execute_one(
        "SELECT status_code, irp_id, settings_metadata FROM irp_analysis "
        "WHERE id = :id", {"id": g["group_id"]}, connection="WORKBENCH")
    assert row["status_code"] == "ready"
    assert row["irp_id"] == "9001"
    assert json.loads(row["settings_metadata"])["engineType"] == "DLM"
    retrieval = execute_one(
        "SELECT input_data FROM rwb_job "
        "WHERE rwb_job_type = 'retrieve_analysis_results'",
        {}, connection="WORKBENCH")
    assert json.loads(retrieval["input_data"]) == {"analysis_id": g["group_id"]}


def test_finalize_fails_on_zero_or_many_name_hits(iteration2_db, fake_irp):
    g = _submitted_group(iteration2_db, fake_irp)
    # Two platform analyses share the group's name — tenant hygiene error worth
    # failing loudly on (contracts/grouping-worker.md).
    fake_irp.add_analysis(source_rdm_name="", exposure_name="",
                          analysis_id="9001", name=g["group_name"])
    fake_irp.add_analysis(source_rdm_name="", exposure_name="",
                          analysis_id="9002", name=g["group_name"])
    fake_irp.finish(g["irp_id"])
    poller.poll_once()
    head = _finalize_head()

    analysis_jobs.run_one(rwb_job_id=head["id"],
                          rwb_job_type="finalize_analysis")

    finalize = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                           {"id": head["id"]}, connection="WORKBENCH")
    assert finalize["status_code"] == "failed"
    row = execute_one(
        "SELECT status_code, failure_reason, irp_id FROM irp_analysis "
        "WHERE id = :id", {"id": g["group_id"]}, connection="WORKBENCH")
    assert row["status_code"] == "error"
    assert "group resolve failed" in row["failure_reason"]
    assert row["irp_id"] is None
