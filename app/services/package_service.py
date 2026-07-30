"""Package service — bundle *structure* operations (Iteration 1, US6).

Structure only (FR-028): create a bundle, add/remove EDM/RDM members, attach a
package to submissions (M:N), soft-delete. No shared-drive browse, no Risk
Modeler name-collision check, no IRP jobs, no package UI — those are Iteration 2,
built on these functions.

The **≥1-member invariant** (FR-024/R5) is enforced here, not by a column CHECK,
because membership spans two child tables (``irp_edm`` + ``irp_rdm``). Portability
rules match ``submission_service`` (app-side UUIDs bound as ``str``, app-supplied
timestamps, no dialect-only SQL).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import text

from db import execute, execute_scalar, execute_command, get_connection
from app.services._common import _txn, _utcnow
from app.services.errors import (
    EmptyPackageError, InvalidMemberName, MemberNotAttachable)

# An EDM/RDM name may use only letters, digits, underscores, and hyphens, capped at
# 50 characters. This is the ONE source of truth for the rule (review item 3): it is
# enforced on every path that names an entity — package members
# (``package_sync_service``) and standalone imports (``edm_service``/``rdm_service``) —
# so the name that reaches Risk Modeler (and gets interpolated into search filters) is
# always clean. Lives here because this module imports neither service (no cycle).
_NAME_MAX = 50
_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")

# Statuses that mean the member is already on its way out of Risk Modeler, so it must
# not be attached or detached (issue #22). Literals rather than edm_service constants
# because this module imports neither entity service (module docstring above), and
# ``package_sync_service.finalize_package`` already hardcodes ``'deleted'`` in SQL.
# Article 3 carve-out column.
_OUTGOING = ("delete_pending", "deleted")


def clean_member_name(name: str) -> str:
    """Validate and normalise an EDM/RDM name; raise ``InvalidMemberName`` if it is
    empty, longer than 50 characters, or carries characters outside ``[A-Za-z0-9_-]``."""
    cleaned = (name or "").strip()
    if not cleaned or len(cleaned) > _NAME_MAX or not _NAME_RE.fullmatch(cleaned):
        raise InvalidMemberName(
            "EDM/RDM names may use only letters, numbers, underscores, and "
            f"hyphens, with a maximum of {_NAME_MAX} characters.")
    return cleaned


@dataclass
class Package:
    id: str
    name: str | None
    deleted_at: Any
    member_count: int
    inserted_at: Any


@dataclass(frozen=True)
class SubmissionRef:
    """A minimal owning-submission reference for the EDM/RDM libraries (US7):
    the submission ``id`` (deep-link target) and its display ``name``."""
    id: str
    name: str | None


def submission_refs_for_packages(
    package_ids: Sequence[Any],
) -> dict[str, list[SubmissionRef]]:
    """Map each package id → its owning submissions (M:N ``submission_package``),
    **oldest submission first** (``submission.inserted_at``), for the library
    owning-submission column (US7 / T058).

    Lives here (not in edm/rdm_service) because ``package_service`` imports neither
    of those, so no import cycle. One portable query with a dynamically-built ``IN``
    param set; the package→refs map is assembled app-side (no ``STRING_AGG``/``TOP``,
    no row fan-out into the caller's list). Returns ``{}`` for empty input; keys are
    lower-cased so a caller keyed on ``str(id).lower()`` always matches."""
    ids = list(dict.fromkeys(str(p).lower() for p in package_ids if p))
    if not ids:
        return {}
    params = {f"p{i}": pid for i, pid in enumerate(ids)}
    placeholders = ", ".join(f":{k}" for k in params)
    rows = execute(
        f"""
        SELECT sp.package_id AS package_id, s.id AS sub_id, s.name AS sub_name
        FROM submission_package sp
        JOIN submission s ON s.id = sp.submission_id
        WHERE sp.package_id IN ({placeholders})
        ORDER BY s.inserted_at ASC
        """,
        params, connection="WORKBENCH",
    )
    result: dict[str, list[SubmissionRef]] = {}
    for row in rows:
        key = str(row["package_id"]).lower()
        result.setdefault(key, []).append(
            SubmissionRef(id=str(row["sub_id"]), name=row["sub_name"]))
    return result


def _table_for_kind(member_kind: str) -> str:
    """Whitelist member_kind → child table. The result is a trusted identifier
    (never interpolated from user free-text), so it is safe to format into SQL."""
    if member_kind == "edm":
        return "irp_edm"
    if member_kind == "rdm":
        return "irp_rdm"
    raise ValueError(f"member_kind must be 'edm' or 'rdm', got {member_kind!r}")


def create_package(
    *, name: str | None, edm_ids: Sequence[Any] = (), rdm_ids: Sequence[Any] = (),
    actor_id: Any,
) -> str:
    """Create a package and stamp ``package_id`` on the given members in one
    transaction. Raises ``EmptyPackageError`` if no members are supplied
    (FR-024/R5)."""
    if not edm_ids and not rdm_ids:
        raise EmptyPackageError("A package must have at least one member.")

    package_id = str(uuid.uuid4())
    now = _utcnow()
    actor = str(actor_id)
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            conn.execute(text(
                "INSERT INTO package (id, name, inserted_at, updated_at, "
                "inserted_by, updated_by) VALUES (:id, :name, :now, :now, :by, :by)"
            ), {"id": package_id, "name": name, "now": now, "by": actor})
            for member_id in edm_ids:
                conn.execute(text(
                    "UPDATE irp_edm SET package_id = :pid, updated_at = :now, "
                    "updated_by = :by WHERE id = :mid"
                ), {"pid": package_id, "now": now, "by": actor, "mid": str(member_id)})
            for member_id in rdm_ids:
                conn.execute(text(
                    "UPDATE irp_rdm SET package_id = :pid, updated_at = :now, "
                    "updated_by = :by WHERE id = :mid"
                ), {"pid": package_id, "now": now, "by": actor, "mid": str(member_id)})
    return package_id


def package_member_count(package_id: Any) -> int:
    """Count live members across both child tables — the basis for the ≥1-member
    invariant test (FR-024/SC-008)."""
    member_count = execute_scalar(
        """
        SELECT
            (SELECT COUNT(*) FROM irp_edm
             WHERE package_id = :id AND deleted_at IS NULL)
          + (SELECT COUNT(*) FROM irp_rdm
             WHERE package_id = :id AND deleted_at IS NULL)
        """,
        {"id": str(package_id)}, connection="WORKBENCH",
    )
    return int(member_count or 0)


def add_member(
    *, package_id: Any, member_id: Any, member_kind: str, actor_id: Any, conn=None,
) -> None:
    """Set ``package_id`` on an irp_edm/irp_rdm row (FR-023). Pure bookkeeping —
    nothing is submitted to (or removed from) Risk Modeler; the entity is untouched
    apart from its membership FK (issue #22).

    Every attachability rule lives in the UPDATE predicate, so a concurrent write
    cannot slip between a check and the write: the row must be live, must be either
    unattached or *already in this package*, and must not be on its way out of Risk
    Modeler. ``rowcount == 0`` ⇒ ``MemberNotAttachable``. Idempotent on the
    (package, member) pair — the same posture as ``attach_to_submission`` — so a
    double-submitted picker does not report a phantom failure for a member that is
    in fact attached. Pass ``conn`` to enlist in a caller's transaction."""
    table = _table_for_kind(member_kind)
    params = {"pid": str(package_id), "now": _utcnow(), "by": str(actor_id),
              "mid": str(member_id), "dp": _OUTGOING[0], "d": _OUTGOING[1]}
    sql = (
        f"UPDATE {table} SET package_id = :pid, updated_at = :now, updated_by = :by "
        "WHERE id = :mid AND deleted_at IS NULL "
        "AND (package_id IS NULL OR package_id = :pid) "
        "AND (status IS NULL OR status NOT IN (:dp, :d))"
    )
    with _txn(conn) as c:
        rows = c.execute(text(sql), params).rowcount
    if rows == 0:
        raise MemberNotAttachable(
            f"That {member_kind.upper()} can't be added — it may have been deleted, "
            "already belong to another package, or be on its way out of Risk Modeler. "
            "Reload and try again.")


def remove_member(
    *, package_id: Any, member_id: Any, member_kind: str, actor_id: Any,
) -> None:
    """Clear ``package_id``. If this empties the package, soft-delete the package
    rather than leave a zero-member bundle (R5/FR-027).

    Pure bookkeeping (issue #22): the entity stays in Risk Modeler and reappears in
    the library as a standalone import — unlike the card's Delete action, which
    removes every member from Risk Modeler. Emptying a package this way soft-deletes
    the *package* row only; the member's own ``deleted_at`` stays NULL.

    The package is bound in the WHERE clause: without it a mismatched
    ``(package_id, member_id)`` pair would clear the member's *real* package and then
    evaluate the emptiness check against the wrong one. ``rowcount == 0`` ⇒
    ``MemberNotAttachable``."""
    table = _table_for_kind(member_kind)
    rows = execute_command(
        f"UPDATE {table} SET package_id = NULL, updated_at = :now, "
        f"updated_by = :by WHERE id = :mid AND package_id = :pid "
        "AND deleted_at IS NULL AND (status IS NULL OR status NOT IN (:dp, :d))",
        {"now": _utcnow(), "by": str(actor_id), "mid": str(member_id),
         "pid": str(package_id), "dp": _OUTGOING[0], "d": _OUTGOING[1]},
        connection="WORKBENCH",
    )
    if rows == 0:
        raise MemberNotAttachable(
            f"That {member_kind.upper()} is no longer a member of this package, or is "
            "on its way out of Risk Modeler. Reload to see where it stands.")
    if package_member_count(package_id) == 0:
        soft_delete_package(package_id=package_id, actor_id=actor_id)


def attach_to_submission(
    *, submission_id: Any, package_id: Any, actor_id: Any,
) -> None:
    """Insert the submission_package pair (composite PK). Idempotent on the pair.
    A package may attach to many submissions and vice versa (FR-025/SC-008)."""
    execute_command(
        """
        INSERT INTO submission_package (submission_id, package_id, inserted_at,
            inserted_by)
        SELECT :sid, :pid, :now, :by
        WHERE NOT EXISTS (
            SELECT 1 FROM submission_package
            WHERE submission_id = :sid AND package_id = :pid
        )
        """,
        {"sid": str(submission_id), "pid": str(package_id), "now": _utcnow(),
         "by": str(actor_id)},
        connection="WORKBENCH",
    )


def detach_from_submission(*, submission_id: Any, package_id: Any) -> None:
    execute_command(
        "DELETE FROM submission_package "
        "WHERE submission_id = :sid AND package_id = :pid",
        {"sid": str(submission_id), "pid": str(package_id)},
        connection="WORKBENCH",
    )


def soft_delete_package(*, package_id: Any, actor_id: Any) -> None:
    """Stamp ``deleted_at`` (FR-027). No hard delete."""
    execute_command(
        "UPDATE package SET deleted_at = :now, updated_at = :now, updated_by = :by "
        "WHERE id = :id AND deleted_at IS NULL",
        {"now": _utcnow(), "by": str(actor_id), "id": str(package_id)},
        connection="WORKBENCH",
    )


def get_packages_for_submission(submission_id: Any) -> list[Package]:
    """Live packages attached to a submission (read-only placeholder source for
    the detail view this iteration; FR-028)."""
    rows = execute(
        """
        SELECT p.id, p.name, p.deleted_at, p.inserted_at
        FROM package p
        JOIN submission_package sp ON sp.package_id = p.id
        WHERE sp.submission_id = :sid AND p.deleted_at IS NULL
        ORDER BY p.inserted_at
        """,
        {"sid": str(submission_id)}, connection="WORKBENCH",
    )
    return [
        Package(
            id=str(row["id"]),
            name=row["name"],
            deleted_at=row["deleted_at"],
            member_count=package_member_count(row["id"]),
            inserted_at=row["inserted_at"],
        )
        for row in rows
    ]
