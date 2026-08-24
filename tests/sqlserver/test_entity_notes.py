"""Shared EDM and RDM note behavior."""

from __future__ import annotations

import uuid

import pytest

from app.services import edm_service, entity_note_service, rdm_service
from app.services.errors import NoteConflict
from db import execute_command, execute_one


@pytest.mark.parametrize(
    ("table", "kind"),
    [
        ("irp_edm", "edm"),
        ("irp_rdm", "rdm"),
    ],
)
def test_notes_save_replace_clear_and_read(iteration1_db, table, kind):
    entity_id = str(uuid.uuid4())
    execute_command(
        f"INSERT INTO {table} (id, name, status) VALUES (:id, 'Shared', 'ready')",
        {"id": entity_id}, connection="WORKBENCH")
    args = {"kind": kind, "entity_id": entity_id,
            "actor_id": iteration1_db.user_a}

    first_note = "x" * 250
    assert entity_note_service.update_notes(
        **args, notes=first_note, original_notes="") == first_note
    assert entity_note_service.update_notes(
        **args, notes="Replacement", original_notes=first_note) == "Replacement"
    assert entity_note_service.update_notes(
        **args, notes="   ", original_notes="Replacement") is None
    row = edm_service.get_edm(entity_id) if kind == "edm" else rdm_service.get_rdm(entity_id)
    assert row.notes is None


@pytest.mark.parametrize(
    ("table", "kind"),
    [
        ("irp_edm", "edm"),
        ("irp_rdm", "rdm"),
    ],
)
def test_note_conflict_preserves_input_and_second_save_replaces(
    iteration1_db, table, kind,
):
    entity_id = str(uuid.uuid4())
    execute_command(
        f"INSERT INTO {table} (id, name, status, notes) "
        "VALUES (:id, 'Shared', 'ready', 'Newer note')",
        {"id": entity_id}, connection="WORKBENCH")
    args = {"kind": kind, "entity_id": entity_id,
            "actor_id": iteration1_db.user_a}

    with pytest.raises(NoteConflict) as conflict:
        entity_note_service.update_notes(
            **args, notes="My note", original_notes="Older note")
    assert conflict.value.current_note == "Newer note"
    assert entity_note_service.update_notes(
        **args, notes="My note", original_notes=conflict.value.current_note) == "My note"


@pytest.mark.parametrize(
    ("table", "kind"),
    [
        ("irp_edm", "edm"),
        ("irp_rdm", "rdm"),
    ],
)
def test_note_rejects_more_than_250_characters(iteration1_db, table, kind):
    entity_id = str(uuid.uuid4())
    execute_command(
        f"INSERT INTO {table} (id, name, status) VALUES (:id, 'Shared', 'ready')",
        {"id": entity_id}, connection="WORKBENCH")
    with pytest.raises(ValueError, match="250"):
        entity_note_service.update_notes(
            kind=kind, entity_id=entity_id, notes="x" * 251, original_notes="",
            actor_id=iteration1_db.user_a)


def test_note_columns_are_nullable_nvarchar_250(iteration1_db):
    for table in ("irp_edm", "irp_rdm"):
        note = execute_one(
            "SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = :table AND COLUMN_NAME = 'notes'",
            {"table": table}, connection="WORKBENCH")
        assert note == {
            "DATA_TYPE": "nvarchar",
            "CHARACTER_MAXIMUM_LENGTH": 250,
            "IS_NULLABLE": "YES",
        }
