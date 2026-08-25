#!/usr/bin/env bash
# wsl-start.sh — start infrastructure for WSL2 dev session.
# Idempotent: SQL Server and Redis are no-ops if already running.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

COMPOSE="docker compose -f infra/docker-compose.yml --env-file infra/.env"

# ── SQL Server ────────────────────────────────────────────────────────────────
$COMPOSE up -d sqlserver

# ── Redis (AOF durability required) ──────────────────────────────────────────
# appendonly yes + appendfsync everysec: acknowledged enqueues survive a broker
# crash (≤ ~1s worst-case loss). This closes the pending-lost failure case and
# removes the need for a pending-side sweep in the reconciler.
if redis-cli ping > /dev/null 2>&1; then
    echo "Redis already running"
    AOF=$(redis-cli CONFIG GET appendonly 2>/dev/null | tail -1)
    if [ "$AOF" != "yes" ]; then
        echo "WARNING: Redis is running but AOF is not enabled (appendonly=$AOF)."
        echo "         Stop Redis with 'make wsl-stop' and rerun 'make wsl-start'."
    fi
else
    redis-server \
        --daemonize yes \
        --logfile /tmp/rwb-redis.log \
        --bind 127.0.0.1 \
        --appendonly yes \
        --appendfsync everysec \
        --dir /tmp
    echo "Redis started with AOF (log: /tmp/rwb-redis.log)"
fi

echo ""
echo "Infrastructure is running. Open 3 more terminals:"
echo "  make wsl-app      ← web app on :8000 (uvicorn --reload)"
echo "  make wsl-worker   ← Dramatiq background workers"
echo "  make wsl-poller   ← IRP job poller"
