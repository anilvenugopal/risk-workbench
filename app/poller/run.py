"""IRP job poller — standalone process, never imported by the web layer (Article 11).

One ``poll_once`` pass per ``POLL_INTERVAL_SECS`` does four things:

1. **Track in-flight ``irp_job`` rows** — batched by type, one single-status-check
   ``get_*_job`` each, mirror the status in place, and on a terminal status backfill
   the entity + idempotently enqueue the dependent head ``rwb_job``. (The body is
   filled in per user story — US1 T022 onward; a stub here keeps the loop shape.)
2. **Reconciler** (Article 10) — reclaim ``rwb_job`` rows a dead worker left
   ``running`` (heartbeat older than ``RWB_HEARTBEAT_STALE_SECS``) back to ``pending``.
3. **Dispatch pending heads** — wake a worker for every ``pending`` ``rwb_job``. The
   heads this poller enqueues in step 1 (``upload_rdm``, ``backfill_rdm_analyses``,
   ``backfill_edm_detail``) are never dispatched at enqueue time (the poller is a
   separate process from the worker), so without this the EDM→RDM chain stalls; this
   also delivers the rows step 2 reset.
4. **``submission_retry`` batch** — re-attempt ``SUBMISSION FAILED`` ``irp_job`` rows
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
from datetime import datetime, timezone

from sqlalchemy import text

from app import log_context
from app.config import settings
from app.logging_setup import setup_logging
from app.services import (
    edm_service,
    irp_gateway,
    irp_job_service,
    portfolio_service,
    rdm_service,
    rwb_job_service,
)
from app.workers import dispatch
from db import execute, execute_one, get_connection

logger = logging.getLogger(__name__)

# The single-status getter for each async op (Article 11 — never poll_*_to_completion).
_GETTERS = {
    "import_edm": irp_gateway.get_import_job,
    "import_rdm": irp_gateway.get_import_job,
    "geohaz": irp_gateway.get_geohaz_job,
}


def _handle_import_edm_terminal(conn, job: dict, status: str, resolved: dict) -> None:
    """FINISHED → EDM ``ready`` + backfill the RM ``exposureId`` (the durable entity id,
    resolved by name into ``resolved['edm_exposure_id']``) as ``irp_id`` and the import
    job id as ``created_by_irp_job_irp_id``; then idempotently enqueue the
    ``backfill_edm_detail`` head. Any other terminal marks the EDM as ``error``."""
    if status == "FINISHED":
        edm_service.backfill_on_terminal(
            conn, edm_id=job["irp_edm_id"], status=edm_service.READY,
            irp_id=resolved.get("edm_exposure_id"),
            created_by_irp_job_irp_id=job["irp_id"])
        rwb_job_service.enqueue_rwb_job(
            requestor_type="irp_job", requestor_id=job["id"],
            rwb_job_type="backfill_edm_detail",
            input_data={"edm_id": str(job["irp_edm_id"])},
            conn=conn,
        )
    else:
        edm_service.backfill_on_terminal(
            conn, edm_id=job["irp_edm_id"], status=edm_service.ERROR, irp_id=None)


def _handle_import_rdm_terminal(conn, job: dict, status: str, resolved: dict) -> None:
    """On ``FINISHED``, enqueue one RDM-wide analysis backfill.

    The worker marks the RDM ready after capture. Other terminal statuses mark
    the RDM as error.
    """
    if status == "FINISHED":
        jid = rwb_job_service.enqueue_rwb_job(
            requestor_type="irp_job", requestor_id=job["id"],
            rwb_job_type="backfill_rdm_analyses",
            input_data={
                "rdm_id": (str(job["irp_rdm_id"]) if job["irp_rdm_id"] else None),
                "apply_irp_id": job["irp_id"]},
            conn=conn,
        )
        if jid:
            logger.info("chained backfill_rdm_analyses head")
    else:
        rdm_service.rollup_on_terminal(
            conn, rdm_id=job["irp_rdm_id"], rm_status=status, irp_id=None)


def _handle_geohaz_terminal(conn, job: dict, status: str, resolved: dict) -> None:
    # _resolve_geohaz_metadata returns {} unless FINISHED, so a present value is
    # already proof of a FINISHED run.
    metadata = resolved.get("portfolio_metadata")
    if metadata is not None:
        portfolio_service.update_exposure_metrics(
            conn, portfolio_id=job["irp_portfolio_id"], metrics=metadata)
    # A hazard lookup writes hazard data onto the portfolio's locations, which
    # advances Risk Modeler's stampDate. The breakout confirm compares that
    # stamp against exposure_detail.stamp_date (spec 005 FR-002a), so without a
    # re-sync every later breakout on this portfolio is refused as stale. The
    # backfill rewrites metrics, summary, and stamp_date from one read, keeping
    # the stored stamp and the summary it describes in step. Chained on any
    # terminal status: a failed lookup can still have written part of its data.
    rwb_job_service.enqueue_rwb_job(
        requestor_type="irp_job", requestor_id=job["id"],
        rwb_job_type="backfill_edm_detail",
        input_data={"edm_id": str(job["irp_edm_id"])},
        conn=conn,
    )


# terminal irp_job.status → handler (extended per user story).
_TERMINAL_HANDLERS = {
    "import_edm": _handle_import_edm_terminal,
    "import_rdm": _handle_import_rdm_terminal,
    "geohaz": _handle_geohaz_terminal,
}


def _resolve_edm_exposure_id(edm_id) -> str | None:
    """Resolve a just-imported EDM's durable RM ``exposureId`` by name — the entity id
    delete needs, NOT the import job id (see the ``irp_gateway`` caveat). Best-effort:
    on miss/failure return ``None`` so the EDM still reaches ``ready`` and can be
    recovered later. Names are not unique in RM (duplicates can pre-date the blocking
    collision check, or slip through its fail-open window — issue #17), so a search may
    return >1 — take the newest (highest ``exposureId``), which is the just-created one."""
    edm = edm_service.get_edm(edm_id)
    if edm is None:
        return None
    try:
        hits = irp_gateway.search_edms(edm.name)
    except Exception:
        logger.exception("exposureId resolve failed for edm=%s", edm_id)
        return None
    ids = [h.irp_id for h in hits if h.irp_id]
    if not ids:
        logger.warning("no exposureId found by name for edm=%s (%r)", edm_id, edm.name)
        return None
    try:
        return max(ids, key=lambda x: int(x))
    except (TypeError, ValueError):
        return ids[-1]


def _resolve_geohaz_metadata(job: dict, result) -> dict:
    if result.status != "FINISHED" or not job.get("irp_portfolio_id"):
        return {}
    ids = execute_one(
        "SELECT e.irp_id AS edm_irp_id, p.irp_id AS portfolio_irp_id "
        "FROM irp_portfolio p JOIN irp_edm e ON e.id = p.edm_id "
        "WHERE p.id = :id AND p.deleted_at IS NULL",
        {"id": str(job["irp_portfolio_id"])}, connection="WORKBENCH")
    if not ids or ids["edm_irp_id"] is None or ids["portfolio_irp_id"] is None:
        logger.warning("portfolio metadata ids unavailable for irp_job=%s", job["id"])
        return {}
    exposure = irp_gateway.get_portfolio_exposure(
        edm_irp_id=int(ids["edm_irp_id"]),
        portfolio_irp_id=int(ids["portfolio_irp_id"]))
    return {"portfolio_metadata": exposure.payload}


# Terminal-time entity-id lookups that need a Risk Modeler call — run OUTSIDE the DB
# transaction (Article 11: never hold a txn across a network round-trip). Each returns
# a dict merged into the handler's ``resolved`` argument.
_TERMINAL_RESOLVERS = {
    "import_edm": lambda job, result: (
        {"edm_exposure_id": _resolve_edm_exposure_id(job["irp_edm_id"])}
        if result.status == "FINISHED" else {}),
    "geohaz": _resolve_geohaz_metadata,
}


def _geohaz_completion_summary(result: dict | None) -> str | None:
    """Return the GeoHaz task's summary text from a terminal completion body."""
    if not isinstance(result, dict):
        return None
    tasks = result.get("tasks")
    if not isinstance(tasks, list):
        return None
    for task in tasks:
        if not isinstance(task, dict):
            continue
        output = task.get("output")
        summary = output.get("summary") if isinstance(output, dict) else None
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    return None


# Per-type completion-summary extractor, applied only on a terminal write —
# update_tracking keeps the stored summary unchanged while the job is running.
_SUMMARIZERS = {
    "geohaz": _geohaz_completion_summary,
}


def _fmt_elapsed(submitted_at) -> str:
    """``4m22s``-style elapsed time since a naive-UTC stamp — a ``datetime`` from
    SQL Server, an ISO string from the SQLite unit tier; ``?`` when unparseable."""
    if isinstance(submitted_at, str):
        try:
            submitted_at = datetime.fromisoformat(submitted_at)
        except ValueError:
            return "?"
    if not isinstance(submitted_at, datetime):
        return "?"
    secs = (datetime.now(timezone.utc).replace(tzinfo=None)
            - submitted_at).total_seconds()
    if secs < 0:
        return "?"
    mins, s = divmod(int(secs), 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}h{mins:02d}m{s:02d}s"
    return f"{mins}m{s:02d}s" if mins else f"{s}s"


def _track_irp_jobs() -> None:
    """Track in-flight ``irp_job`` rows: one single-status ``get_*_job`` each, mirror
    the status in place, and on a terminal status backfill the entity + idempotently
    enqueue the dependent head — all in one transaction per job. Batched by type.

    Observed status *transitions* log at INFO (terminal ones with elapsed-since-submit);
    every check logs at DEBUG. A transition seen across one pass may collapse
    intermediate RM states (QUEUED -> FINISHED), so the log is not a full history."""
    jobs = irp_job_service.list_non_terminal()
    logger.debug("tracking %d in-flight irp_job(s)", len(jobs))
    for job in jobs:
        # Per-job log context: the chained enqueue_rwb_job calls inside the
        # terminal handlers inherit correlation_id through this bind, so the
        # whole chain keeps one id (issue #28).
        token = log_context.bind(
            correlation_id=job.get("correlation_id"), irp_job_id=str(job["id"]),
            irp_job_type=job["irp_job_type"], irp_id=job["irp_id"])
        try:
            getter = _GETTERS.get(job["irp_job_type"])
            if getter is None:
                logger.warning("No getter for irp_job_type=%s, skipping id=%s",
                               job["irp_job_type"], job["id"])
                continue
            try:
                result = getter(job["irp_id"])
            except Exception:
                logger.exception("get_%s_job failed for irp_id=%s",
                                 job["irp_job_type"], job["irp_id"])
                continue
            logger.debug("irp_job status check: %s", result.status)
            # Resolve any terminal-time entity ids needing a Risk Modeler lookup BEFORE
            # opening the DB transaction (Article 11 — never hold a txn across HTTP).
            resolved: dict = {}
            if result.status in irp_job_service.TERMINAL:
                resolver = _TERMINAL_RESOLVERS.get(job["irp_job_type"])
                if resolver is not None:
                    try:
                        resolved = resolver(job, result) or {}
                    except Exception:
                        logger.exception("terminal resolver failed for irp_job=%s",
                                         job["id"])
            try:
                with get_connection("WORKBENCH") as conn:
                    with conn.begin():
                        summary = None
                        if result.status in irp_job_service.TERMINAL:
                            summarizer = _SUMMARIZERS.get(job["irp_job_type"])
                            if summarizer is not None:
                                summary = summarizer(result.result)
                        irp_job_service.update_tracking(
                            conn, irp_job_id=job["id"], status=result.status,
                            result=result.result, completion_summary=summary,
                        )
                        if result.status in irp_job_service.TERMINAL:
                            handler = _TERMINAL_HANDLERS.get(job["irp_job_type"])
                            if handler is not None:
                                handler(conn, job, result.status, resolved)
                if result.status in irp_job_service.TERMINAL:
                    logger.info("irp_job terminal: %s -> %s (after %s)",
                                job["status"], result.status,
                                _fmt_elapsed(job["submitted_at"]))
                elif result.status != job["status"]:
                    logger.info("irp_job status: %s -> %s",
                                job["status"], result.status)
            except Exception:
                logger.exception("persisting tracking for irp_job=%s failed",
                                 job["id"])
        finally:
            log_context.clear(token)


def _dispatch_pending() -> None:
    """Deliver every currently-``pending`` ``rwb_job`` to a worker.

    The poller enqueues the chained heads (``upload_rdm`` + ``backfill_edm_detail``
    when an ``import_edm`` reaches FINISHED; ``backfill_rdm_analyses`` when an
    ``import_rdm`` does) but runs in its own process, so — unlike the request path
    and the worker's own follow-on enqueues — those rows are never dispatched at
    enqueue time. Without this sweep they sit
    ``pending`` forever and the EDM→RDM chain stalls.

    A Dramatiq message is only a wake-up (Article 10): re-sending one for a row already
    in flight is harmless — the worker's atomic claim (``UPDATE ... WHERE
    status_code='pending'``) admits exactly one runner. This is also the delivery half of
    the reconciler contract: a row a dead worker left ``running`` is reset to ``pending``
    and picked up here on the next pass. No-op when no dispatcher is wired (``dispatch``
    stays unset in the unit tier, which drives worker bodies directly)."""
    for row in execute(
        "SELECT id, rwb_job_type, correlation_id FROM rwb_job "
        "WHERE status_code = 'pending'",
        {}, connection="WORKBENCH",
    ):
        token = log_context.bind(correlation_id=row["correlation_id"],
                                 rwb_job_id=str(row["id"]),
                                 rwb_job_type=row["rwb_job_type"])
        try:
            dispatch.dispatch(rwb_job_id=row["id"], rwb_job_type=row["rwb_job_type"])
            logger.debug("dispatched pending rwb_job")
        finally:
            log_context.clear(token)


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
        _dispatch_pending()
    except Exception:
        logger.exception("poll_once: dispatch_pending failed")
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

    setup_logging("poller")

    # Discover the job actors and wire the Dramatiq dispatch seam so _dispatch_pending
    # can wake a worker for the heads this poller enqueues. Deferred import keeps dramatiq
    # out of the request/test import path (only this startup path pulls it in), matching
    # app.main's lifespan and app.workers.entrypoint.
    from app.workers import loader  # noqa: PLC0415
    loader.bootstrap()
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
