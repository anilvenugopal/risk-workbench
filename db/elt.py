"""
db/elt.py — ELT load and enrichment utilities for the db package.

upload_parquet: stream a Parquet file into a SQL Server table.
enrich: set-based UPDATE of a SQL Server table from a pandas DataFrame,
matched by a single or composite key, via a session-scoped temp table.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import pandas as pd
import pyarrow.parquet as pq
from sqlalchemy import text

from .connection import get_connection, get_engine
from .errors import SQLServerConfigurationError, SQLServerQueryError

logger = logging.getLogger(__name__)

_SAFE_IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")



def _validate_identifier(name: str, kind: str = "identifier") -> str:
    """Validate that table, schema, and column names are safe SQL identifiers."""
    if not isinstance(name, str) or not _SAFE_IDENTIFIER_REGEX.match(name):
        raise SQLServerQueryError(
            f"Invalid SQL {kind} name: {name!r}. "
            "Identifiers must match [a-zA-Z_][a-zA-Z0-9_]*."
        )
    return f"[{name}]"


def _check_column_mapping_matches_source(
    source_cols: Sequence[str], column_mapping: Dict[str, str],
) -> None:
    """Raise if column_mapping names a source column that doesn't exist —
    almost always a typo in the caller's mapping. Check this against the
    original source columns, before any columns are dropped, so a column
    correctly dropped by drop_unmapped_columns is never mistaken for a typo.
    """
    unknown_mapped_columns = [c for c in column_mapping if c not in source_cols]
    if unknown_mapped_columns:
        raise SQLServerQueryError(
            f"column_mapping references column(s) {unknown_mapped_columns} "
            f"that are not present in the source columns: {list(source_cols)}."
        )


def _build_destination_columns(
    source_cols: Sequence[str],
    column_mapping: Dict[str, str],
    extra_columns: Dict[str, Any],
) -> List[str]:
    """Resolve the ordered destination column list for a bulk INSERT: each
    source column renamed via column_mapping (unmapped columns keep their own
    name), followed by the extra_columns names. Raises if an extra column name
    collides with a (possibly renamed) source column.
    """
    dest_cols = [column_mapping.get(c, c) for c in source_cols]

    for extra_col in extra_columns:
        if extra_col in dest_cols:
            raise SQLServerQueryError(
                f"Extra column conflict: '{extra_col}' matches a parquet source column."
            )
        dest_cols.append(extra_col)

    return dest_cols


def _drop_unmapped_source_columns(
    source_cols: Sequence[str],
    column_mapping: Dict[str, str],
    target_columns: Sequence[str],
) -> List[str]:
    """Return only the source columns whose destination name (after
    column_mapping) is a real column on the target table. Used by
    upload_parquet's drop_unmapped_columns=True to silently skip Parquet
    columns the target table doesn't have, instead of failing the load."""
    target_set = set(target_columns)
    return [c for c in source_cols if column_mapping.get(c, c) in target_set]


def _fetch_target_column_names(
    conn, schema: str, table_name: str,
) -> List[str]:
    """Column names for one table, read from INFORMATION_SCHEMA.COLUMNS."""
    rows = conn.execute(
        text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table"
        ),
        {"schema": schema, "table": table_name},
    ).fetchall()
    return [row[0] for row in rows]


def upload_parquet(
    file_path: Union[str, Path],
    table_name: str,
    schema: str = "dbo",
    extra_columns: Optional[Dict[str, Any]] = None,
    column_mapping: Optional[Dict[str, str]] = None,
    drop_unmapped_columns: bool = False,
    batch_size: int = 50_000,
    connection: str = "WORKBENCH",
    database: Optional[str] = None,
) -> int:
    """Stream-load a Parquet file into SQL Server using pyodbc fast_executemany.

    Identity and DEFAULT columns (e.g. insert timestamps) on the target table
    should be omitted from parquet/extra_columns so SQL Server generates them.

    Args:
        file_path: Path to the .parquet file.
        table_name: Target table name.
        schema: Target schema (default: 'dbo').
        extra_columns: Dict of {target_col_name: static_value} injected into every row.
        column_mapping: Optional map of {parquet_col_name: target_col_name}.
                        Parquet columns not listed in mapping are used as-is.
        drop_unmapped_columns: If True, a Parquet column that (after
            column_mapping) does not match a real column on the target table
            is silently excluded from the load, instead of failing the whole
            upload. Default False: an unrecognized column is a caller error
            (e.g. a typo) and still fails as one.
        batch_size: Number of records to stream per bulk execute.
        connection: Named connection defined in MSSQL_{NAME}_* env config.
        database: Optional database override.

    Returns:
        int: Total number of rows inserted.
    """
    path = Path(file_path)
    if not path.exists():
        raise SQLServerQueryError(f"Parquet file not found: {path}")

    extra_cols = extra_columns or {}
    col_map = column_mapping or {}

    # 1. Read metadata schema from the Parquet header (no data load yet)
    try:
        parquet_file = pq.ParquetFile(path)
        parquet_cols = parquet_file.schema.names
    except Exception as e:
        raise SQLServerQueryError(f"Failed to inspect Parquet metadata for {path}: {e}") from e

    _check_column_mapping_matches_source(parquet_cols, col_map)

    # 1b. Optionally drop Parquet columns the target table doesn't have.
    if drop_unmapped_columns:
        with get_connection(connection, database=database) as conn:
            target_column_names = _fetch_target_column_names(conn, schema, table_name)
        kept_cols = _drop_unmapped_source_columns(parquet_cols, col_map, target_column_names)
        dropped_cols = [c for c in parquet_cols if c not in kept_cols]
        if dropped_cols:
            logger.info(
                "Dropping Parquet column(s) not present on %s.%s: %s",
                schema, table_name, dropped_cols,
            )
        parquet_cols = kept_cols

    # 2. Build the explicit destination column list: mapped parquet columns
    # followed by injected static columns.
    dest_cols = _build_destination_columns(parquet_cols, col_map, extra_cols)
    extra_col_names = list(extra_cols.keys())

    # Sanitize schema, table, and column names for the query statement
    safe_schema = _validate_identifier(schema, "schema")
    safe_table = _validate_identifier(table_name, "table")
    safe_col_clause = ", ".join(_validate_identifier(c, "column") for c in dest_cols)
    placeholders = ", ".join(["?"] * len(dest_cols))

    insert_sql = f"INSERT INTO {safe_schema}.{safe_table} ({safe_col_clause}) VALUES ({placeholders})"

    # 3. Stream data via pyarrow batches and insert via pyodbc fast_executemany
    engine = get_engine(connection, database=database)
    raw_conn = engine.raw_connection()
    total_rows = 0

    try:
        cursor = raw_conn.cursor()
        cursor.fast_executemany = True

        extra_values = [extra_cols[col] for col in extra_col_names]

        logger.info(
            "Starting Parquet bulk load: %s -> %s.%s (connection=%s, batch_size=%d)",
            path.name,
            schema,
            table_name,
            connection,
            batch_size,
        )

        for record_batch in parquet_file.iter_batches(batch_size=batch_size):
            pydict = record_batch.to_pydict()
            num_rows = record_batch.num_rows

            if num_rows == 0:
                continue

            # Convert columnar PyArrow arrays into row tuples
            row_columns = [pydict[col] for col in parquet_cols]

            # Append static values broadcast across all rows in the batch
            for val in extra_values:
                row_columns.append([val] * num_rows)

            rows = list(zip(*row_columns))

            cursor.executemany(insert_sql, rows)
            total_rows += num_rows

        raw_conn.commit()
        logger.info(
            "Successfully inserted %d rows into %s.%s from %s",
            total_rows,
            schema,
            table_name,
            path.name,
        )
        return total_rows

    except Exception as e:
        raw_conn.rollback()
        raise SQLServerQueryError(
            f"Failed during parquet bulk upload to {schema}.{table_name}: {e}"
        ) from e
    finally:
        raw_conn.close()  # Return pooled connection


def enrich(
    df: pd.DataFrame,
    table_name: str,
    key_fields: Union[str, Sequence[str]],
    schema: str = "dbo",
    column_mapping: Optional[Dict[str, str]] = None,
    connection: str = "WORKBENCH",
    database: Optional[str] = None,
) -> int:
    """
    Update target SQL Server table columns with matching values from a DataFrame.

    Matches existing rows using the specified single or composite key fields,
    stages data into a connection-scoped temporary table (#stg_enrichment), and
    executes an atomic set-based UPDATE join.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame containing key fields and the enrichment columns to update.
    table_name : str
        Target table name (unqualified).
    key_fields : str or Sequence[str]
        One or more DataFrame column names acting as the primary/composite join
        key. If column_mapping renames a key column, name it here using the
        DataFrame's column name, not the target table's.
    schema : str
        Target schema (default: 'dbo').
    column_mapping : Optional[Dict[str, str]]
        Optional map of {dataframe_col: target_col}, for key and/or enrichment
        columns. DataFrame columns not listed use their own name as-is.
    connection : str
        Named connection identifier configured in env (default: 'WORKBENCH').
    database : Optional[str]
        Optional target database override.

    Returns
    -------
    int
        The total number of rows updated in the target table.
    """
    if df.empty:
        return 0

    col_map = column_mapping or {}

    # 1. Normalize and validate key fields (DataFrame-side names)
    keys = [key_fields] if isinstance(key_fields, str) else list(key_fields)
    if not keys:
        raise SQLServerConfigurationError("At least one key field must be provided.")

    # 2. Ensure key fields exist within DataFrame columns
    df_cols = list(df.columns)
    missing_df_keys = [k for k in keys if k not in df_cols]
    if missing_df_keys:
        raise SQLServerConfigurationError(
            f"Key field(s) {missing_df_keys} not present in DataFrame columns: {df_cols}"
        )

    # Resolve DataFrame column names to target column names via column_mapping.
    target_keys = [col_map.get(k, k) for k in keys]
    for k in target_keys:
        _validate_identifier(k)

    _validate_identifier(schema, "schema")
    _validate_identifier(table_name, "table")

    # 3. Use pooled connection context (holds session for #temp table lifecycle)
    with get_connection(connection, database=database) as conn:
        # Query target table schema metadata
        schema_query = text("""
            SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
                   NUMERIC_PRECISION, NUMERIC_SCALE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table
        """)
        results = conn.execute(
            schema_query,
            {"schema": schema, "table": table_name}
        ).fetchall()

        if not results:
            raise SQLServerQueryError(
                f"Target table '{schema}.{table_name}' does not exist or user lacks permissions."
            )

        # The INFORMATION_SCHEMA reads above autobegin a transaction on `conn`
        # (SQLAlchemy 2.0 autobegin-on-execute); close it out before opening the
        # explicit transaction for the mutating work below.
        conn.rollback()

        # Map column definitions: col_name -> row (COLUMN_NAME, DATA_TYPE,
        # CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE)
        target_columns = {row[0]: row for row in results}

        # 4. Verify keys exist in the database table (target-side names)
        missing_db_keys = [k for k in target_keys if k not in target_columns]
        if missing_db_keys:
            raise SQLServerConfigurationError(
                f"Key field(s) {missing_db_keys} not found in target table '{schema}.{table_name}'."
            )

        # 5. Determine enrichment columns: DataFrame columns (excluding keys),
        # resolved through column_mapping, that exist on the target table.
        enrich_col_pairs = [
            (c, col_map.get(c, c)) for c in df_cols if c not in keys
        ]
        enrich_col_pairs = [
            (df_col, target_col) for df_col, target_col in enrich_col_pairs
            if target_col in target_columns
        ]
        for _, target_col in enrich_col_pairs:
            _validate_identifier(target_col)

        if not enrich_col_pairs:
            raise SQLServerConfigurationError(
                f"No matching enrichment columns found between DataFrame and '{schema}.{table_name}'."
            )

        target_enrich_names = [target_col for _, target_col in enrich_col_pairs]
        duplicate_targets = {c for c in target_enrich_names if target_enrich_names.count(c) > 1}
        if duplicate_targets:
            raise SQLServerConfigurationError(
                f"column_mapping maps more than one DataFrame column to target "
                f"column(s) {sorted(duplicate_targets)}."
            )

        # Restrict staging data strictly to required fields, renamed to their
        # target column names so the rest of the function works in target
        # column name space (temp-table DDL, staging, UPDATE join).
        key_pairs = list(zip(keys, target_keys))
        rename_map = dict(key_pairs + enrich_col_pairs)
        df_source_cols = [df_col for df_col, _ in key_pairs + enrich_col_pairs]
        staging_df = df[df_source_cols].rename(columns=rename_map)

        enrich_cols = [target_col for _, target_col in enrich_col_pairs]
        active_columns = target_keys + enrich_cols

        # 6. Build DDL for connection-scoped temporary table
        col_defs: List[str] = []
        for col in active_columns:
            _, data_type, max_len, precision, scale = target_columns[col]

            if data_type in ("decimal", "numeric") and precision is not None:
                type_spec = f"{data_type}({precision}, {scale or 0})"
            elif max_len == -1:
                type_spec = f"{data_type}(MAX)"
            elif max_len is not None and max_len > 0:
                type_spec = f"{data_type}({max_len})"
            else:
                type_spec = data_type

            col_defs.append(f"[{col}] {type_spec}")

        # A local temp table lives for the life of its SQL Server session, and
        # conn.close() returns the pooled connection (and its session) to the
        # pool rather than ending it — so a later enrich() call reusing this
        # same pooled connection can find #stg_enrichment still there from a
        # prior call. Drop it first; sessions are never shared concurrently,
        # so this never touches another call's temp table.
        temp_table = "#stg_enrichment"
        create_temp_sql = (
            f"IF OBJECT_ID('tempdb..{temp_table}') IS NOT NULL DROP TABLE {temp_table};\n"
            f"CREATE TABLE {temp_table} ({', '.join(col_defs)});"
        )

        # 7. Construct set-based UPDATE join
        set_clauses = [f"target.[{col}] = stg.[{col}]" for col in enrich_cols]
        join_clauses = [f"target.[{k}] = stg.[{k}]" for k in target_keys]

        update_sql = f"""
            UPDATE target
            SET {', '.join(set_clauses)}
            FROM [{schema}].[{table_name}] AS target
            INNER JOIN {temp_table} AS stg
                ON {' AND '.join(join_clauses)};
        """

        # 8. Execute staging and update within an explicit transaction
        with conn.begin():
            # Create session temp table
            conn.execute(text(create_temp_sql))

            # Bulk append DataFrame to #temp table
            staging_df.to_sql(
                name=temp_table,
                con=conn,
                if_exists="append",
                index=False,
            )

            # Execute set-based update
            result = conn.execute(text(update_sql))
            rows_updated = result.rowcount

        # Temporary table #stg_enrichment is automatically discarded when conn closes
        return rows_updated