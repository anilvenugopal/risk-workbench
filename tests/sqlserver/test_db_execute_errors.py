"""Error-path tests for db/execute.py against the real driver.

Each of the four safe-path functions must wrap a driver failure in
SQLServerQueryError with the connection name in the message. Bad SQL never
writes anything, so no per-test wipe is needed.
"""

from __future__ import annotations

import pytest

from db.errors import SQLServerQueryError
from db.execute import execute, execute_command, execute_one, execute_scalar


class TestExecuteErrorPath:
    def test_bad_sql_raises_query_error(self):
        with pytest.raises(SQLServerQueryError):
            execute("THIS IS NOT SQL", {}, connection="WORKBENCH")

    def test_error_message_includes_connection_name(self):
        with pytest.raises(SQLServerQueryError, match="WORKBENCH"):
            execute("SELECT * FROM nonexistent_table_xyz", {},
                    connection="WORKBENCH")

    def test_unknown_param_placeholder_raises_query_error(self):
        # SQLAlchemy text() rejects unbound :params by design
        with pytest.raises(SQLServerQueryError):
            execute("SELECT :missing_param", {}, connection="WORKBENCH")


class TestExecuteOneErrorPath:
    def test_bad_sql_raises_query_error(self):
        with pytest.raises(SQLServerQueryError):
            execute_one("GIBBERISH SQL HERE", {}, connection="WORKBENCH")


class TestExecuteScalarErrorPath:
    def test_bad_sql_raises_query_error(self):
        with pytest.raises(SQLServerQueryError):
            execute_scalar("NOT VALID SQL !", {}, connection="WORKBENCH")


class TestExecuteCommandErrorPath:
    def test_bad_sql_raises_query_error(self):
        with pytest.raises(SQLServerQueryError):
            execute_command("INVALID COMMAND !", {}, connection="WORKBENCH")

    def test_error_message_includes_connection_name(self):
        with pytest.raises(SQLServerQueryError, match="WORKBENCH"):
            execute_command("INSERT INTO no_such_table VALUES (1)", {},
                            connection="WORKBENCH")
