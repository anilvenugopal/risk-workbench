from __future__ import annotations

import json

from app.services import geohaz_service
from app.workers import geohaz_jobs, runtime
from db import execute, execute_one
from tests.sqlserver.test_geohaz_service import _edm_with_portfolios


def _run(job_id: str) -> bool:
    return runtime.run_job(
        rwb_job_id=job_id,
        worker_id="geohaz-test-worker",
        body=lambda: geohaz_jobs._run_geohaz_body(job_id),
    )


def test_worker_submit_success_records_geohaz_job_and_resource(
    iteration2_db, fake_irp,
):
    edm_id, portfolio_ids = _edm_with_portfolios(1)
    launched = geohaz_service.launch(
        edm_id=edm_id, portfolio_ids=portfolio_ids,
        actor_id=iteration2_db.user_a)

    assert _run(launched.rwb_job_ids[0]) is True

    job = execute_one(
        "SELECT * FROM irp_job WHERE irp_job_type = 'geohaz'",
        {}, connection="WORKBENCH")
    assert job["status"] == "SUBMITTED"
    assert str(job["irp_edm_id"]).lower() == edm_id
    assert str(job["irp_portfolio_id"]).lower() == portfolio_ids[0]
    assert str(job["inserted_by"]).lower() == iteration2_db.user_a
    enqueued = execute_one(
        "SELECT input_data FROM rwb_job WHERE id = :id",
        {"id": launched.rwb_job_ids[0]}, connection="WORKBENCH")
    assert (json.loads(job["request_params"])
            == json.loads(enqueued["input_data"])["params"])
    assert json.loads(job["last_submission_payload"])["kind"] == "geohaz"
    resource = execute_one(
        "SELECT resource_type, resource_uri FROM irp_job_resource "
        "WHERE irp_job_id = :id",
        {"id": str(job["id"])}, connection="WORKBENCH")
    assert resource == {
        "resource_type": "portfolio",
        "resource_uri": f"/irp/geohaz/{job['irp_id']}",
    }
    head = execute_one(
        "SELECT status_code, error_detail FROM rwb_job WHERE id = :id",
        {"id": launched.rwb_job_ids[0]}, connection="WORKBENCH")
    assert head["status_code"] == "succeeded"
    assert head["error_detail"] is None
    assert fake_irp.submits[0]["skip_prev_hazard"] is False
    assert fake_irp.submits[0]["override_user_def"] is True


def test_worker_failure_is_terminal_and_does_not_touch_sibling(
    iteration2_db, fake_irp, monkeypatch,
):
    edm_id, portfolio_ids = _edm_with_portfolios(2)
    geohaz_service.launch(
        edm_id=edm_id, portfolio_ids=portfolio_ids,
        actor_id=iteration2_db.user_b)
    enqueued = execute(
        "SELECT id, requestor_id, input_data FROM rwb_job "
        "WHERE rwb_job_type = 'run_geohaz'",
        {}, connection="WORKBENCH")
    jobs = {str(row["requestor_id"]).lower(): str(row["id"]).lower()
            for row in enqueued}
    enqueued_params = {
        str(row["requestor_id"]).lower(): json.loads(row["input_data"])["params"]
        for row in enqueued
    }
    original_submit = fake_irp.submit_geohaz

    def submit_geohaz(**kwargs):
        if kwargs["portfolio_name"] == "Portfolio 1":
            raise RuntimeError("portfolio rejected")
        return original_submit(**kwargs)

    monkeypatch.setattr(fake_irp, "submit_geohaz", submit_geohaz)
    assert _run(jobs[portfolio_ids[0]]) is True

    sibling = execute_one(
        "SELECT status_code FROM rwb_job WHERE id = :id",
        {"id": jobs[portfolio_ids[1]]}, connection="WORKBENCH")
    assert sibling["status_code"] == "pending"

    assert _run(jobs[portfolio_ids[1]]) is True
    irp_jobs = execute(
        "SELECT irp_portfolio_id, irp_id, status, request_params, inserted_by "
        "FROM irp_job WHERE irp_job_type = 'geohaz'",
        {}, connection="WORKBENCH")
    by_portfolio = {str(row["irp_portfolio_id"]).lower(): row for row in irp_jobs}
    failed = by_portfolio[portfolio_ids[0]]
    assert failed["status"] == "SUBMISSION FAILED"
    assert failed["irp_id"] is None
    assert (json.loads(failed["request_params"])
            == enqueued_params[portfolio_ids[0]])
    assert str(failed["inserted_by"]).lower() == iteration2_db.user_b
    succeeded = by_portfolio[portfolio_ids[1]]
    assert succeeded["status"] == "SUBMITTED"
    assert succeeded["irp_id"] is not None

    heads = {
        str(row["requestor_id"]).lower(): row
        for row in execute(
            "SELECT requestor_id, status_code, error_detail FROM rwb_job "
            "WHERE rwb_job_type = 'run_geohaz'",
            {}, connection="WORKBENCH")
    }
    assert heads[portfolio_ids[0]]["status_code"] == "failed"
    assert "portfolio rejected" in heads[portfolio_ids[0]]["error_detail"]
    assert heads[portfolio_ids[1]]["status_code"] == "succeeded"
