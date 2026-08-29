"""Print the table- and column-level descriptions on dbo.poc_upload_trades
(the same MS_Description extended properties SSMS shows in Object Explorer).

    source infra/scripts/wsl-env.sh && uv run python pocs/elt_parquet_upload/show_table_comments.py
"""

from __future__ import annotations

from db import execute

TABLE = "poc_upload_trades"


def main() -> None:
    rows = execute(
        """
        SELECT c.name AS column_name, ep.value AS description
        FROM sys.extended_properties ep
        LEFT JOIN sys.columns c ON c.object_id = ep.major_id AND c.column_id = ep.minor_id
        WHERE ep.major_id = OBJECT_ID(:table)
        ORDER BY ep.minor_id
        """,
        {"table": f"dbo.{TABLE}"},
        connection="WORKBENCH",
    )

    if not rows:
        print(f"No comments found on dbo.{TABLE} — run run_poc.py first.")
        return

    for row in rows:
        label = row["column_name"] or "(table)"
        print(f"{label}:\n    {row['description']}\n")


if __name__ == "__main__":
    main()
