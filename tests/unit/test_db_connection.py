"""Unit tests for db/connection.py.

Engine creation, caching, the get_connection context manager, the
test_connection probe, and dispose_all — engine construction is stubbed with
MagicMock via monkeypatch, so no database is involved (Article 12 tier 1).
Real-driver behavior lives in tests/sqlserver.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from db import connection as conn_mod
from db.connection import (
    _ENGINES,
    dispose_all,
    get_connection,
)
from db.connection import (
    test_connection as probe_connection,
)
from db.errors import SQLServerConnectionError


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    _ENGINES.clear()
    yield
    _ENGINES.clear()


@pytest.fixture()
def stub_engine_creation(monkeypatch):
    """Route get_engine's creation path through MagicMock engines: config and
    URL building are stubbed, create_engine returns a fresh MagicMock, and the
    query-timing listener (which requires a real Engine) is skipped."""
    monkeypatch.setattr(conn_mod, "get_connection_config",
                        lambda name: {"auth_type": "SQL", "name": name.upper()})
    monkeypatch.setattr(conn_mod, "build_sqlalchemy_url",
                        lambda cfg, database=None: f"stub://{cfg['name']}/{database or ''}")
    monkeypatch.setattr(conn_mod, "_pool_kwargs", lambda: {})
    monkeypatch.setattr(conn_mod, "create_engine",
                        lambda url, **kw: MagicMock(name=f"engine[{url}]"))
    monkeypatch.setattr(conn_mod, "_attach_query_timing", lambda eng: None)


class TestGetEngine:
    def test_connection_name_is_case_insensitive(self, stub_engine_creation):
        assert conn_mod.get_engine("workbench") is conn_mod.get_engine("WORKBENCH")

    def test_database_param_scopes_separately(self, stub_engine_creation):
        default = conn_mod.get_engine("TEST")
        other = conn_mod.get_engine("TEST", database="other")
        assert default is not other
        assert conn_mod.get_engine("TEST", database="other") is other

    def test_engine_cached_in_engines_dict(self, stub_engine_creation):
        eng1 = conn_mod.get_engine("CACHED")
        eng2 = conn_mod.get_engine("CACHED")
        assert eng1 is eng2
        assert ("CACHED", "") in _ENGINES


class TestGetConnection:
    def test_yields_engine_connection_and_closes_it(self, monkeypatch):
        engine = MagicMock()
        monkeypatch.setattr(conn_mod, "get_engine",
                            lambda name, database=None: engine)
        with get_connection("TEST") as conn:
            assert conn is engine.connect.return_value
            conn.close.assert_not_called()
        conn.close.assert_called_once()

    def test_connection_error_raises_sqlserver_error(self, monkeypatch):
        def _raise(name, database=None):
            raise SQLServerConnectionError("simulated connection failure")

        monkeypatch.setattr(conn_mod, "get_engine", _raise)
        with pytest.raises(SQLServerConnectionError), conn_mod.get_connection("BROKEN"):
            pass

    def test_connect_failure_raises_sqlserver_error(self, monkeypatch):
        """When engine.connect() raises, conn is still None, so the except
        branch wraps it as SQLServerConnectionError."""
        bad_engine = MagicMock()
        bad_engine.connect.side_effect = OSError("network error")

        monkeypatch.setattr(conn_mod, "get_engine",
                            lambda name, database=None: bad_engine)
        with pytest.raises(SQLServerConnectionError, match="Failed to connect"), \
                conn_mod.get_connection("ANY"):
            pass

    def test_exception_during_yield_reraises_as_is(self, monkeypatch):
        """An exception raised inside the `with` block (conn is not None)
        propagates unchanged — not wrapped as SQLServerConnectionError."""
        monkeypatch.setattr(conn_mod, "get_engine",
                            lambda name, database=None: MagicMock())
        with pytest.raises(ValueError, match="body error"), get_connection("TEST"):
            raise ValueError("body error")


class TestConnectionProbe:
    def test_probe_returns_true_on_working_engine(self, monkeypatch):
        monkeypatch.setattr(conn_mod, "get_engine",
                            lambda name, database=None: MagicMock())
        assert probe_connection("TEST") is True

    def test_probe_returns_false_on_connection_error(self, monkeypatch):
        @contextmanager
        def _fail(name, database=None):
            raise SQLServerConnectionError("down")
            yield  # make it a generator

        monkeypatch.setattr(conn_mod, "get_connection", _fail)
        assert probe_connection("ANYTHING") is False

    def test_probe_never_raises_on_unexpected_error(self, monkeypatch):
        @contextmanager
        def _boom(name, database=None):
            raise RuntimeError("unexpected")
            yield

        monkeypatch.setattr(conn_mod, "get_connection", _boom)
        assert probe_connection("X") is False


class TestDisposeAll:
    def test_disposes_without_error_when_empty(self):
        dispose_all()  # should not raise

    def test_disposes_and_clears_engine_cache(self):
        engine = MagicMock()
        _ENGINES[("TEST", "")] = engine
        dispose_all()
        engine.dispose.assert_called_once()
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
