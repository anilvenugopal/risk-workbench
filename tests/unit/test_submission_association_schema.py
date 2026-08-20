"""Schema contract for direct Submission-to-EDM/RDM associations."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from db import execute, execute_command, execute_scalar
from db.errors import SQLServerQueryError


def _submission(actor: str, name: str) -> str:
    submission_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO submission "
        "(id, assigned_analyst_id, name, cedant_name, treaty_type_code, "
        "inception_date, status_code, inserted_by, updated_by) "
        "VALUES (:id, :actor, :name, 'Cedant', 'cat_xol', '2026-01-01', "
        "'ACTIVE', :actor, :actor)",
        {"id": submission_id, "actor": actor, "name": name},
        connection="WORKBENCH",
    )
    return submission_id


def _entity(table: str, name: str) -> str:
    entity_id = str(uuid.uuid4())
    execute_command(
        f"INSERT INTO {table} (id, name, status) VALUES (:id, :name, 'ready')",
        {"id": entity_id, "name": name},
        connection="WORKBENCH",
    )
    return entity_id


@pytest.mark.parametrize(
    ("table", "entity_table", "entity_column"),
    [
        ("submission_edm", "irp_edm", "edm_id"),
        ("submission_rdm", "irp_rdm", "rdm_id"),
    ],
)
def test_association_foreign_keys_target_submission_and_entity(
    iteration1_db, table, entity_table, entity_column
):
    with iteration1_db.engine.connect() as conn:
        foreign_keys = conn.execute(text(f"PRAGMA foreign_key_list('{table}')")).mappings()
        targets = {(row["from"], row["table"], row["to"]) for row in foreign_keys}
    assert ("submission_id", "submission", "id") in targets
    assert (entity_column, entity_table, "id") in targets


@pytest.mark.parametrize(
    ("association_table", "entity_table", "entity_column"),
    [
        ("submission_edm", "irp_edm", "edm_id"),
        ("submission_rdm", "irp_rdm", "rdm_id"),
    ],
)
def test_duplicate_association_is_rejected(
    iteration1_db, association_table, entity_table, entity_column
):
    submission_id = _submission(iteration1_db.user_a, "A")
    entity_id = _entity(entity_table, "Resource")
    sql = (
        f"INSERT INTO {association_table} (submission_id, {entity_column}) "
        f"VALUES (:submission_id, :entity_id)"
    )
    execute_command(
        sql,
        {"submission_id": submission_id, "entity_id": entity_id},
        connection="WORKBENCH",
    )
    with pytest.raises(SQLServerQueryError):
        execute_command(
            sql,
            {"submission_id": submission_id, "entity_id": entity_id},
            connection="WORKBENCH",
        )


@pytest.mark.parametrize(
    ("association_table", "entity_table", "entity_column"),
    [
        ("submission_edm", "irp_edm", "edm_id"),
        ("submission_rdm", "irp_rdm", "rdm_id"),
    ],
)
def test_detach_preserves_entity_and_other_submission(
    iteration1_db, association_table, entity_table, entity_column
):
    first = _submission(iteration1_db.user_a, "First")
    second = _submission(iteration1_db.user_a, "Second")
    entity_id = _entity(entity_table, "Shared")
    for submission_id in (first, second):
        execute_command(
            f"INSERT INTO {association_table} (submission_id, {entity_column}) "
            f"VALUES (:submission_id, :entity_id)",
            {"submission_id": submission_id, "entity_id": entity_id},
            connection="WORKBENCH",
        )

    execute_command(
        f"DELETE FROM {association_table} "
        f"WHERE submission_id = :submission_id AND {entity_column} = :entity_id",
        {"submission_id": first, "entity_id": entity_id},
        connection="WORKBENCH",
    )

    assert execute_scalar(
        f"SELECT COUNT(*) FROM {entity_table} WHERE id = :id",
        {"id": entity_id},
        connection="WORKBENCH",
    ) == 1
    rows = execute(
        f"SELECT submission_id FROM {association_table} WHERE {entity_column} = :id",
        {"id": entity_id},
        connection="WORKBENCH",
    )
    assert [str(row["submission_id"]) for row in rows] == [second]
