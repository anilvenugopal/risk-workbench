"""Drop and recreate the three application-managed databases.

Drops rwb_workbench, rwb_exposure, rwb_loss and recreates them empty.
Alembic upgrade and seed_db are run by the Makefile targets after this script.

NEVER touches DATABRIDGE — that database is Moody's-managed and is never
created, dropped, or migrated by this application.

Run via Makefile (preferred):
    make wsl-db-rebuild     # WSL2 native
    make db-rebuild         # Docker
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


DATABASES = ["rwb_workbench", "rwb_exposure", "rwb_loss"]


def _master_engine() -> Engine:
    server = os.environ["MSSQL_WORKBENCH_SERVER"]
    port = os.environ.get("MSSQL_WORKBENCH_PORT", "1433")
    user = os.environ.get("MSSQL_WORKBENCH_USER", "sa")
    password = os.environ["MSSQL_SA_PASSWORD"]
    driver = os.environ.get("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server")
    trust = os.environ.get("MSSQL_TRUST_CERT", "yes")

    import urllib.parse
    odbc = (
        f"DRIVER={{{driver}}};SERVER={server},{port};DATABASE=master;"
        f"UID={user};PWD={password};TrustServerCertificate={trust};"
    )
    url = "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc)
    return create_engine(url, isolation_level="AUTOCOMMIT")


def main(args: list[str] | None = None) -> int:
    print("Reset: connecting to master...")
    engine = _master_engine()
    try:
        with engine.connect() as conn:
            for db_name in DATABASES:
                safe = db_name.replace("]", "]]")
                # Kick all other connections before dropping.
                conn.execute(text(
                    f"IF DB_ID('{db_name}') IS NOT NULL "
                    f"ALTER DATABASE [{safe}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
                ))
                conn.execute(text(f"DROP DATABASE IF EXISTS [{safe}]"))
                conn.execute(text(f"CREATE DATABASE [{safe}]"))
                print(f"  [{db_name}] dropped and recreated")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    print("Reset complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
