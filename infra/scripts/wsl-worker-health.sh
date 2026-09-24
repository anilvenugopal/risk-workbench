#!/usr/bin/env bash
# wsl-worker-health.sh — report the live state of per-queue Dramatiq workers
# (CR-004), for repeatable before/after inspection around start-all.sh/
# stop-all.sh and native WSL2's `make wsl-worker`.
#
# Two independent things are reported, since either can exist without the
# other depending on which dev path started the workers:
#
#   PID-FILE MODE (Docker start-all.sh / RHEL9 nohup scripts): reads
#   worker-<queue>.pid files from PID_DIR and checks each PID is alive.
#
#   PROCESS-SCAN MODE (native WSL2 `make wsl-worker QUEUE=...`, one foreground
#   terminal per queue, no PID file at all): finds running
#   `dramatiq app.workers.entrypoint -Q <queue>` processes directly via ps.
#
# Usage:
#   bash infra/scripts/wsl-worker-health.sh                  # both modes, default PID_DIR
#   PID_DIR=/path/to/pids bash infra/scripts/wsl-worker-health.sh
#   bash infra/scripts/wsl-worker-health.sh --queue upload_edm   # filter to one queue

set -uo pipefail
# No "-e": report on every queue even if one lookup fails.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

PID_DIR="${PID_DIR:-.dev-pids}"
FILTER_QUEUE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --queue) FILTER_QUEUE="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

QUEUES="$(bash -c 'source infra/scripts/wsl-env.sh 2>/dev/null; uv run python -m app.workers.queues' 2>/dev/null)"
if [ -z "$QUEUES" ]; then
    echo "ERROR: could not list queue names (python -m app.workers.queues returned nothing)." >&2
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

    # Process-scan: matches `dramatiq app.workers.entrypoint -Q <queue>` (the
    # exact flag this feature adds), not just any dramatiq process, so a
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
