"""Drop every table from the configured Workbench database.

The production database may have been built from an older edit of
``0001_initial.py``. Running ``alembic downgrade base`` against that database
can reference tables or constraints that were never installed. Querying SQL
Server for the installed tables avoids that mismatch.

The script prints every table and requires the operator to type the configured
database name before executing any DDL. ``--yes`` exists for the automated
scratch-database test; the RHEL9 rebuild command does not pass it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

repo_root = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "db" / "config.py").is_file()
)
sys.path.insert(0, str(repo_root))

from db import get_connection, get_connection_config  # noqa: E402

_LIST_TABLES = text("""
    SELECT SCHEMA_NAME(schema_id) AS schema_name, name
    FROM sys.tables
    ORDER BY schema_name, name
""")

_LIST_FOREIGN_KEYS = text("""
    SELECT SCHEMA_NAME(t.schema_id) AS schema_name,
           t.name AS table_name,
           fk.name AS constraint_name
    FROM sys.foreign_keys fk
    JOIN sys.tables t ON t.object_id = fk.parent_object_id
""")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the database-name prompt",
    )
    args = parser.parse_args(argv)
    database = get_connection_config("WORKBENCH")["database"]

    with get_connection("WORKBENCH") as conn:
        tables = conn.execute(_LIST_TABLES).all()

        if not tables:
            print(f"{database} has no tables; nothing to drop.")
            return 0

        print(f"About to drop {len(tables)} tables from {database}:")
        for schema_name, table_name in tables:
            print(f"    {schema_name}.{table_name}")

        if not args.yes:
            print()
            confirmation = input("Type the database name to confirm: ")
            if confirmation != database:
                print("Aborted.", file=sys.stderr)
                return 1

        foreign_keys = conn.execute(_LIST_FOREIGN_KEYS).all()

        # End the implicit metadata transaction before grouping every DROP in
        # one transaction. A failed DROP then rolls back the whole deletion.
        conn.commit()
        with conn.begin():
            for schema_name, table_name, constraint_name in foreign_keys:
                conn.execute(text(
                    f"ALTER TABLE [{schema_name}].[{table_name}] "
                    f"DROP CONSTRAINT [{constraint_name}]"
                ))
            for schema_name, table_name in tables:
                conn.execute(text(f"DROP TABLE [{schema_name}].[{table_name}]"))

    print(
        f"Dropped {len(foreign_keys)} foreign keys and {len(tables)} tables "
        f"from {database}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
