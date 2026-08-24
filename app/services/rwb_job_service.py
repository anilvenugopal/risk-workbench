"""The Article-10 work queue — ``rwb_job`` is the queue of record.

Three primitives drive every app-side worker: an **idempotent enqueue** (dedup on
``UNIQUE(requestor_type, requestor_id, rwb_job_type)`` — the A21 backbone), an
**atomic claim** (``UPDATE ... WHERE status_code='pending'`` — a lost race is a
rowcount-0 no-op, never a double-execute), and an in-place **complete**. The
poller's **reconciler** (``reconcile_stale_rwb_jobs``) reclaims rows whose worker
died mid-flight — its logic lives here as queue maintenance; the poller only
invokes it each pass.

Portability: app-side
UUIDs bound as ``str``, app-supplied UTC timestamps, JSON columns serialized with
``json.dumps``, and no dialect-only SQL — the same statements run on the SQLite
unit tier and SQL Server.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app import log_context
from app.services._common import _json, _utcnow
from db import execute_command, execute_one, get_connection, is_unique_violation

_INSERT_IF_ABSENT = """
    INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type,
        status_code, input_data, attempt_count, correlation_id, inserted_at,
        updated_at, inserted_by, updated_by)
    SELECT :id, :rt, :rid, :jt, 'pending', :input, 0, :cid, :now, :now, :by, :by
    WHERE NOT EXISTS (
        SELECT 1 FROM rwb_job
        WHERE requestor_type = :rt AND requestor_id = :rid AND rwb_job_type = :jt
    )
"""


def _insert_head(params: dict, conn) -> bool:
    """Run ``_INSERT_IF_ABSENT``, absorbing a UNIQUE-key violation as a dedup hit.
    Returns ``True`` iff a row was inserted. The ``NOT EXISTS`` pre-check is not atomic
    under READ COMMITTED (SQL Server's default): a genuine race lets both writers pass
    it, and the loser violates ``UNIQUE(requestor_type, requestor_id, rwb_job_type)``.
    Catching that violation makes the UNIQUE key — not the pre-check — the real dedup
    guarantee. On a caller-owned ``conn`` the insert runs in a SAVEPOINT so a caught
    violation leaves the outer transaction intact; the request path (``conn is None``)
    uses ``execute_command``'s own transaction, which rolls itself back cleanly."""
    try:
        if conn is not None:
            with conn.begin_nested():
                rows = conn.execute(text(_INSERT_IF_ABSENT), params).rowcount
        else:
            rows = execute_command(_INSERT_IF_ABSENT, params, connection="WORKBENCH")
    except Exception as exc:  # noqa: BLE001 — a UNIQUE race is a dedup hit, not a failure
        if is_unique_violation(exc):
            return False
        raise
    return rows == 1


def enqueue_rwb_job(
    *, requestor_type: str, requestor_id: Any, rwb_job_type: str,
    input_data: dict | None = None, actor_id: Any | None = None,
    correlation_id: str | None = None, conn=None,
) -> str | None:
    """Idempotent insert on ``UNIQUE(requestor_type, requestor_id, rwb_job_type)``
    (FR-043 / SC-014). Returns the new job id, or ``None`` if a matching row already
    exists (dedup hit — pre-check or a lost UNIQUE-key race) — a re-poll / redelivery /
    reconciler re-enqueue is a no-op. Never resurrects a terminal row (that is the
    fan-in idempotency backbone the poller/workers rely on); the request path uses
    ``ensure_pending_rwb_job``.

    ``correlation_id`` defaults to the bound log context's — the request middleware
    (web tier) or the per-job bind (poller/worker chaining) has stamped it, so call
    sites don't pass it explicitly (issue #28).

    ``conn`` lets a caller enqueue the chained tail in its own open transaction."""
    job_id = str(uuid.uuid4())
    params = {
        "id": job_id, "rt": requestor_type, "rid": str(requestor_id),
        "jt": rwb_job_type, "input": _json(input_data), "now": _utcnow(),
        "cid": correlation_id or log_context.correlation_id(),
        "by": (str(actor_id) if actor_id is not None else None),
    }
    return job_id if _insert_head(params, conn) else None


def ensure_pending_rwb_job(
    *, requestor_type: str, requestor_id: Any, rwb_job_type: str,
    input_data: dict | None = None, actor_id: Any | None = None,
    correlation_id: str | None = None,
) -> str | None:
    """Request-path (re)enqueue for retry / re-sync (FR-044/FR-045). Insert a fresh
    ``pending`` head if none exists; if the existing head is **terminal**
    (``succeeded``/``failed``) reset it to ``pending`` for a new attempt; if it is
    already ``pending``/``running`` skip it (return ``None``). This is the deliberate
    counterpart to ``enqueue_rwb_job`` — that one never revives a terminal row so a
    mechanical re-poll cannot; this one does, because an analyst asked for it.

    A revived row is re-stamped with the *retrying* request's correlation id
    (default: the bound log context's) — a retry is a new causal chain."""
    now = _utcnow()
    correlation_id = correlation_id or log_context.correlation_id()
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            row = conn.execute(text(
                "SELECT id, status_code FROM rwb_job "
                "WHERE requestor_type = :rt AND requestor_id = :rid "
                "AND rwb_job_type = :jt"
            ), {"rt": requestor_type, "rid": str(requestor_id),
                "jt": rwb_job_type}).mappings().first()
            if row is None:
                job_id = str(uuid.uuid4())
                # A concurrent writer may insert the head between our SELECT and this
                # INSERT; _insert_head absorbs the UNIQUE-key race (→ False) as "already
                # in flight", the same outcome as the pending/running skip below.
                if _insert_head({
                    "id": job_id, "rt": requestor_type, "rid": str(requestor_id),
                    "jt": rwb_job_type, "input": _json(input_data), "now": now,
                    "cid": correlation_id,
                    "by": (str(actor_id) if actor_id is not None else None)}, conn):
                    return job_id
                return None
            if row["status_code"] in ("pending", "running"):
                return None  # already in flight — skip
            conn.execute(text(
                """
                UPDATE rwb_job
                SET status_code = 'pending', claimed_by = NULL, output_data = NULL,
                    error_detail = NULL, completed_at = NULL, submitted_at = NULL,
                    input_data = :input, attempt_count = attempt_count + 1,
                    correlation_id = :cid, updated_at = :now, updated_by = :by
                WHERE id = :id
                """
            ), {"input": _json(input_data), "now": now, "cid": correlation_id,
                "by": (str(actor_id) if actor_id is not None else None),
                "id": str(row["id"])})
            return str(row["id"])


def claim_rwb_job(*, rwb_job_id: Any, worker_id: str) -> bool:
    """Atomic claim: flip ``pending`` → ``running`` for exactly one worker. Returns
    ``False`` when rowcount is 0 (already claimed by someone else — exit cleanly)."""
    now = _utcnow()
    rows = execute_command(
        """
        UPDATE rwb_job
        SET status_code = 'running', claimed_by = :wid, submitted_at = :now,
            updated_at = :now
        WHERE id = :id AND status_code = 'pending'
        """,
        {"wid": worker_id, "now": now, "id": str(rwb_job_id)},
        connection="WORKBENCH",
    )
    return rows == 1


def get_rwb_job(*, rwb_job_id: Any) -> dict | None:
    """Read one queue row (post-claim, the worker runtime binds its log context
    from this — ``claim_rwb_job`` deliberately keeps its bool contract). Returns
    ``None`` when the id is unknown."""
    return execute_one(
        """
        SELECT id, requestor_type, requestor_id, rwb_job_type, status_code,
               attempt_count, correlation_id
        FROM rwb_job WHERE id = :id
        """,
        {"id": str(rwb_job_id)},
        connection="WORKBENCH",
    )


def complete_rwb_job(
    *, rwb_job_id: Any, status: str, output_data: dict | None = None,
    error_detail: str | None = None,
) -> None:
    """In-place completion (Article 4): set ``succeeded``/``failed`` + payload +
    ``completed_at``. Chained tail rows are enqueued by the caller in the same
    worker-owned transaction (contracts/data-access.md)."""
    now = _utcnow()
    execute_command(
        """
        UPDATE rwb_job
        SET status_code = :st, output_data = :out, error_detail = :err,
            completed_at = :now, updated_at = :now
        WHERE id = :id
        """,
        {"st": status, "out": _json(output_data), "err": error_detail,
         "now": now, "id": str(rwb_job_id)},
        connection="WORKBENCH",
    )


def reconcile_stale_rwb_jobs(*, stale_secs: int, now: datetime | None = None) -> int:
    """Reclaim rows a dead worker left ``running`` — the Article-10 reconciler,
    invoked by the poller each pass. A row is stale when its heartbeat is older
    than ``stale_secs`` (or it never heartbeated). Reset to ``pending`` so the
    queue re-dispatches it. Returns the number reclaimed."""
    now = now or _utcnow()
    cutoff = now - timedelta(seconds=stale_secs)
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            result = conn.execute(text(
                """
                UPDATE rwb_job
                SET status_code = 'pending', claimed_by = NULL, updated_at = :now
                WHERE status_code = 'running'
                  AND id IN (
                    SELECT rj.id FROM rwb_job rj
                    LEFT JOIN rwb_job_heartbeat hb ON hb.rwb_job_id = rj.id
                    WHERE rj.status_code = 'running'
                      AND (hb.heartbeat_at IS NULL OR hb.heartbeat_at < :cutoff)
                  )
                """
            ), {"now": now, "cutoff": cutoff})
            return result.rowcount


__all__ = [
    "enqueue_rwb_job",
    "ensure_pending_rwb_job",
    "claim_rwb_job",
    "get_rwb_job",
    "complete_rwb_job",
    "reconcile_stale_rwb_jobs",
]
