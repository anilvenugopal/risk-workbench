#!/usr/bin/env bash
# start-all.sh — start every process on the Linux box.
#
# This script runs inside the linux-box container in dev. In production, each
# block below maps 1:1 to a systemd unit file. The commands are identical.
#
# Process layout (mirrors production):
#   redis-server   → background daemon
#   nginx          → background daemon
#   dramatiq       → background process (workers)
#   app.poller     → background process
#   uvicorn        → FOREGROUND (keeps the container alive; logs stream to stdout)
#
# Environment:
#   APP_DEBUG=1    → start uvicorn under debugpy on port 5678 instead of direct
#   APP_WORKERS    → number of dramatiq worker threads (default: 2)

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

# ── 3. Dramatiq workers ───────────────────────────────────────────────────────
echo "[start] Dramatiq workers..."
WORKERS=${APP_WORKERS:-2}
dramatiq app.workers \
    --processes 1 \
    --threads "$WORKERS" \
    >> "$LOG_DIR/worker.log" 2>&1 &
echo $! > "$PID_DIR/worker.pid"
echo "       worker PID=$(cat "$PID_DIR/worker.pid") threads=$WORKERS"

# ── 4. Poller ─────────────────────────────────────────────────────────────────
echo "[start] Poller..."
python -m app.poller.run --loop --interval 30 \
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
