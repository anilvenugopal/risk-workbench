# Contract — `stage.usp_load_elt_result` and its installation

## 1. Interface

```sql
EXEC stage.usp_load_elt_result @manifest_id = <int>, @use_peril = <bit, default 0>;
```

Authored in `db/bootstrap/loss_schema.sql`; installed by the CIC DBA (O-12);
called by the `load_results_export` worker through `db.execute_procedure`,
or by a person in SQL Server Management Studio. `@use_peril` adds `Peril =
manifest.peril_code` to the lookup join for single-peril analyses; it stays
`0` until O-11 confirms the lookup's codes, after which the worker passes
`1` and the default flips.

### Preconditions (checked by the procedure, each raising before any write)

| Check | Error |
|---|---|
| `@@TRANCOUNT > 0` on entry | `50000` "usp_load_elt_result must be called outside a transaction" |
| Claim `UPDATE … SET load_status = 'loading', error_message = NULL WHERE manifest_id = @manifest_id AND stage_status = 'staged' AND load_status IN ('pending','failed')` affected 0 rows | `50001` with the reason read from the row: "manifest {id} not found", "manifest {id} is not staged (stage_status {s})", "manifest {id} is loading", or "manifest {id} already loaded as data ID {data_id}" |
| No `dbo.Lookup_RMS_HistoricalRDS` row with `ModelVersion = manifest.data_model_version` | `50002` "Lookup_RMS_HistoricalRDS has no rows for model version {v}" |
| Any stage `event_id` matches more than one lookup row (join on `EventID`, `ModelVersion`, and `Peril` when `@use_peril = 1` and `peril_code <> 'YY'`) | `50003` "event {event_id} matches {n} historical lookup rows for model version {v}" |

### Effects, in one transaction after the claim

1. `UPDATE stage.rwb_loss_result_elt_data SET event_type = 'historical'` for
   rows with a lookup match; `'stochastic'` for the rest.
2. `UPDATE … SET exp_value = loss, exp_value_raised = 1 WHERE loss > exp_value`
   → `manifest.exp_value_raised_count = @@ROWCOUNT`.
3. `UPDATE … SET std_dev_i = CASE WHEN std_dev_i < 0 THEN 0 ELSE std_dev_i END,
   std_dev_c = …, std_dev_zeroed = 1 WHERE event_type = 'stochastic' AND
   (std_dev_i < 0 OR std_dev_c < 0)` → `manifest.std_dev_zeroed_count`.
4. `INSERT dbo.Data … OUTPUT INSERTED.DataID INTO @inserted SELECT … FROM
   stage.rwb_loss_result_manifest WHERE manifest_id = @manifest_id`
   (data-model.md §5.1); `UPDATE manifest SET data_id = (SELECT data_id FROM @inserted)`.
5. `INSERT dbo.RMSELT` from stochastic rows (§5.2) →
   `stochastic_row_count`; `INSERT dbo.RMS_HistoricalRDS` from historical
   rows joined to the lookup (§5.3) → `historical_row_count`.
6. `UPDATE manifest SET load_status = 'loaded', loaded_at = SYSUTCDATETIME(),
   updated_at = SYSUTCDATETIME()`; `COMMIT`.

`CATCH`: `IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;` then
`UPDATE manifest SET load_status = 'failed', error_message = ERROR_MESSAGE(),
updated_at = SYSUTCDATETIME() WHERE manifest_id = @manifest_id AND load_status <> 'loaded';`
then `THROW;`.

Guarantees: exactly one `Data` row per successful call; zero target rows on
failure; `loaded` and `data_id` visible only after commit; the row never
stays `loading` after a dropped connection (the rollback undoes the claim).
No dynamic SQL anywhere in the body (ownership chaining).

## 2. `db.execute_procedure`

```python
def execute_procedure(name: str, params: dict[str, Any], connection: str = "WORKBENCH",
                      database: str | None = None) -> None
```

Opens the engine connection with
`execution_options(isolation_level="AUTOCOMMIT")`, runs
`EXEC {name} @p1 = :p1, …` with bound parameters (`name` is a code constant,
never user input), and re-raises driver errors unchanged. Lives in
`db/execute.py` beside `execute_command`; exported from `db/__init__.py`.
The unit tier fakes it; the SQL Server tier exercises it.

## 3. `db.read_uncommitted_hint`

```python
def read_uncommitted_hint(connection: str = "WORKBENCH", database: str | None = None) -> str
```

Returns `"WITH (READUNCOMMITTED)"` when the resolved engine dialect is
`mssql`, else `""`. Used only in request-path reads of
`stage.rwb_loss_result_manifest` (T-25; RCSI is off on the repository).

## 4. Installation contract (`db/bootstrap/loss_schema.sql`)

- Run against `CRE_Trial_ELT_Repository` by a login in `db_owner` (never the
  `LOSS` login), so `stage` and the procedure are owned by `dbo` and
  ownership chaining reaches `dbo.Data`, `dbo.RMSELT`,
  `dbo.RMS_HistoricalRDS`.
- Idempotent: `IF SCHEMA_ID('stage') IS NULL CREATE SCHEMA stage AUTHORIZATION dbo`;
  `IF OBJECT_ID(...) IS NULL CREATE TABLE ...`; `CREATE OR ALTER PROCEDURE`.
- The file names no database and nothing else that is environment-specific;
  it installs unchanged in dev and at CIC (T-21). The procedure reads
  `dbo.Lookup_RMS_HistoricalRDS` by two-part name under ownership chaining,
  the same as its inserts into `dbo.Data`, `dbo.RMSELT`, and
  `dbo.RMS_HistoricalRDS`.
- Grants the `LOSS` login needs (requested through O-05), all in
  `CRE_Trial_ELT_Repository`:

| Grant | Why |
|---|---|
| `SELECT ON dbo.Client` | The export form's client list and the exports section's client name |
| `CONTROL ON SCHEMA::stage` (or `SELECT, INSERT, UPDATE, DELETE` on the stage tables) | Manifest, file, and stage rows |
| `EXECUTE ON stage.usp_load_elt_result` | The load |

No grant on `dbo.Lookup_RMS_HistoricalRDS`, `dbo.Data`, `dbo.RMSELT`, or
`dbo.RMS_HistoricalRDS`: only the procedure touches them.

The client team's own accounts need `EXECUTE ON stage.usp_load_elt_result` to
run a load by hand.

## 5. Development bootstrap

- `infra/scripts/bootstrap_loss.py`: refuses unless `MSSQL_LOSS_DATABASE ==
  "rwb_loss"`; applies `db/bootstrap/loss_dev_mirror.sql` (CIC's five
  tables into `rwb_loss`, seed rows), then
  `db/bootstrap/loss_schema.sql`, both through
  `db.scripts.execute_script_file(..., connection="LOSS")`.
- Makefile: `bootstrap-loss` (Docker) and `wsl-bootstrap-loss`; `db-rebuild`
  and `wsl-db-rebuild` call them after `alembic upgrade head`.
