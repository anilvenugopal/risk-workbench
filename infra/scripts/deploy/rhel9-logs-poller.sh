#!/usr/bin/env bash
# rhel9-logs-poller.sh — tail the poller log on RHEL9.
#
# Runs on: the RHEL9 server (or a rehearsal distro), logged in or through `ssh host '...'`.
# Needs:   nothing; reads /var/lib/risk-workbench/poller.log.
# Usage:   bash infra/scripts/deploy/rhel9-logs-poller.sh

set -uo pipefail

logfile="/var/lib/risk-workbench/poller.log"
if [ ! -f "$logfile" ]; then
    echo "ERROR: $logfile does not exist — is the poller running (rhel9-start.sh)?" >&2
    exit 1
fi

exec tail -f "$logfile"
