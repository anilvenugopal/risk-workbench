#!/usr/bin/env bash
# stop-all.sh — graceful shutdown of all background processes.
# Run this before `docker compose down` if you want clean log flush.

set -euo pipefail

WORKSPACE=/workspace
PID_DIR=$WORKSPACE/.dev-pids

stop_pid() {
    local name=$1
    local pidfile="$PID_DIR/$name.pid"
    if [ -f "$pidfile" ]; then
        local pid
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            echo "[stop] $name (PID $pid)..."
            kill -TERM "$pid" && rm -f "$pidfile"
        else
            echo "[stop] $name already stopped"
            rm -f "$pidfile"
        fi
    else
        echo "[stop] $name — no PID file"
    fi
}

stop_pid worker
stop_pid poller

echo "[stop] nginx..."
nginx -s quit 2>/dev/null || true

echo "[stop] redis..."
redis-cli shutdown nosave 2>/dev/null || true

echo "[stop] done"
