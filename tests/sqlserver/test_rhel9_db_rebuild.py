"""Exercise the RHEL9 rebuild helper against a scratch SQL Server database."""

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
_SCRATCH_DB = "rwb_workbench_rebuild_test"


def _master_engine():
    config = get_connection_config("WORKBENCH")
    config["password"] = os.environ["MSSQL_SA_PASSWORD"]
    return create_engine(
        build_sqlalchemy_url(config, database="master"),
        isolation_level="AUTOCOMMIT",
    )


def _drop_scratch_database(conn) -> None:
    conn.execute(text(
        f"IF DB_ID('{_SCRATCH_DB}') IS NOT NULL "
        f"ALTER DATABASE [{_SCRATCH_DB}] "
        "SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
    ))
    conn.execute(text(f"DROP DATABASE IF EXISTS [{_SCRATCH_DB}]"))


@pytest.fixture(scope="module")
def scratch_database():
    if not os.environ.get("MSSQL_SA_PASSWORD"):
        pytest.skip("MSSQL_SA_PASSWORD is required to create a scratch database")

    engine = _master_engine()
    try:
        with engine.connect() as conn:
            _drop_scratch_database(conn)
            conn.execute(text(f"CREATE DATABASE [{_SCRATCH_DB}]"))
        yield _SCRATCH_DB
        with engine.connect() as conn:
            _drop_scratch_database(conn)
    finally:
        engine.dispose()


def _run(args: list[str], database: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "APP_ENV": "development",
        "MSSQL_WORKBENCH_DATABASE": database,
    }
    return subprocess.run(
        args,
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def _run_alembic(database: str) -> None:
    result = _run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        database,
    )
    assert result.returncode == 0, (
        f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"
    )


def test_rebuild_survives_unknown_table_and_foreign_key(scratch_database):
    _run_alembic(scratch_database)

    config = get_connection_config("WORKBENCH")
    engine = create_engine(build_sqlalchemy_url(config, database=scratch_database))
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE drifted_table ("
                "id INT IDENTITY PRIMARY KEY, "
                "rwb_job_id UNIQUEIDENTIFIER NULL)"
            ))
            conn.execute(text(
                "ALTER TABLE drifted_table "
                "ADD CONSTRAINT fk_drifted_rwb_job "
                "FOREIGN KEY (rwb_job_id) REFERENCES rwb_job (id)"
            ))

        result = _run(
            [
                sys.executable,
                "infra/scripts/rhel9/drop_workbench_tables.py",
                "--yes",
            ],
            scratch_database,
        )
        assert result.returncode == 0, (
            f"table drop failed:\n{result.stdout}\n{result.stderr}"
        )

        with engine.connect() as conn:
            remaining_tables = conn.execute(text(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME"
            )).scalars().all()
        assert remaining_tables == []

        _run_alembic(scratch_database)

        with engine.connect() as conn:
            assert conn.execute(text(
                "SELECT COUNT(*) FROM rwb_job_type_kind"
            )).scalar_one() > 0
    finally:
        engine.dispose()
