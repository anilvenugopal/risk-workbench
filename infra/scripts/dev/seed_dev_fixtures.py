"""Insert the development fixture admin into the WORKBENCH database.

Creates admin@example.com / Admin1234567! (bcrypt cost 12, must_change_password
off, role=admin) if it is not already there. Idempotent.

The kind tables are not seeded here. `alembic/versions/0001_initial.py` seeds all
thirteen of them as part of `upgrade()`, so a migrated database already has every
row this script used to MERGE.

Refuses to run unless APP_ENV is development — the fixture's password is in this
file and in the repository.

Run via Makefile (preferred):
    make wsl-db-rebuild     # WSL2 native
    make db-rebuild         # Docker
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import bcrypt
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# The repo root holds the db package: infra/scripts is also mounted at
# /workspace/scripts inside linux-box, so walk up rather than count parents.
sys.path.insert(0, str(next(
    p for p in Path(__file__).resolve().parents if (p / "db" / "config.py").is_file()
)))

from db.config import build_sqlalchemy_url, get_connection_config  # noqa: E402

EMAIL = "admin@example.com"
PASSWORD = "Admin1234567!"


def _workbench_engine() -> Engine:
    return create_engine(build_sqlalchemy_url(get_connection_config("WORKBENCH")))


def _hash(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def main() -> int:
    app_env = os.environ.get("APP_ENV")
    if app_env != "development":
        print(f"ERROR: APP_ENV is {app_env!r}, not 'development'. This script only "
              f"creates the {EMAIL} development fixture.", file=sys.stderr)
        return 1

    print(f"Seed: connecting to {os.environ.get('MSSQL_WORKBENCH_DATABASE')}...")
    engine = _workbench_engine()
    try:
        with engine.begin() as conn:
            existing = conn.execute(
                text("SELECT id FROM app_user WHERE email = :email"), {"email": EMAIL}
            ).fetchone()
            if existing is not None:
                print(f"  [app_user] {EMAIL} already exists — skipped")
                return 0

            conn.execute(text("""
                INSERT INTO app_user
                    (email, display_name, password_hash, must_change_password, is_active)
                VALUES
                    (:email, 'Dev Admin', :pw, 0, 1)
            """), {"email": EMAIL, "pw": _hash(PASSWORD)})
            user_id = conn.execute(
                text("SELECT id FROM app_user WHERE email = :email"), {"email": EMAIL}
            ).scalar()
            conn.execute(text("""
                INSERT INTO user_role (user_id, role_code)
                VALUES (:uid, 'admin')
            """), {"uid": user_id})
            print(f"  [app_user] {EMAIL} created")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    print("Seed complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
