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
| Module | `db.execute`, `db.scope` | `db.scripts` |
| Parameters | **Bound** (`:name`) — sent separately from SQL | `{{ param }}` **substituted into text** |
| Returns | `list[dict]`, scalar, rowcount | pandas DataFrame(s) |
| May receive user input? | **Yes** — injection-safe by construction | **Never** — trusted/curated SQL only |
| Use for | **All application data access** (incl. `apply_scope`) | External data scripts (Databridge), worker-side |
| Multi-result-set / GO batches | no | yes |

The split is by *safety*, not by target: both Databridge and the Workbench can be
queried by the safe path; the script path is reserved for curated external scripts
that need DataFrames and multiple result sets. The script path is **not** exported
from the top-level package — import it explicitly from `db.scripts` so its use is
always visible in review. It must never be imported by the web layer and must
never touch the app's own multi-tenant tables.

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
from db import execute, execute_one, execute_command, scoped_execute

rows = execute("SELECT * FROM submission WHERE status_code = :s",
               {"s": "open"}, connection="WORKBENCH")

# RLS — allowed customer ids are bound, never interpolated; empty = no rows
rows = scoped_execute("SELECT * FROM submission",
                      customer_ids=user.customer_ids, is_admin=user.is_admin)

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
├── scope.py        apply_scope()/scoped_execute() on the safe path only
└── scripts.py      TRUSTED {{param}} script path -> DataFrames (import explicitly)
```

## Dependencies

- `sqlalchemy>=2.0` (pool/engine; no ORM)
- `pyodbc` + **Microsoft ODBC Driver 18 for SQL Server**
- `pandas`, `numpy` — only for the `db.scripts` path
