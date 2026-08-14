"""Shared EDM and RDM note updates."""

from __future__ import annotations

from typing import Any

from app.services._common import _utcnow
from app.services.errors import NoteConflict
from db import execute_command, execute_one

_TABLES = {"edm": "irp_edm", "rdm": "irp_rdm"}


def update_notes(
    *, kind: str, entity_id: Any, notes: str, original_notes: str, actor_id: Any,
) -> str | None:
    value = notes.strip() or None
    if value is not None and len(value.encode("utf-16-le")) // 2 > 250:
        raise ValueError("Notes must be 250 characters or fewer.")
    table = _TABLES[kind]
    entity_id = str(entity_id)
    original = original_notes or None
    current = execute_one(
        f"SELECT notes FROM {table} WHERE id = :id AND deleted_at IS NULL",
        {"id": entity_id}, connection="WORKBENCH")
    if current is None:
        raise LookupError(f"{kind.upper()} not found")
    if current["notes"] != original:
        raise NoteConflict(current["notes"])
    rows = execute_command(
        f"UPDATE {table} SET notes = :notes, updated_at = :now, updated_by = :by "
        "WHERE id = :id AND deleted_at IS NULL "
        "AND ((notes IS NULL AND :original IS NULL) OR notes = :original)",
        {"notes": value, "now": _utcnow(), "by": str(actor_id),
         "id": entity_id, "original": original}, connection="WORKBENCH")
    if rows == 0:
        newer = execute_one(f"SELECT notes FROM {table} WHERE id = :id",
                            {"id": entity_id}, connection="WORKBENCH")
        raise NoteConflict(newer["notes"] if newer else None)
    return value
