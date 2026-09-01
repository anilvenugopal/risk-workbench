"""Unified SQL Server data access for the Risk Workbench.

ONE package handles connection management, SQL Server + Windows/Kerberos auth, and
SQL execution for **every** target (Workbench app DB, Databridge/Moody's, …).
Targets are just named connections (`MSSQL_{NAME}_*`); auth is per-connection.

It exposes **two execution styles**, split by safety — not by target:

  • Safe path (DEFAULT) — `execute`, `execute_one`, `execute_scalar`, and
    `execute_command`. Bound parameters, returns
    `list[dict]`/scalar/rowcount. Injection-safe; the ONLY path that may receive
    user-derived values. Use this for ALL application data access.

  • Trusted-script path — `execute_query`, `execute_script_file`,
    `display_result_sets`. `{{ param }}` text substitution, GO batches, multiple
    result sets, pandas DataFrames. For curated, team-authored scripts against
    external data sources (Databridge) ONLY. Never user input; never the app's
    own tables; never the web layer. (Lives in `db.scripts`.)

Both styles share the same pooled SQLAlchemy-core engine layer (no ORM).

Examples:
    from db import execute, execute_command
    rows = execute("SELECT * FROM submission WHERE id = :id", {"id": 7},
                   connection="WORKBENCH")

    from db.scripts import execute_script_file       # trusted external scripts
    dfs = execute_script_file("control_totals/3d_RMS_EDM_Control_Totals.sql",
                              params={"DATE_VALUE": "202503"}, connection="DATABRIDGE")
"""

from .errors import (SQLServerError, SQLServerConnectionError,
                     SQLServerConfigurationError, SQLServerQueryError,
                     is_unique_violation)
from .config import (get_connection_config, build_odbc_connection_string,
                     build_sqlalchemy_url)
from .connection import (get_engine, get_connection, test_connection,
                         dispose_all)
from .kerberos import (check_kerberos_status, init_kerberos, is_ticket_valid,
                       ensure_valid_kerberos_ticket)
from .execute import (execute, execute_one, execute_scalar, execute_command,
                      row_limit)
from .elt import upload_parquet, enrich

__all__ = [
    # errors
    "SQLServerError", "SQLServerConnectionError", "SQLServerConfigurationError",
    "SQLServerQueryError", "is_unique_violation",
    # config
    "get_connection_config", "build_odbc_connection_string", "build_sqlalchemy_url",
    # connection / pool
    "get_engine", "get_connection", "test_connection", "dispose_all",
    # kerberos
    "check_kerberos_status", "init_kerberos", "is_ticket_valid",
    "ensure_valid_kerberos_ticket",
    # safe execution (default)
    "execute", "execute_one", "execute_scalar", "execute_command", "row_limit",
    # ELT / enrichment
    "upload_parquet", "enrich",
]
# NOTE: the trusted-script path is intentionally NOT re-exported here. Import it
# explicitly from db.scripts so its use is always visible in code review.
