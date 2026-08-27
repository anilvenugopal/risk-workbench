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
#
# Try WITHOUT sudo first: ss already shows the owning process/PID for
# anything you own (confirmed directly — a plain "ss -tlnp" sees a
# same-user redis-server just fine), so most checks never need root at
# all. Only escalate to sudo if the unprivileged pass found the port bound
# but couldn't see an owner (a root-owned process, e.g. nginx on 80).
#
# This matters because "sudo ss ... || true" (the old approach) could not
# tell "sudo ran and genuinely found nothing" apart from "sudo itself
# failed" (no TTY for the password prompt, e.g. in a non-interactive
# shell) — both produced an empty $MATCH, so a real sudo failure was
# silently reported as "nothing listening," which is wrong: the two are
# very different situations and the script must never conflate them.
MATCH="$(ss -tlnp 2>/dev/null | grep ":$PORT[[:space:]]" || true)"

if [ -z "$MATCH" ]; then
    # Unprivileged pass saw no bound socket at all — sudo can't change that
    # answer (a socket either exists in the kernel's table or it doesn't,
    # regardless of who's allowed to see its owner), so there's no reason
    # to escalate here.
    echo "Nothing listening on port $PORT on THIS machine."
    echo ""
    echo "This does not rule out a conflict — if you're on WSL2 and another"
    echo "distro shares this machine's IP, run this same script there too:"
    echo "  wsl -d <other-distro> -- bash check-port.sh $PORT"
    exit 0
fi

if ! echo "$MATCH" | grep -q '(('; then
    # Bound, but no owning process visible without root (a root-owned
    # process, e.g. nginx on 80). "sudo -n true" is a clean, separate probe
    # for "can sudo run right now with no password prompt" — checked BEFORE
    # the real sudo ss call, so the two questions ("is sudo usable at all"
    # vs. "what did ss find") never get answered by inspecting one
    # compound exit code.
    if sudo -n true 2>/dev/null; then
        SUDO_MATCH="$(sudo -n ss -tlnp 2>/dev/null | grep ":$PORT[[:space:]]" || true)"
        if [ -n "$SUDO_MATCH" ]; then
            MATCH="$SUDO_MATCH"
        fi
        # else: sudo ran fine but found no match — a real race (the process
        # exited between the two ss calls), not a privilege problem. Fall
        # through with the original unprivileged $MATCH.
    else
        echo "Port $PORT is bound, but this process needs root to identify"
        echo "(sudo needs a password here — re-run this script from a"
        echo "regular interactive terminal, not a non-interactive session,"
        echo "so it can prompt you)."
        echo ""
        echo "Unprivileged view of the socket:"
        echo "$MATCH"
        exit 1
    fi
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
# hand (e.g. RHEL9's Valkey, run directly with no systemd unit — or even
# Ubuntu's own redis-server, if someone started it manually instead of via
# systemd) has no such live unit, so the plain command is correct there
# instead.
#
# The check must compare the systemd unit's OWN MainPID against the PID ss
# actually found holding the port — NOT just whether a unit file exists.
# Confirmed directly: a unit file can exist and be "enabled" while the
# process actually serving traffic right now was started by hand outside
# systemd (MainPID=0, ActiveState=inactive) — in that exact case, "does a
# unit file exist" says yes, but `systemctl stop` would stop nothing, since
# systemd has no process to stop. Comparing PIDs is what tells the two
# situations apart.
SYSTEMD_UNIT=""
case "$PROC_NAME" in
    redis-server) SYSTEMD_UNIT="redis-server" ;;
    valkey-server) SYSTEMD_UNIT="valkey" ;;
    nginx) SYSTEMD_UNIT="nginx" ;;
esac

MANAGED_BY_SYSTEMD="no"
if [ -n "$SYSTEMD_UNIT" ]; then
    UNIT_MAIN_PID="$(systemctl show "$SYSTEMD_UNIT" --property=MainPID --value 2>/dev/null || true)"
    if [ -n "$UNIT_MAIN_PID" ] && [ "$UNIT_MAIN_PID" != "0" ] && [ "$UNIT_MAIN_PID" = "$PROC_PID" ]; then
        MANAGED_BY_SYSTEMD="yes"
    fi
fi

echo "Suggested fix, based on what's holding it:"
if [ "$MANAGED_BY_SYSTEMD" = "yes" ]; then
    echo "  sudo systemctl stop $SYSTEMD_UNIT"
    echo "  (this PID is the one systemd's $SYSTEMD_UNIT.service is actively"
    echo "   managing — a plain shutdown/kill command will be silently undone"
    echo "   by systemd restarting it automatically)"
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
