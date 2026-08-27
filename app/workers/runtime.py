"""Worker runtime helpers — the Article-10 lifecycle every rwb_job actor shares.

An actor's shape is always: **claim** the row atomically → run under a **heartbeat**
daemon thread → **complete** in place. This module owns that scaffolding.

The heartbeat upsert keeps exactly one ``rwb_job_heartbeat`` row per job; the
poller's reconciler (``rwb_job_service.reconcile_stale_rwb_jobs``) reads it to
reclaim a dead worker's row.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from dramatiq.middleware import TimeLimitExceeded
from sqlalchemy import text

from app import log_context
from app.config import settings
from app.services import rwb_job_service
from app.services._common import _utcnow
from db import execute, get_connection

logger = logging.getLogger(__name__)


def worker_id() -> str:
    """The heartbeat identity for a job actor — identifies the OS process, not
    the job or the module it happens to be defined in (neither is the
    worker's own business; ``rwb_job_type`` already names the job on every
    log line and DB row). ``hostname:pid`` is unique among currently-running
    processes, which is what matters for "is this the process to kill" and
    "which of N processes on this queue handled this" (``RWB_WORKER_PROCESSES``
    > 1) — without the PID, every process on a host produced the identical
    string. Sliced to 128 chars to match ``rwb_job.claimed_by`` and
    ``rwb_job_heartbeat.worker_id``'s ``NVARCHAR(128)`` width: a longer value
    would otherwise raise "String or binary data would be truncated" on
    write and fail the claim, rather than merely displaying truncated."""
    raw = f"{socket.gethostname()}:{os.getpid()}"
    return raw[:128]


# ── the body → rwb_job outcome contract (worker-poller.md §1) ────────────────────

@dataclass
class JobResult:
    """The outcome a worker body reports back to ``run_job`` so the ``rwb_job`` row
    reflects what actually happened (contract §1: *on success → succeeded; on failure
    → failed + error_detail*).

    A body signals a real failure **without raising** — e.g. an IRP submit that never
    reached Risk Modeler is recorded as a ``SUBMISSION FAILED`` ``irp_job`` for the
    poller's retry batch, yet the ``rwb_job`` itself did NOT accomplish its unit of work
    and must be ``failed`` (not silently ``succeeded``). Use ``JobResult.fail`` for that.
    Idempotent no-ops (entity vanished, already advanced) are ``JobResult.ok`` — the
    body correctly did nothing. A body may still return a plain ``dict``/``None`` (stub
    bodies, backfill): ``run_job`` treats that as ``succeeded`` for backward-compat."""
    status: str                                  # 'succeeded' | 'failed'
    output: dict = field(default_factory=dict)
    error_detail: str | None = None

    @classmethod
    def ok(cls, **output: Any) -> "JobResult":
        return cls(status="succeeded", output=output)

    @classmethod
    def fail(cls, error_detail: str, **output: Any) -> "JobResult":
        return cls(status="failed", output=output, error_detail=error_detail)


# ── heartbeat ────────────────────────────────────────────────────────────────

def upsert_heartbeat(*, rwb_job_id: Any, worker_id: str,
                     now: datetime | None = None) -> None:
    """Upsert the single heartbeat row for a job (one row per job). Portable
    UPDATE-then-insert-if-absent — runs on SQLite and SQL Server alike."""
    now = now or _utcnow()
    jid = str(rwb_job_id)
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            updated = conn.execute(text(
                "UPDATE rwb_job_heartbeat SET worker_id = :wid, heartbeat_at = :now "
                "WHERE rwb_job_id = :id"
            ), {"wid": worker_id, "now": now, "id": jid}).rowcount
            if updated == 0:
                conn.execute(text(
                    "INSERT INTO rwb_job_heartbeat (rwb_job_id, worker_id, heartbeat_at) "
                    "SELECT :id, :wid, :now WHERE NOT EXISTS ("
                    "  SELECT 1 FROM rwb_job_heartbeat WHERE rwb_job_id = :id)"
                ), {"id": jid, "wid": worker_id, "now": now})


class _Heartbeat:
    """Daemon thread that upserts the job's heartbeat every interval until stopped."""

    def __init__(self, *, rwb_job_id: Any, worker_id: str, interval_secs: int,
                 correlation_id: str | None = None) -> None:
        self._rwb_job_id = rwb_job_id
        self._worker_id = worker_id
        self._correlation_id = correlation_id
        self._interval = max(1, interval_secs)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        # A new thread starts with an empty contextvars context — re-bind here so
        # the failure line below carries the job's ids. No clear needed: the
        # context dies with the thread.
        log_context.bind(rwb_job_id=str(self._rwb_job_id),
                         worker_id=self._worker_id,
                         correlation_id=self._correlation_id)
        # __enter__ already beat once on the caller's thread (t=0), so wait a full
        # interval before the first daemon-thread beat (cadence: t=interval, 2·interval, …).
        # A job that finishes within one interval never opens a connection on this
        # daemon thread — which matters under the unit tier's per-thread SQLite pool,
        # where a connection opened here can only be closed from here, not by the
        # main thread's engine.dispose() (it would otherwise leak until GC).
        while not self._stop.wait(self._interval):
            try:
                upsert_heartbeat(rwb_job_id=self._rwb_job_id, worker_id=self._worker_id)
            except Exception:  # noqa: BLE001 — a heartbeat blip must not kill the job
                logger.exception("heartbeat upsert failed for %s", self._rwb_job_id)

    def __enter__(self) -> _Heartbeat:
        # Beat once immediately so a stale row can't trip the reconciler at t=0.
        upsert_heartbeat(rwb_job_id=self._rwb_job_id, worker_id=self._worker_id)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval)


# ── the shared claim → heartbeat → complete lifecycle ───────────────────────────

def run_job(*, rwb_job_id: Any, worker_id: str,
            body: Callable[[], "JobResult | dict | None"]) -> bool:
    """Claim the row; if won, run ``body`` under a heartbeat and complete it in place.
    The body's outcome drives the terminal status (contract §1):

      • ``JobResult`` → its own ``status``/``output``/``error_detail`` (a body reports a
        handled failure as ``JobResult.fail`` — no raise needed);
      • a plain ``dict``/``None`` → ``succeeded`` with that dict as output (stub/backfill
        bodies, backward-compat);
      • an **unhandled** exception → ``failed`` with the exception text.

    Returns ``False`` without running when the row was already claimed."""
    if not rwb_job_service.claim_rwb_job(rwb_job_id=rwb_job_id, worker_id=worker_id):
        logger.debug("rwb_job %s already claimed — skipping", rwb_job_id)
        return False
    # claim keeps its bool contract; a separate read supplies the log context
    # (correlation_id inherited from the enqueuing request/chain — issue #28).
    row = rwb_job_service.get_rwb_job(rwb_job_id=rwb_job_id) or {}
    token = log_context.bind(
        correlation_id=row.get("correlation_id"), rwb_job_id=str(rwb_job_id),
        rwb_job_type=row.get("rwb_job_type"), worker_id=worker_id)
    started = time.monotonic()

    def _finished(status: str) -> None:
        log_context.add(duration_ms=round((time.monotonic() - started) * 1000, 1))
        logger.info("rwb_job %s", status)

    try:
        logger.info("rwb_job claimed (attempt %s)", row.get("attempt_count"))
        with _Heartbeat(rwb_job_id=rwb_job_id, worker_id=worker_id,
                        interval_secs=settings.rwb_heartbeat_interval_secs,
                        correlation_id=row.get("correlation_id")):
            try:
                result = body()
            except TimeLimitExceeded:
                # Dramatiq killed the actor thread at its time limit. Mark the
                # row failed HERE: TimeLimitExceeded is a BaseException the
                # generic handler below never sees, and a row left 'running'
                # would be reset to pending by the reconciler and re-dispatched
                # into the same kill, forever. Re-raise so dramatiq finishes
                # its interrupt handling.
                logger.error("rwb_job %s exceeded the actor time limit",
                             rwb_job_id)
                rwb_job_service.complete_rwb_job(
                    rwb_job_id=rwb_job_id, status="failed",
                    error_detail="the run exceeded the worker time limit")
                _finished("failed")
                raise
            except Exception as exc:  # noqa: BLE001 — record failure, never crash the worker
                logger.exception("rwb_job %s body failed", rwb_job_id)
                rwb_job_service.complete_rwb_job(
                    rwb_job_id=rwb_job_id, status="failed", error_detail=str(exc))
                _finished("failed")
                return True
            if isinstance(result, JobResult):
                if result.status == "failed":
                    logger.warning("rwb_job %s failed: %s",
                                   rwb_job_id, result.error_detail)
                rwb_job_service.complete_rwb_job(
                    rwb_job_id=rwb_job_id, status=result.status,
                    output_data=result.output, error_detail=result.error_detail)
                _finished(result.status)
            else:
                rwb_job_service.complete_rwb_job(
                    rwb_job_id=rwb_job_id, status="succeeded", output_data=result or {})
                _finished("succeeded")
        return True
    finally:
        log_context.clear(token)


# ── synchronous drain (unit tier) ───────────────────────────────────────────────

JobBodies = dict[str, Callable[[Any], "JobResult | dict | None"]]


def run_one(bodies: JobBodies, *, rwb_job_id: Any, rwb_job_type: str,
            worker_id: str = "worker") -> bool:
    """Claim + run a single ``rwb_job`` through the body ``bodies`` maps its type to.
    Returns ``run_job``'s result (``False`` if the row was already claimed, or
    ``bodies`` has no body for the type)."""
    body = bodies.get(rwb_job_type)
    if body is None:
        logger.debug("no body for rwb_job_type %s — skipping", rwb_job_type)
        return False
    return run_job(rwb_job_id=rwb_job_id, worker_id=worker_id,
                   body=lambda: body(rwb_job_id))


def run_pending(bodies: JobBodies, *, worker_id: str = "worker") -> int:
    """Claim + run every currently-``pending`` ``rwb_job`` once, skipping the types
    ``bodies`` has no body for. Snapshot-based (rows a body enqueues are picked up on
    the next call), so tests advance the queue by calling this after each poller pass.
    Returns the number of rows run."""
    rows = execute(
        "SELECT id, rwb_job_type FROM rwb_job WHERE status_code = 'pending' "
        "ORDER BY inserted_at, id",
        {}, connection="WORKBENCH",
    )
    count = 0
    for row in rows:
        if run_one(bodies, rwb_job_id=row["id"], rwb_job_type=row["rwb_job_type"],
                   worker_id=worker_id):
            count += 1
    return count


__all__ = [
    "JobBodies",
    "JobResult",
    "run_job",
    "run_one",
    "run_pending",
    "worker_id",
]
