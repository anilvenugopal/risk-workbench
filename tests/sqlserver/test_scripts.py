"""SQL Server integration test for db/scripts.py.

Run with: pytest tests/sqlserver --run-sqlserver  (requires live SQL Server)
"""

from __future__ import annotations

import pytest

from db import execute_command
from db.scripts import execute_script_file

pytestmark = pytest.mark.sqlserver


@pytest.fixture()
def counted_table():
    execute_command("CREATE TABLE dbo.scripts_test_rowcount (id INT)", connection="WORKBENCH")
    execute_command("INSERT INTO dbo.scripts_test_rowcount VALUES (1)", connection="WORKBENCH")
    yield
    execute_command("DROP TABLE dbo.scripts_test_rowcount", connection="WORKBENCH")


def test_a_scripts_session_settings_do_not_reach_the_pool(tmp_path, counted_table):
    """The bulk update script opens with SET NOCOUNT ON. Before the runner closed
    its connection instead of pooling it, and before pyodbc's own pooling was
    off, the next UPDATE on that connection reported -1 and every upsert that
    branches on rowcount stopped inserting."""
    script = tmp_path / "nocount.sql"
    script.write_text("SET NOCOUNT ON;\nSELECT 1 AS probe;\n", encoding="utf-8")
    (frame,) = execute_script_file(script, connection="WORKBENCH")
    assert frame["probe"].tolist() == [1]

    for _ in range(3):
        assert execute_command("UPDATE dbo.scripts_test_rowcount SET id = 1",
                               connection="WORKBENCH") == 1
