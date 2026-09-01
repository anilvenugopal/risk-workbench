# POC: `db.upload_parquet`

Hands-on demonstration of every `upload_parquet` scenario against a real SQL
Server database, so you can see the behavior yourself instead of trusting
test assertions. Background and API reference: [`db/README.md`](../../db/README.md#elt-bulk-load-and-enrichment-dbeltpy).

## Prerequisites

- The `sqlserver` container is up (`infra-sqlserver-1` or equivalent) and
  reachable — same requirement as `make wsl-test-sql`.
- ODBC Driver 18 for SQL Server installed natively (check with
  `odbcinst -q -d`).
- Run everything from the repo root, with the WSL2-native env loaded first:

  ```bash
  source infra/scripts/wsl-env.sh
  ```

  This exports the `MSSQL_WORKBENCH_*` variables pointed at `localhost` (the
  container's mapped port). Every command below assumes this has been run in
  your shell session — either run it once per terminal, or prefix each
  command with it as shown.

This POC uses the real `rwb_workbench` database (`WORKBENCH` connection) —
the same one the app and `tests/sqlserver/` use. Its own table is named
`dbo.poc_upload_trades`, clearly separate from any real application table.

## Run it

```bash
source infra/scripts/wsl-env.sh && uv run python pocs/elt_parquet_upload/run_poc.py
```

Drops and recreates `dbo.poc_upload_trades` at the start, so it's safe to
re-run any time — you always get a clean slate. The table is **left in
place** after the script finishes; it does not clean up after itself the way
the automated tests do. That's on purpose, so you can inspect the result.

## What each scenario proves

The script runs all of these against one table in a single pass. Every
scenario's log line states plainly what to expect afterward, and every
`upload_parquet` call also stamps `source_file_name` via `extra_columns` (a
real `extra_columns` use, not a special feature) — so the final table makes
it obvious which scenario produced which row.

1. **`column_mapping`** — the Parquet file's `ticker` column loads into the
   table's `symbol` column, because the names don't match. Expect: AAPL and
   MSFT.
2. **`drop_unmapped_columns`** — the Parquet file has an `internal_notes`
   column the table doesn't have. Without `drop_unmapped_columns=True` this
   would fail the whole load; with it, that column is silently skipped.
   Expect: one new TSLA row, no `internal_notes` column anywhere (there's no
   such column on the table to show it in).
3. **Nullable columns** — the `notes` column is never populated by any
   scenario's Parquet file. It stays `NULL`, exactly as SQL Server would
   leave any column not named in an `INSERT`. Expect: one new NFLX row with
   `notes = None`.
4. **`DEFAULT` columns** — `loaded_at DATETIME2 DEFAULT SYSUTCDATETIME()` is
   never in the Parquet file or `column_mapping`. SQL Server computes it
   itself, per row, at insert time. Expect: META and NVDA, each with a real
   timestamp close to when you ran the script.
5. **Identity columns under concurrent uploads** — 8 Python threads each
   upload 25 rows into the same table at the same time. `trade_id
   INT IDENTITY(1,1)` is never named in the insert; SQL Server hands out the
   next identity value itself. The script asserts every `trade_id` in the
   table is unique — identity assignment is serialized inside SQL Server, so
   two concurrent uploads can never collide on the same value. It also
   groups the resulting rows by `source_file_name` and asserts each of the
   8 files landed exactly 25 rows, proving no thread's rows got mixed up
   with another's. Expect: 206 total rows (6 from earlier scenarios + 200
   here), all `trade_id` values distinct, exactly 25 rows per
   `scenario5_N.parquet` file.

`source_file_name` (via `extra_columns`) is a real, meaningful use of that
parameter across every scenario — unlike an arbitrary placeholder value, it
also does double duty as scenario 5's cross-thread-mixing check.

## Inspect the results

After running, query the table directly — via SSMS, `make shell` + `sqlcmd`,
or the `db` package from a Python shell:

```bash
source infra/scripts/wsl-env.sh && uv run python -c "
from db import execute
for row in execute('SELECT * FROM dbo.poc_upload_trades ORDER BY trade_id', connection='WORKBENCH'):
    print(row)
"
```

The table and every column also carry a SQL Server **extended property**
(`MS_Description`) explaining what each one demonstrates — the same
"Description" field SSMS and DBeaver show in their object browser, so nobody
has to guess what this table is for from inside the database.

## Cleaning up

Nothing to do — `dbo.poc_upload_trades` is dropped and recreated the next
time you run `run_poc.py`. If you want it gone entirely:

```bash
source infra/scripts/wsl-env.sh && uv run python -c "
from db import execute_command
execute_command('DROP TABLE IF EXISTS dbo.poc_upload_trades', connection='WORKBENCH')
"
```

## Files

```
elt_parquet_upload/
├── README.md   this file
├── run_poc.py  runs all 5 scenarios end-to-end
└── data/       Parquet fixture files, written by run_poc.py
```
