#!/usr/bin/env bash
# check-port.sh — diagnose what's listening on a port, and how to stop it.
#
# Standalone: works identically whichever machine you run it on (Ubuntu,
# RHEL9, or any other Linux box) — no SSH, no cross-machine calls. If
# you're on WSL2 and suspect the OTHER distro is holding a port (they
# share one IP — see docs/RHEL9_SYSTEM_SETUP.md's Redis/Valkey section),
# run this SAME script over there too and compare; this script only ever
# reports what it can see from wherever it's actually running.
#
# Usage: ./check-port.sh 6379

set -euo pipefail

PORT="${1:?usage: check-port.sh <port-number>}"

echo "=== Checking port $PORT ==="

# "ss -tlnp" lists TCP (t) listening (l) sockets, numeric ports (n), with
# the owning process (p). "grep :$PORT" narrows to just our port; the
# trailing "$" in the pattern is deliberate — without it, checking port 80
# would also match 8000, 8080, etc.
MATCH="$(sudo ss -tlnp | grep ":$PORT[[:space:]]" || true)"

if [ -z "$MATCH" ]; then
    echo "Nothing listening on port $PORT on THIS machine."
    echo ""
    echo "This does not rule out a conflict — if you're on WSL2 and another"
    echo "distro shares this machine's IP, run this same script there too:"
    echo "  wsl -d <other-distro> -- bash check-port.sh $PORT"
    exit 0
fi

echo "$MATCH"
echo ""

# Pull out the process name from ss's output, e.g. users:(("nginx",pid=123,...
PROC_NAME="$(echo "$MATCH" | grep -oP '\(\("\K[^"]+' | head -1)"
PROC_PID="$(echo "$MATCH" | grep -oP 'pid=\K[0-9]+' | head -1)"

if [ -z "$PROC_NAME" ]; then
    echo "Port $PORT is bound, but no owning process is visible from here."
    echo ""
    echo "Confirmed cause on a WSL2 machine sharing its IP with another"
    echo "distro (see docs/RHEL9_SYSTEM_SETUP.md's Redis/Valkey section):"
    echo "this machine's kernel can see the port is taken at the network"
    echo "level, but the actual process runs in the OTHER distro — it has"
    echo "no visibility into that distro's process list at all, so the"
    echo "owner column comes back blank rather than wrong."
    echo ""
    echo "Run this same script in the other distro to find the real owner:"
    echo "  wsl -d Ubuntu-26.04 -- bash infra/scripts/check-port.sh $PORT"
    echo "  wsl -d RHEL9 -- bash infra/scripts/check-port.sh $PORT"
    echo ""
    echo "If you're not on WSL2, or already checked the other distro and"
    echo "found nothing there either, this may be a stale kernel socket"
    echo "left behind by a process that already exited — try starting your"
    echo "service anyway; it may simply work."
    exit 1
fi

echo "Held by: $PROC_NAME (PID $PROC_PID)"
echo ""

# A process managed by systemd will restart itself after a plain
# "shutdown"/"kill" — confirmed directly: Ubuntu's redis-server is a real
# systemd service (redis-server.service, enabled), and `redis-cli shutdown
# nosave` gets silently undone within seconds by systemd bringing it back
# up under a new PID. The correct stop command in that case is
# "systemctl stop", not the plain client command. A process we started by
# hand (e.g. RHEL9's Valkey, run directly with no systemd unit) has no
# such unit, so the plain command is correct there instead.
#
# "systemctl list-unit-files <name>.service" lists the unit if a unit FILE
# exists at all, regardless of whether it's currently running — a more
# direct answer to "does systemd manage this" than interpreting "status"'s
# exit code. "--no-legend" drops the header row so all we get is the
# actual matching line(s), or nothing.
SYSTEMD_UNIT=""
case "$PROC_NAME" in
    redis-server) SYSTEMD_UNIT="redis-server" ;;
    valkey-server) SYSTEMD_UNIT="valkey" ;;
    nginx) SYSTEMD_UNIT="nginx" ;;
esac

HAS_UNIT="no"
if [ -n "$SYSTEMD_UNIT" ]; then
    if systemctl list-unit-files --no-legend "${SYSTEMD_UNIT}.service" 2>/dev/null | grep -q "${SYSTEMD_UNIT}.service"; then
        HAS_UNIT="yes"
    fi
fi

echo "Suggested fix, based on what's holding it:"
if [ "$HAS_UNIT" = "yes" ]; then
    echo "  sudo systemctl stop $SYSTEMD_UNIT"
    echo "  (this is a real systemd service — a plain shutdown/kill command"
    echo "   will be silently undone by systemd restarting it automatically)"
else
    case "$PROC_NAME" in
        redis-server)
            echo "  redis-cli shutdown nosave"
            ;;
        valkey-server)
            echo "  valkey-cli shutdown nosave"
            ;;
        nginx)
            echo "  sudo pkill nginx"
            ;;
        uvicorn|python3*)
            echo "  kill -TERM $PROC_PID   # or Ctrl+C in the terminal running it"
            ;;
        *)
            echo "  kill -TERM $PROC_PID   # confirm this is safe to stop first"
            ;;
    esac
fi
echo ""
echo "If this process belongs to the OTHER WSL2 distro sharing this"
echo "machine's IP, run the stop command there instead — e.g.:"
echo "  wsl -d Ubuntu-26.04 -- <stop command>"
echo "  wsl -d RHEL9 -- <stop command>"
