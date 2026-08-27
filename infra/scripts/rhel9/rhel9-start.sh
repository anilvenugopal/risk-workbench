#!/usr/bin/env bash
# rhel9-start.sh — start the app's own processes on RHEL9 (Valkey, uvicorn,
# worker, poller, nginx), checking each port is actually free BEFORE
# trying to bind it — so a conflict fails with a clear, specific message
# instead of a cryptic bind error partway through startup.
#
# Run directly on RHEL9 — not over SSH, not from Ubuntu.
#
# Assumes rhel9-app-install.sh has already been run (dependencies
# installed, .venv exists) and infra/.env is in place.

set -euo pipefail

APP_DIR="${APP_DIR:?set APP_DIR, e.g. /opt/risk-workbench}"
PID_DIR="${PID_DIR:-/var/lib/risk-workbench/pids}"
mkdir -p "$PID_DIR"

cd "$APP_DIR"

# Checks one port; refuses to continue if something's already there,
# pointing at check-port.sh for full diagnosis rather than repeating its
# logic here.
require_port_free() {
    local port="$1"
    local what="$2"
    # "-H" suppresses ss's header row. "awk '{print $4}'" extracts just the
    # local-address column (e.g. "0.0.0.0:8000" or "*:1433") regardless of
    # how many spaces separate columns. Matching ":$port$" (end of field)
    # instead of assuming a trailing space after the port avoids depending
    # on ss's exact column spacing.
    if ss -tlnH 2>/dev/null | awk '{print $4}' | grep -q ":$port$"; then
        echo "ERROR: port $port is already in use — needed for $what." >&2
        echo "       Diagnose with: bash infra/scripts/check-port.sh $port" >&2
        exit 1
    fi
}

echo "=== 1. Checking ports are free ==="
require_port_free 6379 "Valkey"
require_port_free 8000 "uvicorn"
echo "  OK."

echo ""
echo "=== 2. Starting Valkey ==="
if valkey-cli ping > /dev/null 2>&1; then
    echo "  Already running."
else
    VALKEY_DIR=/var/lib/risk-workbench/valkey
    valkey-server \
        --daemonize yes \
        --port 6379 \
        --dir "$VALKEY_DIR" \
        --logfile "$VALKEY_DIR/valkey.log" \
        --bind 127.0.0.1 \
        --appendonly yes \
        --appendfsync everysec
    sleep 1
    if valkey-cli ping > /dev/null 2>&1; then
        echo "  Started."
    else
        echo "ERROR: Valkey did not start — check $VALKEY_DIR/valkey.log" >&2
        exit 1
    fi
fi

echo ""
echo "=== 3. Starting uvicorn (background) ==="
set -a
source infra/.env
set +a
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    > /var/lib/risk-workbench/uvicorn.log 2>&1 &
echo $! > "$PID_DIR/uvicorn.pid"
sleep 2
if kill -0 "$(cat "$PID_DIR/uvicorn.pid")" 2>/dev/null; then
    echo "  Started (PID $(cat "$PID_DIR/uvicorn.pid")). Log: /var/lib/risk-workbench/uvicorn.log"
else
    echo "ERROR: uvicorn exited immediately — check /var/lib/risk-workbench/uvicorn.log" >&2
    exit 1
fi

echo ""
echo "=== 4. Starting Dramatiq workers (one process per queue — CR-004) ==="
while read -r queue; do
    nohup .venv/bin/dramatiq app.workers.entrypoint -Q "$queue" \
        --processes "${RWB_WORKER_PROCESSES:-1}" --threads "${RWB_WORKER_THREADS:-2}" \
        > "/var/lib/risk-workbench/worker-$queue.log" 2>&1 &
    echo $! > "$PID_DIR/worker-$queue.pid"
    echo "  Started $queue (PID $(cat "$PID_DIR/worker-$queue.pid")). Log: /var/lib/risk-workbench/worker-$queue.log"
done < <(.venv/bin/python -m app.workers.queues)

echo ""
echo "=== 5. Starting poller (background) ==="
nohup .venv/bin/python -m app.poller.run --loop \
    > /var/lib/risk-workbench/poller.log 2>&1 &
echo $! > "$PID_DIR/poller.pid"
echo "  Started (PID $(cat "$PID_DIR/poller.pid")). Log: /var/lib/risk-workbench/poller.log"

echo ""
echo "=== 6. nginx ==="
if systemctl is-active --quiet nginx; then
    echo "  Already running (systemd) — deploy scripts reload it, they don't"
    echo "  need to start it here."
else
    echo "  Not running. Start with: sudo systemctl start nginx"
fi

echo ""
echo "=== Done. Verify with: curl -s http://127.0.0.1:8000/api/health ==="
