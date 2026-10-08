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

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app import log_context
from app.config import settings
from app.services._common import _in_clause, _json, _utcnow
from db import execute, execute_command, execute_one, get_connection, is_unique_violation, row_limit

logger = logging.getLogger(__name__)

_INSERT_IF_ABSENT = """
    INSERT INTO rwb_job (id, requestor_type, requestor_id, link_type, link_id,
        context_type, context_id, rwb_job_type, status_code, input_data,
        attempt_count, correlation_id, inserted_at, updated_at, inserted_by,
        updated_by)
    SELECT :id, :rt, :rid, :lt, :lid, :ct, :ctxid, :jt, 'pending', :input, 0,
        :cid, :now, :now, :by, :by
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


def analysis_link(edm_id: Any, rdm_id: Any, submission_id: Any) -> tuple[str, Any]:
    """The ``rwb_job`` link for a job about one analysis: its owner — the EDM,
    else the RDM (broker analyses), else the submission (group and imported
    analyses)."""
    if edm_id:
        return "edm", edm_id
    if rdm_id:
        return "rdm", rdm_id
    return "submission", submission_id


def enqueue_rwb_job(
    *, requestor_type: str, requestor_id: Any, rwb_job_type: str,
    link_type: str, link_id: Any | None, context_type: str | None,
    context_id: Any | None, input_data: dict | None = None,
    actor_id: Any | None = None, correlation_id: str | None = None, conn=None,
) -> str | None:
    """Idempotent insert on ``UNIQUE(requestor_type, requestor_id, rwb_job_type)``
    (FR-043 / SC-014). Returns the new job id, or ``None`` if a matching row already
    exists (dedup hit — pre-check or a lost UNIQUE-key race) — a re-poll / redelivery /
    reconciler re-enqueue is a no-op. Never resurrects a terminal row (that is the
    fan-in idempotency backbone the poller/workers rely on); the request path uses
    ``ensure_pending_rwb_job``.

    ``link_type``/``link_id`` name the EDM, RDM, or submission this job concerns
    (CR-04c) — always required; ``link_type="not_applicable"`` covers job types
    with none of the three. ``context_type``/``context_id`` name what this job's
    own operation acts on, derived from the worker body — never copied from
    ``requestor_id``. Both are required keyword arguments but accept ``None``
    for job types that act on no single application row.

    ``correlation_id`` defaults to the bound log context's — the request middleware
    (web tier) or the per-job bind (poller/worker chaining) has stamped it, so call
    sites don't pass it explicitly (issue #28).

    ``conn`` lets a caller enqueue the chained tail in its own open transaction."""
    job_id = str(uuid.uuid4())
    params = {
        "id": job_id, "rt": requestor_type, "rid": str(requestor_id),
        "lt": link_type, "lid": (str(link_id) if link_id is not None else None),
        "ct": context_type,
        "ctxid": (str(context_id) if context_id is not None else None),
        "jt": rwb_job_type, "input": _json(input_data), "now": _utcnow(),
        "cid": correlation_id or log_context.correlation_id(),
        "by": (str(actor_id) if actor_id is not None else None),
    }
    return job_id if _insert_head(params, conn) else None


def ensure_pending_rwb_job(
    *, requestor_type: str, requestor_id: Any, rwb_job_type: str,
    link_type: str, link_id: Any | None, context_type: str | None,
    context_id: Any | None, input_data: dict | None = None,
    actor_id: Any | None = None, correlation_id: str | None = None,
) -> str | None:
    """Request-path (re)enqueue for retry / re-sync (FR-044/FR-045). Insert a fresh
    ``pending`` head if none exists; if the existing head is **terminal**
    (``succeeded``/``failed``) reset it to ``pending`` for a new attempt; if it is
    already ``pending``/``running``/``cancelled`` skip it (return ``None``). This is
    the deliberate counterpart to ``enqueue_rwb_job`` — that one never revives a
    terminal row so a mechanical re-poll cannot; this one does, because an analyst
    asked for it.

    ``link_type``/``link_id``/``context_type``/``context_id`` — see
    ``enqueue_rwb_job`` (CR-04c). A revived row's four fields are re-stamped from
    this call's values, replacing rather than merging with the prior row's, the
    same way ``input_data`` is fully replaced.

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
                    "lt": link_type,
                    "lid": (str(link_id) if link_id is not None else None),
                    "ct": context_type,
                    "ctxid": (str(context_id) if context_id is not None else None),
                    "jt": rwb_job_type, "input": _json(input_data), "now": now,
                    "cid": correlation_id,
                    "by": (str(actor_id) if actor_id is not None else None)}, conn):
                    return job_id
                return None
            if row["status_code"] not in ("succeeded", "failed"):
                return None
            conn.execute(text(
                """
                UPDATE rwb_job
                SET status_code = 'pending', claimed_by = NULL, output_data = NULL,
                    error_detail = NULL, completed_at = NULL, submitted_at = NULL,
                    input_data = :input, attempt_count = attempt_count + 1,
                    correlation_id = :cid, updated_at = :now, updated_by = :by,
                    link_type = :lt, link_id = :lid, context_type = :ct,
                    context_id = :ctxid
                WHERE id = :id
                """
            ), {"input": _json(input_data), "now": now, "cid": correlation_id,
                "by": (str(actor_id) if actor_id is not None else None),
                "id": str(row["id"]), "lt": link_type,
                "lid": (str(link_id) if link_id is not None else None),
                "ct": context_type,
                "ctxid": (str(context_id) if context_id is not None else None)})
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


def cancel_rwb_job(*, rwb_job_id: Any) -> bool:
    """Cancel a job from the monitoring page: ``pending`` (racing ``claim_rwb_job``
    — whichever runs first wins), ``failed`` (dismissing a failure nobody intends
    to resubmit, which forecloses ``resubmit_rwb_job``), or a ``running`` row
    whose heartbeat is missing or older than ``rwb_heartbeat_stale_secs``. One
    guarded ``UPDATE`` for all three. Returns ``False`` when the row is
    ``succeeded``, ``cancelled``, or ``running`` with a live heartbeat."""
    now = _utcnow()
    cutoff = now - timedelta(seconds=settings.rwb_heartbeat_stale_secs)
    rows = execute_command(
        """
        UPDATE rwb_job
        SET status_code = 'cancelled', updated_at = :now
        WHERE id = :id
          AND (
            status_code = 'pending'
            OR status_code = 'failed'
            OR (status_code = 'running' AND id IN (
                SELECT rj.id FROM rwb_job rj
                LEFT JOIN rwb_job_heartbeat hb ON hb.rwb_job_id = rj.id
                WHERE rj.id = :id
                  AND (hb.heartbeat_at IS NULL OR hb.heartbeat_at < :cutoff)
            ))
          )
        """,
        {"now": now, "cutoff": cutoff, "id": str(rwb_job_id)},
        connection="WORKBENCH",
    )
    return rows == 1


def get_rwb_job(*, rwb_job_id: Any) -> dict | None:
    """Read one queue row (post-claim, the worker runtime binds its log context
    from this — ``claim_rwb_job`` deliberately keeps its bool contract). Returns
    ``None`` when the id is unknown."""
    return execute_one(
        """
        SELECT id, requestor_type, requestor_id, link_type, link_id,
               context_type, context_id, rwb_job_type, status_code,
               attempt_count, correlation_id, inserted_by
        FROM rwb_job WHERE id = :id
        """,
        {"id": str(rwb_job_id)},
        connection="WORKBENCH",
    )


# Capped rather than paged: ``rwb_job`` is append-only, and the filters are how
# an analyst reaches older jobs.
MONITOR_LIMIT = 50


# The monitoring page's sortable columns. Each sort runs before MONITOR_LIMIT,
# so no matching job is left off the page by the cap.
MONITOR_SORTS = {
    "rwb_job_type": "rj.rwb_job_type",
    "entity_name": "entity_name",
    "submitted_by": "u.display_name",
    "status_code": "rj.status_code",
    "submitted_at": "COALESCE(rj.submitted_at, rj.inserted_at)",
}


def list_rwb_jobs_for_monitoring(
    *, submitted_by: list[Any] | None = None, rwb_job_types: list[str] | None = None,
    status_codes: list[str] | None = None, rwb_job_ids: list[Any] | None = None,
    sort: str = "submitted_at", descending: bool = True,
) -> list[dict]:
    """The first ``MONITOR_LIMIT`` ``rwb_job`` rows for the monitoring page in
    ``MONITOR_SORTS[sort]`` order, newest first by default
    (``contracts/job-monitoring-routes.md``). Every filter is optional and
    AND-combined; ``submitted_by`` matches ``rwb_job.inserted_by``.
    ``rwb_job_ids`` re-reads named rows with the same computed columns, which is
    how cancel and resubmit render the one row they changed.

    ``entity_name``/``entity_kind`` name the job's context (docs/DATA_MODEL.md
    §8): an ``execution`` has no table, so it names the linked EDM, and a
    ``result_export`` record lives in the loss repository, so it names the
    linked submission.

    Elapsed time is computed by the caller: it changes on every render.

    Every row carries ``is_dead`` (0/1) — ``status_code = 'running'`` with a
    ``rwb_job_heartbeat`` row missing or older than
    ``settings.rwb_heartbeat_stale_secs``, the same staleness
    ``reconcile_stale_rwb_jobs`` reclaims. ``status_codes`` accepts the
    synthetic ``"dead"`` to filter on that computed condition; the row's stored
    ``status_code`` stays ``'running'`` until ``cancel_rwb_job`` or the
    reconciler moves it."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for column, values, prefix in (("rj.id", rwb_job_ids, "rid"),
                                   ("rj.inserted_by", submitted_by, "by"),
                                   ("rj.rwb_job_type", rwb_job_types, "jt")):
        if values:
            clause, p = _in_clause(column, [str(v) for v in values], prefix)
            clauses.append(clause)
            params |= p
    if status_codes:
        # "dead" isn't a stored status_code — it's a running row whose heartbeat
        # is stale or missing, the same condition reconcile_stale_rwb_jobs
        # reclaims. Split it out and OR it back in against the heartbeat join
        # below, alongside a plain status_code IN (...) for whatever real
        # statuses were also asked for.
        real_statuses = [s for s in status_codes if s != "dead"]
        status_clauses: list[str] = []
        if real_statuses:
            clause, p = _in_clause("rj.status_code", real_statuses, "st")
            status_clauses.append(clause)
            params |= p
        if "dead" in status_codes:
            status_clauses.append(
                "(rj.status_code = 'running' "
                "AND (hb.heartbeat_at IS NULL OR hb.heartbeat_at < :dead_cutoff))")
        clauses.append("(" + " OR ".join(status_clauses) + ")")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    direction = "DESC" if descending else "ASC"
    # Always bound: both the "dead" filter clause above and the is_dead display
    # column below reference the same cutoff, so every row's dead-ness reflects
    # one consistent instant rather than "now" drifting between the two reads.
    params["dead_cutoff"] = (
        _utcnow() - timedelta(seconds=settings.rwb_heartbeat_stale_secs))
    return execute(
        f"""
        SELECT rj.id, rj.requestor_type, rj.requestor_id, rj.link_type, rj.link_id,
               rj.context_type, rj.context_id, rj.rwb_job_type, rj.status_code,
               rj.error_detail, rj.attempt_count, rj.submitted_at, rj.completed_at,
               rj.inserted_at, rj.updated_at,
               u.display_name AS submitted_by,
               COALESCE(e.name, r.name, a.name, p.name, bg.label, le.name, ls.name)
                   AS entity_name,
               CASE rj.context_type
                    WHEN 'edm' THEN 'EDM' WHEN 'rdm' THEN 'RDM'
                    WHEN 'irp_analysis' THEN 'Analysis' WHEN 'portfolio' THEN 'Portfolio'
                    WHEN 'breakout_group' THEN 'Breakout group'
                    WHEN 'execution' THEN 'EDM' WHEN 'result_export' THEN 'Submission'
               END AS entity_kind,
               CASE WHEN rj.status_code = 'running'
                         AND (hb.heartbeat_at IS NULL OR hb.heartbeat_at < :dead_cutoff)
                    THEN 1 ELSE 0 END AS is_dead
        FROM rwb_job rj
        LEFT JOIN app_user u ON u.id = rj.inserted_by
        LEFT JOIN irp_edm e ON rj.context_type = 'edm' AND e.id = rj.context_id
        LEFT JOIN irp_rdm r ON rj.context_type = 'rdm' AND r.id = rj.context_id
        LEFT JOIN irp_analysis a ON rj.context_type = 'irp_analysis' AND a.id = rj.context_id
        LEFT JOIN irp_portfolio p ON rj.context_type = 'portfolio' AND p.id = rj.context_id
        LEFT JOIN breakout_group bg
               ON rj.context_type = 'breakout_group' AND bg.id = rj.context_id
        LEFT JOIN irp_edm le
               ON rj.context_type = 'execution' AND rj.link_type = 'edm'
              AND le.id = rj.link_id
        LEFT JOIN submission ls
               ON rj.context_type = 'result_export' AND rj.link_type = 'submission'
              AND ls.id = rj.link_id
        LEFT JOIN rwb_job_heartbeat hb ON hb.rwb_job_id = rj.id
        {where}
        ORDER BY {MONITOR_SORTS[sort]} {direction}, rj.id {direction}
        """ + row_limit(MONITOR_LIMIT),
        params,
        connection="WORKBENCH",
    )


def job_type_kinds() -> list[tuple[str, str]]:
    """Every ``rwb_job_type`` as ``(code, label)`` in display order, for the
    monitoring page's job-type filter — mirrors
    ``submission_service.status_kinds()``'s read-from-the-kind-table
    convention rather than a literal list."""
    rows = execute(
        "SELECT code, label FROM rwb_job_type_kind ORDER BY sort_order, code",
        {}, connection="WORKBENCH",
    )
    return [(row["code"], row["label"]) for row in rows]


def status_kinds() -> list[tuple[str, str]]:
    """Every ``rwb_job_status_kind`` as ``(code, label)``, plus the synthetic
    ``"dead"`` value (§3 decision 6a — a computed condition, not a stored
    status, so it has no kind-table row of its own), for the monitoring
    page's job-status filter. ``"dead"`` sorts right after ``"running"``,
    where it belongs conceptually."""
    rows = execute(
        "SELECT code, label FROM rwb_job_status_kind ORDER BY sort_order, code",
        {}, connection="WORKBENCH",
    )
    kinds = [(row["code"], row["label"]) for row in rows]
    running_index = next(
        (i for i, (code, _) in enumerate(kinds) if code == "running"), len(kinds) - 1)
    kinds.insert(running_index + 1, ("dead", "Dead"))
    return kinds


def resubmit_rwb_job(*, rwb_job_id: Any) -> str | None:
    """Resubmit a failed job from the monitoring page (CR-004a), given only its
    id — the UI doesn't already know a row's ``requestor_type``/``requestor_id``/
    ``rwb_job_type``/``input_data`` the way a code caller of
    ``ensure_pending_rwb_job`` normally would, so this looks them up first. Calls
    ``ensure_pending_rwb_job`` unchanged: resets the SAME row (same ``id``,
    ``attempt_count`` incremented, ``error_detail``/``output_data``/``completed_at``
    cleared) — no new row, no new dedup logic. Returns ``None`` if the row is
    unknown or is not failed."""
    row = execute_one(
        "SELECT requestor_type, requestor_id, rwb_job_type, input_data, "
        "link_type, link_id, context_type, context_id "
        "FROM rwb_job WHERE id = :id AND status_code = 'failed'",
        {"id": str(rwb_job_id)},
        connection="WORKBENCH",
    )
    if row is None:
        return None
    input_data = json.loads(row["input_data"]) if row["input_data"] else None
    return ensure_pending_rwb_job(
        requestor_type=row["requestor_type"],
        requestor_id=row["requestor_id"],
        rwb_job_type=row["rwb_job_type"],
        link_type=row["link_type"],
        link_id=row["link_id"],
        context_type=row["context_type"],
        context_id=row["context_id"],
        input_data=input_data,
    )


def complete_rwb_job(
    *, rwb_job_id: Any, status: str, output_data: dict | None = None,
    error_detail: str | None = None,
) -> None:
    """In-place completion (Article 4): set ``succeeded``/``failed`` + payload +
    ``completed_at``. Chained tail rows are enqueued by the caller in the same
    worker-owned transaction (contracts/data-access.md).

    Guarded on ``running`` like every other transition here: a worker that
    finishes after ``cancel_rwb_job`` cancelled its dead row, or after
    ``reconcile_stale_rwb_jobs`` reclaimed it to ``pending`` and the queue
    re-dispatched it, matches zero rows and leaves the newer state alone.
    ``cancelled`` is terminal (data-model.md)."""
    now = _utcnow()
    rows = execute_command(
        """
        UPDATE rwb_job
        SET status_code = :st, output_data = :out, error_detail = :err,
            completed_at = :now, updated_at = :now
        WHERE id = :id AND status_code = 'running'
        """,
        {"st": status, "out": _json(output_data), "err": error_detail,
         "now": now, "id": str(rwb_job_id)},
        connection="WORKBENCH",
    )
    if rows != 1:
        logger.warning(
            "rwb_job %s completion as %s ignored — row is no longer running",
            rwb_job_id, status)


def load_input_data(rwb_job_id: Any) -> dict:
    """The job's parsed ``input_data`` payload — ``{}`` when the row is missing
    or carries none. The one queue-payload decoder: workers read their approved
    plan and context through this."""
    row = execute_one("SELECT input_data FROM rwb_job WHERE id = :id",
                      {"id": str(rwb_job_id)}, connection="WORKBENCH")
    if row is None or not row["input_data"]:
        return {}
    return json.loads(row["input_data"])


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


def backfill_edm_detail_rows(
    edm_ids: list[Any], *, statuses: tuple[str, ...] | None = None,
) -> list[dict]:
    """Every ``backfill_edm_detail`` queue row belonging to the given EDMs,
    newest ``updated_at`` first, as ``{edm_id, status_code}`` — the one owner
    of the membership predicate across the two enqueue keys: the poller's head
    keys on the finished ``import_edm`` irp_job, the manual Sync's on
    ``(analyst_request, edm_id)``. ``statuses`` narrows the read (the in-flight checks pass
    ``('pending', 'running')``)."""
    if not edm_ids:
        return []
    params: dict[str, Any] = {f"e{i}": str(e) for i, e in enumerate(edm_ids)}
    edms = ", ".join(f":e{i}" for i in range(len(edm_ids)))
    status_clause = ""
    if statuses:
        params.update({f"s{i}": s for i, s in enumerate(statuses)})
        codes = ", ".join(f":s{i}" for i in range(len(statuses)))
        status_clause = f"AND rj.status_code IN ({codes}) "
    return execute(
        "SELECT rj.status_code, "
        "COALESCE(ij.irp_edm_id, rj.requestor_id) AS edm_id "
        "FROM rwb_job rj "
        "LEFT JOIN irp_job ij ON rj.requestor_type = 'irp_job' "
        "AND rj.requestor_id = ij.id "
        "WHERE rj.rwb_job_type = 'backfill_edm_detail' "
        f"AND (ij.irp_edm_id IN ({edms}) "
        "     OR (rj.requestor_type = 'analyst_request' "
        f"         AND rj.requestor_id IN ({edms}))) "
        + status_clause +
        "ORDER BY rj.updated_at DESC",
        params, connection="WORKBENCH")


__all__ = [
    "analysis_link",
    "enqueue_rwb_job",
    "ensure_pending_rwb_job",
    "claim_rwb_job",
    "cancel_rwb_job",
    "resubmit_rwb_job",
    "get_rwb_job",
    "list_rwb_jobs_for_monitoring",
    "job_type_kinds",
    "status_kinds",
    "complete_rwb_job",
    "load_input_data",
    "reconcile_stale_rwb_jobs",
    "backfill_edm_detail_rows",
]
