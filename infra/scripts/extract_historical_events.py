"""Extract the historical events from Moody's Risk Modeler ``EVENT`` reference export.

    uv run python infra/scripts/extract_historical_events.py ../EVENT.csv.gz

Reads the ``~``-delimited CSV inside the gzip, keeps the rows whose
``EVENTTYPECODE`` is ``HIST`` (case and surrounding whitespace ignored; inactive
rows are kept because the live lookup has no active flag), and writes
``db/bootstrap/seed/lookup_rms_historical_rds.csv`` with the columns
``EventID, Peril, Type, Name, ModelVersion, CatYear, PCS``
(specs/014-results-export/research.md R16).
The source file is 116 MB and is never committed; the seed is.
"""

from __future__ import annotations

import csv
import gzip
import re
import sys
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "db" / "bootstrap").is_dir())
SEED_PATH = ROOT / "db" / "bootstrap" / "seed" / "lookup_rms_historical_rds.csv"
COLUMNS = ["EventID", "Peril", "Type", "Name", "ModelVersion", "CatYear", "PCS"]
_YEAR = re.compile(r"(?<!\d)(1[6-9]\d{2}|20\d{2})(?!\d)")


def _seed_row(source: dict) -> dict:
    name = (source["EVENTNAME"] or "").strip()
    year = _YEAR.search(name)
    return {
        "EventID": source["EVENTID"].strip(),
        "Peril": source["PERILCODE"].strip(),
        "Type": "HIST",
        "Name": name,
        "ModelVersion": source["MODELVERSIONCODE"].strip(),
        "CatYear": year.group(1) if year else "",
        "PCS": "",
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: extract_historical_events.py <path to EVENT.csv.gz>", file=sys.stderr)
        return 2
    source = Path(argv[1])
    if not source.is_file():
        print(f"ERROR: {source} not found", file=sys.stderr)
        return 1

    rows: list[dict] = []
    with gzip.open(source, "rt", encoding="utf-8", errors="replace", newline="") as handle:
        for record in csv.DictReader(handle, delimiter="~"):
            if (record.get("EVENTTYPECODE") or "").strip().upper() == "HIST":
                rows.append(_seed_row(record))
    rows.sort(key=lambda r: (r["ModelVersion"], r["Peril"], int(r["EventID"])))

    SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SEED_PATH.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    versions = {r["ModelVersion"] for r in rows}
    print(f"{len(rows)} rows written to {SEED_PATH} "
          f"({len(versions)} distinct model versions)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
