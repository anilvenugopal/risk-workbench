"""The shared cedant list (issue 129). A submission references one cedant by
id, so a rename shows on every submission."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text

from app.services._common import _in_clause, _uid, _utcnow, _word_and_clauses
from db import execute, execute_command, execute_one, is_unique_violation

MAX_NAME_LENGTH = 255


class CedantValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Cedant:
    id: str
    name: str
    submission_count: int


def list_cedants(*, name: str = "") -> list[Cedant]:
    """``name`` keeps the cedants whose name contains every word of it, any order."""
    where, params = _word_and_clauses(name, ("c.name",), "n")
    rows = execute(
        "SELECT c.id, c.name, COUNT(s.id) AS submission_count FROM cedant c "
        "LEFT JOIN submission s ON s.cedant_id = c.id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " GROUP BY c.id, c.name ORDER BY c.name",
        params, connection="WORKBENCH",
    )
    return [Cedant(id=_uid(row["id"]), name=row["name"],
                   submission_count=row["submission_count"]) for row in rows]


def _trimmed(name: str) -> str:
    name = name.strip()
    if not name:
        raise CedantValidationError("Enter a cedant name.")
    if len(name) > MAX_NAME_LENGTH:
        raise CedantValidationError(
            f"A cedant name is at most {MAX_NAME_LENGTH} characters.")
    return name


def _named(name: str, exclude_id: str | None = None) -> dict | None:
    sql = "SELECT id, name FROM cedant WHERE LOWER(name) = LOWER(:name)"
    params = {"name": name}
    if exclude_id is not None:
        sql += " AND id <> :id"
        params["id"] = exclude_id
    return execute_one(sql, params, connection="WORKBENCH")


def _checked_name(name: str, exclude_id: str | None = None) -> str:
    """Trim ``name`` and raise ``CedantValidationError`` unless no other cedant
    has it, case ignored (P-03, P-04)."""
    name = _trimmed(name)
    taken = _named(name, exclude_id)
    if taken is not None:
        raise CedantValidationError(f'A cedant named "{taken["name"]}" already exists.')
    return name


def _write(sql: str, params: dict, name: str, exclude_id: str | None = None) -> None:
    # Two admins can pass the name check together; uq_cedant_name refuses the
    # second write, which then gets the same message the check gives.
    try:
        execute_command(sql, params, connection="WORKBENCH")
    except Exception as exc:
        if not is_unique_violation(exc):
            raise
        _checked_name(name, exclude_id)
        raise


def add_cedant(name: str, *, actor_id: str) -> str:
    name = _checked_name(name)
    cedant_id = str(uuid.uuid4())
    now = _utcnow()
    _write(
        "INSERT INTO cedant (id, name, inserted_at, updated_at, inserted_by, updated_by) "
        "VALUES (:id, :name, :now, :now, :actor, :actor)",
        {"id": cedant_id, "name": name, "now": now, "actor": actor_id}, name,
    )
    return cedant_id


def rename_cedant(cedant_id: str, name: str, *, actor_id: str) -> None:
    name = _checked_name(name, exclude_id=cedant_id)
    _write(
        "UPDATE cedant SET name = :name, updated_at = :now, updated_by = :actor "
        "WHERE id = :id",
        {"name": name, "now": _utcnow(), "actor": actor_id, "id": cedant_id},
        name, exclude_id=cedant_id,
    )


@dataclass(frozen=True)
class NewCedant:
    """A name typed in the submission form's Cedant field. Nothing is written
    until the submission saves (P-06): ``save_new_cedant`` runs inside the
    submission's transaction."""
    name: str
    id: str | None  # the cedant that already has this name, if any


def new_cedant(name: str) -> NewCedant:
    """Raise ``CedantValidationError`` for a blank or over-long name. A name an
    existing cedant has (case ignored) resolves to that cedant, which covers
    another analyst adding the same name since the form loaded."""
    name = _trimmed(name)
    row = _named(name)
    if row is None:
        return NewCedant(name=name, id=None)
    return NewCedant(name=row["name"], id=_uid(row["id"]))


def save_new_cedant(conn, cedant: NewCedant, *, actor_id: str) -> str:
    """Insert ``cedant`` on the caller's open transaction unless it already
    exists, and return its id."""
    if cedant.id is not None:
        return cedant.id
    cedant_id = str(uuid.uuid4())
    now = _utcnow()
    conn.execute(text(
        "INSERT INTO cedant (id, name, inserted_at, updated_at, inserted_by, updated_by) "
        "VALUES (:id, :name, :now, :now, :actor, :actor)"),
        {"id": cedant_id, "name": cedant.name, "now": now, "actor": actor_id})
    return cedant_id


def delete_cedants(cedant_ids: list[str]) -> list[str]:
    """Delete each of ``cedant_ids`` that no submission uses, and return the
    names of the ones kept because a submission uses them."""
    if not cedant_ids:
        return []
    clause, params = _in_clause("id", cedant_ids, "c")
    execute_command(
        f"DELETE FROM cedant WHERE {clause} AND NOT EXISTS "
        "(SELECT 1 FROM submission s WHERE s.cedant_id = cedant.id)",
        params, connection="WORKBENCH",
    )
    return [row["name"] for row in execute(
        f"SELECT name FROM cedant WHERE {clause} ORDER BY name",
        params, connection="WORKBENCH")]
