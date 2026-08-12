"""RDM service — import broker results as an ``irp_rdm`` and track it (US2).

Mirrors ``edm_service`` (same request-path discipline, blocking name collision per
issue #17, no row scoping). ``import_rdm`` creates the ``irp_rdm``
(``pending_import``) and enqueues one ``upload_rdm`` head; the worker submits one
standalone import.

The RDM is imported against an exposure set of its own name, never into an EDM —
broker results cannot be tied to an EDM's portfolios reliably. Which EDMs an RDM
belongs with is an app-side fact only, carried by package membership.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy import text

from app.services import (
    analysis_service, name_check, package_service, rwb_job_service)
from app.services._common import _uid, _utcnow
from app.services.edm_service import ImportResult  # shared DTO
from app.services.errors import ConcurrencyConflict, NameCollisionError
from app.services.name_check import CollisionCheck
from app.services.package_service import SubmissionRef
from app.services.shared_drive import validate_selection
from app.workers import dispatch
from db import execute, execute_command, execute_one

logger = logging.getLogger(__name__)

PENDING = "pending_import"
IMPORTING = "importing"
READY = "ready"
ERROR = "error"
_LOCKED = (IMPORTING, READY)
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
    package_id: str | None
    inserted_at: Any
    updated_at: Any
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
    *, name: str, source_file_path: str, package_id: Any | None = None,
    actor_id: Any,
) -> ImportResult:
    """Create an ``irp_rdm`` (``pending_import``) and enqueue one ``upload_rdm`` head.
    The only Risk Modeler call here is the cached name-collision *read* (permitted,
    Article 11); raises ``NameCollisionError`` — before persisting anything — when
    the name already exists there (issue #17). Validates the source (else
    ``InvalidSourceFile``)."""
    name = package_service.clean_member_name(name)     # raises InvalidMemberName
    canonical = validate_selection(source_file_path)
    check = check_name_collision(name)
    if check.collides:
        raise NameCollisionError(
            f"An RDM named '{name}' already exists in Risk Modeler. "
            "Choose a different name.")

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
         "src": canonical, "name": name, "status": PENDING,
         "now": now, "by": actor},
        connection="WORKBENCH",
    )
    job_id = rwb_job_service.enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=rdm_id,
        rwb_job_type="upload_rdm",
        input_data={"rdm_ids": [rdm_id], "package_id": _uid(package_id)},
        actor_id=actor,
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="upload_rdm")
    return ImportResult(entity_id=rdm_id, collision_unchecked=not check.checked)


_ROW_SELECT = (
    "SELECT id, package_id, source_file_path, name, irp_id, status, as_of, "
    "inserted_at, updated_at FROM irp_rdm"
)


def _to_row(row: dict) -> RdmRow:
    return RdmRow(
        id=_uid(row["id"]), name=row["name"], status=row["status"],
        source_file_path=row["source_file_path"], irp_id=row["irp_id"],
        package_id=_uid(row["package_id"]), inserted_at=row["inserted_at"],
        updated_at=row["updated_at"], as_of=row["as_of"],
    )


def list_rdms(*, package_id: Any | None = None, name: str | None = None,
              status: str | None = None) -> list[RdmRow]:
    """Every RDM (library), one package's RDMs, or a filtered slice. NO row scoping
    (Article 6). Soft-deleted rows excluded.

    ``name`` narrows by case-insensitive substring (``LIKE``); ``status`` narrows to
    the exact import status; both combine with AND; blank/``None`` are no-ops (US7 /
    T058). Each row's ``.submissions`` is set to its owning submissions (oldest-first)."""
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


def list_unattached(*, name: str | None = None) -> list[RdmRow]:
    """``ready`` RDMs belonging to no package — the attach picker's RDM candidates
    (issue #22), newest-first. Mirrors ``edm_service.list_unattached``; see there for
    the ``ready``-only rule and for why this is not expressible as
    ``list_rdms(package_id=…)``."""
    where = ("WHERE deleted_at IS NULL AND package_id IS NULL AND status = :ready")
    params: dict[str, Any] = {"ready": READY}
    if name:
        where += " AND name LIKE :q"
        params["q"] = f"%{name}%"
    return [_to_row(r) for r in execute(
        f"{_ROW_SELECT} {where} ORDER BY inserted_at DESC, name",
        params, connection="WORKBENCH")]


def get_rdms_by_ids(rdm_ids: Sequence[Any]) -> list[RdmRow]:
    """Live RDMs for an explicit id list, newest-first — the picker tray's labels
    (issue #22). Mirrors ``edm_service.get_edms_by_ids``: generated placeholder names,
    bound values, unknown ids simply absent."""
    ids = list(dict.fromkeys(str(r) for r in rdm_ids if r))
    if not ids:
        return []
    params = {f"r{i}": rid for i, rid in enumerate(ids)}
    placeholders = ", ".join(f":{k}" for k in params)
    return [_to_row(r) for r in execute(
        f"{_ROW_SELECT} WHERE deleted_at IS NULL AND id IN ({placeholders}) "
        "ORDER BY inserted_at DESC, name", params, connection="WORKBENCH")]


def _attach_submissions(rows: list[RdmRow]) -> None:
    """Set each row's ``.submissions`` from the M:N ``submission_package`` join
    (oldest-first). One query for the whole page; standalone rows keep the default []."""
    package_ids = [r.package_id for r in rows if r.package_id]
    if not package_ids:
        return
    refs = package_service.submission_refs_for_packages(package_ids)
    for row in rows:
        if row.package_id:
            row.submissions = refs.get(row.package_id, [])


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


def _package_id(rdm_id: str) -> str | None:
    """The RDM's owning package (if any). A retry/replace re-enqueue MUST carry this so
    the new ``import_rdm`` job — and the ``irp_analysis`` rows its backfill captures,
    which copy ``package_id`` from that job — are stamped with the package."""
    row = execute_one("SELECT package_id FROM irp_rdm WHERE id = :id",
                      {"id": rdm_id}, connection="WORKBENCH")
    return str(row["package_id"]) if row and row["package_id"] else None


def retry_import(*, rdm_id: Any, actor_id: Any) -> None:
    """Re-enqueue a single RDM's ``upload_rdm`` head (FR-045). No-op when ready / in
    flight; otherwise resets an ``error`` entity back to ``pending_import`` **and** the
    head to ``pending`` so the worker re-submits (the body only advances a
    ``pending_import`` row)."""
    rid = str(rdm_id)
    current = _current(rid)
    if current is None or current["status"] in _LOCKED:
        return
    execute_command(
        "UPDATE irp_rdm SET status = :p, updated_at = :now, updated_by = :by "
        "WHERE id = :id AND status = :err",
        {"p": PENDING, "err": ERROR, "now": _utcnow(), "by": str(actor_id), "id": rid},
        connection="WORKBENCH",
    )
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=rid,
        rwb_job_type="upload_rdm",
        input_data={"rdm_ids": [rid], "package_id": _package_id(rid)},
        actor_id=str(actor_id),
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="upload_rdm")
    logger.info("rdm %s import retry requested by analyst %s", rid, actor_id)


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
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=rid,
        rwb_job_type="upload_rdm",
        input_data={"rdm_ids": [rid], "package_id": _package_id(rid)},
        actor_id=str(actor_id),
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="upload_rdm")
    logger.info("rdm %s source file replaced by analyst %s — re-import enqueued",
                rid, actor_id)


# ── manual analysis-details sync (spec 004 follow-up, 2026-07-24) ─────────────────

def latest_backfill_status(rdm_id: Any) -> str | None:
    """The newest ``backfill_rdm_analyses`` job status touching this RDM across
    BOTH enqueue sources: the poller's head keys on the finished ``import_rdm``
    irp_job (hence the join), the manual Sync's on ``(analyst_request, rdm_id)``
    directly. Newest ``updated_at`` wins — a
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


def sync_detail(*, rdm_id: Any, actor_id: Any) -> str | None:
    """Analyst-triggered re-run of ``backfill_rdm_analyses`` for one RDM — the
    recovery path for RDMs imported before the settings/pointer capture shipped
    (the automatic backfill runs only when an ``import_rdm`` FINISHES, so older rows
    stay name-only forever without this). Keyed ``(analyst_request, rdm_id)``. Skips
    (→ ``None``) when the RDM is missing/deleted, the import is still in flight, or
    a backfill under EITHER key is already pending/running."""
    rid = str(rdm_id)
    current = _current(rid)
    if current is None or current["status"] in (PENDING, IMPORTING):
        return None
    if latest_backfill_status(rid) in ("pending", "running"):
        return None
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=rid,
        rwb_job_type="backfill_rdm_analyses",
        input_data={"rdm_id": rid, "package_id": _package_id(rid)},
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


def sync_analyses_for_edm(*, edm_id: Any, actor_id: Any) -> list[str]:
    """The EDM detail page shows RDM-sourced analyses too, so its Sync refreshes
    both: one ``sync_detail`` per RDM in this EDM's package — the same membership
    the page lists them by (``analysis_service.list_edm_analyses``). Per-RDM guards
    apply — an in-flight or still-importing RDM is skipped, never stacked. Returns
    the enqueued ids."""
    rdm_ids = [str(r["id"]) for r in execute(
        "SELECT r.id FROM irp_rdm r "
        "JOIN irp_edm e ON e.package_id = r.package_id "
        "WHERE e.id = :e AND r.deleted_at IS NULL",
        {"e": str(edm_id)}, connection="WORKBENCH")]
    jobs: list[str] = []
    for rid in rdm_ids:
        job_id = sync_detail(rdm_id=rid, actor_id=actor_id)
        if job_id is not None:
            jobs.append(job_id)
    return jobs


# ── worker / poller status writers ───────────────────────────────────────────────

def mark_importing(*, rdm_id: Any, actor_id: Any | None = None) -> None:
    """Worker-side: the import submit succeeded — flip ``pending_import`` → ``importing``."""
    execute_command(
        "UPDATE irp_rdm SET status = :s, updated_at = :now, updated_by = :by "
        "WHERE id = :id AND status = :from_status",
        {"s": IMPORTING, "now": _utcnow(),
         "by": (str(actor_id) if actor_id is not None else None),
         "id": str(rdm_id), "from_status": PENDING},
        connection="WORKBENCH",
    )


def mark_error(*, rdm_id: Any, actor_id: Any | None = None) -> None:
    """Worker-side: a **submit-side** import failure (never reached Risk Modeler) — flip
    the RDM to ``error``. Only touches ``pending_import``/``importing`` so it never
    clobbers a delete; idempotent."""
    execute_command(
        "UPDATE irp_rdm SET status = :s, updated_at = :now, updated_by = :by "
        "WHERE id = :id AND status IN (:p, :i)",
        {"s": ERROR, "now": _utcnow(),
         "by": (str(actor_id) if actor_id is not None else None),
         "id": str(rdm_id), "p": PENDING, "i": IMPORTING},
        connection="WORKBENCH",
    )


def rollup_on_terminal(conn, *, rdm_id: Any, rm_status: str,
                       irp_id: str | None) -> None:
    """The status rollup (data-model §6): ``error`` if the import failed, ``ready``
    once it is ``FINISHED``. Called by the worker on ``FINISHED`` (with the captured
    analyses, in one transaction) and by the poller on any other terminal status.
    Reads the status of the import the caller is handling —
    one RDM means one import, so a superseded ``SUBMISSION FAILED`` row from an earlier
    attempt must not drag a successful re-import back to ``error`` (issue #38).
    Backfills ``irp_id`` from the finished import. Runs in the caller's transaction."""
    rid = str(rdm_id)
    if rm_status != "FINISHED":
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
    "get_rdm_detail", "latest_import_error",
    "retry_import", "replace_source_file", "latest_backfill_status",
    "sync_detail", "sync_analyses_for_edm", "mark_importing", "mark_error",
    "rollup_on_terminal",
]
