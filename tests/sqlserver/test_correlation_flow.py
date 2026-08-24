"""Correlation-id flow into the worker tier (issue #28).

A request-path enqueue happens under the request middleware's bound context
(simulated here with a plain ``log_context.bind``); the worker's ``run_job``
re-binds from the claimed ``rwb_job`` row; ``irp_job`` rows written during the
body inherit the id from that bind — including ``SUBMISSION FAILED`` rows. The
full request → worker → poller → chained-worker assertion lives in
tests/unit/test_job_chaining.py.
"""

from __future__ import annotations

import logging

from app import log_context
from app.logging_setup import ContextFilter
from app.services import edm_service
from app.workers import entity_jobs
from db import execute


def _build_and_sync(drive, actor, correlation_id: str) -> str:
    token = log_context.bind(correlation_id=correlation_id)
    try:
        result = edm_service.import_edm(
            name="E1", source_file_path=str(drive / "edm1.bak"),
            actor_id=actor,
        )
    finally:
        log_context.clear(token)
    return result.entity_id


def test_irp_job_inherits_rwb_job_correlation(workbench_db, fake_irp, drive):
    _build_and_sync(drive, workbench_db.user_a, "req-123")
    entity_jobs.run_pending()  # worker claims, binds from the row, submits
    rwb = execute("SELECT correlation_id FROM rwb_job WHERE rwb_job_type='upload_edm'",
                  {}, connection="WORKBENCH")
    irp = execute("SELECT correlation_id FROM irp_job WHERE irp_job_type='import_edm'",
                  {}, connection="WORKBENCH")
    assert rwb[0]["correlation_id"] == "req-123"
    assert irp[0]["correlation_id"] == "req-123"


def test_submission_failure_row_is_stamped(workbench_db, fake_irp, drive):
    _build_and_sync(drive, workbench_db.user_a, "req-456")
    fake_irp.raise_on_submit = True  # submit never reaches Risk Modeler
    entity_jobs.run_pending()
    rows = execute("SELECT status, correlation_id FROM irp_job", {},
                   connection="WORKBENCH")
    assert rows[0]["status"] == "SUBMISSION FAILED"
    assert rows[0]["correlation_id"] == "req-456"


class _Capture(logging.Handler):
    """Collects records enriched the way the real root handler enriches them."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []
        self.addFilter(ContextFilter("worker"))

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_run_job_lifecycle_records_carry_context(workbench_db, fake_irp, drive):
    _build_and_sync(drive, workbench_db.user_a, "req-789")
    cap = _Capture()
    runtime_logger = logging.getLogger("app.workers.runtime")
    saved_level = runtime_logger.level
    runtime_logger.addHandler(cap)
    runtime_logger.setLevel(logging.INFO)
    try:
        entity_jobs.run_pending()
    finally:
        runtime_logger.removeHandler(cap)
        runtime_logger.setLevel(saved_level)

    claimed = [r for r in cap.records if "claimed" in r.getMessage()]
    finished = [r for r in cap.records if r.getMessage() == "rwb_job succeeded"]
    assert claimed and finished
    assert claimed[0].correlation_id == "req-789"
    assert claimed[0].rwb_job_type == "upload_edm"
    assert claimed[0].rwb_job_id
    assert finished[0].correlation_id == "req-789"
    assert finished[0].duration_ms >= 0
