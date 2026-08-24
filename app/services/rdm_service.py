"""RDM imports, stored details, and analysis refresh operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.services import analysis_service, name_check, rwb_job_service
from app.services._common import (
    SubmissionRef,
    _attach_submissions,
    _import_entity,
    _mark_error,
    _mark_importing,
    _replace_source_file,
    _retry_import,
    _submission_entity_context,
    _uid,
    _utcnow,
)
from app.services.edm_service import ImportResult  # shared DTO
from app.services.name_check import CollisionCheck
from app.workers import dispatch
from db import execute, execute_one

logger = logging.getLogger(__name__)

PENDING = "pending_import"
IMPORTING = "importing"
READY = "ready"
ERROR = "error"
# Ordered status vocabulary offered as the library status filter (US7 / T058).
STATUSES = (PENDING, IMPORTING, READY, ERROR)
# Statuses a worker still moves on its own — the library list polls while any row
# sits in one of these and stops once every row is terminal.
TRANSIENT_STATUSES = (PENDING, IMPORTING)


@dataclass
class RdmRow:
    id: str
    name: str
    status: str | None
    source_file_path: str | None
    irp_id: int | None
    inserted_at: Any
    updated_at: Any
    notes: str | None = None
    # Owning submissions (M:N), oldest-first — populated only by ``list_rdms``;
    # defaulted so ``get_rdm`` and every existing caller are unaffected (US7 / T058).
    submissions: list[SubmissionRef] = field(default_factory=list)
    # Last-synced trust signal (FR-052, spec 004 US3) — stamped by the
    # backfill_rdm_analyses worker when the analysis capture lands.
    as_of: Any = None


def check_name_collision(name: str) -> CollisionCheck:
    """Check ``name`` against Risk Modeler (empty = clear). A hit blocks the save
    (issue #17); ``checked=False`` means the gateway couldn't answer — the caller
    fails open with a warning. Cached briefly in-process (issue #11)."""
    return name_check.check_rdm_name(name)


def import_rdm(
    *, name: str, source_file_path: str, actor_id: Any,
    submission_id: Any | None = None,
) -> ImportResult:
    """Create a pending RDM and enqueue one standalone RDM import.

    The only Risk Modeler call here is the cached name-collision read. Raises
    ``NameCollisionError`` — before persisting anything — when the name already
    exists there (issue #17). Validates the source (else ``InvalidSourceFile``)."""
    entity_id, collision_unchecked = _import_entity(
        "rdm", name=name, source_file_path=source_file_path, actor_id=actor_id,
        submission_id=submission_id)
    return ImportResult(entity_id=entity_id, collision_unchecked=collision_unchecked)


_ROW_SELECT = (
    "SELECT id, source_file_path, name, irp_id, status, as_of, "
    "inserted_at, updated_at, notes FROM irp_rdm"
)


def _to_row(row: dict) -> RdmRow:
    return RdmRow(
        id=_uid(row["id"]), name=row["name"], status=row["status"],
        source_file_path=row["source_file_path"], irp_id=row["irp_id"],
        inserted_at=row["inserted_at"],
        updated_at=row["updated_at"], as_of=row["as_of"],
        notes=row["notes"],
    )


def list_rdms(*, name: str | None = None,
              status: str | None = None) -> list[RdmRow]:
    """Return every live RDM, optionally filtered by name and status.

    ``name`` narrows by case-insensitive substring (``LIKE``); ``status`` narrows to
    the exact import status; both combine with AND; blank/``None`` are no-ops (US7 /
    T058). Each row's ``.submissions`` is set to its owning submissions (oldest-first)."""
    where = "WHERE deleted_at IS NULL"
    params: dict[str, Any] = {}
    if name:
        where += " AND name LIKE :q"
        params["q"] = f"%{name}%"
    if status:
        where += " AND status = :status"
        params["status"] = status
    rows = execute(f"{_ROW_SELECT} {where} ORDER BY inserted_at DESC, name",
                   params, connection="WORKBENCH")
    result = [_to_row(r) for r in rows]
    _attach_submissions("rdm", result)
    return result


def get_rdm(rdm_id: Any) -> RdmRow | None:
    row = execute_one(f"{_ROW_SELECT} WHERE id = :id",
                      {"id": str(rdm_id)}, connection="WORKBENCH")
    return _to_row(row) if row is not None else None


def latest_import_error(rdm_id: Any) -> str | None:
    """Mirror of ``edm_service.latest_import_error`` keyed on ``upload_rdm``: the
    failed head's ``error_detail`` with the worker framing stripped, or ``None``
    when nothing was recorded."""
    row = execute_one(
        "SELECT error_detail FROM rwb_job "
        "WHERE requestor_type = 'analyst_request' AND requestor_id = :r "
        "AND rwb_job_type = 'upload_rdm' AND status_code = 'failed'",
        {"r": str(rdm_id)}, connection="WORKBENCH")
    detail = row["error_detail"] if row is not None else None
    if not detail:
        return None
    prefix = "upload_rdm submit failed: "
    return detail[len(prefix):] if detail.startswith(prefix) else detail


def _current(rdm_id: str) -> dict | None:
    return execute_one(
        "SELECT status, updated_at FROM irp_rdm WHERE id = :id AND deleted_at IS NULL",
        {"id": rdm_id}, connection="WORKBENCH")


def retry_import(*, rdm_id: Any, actor_id: Any) -> None:
    """Re-enqueue a single RDM's ``upload_rdm`` head (FR-045). No-op when ready / in
    flight; otherwise resets an ``error`` entity back to ``pending_import`` **and** the
    head to ``pending`` so the worker re-runs the import (the body only advances a
    ``pending_import`` row)."""
    _retry_import("rdm", entity_id=rdm_id, actor_id=actor_id)


def replace_source_file(
    *, rdm_id: Any, new_source_file_path: str, expected_updated_at: Any, actor_id: Any,
) -> None:
    """Replace the source file of a failed/errored RDM and re-import (FR-046).
    Optimistic-concurrency checked on ``updated_at`` (FR-039)."""
    _replace_source_file(
        "rdm", entity_id=rdm_id, new_source_file_path=new_source_file_path,
        expected_updated_at=expected_updated_at, actor_id=actor_id)


# ── manual analysis-details sync (spec 004 follow-up, 2026-07-24) ─────────────────

def latest_backfill_status(rdm_id: Any) -> str | None:
    """The newest ``backfill_rdm_analyses`` job status touching this RDM across
    BOTH enqueue sources: the poller's head keys on the finished
    ``import_rdm`` irp_job (hence the join), the manual Sync's on
    ``(analyst_request, rdm_id)`` directly. Newest ``updated_at`` wins — a
    revived (re-synced) row keeps its ``inserted_at``, so insert order would
    lie. ``None`` when the capture never ran (pre-capability RDMs)."""
    row = execute_one(
        "SELECT rj.status_code FROM rwb_job rj "
        "LEFT JOIN irp_job ij ON rj.requestor_type = 'irp_job' "
        "AND rj.requestor_id = ij.id "
        "WHERE rj.rwb_job_type = 'backfill_rdm_analyses' "
        "AND (ij.irp_rdm_id = :r "
        "     OR (rj.requestor_type = 'analyst_request' AND rj.requestor_id = :r)) "
        "ORDER BY rj.updated_at DESC",
        {"r": str(rdm_id)}, connection="WORKBENCH")
    return row["status_code"] if row is not None else None


def latest_backfill_statuses(rdm_ids: list[Any]) -> dict[str, str | None]:
    """``latest_backfill_status`` for a whole entity table in one query —
    newest ``updated_at`` per RDM reduced app-side. Every requested id gets a
    key; RDMs whose analyses capture never ran map to ``None``."""
    statuses: dict[str, str | None] = {str(r): None for r in rdm_ids}
    if not statuses:
        return statuses
    params = {f"e{i}": value for i, value in enumerate(statuses)}
    placeholders = ", ".join(f":e{i}" for i in range(len(statuses)))
    rows = execute(
        "SELECT rj.status_code, "
        "COALESCE(ij.irp_rdm_id, rj.requestor_id) AS rdm_id "
        "FROM rwb_job rj "
        "LEFT JOIN irp_job ij ON rj.requestor_type = 'irp_job' "
        "AND rj.requestor_id = ij.id "
        "WHERE rj.rwb_job_type = 'backfill_rdm_analyses' "
        f"AND (ij.irp_rdm_id IN ({placeholders}) "
        "     OR (rj.requestor_type = 'analyst_request' "
        f"         AND rj.requestor_id IN ({placeholders}))) "
        "ORDER BY rj.updated_at DESC",
        params, connection="WORKBENCH")
    for row in rows:
        key = str(row["rdm_id"])
        if statuses.get(key) is None:
            statuses[key] = row["status_code"]
    return statuses


def sync_detail(*, rdm_id: Any, actor_id: Any) -> str | None:
    """Analyst-triggered re-run of ``backfill_rdm_analyses`` for one RDM — the
    recovery path for RDMs imported before the settings capture shipped.
    Keyed ``(analyst_request, rdm_id)`` and captures all analyses for the RDM.
    Skips (→ ``None``) when the RDM is missing/deleted,
    the import is still in flight, or a backfill under EITHER key is already
    pending/running."""
    rid = str(rdm_id)
    current = _current(rid)
    if current is None or current["status"] in (PENDING, IMPORTING):
        return None
    if latest_backfill_status(rid) in ("pending", "running"):
        return None
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=rid,
        rwb_job_type="backfill_rdm_analyses",
        input_data={"rdm_id": rid},
        actor_id=str(actor_id),
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="backfill_rdm_analyses")
    return job_id


def get_rdm_detail(rdm_id: Any) -> dict | None:
    """The RDM detail read model — shared by the full page and the live
    #rdm-detail body partial (mirrors ``edm_service.get_edm_detail``). STORED
    detail only, no Risk Modeler call (Article 11): the broker analyses grouped
    by rdm_id + resolved portfolios (US3), plus the newest
    ``backfill_rdm_analyses`` status (either key) driving the Sync button state
    and the self-poll trigger. ``None`` ⇒ the RDM is gone."""
    rdm = get_rdm(rdm_id)
    if rdm is None:
        return None
    sync_status = latest_backfill_status(rdm_id)
    return {"rdm": rdm,
            "analyses": analysis_service.list_broker_analyses(rdm_id=rdm_id),
            "sync_status": sync_status,
            "sync_running": sync_status in ("pending", "running"),
            # Issue #17 backstop surfacing: the failed upload head's specific
            # Risk Modeler message, shown in the error banner when present.
            "import_error": (latest_import_error(rdm_id)
                             if rdm.status == ERROR else None)}


def get_contextual_rdm_detail(
    *, submission_id: Any, rdm_id: Any,
) -> dict | None:
    """Return RDM detail only when the RDM belongs to the named submission."""
    sid = str(submission_id)
    rid = str(rdm_id)
    ctx = _submission_entity_context("rdm", submission_id=sid, entity_id=rid)
    if ctx is None:
        return None
    source, choices = ctx
    detail = get_rdm_detail(rid)
    if detail is None:
        return None
    return {**detail, "source_submission": source, "rdm_choices": choices}


# ── worker / poller status writers ───────────────────────────────────────────────

def mark_importing(*, rdm_id: Any, actor_id: Any | None = None) -> None:
    """Worker-side: an import submit succeeded."""
    _mark_importing("rdm", entity_id=rdm_id, actor_id=actor_id)


def mark_error(*, rdm_id: Any, actor_id: Any | None = None) -> None:
    """Worker-side: mark an RDM whose import submit failed."""
    _mark_error("rdm", entity_id=rdm_id, actor_id=actor_id)


def rollup_on_terminal(conn, *, rdm_id: Any, rm_status: str,
                       irp_id: str | None) -> None:
    """Poller-side terminal status update for one RDM import."""
    rid = str(rdm_id)
    if rm_status != "FINISHED":
        conn.execute(text(
            "UPDATE irp_rdm SET status = :s, updated_at = :now WHERE id = :id"
        ), {"s": ERROR, "now": _utcnow(), "id": rid})
        return
    remaining = conn.execute(text(
        "SELECT COUNT(*) FROM irp_job WHERE irp_rdm_id = :r "
        "AND irp_job_type = 'import_rdm' "
        "AND status NOT IN ('FINISHED', 'FAILED', 'CANCELLED', 'SUBMISSION FAILED')"
    ), {"r": rid}).scalar()
    if remaining and int(remaining) > 0:
        return
    failed = conn.execute(text(
        "SELECT COUNT(*) FROM irp_job WHERE irp_rdm_id = :r "
        "AND irp_job_type = 'import_rdm' "
        "AND status IN ('FAILED', 'CANCELLED', 'SUBMISSION FAILED')"
    ), {"r": rid}).scalar()
    if failed and int(failed) > 0:
        conn.execute(text(
            "UPDATE irp_rdm SET status = :s, updated_at = :now WHERE id = :id"
        ), {"s": ERROR, "now": _utcnow(), "id": rid})
        return
    numeric = int(irp_id) if irp_id is not None else None
    conn.execute(text(
        "UPDATE irp_rdm SET status = :s, "
        "irp_id = CASE WHEN irp_id IS NULL THEN :iid ELSE irp_id END, "
        "created_by_irp_job_irp_id = "
        "  CASE WHEN created_by_irp_job_irp_id IS NULL THEN :cid "
        "       ELSE created_by_irp_job_irp_id END, "
        "updated_at = :now WHERE id = :id"
    ), {"s": READY, "iid": numeric,
        "cid": (str(irp_id) if irp_id is not None else None),
        "now": _utcnow(), "id": rid})


__all__ = [
    "RdmRow", "PENDING", "IMPORTING", "READY", "ERROR", "STATUSES",
    "TRANSIENT_STATUSES",
    "check_name_collision", "import_rdm", "list_rdms", "get_rdm",
    "get_rdm_detail", "get_contextual_rdm_detail", "latest_import_error",
    "retry_import", "replace_source_file", "latest_backfill_status",
    "latest_backfill_statuses", "sync_detail", "mark_importing", "mark_error",
    "rollup_on_terminal",
]
