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
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app import log_context
from app.config import settings
from app.services._common import _json, _utcnow
from db import execute, execute_command, execute_one, get_connection, is_unique_violation

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

    ``link_type``/``link_id`` name the EDM or RDM this job concerns (CR-04c) —
    always required; ``link_type="not_applicable"`` covers job types with no
    EDM/RDM. ``context_type``/``context_id`` name what this job's own operation
    acts on, derived from the worker body — never copied from ``requestor_id``.
    Both are required keyword arguments but accept ``None`` for job types that
    act on no single application row.

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
    """Cancel a job from the monitoring page (CR-004a): ``pending`` → ``cancelled``
    (before a worker claims it — same atomic-guard shape as ``claim_rwb_job``,
    whichever of the two runs first against a given row wins), ``failed`` →
    ``cancelled`` (dismiss a failure nobody intends to resubmit — the alternative
    to calling ``resubmit_rwb_job``, forecloses it), or a **dead** ``running`` row
    → ``cancelled`` (a worker claimed it and then never heartbeated, or stopped
    heartbeating more than ``rwb_heartbeat_stale_secs`` ago — the same staleness
    ``reconcile_stale_rwb_jobs`` detects, but that function's only remedy is
    resetting the row to ``pending`` for another attempt; this lets the
    monitoring page cancel it outright instead of waiting for that reclaim,
    without reintroducing a general "stop a running job" action — a row with a
    live heartbeat never matches this guard). Returns ``False`` when the row is
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
               attempt_count, correlation_id
        FROM rwb_job WHERE id = :id
        """,
        {"id": str(rwb_job_id)},
        connection="WORKBENCH",
    )


def _word_and_clauses(
    term: str, columns: tuple[str, ...], prefix: str,
) -> tuple[list[str], dict[str, Any]]:
    """One clause per whitespace-separated word in ``term``: the word must appear
    in at least one of ``columns``, and every word must match (AND across words,
    OR across columns) — mirrors ``submission_service._word_and_clauses``, kept
    local rather than imported so this module doesn't reach into another
    service's private helpers."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for index, word in enumerate(term.split()):
        key = f"{prefix}{index}"
        escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        match = " OR ".join(f"{col} LIKE :{key} ESCAPE '\\'" for col in columns)
        clauses.append(f"({match})")
        params[key] = f"%{escaped}%"
    return clauses, params


def _in_clause(column: str, values: list[Any], prefix: str) -> tuple[str, dict[str, Any]]:
    params = {f"{prefix}{i}": v for i, v in enumerate(values)}
    placeholders = ", ".join(f":{k}" for k in params)
    return f"{column} IN ({placeholders})", params


def list_rwb_jobs_for_monitoring(
    *, submission_name: str | None = None, submission_status_codes: list[str] | None = None,
    owner_ids: list[Any] | None = None, rwb_job_types: list[str] | None = None,
    status_codes: list[str] | None = None,
) -> list[dict]:
    """``rwb_job`` rows for the monitoring page (CR-004a), grouped by
    ``rwb_job_type`` and ordered by status then most-recently-updated within each
    group, per ``contracts/job-monitoring-routes.md``. Every filter is optional
    and AND-combined; an empty/``None`` value turns that filter off.

    Search reaches submission through the job's own ``link_type``/``link_id``
    (CR-04c) — never through ``requestor_type``/``requestor_id``, which names
    who triggered the job, not what EDM/RDM (and therefore submission) it
    concerns. ``submission_name`` matches the submission's ``name`` or
    ``cedant_name`` the same word-and-clauses way
    ``submission_service.list_submissions`` matches them. ``owner_ids`` filters
    on the submission's ``assigned_analyst_id`` — a plain predicate (Article 6),
    not an access gate; the caller decides whether to default it to the current
    user. A job whose ``link_type = 'not_applicable'``, or whose EDM/RDM belongs
    to no submission, is excluded by any of the three submission-scoped filters
    but still returned when none of them are set. A job's EDM/RDM belonging to
    more than one submission still returns exactly one row — the submission
    filters match "belongs to at least one qualifying submission," they never
    fan a job out per submission (that's ``list_submissions_for_rwb_jobs``,
    the batched second read the caller uses for display).

    Elapsed-time display (now minus ``submitted_at``/``completed_at``) is
    computed by the caller, not here — it changes on every render, so baking it
    into the query would only be correct at the instant the query ran.

    Every row carries ``is_dead`` (0/1): true iff ``status_code = 'running'``
    and its ``rwb_job_heartbeat`` row is missing or older than
    ``settings.rwb_heartbeat_stale_secs`` — the same staleness
    ``reconcile_stale_rwb_jobs`` reclaims to ``pending`` on the poller's next
    pass. ``status_codes`` accepts the synthetic value ``"dead"`` alongside real
    ``rwb_job_status_kind`` codes to filter on this computed condition instead
    of a stored column; a dead row's own ``status_code`` is still ``'running'``
    underneath (nothing here writes to the row — see ``cancel_rwb_job`` for the
    one action a dead row accepts)."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if rwb_job_types:
        clause, p = _in_clause("rj.rwb_job_type", rwb_job_types, "jt")
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
    submission_scoped = bool(submission_name or submission_status_codes or owner_ids)
    if submission_scoped:
        sub_clauses: list[str] = []
        sub_params: dict[str, Any] = {}
        if submission_name:
            name_clauses, name_params = _word_and_clauses(
                submission_name.strip(), ("s.name", "s.cedant_name"), "sn")
            sub_clauses += name_clauses
            sub_params |= name_params
        if submission_status_codes:
            clause, p = _in_clause("s.status_code", submission_status_codes, "ss")
            sub_clauses.append(clause)
            sub_params |= p
        if owner_ids:
            clause, p = _in_clause("s.assigned_analyst_id",
                                    [str(o) for o in owner_ids], "so")
            sub_clauses.append(clause)
            sub_params |= p
        sub_where = (" AND " + " AND ".join(sub_clauses)) if sub_clauses else ""
        clauses.append(
            "EXISTS ("
            "SELECT 1 FROM submission_edm se JOIN submission s ON s.id = se.submission_id "
            f"WHERE rj.link_type = 'edm' AND se.edm_id = rj.link_id{sub_where}"
            " UNION ALL "
            "SELECT 1 FROM submission_rdm sr JOIN submission s ON s.id = sr.submission_id "
            f"WHERE rj.link_type = 'rdm' AND sr.rdm_id = rj.link_id{sub_where}"
            ")"
        )
        params |= sub_params
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
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
               COALESCE(e.name, r.name) AS entity_name,
               CASE WHEN rj.status_code = 'running'
                         AND (hb.heartbeat_at IS NULL OR hb.heartbeat_at < :dead_cutoff)
                    THEN 1 ELSE 0 END AS is_dead
        FROM rwb_job rj
        LEFT JOIN irp_edm e ON rj.link_type = 'edm' AND e.id = rj.link_id
        LEFT JOIN irp_rdm r ON rj.link_type = 'rdm' AND r.id = rj.link_id
        LEFT JOIN rwb_job_heartbeat hb ON hb.rwb_job_id = rj.id
        {where}
        ORDER BY rj.rwb_job_type, rj.status_code, rj.updated_at DESC
        """,
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


def list_submissions_for_rwb_jobs(
    links: list[tuple[str, Any]],
) -> dict[tuple[str, str], list[dict]]:
    """Every submission each ``(link_type, link_id)`` pair belongs to, keyed by
    that same pair (``link_id`` normalized to ``str``) — the monitoring page's
    batched second read for the "submission(s)" display column, kept separate
    from ``list_rwb_jobs_for_monitoring`` so a job's row count never depends on
    how many submissions its EDM/RDM belongs to. One query per link type (``edm``
    ids and ``rdm`` ids don't share a source table), Python-side dict build
    rather than ``STRING_AGG``/``GROUP_CONCAT`` — not portable to the SQLite unit
    tier (``submission_service.py``'s own portability contract)."""
    result: dict[tuple[str, str], list[dict]] = {}
    edm_ids = [str(lid) for lt, lid in links if lt == "edm" and lid is not None]
    rdm_ids = [str(lid) for lt, lid in links if lt == "rdm" and lid is not None]
    if edm_ids:
        clause, params = _in_clause("se.edm_id", edm_ids, "e")
        rows = execute(
            "SELECT se.edm_id AS link_id, s.id, s.name FROM submission_edm se "
            f"JOIN submission s ON s.id = se.submission_id WHERE {clause} "
            "ORDER BY s.name",
            params, connection="WORKBENCH",
        )
        for row in rows:
            key = ("edm", str(row["link_id"]))
            result.setdefault(key, []).append({"id": row["id"], "name": row["name"]})
    if rdm_ids:
        clause, params = _in_clause("sr.rdm_id", rdm_ids, "r")
        rows = execute(
            "SELECT sr.rdm_id AS link_id, s.id, s.name FROM submission_rdm sr "
            f"JOIN submission s ON s.id = sr.submission_id WHERE {clause} "
            "ORDER BY s.name",
            params, connection="WORKBENCH",
        )
        for row in rows:
            key = ("rdm", str(row["link_id"]))
            result.setdefault(key, []).append({"id": row["id"], "name": row["name"]})
    return result


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
    of the membership predicate across the THREE enqueue keys: the poller's
    head keys on the finished ``import_edm`` irp_job, the manual Sync's on
    ``(analyst_request, edm_id)``, and a completed breakout's auto-fired head
    on its ``run_breakout_*`` job row (spec 005 FR-013). A quick breakout job's
    requestor IS the source portfolio; a custom group's is the
    ``breakout_group`` row, whose ``source_portfolio_id`` resolves the EDM.
    ``statuses`` narrows the read (the in-flight checks pass
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
        "COALESCE(ij.irp_edm_id, p.edm_id, rj.requestor_id) AS edm_id "
        "FROM rwb_job rj "
        "LEFT JOIN irp_job ij ON rj.requestor_type = 'irp_job' "
        "AND rj.requestor_id = ij.id "
        "LEFT JOIN rwb_job bj ON rj.requestor_type = 'rwb_job' "
        "AND rj.requestor_id = bj.id "
        "AND bj.rwb_job_type LIKE 'run_breakout_%' "
        "LEFT JOIN breakout_group bg ON bj.requestor_type = 'breakout_group' "
        "AND bj.requestor_id = bg.id "
        "LEFT JOIN irp_portfolio p "
        "ON p.id = COALESCE(bg.source_portfolio_id, bj.requestor_id) "
        "WHERE rj.rwb_job_type = 'backfill_edm_detail' "
        f"AND (ij.irp_edm_id IN ({edms}) "
        "     OR (rj.requestor_type = 'analyst_request' "
        f"         AND rj.requestor_id IN ({edms})) "
        f"     OR p.edm_id IN ({edms})) "
        + status_clause +
        "ORDER BY rj.updated_at DESC",
        params, connection="WORKBENCH")


__all__ = [
    "enqueue_rwb_job",
    "ensure_pending_rwb_job",
    "claim_rwb_job",
    "cancel_rwb_job",
    "resubmit_rwb_job",
    "get_rwb_job",
    "list_rwb_jobs_for_monitoring",
    "list_submissions_for_rwb_jobs",
    "job_type_kinds",
    "status_kinds",
    "complete_rwb_job",
    "load_input_data",
    "reconcile_stale_rwb_jobs",
    "backfill_edm_detail_rows",
]
