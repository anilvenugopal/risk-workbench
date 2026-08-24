#!/usr/bin/env bash
# rhel9-start-podman-mssql.sh — start the local SQL Server container
# created by rhel9-setup-podman-mssql.sh.
#
# Exits quietly (not an error) if Podman isn't installed at all — this
# script is only relevant on a machine that opted into the Podman/local
# SQL Server option; its absence is a normal, expected state elsewhere.
#
# Usage: APP_DIR=/opt/risk-workbench bash rhel9-start-podman-mssql.sh

set -uo pipefail

APP_DIR="${APP_DIR:?set APP_DIR, e.g. /opt/risk-workbench}"

if ! command -v podman > /dev/null 2>&1; then
    echo "podman not installed — nothing to start. (Run"
    echo "rhel9-setup-podman-mssql.sh first if you want a local SQL Server"
    echo "container on this machine.)"
    exit 0
fi

if ! podman ps -a --format '{{.Names}}' 2>/dev/null | grep -qx sqlserver; then
    echo "No 'sqlserver' container exists yet — run"
    echo "rhel9-setup-podman-mssql.sh first to create it."
    exit 1
fi

if podman ps --format '{{.Names}}' 2>/dev/null | grep -qx sqlserver; then
    echo "Already running."
    exit 0
fi

echo "Starting 'sqlserver'..."
podman start sqlserver > /dev/null

# Needed for the readiness check below — the password to authenticate the
# test query with. Not read at the top of the script because the
# "podman not installed" / "container doesn't exist" exits above shouldn't
# depend on infra/.env existing at all.
ENV_FILE="$APP_DIR/infra/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

echo "Waiting for SQL Server to accept connections (up to 90s)..."
for i in $(seq 1 30); do
    if podman exec sqlserver /opt/mssql-tools18/bin/sqlcmd \
        -C -S localhost -U sa -P "${MSSQL_SA_PASSWORD:-}" -Q "SELECT 1" \
        > /dev/null 2>&1; then
        echo "Ready."
        exit 0
    fi
    sleep 3
done

echo "WARNING: did not confirm ready after 90s. Check logs with:" >&2
echo "  podman logs sqlserver" >&2
exit 1
