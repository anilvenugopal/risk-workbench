# Developer Playbook
## Risk Workbench — Claude Code + SpecKit on Windows / WSL2

**Use this when** you set up VS Code, Claude Code and SpecKit, or develop with
Docker only, without WSL2 (Option A).
**Not this:** the WSL2 setup steps are in [LOCAL_DEV_SETUP.md](LOCAL_DEV_SETUP.md);
Option B below hands off to it. Deploying to the server →
[deploy/RHEL9_QUICKSTART.md](deploy/RHEL9_QUICKSTART.md).

---

## What You're Setting Up

Option B (WSL2, recommended) runs only SQL Server in a container and everything
else in your WSL2 shell. Option A runs the Workbench as two Docker containers:

```
┌─ linux-box ──────────────────────────┐   ┌─ sqlserver ──────────────┐
│ nginx, uvicorn, Redis, workers, poller│   │ SQL Server 2022 Dev       │
└──────────────────────────────────────┘   └──────────────────────────┘
```

Your editor (VS Code) attaches directly to the `linux-box` container.
When you edit a Python file, uvicorn inside the container reloads it instantly.
You never rebuild the Docker image to pick up code changes.

---

## Option A: Pure Windows (Docker Desktop, no WSL2)

Use this if you don't have WSL2 or prefer not to set it up.

### Step 1 — Install prerequisites

1. [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)
   - During install, select "Use WSL 2 based engine" if offered — but it is not
     required for this setup.
   - After install, confirm it works: open a terminal and run `docker version`.

2. [VS Code](https://code.visualstudio.com/)

3. Install VS Code extensions (one-time, via Extensions panel or command line):
   ```
   code --install-extension ms-vscode-remote.remote-containers
   code --install-extension ms-python.python
   code --install-extension ms-python.debugpy
   code --install-extension charliermarsh.ruff
   code --install-extension samuelcolvin.jinjahtml
   code --install-extension github.vscode-pull-request-github
   ```

4. [Git for Windows](https://gitforwindows.org/) — choose "Use from Windows
   Command Prompt" during install.

### Step 2 — Clone the repository

Open a terminal (Command Prompt or PowerShell):

```powershell
git clone <repo-url> risk-workbench
cd risk-workbench
```

### Step 3 — Configure environment

```powershell
copy infra\.env.example infra\.env
```

Open `infra\.env` in Notepad and fill in:

```ini
# Generate a random secret key (run in Python if available, or use any 64-char hex string)
SESSION_SECRET_KEY=<64-char-hex-string>

# SQL Server password — must be strong (upper+lower+digit+symbol, min 8 chars)
MSSQL_SA_PASSWORD=<>

# Leave everything else as-is for local dev
```

### Step 4 — Start the stack

In your terminal (from the `risk-workbench` folder):

```powershell
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d --build
```

This builds the `linux-box` image (takes 3–5 minutes the first time — it installs
system packages) and starts both containers. Subsequent starts are fast.

Watch until SQL Server is healthy:
```powershell
docker compose -f infra/docker-compose.yml logs -f sqlserver
# Wait until you see: "SQL Server is now ready for client connections"
```

### Step 5 — Bootstrap databases (first time only)

```powershell
docker compose -f infra/docker-compose.yml exec linux-box python scripts/bootstrap_db.py
docker compose -f infra/docker-compose.yml exec linux-box alembic upgrade head
docker compose -f infra/docker-compose.yml exec linux-box python scripts/seed_db.py
docker compose -f infra/docker-compose.yml exec linux-box python scripts/bootstrap_loss.py
```

### Step 6 — Verify

Open your browser: http://localhost/api/health

You should see JSON with `status`, `db_workbench`, `db_exposure`, `db_loss`,
`redis` and `env` keys.

### Step 7 — Attach VS Code to the container

1. Open VS Code in the `risk-workbench` folder:
   ```powershell
   code .
   ```

2. Press `Ctrl+Shift+P` → "Dev Containers: Attach to Running Container"

3. Select `/infra-linux-box-1` (or similar name)

4. VS Code opens a new window connected to the container. `app/`, `alembic/`,
   `db/`, `deploy/` and `tests/` are mounted at `/workspace/<name>`, and
   `infra/scripts/` at `/workspace/scripts` — the same files on your disk.
   Other files (`docs/`, `specs/`, `Makefile`, `pyproject.toml`) are copied
   into the image at build time or not present.

5. Open a terminal in VS Code (`Ctrl+Backtick`). You are now inside the
   Linux container — same as if you had SSHed into a Linux server.

Next: add users with [USER_PROVISIONING.md](USER_PROVISIONING.md) (the dev admin
`admin@example.com` is already seeded), then [Claude Code Setup](#claude-code-setup).

---

## Option B: Windows with WSL2 (Recommended for active development)

WSL2 gives you a real Linux shell. VS Code connects to it natively.
This gives faster file system performance and a closer match to production.
Use Ubuntu or RHEL9; RHEL9 matches the deployed server.

### Step 1 — Install a WSL2 distro

**Ubuntu:** in PowerShell (as Administrator):
```powershell
wsl --install
```

Reboot when prompted. After reboot, WSL2 will finish installing Ubuntu.
Set a username and password when asked.

**RHEL9:** follow [RHEL9_WSL_INSTALL.md](RHEL9_WSL_INSTALL.md).
It ends with a personal account in a distro named `RHEL9`.

### Step 2 — Install Docker Desktop with WSL2 backend (Docker only)

Skip this step if you run SQL Server under Podman (the client's choice), which
[LOCAL_DEV_SETUP.md](LOCAL_DEV_SETUP.md) Step 4 installs on either distro.

1. Install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)
2. In Docker Desktop → Settings → Resources → WSL Integration:
   - Enable your WSL2 distro (`Ubuntu` or `RHEL9`)
3. Confirm in your WSL2 terminal: `docker version` should work.

### Step 3 — Install VS Code and the WSL extension

1. Install [VS Code on Windows](https://code.visualstudio.com/)
2. Install the WSL extension: `code --install-extension ms-vscode-remote.remote-wsl`

### Step 4 — Open VS Code in your distro

In VS Code, press `Ctrl+Shift+P` → "WSL: Connect to WSL using Distro..." and
pick `Ubuntu` or `RHEL9`. "WSL: New Window" opens the default distro, which is
Ubuntu if you installed it first.

VS Code opens connected to WSL2. All terminals in VS Code are now Linux shells.

### Step 5 — First-time setup

Follow [LOCAL_DEV_SETUP.md](LOCAL_DEV_SETUP.md) First-Time Setup. It clones the repo,
installs uv, the ODBC driver, Redis (Valkey on RHEL9) and Podman, then runs
`make wsl-setup`, which starts SQL Server and Redis and creates, migrates, and
seeds all three databases.

### Step 6 — Start development processes

Follow [LOCAL_DEV_SETUP.md](LOCAL_DEV_SETUP.md#daily-workflow) Daily Workflow:
`make wsl-start`, then `make wsl-app`, `make wsl-poller` and `make wsl-workers`
in their own terminals.

---

## Claude Code Setup

Claude Code is used for all feature development via SpecKit.

### Install Claude Code

```bash
# In your WSL2 terminal (Option B) or PowerShell (Option A)
npm install -g @anthropic-ai/claude-code
```

Or install the VS Code extension:
- VS Code Extensions → Search "Claude Code" → Install

### Authenticate

```bash
claude auth login
```

Follow the prompts to authenticate with your Anthropic account.

### Verify

```bash
claude --version
```

### Claude Code in VS Code

Once the extension is installed, you'll see a Claude icon in the VS Code
sidebar. Click it to open the Claude Code panel. You can also open it
with `Ctrl+Shift+P` → "Claude: Open Chat".

**Important:** Always open VS Code connected to WSL2 (Option B) or attached
to the container (Option A) before using Claude Code. Claude Code reads
the files in your current workspace — it needs to see the actual code.

---

## SpecKit Workflow

SpecKit is the AI-assisted development workflow used in this project.
All features are developed through Claude Code using SpecKit commands.

### How it works

1. **Specify** — describe a feature in natural language
2. **Plan** — Claude researches the codebase and creates an implementation plan
3. **Tasks** — Claude breaks the plan into tasks
4. **Implement** — Claude implements each task, guided by the constitution

### Commands

All SpecKit commands start with `/speckit-` in Claude Code:

| Command | What it does |
|---|---|
| `/speckit-specify "description"` | Create a feature specification |
| `/speckit-plan` | Research + create implementation plan |
| `/speckit-tasks` | Break plan into concrete tasks |
| `/speckit-implement` | Implement the tasks in `tasks.md` |
| `/speckit-analyze` | Check spec.md, plan.md and tasks.md for consistency and constitution violations |

### Starting a new feature

In the Claude Code chat (VS Code panel or terminal):

```
/speckit-specify "Add a submission history page that lists all past 
submissions assigned to the current analyst, with status and date"
```

Claude will ask clarifying questions, then generate a spec in `specs/`.

Then follow the SpecKit workflow:
```
/speckit-plan
/speckit-tasks
/speckit-implement
```

### The constitution

The project has a constitution at `.specify/memory/constitution.md`.
It defines 13 architectural rules that Claude Code enforces during implementation.
You don't need to read it to contribute, but if Claude raises a constitution
violation, it means the proposed implementation breaks an architectural rule —
discuss it rather than overriding.

---

## Daily Commands Reference

```bash
# Start / stop
make start              # Start full Docker stack
make stop               # Stop everything (data preserved)
make wsl-start          # Start SQL Server + Redis only (WSL2 native mode)
make wsl-setup          # WSL2 first run: start, create, migrate, seed all 3 databases

# Logs
make logs                # uvicorn log stream
make logs-worker QUEUE=upload_edm   # one queue's dramatiq worker log
make logs-poller         # poller log

# Shell access
make shell               # bash inside linux-box

# Database
make db-bootstrap        # Create 3 app databases (first time only)
make db-migrate          # Run alembic upgrade head
make db-rebuild          # DESTRUCTIVE: drop + recreate + migrate + seed

# Tests
make test                # Unit tests (fast, no SQL Server)
make test-sql            # SQL Server integration tests
make lint                # ruff linter
make format              # ruff formatter

# Debug: set APP_DEBUG=1 in infra/.env, then make start (debugpy on :5678)
```

---

## Debugging with VS Code

### Quick: live reload

Just run `make start` (Docker) or `make wsl-app` (WSL2). Save a file;
uvicorn picks it up in under a second. Use `print()` or `logging` for quick
inspection.

### Full: breakpoint debugging

1. Set `APP_DEBUG=1` in `infra/.env` and run `make start` (this recreates
   `linux-box`; `infra/scripts/start-all.sh` then starts uvicorn under debugpy)
2. Open VS Code Run panel (Ctrl+Shift+D)
3. Select "Attach to uvicorn (debugpy)"
4. Press F5
5. VS Code attaches. The app will now serve requests.
6. Set a breakpoint in any `app/` file
7. Make a request in the browser — execution pauses at your breakpoint

**Note:** `--reload` is disabled in debug mode (incompatible with debugpy).
You'll need to restart the container to pick up code changes while debugging.

---

## Git Workflow

```bash
# Always work on a feature branch
git checkout -b 001-my-feature

# Make changes, test
make test

# Commit
git add app/...
git commit -m "feat: my feature description"

# Push and open a PR
git push -u origin 001-my-feature
gh pr create
```

Commit messages follow AGENTS.md: a subject of at most 72 characters, a body
that says why, and no AI attribution lines.

---

## Troubleshooting

### "Cannot connect to Docker daemon"

Docker Desktop is not running. Open Docker Desktop from the Windows Start menu
and wait for it to finish starting (the whale icon in the system tray stops
animating).

### "Port 1433 already in use"

You have SQL Server installed on Windows itself. Change the mapped port:
In `infra/docker-compose.yml`, change `"1433:1433"` to `"1434:1433"`,
then update all `MSSQL_*_PORT` values in `infra/.env` to `1434`.

### VS Code can't find Python interpreter

In VS Code (connected to WSL2 or container), open the command palette:
`Python: Select Interpreter` → choose `/workspace/.venv/bin/python`
(container) or `<repo>/.venv/bin/python` (WSL2).

### "Module not found" errors in tests

You're running pytest from outside the virtual environment. In the container:
```bash
make shell
pytest tests/unit -v
```
In WSL2:
```bash
source .venv/bin/activate
pytest tests/unit -v
```

### `make` not found on Windows

The Makefile targets require a Linux shell. Use WSL2 or attach to the
linux-box container (`make shell`) and run commands from there.

Alternatively, look up the command in the Makefile and run it directly
with `docker compose exec linux-box <command>`.

### Claude Code says "constitution violation"

The project has architectural rules (the constitution at
`.specify/memory/constitution.md`). When Claude flags a violation, it means
the proposed implementation breaks a rule. Don't bypass it — bring it up
with the lead developer. The constitution can be amended, but only
intentionally.
