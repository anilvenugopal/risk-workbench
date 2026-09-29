"""A throwaway Workbench database for tests that run DDL end to end.

The SQL Server tier otherwise runs against the developer's ``rwb_workbench``.
Migrations, downgrades, and the RHEL9 drop script must never touch it, so
these tests create ``rwb_workbench_scratch_test`` with the SA login, point
``MSSQL_WORKBENCH_DATABASE`` at it for each subprocess, and drop it afterwards.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from db.config import build_sqlalchemy_url, get_connection_config

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRATCH_DB = "rwb_workbench_scratch_test"


def master_engine():
    config = get_connection_config("WORKBENCH")
    config["password"] = os.environ["MSSQL_SA_PASSWORD"]
    return create_engine(
        build_sqlalchemy_url(config, database="master"),
        isolation_level="AUTOCOMMIT",
    )


def drop_scratch_database(conn) -> None:
    conn.execute(text(
        f"IF DB_ID('{SCRATCH_DB}') IS NOT NULL "
        f"ALTER DATABASE [{SCRATCH_DB}] "
        "SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
    ))
    conn.execute(text(f"DROP DATABASE IF EXISTS [{SCRATCH_DB}]"))


def run_against_database(args: list[str], database: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "APP_ENV": "development",
        "MSSQL_WORKBENCH_DATABASE": database,
    }
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def run_alembic(command: str, revision: str, database: str) -> None:
    result = run_against_database(
        [sys.executable, "-m", "alembic", command, revision],
        database,
    )
    assert result.returncode == 0, (
        f"alembic {command} {revision} failed:\n{result.stdout}\n{result.stderr}"
    )


def list_tables_and_views(database: str) -> list[str]:
    config = get_connection_config("WORKBENCH")
    engine = create_engine(build_sqlalchemy_url(config, database=database))
    try:
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "ORDER BY TABLE_NAME"
            )).scalars().all()
    finally:
        engine.dispose()
