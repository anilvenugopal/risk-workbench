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
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# The repo root holds the db package: infra/scripts is also mounted at
# /workspace/scripts inside linux-box, so walk up rather than count parents.
sys.path.insert(0, str(next(
    p for p in Path(__file__).resolve().parents if (p / "db" / "config.py").is_file()
)))

from db.config import build_sqlalchemy_url, get_connection_config  # noqa: E402

DATABASES = ["rwb_workbench", "rwb_exposure", "rwb_loss"]


def _master_engine() -> Engine:
    config = get_connection_config("WORKBENCH")
    # CREATE DATABASE needs server-level rights, which the app login lacks.
    config["password"] = os.environ["MSSQL_SA_PASSWORD"]
    return create_engine(
        build_sqlalchemy_url(config, database="master"),
        isolation_level="AUTOCOMMIT",
    )


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
