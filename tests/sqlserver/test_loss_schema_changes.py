"""SQL Server tier: the change scripts in db/bootstrap/changes/ agree with
db/bootstrap/loss_schema.sql (spec 014 T049, plan O-12).

A fresh install runs loss_schema.sql alone; an upgrade runs loss_schema.sql and
then every change script above the installed version. Both must end at the same
shape, so this module drops the stage tables, installs the definitive file,
snapshots the stage tables' columns and check constraints, applies every change
script in name order, and asserts the snapshot is unchanged and the stamped
version still equals what the worker requires.

Dropping the stage tables loses every manifest, file, and staged loss row in
rwb_loss; re-run ``make bootstrap-loss`` afterwards. Never point
MSSQL_LOSS_DATABASE at CIC's repository while running this tier.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from app.workers.export_jobs import REQUIRED_LOSS_SCHEMA_VERSION
from db import execute, execute_one, get_connection

pytestmark = pytest.mark.sqlserver

BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / "db" / "bootstrap"
STAGE_TABLES = ("rwb_loss_result_elt_data", "rwb_loss_result_file",
                "rwb_loss_result_manifest", "rwb_loss_schema_version")


def _snapshot() -> dict:
    columns = execute(
        "SELECT t.name AS [table], c.name AS [column], ty.name AS [type], "
        "c.max_length, c.is_nullable "
        "FROM sys.columns c "
        "JOIN sys.tables t ON t.object_id = c.object_id "
        "JOIN sys.types ty ON ty.user_type_id = c.user_type_id "
        "WHERE SCHEMA_NAME(t.schema_id) = 'stage' ORDER BY t.name, c.column_id",
        {}, connection="LOSS")
    constraints = execute(
        "SELECT OBJECT_NAME(parent_object_id) AS [table], name, definition "
        "FROM sys.check_constraints "
        "WHERE SCHEMA_NAME(schema_id) = 'stage' ORDER BY name",
        {}, connection="LOSS")
    return {"columns": [tuple(r.values()) for r in columns],
            "constraints": [tuple(r.values()) for r in constraints]}


def _installed_version() -> int | None:
    return execute_one("SELECT MAX(version) AS v FROM stage.rwb_loss_schema_version",
                       {}, connection="LOSS")["v"]


def test_change_scripts_converge_on_the_definitive_schema():
    from db.scripts import execute_script_file  # noqa: PLC0415 — trusted DDL, test only

    with get_connection("LOSS") as conn, conn.begin():
        for table in STAGE_TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS stage.{table}"))
    execute_script_file(BOOTSTRAP_DIR / "loss_schema.sql", connection="LOSS")
    fresh = _snapshot()
    stamped = _installed_version()
    assert stamped == REQUIRED_LOSS_SCHEMA_VERSION, (
        f"loss_schema.sql stamps version {stamped}; app/workers/export_jobs.py "
        f"requires {REQUIRED_LOSS_SCHEMA_VERSION}")

    scripts = sorted((BOOTSTRAP_DIR / "changes").glob("*.sql"))
    assert scripts, "db/bootstrap/changes/ holds no change script"
    assert int(scripts[-1].name.split("-", 1)[0]) == stamped, (
        f"the newest change script is {scripts[-1].name}; loss_schema.sql stamps {stamped}")
    for script in scripts:
        execute_script_file(script, connection="LOSS")

    upgraded = _snapshot()
    assert upgraded["columns"] == fresh["columns"], (
        "a change script leaves a column different from loss_schema.sql:\n"
        f"  fresh:    {sorted(set(fresh['columns']) - set(upgraded['columns']))}\n"
        f"  upgraded: {sorted(set(upgraded['columns']) - set(fresh['columns']))}")
    assert upgraded["constraints"] == fresh["constraints"], (
        "a change script leaves a check constraint different from loss_schema.sql:\n"
        f"  fresh:    {sorted(set(fresh['constraints']) - set(upgraded['constraints']))}\n"
        f"  upgraded: {sorted(set(upgraded['constraints']) - set(fresh['constraints']))}")
    assert _installed_version() == stamped
