#!/usr/bin/env bash
# rhel9-drain-check.sh — poll rwb_job until no queue has pending/running rows,
# or a timeout is hit (CR-004). Run after stopping the per-queue worker
# processes (rhel9-stop.sh), before deploying new code.
#
# Run directly on RHEL9 — not over SSH, not from Ubuntu (same rule as
# rhel9-start.sh/rhel9-stop.sh). Uses .venv/bin/python directly, not `uv run`.
#
# Reads only rwb_job via the app's own db.execute — no dependency on
# Dramatiq/Redis, since rwb_job is the queue of record (app/workers/dispatch.py).
#
# Usage:
#   APP_DIR=/rms bash infra/scripts/rhel9/rhel9-drain-check.sh
#   DRAIN_TIMEOUT_SECS=600 DRAIN_POLL_INTERVAL_SECS=10 bash ...

set -uo pipefail
# No "-e" — this script's own control flow (poll, check, loop) handles its
# failure path explicitly via the exit codes below; -e would exit on the
# Python subprocess's transient connection error instead of letting the next
# poll retry it.

APP_DIR="${APP_DIR:?set APP_DIR, e.g. /rms}"
DRAIN_TIMEOUT_SECS="${DRAIN_TIMEOUT_SECS:-300}"
DRAIN_POLL_INTERVAL_SECS="${DRAIN_POLL_INTERVAL_SECS:-5}"

cd "$APP_DIR"

if [ -f infra/.env ]; then
    set -a
    source infra/.env
    set +a
else
    echo "ERROR: $APP_DIR/infra/.env not found — needed for db.execute's connection settings." >&2
    exit 1
fi

_check_outstanding() {
    .venv/bin/python -c "
from db import execute
rows = execute(
    \"SELECT rwb_job_type, status_code, COUNT(*) AS n FROM rwb_job \"
    \"WHERE status_code IN ('pending', 'running') \"
    \"GROUP BY rwb_job_type, status_code\",
    {}, connection='WORKBENCH')
for r in rows:
    print(f\"{r['rwb_job_type']}\t{r['status_code']}\t{r['n']}\")
"
}

elapsed=0
while true; do
    if ! outstanding="$(_check_outstanding)"; then
        if [ "$elapsed" -ge "$DRAIN_TIMEOUT_SECS" ]; then
            echo "ERROR: drain query did not succeed within ${DRAIN_TIMEOUT_SECS}s." >&2
            exit 1
        fi
        echo "[drain-check] query failed; retrying in ${DRAIN_POLL_INTERVAL_SECS}s." >&2
        sleep "$DRAIN_POLL_INTERVAL_SECS"
        elapsed=$((elapsed + DRAIN_POLL_INTERVAL_SECS))
        continue
    fi
    if [ -z "$outstanding" ]; then
        echo "[drain-check] all queues empty."
        exit 0
    fi
    if [ "$elapsed" -ge "$DRAIN_TIMEOUT_SECS" ]; then
        echo "ERROR: drain timed out after ${DRAIN_TIMEOUT_SECS}s. Outstanding:" >&2
        echo "$outstanding" >&2
        exit 1
    fi
    sleep "$DRAIN_POLL_INTERVAL_SECS"
    elapsed=$((elapsed + DRAIN_POLL_INTERVAL_SECS))
done
