"""Trusted-script execution path — for **curated, repository-authored SQL only.**

This path keeps the capabilities the external-data-source work (Databridge /
Moody's RMS backend) genuinely needs and the safe path deliberately omits:
`{{ param }}` text substitution, `GO`-batch handling, multiple result sets, and
pandas DataFrame returns.

⚠️  SECURITY CONTRACT — READ THIS:
    Parameters here are substituted **into the SQL text**, not bound. That is
    acceptable *only* because these scripts are authored by the team and their
    parameters come from configuration. **Never pass an end-user-derived value
    to anything in this module.** For any dynamic/user-influenced value, use the
    safe bound-parameter path (`db.execute`) instead. This module must never be
    imported by the web request layer and must never touch the application's own
    multi-tenant tables — it is for reading external data sources from worker
    tasks, behind the IRP/data-source interface.

Hardened vs. the notebook original: logging instead of `print`, connections drawn
from the shared pooled engine (so auth, Kerberos, and pooling are identical to
the safe path), explicit connection names.
"""

import re
import logging
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional, Union

from .connection import get_engine
from .errors import (SQLServerConnectionError, SQLServerConfigurationError,
                     SQLServerQueryError)

logger = logging.getLogger(__name__)

try:
    import pandas as pd
    import numpy as np
except ImportError as e:  # pragma: no cover  # pandas/numpy absent; only needed for script path
    pd = None  # type: ignore
    np = None  # type: ignore
    _PANDAS_IMPORT_ERROR = e
else:
    _PANDAS_IMPORT_ERROR = None


def _require_pandas() -> None:
    if pd is None:  # pragma: no cover
        raise SQLServerConfigurationError(
            "pandas/numpy are required for the trusted-script path "
            "(db.scripts). Install pandas and numpy."
        ) from _PANDAS_IMPORT_ERROR


# ---- {{ param }} template --------------------------------------------------
class ExpressionTemplate(Template):
    """`{{ PARAM }}` placeholder syntax (space-padded to avoid clashing with SQL)."""
    delimiter = "{{"
    pattern = r"""
    \{\{\s*
    (?:
    (?P<escaped>\{\{)|
    (?P<named>[_a-zA-Z][_a-zA-Z0-9]*)\s*\}\}|
    (?P<braced>[_a-zA-Z][_a-zA-Z0-9]*)\s*\}\}|
    (?P<invalid>)
    )
    """  # type: ignore


# ---- value conversion / escaping -------------------------------------------
def _escape_sql_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"


def _convert_param_value(value: Any) -> Any:
    if value is None:
        return None
    if np is not None and isinstance(value, np.ndarray):
        return value.tolist()
    if pd is not None and isinstance(value, pd.Series):
        return value.tolist()
    try:
        if pd is not None and pd.isna(value):
            return None
    except (ValueError, TypeError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def _convert_params(params: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if params is None:
        return None
    return {k: _convert_param_value(v) for k, v in params.items()}


def _substitute_named_parameters(query: str, params: Optional[Dict[str, Any]]) -> str:
    """Context-aware `{{ param }}` substitution (value vs identifier contexts)."""
    if not params:
        return query
    converted = _convert_params(params) or {}
    escaped: Dict[str, str] = {}
    for key, value in converted.items():
        identifier_patterns = [
            rf"\[\s*\{{\{{\s*{re.escape(key)}\s*\}}\}}\s*\]",
            rf"'[^'\n\r]*\{{\{{\s*{re.escape(key)}\s*\}}\}}[^'\n\r]*'",
            rf"\w+_\{{\{{\s*{re.escape(key)}\s*\}}\}}",
            rf"\{{\{{\s*{re.escape(key)}\s*\}}\}}_\w+",
        ]
        is_identifier = any(re.search(p, query) for p in identifier_patterns)
        if is_identifier:
            if isinstance(value, str) and not all(
                c.isascii() and (c.isalnum() or c in ("_", "-", " ", "/")) for c in value
            ):
                raise SQLServerQueryError(
                    f"Invalid identifier value for '{key}': {value!r}. "
                    f"Identifier-context params allow [A-Za-z0-9_-/space] only."
                )
            escaped[key] = str(value)
        else:
            escaped[key] = _escape_sql_value(value)
    try:
        return ExpressionTemplate(query).substitute(escaped)
    except KeyError as e:
        raise SQLServerQueryError(
            f"Missing required parameter: {e}. Provided: {', '.join(converted)}"
        ) from e
    except ValueError as e:
        raise SQLServerQueryError(f"Parameter substitution error: {e}") from e


# ---- execution -------------------------------------------------------------
def execute_query(query: str, params: Optional[Dict[str, Any]] = None,
                  connection: str = "DATABRIDGE",
                  database: Optional[str] = None):
    """Run a single trusted SELECT (with `{{ }}` substitution) → DataFrame."""
    _require_pandas()
    sql = _substitute_named_parameters(query, params) if params else query
    engine = get_engine(connection, database=database)
    try:
        with engine.connect() as conn:
            return pd.read_sql_query(sql, conn)
    except (SQLServerConnectionError, SQLServerConfigurationError):
        raise
    except Exception as e:
        raise SQLServerQueryError(
            f"Script query failed (connection: {connection}): {e}"
        ) from e


def _resolve_sql_path(file_path: Union[str, Path], sql_dir: Optional[Union[str, Path]]) -> Path:
    p = Path(file_path)
    if not p.is_absolute():
        base = Path(sql_dir) if sql_dir else Path(
            __import__("os").getenv("MSSQL_SQL_DIR", "sql"))
        p = base / p
    if not p.exists():
        raise SQLServerQueryError(f"SQL script not found: {p}")
    return p


def execute_script_file(file_path: Union[str, Path],  # pragma: no cover
                        params: Optional[Dict[str, Any]] = None,
                        connection: str = "DATABRIDGE",
                        database: Optional[str] = None,
                        sql_dir: Optional[Union[str, Path]] = None) -> List[Any]:
    """Run a trusted multi-statement SQL script (GO batches, multiple SELECTs) and
    return one DataFrame per result set. Connection comes from the shared pool.

    Requires a real pyodbc-backed engine; tested in tests/sqlserver/ only.
    """
    _require_pandas()
    path = _resolve_sql_path(file_path, sql_dir)
    logger.info("Executing SQL script %s (connection=%s)", path.name, connection)
    script = path.read_text(encoding="utf-8")
    sql = _substitute_named_parameters(script, params) if params else script

    engine = get_engine(connection, database=database)
    raw = engine.raw_connection()  # pooled DBAPI (pyodbc) connection
    dataframes: List[Any] = []
    try:
        cursor = raw.cursor()
        # Split on GO batch separators (line-only GO), execute each batch.
        batches = [b.strip() for b in re.split(r"^\s*GO\s*$", sql, flags=re.MULTILINE) if b.strip()]
        for batch in batches:
            cursor.execute(batch)
            while True:
                if cursor.description is not None:
                    cols = [c[0] for c in cursor.description]
                    rows = [tuple(r) for r in cursor.fetchall()]
                    dataframes.append(pd.DataFrame.from_records(rows, columns=cols))
                if not cursor.nextset():
                    break
        raw.commit()
        logger.info("Script %s returned %d result set(s)", path.name, len(dataframes))
        return dataframes
    except (SQLServerConnectionError, SQLServerConfigurationError):
        raise
    except Exception as e:
        raise SQLServerQueryError(
            f"Script failed (connection: {connection}, file: {path.name}): {e}"
        ) from e
    finally:
        raw.close()  # returns the connection to the pool


def sql_file_exists(file_path: Union[str, Path],
                    sql_dir: Optional[Union[str, Path]] = None) -> bool:
    try:
        _resolve_sql_path(file_path, sql_dir)
        return True
    except SQLServerQueryError:
        return False


def display_result_sets(dataframes: List[Any], max_rows: int = 10) -> None:
    """Pretty-print a list of DataFrames (notebook/debug convenience)."""
    _require_pandas()
    if not dataframes:
        print("No result sets to display")
        return
    try:
        from IPython.display import display
    except ImportError:
        display = print  # type: ignore
    for i, df in enumerate(dataframes, 1):
        print(f"\n--- Result set {i} of {len(dataframes)} "
              f"({len(df)} rows, {len(df.columns)} cols) ---")
        display(df.head(max_rows) if len(df) > max_rows else df)


__all__ = [
    "execute_query",
    "execute_script_file",
    "sql_file_exists",
    "display_result_sets",
    "ExpressionTemplate",
]
