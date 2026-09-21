#!/usr/bin/env bash
# run_user_setup.sh — interactive user provisioning CLI for Risk Workbench.
#
# Uses the project virtual environment with WSL or deployed database settings.
#
# Usage:
#   ./infra/scripts/run_user_setup.sh
#   make wsl-user-setup
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
