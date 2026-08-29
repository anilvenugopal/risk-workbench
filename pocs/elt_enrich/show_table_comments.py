"""Print the table- and column-level descriptions on both enrich POC tables
(the same MS_Description extended properties SSMS shows in Object Explorer).

    source infra/scripts/wsl-env.sh && uv run python pocs/elt_enrich/show_table_comments.py
"""

from __future__ import annotations

from db import execute

TABLES = ["poc_enrich_submission", "poc_enrich_policy_coverage"]


def show(table: str) -> None:
    rows = execute(
        """
        SELECT c.name AS column_name, ep.value AS description
        FROM sys.extended_properties ep
        LEFT JOIN sys.columns c ON c.object_id = ep.major_id AND c.column_id = ep.minor_id
        WHERE ep.major_id = OBJECT_ID(:table)
        ORDER BY ep.minor_id
        """,
        {"table": f"dbo.{table}"},
        connection="WORKBENCH",
    )

    print(f"--- dbo.{table} ---")
    if not rows:
        print(f"    No comments found — run the matching POC script first.\n")
        return

    for row in rows:
        label = row["column_name"] or "(table)"
        print(f"{label}:\n    {row['description']}\n")


def main() -> None:
    for table in TABLES:
        show(table)


if __name__ == "__main__":
    main()
