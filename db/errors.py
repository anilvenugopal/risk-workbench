"""Exception hierarchy for the SQL Server data-access package.

One base type so callers can `except SQLServerError` to catch everything, with
specific subtypes for connection, configuration, and query failures.
"""

from sqlalchemy.exc import IntegrityError


class SQLServerError(Exception):
    """Base exception for all SQL Server operations."""


class SQLServerConnectionError(SQLServerError):
    """Connection could not be established (network, auth handshake, driver)."""


class SQLServerConfigurationError(SQLServerError):
    """Missing/invalid configuration (env vars, auth type, driver name)."""


class SQLServerQueryError(SQLServerError):
    """A query or command failed during execution."""


def is_unique_violation(exc: BaseException | None) -> bool:
    """True if ``exc`` — or any exception it chains from — is a UNIQUE/PK constraint
    violation. The safe path wraps driver errors in ``SQLServerQueryError`` via
    ``raise ... from e``, so walk ``__cause__`` to find a SQLAlchemy ``IntegrityError``
    whichever layer surfaced it (both SQLite and pyodbc map a UNIQUE violation to it).

    Lets an idempotent ``INSERT ... WHERE NOT EXISTS`` absorb the concurrent-writer race
    it cannot fully close under READ COMMITTED (both writers pass the pre-check; the
    loser hits the UNIQUE key) as a dedup hit instead of surfacing an unhandled 500."""
    seen: set[int] = set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, IntegrityError):
            return True
        seen.add(id(cur))
        cur = cur.__cause__
    return False


__all__ = [
    "SQLServerError",
    "SQLServerConnectionError",
    "SQLServerConfigurationError",
    "SQLServerQueryError",
    "is_unique_violation",
]
