"""POC: db.upload_parquet, all scenarios in one run.

See README.md in this folder for prerequisites and what to look at after
running. Run from the repo root with the WSL2-native SQL Server env loaded:

    source infra/scripts/wsl-env.sh && uv run python pocs/elt_parquet_upload/run_poc.py

Drops and recreates dbo.poc_upload_trades every run, so re-running is safe
and always starts from a clean table. The table is left in place afterward —
query it yourself (SSMS, `make shell` + sqlcmd, or the db package) to inspect
the result of every scenario below.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from db import execute, execute_command, upload_parquet

TABLE = "poc_upload_trades"
DATA_DIR = Path(__file__).parent / "data"


def log(msg: str) -> None:
    print(f"\n>>> {msg}")


def setup_table() -> None:
    log("Dropping and recreating dbo.poc_upload_trades")
    execute_command(f"DROP TABLE IF EXISTS dbo.{TABLE}", connection="WORKBENCH")
    execute_command(
        f"""
        CREATE TABLE dbo.{TABLE} (
            trade_id                INT IDENTITY(1,1) PRIMARY KEY,
            symbol                  VARCHAR(20) NOT NULL,
            notes                   VARCHAR(200) NULL,
            source_file_name        VARCHAR(200) NULL,
            loaded_at               DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
        )
        """,
        connection="WORKBENCH",
    )

    _describe_table(
        "POC for db.upload_parquet (docs/agents or db/README.md ELT section). "
        "Demonstrates column_mapping, extra_columns, drop_unmapped_columns, "
        "nullable columns, a DEFAULT timestamp column, and an IDENTITY column "
        "under concurrent uploads. Safe to drop; recreated by "
        "pocs/elt_parquet_upload/run_poc.py on every run."
    )
    _describe_column("trade_id", "IDENTITY primary key. Never appears in the "
                      "Parquet file or column_mapping — SQL Server assigns it, "
                      "safely even when uploads run concurrently (see scenario 5).")
    _describe_column("symbol", "Ordinary required column. Comes from the "
                      "Parquet file's 'ticker' column via column_mapping "
                      "(scenario 1) — the names deliberately don't match.")
    _describe_column("notes", "Nullable column the Parquet file never "
                      "supplies (scenario 3) — stays NULL after every load.")
    _describe_column("source_file_name", "Populated by every scenario via "
                      "extra_columns={'source_file_name': path.name} — every "
                      "row loaded from a given file gets that file's own name. "
                      "Also what scenario 5 groups by to prove no thread's rows "
                      "got mixed up with another's.")
    _describe_column("loaded_at", "DEFAULT SYSUTCDATETIME(). Never appears "
                      "in the Parquet file or column_mapping — SQL Server "
                      "computes it per row (scenario 4).")


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


def write_parquet(name: str, rows: dict) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / name
    pq.write_table(pa.table(rows), path)
    return path


def show_table() -> None:
    """Pretty-print the whole table as an aligned grid — clearer than a raw
    dict per row, especially with 200+ rows from scenario 5."""
    rows = execute(
        f"SELECT trade_id, symbol, notes, source_file_name, loaded_at "
        f"FROM dbo.{TABLE} ORDER BY trade_id",
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


# ── Scenario 1: column_mapping (Parquet columns named differently) ──────────

def scenario_1_column_mapping() -> None:
    log("Scenario 1: column_mapping — Parquet's 'ticker' column renames to 'symbol'. "
        "Expect: AAPL and MSFT added, both with source_file_name = 'scenario1.parquet'.")
    path = write_parquet("scenario1.parquet", {"ticker": ["AAPL", "MSFT"]})

    rows_inserted = upload_parquet(
        path, TABLE,
        column_mapping={"ticker": "symbol"},
        extra_columns={"source_file_name": path.name},
        connection="WORKBENCH",
    )
    print(f"    rows_inserted = {rows_inserted}")


# ── Scenario 2: drop_unmapped_columns (file has more columns than the table) ─

def scenario_2_drop_unmapped_columns() -> None:
    log("Scenario 2: drop_unmapped_columns — 'internal_notes' isn't a real "
        "column and is silently skipped instead of failing the load. Expect: "
        "one new TSLA row, no 'internal_notes' anywhere in the table (there's "
        "no such column to show).")
    path = write_parquet("scenario2.parquet", {
        "symbol": ["TSLA"],
        "internal_notes": ["not a real column on this table"],
    })

    rows_inserted = upload_parquet(
        path, TABLE,
        drop_unmapped_columns=True,
        extra_columns={"source_file_name": path.name},
        connection="WORKBENCH",
    )
    print(f"    rows_inserted = {rows_inserted}")


# ── Scenario 3: nullable column left alone ──────────────────────────────────

def scenario_3_nullable_column() -> None:
    log("Scenario 3: nullable column — 'notes' is never in the file, stays NULL. "
        "Expect: one new NFLX row with notes = None.")
    path = write_parquet("scenario3.parquet", {"symbol": ["NFLX"]})

    upload_parquet(
        path, TABLE, extra_columns={"source_file_name": path.name}, connection="WORKBENCH",
    )
    row = execute(
        f"SELECT notes FROM dbo.{TABLE} WHERE symbol = 'NFLX'", connection="WORKBENCH"
    )[0]
    print(f"    notes = {row['notes']!r} (expected: None)")


# ── Scenario 4: DEFAULT timestamp column populated by SQL Server ───────────

def scenario_4_default_timestamp() -> None:
    log("Scenario 4: DEFAULT column — 'loaded_at' is never in the file, "
        "SQL Server computes it per row. Expect: META and NVDA each get a "
        "real timestamp close to when this script ran.")
    path = write_parquet("scenario4.parquet", {"symbol": ["META", "NVDA"]})

    upload_parquet(
        path, TABLE, extra_columns={"source_file_name": path.name}, connection="WORKBENCH",
    )
    rows = execute(
        f"SELECT symbol, loaded_at FROM dbo.{TABLE} WHERE symbol IN ('META', 'NVDA')",
        connection="WORKBENCH",
    )
    for row in rows:
        print(f"    {row['symbol']}: loaded_at = {row['loaded_at']}")


# ── Scenario 5: IDENTITY column stays unique under concurrent uploads ──────

def scenario_5_identity_under_parallel_uploads() -> None:
    log("Scenario 5: parallel uploads — 8 threads each upload 25 rows "
        "concurrently; trade_id must stay unique with no duplicates or gaps "
        "in the count. Expect: 200 new rows (8 files x 25 rows), each row's "
        "source_file_name matching the file its own thread loaded.")
    paths = [
        write_parquet(f"scenario5_{i}.parquet", {"symbol": [f"SYM{i}-{j}" for j in range(25)]})
        for i in range(8)
    ]

    errors: list[Exception] = []

    def upload_one(path: Path) -> None:
        try:
            upload_parquet(
                path, TABLE, extra_columns={"source_file_name": path.name}, connection="WORKBENCH",
            )
        except Exception as e:  # noqa: BLE001 — surfaced to the main thread below
            errors.append(e)

    threads = [threading.Thread(target=upload_one, args=(p,)) for p in paths]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if errors:
        raise errors[0]

    ids = [
        row["trade_id"]
        for row in execute(f"SELECT trade_id FROM dbo.{TABLE}", connection="WORKBENCH")
    ]
    print(f"    total rows in table = {len(ids)}")
    print(f"    distinct trade_id values = {len(set(ids))} (must match total rows)")
    assert len(ids) == len(set(ids)), "duplicate identity values were assigned!"

    file_counts = execute(
        f"SELECT source_file_name, COUNT(*) AS n FROM dbo.{TABLE} "
        f"WHERE source_file_name LIKE 'scenario5_%' GROUP BY source_file_name "
        f"ORDER BY source_file_name",
        connection="WORKBENCH",
    )
    print("    rows per file (each should be 25, with no cross-thread mixing):")
    for row in file_counts:
        print(f"      {row['source_file_name']}: {row['n']}")
    assert all(row["n"] == 25 for row in file_counts), (
        "a thread's rows ended up tagged with the wrong source_file_name!"
    )


if __name__ == "__main__":
    setup_table()
    scenario_1_column_mapping()
    scenario_2_drop_unmapped_columns()
    scenario_3_nullable_column()
    scenario_4_default_timestamp()
    scenario_5_identity_under_parallel_uploads()

    log("Final table contents (dbo.poc_upload_trades, left in place — inspect it yourself):")
    show_table()
