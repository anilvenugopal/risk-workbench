#!/usr/bin/env bash
# start-all.sh — start every process on the Linux box.
#
# This script runs inside the linux-box container in dev. In production, each
# block below maps 1:1 to a systemd unit file. The commands are identical.
#
# Process layout (mirrors production):
#   redis-server   → background daemon
#   nginx          → background daemon
#   dramatiq       → background process, one per queue (one per rwb_job_type — CR-004)
#   app.poller     → background process
#   uvicorn        → FOREGROUND (keeps the container alive; logs stream to stdout)
#
# Environment:
#   APP_DEBUG=1            → start uvicorn under debugpy on port 5678 instead of direct
#   RWB_WORKER_PROCESSES   → dramatiq worker OS processes to fork, per queue (default: 1)
#   RWB_WORKER_THREADS     → dramatiq worker threads per process, per queue (default: 2)

set -euo pipefail

WORKSPACE=/workspace
LOG_DIR=$WORKSPACE/.dev-logs
PID_DIR=$WORKSPACE/.dev-pids

mkdir -p "$LOG_DIR" "$PID_DIR"

# ── 1. Redis (AOF durability required) ───────────────────────────────────────
# appendonly yes + appendfsync everysec ensures acknowledged Dramatiq enqueues
# survive a broker crash (≤ ~1s worst-case loss). Required in all environments.
echo "[start] Redis (AOF enabled)..."
redis-server \
    --daemonize yes \
    --logfile "$LOG_DIR/redis.log" \
    --bind 127.0.0.1 \
    --protected-mode yes \
    --appendonly yes \
    --appendfsync everysec \
    --dir "$LOG_DIR"

# ── 2. nginx ──────────────────────────────────────────────────────────────────
echo "[start] nginx..."
# nginx.conf is volume-mounted so edits take effect on reload (make nginx-reload)
nginx -c "$WORKSPACE/deploy/nginx/nginx.conf" -g "daemon on;"

# ── 3. Dramatiq workers (one process per queue — CR-004) ─────────────────────
echo "[start] Dramatiq workers..."
PROCESSES=${RWB_WORKER_PROCESSES:-1}
THREADS=${RWB_WORKER_THREADS:-2}
if ! QUEUES="$(python -m app.workers.queues)"; then
    echo "ERROR: could not list queue names (python -m app.workers.queues failed)." >&2
    exit 1
fi
if [ -z "$QUEUES" ]; then
    echo "ERROR: could not list queue names (python -m app.workers.queues returned nothing)." >&2
    exit 1
fi
while read -r queue; do
    dramatiq app.workers.entrypoint -Q "$queue" \
        --processes "$PROCESSES" \
        --threads "$THREADS" \
        --pid-file "$PID_DIR/worker-$queue.pid" \
        >> "$LOG_DIR/worker-$queue.log" 2>&1 &
    echo "       worker[$queue] PID=$! processes=$PROCESSES threads=$THREADS"
done <<< "$QUEUES"

# ── 4. Poller ─────────────────────────────────────────────────────────────────
echo "[start] Poller..."
python -m app.poller.run --loop \
    >> "$LOG_DIR/poller.log" 2>&1 &
echo $! > "$PID_DIR/poller.pid"
echo "       poller PID=$(cat "$PID_DIR/poller.pid")"

# ── 5. uvicorn (foreground) ──────────────────────────────────────────────────
echo "[start] uvicorn..."
if [ "${APP_DEBUG:-0}" = "1" ]; then
    echo "       DEBUG MODE — debugpy listening on 0.0.0.0:5678"
    echo "       Attach VS Code debugger before requests will be served."
    exec python -m debugpy \
        --listen 0.0.0.0:5678 \
        --wait-for-client \
        -m uvicorn app.main:app \
        --host 0.0.0.0 \
        --port 8000
else
    exec uvicorn app.main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --reload \
        --reload-dir "$WORKSPACE/app"
fi
