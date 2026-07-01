"""Tests for db/config.py — connection string resolution from env vars.

These tests cover the only logic that can be tested without SQL Server:
the rules for reading env vars and building ODBC connection strings.
No database required — all tests run offline.

What we're protecting:
- Missing required env vars raise a clear error (not a cryptic driver error)
- Auth type is validated before attempting a connection
- The ODBC string is correctly percent-encoded in the SQLAlchemy URL
  (a miscoded URL fails silently because ConfigParser treats % as interpolation)
"""

from __future__ import annotations

import urllib.parse

import pytest

from db.config import (
    build_odbc_connection_string,
    build_sqlalchemy_url,
    get_connection_config,
)
from db.errors import SQLServerConfigurationError


class TestGetConnectionConfig:
    def test_sql_auth_resolves_server_user_password(self, monkeypatch):
        monkeypatch.setenv("MSSQL_TEST_SERVER", "myserver")
        monkeypatch.setenv("MSSQL_TEST_USER", "sa")
        monkeypatch.setenv("MSSQL_TEST_PASSWORD", "secret")

        cfg = get_connection_config("TEST")

        assert cfg["server"] == "myserver"
        assert cfg["user"] == "sa"
        assert cfg["auth_type"] == "SQL"

    def test_missing_server_raises_with_var_name_in_message(self, monkeypatch):
        monkeypatch.delenv("MSSQL_TEST_SERVER", raising=False)
        monkeypatch.setenv("MSSQL_TEST_USER", "sa")
        monkeypatch.setenv("MSSQL_TEST_PASSWORD", "secret")

        with pytest.raises(SQLServerConfigurationError, match="SERVER"):
            get_connection_config("TEST")

    def test_missing_password_raises_with_var_name_in_message(self, monkeypatch):
        monkeypatch.setenv("MSSQL_TEST_SERVER", "myserver")
        monkeypatch.setenv("MSSQL_TEST_USER", "sa")
        monkeypatch.delenv("MSSQL_TEST_PASSWORD", raising=False)

        with pytest.raises(SQLServerConfigurationError, match="PASSWORD"):
            get_connection_config("TEST")

    def test_empty_connection_name_raises(self):
        with pytest.raises(SQLServerConfigurationError):
            get_connection_config("")

    def test_invalid_auth_type_raises(self, monkeypatch):
        monkeypatch.setenv("MSSQL_TEST_SERVER", "myserver")
        monkeypatch.setenv("MSSQL_TEST_AUTH_TYPE", "KERBEROS")

        with pytest.raises(SQLServerConfigurationError, match="KERBEROS"):
            get_connection_config("TEST")

    def test_lowercase_connection_name_is_uppercased(self, monkeypatch):
        monkeypatch.setenv("MSSQL_WORKBENCH_SERVER", "myserver")
        monkeypatch.setenv("MSSQL_WORKBENCH_USER", "sa")
        monkeypatch.setenv("MSSQL_WORKBENCH_PASSWORD", "secret")

        cfg = get_connection_config("workbench")
        assert cfg["name"] == "WORKBENCH"

    def test_default_port_is_1433(self, monkeypatch):
        monkeypatch.setenv("MSSQL_TEST_SERVER", "myserver")
        monkeypatch.setenv("MSSQL_TEST_USER", "sa")
        monkeypatch.setenv("MSSQL_TEST_PASSWORD", "secret")
        monkeypatch.delenv("MSSQL_TEST_PORT", raising=False)

        assert get_connection_config("TEST")["port"] == "1433"


class TestBuildOdbcConnectionString:
    def _cfg(self, **overrides):
        base = {
            "name": "TEST", "server": "myserver", "port": "1433",
            "database": "mydb", "driver": "ODBC Driver 18 for SQL Server",
            "trust_cert": "yes", "timeout": "30",
            "auth_type": "SQL", "user": "sa", "password": "secret",
        }
        return {**base, **overrides}

    def test_sql_auth_puts_uid_and_pwd_in_string(self):
        cs = build_odbc_connection_string(self._cfg())
        assert "UID=sa" in cs
        assert "PWD=secret" in cs
        assert "Trusted_Connection" not in cs

    def test_windows_auth_uses_trusted_connection(self):
        cfg = {k: v for k, v in self._cfg().items() if k not in ("user", "password")}
        cfg["auth_type"] = "WINDOWS"
        cs = build_odbc_connection_string(cfg)
        assert "Trusted_Connection=yes" in cs
        assert "UID" not in cs

    def test_database_override_replaces_default(self):
        cs = build_odbc_connection_string(self._cfg(), database="master")
        assert "DATABASE=master" in cs

    def test_sqlalchemy_url_percent_encodes_the_odbc_string(self):
        url = build_sqlalchemy_url(self._cfg())
        assert url.startswith("mssql+pyodbc:///?odbc_connect=")
        decoded = urllib.parse.unquote_plus(url.split("odbc_connect=")[1])
        assert "UID=sa" in decoded
        assert "PWD=secret" in decoded


class TestApplyScope:
    """apply_scope() rewrites SQL to add a customer_id filter.

    These tests verify the scoping logic without touching any database.
    apply_scope() is a pure SQL-string transformation.
    """

    def test_single_customer_adds_in_clause(self):
        from db.scope import apply_scope
        sql, params = apply_scope("SELECT * FROM submission", customer_ids=[42])
        assert "IN" in sql.upper()
        assert 42 in params.values()

    def test_empty_customer_ids_returns_where_1_0(self):
        from db.scope import apply_scope
        sql, params = apply_scope("SELECT * FROM submission", customer_ids=[])
        assert "1=0" in sql

    def test_admin_bypass_returns_original_sql_unchanged(self):
        from db.scope import apply_scope
        original = "SELECT * FROM submission"
        sql, params = apply_scope(original, customer_ids=[1, 2], is_admin=True)
        assert sql == original
        assert params == {}

    def test_multiple_customer_ids_all_bound_as_parameters(self):
        from db.scope import apply_scope
        sql, params = apply_scope("SELECT * FROM submission", customer_ids=[1, 2, 3])
        assert len([v for v in params.values() if v in (1, 2, 3)]) == 3
        assert "IN" in sql.upper()
