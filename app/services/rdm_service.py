"""RDM service — import broker results as an ``irp_rdm`` and track it (US2).

Mirrors ``edm_service`` (same request-path discipline, non-blocking collision, no row
scoping) with one shape difference: an RDM apply targets **one or more EDMs**.
``import_rdm`` creates the ``irp_rdm`` (``pending_import``) and enqueues **one**
``upload_rdm`` head; the worker fans it out to one apply per applied EDM. Every apply
targets an EDM — a no-EDM (review-only) import is **rejected** (``EmptyPackageError``);
RDM-only / review-only import is deferred (D3 / FR-016). Broker results are one logical
source across the EDMs it applies to (no per-EDM duplication).

``irp_rdm.status`` is the **combined rollup** of its apply jobs (data-model §6): ``ready``
only once every apply is ``FINISHED``; ``error`` if any apply fails.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import text

from app.services import irp_gateway, rwb_job_service
from app.services.edm_service import ImportResult  # shared DTO
from app.services.errors import ConcurrencyConflict, EmptyPackageError
from app.services.shared_drive import validate_selection
from app.workers import dispatch
from db import execute, execute_command, execute_one

logger = logging.getLogger(__name__)

PENDING = "pending_import"
IMPORTING = "importing"
READY = "ready"
ERROR = "error"
_LOCKED = (IMPORTING, READY)


@dataclass
class RdmRow:
    id: str
    name: str
    status: str | None
    source_file_path: str | None
    irp_id: int | None
    package_id: str | None
    inserted_at: Any
    updated_at: Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _uid(value: Any) -> str | None:
    return None if value is None else str(value).lower()


def check_name_collision(name: str) -> list[str]:
    """Colliding IRP RDM names (empty = clear). Non-blocking (FR-012): best-effort —
    if the gateway can't answer, log and report no collisions rather than raise."""
    trimmed = (name or "").strip()
    if not trimmed:
        return []
    try:
        return [hit.name for hit in irp_gateway.search_rdms(trimmed)]
    except Exception:  # noqa: BLE001 — advisory check must never break the save
        logger.warning("RDM name-collision check skipped (gateway unavailable)",
                       exc_info=True)
        return []


def import_rdm(
    *, name: str, source_file_path: str, package_id: Any | None = None,
    applied_edm_ids: Sequence[Any] = (), actor_id: Any,
) -> ImportResult:
    """Create an ``irp_rdm`` (``pending_import``) and enqueue one ``upload_rdm`` head
    that fans out to one apply per applied EDM. ``applied_edm_ids`` **MUST** be
    non-empty — every apply targets an EDM; a no-EDM (review-only) import is rejected
    with ``EmptyPackageError`` (D3 / FR-016). **No Risk Modeler call here** (FR-042).
    Validates the source (else ``InvalidSourceFile``)."""
    edm_ids = [str(e) for e in applied_edm_ids if e]
    if not edm_ids:
        raise EmptyPackageError(
            "An RDM import must be applied to at least one EDM "
            "(review-only import is deferred).")
    canonical = validate_selection(source_file_path)
    collision = check_name_collision(name)

    rdm_id = str(uuid.uuid4())
    now = _utcnow()
    actor = str(actor_id)
    execute_command(
        """
        INSERT INTO irp_rdm (id, package_id, source_file_path, name, status,
            inserted_at, updated_at, inserted_by, updated_by)
        VALUES (:id, :pkg, :src, :name, :status, :now, :now, :by, :by)
        """,
        {"id": rdm_id, "pkg": (str(package_id) if package_id else None),
         "src": canonical, "name": name.strip(), "status": PENDING,
         "now": now, "by": actor},
        connection="WORKBENCH",
    )
    job_id = rwb_job_service.enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=rdm_id,
        rwb_job_type="upload_rdm",
        input_data={"rdm_ids": [rdm_id], "edm_ids": edm_ids,
                    "package_id": _uid(package_id)},
        actor_id=actor,
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="upload_rdm")
    return ImportResult(entity_id=rdm_id, collision=collision)


_ROW_SELECT = (
    "SELECT id, package_id, source_file_path, name, irp_id, status, "
    "inserted_at, updated_at FROM irp_rdm"
)


def _to_row(row: dict) -> RdmRow:
    return RdmRow(
        id=_uid(row["id"]), name=row["name"], status=row["status"],
        source_file_path=row["source_file_path"], irp_id=row["irp_id"],
        package_id=_uid(row["package_id"]), inserted_at=row["inserted_at"],
        updated_at=row["updated_at"],
    )


def list_rdms(*, package_id: Any | None = None) -> list[RdmRow]:
    """Every RDM (library), or one package's RDMs. NO row scoping (Article 6)."""
    where = "WHERE deleted_at IS NULL"
    params: dict[str, Any] = {}
    if package_id is not None:
        where += " AND package_id = :pid"
        params["pid"] = str(package_id)
    rows = execute(f"{_ROW_SELECT} {where} ORDER BY inserted_at DESC, name",
                   params, connection="WORKBENCH")
    return [_to_row(r) for r in rows]


def get_rdm(rdm_id: Any) -> RdmRow | None:
    row = execute_one(f"{_ROW_SELECT} WHERE id = :id",
                      {"id": str(rdm_id)}, connection="WORKBENCH")
    return _to_row(row) if row is not None else None


def _current(rdm_id: str) -> dict | None:
    return execute_one(
        "SELECT status, updated_at FROM irp_rdm WHERE id = :id AND deleted_at IS NULL",
        {"id": rdm_id}, connection="WORKBENCH")


def retry_import(*, rdm_id: Any, actor_id: Any) -> None:
    """Re-enqueue a single RDM's ``upload_rdm`` head (FR-045). No-op when ready / in
    flight; resets a failed head to ``pending`` otherwise."""
    rid = str(rdm_id)
    current = _current(rid)
    if current is None or current["status"] in _LOCKED:
        return
    edm_ids = [str(r["irp_edm_id"]) for r in execute(
        "SELECT DISTINCT irp_edm_id FROM irp_job "
        "WHERE irp_rdm_id = :r AND irp_job_type = 'import_rdm' "
        "AND irp_edm_id IS NOT NULL", {"r": rid}, connection="WORKBENCH")]
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=rid,
        rwb_job_type="upload_rdm",
        input_data={"rdm_ids": [rid], "edm_ids": edm_ids, "package_id": None},
        actor_id=str(actor_id),
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="upload_rdm")


def replace_source_file(
    *, rdm_id: Any, new_source_file_path: str, expected_updated_at: Any, actor_id: Any,
) -> None:
    """Replace the source file of a failed/errored RDM and re-import (FR-046).
    Optimistic-concurrency checked on ``updated_at`` (FR-039)."""
    rid = str(rdm_id)
    canonical = validate_selection(new_source_file_path)
    rows = execute_command(
        """
        UPDATE irp_rdm
        SET source_file_path = :src, status = :status, updated_at = :now,
            updated_by = :by
        WHERE id = :id AND updated_at = :expected AND deleted_at IS NULL
        """,
        {"src": canonical, "status": PENDING, "now": _utcnow(),
         "by": str(actor_id), "id": rid, "expected": expected_updated_at},
        connection="WORKBENCH",
    )
    if rows == 0:
        raise ConcurrencyConflict(
            "This RDM changed since you opened it — reload and re-apply.")
    edm_ids = [str(r["irp_edm_id"]) for r in execute(
        "SELECT DISTINCT irp_edm_id FROM irp_job "
        "WHERE irp_rdm_id = :r AND irp_job_type = 'import_rdm' "
        "AND irp_edm_id IS NOT NULL", {"r": rid}, connection="WORKBENCH")]
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=rid,
        rwb_job_type="upload_rdm",
        input_data={"rdm_ids": [rid], "edm_ids": edm_ids, "package_id": None},
        actor_id=str(actor_id),
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="upload_rdm")


# ── worker / poller status writers ───────────────────────────────────────────────

def mark_importing(*, rdm_id: Any, actor_id: Any | None = None) -> None:
    """Worker-side: an apply submit succeeded — flip ``pending_import`` → ``importing``."""
    execute_command(
        "UPDATE irp_rdm SET status = :s, updated_at = :now, updated_by = :by "
        "WHERE id = :id AND status = :from_status",
        {"s": IMPORTING, "now": _utcnow(),
         "by": (str(actor_id) if actor_id is not None else None),
         "id": str(rdm_id), "from_status": PENDING},
        connection="WORKBENCH",
    )


def rollup_on_terminal(conn, *, rdm_id: Any, rm_status: str,
                       irp_id: str | None) -> None:
    """Poller-side combined rollup (data-model §6): ``error`` if any apply failed;
    ``ready`` only once **all** applies for this RDM are ``FINISHED``. Backfills
    ``irp_id`` from the first finished apply. Runs in the poller's transaction."""
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
        return  # more applies still in flight — not ready yet
    failed = conn.execute(text(
        "SELECT COUNT(*) FROM irp_job WHERE irp_rdm_id = :r "
        "AND irp_job_type = 'import_rdm' AND status IN ('FAILED', 'CANCELLED')"
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
    "RdmRow", "PENDING", "IMPORTING", "READY", "ERROR",
    "check_name_collision", "import_rdm", "list_rdms", "get_rdm",
    "retry_import", "replace_source_file", "mark_importing", "rollup_on_terminal",
]
