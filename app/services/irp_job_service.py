"""The Article-11 bridge — records an async Risk Modeler op as an ``irp_job``.

Written by the **worker** at submit time (``record_submitted_irp_job`` / on failure
``record_submission_failure``) and later updated in place by the **poller** (the
status-mirror transitions live alongside the poller, worker-poller.md §3). The web
layer never writes here.

Every function is a thin per-table statement that optionally accepts an explicit
``conn`` so the caller can span *both* tables in one transaction (a worker completes
its ``rwb_job`` **and** records the ``irp_job`` atomically — contracts/data-access.md).
With no ``conn`` it opens its own transaction. Portable across SQLite / SQL Server.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app import log_context
from app.services._common import _json, _txn, _utcnow
from db import execute


def _insert_irp_job(conn, *, job_id: str, requested_from_submission_id,
                    irp_edm_id, irp_rdm_id,
                    irp_job_type: str, irp_id: str | None, status: str,
                    payload: dict | None, response: dict | None,
                    attempt_count: int, actor_id, now: datetime) -> None:
    conn.execute(text(
        """
        INSERT INTO irp_job (id, requested_from_submission_id, irp_edm_id,
            irp_rdm_id, irp_job_type,
            irp_id, status, correlation_id, last_submission_payload,
            last_submission_response, submission_attempt_count, submitted_at,
            inserted_at, updated_at, inserted_by, updated_by)
        VALUES (:id, :submission, :edm, :rdm, :jt, :irp_id, :status, :cid, :payload,
            :response, :attempts, :now, :now, :now, :by, :by)
        """
    ), {
        "id": job_id,
        "submission": (str(requested_from_submission_id)
                       if requested_from_submission_id is not None else None),
        "edm": (str(irp_edm_id) if irp_edm_id is not None else None),
        "rdm": (str(irp_rdm_id) if irp_rdm_id is not None else None),
        "jt": irp_job_type,
        "irp_id": irp_id,
        "status": status,
        # Inherited from the worker's bound per-job context (issue #28) — both
        # writers (submit + submission-failure) run inside run_job's bind.
        "cid": log_context.correlation_id(),
        "payload": _json(payload),
        "response": _json(response),
        "attempts": attempt_count,
        "now": now,
        "by": (str(actor_id) if actor_id is not None else None),
    })


def record_submitted_irp_job(
    *, irp_job_type: str, requested_from_submission_id: Any | None = None,
    irp_edm_id: Any | None = None, irp_rdm_id: Any | None = None,
    irp_id: str, resource_uri: str | None = None,
    payload: dict | None = None, response: dict | None = None,
    actor_id: Any | None = None, conn=None,
) -> str:
    """Worker-side: write the ``irp_job`` (status ``QUEUED``, ``irp_id`` set) plus
    any ``irp_job_resource`` (the ``resource_uri`` captured at submit — the
    completion response omits it, R1). Returns the new ``irp_job`` id."""
    job_id = str(uuid.uuid4())
    now = _utcnow()
    with _txn(conn) as c:
        _insert_irp_job(
            c, job_id=job_id,
            requested_from_submission_id=requested_from_submission_id,
            irp_edm_id=irp_edm_id,
            irp_rdm_id=irp_rdm_id, irp_job_type=irp_job_type, irp_id=irp_id,
            status="QUEUED", payload=payload, response=response,
            attempt_count=0, actor_id=actor_id, now=now)
        if resource_uri is not None:
            c.execute(text(
                "INSERT INTO irp_job_resource (id, irp_job_id, resource_type, "
                "resource_uri, inserted_at) "
                "VALUES (:id, :jid, 'portfolio', :uri, :now)"
            ), {"id": str(uuid.uuid4()), "jid": job_id, "uri": resource_uri,
                "now": now})
    return job_id


# Terminal irp_job.status values (data-model §2). SUBMISSION FAILED is terminal
# too — owned by the poller's submission_retry batch, never the status tracker.
TERMINAL = frozenset({"FINISHED", "FAILED", "CANCELLED", "SUBMISSION FAILED"})


def list_non_terminal() -> list[dict]:
    """Poller-side: every ``irp_job`` still worth a single-status check — non-terminal
    and actually submitted (``irp_id`` present). Batched by the caller per type."""
    params = {f"t{i}": s for i, s in enumerate(sorted(TERMINAL))}
    placeholders = ", ".join(f":{k}" for k in params)
    rows = execute(
        f"""
        SELECT id, irp_id, irp_job_type, irp_edm_id, irp_rdm_id,
               requested_from_submission_id,
               status, correlation_id, submitted_at
        FROM irp_job
        WHERE irp_id IS NOT NULL
          AND status NOT IN ({placeholders})
        ORDER BY irp_job_type
        """,
        params, connection="WORKBENCH",
    )
    return [dict(r) for r in rows]


def update_tracking(conn, *, irp_job_id: Any, status: str,
                    result: dict | None = None) -> None:
    """Poller-side: mirror the Risk Modeler status in place (Article 4) and stamp
    ``last_tracked_at``; on a terminal status also stamp ``completed_at`` and store
    the completion body. Runs inside the poller's transaction (accepts ``conn``)."""
    now = _utcnow()
    terminal = status in TERMINAL
    conn.execute(text(
        """
        UPDATE irp_job
        SET status = :s, last_tracked_at = :now, updated_at = :now,
            completed_at = CASE WHEN :terminal = 1 THEN :now ELSE completed_at END,
            last_completion_result = CASE WHEN :terminal = 1 THEN :result
                                          ELSE last_completion_result END
        WHERE id = :id
        """
    ), {"s": status, "now": now, "terminal": (1 if terminal else 0),
        "result": _json(result), "id": str(irp_job_id)})


def record_submission_failure(
    *, irp_job_type: str, requested_from_submission_id: Any | None = None,
    irp_edm_id: Any | None = None, irp_rdm_id: Any | None = None,
    payload: dict | None = None, actor_id: Any | None = None, conn=None,
) -> str:
    """Worker-side: the submit never reached Risk Modeler — write the ``irp_job``
    as terminal ``SUBMISSION FAILED`` with ``irp_id=NULL`` (distinct from an RM-side
    ``FAILED``, FR-029). The poller's ``submission_retry`` batch re-attempts it.

    NOTE (review item 5): this inserts a **new** row per failure, so an entity that
    fails to submit N times accumulates N ``SUBMISSION FAILED`` rows for the same
    (entity, type). The US6 ``submission_retry`` batch (descoped this iteration — the
    poller stub is a no-op) MUST therefore select/retry per **entity**, not per row —
    a per-row scan would re-submit the same head once per accumulated failure. Dedup
    is left to that consumer (not enforced here) since nothing reads these rows yet."""
    job_id = str(uuid.uuid4())
    now = _utcnow()
    with _txn(conn) as c:
        _insert_irp_job(
            c, job_id=job_id,
            requested_from_submission_id=requested_from_submission_id,
            irp_edm_id=irp_edm_id,
            irp_rdm_id=irp_rdm_id, irp_job_type=irp_job_type, irp_id=None,
            status="SUBMISSION FAILED", payload=payload, response=None,
            attempt_count=1, actor_id=actor_id, now=now)
    return job_id


__all__ = [
    "record_submitted_irp_job", "record_submission_failure",
    "TERMINAL", "list_non_terminal", "update_tracking",
]
