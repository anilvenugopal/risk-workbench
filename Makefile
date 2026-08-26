# Makefile — Risk Analysis Workbench
#
# Two developer setups, same targets where possible:
#
#   DOCKER (your partner, Windows)
#     All processes run inside the linux-box container alongside SQL Server.
#     One command starts everything. Nothing to install locally.
#     Targets: start, stop, logs, shell, db-*, test, lint
#
#   WSL2 (you)
#     SQL Server runs in Docker. Everything else (app, Redis, workers, poller)
#     runs directly in your WSL2 shell — same processes as Docker, no container
#     overhead. Faster reload and debugger attach.
#     Targets: wsl-setup, wsl-start, wsl-stop, wsl-db-*, wsl-test, lint
#
# Production uses the same commands as wsl-* but via systemd units.
# See infra/scripts/start-all.sh for the mapping.

.PHONY: help \
        start stop logs shell \
        db-bootstrap db-migrate db-rebuild \
        test test-sql lint format \
        wsl-setup wsl-start wsl-stop \
        wsl-db-bootstrap wsl-db-migrate wsl-db-seed wsl-db-rebuild \
        wsl-test wsl-test-sql \
        wsl-user-setup \
        irp-pypi irp-testpypi irp-local irp-status \
        _irp-hide-local _irp-show-local

COMPOSE     = docker compose -f infra/docker-compose.yml --env-file infra/.env
BOX         = $(COMPOSE) exec linux-box

# Path to the optional local editable irp-integration checkout (only used by
# `make irp-local`). Relative to this repo root; matches [tool.uv.sources].
IRP_LOCAL_PATH = ../../IRP/irp-integration

# ── Help ──────────────────────────────────────────────────────────────────────
help:   ## Show all targets
	@grep -E '^[a-zA-Z_-]+:.*## .*$$' $(MAKEFILE_LIST) \
	    | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ══ DOCKER TARGETS ════════════════════════════════════════════════════════════
# Use these if you are on Windows or prefer Docker for everything.

start:   ## [Docker] Build and start all services (linux-box + sqlserver)
	$(COMPOSE) up -d --build
	@echo ""
	@echo "  App:   http://localhost"
	@echo "  Logs:  make logs"
	@echo "  Shell: make shell"

stop:   ## [Docker] Stop all services (data volumes preserved)
	$(COMPOSE) down

logs:   ## [Docker] Stream logs from the app container
	$(COMPOSE) logs -f linux-box

logs-worker:   ## [Docker] Stream one queue's dramatiq worker log (usage: make logs-worker QUEUE=upload_edm)
	@test -n "$(QUEUE)" || (echo "Usage: make logs-worker QUEUE=<job_type>  (see: make wsl-worker-list)" && exit 1)
	$(BOX) tail -f /workspace/.dev-logs/worker-$(QUEUE).log

logs-poller:   ## [Docker] Stream poller log
	$(BOX) tail -f /workspace/.dev-logs/poller.log

shell:   ## [Docker] Open a bash shell inside the app container
	$(BOX) bash

db-bootstrap:   ## [Docker] Create the 3 app databases (run once on first start)
	$(BOX) python infra/scripts/bootstrap_db.py

db-migrate:   ## [Docker] Run pending Alembic migrations on WORKBENCH
	$(BOX) alembic upgrade head

db-rebuild:   ## [Docker] DESTRUCTIVE — drop and recreate all 3 app databases
	@echo ""
	@echo "  WARNING: drops rwb_workbench, rwb_exposure, rwb_loss — all data lost."
	@echo ""
	@read -p "  Type 'yes' to confirm: " C && [ "$$C" = "yes" ]
	$(BOX) python infra/scripts/reset_db.py --all
	$(BOX) alembic upgrade head
	$(BOX) python infra/scripts/seed_db.py

test:   ## [Docker] Run unit tests (no SQL Server needed)
	$(BOX) uv run pytest tests/unit -v

test-sql:   ## [Docker] Run SQL Server integration tests
	$(BOX) uv run pytest tests/sqlserver -v --run-sqlserver

lint:   ## [Docker] Run ruff linter
	$(BOX) uv run ruff check .

format:   ## [Docker] Run ruff formatter
	$(BOX) uv run ruff format .

# ══ WSL2 TARGETS ══════════════════════════════════════════════════════════════
# Use these for day-to-day development in WSL2.
# SQL Server runs in Docker. Everything else (app, Redis, workers, poller)
# runs directly in your WSL2 shell — same processes as production.
#
# All env loading and idempotency logic lives in infra/scripts/*.sh, not here.
# Makefile targets are thin dispatchers only — no secrets, no env parsing.
#
# First time: make wsl-setup
# Every day:  make wsl-start  →  make wsl-app / wsl-worker / wsl-poller

wsl-setup:   ## [WSL2] Create databases and run migrations (after manual system installs)
	@echo "  Prerequisites: uv, ODBC Driver 18, Redis must be installed first."
	@echo "  See docs/SCAFFOLDING.md Steps 2-4 if this is your first time."
	@echo ""
	bash infra/scripts/wsl-setup.sh

wsl-start:   ## [WSL2] Start SQL Server + Redis (idempotent — safe if already running)
	@bash infra/scripts/wsl-start.sh

wsl-stop:   ## [WSL2] Stop SQL Server container and Redis
	$(COMPOSE) stop sqlserver
	redis-cli shutdown nosave 2>/dev/null || true
	@echo "Stopped."

wsl-app:   ## [WSL2] Start the web app (uvicorn with live reload on :8000)
	@bash -c 'source infra/scripts/wsl-env.sh && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload'

wsl-worker:   ## [WSL2] Start one queue's Dramatiq worker (usage: make wsl-worker QUEUE=upload_edm)
	@test -n "$(QUEUE)" || (echo "Usage: make wsl-worker QUEUE=<job_type>  (see: make wsl-worker-list)" && exit 1)
	@bash -c 'source infra/scripts/wsl-env.sh && uv run dramatiq app.workers.entrypoint -Q "$(QUEUE)" --processes "$${RWB_WORKER_PROCESSES:-1}" --threads "$${RWB_WORKER_THREADS:-2}"'

wsl-worker-list:   ## [WSL2] List available queue names for `make wsl-worker QUEUE=...`
	@bash -c 'source infra/scripts/wsl-env.sh && uv run python -m app.workers.queues'

wsl-worker-health:   ## [WSL2] Report live/dead status of every queue's worker (PID file + process scan)
	@bash infra/scripts/wsl-worker-health.sh

wsl-poller:   ## [WSL2] Start the IRP job poller
	@bash -c 'source infra/scripts/wsl-env.sh && uv run python -m app.poller.run --loop'

wsl-db-bootstrap:   ## [WSL2] Create the 3 app databases (safe to re-run — skips existing)
	@bash -c 'source infra/scripts/wsl-env.sh && uv run python infra/scripts/bootstrap_db.py'

wsl-db-migrate:   ## [WSL2] Run pending Alembic migrations on WORKBENCH
	@bash -c 'source infra/scripts/wsl-env.sh && uv run alembic upgrade head'

wsl-db-seed:   ## [WSL2] Seed kind tables + dev admin (idempotent MERGE — safe to re-run)
	@bash -c 'source infra/scripts/wsl-env.sh && uv run python infra/scripts/seed_db.py'

wsl-db-rebuild:   ## [WSL2] DESTRUCTIVE — drop and recreate all 3 app databases
	@echo ""
	@echo "  WARNING: drops rwb_workbench, rwb_exposure, rwb_loss — all data lost."
	@echo ""
	@read -p "  Type 'yes' to confirm: " C && [ "$$C" = "yes" ]
	@bash -c 'source infra/scripts/wsl-env.sh && uv run python infra/scripts/reset_db.py --all'
	@bash -c 'source infra/scripts/wsl-env.sh && uv run alembic upgrade head'
	@bash -c 'source infra/scripts/wsl-env.sh && uv run python infra/scripts/seed_db.py'

wsl-test:   ## [WSL2] Run unit tests (no SQL Server needed)
	uv run pytest tests/unit -v

wsl-test-sql:   ## [WSL2] Run SQL Server integration tests
	@bash -c 'source infra/scripts/wsl-env.sh && uv run pytest tests/sqlserver -v --run-sqlserver'

wsl-user-setup:   ## [WSL2] Interactive user provisioning CLI (provision, create, reset password)
	@bash infra/scripts/run_user_setup

# ══ irp-integration SOURCE SWITCHING ══════════════════════════════════════════
# Flip which source irp-integration resolves from, then re-sync. The choice is
# sticky (written to default-groups in pyproject.toml), so every later `uv run` /
# `uv sync` uses it. Commit the pyproject/uv.lock change only if you want CI/prod
# on that source (the committed default is PyPI). Run on the host (WSL2/native);
# Docker users re-`make start` afterwards to rebuild the image.
#
# The irp-local EDITABLE PATH source in [tool.uv.sources] is COMMENTED OUT by
# default so machines that only use PyPI/TestPyPI (CI, prod, teammates without
# the checkout) are never forced to have the local clone present just to
# re-resolve the lock. `make irp-local` uncomments it (and requires the clone);
# the other two targets re-comment it to keep the committed default portable.
#   _irp-hide-local  → comment the path line out (idempotent)
#   _irp-show-local  → uncomment the path line     (idempotent)
_irp-hide-local:
	@sed -i 's|^\(    \){ path = "$(IRP_LOCAL_PATH)"|\1# { path = "$(IRP_LOCAL_PATH)"|' pyproject.toml
_irp-show-local:
	@sed -i 's|^\(    \)# { path = "$(IRP_LOCAL_PATH)"|\1{ path = "$(IRP_LOCAL_PATH)"|' pyproject.toml

irp-pypi:   ## irp-integration → PyPI (latest stable; production default) + re-sync
	@$(MAKE) --no-print-directory _irp-hide-local
	@sed -i 's/^default-groups = .*/default-groups = ["dev", "irp-pypi"]/' pyproject.toml
	uv --upgrade-package irp-integration
	@$(MAKE) --no-print-directory irp-status

irp-testpypi:   ## irp-integration → TestPyPI (newest pre-release build) + re-sync
	@$(MAKE) --no-print-directory _irp-hide-local
	@sed -i 's/^default-groups = .*/default-groups = ["dev", "irp-testpypi"]/' pyproject.toml
	uv sync --upgrade-package irp-integration
	@$(MAKE) --no-print-directory irp-status

irp-local:   ## irp-integration → your local editable checkout + re-sync
	@test -d "$(IRP_LOCAL_PATH)" || { \
	    echo "ERROR: local checkout not found at $(IRP_LOCAL_PATH)"; \
	    echo "       Clone it there first:  git clone <url> $(IRP_LOCAL_PATH)"; \
	    exit 1; }
	@$(MAKE) --no-print-directory _irp-show-local
	@sed -i 's/^default-groups = .*/default-groups = ["dev", "irp-local"]/' pyproject.toml
	uv sync
	@$(MAKE) --no-print-directory irp-status

irp-status:   ## Show which irp-integration source/version is currently active
	@grep -E '^default-groups' pyproject.toml | sed 's/^/  mode:  /'
	@uv run --no-sync python -c "import irp_integration as m; print('  version:', getattr(m, '__version__', '?')); print('  path:   ', m.__file__)" 2>/dev/null \
	    || echo "  (run a sync target first: make irp-pypi | irp-testpypi | irp-local)"
