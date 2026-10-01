"""The shared cedant list (issue 129). A submission references one cedant by
id, so a rename shows on every submission."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text

from app.services._common import _uid, _utcnow, _word_and_clauses
from db import execute, execute_command, execute_one, is_unique_violation

MAX_NAME_LENGTH = 255


class CedantValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Cedant:
    id: str
    name: str
    is_active: bool


def list_cedants(*, include_inactive: bool, name: str = "") -> list[Cedant]:
    """``name`` keeps the cedants whose name contains every word of it, any order."""
    where, params = _word_and_clauses(name, ("name",), "n")
    if not include_inactive:
        where.append("is_active = 1")
    rows = execute(
        "SELECT id, name, is_active FROM cedant"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY name",
        params, connection="WORKBENCH",
    )
    return [Cedant(id=_uid(row["id"]), name=row["name"], is_active=bool(row["is_active"]))
            for row in rows]


def _trimmed(name: str) -> str:
    name = name.strip()
    if not name:
        raise CedantValidationError("Enter a cedant name.")
    if len(name) > MAX_NAME_LENGTH:
        raise CedantValidationError(
            f"A cedant name is at most {MAX_NAME_LENGTH} characters.")
    return name


def _named(name: str, exclude_id: str | None = None) -> dict | None:
    sql = "SELECT id, name, is_active FROM cedant WHERE LOWER(name) = LOWER(:name)"
    params = {"name": name}
    if exclude_id is not None:
        sql += " AND id <> :id"
        params["id"] = exclude_id
    return execute_one(sql, params, connection="WORKBENCH")


def _checked_name(name: str, exclude_id: str | None = None) -> str:
    """Trim ``name`` and raise ``CedantValidationError`` unless it is free (P-03, P-04).
    Names are unique case-insensitively across active and inactive cedants."""
    name = _trimmed(name)
    taken = _named(name, exclude_id)
    if taken is None:
        return name
    if taken["is_active"]:
        raise CedantValidationError(f'A cedant named "{taken["name"]}" already exists.')
    raise CedantValidationError(
        f'A cedant named "{taken["name"]}" exists but is inactive — reactivate it instead.')


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
    is_active: bool


def new_cedant(name: str) -> NewCedant:
    """Raise ``CedantValidationError`` for a blank or over-long name. A name an
    existing cedant has (case ignored) resolves to that cedant: an inactive one
    is reactivated on save (P-07), and an active one covers another analyst
    adding the same name since the form loaded."""
    name = _trimmed(name)
    row = _named(name)
    if row is None:
        return NewCedant(name=name, id=None, is_active=False)
    return NewCedant(name=row["name"], id=_uid(row["id"]), is_active=bool(row["is_active"]))


def save_new_cedant(conn, cedant: NewCedant, *, actor_id: str) -> str:
    """Insert or reactivate ``cedant`` on the caller's open transaction and
    return its id."""
    now = _utcnow()
    if cedant.id is None:
        cedant_id = str(uuid.uuid4())
        conn.execute(text(
            "INSERT INTO cedant (id, name, inserted_at, updated_at, inserted_by, updated_by) "
            "VALUES (:id, :name, :now, :now, :actor, :actor)"),
            {"id": cedant_id, "name": cedant.name, "now": now, "actor": actor_id})
        return cedant_id
    if not cedant.is_active:
        conn.execute(text(
            "UPDATE cedant SET is_active = 1, updated_at = :now, updated_by = :actor "
            "WHERE id = :id"), {"now": now, "actor": actor_id, "id": cedant.id})
    return cedant.id


def set_cedant_active(cedant_id: str, active: bool, *, actor_id: str) -> None:
    execute_command(
        "UPDATE cedant SET is_active = :active, updated_at = :now, updated_by = :actor "
        "WHERE id = :id",
        {"active": int(active), "now": _utcnow(), "actor": actor_id, "id": cedant_id},
        connection="WORKBENCH",
    )
