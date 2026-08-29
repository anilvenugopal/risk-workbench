# POC: `db.enrich`

Hands-on demonstration of `db.enrich`, so you can see single-key,
composite-key, and `column_mapping` updates run against a real SQL Server
database instead of trusting test assertions. Background and API reference:
[`db/README.md`](../../db/README.md#elt-bulk-load-and-enrichment-dbeltpy).

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
the same one the app and `tests/sqlserver/` use. Its tables are named
`dbo.poc_enrich_submission` and `dbo.poc_enrich_policy_coverage`, clearly
separate from any real application table.

## Run it

Two scripts, one per key shape:

```bash
source infra/scripts/wsl-env.sh && uv run python pocs/elt_enrich/single_key.py
source infra/scripts/wsl-env.sh && uv run python pocs/elt_enrich/composite_key.py
```

Each drops and recreates its own table at the start, so it's safe to re-run
either one any time. Both tables are **left in place** after their script
finishes — they do not clean up after themselves the way the automated tests
do. That's on purpose, so you can inspect the result.

Each script prints the starting table, then for every scenario: the input
DataFrame, the exact parameters passed to `enrich(...)`, and the rows
updated — finishing with the full final table, so you can follow each
scenario's before/after without querying the database yourself.

## What each script proves

### `single_key.py` — `dbo.poc_enrich_submission`

1. **Plain single-key update** — a DataFrame keyed on `elt_data_key`, with
   column names that already match the table, updates two of the three
   seeded rows. The third row's key isn't present in the DataFrame, so it's
   left untouched — `enrich` only ever updates, never inserts.
2. **`column_mapping`** — a DataFrame using its own column names
   (`src_id`, `src_score`) updates `elt_data_key` and `risk_score` via
   `column_mapping={"src_id": "elt_data_key", "src_score": "risk_score"}`.
   Note `key_fields` names the DataFrame's own column (`src_id`), not the
   target's. The `status` column is updated by matching name, showing a
   single call can mix mapped and unmapped columns.

### `composite_key.py` — `dbo.poc_enrich_policy_coverage`

1. **Plain composite-key update** — a DataFrame keyed on
   `["region_id", "coverage_code"]` together updates only the rows where
   *both* columns match. `(region_id=2, coverage_code='WIND')` is left alone
   even though `region_id=2` alone isn't unique across the table — proving
   the match really is on the pair, not just the first column.
2. **`column_mapping` on part of a composite key** — the DataFrame's
   `cov_code` column maps to `coverage_code`, while `region_id` matches by
   its own name. Shows `column_mapping` can rename just one half of a
   composite key.

## Inspect the results

After running, query either table directly — via SSMS, `make shell` +
`sqlcmd`, or the `db` package from a Python shell:

```bash
source infra/scripts/wsl-env.sh && uv run python -c "
from db import execute
for row in execute('SELECT * FROM dbo.poc_enrich_submission ORDER BY elt_data_key', connection='WORKBENCH'):
    print(row)
"
```

Both tables and every column also carry a SQL Server **extended property**
(`MS_Description`) explaining what each one demonstrates — the same
"Description" field SSMS and DBeaver show in their object browser.

## Cleaning up

Nothing to do — each table is dropped and recreated the next time you run
its script. If you want them gone entirely:

```bash
source infra/scripts/wsl-env.sh && uv run python -c "
from db import execute_command
execute_command('DROP TABLE IF EXISTS dbo.poc_enrich_submission', connection='WORKBENCH')
execute_command('DROP TABLE IF EXISTS dbo.poc_enrich_policy_coverage', connection='WORKBENCH')
"
```

## Files

```
elt_enrich/
├── README.md          this file
├── single_key.py      single-column key: plain update + column_mapping
└── composite_key.py   composite key: plain update + column_mapping
```
