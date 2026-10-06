#!/usr/bin/env bash
# run_user_setup.sh — interactive user provisioning CLI for Risk Workbench.
#
# Runs on: a WSL2 dev checkout (make wsl-user-setup) or the server, from APP_DIR.
# Needs:   infra/.env — through wsl-env.sh when WSL_DISTRO_NAME is set, directly
#          otherwise; .venv with the dev group (uv sync, or rhel9-app-install.sh).
# Usage:   make wsl-user-setup      server: cd /rms && ./infra/scripts/run_user_setup.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [ -n "${WSL_DISTRO_NAME:-}" ]; then
    source "$ROOT/infra/scripts/wsl-env.sh"
else
    set -a
    source "$ROOT/infra/.env"
    set +a
fi
exec "$ROOT/.venv/bin/python" "$ROOT/infra/scripts/user_setup.py" "$@"
