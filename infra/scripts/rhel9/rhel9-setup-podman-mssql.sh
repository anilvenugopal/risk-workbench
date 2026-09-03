#!/usr/bin/env bash
# rhel9-setup-podman-mssql.sh — one-time: install Podman and CREATE (not
# start) a local SQL Server container on RHEL9. Same image, environment
# variables, and port as Ubuntu's infra/docker-compose.yml SQL Server
# service; data directory differs deliberately (see below).
#
# Local dev/testing convenience ONLY. Production's SQL Server is a
# separate, already-existing instance outside this box — never something
# this project containerizes for real deployment.
#
# Standalone and optional: nothing else in this project depends on this
# script having been run. Use it only if you want RHEL9 to have its own
# SQL Server rather than reaching across to Ubuntu's.
#
# Creates the container but does NOT start it — start it explicitly with
# rhel9-start-podman-mssql.sh, every time, including the first time.
#
# Same port (1433) as Ubuntu's SQL Server — deliberate, so infra/.env never
# needs environment-specific values. Consequence: RHEL9's and Ubuntu's SQL
# Server containers cannot both be reachable at once, since this machine's
# WSL2 distros share one IP (see docs/RHEL9_SYSTEM_SETUP.md's Redis/Valkey
# section for the same conflict pattern). Stop one before starting the
# other — see infra/scripts/check-port.sh to diagnose which is holding it.
#
# Usage: APP_DIR=/rms DEPLOY_USER=cinreadm \
#        bash rhel9-setup-podman-mssql.sh

set -euo pipefail

APP_DIR="${APP_DIR:?set APP_DIR, e.g. /rms}"
DEPLOY_USER="${DEPLOY_USER:?set DEPLOY_USER, the account running Podman}"

echo "=== 1. Podman ==="
if ! rpm -q podman > /dev/null 2>&1; then
    sudo dnf install -y podman
    echo "  Installed podman."
else
    echo "  podman already installed."
fi

# Rootless Podman (running containers as a normal user, not root) needs a
# range of extra user/group IDs reserved for it — "subuid"/"subgid" — so
# containers can remap their own internal users without needing real root.
# useradd normally assigns these automatically for new accounts, but
# cinreadm may predate this or have been created differently.
if ! grep -q "^$DEPLOY_USER:" /etc/subuid 2>/dev/null; then
    sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$DEPLOY_USER"
    echo "  Assigned subuid/subgid ranges to $DEPLOY_USER."
    # Applies the subuid/subgid assignment to Podman's internal state —
    # only needed right after the ranges actually change; safe to re-run
    # but only run here so its output doesn't imply reconfiguration
    # happened on a run where nothing changed.
    podman system migrate
else
    echo "  subuid/subgid already assigned."
fi
echo "  Podman rootless setup complete."
echo "  (No port-related sysctl needed — SQL Server's port, 1433, is above"
echo "  1024, unlike nginx's port 80, so the unprivileged-port restriction"
echo "  that needed a separate fix for nginx doesn't apply here.)"

echo ""
echo "=== 2. Data directory ==="
# /var/lib is the standard Linux location for a service's own persistent
# data — not a personal user's home directory. Same reasoning and same
# pattern as Valkey's data directory above: not tied to cinreadm
# specifically, survives cinreadm being replaced by a real service account
# later. A Podman named volume would default to
# ~/.local/share/containers/storage/volumes/ under cinreadm's home instead
# — rejected for the same reason the Valkey home-directory default was
# rejected earlier in this project.
MSSQL_DATA_DIR=/var/lib/risk-workbench/mssql
if [ ! -d "$MSSQL_DATA_DIR" ]; then
    sudo mkdir -p "$MSSQL_DATA_DIR"
    sudo chown -R "$DEPLOY_USER:$DEPLOY_USER" "$(dirname "$MSSQL_DATA_DIR")"
    echo "  Created $MSSQL_DATA_DIR, owned by $DEPLOY_USER."
else
    echo "  $MSSQL_DATA_DIR already exists."
fi

# SQL Server's container runs internally as UID 10001, GID 0 (fixed by the
# image, same on every machine). Rootless Podman maps that internal UID to
# a host UID from cinreadm's own subuid range, not to cinreadm's real UID
# — a plain cinreadm-owned directory is invisible to it for writing.
# "podman unshare" runs the following command using that same mapping, so
# "chown 10001:0" here resolves to the correct real host UID for whatever
# cinreadm's actual subuid range is on this machine — it adapts
# automatically, the 10001:0 numbers themselves never need to change.
podman unshare chown -R 10001:0 "$MSSQL_DATA_DIR"
echo "  Set ownership of $MSSQL_DATA_DIR for the container's internal user."

echo ""
echo "=== 3. SQL Server container (created, not started) ==="
ENV_FILE="$APP_DIR/infra/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi
if [ -z "${MSSQL_SA_PASSWORD:-}" ]; then
    echo "ERROR: MSSQL_SA_PASSWORD not set (checked $ENV_FILE)." >&2
    echo "       Set infra/.env first, or export MSSQL_SA_PASSWORD directly." >&2
    exit 1
fi

if podman ps -a --format '{{.Names}}' 2>/dev/null | grep -qx sqlserver; then
    echo "  Container 'sqlserver' already exists — not recreating."
else
    # "podman create" (not "podman run") builds the container without
    # starting it. Same image, environment variables, and port as Ubuntu's
    # infra/docker-compose.yml. The volume mount differs deliberately: a
    # bind mount to a chosen real path ($MSSQL_DATA_DIR) instead of a
    # Podman-managed named volume, per the reasoning above.
    podman create \
        --name sqlserver \
        -e ACCEPT_EULA=Y \
        -e MSSQL_PID=Developer \
        -e MSSQL_SA_PASSWORD="$MSSQL_SA_PASSWORD" \
        -p 1433:1433 \
        -v "$MSSQL_DATA_DIR:/var/opt/mssql" \
        mcr.microsoft.com/mssql/server:2022-latest
    echo "  Created container 'sqlserver' (not started)."
fi

echo ""
echo "Start it with: bash rhel9-start-podman-mssql.sh"
