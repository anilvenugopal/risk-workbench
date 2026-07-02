"""Connection configuration — the single source of connection details for every
SQL Server target (Databridge, Workbench, anything else), resolved from
environment variables.

A *connection* is a named target: `MSSQL_{NAME}_*`. The same code resolves both
SQL Server authentication and Windows/Active Directory (Kerberos) authentication;
the only difference is `AUTH_TYPE`.

    MSSQL_{NAME}_SERVER       hostname or IP            (required)
    MSSQL_{NAME}_AUTH_TYPE    'SQL' | 'WINDOWS'         (optional, default SQL)
    MSSQL_{NAME}_USER         username                 (required for SQL auth)
    MSSQL_{NAME}_PASSWORD     password                 (required for SQL auth)
    MSSQL_{NAME}_PORT         port                     (optional, default 1433)
    MSSQL_{NAME}_DATABASE     default database         (optional)

Global (apply to every connection):

    MSSQL_DRIVER     ODBC driver name   (default 'ODBC Driver 18 for SQL Server')
    MSSQL_TRUST_CERT trust server cert  (default 'yes')
    MSSQL_TIMEOUT    connect timeout s  (default '30')

This module has no third-party imports, so it is trivially testable.
"""

import os
import urllib.parse
from typing import Dict, Optional

from .errors import SQLServerConfigurationError

VALID_AUTH_TYPES = ("SQL", "WINDOWS")


def get_connection_config(connection_name: str) -> Dict[str, str]:
    """Resolve the configuration dict for a named connection.

    Raises SQLServerConfigurationError if required variables are missing. Note
    there is **no default connection name** — callers always pass one explicitly,
    so a misconfiguration fails loudly instead of silently hitting a fallback.
    """
    if not connection_name:
        raise SQLServerConfigurationError("A connection name is required (none given).")

    name = connection_name.upper()
    prefix = f"MSSQL_{name}_"
    auth_type = os.getenv(f"{prefix}AUTH_TYPE", "SQL").upper()

    if auth_type not in VALID_AUTH_TYPES:
        raise SQLServerConfigurationError(
            f"Invalid auth type '{auth_type}' for connection '{name}'. "
            f"Valid values: {', '.join(VALID_AUTH_TYPES)}."
        )

    config: Dict[str, str] = {
        "name": name,
        "server": os.getenv(f"{prefix}SERVER") or "",
        "port": os.getenv(f"{prefix}PORT", "1433"),
        "database": os.getenv(f"{prefix}DATABASE", "") or "",
        "driver": os.getenv("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server"),
        "trust_cert": os.getenv("MSSQL_TRUST_CERT", "yes"),
        "timeout": os.getenv("MSSQL_TIMEOUT", "30"),
        "auth_type": auth_type,
    }

    if auth_type == "SQL":
        config["user"] = os.getenv(f"{prefix}USER") or ""
        config["password"] = os.getenv(f"{prefix}PASSWORD") or ""
        missing = [k for k in ("server", "user", "password") if not config[k]]
        if missing:
            raise SQLServerConfigurationError(
                f"Connection '{name}' (auth=SQL) is missing: "
                + ", ".join(f"{prefix}{k.upper()}" for k in missing)
            )
    else:  # WINDOWS
        if not config["server"]:
            raise SQLServerConfigurationError(  # pragma no cover
                f"Connection '{name}' (auth=WINDOWS) is missing {prefix}SERVER. "
                f"Windows auth also needs a valid Kerberos ticket (see db.kerberos)."
            )

    return config


def build_odbc_connection_string(config: Dict[str, str], database: Optional[str] = None) -> str:
    """Build the raw ODBC connection string from a config dict.

    Works for both auth modes: SQL auth adds UID/PWD, Windows auth adds
    Trusted_Connection=yes (the Kerberos ticket is established separately).
    """
    db = database or config.get("database") or None
    parts = [
        f"DRIVER={{{config['driver']}}}",
        f"SERVER={config['server']},{config['port']}",
    ]
    if db:
        parts.append(f"DATABASE={db}")
    if config["auth_type"] == "WINDOWS":
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={config['user']}")
        parts.append(f"PWD={config['password']}")
    parts.append(f"TrustServerCertificate={config['trust_cert']}")
    parts.append(f"Connection Timeout={config['timeout']}")
    return ";".join(parts) + ";"


def build_sqlalchemy_url(config: Dict[str, str], database: Optional[str] = None) -> str:
    """Wrap the ODBC string in a SQLAlchemy URL via odbc_connect.

    Using `odbc_connect=` means SQLAlchemy passes our exact ODBC string straight
    to pyodbc — identical behavior for SQL and Kerberos auth — while we still get
    SQLAlchemy's connection pool. No ORM, no dialect-specific string-building.
    """
    odbc = build_odbc_connection_string(config, database=database)
    return "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc)


__all__ = [
    "get_connection_config",
    "build_odbc_connection_string",
    "build_sqlalchemy_url",
    "VALID_AUTH_TYPES",
]
