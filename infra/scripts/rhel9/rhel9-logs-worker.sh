#!/usr/bin/env bash
# rhel9-logs-worker.sh — tail one queue's Dramatiq worker log on RHEL9 (CR-004).
#
# Run directly on RHEL9 — not over SSH, not from Ubuntu (same rule as every
# other rhel9-*.sh script). No Makefile wrapper exists on RHEL9, so this is
# invoked directly, same as rhel9-start.sh/rhel9-stop.sh.
#
# Usage:
#   bash infra/scripts/rhel9/rhel9-logs-worker.sh upload_edm
#   bash infra/scripts/rhel9/rhel9-logs-worker.sh          # lists queue names and exits

set -uo pipefail

APP_DIR="${APP_DIR:?set APP_DIR, e.g. /rms}"
QUEUE="${1:-}"

cd "$APP_DIR"

if [ -z "$QUEUE" ]; then
    # `.venv/bin/python -m app.workers.queues` imports app.config, which
    # needs every setting env var (e.g. session_secret_key) set — confirmed
    # directly against a real RHEL9 host: without this, that import fails
    # with a pydantic ValidationError before it ever lists a queue name.
    # Only needed on this branch — the tail -f path below never imports
    # app code at all.
    set -a
    source infra/.env
    set +a

    echo "Usage: bash infra/scripts/rhel9/rhel9-logs-worker.sh <queue>" >&2
    echo "" >&2
    echo "Available queues:" >&2
    .venv/bin/python -m app.workers.queues >&2
    exit 1
fi

logfile="/var/lib/risk-workbench/worker-$QUEUE.log"
if [ ! -f "$logfile" ]; then
    echo "ERROR: $logfile does not exist — is the '$QUEUE' worker running (rhel9-start.sh)?" >&2
    exit 1
fi

exec tail -f "$logfile"
