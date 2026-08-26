#!/usr/bin/env bash
# rhel9-stop.sh — stop the app's own processes on RHEL9 (Valkey, uvicorn,
# worker, poller, nginx), and VERIFY each one actually stopped rather than
# assuming a stop signal worked.
#
# Run directly on RHEL9 — not over SSH, not from Ubuntu.
#
# Why verification matters: confirmed directly tonight that a plain stop
# command can silently fail to take effect — Ubuntu's Redis is managed by
# systemd with a restart policy, so "redis-cli shutdown nosave" appeared to
# succeed (no error) but systemd brought it back up within seconds under a
# new PID. This script checks the actual port state after stopping each
# service, and separately checks for a systemd restart policy that could
# undo the stop, rather than trusting a stop command's exit code alone.

set -uo pipefail
# NOTE: no "-e" here deliberately — like rhel9-check-prereqs.sh, this
# script should attempt to stop every service and report on all of them,
# not halt after the first one that has a problem.

APP_DIR="${APP_DIR:?set APP_DIR, e.g. /opt/risk-workbench}"
PID_DIR="${PID_DIR:-/var/lib/risk-workbench/pids}"

# Needed so `.venv/bin/python -m app.workers.queues` (per-queue stop loop
# below) resolves both the venv binary and the `app` package by relative
# path — this script previously only read PID files by absolute path and
# never needed a working directory.
cd "$APP_DIR"

# `.venv/bin/python -m app.workers.queues` imports app.config, which needs
# every setting env var (e.g. session_secret_key) to be set — confirmed
# directly: without this, that import fails with a pydantic
# ValidationError before it ever gets to listing queue names. rhel9-start.sh
# already does this; this script previously never needed to import
# app code at all, so it never had this line.
set -a
source infra/.env
set +a

# Checks whether a systemd unit of this name exists AND has a restart
# policy that isn't "no" — if so, a plain kill/shutdown command would be
# silently undone, the same trap found in Ubuntu's Redis tonight.
warn_if_systemd_restarts() {
    local unit="$1"
    if systemctl list-unit-files --no-legend "${unit}.service" 2>/dev/null | grep -q "${unit}.service"; then
        local restart_policy
        restart_policy="$(systemctl show "$unit" 2>/dev/null | grep '^Restart=' | cut -d= -f2)"
        if [ -n "$restart_policy" ] && [ "$restart_policy" != "no" ]; then
            echo "  WARNING: $unit is a systemd service with Restart=$restart_policy —"
            echo "           it may restart itself. Use 'sudo systemctl stop $unit' instead"
            echo "           of a plain kill/shutdown command."
        fi
    fi
}

# Stops a process by PID file, then verifies the port it was using is
# actually free afterward — not just that the kill command didn't error.
stop_and_verify() {
    local name="$1"
    local pidfile="$PID_DIR/$name.pid"
    local port="$2"

    warn_if_systemd_restarts "$name"

    if [ ! -f "$pidfile" ]; then
        echo "[$name] no PID file — was it started by this script?"
        return
    fi

    local pid
    pid="$(cat "$pidfile")"
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "[$name] PID $pid not running — already stopped."
        rm -f "$pidfile"
        return
    fi

    echo "[$name] sending stop signal to PID $pid..."
    kill -TERM "$pid"

    # Poll instead of one fixed sleep: a single Dramatiq worker's graceful
    # shutdown can legitimately take several seconds (default
    # --worker-shutdown-timeout is 30s — it lets in-flight messages finish
    # before exiting), and with one process per queue (CR-004) several
    # workers shut down at the same time, competing for CPU. Confirmed
    # directly: a fixed 2s sleep reported 5 of 13 workers as "still
    # running" when every one of them had actually exited within a few
    # more seconds — a false positive, not a real stuck process. Exiting
    # the loop as soon as the PID is actually gone (rather than always
    # waiting the full ceiling) keeps a normal, fast stop just as quick as
    # the old fixed sleep was.
    local waited=0
    local max_wait=35
    while [ "$waited" -lt "$max_wait" ] && kill -0 "$pid" 2>/dev/null; do
        sleep 1
        waited=$((waited + 1))
    done

    if kill -0 "$pid" 2>/dev/null; then
        echo "[$name] WARNING: PID $pid still running ${max_wait}s after stop signal."
        echo "         It may need more time, or 'kill -9 $pid' if truly stuck."
    else
        rm -f "$pidfile"
        echo "[$name] stopped (after ${waited}s)."
    fi

    if [ -n "$port" ]; then
        # Same parsing as rhel9-start.sh's require_port_free — see there
        # for why the local-address column is extracted and matched at
        # end-of-field rather than assuming a trailing space.
        if ss -tlnH 2>/dev/null | awk '{print $4}' | grep -q ":$port$"; then
            echo "[$name] WARNING: port $port is STILL in use after stopping."
            echo "         Run: bash infra/scripts/check-port.sh $port"
        else
            echo "[$name] port $port confirmed free."
        fi
    fi
}

echo "=== Stopping Risk Workbench processes on RHEL9 ==="
stop_and_verify uvicorn 8000
while read -r queue; do
    stop_and_verify "worker-$queue" ""
done < <(.venv/bin/python -m app.workers.queues)
stop_and_verify poller ""

echo ""
echo "[valkey] checking..."
warn_if_systemd_restarts valkey
if command -v valkey-cli > /dev/null 2>&1 && valkey-cli ping > /dev/null 2>&1; then
    valkey-cli shutdown nosave 2>/dev/null || true
    sleep 1
    if valkey-cli ping > /dev/null 2>&1; then
        echo "[valkey] WARNING: still responding after shutdown."
    else
        echo "[valkey] stopped."
    fi
else
    echo "[valkey] not running."
fi

echo ""
echo "[nginx] checking..."
warn_if_systemd_restarts nginx
if systemctl is-active --quiet nginx 2>/dev/null; then
    echo "[nginx] this is a systemd service — use 'sudo systemctl stop nginx'"
    echo "        directly if you want it stopped, not just reloaded."
else
    echo "[nginx] not running (or not managed by systemd)."
fi

echo ""
echo "=== Done. Review any WARNING lines above before assuming a clean stop. ==="
