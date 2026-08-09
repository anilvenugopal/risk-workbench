"""The trusted-script path's execute_query against the real driver.

The pure substitution/parameter logic is covered in
tests/unit/test_scripts_extended.py; this module proves the execution and
error-wrapping behavior on SQL Server.
"""

from __future__ import annotations

import pandas as pd
import pytest

from db import execute_command
from db.errors import SQLServerQueryError
from db.scripts import execute_query


@pytest.fixture
def demo_table():
    execute_command("DROP TABLE IF EXISTS scripts_demo", connection="WORKBENCH")
    execute_command("CREATE TABLE scripts_demo (n INT)", connection="WORKBENCH")
    execute_command("INSERT INTO scripts_demo VALUES (7)", connection="WORKBENCH")
    yield
    execute_command("DROP TABLE IF EXISTS scripts_demo", connection="WORKBENCH")


def test_returns_dataframe(demo_table):
    result = execute_query("SELECT n FROM scripts_demo", connection="WORKBENCH")
    assert isinstance(result, pd.DataFrame)
    assert result["n"].iloc[0] == 7


def test_bad_sql_raises_query_error():
    with pytest.raises(SQLServerQueryError):
        execute_query("NOT VALID SQL", connection="WORKBENCH")
