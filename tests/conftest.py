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

import sqlite3
import uuid
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from db.connection import _ENGINE_OVERRIDES, dispose_all, register_engine
from tests.iteration1_mirror import (
    IRP_ANALYSIS_STATUS_SEED,
    IRP_JOB_RESOURCE_TYPE_SEED,
    IRP_JOB_TYPE_SEED,
    ITERATION1_SCHEMA,
    ITERATION2_SCHEMA,
    ITERATION3_SCHEMA,
    ITERATION4_SCHEMA,
    RWB_JOB_REQUESTOR_TYPE_SEED,
    RWB_JOB_STATUS_SEED,
    RWB_JOB_TYPE_SEED,
    STATUS_SEED,
    TREATY_SEED,
)

# Python 3.12+ removed the implicit sqlite3 date/datetime adapters (fully gone in
# 3.14). The service layer binds native date/datetime (SQL Server wants those);
# register explicit adapters so the SQLite unit tier can store them as ISO text.
sqlite3.register_adapter(datetime, lambda v: v.isoformat(sep=" "))
sqlite3.register_adapter(date, lambda v: v.isoformat())


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


# ── Deterministic engine disposal (all tiers) ────────────────────────────────
# In-memory SQLite engines registered during a test keep their sqlite3
# connection open until GC, which Python 3.13+ surfaces as a noisy
# ResourceWarning (via pytest's unraisableexception plugin). Dispose and clear
# every registered/cached engine after each test so connections close
# deterministically and no engine state leaks across tests.

@pytest.fixture(autouse=True)
def _dispose_registered_engines():
    yield
    for engine in list(_ENGINE_OVERRIDES.values()):
        engine.dispose()
    _ENGINE_OVERRIDES.clear()
    dispose_all()


@pytest.fixture()
def fake_irp():
    """Inject an in-memory fake Risk Modeler as the active IRP gateway."""
    from app.services import irp_gateway
    from tests.unit.fakes.fake_irp import FakeIRP

    fake = FakeIRP()
    irp_gateway.configure(fake)
    yield fake
    irp_gateway.reset()


@pytest.fixture()
def drive(tmp_path, monkeypatch):
    """Create exposure files under the configured shared-drive root."""
    from app.config import settings

    root = tmp_path / "share"
    root.mkdir()
    for fname in ("edm1.bak", "edm2.bak", "rdm1.mdf", "rdm2.mdf"):
        (root / fname).write_text("x")
    (root / "deals" / "zephyr").mkdir(parents=True)
    monkeypatch.setattr(settings, "shared_drive_root", str(root))
    return root


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


# ── Iteration-1 submission data schema (unit tier) ───────────────────────────
# The portable SQLite mirror of the WORKBENCH tables the submission and entity
# services touch lives in tests/iteration1_mirror.py (single source, so the SQL
# Server drift guard validates the exact same shape). This fixture builds it,
# seeds the kind tables + two analysts, and registers it as WORKBENCH.


def _memory_engine() -> Engine:
    """In-memory SQLite the route tests can reach. StaticPool +
    check_same_thread=False, not the SQLite defaults: TestClient dispatches routes
    on a worker thread, and a per-thread connection would hand that thread its own
    empty database."""
    return create_engine("sqlite://", poolclass=StaticPool,
                         connect_args={"check_same_thread": False})


@pytest.fixture()
def iteration1_db() -> SimpleNamespace:
    """Build the Iteration-1 WORKBENCH schema in SQLite, seed the kind tables and
    two analysts, register it as the WORKBENCH connection, and return the two
    analyst ids. Engine disposal is handled by the autouse root fixture."""
    engine = _memory_engine()
    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    with engine.begin() as conn:
        for ddl in ITERATION1_SCHEMA:
            conn.execute(text(ddl))
        conn.execute(text(
            "INSERT INTO app_user (id, email, display_name) VALUES "
            "(:a, 'analyst.a@example.com', 'Analyst A'), "
            "(:b, 'analyst.b@example.com', 'Analyst B')"
        ), {"a": user_a, "b": user_b})
        for code, label, order in STATUS_SEED:
            conn.execute(text(
                "INSERT INTO submission_status_kind (code, label, sort_order) "
                "VALUES (:c, :l, :o)"), {"c": code, "l": label, "o": order})
        for code, label, order in TREATY_SEED:
            conn.execute(text(
                "INSERT INTO treaty_type_kind (code, label, sort_order) "
                "VALUES (:c, :l, :o)"), {"c": code, "l": label, "o": order})
    register_engine("WORKBENCH", engine)
    yield SimpleNamespace(engine=engine, user_a=user_a, user_b=user_b)
    engine.dispose()


# ── Iteration-2 schema (unit tier) ───────────────────────────────────────────
# The Iteration-1 WORKBENCH mirror PLUS the irp_job / rwb_job families and their
# five kind tables (single source: tests/iteration1_mirror.py). Seeds every kind
# table and two analysts, and registers it as the WORKBENCH connection.


@pytest.fixture()
def iteration2_db() -> SimpleNamespace:
    """Build the Iteration-1 + Iteration-2 + Iteration-3 WORKBENCH schema in SQLite
    (the dev DB is drop-create-seed, so services always see the full shape), seed
    the kind tables and two analysts, register it as WORKBENCH, and return the
    analyst ids. Engine disposal is handled by the autouse root fixture."""
    engine = _memory_engine()
    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    with engine.begin() as conn:
        for ddl in (*ITERATION1_SCHEMA, *ITERATION2_SCHEMA, *ITERATION3_SCHEMA,
                    *ITERATION4_SCHEMA):
            conn.execute(text(ddl))
        conn.execute(text(
            "INSERT INTO app_user (id, email, display_name) VALUES "
            "(:a, 'analyst.a@example.com', 'Analyst A'), "
            "(:b, 'analyst.b@example.com', 'Analyst B')"
        ), {"a": user_a, "b": user_b})
        _seed(conn, "submission_status_kind", STATUS_SEED)
        _seed(conn, "treaty_type_kind", TREATY_SEED)
        _seed(conn, "irp_job_type_kind", IRP_JOB_TYPE_SEED)
        _seed(conn, "irp_job_resource_type_kind", IRP_JOB_RESOURCE_TYPE_SEED)
        _seed(conn, "rwb_job_type_kind", RWB_JOB_TYPE_SEED)
        _seed(conn, "rwb_job_requestor_type_kind", RWB_JOB_REQUESTOR_TYPE_SEED)
        _seed(conn, "rwb_job_status_kind", RWB_JOB_STATUS_SEED)
        _seed(conn, "irp_analysis_status_kind", IRP_ANALYSIS_STATUS_SEED)
    register_engine("WORKBENCH", engine)
    yield SimpleNamespace(engine=engine, user_a=user_a, user_b=user_b)
    engine.dispose()


def _seed(conn, table: str, rows: list[tuple[str, str, int]]) -> None:
    """Insert (code, label, sort_order) kind rows. ``table`` is a trusted literal
    from the mirror module — never user input."""
    for code, label, order in rows:
        conn.execute(text(
            f"INSERT INTO {table} (code, label, sort_order) VALUES (:c, :l, :o)"
        ), {"c": code, "l": label, "o": order})
