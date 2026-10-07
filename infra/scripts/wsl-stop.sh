#!/usr/bin/env bash
# wsl-stop.sh — stop the SQL Server container and Redis started by wsl-start.sh.
#
# Usage: make wsl-stop

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

source infra/scripts/wsl-env.sh

if [ "$RWB_CONTAINER_RUNTIME" = docker ]; then
    docker compose -f infra/docker-compose.yml --env-file infra/.env stop sqlserver
else
    podman stop sqlserver > /dev/null
fi
redis-cli shutdown nosave 2>/dev/null || valkey-cli shutdown nosave 2>/dev/null || true
echo "Stopped."
