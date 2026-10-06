#!/usr/bin/env bash
# wsl-start.sh — start infrastructure for a WSL2 dev session and wait until
# SQL Server accepts connections.
# Idempotent: SQL Server and Redis are no-ops if already running.
#
# Usage: make wsl-start
#
# RWB_CONTAINER_RUNTIME in infra/.env picks docker (the default) or podman for
# SQL Server. Redis is `redis-server` on Ubuntu and `valkey-server` on RHEL9.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

source infra/scripts/wsl-env.sh

# ── SQL Server ────────────────────────────────────────────────────────────────
if [ "$RWB_CONTAINER_RUNTIME" = docker ]; then
    COMPOSE="docker compose -f infra/docker-compose.yml --env-file infra/.env"
    $COMPOSE up -d sqlserver
    SQL_EXEC="$COMPOSE exec sqlserver"
else
    # Same image, environment and port as the sqlserver service in
    # infra/docker-compose.yml. An existing 'sqlserver' container, including
    # one made by rhel9-setup-podman-mssql.sh, is reused as is.
    if ! podman container exists sqlserver; then
        podman create \
            --name sqlserver \
            -e ACCEPT_EULA=Y \
            -e MSSQL_PID=Developer \
            -e MSSQL_SA_PASSWORD="$MSSQL_SA_PASSWORD" \
            -p 1433:1433 \
            -v rwb-mssql-data:/var/opt/mssql \
            mcr.microsoft.com/mssql/server:2022-latest > /dev/null
        echo "Created Podman container 'sqlserver'"
    fi
    podman start sqlserver > /dev/null
    SQL_EXEC="podman exec sqlserver"
fi

echo "Waiting for SQL Server to accept connections (up to 90s)..."
for i in $(seq 1 30); do
    if $SQL_EXEC /opt/mssql-tools18/bin/sqlcmd \
        -C -S localhost -U sa -P "$MSSQL_SA_PASSWORD" \
        -Q "SELECT 1" > /dev/null 2>&1; then
        echo "SQL Server ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "ERROR: SQL Server did not become ready after 90s." >&2
        echo "Check its log: docker compose -f infra/docker-compose.yml logs sqlserver" >&2
        echo "               or: podman logs sqlserver" >&2
        exit 1
    fi
    sleep 3
done

# ── Redis (AOF durability required) ──────────────────────────────────────────
# appendonly yes + appendfsync everysec: acknowledged enqueues survive a broker
# crash (≤ ~1s worst-case loss). This closes the pending-lost failure case and
# removes the need for a pending-side sweep in the reconciler.
if command -v redis-server > /dev/null 2>&1; then
    REDIS=redis
else
    REDIS=valkey
fi

if "$REDIS-cli" ping > /dev/null 2>&1; then
    echo "Redis already running"
    AOF=$("$REDIS-cli" CONFIG GET appendonly 2>/dev/null | tail -1)
    if [ "$AOF" != "yes" ]; then
        echo "WARNING: Redis is running but AOF is not enabled (appendonly=$AOF)."
        echo "         Stop Redis with 'make wsl-stop' and rerun 'make wsl-start'."
    fi
else
    "$REDIS-server" \
        --daemonize yes \
        --logfile "/tmp/rwb-$REDIS.log" \
        --bind 127.0.0.1 \
        --appendonly yes \
        --appendfsync everysec \
        --dir /tmp
    echo "$REDIS-server started with AOF (log: /tmp/rwb-$REDIS.log)"
fi

echo ""
echo "Infrastructure is running. Open 3 more terminals:"
echo "  make wsl-app           ← web app on :8000 (uvicorn --reload)"
echo "  make wsl-poller        ← IRP job poller"
echo "  make wsl-workers && make wsl-worker-logs"
echo "                         ← one worker per queue, backgrounded, live combined log"
