"""SQL Server connectivity smoke test.

Runs only with: pytest tests/sqlserver --run-sqlserver

Verifies the WORKBENCH, EXPOSURE, and LOSS connections can all reach SQL Server.
"""

from __future__ import annotations

import pytest

from db.connection import test_connection


@pytest.mark.sqlserver
class TestConnectivity:
    def test_workbench_connection(self):
        assert test_connection("WORKBENCH"), "Could not connect to WORKBENCH database"

    def test_exposure_connection(self):
        assert test_connection("EXPOSURE"), "Could not connect to EXPOSURE database"

    def test_loss_connection(self):
        assert test_connection("LOSS"), "Could not connect to LOSS database"


@pytest.mark.sqlserver
def test_workbench_alembic_version_table_exists():
    """After `make db-migrate`, the alembic_version table must exist."""
    from db.execute import execute
    rows = execute(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_NAME = 'alembic_version'",
        connection="WORKBENCH",
    )
    assert rows[0]["cnt"] == 1, "alembic_version table not found — run `make db-migrate`"
