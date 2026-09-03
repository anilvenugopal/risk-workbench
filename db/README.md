# `db` — unified SQL Server access for the Risk Workbench

One package handles **connection management, SQL Server + Windows/Kerberos
authentication, and SQL execution** for every target — the Workbench's own
database *and* external sources like Databridge/Moody's. Targets are just named
connections; there is no second helper.

It uses **SQLAlchemy Core as a connection pool and execution surface only — no
ORM.** You keep writing SQL.

## The one rule: two execution paths, split by safety

| | Safe path (default) | Trusted-script path |
|---|---|---|
| Module | `db.execute` | `db.scripts` |
| Parameters | **Bound** (`:name`) — sent separately from SQL | `{{ param }}` **substituted into text** |
| Returns | `list[dict]`, scalar, rowcount | pandas DataFrame(s) |
| May receive user input? | **Yes** — injection-safe by construction | **Never** — trusted/curated SQL only |
| Use for | **All application data access** | External data scripts (Databridge), worker-side |
| Multi-result-set / GO batches | no | yes |

The split is by *safety*, not by target: both Databridge and the Workbench can be
queried by the safe path; the script path is reserved for curated external scripts
that need DataFrames and multiple result sets. The script path is **not** exported
from the top-level package — import it explicitly from `db.scripts` so its use is
always visible in review. It must never be imported by the web layer and must
never touch the app's own tables.

## Configuration (env)

Each target is a named connection:

```
MSSQL_WORKBENCH_SERVER=localhost
MSSQL_WORKBENCH_USER=raw_app
MSSQL_WORKBENCH_PASSWORD=...
MSSQL_WORKBENCH_DATABASE=raw_db

MSSQL_DATABRIDGE_SERVER=...databridge.rms-pe.com
MSSQL_DATABRIDGE_USER=Modeling_Automation
MSSQL_DATABRIDGE_PASSWORD=...

MSSQL_ASSURANT_SERVER=...database.cead.prd
MSSQL_ASSURANT_AUTH_TYPE=WINDOWS        # Kerberos; no USER/PASSWORD
```

Global / pool / Kerberos:

```
MSSQL_DRIVER="ODBC Driver 18 for SQL Server"
MSSQL_TRUST_CERT=yes
MSSQL_TIMEOUT=30
MSSQL_POOL_SIZE=5
MSSQL_POOL_MAX_OVERFLOW=5
MSSQL_POOL_RECYCLE=1800
MSSQL_SQL_DIR=sql                       # base dir for execute_script_file

KERBEROS_ENABLED=true                   # only for WINDOWS-auth targets
KRB5_PRINCIPAL=svc_acct@REALM
KRB5_KEYTAB=/path/service.keytab        # preferred, or:
KRB5_PASSWORD=...
```

## Usage

Application code (always the safe path):

```python
from db import execute, execute_one, execute_command

rows = execute("SELECT * FROM submission WHERE status_code = :s",
               {"s": "open"}, connection="WORKBENCH")

execute_command("UPDATE submission SET status_code = :s WHERE id = :id",
                {"s": "closed", "id": 7}, connection="WORKBENCH")
```

External data scripts (worker-side only, trusted SQL):

```python
from db.scripts import execute_script_file, display_result_sets

dfs = execute_script_file(
    "control_totals/3d_RMS_EDM_Control_Totals.sql",
    params={"DATE_VALUE": "202503", "CYCLE_TYPE": "Quarterly"},
    connection="DATABRIDGE",
)
display_result_sets(dfs)
```

## Files

```
db/
├── __init__.py     public API (safe path + connection/auth; NOT the script path)
├── errors.py       exception hierarchy
├── config.py       named-connection env resolution + ODBC/SQLAlchemy URLs (no deps)
├── kerberos.py     Windows-auth ticket check/renew (logging, not prints)
├── connection.py   pooled SQLAlchemy engines (per target) + Kerberos hook
├── execute.py      SAFE bound-parameter path -> list[dict]/scalar/rowcount
├── elt.py          Parquet bulk load (upload_parquet) + DataFrame enrichment (enrich)
└── scripts.py      TRUSTED {{param}} script path -> DataFrames (import explicitly)
```

## Dependencies

- `sqlalchemy>=2.0` (pool/engine; no ORM)
- `pyodbc` + **Microsoft ODBC Driver 18 for SQL Server**
- `pandas`, `pyarrow` — required for `db.elt`
- `pandas`, `numpy` — also used by the `db.scripts` path

## ELT: bulk load and enrichment (`db/elt.py`)

Two functions for moving data between files/DataFrames and SQL Server tables,
built on the same pooled engine as the rest of `db/`. Both use the safe,
bound-parameter query style — no string-built SQL from caller-supplied values.

| | `upload_parquet` | `enrich` |
|---|---|---|
| Direction | Parquet file → new rows (INSERT) | DataFrame → existing rows (UPDATE) |
| Matches by | nothing — always appends | one or more key columns |
| Use for | loading a landing/staging table from a file | patching columns on rows that already exist |

### `upload_parquet` — stream a Parquet file into a table

```python
from db import upload_parquet

rows_inserted = upload_parquet(
    file_path="/data/incoming/trades_2026_Q1.parquet",
    table_name="trades_landing",
    schema="stage",
    connection="WORKBENCH",
)
```

Reads the Parquet file in batches (`batch_size`, default 50,000 rows) and
inserts each batch with `pyodbc`'s `fast_executemany`, so memory use stays
flat regardless of file size. Returns the total row count inserted.

**Column handling**

- By default, every column in the Parquet file must have a matching column on
  the target table (its own name, or a renamed one via `column_mapping`) — an
  unrecognized column fails the whole load. This catches typos early.
- `column_mapping={"parquet_col": "target_col"}` renames a Parquet column to
  match the target table's column name. Every key in `column_mapping` must
  actually be a column in the Parquet file, or the call raises immediately.
- `extra_columns={"target_col": value}` adds a column to every inserted row
  with a fixed value (e.g. a batch id or source-system tag) that doesn't come
  from the file at all.
- `drop_unmapped_columns=True` relaxes the "every column must match" rule: a
  Parquet column that still doesn't resolve to a real target column (after
  `column_mapping`) is silently skipped instead of failing the load. Use this
  when the file legitimately has more columns than the table cares about.

**Columns to leave out of the file, mapping, and `extra_columns`**

- **Identity columns** (`IDENTITY(1,1)`, auto-increment primary keys): omit
  them entirely. `upload_parquet` builds an explicit `INSERT` column list, so
  SQL Server assigns the identity value itself. Identity assignment is
  serialized inside SQL Server, so this is safe even when several uploads run
  against the same table at the same time — two concurrent loads can never be
  handed the same identity value. See `pocs/elt_parquet_upload` for a runnable
  demonstration.
- **Columns with a `DEFAULT` constraint** (e.g. `loaded_at DATETIME2 DEFAULT
  SYSUTCDATETIME()`): also omit them, for the same reason — SQL Server
  computes the default per row at insert time.
- **Nullable columns** the file has no data for: also just omit them from
  `column_mapping`/`extra_columns` (or leave them absent from the Parquet
  file). SQL Server inserts `NULL`.

### `enrich` — update existing rows from a DataFrame

```python
import pandas as pd
from db import enrich

df = pd.DataFrame({
    "elt_data_key": [1001, 1002, 1003],
    "risk_score": [0.92, 0.45, 0.78],
})

rows_updated = enrich(
    df,
    table_name="submission",
    key_fields="elt_data_key",
    connection="WORKBENCH",
)
```

Matches existing rows on `key_fields` (a single column name, or a list for a
composite key) and updates every other DataFrame column that also exists on
the target table. Rows with no matching key are left untouched — `enrich`
never inserts. Internally it stages the DataFrame into a temp table and runs
one set-based `UPDATE ... FROM ... JOIN`, so it scales to large DataFrames far
better than a row-by-row loop.

```python
# Composite key
enrich(df, "policy_coverage", key_fields=["policy_id", "coverage_code"])
```

**`column_mapping`** — when the DataFrame's column names don't match the
target table's, pass `column_mapping={"dataframe_col": "target_col"}`. It
applies to key columns and enrichment columns alike; `key_fields` always
names the *DataFrame's* column, not the target's:

```python
enrich(
    df,                      # has "src_id", "src_score"
    "submission",            # has "elt_data_key", "risk_score"
    key_fields="src_id",
    column_mapping={"src_id": "elt_data_key", "src_score": "risk_score"},
)
```

A DataFrame column not listed in `column_mapping` is matched against a target
column of the same name, same as `upload_parquet`. Any DataFrame column that
doesn't resolve to a real target column (mapped or not) is dropped from the
update rather than raising — `enrich` only requires the key column(s) to
resolve.

### Testing

- **Unit tier** (`tests/unit/test_elt.py`): identifier validation and every
  pre-flight error path, run for real — no mocking beyond registering a
  SQLite engine. Both functions become SQL-Server-only past validation
  (`upload_parquet`'s `fast_executemany` is a pyodbc-only cursor attribute;
  `enrich`'s closing statement is SQL Server's `UPDATE ... FROM ... JOIN`
  syntax), so this tier stops there by construction, not convenience.
- **SQL Server tier** (`tests/sqlserver/test_elt.py`, `--run-sqlserver`): the
  real thing — actual inserts, actual updates, decimal precision, identity
  and default columns, `column_mapping`, `drop_unmapped_columns`, single and
  composite keys.
- **`pocs/`**: runnable, narrated scenarios for hands-on verification against
  a real database — see `pocs/elt_parquet_upload/README.md` and
  `pocs/elt_enrich/README.md`.
