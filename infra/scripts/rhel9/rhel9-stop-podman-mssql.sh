#!/usr/bin/env bash
# rhel9-stop-podman-mssql.sh — stop the local SQL Server container started
# by rhel9-start-podman-mssql.sh.
#
# Exits quietly (not an error) if Podman isn't installed, or if the
# container isn't running — both are normal states, not failures.

set -uo pipefail

if ! command -v podman > /dev/null 2>&1; then
    echo "podman not installed — nothing to stop."
    exit 0
fi

if ! podman ps --format '{{.Names}}' 2>/dev/null | grep -qx sqlserver; then
    echo "'sqlserver' is not running — nothing to stop."
    exit 0
fi

echo "Stopping 'sqlserver'..."
# This container's own entrypoint script (/opt/mssql/bin/launch_sqlservr.sh,
# confirmed by reading it directly) starts sqlservr as a background process
# and "wait"s on it, with no "trap" to forward SIGTERM down to it. SIGTERM
# sent to the container reaches only that wrapper script (PID 1), never the
# real sqlservr process — so no timeout length lets it shut down on its
# own; Podman always ends up forcing a SIGKILL regardless of --time. Given
# that, a short timeout (10s, Podman's own default) is used rather than a
# longer one — waiting longer only delays an outcome that's the same
# either way. SQL Server's own crash-recovery journaling is what protects
# data here, the same mechanism that protects against a real power loss.
echo "(SIGKILL warning below is expected)"
podman stop sqlserver > /dev/null

if podman ps --format '{{.Names}}' 2>/dev/null | grep -qx sqlserver; then
    echo "WARNING: still running after stop." >&2
    exit 1
else
    echo "Stopped."
fi
