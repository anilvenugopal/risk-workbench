"""Seed the WORKBENCH database after Alembic migrations.

Idempotent: safe to run multiple times. Uses MERGE / IF NOT EXISTS patterns.

Inserts:
  - role_kind rows (analyst, admin) — these are also seeded in the migration,
    but this script handles the case where the migration already ran them.
  - One dev fixture admin user: admin@example.com / password: Admin1234567!
    (bcrypt cost 12, must_change_password=False, role=admin)
    Only inserted in development (APP_ENV=development).

Run via Makefile (preferred):
    make wsl-db-rebuild     # WSL2 native
    make db-rebuild         # Docker
"""

from __future__ import annotations

import os
import sys

import bcrypt
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def _workbench_engine() -> Engine:
    server = os.environ["MSSQL_WORKBENCH_SERVER"]
    port = os.environ.get("MSSQL_WORKBENCH_PORT", "1433")
    user = os.environ.get("MSSQL_WORKBENCH_USER", "sa")
    password = os.environ["MSSQL_WORKBENCH_PASSWORD"]
    database = os.environ.get("MSSQL_WORKBENCH_DATABASE", "rwb_workbench")
    driver = os.environ.get("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server")
    trust = os.environ.get("MSSQL_TRUST_CERT", "yes")

    import urllib.parse
    odbc = (
        f"DRIVER={{{driver}}};SERVER={server},{port};DATABASE={database};"
        f"UID={user};PWD={password};TrustServerCertificate={trust};"
    )
    url = "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc)
    return create_engine(url)


def _hash(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def main() -> int:
    print("Seed: connecting to rwb_workbench...")
    engine = _workbench_engine()
    try:
        with engine.begin() as conn:
            # role_kind seeds (idempotent via MERGE)
            conn.execute(text("""
                MERGE role_kind AS target
                USING (VALUES
                    ('analyst', 'Analyst', 10, 0),
                    ('admin',   'Administrator', 20, 1)
                ) AS src (code, label, sort_order, is_admin)
                ON target.code = src.code
                WHEN NOT MATCHED THEN
                    INSERT (code, label, sort_order, is_admin)
                    VALUES (src.code, src.label, src.sort_order, src.is_admin);
            """))
            print("  [role_kind] seeds OK")

            app_env = os.environ.get("APP_ENV", "development")
            if app_env == "development":
                # Dev fixture: admin@example.com — admin role, no forced change
                existing = conn.execute(
                    text("SELECT id FROM app_user WHERE email = 'admin@example.com'")
                ).fetchone()
                if existing is None:
                    pw_hash = _hash("Admin1234567!")
                    conn.execute(text("""
                        INSERT INTO app_user
                            (email, display_name, password_hash, must_change_password, is_active)
                        VALUES
                            ('admin@example.com', 'Dev Admin', :pw, 0, 1)
                    """), {"pw": pw_hash})
                    user_id = conn.execute(
                        text("SELECT id FROM app_user WHERE email = 'admin@example.com'")
                    ).scalar()
                    conn.execute(text("""
                        INSERT INTO user_role (user_id, role_code)
                        VALUES (:uid, 'admin')
                    """), {"uid": user_id})
                    print("  [app_user] dev fixture admin@example.com created")
                else:
                    print("  [app_user] dev fixture already exists — skipped")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    print("Seed complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
