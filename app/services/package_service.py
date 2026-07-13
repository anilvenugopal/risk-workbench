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

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import text

from db import execute, execute_scalar, execute_command, get_connection
from app.services.errors import EmptyPackageError


@dataclass
class Package:
    id: str
    name: str | None
    deleted_at: Any
    member_count: int
    inserted_at: Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    *, package_id: Any, member_id: Any, member_kind: str, actor_id: Any,
) -> None:
    """Set ``package_id`` on an irp_edm/irp_rdm row (FR-023)."""
    table = _table_for_kind(member_kind)
    execute_command(
        f"UPDATE {table} SET package_id = :pid, updated_at = :now, "
        f"updated_by = :by WHERE id = :mid",
        {"pid": str(package_id), "now": _utcnow(), "by": str(actor_id),
         "mid": str(member_id)},
        connection="WORKBENCH",
    )


def remove_member(
    *, package_id: Any, member_id: Any, member_kind: str, actor_id: Any,
) -> None:
    """Clear ``package_id``. If this empties the package, soft-delete the package
    rather than leave a zero-member bundle (R5/FR-027)."""
    table = _table_for_kind(member_kind)
    execute_command(
        f"UPDATE {table} SET package_id = NULL, updated_at = :now, "
        f"updated_by = :by WHERE id = :mid",
        {"now": _utcnow(), "by": str(actor_id), "mid": str(member_id)},
        connection="WORKBENCH",
    )
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
