<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->

# Risk Analysis Workbench — Claude Code Context

## Source of Truth Documents

Read these before any implementation work:

- [docs/PRD.md](docs/PRD.md) — product requirements, feature scope, iteration roadmap
- [docs/DATA_MODEL.md](docs/DATA_MODEL.md) — canonical entity and relationship definitions
- [.specify/memory/constitution.md](.specify/memory/constitution.md) — 13 architectural rules (v1.1.0); all compliance gates

## Development Environment

**Two containers:**
- `linux-box` — runs nginx, uvicorn, redis, dramatiq workers, poller (mirrors production Linux server)
- `sqlserver` — SQL Server 2022 Developer edition (mirrors separate SQL Server instance in prod)

**Key commands:**
```bash
make dev-up          # start full Docker stack (partner / Windows)
make sqlserver-up    # start SQL Server only (WSL2 native mode)
make native-dev      # uvicorn --reload natively in WSL2
make shell           # bash inside linux-box
make db-rebuild      # DESTRUCTIVE: drop/recreate 3 app DBs + migrate + seed
make test            # unit tests
make test-sql        # SQL Server integration tests (--run-sqlserver)
make debug-up        # start with debugpy on :5678 for VS Code attach
```

See [docs/SCAFFOLDING.md](docs/SCAFFOLDING.md) for full setup and debugging tutorial.

## Architecture Rules (Summary)

Full rules in the constitution. Key points for implementation:

1. **Data access**: all SQL through `db/` package. Safe path: `db.execute()`, `db.scoped_execute()`. Trusted-script path: `from db.scripts import execute_script_file` (explicit import only — never at top level).
2. **Scoping**: `apply_scope()` MUST only be called with `connection="WORKBENCH"`. Raises immediately on EXPOSURE, LOSS, DATABRIDGE.
3. **Status**: always event-sourced (insert event row + stamp cached column in one transaction via `get_connection()` context manager). Never UPDATE in place.
4. **Categoricals**: kind tables (`*_kind`) for all internal values. Plain VARCHAR for external-mirror columns only (listed in Article 3 carve-out).
5. **IRP**: submission on request path is permitted. Polling and result work MUST be in the poller/workers — never in route handlers. `poll_*_to_completion` FORBIDDEN in poller; use `get_*` single-status-check only.
6. **Frontend**: FastAPI + Jinja2 + HTMX. No SPA. `hx-boost` for top-level nav. Alpine.js only for small client slivers.
7. **Auth**: `AUTH_MODE=password` is a gated v1 fallback; never reachable in production. Session cookie contains session ID only.

## Three Databases

| Name | Env prefix | Purpose | Managed by |
|---|---|---|---|
| `rwb_workbench` | `MSSQL_WORKBENCH_*` | App state, workflow, audit | Alembic (`make db-migrate`) |
| `rwb_exposure` | `MSSQL_EXPOSURE_*` | Exposure data (EDM/RDM) | Bootstrap SQL script |
| `rwb_loss` | `MSSQL_LOSS_*` | Loss results | Bootstrap SQL script |
| DATABRIDGE | `MSSQL_DATABRIDGE_*` | Moody's — read-only | **Never touched by this app** |

## Dev DB Strategy

Drop-create-seed. Single revision `alembic/versions/0001_initial.py` until production cutover.
Before each schema-affecting iteration, choose: **Rebuild** / **Refresh** / **Skip**.
DATABRIDGE is never in scope.

## irp-integration v0.2.1.dev23

- `IRPClient()` reads all config from env vars — no constructor args
- Batch analysis: `submit_portfolio_analysis_jobs(list)` → `List[int]` (ordered, positional)
- Single analysis: `submit_portfolio_analysis_job()` → `Tuple[int, request_body]`; store `request_body["resourceUri"]` as `irp_job.resource_uri` immediately — not available in completion response
- Portfolio creation: `create_portfolio()` → sync (HTTP 201), writes `irp_portfolio_id` inline on request path
- Poller uses: `get_edm_import_job()`, `get_analysis_job()`, etc. (single-status-check)
- `poll_*_to_completion()` — FORBIDDEN everywhere (blocks for minutes)

## Testing

```bash
pytest tests/unit                    # unit (no external deps) — default CI
pytest tests/sqlserver --run-sqlserver  # SQL Server integration
pytest tests/irp --run-irp           # IRP sandbox
```

Unit tests use SQLite (injected via `register_engine`). SQL Server tests use the real driver.
