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
from sqlalchemy.engine import URL, Engine

DATABASES = ["rwb_workbench", "rwb_exposure", "rwb_loss"]


def _master_engine() -> Engine:
    server = os.environ["MSSQL_WORKBENCH_SERVER"]
    port = os.environ.get("MSSQL_WORKBENCH_PORT") or "1433"
    user = os.environ.get("MSSQL_WORKBENCH_USER", "sa")
    password = os.environ["MSSQL_SA_PASSWORD"]
    driver = os.environ.get("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server")
    trust = os.environ.get("MSSQL_TRUST_CERT", "yes")
    encrypt = os.environ.get("MSSQL_ENCRYPT", "no")

    # URL.create escapes the credentials; a hand-built ODBC string breaks on a
    # password holding ; or {.
    url = URL.create(
        "mssql+pyodbc",
        username=user, password=password, host=server, port=int(port),
        database="master",
        query={"driver": driver, "TrustServerCertificate": trust, "Encrypt": encrypt},
    )
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
