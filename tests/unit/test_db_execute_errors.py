"""Unit tests for the error paths in db/execute.py.

The happy paths (execute returns rows, execute_one returns None, etc.) are
already covered by test_db_package.py via a SQLite fixture. This file covers
the four except-Exception re-raise paths — lines 43-46, 59-62, 74-77, 94-97
— by running bad SQL against a registered SQLite engine.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from db.connection import register_engine, _ENGINE_OVERRIDES
from db.errors import SQLServerQueryError
from db.execute import execute, execute_one, execute_scalar, execute_command


@pytest.fixture(autouse=True)
def _sqlite(monkeypatch):
    """Register a blank SQLite engine for WORKBENCH so no real DB is needed."""
    eng = create_engine("sqlite:///:memory:")
    _ENGINE_OVERRIDES.clear()
    register_engine("WORKBENCH", eng)
    yield
    _ENGINE_OVERRIDES.clear()


class TestExecuteErrorPath:
    def test_bad_sql_raises_query_error(self):
        with pytest.raises(SQLServerQueryError):
            execute("THIS IS NOT SQL", {}, connection="WORKBENCH")

    def test_error_message_includes_connection_name(self):
        try:
            execute("SELECT * FROM nonexistent_table_xyz", {}, connection="WORKBENCH")
        except SQLServerQueryError as e:
            assert "WORKBENCH" in str(e)

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
        try:
            execute_command("INSERT INTO no_such_table VALUES (1)", {}, connection="WORKBENCH")
        except SQLServerQueryError as e:
            assert "WORKBENCH" in str(e)


class TestConnectionErrorReraised:
    """Lines 44, 60, 75, 95: SQLServerConnectionError raised inside engine.connect()
    must propagate unchanged (not wrapped as SQLServerQueryError)."""

    @pytest.fixture(autouse=True)
    def _patch_engine_connect(self, monkeypatch):
        """Provide an engine whose connect() raises SQLServerConnectionError —
        the exception must be inside the try block to hit the re-raise branch."""
        from unittest.mock import MagicMock
        from db.errors import SQLServerConnectionError
        from db.connection import _ENGINE_OVERRIDES

        bad_engine = MagicMock()
        bad_engine.connect.side_effect = SQLServerConnectionError("connection lost")
        bad_engine.begin.side_effect = SQLServerConnectionError("connection lost")
        _ENGINE_OVERRIDES[("CONNFAIL", "")] = bad_engine
        yield
        _ENGINE_OVERRIDES.pop(("CONNFAIL", ""), None)

    def test_execute_reraises_connection_error(self):
        from db.errors import SQLServerConnectionError
        with pytest.raises(SQLServerConnectionError):
            execute("SELECT 1", connection="CONNFAIL")

    def test_execute_one_reraises_connection_error(self):
        from db.errors import SQLServerConnectionError
        with pytest.raises(SQLServerConnectionError):
            execute_one("SELECT 1", connection="CONNFAIL")

    def test_execute_scalar_reraises_connection_error(self):
        from db.errors import SQLServerConnectionError
        with pytest.raises(SQLServerConnectionError):
            execute_scalar("SELECT 1", connection="CONNFAIL")

    def test_execute_command_reraises_connection_error(self):
        from db.errors import SQLServerConnectionError
        with pytest.raises(SQLServerConnectionError):
            execute_command("INSERT INTO t VALUES (1)", connection="CONNFAIL")
