# Local Development Setup (WSL2)

For development in a WSL2 Ubuntu or RHEL9 distro. On RHEL9, do
[RHEL9_WSL_INSTALL.md](RHEL9_WSL_INSTALL.md) first. Docker only, without WSL2:
[Docker-only stack](#docker-only-stack-no-wsl2). Deploying:
[deploy/RHEL9_QUICKSTART.md](deploy/RHEL9_QUICKSTART.md).

---

## About uv (the Python package manager)

This project uses **uv** to manage Python dependencies. It is not a runtime
dependency and has nothing to do with production. It is a faster replacement
for `pip` + `venv` — same Python packages, same result, no compliance exposure.

**uv is a developer tool only.** Production runs plain Python with a virtual
environment. Procurement, legal, and compliance will never see it.

If your organisation bans uv, replace `uv run <cmd>` with `.venv/bin/<cmd>`
and `uv sync` with `pip install -r requirements.txt`. `requirements.txt` holds the
runtime packages only (`uv export --no-dev`), so it has no pytest or ruff.

---

## Environment Topology

```
WSL2 Development                          Production (Linux server)
─────────────────────────────────         ──────────────────────────────
  nginx          (not needed in dev)       nginx          (systemd)
  uvicorn        make wsl-app        ≡    uvicorn        (rhel9-start.sh)
  redis-server   make wsl-start           valkey-server  (rhel9-start.sh)
  dramatiq       make wsl-worker          dramatiq       (rhel9-start.sh)
  poller         make wsl-poller          poller         (rhel9-start.sh)

  SQL Server ─── Docker or Podman  ≡    SQL Server ─── separate host
```

The same five processes run in development and production. In development they
are started manually (one terminal each). In production
`infra/scripts/deploy/rhel9-start.sh` starts them with `nohup`; nginx alone is a
systemd service. The commands are identical — only the launcher changes.

**Redis is durable (AOF) in all environments.** `appendonly yes`,
`appendfsync everysec`, persisted SSD volume. This ensures acknowledged
Dramatiq enqueues survive a broker crash (≤ ~1s worst-case loss). The poller
also runs a reconciler sweep each cycle to recover any `rwb_job` rows stuck in
`running` with a stale heartbeat — it is folded into the same poller process,
so the five-process count is unchanged.

Only SQL Server runs in a container (Docker or Podman). Everything else runs in
your WSL2 shell.

---

## What You Need Before Starting

- WSL2 running Ubuntu 22.04+ or RHEL9 (on RHEL9: `sudo dnf install -y git make`)
- Podman (installed in Step 4; the client's choice) or Docker Desktop with WSL2
  integration enabled for the distro
- VS Code on Windows, connected to the distro ([Connect VS Code to WSL2](#connect-vs-code-to-wsl2))

WSL2 distros share one IP. With both Ubuntu and RHEL9, `make wsl-stop` in one
before `make wsl-start` in the other, or ports 1433 and 6379 collide.

---

## Connect VS Code to WSL2

VS Code runs on Windows and opens the repo inside the distro through the WSL
extension. Its terminals, Python interpreter, test runner and extensions then
run in the distro, where `make wsl-*` runs.

1. Install [VS Code](https://code.visualstudio.com/) on Windows, not inside the
   distro. In PowerShell, install the WSL extension:
   ```powershell
   code --install-extension ms-vscode-remote.remote-wsl
   ```
2. In VS Code, `Ctrl+Shift+P` → **WSL: Connect to WSL using Distro...** → the
   distro (`wsl -l -v` lists the names, such as `Ubuntu` or `RHEL9`). The first
   connection installs the VS Code Server in the distro under `~/.vscode-server`.
   The bottom-left corner of the window then shows `WSL: <distro>`.
3. Open a terminal with ``Ctrl+` ``. It is a shell in the distro; do the
   First-Time Setup below in it.
4. After Step 1, **File → Open Folder** → `~/projects/risk-workbench`. Keep the
   repo in the distro's own file system, not under `/mnt/c/`: WSL2 reads files
   on the Windows drive slowly. From a terminal in the distro, `code .` in the
   repo opens the same window.
5. Extensions install either on Windows or in the distro. In the WSL window,
   open Extensions and choose **Install in WSL: \<distro\>** for:
   - Python (`ms-python.python`)
   - Python Debugger (`ms-python.debugpy`)
   - Ruff (`charliermarsh.ruff`): `.vscode/settings.json` formats and fixes
     imports with it on save
   - Better Jinja (`samuelcolvin.jinjahtml`) for `app/templates/`
6. After Step 5 creates `.venv`, VS Code uses `.venv/bin/python`
   (`.vscode/settings.json`). If the status bar shows another interpreter,
   `Ctrl+Shift+P` → **Python: Select Interpreter** → `./.venv/bin/python`.

Each distro has its own VS Code Server and extensions. With both Ubuntu and
RHEL9, install the extensions in each.

---

## First-Time Setup

Do this once. Every step is safe to re-run if something goes wrong.

### Step 1 — Clone the repository and create your env file

In your WSL2 terminal:

```bash
git clone <repo-url> ~/projects/risk-workbench
cd ~/projects/risk-workbench
cp infra/.env.example infra/.env
```

If `git clone` fails with an SSL certificate error, see
[SSL certificate errors](#ssl-certificate-errors-behind-a-corporate-proxy).

Open `infra/.env` and set three values:

```ini
# Generate this with: python3 -c "import secrets; print(secrets.token_hex(32))"
SESSION_SECRET_KEY=<paste 64-char hex string here>

# Must be 8+ chars, upper + lower + digit + symbol (SQL Server requirement)
MSSQL_SA_PASSWORD=<your password here>

# docker or podman
RWB_CONTAINER_RUNTIME=docker
```

Leave everything else as-is for local development.

### Step 2 — Install uv

uv installs and manages Python packages. It replaces pip for this project.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

Verify: `uv --version` should print a version number.

### Step 3 — Install the ODBC Driver 18 for SQL Server

**RHEL9:**

```bash
sudo curl -fsSL https://packages.microsoft.com/config/rhel/9/prod.repo \
    -o /etc/yum.repos.d/mssql-release.repo
sudo ACCEPT_EULA=Y dnf install -y msodbcsql18 unixODBC-devel
```

**Ubuntu:** Microsoft only publishes packages up to Ubuntu 24.04. The 24.04 package
installs and runs correctly on Ubuntu 26.04 and later — use the URL below
regardless of your Ubuntu version.

```bash
# Add Microsoft's signing key
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
    | gpg --dearmor \
    | sudo tee /usr/share/keyrings/microsoft-prod.gpg > /dev/null

# Add the repository — pinned to 24.04 (works on 26.04 too)
curl -fsSL https://packages.microsoft.com/config/ubuntu/24.04/prod.list \
    | sudo tee /etc/apt/sources.list.d/mssql-release.list > /dev/null

sudo apt-get update

# Install the driver and development headers
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev
```

Verify: `odbcinst -j` should show a config file path without errors.

### Step 4 — Install Redis (and Podman)

RHEL9 ships Valkey instead of Redis; `make wsl-start` uses whichever is
installed. Skip `podman` if you use Docker Desktop.

```bash
sudo apt-get install -y redis-server podman  # Ubuntu
sudo dnf install -y valkey podman            # RHEL9
```

Verify: `redis-server --version` or `valkey-server --version`.

### Step 5 — Run first-time setup

```bash
make wsl-setup
```

Runs `uv sync`, `make wsl-start`, creates the three databases, migrates
`rwb_workbench`, seeds it (including the dev admin `admin@example.com`), and
bootstraps `rwb_loss`. It stops on a missing prerequisite. Idempotent; fix the
error and rerun.

### Step 6 — Verify

```bash
make wsl-app
```

Open http://localhost:8000/api/health; each `db_*` and `redis` field should be `ok`.

Then open http://localhost:8000 and sign in with the dev admin:

| Email | Password |
|---|---|
| `admin@example.com` | `Admin1234567!` |

`infra/scripts/seed_db.py` creates this admin only when `APP_ENV=development`,
and the login page shows the password form only when `AUTH_MODE` is `password`
or `both`. Both are the `infra/.env.example` defaults.

### Next

- Users: [USER_PROVISIONING.md](USER_PROVISIONING.md)
- Before changing code: [AGENTS.md](../AGENTS.md)

---

## Daily Workflow

```bash
make wsl-start                              # SQL Server + Redis; waits for SQL Server
make wsl-app                                # terminal 1, :8000
make wsl-poller                             # terminal 2
make wsl-workers && make wsl-worker-logs    # terminal 3
make wsl-stop                               # end of day; data persists
```

---

## Make Command Reference

All commands are in the [Makefile](../Makefile). Run `make help` to list them.

### WSL2 commands

| Command | What it does |
|---|---|
| `make wsl-setup` | First run: `uv sync`, create `data/export_archive`, `data/staging` and `data/shared_drive`, `wsl-start`, create, migrate and seed all 3 databases. Idempotent. |
| `make wsl-start` | Start SQL Server and Redis/Valkey (AOF on); wait for SQL Server. Idempotent. |
| `make wsl-stop` | Stop SQL Server container and Redis. |
| `make wsl-app` | Start uvicorn with live reload on port 8000. |
| `make wsl-workers` | Start every queue's Dramatiq worker in the background (`make wsl-worker QUEUE=<name>` starts one in the foreground). |
| `make wsl-poller` | Start IRP job poller (interval from `POLL_INTERVAL_SECS`, default 15s). |
| `make wsl-test` | Run unit tests (no SQL Server needed, fast). |
| `make wsl-test-sql` | Run SQL Server integration tests. |
| `make wsl-db-bootstrap` | Create the 3 app databases (skips existing). |
| `make wsl-db-migrate` | Run pending Alembic migrations. |
| `make wsl-db-rebuild` | **Destructive.** Drop and recreate all 3 databases (runs `wsl-bootstrap-loss` last). |
| `make wsl-bootstrap-loss` | Apply the dev mirror of CIC's five loss tables and the `stage` schema to `rwb_loss`, then seed `dbo.Client` and `dbo.Lookup_RMS_HistoricalRDS`. Idempotent. |

### Docker-only stack (no WSL2)

Use this only on a machine that cannot run WSL2. It runs the whole Workbench in
Docker Desktop, as the `linux-box` (nginx, uvicorn, Redis, workers, poller) and
`sqlserver` containers.

Create `infra/.env` as in Step 1, then run `make start`; the first build takes a
few minutes. The first time, once SQL Server is up, run `make db-bootstrap` and
`make db-migrate`, then `python scripts/seed_db.py` inside `make shell`, then
`make bootstrap-loss`. Open `http://localhost`. For breakpoints, set
`APP_DEBUG=1` in `infra/.env` and run `make start`; uvicorn then waits for a
debugger on port 5678.

| Command | What it does |
|---|---|
| `make start` | Build and start everything in Docker. |
| `make stop` | Stop all Docker containers. |
| `make logs` | Stream app logs. |
| `make shell` | Open a shell inside the app container. |
| `make test` | Run unit tests inside Docker. |
| `make db-bootstrap` | Create databases inside Docker. |
| `make db-migrate` | Run migrations inside Docker. |
| `make bootstrap-loss` | Set up `rwb_loss` (CIC table mirror, `stage` schema, seeds) inside Docker. |

---

## Database Lifecycle

Three databases are managed by the app: `rwb_workbench`, `rwb_exposure`,
`rwb_loss`. A fourth, DATABRIDGE (Moody's), is never touched by this app.

`rwb_workbench` is managed by Alembic. `alembic/versions/0001_initial.py` is the
base revision, frozen when spec 017 merged (2026-09-29). Never edit it. Every
schema change is a new revision:

```bash
uv run alembic revision -m "add contract broker column" --rev-id NNNN   # one more than `uv run alembic heads`; no database needed
make wsl-db-migrate                                                      # alembic upgrade head (Docker: make db-migrate)
```

Write `upgrade()` and `downgrade()` by hand with `op.*`; `alembic/env.py` has no
model metadata, so autogenerate is not available. Put the kind-table rows a
change needs in the same revision. If the revision changes a table mirrored in
`tests/iteration1_mirror.py`, update the mirror in the same commit, or
`tests/sqlserver/test_schema_drift.py` fails.

`make wsl-db-rebuild` (Docker: `make db-rebuild`) drops all three databases,
replays every revision, seeds, and bootstraps `rwb_loss`. Use it when your dev
data is disposable. It never replaces writing the revision.

`rwb_loss` is not migrated by Alembic. `make db-rebuild` / `make wsl-db-rebuild`
end by running `bootstrap-loss`, which applies `db/bootstrap/loss_dev_mirror.sql`
(CIC's five tables), `db/bootstrap/loss_schema.sql` (the Workbench `stage`
schema and `stage.usp_load_elt_result`), and the two seeds. Run it on its own
after editing either SQL file.

---

## Debugging with VS Code

### Option 1: Live reload (everyday use)

Run `make wsl-app`. uvicorn restarts automatically when you save a Python file.
Use `print()` or `logging.debug()` for quick inspection.

### Option 2: Breakpoint debugger

When you need to pause execution and inspect state:

**Step 1** — Stop `make wsl-app` if running.

**Step 2** — Start uvicorn under debugpy:

```bash
bash -c 'source infra/scripts/wsl-env.sh && \
    uv run python -m debugpy --listen 0.0.0.0:5678 --wait-for-client \
    -m uvicorn app.main:app --host 0.0.0.0 --port 8000'
```

The terminal will hang — it is waiting for VS Code to attach before accepting
any requests.

**Step 3** — In VS Code, open the Run panel (Ctrl+Shift+D), select
**"Attach to uvicorn (debugpy)"**, and press F5.

The terminal will unblock. Set breakpoints in any `app/` file and make a
request in the browser.

**Note:** live reload (`--reload`) is disabled in debugger mode. Save + restart
is not automatic while the debugger is attached.

---

## Testing

### Three tiers

| Tier | Run with | What it needs |
|---|---|---|
| Unit | `make wsl-test` | Nothing — runs offline |
| SQL Server | `make wsl-test-sql` | SQL Server running |
| IRP | `uv run pytest tests/irp --run-irp` | Sandbox IRP credentials |

### Continuous integration

CI runs on GitHub Actions (`.github/workflows/ci.yml`) on every pull request
into `main`, on every push to `main`, and on manual dispatch. Two jobs run in
parallel:

| Job (check name) | What it runs |
|---|---|
| **Unit tests** | `uv sync --frozen` → `make wsl-test` |
| **SQL Server integration tests** | spins up a `mssql/server:2022` service container, installs ODBC Driver 18, then `make wsl-db-bootstrap` → `wsl-db-migrate` → `wsl-db-seed` → `make wsl-test-sql` |

The workflow reuses the same Make targets developers run locally, so there is
no separate CI-only test path. It materializes `infra/.env` from
`infra/.env.example` (the disposable CI SQL Server uses the example's SA
password), so no GitHub Secrets are required. The IRP tier is not in CI yet —
it needs sandbox credentials.

**Branch protection (one-time, needs repo admin).** GitHub only lists a status
check for protection *after* it has run once, so: merge the workflow to `main`
first, then in **Settings → Branches** add a rule for `main` that requires a PR
and requires both `Unit tests` and `SQL Server integration tests` to pass
before merging (plus "require branches up to date").

### Writing a unit test

Unit tests live in `tests/unit/`. They run without a database.

```python
# tests/unit/test_my_module.py

def test_something():
    from app.services.my_service import compute
    assert compute(2, 3) == 5
```

To test SQL logic without SQL Server, use the `sqlite_conn` fixture
(defined in `tests/conftest.py`). It injects an in-memory SQLite engine
and gives you a connection that rolls back after the test:

```python
def test_scope_filter(sqlite_conn):
    from sqlalchemy import text
    row = sqlite_conn.execute(text("SELECT 1 AS n")).mappings().first()
    assert row["n"] == 1
```

### Running one test

```bash
uv run pytest tests/unit/test_db_config.py::TestGetConnectionConfig::test_sql_auth_resolves_server_user_password -v
```

---

## RWB Job Queue & Redis AOF

### New env vars (`infra/.env.example`)

Two env vars control the heartbeat and reconciler timing. Both are in
`infra/.env.example`:

```ini
# Heartbeat: how often the daemon thread stamps rwb_job_heartbeat (seconds)
RWB_HEARTBEAT_INTERVAL_SECS=30

# Reconciler: how old a heartbeat must be before the reconciler reclaims the job.
# Must be a constant multiple of INTERVAL (3-4× is recommended).
# Never set this close to INTERVAL — a transient DB blip must not cause false reclaims.
RWB_HEARTBEAT_STALE_SECS=120
```

These are constants, not per-job durations. The reconciler's stale threshold
is a multiple of the heartbeat interval — never tied to how long a job takes.

### Redis AOF — all environments

**Dev (WSL2 native `redis-server`, `valkey-server` on RHEL9):**
`make wsl-start` starts Redis as:
```
redis-server --appendonly yes --appendfsync everysec --dir /tmp --logfile /tmp/rwb-redis.log
```
On RHEL9 the command is `valkey-server` and the log is `/tmp/rwb-valkey.log`.
The AOF file is under `/tmp`, so it does not survive a reboot of the WSL2 VM.

**Docker-only (`linux-box`):**
There is no separate Redis service. `infra/scripts/start-all.sh` starts
`redis-server --appendonly yes --appendfsync everysec --dir /workspace/.dev-logs`
inside `linux-box`. That directory is not a volume, so the AOF file does not
survive recreating the container.

**Production (RHEL9, Valkey):**
`infra/scripts/deploy/rhel9-start.sh` starts:
```
valkey-server --port 6379 --bind 127.0.0.1 --appendonly yes --appendfsync everysec \
    --dir /var/lib/risk-workbench/valkey --logfile /var/lib/risk-workbench/valkey/valkey.log
```
Leave `auto-aof-rewrite-percentage` and `auto-aof-rewrite-min-size` at
defaults (self-compacting; the AOF file tracks live queue size, not history).

**Verify AOF is active** (`valkey-cli` on RHEL9):
```bash
redis-cli CONFIG GET appendonly   # → appendonly / yes
redis-cli INFO persistence | grep aof_enabled  # → aof_enabled:1
```

### Reconciler

The reconciler is folded into the poller process (`app/poller/run.py`). It
runs on every poller pass (`POLL_INTERVAL_SECS`) and scans for `rwb_job` rows with
`status_code='running'` whose latest `rwb_job_heartbeat.heartbeat_at` is older than
`RWB_HEARTBEAT_STALE_SECS`. For each stale row it atomically resets
`running → pending` and re-enqueues the Dramatiq message.

The reconciler **must run as a single instance** — running two pollers
simultaneously would double-re-enqueue (still safe via the atomic claim, but
wasteful). The single-poller dev topology enforces this naturally.

---

## Troubleshooting

### SSL certificate errors behind a corporate proxy

A proxy that inspects HTTPS traffic (Zscaler, for example) replaces each
site's certificate with one signed by its own root CA. Windows trusts that root
CA; a new WSL2 distro does not, so `git clone`, `curl` and `dnf` fail with a
certificate error.

1. In the distro, check who issued the certificate GitHub presents:

   ```bash
   openssl s_client -connect github.com:443 -servername github.com </dev/null 2>/dev/null | grep -E '^(subject|issuer)='
   ```

   GitHub's own certificate is issued by Sectigo or DigiCert. Any other issuer
   is the proxy; note its name for the next step.

2. In PowerShell on Windows, export the proxy's root CA. Replace `*Zscaler*`
   with the issuer name from step 1:

   ```powershell
   $c = Get-ChildItem Cert:\LocalMachine\Root | Where-Object Subject -like '*Zscaler*' | Select-Object -First 1
   New-Item -ItemType Directory -Force C:\temp | Out-Null
   Export-Certificate -Cert $c -FilePath C:\temp\corp-root.cer -Type CERT
   ```

   If `$c` is empty, search `Cert:\CurrentUser\Root` instead.

3. In the distro, add the root CA to the system trust store:

   ```bash
   sudo openssl x509 -inform der -in /mnt/c/temp/corp-root.cer -out /etc/pki/ca-trust/source/anchors/corp-root.pem
   sudo update-ca-trust extract
   ```

   `git`, `curl`, `dnf` and `uv` then trust the proxy. Continue from
   `git clone` in Step 1.

4. Python's HTTP clients do not read the system trust store. After Step 1
   creates `infra/.env`, add these lines so Risk Modeler calls (`requests`),
   S3 uploads (`boto3`) and Microsoft Graph email (`httpx`) use it:

   ```ini
   REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt
   AWS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt
   SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt
   ```

   The path exists only on RHEL9. Do not add these lines to an `infra/.env`
   that the Docker `linux-box` container also reads.

On Python 3.13 and later, Risk Modeler calls can still fail with
`Basic Constraints of CA cert not marked critical`. Python rejects a root CA
that leaves Basic Constraints non-critical, and the Zscaler root does. Add
`RISK_MODELER_X509_STRICT=false` to `infra/.env` to turn off that check.
Certificates are still verified against `REQUESTS_CA_BUNDLE`. The setting is
for developer machines only; the deployed server does not need it.

### `libodbc.so.2: cannot open shared object file`

The ODBC Driver 18 is not installed. Run Step 3 of First-Time Setup.

### `Login failed for user 'sa'`

The SQL Server container was previously started with a different password than
what is in `infra/.env`. The password is baked into the container's data volume
at first start and does not change when you update `.env`.

Fix (deletes all SQL Server data):
```bash
# Docker
docker compose -f infra/docker-compose.yml --env-file infra/.env down
docker volume rm infra_mssql-data
# Podman
podman rm -f sqlserver
podman volume rm rwb-mssql-data

make wsl-setup
```

### `redis-server: command not found` / `valkey-server: command not found`

Redis (Valkey on RHEL9) is not installed. Run Step 4 of First-Time Setup.

### `uv: command not found`

uv is not installed or not on PATH. Run Step 2 of First-Time Setup, then
`source ~/.bashrc`.

### SQL Server container stuck at "Waiting..."

SQL Server takes 20–30 seconds on first start (it initialises the data files).
If it exceeds 90 seconds, check the container logs:

```bash
docker compose -f infra/docker-compose.yml logs sqlserver | tail -20   # Docker
podman logs --tail 20 sqlserver                                      # Podman
```

Common causes: password complexity failure (must have upper + lower + digit +
symbol, min 8 chars), or port 1433 already in use on the host.

### VS Code debugpy times out

The process is waiting for you to attach before it will serve any requests.
Attach VS Code first (F5 with "Attach to uvicorn (debugpy)"), then open the browser.
