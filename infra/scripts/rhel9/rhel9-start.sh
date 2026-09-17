#!/usr/bin/env bash
# Start Valkey, uvicorn, one Dramatiq supervisor per queue, and the poller.

set -euo pipefail

APP_DIR="${APP_DIR:?set APP_DIR, e.g. /rms}"
PID_DIR="${PID_DIR:-/var/lib/risk-workbench/pids}"
STATE_DIR="${STATE_DIR:-/var/lib/risk-workbench}"
mkdir -p "$PID_DIR"
cd "$APP_DIR"

started_pids=()
started_pidfiles=()
started_valkey=0
startup_complete=0

cleanup_failed_start() {
    [ "$startup_complete" -eq 1 ] && return
    echo "Startup failed; stopping processes started by this attempt." >&2
    for pid in "${started_pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    for pidfile in "${started_pidfiles[@]}"; do
        rm -f "$pidfile"
    done
    if [ "$started_valkey" -eq 1 ]; then
        valkey-cli shutdown 2>/dev/null || true
    fi
}
trap cleanup_failed_start EXIT

require_port_free() {
    local port="$1"
    local name="$2"
    if ss -tlnH 2>/dev/null | awk '{print $4}' | grep -q ":$port$"; then
        echo "ERROR: port $port is already in use by something other than $name." >&2
        exit 1
    fi
}

prepare_pidfile() {
    local pidfile="$1"
    local name="$2"
    if [ ! -f "$pidfile" ]; then
        return
    fi
    local pid
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
        echo "ERROR: $name is already running (PID $pid)." >&2
        exit 1
    fi
    echo "  Removing stale $pidfile"
    rm -f "$pidfile"
}

reject_process_match() {
    local pattern="$1"
    local name="$2"
    if pgrep -f -- "$pattern" >/dev/null 2>&1; then
        echo "ERROR: $name is already running without a usable PID file." >&2
        exit 1
    fi
}

start_background() {
    local pidfile="$1"
    local logfile="$2"
    shift 2
    nohup "$@" > "$logfile" 2>&1 &
    local pid=$!
    echo "$pid" > "$pidfile"
    started_pids+=("$pid")
    started_pidfiles+=("$pidfile")
}

wait_for_pid() {
    local pidfile="$1"
    local name="$2"
    local attempts="${3:-10}"
    local pid
    pid="$(cat "$pidfile")"
    for ((i=1; i<=attempts; i++)); do
        if kill -0 "$pid" 2>/dev/null; then
            sleep 1
        else
            echo "ERROR: $name exited during startup." >&2
            return 1
        fi
    done
}

for required in .venv/bin/python .venv/bin/uvicorn .venv/bin/dramatiq infra/.env; do
    if [ ! -e "$required" ]; then
        echo "ERROR: $required not found; run rhel9-app-install.sh first." >&2
        exit 1
    fi
done

set -a
source infra/.env
set +a

QUEUES="$(.venv/bin/python -m app.workers.queues)"
if [ -z "$QUEUES" ]; then
    echo "ERROR: no Dramatiq queues were returned." >&2
    exit 1
fi

echo "=== 1. Preflight ==="
prepare_pidfile "$PID_DIR/uvicorn.pid" "uvicorn"
prepare_pidfile "$PID_DIR/poller.pid" "poller"
reject_process_match "[u]vicorn app.main:app" "uvicorn"
reject_process_match "[p]ython -m app.poller.run --loop" "poller"
while read -r queue; do
    prepare_pidfile "$PID_DIR/worker-$queue.pid" "worker $queue"
    reject_process_match "[d]ramatiq app.workers.entrypoint.*-Q[= ]$queue([[:space:]]|$)" "worker $queue"
done <<< "$QUEUES"
require_port_free 8000 "uvicorn"

echo ""
echo "=== 2. Valkey ==="
if valkey-cli ping >/dev/null 2>&1; then
    echo "  Already running."
else
    require_port_free 6379 "Valkey"
    valkey-server \
        --daemonize yes \
        --port 6379 \
        --dir "$STATE_DIR/valkey" \
        --logfile "$STATE_DIR/valkey/valkey.log" \
        --bind 127.0.0.1 \
        --appendonly yes \
        --appendfsync everysec
    started_valkey=1
    for _ in {1..10}; do
        valkey-cli ping >/dev/null 2>&1 && break
        sleep 1
    done
    if ! valkey-cli ping >/dev/null 2>&1; then
        echo "ERROR: Valkey did not become ready." >&2
        exit 1
    fi
    echo "  Started."
fi

echo ""
echo "=== 3. uvicorn ==="
start_background "$PID_DIR/uvicorn.pid" "$STATE_DIR/uvicorn.log" \
    .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
wait_for_pid "$PID_DIR/uvicorn.pid" "uvicorn" 2
for _ in {1..30}; do
    if curl -sf http://127.0.0.1:8000/api/health >/dev/null; then
        break
    fi
    sleep 1
done
if ! curl -sf http://127.0.0.1:8000/api/health >/dev/null; then
    echo "ERROR: uvicorn did not pass the readiness check; see $STATE_DIR/uvicorn.log" >&2
    exit 1
fi
echo "  Ready (PID $(cat "$PID_DIR/uvicorn.pid"))."

echo ""
echo "=== 4. Dramatiq workers ==="
while read -r queue; do
    start_background "$PID_DIR/worker-$queue.pid" "$STATE_DIR/worker-$queue.log" \
        .venv/bin/dramatiq app.workers.entrypoint -Q "$queue" --processes 1 \
        --threads "${RWB_WORKER_THREADS:-2}"
done <<< "$QUEUES"
while read -r queue; do
    wait_for_pid "$PID_DIR/worker-$queue.pid" "worker $queue" 2
    echo "  $queue ready (PID $(cat "$PID_DIR/worker-$queue.pid"))."
done <<< "$QUEUES"
APP_DIR="$APP_DIR" PID_DIR="$PID_DIR" \
    bash infra/scripts/rhel9/rhel9-worker-health.sh

echo ""
echo "=== 5. Poller ==="
start_background "$PID_DIR/poller.pid" "$STATE_DIR/poller.log" \
    .venv/bin/python -m app.poller.run --loop
wait_for_pid "$PID_DIR/poller.pid" "poller" 2
echo "  Ready (PID $(cat "$PID_DIR/poller.pid"))."

echo ""
echo "=== 6. nginx ==="
if systemctl is-active --quiet nginx; then
    echo "  Running."
else
    echo "  Not running. Start with: sudo systemctl start nginx"
fi

startup_complete=1
echo ""
echo "=== Started. Verify with: curl -sf http://127.0.0.1:8000/api/health ==="
