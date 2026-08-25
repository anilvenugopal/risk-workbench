#!/usr/bin/env bash
# wsl-setup.sh — database setup for WSL2 dev environment.
#
# Prerequisites (must be done manually before running this — see docs/SCAFFOLDING.md):
#   1. uv installed          (curl -LsSf https://astral.sh/uv/install.sh | sh)
#   2. ODBC Driver 18        (sudo apt-get install msodbcsql18 unixodbc-dev)
#   3. Redis                 (sudo apt-get install redis-server)
#   4. infra/.env populated  (cp infra/.env.example infra/.env, then edit)
#
# This script:
#   - uv sync (install Python deps)
#   - Start SQL Server container and wait for it to be healthy
#   - Create rwb_workbench, rwb_exposure, rwb_loss (skips existing)
#   - Run Alembic migrations on rwb_workbench
#
# Safe to re-run: every step checks state before acting.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# ── Preflight checks ──────────────────────────────────────────────────────────
for cmd in uv redis-server odbcinst docker; do
    if ! which "$cmd" > /dev/null 2>&1; then
        echo "ERROR: '$cmd' not found. See docs/SCAFFOLDING.md First-Time Setup." >&2
        exit 1
    fi
done

if ! dpkg -l msodbcsql18 > /dev/null 2>&1; then
    echo "ERROR: ODBC Driver 18 not installed." >&2
    echo "       Run Step 3 in docs/SCAFFOLDING.md." >&2
    echo "       If you are on Ubuntu 26.04, use the 24.04 repo URL (see SCAFFOLDING.md)." >&2
    exit 1
fi

if [ ! -f "infra/.env" ]; then
    echo "ERROR: infra/.env not found." >&2
    echo "       cp infra/.env.example infra/.env  then fill in SESSION_SECRET_KEY and MSSQL_SA_PASSWORD." >&2
    exit 1
fi

source infra/scripts/wsl-env.sh

COMPOSE="docker compose -f infra/docker-compose.yml --env-file infra/.env"

# ── Step 1: Python deps ───────────────────────────────────────────────────────
echo "=== Step 1: Python dependencies ==="
uv sync --frozen
echo ""

# ── Step 2: SQL Server container ──────────────────────────────────────────────
echo "=== Step 2: SQL Server container ==="
$COMPOSE up -d sqlserver

echo "  Waiting for SQL Server to accept connections (up to 90s)..."
for i in $(seq 1 30); do
    if $COMPOSE exec sqlserver \
        /opt/mssql-tools18/bin/sqlcmd \
        -C -S localhost -U sa -P "$MSSQL_SA_PASSWORD" \
        -Q "SELECT 1" > /dev/null 2>&1; then
        echo "  SQL Server ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo ""
        echo "ERROR: SQL Server did not become ready after 90s." >&2
        echo "Check logs: docker compose -f infra/docker-compose.yml logs sqlserver" >&2
        exit 1
    fi
    echo "  ($i/30) not ready, waiting 3s..."
    sleep 3
done
echo ""

# ── Step 3: Create databases ──────────────────────────────────────────────────
echo "=== Step 3: Create databases (skips existing) ==="
uv run python infra/scripts/bootstrap_db.py
echo ""

# ── Step 4: Migrations ────────────────────────────────────────────────────────
echo "=== Step 4: Alembic migrations ==="
uv run alembic upgrade head
echo ""

echo "Setup complete."
echo ""
echo "  Next: make wsl-start"
echo "  Then open 3 terminals: make wsl-app / make wsl-worker / make wsl-poller"
