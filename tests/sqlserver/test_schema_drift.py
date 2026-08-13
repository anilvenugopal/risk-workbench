"""Guard the SQLite unit-tier mirror against the real migrated schema.

The unit tests run on a hand-written SQLite mirror of the WORKBENCH tables
(``tests/iteration1_mirror.py``). If a migration adds, removes, or renames a
column and the mirror is not updated to match, the SQLite tests would keep
passing against a stale shape — a false green. This suite fails loudly and names
the drifted column, so the mirror can never silently rot.

Two contracts (see tests/iteration1_mirror.py):
  * EXACT_MATCH_TABLES — mirror columns must equal the real columns exactly.
  * SUBSET_TABLES (irp_edm/irp_rdm) — the mirror is intentionally trimmed to the
    columns the unit services use; the real tables carry extra
    Iteration-2 IRP columns. Here the invariant is mirror ⊆ real.

Run with:  pytest tests/sqlserver --run-sqlserver   (requires live SQL Server)
"""

from __future__ import annotations

import pytest

from db import execute
from tests.iteration1_mirror import (
    EXACT_MATCH_TABLES,
    SUBSET_TABLES,
    mirror_columns,
)

pytestmark = pytest.mark.sqlserver


def _real_columns(table: str) -> set[str]:
    return {r["COLUMN_NAME"] for r in execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME = :t AND TABLE_SCHEMA = 'dbo'",
        {"t": table}, connection="WORKBENCH")}


@pytest.mark.parametrize("table", EXACT_MATCH_TABLES)
def test_mirror_matches_real_schema_exactly(table):
    mirror = mirror_columns()[table]
    real = _real_columns(table)
    assert real, f"{table} not found in WORKBENCH (did the migration run?)"
    assert mirror == real, (
        f"{table}: the SQLite unit mirror drifted from the migration.\n"
        f"  only in mirror: {sorted(mirror - real)}\n"
        f"  only in real:   {sorted(real - mirror)}\n"
        f"Update tests/iteration1_mirror.py:ITERATION1_SCHEMA to match.")


@pytest.mark.parametrize("table", SUBSET_TABLES)
def test_mirror_is_subset_of_real_schema(table):
    mirror = mirror_columns()[table]
    real = _real_columns(table)
    assert real, f"{table} not found in WORKBENCH (did the migration run?)"
    missing = mirror - real
    assert not missing, (
        f"{table}: the mirror declares columns absent from the migration: "
        f"{sorted(missing)}.\n"
        f"(Extra real columns are expected — irp_* mirrors are trimmed to the "
        f"columns used by unit services. A *missing* column "
        f"means a rename/removal the mirror must follow.)")
