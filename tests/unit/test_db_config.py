"""Tests for db/config.py — connection string resolution from env vars.

These tests cover the only logic that can be tested without SQL Server:
the rules for reading env vars and building the SQLAlchemy URL.
No database required — all tests run offline.

What we're protecting:
- Missing required env vars raise a clear error (not a cryptic driver error)
- Auth type is validated before attempting a connection
- The connection string the pyodbc dialect renders from the URL keeps a password
  holding ODBC delimiters intact, and asks for Trusted_Connection under Windows auth
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.mssql.pyodbc import MSDialect_pyodbc

from db.config import build_sqlalchemy_url, get_connection_config
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

    def test_blank_port_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("MSSQL_TEST_SERVER", "myserver")
        monkeypatch.setenv("MSSQL_TEST_USER", "sa")
        monkeypatch.setenv("MSSQL_TEST_PASSWORD", "secret")
        monkeypatch.setenv("MSSQL_TEST_PORT", "")

        assert get_connection_config("TEST")["port"] == "1433"

    def test_encrypt_defaults_to_no_and_is_overridable(self, monkeypatch):
        monkeypatch.setenv("MSSQL_TEST_SERVER", "myserver")
        monkeypatch.setenv("MSSQL_TEST_USER", "sa")
        monkeypatch.setenv("MSSQL_TEST_PASSWORD", "secret")
        monkeypatch.delenv("MSSQL_ENCRYPT", raising=False)

        assert get_connection_config("TEST")["encrypt"] == "no"

        monkeypatch.setenv("MSSQL_ENCRYPT", "yes")
        assert get_connection_config("TEST")["encrypt"] == "yes"


class TestBuildSqlalchemyUrl:
    def _cfg(self, **overrides):
        base = {
            "name": "TEST", "server": "myserver", "port": "1433",
            "database": "mydb", "driver": "ODBC Driver 18 for SQL Server",
            "trust_cert": "yes", "encrypt": "no", "timeout": "30",
            "auth_type": "SQL", "user": "sa", "password": "secret",
        }
        return {**base, **overrides}

    def _connection_string(self, url) -> str:
        return MSDialect_pyodbc().create_connect_args(url)[0][0]

    def test_sql_auth_carries_target_credentials_and_driver(self):
        url = build_sqlalchemy_url(self._cfg())
        assert url.drivername == "mssql+pyodbc"
        assert (url.username, url.password) == ("sa", "secret")
        assert (url.host, url.port, url.database) == ("myserver", 1433, "mydb")
        assert url.query["driver"] == "ODBC Driver 18 for SQL Server"
        assert url.query["Encrypt"] == "no"

    def test_password_holding_odbc_delimiters_survives(self):
        url = build_sqlalchemy_url(self._cfg(password="pa;ss{word"))
        assert "PWD={pa;ss{word}" in self._connection_string(url)

    def test_windows_auth_sends_no_username(self):
        cfg = {k: v for k, v in self._cfg().items() if k not in ("user", "password")}
        cfg["auth_type"] = "WINDOWS"
        url = build_sqlalchemy_url(cfg)
        assert url.username is None
        assert "Trusted_Connection=Yes" in self._connection_string(url)

    def test_database_override_replaces_default(self):
        assert build_sqlalchemy_url(self._cfg(), database="master").database == "master"

    def test_encrypt_reaches_the_connection_string(self):
        url = build_sqlalchemy_url(self._cfg(encrypt="yes"))
        assert "Encrypt=yes" in self._connection_string(url)

    def test_no_database_anywhere_raises(self):
        with pytest.raises(SQLServerConfigurationError, match="DATABASE"):
            build_sqlalchemy_url(self._cfg(database=""))
