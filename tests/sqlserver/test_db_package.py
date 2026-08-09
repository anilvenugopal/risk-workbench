"""Safe-path execution (db/execute.py) against the real driver.

Bound parameters, row/scalar/rowcount return shapes, and row_limit's
OFFSET/FETCH clause — run on a scratch table in the test database so the app
schema stays out of the way.
"""

import pytest

from db import execute, execute_command, execute_one, execute_scalar, row_limit


@pytest.fixture
def probe_table():
    execute_command("DROP TABLE IF EXISTS db_pkg_probe", connection="WORKBENCH")
    execute_command(
        "CREATE TABLE db_pkg_probe (id INT PRIMARY KEY, status_code NVARCHAR(20))",
        connection="WORKBENCH")
    for r in [(1, "open"), (2, "closed"), (3, "open"), (4, "open")]:
        execute_command("INSERT INTO db_pkg_probe VALUES (:a, :b)",
                        {"a": r[0], "b": r[1]}, connection="WORKBENCH")
    yield
    execute_command("DROP TABLE IF EXISTS db_pkg_probe", connection="WORKBENCH")


def test_execute_returns_list_of_dicts(probe_table):
    rows = execute("SELECT * FROM db_pkg_probe WHERE status_code = :s",
                   {"s": "open"}, connection="WORKBENCH")
    assert isinstance(rows, list) and all(isinstance(r, dict) for r in rows)
    assert len(rows) == 3


def test_execute_binds_not_interpolates(probe_table):
    rows = execute("SELECT * FROM db_pkg_probe WHERE status_code = :s",
                   {"s": "x' OR '1'='1"}, connection="WORKBENCH")
    assert rows == []


def test_execute_one_and_scalar(probe_table):
    assert execute_one("SELECT * FROM db_pkg_probe WHERE id = :id",
                       {"id": 2}, "WORKBENCH")["status_code"] == "closed"
    assert execute_one("SELECT * FROM db_pkg_probe WHERE id = :id",
                       {"id": 999}, "WORKBENCH") is None
    assert execute_scalar("SELECT COUNT(*) FROM db_pkg_probe",
                          connection="WORKBENCH") == 4


def test_execute_command_rowcount(probe_table):
    n = execute_command("UPDATE db_pkg_probe SET status_code='void' WHERE id=:i",
                        {"i": 4}, "WORKBENCH")
    assert n == 1


def test_row_limit_pages_through_ordered_rows(probe_table):
    page1 = execute(f"SELECT id FROM db_pkg_probe ORDER BY id {row_limit(2)}",
                    connection="WORKBENCH")
    page2 = execute(
        f"SELECT id FROM db_pkg_probe ORDER BY id {row_limit(2, offset=2)}",
        connection="WORKBENCH")
    assert [r["id"] for r in page1] == [1, 2]
    assert [r["id"] for r in page2] == [3, 4]
