"""Unit tests for the ``submit_results_export`` worker (spec 014 T031) with
``fake_irp``: one Risk Modeler export request per pending manifest row, the
``export`` irp_job written with ``export_id``, a per-analysis rejection marking
that row failed, an unreachable Risk Modeler stopping the run, and rows that
already have a job id being skipped on a re-run."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.services import export_service as svc
from app.workers import export_jobs
from db import execute, execute_one
from tests.unit.export_rows import seed_analysis, seed_client, seed_edm_for, seed_submission


@pytest.fixture()
def export(iteration2_db, loss_db, fake_irp):
    submission_id = seed_submission(iteration2_db.user_a)
    edm_id = seed_edm_for(submission_id)
    a = seed_analysis(edm_id=edm_id, name="A", irp_id="41958", irp_app_analysis_id="41958")
    b = seed_analysis(edm_id=edm_id, name="B", irp_id="41959", irp_app_analysis_id="41959")
    seed_client()
    export_id = svc.create_export(
        submission_id=submission_id, user_email="analyst.a@example.com",
        analysis_ids=[a, b], perspective_code="GR", client_id=1,
        treaty_incept=date(2026, 4, 1), crm_id="CRM-1", data_vintage=None)
    return {"export_id": export_id, "submission_id": submission_id, "edm_id": edm_id,
            "a": a, "b": b}


def _manifests(export_id):
    return {r["irp_analysis_id"]: r for r in execute(
        "SELECT * FROM stage.rwb_loss_result_manifest WHERE export_id = :e",
        {"e": export_id}, connection="LOSS")}


def _submit_job(export_id):
    return execute_one(
        "SELECT status_code, output_data, error_detail FROM rwb_job "
        "WHERE rwb_job_type = 'submit_results_export' AND requestor_id = :e",
        {"e": export_id}, connection="WORKBENCH")


def test_two_rows_submitted_with_export_jobs_and_job_ids(export, fake_irp):
    assert export_jobs.run_pending(worker_id="w1") == 1

    assert [s["analysis_id"] for s in fake_irp.export_submits] == [41958, 41959]
    assert fake_irp.export_submits[0]["loss_details"] == [{
        "metricType": "LOSS_TABLES", "outputLevels": ["Portfolio"], "perspectiveCodes": ["GR"]}]
    rows = _manifests(export["export_id"])
    assert {r["irp_export_job_id"] for r in rows.values()} == {"1", "2"}
    assert all(r["updated_at"] != r["inserted_at"] for r in rows.values())
    assert all(r["stage_status"] == "pending" for r in rows.values())

    jobs = execute("SELECT * FROM irp_job WHERE irp_job_type = 'export' ORDER BY irp_id",
                   {}, connection="WORKBENCH")
    assert [(j["irp_id"], j["irp_analysis_id"]) for j in jobs] == [
        ("1", export["a"]), ("2", export["b"])]
    assert all(j["export_id"] == export["export_id"] for j in jobs)
    assert all(j["requested_from_submission_id"] == export["submission_id"] for j in jobs)
    assert all(j["irp_edm_id"] == export["edm_id"] for j in jobs)
    assert all(j["status"] == "QUEUED" and j["submitted_at"] for j in jobs)
    assert json.loads(jobs[0]["request_params"])["settings"]["lossDetails"][0][
        "perspectiveCodes"] == ["GR"]
    job = _submit_job(export["export_id"])
    assert job["status_code"] == "succeeded"
    assert json.loads(job["output_data"]) == {"submitted": 2, "failed": 0}


def test_a_rejected_analysis_fails_its_row_and_the_rest_continue(export, fake_irp):
    fake_irp.raise_on_export_submit_for = {41958}

    export_jobs.run_pending(worker_id="w1")

    rows = _manifests(export["export_id"])
    assert rows[export["a"]]["stage_status"] == "failed"
    assert "41958 not found" in rows[export["a"]]["error_message"]
    assert rows[export["a"]]["irp_export_job_id"] is None
    assert rows[export["b"]]["irp_export_job_id"] == "1"
    assert len(execute("SELECT 1 FROM irp_job", {}, connection="WORKBENCH")) == 1
    assert json.loads(_submit_job(export["export_id"])["output_data"]) == {
        "submitted": 1, "failed": 1}


def test_unreachable_risk_modeler_stops_the_run_without_touching_rows(export, fake_irp):
    fake_irp.raise_on_export_submit = True

    export_jobs.run_pending(worker_id="w1")

    rows = _manifests(export["export_id"])
    assert all(r["stage_status"] == "pending" and r["irp_export_job_id"] is None
               for r in rows.values())
    job = _submit_job(export["export_id"])
    assert job["status_code"] == "failed" and "unreachable" in job["error_detail"]
    assert execute("SELECT 1 FROM irp_job", {}, connection="WORKBENCH") == []


def test_rows_with_a_job_id_are_skipped_on_a_rerun(export, fake_irp):
    export_jobs.run_pending(worker_id="w1")
    from app.services import rwb_job_service
    rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=export["export_id"],
        rwb_job_type="submit_results_export", link_type="not_applicable", link_id=None,
        context_type="result_export", context_id=export["export_id"],
        input_data={"export_id": export["export_id"], "submission_id": export["submission_id"]})

    export_jobs.run_pending(worker_id="w1")

    assert len(fake_irp.export_submits) == 2
    assert json.loads(_submit_job(export["export_id"])["output_data"]) == {
        "submitted": 0, "failed": 0}


def test_a_job_recorded_by_a_crashed_run_is_reused_not_resubmitted(export, fake_irp):
    # The previous run died after record_submitted_irp_job and before stamping
    # the manifest row: the row still reads pending with no job id.
    from tests.unit.export_rows import seed_export_job
    seed_export_job(export_id=export["export_id"], irp_analysis_id=export["a"], irp_id="77",
                    edm_id=export["edm_id"])

    export_jobs.run_pending(worker_id="w1")

    assert [s["analysis_id"] for s in fake_irp.export_submits] == [41959]
    rows = _manifests(export["export_id"])
    assert rows[export["a"]]["irp_export_job_id"] == "77"
    assert rows[export["b"]]["irp_export_job_id"] == "1"
    assert len(execute("SELECT 1 FROM irp_job WHERE irp_job_type = 'export'", {},
                       connection="WORKBENCH")) == 2
    assert json.loads(_submit_job(export["export_id"])["output_data"]) == {
        "submitted": 2, "failed": 0}
