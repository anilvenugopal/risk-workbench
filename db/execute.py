"""Safe execution path — **the default for all application code.**

Every function here uses **bound parameters** (`:name` placeholders compiled to
the driver's positional binds by SQLAlchemy `text()`), so values are sent to the
server separately from the SQL text. SQL injection is structurally impossible on
this path, which is why it is the default and the only path that may ever receive
user-derived values (submission ids, search text, …).

Returns plain Python (`list[dict]`, a scalar, or a rowcount) — no pandas. The
application repository sits on top of these.

    rows  = execute("SELECT * FROM submission WHERE status_code = :s",
                    {"s": "open"}, connection="WORKBENCH")
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


def row_limit(count: int, connection: str = "WORKBENCH",
              database: Optional[str] = None, *, offset: int = 0) -> str:
    """The trailing row cap in the connection's own dialect.

    SQL Server takes `OFFSET m ROWS FETCH NEXT n ROWS ONLY`; SQLite (the unit
    tier) takes `LIMIT n OFFSET m`. Neither `TOP` nor `LIMIT` runs on both, so
    callers that need a capped read append this instead of writing either one:

        rows = execute(f"SELECT ... ORDER BY name {row_limit(10)}", ...)
        page = execute(f"SELECT ... ORDER BY name {row_limit(50, offset=100)}", ...)

    Append to a query that already ends in ORDER BY — SQL Server rejects
    OFFSET/FETCH without one. `count` and `offset` are cast to int, so no caller
    text reaches the SQL text.
    """
    n, skip = int(count), int(offset)
    if get_engine(connection, database=database).dialect.name == "sqlite":
        return f"LIMIT {n} OFFSET {skip}"
    return f"OFFSET {skip} ROWS FETCH NEXT {n} ROWS ONLY"


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


def procedure_call(name: str, params: Dict[str, Any]) -> str:
    """The ``EXEC name @p = :p, ...`` statement ``execute_procedure`` runs.
    ``name`` and the parameter names are code constants, never user input."""
    assignments = ", ".join(f"@{p} = :{p}" for p in params)
    return f"EXEC {name} {assignments}"


def execute_procedure(name: str, params: Dict[str, Any], connection: str = "WORKBENCH",
                      database: Optional[str] = None) -> None:
    """Run a stored procedure with bound parameters on an autocommit connection.

    Autocommit keeps ``@@TRANCOUNT`` at 0 inside the procedure, so a procedure
    that manages its own transaction (``stage.usp_load_elt_result``) can run.
    Driver errors are re-raised unchanged: the caller reads the procedure's
    message from them."""
    engine = get_engine(connection, database=database)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(procedure_call(name, params)), params)


def read_uncommitted_hint(connection: str = "WORKBENCH",
                         database: Optional[str] = None) -> str:
    """``WITH (READUNCOMMITTED)`` on SQL Server, empty on any other dialect.
    Appended after a table name in request-path reads of tables another
    session may be updating inside a long transaction (the loss repository has
    read-committed snapshot isolation off)."""
    if get_engine(connection, database=database).dialect.name == "mssql":
        return "WITH (READUNCOMMITTED)"
    return ""


__all__ = ["execute", "execute_one", "execute_scalar", "execute_command",
           "execute_procedure", "procedure_call", "read_uncommitted_hint",
           "row_limit"]
