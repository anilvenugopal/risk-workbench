#!/usr/bin/env bash
# rhel9-check-prereqs.sh — verify everything rhel9-setup.sh was supposed to
# already have done, and that connectivity actually works. Read-only: this
# script never installs, creates, or changes anything — it only checks and
# reports. Safe to run repeatedly, from the server itself or from a CI
# pipeline (over SSH), as a pre-flight check before pulling code or
# installing dependencies.
#
# Usage: APP_DIR=/opt/risk-workbench DEPLOY_USER=dev-user \
#        PYTHON_PKG=python3.14 ./rhel9-check-prereqs.sh

set -euo pipefail

APP_DIR="${APP_DIR:?set APP_DIR, e.g. /opt/risk-workbench}"
DEPLOY_USER="${DEPLOY_USER:?set DEPLOY_USER, the account the app runs as}"
PYTHON_PKG="${PYTHON_PKG:-python3.14}"

# Track whether anything failed, without stopping at the first problem —
# a prerequisite check is more useful telling you EVERYTHING that's wrong
# in one pass, rather than making you fix one thing, re-run, find the next
# thing, re-run again. Note this means we deliberately do NOT use
# "set -e" here the way other scripts do — a failed check should be
# recorded and reported, not treated as a fatal script error.
FAILED=0

# A small helper: print PASS or FAIL consistently for every check below,
# and mark FAILED if this one didn't pass, without stopping the script.
check() {
    local description="$1"
    local passed="$2"
    if [ "$passed" = "yes" ]; then
        echo "  [PASS] $description"
    else
        echo "  [FAIL] $description"
        FAILED=1
    fi
}

echo "=== 1. System packages ==="
for pkg in git "$PYTHON_PKG" "${PYTHON_PKG}-devel" "${PYTHON_PKG}-pip" \
    unixODBC-devel msodbcsql18 gcc gcc-c++ make nginx valkey gettext rsync; do
    if rpm -q "$pkg" > /dev/null 2>&1; then
        check "$pkg installed" "yes"
    else
        check "$pkg installed" "no"
    fi
done

echo ""
echo "=== 2. Command-line tools actually work ==="
# Checking the package is installed (above) isn't quite the same as
# checking the actual command works — this catches a broken install rpm
# somehow left behind without the real binary, or a PATH problem.
for cmd in git "$PYTHON_PKG" nginx valkey-server envsubst rsync; do
    if command -v "$cmd" > /dev/null 2>&1; then
        check "'$cmd' command available" "yes"
    else
        check "'$cmd' command available" "no"
    fi
done

echo ""
echo "=== 3. Application directory ==="
if [ -d "$APP_DIR" ]; then
    check "$APP_DIR exists" "yes"
    # "stat -c '%U'" prints the username that owns a file/folder. Comparing
    # it against DEPLOY_USER confirms the account this script is meant to
    # run as can actually write into this directory — the exact permission
    # question we proved by hand, by hitting "Permission denied", earlier
    # in this project's setup.
    OWNER="$(stat -c '%U' "$APP_DIR")"
    if [ "$OWNER" = "$DEPLOY_USER" ]; then
        check "$APP_DIR owned by $DEPLOY_USER" "yes"
    else
        check "$APP_DIR owned by $DEPLOY_USER (actually owned by $OWNER)" "no"
    fi
else
    check "$APP_DIR exists" "no"
fi

echo ""
echo "=== 4. nginx service ==="
if systemctl is-enabled --quiet nginx 2>/dev/null; then
    check "nginx enabled (starts on boot)" "yes"
else
    check "nginx enabled (starts on boot)" "no"
fi
if systemctl is-active --quiet nginx 2>/dev/null; then
    check "nginx currently running" "yes"
else
    check "nginx currently running" "no"
fi
# This confirms the specific narrow permission rhel9-setup.sh granted —
# without it, a deploy can never reload nginx's config without full sudo.
if sudo -n -l 2>/dev/null | grep -q "systemctl reload nginx"; then
    check "$DEPLOY_USER can reload nginx without a password" "yes"
else
    check "$DEPLOY_USER can reload nginx without a password" "no"
fi

echo ""
echo "=== 5. ODBC driver registration ==="
# This checks the OS-level ODBC driver manager directly — no Python, no
# pyodbc, no virtual environment involved. pyodbc itself is an application
# dependency (installed by rhel9-app-install.sh, into .venv), NOT a system
# prerequisite — testing an actual database login belongs AFTER that
# install step, as a verification of it, not before it as a prerequisite.
if odbcinst -q -d -n "ODBC Driver 18 for SQL Server" > /dev/null 2>&1; then
    check "ODBC Driver 18 for SQL Server registered" "yes"
else
    check "ODBC Driver 18 for SQL Server registered" "no"
fi

echo ""
echo "=== 6. SQL Server network reachability ==="
# A plain TCP connection test — confirms the network path and port are
# open, without needing pyodbc, a driver, or real database credentials at
# all. This deliberately does NOT prove a login would succeed (wrong
# password would still pass this check) — it only proves "something is
# listening at this address," which is what's actually knowable before the
# app's own dependencies exist.
ENV_FILE="$APP_DIR/infra/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "  [SKIP] infra/.env not found at $ENV_FILE — cannot test network"
    echo "         reachability yet. Not counted as a failure; place the"
    echo "         real .env file and re-run this check."
else
    set -a
    source "$ENV_FILE"
    set +a
    # Bash can open a raw TCP connection to a "file" named
    # /dev/tcp/HOST/PORT — a bash-only feature, no external tool needed.
    # Redirecting into it and immediately closing (3<&- 3>&-) just tests
    # whether the connection itself succeeds.
    if timeout 5 bash -c "exec 3<>/dev/tcp/${MSSQL_WORKBENCH_SERVER}/${MSSQL_WORKBENCH_PORT} && exec 3<&- 3>&-" 2>/dev/null; then
        check "SQL Server host:port reachable ($MSSQL_WORKBENCH_SERVER:$MSSQL_WORKBENCH_PORT)" "yes"
    else
        check "SQL Server host:port reachable ($MSSQL_WORKBENCH_SERVER:$MSSQL_WORKBENCH_PORT)" "no"
    fi
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "=== All checks passed. ==="
    exit 0
else
    echo "=== One or more checks FAILED — see [FAIL] lines above. ===" >&2
    exit 1
fi
