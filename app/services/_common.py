"""Shared low-level helpers reused across the entity/job services and their workers.

Consolidated here to remove copy-paste drift. Kept intentionally tiny: a naive-UTC
stamp, NULL-safe JSON/id coercions, the caller-or-own transaction context manager
the poller and workers share, and the race-safe snapshot upsert + stale-row prune
the JSON-cache services (irp_portfolio / irp_treaty) share.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from db import execute, execute_command, execute_one, get_connection, is_unique_violation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubmissionRef:
    id: str
    name: str


# EDM/RDM shared identity: entity table, M:N submission-association table and
# its FK column naming the entity, the ``rwb_job_type`` its upload head
# enqueues under, and the display label for error/log text — keyed the same
# way entity_note_service._TABLES is, for the same reason (one place per kind).
_ENTITY_ASSOC = {
    "edm": {"table": "irp_edm", "assoc": "submission_edm", "id_col": "edm_id",
            "job_type": "upload_edm", "label": "EDM"},
    "rdm": {"table": "irp_rdm", "assoc": "submission_rdm", "id_col": "rdm_id",
            "job_type": "upload_rdm", "label": "RDM"},
}
# The EDM/RDM import-status vocabulary — identical values in both kinds (each
# service also exports its own PENDING/IMPORTING/READY/ERROR constants for its
# own status-filter vocabulary; only the values need to agree here).
_PENDING = "pending_import"
_IMPORTING = "importing"
_READY = "ready"
_ERROR = "error"
_LOCKED = (_IMPORTING, _READY)


def _utcnow() -> datetime:
    """Naive UTC timestamp — safe for DATETIME2 (no tz) and SQLite alike."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def _uid(value: Any) -> str | None:
    """Normalize a UUID/id to a lowercase string (``None`` passes through).

    App-generated ids come from ``uuid4()`` (lowercase); SQL Server's
    ``uniqueidentifier`` reads them back UPPERCASE. Lowercasing every id the
    service hands out keeps app-generated, bound, and read-back ids
    byte-identical, so Python-side equality (dedup sets, "is this the selected
    row?" checks, redirect URLs) is stable across both backends. SQL Server
    compares ``uniqueidentifier`` case-insensitively so lookups are unaffected,
    and the SQLite unit tier stores strings verbatim so this is a no-op there."""
    return None if value is None else str(value).lower()


@contextmanager
def _txn(conn):
    """Yield a working connection: reuse the caller's (no new transaction, so a worker/
    poller can span ``irp_job`` + ``rwb_job`` in one transaction) or open our own
    ``get_connection("WORKBENCH") + begin()`` when none was supplied."""
    if conn is not None:
        yield conn
    else:
        with get_connection("WORKBENCH") as owned:
            with owned.begin():
                yield owned


def _snapshot_upsert(conn, params: dict, *, update_by_irp: str,
                     update_by_name: str, insert: str) -> None:
    """The idempotent JSON-snapshot upsert shared by ``portfolio_service`` and
    ``treaty_service`` (each keeps its own three SQL statements — same params:
    ``irp``/``name``/``edm``/``snap``/``asof``/``now``): overwrite by
    (edm_id, irp_id) first, fall back to the (edm_id, name) match for a row
    first written without its RM id, else insert. A concurrent backfill of the
    same EDM can win the insert race; absorb the UNIQUE(edm_id, irp_id)
    violation in a SAVEPOINT and overwrite in place — the constraint, not the
    pre-check, is the real dedup guarantee."""
    if params["irp"] is not None and conn.execute(
            text(update_by_irp), params).rowcount:
        return
    if conn.execute(text(update_by_name), params).rowcount:
        return
    try:
        with conn.begin_nested():
            conn.execute(text(insert), params)
    except Exception as exc:  # noqa: BLE001 — a UNIQUE race is a dedup hit
        if not is_unique_violation(exc):
            raise
        if params["irp"] is not None and conn.execute(
                text(update_by_irp), params).rowcount:
            return
        if conn.execute(text(update_by_name), params).rowcount:
            return
        # Neither recovery update matched — e.g. SQL Server treats two NULL
        # irp_ids as equal, so a second id-less row violates the constraint yet
        # matches nothing by name. Re-raise: a loud, recoverable job failure
        # beats silently dropping the row.
        raise


def _snapshot_prune(conn, *, table: str, edm_id: Any,
                    seen: list[tuple[str | None, str]], now: Any) -> int:
    """Reconcile a snapshot table's row set against a SUCCESSFUL full Risk
    Modeler enumeration: soft-delete (``deleted_at``) this EDM's live rows the
    enumeration no longer returned (the entity was deleted in RM — its stale
    snapshot must not keep rendering with a fresh-looking ``as_of``), and clear
    ``deleted_at`` on pruned rows it returned again (RM-side delete/recreate).
    "Seen" mirrors ``_snapshot_upsert``'s identity resolution: an irp_id match
    OR a name match keeps the row. Never call this after a failed or partial
    enumeration — pruning is only valid against the full set. ``table`` is a
    module-supplied literal, never user input. Returns the rows pruned."""
    ids = [str(irp_id) for irp_id, _ in seen if irp_id is not None]
    names = [name for _, name in seen]
    params: dict[str, Any] = {"edm": str(edm_id), "now": now}
    params.update({f"i{i}": v for i, v in enumerate(ids)})
    params.update({f"n{i}": v for i, v in enumerate(names)})
    id_marks = [f":i{i}" for i in range(len(ids))]
    name_marks = [f":n{i}" for i in range(len(names))]

    kept: list[str] = []       # a live row stays when either identity matched
    stale: list[str] = []      # ... and is pruned when neither did
    if id_marks:
        kept.append(f"irp_id IN ({', '.join(id_marks)})")
        stale.append(f"(irp_id IS NULL OR irp_id NOT IN ({', '.join(id_marks)}))")
    if name_marks:
        kept.append(f"name IN ({', '.join(name_marks)})")
        stale.append(f"name NOT IN ({', '.join(name_marks)})")
    if kept:  # resurrect first so the caller's upserts see a live row to claim
        conn.execute(text(
            f"UPDATE {table} SET deleted_at = NULL, updated_at = :now "
            f"WHERE edm_id = :edm AND deleted_at IS NOT NULL "
            f"AND ({' OR '.join(kept)})"), params)
    stale_sql = ("".join(f" AND {c}" for c in stale)
                 if stale else "")  # RM returned nothing → prune every live row
    return conn.execute(text(
        f"UPDATE {table} SET deleted_at = :now, updated_at = :now "
        f"WHERE edm_id = :edm AND deleted_at IS NULL{stale_sql}"),
        params).rowcount


def _attach_submissions(kind: str, rows: list) -> None:
    """Set each row's ``.submissions`` from the entity's M:N submission
    association table (oldest-first) — shared by ``edm_service.list_edms``
    and ``rdm_service.list_rdms``. One query for the whole page; standalone
    rows keep the default []."""
    cfg = _ENTITY_ASSOC[kind]
    entity_ids = [r.id for r in rows]
    if not entity_ids:
        return
    params = {f"e{i}": value for i, value in enumerate(entity_ids)}
    placeholders = ", ".join(f":e{i}" for i in range(len(entity_ids)))
    refs: dict[str, list[SubmissionRef]] = {}
    for association in execute(
        f"SELECT a.{cfg['id_col']} AS entity_id, s.id, s.name "
        f"FROM {cfg['assoc']} a JOIN submission s ON s.id = a.submission_id "
        f"WHERE a.{cfg['id_col']} IN ({placeholders}) ORDER BY s.inserted_at",
        params, connection="WORKBENCH",
    ):
        refs.setdefault(_uid(association["entity_id"]), []).append(
            SubmissionRef(id=_uid(association["id"]), name=association["name"]))
    for row in rows:
        row.submissions = refs.get(row.id, [])


def _submission_entity_context(
    kind: str, *, submission_id: Any, entity_id: Any,
) -> tuple[SubmissionRef, list[SubmissionRef]] | None:
    """Confirm ``entity_id`` (an EDM or RDM) belongs to ``submission_id``, and
    return the owning submission plus the sibling choices — this submission's
    other same-kind entities, the named one first. ``None`` when the entity
    isn't associated with the submission (or is soft-deleted) — shared by
    ``edm_service.get_contextual_edm_detail`` and
    ``rdm_service.get_contextual_rdm_detail``."""
    cfg = _ENTITY_ASSOC[kind]
    sid = str(submission_id)
    eid = str(entity_id)
    source = execute_one(
        f"SELECT s.id, s.name FROM submission s "
        f"JOIN {cfg['assoc']} a ON a.submission_id = s.id "
        f"JOIN {cfg['table']} e ON e.id = a.{cfg['id_col']} "
        "WHERE s.id = :submission_id AND e.id = :entity_id "
        "AND e.deleted_at IS NULL",
        {"submission_id": sid, "entity_id": eid}, connection="WORKBENCH")
    if source is None:
        return None
    choices = execute(
        f"SELECT e.id, e.name FROM {cfg['assoc']} a "
        f"JOIN {cfg['table']} e ON e.id = a.{cfg['id_col']} "
        "WHERE a.submission_id = :submission_id AND e.deleted_at IS NULL "
        "ORDER BY CASE WHEN e.id = :entity_id THEN 0 ELSE 1 END, e.name",
        {"submission_id": sid, "entity_id": eid}, connection="WORKBENCH")
    return (
        SubmissionRef(id=_uid(source["id"]), name=source["name"]),
        [SubmissionRef(id=_uid(row["id"]), name=row["name"]) for row in choices],
    )


def _import_entity(
    kind: str, *, name: str, source_file_path: str, actor_id: Any,
    submission_id: Any | None,
) -> tuple[str, bool]:
    """Create a pending EDM/RDM and enqueue its upload head — the shared shape
    of ``edm_service.import_edm``/``rdm_service.import_rdm``. The only Risk
    Modeler call is the cached name-collision read (Article 11), raised as
    ``NameCollisionError`` before anything is persisted. Returns
    ``(entity_id, collision_unchecked)``; the caller wraps it in its own
    ``ImportResult``."""
    from app.services import rwb_job_service, shared_drive
    from app.services.errors import NameCollisionError, SubmissionClosed
    from app.services.name_check import _check as _check_name_collision
    from app.services.name_check import clean_entity_name
    from app.workers import dispatch

    cfg = _ENTITY_ASSOC[kind]
    name = clean_entity_name(name)
    canonical = shared_drive.validate_selection(source_file_path)
    check = _check_name_collision(kind, name)
    if check.collides:
        raise NameCollisionError(
            f"An {cfg['label']} named '{name}' already exists in Risk Modeler. "
            "Choose a different name.")

    entity_id = str(uuid.uuid4())
    now = _utcnow()
    actor = str(actor_id)
    sid = str(submission_id) if submission_id is not None else None
    with get_connection("WORKBENCH") as conn, conn.begin():
        if sid is not None:
            status = conn.execute(text(
                "SELECT status_code FROM submission WHERE id = :id"
            ), {"id": sid}).scalar()
            if status != "ACTIVE":
                raise SubmissionClosed(
                    f"Submission is {status or 'missing'}; only ACTIVE deals are editable.")
        conn.execute(text(
            f"""
            INSERT INTO {cfg['table']} (id, source_file_path, name, status,
                inserted_at, updated_at, inserted_by, updated_by)
            VALUES (:id, :src, :name, :status, :now, :now, :by, :by)
            """
        ), {"id": entity_id, "src": canonical, "name": name, "status": _PENDING,
            "now": now, "by": actor})
        if sid is not None:
            conn.execute(text(
                f"INSERT INTO {cfg['assoc']} "
                f"(submission_id, {cfg['id_col']}, inserted_at, inserted_by) "
                "VALUES (:submission_id, :entity_id, :now, :actor)"
            ), {"submission_id": sid, "entity_id": entity_id,
                "now": now, "actor": actor})
    input_data = {cfg["id_col"]: entity_id}
    if sid is not None:
        input_data["requested_from_submission_id"] = sid
    job_id = rwb_job_service.enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=entity_id,
        rwb_job_type=cfg["job_type"], input_data=input_data, actor_id=actor,
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type=cfg["job_type"])
    return entity_id, not check.checked


def _mark_importing(kind: str, *, entity_id: Any, actor_id: Any | None = None) -> None:
    """Worker-side: the import submit succeeded — flip ``pending_import`` →
    ``importing`` (FR-004). Left alone if the row was already advanced
    (idempotent re-run)."""
    cfg = _ENTITY_ASSOC[kind]
    execute_command(
        f"UPDATE {cfg['table']} SET status = :s, updated_at = :now, updated_by = :by "
        "WHERE id = :id AND status = :from_status",
        {"s": _IMPORTING, "now": _utcnow(),
         "by": (str(actor_id) if actor_id is not None else None),
         "id": str(entity_id), "from_status": _PENDING},
        connection="WORKBENCH",
    )


def _mark_error(kind: str, *, entity_id: Any, actor_id: Any | None = None) -> None:
    """Worker-side: a submit-side failure (never reached Risk Modeler) — flip
    an import-in-progress entity to the visible, analyst-recoverable ``error``
    state. Only touches ``pending_import``/``importing``; idempotent on
    re-run."""
    cfg = _ENTITY_ASSOC[kind]
    execute_command(
        f"UPDATE {cfg['table']} SET status = :s, updated_at = :now, updated_by = :by "
        "WHERE id = :id AND status IN (:p, :i)",
        {"s": _ERROR, "now": _utcnow(),
         "by": (str(actor_id) if actor_id is not None else None),
         "id": str(entity_id), "p": _PENDING, "i": _IMPORTING},
        connection="WORKBENCH",
    )


def _retry_import(kind: str, *, entity_id: Any, actor_id: Any) -> None:
    """Re-enqueue a single EDM/RDM's upload head (FR-045). No-op when already
    ``ready`` or in flight (``importing``); otherwise resets an ``error``
    entity back to ``pending_import`` **and** the head back to ``pending`` so
    the worker re-submits (the body only advances a ``pending_import`` row)."""
    from app.services import rwb_job_service
    from app.workers import dispatch

    cfg = _ENTITY_ASSOC[kind]
    eid = str(entity_id)
    current = execute_one(
        f"SELECT status, updated_at FROM {cfg['table']} "
        "WHERE id = :id AND deleted_at IS NULL",
        {"id": eid}, connection="WORKBENCH")
    if current is None or current["status"] in _LOCKED:
        return
    execute_command(
        f"UPDATE {cfg['table']} SET status = :p, updated_at = :now, updated_by = :by "
        "WHERE id = :id AND status = :err",
        {"p": _PENDING, "err": _ERROR, "now": _utcnow(), "by": str(actor_id), "id": eid},
        connection="WORKBENCH",
    )
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=eid,
        rwb_job_type=cfg["job_type"], input_data={cfg["id_col"]: eid},
        actor_id=str(actor_id),
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type=cfg["job_type"])
    logger.info("%s %s import retry requested by analyst %s",
               cfg["label"].lower(), eid, actor_id)


def _replace_source_file(
    kind: str, *, entity_id: Any, new_source_file_path: str,
    expected_updated_at: Any, actor_id: Any,
) -> None:
    """Replace the source file of a failed/errored EDM/RDM and re-import
    (FR-046). Optimistic-concurrency checked on ``updated_at`` (FR-039)."""
    from app.services import rwb_job_service, shared_drive
    from app.services.errors import ConcurrencyConflict
    from app.workers import dispatch

    cfg = _ENTITY_ASSOC[kind]
    eid = str(entity_id)
    canonical = shared_drive.validate_selection(new_source_file_path)
    rows = execute_command(
        f"""
        UPDATE {cfg['table']}
        SET source_file_path = :src, status = :status, updated_at = :now,
            updated_by = :by
        WHERE id = :id AND updated_at = :expected AND deleted_at IS NULL
        """,
        {"src": canonical, "status": _PENDING, "now": _utcnow(),
         "by": str(actor_id), "id": eid, "expected": expected_updated_at},
        connection="WORKBENCH",
    )
    if rows == 0:
        raise ConcurrencyConflict(
            f"This {cfg['label']} changed since you opened it — reload and re-apply.")
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=eid,
        rwb_job_type=cfg["job_type"], input_data={cfg["id_col"]: eid},
        actor_id=str(actor_id),
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type=cfg["job_type"])
    logger.info("%s %s source file replaced by analyst %s — re-import enqueued",
               cfg["label"].lower(), eid, actor_id)


def _parse_json_dict(raw: Any, what: str) -> dict | None:
    """A stored JSON snapshot column parsed to a dict, ``None`` on NULL /
    unparseable / non-dict content — the read models render the graceful empty
    state instead of erroring (R2)."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("unparseable %s snapshot — rendering empty", what)
        return None
    return parsed if isinstance(parsed, dict) else None


__all__ = ["SubmissionRef", "_utcnow", "_json", "_uid", "_txn", "_snapshot_upsert",
           "_snapshot_prune", "_parse_json_dict", "_attach_submissions",
           "_submission_entity_context", "_import_entity", "_mark_importing",
           "_mark_error", "_retry_import", "_replace_source_file"]
