"""Exercise the RHEL9 rebuild helper against a scratch SQL Server database."""

from __future__ import annotations

import sys

import pytest
from sqlalchemy import create_engine, text

from db.config import build_sqlalchemy_url, get_connection_config
from tests.sqlserver.scratch import run_against_database, run_alembic

pytestmark = pytest.mark.sqlserver


def test_rebuild_survives_unknown_table_and_foreign_key(scratch_database):
    run_alembic("upgrade", "head", scratch_database)

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

        result = run_against_database(
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

        run_alembic("upgrade", "head", scratch_database)

        with engine.connect() as conn:
            assert conn.execute(text(
                "SELECT COUNT(*) FROM rwb_job_type_kind"
            )).scalar_one() > 0
    finally:
        engine.dispose()
