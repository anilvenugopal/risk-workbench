#!/usr/bin/env bash
# rhel9-setup.sh — one-time RHEL9 server prep. Idempotent: every step checks
# current state before acting, safe to re-run.
#
# Run as a user with sudo (setup phase only — the app itself never needs
# standing sudo once this script finishes).
#
# Usage: DEPLOY_USER=dev-user APP_DIR=/opt/risk-workbench ./rhel9-setup.sh

# "set" changes how THIS script behaves — like flipping safety switches
# before driving, not a real-world plumbing/water thing.
#   e  = if any command fails, stop the whole script immediately instead of
#        plowing ahead on a broken foundation.
#   u  = if a variable (a named placeholder, e.g. $DEPLOY_USER below) is
#        referenced but was never actually set, treat that as an error too
#        (catches typos instead of silently treating it as empty).
#   o pipefail = when commands are chained with "|" (piping one command's
#        output into the next), fail the whole chain if ANY command in it
#        fails — not just the last one.
set -euo pipefail

# ${VAR:?message} means: "VAR must be provided by whoever runs this script;
# if it's missing, stop immediately and print this message." This is how
# the script demands its two required inputs instead of guessing or using a
# possibly-wrong default.
DEPLOY_USER="${DEPLOY_USER:?set DEPLOY_USER to the account the app will run as}"
APP_DIR="${APP_DIR:?set APP_DIR to the application directory, e.g. /opt/risk-workbench}"
# ${VAR:-default} is the softer version: use "default" only if VAR wasn't
# provided, but don't error out — this one's optional.
PYTHON_PKG="${PYTHON_PKG:-python3.14}"

echo "=== 1. Microsoft's package repository ==="
# unixODBC-devel (needed below) and msodbcsql18 do NOT come from RHEL's own
# built-in package sources — they come from Microsoft's repository, which
# has to be registered BEFORE dnf can find either of them. This step must
# run first, or the next section's install will fail on a truly fresh
# server with "no package unixODBC-devel available."
if [ ! -f /etc/yum.repos.d/mssql-release.repo ]; then
    sudo curl -fsSL https://packages.microsoft.com/config/rhel/9/prod.repo \
        -o /etc/yum.repos.d/mssql-release.repo
    echo "  Microsoft repo registered."
else
    echo "  Microsoft repo already registered."
fi

echo ""
echo "=== 2. System packages ==="
# An "array" is just a named list. This is every package this project needs
# on RHEL9 — the same list we installed by hand earlier, written once here
# instead of typing "sudo dnf install" nine separate times. unixODBC-devel
# is included here (not in section 3) because it comes from Microsoft's
# repo, registered just above — grouping it with msodbcsql18 in section 3
# would be more "obviously ODBC-related," but this script installs it here
# so one dnf command handles every general package at once. rsync is
# needed on BOTH ends of a push-based deploy (rhel9-ssh-deploy.sh) — it's
# not just a client-side tool, the server needs its own copy too, or the
# transfer fails with "rsync: command not found" even though the deploy
# script itself runs fine from the pushing machine.
NEEDED_PKGS=(git "$PYTHON_PKG" "${PYTHON_PKG}-devel" "${PYTHON_PKG}-pip"
    unixODBC-devel gcc gcc-c++ make nginx valkey gettext rsync)
# A second, empty list — we'll only add a package's name here if it turns
# out to genuinely be missing.
MISSING_PKGS=()
# "for pkg in LIST; do ...; done" means: repeat the next lines once for each
# item in the list, calling the current item "$pkg" each time.
for pkg in "${NEEDED_PKGS[@]}"; do
    # rpm -q asks RHEL9's own package database "is this already installed?"
    # ">/dev/null 2>&1" throws away the answer's text — we only care whether
    # the question itself succeeded or failed, not what it printed.
    # "||" means "if the thing before this failed" — i.e. NOT installed —
    # "then do this": add the package's name to our missing-packages list.
    rpm -q "$pkg" > /dev/null 2>&1 || MISSING_PKGS+=("$pkg")
done
# "${#MISSING_PKGS[@]}" counts how many items are in the missing-list.
# If it's more than zero, install exactly those — nothing already present
# gets touched, so running this script again does nothing here.
if [ "${#MISSING_PKGS[@]}" -gt 0 ]; then
    echo "  Installing: ${MISSING_PKGS[*]}"
    sudo dnf install -y "${MISSING_PKGS[@]}"
else
    echo "  All packages already installed."
fi

echo ""
echo "=== 3. Microsoft ODBC Driver 18 ==="
# "!" in front of a check means "if this is NOT true." So: "if
# msodbcsql18 is NOT already installed, do the following." The repo needed
# to find this package was already registered in section 1, above.
if ! rpm -q msodbcsql18 > /dev/null 2>&1; then
    # ACCEPT_EULA=Y answers "do you accept Microsoft's license?" ahead of
    # time, since this script runs unattended and can't respond to a prompt.
    sudo ACCEPT_EULA=Y dnf install -y msodbcsql18
else
    echo "  msodbcsql18 already installed."
fi

echo ""
echo "=== 4. Locale (Image Builder WSL images may ship without en_US.UTF-8) ==="
# "locale -a" lists every language/region setting actually installed on this
# machine. "grep -q" silently checks whether "en_US.utf8" is one of them —
# if it's NOT found, install the package that provides it.
if ! locale -a 2>/dev/null | grep -q "^en_US.utf8$"; then
    sudo dnf install -y glibc-langpack-en
else
    echo "  en_US.utf8 already present."
fi

echo ""
echo "=== 5. Application directory ==="
# "-d" checks "does this exist AND is it a directory (folder)?" "!" flips it
# to "does NOT exist." /opt is owned by root, so creating anything inside it
# needs sudo — this is the same fact we proved by hand earlier (a plain user
# gets "Permission denied" trying to mkdir there directly).
if [ ! -d "$APP_DIR" ]; then
    sudo mkdir -p "$APP_DIR"
    # chown = "change owner." This hands the folder over to the deployment
    # account so it can write files into it without needing sudo afterward.
    sudo chown "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"
    echo "  Created $APP_DIR, owned by $DEPLOY_USER."
else
    echo "  $APP_DIR already exists."
fi

echo ""
echo "=== 6. nginx as a systemd service ==="
# systemd is RHEL9's system for managing background services (programs that
# run continuously, started/stopped/restarted in a standard way rather than
# just typed into a terminal by hand). "systemctl enable" tells it "start
# nginx automatically every time this server boots." "|| true" means "if
# this command fails for any reason, don't treat that as fatal" — enabling
# an already-enabled service can sometimes print a harmless notice that
# would otherwise trip the "stop on any error" switch from the top of this
# file.
sudo systemctl enable nginx > /dev/null 2>&1 || true
# "systemctl is-active" asks "is this service currently running?" "--quiet"
# suppresses its output — we only care about yes/no.
if ! systemctl is-active --quiet nginx; then
    sudo systemctl start nginx
    echo "  nginx started."
else
    echo "  nginx already running."
fi

# "sudoers" is RHEL9's file-based system for granting specific commands to
# specific accounts, without giving them full root access. This is what
# lets the deployment account (which will NOT have general sudo rights)
# reload nginx's settings after every deploy without needing a password —
# and without being able to do anything else as root.
SUDOERS_FILE=/etc/sudoers.d/risk-workbench-nginx-reload
SUDOERS_LINE="$DEPLOY_USER ALL=(root) NOPASSWD: /usr/bin/systemctl reload nginx"
# Only write this rule if it doesn't already exist exactly as written —
# avoids appending duplicate copies of the same line if this script re-runs.
# /etc/sudoers.d is root:root, mode 750 — a plain user
# has no permission to even check whether a file exists inside it, so a
# non-sudo "[ -f ... ]" always reports "not found" regardless of the real
# state. "sudo grep" alone is enough — it fails cleanly (same as "not
# found") if the file genuinely doesn't exist, so no separate existence
# check is needed once everything already runs through sudo.
if ! sudo grep -qF "$SUDOERS_LINE" "$SUDOERS_FILE" 2>/dev/null; then
    # "tee" writes text to a file. We route it through sudo because the
    # /etc/sudoers.d/ folder itself is root-only.
    echo "$SUDOERS_LINE" | sudo tee "$SUDOERS_FILE" > /dev/null
    # chmod 440 = "only root and this file's own group can read it, nobody
    # can write to it, nobody can execute it" — sudoers files are sensitive
    # and RHEL9 requires this specific restrictive permission.
    sudo chmod 440 "$SUDOERS_FILE"
    # visudo -c checks the file for syntax mistakes BEFORE it's trusted —
    # a broken sudoers file can lock out sudo entirely, so this is a real
    # safety check, not a formality.
    sudo visudo -c -f "$SUDOERS_FILE"
    echo "  Granted $DEPLOY_USER passwordless 'systemctl reload nginx'."
else
    echo "  nginx reload permission already granted."
fi

# Deploying a config means writing /etc/nginx/conf.d/ (root owned).
# This needs its own narrow grant.
NGINX_CONF_PATH=/etc/nginx/conf.d/risk-workbench.conf
CONF_SUDOERS_FILE=/etc/sudoers.d/risk-workbench-nginx-conf-write
CONF_SUDOERS_LINE="$DEPLOY_USER ALL=(root) NOPASSWD: /usr/bin/tee $NGINX_CONF_PATH"
# Same /etc/sudoers.d permission issue as the reload rule above — plain
# "[ -f ... ]" can't see inside this root-only directory; sudo grep alone
# is sufficient.
if ! sudo grep -qF "$CONF_SUDOERS_LINE" "$CONF_SUDOERS_FILE" 2>/dev/null; then
    echo "$CONF_SUDOERS_LINE" | sudo tee "$CONF_SUDOERS_FILE" > /dev/null
    sudo chmod 440 "$CONF_SUDOERS_FILE"
    sudo visudo -c -f "$CONF_SUDOERS_FILE"
    echo "  Granted $DEPLOY_USER passwordless 'tee $NGINX_CONF_PATH'."
else
    echo "  nginx config write permission already granted."
fi

echo ""
echo "=== 7. Valkey data directory ==="
# /var/lib is the standard Linux location for a service's own persistent
# data (same category as where a real database keeps its files) — NOT a
# personal user's home directory. Using a home directory (e.g.
# /home/dev-user/valkey-data, used during early manual testing) ties
# Valkey's data to one specific person's account, which is exactly the
# problem this project moved away from when it stopped running everything
# as dev-user. Same pattern as section 5's application directory: only
# create it if missing, then hand ownership to the deployment account so
# Valkey can write to it without needing sudo at runtime.
VALKEY_DATA_DIR=/var/lib/risk-workbench/valkey
if [ ! -d "$VALKEY_DATA_DIR" ]; then
    sudo mkdir -p "$VALKEY_DATA_DIR"
    # -R = recursive, and "dirname" derives the PARENT of $VALKEY_DATA_DIR
    # (i.e. /var/lib/risk-workbench) FROM the variable itself, rather than
    # typing that path out separately — so if VALKEY_DATA_DIR's value ever
    # changes above, this line automatically follows it instead of quietly
    # pointing at a now-wrong, hardcoded path. This hands over both the
    # outer risk-workbench folder (created as a side effect of "mkdir -p"
    # above, since it didn't exist yet either) and the inner valkey folder.
    sudo chown -R "$DEPLOY_USER:$DEPLOY_USER" "$(dirname "$VALKEY_DATA_DIR")"
    echo "  Created $VALKEY_DATA_DIR, owned by $DEPLOY_USER."
else
    echo "  $VALKEY_DATA_DIR already exists."
fi

echo ""
echo "=== 8. Memory overcommit (for Valkey background saves) ==="
# Valkey/Redis warns on startup if this kernel setting isn't 1 — without
# it, a background save (which AOF rewrites depend on) can fail under
# memory pressure. "sysctl -n" reads the CURRENT live value; only touch
# the persistent config file if it isn't already set to 1.
if [ "$(sysctl -n vm.overcommit_memory)" != "1" ]; then
    echo 'vm.overcommit_memory = 1' | sudo tee -a /etc/sysctl.conf > /dev/null
    sudo sysctl -w vm.overcommit_memory=1 > /dev/null
    echo "  Set vm.overcommit_memory=1 (persisted in /etc/sysctl.conf)."
else
    echo "  vm.overcommit_memory already set to 1."
fi

echo ""
echo "=== Done. ==="
echo "Still needed once, separately (not automated by this script):"
echo "  - Deploy SSH key: add the CI/pipeline's public key to"
echo "    /home/$DEPLOY_USER/.ssh/authorized_keys"
echo "  - infra/.env: place the real secrets file at $APP_DIR/infra/.env"
echo "    (never generated or overwritten by any script)"
