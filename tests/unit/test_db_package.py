"""Tests for the `db` package's pure-Python logic.

Connection config resolution, ODBC/SQLAlchemy string building, and the
trusted-script parameter substitution — no engine, no database. The safe
execution path (execute/execute_one/execute_scalar/execute_command) runs
against the real driver in tests/sqlserver/test_db_package.py.
"""

import pytest

import db
from db import scripts

# ── config / auth ──────────────────────────────────────────────────────────────

@pytest.fixture
def sql_env(monkeypatch):
    monkeypatch.setenv("MSSQL_WB_SERVER", "localhost")
    monkeypatch.setenv("MSSQL_WB_USER", "sa")
    monkeypatch.setenv("MSSQL_WB_PASSWORD", "p@ss")
    monkeypatch.setenv("MSSQL_WB_DATABASE", "raw_db")
    monkeypatch.setenv("MSSQL_AD_SERVER", "host.corp")
    monkeypatch.setenv("MSSQL_AD_AUTH_TYPE", "WINDOWS")


def test_sql_auth_config_and_string(sql_env):
    cfg = db.get_connection_config("WB")
    assert cfg["auth_type"] == "SQL" and cfg["user"] == "sa"
    odbc = db.build_odbc_connection_string(cfg)
    assert "UID=sa" in odbc and "PWD=p@ss" in odbc
    assert "Trusted_Connection" not in odbc
    assert db.build_sqlalchemy_url(cfg).startswith("mssql+pyodbc:///?odbc_connect=")


def test_windows_auth_config_and_string(sql_env):
    cfg = db.get_connection_config("AD")
    assert cfg["auth_type"] == "WINDOWS"
    odbc = db.build_odbc_connection_string(cfg)
    assert "Trusted_Connection=yes" in odbc and "UID=" not in odbc


def test_missing_var_raises(monkeypatch):
    monkeypatch.delenv("MSSQL_GHOST_SERVER", raising=False)
    with pytest.raises(db.SQLServerConfigurationError) as e:
        db.get_connection_config("GHOST")
    assert "MSSQL_GHOST_SERVER" in str(e.value)


def test_invalid_auth_type(monkeypatch):
    monkeypatch.setenv("MSSQL_X_SERVER", "h")
    monkeypatch.setenv("MSSQL_X_AUTH_TYPE", "LDAP")
    with pytest.raises(db.SQLServerConfigurationError):
        db.get_connection_config("X")


def test_empty_connection_name_raises():
    with pytest.raises(db.SQLServerConfigurationError):
        db.get_connection_config("")


# ── trusted-script path: substitution + injection containment ──────────────────

def test_value_context_quotes_and_escapes():
    out = scripts._substitute_named_parameters(
        "WHERE id = {{ uid }} AND name = {{ nm }}", {"uid": 123, "nm": "O'Brien"})
    assert out == "WHERE id = 123 AND name = 'O''Brien'"


def test_identifier_context_raw_substitution():
    assert scripts._substitute_named_parameters(
        "USE [{{ db }}]", {"db": "EDM_202503"}) == "USE [EDM_202503]"
    assert scripts._substitute_named_parameters(
        "FROM Data_{{ d }}_Work", {"d": "20250115"}) == "FROM Data_20250115_Work"


def test_value_context_injection_contained():
    out = scripts._substitute_named_parameters(
        "WHERE x = {{ v }}", {"v": "a'; DROP TABLE t;--"})
    assert out == "WHERE x = 'a''; DROP TABLE t;--'"


def test_identifier_context_rejects_unsafe():
    with pytest.raises(db.SQLServerQueryError):
        scripts._substitute_named_parameters("USE [{{ db }}]", {"db": "a]; DROP TABLE t--"})


def test_missing_param_raises():
    with pytest.raises(db.SQLServerQueryError):
        scripts._substitute_named_parameters("WHERE id = {{ missing }}", {"other": 1})
