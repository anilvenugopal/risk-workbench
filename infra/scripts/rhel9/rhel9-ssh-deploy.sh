#!/usr/bin/env bash
# rhel9-ssh-deploy.sh — push-based deployment to RHEL9 over SSH.
#
# Runs from wherever this script is invoked (a dev machine, or a CI/CD
# runner) — NOT on the RHEL9 server itself. RHEL9 never talks to GitHub or
# any package index directly; it only receives files pushed to it and runs
# commands this script triggers remotely over SSH.
#
# This deliberately does NOT call rhel9-pull-code.sh — that script is for
# the separate, local/manual "log into the server and git pull yourself"
# flow. This script pushes files via rsync instead, so RHEL9 never needs
# outbound internet access to GitHub at all (the same corporate-firewall
# concern that ruled out uv needing access to astral.sh applies here).
#
# Prerequisite for rsync: rsync must be installed on BOTH the
# pushing machine AND the RHEL9 server. rsync-over-SSH launches a matching
# rsync process on the remote end even for a single transfer — the local
# rsync command having it installed is not enough on its own. rhel9-setup.sh
# installs it on RHEL9 for exactly this reason.
#
# Usage:
#   DEPLOY_HOST=dev-user@172.19.253.47 \
#   DEPLOY_DIR=/opt/risk-workbench \
#   SSH_KEY=~/.ssh/risk-workbench-deploy \
#   ./rhel9-ssh-deploy.sh

set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:?set DEPLOY_HOST, e.g. dev-user@172.19.253.47}"
DEPLOY_DIR="${DEPLOY_DIR:?set DEPLOY_DIR, e.g. /opt/risk-workbench}"
SSH_KEY="${SSH_KEY:?set SSH_KEY, e.g. ~/.ssh/risk-workbench-deploy}"
PYTHON_PKG="${PYTHON_PKG:-python3.14}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# "-i" tells ssh/rsync which private key to use for this connection,
# instead of trying whatever key it would normally guess. Every ssh/rsync
# call below reuses this same variable so the key is only named once.
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=accept-new"

echo "=== 1. Verify prerequisites (remote) ==="
# "ssh HOST 'command'" runs that one command ON the remote machine and
# streams its output back here — this is what "remote" means throughout
# this script. Nothing in this step touches this machine's own files.
ssh $SSH_OPTS "$DEPLOY_HOST" \
    "APP_DIR=$DEPLOY_DIR DEPLOY_USER=\$(whoami) PYTHON_PKG=$PYTHON_PKG \
     bash $DEPLOY_DIR/infra/scripts/rhel9/rhel9-check-prereqs.sh"

echo ""
echo "=== 2. Push code (rsync, git-tracked files only) ==="
# rsync normally needs an explicit list of what to send and what to
# exclude — error-prone, since a new server-only folder added later could
# get silently deleted by --delete if nobody remembers to add it to an
# exclude list by hand (a real problem found and fixed earlier in this
# project). Instead, "--filter=':- .gitignore'" tells rsync to read the
# .gitignore file itself and treat every pattern in it as "never touch
# this" — the exact same file git already uses to mark infra/.env, logs,
# .venv, and generated data as off-limits. As new entries are added to
# .gitignore in the future, this filter updates itself automatically.
#
# --delete makes the server's copy match the source exactly — removing
# anything git-tracked that no longer exists in this checkout. Paired with
# the .gitignore filter, "anything git-tracked" is the only thing that can
# ever be deleted; gitignored files are invisible to this command entirely.
#
# -a (archive: preserves permissions/timestamps, recurses into folders)
# -v (verbose: show what's being sent)
# -z (compress during transfer)
# -e "ssh $SSH_OPTS" tells rsync to tunnel through ssh using our key,
# the same way the plain ssh commands in this script do.
rsync -avz --delete \
    --filter=':- .gitignore' \
    -e "ssh $SSH_OPTS" \
    ./ "$DEPLOY_HOST:$DEPLOY_DIR/"

echo ""
echo "=== 3. Install dependencies and run migrations (remote) ==="
# SSH opens the remote shell in the login account's home directory
# (/home/dev-user), NOT in $DEPLOY_DIR — confirmed the hard way: without
# this "cd", rhel9-app-install.sh's own precondition check correctly
# refused to run, reporting 'app' not found in /home/dev-user, since its
# checks use paths relative to wherever it's invoked FROM, not relative to
# where the script file itself lives. Every earlier manual test worked
# because we always cd'd into the app directory by hand first — this
# one-line SSH command needs to do that explicitly instead.
ssh $SSH_OPTS "$DEPLOY_HOST" \
    "cd $DEPLOY_DIR && PYTHON_BIN=$PYTHON_PKG bash infra/scripts/rhel9/rhel9-app-install.sh"

echo ""
echo "=== 4. Reload nginx (remote, pre-authorized, no password) ==="
# Relies on TWO one-time sudoers grants from rhel9-setup.sh: writing the
# config file (tee /etc/nginx/conf.d/risk-workbench.conf) and reloading
# nginx (systemctl reload nginx) — see docs/RHEL9_DEPLOYMENT.md Step 0. If
# this fails with a password prompt, one of those grants is missing.
ssh $SSH_OPTS "$DEPLOY_HOST" bash -s -- "$DEPLOY_DIR" << 'REMOTE_NGINX'
set -euo pipefail
DEPLOY_DIR="$1"
# site.conf (not nginx.conf) — a conf.d fragment containing only the app's
# server block, not the worker_processes/events/http wrapper a full config
# needs. nginx.conf is for standalone use (Docker, manual `nginx -c`
# testing) and is not valid inside /etc/nginx/conf.d/.
APP_ROOT="$DEPLOY_DIR" envsubst '$APP_ROOT' \
    < "$DEPLOY_DIR/deploy/nginx/site.conf" \
    | sudo tee /etc/nginx/conf.d/risk-workbench.conf > /dev/null
sudo systemctl reload nginx
REMOTE_NGINX

echo ""
echo "=== 5. Health check ==="
if ssh $SSH_OPTS "$DEPLOY_HOST" "curl -sf http://127.0.0.1:80/api/health" > /tmp/rhel9-deploy-health.json; then
    echo "  Health check OK:"
    cat /tmp/rhel9-deploy-health.json
else
    echo "  WARNING: health check did not return success. The app may not" >&2
    echo "  have restarted yet — restarting uvicorn/worker/poller is still" >&2
    echo "  a manual step (see docs/RHEL9_DEPLOYMENT.md Open items)." >&2
fi

echo ""
echo "=== Deploy complete. ==="
echo "NOTE: uvicorn/Dramatiq worker/poller restart is NOT automated by this"
echo "script yet — see docs/RHEL9_DEPLOYMENT.md Open items (systemd units"
echo "and Dramatiq queue-drain are both still unbuilt, deliberately)."
