#!/usr/bin/env bash
# Install pinned runtime dependencies and migrate the configured Workbench
# database. All databases must exist before this script runs.

set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.14}"

for required in app db pyproject.toml alembic requirements.txt infra/.env; do
    if [ ! -e "$required" ]; then
        echo "ERROR: '$required' not found in $(pwd)." >&2
        echo "       Run from the deployed application directory." >&2
        exit 1
    fi
done

echo "=== 1. Virtual environment ==="
if [ ! -d .venv ]; then
    "$PYTHON_BIN" -m venv .venv
    echo "  Created .venv"
else
    echo "  .venv already exists"
fi

source .venv/bin/activate

echo ""
echo "=== 2. Install hash-verified runtime dependencies ==="
python -m pip install --require-hashes -r requirements.txt
python - <<'PY'
from importlib.metadata import version

installed = version("irp-integration")
if installed != "0.9.0":
    raise SystemExit(f"ERROR: irp-integration 0.9.0 required, found {installed}")
print(f"  irp-integration {installed} from PyPI")
PY

echo ""
echo "=== 3. Verify Python and SQL Server driver ==="
python -c "import pyodbc; print('  pyodbc drivers:', pyodbc.drivers())"

set -a
source infra/.env
set +a

echo ""
echo "=== 4. Verify configured Workbench database ==="
python - <<'PY'
from db.connection import test_connection

if not test_connection("WORKBENCH"):
    raise SystemExit("ERROR: cannot connect to the configured WORKBENCH database")
print("  WORKBENCH connection OK")
PY

echo ""
echo "=== 5. Migrate Workbench database ==="
alembic upgrade head
alembic current --check-heads

echo ""
echo "=== 6. Verify application import ==="
python -c "import app.main; print('  app import OK')"

echo ""
echo "=== Done. Dependencies installed; Workbench migrations applied. ==="
