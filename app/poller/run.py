"""IRP job poller — standalone process, never imported by the web layer (Article 11).

One ``poll_once`` pass per ``POLL_INTERVAL_SECS`` does three things:

1. **Track in-flight ``irp_job`` rows** — batched by type, one single-status-check
   ``get_*_job`` each, mirror the status in place, and on a terminal status backfill
   the entity + idempotently enqueue the dependent head ``rwb_job``. (The body is
   filled in per user story — US1 T022 onward; a stub here keeps the loop shape.)
2. **Reconciler** (Article 10) — reclaim ``rwb_job`` rows a dead worker left
   ``running`` (heartbeat older than ``RWB_HEARTBEAT_STALE_SECS``) back to ``pending``.
3. **``submission_retry`` batch** — re-attempt ``SUBMISSION FAILED`` ``irp_job`` rows
   under the configured max (a single-threaded batch, not a Dramatiq actor; scaffold
   here, wired in US6 T053).

``poll_*_to_completion`` is forbidden everywhere — this loop only ever uses
single-status ``get_*`` checks.

Run:
    python -m app.poller.run --loop            (interval from POLL_INTERVAL_SECS)
    python -m app.poller.run                    (single pass, for testing)
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import text

from app.config import settings
from app.services import (
    edm_service,
    irp_gateway,
    irp_job_service,
    package_sync_service,
    rdm_service,
    rwb_job_service,
)
from db import get_connection

logger = logging.getLogger(__name__)

# The single-status getter for each async op (Article 11 — never poll_*_to_completion).
_GETTERS = {
    "import_edm": irp_gateway.get_import_job,
    "import_rdm": irp_gateway.get_import_job,
    "delete_edm": irp_gateway.get_delete_edm_job,
}


def _handle_import_edm_terminal(conn, job: dict, status: str) -> None:
    """FINISHED → EDM ``ready`` + backfill ``irp_id``; then, for a package member,
    idempotently enqueue the ``upload_rdm`` head that fans out to one apply per RDM of
    THIS just-finished EDM (per-pair, FR-015/FR-043). Any other terminal → ``error``."""
    entity_status = edm_service.READY if status == "FINISHED" else edm_service.ERROR
    edm_service.backfill_on_terminal(
        conn, edm_id=job["irp_edm_id"], status=entity_status, irp_id=job["irp_id"])
    if status != "FINISHED" or not job.get("package_id"):
        return
    rdm_rows = conn.execute(text(
        "SELECT id FROM irp_rdm WHERE package_id = :p AND deleted_at IS NULL"
    ), {"p": str(job["package_id"])}).mappings().all()
    rdm_ids = [str(r["id"]) for r in rdm_rows]
    if not rdm_ids:
        return  # EDM-only package — nothing to apply
    rwb_job_service.enqueue_rwb_job(
        requestor_type="irp_job", requestor_id=job["id"], rwb_job_type="upload_rdm",
        input_data={"rdm_ids": rdm_ids, "edm_ids": [str(job["irp_edm_id"])],
                    "package_id": str(job["package_id"])},
        conn=conn,
    )


def _handle_import_rdm_terminal(conn, job: dict, status: str) -> None:
    """Roll the RDM's combined status up (data-model §6): ``ready`` once every apply is
    ``FINISHED``, ``error`` if any failed. (Iteration 6 chains retrieve_analysis_results
    here.)"""
    rdm_service.rollup_on_terminal(
        conn, rdm_id=job["irp_rdm_id"], rm_status=status, irp_id=job["irp_id"])


def _handle_delete_edm_terminal(conn, job: dict, status: str) -> None:
    """FINISHED → mark the EDM ``deleted`` and run the idempotent package-finalize
    fan-in (soft-delete the package + members once no live member remains, FR-021).
    Any other terminal → flip the EDM to ``error`` for analyst recovery."""
    if status == "FINISHED":
        edm_service.set_deleted(conn, edm_id=job["irp_edm_id"])
        if job.get("package_id"):
            package_sync_service.finalize_package(package_id=job["package_id"],
                                                  conn=conn)
    else:
        edm_service.backfill_on_terminal(
            conn, edm_id=job["irp_edm_id"], status=edm_service.ERROR, irp_id=None)


# terminal irp_job.status → handler (extended per user story).
_TERMINAL_HANDLERS = {
    "import_edm": _handle_import_edm_terminal,
    "import_rdm": _handle_import_rdm_terminal,
    "delete_edm": _handle_delete_edm_terminal,
}


def _track_irp_jobs() -> None:
    """Track in-flight ``irp_job`` rows: one single-status ``get_*_job`` each, mirror
    the status in place, and on a terminal status backfill the entity + idempotently
    enqueue the dependent head — all in one transaction per job. Batched by type."""
    for job in irp_job_service.list_non_terminal():
        getter = _GETTERS.get(job["irp_job_type"])
        if getter is None:
            continue
        try:
            result = getter(job["irp_id"])
        except Exception:
            logger.exception("get_%s_job failed for irp_id=%s",
                             job["irp_job_type"], job["irp_id"])
            continue
        try:
            with get_connection("WORKBENCH") as conn:
                with conn.begin():
                    irp_job_service.update_tracking(
                        conn, irp_job_id=job["id"], status=result.status,
                        result=result.result)
                    if result.status in irp_job_service.TERMINAL:
                        handler = _TERMINAL_HANDLERS.get(job["irp_job_type"])
                        if handler is not None:
                            handler(conn, job, result.status)
        except Exception:
            logger.exception("persisting tracking for irp_job=%s failed", job["id"])


def _submission_retry() -> None:
    """Re-attempt submit-side failures. Scaffold: with no ``IRP_SUBMISSION_MAX_RETRIES``
    configured there is nothing to do (FR-029); the full batch lands in US6 (T053)."""
    if settings.irp_submission_max_retries is None:
        logger.debug("submission_retry: IRP_SUBMISSION_MAX_RETRIES unset — skipping")
        return
    logger.debug("submission_retry: no-op scaffold (implemented in US6)")


def poll_once() -> None:
    """A single polling pass. Each batch is isolated so one failure cannot abort the
    others or the loop."""
    try:
        _track_irp_jobs()
    except Exception:
        logger.exception("poll_once: track_irp_jobs failed")
    try:
        reclaimed = rwb_job_service.reconcile_stale_rwb_jobs(
            stale_secs=settings.rwb_heartbeat_stale_secs)
        if reclaimed:
            logger.info("reconciler: reclaimed %d stale rwb_job row(s)", reclaimed)
    except Exception:
        logger.exception("poll_once: reconciler failed")
    try:
        _submission_retry()
    except Exception:
        logger.exception("poll_once: submission_retry failed")


def main() -> None:
    parser = argparse.ArgumentParser(description="IRP job status poller")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=settings.poll_interval_secs,
                        help="Seconds between passes (default: POLL_INTERVAL_SECS)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("Poller started (loop=%s interval=%ds)", args.loop, args.interval)

    if args.loop:
        import time
        while True:
            try:
                poll_once()
            except Exception:
                logger.exception("Unhandled error in poll_once")
            time.sleep(args.interval)
    else:
        poll_once()
        logger.info("Poller: single pass complete")


if __name__ == "__main__":
    main()
