"""EDM service — import an exposure file as an ``irp_edm`` and track it (US1).

Every Risk Modeler call is deferred to a worker (Article 11 / FR-042): ``import_edm``
creates the ``irp_edm`` (``status='pending_import'``) and enqueues one ``upload_edm``
head — **no gateway call on the request path**. The worker submits; the poller
mirrors status and flips the entity to ``ready``/``error`` (worker-poller.md).

Name collision is a **non-blocking warning** (FR-012 / R8): ``check_name_collision``
returns colliding IRP names and never raises. No function applies row scoping — every
analyst sees every EDM (Article 6 / FR-037).

Portability matches ``submission_service`` / ``package_service``: app-side UUIDs bound
as ``str``, app-supplied UTC timestamps, no dialect-only SQL.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services import (
    irp_gateway, package_service, portfolio_service, rwb_job_service, treaty_service)
from app.services._common import _uid, _utcnow
from app.services.errors import ConcurrencyConflict
from app.services.package_service import SubmissionRef
from app.services.portfolio_service import PortfolioRow
from app.services.treaty_service import TreatyRow
from app.services.shared_drive import validate_selection
from app.workers import dispatch
from db import execute, execute_command, execute_one

logger = logging.getLogger(__name__)

# Entity-status lifecycle (plain string — Article 3 carve-out): pending_import →
# importing → ready / error → delete_pending → deleted (data-model §6).
PENDING = "pending_import"
IMPORTING = "importing"
READY = "ready"
ERROR = "error"
DELETE_PENDING = "delete_pending"
DELETED = "deleted"
# statuses from which a (re)import must NOT be launched (in flight or already done).
_LOCKED = (IMPORTING, READY)
# Ordered status vocabulary offered as the library status filter (US7 / T058).
STATUSES = (PENDING, IMPORTING, READY, ERROR, DELETE_PENDING, DELETED)


@dataclass
class ImportResult:
    """The id of the created entity plus any non-blocking name-collision warning."""
    entity_id: str
    collision: list[str] = field(default_factory=list)


@dataclass
class EdmRow:
    id: str
    name: str
    status: str | None
    source_file_path: str | None
    irp_id: int | None
    package_id: str | None
    inserted_at: Any
    updated_at: Any
    # Owning submissions (M:N), oldest-first — populated only by ``list_edms``;
    # defaulted so ``get_edm`` and every existing caller are unaffected (US7 / T058).
    submissions: list[SubmissionRef] = field(default_factory=list)


def check_name_collision(name: str) -> list[str]:
    """Colliding IRP EDM names for ``name`` (empty = clear). Non-blocking (FR-012):
    the caller renders a warning, nothing is ever raised. If the gateway can't answer
    (IRP unavailable, or the search not yet wired) the check is best-effort — we log
    and report no collisions rather than fail the caller's save."""
    trimmed = (name or "").strip()
    if not trimmed:
        return []
    try:
        return [hit.name for hit in irp_gateway.search_edms(trimmed)]
    except Exception:  # noqa: BLE001 — advisory check must never break the save
        logger.warning("EDM name-collision check skipped (gateway unavailable)",
                       exc_info=True)
        return []


def import_edm(
    *, name: str, source_file_path: str, package_id: Any | None = None,
    actor_id: Any,
) -> ImportResult:
    """Create an ``irp_edm`` (``pending_import``) and enqueue one ``upload_edm`` head
    (``requestor_type='analyst_request'``, ``requestor_id=irp_edm.id``). The worker
    performs the submit — **no Risk Modeler call here** (FR-042). Validates the source
    is within ``SHARED_DRIVE_ROOT`` and is a file (else ``InvalidSourceFile``). Returns
    the new id alongside the non-blocking collision warning."""
    name = package_service.clean_member_name(name)     # raises InvalidMemberName
    canonical = validate_selection(source_file_path)   # raises InvalidSourceFile
    collision = check_name_collision(name)

    edm_id = str(uuid.uuid4())
    now = _utcnow()
    actor = str(actor_id)
    execute_command(
        """
        INSERT INTO irp_edm (id, package_id, source_file_path, name, status,
            inserted_at, updated_at, inserted_by, updated_by)
        VALUES (:id, :pkg, :src, :name, :status, :now, :now, :by, :by)
        """,
        {"id": edm_id, "pkg": (str(package_id) if package_id else None),
         "src": canonical, "name": name, "status": PENDING,
         "now": now, "by": actor},
        connection="WORKBENCH",
    )
    job_id = rwb_job_service.enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=edm_id,
        rwb_job_type="upload_edm",
        input_data={"edm_id": edm_id, "package_id": _uid(package_id)},
        actor_id=actor,
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="upload_edm")
    return ImportResult(entity_id=edm_id, collision=collision)


def _to_row(row: dict) -> EdmRow:
    return EdmRow(
        id=_uid(row["id"]),
        name=row["name"],
        status=row["status"],
        source_file_path=row["source_file_path"],
        irp_id=row["irp_id"],
        package_id=_uid(row["package_id"]),
        inserted_at=row["inserted_at"],
        updated_at=row["updated_at"],
    )


_ROW_SELECT = (
    "SELECT id, package_id, source_file_path, name, irp_id, status, "
    "inserted_at, updated_at FROM irp_edm"
)


def list_edms(*, package_id: Any | None = None, name: str | None = None,
              status: str | None = None) -> list[EdmRow]:
    """Every EDM (library), one package's EDMs, or a filtered slice. NO row scoping
    (FR-037 / Article 6) — all analysts see all EDMs. Soft-deleted rows excluded.

    ``name`` narrows by case-insensitive substring (``LIKE`` — case-insensitive on
    SQL Server's default collation and on SQLite for ASCII); ``status`` narrows to the
    exact import status; both combine with AND; blank/``None`` are no-ops (US7 / T058).
    Each returned row's ``.submissions`` is set to its owning submissions (oldest-first)."""
    where = "WHERE deleted_at IS NULL"
    params: dict[str, Any] = {}
    if package_id is not None:
        where += " AND package_id = :pid"
        params["pid"] = str(package_id)
    if name:
        where += " AND name LIKE :q"
        params["q"] = f"%{name}%"
    if status:
        where += " AND status = :status"
        params["status"] = status
    rows = execute(f"{_ROW_SELECT} {where} ORDER BY inserted_at DESC, name",
                   params, connection="WORKBENCH")
    result = [_to_row(r) for r in rows]
    _attach_submissions(result)
    return result


def _attach_submissions(rows: list[EdmRow]) -> None:
    """Set each row's ``.submissions`` from the M:N ``submission_package`` join
    (oldest-first). One query for the whole page; standalone rows keep the default []."""
    package_ids = [r.package_id for r in rows if r.package_id]
    if not package_ids:
        return
    refs = package_service.submission_refs_for_packages(package_ids)
    for row in rows:
        if row.package_id:
            row.submissions = refs.get(row.package_id, [])


def get_edm(edm_id: Any) -> EdmRow | None:
    row = execute_one(f"{_ROW_SELECT} WHERE id = :id",
                      {"id": str(edm_id)}, connection="WORKBENCH")
    return _to_row(row) if row is not None else None


# ── the redesigned detail page's single read (spec 004 US1 — R6) ─────────────────

@dataclass
class EdmDetail:
    """The redesigned EDM detail page payload: a light header (FR-011 — MUST NOT
    include cedant or line of business) + the per-portfolio read model (US1's
    primary content) + the section state. US2/US3/US4 extend this with treaties,
    analyses, and the derived aggregate."""
    id: str
    name: str
    status: str | None
    as_of: Any                      # last-synced trust signal (FR-052)
    source_file_path: str | None
    irp_id: int | None              # RM exposureId (durable entity id)
    created_by_irp_job_irp_id: str | None
    package_id: str | None
    inserted_at: Any
    updated_at: Any
    portfolio_count: int
    portfolios: list[PortfolioRow]
    # 'populated' | 'importing' | 'pending' | 'failed' | 'empty' | 'unavailable'
    detail_state: str
    # a backfill head (either key) is pending/running — drives the "Syncing…"
    # button state even when the table is already populated
    sync_running: bool = False
    # US2: the EDM-level treaty set (parsed attributes) for the expand/collapse
    # view + Excel export; empty list ⇒ the section renders its own state.
    treaties: list[TreatyRow] = field(default_factory=list)


def _latest_backfill_status(edm_id: str) -> str | None:
    """The newest ``backfill_edm_detail`` job status for this EDM across BOTH
    enqueue sources: the poller's heads key on the finished ``import_edm``
    irp_job (hence the join), the manual Sync's key on ``(analyst_request,
    edm_id)`` directly. Newest ``updated_at`` wins — a revived (re-synced) row
    keeps its ``inserted_at``, so insert order would lie. ``None`` when detail
    backfill never ran — the pre-capability / forward-only state."""
    row = execute_one(
        "SELECT rj.status_code FROM rwb_job rj "
        "LEFT JOIN irp_job ij ON rj.requestor_type = 'irp_job' "
        "AND rj.requestor_id = ij.id "
        "WHERE rj.rwb_job_type = 'backfill_edm_detail' "
        "AND (ij.irp_edm_id = :e "
        "     OR (rj.requestor_type = 'analyst_request' AND rj.requestor_id = :e)) "
        "ORDER BY rj.updated_at DESC",
        {"e": edm_id}, connection="WORKBENCH")
    return row["status_code"] if row is not None else None


def _detail_state(status: str | None, as_of: Any,
                  portfolios: list[PortfolioRow], job_status: str | None) -> str:
    """Which graceful section state the page renders (ui.md §5) — never an error.
    ``empty`` (a real zero-portfolio EDM, FR-015) is distinguished from
    ``unavailable`` by the ``as_of`` stamp: the worker stamps it only after a real
    enumeration, so a succeeded-as-skip run (no exposureId) stays unavailable."""
    if status in (PENDING, IMPORTING):
        return "importing"
    if portfolios:
        return "populated"
    if job_status in ("pending", "running"):
        return "pending"
    if job_status == "failed":
        return "failed"
    if job_status == "succeeded" and as_of is not None:
        return "empty"
    return "unavailable"


def get_edm_detail(edm_id: Any) -> EdmDetail | None:
    """The redesigned EDM detail page's single read (contracts/data-access.md):
    light header from the existing ``irp_edm`` columns + every portfolio with its
    parsed snapshot (graceful empty when none). ``None`` only if the EDM itself
    is missing (→ router 404). ``get_edm`` stays unchanged for the worker and
    recovery paths."""
    eid = str(edm_id)
    row = execute_one(
        "SELECT id, package_id, source_file_path, name, irp_id, "
        "created_by_irp_job_irp_id, as_of, status, inserted_at, updated_at "
        "FROM irp_edm WHERE id = :id",
        {"id": eid}, connection="WORKBENCH")
    if row is None:
        return None
    portfolios = portfolio_service.list_portfolios(edm_id=eid)
    treaties = treaty_service.list_treaties(edm_id=eid)
    job_status = _latest_backfill_status(eid)
    return EdmDetail(
        id=_uid(row["id"]),
        name=row["name"],
        status=row["status"],
        as_of=row["as_of"],
        source_file_path=row["source_file_path"],
        irp_id=row["irp_id"],
        created_by_irp_job_irp_id=row["created_by_irp_job_irp_id"],
        package_id=_uid(row["package_id"]),
        inserted_at=row["inserted_at"],
        updated_at=row["updated_at"],
        portfolio_count=len(portfolios),
        portfolios=portfolios,
        detail_state=_detail_state(row["status"], row["as_of"], portfolios,
                                   job_status),
        sync_running=job_status in ("pending", "running"),
        treaties=treaties,
    )


def sync_detail(*, edm_id: Any, actor_id: Any) -> str | None:
    """Analyst-triggered re-run of ``backfill_edm_detail`` for one EDM (FR-003 as
    amended 2026-07-23) — the recovery path for pre-capability EDMs and failed
    fetches; its scope grows with the worker (portfolios now, treaties with US2).
    Keyed ``(analyst_request, edm_id)`` so it works for EVERY EDM, including those
    with no FINISHED import irp_job; ``ensure_pending_rwb_job`` revives a terminal
    head in place. Skips (→ ``None``) when the EDM is missing/deleted, the import
    is still in flight, or a backfill head under EITHER key is pending/running."""
    eid = str(edm_id)
    current = _current(eid)
    if current is None or current["status"] in (PENDING, IMPORTING):
        return None
    if _latest_backfill_status(eid) in ("pending", "running"):
        return None
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=eid,
        rwb_job_type="backfill_edm_detail",
        input_data={"edm_id": eid, "package_id": _package_id(eid)},
        actor_id=str(actor_id),
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="backfill_edm_detail")
    return job_id


def _current(edm_id: str) -> dict | None:
    return execute_one(
        "SELECT status, updated_at FROM irp_edm WHERE id = :id AND deleted_at IS NULL",
        {"id": edm_id}, connection="WORKBENCH")


def _package_id(edm_id: str) -> str | None:
    """The EDM's owning package (if any). A retry/replace re-enqueue MUST carry this so
    the resulting ``import_edm`` job stays package-scoped — the poller chains the RDM
    applies off ``job.package_id`` (``upload_rdm`` on FINISHED), and a null there silently
    severs the EDM→RDM chain (no ``upload_rdm``, no ``import_rdm``)."""
    row = execute_one("SELECT package_id FROM irp_edm WHERE id = :id",
                      {"id": edm_id}, connection="WORKBENCH")
    return str(row["package_id"]) if row and row["package_id"] else None


def retry_import(*, edm_id: Any, actor_id: Any) -> None:
    """Re-enqueue a single EDM's ``upload_edm`` head (FR-045). Idempotent: a no-op
    when the EDM is already ``ready`` or in flight (``importing``); otherwise resets an
    ``error`` entity back to ``pending_import`` **and** the head back to ``pending`` so
    the worker re-submits (the body only advances a ``pending_import`` row, so the
    entity reset is required for the resubmit to actually fire)."""
    eid = str(edm_id)
    current = _current(eid)
    if current is None or current["status"] in _LOCKED:
        return
    execute_command(
        "UPDATE irp_edm SET status = :p, updated_at = :now, updated_by = :by "
        "WHERE id = :id AND status = :err",
        {"p": PENDING, "err": ERROR, "now": _utcnow(), "by": str(actor_id), "id": eid},
        connection="WORKBENCH",
    )
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=eid,
        rwb_job_type="upload_edm",
        input_data={"edm_id": eid, "package_id": _package_id(eid)},
        actor_id=str(actor_id),
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="upload_edm")


def replace_source_file(
    *, edm_id: Any, new_source_file_path: str, expected_updated_at: Any,
    actor_id: Any,
) -> None:
    """Replace the source file of a failed/errored EDM and re-import (FR-046).
    Optimistic-concurrency checked on ``updated_at`` (FR-039). Validates the new path."""
    eid = str(edm_id)
    canonical = validate_selection(new_source_file_path)  # raises InvalidSourceFile
    rows = execute_command(
        """
        UPDATE irp_edm
        SET source_file_path = :src, status = :status, updated_at = :now,
            updated_by = :by
        WHERE id = :id AND updated_at = :expected AND deleted_at IS NULL
        """,
        {"src": canonical, "status": PENDING, "now": _utcnow(),
         "by": str(actor_id), "id": eid, "expected": expected_updated_at},
        connection="WORKBENCH",
    )
    if rows == 0:
        raise ConcurrencyConflict(
            "This EDM changed since you opened it — reload and re-apply.")
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=eid,
        rwb_job_type="upload_edm",
        input_data={"edm_id": eid, "package_id": _package_id(eid)},
        actor_id=str(actor_id),
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="upload_edm")


# ── worker / poller status writers (Article 11 boundary) ─────────────────────────

def mark_importing(*, edm_id: Any, actor_id: Any | None = None) -> None:
    """Worker-side: the import submit succeeded — flip ``pending_import`` → ``importing``
    (FR-004). Left alone if the row was already advanced (idempotent re-run)."""
    execute_command(
        "UPDATE irp_edm SET status = :s, updated_at = :now, updated_by = :by "
        "WHERE id = :id AND status = :from_status",
        {"s": IMPORTING, "now": _utcnow(),
         "by": (str(actor_id) if actor_id is not None else None),
         "id": str(edm_id), "from_status": PENDING},
        connection="WORKBENCH",
    )


def mark_error(*, edm_id: Any, actor_id: Any | None = None) -> None:
    """Worker-side: a **submit-side** failure (never reached Risk Modeler) — flip an
    import-in-progress EDM to the visible, analyst-recoverable ``error`` state, the same
    state the poller uses for an RM-side terminal failure (worker-poller.md §3). Only
    touches ``pending_import``/``importing`` so it never clobbers ``ready`` or a delete
    (``delete_pending``/``deleted``); idempotent on re-run."""
    execute_command(
        "UPDATE irp_edm SET status = :s, updated_at = :now, updated_by = :by "
        "WHERE id = :id AND status IN (:p, :i)",
        {"s": ERROR, "now": _utcnow(),
         "by": (str(actor_id) if actor_id is not None else None),
         "id": str(edm_id), "p": PENDING, "i": IMPORTING},
        connection="WORKBENCH",
    )


def backfill_on_terminal(conn, *, edm_id: Any, status: str,
                         irp_id: str | None,
                         created_by_irp_job_irp_id: str | None = None) -> None:
    """Poller-side: on the import job's terminal status, flip the entity to
    ``ready``/``error`` and (on ready) backfill two *distinct* identifiers (FR-006):
    ``irp_id`` = the durable RM **entity id** (the EDM's ``exposureId``, resolved by
    name — NOT the import job id; see the ``irp_gateway`` caveat), which delete later
    uses to remove the exposure; ``created_by_irp_job_irp_id`` = the **import job's**
    ``irp_id`` (audit / lineage). Runs inside the poller's transaction (accepts ``conn``)."""
    from sqlalchemy import text  # noqa: PLC0415 — local: keep module import surface small
    ready = status == READY
    numeric = int(irp_id) if (ready and irp_id is not None) else None
    conn.execute(text(
        """
        UPDATE irp_edm
        SET status = :s, irp_id = :iid, created_by_irp_job_irp_id = :cid,
            updated_at = :now
        WHERE id = :id
        """
    ), {"s": status, "iid": numeric,
        "cid": (str(created_by_irp_job_irp_id)
                if ready and created_by_irp_job_irp_id is not None else None),
        "now": _utcnow(), "id": str(edm_id)})


def claim_for_delete(*, edm_id: Any) -> bool:
    """Worker-side atomic guard (worker-poller.md §2): flip to ``delete_pending`` iff
    not already deleting/deleted. ``False`` (rowcount 0) ⇒ another worker owns it."""
    rows = execute_command(
        "UPDATE irp_edm SET status = :s, updated_at = :now "
        "WHERE id = :id AND status NOT IN (:dp, :d)",
        {"s": DELETE_PENDING, "now": _utcnow(), "id": str(edm_id),
         "dp": DELETE_PENDING, "d": DELETED},
        connection="WORKBENCH",
    )
    return rows == 1


def set_deleted(conn, *, edm_id: Any) -> None:
    """Poller-side: the delete_edm job reached FINISHED — mark the EDM ``deleted``
    (the entity soft-delete happens at package finalize). Runs in the poller's txn."""
    from sqlalchemy import text  # noqa: PLC0415
    conn.execute(text(
        "UPDATE irp_edm SET status = :s, updated_at = :now WHERE id = :id"
    ), {"s": DELETED, "now": _utcnow(), "id": str(edm_id)})


def mark_delete_error(conn, *, edm_id: Any) -> None:
    """Poller-side: a delete_edm job reached a non-FINISHED terminal — flip the EDM to
    the recoverable ``error`` state but **preserve ``irp_id``** (the RM exposureId).
    Unlike ``backfill_on_terminal`` (an import writer that nulls ``irp_id`` on a
    non-ready terminal), delete must keep the exposureId so a re-triggered delete still
    calls ``submit_delete_edm`` instead of the "never imported" inline branch (which
    would orphan the exposure in Risk Modeler). Runs in the poller's txn."""
    from sqlalchemy import text  # noqa: PLC0415
    conn.execute(text(
        "UPDATE irp_edm SET status = :s, updated_at = :now WHERE id = :id"
    ), {"s": ERROR, "now": _utcnow(), "id": str(edm_id)})


__all__ = [
    "ImportResult", "EdmRow", "EdmDetail", "PENDING", "IMPORTING", "READY", "ERROR",
    "DELETE_PENDING", "DELETED", "STATUSES",
    "check_name_collision", "import_edm", "list_edms", "get_edm", "get_edm_detail",
    "sync_detail",
    "retry_import", "replace_source_file", "mark_importing", "mark_error",
    "backfill_on_terminal", "claim_for_delete", "set_deleted", "mark_delete_error",
]
