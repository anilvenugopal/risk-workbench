"""Schema contract for direct Submission-to-EDM/RDM associations."""

from __future__ import annotations

import uuid

import pytest

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
    workbench_db, table, entity_table, entity_column
):
    rows = execute(
        "SELECT parent_column.name AS parent_column, "
        "referenced_table.name AS referenced_table, "
        "referenced_column.name AS referenced_column "
        "FROM sys.foreign_key_columns AS foreign_key "
        "JOIN sys.tables AS parent_table "
        "ON parent_table.object_id = foreign_key.parent_object_id "
        "JOIN sys.columns AS parent_column "
        "ON parent_column.object_id = foreign_key.parent_object_id "
        "AND parent_column.column_id = foreign_key.parent_column_id "
        "JOIN sys.tables AS referenced_table "
        "ON referenced_table.object_id = foreign_key.referenced_object_id "
        "JOIN sys.columns AS referenced_column "
        "ON referenced_column.object_id = foreign_key.referenced_object_id "
        "AND referenced_column.column_id = foreign_key.referenced_column_id "
        "WHERE parent_table.name = :table",
        {"table": table}, connection="WORKBENCH")
    targets = {
        (row["parent_column"], row["referenced_table"], row["referenced_column"])
        for row in rows
    }
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
    workbench_db, association_table, entity_table, entity_column
):
    submission_id = _submission(workbench_db.user_a, "A")
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
    workbench_db, association_table, entity_table, entity_column
):
    first = _submission(workbench_db.user_a, "First")
    second = _submission(workbench_db.user_a, "Second")
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
    assert [str(row["submission_id"]).lower() for row in rows] == [second.lower()]
