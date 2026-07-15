"""Package sync service — assemble a package and sync it to Risk Modeler (US3).

Builds on the Iteration-1 ``package_service`` (structure) and the US1/US2 entity
services. Two request-path operations, both **non-blocking** (FR-042 / SC-014):

  • ``save_package`` — persist the package + its member entities (each ``pending_import``)
    and run the per-member collision check. **Submits nothing.** ≥1-member invariant
    (``EmptyPackageError``); optimistic concurrency on edit (``ConcurrencyConflict``).
  • ``save_and_sync`` — record the pending work and **return immediately**. Enqueues one
    ``upload_edm`` head per EDM; the poller chains one ``upload_rdm`` per finished EDM,
    fanning out to one apply per (EDM × RDM) pair. **Every apply targets an EDM (D3):**
    an RDM-only package (no EDM) is rejected with ``EmptyPackageError`` — review-only
    sync is deferred. Idempotent: re-sync skips ready/in-flight members and re-enqueues
    only unstarted/errored ones.

``delete_package`` / ``get_package_cards`` are added in US4 / US5.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import text

from app.services import edm_service, rdm_service, rwb_job_service
from app.services.errors import ConcurrencyConflict, EmptyPackageError
from app.services.package_service import clean_member_name
from app.services.shared_drive import validate_selection
from app.workers import dispatch
from db import execute, execute_command, execute_one, get_connection

# entity statuses that mean "in flight or done" — a re-sync leaves these alone.
_LOCKED = ("importing", "ready")


@contextmanager
def _txn(conn):
    """Reuse the caller's transaction (poller) or open our own (worker)."""
    if conn is not None:
        yield conn
    else:
        with get_connection("WORKBENCH") as owned:
            with owned.begin():
                yield owned


@dataclass
class MemberSpec:
    kind: str            # 'edm' | 'rdm'
    name: str
    source_file_path: str


@dataclass
class MemberCollision:
    kind: str
    name: str
    collision: list[str]


@dataclass
class SaveResult:
    package_id: str
    warnings: list[MemberCollision] = field(default_factory=list)


@dataclass
class MemberCard:
    id: str
    kind: str            # 'edm' | 'rdm'
    name: str
    status: str | None
    source_file_path: str | None


@dataclass
class PackageCard:
    """Per-package card data for the submission detail. Basic in US3 (members + their
    status chips); US5 enriches it with upload progress + all/active/failed job counts
    and leaves portfolio/analysis areas empty (R13). No rolled-up package status
    (FR-018) — each member carries its own."""
    id: str
    name: str | None
    edms: list[MemberCard] = field(default_factory=list)
    rdms: list[MemberCard] = field(default_factory=list)
    deleted_at: Any = None
    job_counts: Any = None  # set by US5 get_package_cards


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _live_members(package_id: str, table: str) -> list[dict]:
    return [dict(r) for r in execute(
        f"SELECT id, name, status, source_file_path FROM {table} "
        "WHERE package_id = :p AND deleted_at IS NULL ORDER BY inserted_at, id",
        {"p": package_id}, connection="WORKBENCH")]


def save_package(
    *, package_id: Any | None, name: str | None, members: Sequence[MemberSpec],
    actor_id: Any, expected_updated_at: Any = None,
) -> SaveResult:
    """Persist the package + its member entities; run the per-member collision check;
    submit nothing (FR-013/FR-014). ≥1-member invariant (``EmptyPackageError``);
    optimistic concurrency on edit (``ConcurrencyConflict``). Returns per-member
    non-blocking collision warnings."""
    specs = list(members)
    for spec in specs:
        validate_selection(spec.source_file_path)   # raises InvalidSourceFile
        spec.name = clean_member_name(spec.name)     # raises InvalidMemberName

    now = _utcnow()
    actor = str(actor_id)
    creating = package_id is None
    if creating and not specs:
        raise EmptyPackageError("A package must have at least one member.")

    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            if creating:
                pid = str(uuid.uuid4())
                conn.execute(text(
                    "INSERT INTO package (id, name, inserted_at, updated_at, "
                    "inserted_by, updated_by) VALUES (:id, :n, :now, :now, :by, :by)"
                ), {"id": pid, "n": name, "now": now, "by": actor})
            else:
                pid = str(package_id)
                rows = conn.execute(text(
                    "UPDATE package SET name = :n, updated_at = :now, updated_by = :by "
                    "WHERE id = :id AND deleted_at IS NULL"
                    + (" AND updated_at = :expected" if expected_updated_at is not None
                       else "")
                ), {"n": name, "now": now, "by": actor, "id": pid,
                    "expected": expected_updated_at}).rowcount
                if rows == 0:
                    raise ConcurrencyConflict(
                        "This package changed since you opened it — reload and re-apply.")
            for spec in specs:
                table = "irp_edm" if spec.kind == "edm" else "irp_rdm"
                conn.execute(text(
                    f"INSERT INTO {table} (id, package_id, source_file_path, name, "
                    "status, inserted_at, updated_at, inserted_by, updated_by) "
                    "VALUES (:id, :p, :src, :n, 'pending_import', :now, :now, :by, :by)"
                ), {"id": str(uuid.uuid4()), "p": pid,
                    "src": validate_selection(spec.source_file_path),
                    "n": spec.name.strip(), "now": now, "by": actor})

    if _member_count(pid) == 0:
        raise EmptyPackageError("A package must have at least one member.")

    # Collision check (outside the txn — it reaches the gateway). Non-blocking (R8).
    warnings: list[MemberCollision] = []
    for spec in specs:
        hits = (edm_service.check_name_collision(spec.name) if spec.kind == "edm"
                else rdm_service.check_name_collision(spec.name))
        if hits:
            warnings.append(MemberCollision(kind=spec.kind, name=spec.name,
                                            collision=hits))
    return SaveResult(package_id=pid, warnings=warnings)


def _member_count(package_id: str) -> int:
    from app.services.package_service import package_member_count  # noqa: PLC0415
    return package_member_count(package_id)


def save_and_sync(*, package_id: Any, actor_id: Any) -> None:
    """Record the pending work and return immediately (FR-015/FR-042/FR-044). No Risk
    Modeler call here. **Every apply targets an EDM (D3):** an RDM-only package (no EDM)
    is rejected with ``EmptyPackageError`` — review-only sync is deferred; this also
    covers the empty-package case. Idempotent on the dedup key (re-sync skips
    ready/in-flight)."""
    pid = str(package_id)
    actor = str(actor_id)
    edms = _live_members(pid, "irp_edm")
    rdms = _live_members(pid, "irp_rdm")
    if not edms:
        raise EmptyPackageError(
            "A package must include at least one EDM to sync "
            "(RDM-only / review-only sync is deferred).")

    rdm_ids = [str(r["id"]) for r in rdms]

    for edm in edms:
        edm_id = str(edm["id"])
        if edm["status"] not in _LOCKED:
            # pending/errored EDM → (re)enqueue its import; the poller chains the
            # per-pair RDM applies once it FINISHES. Reset an errored EDM back to
            # pending_import first — the worker body only advances a pending_import row,
            # so without this an error → re-sync would be skipped by the worker.
            if edm["status"] == edm_service.ERROR:
                execute_command(
                    "UPDATE irp_edm SET status = :p, updated_at = :now, updated_by = :by "
                    "WHERE id = :id AND status = :err",
                    {"p": edm_service.PENDING, "err": edm_service.ERROR,
                     "now": _utcnow(), "by": actor, "id": edm_id},
                    connection="WORKBENCH")
            job_id = rwb_job_service.ensure_pending_rwb_job(
                requestor_type="analyst_request", requestor_id=edm_id,
                rwb_job_type="upload_edm",
                input_data={"edm_id": edm_id, "package_id": pid}, actor_id=actor)
            dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="upload_edm")
        elif edm["status"] == "ready" and rdm_ids:
            # EDM already imported (e.g. RDMs added after) → apply the package RDMs
            # to it directly; the body is idempotent per (EDM, RDM) pair.
            job_id = rwb_job_service.ensure_pending_rwb_job(
                requestor_type="analyst_request", requestor_id=edm_id,
                rwb_job_type="upload_rdm",
                input_data={"rdm_ids": rdm_ids, "edm_ids": [edm_id],
                            "package_id": pid}, actor_id=actor)
            dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="upload_rdm")


def delete_package(*, package_id: Any, actor_id: Any) -> None:
    """Enqueue reverse-order removals (FR-019/FR-021) and return immediately. One
    ``delete_rdm`` head per RDM (synchronous worker, no ``irp_job``); the RDM→EDM
    fan-in in that worker then enqueues the async ``delete_edm`` heads. A package with
    no RDMs enqueues ``delete_edm`` heads directly. No hard delete anywhere."""
    pid = str(package_id)
    actor = str(actor_id)
    rdms = _live_members(pid, "irp_rdm")
    edms = _live_members(pid, "irp_edm")
    if rdms:
        for rdm in rdms:
            job_id = rwb_job_service.ensure_pending_rwb_job(
                requestor_type="analyst_request", requestor_id=str(rdm["id"]),
                rwb_job_type="delete_rdm",
                input_data={"rdm_id": str(rdm["id"]), "package_id": pid},
                actor_id=actor)
            dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="delete_rdm")
    elif edms:
        for edm in edms:
            job_id = rwb_job_service.ensure_pending_rwb_job(
                requestor_type="analyst_request", requestor_id=str(edm["id"]),
                rwb_job_type="delete_edm",
                input_data={"edm_id": str(edm["id"]), "package_id": pid},
                actor_id=actor)
            dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="delete_edm")
    else:
        finalize_package(package_id=pid)  # nothing live → soft-delete the shell


def finalize_package(*, package_id: Any, conn=None) -> bool:
    """Idempotent package soft-delete (FR-021): once no live member is still
    un-removed in Risk Modeler, soft-delete the package **and** its members (never a
    hard delete). Returns ``True`` if it finalized this call. Called by the delete_rdm
    worker (RDM-only package) and the poller on ``delete_edm`` FINISHED."""
    pid = str(package_id)
    now = _utcnow()
    with _txn(conn) as c:
        remaining = c.execute(text(
            """
            SELECT
              (SELECT COUNT(*) FROM irp_edm WHERE package_id = :p
                 AND deleted_at IS NULL AND (status IS NULL OR status <> 'deleted'))
            + (SELECT COUNT(*) FROM irp_rdm WHERE package_id = :p
                 AND deleted_at IS NULL AND (status IS NULL OR status <> 'deleted'))
            """
        ), {"p": pid}).scalar()
        if remaining and int(remaining) > 0:
            return False  # a member is still live in RM — not done yet
        rows = c.execute(text(
            "UPDATE package SET deleted_at = :now, updated_at = :now "
            "WHERE id = :p AND deleted_at IS NULL"
        ), {"now": now, "p": pid}).rowcount
        c.execute(text("UPDATE irp_edm SET deleted_at = :now, updated_at = :now "
                       "WHERE package_id = :p AND deleted_at IS NULL"),
                  {"now": now, "p": pid})
        c.execute(text("UPDATE irp_rdm SET deleted_at = :now, updated_at = :now "
                       "WHERE package_id = :p AND deleted_at IS NULL"),
                  {"now": now, "p": pid})
        return rows > 0


def retry_member(*, package_id: Any, member_id: Any, member_kind: str,
                 actor_id: Any) -> None:
    """Re-enqueue exactly one member's operation head (FR-045). Idempotent — delegates
    to the entity service, which no-ops a ready/in-flight member."""
    if member_kind == "edm":
        edm_service.retry_import(edm_id=member_id, actor_id=actor_id)
    elif member_kind == "rdm":
        rdm_service.retry_import(rdm_id=member_id, actor_id=actor_id)
    else:
        raise ValueError(f"member_kind must be 'edm' or 'rdm', got {member_kind!r}")


def _member_card(row: dict, kind: str) -> MemberCard:
    return MemberCard(id=str(row["id"]).lower(), kind=kind, name=row["name"],
                      status=row["status"], source_file_path=row["source_file_path"])


def get_package_card(package_id: Any, *, with_counts: bool = False) -> PackageCard | None:
    """Card data for one package: members + their status chips, and (US5) the all/active/
    failed job counts scoped to the package's members. Portfolio/analysis areas are left
    empty (R13); no rolled-up package status (FR-018). ``None`` if the package is gone."""
    pid = str(package_id)
    row = execute_one(
        "SELECT id, name, deleted_at FROM package WHERE id = :id",
        {"id": pid}, connection="WORKBENCH")
    if row is None:
        return None
    card = PackageCard(
        id=pid, name=row["name"], deleted_at=row["deleted_at"],
        edms=[_member_card(m, "edm") for m in _live_members(pid, "irp_edm")],
        rdms=[_member_card(m, "rdm") for m in _live_members(pid, "irp_rdm")],
    )
    if with_counts:
        from app.services import job_query  # noqa: PLC0415 — avoid an import cycle
        card.job_counts = job_query.package_job_counts(pid)
    return card


def get_package_cards(submission_id: Any) -> list[PackageCard]:
    """One card per live package attached to the submission (FR-022), each with its
    job counts (FR-023/FR-024). Portfolio/analysis empty (R13)."""
    rows = execute(
        "SELECT p.id FROM package p "
        "JOIN submission_package sp ON sp.package_id = p.id "
        "WHERE sp.submission_id = :s AND p.deleted_at IS NULL "
        "ORDER BY p.inserted_at",
        {"s": str(submission_id)}, connection="WORKBENCH")
    cards = [get_package_card(r["id"], with_counts=True) for r in rows]
    return [c for c in cards if c is not None]


__all__ = [
    "MemberSpec", "MemberCollision", "SaveResult", "MemberCard", "PackageCard",
    "save_package", "save_and_sync", "delete_package", "finalize_package",
    "retry_member", "get_package_card", "get_package_cards",
]
