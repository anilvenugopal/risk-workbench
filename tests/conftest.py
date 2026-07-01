"""Root conftest — shared fixtures and pytest configuration.

Three test tiers (Constitution Article 12):
  tests/unit/      — fast, no external deps. Default CI.
  tests/sqlserver/ — requires live SQL Server. Mark: @pytest.mark.sqlserver
  tests/irp/       — requires sandbox IRP. Mark: @pytest.mark.irp

The sqlserver and irp suites are opt-in: they skip unless the corresponding
flag is passed.

    pytest tests/unit                          # unit only (default)
    pytest tests/sqlserver --run-sqlserver     # SQL Server suite
    pytest tests/irp --run-irp                 # IRP suite
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from db.connection import register_engine


def pytest_addoption(parser):
    parser.addoption("--run-sqlserver", action="store_true", default=False,
                     help="Run SQL Server integration tests")
    parser.addoption("--run-irp", action="store_true", default=False,
                     help="Run IRP integration tests (sandbox)")


def pytest_configure(config):
    config.addinivalue_line("markers",
        "sqlserver: requires a live SQL Server connection")
    config.addinivalue_line("markers",
        "irp: requires a sandbox IRP environment")


def pytest_collection_modifyitems(config, items):
    skip_sql = pytest.mark.skip(reason="Pass --run-sqlserver to run")
    skip_irp = pytest.mark.skip(reason="Pass --run-irp to run")
    for item in items:
        if "sqlserver" in item.keywords and not config.getoption("--run-sqlserver"):
            item.add_marker(skip_sql)
        if "irp" in item.keywords and not config.getoption("--run-irp"):
            item.add_marker(skip_irp)


# ── SQLite engine fixture (unit tests) ───────────────────────────────────────
# Injects a SQLite engine into the db/ package for the WORKBENCH connection.
# Unit tests never touch SQL Server.

@pytest.fixture()
def sqlite_engine() -> Engine:
    """In-memory SQLite engine with the WORKBENCH schema applied."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    # Register so db.get_engine("WORKBENCH") returns this in tests.
    register_engine("WORKBENCH", engine)
    # TODO (Iteration 0): run DDL here once models exist.
    # with engine.begin() as conn:
    #     Base.metadata.create_all(conn)
    yield engine
    engine.dispose()


@pytest.fixture()
def sqlite_conn(sqlite_engine):
    """Raw SQLite connection for a single test (auto-rollback)."""
    with sqlite_engine.begin() as conn:
        yield conn
        conn.rollback()
