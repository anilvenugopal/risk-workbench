"""Failure-handling posture for the rwb_job lifecycle (worker-poller.md §1).

The contract: a worker body that could not do its unit of work must leave the
``rwb_job`` ``failed`` (with ``error_detail``) — never a silent ``succeeded`` — and the
member entity must land in the visible, analyst-recoverable ``error`` state, the same
place an RM-side terminal failure lands. A submit that never reached Risk Modeler is
*additionally* recorded as a ``SUBMISSION FAILED`` ``irp_job`` (the poller's retry
vehicle), but that record does not make the ``rwb_job`` a success.

These lock the fix for the swallowed-failure defect: bodies used to catch their own
gateway exception and ``return {...}``, which ``run_job`` reported as ``succeeded``.

Runs on the SQLite unit mirror (``iteration2_db``) with the fake IRP; no external deps.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from dramatiq.middleware import TimeLimitExceeded

from app.poller import run as poller
from app.services import (
    edm_service,
    irp_job_service,
    rdm_service,
)
from app.services import (
    package_sync_service as sync,
)
from app.workers import package_jobs, runtime
from db import execute, execute_command, execute_one, get_connection

MS = sync.MemberSpec


def _rwb(requestor_id, rwb_job_type):
    return execute_one(
        "SELECT status_code, error_detail FROM rwb_job "
        "WHERE requestor_id = :r AND rwb_job_type = :t",
        {"r": str(requestor_id), "t": rwb_job_type}, connection="WORKBENCH")


# ── upload_edm submit failure ────────────────────────────────────────────────────

def test_upload_edm_submit_failure_fails_rwb_job_and_errors_entity(
        iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    res = edm_service.import_edm(name="E", source_file_path=str(drive / "edm1.bak"),
                                 actor_id=a)
    fake_irp.raise_on_submit = True
    package_jobs.run_pending()

    row = _rwb(res.entity_id, "upload_edm")
    assert row["status_code"] == "failed"            # not a silent succeeded
    assert row["error_detail"]                        # carries the reason
    assert edm_service.get_edm(res.entity_id).status == edm_service.ERROR
    job = execute_one("SELECT status, irp_id FROM irp_job WHERE irp_edm_id = :e",
                      {"e": res.entity_id}, connection="WORKBENCH")
    assert job["status"] == "SUBMISSION FAILED" and job["irp_id"] is None


def test_errored_edm_recovers_on_resync(iteration2_db, fake_irp, drive):
    """An errored EDM re-submits when the analyst clicks Save & Sync again — the
    request path resets it to pending_import so the worker body advances it."""
    a = iteration2_db.user_a
    pid = sync.save_package(
        package_id=None, name="P",
        members=[MS(kind="edm", name="E", source_file_path=str(drive / "edm1.bak"))],
        actor_id=a).package_id
    sync.save_and_sync(package_id=pid, actor_id=a)
    fake_irp.raise_on_submit = True
    package_jobs.run_pending()                        # fails → EDM error, rwb failed
    edm_id = execute_one("SELECT id FROM irp_edm WHERE package_id = :p",
                         {"p": pid}, connection="WORKBENCH")["id"]
    assert edm_service.get_edm(edm_id).status == edm_service.ERROR

    fake_irp.raise_on_submit = False
    sync.save_and_sync(package_id=pid, actor_id=a)    # re-sync: error → pending, re-enqueue
    package_jobs.run_pending()                        # now the submit succeeds

    assert edm_service.get_edm(edm_id).status == edm_service.IMPORTING
    assert _rwb(edm_id, "upload_edm")["status_code"] == "succeeded"
    assert execute_one(
        "SELECT status FROM irp_job WHERE irp_edm_id = :e AND status <> 'SUBMISSION FAILED'",
        {"e": edm_id}, connection="WORKBENCH")["status"] == "QUEUED"


def test_retry_import_resets_errored_edm_to_pending(iteration2_db, fake_irp, drive):
    """Per-member retry (FR-045) must reset the entity, not just the head — otherwise
    the worker body skips the still-``error`` row and the resubmit never fires."""
    a = iteration2_db.user_a
    res = edm_service.import_edm(name="E", source_file_path=str(drive / "edm1.bak"),
                                 actor_id=a)
    fake_irp.raise_on_submit = True
    package_jobs.run_pending()
    assert edm_service.get_edm(res.entity_id).status == edm_service.ERROR

    edm_service.retry_import(edm_id=res.entity_id, actor_id=a)
    assert edm_service.get_edm(res.entity_id).status == edm_service.PENDING
    assert _rwb(res.entity_id, "upload_edm")["status_code"] == "pending"


# ── upload_rdm submit failure ────────────────────────────────────────────────────

def test_upload_rdm_submit_failure_fails_rwb_job_and_errors_rdm(
        iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    e = edm_service.import_edm(name="E", source_file_path=str(drive / "edm1.bak"),
                               actor_id=a).entity_id
    r = rdm_service.import_rdm(name="R", source_file_path=str(drive / "rdm1.mdf"),
                               applied_edm_ids=[e], actor_id=a).entity_id
    fake_irp.raise_on_submit = True
    package_jobs.run_pending()                        # both upload_edm + upload_rdm fail

    row = _rwb(r, "upload_rdm")
    assert row["status_code"] == "failed"
    assert rdm_service.get_rdm(r).status == rdm_service.ERROR


def test_rdm_rollup_stays_error_when_an_apply_submission_failed(
        iteration2_db, fake_irp, drive):
    """The combined rollup must count a ``SUBMISSION FAILED`` apply as a failure, so a
    later FINISHED apply cannot mask it by flipping the RDM to ``ready``."""
    a = iteration2_db.user_a
    pid = sync.save_package(
        package_id=None, name="P",
        members=[MS(kind="edm", name="E1", source_file_path=str(drive / "edm1.bak")),
                 MS(kind="edm", name="E2", source_file_path=str(drive / "edm2.bak")),
                 MS(kind="rdm", name="R1", source_file_path=str(drive / "rdm1.mdf"))],
        actor_id=a).package_id
    edms = [row["id"] for row in execute(
        "SELECT id FROM irp_edm WHERE package_id = :p", {"p": pid},
        connection="WORKBENCH")]
    rid = execute_one("SELECT id FROM irp_rdm WHERE package_id = :p",
                      {"p": pid}, connection="WORKBENCH")["id"]

    # One apply reached FINISHED; the other never reached RM (SUBMISSION FAILED).
    irp_job_service.record_submitted_irp_job(
        package_id=pid, irp_job_type="import_rdm", irp_edm_id=edms[0],
        irp_rdm_id=rid, irp_id="500")
    execute_command("UPDATE irp_job SET status = 'FINISHED' WHERE irp_id = '500'",
                    {}, connection="WORKBENCH")
    irp_job_service.record_submission_failure(
        package_id=pid, irp_job_type="import_rdm", irp_edm_id=edms[1], irp_rdm_id=rid)

    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            rdm_service.rollup_on_terminal(conn, rdm_id=rid, rm_status="FINISHED",
                                           irp_id="500")
    assert rdm_service.get_rdm(rid).status == rdm_service.ERROR


# ── delete_edm submit failure ────────────────────────────────────────────────────

def test_delete_edm_submit_failure_fails_rwb_job(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    pid = sync.save_package(
        package_id=None, name="P",
        members=[MS(kind="edm", name="E", source_file_path=str(drive / "edm1.bak"))],
        actor_id=a).package_id
    sync.save_and_sync(package_id=pid, actor_id=a)
    package_jobs.run_pending()                        # import_edm submitted
    for row in execute("SELECT irp_id FROM irp_job WHERE irp_job_type = 'import_edm'",
                       {}, connection="WORKBENCH"):
        fake_irp.finish(str(row["irp_id"]))
    poller.poll_once()                                # EDM → ready, irp_id backfilled
    edm_id = execute_one("SELECT id FROM irp_edm WHERE package_id = :p",
                         {"p": pid}, connection="WORKBENCH")["id"]
    assert edm_service.get_edm(edm_id).status == edm_service.READY

    sync.delete_package(package_id=pid, actor_id=a)   # EDM-only → delete_edm head
    fake_irp.raise_on_submit = True
    package_jobs.run_pending()

    assert _rwb(edm_id, "delete_edm")["status_code"] == "failed"
    assert execute_one(
        "SELECT status FROM irp_job WHERE irp_edm_id = :e AND irp_job_type = 'delete_edm'",
        {"e": edm_id}, connection="WORKBENCH")["status"] == "SUBMISSION FAILED"
    # The EDM is NOT 'deleted' (still delete_pending), so the package must not finalize.
    assert execute_one("SELECT deleted_at FROM package WHERE id = :p",
                       {"p": pid}, connection="WORKBENCH")["deleted_at"] is None


# ── dramatiq time-limit kill ─────────────────────────────────────────────────────

def test_time_limit_kill_marks_the_row_failed_before_reraising(iteration2_db):
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
