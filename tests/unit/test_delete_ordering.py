"""Delete ordering + fan-in idempotency (US4, T037).

Delete is asymmetric (A21 / R6): RDM removal is **synchronous** (no ``irp_job``); EDM
removal is **async** (a pollable ``irp_job(delete_edm)``). ``delete_edm`` is enqueued
only once **all** the package's RDMs are ``deleted`` (RDM-before-EDM). A duplicate
``delete_rdm`` success never double-enqueues, and the package soft-delete fires exactly
once — never a hard delete (SC-007/SC-014).
"""

from __future__ import annotations

from app.poller import run as poller
from app.services import package_sync_service as sync
from app.workers import package_jobs
from db import execute, execute_command, execute_scalar

MS = sync.MemberSpec


def _ready_package(drive, actor, n_edms=1, n_rdms=1):
    """Build a package and force its members to ``ready`` with fake irp_ids, so a
    delete has real members to remove."""
    members = [MS(kind="edm", name=f"E{i}", source_file_path=str(drive / "edm1.bak"))
               for i in range(n_edms)]
    members += [MS(kind="rdm", name=f"R{i}", source_file_path=str(drive / "rdm1.mdf"))
                for i in range(n_rdms)]
    pid = sync.save_package(package_id=None, name="P", members=members,
                            actor_id=actor).package_id
    seq = iter(range(1000, 2000))
    for row in execute("SELECT id FROM irp_edm WHERE package_id=:p",
                       {"p": pid}, connection="WORKBENCH"):
        execute_command("UPDATE irp_edm SET status='ready', irp_id=:i WHERE id=:id",
                        {"i": next(seq), "id": str(row["id"])}, connection="WORKBENCH")
    for row in execute("SELECT id FROM irp_rdm WHERE package_id=:p",
                       {"p": pid}, connection="WORKBENCH"):
        execute_command("UPDATE irp_rdm SET status='ready', irp_id=:i WHERE id=:id",
                        {"i": next(seq), "id": str(row["id"])}, connection="WORKBENCH")
    return pid


def _finish_all_delete_edm(fake):
    for row in execute("SELECT irp_id FROM irp_job WHERE irp_job_type='delete_edm'",
                       {}, connection="WORKBENCH"):
        fake.finish(str(row["irp_id"]))


def test_delete_enqueues_rdm_removals_before_edm(iteration2_db, fake_irp, drive):
    pid = _ready_package(drive, iteration2_db.user_a, n_edms=1, n_rdms=2)
    sync.delete_package(package_id=pid, actor_id=iteration2_db.user_a)
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='delete_rdm'",
                          {}, connection="WORKBENCH") == 2
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='delete_edm'",
                          {}, connection="WORKBENCH") == 0  # not yet — RDMs first


def test_delete_rdm_is_synchronous_no_irp_job(iteration2_db, fake_irp, drive):
    pid = _ready_package(drive, iteration2_db.user_a, n_edms=1, n_rdms=1)
    sync.delete_package(package_id=pid, actor_id=iteration2_db.user_a)
    package_jobs.run_pending()  # run the delete_rdm worker
    assert fake_irp.deleted_rdm_names  # analyses deleted synchronously
    assert execute_scalar(
        "SELECT COUNT(*) FROM irp_job WHERE irp_job_type='delete_rdm'",
        {}, connection="WORKBENCH") == 0  # RDM delete never writes an irp_job (R6)


def test_delete_edm_enqueued_only_after_all_rdms_deleted(iteration2_db, fake_irp, drive):
    pid = _ready_package(drive, iteration2_db.user_a, n_edms=1, n_rdms=2)
    sync.delete_package(package_id=pid, actor_id=iteration2_db.user_a)
    package_jobs.run_pending()  # both delete_rdm workers → all RDMs deleted → fan-in
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='delete_edm'",
                          {}, connection="WORKBENCH") == 1  # one per EDM, now enqueued
    package_jobs.run_pending()  # delete_edm worker → pollable irp_job
    assert execute_scalar(
        "SELECT COUNT(*) FROM irp_job WHERE irp_job_type='delete_edm'",
        {}, connection="WORKBENCH") == 1


def test_duplicate_delete_rdm_success_never_double_enqueues(iteration2_db, fake_irp, drive):
    pid = _ready_package(drive, iteration2_db.user_a, n_edms=1, n_rdms=1)
    sync.delete_package(package_id=pid, actor_id=iteration2_db.user_a)
    rdm_head = execute("SELECT id FROM rwb_job WHERE rwb_job_type='delete_rdm'",
                       {}, connection="WORKBENCH")[0]["id"]
    # run the delete_rdm body twice (e.g. a reconciler redelivery).
    package_jobs.run_one(rwb_job_id=rdm_head, rwb_job_type="delete_rdm")
    execute_command("UPDATE rwb_job SET status_code='pending' WHERE id=:id",
                    {"id": str(rdm_head)}, connection="WORKBENCH")  # simulate reclaim
    package_jobs.run_one(rwb_job_id=rdm_head, rwb_job_type="delete_rdm")
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='delete_edm'",
                          {}, connection="WORKBENCH") == 1  # not doubled


def test_package_soft_deletes_once_no_hard_delete(iteration2_db, fake_irp, drive):
    pid = _ready_package(drive, iteration2_db.user_a, n_edms=1, n_rdms=1)
    sync.delete_package(package_id=pid, actor_id=iteration2_db.user_a)
    package_jobs.run_pending()   # delete_rdm → fan-in delete_edm
    package_jobs.run_pending()   # delete_edm → pollable irp_job
    _finish_all_delete_edm(fake_irp)
    poller.poll_once()           # delete_edm FINISHED → finalize
    pkg = execute("SELECT deleted_at FROM package WHERE id=:p", {"p": pid},
                  connection="WORKBENCH")
    assert pkg[0]["deleted_at"] is not None       # soft-deleted, not hard-deleted
    assert execute_scalar("SELECT COUNT(*) FROM package WHERE id=:p", {"p": pid},
                          connection="WORKBENCH") == 1  # row still exists
    # members soft-deleted too (FR-021)
    assert execute_scalar("SELECT COUNT(*) FROM irp_edm WHERE package_id=:p "
                          "AND deleted_at IS NULL", {"p": pid},
                          connection="WORKBENCH") == 0
    # re-poll must not double-finalize (idempotent).
    poller.poll_once()
    assert execute_scalar("SELECT COUNT(*) FROM package WHERE id=:p", {"p": pid},
                          connection="WORKBENCH") == 1
