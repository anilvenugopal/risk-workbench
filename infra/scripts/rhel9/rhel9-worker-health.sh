#!/usr/bin/env bash
# Verify exactly one live Dramatiq supervisor for every expected queue.

set -uo pipefail

APP_DIR="${APP_DIR:?set APP_DIR, e.g. /rms}"
PID_DIR="${PID_DIR:-/var/lib/risk-workbench/pids}"
FILTER_QUEUE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --queue) FILTER_QUEUE="${2:?--queue requires a value}"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

cd "$APP_DIR"
set -a
source infra/.env
set +a

QUEUES="$(.venv/bin/python -m app.workers.queues 2>/dev/null)"
if [ -z "$QUEUES" ]; then
    echo "ERROR: could not list queue names." >&2
    exit 1
fi

printf "%-25s %-10s %-10s %-12s %-8s\n" "QUEUE" "PIDFILE" "PID" "SUPERVISORS" "WORKERS"
printf "%-25s %-10s %-10s %-12s %-8s\n" "-----" "-------" "---" "-----------" "-------"

all_healthy=1
checked=0
while read -r queue; do
    [ -n "$FILTER_QUEUE" ] && [ "$queue" != "$FILTER_QUEUE" ] && continue
    checked=$((checked + 1))

    pidfile="$PID_DIR/worker-$queue.pid"
    pid="-"
    pidfile_status="absent"
    if [ -f "$pidfile" ]; then
        pid="$(cat "$pidfile")"
        if kill -0 "$pid" 2>/dev/null; then
            pidfile_status="alive"
        else
            pidfile_status="stale"
        fi
    fi

    mapfile -t scan_pids < <(
        pgrep -f "[d]ramatiq app\.workers\.entrypoint.*-Q[= ]$queue([[:space:]]|$)" || true
    )
    supervisor_pids=()
    for scan_pid in "${scan_pids[@]}"; do
        parent_pid="$(ps -o ppid= -p "$scan_pid" 2>/dev/null | tr -d ' ')"
        child=0
        for candidate in "${scan_pids[@]}"; do
            [ "$parent_pid" = "$candidate" ] && child=1
        done
        [ "$child" -eq 1 ] || supervisor_pids+=("$scan_pid")
    done
    count="${#supervisor_pids[@]}"
    worker_count=$((${#scan_pids[@]} - count))
    healthy=0
    if [ "$pidfile_status" = "alive" ] \
        && [ "$count" -eq 1 ] \
        && [ "$worker_count" -eq 1 ] \
        && [ "${supervisor_pids[0]}" = "$pid" ]; then
        healthy=1
    fi
    [ "$healthy" -eq 1 ] || all_healthy=0

    printf "%-25s %-10s %-10s %-12s %-8s\n" \
        "$queue" "$pidfile_status" "$pid" "$count" "$worker_count"
done <<< "$QUEUES"

if [ "$checked" -eq 0 ]; then
    echo "ERROR: queue '$FILTER_QUEUE' is not configured." >&2
    exit 1
fi
if [ "$all_healthy" -eq 1 ]; then
    echo "Every checked queue has exactly one supervisor and one worker process."
    exit 0
fi
echo "One or more queues are missing a supervisor/worker or have duplicates." >&2
exit 1
