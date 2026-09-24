#!/usr/bin/env bash
# Report whether a Risk Workbench app process is still active.
# Valkey and nginx may remain active: neither writes to the Workbench database.

set -uo pipefail

if ! command -v pgrep > /dev/null 2>&1; then
    echo "ERROR: pgrep is required to check Risk Workbench processes." >&2
    exit 2
fi
if ! command -v ss > /dev/null 2>&1; then
    echo "ERROR: ss is required to check the uvicorn port." >&2
    exit 2
fi

active=0

report_processes() {
    local name="$1"
    local pattern="$2"
    local matches
    local status

    matches="$(pgrep -af -- "$pattern" 2>/dev/null)"
    status=$?
    if [ "$status" -gt 1 ]; then
        echo "ERROR: pgrep could not inspect $name." >&2
        active=1
        return
    fi
    if [ -n "$matches" ]; then
        local count
        count="$(awk 'END { print NR }' <<< "$matches")"
        echo "ERROR: $name is still active ($count matching processes):" >&2
        sed -n '1,5p' <<< "$matches" >&2
        if [ "$count" -gt 5 ]; then
            echo "  ... $((count - 5)) more" >&2
        fi
        active=1
    fi
}

report_processes "uvicorn" '[u]vicorn.*app\.main:app'
report_processes "a Dramatiq worker" '[d]ramatiq.*app\.workers\.entrypoint'
report_processes "the poller" 'app\.poller\.run.*--loop'

if ! listeners="$(ss -tlnH 2>/dev/null)"; then
    echo "ERROR: ss could not inspect listening ports." >&2
    exit 2
fi
if awk '{print $4}' <<< "$listeners" | grep ':8000$' > /dev/null; then
    echo "ERROR: port 8000 is still listening." >&2
    active=1
fi

if [ "$active" -ne 0 ]; then
    echo "ERROR: stop every process listed above before continuing." >&2
    exit 1
fi

echo "No active Risk Workbench app processes found."
