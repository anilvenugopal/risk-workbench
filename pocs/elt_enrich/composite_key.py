"""POC: db.enrich with a composite (multi-column) key.

See README.md in this folder for prerequisites and what to look at after
running. Run from the repo root with the WSL2-native SQL Server env loaded:

    source infra/scripts/wsl-env.sh && uv run python pocs/elt_enrich/composite_key.py

Drops and recreates dbo.poc_enrich_policy_coverage every run, so re-running
is safe. The table is left in place afterward — query it yourself to inspect
the result.
"""

from __future__ import annotations

import pandas as pd

from db import enrich, execute, execute_command

TABLE = "poc_enrich_policy_coverage"


def log(msg: str) -> None:
    print(f"\n>>> {msg}")


def setup_table() -> None:
    log("Dropping and recreating dbo.poc_enrich_policy_coverage")
    execute_command(f"DROP TABLE IF EXISTS dbo.{TABLE}", connection="WORKBENCH")
    execute_command(
        f"""
        CREATE TABLE dbo.{TABLE} (
            region_id       INT NOT NULL,
            coverage_code   VARCHAR(10) NOT NULL,
            credit_rating   VARCHAR(10) NULL,
            exposure        DECIMAL(18, 2) NULL,
            PRIMARY KEY (region_id, coverage_code)
        )
        """,
        connection="WORKBENCH",
    )
    execute_command(
        f"""
        INSERT INTO dbo.{TABLE} (region_id, coverage_code, credit_rating, exposure) VALUES
        (1, 'WIND', 'BBB', 10000.00),
        (1, 'FLOOD', 'A', 25000.00),
        (2, 'WIND', 'BBB', 12000.00)
        """,
        connection="WORKBENCH",
    )

    _describe_table(
        "POC for db.enrich (composite key). Demonstrates matching on two "
        "columns (region_id, coverage_code) together, plus column_mapping "
        "renaming one half of the composite key. Safe to drop; recreated by "
        "pocs/elt_enrich/composite_key.py on every run."
    )
    _describe_column("region_id", "First half of the composite key. Both "
                      "columns must match for a row to be updated.")
    _describe_column("coverage_code", "Second half of the composite key. "
                      "Renamed from 'cov_code' via column_mapping in scenario 2.")
    _describe_column("credit_rating", "Enrichment column, plain name in both scenarios.")
    _describe_column("exposure", "Enrichment column, plain name in both scenarios.")

    log("Starting table contents:")
    show_table()


def _describe_table(description: str) -> None:
    execute_command(
        """
        EXEC sys.sp_addextendedproperty
            @name = N'MS_Description', @value = :description,
            @level0type = N'SCHEMA', @level0name = N'dbo',
            @level1type = N'TABLE',  @level1name = :table
        """,
        {"description": description, "table": TABLE},
        connection="WORKBENCH",
    )


def _describe_column(column: str, description: str) -> None:
    execute_command(
        """
        EXEC sys.sp_addextendedproperty
            @name = N'MS_Description', @value = :description,
            @level0type = N'SCHEMA', @level0name = N'dbo',
            @level1type = N'TABLE',  @level1name = :table,
            @level2type = N'COLUMN', @level2name = :column
        """,
        {"description": description, "table": TABLE, "column": column},
        connection="WORKBENCH",
    )


def show_dataframe(label: str, df: pd.DataFrame) -> None:
    print(f"    {label}:")
    for line in df.to_string(index=False).splitlines():
        print(f"      {line}")


def show_params(**params) -> None:
    print("    enrich(...) called with:")
    for name, value in params.items():
        print(f"      {name} = {value!r}")


def show_table() -> None:
    """Pretty-print the whole table as an aligned grid."""
    rows = execute(
        f"SELECT region_id, coverage_code, credit_rating, exposure "
        f"FROM dbo.{TABLE} ORDER BY region_id, coverage_code",
        connection="WORKBENCH",
    )
    if not rows:
        print("    (table is empty)")
        return

    columns = list(rows[0].keys())
    str_rows = [{c: "" if row[c] is None else str(row[c]) for c in columns} for row in rows]
    widths = {c: max(len(c), *(len(r[c]) for r in str_rows)) for c in columns}

    def format_row(values: dict) -> str:
        return "  ".join(values[c].ljust(widths[c]) for c in columns)

    print(f"    {format_row({c: c for c in columns})}")
    print(f"    {'  '.join('-' * widths[c] for c in columns)}")
    for row in str_rows:
        print(f"    {format_row(row)}")


# ── Scenario 1: plain composite-key update ──────────────────────────────────

def scenario_1_composite_key() -> None:
    log("Scenario 1: composite key — (region_id, coverage_code) must both "
        "match. Expect: (1, 'WIND') and (1, 'FLOOD') updated; (2, 'WIND') "
        "untouched even though region_id=2 alone isn't unique across regions.")
    df = pd.DataFrame({
        "region_id": [1, 1],
        "coverage_code": ["WIND", "FLOOD"],
        "credit_rating": ["AAA", "AA+"],
        "exposure": [15000.00, 32000.00],
    })
    show_dataframe("Input DataFrame", df)
    show_params(
        table_name=TABLE, key_fields=["region_id", "coverage_code"], connection="WORKBENCH",
    )

    rows_updated = enrich(
        df, TABLE, key_fields=["region_id", "coverage_code"], connection="WORKBENCH",
    )
    print(f"    rows_updated = {rows_updated}")


# ── Scenario 2: column_mapping renames one of the two key columns ──────────

def scenario_2_column_mapping() -> None:
    log("Scenario 2: column_mapping — DataFrame's 'cov_code' maps to "
        "'coverage_code'; 'region_id' matches by its own name. Expect: "
        "(2, 'WIND') updated with credit_rating='BB', exposure=9000.00.")
    df = pd.DataFrame({
        "region_id": [2],
        "cov_code": ["WIND"],
        "credit_rating": ["BB"],
        "exposure": [9000.00],
    })
    show_dataframe("Input DataFrame", df)
    show_params(
        table_name=TABLE,
        key_fields=["region_id", "cov_code"],
        column_mapping={"cov_code": "coverage_code"},
        connection="WORKBENCH",
    )

    rows_updated = enrich(
        df, TABLE,
        key_fields=["region_id", "cov_code"],
        column_mapping={"cov_code": "coverage_code"},
        connection="WORKBENCH",
    )
    print(f"    rows_updated = {rows_updated}")


if __name__ == "__main__":
    setup_table()
    scenario_1_composite_key()
    scenario_2_column_mapping()

    log("Final table contents (dbo.poc_enrich_policy_coverage, left in place — inspect it yourself):")
    show_table()
