# RHEL9 Quickstart

Condensed end-to-end sequence for setting up a RHEL9 deployment target (the
server, or a WSL2 RHEL9 distro used to rehearse a deployment) and deploying to
it. For local development on RHEL9, see [LOCAL_DEV_SETUP.md](../LOCAL_DEV_SETUP.md)
instead. For explanations and troubleshooting, see
[RHEL9_WSL_INSTALL.md](../RHEL9_WSL_INSTALL.md),
[RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md),
[RHEL9_SSH_KEY_SETUP.md](RHEL9_SSH_KEY_SETUP.md), and
[RHEL9_DEPLOYMENT.md](RHEL9_DEPLOYMENT.md) — this file only lists commands
in order.

`cinreadmd` (the account) and `/rms` (the app directory) are the values on
the deployed server. `172.19.253.47` is the WSL2 address of the RHEL9 test
VM (find it fresh each session, see below); the deployed environment does
not use it, so substitute the server's own address.

---

## 1. Install RHEL9 (only on WSL2)

Covered in [RHEL9_WSL_INSTALL.md](../RHEL9_WSL_INSTALL.md): create a free Red
Hat Developer account, build a RHEL9 WSL image via Red Hat's Image
Builder, install it with `wsl --install --from-file`, register it with
`subscription-manager`, fix the locale gap, and create a personal
non-`cloud-user` account (`cinreadmd`) with `sudo` via the `wheel` group.

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
ssh -i ~/.ssh/risk-workbench-deploy cinreadmd@172.19.253.47 "echo ok"
```

## 3. One-time system setup (run on RHEL9, as `cinreadmd`)

Copy `infra/scripts/deploy/rhel9-setup.sh` to the server first (the repo
isn't cloned yet at this point), `chmod +x` it, then:

```bash
DEPLOY_USER=cinreadmd APP_DIR=/rms bash rhel9-setup.sh
```

Installs: git, Python 3.14 + pip, `unixODBC-devel`, the Microsoft ODBC
Driver 18 (via Microsoft's own RHEL9 repo), nginx, valkey, gcc/gcc-c++/
make, gettext, rsync. Does **not** install Podman — that's a separate,
optional step (Section 5), needed only for a local WSL2 SQL Server
instance, never for a real deployment target.

Also: creates `/rms` (owned by `cinreadmd`), starts nginx as
a systemd service, grants `cinreadmd` two narrow passwordless `sudo` rules
(writing `/etc/nginx/conf.d/`, reloading nginx), creates the Valkey data
directory, and sets `vm.overcommit_memory=1`. Full detail:
[RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md).

Verify the two sudo grants landed correctly:

```bash
sudo cat /etc/sudoers.d/risk-workbench-nginx-reload
# cinreadmd ALL=(root) NOPASSWD: /usr/bin/systemctl reload nginx

sudo cat /etc/sudoers.d/risk-workbench-nginx-conf-write
# cinreadmd ALL=(root) NOPASSWD: /usr/bin/tee /etc/nginx/conf.d/risk-workbench.conf
```

## 4. Place the secrets file

`/rms` now exists (created by step 3). Copy the real
`infra/.env` there — never generated or pushed by any script:

```
/rms/infra/.env
```

## 5. (Optional, WSL2-only) Local SQL Server via Podman

Skip this if pointing at a real, separately-hosted SQL Server instead.

On a fresh server `/rms/infra/scripts/deploy/` does not exist until the first
deploy (Section 7), so create it before copying the script:

```bash
ssh -i ~/.ssh/risk-workbench-deploy cinreadmd@172.19.253.47 \
    "mkdir -p /rms/infra/scripts/deploy"
scp -i ~/.ssh/risk-workbench-deploy \
    infra/scripts/deploy/rhel9-setup-podman-mssql.sh \
    cinreadmd@172.19.253.47:/rms/infra/scripts/deploy/
ssh -i ~/.ssh/risk-workbench-deploy cinreadmd@172.19.253.47 \
    "APP_DIR=/rms DEPLOY_USER=cinreadmd bash /rms/infra/scripts/deploy/rhel9-setup-podman-mssql.sh"
```

Installs Podman, does the one-time rootless setup, and **creates** (does
not start) a SQL Server container with the same image, environment and port
as the `sqlserver` service in `infra/docker-compose.yml`,
bind-mounted to `/var/lib/risk-workbench/mssql`. Start it before deploying
(Section 9) — the prerequisite check in the next section tests real
network connectivity to whatever `infra/.env` points at, container or not.

## 6. Verify prerequisites (remote, no password prompt expected)

```bash
ssh -i ~/.ssh/risk-workbench-deploy cinreadmd@172.19.253.47 \
    "APP_DIR=/rms DEPLOY_USER=cinreadmd PYTHON_PKG=python3.14 bash /rms/infra/scripts/deploy/rhel9-check-prereqs.sh"
```

On a fresh server this script exists only after the first deploy (Section 7),
which runs it for you right after the push; run it by hand on a server that
already has the code.

Checks packages, commands, directory ownership, the nginx reload grant,
ODBC driver registration, and (once `infra/.env` exists) SQL Server
network reachability. Read-only — safe to run anytime.

## 7. Deploy the code

**Push-based (from Ubuntu/dev machine/CI) — the real deployment path:**

```bash
DEPLOY_HOST=cinreadmd@172.19.253.47 \
DEPLOY_DIR=/rms \
SSH_KEY=~/.ssh/risk-workbench-deploy \
bash infra/scripts/deploy/rhel9-ssh-deploy.sh
```

`SSH_KEY` must not contain spaces. Pushes git-tracked files via `rsync`
(honors `.gitignore` — `infra/.env` and similar are never touched or
deleted), then remotely verifies prerequisites, waits for queued `rwb_job`
rows to drain, installs dependencies, runs migrations, and reloads nginx. RHEL9 never talks to GitHub directly.

**Pull-based (run directly on RHEL9) — manual/local alternative:**

```bash
APP_DIR=/rms BRANCH=main bash infra/scripts/deploy/rhel9-pull-code.sh
APP_DIR=/rms DEPLOY_USER=cinreadmd PYTHON_PKG=python3.14 bash infra/scripts/deploy/rhel9-check-prereqs.sh
PYTHON_BIN=python3.14 bash infra/scripts/deploy/rhel9-app-install.sh
```

`rhel9-app-install.sh` takes `PYTHON_BIN`, not `BRANCH` — it doesn't fetch
code itself, only installs dependencies and migrates against whatever's
already on disk. Must be run from inside `/rms`.

## 8. Start and stop the app

```bash
ssh -i ~/.ssh/risk-workbench-deploy cinreadmd@172.19.253.47 \
    "APP_DIR=/rms bash /rms/infra/scripts/deploy/rhel9-start.sh"
ssh -i ~/.ssh/risk-workbench-deploy cinreadmd@172.19.253.47 \
    "APP_DIR=/rms bash /rms/infra/scripts/deploy/rhel9-stop.sh"
```

Starts/stops Valkey, uvicorn, one Dramatiq worker process per queue (one per
`rwb_job_type` — CR-004; each queue gets its own PID file
`worker-<queue>.pid` and log `worker-<queue>.log`), and the poller.
Accepts an already-running Valkey instance when `valkey-cli ping` succeeds.
Refuses to start when another process occupies port 6379 or when port 8000
is occupied; verifies port 8000 is free after stopping uvicorn. nginx is left alone
(managed separately via `systemctl` and the deploy script's reload step).

`rhel9-stop.sh` now requires `APP_DIR` (it didn't before this feature) —
stopping the per-queue workers means running `.venv/bin/python -m
app.workers.queues` to get the current queue list, which needs to resolve
both the venv and the `app` package from the checkout.

Check worker health at any point (before/after start or stop) with:

```bash
ssh -i ~/.ssh/risk-workbench-deploy cinreadmd@172.19.253.47 \
    "APP_DIR=/rms bash /rms/infra/scripts/deploy/rhel9-worker-health.sh"
```

Lists every queue with its PID-file status and an independent process-scan
status side by side — useful to confirm a start actually brought up all
queues, or a stop actually took all of them down, rather than assuming from
the start/stop script's own output alone.

Tail one queue's worker log or the poller log directly on the host (no
Makefile on RHEL9 — plain scripts, same as `rhel9-start.sh`/`rhel9-stop.sh`):

```bash
APP_DIR=/rms bash infra/scripts/deploy/rhel9-logs-worker.sh upload_edm
bash infra/scripts/deploy/rhel9-logs-poller.sh
```

Run `rhel9-logs-worker.sh` with no queue name to list the available queues.
Both scripts report a clear error (not a raw `tail` failure) if the log file
doesn't exist yet — usually meaning that worker/the poller isn't running.

## 9. (Optional, WSL2-only) Start/stop the local SQL Server container

```bash
APP_DIR=/rms bash infra/scripts/deploy/rhel9-start-podman-mssql.sh
bash infra/scripts/deploy/rhel9-stop-podman-mssql.sh
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

## Next

After the first deployment:

- Finish the Entra configuration: [ENTRA_SETUP.md](../ENTRA_SETUP.md).
- Create the first admin: [USER_PROVISIONING.md](../USER_PROVISIONING.md)
  (`./infra/scripts/run_user_setup.sh` on the server).
