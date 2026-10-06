"""Add the cedant names in an Excel file that are not yet in rwb_workbench.cedant.

The file has one column headed ``Cedant`` (issue 129). The default is the
committed db/bootstrap/seed/cedants.xlsx, which revision 0005 loads once; run
this after the client sends a new list. A name already in the table (case
ignored) is skipped, so a second run inserts nothing, and no cedant is renamed
or deleted.

Usage, dev:    make wsl-load-cedants [FILE=path/to/cedants.xlsx]   (Docker-only: make load-cedants)
Usage, server: cd /rms && set -a && source infra/.env && set +a &&
               .venv/bin/python infra/scripts/load_cedants.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# infra/scripts is mounted at /workspace/scripts inside linux-box, so the repo
# root is found by walking up to the directory that holds db/bootstrap.
ROOT = next(p for p in Path(__file__).resolve().parents if (p / "db" / "bootstrap").is_dir())
sys.path.insert(0, str(ROOT))


def main() -> int:
    from db import get_connection  # noqa: PLC0415
    from db.cedant_seed import SEED_FILE, load_cedant_names, read_cedant_names  # noqa: PLC0415

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else SEED_FILE
    try:
        names = read_cedant_names(path)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    with get_connection("WORKBENCH") as conn, conn.begin():
        inserted = load_cedant_names(conn, names)
    print(f"load-cedants: {path.name} lists {len(names)} cedants; inserted {inserted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
