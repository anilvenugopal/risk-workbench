#!/usr/bin/env bash
# wsl-worker-logs.sh — live tail of the per-queue worker logs written by
# wsl-workers.sh, all in one terminal, each line prefixed with its queue name.
#
# Usage:
#   bash infra/scripts/wsl-worker-logs.sh                    # every queue
#   bash infra/scripts/wsl-worker-logs.sh --queue upload_edm # one queue

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR=.dev-logs
FILTER_QUEUE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --queue) FILTER_QUEUE="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [ -n "$FILTER_QUEUE" ]; then
    exec tail -n 50 -F "$LOG_DIR/worker-$FILTER_QUEUE.log"
fi

QUEUES="$(bash -c 'source infra/scripts/wsl-env.sh && uv run python -m app.workers.queues')"
[ -z "$QUEUES" ] && { echo "ERROR: could not list queue names." >&2; exit 1; }

FILES=()
while read -r queue; do
    FILES+=("$LOG_DIR/worker-$queue.log")
done <<< "$QUEUES"

# One tail over every log and one awk to label the lines: a single foreground
# pipeline, so Ctrl-C ends exactly these two processes and nothing else. -F
# (not -f) picks up a queue whose log does not exist yet. tail announces each
# file it switches to as "==> path <==", which is where the label comes from.
exec tail -n 10 -F "${FILES[@]}" 2>/dev/null | awk '
    /^==> .* <==$/ {
        q = $2
        sub(/^.*\/worker-/, "", q)
        sub(/\.log$/, "", q)
        next
    }
    NF { printf "%-24s %s\n", "[" q "]", $0; fflush() }
'
