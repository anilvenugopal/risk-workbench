"""Worker runtime helpers — the Article-10 lifecycle every rwb_job actor shares.

An actor's shape is always: **claim** the row atomically → run under a **heartbeat**
daemon thread → **complete** in place. This module owns that scaffolding plus the
**stub↔real worker-body switch** (FR-048): the same ``rwb_job_type`` and the whole
chaining/fan-in shape are identical in either mode; only the body differs (a stub
heartbeats and marks succeeded without calling Risk Modeler), so the package UI can
be built ahead of the real gateway wiring.

The heartbeat upsert keeps exactly one ``rwb_job_heartbeat`` row per job; the
poller's reconciler (``rwb_job_service.reconcile_stale_rwb_jobs``) reads it to
reclaim a dead worker's row.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import text

from app.config import settings
from app.services import rwb_job_service
from db import get_connection

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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

    def __init__(self, *, rwb_job_id: Any, worker_id: str, interval_secs: int) -> None:
        self._rwb_job_id = rwb_job_id
        self._worker_id = worker_id
        self._interval = max(1, interval_secs)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                upsert_heartbeat(rwb_job_id=self._rwb_job_id, worker_id=self._worker_id)
            except Exception:  # noqa: BLE001 — a heartbeat blip must not kill the job
                logger.exception("heartbeat upsert failed for %s", self._rwb_job_id)
            self._stop.wait(self._interval)

    def __enter__(self) -> _Heartbeat:
        # Beat once immediately so a stale row can't trip the reconciler at t=0.
        upsert_heartbeat(rwb_job_id=self._rwb_job_id, worker_id=self._worker_id)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval)


# ── worker-body switch (FR-048) ────────────────────────────────────────────────

def is_stub_mode() -> bool:
    """True when the deployment runs stub bodies (build the UI ahead of Risk
    Modeler wiring). Real bodies call the ``irp_gateway``; stubs do not."""
    return settings.rwb_worker_mode == "stub"


def stub_body(*, sleep_secs: float = 0.0) -> dict:
    """A no-op body a stub actor can run: optionally pause, then report success —
    the chaining/fan-in shape is unchanged, only the Risk Modeler call is skipped."""
    if sleep_secs:
        time.sleep(sleep_secs)
    return {"stub": True}


# ── the shared claim → heartbeat → complete lifecycle ───────────────────────────

def run_job(*, rwb_job_id: Any, worker_id: str,
            body: Callable[[], dict | None]) -> bool:
    """Claim the row; if won, run ``body`` under a heartbeat and complete it
    (``succeeded`` with the body's dict output, or ``failed`` with the error).
    Returns ``False`` without running when the row was already claimed."""
    if not rwb_job_service.claim_rwb_job(rwb_job_id=rwb_job_id, worker_id=worker_id):
        logger.debug("rwb_job %s already claimed — skipping", rwb_job_id)
        return False
    with _Heartbeat(rwb_job_id=rwb_job_id, worker_id=worker_id,
                    interval_secs=settings.rwb_heartbeat_interval_secs):
        try:
            output = body() or {}
        except Exception as exc:  # noqa: BLE001 — record failure, never crash the worker
            logger.exception("rwb_job %s body failed", rwb_job_id)
            rwb_job_service.complete_rwb_job(
                rwb_job_id=rwb_job_id, status="failed", error_detail=str(exc))
            return True
        rwb_job_service.complete_rwb_job(
            rwb_job_id=rwb_job_id, status="succeeded", output_data=output)
    return True


__all__ = [
    "upsert_heartbeat",
    "is_stub_mode",
    "stub_body",
    "run_job",
]
