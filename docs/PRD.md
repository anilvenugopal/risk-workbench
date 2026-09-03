# Risk Workbench — Product Requirements Document

**Status:** Draft for build · **Format:** Living document, kept in the repo  
**Intended builder:** Claude Code (agent-built, iteration-sequenced)  
**Source of domain truth:** `irp-workbench/` (IRP integration ground truth) + `irp-integration` 0.4.0 from TestPyPI

---

## 0. How to use this PRD

This document is **feature-organized** (§4–§20). Each feature section is self-contained — purpose, data, behavior, and rules in one place. §21 is the **build plan** (iterations, sequencing, exit criteria). §22 is an **adversarial review**. §23 logs locked decisions, open decisions, and external dependencies. §24 is the **change log**.

The **one declarative source of truth** (§2.1) — the navigation manifest — is the spine; "add a page" is a one-place edit to it (CR-002 removed the other two: the workflow-definition manifest and the type/port registry). Two companion documents carry the domain detail this PRD builds from: **`DATA_MODEL.md`** (canonical entities/relationships) and **`FUNCTIONAL_REQUIREMENTS.md`** (the reconciled statement of what the workbench does, edited line-by-line in the CIC design sessions). Where this PRD and `FUNCTIONAL_REQUIREMENTS.md` disagree, the functional requirements are the newer source of truth.

---

## 1. Product overview

### 1.1 What this is

An internal workbench for a reinsurance catastrophe-modeling team. It is an **integration hub over Moody's Risk Modeler (IRP)**, designed to make each step of the modeling workflow fast, keep everything in one place, and eliminate context-switching between applications.

It is **not a workflow engine** — the analyst is always in the driver's seat. Every significant step involves judgment. The value is execution speed and functional coverage, not automation.

It is also **not** a multi-tenant SaaS and **not** a governance platform.

### 1.2 Primary users

Reinsurance catastrophe analysts who:
- Receive broker submissions (EDM and/or RDM files)
- Import exposure data into IRP, review it, and create sub-portfolios (filtered breakouts)
- Configure and run cat model analyses (potentially 50–150+ combinations per worldwide contract)
- Review results (ELTs, EP numbers, AAL), compare against broker results
- Export finalized loss sets to downstream repositories

Administrators manage users, roles, and system configuration.

### 1.3 The three-phase workflow

Every submission follows three sequential phases. The workbench covers all three.

#### Phase A — Data Setup & Shaping
1. **File ingestion** — select EDM/RDM files from network shares (never the local machine — §8.2); import into IRP via API. Auto-apply naming conventions from submission context.
2. **Exposure review** — a fast textual snapshot of the imported exposure (counts, perils, geography, currency, record volume, treaties) so the analyst understands it without clicking through Risk Modeler (§2.2 of `FUNCTIONAL_REQUIREMENTS.md`; delivered as the EDM detail view, Iteration 3).
3. **Exposure shaping** — create sub-portfolios by **filtering** the exposure (synchronous IRP `create_portfolio()`, §10A / §14.3 / §15.5) and one-click breakouts (by LOB, by geography, complement splits). This is the MVP path.

> **MVP scope note (2026-07-21).** Of the earlier Phase A vision, only file ingestion, exposure review, and sub-portfolio **creation** are in the MVP. **Deferred / out of MVP:** SQL-based *validation reports and exposure profiling* via DataBridge, *data-element modification* (construction/currency normalization), *peril-specific portfolios* ("we don't have to split by peril" — FR §3), *merge/combine portfolios* (recombination happens on results, not exposure), and the *Exposure Repository load* (§10, §16.5). Sub-portfolio creation is the **synchronous IRP `create_portfolio()`** path (§10A), **not** the deferred DataBridge exposure-modification path (§10.3).

#### Phase B — Analysis Execution
1. **Hazard lookup (GeoHaz)** — optionally run hazard lookup on a portfolio before analysis (§10B). Broker geocoding is preserved; re-geocoding is not a workbench action.
2. **Analysis configuration** — select model profiles, output profiles, event rate schemes; currency, currency scheme, and vintage are chosen at submit time (spec 009 P-11). For worldwide contracts: batch submission from predefined templates ("global suite", 50–150+ combinations).
3. **Job submission & tracking** — submit analysis jobs via IRP API. Auto-poll for status. Surface progress, completion, and failures.
4. **Notifications** — push notification (Teams, email, or in-app center) on job completion or failure (Iteration 12; §18).

#### Phase C — Results Management
1. **Results review** — view analysis outputs (ELT summary, EP numbers, AAL, return periods) per financial perspective. Compare own results against broker-supplied RDM results and prior-year benchmarks.
2. **Results grouping** — combine or break out results by geography or other dimensions (e.g., county → state rollups).
3. **Downstream upload** — push finalized **own** loss sets to the Loss Repository SQL Server (pushing broker results to the Loss Repository is out of MVP — FR §7).

### 1.4 Core domain glossary

- **Submission (the deal)** — the top-level unit of work and the only user-facing container for EDMs and RDMs: a specific cedant's specific treaty at a specific inception. There is no hierarchy above it (no Customer or Program — dropped, CR-003). EDMs and RDMs relate directly to zero or more submissions without copying the Risk Modeler resource. The submission carries the deal's identity and filter attributes (`cedant_name`, `treaty_type_code`, `inception_date`, `treaty_year`), an assigned analyst (soft owner, for the "my submissions" view — **not** an access gate), an optional shared-drive `directory_path`, and an optional self-referential renewal link. CRM identifiers attach as a 0..N tag set (`submission_crm_id`), not a single field.
- **EDM (Exposure Data Module)** — an exposure database, typically a `.bak` or `.mdf` file from a broker. First-class tracked entity in the workbench (name + IRP exposure ID). Imported into IRP, validated, and used as the basis for analysis.
- **RDM (Risk Data Model)** — a results database from the broker (their own prior analysis). First-class tracked entity. Imported into IRP; used for comparison against the analyst's own results.
- **Portfolio** — a named view within an EDM in IRP (all accounts, or a filtered subset). Analysis jobs run against portfolios, not EDMs directly. Each `irp_*` entity tracks its own Risk Modeler id in `irp_id`.
- **Sub-portfolio (breakout)** — a portfolio created by **filtering** an EDM's exposure (e.g. isolate a state with a different retention, exclude an LOB), so it matches treaty terms the broker didn't break out. Created synchronously (`create_portfolio()`, HTTP 201, no job). Filter values are picked from the *real values present in the portfolio*, not free-text. One-click breakouts fan a portfolio out by LOB or geography, including complement ("X vs. not-X") splits (§10A). Creation granularity is capped at state/country; finer cuts (CRESTA, ZIP) are results, not portfolios.
- **Analysis** — an analysis (or, when `is_group=true`, a **group** — a group *is* an analysis in Risk Modeler) *related to* an EDM because it ran against a portfolio in it — never "in" the EDM: EDMs contain exposures only, and platform-run analyses live only on the platform (terminology rule, design note 22 D23 — analyst-facing copy must use the relationship phrasing). `rdm_id` set → the analysis came from importing that RDM (broker); null → a net-new analysis the analyst ran (own). Backed by `irp_analysis`.
- **Treaty** — a reinsurance treaty belonging to an EDM, referenced by analyses by name. Create/edit is synchronous (no job). Backed by `irp_treaty`.
- **Analysis template** — a saved configuration for one analysis job (model profile, output profile, event-rate scheme, analysis settings, tags — currency and treaty selection happen at submit time, not stored; spec 009 P-09/P-11, §11.3a), for batch submission. **In MVP** (practice-lead call, 2026-07-06 — reverses the CR-002 deferral); batch submission from saved templates is the top analyst pain point. Backed by `analysis_template` + `template_suite` (shipped Iteration 6, spec 009).
- **Prerequisite gate** — the computed "what can the analyst do right now": a lookup + entity-existence/job-terminal-status check in code (§13.1), not a stored stage machine. Replaces the removed Workflow/Stage/Task construct.
- **Name-based coupling** — each op resolves its inputs live from Risk Modeler by name at submit time (`search_edms`/`search_portfolios`/`search_analyses`/`search_treaties`); there is no typed handle to chain (§13.2).
- **Job (`irp_job`)** — an IRP async operation tracked in the Workbench Metamodel DB — one row per real IRP op (the executable unit). Has an `irp_job_type` (which IRP endpoint to poll), an `irp_id` (RM's job id), and one or more `rwb_job` rows written on completion.
- **RWB job** — a general queued-work row in the `rwb_job` SQL table, for work **this app executes** in-process. Fully decoupled from `irp_job` (no FK). `requestor_type` + `requestor_id` record what triggered it (an `irp_job` completion, an analyst request, or a parent `rwb_job` for chaining). Picked up by a Dramatiq worker.
- **RWB job dedup key** — the composite `UNIQUE(requestor_type, requestor_id, rwb_job_type)` on every `rwb_job` row (replaces the old `request_key` string).
- **Job heartbeat** — a child-table row (`rwb_job_heartbeat`) stamped every `RWB_HEARTBEAT_INTERVAL_SECS` by a daemon thread while a worker holds a job. Proves the job is progressing, independent of which worker and independent of job duration.
- **Reconciler** — a single-instance periodic sweep (folded into the poller process) that recovers `running` `rwb_job` rows whose heartbeat is stale. Stale = heartbeat older than `RWB_HEARTBEAT_STALE_SECS` (a constant multiple of the heartbeat interval; never a function of job size or duration). Resets `running → pending` and re-enqueues via Dramatiq. Does not scan `pending` rows (durable Redis covers those).
- **IRP job type** — a kind-table discriminator on every `irp_job` row; determines which IRP polling endpoint to call: `import_edm`, `import_rdm`, `delete_edm`, `geohaz`, `analysis`, `grouping`, `export`.
- **DLM / HD** — two Moody's model families (Detailed Loss Module / High-Definition). Not file-level attributes — determined by the selected analysis profile's `softwareVersionCode`. Cannot be mixed within a group. DLM requires an event-rate scheme; HD makes it optional.
- **GeoHaz (hazard lookup)** — an optional pre-analysis operation that runs Moody's hazard lookup on a portfolio (`irp_job_type = geohaz`, §10B). In this workbench it is **hazard lookup only** — geocoding is *not* re-run (broker geocoding is preserved); re-geocoding, if ever needed, is done intentionally inside the model, not here. Async (polled by the poller).
- **Exposure Repository** — on-prem SQL Server that holds pre-aggregated exposure summary data (output of Phase A). Separate connection from the Workbench Metamodel DB.
- **Loss Repository** — on-prem SQL Server that holds finalized loss sets / analysis results (output of Phase C). Separate connection from both other databases.
- **Workbench Metamodel DB** — the app's own SQL Server (app state, job inventory, audit). One of three distinct database connections.
- **DataBridge** — Moody's cloud SQL Server, accessed via `client.databridge` (ODBC). Used for validation reports, exposure profiling, and exposure modification. Cannot serve analysis results.

---

## 2. Architecture principles

### 2.1 The navigation manifest (the one declarative source of truth)

> **CR-002.** Earlier drafts named *three* declarative sources of truth — a nav manifest, a workflow-definition manifest, and a type/port registry. The latter two are **removed** with the workflow engine (§12). What remains is the general principle applied to the **navigation manifest** alone.

The **navigation manifest** (§4.2) — the rail/sidebar/breadcrumb/search tree — is a versioned code manifest, so engine code stays fixed: "add a page" is one manifest node + one handler + one template. There is **no workflow-definition manifest, no type/port registry, and no manifest→DB projection subsystem** — nothing in this app projects a manifest into tables, so the fail-fast content-hash consistency check and version-retention machinery that existed only to serve the workflow definition are gone too.

Validation is **at the point of action** (§13.3), not a registered-validator graph pass: entity existence + name uniqueness + reference-data lookups against the local IRP cache, checked when the analyst acts.

### 2.2 Three-database architecture

The workbench connects to **three logically separate SQL Server databases**. In local dev all three are separate databases on one SQL Server container; in production they are separate servers.

| Named connection | Database | Purpose | Owner |
|---|---|---|---|
| `WORKBENCH` | Workbench Metamodel DB | App state: submissions, EDMs, RDMs, jobs, workflows, audit, reference cache | App / Alembic |
| `EXPOSURE` | Exposure Repository | Pre-aggregated exposure summaries pushed by Phase A | App writes; downstream reads |
| `LOSS` | Loss Repository | Finalized loss sets pushed by Phase C | App writes; downstream reads |
| `DATABRIDGE` | DataBridge (Moody's cloud) | Validation, profiling, exposure modification via ODBC | Moody's — app never runs DDL here |

**Connection configuration follows the `db/` package convention** (`MSSQL_{NAME}_*` env vars). App code calls `get_connection("WORKBENCH")`, `get_connection("EXPOSURE")`, `get_connection("LOSS")`, `get_connection("DATABRIDGE")` — no URL strings in application code.

```
MSSQL_WORKBENCH_SERVER, MSSQL_WORKBENCH_USER, MSSQL_WORKBENCH_PASSWORD, MSSQL_WORKBENCH_DATABASE
MSSQL_EXPOSURE_SERVER,  MSSQL_EXPOSURE_USER,  MSSQL_EXPOSURE_PASSWORD,  MSSQL_EXPOSURE_DATABASE
MSSQL_LOSS_SERVER,      MSSQL_LOSS_USER,      MSSQL_LOSS_PASSWORD,      MSSQL_LOSS_DATABASE
MSSQL_DATABRIDGE_SERVER, MSSQL_DATABRIDGE_USER, MSSQL_DATABRIDGE_PASSWORD, MSSQL_DATABRIDGE_DATABASE
```

Pool sizing is **per-connection**, not global. Each named connection has its own pool. Per-connection overrides: `MSSQL_{NAME}_POOL_SIZE`, `MSSQL_{NAME}_POOL_MAX_OVERFLOW`. Falls back to global `MSSQL_POOL_SIZE` / `MSSQL_POOL_MAX_OVERFLOW` if not set (default 5 / 5). **Watch the total**: with four connections and defaults, you can open up to 40 physical connections to SQL Server. Tune per connection based on actual load. Recommended starting point for 30 users: `MSSQL_WORKBENCH_POOL_SIZE=10`, `MSSQL_WORKBENCH_POOL_MAX_OVERFLOW=20`; `MSSQL_EXPOSURE_POOL_SIZE=5`, `MSSQL_LOSS_POOL_SIZE=5` (Phase C Dramatiq workers). `MSSQL_DATABRIDGE_POOL_SIZE=3` (DataBridge ODBC is session-scoped; small pool is correct). **Note:** per-connection pool env vars require a one-line change to `_pool_kwargs()` in `db/connection.py` to prefer `MSSQL_{NAME}_POOL_SIZE` over the global fallback.

### 2.3 Stack posture

Server-rendered HTML over **FastAPI + Jinja2 + HTMX 2.x**, with **Alpine.js** for client-only behaviors (modal, keyboard shortcuts, focus trap). No SPA, no client state tree, no build step for the app shell. Styling: **custom ITCSS design system** (DocIntel/Verity), copied verbatim — not Tailwind.

**Concurrency model — sync by default.** Route handlers are plain `def`; FastAPI runs them in its threadpool. `irp-integration` is sync; pyodbc is sync. Both are called directly from services — no `asyncio.to_thread` needed in sync handlers. **SSE endpoints are the only `async def`**; inside them, DB reads use `await asyncio.to_thread(sync_read)`.

**Background work splits into three tiers:**
- **IRP job submission** — synchronous on the request path. The IRP submit call returns a job ID immediately; the round-trip is fast enough that there is no benefit to deferring it. On failure the job is marked `SUBMISSION FAILED` and the retry batch job picks it up.
- **Poller** — standalone loop process (`app/poller/run.py`). One process, one pass per interval: bulk-queries all non-terminal `irp_job` rows, polls IRP per `irp_job_type`, updates `irp_job.status`, writes `rwb_job` rows (idempotent, via the composite dedup key) on terminal status. Also runs the reconciler sweep each cycle. Not Dramatiq — batching by design; a per-message queue would break the natural grouping.
- **Dramatiq workers** — consume `rwb_job` rows (post-terminal / analyst-requested / chained work). Redis broker (durable via AOF). Each result worker class owns one action. Submission retry is a separate **single-threaded batch job** (not a Dramatiq actor), re-attempting failed IRP submissions up to a configurable limit.

### 2.3a Queue resilience model (CR-001)

The queue is resilient through a layered design — each layer handles the failure modes it is best suited for:

| Failure | Handled by | Custom code? |
|---|---|---|
| Worker dies mid-job, Redis alive | Dramatiq redelivery (ack-after-success; per-process heartbeat) | No |
| Task raises / fails | Dramatiq Retries middleware (backoff, max_retries, dead-letter) | No |
| Graceful shutdown / redeploy | Dramatiq requeues in-flight messages | No |
| Redis loses data (crash) | AOF durability (`appendonly yes`, `appendfsync everysec`) + Dramatiq redelivery on restart | Config only |
| Job stops progressing (wedged worker; or running-job message lost) | Job heartbeat + reconciler (stale `running` → re-enqueue) | Minimal (§14.5) |
| Any rare double-delivery | Idempotent worker + atomic status claim (composite dedup key) | Yes (backstop) |

**Redis AOF durability.** Redis runs with `appendonly yes`, `appendfsync everysec`, persisted SSD volume, and default auto-rewrite (self-compacting). With AOF, acknowledged enqueues survive a broker crash (≤ ~1s worst-case loss), so pending-lost stops being a case that requires detection — which eliminates the need for any pending-side timeout. Outstanding work is always inspectable in the SQL `rwb_job` table; never by parsing AOF/RDB files.

**Heartbeat + reconciler.** When a Dramatiq worker claims an `rwb_job` (`pending → running`), it starts a daemon thread whose only job is to write `(rwb_job_id, worker_id, heartbeat_at=now)` to `rwb_job_heartbeat` every `RWB_HEARTBEAT_INTERVAL_SECS`. The heartbeat thread is separate from the work thread — it keeps stamping even while the work thread is blocked in a long, non-chunkable call (e.g., a large file download). The reconciler (folded into the poller process) checks for `running` rows whose heartbeat is older than `RWB_HEARTBEAT_STALE_SECS` (a constant multiple of the interval; never job-duration-based) and re-enqueues them atomically. The reconciler never scans `pending` rows; AOF makes that unnecessary.

**Idempotency backstop.** Workers are idempotent: file writes go to a temp path + atomic rename; chained `rwb_job` rows are created via idempotent insert on the composite `UNIQUE(requestor_type, requestor_id, rwb_job_type)`. The atomic claim (`UPDATE ... WHERE status_code='pending'`) means any rare double-delivery results in one effective execution.

### 2.4 Styling discipline

Extend the ITCSS system via tokens, never override it. New UI is layered into the existing ITCSS structure (settings → tools → generic → elements → objects → components → utilities). Every color, surface, and spacing value comes from a **CSS custom property** in the settings layer — never a hardcoded hex inline. Rule of thumb: if a new screen needs a color the system doesn't have, add a token, don't write the hex into the component.

### 2.5 Maintainability contract

These tasks must each be a bounded, one-place change:
- **Add a page** → one nav-manifest node + one handler + one template. Rail, sidebar, breadcrumb, active-state, RBAC, search visibility are inherited.
- **Add a searchable object type** → register one search provider.
- **Add a new IRP operation** → one `irp_job_type_kind` seed + one submit call + one poll-method mapping (§14.3–§14.4). No manifest, no projection.
- **Add a post-completion action** → one `rwb_job_type_kind` seed + one Dramatiq worker (§14.5).
- **Add a validation rule** → one check at the point of action (§13.3).

### 2.6 Auto-naming

Auto-naming is a first-class feature, not a convenience. An analyst submitting a worldwide contract should never have to type 50+ analysis names. EDM/RDM import names are auto-populated from the chosen file (§8, §9). **Analysis names follow a fixed workbench rule: portfolio name + template name, truncated from the right to Risk Modeler's 64-character analysis-name cap** (locked 2026-08-20, resolving O7-3/O14-9) — the template name already conveys profile, region, and peril, so no configurable pattern is needed; the per-template `auto_name_pattern` was dropped in Iteration 6 (spec 009 P-03). The workbench stores the full untruncated name on `irp_analysis`; only the name sent to Risk Modeler is clipped. Group naming lands with grouping (Iteration 9).

---

## 3. Technology stack & environment

| Concern | Choice |
|---|---|
| Web | FastAPI + uvicorn |
| Templating / interactivity | Jinja2 + HTMX 2.x (self-hosted) + Alpine.js (self-hosted) |
| Styling | Custom ITCSS design system (from `docintel/ui/src/styles`) |
| Databases | SQL Server: Workbench Metamodel DB + Exposure Repository + Loss Repository (3 separate connections) |
| DB access | `db/` package (SQLAlchemy Core + pyodbc + ODBC Driver 18). Named connections: `WORKBENCH`, `EXPOSURE`, `LOSS`, `DATABRIDGE`. Pool sizing via `MSSQL_POOL_SIZE` / `MSSQL_POOL_MAX_OVERFLOW`. |
| Migrations | Alembic (targets `WORKBENCH` connection only). **Dev strategy: drop-create-seed.** Until production (or significant data risk), the dev workflow is full drop-and-recreate — no accumulation of migration versions. A single `alembic/versions/0001_initial.py` creates all tables and seeds all kind tables. Re-running it drops and recreates. Migration version history begins at production cutover. |
| Poller | Standalone loop process; `app/poller/run.py`. Batch-polls all non-terminal IRP jobs per interval. Not Dramatiq. |
| Dramatiq workers | **Dramatiq** + **Redis** broker. Workers in `app/workers/`. Result workers (one class per `work_type`) + `submission_retry` actor. |
| Auth | Entra ID (OIDC/BFF) via MSAL; dev header stub for local development |
| Sessions | Server-side session store abstraction (in-memory dev stub; DB-backed or Redis in prod) |
| Live status | SSE (`sse-starlette`) for job status push; HTMX polling for page-level status |
| Reverse proxy | nginx (TLS termination, static assets, routing; `proxy_buffering off` on SSE routes) |
| Assets | All local — no CDN (org network policy) |
| External integration | `irp-integration` (sync) — Risk Modeler REST + DataBridge ODBC |
| Notifications | Dramatiq worker posts to Teams webhook and/or sends email |
| Dev environment | **Linux-native** — app, uvicorn, nginx, Redis, poller, and Dramatiq workers run directly on the host (systemd units or shell processes). **SQL Server only** runs in Docker (`docker run mcr.microsoft.com/mssql/server`). No Docker Compose wrapping the application stack. |

---

## 4. Feature: Application shell & navigation

### 4.1 Layout

The IDE shell: left **rail** (icons), **sidebar** (contextual nav panel), **main** area, **top bar** (breadcrumb + global search + Help), **bottom status bar**. Home renders without a sidebar (full-width dashboard); all other rail destinations show a sidebar.

### 4.2 Navigation manifest (the keystone)

One declarative tree. Each node declares:

| Field | Meaning |
|---|---|
| `key` | Unique stable id |
| `label` | Display label |
| `parent` | Parent node key (null for rail-level roots) |
| `rail_icon` | Local SVG icon name (rail-level nodes only) |
| `route` | URL path it owns |
| `template` | Template/handler binding |
| `breadcrumb_label` | Label used in breadcrumb trails (defaults to `label`) |
| `searchable` | Whether this node appears in the global search nav group |
| `roles` | Roles permitted to see/use this node (RBAC gate) |

**Derived from this one structure:** the rail (root nodes), the sidebar (a root's children), breadcrumb trails (walk `parent` upward), active-state highlight (current route → node → root ancestor), and the search nav group.

**Dynamic detail pages** (e.g., `SUB-123`) are not manifest nodes. A detail route declares the manifest node it "lives under"; breadcrumb = walk up from that declared home node, then append the entity's own label.

### 4.3 Breadcrumbs — context-based, not history-based

A breadcrumb is a **pure function of the manifest position**, never of navigation history. Every page and detail view has a real URL; HTMX navigations use `hx-push-url` so the address bar stays truthful. Breadcrumb/active-state resolution: `current URL → manifest node (or declared home) → walk up`. Refresh, deep-link, bookmark, and browser back/forward all fall out of this.

**Navigation transport — `hx-boost`.** Top-level rail/sidebar navigation uses `hx-boost` on the shell — anchors are progressively enhanced into AJAX swaps of the main content region, with history managed automatically. Degrades gracefully without JS.

### 4.4 Status bar

IDE-style, three zones:
- **Left** — environment badge (loud in LOCAL/dev mode), signed-in user, active role
- **Center** — background activity: "3 jobs running · 1 result worker pending" (wired when execution lands, §14.7)
- **Right** — last-action result ("EDM-123 imported") + HTMX request spinner (`htmx-indicator`)

### 4.5 Rail destinations (indicative)

| Rail item | Sidebar children |
|---|---|
| Home (dashboard) | — |
| Submissions | List |
| Jobs | IRP Jobs, RWB Jobs, Exceptions |
| Results | Results, Loss Repository |
| Moody's IRP | Sync Metadata, EDM Library, RDM Library |
| Administration | Users, Settings |

### 4.6 Icons

SVGs stored under `static/icons/`, inlined via an `icon(name)` Jinja macro. Inline SVG inherits `currentColor`, so active-state theming is free.

---

## 5. Feature: Authentication & session management

### 5.0 Auth mode overview

Authentication uses a **mode switch** controlled by `AUTH_MODE` in config:

| `AUTH_MODE` | Login page shows | Who can log in |
|---|---|---|
| `password` | Password form only | Users with a `password_hash` set in `app_user` |
| `oidc` | "Sign in with Microsoft" button only | Entra ID users (PremiumIQ tenant) |
| `both` | Password form + "Sign in with Microsoft" button | Either — user chooses their path |

**`both` is the recommended default for development.** It lets the developer test both paths without restarting the app. In production, choose `password` or `oidc` based on what the organisation is ready to support. If `AUTH_MODE=oidc` or `AUTH_MODE=both`, the OIDC env vars (`ENTRA_CLIENT_ID`, `ENTRA_TENANT_ID`, `ENTRA_CLIENT_SECRET`, `ENTRA_REDIRECT_URI`) must be set. If `AUTH_MODE=password` only, OIDC env vars are not required.

The login page renders exactly the options corresponding to the configured mode — no dead UI elements, no hidden forms. Switching mode is a one-env-var change; no code changes are required.

**Entra app registration status (2026-07-01):** The Entra app ("Governance", PremiumIQ tenant) is registered with redirect URI `http://localhost:8000/auth/callback` (Web) and `User.Read` delegated permission granted. See `docs/ENTRA_SETUP.md` for remaining steps before production.

The `CurrentUser(id, email, role)` dataclass is identical across all modes. All downstream code — audit, role gates, analyst filters — is auth-mode-agnostic.

**Dev header stub** (`AUTH_MODE=dev`) remains available for local development only. Enabled only when `APP_ENV != production` AND `AUTH_MODE=dev`. Loud persistent banner when active. Never reachable in production.

---

### 5.1 v1 — Password authentication

#### 5.1.1 Login form

A standard server-rendered login page at `GET /auth/login`. On `POST /auth/login`:
1. Look up `app_user` by email (case-insensitive).
2. Verify the submitted password against `app_user.password_hash` (bcrypt, cost factor 12).
3. On success: create a `user_session` row, set `HttpOnly Secure SameSite=Lax` cookie containing only the session ID (random 32-byte hex). Redirect to the originally-requested URL or home.
4. On failure: increment `login_attempt` counter for `(email, ip_address)`. Apply rate limit (§5.1.3). Return the login form with a generic error — never indicate whether the email exists.

#### 5.1.2 Password management

- `password_hash` is bcrypt (cost factor 12). Never stored or logged in plaintext.
- New accounts are created by an admin. The admin sets a temporary password; `must_change_password = true` is set on the account.
- On first login (or when `must_change_password = true`), the user is redirected to `GET /auth/change-password` and cannot access any other route until the password is changed.
- Password requirements (enforced at set time, not just client-side): minimum 12 characters, at least one uppercase, one lowercase, one digit.
- **Password reset by admin only** — no self-service reset in v1 (no email infrastructure required). Admin uses the admin UI or a CLI command (`python -m app.cli reset-password --email x@y.com`) to set a new temporary password and flag `must_change_password = true`.
- Passwords for `AUTH_MODE=oidc` accounts are null. If an `oidc`-provisioned account somehow reaches the password login form, it is rejected with "account uses SSO login."

#### 5.1.3 Rate limiting

Tracked in the `login_attempt` table. Two independent limits applied on every failed attempt:

| Scope | Limit | Lockout |
|---|---|---|
| Per email | 5 failed attempts in 15 minutes | 15-minute lockout on that email |
| Per IP | 20 failed attempts in 15 minutes | 15-minute lockout on that IP |

Lockout check runs **before** password verification — a locked account/IP receives the generic error without hitting bcrypt. On success, the attempt counter for that email is cleared. Lockout state is read from the `login_attempt` table (count of failed attempts in the window); no separate lockout column needed.

#### 5.1.4 Session management

Session store: `user_session` table in the WORKBENCH DB. **Not Redis** — the DB session store means sessions survive Redis restarts, active sessions are queryable by admins, and forced invalidation is a single UPDATE. Redis is not a hard dependency for auth.

Session lifecycle:
- **Sliding expiry:** `last_active_at` updated on every authenticated request. Session expires if `last_active_at` is older than `SESSION_IDLE_TIMEOUT` (default 8h).
- **Absolute cap:** session expires unconditionally when `now() > expires_at` (set to `created_at + SESSION_ABSOLUTE_TIMEOUT`, default 24h).
- **Expiry handling:** on an HTMX request, return `HX-Redirect: /auth/login` (prevents the login page being swapped into a content fragment). On a full request, return HTTP 302.
- **Sign-out:** `POST /auth/logout` sets `invalidated_at` on the session row and clears the cookie.
- **Admin force-logout:** admin sets `invalidated_at` on any session row. Takes effect on next request by that user.
- Cookie: `HttpOnly`, `Secure`, `SameSite=Lax`. Contains only the session ID. Session ID is a cryptographically random 32-byte value (hex-encoded, 64 characters).

#### 5.1.5 CSRF protection

All state-changing requests (`POST`, `PUT`, `DELETE`, `PATCH`) require a CSRF token. Token is a signed value derived from the session ID, included as a hidden field in every form and as a request header for HTMX requests (`hx-headers`). Validated server-side before the handler runs. Mismatch returns HTTP 403.

#### 5.1.6 Audit

Every login attempt (success and failure) inserts a `login_attempt` row. Every authenticated state-changing action inserts an `audit_log` row. The audit log is the tamper-evident trail for all user activity.

---

### 5.2 v2 — Entra ID SSO (OIDC/BFF)

Activated by setting `AUTH_MODE=oidc`. The login form is replaced by an Entra redirect. Everything downstream of `CurrentUser` is unchanged.

OIDC authorization-code flow, backend-for-frontend pattern: the OIDC exchange is server-side; the browser never sees tokens. Entra authenticates **identity only** — `oid` claim maps to a local `app_user` record. **Authorization (roles) is always owned by the app**, never read from token claims or Entra groups.

On first sign-in from a new Entra identity: a `app_user` row is provisioned automatically (`entra_oid` set, `password_hash` null). Roles must be assigned by an admin before the user can do anything useful (fail-closed: no privileged access by default).

Full implementation steps are in §5.3.

---

### 5.3 v2 implementation checklist

#### In Entra ID (performed by an org admin)

1. ✅ **Register an application** in the org's Entra ID tenant.
   - App name: `Governance` (PremiumIQ tenant)
   - Supported account types: single tenant
   - Redirect URI: `http://localhost:8000/auth/callback` (Web) — dev only; production needs `https://`

2. ✅ **Tenant and client identifiers** noted and stored as env vars:
   - Application (client) ID → `OIDC_CLIENT_ID`
   - Directory (tenant) ID → `OIDC_TENANT_ID`

3. ✅ **Client secret** created → `OIDC_CLIENT_SECRET` env var set.
   - **Calendar a rotation reminder** when the secret expires.

4. ⬜ **Configure token settings** (Token configuration → Add optional claim → ID token → `email`). Required so the callback can match the Entra identity to a local `app_user` by email. Also add `preferred_username` for display name.

5. ⬜ **Set logout URL** (Authentication → Settings → Front-channel logout URL): `http://localhost:8000/auth/logout` (dev). Update to `https://` for production.

6. ⬜ **Restrict access** (Enterprise applications → Governance → Properties → Assignment required = Yes). Then assign users under Users and groups. Without this, any PremiumIQ tenant user can authenticate.

7. ⬜ **Production redirect URI**: add `https://{app_hostname}/auth/callback` before go-live. Entra blocks `http://` for non-localhost redirect URIs in production.

#### In the application (code changes for v2)

8. **Add MSAL dependency** (`msal` Python package). Configure in `app/auth/oidc.py`:
   - `AUTHORITY = https://login.microsoftonline.com/{ENTRA_TENANT_ID}`
   - `SCOPES = ["openid", "email", "profile"]`
   - `REDIRECT_URI = ENTRA_REDIRECT_URI env var`

9. **Implement OIDC routes** behind `AUTH_MODE=oidc` guard:
   - `GET /auth/login` → generate PKCE code verifier + challenge, store in session, redirect to Entra authorization endpoint
   - `GET /auth/callback` → exchange code for tokens (MSAL `acquire_token_by_auth_code_flow`), extract `oid` + `email` from ID token claims, upsert `app_user` (create if new, update `last_login_at`), create `user_session` row, set session cookie, redirect to home
   - `POST /auth/logout` → invalidate `user_session`, redirect to Entra logout endpoint (`https://login.microsoftonline.com/{tenant}/oauth2/v2.0/logout?post_logout_redirect_uri=...`) to clear the Entra session too

10. **State parameter validation** — the `state` parameter in the OIDC flow must be a random value stored in the pre-auth session and validated on callback. Mismatch aborts the flow (CSRF protection for the OIDC redirect).

11. **Token storage** — ID token and access token are never stored in the browser or in the `user_session` row. The session row records only the local `user_id`. Tokens are discarded after identity is confirmed.

12. **Auto-provision on first login** — on callback, if no `app_user` row exists with `entra_oid = oid_claim`: insert a new row with `email`, `display_name`, `entra_oid`, `is_active=true`, `password_hash=null`. Log the auto-provision. Do **not** assign roles automatically — require admin action before the user gains any privileged role.

13. **Migrate existing password accounts to SSO** — for each `app_user` that has a `password_hash` and whose email matches an Entra user: set `entra_oid` from Entra, set `password_hash = null`. Run as an admin CLI command (`python -m app.cli migrate-accounts-to-sso`). Accounts not yet in Entra keep their `password_hash` until manually migrated.

14. **Env vars (already in `infra/.env.example` as `ENTRA_*`):**
    ```
    AUTH_MODE=oidc
    ENTRA_CLIENT_ID=<Application (client) ID from Azure Portal>
    ENTRA_TENANT_ID=<Directory (tenant) ID from Azure Portal>
    ENTRA_CLIENT_SECRET=<client secret value>
    ENTRA_REDIRECT_URI=http://localhost:8000/auth/callback   # dev; use https:// in production
    ```

15. **Remove or disable** `AUTH_MODE=password` login route once all accounts are on SSO and the cutover is confirmed stable. Keep the route code behind the mode check — don't delete it until SSO has been running for a full season without issues.

---

### 5.4 Identity vs authorization (both versions)

Hard rule, applies to both auth modes: **identity** (who you are) comes from the auth provider (password check or Entra); **authorization** (what you can do) comes from the app's own tables (`user_role`), evaluated live on every request. The two are never conflated. Authorization is never read from token claims, never cached in the session cookie, and never derived from Entra group membership.

---

### 5.5 Schema additions for v1 auth

These tables and columns are additions to the WORKBENCH DB schema (handled by Alembic):

**`app_user` additions:**
- `password_hash` — `VARCHAR(255)` nullable. Null for SSO-only accounts.
- `must_change_password` — `BIT` default 1. Set on account creation; cleared after first password change.
- `entra_oid` — `VARCHAR(100)` nullable, UNIQUE when set. Populated in v2.

**New table: `user_session`**
```
id               CHAR(64) PK          -- 32-byte random hex session ID (the cookie value)
user_id          FK → app_user
created_at       DATETIME
last_active_at   DATETIME
expires_at       DATETIME             -- absolute cap: created_at + SESSION_ABSOLUTE_TIMEOUT
ip_address       VARCHAR(45)          -- IPv4 or IPv6
user_agent       VARCHAR(500)
invalidated_at   DATETIME nullable    -- set by logout or admin force-logout
```

**New table: `login_attempt`**
```
id               INT PK identity
email_tried      VARCHAR(255)         -- what the user typed; not FK (may not exist)
ip_address       VARCHAR(45)
succeeded        BIT
attempted_at     DATETIME
```
Index: `(email_tried, attempted_at)` and `(ip_address, attempted_at)` for rate-limit window queries.

---

## 6. Feature: Authorization

> **No row-level security (CR-003, constitution Article 6 v3.0.0).** The workbench has **no customer isolation**: there is no `customer_id`, no `apply_scope()`, and no `user_customer_access`. Every authenticated analyst can view — and act on — every submission and everything under it. Roles gate *functions*, never *rows*. `assigned_analyst_id` is a soft owner used for the "my submissions" filter, not an access boundary. This section replaces the earlier customer-access/RLS model wholesale.

### 6.1 Roles

Global roles in v1. Exact codes TBD with the team; at minimum: `analyst`, `admin`. Roles gate manifest nodes and actions (which *functions* a user may invoke — e.g. admin-only user management), checked server-side via `require_role(*allowed_roles)` dependency. Roles never restrict which rows a user can read or write — all authenticated analysts see all deals.

### 6.2 Analyst-centric views

Ownership is a plain list filter: `WHERE assigned_analyst_id = current_user.id`. The submission list defaults to it, and the Owner filter switches to another analyst or to every owner. This reflects the real workflow: analysts each own a deal end-to-end during peak season. It is a convenience filter over data everyone can already see — never an access restriction.

### 6.3 Admin maintenance

Admin rail destination maintains users and role assignments only (there is no customer-access grant to manage). Building it early makes role-gating testable end-to-end immediately.

---

## 7. Feature: Domain model — Submission and data associations

### 7.1 The deal is the root

**Submission is the top-level entity** (CR-003 M1). There is no Customer or Program above it. A submission models a *deal*: a specific cedant's specific treaty at a specific inception, related directly to zero or more EDMs and RDMs and tracked for the business by zero or more CRM IDs. It anchors contextual navigation and records optional request provenance for jobs. No entity carries `customer_id` (no RLS — §6).

### 7.2 Submission fields

The analyst's unit of work. Fields (schema: DATA_MODEL.md §4):
- `id` (surrogate UUID — the real key), `name` — the naming-convention label (e.g. `TY2604_AmericanFamily`), a human label that is **not unique** (§7.2b)
- `cedant_name` — plain string, primary filter, kept consistent via autocomplete over existing values (no `cedant` table — that would re-create `customer` under a new name, CR-003 O3)
- `treaty_type_code` FK → `treaty_type_kind` — deal-level treaty type, primary filter (kind table, Article 3)
- `inception_date` — primary filter
- `treaty_year` — nullable; defaults to the inception year and stays editable (CR5), for renewal-year grouping
- `links_to_submission_id` FK → `submission` — nullable self-reference to a related submission, **manual** (no treaty-system integration to infer it — CR-003 O4). Labelled "links to" and picked by name, not id: the relationship is a link to a related deal, not necessarily a renewal (design note 08 CR8, superseding `renews_from_submission_id`)
- `directory_path` — nullable; the per-deal shared-drive directory the analyst stages files in. Seeds the file browse location and the naming-convention parse; there is no directory *inventory* (§8)
- `assigned_analyst_id` FK → `app_user` — soft owner for the "my submissions" filter (§6.2), **not** an access gate
- `status_code` FK → `submission_status_kind` — cached current status (§7.2a)
- CRM identifiers attach as a **0..N tag set** via `submission_crm_id` (hand-entered, optional, editable, may be absent or mistyped, many per deal), **not** a single `crm_id` column (CR-003 M3/O6)
- `created_at` and the standard audit columns

A submission has:
- Zero or more **EDM records** through `submission_edm`
- Zero or more **RDM records** through `submission_rdm`

The same EDM or RDM may relate to several submissions. Adding an existing resource
inserts only the association. Removing it from one submission deletes only that
association and does not delete or re-import the Risk Modeler resource.

A submission's progress is derived from its jobs and entity state (§12–14: IRP Jobs, RWB Jobs, and the prerequisite gate), not from a stored workflow.

### 7.2a Submission status

Three values only, event-sourced (insert `submission_status_event` + stamp cached `submission.status_code`, in one transaction, per the standard convention):

| Status | Meaning |
|---|---|
| `ACTIVE` | Open — fully editable: the analyst can edit its fields and CRM-ID tags, set its directory, and add or remove EDM/RDM associations. |
| `COMPLETED` | Closed for tracking purposes. The submission is **read-only** — all analyst-initiated edits, including EDM/RDM association changes, are blocked; viewing continues. Reopening to `ACTIVE` restores edit capability. |
| `CANCELLED` | Withdrawn — the analyst is no longer pursuing it. Read-only in the same way as `COMPLETED`, and likewise reopenable to `ACTIVE` (with no delete, reopening is the recovery path for a mistaken cancel). |

Rules:
- **Reopening to `ACTIVE` is allowed from either `COMPLETED` or `CANCELLED`** — set it back to `ACTIVE` and work resumes. Neither closed state is a one-way door; because there is no delete (below), reopening is also how a mistaken `CANCELLED` is recovered.
- **Both closed states are fully read-only.** `COMPLETED` and `CANCELLED` alike block edits to the submission's own fields, CRM-ID tags, and EDM/RDM associations. The only actions on a closed submission are viewing and reopening.
- **No system-enforced precondition on any transition.** The analyst decides when a submission is done or withdrawn. The system does not block `ACTIVE → COMPLETED` because an import is still running.
- **There is no file-inventory scanning to keep running on a `COMPLETED` submission** — the scanner subsystem is dropped (CR-003 M5, §8); the only ongoing operation is viewing.
- **There is no delete, ever.** A submission can carry EDMs/RDMs with real Risk Modeler identity by the time anyone would want to remove it — deleting the row would orphan or mis-audit that Risk Modeler-side state. `CANCELLED` exists specifically as the "this isn't happening" outcome in place of a delete.

This replaces the prior `authoring_status` field, whose three-value guess (`draft`/`active`/`complete`) assumed a workflow-authoring lifecycle. `submission.status_code` describes the submission itself, independent of whatever job or workflow machinery runs underneath it.

### 7.2b Submission identity — surrogate key, non-unique label

`submission.name` is **not unique.** The July 9 CIC session established that two genuinely distinct deals can share every naming-convention attribute — same cedant, same inception, same treaty type (e.g. a regional cat and a corporate cat incepting the same day) — and differ only by the **manual, optional CRM ID** (design note 03 §4). A DB-level `UNIQUE(name)` would therefore reject a legitimate second deal, or force analysts to mangle the label with a suffix at peak season. So identity rests on the surrogate `id` (UUID); `name` is a human label kept consistent by autocomplete. To still guard against *accidental* re-creation, create/rename runs a **non-blocking** "a similar deal already exists" check (same UX as the EDM/RDM name-collision warning, §9.4) — it warns and lets the analyst proceed, never hard-blocks. *(Resolves the OQ-3 identity/uniqueness tension from design note 03 in favor of a surrogate key + soft warning.)*

> **Note on `cycle` (removed).** The prior data model had a `submission.cycle` field ("e.g. 2026Q1") intended for auto-naming. It has been removed — it modeled a renewal-cycle concept that doesn't correspond to how this team works broker submissions; there is no cycle, just deals (and a nullable `treaty_year`/`renews_from_submission_id` where a renewal relationship actually exists, §7.2). It was only ever consumed by the auto-naming pattern example in §11.2 (Iteration 6, not yet built). The replacement token set is resolved in CR-003: `cedant_name` + `treaty_year` + region + peril (§2.6, §11.2).

### 7.3 Submission UI

Master-detail pattern: filterable list (Owner defaulting to the signed-in analyst, plus cedant / treaty-type / inception-date filters) + detail panel. List ergonomics per §20.4. Status badges surface active job counts and review queue depth per submission.

### 7.4 Submission detail — EDM and RDM tables

Submission detail shows separate, always-visible EDM and RDM tables. The EDM table
shows Name and Portfolio count. The RDM table shows Name and Analysis count. Ready
resources show a Risk Modeler link; importing resources do not. Name, Status, and
count are independently sortable in each table, and the selected orders remain
during polling. The tables do not have search. Each table has its own empty state
and add action. The tables do not expand or collapse. Each table refreshes while an
import or subsequent detail backfill for a listed EDM or RDM is pending or running.

An EDM link from this page uses `/submissions/{submission_id}/edms/{edm_id}` so
the source submission is explicit. The direct `/edms/{edm_id}` library route stays
context-free.

---

## 8. Feature: File handling

> **The file-inventory subsystem is dropped (CR-003 M5/O9).** There is no directory inventory, no immutable `file_artifact` model, no reconciliation scanner, no tagging, no discrepancy/drift detection, no upload store split, no ignore ruleset, and no directory error/warning states. The workbench is a cat-modeling tool, not a File Explorer (design notes 01 §2 D8). All of §8.1–§8.8 in earlier drafts is removed and replaced by the flow below.

### 8.1 The flow — pick a file when importing an EDM or RDM

File handling happens from a Submission add action or an EDM/RDM Library import,
not as a standing inventory:

1. The analyst chooses EDM or RDM and browses the shared drive for `.bak`, `.mdf`, or `.csv` files. The browse location may be seeded from `submission.directory_path` (§7.2).
2. The workbench creates one global `irp_edm` or `irp_rdm` row per selected file. A Submission import creates the matching association in the same transaction.
3. Each entity starts one entity-scoped background upload. An RDM imports once against its own exposure set; it is never applied once per EDM.
4. The chosen path is stored directly as `irp_edm.source_file_path` / `irp_rdm.source_file_path` (§9.1/§9.2) — a single string, no versioning.
4. Optionally, **delete-after-transfer**: a per-import checkbox deletes the temporary `.bak` once it has been transferred into DataBridge — these BAKs exist only to move data, and otherwise *"you have thousands of files sitting out there forever"* (design note 03 §6.3). The read-only shared-drive mount (§8.2) is never touched; this deletes only app-created temp files.

### 8.2 The shared drive

The shared drive is mounted **read-only** into the Linux host (CIFS/SMB, least-privilege service account); the app reads to browse and to upload, never writes/moves/deletes broker files. Browsing is a live directory listing, not a scanned-and-cached inventory — there is nothing to reconcile, and no `missing`/`changed`/`present` states to track.

### 8.3 CSV files

A `source_file_path` may point to a `.csv` (ELT/PLT, or exposure too large for an RDM) as readily as a `.bak`/`.mdf` (CR-003 O8). The analyst-facing selection is identical; any difference in how the IRP integration ingests a CSV is an implementation detail, not a separate file type in the model.

---

## 9. Feature: EDM & RDM entity management

### 9.1 EDM as a first-class entity

An **EDM record** (`irp_edm` table) is distinct from the source file that produced it. The EDM record represents the exposure database **as it exists in IRP**, with:
- `name` — the EDM name in IRP (auto-generated from submission context per §2.6, editable)
- `irp_id` — IRP's integer exposureId (backfilled by the poller on import `FINISHED`)
- `status` — `pending_import`, `importing`, `ready`, `error`, `delete_pending`, `deleted` (plain string)
- `source_file_path` — nullable string; the shared-drive path of the `.bak`/`.mdf`/`.csv` this EDM was created from (CR-003 M5; replaces the `file_artifact_id` FK — there is no file-artifact table)
- `created_by_irp_job_irp_id`, `as_of` — creation lineage + last-confirmed-against-IRP trust signal
- `server_name` — the DataBridge server the EDM lives on
- Submission associations are stored in `submission_edm`; there is no `submission_id` or grouping FK on `irp_edm`.
- `notes` stores an optional 250-character note shared across every submission related to the EDM.

The EDM is a **DataBridge SQL database** (persistent, storage-limited — never duplicated). There is no `submission_id` or `customer_id` on the EDM. `submission_edm` supplies the many-to-many organization and never restricts row visibility.
- Soft-delete via `deleted_at`

**EDM operations** (MVP spine is import; create-fresh / upgrade / delete are out of the MVP spine, `mvp-scope.md §1`):
- **Import from .bak/.mdf** (the MVP spine) — `client.edm.submit_edm_import_job(edm_name, file_path, server_name)` → `irp_job_type = import_edm` (uploads to S3 first, inside the library — a heavy submit). Both `.bak` and `.mdf` database files are accepted (`mvp-scope.md` row 4).

All async operations create an `irp_job` row and are polled by the poller.

### 9.2 RDM as a first-class entity

An **RDM record** (`irp_rdm` table) tracks a broker-supplied results database in IRP:
- `name` — the RDM name in IRP
- `irp_id` — IRP's integer id (backfilled on import `FINISHED`)
- Submission associations are stored in `submission_rdm`; there is no `submission_id`, grouping FK, or `edm_id` on `irp_rdm`. Importing an RDM creates broker analyses with `irp_analysis.edm_id` null.
- `status` — `pending_import`, `importing`, `ready`, `error`, `delete_pending`, `deleted` (plain string)
- `source_file_path` — nullable string; the shared-drive path of the `.bak`/`.mdf`/`.csv` this RDM was created from (CR-003 M5; replaces `file_artifact_id`)
- `created_by_irp_job_irp_id`, `as_of`
- `notes` stores an optional 250-character note shared across every submission related to the RDM.
- Soft-delete via `deleted_at`

No `submission_id`/`customer_id` — same as the EDM. `submission_rdm` supplies the many-to-many organization and never restricts row visibility.

Every authenticated analyst can edit the shared EDM or RDM note from its detail
page or in the submission table cell. Double-clicking the table note or selecting
Edit opens the plain-text editor; Enter saves. Submission tables show the complete
wrapped note. Library tables do not show notes. Blank input clears the note.
Concurrent edits require confirmation before one analyst replaces another analyst's
saved note.

**RDM operations:**

- **Import from .bak/.mdf** — `client.rdm.submit_rdm_import_job(rdm_name, rdm_file_path, exposure_set_name=rdm_name)` → `irp_job_type = import_rdm` (uploads to S3 first). The import runs once per RDM and does not accept an EDM.
- **Retrieve broker results (REST)** — once imported, the RDM's broker analyses are cached as `irp_analysis` rows with `rdm_id` set; their loss numbers are retrieved via the **same REST result endpoints as own results** (§15.3) into each row's `loss_results` extract, once per `rdm_id` by construction (§16.1, §17.2). **Not** a DataBridge query.
- ~~Export to Loss Repository~~ — pushing broker results to the Loss Repository is **out of MVP** (FR §7; §17.4).

### 9.3 EDM library & RDM library

Rail destinations under "Moody's IRP" that show all EDMs / RDMs across submissions (global — every analyst sees all of them; no customer scoping, §6). Entry points for:
- Importing new EDM/RDM files
- Viewing import job status
- Relating an existing EDM/RDM to a submission without re-import
- Triggering DataBridge validation and profiling (§10)

### 9.4 Direct Submission associations

A Submission relates directly to EDMs through `submission_edm` and to RDMs
through `submission_rdm`. Both tables use composite primary keys and insertion
audit columns. One EDM or RDM can relate to several Submissions without copying
the workbench row or Risk Modeler resource. There is no direct EDM-to-RDM
relationship.

**Add flow:** an Active Submission offers Import new and Add existing actions for
each entity type. Import new writes the entity and association before dispatching
the entity-scoped upload. Add existing lists every live entity not already related
to the Submission and inserts only the selected associations. Duplicate or stale
selections are rejected by the write predicate.

**Name-collision check.** EDM and RDM names must not be duplicated on Risk
Modeler. Import checks the proposed name through `search_edms()` or `search_rdms()`.
A collision blocks import until the analyst renames the resource. When Risk
Modeler cannot be reached the check fails open with a visible warning; submit-side
validation remains the backstop.

**Remove from Submission:** detaching an EDM or RDM deletes only the named
association. It never changes the entity row, starts a worker, or deletes anything
from Risk Modeler. Physical deletion is outside the MVP. Completed and Cancelled
Submissions reject add and detach actions until reopened.

Schema: DATA_MODEL.md §4 (`submission_edm`, `submission_rdm`) and §5
(`irp_edm`, `irp_rdm`).

---

## 10. Feature: Phase A — Data validation & exposure modification (DEFERRED — out of MVP)

> **Out of MVP (`mvp-scope.md §6`).** This section is the *deferred* Phase A vision: SQL-based validation reports, exposure profiling, DataBridge data-element modification, and the Exposure Repository load. **None of it is built for the MVP.** The MVP data-shaping capability that *is* built — sub-portfolio creation and one-click breakouts — is a distinct, synchronous IRP path and lives in **§10A (Portfolio management)**, not here. This section is retained only so the deferred scope and its re-entry path stay documented (it slots back in as its own iteration if picked up — §21). Do not build from this section for the MVP.

### 10.1 Purpose

Before any analysis runs, the analyst would (in the deferred vision) validate the quality of the imported EDM and load summary data to the Exposure Repository. This ran entirely through DataBridge (`client.databridge`). **Sub-portfolio creation is no longer part of this section** — it is the synchronous IRP path in §10A.

### 10.2 Validation & profiling

SQL-based checks and exposure profiles run against the imported EDM via DataBridge. The workbench ships with a library of standard validation queries (stored as SQL files under `app/databridge_queries/`). Each validation job:
- Connects to the EDM's DataBridge server via `client.databridge.get_connection(server_name, database=edm_name)`
- Executes validation SQL (parameterized, `{{ param }}` substitution)
- Returns a `pd.DataFrame` written to a `validation_result` table in the Metamodel DB for display

Validation categories (initial set, extensible):
- **Quality checks** — null coverage, geocoding hit rate, construction/occupancy distribution
- **Consistency checks** — currency consistency, limit/deductible relationships
- **Completeness checks** — required fields, geographic coverage
- **Portfolio summaries** — total insured value by portfolio, geography, LOB

### 10.3 Exposure modification via DataBridge (deferred)

In the deferred Phase A vision the analyst runs exposure modification operations via DataBridge:
- ~~Create sub-portfolios~~ — **moved to §10A** and reimplemented as the **synchronous IRP `create_portfolio()`** path; it is *not* a DataBridge operation. (This resolves the earlier §1.3/§10.3-vs-§14.3/§15.5 contradiction in favor of the IRP path.)
- Modify data elements (e.g., construction class mapping, currency normalization) — **out of MVP** (FR §3).
- ~~Create peril-specific portfolios~~ — **out of MVP**: "we don't have to split it up by peril" (FR §3; verify separately whether RM adds a *missing* peril). *(Superseded 2026-08-12, spec 005 P-19 rev.: peril shipped as a quick breakout dimension.)*

The remaining (deferred) operations would run as DataBridge SQL commands via `client.databridge.execute_command(query, params, ...)`, logged in the audit log.

### 10.4 Load to Exposure Repository (deferred)

After validation passes, the analyst would push pre-aggregated exposure summaries to the **Exposure Repository** (separate SQL Server connection `EXPOSURE_REPO_URL`), via a Dramatiq worker action (`push_exposure_summary`, §16.5). **Out of MVP** with the rest of Phase A.

---

## 10A. Feature: Portfolio management (sub-portfolios & breakouts) — **IN MVP**

> **Distinct from §10.** This is the MVP data-shaping capability: creating **filtered sub-portfolios** of an EDM, synchronously, via IRP `create_portfolio()`. It is *not* the deferred DataBridge exposure-modification path (§10.3). Data-element edits and merge/combine are out of MVP. Peril splits are **in**, as a quick breakout dimension (spec 005 P-19 rev., 2026-08-12).

### 10A.1 What this is

Reshaping exposure to match treaty terms *before* analysis, by creating filtered sub-portfolios. This cannot be done in the current workflow tool (done in RiskLink today, which is slow); Risk Modeler makes it fast and synchronous, so it becomes a **preferred** path (FR §3). It is the fastest operation in the whole flow — HTTP 201, no job.

### 10A.2 The portfolio model

- Portfolios **arrive with the EDM** (broker-supplied — "sometimes 1 portfolio, sometimes 25", FR §2.2). An EDM drills down to its portfolios (§1.4 contextual nav depth: Submission → EDM → Portfolio); analyses run against a portfolio, never the whole EDM.
- A **sub-portfolio** is a new portfolio created by **filtering** an existing one — e.g. isolate a state with a different retention, or exclude a line of business the treaty doesn't cover. Backed by `irp_portfolio` (§15.5): `edm_id` FK, `irp_id`, `name`; synchronous creation, so no `created_by_irp_job` lineage.
- **Creation granularity is capped at state/country.** Finer cuts (CRESTA, ZIP) are saved as *results*, not portfolios — "too much to manage" (FR §3).
- **Regions are not pre-defined constants.** "Northeast" is defined by the treaty / how the cedent writes the business; the analyst composes a region from states rather than picking a fixed list.

### 10A.3 The current-split view

Before deciding how to re-group, the analyst can see the EDM's **current portfolio split** — how many portfolios exist and what each covers (FR §3: "the current split is visible before deciding how to re-group"). This is the entry point for the breakout actions below.

### 10A.4 Creating a sub-portfolio by filter

- Filter values are picked from the **real values present in the portfolio** — a pick-list, **not free text** ("people put crazy things in the LOB field," and typing them exactly is messy — FR §3).
- Filter dimensions in scope: **line of business**, **geography (state/country)**, and the other native account-filter fields Risk Modeler exposes.
- The filter maps to a Risk Modeler **native account-filter**. The resulting portfolio is **account-bucketed**, so slices can double-count and cannot be made perfectly "pure" (memory / `moodys-portfolio-filter-lob`). This matters most for the geographic breakouts below.
- Submission is **synchronous**: `create_portfolio()` returns `(portfolio_id, request_body)` (HTTP 201 + Location); the service writes `irp_portfolio.irp_id` inline (§14.3, §15.5). No `irp_job`, no poller.

### 10A.5 One-click breakouts *(Ben to build, 7/16)*

Beyond a single filtered sub-portfolio, the analyst can fan a source portfolio out in one action. Each generated portfolio is an ordinary synchronous `create_portfolio()` call; a breakout is an **app-side loop** over that call (the same pattern as batch analysis, §14.3), each producing its own `irp_portfolio` row.

- **By line of business** — one sub-portfolio per LOB. Simplest case; unaffected by the geography problem below.
- **By state/country** — one sub-portfolio per geography.
- **Complement split ("X vs. not-X")** — one portfolio for a selected set (e.g. the Northeast states) and one for everything else, from a single action.
- **"Do the opposite"** — produce the complement of a defined filter without re-coding it (define "Florida mobile home" once, and also get "everything that's not Florida mobile home").
- **Breakouts sum to 100%** of the source portfolio — not "run the whole thing, then a subset, and subtract" (FR §3).

> **Resolved by product direction (2026-07-29 — O6-1/O6-2).** Risk Modeler assigns **whole accounts**: an account matching several values lands in full in each matching sub-portfolio, and no location-level toggle is awaited. The **geography breakout ships now** with that behavior accepted and **disclosed** — the preview quantifies the overlap for the portfolio being broken out (spec 005 FR-007) and states that blank-value exposure lands in no sub-portfolio. The complement split remains a fast-follow.

### 10A.6 Prerequisite gate

Sub-portfolio creation is enabled once an EDM with **≥1 portfolio** exists (portfolios arrive with the EDM), per the gate (§13.1). The gate rule for this op is built as part of the Iteration 4 slice (§21).

### 10A.7 Out of scope (FR §3)

Data-element modification (construction/currency normalization); **merge/combine portfolios** (recombination happens on **results** — grouping, §16.4 — not on exposure); geographic granularity finer than state/country as a portfolio; portfolio deletion (not addressed by the FR — treat as out of MVP unless requested). Peril-specific portfolios are **in** (spec 005 P-19 rev., 2026-08-12) — verify separately whether RM adds a *missing* peril.

---

## 10B. Feature: GeoHaz (hazard lookup) — **IN MVP**

### 10B.1 What this is

An **optional** pre-analysis operation that runs Moody's hazard lookup on a portfolio. In this workbench it is **hazard lookup only** — **geocoding is not re-run** (broker geocoding is preserved; Cheryl has never re-geocoded in this role). Re-geocoding, if ever needed, is done intentionally *inside the model*, not as a workbench action (FR §5).

The action lives on the **EDM/portfolio summary page**: the analyst selects **one or more portfolios** and clicks Run hazard lookup once; no parameter modal opens. The workbench submits **one geohaz job per selected portfolio** (design sessions 2026-08-07 and 2026-08-14).

Async: `client.portfolio.submit_geohaz_job(portfolio_name, edm_name, ...)` → `irp_job_type = geohaz` (§14.3), polled via `client.portfolio.get_geohaz_job(id)` (§14.4). *(Confirm the exact `submit_geohaz_job` parameter set against the installed `irp-integration` wheel before implementing — §14.3.)*

### 10B.2 Fixed DLM parameters

Every launch uses the same parameter set. The analyst does not review or change the parameters in the workbench.

- **Data version** — a configured value (`HAZARD_DATA_VERSION`, v25 as of now); Risk Modeler has no `"latest"` resolution, so this is bumped by config edit as Moody's ships new versions.
- **Model family** — DLM (non-HD).
- **Perils** — earthquake and windstorm. Running an inapplicable peril returns **zero for that layer, not a failure** (e.g. earthquake on a windstorm book).
- **Previous hazard results** — do not skip previously looked-up locations; overwrite user-defined hazard values ("the more comprehensive the data, the better").

### 10B.3 Result

The hazard job returns a **summary of locations looked up per layer**, shown to the analyst when it completes. Exactly which fields of the completion response the workbench records is decided at Iteration 5 spec time (O8-3).

### 10B.4 On-screen display: Hazard Version column + app-side lineage

The 2026-08-07 design session settled "Geocode and hazard information on the screen — no. Ability to
execute hazard lookup from the screen — yes." Approver direction on 2026-08-17 (P-03/P-07) added a
Hazard Version column to the portfolios table, superseding the "no version stamp" framing this
section originally carried.

- The portfolios table's final column is **"Hazard Version"**. It shows **SUBMITTING** while the
  worker sends a job, the Risk Modeler job status while a geohaz job is non-terminal, and otherwise
  the portfolio's **raw stored `hazardVersion`** from Get Portfolio Metadata (empty when absent).
  Status refreshes by polling the workbench (§14.7 SSE lands with Iteration 6 and can replace the
  polling).
- The stamp still gates nothing: the workbench **never reads `hazardVersion` to gate an action** — a
  live analysis on parcel-geocoded data with no stamp succeeded, so the stamp is not evidence of
  geocode state (O8-1 tracks confirming its origin with Moody's).
- Expanding a portfolio row shows the **workbench's own execution history**: the most recent geohaz
  lookup's parameters and result, from `irp_job` rows with `irp_job_type = geohaz`. Which execution
  details are recorded and displayed per lookup (data version, perils, per-layer counts, …) is
  **O8-3**, settled at Iteration 5 spec time.

### 10B.5 Prerequisite gate & relationship to analysis

Enabled once an **EDM + portfolio** exist (§13.1). Hazard lookup is **optional** and is **not a hard prerequisite for analysis** — broker exposure is usually already geocoded/hazarded, so analysis is not gated on a geohaz job having run. (This is why the Analysis gate row, §13.1, does not list geohaz.)

> **Open questions (Cheryl investigating, FR §5).** (1) Whether hazard retrieval must be run ahead of time for **HD** models is unconfirmed (O7-1). (2) **Enhanced risk data** is not used today, may be HD-only, and its availability / whether CIC wants it is being checked (O7-2).

---

## 11. Feature: Analysis templates & template suites — **IN MVP**

> **In scope for the MVP** (practice-lead call, 2026-07-06 — reverses the CR-002 deferral). The batch problem below is the #1 analyst pain point, so saved templates and suites ship rather than being built only on demand.
>
> **Suites first (2026-08-14, D9).** Build order reversed: template/suite definition and administration land **before** individual analysis execution (Iteration 6 before Iteration 7 — §21). CIC thinks in outcomes — "pick these five portfolios, pick these three treaties, run the Canada suite" — and today must explode that thought into model profiles, perils, regions, and output selections by hand. The suite is the top-level object the analyst reaches for; single-analysis execution is the substrate under it, not the first deliverable.

### 11.1 The batch problem

A worldwide reinsurance contract may require 50–150+ individual model/region/peril/treaty combinations, each historically configured manually. This is the #1 analyst pain point. The workbench solves it with **analysis templates** and **template suites**.

### 11.1a Profiles & per-analysis settings (FR §4)

The configurable inputs a template captures, and how the builder exposes them:

- **Model (DLM) profiles** — selected from a **pre-compiled list**; profiles are created and managed in Risk Modeler, the workbench only selects. **Multiple** model profiles can be selected for one portfolio/treaty combination (each yields its own analysis). **User-defined (UD) profiles** are supported and selectable (naming convention `UD` + initials, e.g. `UDCT`). Long profile lists are **filterable** ("just get to UDCT").
- **Output profiles** — also from a pre-compiled Risk-Modeler-managed list.
- **Event-rate scheme** — configurable per analysis (people are "very picky" about it). **DLM requires** an event-rate scheme; **HD makes it optional** — determined by the model profile, not the file (§11.4, §13.3).
- **Analysis settings, surfaced with defaults** — **min loss threshold**, **max loss events**, **franchise deductible**, and **unrecognized occupancy treatment** appear in the template builder pre-filled with defaults (spec 009 FR-005; supersedes the earlier plan to hold min-loss/max-event unsurfaced).
- **Currency** — **templates store no currency** (design note 17 D4, 2026-08-20 — reverses spec 009 P-10). Analysis currency, currency scheme, and scheme vintage are chosen **at submit time in the execution modal, per chosen suite** (D5); the pickers pre-fill from **pinned env-var defaults** the analyst confirms every run (D6/D7 — ops changes the default, the system never flips it when a new vintage releases, so templates never go stale). The submit-time currency block is `{code, scheme, vintage, asOfDate}` with `asOfDate` derived from the chosen vintage's effective date. A genuinely mixed-currency book runs as separate regional suites; per-country currency inside one suite is configured directly in Risk Modeler. The workbench does not build or import schemes — those are built in Risk Modeler.
- **Treaties** — selected **explicitly at run time in the execution modal** (spec 009 P-09 dropped the template-stored name pattern); the selection still resolves by name (name-based coupling, §13.2).
- **Tags** — set per template, stored as names; Risk Modeler resolves and creates tags at submit time.
- **Analysis metadata screen (2026-08-14).** Model profiles, output profiles, accumulation profiles, and currency schemes are **viewed in the workbench, created/edited in Risk Modeler, and synced back** (IRP metadata sync, §15.2) — the same "selected, not owned" pattern as EDM data. Event-rate schemes are selected, never authored (CIC does not create custom event rates).

### 11.2 Analysis template

One analysis definition — "one row in Analysis Builder": model profile + output profile + event-rate scheme (required for DLM, optional otherwise) + analysis settings (2026-08-14, D10; shipped Iteration 6, spec 009; currency moved to submit time 2026-08-20, note 17 D4 → spec 009 P-11). Stored in the Metamodel DB as `analysis_template`:
- `name` — template name, unique
- `created_by` — authorship only; templates are **global** (visible to all analysts; CR-003 M2/O1 — no customer isolation). Create/edit/delete is **admin-role-gated** (spec 009 P-01)
- `analysis_profile_name` — IRP model profile name (DLM/HD/Accumulation classification derived from the cached profile, §11.4)
- `output_profile_name`
- `event_rate_scheme_name` (nullable — required for DLM, optional for HD/Accumulation)
- `tag_names` — IRP tag names to apply (autocomplete over names already used; RM resolves and creates tags at submit)
- `min_loss_threshold`, `num_max_loss_event`, `franchise_deductible`, unrecognized-occupancy treatment — surfaced in the builder with defaults (spec 009 FR-005)

**Dropped during Iteration 6 (spec 009):** `treaty_name_pattern` (P-09 — treaties are picked at run time in the execution modal, §11.3a), `currency_code` / `currency_scheme_code` / `currency_vintage` (P-11 — chosen at submit time), `region_label` / `peril_code` (P-03 — region and output level are conveyed by names, not stored; closes O14-3), and `auto_name_pattern` (analysis names follow the fixed portfolio + template name rule, §2.6). A template whose saved reference value disappears on re-sync is flagged unresolved, never silently changed (spec 009 FR-011).

### 11.3 Template suite

An **unordered set of templates** (spec 009 P-08 — no item order, no per-item settings), conceived by **region + output level**, both conveyed by the suite's **name** rather than stored fields (P-03); the other settings are standardized within the suite (2026-08-14, D10). LOB (property / auto / workers comp) is a further axis carried via template tags and naming convention (O14-8). Stored as `template_suite` + `template_suite_item`:
- `name` — suite name (e.g., "Global 2026 Q1"), unique; conveys region and output level
- `created_by` — authorship only; suites and templates are global (visible to all analysts; CR-003 M2/O1). Create/edit/delete is admin-role-gated (P-01)
- Items link to `analysis_template` rows — a plain set; a template appears at most once per suite

**Suites are predefined, not freeform user-built (2026-08-14, D11).** Predefined suites are how CIC enforces consistent settings ("are you running the US defaults with our settings?"); analysts use what's available, and only the exception path drops to a long list or Risk Modeler. The **suite administration surface** (create/edit templates and suites, admin-gated) shipped in Iteration 6, with **duplicate-and-edit** (spec 009 P-12) as the fast path for variants. **Starter-suite seeding and CSV/Excel export + import are deferred out of MVP** (spec 009 P-02) — initial setup, including any starter suites, is manual via the admin page; the Excel transfer design is retained in `specs/009-template-suites/contracts/transfer-workbook.md`.

**A suite may mix DLM, HD, and accumulation templates (2026-08-14, D14).** US wildfire is HD-only while most US perils are DLM; Japan has both ("Japan DLM suite" and "Japan HD suite" coexist). Keeping DLM and accumulation in separate suites is a **convention, not a rule**. Reinforces `event_rate_scheme_name` nullable-for-HD (§11.4).

### 11.3a Executing suites and templates — portfolio-first, modal, direct submit

*(2026-08-14 D13/D14; reshaped 2026-08-20 for Iteration 7 planning. Suite execution ships first; single-template execution follows — §21 Iteration 7.)*

- **Portfolio-first:** the analyst selects **one or more portfolios** (on the EDM detail page's portfolio table), then chooses **Execute Suite** or **Execute Template**.
- **Modal selection:** a modal lists the suites (or templates) with a **simple search**. **Several suites — or several templates — can be chosen in one execution; suites and templates are never mixed** in the same execution. **Submit is disabled until at least one is chosen.**
- **Expand-to-deselect:** a chosen suite expands inside the modal into its template list so the analyst can deselect what doesn't apply (e.g. flood not covered by the treaty); the rest still submits (D13). There is no separate review page.
- **Currency is confirmed in the modal, per chosen suite** (2026-08-20, note 17 D4/D5): analysis currency + currency scheme + scheme vintage, pre-filled from the pinned env-var defaults (§11.1) and overridable; a template run confirms one currency for the execution. Vintages list nested under the chosen scheme.
- **Treaties are picked in the modal** — explicitly, by name, at run time (spec 009 P-09); the selected treaties apply to every submitted analysis.
- **Direct submit:** Submit runs the app-side loop over `submit_portfolio_analysis_job` — **one analysis per selected portfolio × selected template of each chosen suite** (no dedup across suites — a template in two chosen suites submits once per suite, with that suite's currency; dedup dropped 2026-08-20), each producing its own `irp_job` row (§14.3). An `irp_analysis` row is written as each job is submitted and **backfilled with its settings/metadata when the job completes**; loss-number retrieval follows in Iteration 8 (§15.3, §21). Analyses submitted from a submission context carry the **submission's name as a Risk Modeler tag** so they're findable in the platform and via API (2026-08-20, note 17 D12 direction).
- **Naming:** each analysis is named by the fixed rule **portfolio name + template name**, truncated from the right to Risk Modeler's 64-character analysis-name cap; the full name is stored on `irp_analysis` (§2.6).
- **Live on the EDM page:** executed analyses appear in a **user-executed analyses section on the EDM detail page** — presented like the broker-analysis sections but with no RDM grouping, each showing the portfolio it ran against (trustworthy here: the workbench submitted it; the §2.2 trust rule concerns data that left CIC's environment) — and update live as their jobs move through statuses (§14.7).
- **Failure handling is graceful at both stages:** a failure to submit is surfaced immediately and takes the `SUBMISSION FAILED` retry path (§14.3); a Moody's-side run failure is surfaced with its reason. **Peril/portfolio mismatch is expected, not an error:** a broad suite run against data lacking a peril fails those sub-analyses ("no locations match the criteria") and generates no loss → no charge. CIC prefers "run it all, deal with the failures at the end" over interrogating the database first — **but every failure is surfaced with its reason** (the job summary says the peril wasn't present), never silently ignored (D14).

### 11.4 DLM vs HD detection

At batch-apply time, the workbench checks each template's `analysis_profile_name` against the locally cached `irp_model_profile.software_version_code` (`"HD" in code → HD, else DLM`). For DLM templates, `event_rate_scheme_name` is required; for HD, it is optional. The homogeneity check (§13.3) catches any DLM+HD mixing when composing a grouping op.

---

## 12. Feature: Work model — Submission → EDM/RDM → Job

> **Pivot (CR-002).** This app is a **workbench, not a workflow engine.** There is no `workflow`, stage machine, task template, typed port, handle-type registry, or manifest-projection subsystem. A submission's progress is **derived** from its `irp_job` rows and entity state; "what's next" is the prerequisite gate (§13.1), computed in code, not read off a stored `stage.exec_status`.

### 12.1 The spine

```
Submission            deal context (Name + CRM ID); assigned analyst; WORKBENCH-only concept
  ├──< submission_edm / submission_rdm   direct associations to shared EDM/RDM resources
  │       └── work targets the physical EDM or RDM, never an association row
  ├──< irp_job          one IRP operation (async-polled or heavy-deferred); resubmit lineage
  │       └──< rwb_job  app-side post-terminal / analyst-requested / chained work (decoupled, no FK)
  └──< {irp_portfolio, irp_analysis, irp_treaty}   entity artifacts produced by ops (a group IS an analysis)
```

### 12.2 Persistence tiers (the governing principle)

**A construct earns a table only if it must persist after the HTTP response returns.**
- **Entity** — EDM / RDM / Portfolio / Analysis / Treaty: a durable artifact.
- **`irp_job`** — must be tracked after the response: an async IRP poll, or a heavy-deferred submit (e.g. the S3 upload inside an EDM import).
- **`rwb_job`** — app-side post-terminal / analyst-requested / chained work (CR-001, redesigned in CR-002 — see DATA_MODEL §8).
- **Audit** — *deferred, not built* (DATA_MODEL §1, `audit_log`).

Synchronous single ops (create-subportfolio, treaty CRUD) create **no job and no batch** — they persist the entity in-request. **A group is an `irp_analysis` with `is_group=true`**, not a separate entity — viewed/exported identically. There is **no workflow-definition manifest, no projection, no typed ports, no handle-type registry, no stage machine, no version pinning** — those existed to model authored, evolvable DAG topology as data, a problem this app does not have.

### 12.3 EDM & RDM operations

Every async op is tracked as an `irp_job` row and polled by the poller (§14.4). Only the operations below are the MVP analysis spine (`mvp-scope.md §1–§3`); EDM create-fresh / upgrade / delete and RDM write-back are out of the MVP spine and, if revived, map onto the same `irp_job_type` set.

- **EDM import from .bak/.mdf** (the MVP spine) — `client.edm.submit_edm_import_job(edm_name, file_path, server_name)` → `irp_job_type = import_edm` (uploads to S3 first, inside the library — a **heavy** submit). `irp_edm.irp_id` is backfilled by the poller on import `FINISHED`.
- **RDM import from .bak/.mdf** — `client.rdm.submit_rdm_import_job(rdm_name, rdm_file_path, exposure_set_name=rdm_name)` → `irp_job_type = import_rdm` (also heavy). The RDM imports once without an EDM target. Broker analyses are captured by `(rdm_id, irp_id)` with `edm_id` null.

### 12.4 Treaties (view in-app / edit via Risk Modeler pass-through)

> **Reconciled to FR §5 (2026-07-21).** Adding or editing a treaty is a **pass-through to Risk Modeler**, not an in-app editor — "the workbench does not rebuild the RM treaty editor" (reconfirmed 7/16). Treaty **viewing** stays in-app; treaty **create/edit** hands off to RM. This reverses the earlier in-app `create_treaty`/`create_treaty_lob` CRUD design.

A **treaty belongs to an EDM** and is referenced by analyses **by name** (not by id), consistent with the name-based coupling used everywhere else (§13.2). Backed by the `irp_treaty` entity (DATA_MODEL §3b) as a **read/cache** record.

**Viewing (in-app, FR §2.2).** Treaty setup is shown at the **EDM level**. The analyst sees the **full treaty attribute detail** — Cheryl wants every attribute visible to catch mis-coding, not blindly trust it ("sometimes people put the wrong thing in the wrong field"). Treaties **expand and collapse** (a few shown expanded, many collapsed to focus one at a time); wide attribute sets **scroll horizontally** in the compact view; and treaties can be **exported to Excel** for extreme cases with too much to render cleanly. Resolving/listing treaties by name uses the synchronous `treaty.search_treaties` (no job).

**Create / edit (Risk Modeler pass-through, FR §5).** Adding or editing a treaty — and adding or editing reinsurance more generally — **opens the Risk Modeler editor in a new window**; the analyst edits and saves there, returns, and the page refreshes (the general pass-through pattern, design note 04 §7: where the workbench would only re-skin RM, hand off to RM). A pass-through edit creates **no `irp_job`** and **never appears in the job monitor**; on return the treaty view re-reads from RM (`search_treaties`). Editing return periods / interpolation on results follows the same pass-through pattern (§16).

**Out of scope:** cedant-ID checks, treaty-accuracy validation, and location-detail checks (treaty-accuracy is caught manually — the full-attribute display above is what helps the analyst catch it).

---

## 13. Feature: Prerequisite gate, name-based coupling & validation

### 13.1 The prerequisite gate (replaces the stage machine)

"What can the analyst do right now" is computed live from entity existence + job terminal status — a lookup + existence check in code, not a stored `stage_kind.sort_order`:

| Op | Enabled once these exist / are `FINISHED` |
|---|---|
| EDM import | server exists; EDM name not already in RM |
| RDM import | RDM name not already in RM; its standalone exposure-set name is available |
| Create subportfolio | EDM + ≥1 portfolio exist |
| GeoHaz | EDM + portfolio exist |
| Treaty create/edit | EDM exists |
| Analysis | EDM + portfolio (+ named treaties) exist |
| Grouping | member analyses/groups exist (`FINISHED`) |
| Export → Loss Repo | analysis/group exists (`FINISHED`) |

This matches every granular sequence diagram's "Pre-requisites" section — the gate centralizes rules each flow already documents, it does not invent new ones. **GeoHaz (hazard lookup) is optional and deliberately *not* an Analysis prerequisite** — broker exposure is usually already geocoded/hazarded, so the Analysis row above does not require a geohaz job to have run (§10B.5, FR §5). *(Open: whether HD models need hazard run ahead — O7-1.)* **Subportfolio** creation needs `≥1 portfolio` because portfolios arrive with the EDM (§10A.2), and a sub-portfolio filters an existing one.

**Auto-fires vs click-gated.** Import completion automatically starts its own
detail backfill. EDM completion never starts RDM import work. Anything requiring
judgment waits for a click.

### 13.2 Name-based coupling (replaces typed handles)

There is **no typed handle to chain or invalidate.** Each op resolves its inputs **live from Risk Modeler by name at submit time** — `search_edms`, `search_portfolios`, `search_analyses`, `search_treaties`. IRP re-validates names to internal IDs on every submit anyway, so a local typed-port/handle-type registry would only track something IRP already owns. Entity tables reference each other directly (`edm_id`, `group_parent_id`, etc.); a job's produced entity records its creator via `created_by_irp_job_irp_id` (DATA_MODEL §3, §3c).

### 13.3 Validation — at the point of action

Validation is **entity-existence + uniqueness + reference-data**, checked when the analyst acts, not as a two-phase whole-graph pass:
- **Uniqueness** — no duplicate EDM/analysis/group name in RM (checked live via `search_*` before submit; a dup name is retryable).
- **Reference-data** — model/output profiles, event-rate schemes, servers, treaties resolve against the local IRP cache (DATA_MODEL §10); pick-lists resolve locally.
- **Homogeneity** (grouping) — members share a model family (DLM vs HD from cached `irp_model_profile.software_version_code`), checked when composing the grouping op. **`dlm`/`hd` are not types** — DLM vs HD is an analysis-profile property, not a file attribute.

A failed prerequisite surfaces as `irp_job.status = 'BLOCKED'` (the only "needs attention" pre-submit state); it is not a stored stage gate.

---

## 14. Feature: Execution engine, job tracking & result processing

### 14.1 `irp_job` is the tracked unit

An `irp_job` row is **one IRP operation** — the executable unit that replaces the old task-instance. It is tracked (by the poller) after the request returns; `rwb_job` is the SQL-backed queue for app-side work (Article 10). No separate queue technology for IRP submission — submit is synchronous on the request path (§14.3).

### 14.2 Readiness = the prerequisite gate

There is no per-task `blocked→ready` machine. An op is offered to the analyst only when its prerequisites (§13.1) are met; a pre-submit prerequisite failure is recorded as `irp_job.status = 'BLOCKED'`.

### 14.3 IRP job submission

**Submission is synchronous on the request path** (Article 11): the service calls the IRP API directly, records the returned job id, and responds immediately — IRP submit calls return quickly (they enqueue work server-side and hand back a job id), the analyst gets immediate confirmation or an error in the same HTTP response, and there is no benefit to deferring a sub-second call through a queue.

**On submission failure** the `irp_job` row is written with `status = 'SUBMISSION FAILED'` (submission-side — it never reached RM, so there is no `irp_id`) and `submission_attempt_count` incremented. A **single-threaded `submission_retry` batch job** re-attempts eligible rows up to `IRP_SUBMISSION_MAX_RETRIES` (default 3) with backoff; after max retries the job stays `SUBMISSION FAILED` (now terminal). `SUBMISSION FAILED` (no `irp_id`) is distinct from `FAILED` (RM ran it and it failed) — different cause, different retry. There is no bare `ERROR` status.

Each IRP-backed op sets `irp_job.irp_job_type` (a kind-table FK, for poll routing), `irp_id` (RM's returned integer job id, as string), and `submission_attempt_count`:

| Op | IRP call | `irp_job_type` |
|---|---|---|
| EDM .bak/.mdf import | `client.edm.submit_edm_import_job(edm_name, file_path, server_name)` | `import_edm` |
| RDM import | `client.rdm.submit_rdm_import_job(rdm_name, rdm_file_path, exposure_set_name=rdm_name)` | `import_rdm` |
| Geo-coding & Hazard | `client.portfolio.submit_geohaz_job(portfolio_name, edm_name, ...)` | `geohaz` |
| Analysis (single) | `client.analysis.submit_portfolio_analysis_job(edm_name, portfolio_name, job_name, ...)` → `(job_id, request_body)` | `analysis` |
| Analysis (batch) | **loop** `submit_portfolio_analysis_job` app-side, once per item, capturing each `(job_id, request_body)` | `analysis` per item |
| Grouping | `client.analysis.submit_analysis_grouping_job(group_name, analysis_names, ...)` | `grouping` |
| File Export (Parquet) | `client.analysis.submit_analysis_export_job(analysis_id, loss_details)` | `export` |

> **Subportfolio creation is synchronous** — `create_portfolio()` returns `(portfolio_id, request_body)` (HTTP 201 + Location), no job; the service writes `irp_portfolio.irp_id` inline (§10A, §15.5). One-click breakouts loop this call app-side. **Treaty create/edit is a Risk Modeler pass-through (§12.4)** — no IRP job from the workbench; `search_treaties` resolves names synchronously. EDM create-fresh / upgrade / delete and RDM write-back are out of the MVP spine; if revived they map onto the same `irp_job_type` set.

> **Resource URI must be captured at submission time.** `submit_portfolio_analysis_job()` returns `(job_id, request_body)` where `request_body["resourceUri"]` is the portfolio's IRP resource URI — needed later for `get_elt()`, `get_ep()`, etc. Store it as an `irp_job_resource` row (`resource_type='portfolio'`, `resource_uri=...`) immediately after submission — RM's completion response does not return it, so it is otherwise unrecoverable without a separate search call (DATA_MODEL §8).

> **Batch analysis — ordered positional mapping.** The batch submit is an **app-side loop** over `submit_portfolio_analysis_job`, once per item, capturing each `(job_id, request_body)`. Each item gets its own `irp_job` row written in the same order; there is no stage-position index anymore.

> **API method signatures** are from `irp-integration` v0.2.1.dev23 (pre-release). Verify against the installed version before implementing any IRP-backed op.

### 14.4 The poller

Standalone loop process (`app/poller/run.py`). **Not Dramatiq** — a batch operation by design: one pass per interval queries all non-terminal jobs in a single SELECT, groups them by `irp_job_type`, polls IRP for each, and writes results. Run `--loop` in dev (interval from `POLL_INTERVAL_SECS`, default 15s); a supervised service in production.

**Each pass:**
1. **Query non-terminal jobs** from `WORKBENCH`: `WHERE status NOT IN ('FINISHED', 'FAILED', 'CANCELLED', 'SUBMISSION FAILED')`, grouped by `irp_job_type`. App-local rows with no `irp_id` are skipped.
2. **Poll each job** via the **single-status-check** method per `irp_job_type` (never `poll_*_to_completion`, which blocks for up to 600 000 s and would freeze the poller):

| `irp_job_type` | Single-status-check method (poller uses this) |
|---|---|
| `import_edm` / `import_rdm` | `client.import_job.get_import_job(id)` |
| `delete_edm` | EDM delete job getter *(exact single-status getter confirmed against the installed library at planning — A21)* |
| `geohaz` | `client.portfolio.get_geohaz_job(id)` |
| `analysis` | `client.analysis.get_analysis_job(id)` |
| `grouping` | `client.analysis.get_analysis_grouping_job(id)` |
| `export` | `client.export_job.get_export_job(id)` |

> Imports poll via `import_job.get_import_job`, **not** `risk_data_job`/`get_workflow` (the prototype confirms this).

3. **Update `irp_job.status`** (updated in place; `last_tracked_at` stamped). **Backfill entity `irp_id`s** directly on import `FINISHED`.
4. **On terminal status:** write head `rwb_job` row(s) via idempotent insert on the composite key (§14.5). `status == 'FINISHED'` is the only success; `FAILED`/`CANCELLED` are failures.

**`irp_job.status` vocabulary** (plain string; future RM statuses never crash the poller):
- RM-mirrored non-terminal: `PENDING`, `QUEUED`, `RUNNING`, `CANCEL_REQUESTED`, `CANCELLING`
- RM-mirrored terminal: `FINISHED` (only success), `FAILED`, `CANCELLED` (**two-L** spellings, per RM — see `irp_integration.constants.WORKFLOW_COMPLETED_STATUSES`, which cites the Moody's workflow-engine docs)
- App-local: `UNSUBMITTED`, `SUBMITTING`, `BLOCKED`, `SUBMISSION RETRYING` (non-terminal); `SUBMISSION FAILED` (terminal; poller skips these, no `irp_id`)
- `SUBMISSION RETRYING` is the `submission_retry` batch's claim on a row. The status tracker skips it too (no `irp_id`), so the poller reclaims a row left there longer than `IRP_SUBMISSION_RETRY_STALE_SECS` back to `SUBMISSION FAILED`, spending one attempt (FR-015)

### 14.5 RWB jobs & Dramatiq workers

`rwb_job` is app-side work **this app executes** in-process (Dramatiq worker), fully decoupled from `irp_job` (no FK). Each row's `requestor_type` (kind-table FK) + `requestor_id` records what triggered it — an `irp_job` completion, an analyst action, or a parent `rwb_job` (chaining); the composite `UNIQUE(requestor_type, requestor_id, rwb_job_type)` is the dedup/idempotency key (replacing `request_key`). `rwb_job_type` is a kind-table FK. See DATA_MODEL §8 for the full vocabulary.

**Workers** submit one entity-scoped `upload_edm` or `upload_rdm`, backfill EDM
detail or RDM analyses after successful imports, and retrieve loss results after
analysis jobs finish and after RDM imports complete (§15.3). Association detach runs on the request path and never enqueues a worker.
**Out of MVP:** `push_rdm_to_loss_repo` and `push_exposure_summary`.

**Chaining without a depends_on column.** The poller writes only **head** rows. Each worker, on success, creates the next `rwb_job` via idempotent insert with `requestor_type='rwb_job'`, `requestor_id=` its own `rwb_job.id`. `retrieve_analysis_results` never races `finalize_analysis` — it does not exist until the parent succeeds; if the parent fails after Dramatiq retries, the chain stops.

**Claim + heartbeat protocol (CR-001, unchanged):** atomic claim (`UPDATE ... SET status_code='running', claimed_by=:w WHERE id=:id AND status_code='pending'`; rowcount 0 → already claimed, ack and drop); heartbeat daemon thread stamps `rwb_job_heartbeat` every `RWB_HEARTBEAT_INTERVAL_SECS`; work runs inside `with heartbeating(job_id, worker_id):`; on success set `succeeded` + `output_data` and create tail rows, on failure set `failed` + `error_detail` (Dramatiq retries with backoff). Stale `running` rows are recovered by the reconciler folded into the poller.

**Submission retry** is a **single-threaded batch job**, not a concurrent Dramatiq actor and not on the `rwb_job` table (per CR-002 — submission is not long-running, so no worker pool and no `retry_locked_until` lock column). Eligibility is a plain query: `status='SUBMISSION FAILED' AND submission_attempt_count < IRP_SUBMISSION_MAX_RETRIES`.

**Dramatiq broker:** Redis (durable via AOF). Workers start with `dramatiq app.workers`.

### 14.6 Submission progress (derived, not rolled up)

There are no stage/workflow rollups to maintain. A submission's progress is **derived on read** from its `irp_job` rows (which ops are `FINISHED`/running/failed) and its entity rows (which EDMs/portfolios/analyses/treaties exist) — the same signals the prerequisite gate (§13.1) reads. `ERROR` is not a stored status; a failure is a job in `FAILED` or `SUBMISSION FAILED`.

### 14.7 Live monitoring

SSE (`sse-starlette`) streams job status updates to the UI as the poller updates `irp_job.status`. The job-monitor list and status-bar activity zone subscribe. nginx must have `proxy_buffering off` on SSE routes. HTMX polling is the fallback for counts.

---

## 15. Feature: Moody's IRP integration

### 15.1 IRPClient instantiation

`from irp_integration import IRPClient; client = IRPClient()` — reads all config from env vars. No constructor args. Lazy-init with double-checked locking in `get_irp_client()` dependency. Raises HTTP 503 if client cannot connect at first use.

**Auth modes** (auto-selected from env):
- API key: `RISK_MODELER_API_KEY` set → sent in Authorization header
- Bearer login: `RISK_MODELER_TENANT_NAME` + `RISK_MODELER_USERNAME` + `RISK_MODELER_PASSWORD` → client logs in at construction; reactive 401 re-login

Always required: `RISK_MODELER_BASE_URL`, `RISK_MODELER_RESOURCE_GROUP_ID`

### 15.2 IRP metadata sync

"Sync IRP Metadata" rail action fetches and caches IRP reference data into the local `irp_*` cache tables in the Metamodel DB. Feeds op-configuration dropdowns and the point-of-action reference-data checks (§13.3).

What is synced (six sets, spec 009 FR-001):
- model profiles → `irp_model_profile` (includes `software_version_code` for DLM/HD detection)
- output profiles → `irp_output_profile`
- event rate schemes → `irp_event_rate_scheme`
- currencies → `irp_currency`
- currency schemes → `irp_currency_scheme`
- currency scheme vintages → `irp_currency_scheme_vintage`

### 15.3 Analysis results retrieval

Analysis results are **REST-only** — never DataBridge. Retrieved per `perspectiveCode` (`GR` = Gross, `GU` = Ground-Up, `RL` = Reinsurance Layer):

- `client.analysis.get_elt(analysis_id, perspective_code, exposure_resource_id)` → ELT records
- `client.analysis.get_ep(analysis_id, perspective_code, exposure_resource_id)` → EP curves (OEP/AEP/CEP/TCE)
- `client.analysis.get_stats(analysis_id, perspective_code, exposure_resource_id)` → AAL/statistics
- `client.analysis.get_plt(analysis_id, perspective_code, exposure_resource_id)` → PLT (**HD only**)

These are called by the `retrieve_analysis_results` Dramatiq worker (§14.5), not on a request path. **For viewing, the worker calls only `get_stats()` and `get_ep()`** — per perspective (GR, RL, WX, QS, GU), 10 calls per analysis — and writes the bounded extract to `irp_analysis.loss_results` (§16.1). `get_elt()`/`get_plt()` belong to the export iteration: design note 19 D5 (2026-08-25) removed ELTs from viewing scope. The worker fires automatically in both cases (Iteration 8, §21):

- **Own analyses** — when an executed analysis reaches `FINISHED`, chained from `finalize_analysis`.
- **Broker analyses** — when an RDM import completes, chained from `backfill_rdm_analyses`, one job per broker analysis (each a single (`rdm_id`, `irp_id`) row, so storage is once per RDM automatically). No analyst action triggers retrieval in either case.

`exposure_resource_id` is the portfolio's IRP resource URI. It is **not** returned by the job completion response — it comes from `submit_portfolio_analysis_job()`'s return value (`request_body["resourceUri"]`) and must be stored in `irp_job.resource_uri` at submission time. The `retrieve_analysis_results` worker reads it from there. Broker analyses have no submission-time capture — their exposure pointer comes from the `exposureResourceId` that `search_analyses` returns, recorded at RDM backfill (spec 004); whether the result endpoints accept it for RDM-imported analyses is confirmed at Iteration 8 spec time (§23 open decisions).

### 15.4 DataBridge usage

`client.databridge.execute_query(query, params, connection, database)` → `pd.DataFrame`

Used for: validation reports (§10.2), exposure profiling, and exposure modification (§10.3) — **all of which are the deferred Phase A** (§10, out of MVP). Connection: `MSSQL_{NAME}_SERVER/USER/PASSWORD` env vars read by irp-integration.

DataBridge **cannot serve analysis results** — **REST only for results, own *and* broker.** (Broker RDM results are retrieved via the same REST result endpoints as own results, deduped by `rdm_id`; §16.1, §17.2. There is no DataBridge query for broker results.)

### 15.5 Portfolio tracking

The `irp_portfolio` table tracks portfolios created within an EDM:
- `edm_id` FK → `irp_edm` (no `customer_id` — dropped with RLS, CR-003 M2)
- `irp_id` — IRP's integer portfolioId
- `name` — the portfolio name in IRP
- Synchronous creation, so no `created_by_irp_job` lineage column (DATA_MODEL §3)

**`create_portfolio()` returns the portfolio ID synchronously** — the IRP endpoint responds with HTTP 201 + a Location header; the library parses this and returns `(portfolio_id, request_body)` before the call returns. The service writes `irp_portfolio.irp_id` on the same request path. The poller is not involved in portfolio ID backfill.

Analysis job submission requires both `edm_name` and `portfolio_name` — IRP resolves these to IDs internally.

### 15.6 IRP constraints

- **Built-in retry** inside irp-integration: 5 attempts, exponential backoff for 429/5xx. Do not add another retry layer.
- **Rate limits / concurrency caps:** honored at the poller level; do not submit faster than IRP allows.
- **IRP availability:** hard runtime dependency for submitting and polling IRP operations. Without IRP, ops that need it are simply not enabled by the prerequisite gate (§13.1); already-imported entities remain viewable.
- **Terminal ≠ success:** always inspect `status == 'FINISHED'` before treating a terminal job as successful.

---

## 16. Feature: Results management & repositories

### 16.1 Analysis results storage — bounded extract for viewing; Parquet + SQL metadata for export only

**Viewing (DATA_MODEL §6 governs).** The `retrieve_analysis_results` worker stores a bounded extract as JSON on `irp_analysis.loss_results`: per perspective (GR, RL, WX, QS, GU), the AAL, standard deviation, and OEP/AEP losses at 11 fixed return periods (5 / 10 / 25 / 50 / 100 / 250 / 500 / 1000 / 2000 / 5000 / 10000) — a few KB per analysis. A perspective the analysis did not produce is present with an explicitly empty value (not a retrieval failure). Every results view reads this extract; **no Risk Modeler call serves a page render**, and no row-level data is stored for viewing. Evidence and rejected alternatives: spec 011 `research.md#R3`.

**Export (DATA_MODEL §9 governs — not built until the export iteration).** Row-level data (ELT, EP, PLT) as **Parquet files on disk** plus an **`analysis_result_meta`** SQL row per (result, `perspective_code`) recording the file paths. Design note 19 D5 (2026-08-25) removed ELTs from viewing scope — they exist only for export to the Loss Repository. **Retrieval is a Risk Modeler export job returning Parquet, pre-fetched (2026-08-28, design note 22 D28/D29 — supersedes the paginated `get_elt()` framing from 8/27).** The workbench submits a job naming the analysis, the ELT at portfolio output level, and the perspective codes; a zip returns one Parquet file per perspective. Pre-fetching puts the file on disk before the analyst opens the export, so the loss-greater-than-exposure check runs on demand at review with no wait. Consequences: `analysis_result_meta.elt_record_count` is a **post-read count of the Parquet file** (`get_elt()` is out of the path entirely — its stated source is wrong twice over); `result_export.location` must hold both the Parquet intermediate and the repository target for one export (the grain question, O21-7); and the **pre-fetch trigger is an open decision (O22-10)** — analysis completion is eager (most perspectives are never exported) and needs storage sizing at 100+ analyses × several perspectives under `{submission_outputs_dir}`. Keep `irp_job_type_kind`'s `export` (this Risk-Modeler-side Parquet job) distinct from `push_results_to_loss_repo` in the seed table and in code.

> **Superseded:** earlier drafts of this section listed row-level SQL tables (`analysis_result`, `elt_record`, `ep_curve`, `plt_record`). Those predate the Parquet-hybrid decision (§23 locked decisions, 2026-07-10) and are **not** built. The 2026-08-25 revision narrowed the hybrid itself to export.

**Broker (RDM) results are deduplicated by `rdm_id`.** A standalone RDM import
captures each Risk Modeler analysis once by `(rdm_id, irp_id)`, so the viewing
extract on that row is once-per-RDM automatically. Own results stay per-analysis.
DATA_MODEL §9 defines the export-only Parquet and SQL metadata storage.

### 16.2 Results review UI

> **Redesigned 2026-08-25 (design note 19).** ELTs are not viewable in the workbench — "we just need the stats, the AAL and the EP curve" (D5). Viewing reads the `irp_analysis.loss_results` extract (§16.1); ELT-derived metrics (max event loss, record count), PLT, and TCE are out of viewing scope.
>
> **Signed off 2026-08-28 (design note 22, D1–D9).** The built views and the comparison builder were demoed and accepted — one change request in forty minutes (right-justify the return periods, D5); Cheryl: "all of this looks great … I think the experience is great." Confirmed: RL + OEP defaults, one EP type at a time, uncapped N-up viewing with sideways scroll (retiring the ~10 soft guideline), the 5-pair comparison cap, the selection-order base contract, the cross-currency comparison block, and clipboard copy unformatted / in ones / full precision. FR §7 carries the row-level status.

Loss numbers appear in three places:

- **One merged analyses table** (D11) — on the submission Results rail section and the EDM detail page. Own and broker analyses in one table: own rows directly (the `CRE_` prefix and absence of an RDM mark them), broker analyses **grouped under an expandable row for the RDM that produced them** — the group-by-RDM requirement relocated, not retired. Own-vs-broker is derived from `rdm_id`; no stored origin column. Columns include **currency** (captured at analysis-detail backfill) and **AAL** (D10); no return-period column; the AAL-only display mode is dropped (D12).
- **Inline in the expanded analysis row** (D9) — the condensed results: one EP type at a time (OEP default — note 20 D11, confirmed 8/28 D1) beside the perspective toggle, no condensed/expanded display toggle. Cheryl's requirement: AAL at a glance as a gut check without leaving the grid.
- **The dedicated results page** (D14, D21, D22) — reached by multi-selecting analyses and choosing View, from **both** the submission page and the EDM detail page (only the submission page reaches cross-EDM analyses and groups). Opens in a **new browser tab**; expanded view by default, one EP type at a time, one column per analysis, user-controlled left-to-right ordering (D15; drag-and-drop deferred 8/28). Breadcrumbs: entry from the EDM retains EDM + submission; entry from the submission retains submission only — **defective as of 8/28** (empty results placeholder, non-link "analyses", contextual back-navigation lost; fix in progress, note 22 O22-15). **The browser tab title carries the submission or EDM name** — a requirement, not a nicety.

Metrics shown (FR §7):

- **AAL / pure premium** and **standard deviation** (from the EP stats endpoint).
- **Return-period losses** — expanded set (11 points): 5 / 10 / 25 / 50 / 100 / 250 / 500 / 1000 / 2000 / 5000 / 10000; condensed set (6): 50 / 100 / 250 / 500 / 1000 / 10000. Fixed sets, not user-editable — editing passes through to Risk Modeler (D7, re-affirming design note 05 §4.1). Closes O5-2.
- **One EP type at a time, OEP default** (note 20 D11; confirmed 8/28 — "No, OEP is great"). Cheryl uses OEP more; showing both meant a merged header that offset the Excel paste.
- **No EP-curve graph is required** — "the drawing's not important… I want the numbers." Re-confirmed 8/25.
- **Units never auto-switch** (D16): a ones / thousands / millions selector, millions default.
- **Copy table with headers** (D13): any rendered results table pastes into Excel — Cheryl's year-over-year working file, and the broker-sent-a-digital-copy case.
- **Analysis metadata** shown alongside (the same metadata list reused for broker-result review, §2.3): engine/model version, engine type (DLM vs HD) + version, analysis type/mode, peril (primary + secondary), region, currency, construction, LOB, group type, long-term vs near-term, event-rate scheme / rate vintage, loss amplification (PLA). Rate/event-rate detail sits one drill-down deeper than the rest.

**Perspective switching is essential** — the five perspectives are **GR, RL, WX, QS, GU** (Ground Up added by Wendy 8/25 for checking treaty application; closes O5-3 — the default view is Gross within this set). The perspective selection applies to the whole screen, never per analysis.

**Density.** Viewing is **uncapped** (confirmed with CIC 8/28, D2 — the ~10 soft guideline was Ben's and never agreed): columns condense to a minimum width, then the view scrolls sideways; demoed at billions-scale values and accepted. A **full listing / full drill-down of all analyses remains available** — anything less is a step back from RiskLink, which lists them all.

- **Comparison** — a separate operation from viewing (D17): strictly pairwise comparisons collected into a cart, with a percent-difference column (§17.3; lands with Iteration 10, not with the Iteration-8 results views).

> **Open question — event-rate scheme round-trip (O5-1).** The event-rate scheme does not appear to survive a Risk Modeler export → re-import (exactly the broker scenario); near-term/long-term and rate vintage both matter. Ben investigating how to recover/carry it, and whether "vintage" is even a first-class RM concept (FR §7).

### 16.3 Loss Repository

The Loss Repository is CIC's production SQL Server holding finalized loss sets, read by downstream reporting. It is an **existing system whose schema is given, not designed here** (A19; design note 20 D19): the workbench connects via `get_connection("LOSS")` and writes designated tables; `db/bootstrap/loss_schema.sql` is a **local dev mirror** of that foreign schema and must never target production.

**Direction settled (2026-08-27, design note 21 D1, re-confirming 8/26 D18).** The workbench owns the whole export: read results from the Risk Modeler API, transform, write the repository tables — the `push_results_to_loss_repo` worker path. Reusing CIC's workflow tool was raised and rejected: it only sees analyses that live in RiskLink, which would mean exporting Risk Modeler results back to RDM and keeping RMS servers alive. DataBridge is not in the path. Treat as settled; do not reopen when the ELT-retrieval cost bites.

**Write targets** (schema screenshots received 2026-08-27, with CIC's population notes):

- **`dbo.Data`** — the header row. `DataID` (int, auto-increment) identifies the export; the workbench inserts the row and reads the ID back — it never generates it. Populated: `ClientID`, `TreatyIncept`, `DataVintage` (date), `DataName`, `DataModelVendor`, `DataModelVersion`, `DataCurrency`, `Server`, `Database`, `AnalysisID`, `Name`, `Description`, `Perspective`, `CRMID`. **Not populated:** `ArchiveFile`, `AReLossSet` (the out-of-MVP Analyze Re upload), `LOB`, `Geography`.
- **`dbo.RMSELT`** — `DataID`, `EventID`, `Loss`, `StdDevI`, `StdDevC`, `ExpValue`. **Stochastic events only.** The loss-greater-than-exposure cap (FR §7) writes into this data; which columns the check compares needs written confirmation (design note 21 O21-12).
- **`dbo.RMS_HistoricalRDS`** — `DataID`, `ClientID`, `Peril`, `ModelVersion`, `TreatyYear`, `TreatyIncept`, `DataInforce`, `EventID`, `Type`, `Event_Name`, `Loss`, `PCS`, `Perspective`, `AReLossSet`. The historical event IDs and metadata, sourced from the RiskLink reference data tables in SQL. Written **only when the analyst opts in** via a commit-time checkbox.
- **The client table** — same database, **read-only** (**reversed 2026-08-28, design note 22 D25** — retracts the 8/27 INSERT requirement; the insert-statement ask is withdrawn). Searched by name on the export screen; clients are created upstream in Cheng's workflow tool during exposure work, so the client exists before the workbench reaches the export. This returns `workbench_is_active` to being the **only** carve-out to the "app never writes to a synced/foreign surface" invariant. `bootstrap-loss` still mirrors the table for local reads. Shape still owed from Cheryl (O21-13).

Only the data ID is carried from the header into the loss tables. The export record captures the data ID, the parameters entered, whether the historical table was written, and how many rows the cap adjusted. Where the selected **client ID** is recorded against the export is still undecided (`result_export` holds only `delivery_code` and `location` — O21-7/O22-9).

**Retrieval is a Parquet export job, pre-fetched** (**revised 2026-08-28, D28/D29** — the paginated `get_elt()` framing from 8/27 is abandoned; FR §7 Delivery carries the row-level requirements). The workbench submits a Risk Modeler export job naming the analysis, the ELT at portfolio output level, and the perspective codes; a zip returns one Parquet file per perspective, queried with Python. Pre-fetching means the data is local before the analyst opens the export, so the integrity checks and the review run on demand — no waiting phase. The flow is still request → review (per analysis: analysis ID, name, description, currency, capped-row count; AAL deliberately not repeated — the results view shows it, D17) → commit writes the three tables. AAL is never written; the repository calculates it (D16). A repeated export creates a new data set under a fresh data ID.

> **Event type — narrowed 2026-08-28 (D30/D31; was the O21-5 blocker).** The type is not in the Parquet and not selectable on the export job, but Risk Modeler's **reference data APIs** return it per event (found live in the RM UI's network calls). Plan: retroactive enrichment — read every event ID from the Parquet, fetch each type, add a type column. Open (O22-11): bulk endpoint vs. one call per event; Moody's answer on `event info` visibility (permissions vs. absence — Cheryl doubts the reference tables exist on DataBridge; fallback is exporting them from RiskLink 25); Cheryl's CSV cross-check of a small ELT against RiskLink's event info table, which may collapse the problem. Design rule (D31): the split is validated against a source, never inferred from analysis type, model profile, or event rate scheme.

**Own results only.** Pushing **broker** RDM results to the Loss Repository is **out of MVP** (FR §7; §17.4), as is the treaty-level (TY, "part B") export mode — treaty selection with cross-analysis aggregation within one EDM (FR §7). Analysts can also **copy / paste** results out for ad-hoc use (FR §7). Uploading loss sets to Analyze Re is a separate API and out of MVP.

### 16.4 Results grouping

> **Rewritten 2026-08-28 (design note 22).** Ben's grouping prototype was demoed to CIC and produced wrong numbers: it passed **both** members' event rate schemes into a group where the manual Risk Modeler flow presents **one** (defaulted, changeable) — gross pure-premium losses off by ~$8M, and "your EP curve will look wonky" (D13). Grouping has no spec (deferred out of spec 011 to Iteration 9) and no group-creation path on the merged main line. **Write the spec before the fix** (O22-2). FR §6 carries the row-level requirements.

After analysis, the analyst groups finished analyses using IRP's grouping API (`submit_analysis_grouping_job` → `irp_job_type = grouping`). A group is an `irp_analysis` with `is_group=true` (not a separate entity); flow: select analyses (scoped to the submission) → group → rename → pick currency → submit.

- **The event rate scheme is a group input** — a selection with a default, never derived from the members (D13/O22-1). "You can't group with two different rate sets … the same event listed twice with two different event rates." The required region-peril simulation set request attribute is currently reverse-engineered from members' model profiles and rate schemes — source it from the API contract, and confirm the attribute's actual name first (transcript-garbled).
- **A group records the run parameters it was created with** (D18/O22-1c): event rate scheme, currency, simulation set. Group metadata is mis-sourced today; `submitted_settings` is the natural home (DATA_MODEL §6, constitution Art. 8).
- **A group shows its member list** — required (D16): "I don't know inherently from looking at that group what's in it." Membership editing is a later round (D17); an edited group must re-run, and the row-vs-supersede semantics are undecided (O22-3).
- **A group's *results* are consumed like any analysis's; the group *object* is not** — amends the earlier "treated like any other analysis."
- **Groups list in line on the EDM pages their member analyses relate to** (D24), distinguished by the engine column with the group name spanning Portfolio + Template. Groups relate only to submissions today, so a group created from the EDM page vanishes there.
- **Invalid groupings show error messaging** — mixing DLM and HD analyses (the §13.3 homogeneity check), **and the likelier, silent case: members with different event rate schemes when no single scheme is chosen.**
- **Group names are auto-generated and editable** — the generation rule is a low bar ("people are going to rename it no matter what you pick," D10).
- **Nested grouping is unconfirmed with CIC — ask before building it** (note 17 D3 rejected the analogous suites-of-suites).
- **Validation (D14, agreed with CIC):** run matched examples manually in Risk Modeler and in the workbench and compare — **the EP curve, not the AAL**, which stays deceptively close.

**Out of scope (FR §6):** creating ELTs by zone / county / country (done in SQL or the old tool today).

### 16.4a Accumulation results

Accumulation analyses are in scope (FR §5) and their output has its own shape (FR §7):
- Output perspectives are **gross** and **pre-cat net** (Reinsurance-Layer retained).
- **Ground-up is currently included** in accumulation output — a Risk Modeler UI constraint, not a preference; possibly droppable via the API (O7-5).
- Accumulation shows how a **policy limit allocates by geographic area** (e.g. a $1M policy over $50M of buildings across several states).

### 16.5 Exposure Repository

The Exposure Repository (`EXPOSURE_REPO_URL`) receives pre-aggregated exposure summaries from Phase A. The `push_exposure_summary` Dramatiq worker writes structured exposure data (total insured value by portfolio/geography/LOB) to the Exposure Repository after the analyst explicitly triggers it from the Phase A UI. *(Out of MVP — no Exposure Repository per `mvp-scope.md §6`; deferred with Phase A.)*

---

## 17. Feature: Broker RDM comparison

### 17.1 Purpose

Analysts must compare their own analysis results against the broker's results (provided as an RDM file) and against prior-year benchmarks. The workbench surfaces this comparison directly rather than requiring export and manual Excel work.

### 17.2 Broker RDM results — retrieval (REST, deduped by `rdm_id`)

> **Reconciled (2026-07-21, revised 2026-08-25).** Broker RDM results are retrieved via the **same REST result endpoints as own results** (§15.3) — **not** DataBridge — and stored in each broker analysis row's **`loss_results` extract** (§16.1). There is no `rdm_result` table and no DataBridge query for broker results.

Importing a broker RDM creates broker analyses as `irp_analysis` rows keyed (`rdm_id`, `irp_id`) with `edm_id` null (§9.2) — one row per source analysis regardless of how many EDM copies the bundle produces, so the extract is stored **once per RDM** by construction. Their result data is **static and identical** across those copies. No analysis execution is required — the broker analyses and their settings/metadata are viewable since Iteration 3; their **loss numbers** are retrieved and viewable from Iteration 8 — retrieval fires automatically when the RDM import completes (§15.3), one `retrieve_analysis_results` job per broker analysis.

### 17.3 Comparison view

> **Designed 2026-08-25 (design note 19 §6, D17–D20). Demoed and accepted 2026-08-28 (note 22 D7–D9):** the cart, the 5-pair cap (offered for expansion, declined), the selection-order base (accepted once explained — the base marker must be legible at selection time, not only after the comparison renders), and the cross-currency block. Comparison reads the same `irp_analysis.loss_results` extract as viewing (§16.1).

- **View and Compare are two operations** (D17): viewing is N-up side-by-side; comparing is **strictly pairwise**. Choosing Compare, the analyst picks exactly two analyses per comparison; each pair goes into a **cart**; ~5 pairs render on one screen (a real layout limit, enforceable). A base analysis compared against many was considered and rejected (Wendy: "I don't think we need that").
- **Selection order is the contract** (D19): the first analysis picked is the base and the first column; percent change follows that order; explicitly decoupled from list order.
- **Table shape** (D20): columns labelled with analysis names, the leftover row-header column dropped; percent-change columns need no per-pair labelling. Perspective and EP-type selections apply **screen-wide**, to every comparison rendered.
- Metrics: AAL, return-period OEP/AEP, by perspective — own vs. broker or any pair (e.g. CIC vs. broker — saves the manual Excel step).
- Numbers, not a required overlay chart (no EP graph is required — §16.2). Ben has a prior comparison engine to build on.
- **Known gap:** the per-analysis software/model version (design note 18 O18-10) — spec 011 FR-021 captures it at retrieval so this view can show which version each side ran in.

> **Portfolio↔analysis linking is not solved — deliberately deferred** (FR §7). It doesn't exist today either; analysts rely on naming conventions and broker documentation to know which portfolio a result ran against. (See also DATA_MODEL §14: whether `analysis_result_meta` should carry an `irp_portfolio` FK is an open decision.)

### 17.4 Push to Loss Repository — **out of MVP**

Pushing **broker** results to the Loss Repository (`push_rdm_to_loss_repo`) is **out of MVP** (FR §7). Only **own** finalized results are pushed (§16.3). The worker name remains a defined `rwb_job_type` for when this is picked up.

**Also out of MVP (FR §7):** Post-Analysis Treaty (PATE — adding a cat treaty onto broker results and re-simulating; rare, portfolio-level only, O5-4); formal loss validation against broker/cedant (confirm the informal multi-analysis view is enough); and carrying CRM-ID tags through to the repository upload.

---

## 18. Feature: Notifications

> **Scheduled as Iteration 12 (2026-07-21; renumbered 2026-08-25).** Notifications were previously deferred (CR-002) and the earlier Iteration-2 "notification on completion" exit criterion was **not actually delivered** — the capability is **greenfield** and is built in full as Iteration 12 (§21). The poller/worker job-completion path it hangs off already exists. This reverses the CR-002 deferral note.

### 18.1 Async job completion notifications

Triggered by the `notify_analyst` Dramatiq worker when a job reaches terminal status. The analyst is notified **when a job completes** and **when a job fails** (FR §5).

**Channels:**
- **Teams webhook** — post a card to a configured channel with job name, status, and a deep link to the results
- **Email** — SMTP, sent to `assigned_analyst.email`
- **In-app** — an **in-app notification center** plus a status-bar item (polled via SSE)

**Content:** job name, submission name, final status (`FINISHED`/`FAILED`/`CANCELLED`/`SUBMISSION FAILED`), timestamp, deep link to the job.

**Events:** terminal status of the tracked ops — import, analysis execution, grouping, export — both success and failure.

### 18.2 Configuration

The `notification_preference` table is re-introduced with this iteration. Per-user preferences (`channel` teams/email/in_app, `enabled`, `on_success`, `on_failure`). Two settings are **open decisions** to confirm with the team before locking (§23): whether the **Teams webhook URL** is per-submission or a single global config, and whether preferences are **per-user opt-in or always-on**.

---

## 19. Feature: Global search

**Ctrl/Cmd-J** opens a modal (Alpine.js: open/close, keyboard nav, focus trap). Search-as-you-type via HTMX (`hx-trigger="keyup changed delay:200ms"`). A **provider registry** fans out across result groups:

- **Navigation** — reads the nav manifest; new nav items are searchable automatically
- **Submissions** — name, cedant, treaty type
- **EDMs** — EDM name, submission
- **RDMs** — RDM name, submission
- **Portfolios** — portfolio name, EDM
- **Treaties** — treaty name, EDM
- **Analyses** — analysis/group name, submission, status
- **Jobs** — IRP job / RWB job, by type and status
- **Results** — analysis job name

FR (7/14) also asks that **search, sort, and filter be available on every list section** — portfolios, treaties, analyses, and results, not just submissions — delivered via the shared list ergonomics (§20.4), with the command palette above spanning the same object set.

Adding a searchable type = register one provider. There is **no customer scoping** to apply (CR-003 M2/O1 — no RLS); every authenticated analyst searches across all deals. Start with SQL `LIKE`; move to Full-Text indexes if volume demands.

---

## 20. Cross-cutting concerns

### 20.1 Audit logging

Who did what, when. Mandatory for dev-stub auth and all state-changing actions. Initially: structured log lines (logger `rwb.audit`). Upgrade path: dedicated `audit_log` table in Metamodel DB; call sites don't change.

### 20.2 Flash / toast

Server-set notification surfaced in the status bar and/or as a toast overlay. Standard pattern across all actions.

### 20.3 Error / empty / loading states

Consistent HTMX-aware 403/404/500 responses (fragment-safe; `HX-Redirect` where a full-page nav is needed).

### 20.4 List ergonomics

Reusable server-side pagination, filtering, sorting. One pattern, reused everywhere.

**Filter state lives in the URL query string — never in server session state or a client store.** This extends the same principle §4.3 already establishes for breadcrumbs ("a pure function of the manifest position, never of navigation history") to filtered lists: a filtered list's state is a pure function of its URL's query string. A filterable list page (Jobs, EDMs, RDMs, Results, …) reads its active filters from `request.query_params` on every request — full load or `hx-boost`'d partial swap, same code path — and renders active-filter chips so the user can see and clear what's applied.

**This is also the mechanism for cross-page pre-applied filters.** A link such as
`/jobs?submission_id={id}&status=failed` uses the ordinary Jobs list and keeps the
filter state in the URL.

**Fixed filter-param vocabulary.** The shared starting parameters are
`submission_id`, `edm_id`, `rdm_id`, `status`, and `job_type`. Each list accepts
the subset that applies and ignores the rest.

**`status` means something different on every list — this is expected, not a conflict.** Submission status (`ACTIVE`/`COMPLETED`/`CANCELLED`, §7.2a), RWB job status (`rwb_job_status_kind`: `pending`/`running`/`succeeded`/`failed`), IRP job status (`irp_job.status`: the IRP job-status vocabulary, §14.4), and any future list's status are independent domains that happen to share a param name because they never appear on the same list at the same time. Each list defines and validates its own `status` domain against its own data; there is no shared "status" enum anywhere in the system.

### 20.5 Master-detail layout

List + detail panel recurs (Submissions, Jobs, Results). Built once as a reusable layout.

### 20.6 Feature flags / config

Centralized. First flags: `APP_ENV`, `ENFORCE_SSO`. More will accrue.

### 20.7 Health check

`GET /api/health` → `{status, db_workbench, db_exposure, db_loss, redis, env}`. Checks connectivity to all three DB connections (`get_connection("WORKBENCH")`, `get_connection("EXPOSURE")`, `get_connection("LOSS")`) and Redis. Returns 200 regardless; callers check individual fields.

### 20.8 Optimistic concurrency (spec 002 FR-045/046)

Two actors can touch the same row at once. Analyst-editable entity rows use
**optimistic concurrency keyed on `updated_at`**. Association writes rely on their
composite primary keys and eligibility predicates. Append-only inserts and
single-threaded machinery do not need an optimistic-concurrency marker.

---

## 21. Build plan

Each iteration ends runnable and demonstrable. Sequencing follows the analyst's Risk Modeler workflow — import exposure → understand it → shape portfolios → geohaz → analyze → group → export — with cross-cutting ergonomics (global search, home dashboard) built last, over the complete entity set.

> **Package retirement (2026-08-12).** The Package portions of Iterations 1-3
> below describe delivered implementation history and are superseded by
> `specs/006-package-retirement/`. The active design uses `submission_edm` and
> `submission_rdm`, entity-scoped imports, and standalone RDM analysis capture.

### 21.0 DB lifecycle prompt (applies to every iteration)

**Before any iteration that touches schema or seed data, the builder (Claude Code) MUST ask:**

> "This iteration will change the schema for [list of affected DBs: WORKBENCH / EXPOSURE / LOSS].
> Choose an action for each:
> - **Rebuild** — drop all tables, recreate schema, re-seed kind tables. All existing data is lost.
> - **Refresh** — apply only the new additions (new tables, new columns, new seeds). Existing data is preserved where possible.
> - **Skip** — leave the database untouched (use only if you are certain this iteration has no schema changes for this DB).
>
> DATABRIDGE is Moody's managed — never touched by this prompt."

This prompt applies independently to each of the three app-managed databases (`WORKBENCH`, `EXPOSURE`, `LOSS`). A single iteration may affect only one (e.g., Iteration 1 only touches `WORKBENCH`), in which case the prompt only lists that database.

**Rebuild** runs the drop-create-seed path (safe in dev; destructive). **Refresh** applies additive SQL only — it is the analyst's responsibility to confirm no breaking changes exist in the diff before choosing Refresh. In early iterations with no production data risk, Rebuild is the recommended default.

---

### Iteration 0 — Foundation & shell

**Alembic `env.py` requirement.** Alembic targets `WORKBENCH` only. `alembic/env.py` must call `get_connection_config("WORKBENCH")` from the `db/` package and pass the result to `build_sqlalchemy_url()`. **Never hardcode a SQLAlchemy URL in `env.py`** — this would bypass the `db/` package convention and break Windows auth + Kerberos renewal. The `EXPOSURE` and `LOSS` schemas are bootstrapped via separate SQL scripts (not Alembic), runnable via `python -m app.cli bootstrap-exposure` and `python -m app.cli bootstrap-loss`.

**`submission_outputs_dir`** is a **derived path**, not stored in the DB. Always `{OUTPUTS_BASE_DIR}/{submission.id}/` where `OUTPUTS_BASE_DIR` is an env var (default `./data/outputs`). Parquet file paths stored in `validation_result.output_file_path` and `analysis_result_meta.*_file_path` are relative to this root (i.e. they store `{submission.id}/{...}` not the absolute path). The absolute path is reconstructed at read time as `OUTPUTS_BASE_DIR / stored_path`.

**In:** §2 (architecture, three-DB config), §3 (full Linux-native stack: SQL Server in Docker only; app + uvicorn + nginx + Redis run on host), §4 (shell, nav manifest, breadcrumbs, `hx-boost`, `hx-push-url`, status-bar shell, icons), §20.3/20.4/20.5 scaffolding, CSS framework integration, health check (§20.7). Alembic drop-create-seed wired against `WORKBENCH` connection.

**Out:** domain data, IRP integration, Dramatiq workers.

**Exit:** unauthenticated request redirects to `/login`; password login works; OIDC login works; new PremiumIQ user is JIT-provisioned on first sign-in and sees "access pending"; admin creates a password account for John Doe; John is forced to change password on first login; sign-out clears session and returns to `/login`; shell renders with nav manifest driving all structure; health check green.

**Moved in from Iteration 1:** §5.1 (password login, bcrypt, forced password change, `must_change_password` flow), §5.2/§5.3 (OIDC/BFF, PKCE, MSAL, JIT provisioning for PremiumIQ), §5.5 (schema: `user_session`, `login_attempt`, `password_hash`/`must_change_password` on `app_user`), §6.1 (roles), §6.3 (admin: Users, password reset, force-logout). **Deferred from Iteration 1:** rate limiting lockout (§5.1.3 — `login_attempt` table created and logged but lockout gate not implemented).

### Iteration 1 — Submission & Package domain model (Package schema since retired)

> **CR-003 restructuring.** This iteration was previously "Domain, file inventory & RLS" and built the Customer/Program spine, the `apply_scope()`/`user_customer_access` RLS machinery, and the full file-inventory subsystem — **all dropped by CR-003.** The scope below is the redesigned deal-centric model with no customer hierarchy, no RLS, and no file inventory. It covers the full DATA_MODEL §4 "Submission & Package" domain — the submission behavior plus the package *structure* that Iteration 2 builds its behavior on. **Reconciliation with the pre-CR-003 leftovers is a small removal step (CR-003 §8.3): the built migration `0001_initial.py` only ever created the `customer`, `program`, and `user_customer_access` shell tables plus the generic `db/scope.py` helper — the `submission`, `package`, and file-inventory tables were never built. So the cleanup is just dropping those three tables + `db/scope.py`/`test_scope.py`, not unwinding a live domain.**

**In:**
- §7 (Submission as the top-level deal: `cedant_name`/`treaty_type_code`/`inception_date`/`treaty_year`/`renews_from_submission_id`/`directory_path`, assigned analyst as soft owner, master-detail, list ergonomics)
- §7.2 (`submission_crm_id` CRM-ID tag set — add/edit/remove tags)
- §7.2a (submission status: `ACTIVE`/`COMPLETED`/`CANCELLED`, event-sourced; closed states are fully read-only and reopenable to `ACTIVE`; no delete)
- §7.2b (submission identity: surrogate `id` key, non-unique `name` label + soft duplicate warning)
- §6.1 (global roles gating functions) + §6.2 (analyst-centric "my submissions" filter)
- **§9.4 Package structure (schema only, DATA_MODEL §4/§5):** the `package` and `submission_package` tables, the submission↔package M:N, the `package_id` FK on `irp_edm`/`irp_rdm` (bundle membership), soft-delete (`deleted_at`), plus the `db/` access functions and tests. Membership FKs live on `irp_edm`/`irp_rdm`, whose tables are created with the initial schema; their *entity management* (import, IRP) is Iteration 2. The **≥1-member rule is an app-enforced invariant** (no column CHECK — membership spans two child tables). **No package creation/sync/delete behavior here** — exercising a non-empty package waits for the EDM/RDM import plumbing in Iteration 2.
- `treaty_type_kind` seed (confirm the authoritative list with the CIC team, CR-003 §5)

**Out:** Customer/Program/RLS/file-inventory (dropped, CR-003); EDM/RDM entity management, search, workflow references; Package *behavior* — creation via shared-drive browse, name-collision check, IRP sync/delete, and the §7.4 package cards — all Iteration 2, building on the package schema defined here.

**Exit:** browse all submissions with the "my submissions" filter (no scoping — every analyst sees every deal); filter by cedant / treaty type / inception; create a submission with CRM-ID tags and an optional renewal link; set its status and confirm reopening works from both `COMPLETED → ACTIVE` and `CANCELLED → ACTIVE`, and that a closed submission is read-only — edits to its fields and CRM-ID tags are blocked until it is reopened. The `package`/`submission_package` schema was delivered and unit-tested as written here (nullable `package_id` on `irp_edm`/`irp_rdm`, app-level ≥1-member invariant, M:N to submissions) and was later removed by `specs/006-package-retirement/`; `submission_edm`/`submission_rdm` replaced it.

### Iteration 2 — EDM & RDM entity management (incl. Packages, since retired)

> **Reordered (2026-07-08).** This was previously Iteration 3; EDM/RDM management now comes before the search framework, and Package *behavior* is built here in full on top of the package schema defined in Iteration 1 (DATA_MODEL §4). Because the IRP import plumbing lands in this iteration, Package sync/delete are **real from the start** — there is no longer a stub-then-real two-step across iterations (the package UI can still be built against 60-second heartbeat stubs first and wired to real IRP within the iteration).

**In:**
- §9 (EDM entity, RDM entity, EDM/RDM library rail destinations)
- §14.3 IRP submit for EDM import and RDM import, §14.4 poller (basic: poll `import_edm` + `import_rdm` types via `import_job.get_import_job`), §14.5 Dramatiq worker scaffold + `notify_analyst` worker
- §8 + §9.4 Package **behavior** (the `package`/`submission_package` schema comes from Iteration 1): creation — pick shape EDM-only/RDM-only/both, browse the shared drive and select file(s), IRP name-collision check, Save/Save-and-Sync/Delete backed by **real** Risk Modeler operations — `upload_edm`/`upload_rdm`/`delete_edm` async jobs plus the synchronous `delete_rdm`
- §7.4 (submission detail package cards — real upload progress/status/job counts)
- §20.4 query-string-driven filtering + the Jobs list (fixed filter-param vocabulary: `submission_id`, `package_id`, `status`, `job_type`) — needed here so a package card's job-count link lands on a pre-filtered Jobs list

**Out:** the global command-palette search framework (§19, Ctrl/Cmd-J and providers — Iteration 3); analysis, grouping, results, repositories; workflow references (Workflow/Stage/Task layer is out of scope for this entire PRD update — being redesigned separately). *(Package job chaining across RWB-job/IRP-job space (A21) — the prerequisite for the real sync/delete paths — is now **resolved**; see §22 A21 and DATA_MODEL.md §8.)*

**Exit:** import an EDM from a .bak/.mdf/CSV file and an RDM; poller mirrors job status; analyst receives a Teams/email notification on completion; EDM/RDM show `ready` status. The Package exit criteria (create EDM-only/RDM-only/both packages from the shared drive, the IRP name-collision warning, Save-and-Sync with EDM-before-RDM ordering, Delete with RDM-before-EDM ordering, package-card job-count links to the pre-filtered Jobs list) were delivered as written here and later removed by `specs/006-package-retirement/`; imports are now entity-scoped.

> **Correction (2026-07-21).** The exit criterion "analyst receives a Teams/email notification on completion" was **not actually delivered** in this iteration; notification delivery is greenfield and is scheduled as **Iteration 12**. The poller/worker job-completion path it would hang off does exist.

### Iteration 3 — EDM/RDM details & backfill

> **Rewritten (2026-07-21).** This slot was previously "Search framework"; search now moves to the end (Iteration 13) and is built once over the complete entity set. This iteration is the EDM/RDM detail-and-backfill work that FR §2.2 ("Exposure Details Viewing") never had a home for. Covers backlog #7 (post-import detail backfill) and #8 (EDM detail page redesign). Placed first so the analyst can *understand* imported exposure and broker results before acting on them.

**In:** post-import **backfill** of entity detail data from Risk Modeler — extends the Iteration-2 poller/worker completion path to fetch and store detail fields when an import job goes terminal; §9 EDM detail view (exposure summary: account/location counts, #portfolios, perils, lines of business, geography, currency, TIV/record volume, associated treaties — FR §2.2; sub-perils dropped 2026-07-28 — an analysis-settings attribute, not exposure), redesigned EDM detail page; **treaty viewing** at the EDM level (§12.4 view side — full attribute detail, expand/collapse, horizontal scroll, Excel export; edit is a later RM pass-through, not this iteration); **RDM / broker-analysis viewing** — RDM import already creates `irp_analysis` objects; this iteration surfaces the **broker analyses grouped by `rdm_id` and each analysis's settings/metadata** (§16.2 metadata list) on the RDM/analysis detail pages **and on the EDM detail page** (inline under each portfolio + a standalone RDM-grouped section). Each analysis is **linked to the portfolio it ran against** — captured from Risk Modeler's `exposureResourceId` (type `PORTFOLIO`) and resolved to the owning `irp_portfolio` at read time (a group shows "Group", an unresolvable exposure "— not linked"; distinct from the FR §7 deferred results-comparison linking — see 2026-07-23 change-log). No analysis execution is required — the analyses exist from the RDM import path built in Iteration 2. Broker **loss numbers** (the `loss_results` extract, once per `rdm_id`, §16.1) and the `retrieve_analysis_results` worker are **deferred to Iteration 8** (spec 004 Clarifications 2026-07-23; renumbered 2026-08-25).

**Out:** own-analysis results produced by execution (those extend these same detail pages in Iteration 8); portfolio/geohaz/execution/grouping; treaty create/edit pass-through (§12.4 — bundled with analysis config, Iteration 7); broker side-by-side comparison (Iteration 10); Loss Repository export.

**Exit:** open an imported EDM and see its exposure summary and treaty detail backfilled from Risk Modeler, with each portfolio's **linked broker analyses inline** and a standalone RDM-grouped analyses section; open an imported RDM and see its **broker analyses and their settings/metadata**, each showing the **portfolio it ran against** (or "Group" / "— not linked") (broker **loss numbers** deferred to Iteration 8 — spec 004 Clarifications 2026-07-23, renumbered 2026-08-25); a newly completed import backfills its detail data automatically via the poller/worker path.

### Iteration 4 — Sub-portfolio creation & breakouts

> **New (2026-07-21).** Portfolio work previously appeared only as the word "portfolio" in the old Iteration 6 exit line and had no scoped iteration. Full §-body: **§10A**. Reconciled to FR §3 — "modification" is dropped (data-element edits and merge/combine are out of MVP); the capability is **filtered sub-portfolio creation + one-click breakouts**.

> **Narrowed (2026-07-29, spec 005).** The iteration ships the **two one-click breakouts** — by line of business and by geography at state/state-equivalent grain — with preview/confirm, lineage, and the prerequisite gate. The **filtered sub-portfolio builder (§10A.4) and the complement split are fast-follows**; the current-split view is already served by spec 004's per-portfolio table. *(Superseded 2026-08-12, spec 005 P-19 rev.: four quick dimensions shipped — LOB, state, country, peril — plus custom groups.)*

**In:** **one-click breakouts** (app-side loop: select the source portfolio's matching account ids → synchronous `create_portfolio()` → account add) by **line of business** and by **geography (state/state-equivalent)**, each previewed and confirmed with the quantified overlap and blank-value disclosures; breakout lineage (source portfolio, dimension, value) stored on `irp_portfolio` and shown in the portfolio list; automatic exposure-detail refresh for generated portfolios; the prerequisite-gate rule for the op (§13.1), built as part of this slice.

**Out:** the **filtered sub-portfolio builder (§10A.4)** and the **complement split** ("X vs. not-X" / "do the opposite") — fast-follows; geohaz (Iteration 5), analysis execution, grouping, results; data-element modification / merge-combine (out of MVP, §10A.7). Peril-specific portfolios shipped as a quick breakout dimension (spec 005 P-19 rev., 2026-08-12).

> **Resolved sub-item — geography breakout unblocked (2026-07-29).** O6-1/O6-2 closed by product direction: Risk Modeler assigns whole accounts, the overlap that produces is accepted and disclosed (quantified per portfolio in the preview), and no location-level toggle is awaited (§10A.5). The geography breakout ships in this iteration alongside LOB.

**Exit:** from the EDM detail page, run a one-click LOB or state breakout that produces one sub-portfolio per distinct value, together covering the source, with the measured overlap disclosed before confirm; generated portfolios appear in the list with lineage and acquire figures automatically; the prerequisite gate enables/disables the op correctly from entity state. *(Superseded 2026-08-12, spec 005 P-19 rev.: the shipped quick dimensions are LOB, state, country, and peril, plus custom groups.)*

### Iteration 5 — GeoHaz (hazard lookup)

> **New (2026-07-21).** Was only the word "geocode" in the old Iteration 6 exit line. Full §-body: **§10B**. Reconciled to FR §5 — this is **hazard lookup only**; geocoding is *not* re-run (broker geocoding preserved). **Updated 2026-08-12** from the Aug 7 design session: multi-portfolio launch from the summary page, app-side lineage/status display, no version-stamp display or gating (§10B.4, §24 change log).

**In:** the hazard-lookup op against Risk Modeler, launched with one click from the EDM/portfolio summary page against **one or more selected portfolios** (one geohaz job per portfolio, one fixed parameter set per launch) — `submit_geohaz_job` → `irp_job_type = geohaz`, polled via `get_geohaz_job` (async); the fixed DLM parameters (configured data version, EQ + windstorm, previous locations not skipped, user-defined values overwritten — §10B.2); **per-portfolio display of app-side hazard-lookup history and in-line job status** on the summary page, refreshed by polling the workbench (§10B.4); the per-layer **"locations looked up" summary** shown on completion (§10B.3); the prerequisite-gate rule (geohaz needs an EDM + portfolio, §13.1). Hazard lookup is **optional** and **not** an analysis prerequisite (§10B.5). No geocode/hazard **version stamp** is displayed or read (§10B.4).

**Out:** analysis execution, grouping, results; geocoding (never a workbench action); SSE live job status (§14.7, Iteration 6 — polling refresh suffices here).

**Exit:** select two portfolios on the EDM summary page and run hazard lookup with one click and no modal; both jobs use the fixed DLM parameters and are tracked via the poller with in-line per-portfolio status; on completion the per-layer locations-looked-up summary is shown and each portfolio shows it has been hazard-looked-up through the workbench; the gate requires a portfolio before geohaz is enabled. *(Open: whether HD models need hazard run ahead — O7-1; what execution detail to record per lookup — O8-3.)*

### Iteration 6 — Analysis templates & template suites (definition & administration) — **IN MVP**

> **Reordered again (2026-08-17, session 8/14 D9).** The 2026-07-21 plan built execution first, templates on top. Reversed: CIC works in outcomes ("pick these portfolios, run the US suite"), so suite/template definition and administration land first and individual execution becomes the substrate under them (Iteration 7). Feasible because execution work had only reached the metadata stage.
>
> **Shipped (spec 009, merged 2026-08-20).** With four scope changes from the spec: starter-suite seeding and CSV/Excel export-import deferred out of MVP (P-02 — setup is manual via the admin page; Excel design retained in `specs/009-template-suites/contracts/transfer-workbook.md`); suites shipped **unordered** with no per-item settings (P-08); `treaty_name_pattern` and `auto_name_pattern` dropped from templates (P-09; naming resolved in §2.6); templates store currency + currency scheme + scheme vintage, all required, with no submit-time defaulting (P-10).

**In:** §11.2/§11.3 (analysis template entity, template suite, `analysis_template_tag`); the **suite administration surface** — create/edit/duplicate templates and suites (D11/O14-1, spec 009 P-12), built comprehensively then pared down; IRP metadata sync (§15.2), `irp_*` cache tables seeded; the **analysis metadata screen** (model/output/accumulation profiles + currencies + currency schemes with vintages — viewed here, created in RM, §11.1a); DLM/HD detection for template validation (§11.4 — event-rate scheme required for DLM, optional for HD).

**Out:** analysis submission/execution and the suite **run** flow (Iteration 7); grouping, results, export; **starter-suite seeding and Excel export/import** (spec 009 P-02, deferred — setup is manual via the admin page).

**Exit:** create a template and a suite ("Global 2026 Q1") on the administration surface; duplicate a template and edit the copy; IRP metadata sync populates the profile/scheme/currency dropdowns and the metadata screen. *(Starter-suite seeding and the CSV/Excel round-trip left the exit criteria with the P-02 deferral.)*

### Iteration 7 — Analysis execution (suite run first, then single templates)

> **Reordered again (2026-08-17, session 8/14 D9).** Was Iteration 6; now follows templates so the run flow is suite-first from day one (§11.3a). Most of the job infrastructure already exists from Iteration 2; the remainder lands here.
>
> **Phased (2026-08-20).** Suite execution is the first and most consequential phase: run one or more template suites against one or more selected portfolios. Single-template execution (running individual templates, not a whole suite) follows as the second phase, through the same modal.
>
> **Loss-number retrieval moved out (2026-08-25).** The "later phase" loss retrieval (spec 010 US4) was not built in this iteration; it moves to Iteration 8 together with broker retrieval and the results review UI.

**In:** §14 execution engine — `irp_job` as the tracked unit, synchronous submit on the request path, the remaining poller `irp_job_type`s for analysis, the `rwb_job` queue with heartbeat + reconciler, the remaining Dramatiq worker types, single-threaded submission retry; the **execution flow** (§11.3a — select one or more portfolios; Execute Suite / Execute Template opens a searchable modal; several suites or several templates per execution, never mixed; Submit disabled until one is chosen; expand-to-deselect; treaty selection in the modal; direct submit loop, one analysis per portfolio × template; fixed portfolio + template naming; per-analysis failures surfaced with reason); **`irp_analysis` rows written at submission and backfilled with settings/metadata on completion**; the **user-executed analyses section on the EDM detail page** (like the broker-analysis sections, no RDM grouping, portfolio shown, live status updates — §14.7 SSE); **treaty create/edit as an RM pass-through** (§12.4); **multiple portfolios selected and run in one action** (FR §5); the prerequisite-gate rule (execution needs an EDM + portfolio (+ named treaties); hazard lookup is **optional**, not gated — §13.1) plus relevant point-of-action validation (§13.3 uniqueness / reference-data).

**Out:** loss-number retrieval and results viewing (Iteration 8); grouping (Iteration 9); Loss Repository export (Iteration 11).

**Exit:** select several portfolios, pick a suite in the modal, and see one auto-named analysis per portfolio × template submitted via the loop, with any peril-mismatch failures surfaced with their reason; run an individual template the same way; executed analyses appear in the EDM detail page's user-executed section, update live through their job statuses, and show settings/metadata on completion; a failed submission is surfaced immediately and retried per §14.3; a treaty edit hands off to the RM editor and the refreshed view reflects it; a wedged job is recovered by the heartbeat/reconciler.

### Iteration 8 — Analysis results sync & viewing

> **New (2026-08-25).** This capability had no iteration of its own: own-analysis loss retrieval was the unshipped final phase of Iteration 7 (spec 010 US4, P-09), broker retrieval was deferred by spec 010 P-12 "to the work that consumes it," and the §16.2 results review UI was never scheduled anywhere. All three land here. Iterations 8–13 renumbered 9–14.

> **Rescoped (2026-08-25, design note 19).** ELTs dropped from viewing (D5); storage narrowed to the `irp_analysis.loss_results` extract (§16.1); the merged analyses table, inline expanded-row results, and the dedicated results page added from the 8/25 design session.

**In:** the `retrieve_analysis_results` worker writing the `loss_results` extract (§15.3, §16.1, DATA_MODEL §6) — AAL, standard deviation, and OEP/AEP losses at 11 fixed return periods per perspective (GR/RL/WX/QS/GU) — for **own** executed analyses (fired automatically on `FINISHED`, chained from `finalize_analysis`) and for **broker** analyses (fired automatically on RDM import completion, chained from `backfill_rdm_analyses`, one per broker analysis, once per `rdm_id` by construction); the §16.2 results review UI — the **merged analyses table** (own + broker in one, origin derived, broker grouped under RDM rows, currency + AAL columns), **condensed results inline in the expanded analysis row**, and the **dedicated results page** (multi-select from submission and EDM pages, new browser tab, breadcrumbs, tab title, N-up soft guideline, units selector, user ordering, copy with headers).

**Out:** the comparison cart / percent-difference view (§17.3 — Iteration 10); grouping (Iteration 9); Loss Repository export and all row-level ELT/PLT retention (Iteration 11); pushing broker results anywhere (out of MVP, §17.4); TCE and EP-curve graphs.

**Exit:** run an analysis to completion and read its AAL, standard deviation, and OEP/AEP return-period losses per perspective without leaving the workbench; import an RDM and see each broker analysis's loss numbers appear with no analyst action, stored once per `rdm_id`; multi-select five analyses from the submission page and view them side by side in a new tab, switch perspective screen-wide, and paste the table into Excel with headers.

### Iteration 9 — Grouping

> **Split (2026-07-21).** Carved out of the old Iteration 6.
>
> **Now the priority, and spec-first (2026-08-28, design note 22).** Ben's grouping prototype was demoed to CIC with wrong output — both event rate schemes passed into a group Risk Modeler builds with one, ~$8M apart on gross pure premium (§16.4). It is the only thing shown to the client that produces incorrect numbers, and it has no spec to fix it against. Write the spec first (O22-2); it must carry D13's failure mode, D14's EP-curve validation method, the member list and edit semantics, builder sort/search, and the EDM-page group listing.

**In:** §14 grouping op (a group is an `irp_analysis` with `is_group=true`, CR-002); the event rate scheme as a group-creation selection with a default (never derived — §16.4); the region-peril simulation set sourced from the API contract or documented with a breaking test (confirm the attribute name first); group run parameters recorded (`submitted_settings` question, O22-1c); the member list view (D16); builder name sort + search (D15); groups listed in line on member EDM pages via the engine column (D24); §13.3 grouping homogeneity check (DLM+HD mixing caught when composing the op) plus the event-rate-scheme case; the prerequisite-gate rule (member analyses must exist and be `FINISHED` — A2); member lookup by analysis ID, never name (O22-16).

**Out:** results export, broker comparison; group membership editing (a later round, D17); nested grouping (unconfirmed with CIC — ask first).

**Exit:** compose a grouping over finished analyses with an explicitly chosen event rate scheme; an invalid mix is caught when the op is composed; the group runs, records what it ran with, shows its member list, and appears on its members' EDM pages — and a matched example run manually in Risk Modeler and in the workbench produces the **same EP curve** (D14).

### Iteration 10 — Broker RDM comparison

> **New placement (2026-07-21).** Promoted to its own iteration between grouping and results export (was bundled into the old Iteration 6).

**In:** §17 broker RDM comparison — side-by-side of broker-provided results (from RDM import; broker **analyses/settings** viewable since Iteration 3, broker **loss numbers** since Iteration 8) against own executed/grouped results.

**Out:** Loss Repository export.

**Exit:** view a side-by-side comparison of broker RDM results and own results for a submission.

### Iteration 11 — Results export

> **Rescoped (2026-07-21).** Narrowed to the Moody's → Loss Repository export only; results *viewing* lives in Iteration 3 (broker settings/metadata) and Iteration 8 (loss numbers, own and broker).
>
> **Redesigned (2026-08-27, design note 21).** The export is a two-phase, analyst-parameterized push into three CIC tables (§16.3), not a single fire-and-forget job. Cheryl's workflow-tool walkthrough and the schema screenshots replace the earlier file-export framing.
>
> **Revised (2026-08-28, design note 22).** Retrieval is the Parquet export job, pre-fetched (D28/D29 — the paginated `get_elt()` path is abandoned); the client table is **read-only** (D25 — client create retracted); event type comes from the reference data APIs via retroactive Parquet enrichment (D30/D31 — the O21-5 blocker narrowed to resolved-pending-volume).

**In:** the export flow (§16.3; FR §7 Delivery): client search / select against CIC's **read-only** client table; the parameter form (treaty year + inception date auto-populated from the submission and editable, data vintage, CRM ID); perspective validity resolved per analysis, intersected across the selection, one perspective per batch, property scope; the Parquet export job (`export` `irp_job_type` — one Parquet file per perspective, ELT at portfolio output level) with a decided pre-fetch trigger (O22-10); event-type enrichment of the Parquet via the reference data APIs; the transform + the loss-greater-than-exposure cap; the review step (per-analysis identity fields + capped-row counts), on demand against the pre-fetched data; commit writing `dbo.Data` / `dbo.RMSELT` / `dbo.RMS_HistoricalRDS` (opt-in) and reading the data ID back; §16.1 / DATA_MODEL §9 storage where it feeds the export.

**Out:** treaty-level (TY, part B) export (FR §7 — source now located: `RDM_TREATY` + description join, its own retrieval call; deferred again 8/28 at Ben's request, D27); pushing broker results (out of MVP, §17.4); the event-catalog check unless CIC confirms it is still needed (O21-8); an analyst-facing Parquet file download (not an MVP deliverable unless separately requested); client creation (upstream, in Cheng's workflow tool — D25).

**Blocked on:** event-type enrichment viability (bulk endpoint vs. one call per event, and Cheryl's CSV cross-check — O22-11); written confirmation of the capped-value columns (O21-12); a test treaty that takes loss, which gates the WX and TY validations (O22-14).

**Exit:** select analyses, pick a valid perspective and a client, review the integrity-checked ELT with no retrieval wait, commit, and see the three tables populated in the dev mirror under a repository-generated data ID.

### Iteration 12 — Notifications

> **New (2026-07-21) — greenfield.** Notification delivery was listed in the old Iteration 2 exit criteria but was not actually delivered; the full capability is built here.

**In:** §18 notifications — in-app notification center, delivery (Teams/email), per-event routing and preferences; wires the existing job-completion path (poller/worker terminal status) to notification events.

**Out:** —

**Exit:** an analyst receives a notification on job completion (import, execution, grouping, export) via the configured channel and sees it in the in-app center.

### Iteration 13 — Global search

> **Consolidated (2026-07-21).** Was split across the old Iteration 3 (framework) and Iteration 7 (remaining providers). Built once here at the end, over the complete entity set — no half-built-then-finished split.

**In:** §19 search framework + §20 command palette (Ctrl/Cmd-J); all providers — navigation, submission, EDM, RDM, jobs, analyses, groupings, and results.

**Out:** per-table sort/filter ergonomics (tracked separately as a GitHub issue).

**Exit:** Ctrl/Cmd-J finds nav, submissions, EDMs, RDMs, jobs, analyses, groupings, and results; providers return results and navigate correctly.

### Iteration 14 — Home dashboard

> **New (2026-07-21).** Deferred to the end (backlog #6); content is defined once the surrounding capabilities exist to feed it.

**In:** home page content — the empty landing page replaced with a useful dashboard (candidates: "my submissions," recent activity, running jobs); exact content decided at build time.

**Out:** —

**Exit:** the home page renders a useful dashboard instead of an empty page.

---

> **Phase A — Validation, profiling & Exposure Repository: out of MVP, not scheduled.** §10 (DataBridge validation/profiling/exposure modification) and §16.5 (Exposure Repository write via `push_exposure_summary`) are out of MVP per `mvp-scope.md §6`. There is nothing to build for MVP, so Phase A is intentionally **not** a build-plan iteration (it was previously Iteration 4). If it is ever picked up, it slots in as its own iteration (§10 + §16.5).

---

## 22. Adversarial review

- **A1 — Stale references on re-run (dissolved by CR-002).** The old concern — a re-run upstream task silently corrupting a downstream task's pinned input — no longer exists: there are no typed handles to pin. Each op resolves its inputs live from Risk Modeler by name at submit time (§13.2), so there is nothing to go stale; a rename shows up on the next `search_*`. `as_of` on entity rows signals when the local copy was last confirmed against RM.
- **A2 — Op with an unmet prerequisite (replaces the "skip a stage with referenced handles" concern).** Grouping needs its member analyses to exist and be `FINISHED`; if they don't, the op is simply not enabled by the prerequisite gate (§13.1) — surfaced as `irp_job.status = 'BLOCKED'` if a submit is attempted anyway. No stage-skip machinery to make unsatisfiable.
- **A3 — Stale source file (dissolved by CR-003).** The old concern — a changed broker file going undetected between scanner triggers — no longer exists: there is no reconciliation scanner or file inventory (CR-003 M5, §8). A file is read live at package creation and its path stored as `source_file_path`; there is nothing to drift out of sync.
- **A4 — Cookie/session vs. live role changes.** Admin changes a user's role; active session doesn't reflect it. Resolution: the session holds identity only; roles are read **live from DB on every request** (§5.4). Changes are immediate. (There is no customer-access scope to change — CR-003 M2.)
- **A5 — Dev stub can't be killed mid-session.** Resolution: explicitly accepted for local development only. `AUTH_MODE=dev` is gated on `APP_ENV != production` server-side. Audit, loud banner (§5.0).
- **A5a — Password auth is weaker than SSO.** Accepted for v1 MVP. Mitigated by: bcrypt cost factor 12, rate limiting (5 attempts / 15 min per email; 20 / 15 min per IP), `HttpOnly Secure SameSite=Lax` cookie, server-side sessions in WORKBENCH DB, CSRF tokens on all state-changing requests, forced password change on first login, admin-only password reset. Upgrade path to Entra SSO (§5.3) requires no downstream code changes.
- **A6 — Three-DB split makes local dev painful.** One SQL Server Docker container hosts all three databases (`rwb_workbench`, `rwb_exposure`, `rwb_loss`). Three connection strings, one server, three database names. Schema isolation is enforced by database name, not separate servers. No extra infra cost locally. All application processes (app, nginx, Redis, poller, workers) run natively on Linux — no Docker overhead for anything except SQL Server.
- **A7 — Dramatiq worker failure leaves RWB job stuck.** Resolution: layered per §2.3a. Worker death → Dramatiq redelivery. Task failure → Dramatiq Retries middleware. Job stops progressing (wedged worker or message lost) → per-job heartbeat + single-instance reconciler resets `running → pending` and re-enqueues. Idempotent workers ensure double-delivery is harmless. No duration-based sweep — stale threshold is a constant multiple of the heartbeat interval.
- **A8 — IRP outage blocks everything.** Resolution: ops that need IRP are simply not enabled by the prerequisite gate while IRP is down (§13.1, §15.6); already-imported entities remain viewable. Submissions in `SUBMISSION FAILED` are retried by the single-threaded submission-retry batch job, and the poller catches up when IRP comes back.
- **A9 — Search leaks across customers (dissolved by CR-003).** There are no customers and no row-level security (M1/M2/O1), so there is no cross-customer boundary to leak across — every analyst is meant to see every deal (§6, §19).
- **A10 — (retired with RLS, CR-003).** The old concern — admin can't see all customers under scoping — is moot: there is no scoping to bypass; all authenticated users see all rows (§6).
- **A11 — Upload vs. shared-drive store split (dissolved by CR-003).** There is no upload store and no `file_artifact` model (M5, §8). A file is selected from the shared drive at package creation and its path stored as `source_file_path`; no two-store reconciliation to get wrong.
- **A12 — Detail pages have no manifest node.** Resolution: detail routes declare a home node; breadcrumb walks up from it + appends entity label (§4.2, §4.3).
- **A13 — (retired with the directory inventory, CR-003).** There is no `submission_directory` table or `UNIQUE(unc_path)` constraint anymore (M5, §8); `submission.directory_path` is a single optional seed for the file browser, with no cross-submission uniqueness concern.
- **A14 — Validation vs. readiness (simplified by CR-002).** There is no separate authoring-graph validation pass and execution-readiness gate to conflate anymore — both collapse into a single prerequisite gate + point-of-action validation (§13.1, §13.3), computed from entity + job state at the moment the analyst acts.
- **A15 — Dramatiq/Redis adds ops complexity.** Accepted; the alternative (polling a SQL queue from the app process) is simpler but does not support per-job-type parallelism or fan-out without entangling the web process. Redis + Dramatiq is the standard pattern for this scale. Redis runs with AOF durability (`appendonly yes`, `appendfsync everysec`) so acknowledged enqueues survive a broker crash (≤ ~1s worst-case loss). Outstanding work is always inspectable in the SQL `rwb_job` table. Results already written survive Redis loss entirely; Redis holds only the Dramatiq message, not the work artifact.
- **A16 — Over-generalizing (settled by CR-002).** The type/port registry and registered-validator graph engine are removed entirely — they modeled authored DAG topology this app doesn't have. Validation is a handful of point-of-action checks (§13.3); sequencing is a static lookup table (§13.1). No DSL, no registry, no engine.
- **A17 — Icon assets.** Dependency logged (§23). Not a code blocker.
- **A18 — (retired with `customer_id`, CR-003).** There is no denormalized `customer_id` on any table (M2/O1), so the drift concern it raised no longer applies.
- **A19 — Loss Repository schema ownership.** The workbench has write-only access to specific tables. Schema is defined and versioned separately (not by Alembic). Breaking schema changes in the Loss Repository require coordination. Mitigated by: write through a thin adapter layer in the Dramatiq worker; the adapter is the single point to update on Loss Repository schema changes.
- **A20 — Analyst submits 150 analysis jobs; IRP rate-limits.** Resolution: irp-integration has built-in retry (5 attempts, exponential backoff). The batch-submit method handles the loop. Do not add another retry layer. The poller polls at an interval; no thundering-herd problem.
- **A21 — Package job chaining crosses RWB-job space and IRP-job space (RESOLVED 2026-07-13, spec 003).** The existing `rwb_job` chaining pattern (§14.5) assumes a worker's own success triggers the next `rwb_job`; package sync/delete (§9.4) doesn't fit that shape once real IRP calls replace the stubs, because it's the **poller** noticing an IRP job go terminal — not the RWB worker — that must trigger the next step. **Resolution:** lineage chaining, with (1) all member ops run as `rwb_job`s and **workers performing every Risk Modeler call** (Save-and-Sync/Delete only enqueue `analyst_request` head rows and return — nothing submitted on the request path); (2) the **poller** writing the dependent head `rwb_job` (`requestor_type='irp_job'`, `requestor_id=` the finished job) on terminal status, driven by a **per-`irp_job_type` completion mapping** so the poller stays generic (the proposed hook shape, §14.4); (3) **idempotent, status-guarded fan-in** for `delete_edm`-after-all-RDM-removals and for the final package soft-delete (no dependency counter); and (4) recovery via idempotent Save-and-Sync + per-member retry + **replace-the-source-file-and-retry**, atop the `submission_retry` batch. **Delete is asymmetric:** EDM delete is asynchronous (`submit_delete_edm_job` → pollable id, polled like imports; only `delete_edm` is added to `irp_job_type_kind`), while **RDM delete is synchronous** — an RDM import yields analysis entities rather than a first-class RM object, so removing an RDM deletes those entities inline (no `irp_job`, no polling) and the RDM→EDM fan-in is detected app-side on `delete_rdm` worker success. Job-type codes follow `<verb>_<entity>` (`import_edm`/`import_rdm`/`delete_edm`; `upload_edm`/`upload_rdm`/`delete_rdm`). Full design: DATA_MODEL.md §8 → *Package sync/delete chaining*; behavior: spec 003 FR-042–FR-048.

---

## 23. Assumptions, decisions & external dependencies

### Locked decisions

- **2026-08-28 — Design session 22: viewing/comparison signed off; the grouping defect; the client table reverses to read-only; Parquet replaces the paginated API; event type via reference data APIs.**
  - **Viewing and comparison signed off (D1–D9)** — the first client acceptance of the results layer; one change request (right-justify return periods, D5). RL + OEP defaults, uncapped viewing, 5-pair comparison cap, selection-order base, cross-currency block, clipboard in ones at full precision — all confirmed. Closes 19 O19-8/O19-9; confirms 20 O20-6/O20-8 (§16.2, FR §7).
  - **The event rate scheme is a group INPUT, not a derivation (D13/D14)** — the demoed grouping passed both members' schemes through and came out ~$8M wrong; the fix is a selection with a default, group run parameters recorded, and validation by comparing the **EP curve** of matched manual-vs-workbench runs. Grouping is spec-first before any fix (§16.4, §21 Iteration 9, FR §6 rewritten).
  - **A group needs more than an analysis (D16–D18):** member list required, editing later, metadata sourced from the group's own run parameters; groups list in line on member EDM pages via the engine column (D24).
  - **The client table is READ-ONLY (D25)** — retracts the 8/27 INSERT: clients are created upstream in Cheng's workflow tool during exposure work; the insert-statement ask is withdrawn; `workbench_is_active` is again the only carve-out to the reference-cache invariant (§16.3).
  - **Parquet replaces the paginated API (D28/D29)** — a Risk Modeler export job returns one Parquet file per perspective (ELT at portfolio output level); pre-fetched so the loss>exposure check runs on demand. Settles 20 O20-11 and 21 O21-9; the pre-fetch trigger is open (O22-10) (§16.1, §16.3).
  - **Event type via reference data APIs (D30/D31)** — not in the Parquet, not selectable on export, but returned per event by the reference data APIs; plan is retroactive Parquet enrichment, split validated against a source and never inferred from business rules. Narrows 21 O21-5 to resolved-pending-volume (§16.3).
  - **TY's source is `RDM_TREATY` + a treaty-description join (D26/D27)** — its own retrieval call; part B stays deferred, and the export pipeline is built with the retrieval source swappable behind one transform/validate/commit flow (FR §7).
  - **EDM terminology (D23):** EDMs contain exposures only — an analysis is *related to* an EDM, never *in* it; analyst-facing copy uses the relationship phrasing (§1.4 glossary, FR §1).
  - **Submitted-vs-returned metadata display rejected (D22)** — a deliberate non-requirement; `submitted_settings` stays captured for support/debugging. Applied treaties join the analysis view instead (D21, FR §7).
- **2026-08-27 — Export walkthrough (design note 21): the workbench owns the export ETL; CIC's client table is a write target *(retracted 2026-08-28 — read-only, D25 above)*; one valid perspective per batch; three target tables; two-phase review/commit.** Workflow-tool reuse rejected (D1 — it would require round-tripping Risk Modeler results back to RDM and keeping RMS servers alive; treat as settled). The export opens with client search / select / create against CIC's client table in the repository database (D2–D6; client ID ≠ `cedant_name`, never stored on `submission` — one submission can push under different client IDs across treaty types). Treaty year + inception date auto-populate from the submission and stay editable (D7); the in-force date is deliberately not populated (D8); the data ID is the repository's auto-increment, read back after the header insert (D15). Perspectives: only valid ones offered, resolved per analysis from the platform, intersected across a multi-select, one per batch — per-analysis multi-select rejected (D9–D11); WX/QS are never uploaded (D14); RP joins the export vocabulary. Commit writes `dbo.Data` + `dbo.RMSELT` (stochastic only) + `dbo.RMS_HistoricalRDS` (opt-in checkbox), correcting the 8/26 two-table reading (D15); AAL is calculated, never stored (D16). Loss > exposure is a must-have integrity check with a corrective cap, surfaced and recorded (D18); the check needs the full ELT before the review renders, making the export two-phase. Treaty-level (TY) export is part B, explicitly deferred (D12/D13). Schema screenshots received the same day (§16.3). §16.1, §16.3, §21 Iteration 11, FR §7 Delivery.
- **2026-08-25 — Iteration 8: analysis results sync & viewing.** Own-analysis loss retrieval (the unshipped last phase of Iteration 7, spec 010 US4) and broker (RDM) loss retrieval (deferred by spec 010 P-12) are consolidated into a new Iteration 8 together with the §16.2 results review UI; Iterations 8–13 renumbered 9–14. Retrieval is automatic in both cases: own analyses on `FINISHED` (chained from `finalize_analysis`), broker analyses on RDM import completion (chained from `backfill_rdm_analyses`), once per `rdm_id` — no analyst action triggers it. The Results rail section ships in Iteration 8 **without** the comparison panel; broker comparison stays its own iteration (now Iteration 10). §15.3, §16.1, §16.2, §17.2, §21.
- **2026-08-20 — Iteration 7 planning: execution flow.** Suite execution ships first (run one or more suites against one or more selected portfolios), then single-template execution — both through the same portfolio-first modal: searchable list, multi-select, suites XOR templates (never mixed), Submit disabled until at least one is chosen, expand-to-deselect inside the modal, treaties picked in the modal, direct submit with no review page. Analysis names follow the fixed rule **portfolio name + template name** (resolves O7-3/O14-9). `irp_analysis` rows are written at submission and backfilled with settings/metadata on completion; loss-number retrieval is a later phase of Iteration 7 *(superseded 2026-08-25: moved to Iteration 8 — see above)*. Executed analyses surface in a user-executed section on the EDM detail page (no RDM grouping, portfolio shown) with live status updates. §11.3a, §21 Iteration 7.
- **2026-08-12 — Package retirement.** Package is removed from the product and
  database. `submission_edm` and `submission_rdm` relate global resources directly
  to submissions. Jobs target entities and may store
  `requested_from_submission_id` as provenance. RDMs import once against their own
  exposure set and broker analyses use `(rdm_id, irp_id)` identity with `edm_id`
  null. `specs/006-package-retirement/` supersedes the Package parts of specs 002-004.
- **2026-07-10 — July 9 CIC session (package = bundle; EDM/RDM asymmetry; broker-result dedup; submission identity).** A `package` is a **bundle** of any combination of EDMs/RDMs; membership is `package_id` on `irp_edm`/`irp_rdm` (no `edm_id`/`rdm_id` on the package; ≥1 member **app-enforced**, not a column CHECK). An **EDM is a DataBridge SQL database**; an **RDM is not a DataBridge asset** — importing it creates analyses on an EDM, and an RDM is applied to **every** EDM in its bundle (full grid). `irp_rdm.edm_id` is **dropped**; `irp_rdm.status` is a combined rollup of its apply jobs; one `irp_rdm` row per file, one `irp_analysis` per Moody's object (with `irp_analysis.edm_id`/`rdm_id` both nullable + ≥1 CHECK — RDM-only analyses have no EDM). **Broker result data is deduplicated by `rdm_id`** (`analysis_result_meta.analysis_id` nullable + CHECK exactly one of `analysis_id`/`rdm_id`; retrieved once per RDM source analysis). **`submission.name` is no longer unique** — surrogate `id` key + soft duplicate warning. **The top-level organization (submission vs project; two- vs three-tier) is reopened by CIC and NOT ratified** — DATA_MODEL §4 is a provisional build-to-learn shape (OQ-1..OQ-4, Open decisions below). Applied to DATA_MODEL.md (2026-07-10); a formal CR follows.
- **CR-003 — Submission + Package; no Customer/Program; no RLS; simplified file handling.** `submission` is the top-level deal (M1); `customer`/`program` and all `customer_id` denormalization are dropped, retiring row-level security entirely — every authenticated analyst sees every deal (M2/O1, §6). CRM IDs are a `submission_crm_id` tag set, not a single field (M3/O6). `package` is many-to-many with `submission`, and EDM-only **and** RDM-only are both valid (`edm_id` nullable, ≥1-of-edm/rdm CHECK) (M4/O2). The file-inventory subsystem is replaced by a single `source_file_path` per EDM/RDM chosen at package creation (M5/O9). `submission.name` is globally unique (O5); `irp_job` lives at the package grain (O7); cedant is a plain string (O3); the renewal link is a manual nullable self-ref (O4). Applied to DATA_MODEL.md (2026-07-07) and the constitution → v3.0.0 (2026-07-08). Full detail: `docs/CR/CR_03__SUBMISSION_PACKAGE_MODEL.md`. *(Superseded in part by 2026-07-10, above: `package` is now a bundle — no `edm_id`/`rdm_id`, members carry `package_id`, ≥1 app-enforced; `submission.name` is no longer unique.)*
- **CR-002 — Not a workflow engine.** Workflow / Stage / Task / typed-handle / type-port-registry / manifest-projection are all removed (§12). Sequencing is the prerequisite gate computed in code (§13.1); coupling is name-based via IRP `search_*` (§13.2); the executable unit is `irp_job` (§14). One declarative source of truth remains: the **navigation manifest** (§2.1, §4.2).
- **CR-002 entities & schema** — `edm`/`rdm` → `irp_edm`/`irp_rdm`; new `irp_treaty`, `irp_analysis` (a group is an analysis with `is_group=true`; `rdm_id` set → broker-from-RDM, null → own). *(Superseded 2026-07-10: `irp_rdm` has **no** `edm_id` — an RDM applies to every EDM in its bundle. `irp_analysis.edm_id` is now **nullable** with a ≥1-of-(edm_id, rdm_id) CHECK — RDM-only imports create analyses with no EDM.)* `irp_job` redesigned (typed lineage FKs; `irp_job_type` kind table, `status` plain string; three `last_*` columns; `irp_job_resource`; single-threaded retry). `rwb_job` decoupled from `irp_job` (`requestor_type`/`requestor_id` + composite dedup key). Full detail: DATA_MODEL.md §CR-002 change-log.
- **Three separate database connections** — named `WORKBENCH`, `EXPOSURE`, `LOSS` — resolved via the `db/` package (`MSSQL_{NAME}_*` env vars). One SQL Server Docker container in dev with three databases (`rwb_workbench`, `rwb_exposure`, `rwb_loss`); separate servers in prod (§2.2).
- **Dev environment is Linux-native.** Only SQL Server runs in Docker. App (uvicorn), nginx, Redis, poller, and Dramatiq workers all run as native Linux processes. No Docker Compose wrapping the application stack.
- **Dev DB strategy: drop-create-seed.** Until production cutover, the WORKBENCH schema is managed via a single Alembic revision that drops all tables, recreates them, and seeds kind tables. No migration version accumulation in dev. EXPOSURE and LOSS bootstrapped via idempotent SQL scripts (`python -m app.cli bootstrap-exposure` / `bootstrap-loss`).
- **Connection pooling handled by `db/` package** — `get_engine()` / `get_connection()` cache one pooled engine per named connection. Pool sizing via `MSSQL_POOL_SIZE` / `MSSQL_POOL_MAX_OVERFLOW` (set to 10/20 for 30 concurrent users).
- **Sync-by-default:** plain `def` handlers, FastAPI threadpool; `async def` only for SSE (§2.3).
- **CR-001 — `rwb_job` general queue (replaces `result_work_item`).** `result_work_item` renamed `rwb_job`. *(Superseded in part by CR-002: `irp_job_id` and the `request_key`/`origin` scheme are replaced by `requestor_type`/`requestor_id` + composite `UNIQUE(requestor_type, requestor_id, rwb_job_type)`; the heartbeat/reconciler mechanism below is unchanged.)*
- **CR-001 — Durable Redis (AOF).** Redis runs with `appendonly yes`, `appendfsync everysec`, persisted SSD volume. Required in dev, partner-Docker, and prod. Eliminates the pending-lost failure case without application-level detection.
- **CR-001 — Per-job heartbeat + single-instance reconciler.** Heartbeat emitted from a daemon thread (§2.3a) every `RWB_HEARTBEAT_INTERVAL_SECS`. Reconciler folds into the poller process. Stale threshold = `RWB_HEARTBEAT_STALE_SECS`, a constant multiple of the interval, never duration-based.
- **CR-001 — No `owner_token`, no worker-liveness table, no duration windows, no new statuses.** Heartbeat is a progress timestamp, not a lease. Recovery is per-job, not per-worker. The only statuses remain `pending / running / succeeded / failed`.
- **IRP job submission is synchronous on the request path.** Fast IRP submit call returns a job ID immediately. On failure: `SUBMISSION FAILED` status + single-threaded submission-retry batch job (§14.3).
- **Poller is a standalone loop process — not Dramatiq.** Batch-queries all non-terminal jobs per pass. Dramatiq would break the natural batching (§14.4).
- **Dramatiq workers for result processing and submission retry only** (§14.5). Redis broker.
- **EDM and RDM are first-class entities** in the Metamodel DB, each created from a shared-drive `source_file_path` (CR-003 M5 — no `file_artifact` table) (§9).
- **Analysis templates and template suites** — in MVP (practice-lead call, 2026-07-06; reverses the CR-002 deferral). Auto-naming from submission context is the intended approach (§11).
- **2026-08-14 — CIC session (suites first; predefined suites; suite run flow).** Five decisions locked (design note 14, D9–D14):
  - **Suites before execution (D9).** Template/suite definition and administration are built before individual analysis execution — CIC works in outcomes ("run the US suite"); execution is the substrate. §21 Iterations 6/7 swapped.
  - **Vocabulary (D10, amended by spec 009).** A **template** = one analysis definition ("one row in Analysis Builder"): analysis/model profile + output profile + event rate (auto-populated) + optional settings — currency is a submit-time choice, never stored (P-11). A **suite** = an unordered set of templates (P-08), defined primarily by **region + output level**, both conveyed by names, not stored fields (P-03; §11.2/§11.3).
  - **Suites are predefined, not freeform user-built (D11).** Admin-maintained; starter-set seeding and Excel export-import were deferred out of MVP (spec 009 P-02) — setup is manual, with duplicate-and-edit (P-12) as the fast path (§11.3).
  - **Run-a-suite is default-first (D13).** Select portfolios + treaties, pick the suite, go; optional expand-to-deselect (§11.3a). *(Reshaped 2026-08-20: portfolio-first modal flow, treaties picked in the modal, direct submit — see the 2026-08-20 decision above.)*
  - **Suites may mix DLM, HD, and accumulation templates (D14);** DLM-vs-accumulation separation is a convention, not a rule. Peril/portfolio mismatch failures are expected, surfaced with a reason, never silently ignored (§11.3, §11.3a).
- **v1 auth: username + bcrypt password** (`AUTH_MODE=password`). bcrypt cost 12, rate limiting, server-side sessions in WORKBENCH DB (`user_session` table), CSRF tokens, forced password change on first login, admin-only reset. No Redis dependency for auth. Upgrade to Entra SSO (`AUTH_MODE=oidc`) requires no downstream changes (§5.1, §5.2, §5.3).
- **Session store is WORKBENCH DB** (`user_session` table), not Redis. Sessions survive Redis restarts; active sessions are queryable; admin force-logout is a single UPDATE (§5.1.4).
- **Signed-cookie / server-side session** — cookie holds only the session ID (random 32-byte hex); all identity and role context lives in DB (§5.1.4).
- **No row-level security (CR-003 M2/O1).** No `customer_id`, no `apply_scope()`, no `user_customer_access`; every authenticated analyst sees every deal. Global roles gate *functions*, not *rows*; `assigned_analyst_id` is a soft "my submissions" owner (§6).
- **No file inventory (CR-003 M5).** No `file_artifact` model, scanner, or discrepancy detection; a single `source_file_path` per EDM/RDM is chosen at package creation (§8).
- **`dlm`/`hd` are NOT types** — an analysis-profile property detected from `softwareVersionCode`, used only by the grouping homogeneity check (§13.3). (There are no handle types at all under CR-002.)
- **Analysis results hybrid storage** — *revised 2026-08-25:* viewing reads the bounded `irp_analysis.loss_results` extract; the Parquet + SQL-metadata hybrid for row-level data (ELT, EP, PLT) is export-only (§16.1).
- **Top-level navigation uses `hx-boost`**, composing with `hx-push-url` (§4.3).
- **Styling extends the ITCSS design system via tokens** — never hardcoded hex (§2.4).
- **2026-07-21 — FR reconciliation pass (feature specs updated to `FUNCTIONAL_REQUIREMENTS.md`).** Five feature-shaping decisions locked from the reconciled functional requirements:
  - **Portfolio management is MVP (new §10A).** Sub-portfolio creation by **filtering** (synchronous IRP `create_portfolio()`, HTTP 201, no job) + one-click breakouts (LOB / geography / complement), values picked from real portfolio values. "Portfolio modification," data-element edits, peril-specific portfolios, and merge/combine are **out of MVP** (FR §3). The DataBridge exposure-modification path (§10.3) is the deferred Phase A route, not the MVP path.
  - **GeoHaz is hazard lookup only (new §10B).** Geocoding is not re-run (broker geocoding preserved); hazard lookup is **optional** and **not** an analysis prerequisite (FR §5).
  - **Treaty create/edit is a Risk Modeler pass-through (§12.4).** Viewing stays in-app (full attributes, expand/collapse, Excel export); create/edit opens the RM editor in a new window. Reverses the earlier in-app `create_treaty`/`create_treaty_lob` CRUD.
  - **Broker RDM results are REST-retrieved, deduped by `rdm_id` (§16.1/§17.2).** Same REST endpoints as own results; **no `rdm_result` table, no DataBridge query for results.** Pushing broker results to the Loss Repository is **out of MVP** (§17.4). *(Revised 2026-08-25: stored as the `loss_results` extract on the broker analysis row; `analysis_result_meta` is export-only.)*
  - **No EP-curve graph (§16.2).** Results are numbers/tables — "I want the numbers"; OEP + AEP both, perspective switching essential. *(Revised 2026-08-25: perspectives GR/RL/WX/QS/GU; TCE and PLT out of viewing scope — §16.2.)*

### Open decisions (need team input; do not block early iterations)

- **OQ-4 — treaty-type precedence** ("cat treaty always at the top"): a modeled attribute or display-only? Not modeled today.
- Concrete `role_kind` codes (analyst, admin, viewer?)
- Teams webhook URL: per-submission or global config?
- Notification preferences: per-user opt-in or always-on?
- **Loss Repository schema — largely received (2026-08-27).** Screenshots cover `dbo.Data`, `dbo.RMSELT`, `dbo.RMS_HistoricalRDS` with CIC's population notes (§16.3). Still owed from Cheryl: the client table's shape (read-only since 8/28 — the `add new client` insert statement is **withdrawn**, D25) and confirmation that no further tables must be written (O21-13).
- Exposure Repository schema (coordinates with reporting team)
- Idle-timeout durations (sliding + absolute)
- Export format beyond Parquet (CSV? Excel?)
- **O6-1/O6-2 — commercial-policy geographic split — resolved by product direction (2026-07-29, §10A.5).** Risk Modeler assigns whole accounts on a geographic split; the geography breakout ships with that behavior accepted and disclosed (the preview quantifies the overlap per portfolio — spec 005 FR-007), and no location-level toggle is awaited. The complement split remains a fast-follow.
- **O7-1 — hazard for HD.** Whether hazard retrieval must be run ahead of time for HD models (§10B.5). Cheryl investigating.
- **O7-2 — enhanced risk data.** Not used today, may be HD-only; availability and whether CIC wants it being checked (§10B.5). Cheryl investigating.
- **O8-1 — geocode/hazard version-stamp origin.** Confirm with Moody's where RM's geocode/hazard stamp comes from and what it gates; the workbench neither displays nor reads it (§10B.4). Cheryl / team.
- **O8-3 — hazard-execution lineage detail.** What execution detail the workbench records and displays per hazard lookup, given a naive stamp read is insufficient (§10B.4). Ben; settled at Iteration 5 spec time.
- **O14-4 — US/Canada default-settings list.** Cheryl drafts the CIC defaults to guide the manual suite setup on the admin page (starter-suite seeding deferred, spec 009 P-02); scope is US / Canada / US+Canada / global (~10 templates each). Cheryl → Ben.
- **O5-1 — event-rate scheme round-trip.** Does not appear to survive RM export → re-import (the broker scenario); near/long-term and rate vintage matter (§16.2). Ben investigating.
- **O5-2 — return-period points. CLOSED 2026-08-25:** stored/expanded set 5/10/25/50/100/250/500/1000/2000/5000/10000; condensed set 50/100/250/500/1000/10000 (§16.2, spec 011 O-03).
- **Broker retrieval exposure pointer.** `get_elt`/`get_ep`/`get_stats` take an `exposure_resource_id`; own analyses store it at submission (`irp_job.resource_uri`), but broker analyses only have the `exposureResourceId` that `search_analyses` returns, captured at RDM backfill (§15.3). Confirm the result endpoints accept it for RDM-imported analyses. Settle at Iteration 8 spec time.
- **O7-5 — accumulation ground-up.** Whether ground-up can be dropped from accumulation output via the API (§16.4a).
- **Portfolio↔analysis linking — deferred (§17.3).** Whether `analysis_result_meta` carries an `irp_portfolio` FK (DATA_MODEL §14); today analysts rely on naming conventions.
- **DataBridge → Loss Repository movement — CLOSED (8/26 D18 + 8/27 D1):** answered by removal — the workbench reads the Risk Modeler API and writes the repository directly; DataBridge is not in the path, and workflow-tool reuse is rejected (§16.3).
- **O21-5 — ELT event type. NARROWED 8/28 (O22-11):** the reference data APIs return event type per event; the plan is retroactive Parquet enrichment (§16.3). Still open, in order: (a) Cheryl's CSV cross-check of a small ELT against RiskLink's event info table — cheap and possibly decisive; (b) whether a bulk/filtered reference endpoint exists or it is one call per event, which decides viability at ELT scale; (c) Moody's answer on `event info` visibility — permissions or absence; (d) whether the type column is written back into the Parquet or held alongside. Ben + Cheryl + Moody's.
- **O21-6 — RiskLink 25 reference data has no named connection. DE-RISKED 8/28:** possibly moot if the reference-data-API route holds; still wanted by the catalog check if that survives (O21-8). Resolve O22-11 before deciding.
- **O21-8 — export corrections list.** The event-catalog check is "usually zero" and possibly obsolete (Cheryl checking); Cheng applies further silent corrections (negative standard deviations is the known case) — get the full list from Cheng before treating the review step as specified. Not advanced 8/28.
- **O21-12 — capped-value columns.** Confirm in writing which `dbo.RMSELT` columns the loss-greater-than-exposure check compares (`Loss` / `StdDevC` / `ExpValue`) — the transcript terms were garbled, and the cap writes corrected data into CIC's production table.
- **O21-2 — expiration date / data vintage.** Expiration date: listed 8/26, absent from the walkthrough and all three schema screenshots — confirm with Cheryl it is genuinely not required. Data vintage: `dbo.Data.DataVintage` is a date — confirm what sources it and whether it relates to currency-scheme vintages.
- **O21-9(d) — Parquet job economics.** Measure the export job's latency for a large ELT and a PLT, the zip size, and rate limits against the 30-concurrent-user pooling target — the answer now feeds the pre-fetch decision (O22-10) rather than the export click.
- **O22-1 — the region-peril simulation set.** The grouping request attribute is reverse-engineered from members' model profiles and rate schemes; confirm its actual name against the Risk Modeler API contract (transcript-garbled), then source it from the contract or document the derivation with a test that fails when Risk Modeler changes. Gates the grouping fix (§16.4). Ben.
- **O22-10 — the ELT pre-fetch trigger.** On analysis completion (eager — every analysis × perspective, most never exported) vs. an earlier signal than the export click; size the storage at 100+ analyses × several perspectives under `{submission_outputs_dir}` (§16.1). Ben.
- **O22-14 — a test treaty that takes loss.** Ben's WX treaties return nothing, which blocks end-to-end validation of the WX perspective and invalidates the TY-missing-from-the-Parquet negative. Cheryl providing a treaty known to take loss; Ben re-running his case manually in parallel.

### External dependencies

- **Moody's IRP** — `irp-integration` library (`IRPClient`). Seven `irp_job_type`s (`import_edm`/`import_rdm`/`delete_edm`/`geohaz`/`analysis`/`grouping`/`export`). Auth via env vars. Rate limits apply.
- **DataBridge** — Moody's cloud SQL Server. ODBC via irp-integration. Used for Phase A validation/profiling/modification and broker RDM queries.
- **Redis** — Dramatiq broker. Required for result workers and notifications.
- **Shared-drive mount** — read-only CIFS/SMB, least-privilege service account (§8.1).
- **Loss Repository** — on-prem SQL Server; app writes via `get_connection("LOSS")`; schema defined in this project (separate from Alembic, coordinated with downstream consumers).
- **Exposure Repository** — on-prem SQL Server; app writes via `get_connection("EXPOSURE")`; schema defined in this project (coordinated with reporting team).
- **Teams webhook URL** — for notifications.
- **Icon SVG source set** committed to `static/icons/`.
- **SQL Server Express** on WSL2 / Docker Desktop for local dev.

---

## 24. Change log

### 2026-08-28 — Design session 22: viewing/comparison signed off; grouping demoed with wrong output; client table read-only; Parquet replaces the paginated API

Scope: §1.4 glossary, §16.1, §16.2, §16.3, §16.4 (rewritten), §17.3, §21 Iterations 9 and 11, §23 locked + open decisions, this log; `FUNCTIONAL_REQUIREMENTS.md` §1/§6/§7 updated the same day (§6 Grouping rewritten; §7 Organizing/Comparison rows moved to Implemented against the sign-off). Source: design note 22 (the first build demo since 8/26, plus Cheryl's answers from Cheng against the note-21 asks).

- **Viewing and comparison signed off (D1–D9)** — one change request in forty minutes (right-justify return periods). Uncapped viewing, the 5-pair comparison cap, the selection-order base, the cross-currency block, and the ones/full-precision clipboard all confirmed; the comparison builder is built and accepted ahead of Iteration 10's broker-comparison scope. Closes 19 O19-8/O19-9; confirms 20 O20-6/O20-8.
- **Grouping produced wrong numbers (D13):** both event rate schemes passed into a group Risk Modeler builds with one — ~$8M apart on gross pure premium. The event rate scheme becomes a group input (selection with default); the region-peril simulation set derivation is suspect (O22-1); validation is matched manual-vs-workbench runs compared on the **EP curve** (D14). §16.4 rewritten; Iteration 9 is now spec-first and the stated priority.
- **The group as an object (D16–D18, D24):** member list required, editing later, metadata sourced from recorded group run parameters, groups listed in line on member EDM pages.
- **The client table reverses to read-only (D25),** retracting the 8/27 INSERT — clients are created upstream in Cheng's workflow tool; the insert-statement ask is withdrawn; `workbench_is_active` is again the only reference-cache carve-out.
- **Parquet replaces the paginated `get_elt()` (D28/D29):** a Risk Modeler export job returns one Parquet file per perspective, pre-fetched so the loss>exposure check runs on demand. Settles 20 O20-11 and 21 O21-9; opens the pre-fetch trigger (O22-10); `elt_record_count` becomes a post-read Parquet count.
- **Event type found in the reference data APIs (D30/D31):** the O21-5 blocker narrows to resolved-pending-volume via retroactive Parquet enrichment; the stochastic/historical split is validated against a source, never inferred.
- **TY located (D26/D27):** `RDM_TREATY` + treaty-description join, its own retrieval call; part B stays deferred, with the retrieval source built as swappable behind one transform/validate/commit flow.
- **Terminology (D23):** an analysis is *related to* an EDM, never *in* it — glossary and FR updated; EDM-screen copy owed.
- **Rejected (D22):** displaying submitted-vs-returned metadata — a deliberate non-requirement; applied treaties join the analysis view instead (D21, the last genuine run-facts gap, O22-7).

### 2026-08-27 — Design session 21: export walkthrough; workflow-tool reuse ruled out; loss repository schema received

Scope: §16.1, §16.3, §21 Iteration 11, §23 locked + open decisions, this log; `FUNCTIONAL_REQUIREMENTS.md` §6/§7 updated the same day (the Delivery block rewritten per design note 21 O21-10). Source: design note 21 (Cheryl's walkthrough of the current export in the workflow tool) plus the loss repository schema screenshots (`dbo.Data`, `dbo.RMSELT`, `dbo.RMS_HistoricalRDS`) and CIC's population notes.

- **Workflow-tool reuse rejected (D1):** the path is Risk Modeler API → workbench transform → CIC SQL; DataBridge is out (closes the standing "DataBridge → Loss Repository" open question by removal, with 8/26 D18).
- **§16.3 rewritten around the received schema:** three write targets (`dbo.Data` header with repository-generated `DataID`; `dbo.RMSELT` stochastic-only; `dbo.RMS_HistoricalRDS` opt-in, sourced from RiskLink reference data) plus CIC's client table as a **read + INSERT** target. `ArchiveFile`, `AReLossSet`, `LOB`, `Geography` are not populated. AAL is never written — calculated downstream.
- **The export is two-phase:** the loss-greater-than-exposure cap (a must-have, D18) needs the full ELT before the review renders, so retrieval fires at export request, not commit. `get_elt()` is capped at 1,000 rows per call and returns no count — `analysis_result_meta.elt_record_count` cannot come "from the response" (DATA_MODEL §9 correction pending).
- **Perspectives:** export offers only valid perspectives (per analysis, platform-derived), intersected across a multi-select, one per batch; per-analysis multi-select rejected; WX/QS never uploaded; RP added to the export vocabulary (advances design note 20 O20-5).
- **Treaty-level (TY) export is part B** — treaty selection with cross-analysis aggregation within one EDM, explicitly deferred (D12/D13); not a financial perspective. CIC's repository-side "group losses" is distinct from workbench grouping — collision guard added to FR §6.
- **New blocker (O21-5):** the ELT API returns no event type; the stochastic/historical split is unresolvable from the response alone. Iteration 11 is marked blocked on it plus the owed client-table insert statement and capped-column confirmation (O21-12/O21-13).

### 2026-08-25 (later) — Design session 19: ELTs out of viewing; bounded extract storage; merged table; dedicated results page; comparison designed

Scope: §15.3, §16.1, §16.2, §17.3, §21 Iteration 8, §23 O5-2, this log. Source: design note 19 (8/25 session) plus the retrieval-model decision recorded in spec 011 `research.md#R3`.

- **ELTs are not viewable** (D5) — Cheryl/Wendy: "we just need the stats, the AAL and the EP curve." ELT-derived metrics (max event loss, record count), PLT, and TCE leave viewing scope; row-level machinery (DATA_MODEL §9) narrows to export, decided at the 8/26 export-requirements session.
- **Storage:** viewing reads a bounded per-perspective extract on `irp_analysis.loss_results` (AAL, standard deviation, OEP/AEP at 11 fixed return periods), written worker-side. Revises session 19 D6's "store nothing" once the live response captures showed the full EP-curve response is ~1.2MB per perspective and the AAL grid column needs list-scale reads.
- **Perspectives fixed:** GR, RL, WX, QS, GU (closes O5-3 — Gross default within the set). **Return periods fixed** (closes O5-2, above).
- **§16.2 redesigned:** one merged own+broker analyses table (group-by-RDM relocated inside it), condensed results inline in the expanded row, dedicated results page (multi-select from both entry points, new tab, breadcrumbs, tab titles), units selector, copy with headers, N-up soft guideline.
- **§17.3 comparison designed** (still Iteration 10): pairwise cart, selection order fixes base/column/percent-change direction, screen-wide perspective and EP-type toggles; base-vs-many rejected.

### 2026-08-25 — New Iteration 8: analysis results sync & viewing; Iterations 8–13 renumbered 9–14

Scope: §14.5, §15.3, §16.2, §17.2, §21 (Iteration 7 trimmed, new Iteration 8, renumbering and cross-references), §23 locked + open decisions, this log. No CR (re-sequencing plus fleshing out capability the PRD already specified). References to Iterations 8–13 in change-log entries dated before 2026-08-25 mean today's Iterations 9–14.

- **Results had no iteration home.** Own-analysis loss retrieval was the unshipped final phase of Iteration 7 (spec 010 US4); broker retrieval was deferred by spec 010 P-12; the §16.2 results review UI was never scheduled. All three consolidate into the new Iteration 8 (spec 011).
- **Retrieval is automatic in both cases** (§15.3): own analyses on `FINISHED` (chained from `finalize_analysis`); broker analyses on RDM import completion (chained from `backfill_rdm_analyses`), once per `rdm_id`. No analyst action triggers retrieval.
- **§16.2 rewritten around the two viewing places:** the submission-scoped Results rail section (own + broker grouped under their RDM) and loss numbers inline on the existing EDM/RDM/analysis detail views. The comparison panel is explicitly excluded from Iteration 8 — broker comparison stays its own iteration (now 10).
- **Open decision added:** whether the result endpoints accept the `search_analyses` exposure pointer for RDM-imported analyses (broker analyses have no submission-time `resource_uri`). O5-2 (return-period points) stamped to settle at Iteration 8 spec time.

### 2026-08-24 — Spec 009 reconciliation: templates/suites as built

Scope: §2.6, §11.2, §11.3, §15.2, §21 Iteration 6, §23 locked + open decisions, this log — brings the PRD in line with the spec-009 decisions made in the 2026-08-18/20 design sessions (design notes 16–18). No CR (the feature shipped as specified; the PRD had kept the earlier drafts).

- **Suites are unordered (P-08, reverses D10's "ordered"):** no item position, no per-item `portfolio_name_override`.
- **Currency removed from templates entirely (P-11):** analysis currency, currency scheme, and scheme vintage are submit-time choices (Iteration 7), never stored on templates or suites.
- **Template columns dropped (P-03/P-09):** `treaty_name_pattern`, `region_label`, `peril_code`, `auto_name_pattern` — region is not a stored attribute; auto-naming carries no template pattern.
- **Starter-suite seeding and Excel export/import deferred out of MVP (P-02):** setup is manual via the admin page; **duplicate-and-edit (P-12)** is the near-term path. O14-2 closed by deferral; O14-3 resolved by P-03.
- **§15.2 sync targets corrected to the six sets actually cached:** model profiles, output profiles, event rate schemes, currencies, currency schemes, currency scheme vintages. `irp_simulation_set`, `irp_tag`, `irp_database_server`, and `irp_edm_cache` are not synced and do not exist.

### 2026-08-20 — Design session 8/20 (note 17): currency to submit time, suite level; dedup dropped; submission tag

Scope: §1.4 (template glossary), §11.1 (currency bullet), §11.2 (template fields), §11.3a (currency-in-modal bullet, direct-submit bullet), this log; `DATA_MODEL.md` §6/§7/§10 and `FUNCTIONAL_REQUIREMENTS.md` §4 updated the same day; spec 010 amended (P-02; new P-15/P-16/P-17). No CR.

- **Currency removed from templates, chosen at submit time per suite (note 17 D4/D5; reverses spec 009 P-10).** The execution modal confirms analysis currency + currency scheme + scheme vintage per chosen suite (once for a template run); mixed-currency books run as separate regional suites. Defaults are **pinned env vars** (`DEFAULT_ANALYSIS_CURRENCY_*`) ops maintains — the system never advances them when a new vintage releases (D6/D7), so templates never go stale.
- **Template dedup across suites dropped (spec 010 P-02 amended).** One analysis per selected portfolio × selected template of each chosen suite; a shared template submits once per suite, with that suite's currency, the rerun suffix keeping names unique.
- **Submission tag at submit (note 17 D12 direction; spec 010 P-17).** Analyses executed from a submission context carry the submission's name as a Risk Modeler tag; the workbench records the execution → submission association.
- Suites remain a single grouping level — no suites-of-suites (D8, validates the existing two-table shape); Excel import/export stays tabled in favor of duplicate-and-edit + manual go-live setup (D9, already shipped/deferred per spec 009); suite/template name uniqueness (D10) already enforced by the spec-009 unique live-name indexes; the "template" name stands — no rename to "analysis settings" (D3 re-opened, settled as template).

### 2026-08-20 — Iteration 7 planning: portfolio-first execution flow; §11 reconciled to shipped Iteration 6

Scope: §1.4 (template glossary), §2.6 (naming rule), §11.1a/§11.2/§11.3 (reconciled to spec 009 as shipped), §11.3a (rewritten to the execution flow), §21 Iterations 6 (shipped note, exit trimmed) and 7 (phased, In/Exit rewritten), §23 locked + open decisions, this log; `FUNCTIONAL_REQUIREMENTS.md` §4/§5 updated the same day. No CR.

- **Execution flow locked (2026-08-20):** portfolio-first — the analyst selects one or more portfolios, and Execute Suite / Execute Template opens a searchable modal; several suites or several templates per execution, never mixed; Submit disabled until at least one is chosen; expand-to-deselect inside the modal; treaties picked in the modal; direct submit with no review page; one analysis per portfolio × template, deduplicated across suites.
- **Analysis naming resolved (closes O7-3/O14-9):** fixed rule, portfolio name + template name; no configurable pattern. Risk Modeler caps analysis names at 64 characters (confirmed 2026-08-20): the submitted name is truncated from the right to fit, and the full name is stored on `irp_analysis`.
- **`irp_analysis` lifecycle:** row written at submission, settings/metadata backfilled on completion; loss-number retrieval is a later phase of Iteration 7. Executed analyses appear in a user-executed section on the EDM detail page (no RDM grouping, portfolio shown — trustworthy per the §2.2 trust-rule carve-out for analyses CIC ran itself) with live status updates.
- **Iteration 7 phased:** suite execution first, single-template execution second, loss retrieval later.
- **§11 reconciled to spec 009 as shipped:** admin-gated create/edit/delete (P-01); starter seeding + CSV/Excel deferred (P-02); no stored region/peril fields (P-03, closes O14-3); unordered suites, no per-item settings (P-08); `treaty_name_pattern` dropped — treaties picked at run time (P-09, closes the O14-8 treaty question; LOB rides on tags/naming); currency + scheme + vintage all required, no submit-time defaulting (P-10); analysis settings surfaced in the builder with defaults (009 FR-005); `auto_name_pattern` dropped.

### 2026-08-19 — Data version config collapsed to a single value

Scope: §10B.2, §21 Iteration 5, spec 007 (research R6) — same day as the entry below. Risk Modeler
does not accept the literal string `"latest"` for geohaz's `version` field; confirmed wrong before
shipping. Data version stays config-owned, but the list-shaped `HAZARD_DATA_VERSIONS` (comma list,
first entry used) is replaced with a single `HAZARD_DATA_VERSION` string (default `25.0`) — the
launch is one-click with no dropdown, so the list never had more than one live member.

### 2026-08-19 — Data version sends the literal "latest" (reverted same day)

Scope: §10B.2, §21 Iteration 5, spec 007 (research R6) — Risk Modeler documentation appeared to
confirm `version` accepts the literal string `"latest"`, resolved server-side. Reverted same day
(entry above) after confirming Risk Modeler rejects the literal.

### 2026-08-17 — Session 8/14 reconciliation: suites before execution; predefined suites + administration

Scope: §11 (header note, §11.1a, §11.2, §11.3, new §11.3a), §21 Iterations 6/7 swapped (cross-references renumbered in §2.6, the `cycle` note, §16.1, §21 Iterations 3/9/10), §23 locked + open decisions, this log. Reconciles the PRD with design session 8/14 (design note 14, D9–D14); `FUNCTIONAL_REQUIREMENTS.md` §4/§5 updated the same day. No CR (feature already in MVP; this re-sequences and pins its shape).

- **Iterations 6 and 7 swapped (D9).** Iteration 6 is now **templates & suites — definition and administration** (entities, admin page, seeded starter suites, CSV/Excel export-import, IRP metadata sync, analysis metadata screen); Iteration 7 is **analysis execution — single + suite run** (execution engine, suite run flow, results retrieval). References to the old Iteration 6 (execution/results) in change-log entries dated before 2026-08-17 mean today's Iteration 7.
- **§11 pinned to the locked vocabulary (D10):** template = one analysis definition ("one row in Analysis Builder"); suite = ordered set of templates defined primarily by region + output level; LOB as a further axis via tags/naming (O14-8).
- **§11.3 rewritten for predefined suites (D11):** admin page (built full-featured first, then pared down — O14-1), starter set (US / Canada / US+Canada / global, ~10 templates each — O14-4), CSV/Excel export + import (O14-2); suites may mix DLM/HD/accumulation (D14) — separation demoted to a convention.
- **New §11.3a — running a suite (D13/D14):** one-action default path, expand-to-deselect, batch apply loop, mismatch failures expected but surfaced with reason. Delivered with Iteration 7.
- **§11.1a — analysis metadata screen:** model/output/accumulation profiles + currency schemes viewed in the workbench, created in Risk Modeler, synced back; event-rate schemes selected, never authored.
- **Open items added:** O14-3 (region as a selection axis — resolve in the Iteration 6 spec), O14-4 (CIC default-settings list), O14-8 (LOB/treaties), O14-9 (auto-naming, folded into O7-3). GeoHaz and EDM-notes decisions from the same session (D1–D8) are tracked in design note 14 and land with their own features, not this pass.
### 2026-08-17 — Portfolios table shows a Hazard Version column

Scope: §10B.4 — approver direction (P-03/P-07), spec 007. Supersedes the 2026-08-12 entry's "no
version stamps" framing below: the portfolios table's final column is now **"Hazard Version"**,
showing a non-terminal geohaz job's in-line status or otherwise the portfolio's raw stored
`hazardVersion`. The value still gates nothing — Risk Modeler's stamp is not read to block or permit
any workbench action.

### 2026-08-14 — DLM hazard lookup changed to one click

Scope: §10B.1, §10B.2, §21 Iteration 5, and this log — applies design session D4 from `docs/design_session_notes/14_analysis_suites_first_geohaz_dlm_hazard_edm_notes.md`.

- The analyst selects portfolios and clicks Run hazard lookup. No parameter modal opens.
- Every workbench launch uses the first configured data version, DLM, earthquake + windstorm, previous locations not skipped, and user-defined hazard values overwritten.

### 2026-08-12 — GeoHaz reconciled to the Aug 7 design session (Iteration 5 prep)

Scope: §10B, §13.1 reference, §21 Iteration 5, §23 open decisions, this log — folds the 2026-08-07 design-session GeoHaz decisions (`docs/design_session_notes/10_edm_summary_submissions_geohaz_currency.md` §2) into the PRD ahead of the Iteration 5 spec. No CR (feature scope unchanged; the session settled how the op is launched and displayed).

- **Multi-portfolio launch from the summary page (§10B.1).** Hazard lookup is launched from the EDM/portfolio summary page against one or more selected portfolios; one geohaz job per portfolio, one parameter set per launch. §10B previously said "a portfolio", singular.
- **App-side lineage, not version stamps (new §10B.4).** The workbench displays no geocode/hazard version stamp and never reads RM's stamp to gate anything — a live analysis on stamp-less, parcel-geocoded data succeeded. Instead the summary page shows, per portfolio, whether hazard lookup has been run through the workbench and the in-line status of any non-terminal geohaz job (polling refresh; §14.7 SSE stays in Iteration 6). What execution detail is recorded and displayed per lookup is O8-3, settled at Iteration 5 spec time. The gate section moved from §10B.4 to §10B.5.
- **§23** — added O8-1 (version-stamp origin, confirm with Moody's) and O8-3 (hazard-execution lineage detail).

### 2026-07-27 — Spec 003 amendment (issues #17 + #11): name collision blocks the save

Scope: §9.4 name-collision paragraph + Save bullet, this log — reconciles the PRD with the amended spec-003 FR-012/SC-005 and superseded research R8 (approver-confirmed 2026-07-27). No CR (behavior refinement forced by irp-integration ≥ 0.2.1, which validates EDM name uniqueness at submit time — the old non-blocking override could no longer produce the duplicate it offered, only a graceless worker failure minutes later).

- **Collision → blocking error at save time** on every surface (the EDM/RDM import forms), with as-you-type validation (debounced ~500ms, results cached ~30s in-process — issue #11) that disables the submit buttons while a collision error is showing.
- **Fail open when Risk Modeler is unreachable:** the save proceeds with a visible warning; the worker-side submit validation is the backstop, and its specific failure message is surfaced on the EDM/RDM detail pages.
- **Delete-the-existing-Risk-Modeler-entity-and-reimport** as a collision remedy is deferred to a follow-up issue.
- The submission-level "similar deal already exists" warning (§7.2b) is unrelated and stays non-blocking.

### 2026-07-23 — Spec 004 (Iteration 3): broker analyses linked to the portfolio they ran against

Scope: §21 Iteration 3 (In/Exit), FR §7 note, this log only — reconciles the PRD with the spec-004 portfolio↔analysis linkage decision (spec 004 FR-036/FR-037, SC-009, research R9; approver-confirmed 7/23). No CR (docs-only refinement; the functional requirement is unchanged — FR §2.3 already lists *"portfolio it ran against"* as broker-analysis metadata).

- **The "portfolio it ran against" is surfaced, and it is distinct from the deferred results-comparison linking.** FR §2.3 lists the *portfolio it ran against* as part of a broker analysis's settings/metadata; Iteration 3 delivers exactly that — each broker analysis is associated with its owning portfolio by **capturing Risk Modeler's `exposureResourceId`** (only where `exposureResourceType = PORTFOLIO`) and **resolving the owning `irp_portfolio` at read time** (join on `edm_id` + RM portfolio id; not a stored FK). This is a concrete RM-field association, **not** the harder cross-analysis "portfolio↔analysis linking" that FR §7 marks deferred (that one is about own-vs-broker results comparison via naming conventions, and stays deferred — §17/§23). Some analyses will not resolve: a **group** (`is_group = true`) is a single analysis shown as **"Group"** (its member sub-analyses are not knowable from RM), and any non-portfolio/unresolvable exposure shows **"— not linked"** — both normal states, never an error. Consequently `irp_analysis.group_parent_id` (DATA_MODEL §6) is **deferred** (nothing populates it this iteration).
- **Analyses surface on the EDM detail page as well as the RDM page.** The prior Iteration-3 "In" scoped broker-analysis viewing to "the RDM/analysis detail pages"; this iteration additionally shows them on the **EDM detail page** in two views — **inline under each portfolio** (only the analyses linked to it) and a **standalone section grouped by source RDM** carrying the resolved portfolio per row. The RDM page shows the same analyses with an added EDM column. Page composition is fixed in spec-004 `ui.md`.
- **Rationale.** The analyst chooses what to run at the portfolio level (7/14), so reading a broker result *in the context of the portfolio it covers* is part of "understand before acting." Because the pointer comes straight from RM and resolves against the portfolios backfilled the same iteration, it needs no new storage layer and no dependency on the deferred comparison work.

### 2026-07-23 — Spec 004 (Iteration 3): broker loss numbers deferred to Iteration 6

Scope: §21 Iteration 3 (In/Exit), §17.2, §21 Iteration 9 (In) only — reconciles the PRD with the spec-004 `/speckit-clarify` scope decisions. No CR (docs-only refinement; the functional requirements are unchanged — FR §2.3 already frames broker review as *analysis settings/metadata* to judge how much work an RDM needs, distinct from the FR §7 loss-results view).

- **Broker RDM viewing split across two iterations.** Iteration 3 delivers broker **analyses grouped by `rdm_id` + each analysis's settings/metadata** (the §16.2 metadata list) so the analyst can gauge how much work an RDM needs before acting. Broker **loss numbers** — ELT/EP/AAL, standard deviation, return-period losses, OEP/AEP/TCE, PLT — and their storage (`analysis_result_meta` + Parquet) and the `retrieve_analysis_results` worker are **deferred to Iteration 6** (the results iteration), where own and broker results are retrieved via the same REST endpoints. This narrows the prior Iteration-3 Exit phrase "broker loss results" and updates the "viewable since Iteration 3" references in §17.2 and Iteration 9 to distinguish settings (Iteration 3) from loss numbers (Iteration 6).
- **Rationale.** Iteration 3 is placed first to let the analyst *understand* imported exposure and broker results before acting; the settings/metadata review satisfies that "how much work does this RDM need" decision without pulling the whole results-storage layer (Parquet + `analysis_result_meta` + perspective switching) forward. Spec/plan/tasks for 004 build no result-number storage.

### 2026-07-21 — Build-plan restructure + functional-requirements reconciliation

Two same-day passes. **(1) Build plan (§21)** restructured into workflow-ordered Iterations 3–13 (details & backfill → portfolio → geohaz → analysis execution → templates → grouping → broker comparison → export → notifications → search → home); the old monolithic Iteration 6 was split, results *viewing* moved to Iteration 3, analysis execution placed before templates, broker comparison promoted to its own iteration, and notifications rescheduled as greenfield Iteration 11 (the Iteration-2 "notification on completion" exit was an overclaim). **(2) Feature specs reconciled to `FUNCTIONAL_REQUIREMENTS.md`** (the newer source of truth). Changes:

- **§0** — corrected the stale section map (build plan §21, adversarial §22, decisions §23, change log §24) and the "three declarative sources of truth" → the one nav manifest (CR-002); noted FR is the newer source of truth on disagreement.
- **§1.2/§1.3** — three-phase framing reconciled: Phase A validation/profiling/Exposure-Repo out of MVP; Phase A now "Data Setup & Shaping" (ingest → review → sub-portfolio creation); sub-portfolio creation is synchronous IRP, not DataBridge; GeoHaz added to Phase B; notification channel "desktop toast" → in-app center; broker-to-Loss-Repo push flagged out.
- **§1.4** — glossary: added **Sub-portfolio (breakout)** and **GeoHaz (hazard lookup)**; DLM/HD gained the event-rate-scheme rule.
- **New §10A — Portfolio management (sub-portfolios & breakouts, MVP).** Filtered sub-portfolio creation (values from real portfolio values), current-split view, one-click breakouts (LOB / geography / complement, "do the opposite", sum-to-100%), the account-bucketing double-count caveat, and the **commercial-policy geographic-split open question** (blocks geo/complement breakouts). "Modification" dropped.
- **New §10B — GeoHaz (hazard lookup, MVP).** Hazard lookup only (geocoding preserved); parameters + defaults (data version latest, DLM, EQ+wind perils, overwrite missing); per-layer locations-looked-up summary; optional, not an analysis prerequisite; HD/enhanced-risk-data open questions.
- **§10** — retitled to mark the whole Phase A section **deferred / out of MVP**; §10.3 sub-portfolio bullet moved to §10A; peril-specific & data-element mods marked out.
- **§11.1a (new)** — profiles & per-analysis settings from FR §4 (multiple/UD profiles, filterable lists, franchise/construction toggles, min-loss/max-event held at defaults, currency defaulting, treaties by name/pattern). §2.6 auto-naming iteration ref 4→7 and draft convention flagged unfinalized (O7-3).
- **§12.4 — treaties: flipped to RM pass-through** for create/edit (FR §5); viewing stays in-app (full attributes, expand/collapse, Excel export). §14.3 treaty note updated.
- **§13.1** — clarified GeoHaz optional / not an analysis prerequisite; sub-portfolio ≥1-portfolio rationale.
- **§16.1 — rewritten to the Parquet-hybrid model** (`analysis_result_meta` + Parquet; the row-level SQL tables retired). **§16.2** — FR §7 metric set (no EP graph, OEP+AEP, TCE toggle, std dev, return periods, PLT HD-only), perspective switching, ~5-on-screen density + full-listing, metadata list, percent-diff. **§16.3** — ELT upload contents; own-only; copy/paste; DataBridge→Loss-Repo open question. **§16.4/§16.4a** — grouping detail + accumulation output.
- **§17 — broker comparison rewritten** to REST/`rdm_id`/`analysis_result_meta` (dropped `rdm_result` + DataBridge); percent-diff; portfolio↔analysis linking deferred; **§17.4 push-to-Loss-Repo out of MVP** (PATE / formal validation / CRM-tag carry also out). Reconciled §9.2/§12.3/§15.4/§14.5 accordingly.
- **§18 — notifications de-deferred** (Iteration 11): channels (Teams/email/in-app center), complete+fail events, `notification_preference` re-introduced; per-user-vs-always-on and Teams-URL scope remain open decisions.
- **§19** — search providers gained packages, portfolios, treaties; noted search/sort/filter on every list (FR 7/14).
- **§23** — added five locked decisions (portfolio MVP, geohaz hazard-only, treaty pass-through, broker-results REST, no EP graph) and the FR open questions (O6-1/2, O7-1/2/3/5, O5-1/2, portfolio↔analysis linking, DataBridge→Loss-Repo).

### 2026-07-10 — Spec-002 clarify: §7.2a closed-state semantics (fully read-only; CANCELLED reopenable)

Scope: §7.2a wording + §21 Iteration 1 In/Exit; reconciles the PRD with the spec-002 `/speckit.clarify` decisions. No CR (docs-only refinement, consistent with the CR-004-skip decision).

- **Closed = fully read-only.** `COMPLETED` (and `CANCELLED`) now block **all** analyst edits — the submission's own fields, its CRM-ID tags, and package create/sync/delete — not just package actions as the prior wording read. This gives the status gate a real, testable effect in Iteration 1 (field/CRM-tag edits), with the package-action effect following in Iteration 2. Reopening remains the escape hatch.
- **`CANCELLED` is reopenable.** Dropped the "terminal state" framing: `CANCELLED → ACTIVE` is now allowed alongside `COMPLETED → ACTIVE`. Because there is no delete, reopening is the recovery path for a mistaken cancel — consistent with "no delete, ever" and "the analyst is always in the driver's seat."

### 2026-07-10 — July 9 CIC session: package = bundle; EDM/RDM asymmetry; broker-result dedup; submission identity

- **Package regrained to a bundle.** Dropped `package.edm_id`/`package.rdm_id`; membership now on `irp_edm.package_id`/`irp_rdm.package_id` (any combination of EDMs/RDMs; ≥1 member app-enforced, no column CHECK). §1.4, §7.4, §8.1, §9.1, §9.2, §9.4 updated; DATA_MODEL §4/§5/§12.
- **EDM/RDM asymmetry.** EDM = DataBridge SQL database; RDM = tracked file, not a DataBridge asset. An RDM applies to **every** EDM in its bundle (full grid), one `rdm_import` apply job per EDM. `irp_rdm.edm_id` dropped; `irp_rdm.status` = combined rollup of apply jobs; one `irp_rdm` row per file, one `irp_analysis` per Moody's object. `irp_analysis.edm_id` is nullable (≥1-of-(edm_id, rdm_id) CHECK) so RDM-only analyses with no EDM are valid.
- **Broker-result dedup by `rdm_id`.** Broker result data (static, identical across the M EDM-copies) is stored once per RDM source analysis + perspective; `analysis_result_meta.analysis_id` nullable + CHECK exactly one of `analysis_id`/`rdm_id`; `retrieve_analysis_results` fires once per `rdm_id`. §16.1; DATA_MODEL §9.
- **Submission identity.** `submission.name` UNIQUE dropped — surrogate `id` key + non-blocking duplicate warning; distinct deals can share every naming attribute (OQ-3). §7.2/§7.2b.
- **Stance retoned to provisional.** CIC reopened the top-level organization on July 9 (OQ-1/OQ-2); DATA_MODEL §4 / PRD §7 are build-to-learn, not ratified. Open questions logged (§23; DATA_MODEL §14).
- **Build-plan corrections.** §21 Iteration 1 package-structure bullet + exit criteria updated for the bundle model; the spec-002 reconciliation note corrected (only `customer`/`program`/`user_customer_access` + `db/scope.py` were ever built — `submission`/`package`/file-inventory never were).

### 2026-07-08 — Build-plan reorder: EDM/RDM + Packages before search; Phase A dropped from plan

Scope: §21 build plan only (no feature-section behavior changes).

- **Swapped old Iterations 2 and 3.** EDM/RDM entity management now comes first (new **Iteration 2 — "EDM & RDM entity management (incl. Packages)"**), and the search framework follows (new **Iteration 3 — "Search framework"**). Rationale: packages depend on the EDM/RDM entities and IRP import plumbing, so building them together is more natural than fronting the search framework.
- **Package structure moved up to Iteration 1; behavior stays in Iteration 2.** Iteration 1 renamed **"Submission & Package domain model"** and now owns the full DATA_MODEL §4 static structure — the `package`/`submission_package` tables, the submission↔package M:N, nullable `edm_id`/`rdm_id` + the ≥1 CHECK, soft-delete, and their `db/` access + constraint tests. Package *behavior* (creation via shared-drive browse, name-collision check, IRP sync/delete, §7.4 cards) remains Iteration 2, because the ≥1 CHECK means a package can't be created until an EDM/RDM exists. This thickens Iteration 1 and gives Iteration 2 a stable schema to build on.
- **Package behavior consolidated into one iteration, real from the start.** Package behavior was previously split — UI + stubbed `rwb_job` rows in old Iteration 2, real IRP calls in old Iteration 3. Because the IRP import plumbing (§14.3/§14.4/§14.5) now lands in the same iteration, Package sync/delete are real in Iteration 2 (the UI may still be built against 60-second stubs first and wired within the iteration). The stub-then-real two-step across iterations is gone.
- **§20.4 query-string filtering + the Jobs list moved into Iteration 2** (from the old search iteration) so a package card's job-count link has a pre-filtered Jobs list to land on. The command-palette search framework (§19, Ctrl/Cmd-J) stays in the search iteration.
- **Phase A dropped as a build-plan iteration.** Old Iteration 4 (§10 validation/profiling/exposure modification, §16.5 Exposure Repository write) is out of MVP per `mvp-scope.md §6` — there is nothing to build — so it is no longer a numbered iteration; a short "not scheduled" note preserves the traceability and re-entry path. The §10/§16.5 feature sections (already marked out of MVP) are unchanged.
- **Renumbered** the tail: Analysis templates 5→4, Prerequisite gate 6→5, Analysis execution 7→6, Export/polish 8→7. Updated forward-references: §2.6/§11.2 auto-naming note (Iteration 5→4), A21 (real Package sync/delete now flagged before Iteration 2), and the prior CR-003 change-log entry's Iteration pointer (5→4).

### 2026-07-08 — CR-003: Submission + Package; drop Customer/Program & RLS; simplify file handling

Applied `docs/CR/CR_03__SUBMISSION_PACKAGE_MODEL.md` (decisions M1–M5, O1–O9) to the PRD, following the same CR's earlier passes on DATA_MODEL.md (2026-07-07) and the constitution → v3.0.0 (2026-07-08). Where the PRD asserted the opposite of a locked decision, the CR won. Highlights:

- **§1.4, §7** — `submission` is now the top-level deal (no Customer/Program). §7 retitled "Submission & Package"; §7.1 business hierarchy and §7.1a customer seeding **deleted**; §7.2 fields rewritten (`cedant_name`/`treaty_type_code`/`inception_date`/`treaty_year`/`renews_from_submission_id`/`directory_path`; CRM IDs as a `submission_crm_id` tag set, not a single field; no `customer_id`/`program_id`).
- **§6** — retitled "Authorization"; the customer-access/RLS subsection **deleted**. New banner: no `customer_id`, no `apply_scope()`, no `user_customer_access`; every authenticated analyst sees every deal; roles gate functions, not rows; `assigned_analyst_id` is a soft owner.
- **§8** — the entire file-inventory subsystem (directory inventory, immutable `file_artifact`, scanner, tagging, discrepancies, upload store, ignore ruleset, directory error/warning states) **deleted** and replaced with "File handling": pick a package shape and browse/select shared-drive file(s) at package creation; path stored as `source_file_path`.
- **§7.2b** — `UNIQUE(program_id, name)` → global `UNIQUE(name)` (O5). **§9.2** — `irp_rdm.edm_id` NOT NULL → **nullable** (O2, RDM-only valid). **§9.1/§9.2** — `submission_id`/`customer_id`/`file_artifact_id` dropped; `source_file_path` added. **§9.4** — package is M:N with submission; EDM-only, RDM-only, and both are all valid; creation flow rewritten per O9; sync head-job is no longer always the EDM.
- **§2.6/§11.2 auto-naming** — token set resolved to `cedant_name` + `treaty_year` + region + peril. **§11.2/§11.3** — template/suite `customer_id` scope → global or `created_by`. **§9.3** libraries "customer-scoped" → global. **§15.5** `irp_portfolio.customer_id` dropped. **§19/§20.4** — search no longer customer-scoped; `customer_id` removed from the filter vocabulary; job-count links filter by package (O7).
- **§21 build plan** — Iteration 1 retitled "Submission domain model" (deal-centric, no RLS/inventory); Iteration 2 "Search framework & packages" (package creation with shared-drive browse, no ignore ruleset). Reconciliation with the already-built spec-002 code (customer/program spine, RLS, file inventory) is flagged as a separate joint decision (CR-003 §8.3), not folded into this plan.
- **§22/§23** — adversarial items A3/A9/A10/A11/A13/A18 retired or reframed (RLS/file-inventory concerns dissolved); locked-decisions list gains a CR-003 entry and drops the App-level-RLS and immutable-artifact bullets.

> **Not changed here:** §12–§14 (Work model / prerequisite gate / execution engine) remain a separate workstream (being redesigned toward an IRP-Jobs/RWB-Jobs-only model); a few references there (e.g. "each RDM is tied to an EDM") will be reconciled with CR-003 O2 when that redesign lands. The spec-002 **code** removal is deferred (CR-003 §8.3).

### 2026-07-06 — Practice-lead review: MDF support, templates back in MVP, package/delete semantics

Review pass over the PRD following the same review of DATA_MODEL.md (see its change log for schema-level detail).

- **Analysis templates & suites returned to MVP (§1.1, §11, §21 Iteration 4, §23).** Reverses the CR-002 deferral (practice-lead call) — batch submission from saved templates is the #1 analyst pain point, so it ships rather than being built only on demand. The `auto_name_pattern` token set (references the dropped `cycle`) remains the one open item.
- **EDM/RDM import accepts `.mdf` as well as `.bak` (§1.1, §9.1, §9.2, §14.3, §21).** File references generalized to `.bak/.mdf`; matches `mvp-scope.md` ("upload EDM MDF/BAK").
- **RDM-only packages are invalid (§9.4).** Every package has an EDM (an RDM is meaningless without one); EDM-only packages remain valid.
- **Package delete dependency corrected — one-way (§9.4, §22 A21).** Deleting an RDM never cascades into deleting its EDM; an EDM delete depends on its RDMs being deleted first and drives that cleanup.
- **`irp_analysis.origin` column dropped (§12).** Own vs. broker is derived from `rdm_id` (own/broker) rather than stored. Schema detail in DATA_MODEL.md. (The creation-lineage column keeps its existing name, `created_by_irp_job_irp_id`.)

### 2026-07-06 — PR #5 / spec 002: domain, file inventory & RLS (Iteration 1)

Reconciled the PRD with the approved spec `specs/002-domain-file-inventory-rls/spec.md` (PR #5). Most of this iteration's scope (§6 RLS, §7 domain/status, §8 file inventory) was already written during CR-002 pre-planning; this entry records what the spec **added or clarified**.

- **§20.8 (new) Optimistic concurrency** — `updated_at`-keyed lost-update protection on analyst-editable rows (`submission`, `file_artifact`); rejected writes surface a conflict rather than overwriting (FR-045/046).
- **§6.2** — access grants/revokes take effect on the next request, not the next login (FR-021).
- **§7.1a** — seeding CSV is minimal (`short_code` + `name` only); malformed/duplicate rows are skipped without aborting the run (FR-001, edge case).
- **§7.2a** — `COMPLETED` now precisely blocks *analyst-initiated edits* only; read-only viewing and discrepancy-detection scans continue; reopening restores edit capability (FR-013).
- **§8.3** — a failed/unreachable scan never flips artifacts to `missing` (FR-044); `COMPLETED` submissions still scan read-only.
- **§8.4** — tagging only sets `tag_code`; the `irp_edm`/`irp_rdm` entity is created later (Package/import), not at tag time (FR-033).
- **§8.5** — discrepancies carry a resolved/unresolved state and are analyst-resolved, not auto-cleared (FR-040).
- **§8.8 (new)** — submission-detail error state (no directory / unreachable) vs. warning state (reachable but empty), re-evaluated on add-directory/refresh, not sticky (FR-041–043).
- **No "Batch" concept** — PR #5 removed the *Batch* metamodel notion from the sequence diagrams; the PRD already frames a batch as an app-side loop over single submits (§14.3), not a persisted entity — no change needed.

### 2026-07-06 — CR-002: no workflow engine

Applied CR-002 (`docs/CR_02__NO_WORKFLOW_ENGINE.md`). The workbench is not a workflow engine — the Workflow / Stage / Task / typed-handle / type-port-registry / manifest-projection layer is removed.

- **§1.4 glossary** — dropped Workflow/Stage/Task/Handle; added Prerequisite gate, Name-based coupling, Analysis, Treaty; `irp_job_type` values are now `edm_import`/`rdm_import`/`geohaz`/`analysis`/`grouping`/`export`; RWB-job idempotency is now a composite key, not `request_key`.
- **§2.1** — "three declarative sources of truth" → the navigation manifest alone; the workflow-definition manifest, type/port registry, and manifest→DB projection are removed. §2.5 maintainability contract rewritten around IRP-op / worker / validation additions.
- **§12** rewritten as "Work model — Submission → EDM/RDM → Job" (spine, persistence tiers, EDM/RDM ops, treaties). **§13** rewritten as "Prerequisite gate, name-based coupling & point-of-action validation." **§14** rewritten: `irp_job` is the executable unit, synchronous submit, `SUBMISSION FAILED` vs `FAILED`, `irp_job_resource`, six `irp_job_type`s polled via single-status-check methods, `rwb_job` decoupled and keyed by `(requestor_type, requestor_id, rwb_job_type)`, single-threaded submission retry, derived submission progress (no stage rollups).
- **Cross-references** updated throughout: rail (§4.5 Workflows→Jobs), file inventory (§8 — `workflow_output` dropped, discrepancy escalation keys off `package`), EDM/RDM entities (§9 — `irp_edm`/`irp_rdm`, `edm_id` NOT NULL, `irp_id` naming), templates (§11), notifications (§18 deferred, `notification_preference` dropped), search (§19), monitoring (§20), build plan (§21 Iterations 5–8), adversarial review (§22 A1/A2/A8/A14/A16), locked decisions & external deps (§23).
- **Practice-lead resolutions folded in:** `irp_analysis.rdm_id` nullable (set → broker-from-RDM, null → own); `irp_rdm.edm_id` and `irp_analysis.edm_id` NOT NULL (no RDM/analysis without an EDM); everything scoped to a submission; `submission.crm_id` added.
- Constitution cleanup (Articles 1/2/3/4/5) tracked separately.

### 2026-07-02 — Pre-Iteration 2 planning: customer seeding, submission, ignore rules, Package

Scope: preparation for Iteration 2 ("Domain, file inventory & search framework"). Full design discussion preserved in the originating conversation; summary below.

**Iteration structure**
- Iteration 1 and Iteration 2 previously listed near-identical "In" scope (§7/§8 both places, differing only in RLS vs. search framework). Resolved: Iteration 1 keeps domain-model/file-inventory/RLS; Iteration 2 retitled to "Domain, file inventory, search framework & packages" and now also carries the new scope below. No iterations after 2 were renumbered.

**New features**
- **§7.1a Customer seeding** — CSV-driven, upsert by `customer.short_code`. Never deletes a customer row missing from the CSV (denormalization blast radius makes delete-on-sync unsafe).
- **§7.2a Submission status** — replaces `authoring_status` with `status_code`: `ACTIVE` / `COMPLETED` / `CANCELLED` only, event-sourced. `COMPLETED → ACTIVE` reopening allowed; no system-enforced transition preconditions (analyst judgment). No delete — `CANCELLED` is the terminal/withdrawal state, since a submission can carry real Risk Modeler-side identity (EDMs/RDMs) by the time anyone would remove it.
- **§7.2b Submission name uniqueness** — `UNIQUE(program_id, name)`, DB-enforced.
- **§8.7 Ignore ruleset** — admin-authored, gitignore-style, three scope levels (global / customer / submission), cumulative cascade with `!negation` (not most-specific-wins replacement) — same semantics as nested `.gitignore` files.
- **§9.4 Package** — new entity pairing an EDM and an RDM (EDM required, RDM optional — RDM-only packages are invalid). No independent status — UI reads EDM/RDM status directly rather than aggregating. Actions: Cancel / Save / Save-and-Sync / Delete. IRP name-collision check reuses the existing `search_edms()`/`search_rdms()` pattern from artifact tagging. Save-and-Sync and Delete each produce **stubbed** `rwb_job` rows this iteration (60s heartbeat wait, no real IRP call) — real IRP calls land in Iteration 3. Sync order is EDM-before-RDM; on delete the dependency is one-way (an EDM-delete drives its RDM-delete first; deleting an RDM never cascades to its EDM).
- **§7.4 Submission detail package cards** — full-width card per package (chosen over a compact-grid or split-column alternative — see rationale below), showing upload progress/status (stubbed), local filename, portfolio summary (empty), analysis counts (empty), and IRP/RWB job counts. Card title → EDM/RDM detail (route reserved, not built). Job count → Jobs list, pre-filtered.
- **§20.4 Query-string-driven filtering** — cross-page "arrive with a filter pre-applied" (e.g. package card → filtered Jobs list) is implemented as ordinary query params on the shared list view, not session state or a separate component. Fixed starting vocabulary: `customer_id`, `submission_id`, `package_id`, `status`, `job_type`. Extends the same "URL is the source of truth" principle §4.3 already used for breadcrumbs.

**Removed**
- `submission.cycle` — did not correspond to how the team works broker submissions (no renewal-cycle concept at the Submission level). Only consumer was the Iteration 5 auto-naming example pattern; flagged there as an open item, not resolved in this pass.
- `submission.authoring_status` — replaced by `status_code` (above).

**Corrections made outside Iteration 1/2 scope (called out per-instance in the text, summarized here)**
- §7.3: fixed a pre-existing broken cross-reference (`§15.4` → `§20.4`; the old reference pointed to "DataBridge usage," unrelated to list ergonomics).
- §2.6 and §11.2: updated auto-naming language to remove `{{ cycle }}`; §11.2's example flagged as needing a new token set when Iteration 5 is actually planned.
- §9.1/§9.2: added `delete_pending` to `edm.status`/`rdm.status` vocabulary (needed for Package delete sequencing).
- §22: added **A21**, documenting an unresolved design question — Package job chaining crosses from RWB-job space into IRP-job space and back (an RWB stub job needs to eventually trigger a real IRP job; the poller noticing that IRP job's completion needs to trigger the *next* RWB job). Not designed in this pass; flagged for a dedicated follow-up discussion before Iteration 3 implements real Package sync/delete.

**Explicitly out of scope for this update:** the Workflow/Stage/Task layer (§12–14) — being redesigned separately by another workstream toward a simpler IRP-Jobs/RWB-Jobs-only model. No changes made there; existing text may reference workflows in ways that will need revisiting once that redesign lands, but that revisiting is not part of this change.

**Rationale for full-width package cards:** considered a compact-card-with-click-through and a split EDM/RDM-column layout as alternatives (favoring scannability across multiple packages per submission); the full-width-card option was chosen deliberately despite that tradeoff, since each package's EDM and RDM sides carry enough independent state (status, jobs, portfolio/analysis summaries) to warrant full layout room now rather than deferring depth to a not-yet-built EDM/RDM detail page.
