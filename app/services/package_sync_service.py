"""Package sync service — assemble a package and sync it to Risk Modeler (US3).

Builds on the Iteration-1 ``package_service`` (structure) and the US1/US2 entity
services. Two request-path operations; neither waits on Risk Modeler work
(FR-042 / SC-014), but both run the **blocking** per-member name-collision check
(issue #17, amended FR-012): a member name that already exists in Risk Modeler
raises ``NameCollisionError`` before anything is persisted or enqueued. When the
check itself is unavailable the save fails OPEN — the affected names are returned
as ``unchecked_names`` for the caller to surface, and the worker-side submit
validation is the backstop.

  • ``save_package`` — persist the package + its member entities (each ``pending_import``).
    **Submits nothing.** ≥1-member invariant (``EmptyPackageError``); optimistic
    concurrency on edit (``ConcurrencyConflict``).
  • ``save_and_sync`` — record the pending work and **return immediately**. Enqueues one
    ``upload_edm`` head per EDM; the poller chains one ``upload_rdm`` per finished EDM,
    fanning out to one apply per (EDM × RDM) pair. **Every apply targets an EDM (D3):**
    an RDM-only package (no EDM) is rejected with ``EmptyPackageError`` — review-only
    sync is deferred. Idempotent: re-sync skips ready/in-flight members and re-enqueues
    only unstarted/errored ones. Collision-checks only the members it will actually
    (re)submit — a ``ready`` member's name legitimately exists in RM (it *is* that
    entity).

``delete_package`` / ``get_package_cards`` are added in US4 / US5.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy import text

from app.services import edm_service, name_check, rdm_service, rwb_job_service
from app.services._common import _txn, _utcnow
from app.services.errors import (
    ConcurrencyConflict, EmptyPackageError, NameCollisionError)
from app.services.package_service import clean_member_name
from app.services.shared_drive import validate_selection
from app.workers import dispatch
from db import execute, execute_command, execute_one, get_connection

logger = logging.getLogger(__name__)

# entity statuses that mean "in flight or done" — a re-sync leaves these alone.
_LOCKED = ("importing", "ready")


@dataclass
class MemberSpec:
    kind: str            # 'edm' | 'rdm'
    name: str
    source_file_path: str


@dataclass
class SaveResult:
    package_id: str
    # member names whose collision check couldn't reach Risk Modeler (fail open) —
    # the caller surfaces these as a warning; the worker submit is the backstop.
    unchecked_names: list[str] = field(default_factory=list)


@dataclass
class MemberCard:
    id: str
    kind: str            # 'edm' | 'rdm'
    name: str
    status: str | None
    source_file_path: str | None
    # Spec 004 (EDM members only): the analysis counts (FR-050).
    analysis_counts: Any = None
    # Issue #17 backstop surfacing: an ``error`` member's specific Risk Modeler
    # submit-failure message (failed upload head, worker framing stripped) —
    # None when the failure recorded no detail (RM-side terminal failure).
    error_detail: str | None = None


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


def _live_members(package_id: str, table: str) -> list[dict]:
    return [dict(r) for r in execute(
        f"SELECT id, name, status, source_file_path FROM {table} "
        "WHERE package_id = :p AND deleted_at IS NULL ORDER BY inserted_at, id",
        {"p": package_id}, connection="WORKBENCH")]


def save_package(
    *, package_id: Any | None, name: str | None, members: Sequence[MemberSpec],
    actor_id: Any, expected_updated_at: Any = None,
) -> SaveResult:
    """Persist the package + its member entities; submit nothing (FR-013/FR-014).
    A member name that already exists in Risk Modeler raises ``NameCollisionError``
    **before any write** — a blocked save persists nothing (issue #17). Names the
    check couldn't verify (RM unreachable) fail open into
    ``SaveResult.unchecked_names``. ≥1-member invariant (``EmptyPackageError``);
    optimistic concurrency on edit (``ConcurrencyConflict``)."""
    specs = list(members)
    for spec in specs:
        spec.source_file_path = validate_selection(spec.source_file_path)  # canonical; raises InvalidSourceFile
        spec.name = clean_member_name(spec.name)                           # strips; raises InvalidMemberName

    colliding, unchecked = _check_member_names(
        (spec.kind, spec.name) for spec in specs)
    if colliding:
        raise NameCollisionError(
            "Name taken: " + ", ".join(colliding) + " already exist(s) in Risk "
            "Modeler. Choose a different name — nothing was saved.")

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
                    "src": spec.source_file_path,
                    "n": spec.name, "now": now, "by": actor})

    if _member_count(pid) == 0:
        raise EmptyPackageError("A package must have at least one member.")
    logger.info("package %s %s by analyst %s (%d member(s) added)",
                pid, "created" if creating else "updated", actor, len(specs))
    return SaveResult(package_id=pid, unchecked_names=unchecked)


def _check_member_names(kinds_and_names) -> tuple[list[str], list[str]]:
    """Run the blocking collision check over ``(kind, name)`` pairs. Returns
    ``(colliding, unchecked)``: names found in Risk Modeler (labelled with their
    kind, e.g. ``"E1 (EDM)"``) and names the gateway couldn't verify (fail open).
    A name repeated WITHIN the batch is also a collision — neither exists in RM
    yet, so the per-name check passes both, but the first submit creates the
    name and the second would fail minutes later at the worker backstop."""
    colliding: list[str] = []
    unchecked: list[str] = []
    seen: set[tuple[str, str]] = set()
    for kind, name in kinds_and_names:
        if (kind, name) in seen:
            colliding.append(f"{name} ({kind.upper()}, duplicated in this package)")
            continue
        seen.add((kind, name))
        check = name_check.check_member_name(kind, name)
        if check.collides:
            colliding.append(f"{name} ({kind.upper()})")
        elif not check.checked:
            unchecked.append(name)
    return colliding, unchecked


def _member_count(package_id: str) -> int:
    from app.services.package_service import package_member_count  # noqa: PLC0415
    return package_member_count(package_id)


def save_and_sync(*, package_id: Any, actor_id: Any) -> list[str]:
    """Record the pending work and return immediately (FR-015/FR-042/FR-044) — the
    only Risk Modeler touch is the cached name-collision read (Article 11). **Every
    apply targets an EDM (D3):** an RDM-only package (no EDM) is rejected with
    ``EmptyPackageError`` — review-only sync is deferred; this also covers the
    empty-package case. Idempotent on the dedup key (re-sync skips ready/in-flight).

    Blocking name check (issue #17) covers only members that will actually be
    (re)submitted — status not in ``_LOCKED`` — so a ``ready`` member never
    self-collides, and a stale draft whose name got taken since save is caught
    here. Raises ``NameCollisionError`` before anything is enqueued; returns the
    names the check couldn't verify (fail open)."""
    pid = str(package_id)
    actor = str(actor_id)
    edms = _live_members(pid, "irp_edm")
    rdms = _live_members(pid, "irp_rdm")
    if not edms:
        raise EmptyPackageError(
            "A package must include at least one EDM to sync "
            "(RDM-only / review-only sync is deferred).")

    colliding, unchecked = _check_member_names(
        (kind, m["name"])
        for kind, rows in (("edm", edms), ("rdm", rdms))
        for m in rows if m["status"] not in _LOCKED)
    if colliding:
        raise NameCollisionError(
            "Sync blocked — name taken: " + ", ".join(colliding) + " now exist(s) "
            "in Risk Modeler. Rename the affected member(s) before syncing; "
            "nothing was submitted.")

    rdm_ids = [str(r["id"]) for r in rdms]

    heads = 0
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
            heads += 1
        elif edm["status"] == "ready" and rdm_ids:
            # EDM already imported (e.g. RDMs added after) → apply the package RDMs
            # to it directly; the body is idempotent per (EDM, RDM) pair.
            job_id = rwb_job_service.ensure_pending_rwb_job(
                requestor_type="analyst_request", requestor_id=edm_id,
                rwb_job_type="upload_rdm",
                input_data={"rdm_ids": rdm_ids, "edm_ids": [edm_id],
                            "package_id": pid}, actor_id=actor)
            dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="upload_rdm")
            heads += 1
    logger.info("package %s sync requested by analyst %s (%d upload head(s))",
                pid, actor, heads)
    return unchecked


def delete_package(*, package_id: Any, actor_id: Any) -> None:
    """Enqueue reverse-order removals (FR-019/FR-021) and return immediately. One
    ``delete_rdm`` head per RDM (synchronous worker, no ``irp_job``); the RDM→EDM
    fan-in in that worker then enqueues the async ``delete_edm`` heads. A package with
    no RDMs enqueues ``delete_edm`` heads directly. No hard delete anywhere."""
    pid = str(package_id)
    actor = str(actor_id)
    rdms = _live_members(pid, "irp_rdm")
    edms = _live_members(pid, "irp_edm")
    heads = 0
    if rdms:
        for rdm in rdms:
            job_id = rwb_job_service.ensure_pending_rwb_job(
                requestor_type="analyst_request", requestor_id=str(rdm["id"]),
                rwb_job_type="delete_rdm",
                input_data={"rdm_id": str(rdm["id"]), "package_id": pid},
                actor_id=actor)
            dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="delete_rdm")
            heads += 1
    elif edms:
        for edm in edms:
            job_id = rwb_job_service.ensure_pending_rwb_job(
                requestor_type="analyst_request", requestor_id=str(edm["id"]),
                rwb_job_type="delete_edm",
                input_data={"edm_id": str(edm["id"]), "package_id": pid},
                actor_id=actor)
            dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="delete_edm")
            heads += 1
    else:
        finalize_package(package_id=pid)  # nothing live → soft-delete the shell
    logger.info("package %s delete requested by analyst %s (%d delete head(s))",
                pid, actor, heads)


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
        if rows:
            logger.info("package %s finalized (soft-deleted)", pid)
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


def _attach_error_details(card: PackageCard) -> None:
    """One batched read over the failed upload heads (issue #17 backstop
    surfacing): give each ``error`` member the specific Risk Modeler message its
    submit failure recorded (``rwb_job.error_detail``, worker framing stripped —
    same read as ``edm_service.latest_import_error``). Members whose failure was
    RM-side (the head itself succeeded) keep ``None``."""
    wanted: dict[tuple[str, str], MemberCard] = {}
    for member in card.edms:
        if member.status == "error":
            wanted[("upload_edm", member.id)] = member
    for member in card.rdms:
        if member.status == "error":
            wanted[("upload_rdm", member.id)] = member
    if not wanted:
        return
    params = {f"m{i}": mid for i, (_jt, mid) in enumerate(wanted)}
    placeholders = ", ".join(f":{k}" for k in params)
    rows = execute(
        "SELECT requestor_id, rwb_job_type, error_detail FROM rwb_job "
        "WHERE requestor_type = 'analyst_request' AND status_code = 'failed' "
        "AND rwb_job_type IN ('upload_edm', 'upload_rdm') "
        f"AND requestor_id IN ({placeholders})",
        params, connection="WORKBENCH")
    for row in rows:
        member = wanted.get((row["rwb_job_type"],
                             str(row["requestor_id"]).lower()))
        detail = row["error_detail"]
        if member is None or not detail:
            continue
        prefix = f"{row['rwb_job_type']} submit failed: "
        member.error_detail = (detail[len(prefix):]
                               if detail.startswith(prefix) else detail)


def get_package_card(package_id: Any, *, with_counts: bool = False) -> PackageCard | None:
    """Card data for one package: members + their status chips, and (US5) the all/active/
    failed job counts scoped to the package's members. Spec 004: each EDM member
    carries its analysis counts (FR-050). No rolled-up package status (FR-018).
    ``None`` if the package is gone."""
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
    _attach_error_details(card)
    from app.services import analysis_service  # noqa: PLC0415 — cycle guard
    for member in card.edms:
        member.analysis_counts = analysis_service.analysis_counts(
            edm_id=member.id)
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
    "MemberSpec", "SaveResult", "MemberCard", "PackageCard",
    "save_package", "save_and_sync", "delete_package", "finalize_package",
    "retry_member", "get_package_card", "get_package_cards",
]
