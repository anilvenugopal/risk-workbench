# Developer Playbook
## Risk Workbench — Claude Code + SpecKit on Windows / WSL2

This guide gets a new developer contributing to the Risk Workbench from
Windows or WSL2 using VS Code, Claude Code, and SpecKit.

---

## What You're Setting Up

The Risk Workbench runs as two Docker containers:

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
MSSQL_SA_PASSWORD=YourStr0ng!Password

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
```

### Step 6 — Verify

Open your browser: http://localhost/api/health

You should see: `{"status": "ok", "version": "0.1.0"}`

### Step 7 — Attach VS Code to the container

1. Open VS Code in the `risk-workbench` folder:
   ```powershell
   code .
   ```

2. Press `Ctrl+Shift+P` → "Dev Containers: Attach to Running Container"

3. Select `/risk-workbench-linux-box-1` (or similar name)

4. VS Code opens a new window connected to the container. Your files are
   at `/workspace` inside the container — the same files on your disk,
   mounted as a volume.

5. Open a terminal in VS Code (`Ctrl+Backtick`). You are now inside the
   Linux container — same as if you had SSHed into a Linux server.

---

## Option B: Windows with WSL2 (Recommended for active development)

WSL2 gives you a real Linux shell. VS Code connects to it natively.
This gives faster file system performance and a closer match to production.

### Step 1 — Install WSL2

In PowerShell (as Administrator):
```powershell
wsl --install
```

Reboot when prompted. After reboot, WSL2 will finish installing Ubuntu.
Set a username and password when asked.

### Step 2 — Install Docker Desktop with WSL2 backend

1. Install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)
2. In Docker Desktop → Settings → Resources → WSL Integration:
   - Enable "Ubuntu" (your WSL2 distro)
3. Confirm in your WSL2 terminal: `docker version` should work.

### Step 3 — Install VS Code and the WSL extension

1. Install [VS Code on Windows](https://code.visualstudio.com/)
2. Install the WSL extension: `code --install-extension ms-vscode-remote.remote-wsl`

### Step 4 — Open your project in WSL2

In VS Code, press `Ctrl+Shift+P` → "WSL: New Window"

Or from your WSL2 terminal:
```bash
cd ~/projects
git clone <repo-url> risk-workbench
cd risk-workbench
code .
```

VS Code opens connected to WSL2. All terminals in VS Code are now Linux shells.

### Step 5 — Install Python toolchain in WSL2

In your VS Code terminal (which is now a Linux shell):

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc   # or close and reopen terminal
```

### Step 6 — Configure environment

```bash
cp infra/.env.example infra/.env
```

Edit `infra/.env`. Key changes for WSL2 native mode:
```ini
# In native WSL2 mode, SQL Server is on localhost (port is mapped from container)
MSSQL_WORKBENCH_SERVER=localhost
MSSQL_EXPOSURE_SERVER=localhost
MSSQL_LOSS_SERVER=localhost
```

### Step 7 — Install Python dependencies

```bash
make native-install
```

### Step 8 — Start SQL Server only

```bash
make sqlserver-up
```

### Step 9 — Bootstrap and migrate

```bash
python scripts/bootstrap_db.py
alembic upgrade head
```

### Step 10 — Start development processes

Open 3 terminals in VS Code (`Ctrl+Backtick`, then split):

```bash
# Terminal 1
make native-dev       # uvicorn --reload

# Terminal 2
make native-worker    # dramatiq worker

# Terminal 3
make native-poller    # IRP poller
```

Open http://localhost:8000/api/health — you should see `{"status": "ok", ...}`

---

## Claude Code Setup

Claude Code is used for all feature development via SpecKit.

### Install Claude Code

```bash
# In your WSL2 terminal (Option B) or PowerShell (Option A)
npm install -g @anthropic/claude-code
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
| `/speckit-implement` | Implement the next task |
| `/speckit-analyze` | Constitution compliance check |

### Starting a new feature

In the Claude Code chat (VS Code panel or terminal):

```
/speckit-specify "Add a submission history page that lists all past 
submissions for the current customer, with status and date"
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
make dev-up              # Start full Docker stack
make dev-down            # Stop everything (data preserved)
make sqlserver-up        # Start SQL Server only (WSL2 native mode)

# Logs
make logs                # uvicorn log stream
make logs-worker         # dramatiq worker log
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

# Debug
make debug-up            # Start with debugpy on :5678
```

---

## Debugging with VS Code

### Quick: live reload

Just run `make dev-up` (Docker) or `make native-dev` (WSL2). Save a file;
uvicorn picks it up in under a second. Use `print()` or `logging` for quick
inspection.

### Full: breakpoint debugging

1. Run `make debug-up` (this restarts the stack with `APP_DEBUG=1`)
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

Commit messages follow Conventional Commits:
- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation only
- `refactor:` — code refactor, no behavior change
- `test:` — tests only

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
(container) or `~/.venv/bin/python` (WSL2).

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
