"""Unit tests for the poller's ``export`` job type (spec 014 T032): every
terminal Risk Modeler status enqueues exactly one ``stage_results_export`` job
carrying the export, analysis, and irp_job ids; a second tick adds nothing."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.poller import run as poller
from app.services import export_service as svc
from app.workers import export_jobs
from db import execute, execute_command, execute_one
from tests.unit.export_rows import seed_analysis, seed_client, seed_edm_for, seed_submission


@pytest.fixture()
def submitted(iteration2_db, loss_db, fake_irp):
    """One export of one analysis, submitted to the fake Risk Modeler."""
    submission_id = seed_submission(iteration2_db.user_a)
    edm_id = seed_edm_for(submission_id)
    a = seed_analysis(edm_id=edm_id, irp_id="41958", irp_app_analysis_id="41958")
    seed_client()
    export_id = svc.create_export(
        submission_id=submission_id, user_email="analyst.a@example.com", analysis_ids=[a],
        perspective_code="GR", client_id=1, treaty_incept=date(2026, 4, 1), crm_id=None,
        data_vintage=None)
    export_jobs.run_pending(worker_id="w1")
    job = execute_one("SELECT id, irp_id FROM irp_job WHERE irp_job_type = 'export'", {},
                      connection="WORKBENCH")
    return {"export_id": export_id, "analysis_id": a, "edm_id": edm_id,
            "irp_job_id": job["id"], "irp_id": job["irp_id"]}


def _stage_jobs():
    return execute("SELECT * FROM rwb_job WHERE rwb_job_type = 'stage_results_export'", {},
                   connection="WORKBENCH")


@pytest.mark.parametrize("status", ["FINISHED", "FAILED", "CANCELLED"])
def test_terminal_status_enqueues_one_stage_job(submitted, fake_irp, status):
    fake_irp.jobs[submitted["irp_id"]] = status
    fake_irp.results[submitted["irp_id"]] = {"status": status}

    poller.poll_once()

    jobs = _stage_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["requestor_type"] == "irp_job" and job["requestor_id"] == submitted["irp_job_id"]
    assert job["link_type"] == "edm" and job["link_id"] == submitted["edm_id"]
    assert job["context_type"] == "irp_analysis" and job["context_id"] == submitted["analysis_id"]
    assert json.loads(job["input_data"]) == {
        "export_id": submitted["export_id"], "irp_analysis_id": submitted["analysis_id"],
        "irp_job_id": submitted["irp_job_id"]}
    tracked = execute_one("SELECT status, completed_at FROM irp_job WHERE id = :id",
                          {"id": submitted["irp_job_id"]}, connection="WORKBENCH")
    assert tracked["status"] == status and tracked["completed_at"] is not None


def test_a_running_job_enqueues_nothing_and_a_second_tick_adds_nothing(submitted, fake_irp):
    fake_irp.run(submitted["irp_id"])
    poller.poll_once()
    assert _stage_jobs() == []

    fake_irp.finish(submitted["irp_id"])
    poller.poll_once()
    poller.poll_once()
    assert len(_stage_jobs()) == 1


def test_an_analysis_with_neither_edm_nor_rdm_gets_a_not_applicable_link(submitted, fake_irp):
    execute_command("UPDATE irp_job SET irp_edm_id = NULL, irp_rdm_id = NULL", {},
                    connection="WORKBENCH")
    fake_irp.finish(submitted["irp_id"])

    poller.poll_once()

    job = _stage_jobs()[0]
    assert job["link_type"] == "not_applicable" and job["link_id"] is None
