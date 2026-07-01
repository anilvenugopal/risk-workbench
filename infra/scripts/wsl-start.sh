#!/usr/bin/env bash
# wsl-start.sh — start infrastructure for WSL2 dev session.
# Idempotent: SQL Server and Redis are no-ops if already running.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

COMPOSE="docker compose -f infra/docker-compose.yml --env-file infra/.env"

# ── SQL Server ────────────────────────────────────────────────────────────────
$COMPOSE up -d sqlserver

# ── Redis ─────────────────────────────────────────────────────────────────────
if redis-cli ping > /dev/null 2>&1; then
    echo "Redis already running"
else
    redis-server --daemonize yes --logfile /tmp/rwb-redis.log --bind 127.0.0.1
    echo "Redis started (log: /tmp/rwb-redis.log)"
fi

echo ""
echo "Infrastructure is running. Open 3 more terminals:"
echo "  make wsl-app      ← web app on :8000 (uvicorn --reload)"
echo "  make wsl-worker   ← Dramatiq background workers"
echo "  make wsl-poller   ← IRP job poller"
