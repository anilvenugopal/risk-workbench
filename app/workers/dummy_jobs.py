"""Dummy Dramatiq actors for manually exercising the rwb_job queue (CR-004).

Not part of the product. These exist so a developer can submit a real,
trackable, cancellable, failable rwb_job without needing a real EDM, RDM, or
IRP connection — useful for testing per-queue isolation, cancel, drain, and
worker restart by hand. See docs/WORKER_ARCHITECTURE.md.

Two job types, each its own queue (dummy_wait, dummy_fail) so isolation
between two independently-running dummy queues can be observed directly,
the same way any two real job types are isolated from each other.

Every log line carries the caller-supplied label (input_data['label']) and
this process's PID, so when several dummy jobs are running at once — e.g.
testing cancel-while-pending against three dummy_wait submissions — the log
tells them apart without cross-referencing the rwb_job_id UUID by hand.

Submit either one with `python -m app.workers.dummy_submit` (see that
module) rather than by hand — it fills in the input_data shape these bodies
expect.
"""

from __future__ import annotations

import logging
import os
import time

from app.services import rwb_job_service
from app.workers import broker, runtime
from app.workers.queues import rwb_actor

logger = logging.getLogger(__name__)
_ = broker.redis_broker


def _dummy_wait_body(rwb_job_id) -> runtime.JobResult:
    """Sleeps for input_data['seconds'] (default 30), then succeeds.

    Long enough to manually cancel while pending, or kill the worker process
    while running, before it completes."""
    ctx = rwb_job_service.load_input_data(rwb_job_id)
    seconds = ctx.get("seconds", 30)
    label = ctx.get("label", "(no label)")
    pid = os.getpid()
    logger.info("dummy_wait [%s] pid=%s sleeping %ss (rwb_job_id=%s)",
                label, pid, seconds, rwb_job_id)
    time.sleep(seconds)
    logger.info("dummy_wait [%s] pid=%s done", label, pid)
    return runtime.JobResult.ok(label=label, pid=pid, slept_seconds=seconds)


def _dummy_fail_body(rwb_job_id) -> runtime.JobResult:
    """Always fails. input_data['message'] becomes error_detail, so a
    resubmit's before/after error_detail is easy to tell apart by eye."""
    ctx = rwb_job_service.load_input_data(rwb_job_id)
    message = ctx.get("message", "dummy_fail: intentional failure")
    label = ctx.get("label", "(no label)")
    pid = os.getpid()
    logger.info("dummy_fail [%s] pid=%s failing on purpose (rwb_job_id=%s): %s",
                label, pid, rwb_job_id, message)
    return runtime.JobResult.fail(f"[{label}] pid={pid}: {message}")


@rwb_actor(max_retries=0)
def dummy_wait(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _dummy_wait_body(rwb_job_id))


@rwb_actor(max_retries=0)
def dummy_fail(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _dummy_fail_body(rwb_job_id))


__all__ = ["dummy_wait", "dummy_fail"]
