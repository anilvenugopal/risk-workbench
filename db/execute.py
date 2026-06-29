"""Safe execution path — **the default for all application code.**

Every function here uses **bound parameters** (`:name` placeholders compiled to
the driver's positional binds by SQLAlchemy `text()`), so values are sent to the
server separately from the SQL text. SQL injection and scope-leakage are
structurally impossible on this path, which is why it is the default and the only
path that may ever receive user-derived values (customer ids, submission ids,
search text, …).

Returns plain Python (`list[dict]`, a scalar, or a rowcount) — no pandas. The
application repository and `apply_scope()` (see `db.scope`) sit on top of these.

    rows  = execute("SELECT * FROM submission WHERE customer_id = :cid",
                    {"cid": 42}, connection="WORKBENCH")
    row   = execute_one("SELECT * FROM submission WHERE id = :id", {"id": 7}, "WORKBENCH")
    n     = execute_scalar("SELECT COUNT(*) FROM submission", connection="WORKBENCH")
    count = execute_command("UPDATE submission SET status_code = :s WHERE id = :id",
                            {"s": "closed", "id": 7}, "WORKBENCH")
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from .connection import get_engine
from .errors import (SQLServerConnectionError, SQLServerConfigurationError,
                     SQLServerQueryError)

logger = logging.getLogger(__name__)

Params = Optional[Dict[str, Any]]


def execute(sql: str, params: Params = None, connection: str = "WORKBENCH",
            database: Optional[str] = None) -> List[Dict[str, Any]]:
    """Run a SELECT and return all rows as a list of dicts (column -> value)."""
    engine = get_engine(connection, database=database)
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            return [dict(m) for m in result.mappings().all()]
    except (SQLServerConnectionError, SQLServerConfigurationError):
        raise
    except Exception as e:
        raise SQLServerQueryError(
            f"Query failed (connection: {connection}): {e}"
        ) from e


def execute_one(sql: str, params: Params = None, connection: str = "WORKBENCH",
                database: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Run a SELECT and return the first row as a dict, or None."""
    engine = get_engine(connection, database=database)
    try:
        with engine.connect() as conn:
            row = conn.execute(text(sql), params or {}).mappings().first()
            return dict(row) if row is not None else None
    except (SQLServerConnectionError, SQLServerConfigurationError):
        raise
    except Exception as e:
        raise SQLServerQueryError(
            f"Query failed (connection: {connection}): {e}"
        ) from e


def execute_scalar(sql: str, params: Params = None, connection: str = "WORKBENCH",
                   database: Optional[str] = None) -> Any:
    """Run a query and return the first column of the first row (or None)."""
    engine = get_engine(connection, database=database)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).scalar()
    except (SQLServerConnectionError, SQLServerConfigurationError):
        raise
    except Exception as e:
        raise SQLServerQueryError(
            f"Scalar query failed (connection: {connection}): {e}"
        ) from e


def execute_command(sql: str, params: Params = None, connection: str = "WORKBENCH",
                    database: Optional[str] = None) -> int:
    """Run INSERT/UPDATE/DELETE inside a transaction; return rows affected.

    The `engine.begin()` block commits on success and rolls back on exception —
    the right default for the app's write path (e.g. append-event + update-current
    in one unit, see the event-sourcing convention in the data model)."""
    engine = get_engine(connection, database=database)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params or {})
            return result.rowcount
    except (SQLServerConnectionError, SQLServerConfigurationError):
        raise
    except Exception as e:
        raise SQLServerQueryError(
            f"Command failed (connection: {connection}): {e}"
        ) from e


__all__ = ["execute", "execute_one", "execute_scalar", "execute_command"]
