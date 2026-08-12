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

from app.poller import run as poller
from app.services import (
    edm_service,
    rdm_service,
)
from app.services import (
    package_sync_service as sync,
)
from app.workers import package_jobs
from db import execute, execute_one

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
    r = rdm_service.import_rdm(name="R", source_file_path=str(drive / "rdm1.mdf"),
                                 actor_id=a).entity_id
    fake_irp.raise_on_submit = True
    package_jobs.run_pending()                        # both upload_edm + upload_rdm fail

    row = _rwb(r, "upload_rdm")
    assert row["status_code"] == "failed"
    assert rdm_service.get_rdm(r).status == rdm_service.ERROR


def test_rdm_rolls_up_ready_after_a_retry_that_followed_a_submission_failure(
        iteration2_db, fake_irp, drive):
    """Issue #38: the superseded ``SUBMISSION FAILED`` row from the first attempt must
    not drag the successful re-import back to ``error``, and ``irp_id`` must still be
    backfilled from the import that finished."""
    a = iteration2_db.user_a
    res = rdm_service.import_rdm(name="R", source_file_path=str(drive / "rdm1.mdf"),
                                 actor_id=a)
    fake_irp.raise_on_submit = True
    package_jobs.run_pending()                        # never reached RM
    assert rdm_service.get_rdm(res.entity_id).status == rdm_service.ERROR

    fake_irp.raise_on_submit = False
    rdm_service.retry_import(rdm_id=res.entity_id, actor_id=a)
    package_jobs.run_pending()                        # resubmits
    for row in execute(
            "SELECT irp_id FROM irp_job WHERE irp_job_type = 'import_rdm' "
            "AND status <> 'SUBMISSION FAILED'", {}, connection="WORKBENCH"):
        fake_irp.finish(str(row["irp_id"]))
    poller.poll_once()
    package_jobs.run_pending()                        # backfill + rollup

    rdm = rdm_service.get_rdm(res.entity_id)
    assert rdm.status == rdm_service.READY
    assert rdm.irp_id is not None


def test_retry_resubmits_after_an_rm_side_import_failure(
        iteration2_db, fake_irp, drive):
    """The worker gates on the RDM's status, not on ``import_rdm`` job history — a
    ``FAILED`` import is not "already submitted", so retry must fire a second import
    rather than leaving the RDM stuck in ``pending_import`` with nothing in flight."""
    a = iteration2_db.user_a
    res = rdm_service.import_rdm(name="R", source_file_path=str(drive / "rdm1.mdf"),
                                 actor_id=a)
    package_jobs.run_pending()
    first = execute_one("SELECT irp_id FROM irp_job WHERE irp_job_type = 'import_rdm'",
                        {}, connection="WORKBENCH")["irp_id"]
    fake_irp.fail(str(first))
    poller.poll_once()                                # RM failed the import → error
    assert rdm_service.get_rdm(res.entity_id).status == rdm_service.ERROR

    rdm_service.retry_import(rdm_id=res.entity_id, actor_id=a)
    package_jobs.run_pending()

    assert rdm_service.get_rdm(res.entity_id).status == rdm_service.IMPORTING
    assert execute_one(
        "SELECT COUNT(*) AS n FROM irp_job WHERE irp_job_type = 'import_rdm'",
        {}, connection="WORKBENCH")["n"] == 2


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
