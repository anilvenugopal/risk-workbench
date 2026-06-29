"""Tests for the `db` package.

These run **without** a SQL Server, pyodbc, or the ODBC driver: the connection
layer is exercised by injecting a sqlite SQLAlchemy engine via
`db.register_engine`, and the pure-Python config/substitution logic is tested
directly. (Integration tests against a real SQL Server container are a separate
suite — see test_sqlserver.sh in the source framework.)

Run: pytest test_db.py -v
"""

import os
import pytest
from sqlalchemy import create_engine, text

import db
from db import (execute, execute_one, execute_scalar, execute_command,
                apply_scope, scoped_execute, register_engine)
from db import scripts


# --------------------------------------------------------------------------- #
# config / auth                                                               #
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# safe path (bound params) against sqlite                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture
def wb(monkeypatch):
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE submission(id INT, customer_id INT, status_code TEXT)"))
        for r in [(1, 10, "open"), (2, 10, "closed"), (3, 20, "open"), (4, 30, "open")]:
            c.execute(text("INSERT INTO submission VALUES (:a,:b,:c)"),
                      {"a": r[0], "b": r[1], "c": r[2]})
    register_engine("WORKBENCH", eng)
    yield
    db._ENGINE_OVERRIDES.clear() if hasattr(db, "_ENGINE_OVERRIDES") else None


def test_execute_returns_list_of_dicts(wb):
    rows = execute("SELECT * FROM submission WHERE status_code = :s",
                   {"s": "open"}, connection="WORKBENCH")
    assert isinstance(rows, list) and all(isinstance(r, dict) for r in rows)
    assert len(rows) == 3


def test_execute_binds_not_interpolates(wb):
    # an injection attempt is treated as a literal bound value -> no rows
    rows = execute("SELECT * FROM submission WHERE status_code = :s",
                   {"s": "x' OR '1'='1"}, connection="WORKBENCH")
    assert rows == []


def test_execute_one_and_scalar(wb):
    assert execute_one("SELECT * FROM submission WHERE id = :id",
                       {"id": 2}, "WORKBENCH")["status_code"] == "closed"
    assert execute_one("SELECT * FROM submission WHERE id = :id",
                       {"id": 999}, "WORKBENCH") is None
    assert execute_scalar("SELECT COUNT(*) FROM submission", connection="WORKBENCH") == 4


def test_execute_command_rowcount(wb):
    n = execute_command("UPDATE submission SET status_code='void' WHERE customer_id=:c",
                        {"c": 30}, "WORKBENCH")
    assert n == 1


# --------------------------------------------------------------------------- #
# scope (RLS) on the safe path                                                #
# --------------------------------------------------------------------------- #
def test_scope_filters_by_customer(wb):
    rows = scoped_execute("SELECT * FROM submission", customer_ids=[10],
                          is_admin=False, connection="WORKBENCH")
    assert {r["customer_id"] for r in rows} == {10}


def test_scope_admin_bypass(wb):
    rows = scoped_execute("SELECT * FROM submission", customer_ids=[10],
                          is_admin=True, connection="WORKBENCH")
    assert len(rows) == 4


def test_scope_no_access_fails_closed(wb):
    rows = scoped_execute("SELECT * FROM submission", customer_ids=[],
                          is_admin=False, connection="WORKBENCH")
    assert rows == []


def test_scope_uses_bound_params_not_text():
    sql, params = apply_scope("SELECT * FROM submission", [10, 20])
    assert ":_scope_0" in sql and ":_scope_1" in sql
    assert params["_scope_0"] == 10 and params["_scope_1"] == 20
    # the numeric ids must not appear as literals in the SQL text
    assert " 10" not in sql and "(10" not in sql


# --------------------------------------------------------------------------- #
# trusted-script path: substitution + injection containment                   #
# --------------------------------------------------------------------------- #
def test_value_context_quotes_and_escapes():
    out = scripts._substitute_named_parameters(
        "WHERE id = {{ uid }} AND name = {{ nm }}", {"uid": 123, "nm": "O'Brien"})
    assert out == "WHERE id = 123 AND name = 'O''Brien'"


def test_identifier_context_raw_substitution():
    assert scripts._substitute_named_parameters("USE [{{ db }}]", {"db": "EDM_202503"}) == "USE [EDM_202503]"
    assert scripts._substitute_named_parameters(
        "FROM Data_{{ d }}_Work", {"d": "20250115"}) == "FROM Data_20250115_Work"


def test_value_context_injection_contained():
    out = scripts._substitute_named_parameters("WHERE x = {{ v }}", {"v": "a'; DROP TABLE t;--"})
    assert out == "WHERE x = 'a''; DROP TABLE t;--'"


def test_identifier_context_rejects_unsafe():
    with pytest.raises(db.SQLServerQueryError):
        scripts._substitute_named_parameters("USE [{{ db }}]", {"db": "a]; DROP TABLE t--"})


def test_missing_param_raises():
    with pytest.raises(db.SQLServerQueryError):
        scripts._substitute_named_parameters("WHERE id = {{ missing }}", {"other": 1})
