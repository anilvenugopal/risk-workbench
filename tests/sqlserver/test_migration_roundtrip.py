"""The Workbench rebuild path, end to end, against a throwaway database.

Rebuilding the schema is `alembic downgrade base && alembic upgrade head`
(issue #121) — the same two commands in WSL2, Docker and RHEL9. Nothing else
runs `downgrade()`, so without this test drift there is silent until a release
needs it. `tests/unit/test_architecture_guards.py` catches the common case
statically; this catches what only the server can tell us, such as a drop
ordered before the foreign key that depends on it.

Runs alembic as a subprocess: `alembic/env.py` resolves the connection from the
environment at import time, so a subprocess with MSSQL_WORKBENCH_DATABASE
overridden is what redirects it at a scratch database.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from db.config import build_sqlalchemy_url, get_connection_config

pytestmark = pytest.mark.sqlserver

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRATCH_DB = "rwb_workbench_roundtrip"


def _master_engine():
    config = get_connection_config("WORKBENCH")
    config["password"] = os.environ["MSSQL_SA_PASSWORD"]
    return create_engine(
        build_sqlalchemy_url(config, database="master"),
        isolation_level="AUTOCOMMIT",
    )


def _drop_scratch(conn):
    conn.execute(text(
        f"IF DB_ID('{_SCRATCH_DB}') IS NOT NULL "
        f"ALTER DATABASE [{_SCRATCH_DB}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE"))
    conn.execute(text(f"DROP DATABASE IF EXISTS [{_SCRATCH_DB}]"))


@pytest.fixture(scope="module")
def scratch_database():
    if not os.environ.get("MSSQL_SA_PASSWORD"):
        pytest.skip("MSSQL_SA_PASSWORD is not set — cannot create a scratch database")
    engine = _master_engine()
    try:
        with engine.connect() as conn:
            _drop_scratch(conn)
            conn.execute(text(f"CREATE DATABASE [{_SCRATCH_DB}]"))
        yield _SCRATCH_DB
        with engine.connect() as conn:
            _drop_scratch(conn)
    finally:
        engine.dispose()


def _run(args, database):
    env = {**os.environ, "MSSQL_WORKBENCH_DATABASE": database, "APP_ENV": "development"}
    return subprocess.run(args, cwd=_REPO_ROOT, env=env,
                          capture_output=True, text=True, timeout=600)


def _alembic(command, database):
    result = _run([sys.executable, "-m", "alembic", command[0], *command[1:]], database)
    assert result.returncode == 0, (
        f"alembic {' '.join(command)} failed:\n{result.stdout}\n{result.stderr}")


def test_downgrade_then_upgrade_then_seed(scratch_database):
    _alembic(["upgrade", "head"], scratch_database)
    _alembic(["downgrade", "base"], scratch_database)

    config = get_connection_config("WORKBENCH")
    engine = create_engine(build_sqlalchemy_url(config, database=scratch_database))
    try:
        with engine.connect() as conn:
            left_behind = conn.execute(text(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_NAME <> 'alembic_version' "
                "ORDER BY TABLE_NAME")).scalars().all()
        assert left_behind == [], f"downgrade base left tables behind: {left_behind}"

        # The leg that actually breaks when downgrade() misses a table.
        _alembic(["upgrade", "head"], scratch_database)

        seed = _run([sys.executable, "infra/scripts/dev/seed_dev_fixtures.py"],
                    scratch_database)
        assert seed.returncode == 0, f"seed failed:\n{seed.stdout}\n{seed.stderr}"

        with engine.connect() as conn:
            # upgrade() seeds every kind table; the fixture admin comes from the script.
            assert conn.execute(text(
                "SELECT COUNT(*) FROM rwb_job_type_kind")).scalar() > 0
            assert conn.execute(text(
                "SELECT COUNT(*) FROM app_user WHERE email = 'admin@example.com'"
            )).scalar() == 1
    finally:
        engine.dispose()


def test_drop_workbench_tables_then_upgrade_survives_drift(scratch_database):
    """The production rebuild path, against a database the migration cannot describe.

    rhel9-db-rebuild.sh drops what the database actually has rather than running
    downgrade(), because the production database was built from an older edit of
    0001_initial.py. The extra table below stands in for that drift: downgrade()
    knows nothing about it, so a downgrade-based rebuild leaves it behind.
    """
    _alembic(["upgrade", "head"], scratch_database)

    config = get_connection_config("WORKBENCH")
    engine = create_engine(build_sqlalchemy_url(config, database=scratch_database))
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE drifted_table ("
                "  id INT IDENTITY PRIMARY KEY,"
                "  rwb_job_id UNIQUEIDENTIFIER NULL)"))
            conn.execute(text(
                "ALTER TABLE drifted_table ADD CONSTRAINT fk_drifted_rwb_job "
                "FOREIGN KEY (rwb_job_id) REFERENCES rwb_job (id)"))

        dropped = _run(
            [sys.executable, "infra/scripts/rhel9/drop_workbench_tables.py", "--yes"],
            scratch_database)
        assert dropped.returncode == 0, (
            f"drop failed:\n{dropped.stdout}\n{dropped.stderr}")

        with engine.connect() as conn:
            left_behind = conn.execute(text(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME")).scalars().all()
        assert left_behind == [], f"tables left behind: {left_behind}"

        # alembic_version went with them, so this stamps a fresh revision.
        _alembic(["upgrade", "head"], scratch_database)

        with engine.connect() as conn:
            assert conn.execute(text(
                "SELECT COUNT(*) FROM rwb_job_type_kind")).scalar() > 0
    finally:
        engine.dispose()
