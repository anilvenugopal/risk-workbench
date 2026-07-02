"""Unit tests for db/connection.py.

Tests get_engine (override path), get_connection context manager,
test_connection probe, and dispose_all — all using a SQLite engine
injected via register_engine so no real SQL Server is needed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from db.connection import (
    register_engine,
    get_engine,
    get_connection,
    test_connection as probe_connection,
    dispose_all,
    _ENGINE_OVERRIDES,
    _ENGINES,
)
from db.errors import SQLServerConnectionError


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Ensure engine overrides don't leak between tests."""
    _ENGINE_OVERRIDES.clear()
    _ENGINES.clear()
    yield
    _ENGINE_OVERRIDES.clear()
    _ENGINES.clear()


@pytest.fixture
def sqlite_engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE t (x INTEGER)"))
        conn.execute(text("INSERT INTO t VALUES (42)"))
    return eng


class TestRegisterAndGetEngine:
    def test_registered_engine_returned_by_get_engine(self, sqlite_engine):
        register_engine("TEST", sqlite_engine)
        assert get_engine("TEST") is sqlite_engine

    def test_connection_name_is_case_insensitive(self, sqlite_engine):
        register_engine("workbench", sqlite_engine)
        assert get_engine("WORKBENCH") is sqlite_engine

    def test_database_param_scopes_separately(self, sqlite_engine):
        eng2 = create_engine("sqlite:///:memory:")
        register_engine("TEST", sqlite_engine)
        register_engine("TEST", eng2, database="other")
        assert get_engine("TEST") is sqlite_engine
        assert get_engine("TEST", database="other") is eng2


class TestGetConnection:
    def test_yields_working_connection(self, sqlite_engine):
        register_engine("TEST", sqlite_engine)
        with get_connection("TEST") as conn:
            rows = conn.execute(text("SELECT x FROM t")).fetchall()
        assert rows[0][0] == 42

    def test_connection_closed_after_context_exit(self, sqlite_engine):
        register_engine("TEST", sqlite_engine)
        captured = []
        with get_connection("TEST") as conn:
            captured.append(conn)
        assert captured[0].closed

    def test_connection_error_raises_sqlserver_error(self, monkeypatch):
        from db import connection as conn_mod
        broken = create_engine("sqlite:///:memory:")
        register_engine("BROKEN", broken)

        original_get_engine = conn_mod.get_engine

        def _raise(name, database=None):
            raise SQLServerConnectionError("simulated connection failure")

        monkeypatch.setattr(conn_mod, "get_engine", _raise)
        with pytest.raises(SQLServerConnectionError):
            with conn_mod.get_connection("BROKEN"):
                pass


class TestConnectionProbe:
    def test_probe_returns_true_on_working_engine(self, sqlite_engine):
        register_engine("TEST", sqlite_engine)
        assert probe_connection("TEST") is True

    def test_probe_returns_false_on_connection_error(self, monkeypatch):
        from db import connection as conn_mod
        from contextlib import contextmanager

        @contextmanager
        def _fail(name, database=None):
            raise SQLServerConnectionError("down")
            yield  # make it a generator

        monkeypatch.setattr(conn_mod, "get_connection", _fail)
        assert probe_connection("ANYTHING") is False

    def test_probe_never_raises_on_unexpected_error(self, monkeypatch):
        from db import connection as conn_mod
        from contextlib import contextmanager

        @contextmanager
        def _boom(name, database=None):
            raise RuntimeError("unexpected")
            yield

        monkeypatch.setattr(conn_mod, "get_connection", _boom)
        result = probe_connection("X")
        assert result is False


class TestDisposeAll:
    def test_disposes_without_error_when_empty(self):
        dispose_all()  # should not raise

    def test_clears_engine_cache(self, sqlite_engine):
        _ENGINES[("TEST", "")] = sqlite_engine
        dispose_all()
        assert ("TEST", "") not in _ENGINES


class TestPoolKwargs:
    def test_returns_expected_keys(self):
        from db.connection import _pool_kwargs
        kwargs = _pool_kwargs()
        assert "pool_size" in kwargs
        assert "max_overflow" in kwargs
        assert "pool_timeout" in kwargs
        assert "pool_recycle" in kwargs
        assert kwargs["pool_pre_ping"] is True

    def test_env_override_applied(self, monkeypatch):
        from db.connection import _pool_kwargs
        monkeypatch.setenv("MSSQL_POOL_SIZE", "10")
        kwargs = _pool_kwargs()
        assert kwargs["pool_size"] == 10


class TestEngineCache:
    def test_engine_cached_in_engines_dict(self, sqlite_engine, monkeypatch):
        """Line 55-56: second get_engine call for same key returns cached engine."""
        from db import connection as conn_mod

        # Bypass real SQL Server creation by patching build/config functions
        monkeypatch.setattr(conn_mod, "get_connection_config",
                            lambda name: {"auth_type": "SQL", "name": name})
        monkeypatch.setattr(conn_mod, "build_sqlalchemy_url",
                            lambda cfg, database=None: "sqlite:///:memory:")
        monkeypatch.setattr(conn_mod, "_pool_kwargs", lambda: {})

        # First call: creates and caches
        eng1 = conn_mod.get_engine("CACHED")
        # Second call: must return cached (not create new)
        eng2 = conn_mod.get_engine("CACHED")
        assert eng1 is eng2
        assert ("CACHED", "") in _ENGINES


class TestGetConnectionConnIsNone:
    def test_connect_failure_raises_sqlserver_error(self, monkeypatch):
        """Lines 93-97: when engine.connect() raises, conn is still None,
        so the except branch wraps it as SQLServerConnectionError."""
        from db import connection as conn_mod
        from unittest.mock import MagicMock

        bad_engine = MagicMock()
        bad_engine.connect.side_effect = OSError("network error")

        monkeypatch.setattr(conn_mod, "get_engine", lambda name, database=None: bad_engine)
        with pytest.raises(SQLServerConnectionError, match="Failed to connect"):
            with conn_mod.get_connection("ANY"):
                pass

    def test_exception_during_yield_reraises_as_is(self, sqlite_engine):
        """Line 98: exception raised inside the `with` block (conn is not None)
        propagates unchanged — not wrapped as SQLServerConnectionError."""
        register_engine("TEST", sqlite_engine)
        with pytest.raises(ValueError, match="body error"):
            with get_connection("TEST"):
                raise ValueError("body error")
