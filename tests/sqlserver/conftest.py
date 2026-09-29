from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from tests.sqlserver.scratch import SCRATCH_DB, drop_scratch_database, master_engine


@pytest.fixture(scope="module")
def scratch_database():
    if not os.environ.get("MSSQL_SA_PASSWORD"):
        pytest.skip("MSSQL_SA_PASSWORD is required to create a scratch database")

    engine = master_engine()
    try:
        with engine.connect() as conn:
            drop_scratch_database(conn)
            conn.execute(text(f"CREATE DATABASE [{SCRATCH_DB}]"))
        yield SCRATCH_DB
        with engine.connect() as conn:
            drop_scratch_database(conn)
    finally:
        engine.dispose()
