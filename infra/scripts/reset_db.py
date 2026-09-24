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
