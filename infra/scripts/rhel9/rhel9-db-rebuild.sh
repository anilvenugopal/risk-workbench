#!/usr/bin/env bash
# Rebuild the Workbench schema on RHEL9 from the checked-out Alembic revision.
#
# DESTRUCTIVE: every table and row in the configured Workbench database is
# deleted. Stop the application before running the script. The rebuild creates
# no user accounts.
#
# Usage: APP_DIR=/rms bash infra/scripts/rhel9/rhel9-db-rebuild.sh

set -euo pipefail

APP_DIR="${APP_DIR:?set APP_DIR, e.g. /rms}"

cd "$APP_DIR"

if [ ! -f infra/.env ]; then
    echo "ERROR: $APP_DIR/infra/.env not found." >&2
    exit 1
fi

set -a
source infra/.env
set +a

DATABASE="${MSSQL_WORKBENCH_DATABASE:?MSSQL_WORKBENCH_DATABASE is not set in infra/.env}"

echo "About to rebuild the Workbench schema."
echo "  server:   ${MSSQL_WORKBENCH_SERVER:-unset}"
echo "  database: $DATABASE"
echo "  login:    ${MSSQL_WORKBENCH_USER:-unset}"
echo ""
echo "Every table in $DATABASE will be dropped and recreated."
echo "Stop the application before continuing."
echo ""

.venv/bin/python infra/scripts/rhel9/drop_workbench_tables.py
.venv/bin/python -m alembic upgrade head

echo ""
echo "=== Done. $DATABASE rebuilt at revision head. ==="
echo "Provision each account with:"
echo "    .venv/bin/python infra/scripts/user_setup.py"
