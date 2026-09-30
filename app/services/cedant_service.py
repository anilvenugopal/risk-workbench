"""The admin-maintained cedant list (issue 129). A submission references one
cedant by id, so a rename shows on every submission."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.services._common import _uid, _utcnow
from db import execute, execute_command, execute_one, is_unique_violation

MAX_NAME_LENGTH = 255


class CedantValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Cedant:
    id: str
    name: str
    is_active: bool


def list_cedants(*, include_inactive: bool) -> list[Cedant]:
    rows = execute(
        "SELECT id, name, is_active FROM cedant"
        + ("" if include_inactive else " WHERE is_active = 1")
        + " ORDER BY name",
        {}, connection="WORKBENCH",
    )
    return [Cedant(id=_uid(row["id"]), name=row["name"], is_active=bool(row["is_active"]))
            for row in rows]


def _checked_name(name: str, exclude_id: str | None = None) -> str:
    """Trim ``name`` and raise ``CedantValidationError`` unless it is free (P-03, P-04).
    Names are unique case-insensitively across active and inactive cedants."""
    name = name.strip()
    if not name:
        raise CedantValidationError("Enter a cedant name.")
    if len(name) > MAX_NAME_LENGTH:
        raise CedantValidationError(
            f"A cedant name is at most {MAX_NAME_LENGTH} characters.")
    sql = "SELECT name, is_active FROM cedant WHERE LOWER(name) = LOWER(:name)"
    params = {"name": name}
    if exclude_id is not None:
        sql += " AND id <> :id"
        params["id"] = exclude_id
    taken = execute_one(sql, params, connection="WORKBENCH")
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


def set_cedant_active(cedant_id: str, active: bool, *, actor_id: str) -> None:
    execute_command(
        "UPDATE cedant SET is_active = :active, updated_at = :now, updated_by = :actor "
        "WHERE id = :id",
        {"active": int(active), "now": _utcnow(), "actor": actor_id, "id": cedant_id},
        connection="WORKBENCH",
    )
