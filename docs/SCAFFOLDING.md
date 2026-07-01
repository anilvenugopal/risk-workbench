# Risk Workbench — Developer Setup Guide

---

## About uv (the Python package manager)

This project uses **uv** to manage Python dependencies. It is not a runtime
dependency and has nothing to do with production. It is a faster replacement
for `pip` + `venv` — same Python packages, same result, no compliance exposure.

**uv is a developer tool only.** Production runs plain Python with a virtual
environment. Procurement, legal, and compliance will never see it.

If your organisation bans uv, replace `uv run <cmd>` with `.venv/bin/<cmd>`
and `uv sync` with `pip install -r requirements.txt`. The packages are identical.

---

## Environment Topology

```
WSL2 Development                          Production (Linux server)
─────────────────────────────────         ──────────────────────────────
  nginx          (not needed in dev)       nginx          (systemd)
  uvicorn        make wsl-app        ≡    uvicorn        (systemd)
  redis-server   make wsl-start           redis-server   (systemd)
  dramatiq       make wsl-worker          dramatiq       (systemd)
  poller         make wsl-poller          poller         (systemd)

  SQL Server ─── Docker container   ≡    SQL Server ─── separate host
```

The same five processes run in development and production. In development they
are started manually (one terminal each). In production they run as systemd
services. The commands are identical — only the launcher changes.

Docker is used in development **only for SQL Server**. Everything else runs
directly in your WSL2 shell. Your partner (Windows, no WSL2) runs everything
including the app inside Docker — see [PARTNER_PLAYBOOK.md](PARTNER_PLAYBOOK.md).

---

## What You Need Before Starting

- WSL2 running Ubuntu 22.04 or 24.04
- Docker Desktop for Windows with the WSL2 integration enabled
- VS Code with the WSL extension (`ms-vscode-remote.remote-wsl`)
- Git

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

Open `infra/.env` and set two values:

```ini
# Generate this with: python3 -c "import secrets; print(secrets.token_hex(32))"
SESSION_SECRET_KEY=<paste 64-char hex string here>

# Must be 8+ chars, upper + lower + digit + symbol (SQL Server requirement)
MSSQL_SA_PASSWORD=<your password here>
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

pyodbc (the Python SQL Server library) requires this driver to be installed
on the system. This is a Microsoft package, installed via apt.

Microsoft only publishes packages up to Ubuntu 24.04. The 24.04 package
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

### Step 4 — Install Redis

Redis is the message broker for Dramatiq background workers. In production it
is installed the same way on the Linux server.

```bash
sudo apt-get install -y redis-server
```

Verify: `redis-server --version` should print a version number.

### Step 5 — Install Python dependencies

```bash
cd ~/projects/risk-workbench
uv sync
```

This creates `.venv/` in the project directory and installs all Python packages
listed in `pyproject.toml`. Takes 1–2 minutes on first run.

Verify: `uv run python --version` should print Python 3.12 or later.

### Step 6 — Start SQL Server

```bash
make wsl-start
```

This starts the SQL Server Docker container and Redis. SQL Server takes about
30 seconds to be ready on first start. The command waits for it automatically.

### Step 7 — Create databases and run migrations

```bash
bash infra/scripts/wsl-setup.sh
```

This is the automated setup script. Steps 2–5 above are prerequisites for it.
Once those are done, the script handles:
- Confirming SQL Server is ready
- Creating `rwb_workbench`, `rwb_exposure`, `rwb_loss` (skips if they exist)
- Running Alembic migrations on `rwb_workbench`

If it fails, read the error, fix it, and run it again — every step is idempotent.

### Step 8 — Verify

```bash
make wsl-app
```

Open http://localhost:8000/api/health in a browser.
You should see: `{"status": "ok", "version": "0.1.0"}`

---

## Daily Workflow

```
Terminal 1          Terminal 2       Terminal 3        Terminal 4
──────────────      ────────────     ─────────────     ────────────
make wsl-start      make wsl-app     make wsl-worker   make wsl-poller
(infrastructure)    (web app)        (bg workers)      (IRP poller)
```

In the morning, open 4 terminals and run one command in each. That's it.

```bash
# End of day — stop SQL Server container and Redis
make wsl-stop
```

SQL Server data persists in the Docker volume across restarts.

---

## Make Command Reference

All commands are in the [Makefile](../Makefile). Run `make help` to list them.

### WSL2 commands (your daily use)

| Command | What it does |
|---|---|
| `make wsl-start` | Start SQL Server (Docker) + Redis. Idempotent. |
| `make wsl-stop` | Stop SQL Server container and Redis. |
| `make wsl-app` | Start uvicorn with live reload on port 8000. |
| `make wsl-worker` | Start Dramatiq background worker. |
| `make wsl-poller` | Start IRP job poller (polls every 30s). |
| `make wsl-test` | Run unit tests (no SQL Server needed, fast). |
| `make wsl-test-sql` | Run SQL Server integration tests. |
| `make wsl-db-bootstrap` | Create the 3 app databases (skips existing). |
| `make wsl-db-migrate` | Run pending Alembic migrations. |
| `make wsl-db-rebuild` | **Destructive.** Drop and recreate all 3 databases. |

### Docker commands (partner / Windows users)

| Command | What it does |
|---|---|
| `make start` | Build and start everything in Docker. |
| `make stop` | Stop all Docker containers. |
| `make logs` | Stream app logs. |
| `make shell` | Open a shell inside the app container. |
| `make test` | Run unit tests inside Docker. |
| `make db-bootstrap` | Create databases inside Docker. |
| `make db-migrate` | Run migrations inside Docker. |

---

## Database Lifecycle

Three databases are managed by the app: `rwb_workbench`, `rwb_exposure`,
`rwb_loss`. A fourth, DATABRIDGE (Moody's), is never touched by this app.

Before any iteration that changes the schema, choose one:

| Option | When | Command |
|---|---|---|
| **Rebuild** | Schema changed — drop all 3 and recreate | `make wsl-db-rebuild` |
| **Migrate** | Schema changed but data must be kept | `make wsl-db-migrate` |
| **Skip** | No schema change | nothing |

In dev we use Rebuild (drop-create-seed) rather than accumulating Alembic
revisions. There is one revision (`0001_initial.py`) which is amended in place
until production cutover.

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
| IRP | `make wsl-test-irp` | Sandbox IRP credentials |

Default CI runs unit tests only.

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

## Troubleshooting

### `libodbc.so.2: cannot open shared object file`

The ODBC Driver 18 is not installed. Run Step 3 of First-Time Setup.

### `Login failed for user 'sa'`

The SQL Server container was previously started with a different password than
what is in `infra/.env`. The password is baked into the container's data volume
at first start and does not change when you update `.env`.

Fix:
```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env down
docker volume rm infra_mssql-data
make wsl-start
make wsl-db-bootstrap
make wsl-db-migrate
```

### `redis-server: command not found`

Redis is not installed. Run Step 4 of First-Time Setup.

### `uv: command not found`

uv is not installed or not on PATH. Run Step 2 of First-Time Setup, then
`source ~/.bashrc`.

### SQL Server container stuck at "Waiting..."

SQL Server takes 20–30 seconds on first start (it initialises the data files).
If it exceeds 90 seconds, check the container logs:

```bash
docker compose -f infra/docker-compose.yml logs sqlserver | tail -20
```

Common causes: password complexity failure (must have upper + lower + digit +
symbol, min 8 chars), or port 1433 already in use on the host.

### VS Code debugpy times out

The process is waiting for you to attach before it will serve any requests.
Attach VS Code first (F5 with "Attach to uvicorn (debugpy)"), then open the browser.
