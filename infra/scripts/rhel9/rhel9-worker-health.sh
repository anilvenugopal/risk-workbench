#!/usr/bin/env bash
# rhel9-worker-health.sh — report the live state of per-queue Dramatiq workers
# on RHEL9 (CR-004), for repeatable before/after inspection around
# rhel9-start.sh/rhel9-stop.sh.
#
# Run directly on RHEL9 — not over SSH, not from Ubuntu (same rule as
# rhel9-start.sh/rhel9-stop.sh). Uses .venv/bin/python directly, not `uv run`
# — RHEL9 has no uv, same reason rhel9-start.sh calls venv binaries directly.
#
# Reports two independent things per queue, since one can be true without the
# other after an unclean stop:
#   PID-FILE: reads worker-<queue>.pid from PID_DIR and checks the PID is alive.
#   PROCESS-SCAN: finds a running `dramatiq app.workers.entrypoint -Q <queue>`
#   process directly via ps, independent of whether its PID file exists.
#
# Usage:
#   APP_DIR=/rms bash infra/scripts/rhel9/rhel9-worker-health.sh
#   PID_DIR=/var/lib/risk-workbench/pids bash ... (default shown)
#   bash ... --queue upload_edm   # filter to one queue

set -uo pipefail
# No "-e" — report on every queue even if one lookup fails, same posture as
# rhel9-stop.sh.

APP_DIR="${APP_DIR:?set APP_DIR, e.g. /rms}"
PID_DIR="${PID_DIR:-/var/lib/risk-workbench/pids}"
FILTER_QUEUE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --queue) FILTER_QUEUE="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

cd "$APP_DIR"

# `.venv/bin/python -m app.workers.queues` imports app.config, which needs
# every setting env var (e.g. session_secret_key) set — confirmed directly
# against a real RHEL9 host: without this, that import fails with a
# pydantic ValidationError before it ever lists a queue name.
set -a
source infra/.env
set +a

QUEUES="$(.venv/bin/python -m app.workers.queues 2>/dev/null)"
if [ -z "$QUEUES" ]; then
    echo "ERROR: could not list queue names (.venv/bin/python -m app.workers.queues returned nothing)." >&2
    exit 1
fi

printf "%-25s %-10s %-10s %-10s\n" "QUEUE" "PIDFILE" "PID" "PROCESS"
printf "%-25s %-10s %-10s %-10s\n" "-----" "-------" "---" "-------"

any_alive=0
while read -r queue; do
    [ -n "$FILTER_QUEUE" ] && [ "$queue" != "$FILTER_QUEUE" ] && continue

    pidfile_status="absent"
    pid="-"
    pidfile="$PID_DIR/worker-$queue.pid"
    if [ -f "$pidfile" ]; then
        pid="$(cat "$pidfile")"
        if kill -0 "$pid" 2>/dev/null; then
            pidfile_status="alive"
        else
            pidfile_status="stale"
        fi
    fi

    # Matches `dramatiq app.workers.entrypoint -Q <queue>` exactly, so a
    # worker for a DIFFERENT queue never shows as a false positive here.
    scan_pid="$(pgrep -f "dramatiq app\.workers\.entrypoint.*-Q[= ]$queue\b" | head -1)"
    scan_status="absent"
    if [ -n "$scan_pid" ]; then
        scan_status="alive (PID $scan_pid)"
        any_alive=1
    fi

    [ "$pidfile_status" = "alive" ] && any_alive=1

    printf "%-25s %-10s %-10s %-20s\n" "$queue" "$pidfile_status" "$pid" "$scan_status"
done <<< "$QUEUES"

echo ""
if [ "$any_alive" -eq 1 ]; then
    echo "At least one queue has a live worker (PID file or process scan)."
    exit 0
else
    echo "No live workers found for any queue."
    exit 1
fi
