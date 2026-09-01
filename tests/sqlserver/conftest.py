"""SQL Server tier fixtures — a dedicated, disposable test database.

This tier owns every test that executes application SQL (Constitution
Article 12 v4.0.0). At session start this conftest:

  1. points the WORKBENCH connection at ``rwb_workbench_tests`` by setting
     ``MSSQL_WORKBENCH_DATABASE`` for the test process only,
  2. drops and recreates that database on the configured SQL Server (via the
     WORKBENCH login against ``master``; dev and CI use ``sa``),
  3. runs ``alembic upgrade head`` against it — the migration also seeds every
     kind table.

The developer databases (``rwb_workbench``, ``rwb_exposure``, ``rwb_loss``)
are never written: ``_wipe_workbench`` refuses to run unless the connected
database is ``rwb_workbench_tests``.

Per-test isolation: the ``workbench_db`` fixture deletes every row from every
non-kind table before seeding two analysts, so each test starts from the empty
state the migration leaves behind. The schema is always the full Alembic head.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

TEST_DATABASE = "rwb_workbench_tests"
_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def _test_database(request):
    """Create ``rwb_workbench_tests`` from scratch and point WORKBENCH at it."""
    if not request.config.getoption("--run-sqlserver"):
        yield
        return

    from db.config import build_sqlalchemy_url, get_connection_config
    from db.connection import dispose_all

    os.environ["MSSQL_WORKBENCH_DATABASE"] = TEST_DATABASE
    dispose_all()  # drop any engine built before the override took effect

    # CREATE/DROP DATABASE cannot run inside a transaction — AUTOCOMMIT.
    master = create_engine(
        build_sqlalchemy_url(get_connection_config("WORKBENCH"), database="master"),
        isolation_level="AUTOCOMMIT", poolclass=NullPool,
    )
    try:
        with master.connect() as conn:
            conn.execute(text(
                f"IF DB_ID('{TEST_DATABASE}') IS NOT NULL "
                f"ALTER DATABASE [{TEST_DATABASE}] SET SINGLE_USER "
                f"WITH ROLLBACK IMMEDIATE"))
            conn.execute(text(f"DROP DATABASE IF EXISTS [{TEST_DATABASE}]"))
            conn.execute(text(f"CREATE DATABASE [{TEST_DATABASE}]"))
    finally:
        master.dispose()

    from alembic.config import Config

    from alembic import command

    # A bare Config (no ini file): alembic/env.py resolves the URL from the db
    # package itself, and skipping the ini avoids fileConfig(), which would
    # disable every existing logger and break caplog-based tests.
    cfg = Config()
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    command.upgrade(cfg, "head")

    yield
    dispose_all()


def _wipe_workbench() -> None:
    """Delete every row from every non-kind table in the test database.

    Constraints are disabled per table for the deletes (the submission
    self-link FK and the FK graph make a single correct delete order fragile),
    then re-enabled WITH CHECK — cheap, since every table is empty.
    """
    from db import get_engine

    engine = get_engine("WORKBENCH")
    with engine.begin() as conn:
        current = conn.execute(text("SELECT DB_NAME()")).scalar()
        if current != TEST_DATABASE:
            raise RuntimeError(
                f"Refusing to wipe '{current}' — the SQL Server tier only ever "
                f"wipes {TEST_DATABASE}. Check MSSQL_WORKBENCH_DATABASE.")
        tables = [r[0] for r in conn.execute(text(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = 'dbo' "
            "AND TABLE_NAME <> 'alembic_version' "
            "AND TABLE_NAME NOT LIKE '%\\_kind' ESCAPE '\\'"))]
        for t in tables:
            conn.execute(text(f"ALTER TABLE [{t}] NOCHECK CONSTRAINT ALL"))
        for t in tables:
            conn.execute(text(f"DELETE FROM [{t}]"))
        for t in tables:
            conn.execute(text(f"ALTER TABLE [{t}] WITH CHECK CHECK CONSTRAINT ALL"))


def _seed_analysts() -> SimpleNamespace:
    from db import execute_command, get_engine

    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    for uid, tag in ((user_a, "a"), (user_b, "b")):
        execute_command(
            "INSERT INTO app_user (id, email, display_name, "
            "must_change_password, is_active) VALUES (:id, :email, :dn, 0, 1)",
            {"id": uid, "email": f"analyst.{tag}@example.com",
             "dn": f"Analyst {tag.upper()}"},
            connection="WORKBENCH")
    return SimpleNamespace(engine=get_engine("WORKBENCH"),
                           user_a=user_a, user_b=user_b)


@pytest.fixture()
def workbench_db() -> SimpleNamespace:
    """Empty WORKBENCH test database + two analysts (Analyst A / Analyst B)."""
    _wipe_workbench()
    return _seed_analysts()


def edm_with_portfolios(count: int = 2) -> tuple[str, list[str]]:
    """Insert one ready EDM with ``count`` portfolios; return their ids.

    Shared by the geohaz service, gateway, route, worker, and poller tests.
    """
    from db import execute_command

    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status, inserted_at, updated_at) "
        "VALUES (:id, 'GeoHaz EDM', 'ready', '2026-08-13', '2026-08-13')",
        {"id": edm_id}, connection="WORKBENCH")
    portfolio_ids: list[str] = []
    for number in range(1, count + 1):
        portfolio_id = str(uuid.uuid4())
        portfolio_ids.append(portfolio_id)
        execute_command(
            "INSERT INTO irp_portfolio "
            "(id, edm_id, name, irp_id, inserted_at, updated_at) "
            "VALUES (:id, :edm, :name, :irp, '2026-08-13', '2026-08-13')",
            {"id": portfolio_id, "edm": edm_id, "name": f"Portfolio {number}",
             "irp": str(100 + number)},
            connection="WORKBENCH")
    return edm_id, portfolio_ids
