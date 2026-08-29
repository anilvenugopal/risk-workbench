"""POC: db.enrich with a single-column key.

See README.md in this folder for prerequisites and what to look at after
running. Run from the repo root with the WSL2-native SQL Server env loaded:

    source infra/scripts/wsl-env.sh && uv run python pocs/elt_enrich/single_key.py

Drops and recreates dbo.poc_enrich_submission every run, so re-running is
safe. The table is left in place afterward — query it yourself to inspect
the result.
"""

from __future__ import annotations

import pandas as pd

from db import enrich, execute, execute_command

TABLE = "poc_enrich_submission"


def log(msg: str) -> None:
    print(f"\n>>> {msg}")


def setup_table() -> None:
    log("Dropping and recreating dbo.poc_enrich_submission")
    execute_command(f"DROP TABLE IF EXISTS dbo.{TABLE}", connection="WORKBENCH")
    execute_command(
        f"""
        CREATE TABLE dbo.{TABLE} (
            elt_data_key    INT PRIMARY KEY,
            risk_score      DECIMAL(18, 2) NULL,
            status          VARCHAR(20) NULL
        )
        """,
        connection="WORKBENCH",
    )
    execute_command(
        f"""
        INSERT INTO dbo.{TABLE} (elt_data_key, risk_score, status) VALUES
        (101, 0.10, 'Pending'),
        (102, 0.40, 'Pending'),
        (103, 0.55, 'Pending')
        """,
        connection="WORKBENCH",
    )

    _describe_table(
        "POC for db.enrich (single-column key). Demonstrates a plain "
        "single-key update and column_mapping renaming both the key and an "
        "enrichment column. Safe to drop; recreated by "
        "pocs/elt_enrich/single_key.py on every run."
    )
    _describe_column("elt_data_key", "Single-column primary key. Matches "
                      "rows by this column alone in both scenarios below.")
    _describe_column("risk_score", "Enrichment column updated by both "
                      "scenarios — plain name in scenario 1, renamed via "
                      "column_mapping from 'src_score' in scenario 2.")
    _describe_column("status", "Enrichment column, plain name in both "
                      "scenarios — shows a DataFrame can update some "
                      "columns via column_mapping and others by matching name.")

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
        f"SELECT elt_data_key, risk_score, status FROM dbo.{TABLE} ORDER BY elt_data_key",
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


# ── Scenario 1: plain single-key update ─────────────────────────────────────

def scenario_1_single_key() -> None:
    log("Scenario 1: single-key update — DataFrame column names match the table. "
        "Expect: rows 101 and 102 updated; row 103 untouched (no matching key "
        "in the DataFrame).")
    df = pd.DataFrame({
        "elt_data_key": [101, 102],
        "risk_score": [0.85, 0.95],
        "status": ["Approved", "Approved"],
    })
    show_dataframe("Input DataFrame", df)
    show_params(table_name=TABLE, key_fields="elt_data_key", connection="WORKBENCH")

    rows_updated = enrich(df, TABLE, key_fields="elt_data_key", connection="WORKBENCH")
    print(f"    rows_updated = {rows_updated}")


# ── Scenario 2: column_mapping renames the key and an enrichment column ────

def scenario_2_column_mapping() -> None:
    log("Scenario 2: column_mapping — DataFrame's 'src_id'/'src_score' map "
        "to 'elt_data_key'/'risk_score'; 'status' matches by its own name. "
        "Expect: row 103 updated with risk_score=0.72, status='Flagged'.")
    df = pd.DataFrame({
        "src_id": [103],
        "src_score": [0.72],
        "status": ["Flagged"],
    })
    show_dataframe("Input DataFrame", df)
    show_params(
        table_name=TABLE,
        key_fields="src_id",
        column_mapping={"src_id": "elt_data_key", "src_score": "risk_score"},
        connection="WORKBENCH",
    )

    rows_updated = enrich(
        df, TABLE,
        key_fields="src_id",
        column_mapping={"src_id": "elt_data_key", "src_score": "risk_score"},
        connection="WORKBENCH",
    )
    print(f"    rows_updated = {rows_updated}")


if __name__ == "__main__":
    setup_table()
    scenario_1_single_key()
    scenario_2_column_mapping()

    log("Final table contents (dbo.poc_enrich_submission, left in place — inspect it yourself):")
    show_table()
