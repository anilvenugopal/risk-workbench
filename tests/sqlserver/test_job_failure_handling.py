"""Failure handling for entity import jobs."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from dramatiq.middleware import TimeLimitExceeded

from app.services import edm_service, rdm_service
from app.workers import entity_jobs, runtime
from db import execute_command, execute_one


def _rwb(requestor_id, rwb_job_type):
    return execute_one(
        "SELECT status_code, error_detail FROM rwb_job "
        "WHERE requestor_id = :r AND rwb_job_type = :t",
        {"r": str(requestor_id), "t": rwb_job_type}, connection="WORKBENCH")


def test_upload_edm_submit_failure_fails_rwb_job_and_errors_entity(
        workbench_db, fake_irp, drive):
    result = edm_service.import_edm(
        name="E", source_file_path=str(drive / "edm1.bak"),
        actor_id=workbench_db.user_a,
    )
    fake_irp.raise_on_submit = True
    entity_jobs.run_pending()

    assert _rwb(result.entity_id, "upload_edm")["status_code"] == "failed"
    assert edm_service.get_edm(result.entity_id).status == edm_service.ERROR
    job = execute_one(
        "SELECT status, irp_id FROM irp_job WHERE irp_edm_id = :id",
        {"id": result.entity_id}, connection="WORKBENCH")
    assert job["status"] == "SUBMISSION FAILED"
    assert job["irp_id"] is None


def test_retry_import_resets_errored_edm_to_pending(workbench_db, fake_irp, drive):
    result = edm_service.import_edm(
        name="E", source_file_path=str(drive / "edm1.bak"),
        actor_id=workbench_db.user_a,
    )
    fake_irp.raise_on_submit = True
    entity_jobs.run_pending()

    edm_service.retry_import(edm_id=result.entity_id, actor_id=workbench_db.user_a)

    assert edm_service.get_edm(result.entity_id).status == edm_service.PENDING
    assert _rwb(result.entity_id, "upload_edm")["status_code"] == "pending"


def test_upload_rdm_submit_failure_fails_rwb_job_and_errors_rdm(
        workbench_db, fake_irp, drive):
    result = rdm_service.import_rdm(
        name="R", source_file_path=str(drive / "rdm1.mdf"),
        actor_id=workbench_db.user_a,
    )
    fake_irp.raise_on_submit = True
    entity_jobs.run_pending()

    assert _rwb(result.entity_id, "upload_rdm")["status_code"] == "failed"
    assert rdm_service.get_rdm(result.entity_id).status == rdm_service.ERROR


# ── dramatiq time-limit kill ─────────────────────────────────────────────────────

def test_time_limit_kill_marks_the_row_failed_before_reraising(workbench_db):
    """``TimeLimitExceeded`` is a ``BaseException`` the generic handler never
    sees. ``run_job`` must mark the row ``failed`` before re-raising — a row
    left ``running`` would be reset to ``pending`` by the reconciler and
    re-dispatched into the same kill, forever (spec 005 R1 revision: large
    breakout fan-outs made the actor time limit reachable)."""
    jid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, "
        "status_code, attempt_count, inserted_at, updated_at) "
        "VALUES (:i, 'analyst_request', :r, 'run_breakout_lob', 'pending', 0, "
        ":now, :now)",
        {"i": jid, "r": str(uuid.uuid4()), "now": datetime.utcnow()},
        connection="WORKBENCH")

    def body():
        raise TimeLimitExceeded()

    with pytest.raises(TimeLimitExceeded):
        runtime.run_job(rwb_job_id=jid, worker_id="w1", body=body)

    row = execute_one(
        "SELECT status_code, error_detail FROM rwb_job WHERE id = :i",
        {"i": jid}, connection="WORKBENCH")
    assert row["status_code"] == "failed"
    assert "time limit" in row["error_detail"]
