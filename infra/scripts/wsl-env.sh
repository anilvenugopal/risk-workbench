#!/usr/bin/env bash
# wsl-env.sh — sourced by other infra scripts to load infra/.env safely.
#
# Why a script instead of Makefile $(shell ...):
#   - Makefile variable expansion exposes values in process listings and output
#   - Values with spaces (MSSQL_DRIVER="ODBC Driver 18...") break xargs-style loading
#   - Sourcing in a child shell keeps secrets out of Make's variable space
#
# In WSL2 mode, SQL Server is reached via localhost (port mapped from Docker).
# The app container uses "sqlserver" as the hostname (Docker internal DNS).
# We override the three server vars here for WSL2.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found. Copy infra/.env.example to infra/.env and fill it in." >&2
    exit 1
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

# Override server addresses for WSL2 (SQL Server container port is mapped to localhost)
export MSSQL_WORKBENCH_SERVER=localhost
export MSSQL_EXPOSURE_SERVER=localhost
export MSSQL_LOSS_SERVER=localhost
