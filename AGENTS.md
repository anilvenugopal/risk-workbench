<!-- SPECKIT START -->
When present, read `specs/005-subportfolio-breakouts/plan.md` for the current
technology, project structure, and shell-command decisions.
<!-- SPECKIT END -->

# Risk Analysis Workbench — Agent Context

This is the single source of truth for coding-agent instructions in this repo.
`CLAUDE.md` points here; Codex and other agents read this file directly.

## Git

- **No AI attribution in git history**: never add `Co-Authored-By: Claude ...`, `Co-Authored-By: Codex ...`, or "Generated with ..." lines to commits or PR bodies (enforced for Claude Code via `includeCoAuthoredBy: false` in `.claude/settings.json`).

## Writing Style

Write clearly and naturally. Applies to chat replies, commit messages, PR
bodies, specs, docs, and code comments.

Name things:

- Use the real name of the thing. Do not replace it with an invented synonym.
- Do not use `genuinely`, `load-bearing`, `leverage`, `robust`, `comprehensive`, `holistic`, `utilize`, `facilitate`, `crucial`, `first-class`, or `it's worth noting`.
- No structural metaphors. Banned: `spine`, `backbone`, `seam`, `surface`, `slice`, `glue`, `plumbing`, `rails`, `guardrails`, `bedrock`, `cornerstone`, `linchpin`, `north star`, `building block`, `primitive`, `first-class citizen`, `footprint`, `surface area`, `ecosystem`, `fabric`, `DNA`. Name the table, route, worker, job, or module instead.
- No inflated verbs. Banned: `unlock`, `empower`, `supercharge`, `streamline`, `elevate`, `drive`, `power`, `harden`, `bake in`, `light up`, `wire up`. Say what the code does.
- If a word stands in for a structure instead of naming it, replace it with the structure's name.
- Avoid vague stand-ins such as `item`, `unit`, `artifact`, `flow`, `piece`, `record`, or `object` when a specific term exists.
- Name the portfolio, submission, analysis, job, requirement, route, table, column, template, file, or user action directly.
- Do not write `this`, `that`, `the above`, `the existing behavior`, or `the current approach` when the reference may be unclear.
- Repeat the exact term when needed for clarity. Do not invent a label to avoid repeating a word.
- Do not assume the reader remembers an earlier section or another document.

Say what happened:

- State what happens, who does it, and what changes.
- Lead with the answer. Context comes after, and only if it changes what the reader does next.
- One idea per sentence. Cut any sentence that only restates the one before it.
- Be specific: the number, the file path, the column name, the limit.
- Report the exception, not the inventory. "No violations" beats thirteen rows of "pass".
- Give one recommendation, then the single real risk. Do not hedge both ways.
- Keep descriptions proportional to the change. Length is not evidence of work.
- No preamble, no closing recap.

Bad:

> The worker creates one slice for each LOB.

Better:

> The worker creates one portfolio for each LOB.

Bad:

> Each slice stores its source and breakout value.

Better:

> Each generated portfolio stores its source portfolio, breakout dimension, and breakout value.

Length:

- Commit subject ≤ 72 characters. The body says why; the diff says what.
- PR descriptions scale with the diff: what changed and why, then how to verify.
- Chat replies answer the question asked. No status inventories, no tables of completed work.

## Code Quality

Implement the smallest change that satisfies the requirement.

- Do not add behavior for hypothetical scale, misuse, or future requirements.
  Require a product rule, observed failure, measurement, or external constraint.
- Do not add an abstraction for one caller unless it makes the caller simpler.
- Do not add a helper that only renames a function call or dictionary construction.
- Do not add a constant used in one module unless it names a business rule or
  prevents the same value from diverging within that module.
- Do not improve nearby code unless the requested change depends on it.
- Prefer deleting unnecessary code over explaining why it exists.

Comments explain constraints that names, types, and structure cannot express.

- Do not narrate the next statement or repeat a function name, test name,
  requirement, PR description, or review discussion.
- Do not preserve implementation history in source comments. Put evidence and
  rejected alternatives in `research.md`.
- Private helpers usually need no docstring. Document a contract, side effect,
  exception, external limitation, or non-obvious correctness condition.
- A comment longer than the code it explains needs a specific reason that cannot
  be expressed in clearer code.

Before handoff, review the diff for subtraction:

1. Remove comments and tests that restate the implementation.
2. Inline helpers that do not name or isolate meaningful behavior.
3. Remove speculative limits, branches, and configurability.
4. Remove documentation from files that do not own the changed fact.
5. Compare the size of the diff with the size of the requirement. Explain any
   large difference; do not use explanation to excuse avoidable code.

## Source of Truth Documents

Read these before any implementation work:

- [docs/PRD.md](docs/PRD.md) — product requirements, feature scope, iteration roadmap
- [docs/DATA_MODEL.md](docs/DATA_MODEL.md) — canonical entity and relationship definitions
- [.specify/memory/constitution.md](.specify/memory/constitution.md) — 13 architectural rules (v3.0.0); all compliance gates

## Specification Workflow

Use SpecKit's native files. Do not add summary documents. Each file owns one
kind of fact. Update the owner and link to it; do not copy the explanation into
other files. A terminology rename may update every reference, but a behavior
change does not authorize repeating its rationale across every reference.

| File | Owns |
|---|---|
| `spec.md` | what the user can do, scope, business rules, open product decisions |
| `plan.md` | what changes in the system, where the code changes, risks, open technical decisions |
| `research.md` | evidence, spikes, rejected alternatives |
| `data-model.md` | schema |
| `contracts/` | interfaces and payloads |
| `quickstart.md` | how to verify |
| `tasks.md` | the work — each task tagged with the `FR-`/decision ID it closes |

`spec.md` and `plan.md` each open with a review section a reviewer can read in
five minutes and decide from. Implementation detail goes below it.

Caps: spec review section 40 lines · 2–4 user stories · ≤ 25 requirements ·
plan design summary 15 bullets · changed directories only · constitution check
lists violations and the articles that shaped the design, never 13 rows of
"pass".

Decisions get IDs: `P-nn` product, `T-nn` technical, `O-nn` open. Status words
(Approved, Proposed, Assumed, Open, Deferred, Blocked) appear in decision tables
and nowhere else — prose reads as current. After a decision, delete the loser;
history lives in `research.md`.

Vendor docs are evidence, not validation — claim validated only after a spike or
observed result. Nothing is "ready for tasks" while an `O-nn` is open.

## UI & Implementation Workflow

Two rules for user-facing work — full detail in [docs/UI_WORKFLOW.md](docs/UI_WORKFLOW.md):

1. **UI-first, for screens with real new layout.** Show a quick **rendered HTML preview** and get an approval before building the Jinja2 template and route. Build previews from [docs/ui_previews/_scaffold.html](docs/ui_previews/_scaffold.html) (reuses the real tokens). **Skip the preview for trivial/derivative changes** — copy tweaks, adding a field to an already-styled component — just build those. Cover the states that matter (don't forget empty/error). Approval is informal; no tables, inventories, or status tracking.
2. **One user story at a time.** Implement a single user story end-to-end, then **stop** for the approver to click the running feature before starting the next. Don't batch several stories into one implement pass (bundle two small related stories if splitting them is silly).

## Development Environment

**Two containers:**
- `linux-box` — runs nginx, uvicorn, redis, dramatiq workers, poller (mirrors production Linux server)
- `sqlserver` — SQL Server 2022 Developer edition (mirrors separate SQL Server instance in prod)

**Key commands** — every `make` target below runs inside `linux-box` and needs the
stack already up. Starting it is the developer's call, not an agent's (see
[Testing](#testing)):
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

The unit tier is the exception: `uv run pytest tests/unit` runs from any host shell
with no container and no database. Prefer it over `make test`.

See [docs/SCAFFOLDING.md](docs/SCAFFOLDING.md) for full setup and debugging tutorial.

## Architecture Rules (Summary)

Full rules in the constitution. Key points for implementation:

1. **Data access**: all SQL through `db/` package. Safe path: `db.execute()`. Trusted-script path: `from db.scripts import execute_script_file` (explicit import only — never at top level).
2. **No row-level security** (CR-003, Article 6 v3.0.0): no `customer_id`, no `apply_scope()`, no `user_customer_access`. Every authenticated analyst sees every deal. `submission.assigned_analyst_id` is a soft "my submissions" owner, not an access gate. Roles gate *functions*, never *rows*.
3. **Status**: `submission.status_code` is event-sourced (insert `submission_status_event` + stamp the cached column in one transaction via `get_connection("WORKBENCH")` with an explicit `conn.begin()`). All other status columns are updated in place.
4. **Categoricals**: kind tables (`*_kind`) for all internal values. Plain VARCHAR for external-mirror columns only (listed in Article 3 carve-out).
5. **IRP**: submission on request path is permitted. Polling and result work MUST be in the poller/workers — never in route handlers. `poll_*_to_completion` FORBIDDEN in poller; use `get_*` single-status-check only.
6. **Frontend**: FastAPI + Jinja2 + HTMX. No SPA. `hx-boost` for top-level nav. Alpine.js only for small client slivers.
7. **Auth**: `AUTH_MODE=password` is a gated v1 fallback; never reachable in production. Session cookie contains session ID only.
8. **Approved plans are immutable**: when an async operation follows a user preview or confirmation, the worker executes the plan the user approved. Persist it and run it — never silently recompute inputs at execution time.

## Three Databases

| Name | Env prefix | Purpose | Managed by |
|---|---|---|---|
| `rwb_workbench` | `MSSQL_WORKBENCH_*` | App state, workflow, audit | Alembic (`make db-migrate`) |
| `rwb_exposure` | `MSSQL_EXPOSURE_*` | Exposure data (EDM/RDM) | Bootstrap SQL script |
| `rwb_loss` | `MSSQL_LOSS_*` | Loss results | Bootstrap SQL script |
| DATABRIDGE | `MSSQL_DATABRIDGE_*` | Moody's — read-only | **Read-only, only via irp-integration methods, worker-side** (constitution Art. 11 v3.1.0); never migrated/bootstrapped; never raw SQL from app code |

## Dev DB Strategy

Drop-create-seed. Single revision `alembic/versions/0001_initial.py` until production cutover.
Before each schema-affecting iteration, choose: **Rebuild** / **Refresh** / **Skip**.
DATABRIDGE is never in schema scope (no DDL/migrations/bootstrap; reads only via irp-integration, worker-side).

## irp-integration (source-switchable: PyPI / TestPyPI / local)

- Source is switchable via uv dependency groups — `make irp-pypi` (PyPI `0.2.0`, production default), `make irp-testpypi` (newest TestPyPI dev build), `make irp-local` (editable checkout at `../../IRP/irp-integration`). `make irp-status` shows the active source. Confirm method signatures against the **active** wheel — it is pre-release and moves.
- `IRPClient()` reads all config from env vars — no constructor args
- Batch analysis: `submit_portfolio_analysis_jobs(list)` → `List[int]` (ordered, positional)
- Single analysis: `submit_portfolio_analysis_job()` → `Tuple[int, request_body]`; store `request_body["resourceUri"]` as `irp_job.resource_uri` immediately — not available in completion response
- Portfolio creation: `create_portfolio()` → sync (HTTP 201), writes `irp_portfolio_id` inline on request path
- Poller uses: `get_edm_import_job()`, `get_analysis_job()`, etc. (single-status-check)
- `poll_*_to_completion()` — FORBIDDEN everywhere (blocks for minutes)

## Testing

Three tiers. Only the unit tier runs from a plain host shell.

| Tier | Directory | Needs | Command |
|---|---|---|---|
| Unit | `tests/unit` | nothing | `uv run pytest tests/unit` |
| SQL Server | `tests/sqlserver` | ODBC driver + live SQL Server | `make test-sql` (Docker) or `make wsl-test-sql` (WSL2) |
| IRP sandbox | `tests/irp` | IRP credentials in env | `make shell`, then `uv run pytest tests/irp --run-irp` |

**Always `uv run pytest`, never bare `pytest` or `python -m pytest`.** Dependencies
live in the uv environment; a bare call fails at import with
`ModuleNotFoundError: No module named 'itsdangerous'`, which is a missing
environment, not a broken test.

Unit tests use SQLite injected via `register_engine`, so they need no database and
are the tier to run after every change. SQL Server tests use the real driver.

### Do not run the SQL Server tier from the host shell

`uv run pytest tests/sqlserver --run-sqlserver` typed into Git Bash or PowerShell
fails every test with "Could not connect to WORKBENCH database" even when
`infra-sqlserver-1` is healthy. The ODBC driver and the `MSSQL_*` env vars live in
the `linux-box` container (Docker) or are exported by `infra/scripts/wsl-env.sh`
(WSL2); the Windows host has neither. `make test-sql` and `make wsl-test-sql` exist
because of this — use them.

`tests/sqlserver/test_connectivity.py` is the check: if it fails, the environment is
wrong, not the code.

### Agents: never start, stop, or rebuild containers

`make dev-up`, `make sqlserver-up`, `docker compose up`, `make db-rebuild` and
friends change the developer's running environment and are the developer's call, not
an agent's. If a tier cannot run because `linux-box` is down, **say so and stop** —
report which tiers ran, which did not, and what the developer needs to run. Never
start a container to make a test pass.

### Reporting results

Name the tier and the count: "unit tier, 722 passed; SQL Server tier not run
(`linux-box` down)". A change that adds `tests/sqlserver` tests is **unverified**
until someone runs `make test-sql` — say that plainly rather than implying the
suite is green.
