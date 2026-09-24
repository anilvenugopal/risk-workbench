"""Empty the Workbench database: drop every foreign key, then every table.

This is the demolition half of a rebuild. `alembic downgrade base` cannot do the
job here, because it is a hand-written list of DROPs describing the schema the
CURRENT migration file builds. The production database was built from an older
edit of that same file, so the two disagree the moment the file gains a table,
and alembic stops on the first object the database never had. Dropping whatever
is actually present asks the database instead of assuming.

Reads MSSQL_WORKBENCH_* from the environment and connects as the app login. It
needs no server-level rights: it stays inside the one database and never issues
CREATE DATABASE, so no SA login is involved.

Prints every table it is about to drop and requires the database name typed back
before it touches anything.

Run:  python infra/scripts/rhel9/drop_workbench_tables.py
      python infra/scripts/rhel9/drop_workbench_tables.py --yes   (skip the prompt)
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, text

# The repo root holds the db package.
sys.path.insert(0, str(next(
    p for p in Path(__file__).resolve().parents if (p / "db" / "config.py").is_file()
)))

from db.config import build_sqlalchemy_url, get_connection_config  # noqa: E402

_LIST_TABLES = text("""
    SELECT SCHEMA_NAME(schema_id) AS schema_name, name
    FROM sys.tables
    ORDER BY schema_name, name
""")

# One statement per constraint/table rather than a single generated batch: a
# failure then names the object it failed on.
_LIST_FOREIGN_KEYS = text("""
    SELECT SCHEMA_NAME(t.schema_id) AS schema_name,
           t.name AS table_name,
           fk.name AS constraint_name
    FROM sys.foreign_keys fk
    JOIN sys.tables t ON t.object_id = fk.parent_object_id
""")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    assume_yes = "--yes" in argv

    config = get_connection_config("WORKBENCH")
    database = config["database"]
    engine = create_engine(build_sqlalchemy_url(config))

    try:
        with engine.connect() as conn:
            tables = conn.execute(_LIST_TABLES).all()

            if not tables:
                print(f"{database} has no tables — nothing to drop.")
                return 0

            print(f"About to drop {len(tables)} tables from {database}:")
            for schema_name, name in tables:
                print(f"    {schema_name}.{name}")
            print("")
            print("  Every row in them is lost.")

            if not assume_yes:
                print("")
                confirm = input("  Type the database name to confirm: ")
                if confirm != database:
                    print("Aborted.", file=sys.stderr)
                    return 1

            foreign_keys = conn.execute(_LIST_FOREIGN_KEYS).all()

            # The metadata reads above start SQLAlchemy's implicit transaction.
            # End it before opening the transaction that owns every DROP.
            conn.commit()

            # Foreign keys first, so the table drops need no ordering.
            with conn.begin():
                for schema_name, table_name, constraint_name in foreign_keys:
                    conn.execute(text(
                        f"ALTER TABLE [{schema_name}].[{table_name}] "
                        f"DROP CONSTRAINT [{constraint_name}]"))
                for schema_name, name in tables:
                    conn.execute(text(f"DROP TABLE [{schema_name}].[{name}]"))

        print("")
        print(f"Dropped {len(foreign_keys)} foreign keys and {len(tables)} tables "
              f"from {database}.")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
