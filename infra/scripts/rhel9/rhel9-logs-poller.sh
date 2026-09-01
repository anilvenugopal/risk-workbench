#!/usr/bin/env bash
# rhel9-logs-poller.sh — tail the poller log on RHEL9.
#
# Run directly on RHEL9 — not over SSH, not from Ubuntu (same rule as every
# other rhel9-*.sh script).
#
# Usage:
#   bash infra/scripts/rhel9/rhel9-logs-poller.sh

set -uo pipefail

logfile="/var/lib/risk-workbench/poller.log"
if [ ! -f "$logfile" ]; then
    echo "ERROR: $logfile does not exist — is the poller running (rhel9-start.sh)?" >&2
    exit 1
fi

exec tail -f "$logfile"
