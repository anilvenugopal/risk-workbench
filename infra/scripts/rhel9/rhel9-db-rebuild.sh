#!/usr/bin/env bash
# rhel9-db-rebuild.sh — rebuild the Workbench schema on the production server:
# drop every table it finds, then alembic upgrade head.
#
# DESTRUCTIVE: every table in the Workbench database is dropped and recreated.
# All Workbench data is lost — submissions, jobs, audit rows, user accounts.
# It does not touch the loss repository or DATABRIDGE.
#
# Why this exists: the project keeps one Alembic revision, edited in place. On a
# database that already has that revision stamped, "alembic upgrade head" does
# nothing, even though the file gained tables or seed rows since — so a deploy
# that changed the schema needs this script, not rhel9-app-install.sh.
#
# THIS SCRIPT EXPIRES. Spec 017 holds the last in-place edit of 0001_initial.py.
# Run this once after 017 ships, which leaves the server at exactly what 0001
# builds, and that is the baseline for real migrations: from then on every change
# is a new revision file, "alembic upgrade head" deploys it without dropping
# anything, and this script and drop_workbench_tables.py should be deleted.
#
# Runs as the app login named in infra/.env. The drop and the upgrade stay inside
# the Workbench database and need no server-level rights, so no SA login and no
# CREATE DATABASE — the database itself is the DBA's, created once.
#
# Run directly on RHEL9, with the app stopped (rhel9-stop.sh) so no worker
# writes into a half-dropped schema.
#
# It drops the tables by asking the database what it has, NOT with "alembic
# downgrade base". downgrade() is a hand-written list of DROPs describing the
# schema the CURRENT migration file builds; this database was built from an
# older edit of that file. The two disagree as soon as the file gains a table,
# and alembic stops on the first object the database never had. Dropping what
# is actually present does not care when the database was built.
#
# A rebuilt database has no accounts, and this script creates none. Every account
# is provisioned with infra/scripts/user_setup.py.
#
# Usage: APP_DIR=/rms bash infra/scripts/rhel9/rhel9-db-rebuild.sh

set -euo pipefail

APP_DIR="${APP_DIR:?set APP_DIR, e.g. /rms}"

cd "$APP_DIR"

if [ ! -f infra/.env ]; then
    echo "ERROR: $APP_DIR/infra/.env not found — needed for the connection settings." >&2
    exit 1
fi
set -a
source infra/.env
set +a

DATABASE="${MSSQL_WORKBENCH_DATABASE:?MSSQL_WORKBENCH_DATABASE is not set in infra/.env}"
SERVER="${MSSQL_WORKBENCH_SERVER:-unset}"

echo "About to rebuild the Workbench schema."
echo "  server:   $SERVER"
echo "  database: $DATABASE"
echo "  login:    ${MSSQL_WORKBENCH_USER:-unset}"
echo ""
echo "  Every table in $DATABASE is dropped and recreated. All Workbench data"
echo "  is lost. Stop the app first (rhel9-stop.sh) if it is still running."
echo ""

# Lists the tables it found, then asks for the database name. Confirming here
# rather than above means the prompt comes after seeing what is actually there.
.venv/bin/python infra/scripts/rhel9/drop_workbench_tables.py
.venv/bin/python -m alembic upgrade head

echo ""
echo "=== Done. $DATABASE rebuilt at revision head. ==="
echo "The migration seeds every kind table. It creates no user accounts, so"
echo "nobody can sign in until one is provisioned:"
echo "    .venv/bin/python infra/scripts/user_setup.py"
