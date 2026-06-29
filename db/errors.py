"""Exception hierarchy for the SQL Server data-access package.

One base type so callers can `except SQLServerError` to catch everything, with
specific subtypes for connection, configuration, and query failures.
"""


class SQLServerError(Exception):
    """Base exception for all SQL Server operations."""


class SQLServerConnectionError(SQLServerError):
    """Connection could not be established (network, auth handshake, driver)."""


class SQLServerConfigurationError(SQLServerError):
    """Missing/invalid configuration (env vars, auth type, driver name)."""


class SQLServerQueryError(SQLServerError):
    """A query or command failed during execution."""


__all__ = [
    "SQLServerError",
    "SQLServerConnectionError",
    "SQLServerConfigurationError",
    "SQLServerQueryError",
]
