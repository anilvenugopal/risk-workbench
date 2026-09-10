"""Set up the development loss repository (rwb_loss).

Applies db/bootstrap/loss_dev_mirror.sql (CIC's five tables) and
db/bootstrap/loss_schema.sql (the Workbench's stage schema and load procedure)
over the LOSS connection, then seeds dbo.Client with three made-up clients and
dbo.Lookup_RMS_HistoricalRDS from db/bootstrap/seed/lookup_rms_historical_rds.csv.
Idempotent: a second run leaves every row count unchanged.

Refuses to run unless MSSQL_LOSS_DATABASE is rwb_loss: at CIC the five tables
are theirs and the DBA installs loss_schema.sql by hand.

Run via Makefile: make bootstrap-loss (Docker) or make wsl-bootstrap-loss (WSL2).
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

from sqlalchemy import text

# infra/scripts is mounted at /workspace/scripts inside linux-box, so the repo
# root is found by walking up to the directory that holds db/bootstrap.
ROOT = next(p for p in Path(__file__).resolve().parents if (p / "db" / "bootstrap").is_dir())
sys.path.insert(0, str(ROOT))
BOOTSTRAP_DIR = ROOT / "db" / "bootstrap"
LOOKUP_SEED = BOOTSTRAP_DIR / "seed" / "lookup_rms_historical_rds.csv"

CLIENTS = [
    (1, "Example Re", "Y"),
    (2, "Sample Mutual Insurance", "Y"),
    (3, "Former Client (inactive)", "N"),
]


def _seed_clients(conn) -> None:
    for client_id, name, active in CLIENTS:
        conn.execute(text(
            "MERGE dbo.Client AS target "
            "USING (VALUES (:id, :name, :active)) AS src (ClientID, ClientName, ActiveFlag) "
            "ON target.ClientID = src.ClientID "
            "WHEN MATCHED THEN UPDATE SET ClientName = src.ClientName, "
            "    ActiveFlag = src.ActiveFlag "
            "WHEN NOT MATCHED THEN INSERT (ClientID, ClientName, ActiveFlag) "
            "    VALUES (src.ClientID, src.ClientName, src.ActiveFlag);"
        ), {"id": client_id, "name": name, "active": active})


def _seed_lookup(conn) -> int:
    with LOOKUP_SEED.open(encoding="utf-8", newline="") as handle:
        rows = [{
            "event_id": int(r["EventID"]),
            "cat_year": int(r["CatYear"]) if r["CatYear"] else None,
            "peril": r["Peril"],
            "type": r["Type"],
            "name": r["Name"],
            "pcs": r["PCS"] or None,
            "model_version": r["ModelVersion"],
        } for r in csv.DictReader(handle)]
    conn.execute(text("TRUNCATE TABLE dbo.Lookup_RMS_HistoricalRDS"))
    conn.execute(text(
        "INSERT INTO dbo.Lookup_RMS_HistoricalRDS "
        "(EventID, CatYear, Peril, [Type], [Name], [PCS#], ModelVersion) "
        "VALUES (:event_id, :cat_year, :peril, :type, :name, :pcs, :model_version)"
    ), rows)
    return len(rows)


def main() -> int:
    database = os.environ.get("MSSQL_LOSS_DATABASE")
    if database != "rwb_loss":
        print(f"ERROR: MSSQL_LOSS_DATABASE is {database!r}, not 'rwb_loss'. "
              "This script only sets up the development mirror.", file=sys.stderr)
        return 1

    from db import get_connection  # noqa: PLC0415
    from db.scripts import execute_script_file  # noqa: PLC0415 — trusted DDL, dev only

    print("bootstrap-loss: applying loss_dev_mirror.sql")
    execute_script_file(BOOTSTRAP_DIR / "loss_dev_mirror.sql", connection="LOSS")
    print("bootstrap-loss: applying loss_schema.sql")
    execute_script_file(BOOTSTRAP_DIR / "loss_schema.sql", connection="LOSS")

    with get_connection("LOSS") as conn, conn.begin():
        _seed_clients(conn)
        lookup_rows = _seed_lookup(conn)
        client_rows = conn.execute(text("SELECT COUNT(*) FROM dbo.Client")).scalar()
    print(f"bootstrap-loss: dbo.Client = {client_rows} rows, "
          f"dbo.Lookup_RMS_HistoricalRDS = {lookup_rows} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
