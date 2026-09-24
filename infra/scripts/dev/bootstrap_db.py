"""Create the local development databases named by MSSQL_WORKBENCH_DATABASE and
MSSQL_LOSS_DATABASE.

Connects to master as the SA login using AUTOCOMMIT — CREATE DATABASE cannot run
inside a transaction.

Refuses to run unless APP_ENV is development. Production's Workbench database is
created once by the DBA with a least-privilege app login, and the loss repository
is CIC's own; neither is this script's to create. --recreate additionally drops
each database first, which is why the refusal comes before any work.

Run:  python infra/scripts/dev/bootstrap_db.py
  or: python scripts/dev/bootstrap_db.py   (from inside linux-box)
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

CONNECTIONS = ["WORKBENCH", "LOSS"]


def _master_engine() -> Engine:
    config = get_connection_config("WORKBENCH")
    # CREATE DATABASE needs server-level rights, which the app login lacks.
    config["password"] = os.environ["MSSQL_SA_PASSWORD"]
    return create_engine(
        build_sqlalchemy_url(config, database="master"),
        isolation_level="AUTOCOMMIT",
    )


def _database_names() -> list[str]:
    names = []
    for connection in CONNECTIONS:
        name = os.environ.get(f"MSSQL_{connection}_DATABASE")
        if not name:
            raise SystemExit(f"ERROR: MSSQL_{connection}_DATABASE is not set.")
        names.append(name)
    return names


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    recreate = "--recreate" in argv

    app_env = os.environ.get("APP_ENV")
    if app_env != "development":
        print(f"ERROR: APP_ENV is {app_env!r}, not 'development'. This script only "
              "creates the local development databases.", file=sys.stderr)
        return 1

    names = _database_names()

    print("Bootstrap: connecting to master...")
    engine = _master_engine()
    try:
        with engine.connect() as conn:
            for db_name in names:
                safe = db_name.replace("]", "]]")
                exists = conn.execute(
                    text("SELECT DB_ID(:name)"), {"name": db_name}
                ).scalar()
                if exists is not None and recreate:
                    conn.execute(text(
                        f"ALTER DATABASE [{safe}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE"))
                    conn.execute(text(f"DROP DATABASE [{safe}]"))
                    print(f"  [{db_name}] dropped")
                    exists = None
                if exists is not None:
                    print(f"  [{db_name}] already exists — skipped")
                else:
                    conn.execute(text(f"CREATE DATABASE [{safe}]"))
                    print(f"  [{db_name}] created")
    finally:
        engine.dispose()

    print("Bootstrap complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
