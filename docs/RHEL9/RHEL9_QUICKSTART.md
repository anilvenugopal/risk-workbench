# RHEL9 Quickstart

Condensed end-to-end sequence for setting up a WSL2 RHEL9 instance and
deploying to it. For explanations and troubleshooting, see
[RHEL9_WSL_INSTALL.md](RHEL9_WSL_INSTALL.md),
[RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md),
[RHEL9_SSH_KEY_SETUP.md](RHEL9_SSH_KEY_SETUP.md), and
[RHEL9_DEPLOYMENT.md](RHEL9_DEPLOYMENT.md) — this file only lists commands
in order.

Placeholders throughout: `dev-user` (the account name), `172.19.253.47`
(the RHEL9 box's IP — find it fresh each session, see below),
`/opt/risk-workbench` (the app directory). A real production server's
account name and IP are infra's to assign — substitute those when known.

---

## 1. Install RHEL9 on WSL2

Covered in [RHEL9_WSL_INSTALL.md](RHEL9_WSL_INSTALL.md): create a free Red
Hat Developer account, build a RHEL9 WSL image via Red Hat's Image
Builder, install it with `wsl --install --from-file`, register it with
`subscription-manager`, fix the locale gap, and create a personal
non-`cloud-user` account (`dev-user`) with `sudo` via the `wheel` group.

Find the box's current IP (re-check each session — WSL2 may reassign it):

```bash
ip -4 addr show eth0
```

## 2. Set up SSH (from Ubuntu or Windows, to RHEL9)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/risk-workbench-deploy -C "risk-workbench-deploy"
```

Get the public key onto RHEL9 (see
[RHEL9_SSH_KEY_SETUP.md](RHEL9_SSH_KEY_SETUP.md) for transfer options if a
direct `scp`/`ssh-copy-id` isn't available):

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
cat <path-to-risk-workbench-deploy.pub> >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Verify — should return with no password prompt:

```bash
ssh -i ~/.ssh/risk-workbench-deploy dev-user@172.19.253.47 "echo ok"
```

## 3. One-time system setup (run on RHEL9, as `dev-user`)

Copy `infra/scripts/rhel9/rhel9-setup.sh` to the server first (the repo
isn't cloned yet at this point), `chmod +x` it, then:

```bash
DEPLOY_USER=dev-user APP_DIR=/opt/risk-workbench bash rhel9-setup.sh
```

Installs: git, Python 3.14 + pip, `unixODBC-devel`, the Microsoft ODBC
Driver 18 (via Microsoft's own RHEL9 repo), nginx, valkey, gcc/gcc-c++/
make, gettext, rsync. Does **not** install Podman — that's a separate,
optional step (Section 6), needed only for a local WSL2 SQL Server
instance, never for a real deployment target.

Also: creates `/opt/risk-workbench` (owned by `dev-user`), starts nginx as
a systemd service, grants `dev-user` two narrow passwordless `sudo` rules
(writing `/etc/nginx/conf.d/`, reloading nginx), creates the Valkey data
directory, and sets `vm.overcommit_memory=1`. Full detail:
[RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md).

Verify the two sudo grants landed correctly:

```bash
sudo cat /etc/sudoers.d/risk-workbench-nginx-reload
# dev-user ALL=(root) NOPASSWD: /usr/bin/systemctl reload nginx

sudo cat /etc/sudoers.d/risk-workbench-nginx-conf-write
# dev-user ALL=(root) NOPASSWD: /usr/bin/tee /etc/nginx/conf.d/risk-workbench.conf
```

## 4. Place the secrets file

`/opt/risk-workbench` now exists (created by step 3). Copy the real
`infra/.env` there — never generated or pushed by any script:

```
/opt/risk-workbench/infra/.env
```

## 5. (Optional, WSL2-only) Local SQL Server via Podman

Skip this if pointing at a real, separately-hosted SQL Server instead.

```bash
scp -i ~/.ssh/risk-workbench-deploy \
    infra/scripts/rhel9/rhel9-setup-podman-mssql.sh \
    dev-user@172.19.253.47:/opt/risk-workbench/infra/scripts/rhel9/
ssh -i ~/.ssh/risk-workbench-deploy dev-user@172.19.253.47 \
    "APP_DIR=/opt/risk-workbench DEPLOY_USER=dev-user bash /opt/risk-workbench/infra/scripts/rhel9/rhel9-setup-podman-mssql.sh"
```

Installs Podman, does the one-time rootless setup, and **creates** (does
not start) a SQL Server container identical to Ubuntu's Docker setup,
bind-mounted to `/var/lib/risk-workbench/mssql`. Start it before deploying
(Section 8) — the prerequisite check in the next section tests real
network connectivity to whatever `infra/.env` points at, container or not.

## 6. Verify prerequisites (remote, no password prompt expected)

```bash
ssh -i ~/.ssh/risk-workbench-deploy dev-user@172.19.253.47 \
    "APP_DIR=/opt/risk-workbench DEPLOY_USER=dev-user PYTHON_PKG=python3.14 bash /opt/risk-workbench/infra/scripts/rhel9/rhel9-check-prereqs.sh"
```

Checks packages, commands, directory ownership, the nginx sudo grants,
ODBC driver registration, and (once `infra/.env` exists) SQL Server
network reachability. Read-only — safe to run anytime.

## 7. Deploy the code

**Push-based (from Ubuntu/dev machine/CI) — the real deployment path:**

```bash
DEPLOY_HOST=dev-user@172.19.253.47 \
DEPLOY_DIR=/opt/risk-workbench \
SSH_KEY=~/.ssh/risk-workbench-deploy \
bash infra/scripts/rhel9/rhel9-ssh-deploy.sh
```

`SSH_KEY` must not contain spaces. Pushes git-tracked files via `rsync`
(honors `.gitignore` — `infra/.env` and similar are never touched or
deleted), then remotely verifies prerequisites, installs dependencies,
runs migrations, and reloads nginx. RHEL9 never talks to GitHub directly.

**Pull-based (run directly on RHEL9) — manual/local alternative:**

```bash
APP_DIR=/opt/risk-workbench BRANCH=main bash infra/scripts/rhel9/rhel9-pull-code.sh
APP_DIR=/opt/risk-workbench DEPLOY_USER=dev-user PYTHON_PKG=python3.14 bash infra/scripts/rhel9/rhel9-check-prereqs.sh
PYTHON_BIN=python3.14 bash infra/scripts/rhel9/rhel9-app-install.sh
```

`rhel9-app-install.sh` takes `PYTHON_BIN`, not `BRANCH` — it doesn't fetch
code itself, only installs dependencies and migrates against whatever's
already on disk. Must be run from inside `/opt/risk-workbench`.

## 8. Start and stop the app

```bash
ssh -i ~/.ssh/risk-workbench-deploy dev-user@172.19.253.47 \
    "APP_DIR=/opt/risk-workbench bash /opt/risk-workbench/infra/scripts/rhel9/rhel9-start.sh"
ssh -i ~/.ssh/risk-workbench-deploy dev-user@172.19.253.47 \
    "bash /opt/risk-workbench/infra/scripts/rhel9/rhel9-stop.sh"
```

Starts/stops Valkey, uvicorn, the Dramatiq worker, and the poller.
Refuses to start if a port is already occupied; verifies ports are free
after stopping. nginx is left alone (managed separately via `systemctl`
and the deploy script's reload step).

## 9. (Optional, WSL2-only) Start/stop the local SQL Server container

```bash
APP_DIR=/opt/risk-workbench bash infra/scripts/rhel9/rhel9-start-podman-mssql.sh
bash infra/scripts/rhel9/rhel9-stop-podman-mssql.sh
```

`rhel9-stop-podman-mssql.sh` takes no arguments. Check logs with
`podman logs sqlserver` if start doesn't confirm ready within 90s.

Verify connectivity directly:

```bash
podman exec sqlserver /opt/mssql-tools18/bin/sqlcmd \
    -C -S localhost -U sa -P '<password>' -Q "SELECT 1"
```

Or through the app's own driver stack:

```bash
.venv/bin/python -c "
import pyodbc, os
conn = pyodbc.connect(
    f\"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER=127.0.0.1,1433;\"
    f\"UID={os.environ['MSSQL_WORKBENCH_USER']};\"
    f\"PWD={os.environ['MSSQL_WORKBENCH_PASSWORD']};TrustServerCertificate=yes;\",
    timeout=5,
)
print(conn.execute('SELECT @@VERSION').fetchone()[0])
"
```
