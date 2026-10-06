#!/usr/bin/env bash
# wsl-setup.sh — first-time database setup for the WSL2 dev environment.
#
# Usage: make wsl-setup
#
# Prerequisites (docs/LOCAL_DEV_SETUP.md Steps 1-4): infra/.env, uv,
# ODBC Driver 18, redis-server or valkey-server, docker or podman.
# Idempotent.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# ── Preflight checks ──────────────────────────────────────────────────────────
if [ ! -f "infra/.env" ]; then
    echo "ERROR: infra/.env not found." >&2
    echo "       cp infra/.env.example infra/.env  then fill in SESSION_SECRET_KEY and MSSQL_SA_PASSWORD." >&2
    exit 1
fi

source infra/scripts/wsl-env.sh

# Passes when any one of the given commands is on PATH.
require() {
    for cmd in "$@"; do
        command -v "$cmd" > /dev/null 2>&1 && return
    done
    echo "ERROR: none of '$*' found. See docs/LOCAL_DEV_SETUP.md First-Time Setup." >&2
    exit 1
}
require uv
require odbcinst
require redis-server valkey-server
require "$RWB_CONTAINER_RUNTIME"

if ! odbcinst -q -d | grep -qF "[ODBC Driver 18 for SQL Server]"; then
    echo "ERROR: ODBC Driver 18 not installed." >&2
    echo "       Run Step 3 in docs/LOCAL_DEV_SETUP.md." >&2
    exit 1
fi

# ── Step 1: Python deps ───────────────────────────────────────────────────────
echo "=== Step 1: Python dependencies ==="
uv sync --frozen
echo ""

# ── Step 2: SQL Server and Redis ──────────────────────────────────────────────
echo "=== Step 2: SQL Server and Redis ==="
bash infra/scripts/wsl-start.sh
echo ""

# ── Step 3: Create databases ──────────────────────────────────────────────────
echo "=== Step 3: Create databases (skips existing) ==="
uv run python infra/scripts/bootstrap_db.py
echo ""

# ── Step 4: Migrations ────────────────────────────────────────────────────────
echo "=== Step 4: Alembic migrations ==="
uv run alembic upgrade head
echo ""

# ── Step 5: Seed rwb_workbench ────────────────────────────────────────────────
echo "=== Step 5: Seed kind tables and the dev admin ==="
uv run python infra/scripts/seed_db.py
echo ""

# ── Step 6: rwb_loss ──────────────────────────────────────────────────────────
echo "=== Step 6: Bootstrap rwb_loss ==="
uv run python infra/scripts/bootstrap_loss.py
echo ""

echo "Setup complete. SQL Server and Redis are running."
echo ""
echo "  Open 3 terminals: make wsl-app / make wsl-workers / make wsl-poller"
