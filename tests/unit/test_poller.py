"""Unit tests for the poller import-tracking path (US1, T019).

The poller (``app/poller/run.py``) bridges the async boundary with single-status
checks only (Article 11). On an import job's terminal status it mirrors the status in
place, backfills ``irp_id`` + ``completed_at``, and flips the ``irp_edm`` to
``ready``/``error``. ``SUBMISSION FAILED`` (never reached Risk Modeler) is distinct
from an RM-side ``FAILED`` and is never tracked. Driven by the fake IRP.
"""

from __future__ import annotations

import logging
import re

from app.poller import run as poller
from app.services import edm_service
from app.services import package_sync_service as sync
from app.workers import package_jobs
from db import execute, execute_one

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


# ── spec 004 (US1, T013): import_edm FINISHED also enqueues backfill_edm_detail ──

def _rwb_jobs_of(rwb_job_type: str) -> list[dict]:
    return execute(
        "SELECT id, requestor_type, requestor_id, rwb_job_type, input_data "
        "FROM rwb_job WHERE rwb_job_type = :t",
        {"t": rwb_job_type}, connection="WORKBENCH")


def test_finished_enqueues_backfill_edm_detail_and_no_rdm_work(
        iteration2_db, fake_irp, drive):
    """A package member's import_edm FINISHED enqueues backfill_edm_detail and
    nothing else. The package's RDMs already have their own upload_rdm heads from
    save_and_sync, so the poller must not chain a second one (worker-poller.md §3);
    a re-poll re-inserts neither."""
    actor = iteration2_db.user_a
    pid = sync.save_package(
        package_id=None, name="P",
        members=[MS(kind="edm", name="EDM", source_file_path=str(drive / "edm1.bak")),
                 MS(kind="rdm", name="RDM", source_file_path=str(drive / "rdm1.mdf"))],
        actor_id=actor).package_id
    sync.save_and_sync(package_id=pid, actor_id=actor)
    assert len(_rwb_jobs_of("upload_rdm")) == 1          # enqueued at sync, not chained
    package_jobs.run_pending(worker_id="w1")  # submit import_edm + import_rdm
    job = execute_one(
        "SELECT id, irp_id, irp_edm_id FROM irp_job WHERE irp_job_type='import_edm'",
        {}, connection="WORKBENCH")
    fake_irp.finish(str(job["irp_id"]))
    poller.poll_once()

    backfills = _rwb_jobs_of("backfill_edm_detail")
    assert len(backfills) == 1
    assert backfills[0]["requestor_type"] == "irp_job"
    assert backfills[0]["requestor_id"] == str(job["id"])
    assert str(job["irp_edm_id"]) in backfills[0]["input_data"]
    assert len(_rwb_jobs_of("upload_rdm")) == 1          # still the sync-time head only

    poller.poll_once()  # re-poll: idempotent — no double backfill
    assert len(_rwb_jobs_of("backfill_edm_detail")) == 1


def test_standalone_edm_import_still_enqueues_backfill_edm_detail(
        iteration2_db, fake_irp, drive):
    """A standalone import (no package) gets its detail backfilled too — the
    enqueue is independent of package_id."""
    edm_id, irp_id = _import_and_submit(drive, iteration2_db.user_a)
    fake_irp.finish(irp_id)
    poller.poll_once()
    backfills = _rwb_jobs_of("backfill_edm_detail")
    assert len(backfills) == 1
    assert edm_id in backfills[0]["input_data"]
    assert _rwb_jobs_of("upload_rdm") == []  # no RDM in play


def test_failed_terminal_enqueues_neither_backfill(iteration2_db, fake_irp, drive):
    """A FAILED/CANCELLED terminal flips the EDM to error and enqueues no
    follow-up work — there is no detail to fetch."""
    edm_id, irp_id = _import_and_submit(drive, iteration2_db.user_a)
    fake_irp.fail(irp_id)
    poller.poll_once()
    assert edm_service.get_edm(edm_id).status == edm_service.ERROR
    assert _rwb_jobs_of("backfill_edm_detail") == []
    assert _rwb_jobs_of("upload_rdm") == []


# ── poller business-level logging (#28 follow-up, PR #31) ────────────────────────

def test_transition_logged_once_and_terminal_line_carries_duration(
        iteration2_db, fake_irp, drive, caplog):
    """Business-level poller logs (#28 follow-up): an observed status change logs one
    INFO line, an unchanged status logs nothing at INFO, and the terminal line carries
    the elapsed time since submit."""
    _, irp_id = _import_and_submit(drive, iteration2_db.user_a)
    with caplog.at_level(logging.INFO, logger="app.poller.run"):
        fake_irp.run(irp_id)
        poller.poll_once()
        assert sum("irp_job status: QUEUED -> RUNNING" in r.getMessage()
                   for r in caplog.records) == 1
        caplog.clear()
        poller.poll_once()  # still RUNNING — an observation, not a transition
        assert not any("irp_job status:" in r.getMessage() for r in caplog.records)
        fake_irp.finish(irp_id)
        poller.poll_once()
    terminal = [r.getMessage() for r in caplog.records
                if "irp_job terminal:" in r.getMessage()]
    assert len(terminal) == 1
    assert "RUNNING -> FINISHED" in terminal[0]
    assert re.search(r"\(after \d+[hms]", terminal[0])


def test_every_status_check_logged_at_debug(iteration2_db, fake_irp, drive, caplog):
    _, irp_id = _import_and_submit(drive, iteration2_db.user_a)
    with caplog.at_level(logging.DEBUG, logger="app.poller.run"):
        poller.poll_once()
    checks = [r for r in caplog.records
              if r.levelno == logging.DEBUG
              and "irp_job status check" in r.getMessage()]
    assert len(checks) == 1
    assert any("tracking 1 in-flight irp_job(s)" in r.getMessage()
               for r in caplog.records)


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
