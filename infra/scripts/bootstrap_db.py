"""Bootstrap the three application-managed databases.

Creates rwb_workbench, rwb_exposure, rwb_loss if they do not exist.
Connects to master as the SA login (dev only) using AUTOCOMMIT — CREATE DATABASE
cannot run inside a transaction.

NEVER run this against the production SQL Server. Production databases are
provisioned once by the DBA with least-privilege app logins.

Run:  python -m infra.scripts.bootstrap_db
  or: python scripts/bootstrap_db.py   (from inside linux-box)
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

    odbc = (
        f"DRIVER={{{driver}}};SERVER={server},{port};DATABASE=master;"
        f"UID={user};PWD={password};TrustServerCertificate={trust};"
    )
    import urllib.parse
    url = "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc)
    return create_engine(url, isolation_level="AUTOCOMMIT")


def main() -> int:
    print("Bootstrap: connecting to master...")
    engine = _master_engine()
    try:
        with engine.connect() as conn:
            for db_name in DATABASES:
                exists = conn.execute(
                    text("SELECT DB_ID(:name)"), {"name": db_name}
                ).scalar()
                if exists is not None:
                    print(f"  [{db_name}] already exists — skipped")
                else:
                    safe = db_name.replace("]", "]]")
                    conn.execute(text(f"CREATE DATABASE [{safe}]"))
                    print(f"  [{db_name}] created")
    finally:
        engine.dispose()

    print("Bootstrap complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
