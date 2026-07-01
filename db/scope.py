"""Row scoping — generic IN-list filter on the safe bound-parameter path.

Wraps any SQL query to restrict rows by a caller-specified column and set of
allowed values, using bound parameters. The column name and values are always
supplied by the caller — this package has no opinion about what they mean.

Usage:

    from db.scope import apply_scope, scoped_execute

    # Returns (rewritten_sql, merged_params) — no database call.
    sql, params = apply_scope(
        "SELECT * FROM submission WHERE status = :s",
        values=user.customer_ids,
        column="customer_id",
        params={"s": "open"},
        is_admin=user.is_admin,
    )
    rows = execute(sql, params, connection="WORKBENCH")

    # Or in one call:
    rows = scoped_execute(
        "SELECT * FROM submission",
        values=user.customer_ids,
        column="customer_id",
        is_admin=user.is_admin,
        connection="WORKBENCH",
    )
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .execute import execute

logger = logging.getLogger(__name__)


def apply_scope(
    sql: str,
    values: Sequence[Any],
    column: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    is_admin: bool = False,
    param_prefix: str = "_scope_",
) -> Tuple[str, Dict[str, Any]]:
    """Return (sql, params) with an IN-list filter on `column` added.

    - Admin bypass: if `is_admin`, returns the original sql and params unchanged.
    - Empty values (non-admin): returns a query that produces no rows (WHERE 1=0).
      Fail-closed — never fail-open.
    - Allowed values are bound parameters, never interpolated into the SQL string.

    The base query is wrapped as a subquery so this composes with any WHERE,
    JOIN, or GROUP BY already present in `sql`.
    """
    params = dict(params or {})

    if is_admin:
        logger.info("apply_scope: admin bypass on column=%s", column)
        return sql, params

    if not values:
        logger.warning("apply_scope: empty values on column=%s — returning no rows", column)
        return f"SELECT * FROM ({sql}) AS _scoped WHERE 1=0", params

    names = []
    for i, value in enumerate(values):
        key = f"{param_prefix}{i}"
        params[key] = value
        names.append(f":{key}")
    in_list = ", ".join(names)
    scoped = f"SELECT * FROM ({sql}) AS _scoped WHERE _scoped.{column} IN ({in_list})"
    return scoped, params


def scoped_execute(
    sql: str,
    values: Sequence[Any],
    column: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    is_admin: bool = False,
    connection: str,
    database: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """apply_scope + execute in one call. `connection` is required (no default)."""
    scoped_sql, scoped_params = apply_scope(
        sql, values, column, params, is_admin=is_admin
    )
    return execute(scoped_sql, scoped_params, connection=connection, database=database)


__all__ = ["apply_scope", "scoped_execute"]
