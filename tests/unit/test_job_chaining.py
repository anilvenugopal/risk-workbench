"""Completion-chaining + fan-in idempotency (US3, T032) — the Article-2 mandate.

The A21 backbone: an ``import_edm`` reaching ``FINISHED`` makes the poller enqueue
exactly one ``upload_rdm`` head (``requestor_type='irp_job'``, keyed to that finished
import job), which fans out to one apply per RDM of THAT EDM — a per-pair fan-out gated
on the target EDM's upload, never a global head. A repeated terminal trigger (re-poll)
must never double-enqueue (SC-014).
"""

from __future__ import annotations

from app.poller import run as poller
from app.services import package_sync_service as sync
from app.workers import package_jobs
from db import execute, execute_scalar

MS = sync.MemberSpec


def _build(drive, actor, edms, rdms):
    """save_package + save_and_sync a package; return its id."""
    members = [MS(kind="edm", name=n, source_file_path=str(drive / f))
               for n, f in edms]
    members += [MS(kind="rdm", name=n, source_file_path=str(drive / f))
                for n, f in rdms]
    res = sync.save_package(package_id=None, name="Pkg", members=members,
                            actor_id=actor)
    sync.save_and_sync(package_id=res.package_id, actor_id=actor)
    return res.package_id


def _finish_all_import_edm(fake):
    for row in execute("SELECT irp_id FROM irp_job WHERE irp_job_type='import_edm'",
                       {}, connection="WORKBENCH"):
        fake.finish(str(row["irp_id"]))


def test_import_edm_finished_enqueues_one_upload_rdm_fanning_out(
        iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    pid = _build(drive, a, edms=[("E1", "edm1.bak")],
                 rdms=[("R1", "rdm1.mdf"), ("R2", "rdm2.mdf")])
    package_jobs.run_pending()                 # submit the EDM import
    _finish_all_import_edm(fake_irp)
    poller.poll_once()                         # chain the upload_rdm head

    heads = execute("SELECT id, requestor_type FROM rwb_job "
                    "WHERE rwb_job_type='upload_rdm'", {}, connection="WORKBENCH")
    assert len(heads) == 1
    assert heads[0]["requestor_type"] == "irp_job"  # keyed to the finished import job

    package_jobs.run_pending()                 # fan out to one apply per RDM
    applies = execute_scalar(
        "SELECT COUNT(*) FROM irp_job WHERE irp_job_type='import_rdm' AND package_id=:p",
        {"p": pid}, connection="WORKBENCH")
    assert applies == 2  # one per RDM of the finished EDM


def test_repeated_terminal_trigger_never_double_enqueues(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    _build(drive, a, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")])
    package_jobs.run_pending()
    _finish_all_import_edm(fake_irp)
    poller.poll_once()
    poller.poll_once()  # re-poll: the import_edm is still FINISHED
    heads = execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='upload_rdm'",
                           {}, connection="WORKBENCH")
    assert heads == 1  # idempotent on UNIQUE(requestor_type, requestor_id, rwb_job_type)


def test_per_pair_fanout_across_multiple_edms(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    pid = _build(drive, a, edms=[("E1", "edm1.bak"), ("E2", "edm2.bak")],
                 rdms=[("R1", "rdm1.mdf"), ("R2", "rdm2.mdf")])
    package_jobs.run_pending()                 # two import_edm submits
    _finish_all_import_edm(fake_irp)
    poller.poll_once()                         # one upload_rdm head per finished EDM
    heads = execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='upload_rdm'",
                           {}, connection="WORKBENCH")
    assert heads == 2  # one per EDM — gated on its own upload, not a global head
    package_jobs.run_pending()
    applies = execute_scalar(
        "SELECT COUNT(*) FROM irp_job WHERE irp_job_type='import_rdm' AND package_id=:p",
        {"p": pid}, connection="WORKBENCH")
    assert applies == 4  # 2 EDMs × 2 RDMs — one apply per pair (SC-006)
