#!/usr/bin/env bash
# wsl-workers.sh — start or stop every per-queue Dramatiq worker (CR-004) at
# once, in native WSL2. Same per-queue loop, log names and PID-file layout as
# start-all.sh/stop-all.sh use inside linux-box.
#
# `make wsl-worker QUEUE=x` still runs one queue in the foreground, which is the
# right thing when you are working on that one job type. This script is for the
# other case: all 13 queues at once, backgrounded, read live with
# `make wsl-worker-logs`.
#
# Only workers started here have a PID file, so only those can be stopped here.
# `make wsl-worker-health` finds any others by process scan.
#
# Usage:
#   bash infra/scripts/wsl-workers.sh          # start (skips queues already up)
#   bash infra/scripts/wsl-workers.sh stop

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR=.dev-logs
PID_DIR=.dev-pids
ACTION="${1:-start}"

# shellcheck source=/dev/null
source infra/scripts/wsl-env.sh

QUEUES="$(uv run python -m app.workers.queues)"

running_pid() {
    local pid
    pid="$(cat "$PID_DIR/worker-$1.pid" 2>/dev/null)" || return 0
    kill -0 "$pid" 2>/dev/null && echo "$pid"
    return 0
}

case "$ACTION" in
start)
    mkdir -p "$LOG_DIR" "$PID_DIR"
    while read -r queue; do
        pid="$(running_pid "$queue")"
        if [ -n "$pid" ]; then
            echo "  worker[$queue] already running (PID $pid)"
            continue
        fi
        # setsid: the worker gets its own session, so Ctrl-C in the terminal
        # that started it — or that later runs `make wsl-worker-logs` on the
        # same command line — does not reach it.
        setsid --fork uv run dramatiq app.workers.entrypoint -Q "$queue" \
            --processes "${RWB_WORKER_PROCESSES:-1}" \
            --threads "${RWB_WORKER_THREADS:-2}" \
            --pid-file "$PID_DIR/worker-$queue.pid" \
            >> "$LOG_DIR/worker-$queue.log" 2>&1
        echo "  worker[$queue] started"
    done <<< "$QUEUES"
    echo ""
    echo "Logs: make wsl-worker-logs      Stop: make wsl-workers-stop"
    ;;
stop)
    stopping=()
    while read -r queue; do
        pid="$(running_pid "$queue")"
        if [ -n "$pid" ]; then
            echo "  stopping worker[$queue] (PID $pid)"
            kill -TERM "$pid"
            stopping+=("$queue:$pid")
        fi
    done <<< "$QUEUES"

    # Wait for the PIDs to actually go, as rhel9-stop.sh's stop_and_verify does
    # and for the same reason: dramatiq's --worker-shutdown-timeout defaults to
    # 30s, so a worker finishing an in-flight message is slow, not stuck.
    # SIGKILL stays the developer's call, again matching that script.
    for _ in $(seq 35); do
        alive=()
        for entry in ${stopping[@]+"${stopping[@]}"}; do
            kill -0 "${entry#*:}" 2>/dev/null && alive+=("$entry")
        done
        [ ${#alive[@]} -eq 0 ] && break
        sleep 1
    done

    for entry in ${alive[@]+"${alive[@]}"}; do
        echo "  WARNING: worker[${entry%:*}] still running 35s after SIGTERM —"
        echo "           needs more time, or 'kill -9 ${entry#*:}' if truly stuck."
    done
    [ ${#alive[@]} -eq 0 ] || exit 1
    echo "Stopped."
    ;;
*)
    echo "Usage: wsl-workers.sh [start|stop]" >&2
    exit 1
    ;;
esac
