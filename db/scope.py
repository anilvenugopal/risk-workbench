"""Application-level row scoping (RLS) — the `apply_scope()` hook the PRD/data
model rely on, implemented on the **safe bound-parameter path only.**

Scoping is expressed as a filter on `customer_id` whose allowed values are passed
as **bound parameters**, never interpolated — so a scope predicate can't be the
source of a cross-customer leak. This is exactly why the application's default
execution path is parameterized: `apply_scope()` cannot be built safely on the
trusted-script path, and must never be.

Usage (the repository layer would wrap this so it is the *only* way to read
scoped tables):

    sql, params = apply_scope(
        "SELECT * FROM submission WHERE status_code = :s",
        customer_ids=user.customer_ids, params={"s": "open"},
        is_admin=user.is_admin,
    )
    rows = execute(sql, params, connection="WORKBENCH")

    # or in one call:
    rows = scoped_execute("SELECT * FROM submission",
                          customer_ids=user.customer_ids, is_admin=user.is_admin)
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .execute import execute

logger = logging.getLogger(__name__)


def apply_scope(sql: str,
                customer_ids: Sequence[Any],
                params: Optional[Dict[str, Any]] = None,
                *,
                is_admin: bool = False,
                column: str = "customer_id",
                param_prefix: str = "_scope_") -> Tuple[str, Dict[str, Any]]:
    """Wrap `sql` so only rows whose `column` is in `customer_ids` are returned.

    - Admin bypass: if `is_admin`, the query is returned unchanged (logged).
    - No access: an empty `customer_ids` (non-admin) yields a query that returns
      no rows (`WHERE 1=0`) — fail-closed, never fail-open.
    - The allowed ids are bound parameters (`:_scope_0`, …), never interpolated.

    The base query is wrapped as a subquery, so it works regardless of the base
    query's own WHERE/JOIN/GROUP BY; the only requirement is that the base query
    projects `column` (e.g. `SELECT *` or an explicit `customer_id`).
    """
    params = dict(params or {})

    if is_admin:
        logger.info("apply_scope: admin bypass (no customer filter applied)")
        return sql, params

    if not customer_ids:
        logger.warning("apply_scope: no customer access — returning empty result set")
        return f"SELECT * FROM ({sql}) AS _scoped WHERE 1=0", params

    names = []
    for i, cid in enumerate(customer_ids):
        key = f"{param_prefix}{i}"
        params[key] = cid
        names.append(f":{key}")
    in_list = ", ".join(names)
    scoped = f"SELECT * FROM ({sql}) AS _scoped WHERE _scoped.{column} IN ({in_list})"
    return scoped, params


def scoped_execute(sql: str,
                   customer_ids: Sequence[Any],
                   params: Optional[Dict[str, Any]] = None,
                   *,
                   is_admin: bool = False,
                   connection: str = "WORKBENCH",
                   database: Optional[str] = None,
                   column: str = "customer_id") -> List[Dict[str, Any]]:
    """`apply_scope` + `execute` in one call — returns scoped rows as list[dict]."""
    scoped_sql, scoped_params = apply_scope(
        sql, customer_ids, params, is_admin=is_admin, column=column)
    return execute(scoped_sql, scoped_params, connection=connection, database=database)


__all__ = ["apply_scope", "scoped_execute"]
