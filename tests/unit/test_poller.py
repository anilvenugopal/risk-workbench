"""Unit tests for the poller import-tracking path (US1, T019).

The poller (``app/poller/run.py``) bridges the async boundary with single-status
checks only (Article 11). On an import job's terminal status it mirrors the status in
place, backfills ``irp_id`` + ``completed_at``, and flips the ``irp_edm`` to
``ready``/``error``. ``SUBMISSION FAILED`` (never reached Risk Modeler) is distinct
from an RM-side ``FAILED`` and is never tracked. Driven by the fake IRP.
"""

from __future__ import annotations

from app.poller import run as poller
from app.services import edm_service
from app.services import package_sync_service as sync
from app.workers import package_jobs
from db import execute_one

MS = sync.MemberSpec


def _import_and_submit(drive, actor, name="EDM", fname="edm1.bak") -> tuple[str, str]:
    """Import an EDM then run its upload_edm worker body → returns (edm_id, irp_id)."""
    res = edm_service.import_edm(name=name, source_file_path=str(drive / fname),
                                 actor_id=actor)
    package_jobs.run_pending(worker_id="w1")  # submits → irp_job(import_edm, QUEUED)
    row = execute_one(
        "SELECT irp_id FROM irp_job WHERE irp_edm_id=:e AND irp_job_type='import_edm'",
        {"e": res.entity_id}, connection="WORKBENCH")
    return res.entity_id, str(row["irp_id"])


def test_finished_backfills_exposure_id_and_readies_edm(iteration2_db, fake_irp, drive):
    edm_id, irp_id = _import_and_submit(drive, iteration2_db.user_a, name="EDM")
    assert edm_service.get_edm(edm_id).status == edm_service.IMPORTING
    fake_irp.finish(irp_id)
    poller.poll_once()
    edm = edm_service.get_edm(edm_id)
    assert edm.status == edm_service.READY
    # irp_id is the RM *exposureId* (the durable entity id, resolved by name), NOT the
    # import job id — the two live in different id spaces (see irp_gateway caveat).
    exposure_id = fake_irp.edm_exposure_id("EDM")
    assert exposure_id is not None and exposure_id != irp_id
    assert edm.irp_id == int(exposure_id)
    # the import job's id is recorded separately, as created_by_irp_job_irp_id.
    row = execute_one("SELECT created_by_irp_job_irp_id FROM irp_edm WHERE id=:i",
                      {"i": edm_id}, connection="WORKBENCH")
    assert row["created_by_irp_job_irp_id"] == irp_id
    job = execute_one("SELECT status, completed_at, last_tracked_at FROM irp_job "
                      "WHERE irp_id=:i", {"i": irp_id}, connection="WORKBENCH")
    assert job["status"] == "FINISHED"
    assert job["completed_at"] is not None
    assert job["last_tracked_at"] is not None


def test_failed_terminal_flips_edm_to_error(iteration2_db, fake_irp, drive):
    edm_id, irp_id = _import_and_submit(drive, iteration2_db.user_a)
    fake_irp.fail(irp_id)
    poller.poll_once()
    edm = edm_service.get_edm(edm_id)
    assert edm.status == edm_service.ERROR
    assert edm.irp_id is None  # no id backfilled on failure


def test_submission_failed_is_not_tracked_and_distinct_from_failed(
        iteration2_db, fake_irp, drive):
    # Force the submit to fail → the worker writes SUBMISSION FAILED (no irp_id).
    res = edm_service.import_edm(name="EDM", source_file_path=str(drive / "edm1.bak"),
                                 actor_id=iteration2_db.user_a)
    fake_irp.raise_on_submit = True
    package_jobs.run_pending(worker_id="w1")
    job = execute_one("SELECT status, irp_id FROM irp_job WHERE irp_edm_id=:e",
                      {"e": res.entity_id}, connection="WORKBENCH")
    assert job["status"] == "SUBMISSION FAILED"
    assert job["irp_id"] is None
    # The poller must not touch it (no irp_id, terminal) — last_tracked_at stays null.
    poller.poll_once()
    after = execute_one("SELECT status, last_tracked_at FROM irp_job WHERE irp_edm_id=:e",
                        {"e": res.entity_id}, connection="WORKBENCH")
    assert after["status"] == "SUBMISSION FAILED"
    assert after["last_tracked_at"] is None


def _import_edm_into_package(drive, actor, name="EDM", fname="edm1.bak"):
    """Build an EDM-only package, sync it, and drive its import to ``ready`` with a
    backfilled exposureId. Returns (package_id, edm_id)."""
    pid = sync.save_package(
        package_id=None, name="P",
        members=[MS(kind="edm", name=name, source_file_path=str(drive / fname))],
        actor_id=actor).package_id
    sync.save_and_sync(package_id=pid, actor_id=actor)
    package_jobs.run_pending(worker_id="w1")  # submit import_edm → irp_job QUEUED
    import_irp = execute_one(
        "SELECT irp_id FROM irp_job WHERE irp_job_type='import_edm'",
        {}, connection="WORKBENCH")["irp_id"]
    fake_edm_id = execute_one("SELECT id FROM irp_edm WHERE package_id=:p",
                              {"p": pid}, connection="WORKBENCH")["id"]
    return pid, str(fake_edm_id), str(import_irp)


def test_failed_delete_edm_preserves_irp_id_and_can_resubmit(
        iteration2_db, fake_irp, drive):
    """A delete_edm that reaches a non-FINISHED terminal must flip the EDM to ``error``
    WITHOUT nulling its exposureId (irp_id) — otherwise a re-triggered delete takes the
    "never imported" inline branch and the RM exposure is orphaned (HIGH review item 1)."""
    actor = iteration2_db.user_a
    pid, edm_id, import_irp = _import_edm_into_package(drive, actor)
    fake_irp.finish(import_irp)
    poller.poll_once()  # EDM → ready, exposureId backfilled as irp_id
    edm = edm_service.get_edm(edm_id)
    assert edm.status == edm_service.READY and edm.irp_id is not None
    exposure_id = edm.irp_id

    # Delete the (EDM-only) package → the worker submits delete_edm against the exposure.
    sync.delete_package(package_id=pid, actor_id=actor)
    package_jobs.run_pending(worker_id="w1")
    delete_irp = execute_one(
        "SELECT irp_id FROM irp_job WHERE irp_job_type='delete_edm'",
        {}, connection="WORKBENCH")["irp_id"]
    assert len([s for s in fake_irp.submits if s["kind"] == "delete_edm"]) == 1

    # The delete job fails on the RM side.
    fake_irp.fail(str(delete_irp))
    poller.poll_once()

    edm = edm_service.get_edm(edm_id)
    assert edm.status == edm_service.ERROR
    assert edm.irp_id is not None            # exposureId preserved, not nulled
    assert edm.irp_id == exposure_id

    # A re-triggered delete must actually re-submit delete_edm (real exposure removal),
    # not silently inline-delete + finalize because irp_id was wiped.
    sync.delete_package(package_id=pid, actor_id=actor)
    package_jobs.run_pending(worker_id="w1")
    assert len([s for s in fake_irp.submits if s["kind"] == "delete_edm"]) == 2
