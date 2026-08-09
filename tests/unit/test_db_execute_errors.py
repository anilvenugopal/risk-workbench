"""Unit tests for the connection-error re-raise paths in db/execute.py.

A SQLServerConnectionError raised inside engine.connect()/begin() must
propagate unchanged (not wrapped as SQLServerQueryError). The engine is a
MagicMock injected via monkeypatch — no database. The query-error wrapping
paths (bad SQL against the real driver) live in
tests/sqlserver/test_db_execute_errors.py.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from db.errors import SQLServerConnectionError
from db.execute import execute, execute_command, execute_one, execute_scalar


@pytest.fixture(autouse=True)
def _failing_engine(monkeypatch):
    """An engine whose connect()/begin() raise SQLServerConnectionError — the
    exception fires inside the try block, hitting the re-raise branch."""
    # importlib, because db/__init__.py re-exports the execute *function*,
    # which shadows the db.execute submodule as an attribute of the package.
    execute_mod = importlib.import_module("db.execute")

    bad_engine = MagicMock()
    bad_engine.connect.side_effect = SQLServerConnectionError("connection lost")
    bad_engine.begin.side_effect = SQLServerConnectionError("connection lost")
    monkeypatch.setattr(execute_mod, "get_engine",
                        lambda name, database=None: bad_engine)


def test_execute_reraises_connection_error():
    with pytest.raises(SQLServerConnectionError):
        execute("SELECT 1", connection="CONNFAIL")


def test_execute_one_reraises_connection_error():
    with pytest.raises(SQLServerConnectionError):
        execute_one("SELECT 1", connection="CONNFAIL")


def test_execute_scalar_reraises_connection_error():
    with pytest.raises(SQLServerConnectionError):
        execute_scalar("SELECT 1", connection="CONNFAIL")


def test_execute_command_reraises_connection_error():
    with pytest.raises(SQLServerConnectionError):
        execute_command("INSERT INTO t VALUES (1)", connection="CONNFAIL")
