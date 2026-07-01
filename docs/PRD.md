# Risk Workbench — Product Requirements Document

**Status:** Draft for build · **Format:** Living document, kept in the repo  
**Intended builder:** Claude Code (agent-built, iteration-sequenced)  
**Source of domain truth:** `irp-workbench/` (IRP integration ground truth) + `irp-integration` library v0.2.1.dev23

---

## 0. How to use this PRD

This document is **feature-organized** (§4–§17). Each feature section is self-contained — purpose, data, behavior, and rules in one place. §18 is the **build plan** (iterations, sequencing, exit criteria). §19 is an **adversarial review**. §20 logs locked decisions and external dependencies.

Three **declarative sources of truth** (§2.1) are the spine — most "add a thing" tasks are a one-place edit to one of them. Everything else follows from them.

---

## 1. Product overview

### 1.1 What this is

An internal workbench for a reinsurance catastrophe-modeling team. It is an **integration hub over Moody's Risk Modeler (IRP)**, designed to make each step of the modeling workflow fast, keep everything in one place, and eliminate context-switching between applications.

It is **not a workflow engine** — the analyst is always in the driver's seat. Every significant step involves judgment. The value is execution speed and functional coverage, not automation.

It is also **not** a multi-tenant SaaS and **not** a governance platform.

### 1.2 Primary users

Reinsurance catastrophe analysts who:
- Receive broker submissions (EDM and/or RDM files)
- Import exposure data into IRP, validate and profile it, create sub-portfolios
- Configure and run cat model analyses (potentially 50–150+ combinations per worldwide contract)
- Review results (ELTs, EP curves, AAL), compare against broker results
- Export finalized loss sets to downstream repositories

Administrators manage users, customer access, and system configuration.

### 1.3 The three-phase workflow

Every submission follows three sequential phases. The workbench covers all three.

#### Phase A — Data Setup & Validation
1. **File ingestion** — select EDM/RDM files from local filesystem or network shares; import into IRP via API. Auto-apply naming conventions from submission context.
2. **Validation & profiling** — run SQL-based validation reports and exposure profiling against imported data via DataBridge. Check quality, completeness, consistency; summarize by portfolio, geography, line of business.
3. **Exposure modification** — create sub-portfolios, modify data elements, create peril-specific portfolios via DataBridge. Re-validate after changes.
4. **Load to Exposure Repository** — push pre-aggregated summary data to the on-prem Exposure Repository SQL Server.

#### Phase B — Analysis Execution
1. **Analysis configuration** — select model profiles, output profiles, event rate schemes, currency. For worldwide contracts: batch submission from predefined templates ("global suite", 50–150+ combinations).
2. **Job submission & tracking** — submit analysis jobs via IRP API. Auto-poll for status. Surface progress, completion, and failures.
3. **Notifications** — push notification (Teams, email, or desktop toast) on job completion or failure.

#### Phase C — Results Management
1. **Results review** — view analysis outputs (ELTs, EP curves, AAL, return periods). Compare own results against broker-supplied RDM results and prior-year benchmarks.
2. **Results grouping** — combine or break out results by geography or other dimensions (e.g., county → state rollups).
3. **Downstream upload** — push finalized loss sets to the Loss Repository SQL Server.

### 1.4 Core domain glossary

- **Customer → Program → Submission** — business hierarchy. A Program belongs to a Customer; a Submission belongs to a Program.
- **Submission** — one broker's package handled by an analyst. Anchors directories, artifacts, EDMs, RDMs, workflows, and jobs. Has an assigned analyst ("my submissions" view).
- **EDM (Exposure Data Module)** — an exposure database, typically a `.bak` file from a broker. First-class tracked entity in the workbench (name + IRP exposure ID). Imported into IRP, validated, and used as the basis for analysis.
- **RDM (Risk Data Model)** — a results database from the broker (their own prior analysis). First-class tracked entity. Imported into IRP; used for comparison against the analyst's own results.
- **Portfolio** — a named view within an EDM in IRP (all accounts, or a filtered subset). Analysis jobs run against portfolios, not EDMs directly. The workbench tracks `irp_exposure_id` + `irp_portfolio_id`.
- **Analysis template** — a saved configuration for one analysis job (model profile, output profile, event rate scheme, treaty names, currency). Used for batch submission. Can be organized into **template suites** (e.g., "Global suite" = all worldwide region/peril combinations).
- **Workflow** — a staged modeling pipeline under a submission. Stages are fixed-order, mode-typed, and manifest-declared. Currently one type: **EDM analysis**.
- **Stage** — one ordered phase of a workflow (EDM Upload, Data Validation & Profiling, Exposure Modification, Portfolio Creation, Geo-coding & Hazard, Analysis, Grouping, Export). Has a mode and an execution status.
- **Task** — the executable unit inside a stage. Consumes typed inputs, produces typed handles.
- **Handle** — a named, typed output produced by a task (EDM name, RDM name, portfolio name, analysis name, group name). The unit of reference chaining. Handles carry **names**, not typed IDs — IRP resolves names to internal IDs at submit time.
- **Job** — an IRP async operation tracked in the Workbench Metamodel DB. Has a `job_type` (which IRP endpoint to poll) and a `result_work_item` written on completion.
- **Result work item** — a row written to a SQL queue table by the poller when a job reaches a terminal status. Picked up by a Dramatiq worker that performs post-completion actions (retrieve results from IRP, write to Loss Repository, notify analyst).
- **IRP job type** — discriminator on every `irp_job` row; determines which IRP polling endpoint to call: `workflow`, `risk_data_job`, `analysis_job`, `grouping_job`, `export_job`.
- **DLM / HD** — two Moody's model families (Detailed Loss Module / High-Definition). Not file-level attributes — determined by the selected analysis profile's `softwareVersionCode`. Cannot be mixed within a group.
- **Exposure Repository** — on-prem SQL Server that holds pre-aggregated exposure summary data (output of Phase A). Separate connection from the Workbench Metamodel DB.
- **Loss Repository** — on-prem SQL Server that holds finalized loss sets / analysis results (output of Phase C). Separate connection from both other databases.
- **Workbench Metamodel DB** — the app's own SQL Server (app state, job inventory, audit). One of three distinct database connections.
- **DataBridge** — Moody's cloud SQL Server, accessed via `client.databridge` (ODBC). Used for validation reports, exposure profiling, and exposure modification. Cannot serve analysis results.

---

## 2. Architecture principles

### 2.1 The three declarative sources of truth

Everything that "changes when requirements change" lives in **versioned code manifests**, so engine code stays fixed:

1. **Navigation manifest** (§4.2) — the rail/sidebar/breadcrumb/search tree.
2. **Workflow-definition manifest** (§9.2) — stages, modes, skippability, task templates, ports.
3. **Type/port registry** (§10.1) — handle types, compatibility, propagation rules.

Graph **invariants** (DLM/HD homogeneity, name uniqueness, IRP reference validity) are **registered named validators** (§10.6) — isolated, independently-testable functions behind a registry, run by a generic pass.

**Versioning rule:** each manifest/registry carries a `version`. A workflow instance **pins** the definition + registry version it was authored under, so later manifest edits never rewrite the meaning of in-flight or historical workflows.

**Manifest-vs-DB rule:** where a manifest is *projected* into DB tables for FK/reporting, the **manifest is canonical and the projection is generated, never hand-edited**, guarded by a fail-fast startup **consistency check** (manifest content-hash vs. stored hash) and a **version-retention** rule (projection is append-only; old versions retained while any instance pins them). Full treatment in §9.1a.

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
- **IRP job submission** — synchronous on the request path. The IRP submit call returns a job ID immediately; the round-trip is fast enough that there is no benefit to deferring it. On failure the job is marked `submission_failed` and a retry actor picks it up.
- **Poller** — standalone loop process (`app/poller/run.py`). One process, one pass per interval: bulk-queries all non-terminal `irp_job` rows, polls IRP per `job_type`, updates `mirrored_status`, writes `result_work_item` rows on terminal status. Not Dramatiq — batching by design; a per-message queue would break the natural grouping.
- **Dramatiq workers** — consume result work items and handle submission retries. Redis broker. Each result worker class owns one post-completion action. A separate `submission_retry` actor re-attempts failed IRP submissions up to a configurable limit.

### 2.4 Styling discipline

Extend the ITCSS system via tokens, never override it. New UI is layered into the existing ITCSS structure (settings → tools → generic → elements → objects → components → utilities). Every color, surface, and spacing value comes from a **CSS custom property** in the settings layer — never a hardcoded hex inline. Rule of thumb: if a new screen needs a color the system doesn't have, add a token, don't write the hex into the component.

### 2.5 Maintainability contract

These tasks must each be a bounded, one-place change:
- **Add a page** → one nav-manifest node + one handler + one template. Rail, sidebar, breadcrumb, active-state, RBAC, search visibility are inherited.
- **Add a searchable object type** → register one search provider.
- **Add a chaining type** → add registry rows + declare ports on task templates.
- **Add a workflow constraint** → write one registered validator + register it.
- **Change a stage's mode / skippability** → one manifest edit.
- **Change a workflow definition** → edit the code manifest + re-run the projection generator. Never hand-edit projected tables.

### 2.6 Auto-naming

Auto-naming is a first-class feature, not a convenience. For EDM imports, analysis jobs, and group names the workbench generates names from submission context (customer short-code, program, cycle, region/peril tag from the template). An analyst submitting a worldwide contract should never have to type 50+ analysis names. The naming scheme is configurable per template suite.

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
- **Center** — background activity: "3 jobs running · 1 result worker pending" (wired when execution lands, §18)
- **Right** — last-action result ("EDM-123 imported") + HTMX request spinner (`htmx-indicator`)

### 4.5 Rail destinations (indicative)

| Rail item | Sidebar children |
|---|---|
| Home (dashboard) | — |
| Submissions | List, My Submissions |
| Workflows | Active, Review Queue, IRP Jobs, Exceptions |
| Templates | Analysis Templates, Template Suites |
| Results | Results, Loss Repository |
| Moody's IRP | Sync Metadata, EDM Library, RDM Library |
| Administration | Users, Customer Access, Settings |

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

The `CurrentUser(id, email, role)` dataclass is identical across all modes. All downstream code — RLS, audit, role gates, analyst filters — is auth-mode-agnostic.

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

OIDC authorization-code flow, backend-for-frontend pattern: the OIDC exchange is server-side; the browser never sees tokens. Entra authenticates **identity only** — `oid` claim maps to a local `app_user` record. **Authorization (roles, customer access) is always owned by the app**, never read from token claims or Entra groups.

On first sign-in from a new Entra identity: a `app_user` row is provisioned automatically (`entra_oid` set, `password_hash` null). Roles and customer access must be assigned by an admin before the user can do anything useful (fail-closed: no access by default).

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

12. **Auto-provision on first login** — on callback, if no `app_user` row exists with `entra_oid = oid_claim`: insert a new row with `email`, `display_name`, `entra_oid`, `is_active=true`, `password_hash=null`. Log the auto-provision. Do **not** assign roles or customer access automatically — require admin action before the user can access anything.

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

Hard rule, applies to both auth modes: **identity** (who you are) comes from the auth provider (password check or Entra); **authorization** (what you can see and do) comes from the app's own tables (`user_role`, `user_customer_access`), evaluated live on every request. The two are never conflated. Authorization is never read from token claims, never cached in the session cookie, and never derived from Entra group membership.

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

## 6. Feature: Authorization & row-level security

### 6.1 Roles

Global roles (not per-customer) in v1. Exact codes TBD with the team; at minimum: `analyst`, `admin`. `admin` bypasses customer scoping. Roles gate manifest nodes and actions, checked server-side via `require_role(*allowed_roles)` dependency.

### 6.2 Customer-access scoping (app-level RLS)

Users are scoped to customers via `user_customer_access(user_id, customer_id)`. Every list/detail query calls `apply_scope()` which injects `WHERE customer_id IN (allowed set)`. `customer_id` is denormalized onto every major entity (set once at creation from the parent chain, never user-editable) so scoping is a single-column predicate — no multi-join.

`apply_scope()` honors an **admin bypass** (admins see all). Native SQL Server RLS is a later hardening layer, not v1.

### 6.3 Analyst-centric views

"My submissions" is a first-class filter: `WHERE assigned_analyst_id = current_user.id`. Every submission list defaults to this view with a toggle to "All team submissions." This reflects the real workflow: analysts each own a submission end-to-end during peak season.

### 6.4 Admin maintenance

Admin rail destination maintains users, roles, and `user_customer_access`. Building this early makes RLS testable end-to-end immediately.

---

## 7. Feature: Domain model — Customer → Program → Submission

### 7.1 Business hierarchy

**Customer → Program → Submission**. A Submission anchors all work: directories, file artifacts, EDMs, RDMs, workflows, jobs, and results. Every major entity carries `customer_id` (denormalized, immutable) for RLS.

### 7.2 Submission

The analyst's unit of work. Fields: `id`, `name`, `customer_id` (denorm), `program_id`, `assigned_analyst_id`, `authoring_status`, `created_at`.

A submission has:
- Zero or more **directories** (shared-drive paths) → file inventory
- Zero or more **file artifacts** (tagged EDM/RDM files found in directories, or uploads)
- Zero or more **EDM records** (IRP exposure databases, tracked separately from the artifact file)
- Zero or more **RDM records** (IRP results databases from broker, tracked separately)
- Zero or more **workflows** (analysis pipelines)

### 7.3 Submission UI

Master-detail pattern: filterable list ("My Submissions" default, "All" toggle, customer/program filter) + detail panel. List ergonomics per §15.4. Status badges surface active job counts and review queue depth per submission.

---

## 8. Feature: File inventory & artifacts

### 8.1 Directory association

`submission_directory` links a submission to one or more shared-drive paths, `UNIQUE(unc_path)`. Stores both the Windows UNC path (human-facing) and the Linux mount path (for reading). The shared drive is mounted **read-only** into the Linux host (CIFS/SMB, least-privilege service account). The app only reads — never writes, moves, or deletes broker files.

### 8.2 Immutable artifact model

A `file_artifact` row = one **version** of a file. Identity: cheap metadata signature `(relative_path, size_bytes, fs_modified_at)` — no content hash (hashing a 1.4 GB MDF is too expensive; identity is best-effort). **Append-only**: a detected change retains the old row and inserts a new one.

Sources: `shared_drive` | `upload` | `workflow_output`. All three share one model, one store; `source` is the discriminator.

### 8.3 Reconciliation scanner & triggers

Background job (not a request). Triggers: directory added/removed, workflow task attempts to use a file, user opens the submission page, explicit "Refresh inventory" button. Per file: not tracked → insert `present`; unchanged → no-op; changed → mark old `changed`, insert new `present`; gone → mark `missing`. A **settle window** (N seconds) prevents fingerprinting files mid-copy.

### 8.4 Tagging

Users tag artifacts as `edm` or `rdm` on the submission detail page. Tagged artifacts are selectable as workflow inputs.

### 8.5 Discrepancies

Raised when a tracked file changes or goes missing. Severity escalates if the artifact was tagged, and further if it had been referenced/pinned by a workflow (provenance in question). Surfaced: count in status bar, marker on submission, dedicated list.

### 8.6 Upload storage

Upload artifacts use `source=upload`, stored under a server-managed upload location (not the read-only broker mount). Uploads are immutable by nature.

---

## 9. Feature: EDM & RDM entity management

### 9.1 EDM as a first-class entity

An **EDM record** (`edm` table) is distinct from the file artifact that produced it. The file artifact is the `.bak` file on disk (tracked by §8). The EDM record represents the exposure database **as it exists in IRP**, with:
- `name` — the EDM name in IRP (auto-generated from submission context per §2.6, editable)
- `irp_exposure_id` — IRP's integer exposureId (backfilled on import completion)
- `submission_id`, `customer_id` (denorm)
- `status` — `pending_import`, `importing`, `ready`, `error`, `deleted`
- `source_artifact_id` FK — the file artifact used for import (nullable for EDMs created fresh in IRP)
- `server_name` — the DataBridge server the EDM lives on
- Soft-delete via `deleted_at`

**EDM operations:**
- **Create fresh in IRP** — `client.edm.submit_create_edm_job(edm_name, server_name)` → `workflow` job type
- **Import from .bak** — `client.edm.submit_edm_import_job(edm_name, file_path, server_name)` → `risk_data_job` (uploads to S3 first, handled inside library)
- **Upgrade data version** — `client.edm.submit_upgrade_edm_data_version_job(edm_name, edm_version)` → `workflow`
- **Delete** — `client.edm.delete_edm(edm_name)` → `workflow`

All async operations create an `irp_job` row and are polled by the cron poller.

### 9.2 RDM as a first-class entity

An **RDM record** (`rdm` table) tracks a broker-supplied results database in IRP:
- `name` — the RDM name in IRP
- `irp_id` — IRP's integer id (backfilled on import completion)
- `submission_id`, `customer_id` (denorm)
- `status` — `pending_import`, `importing`, `ready`, `error`
- `source_artifact_id` FK — the .bak file used for import
- Soft-delete via `deleted_at`

**RDM operations:**
- **Import from .bak** — `client.rdm.submit_rdm_import_job(rdm_name, edm_name, rdm_file_path)` → `risk_data_job` (uploads to S3 first)
- **Query via DataBridge** — once imported, the RDM is accessible via DataBridge for comparison queries (reading broker results tables directly)
- **Export to Loss Repository** — push broker results to the Loss Repository for side-by-side comparison with own analysis (see §17)

### 9.3 EDM library & RDM library

Rail destinations under "Moody's IRP" that show all EDMs / RDMs across submissions (customer-scoped). Entry points for:
- Importing new EDM/RDM files
- Viewing import job status
- Linking an already-in-IRP EDM to a submission (without re-import)
- Triggering DataBridge validation and profiling (§10)

---

## 10. Feature: Phase A — Data validation & exposure modification

### 10.1 Purpose

Before any analysis runs, the analyst validates the quality of the imported EDM, creates sub-portfolios as needed, and loads summary data to the Exposure Repository. This is Phase A, and it runs entirely through DataBridge (`client.databridge`).

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

### 10.3 Exposure modification via DataBridge

The analyst can run exposure modification operations (also via DataBridge):
- Create sub-portfolios (filter on portfolio criteria)
- Modify data elements (e.g., construction class mapping, currency normalization)
- Create peril-specific portfolios (e.g., earthquake-only portfolio from a combined EDM)

These run as DataBridge SQL commands via `client.databridge.execute_command(query, params, ...)`. Results are logged in the audit log. After modification, the analyst re-runs validation.

### 10.4 Load to Exposure Repository

After validation passes, the analyst pushes pre-aggregated exposure summaries to the **Exposure Repository** (separate SQL Server connection `EXPOSURE_REPO_URL`). The workbench writes to a known schema in the Exposure Repository. This is a Dramatiq worker action: the poller triggers it via a result work item when the analyst explicitly requests it from the Phase A UI.

---

## 11. Feature: Analysis templates & template suites

### 11.1 The batch problem

A worldwide reinsurance contract may require 50–150+ individual model/region/peril/treaty combinations, each historically configured manually. This is the #1 analyst pain point. The workbench solves it with **analysis templates** and **template suites**.

### 11.2 Analysis template

A saved configuration for a single analysis job. Stored in the Metamodel DB as `analysis_template`:
- `name` — template name
- `customer_id` (scope), `created_by`
- `analysis_profile_name` — IRP model profile name
- `output_profile_name`
- `event_rate_scheme_name` (nullable — required for DLM, not for HD)
- `treaty_name_pattern` — optional pattern for auto-selecting treaties from an EDM
- `tag_names` — list of IRP tags to apply
- `currency_code`
- `region_label`, `peril_code` — metadata for display and grouping
- `auto_name_pattern` — Jinja-style template string for auto-generating the analysis job name, e.g., `{{ customer_code }}-{{ cycle }}-{{ region }}-{{ peril }}`
- `franchise_deductible` (bool), `min_loss_threshold`, `num_max_loss_event`

### 11.3 Template suite

A named collection of templates for batch submission. Stored as `template_suite` + `template_suite_item` (ordered):
- `name` — suite name (e.g., "Global 2026 Q1")
- `customer_id` (scope)
- Items link to `analysis_template` rows with an optional per-item `portfolio_name_override`

**Applying a suite** to a submission generates one analysis task per template item, all wired into the Analysis stage of the workflow. Names are auto-generated from each template's `auto_name_pattern` + submission context. The analyst reviews, adjusts if needed, then submits.

### 11.4 DLM vs HD detection

At batch-apply time, the workbench checks each template's `analysis_profile_name` against the locally cached `irp_model_profile.software_version_code` (`"HD" in code → HD, else DLM`). For DLM templates, `event_rate_scheme_name` is required; for HD, it is optional. The homogeneity validator (§12.6) catches any DLM+HD mixing in a Grouping stage.

---

## 12. Feature: Workflow model

### 12.1 Definition vs instance

Two clean layers:
- **Definition** (code manifest §12.2): Workflow type → ordered Stages (mode + skippable + task templates with typed ports). Manifest is canonical; DB tables are a generated projection.
- **Instance** (runtime, Metamodel DB): workflow-instance → stage-instances → task-instances, each with status, counts, and resolved I/O.

An instance **pins** the definition + registry version it was authored under (§2.1).

### 12.1a Manifest is canonical; DB is a generated projection

The code manifest is the single source of truth. Projected tables (`workflow_definition`, `definition_stage`, `task_template`, `port_template`) are generated *from* the manifest and **never hand-edited**. Three rules:
- **Never hand-edit projected tables.** Edit the manifest, re-run the projection generator.
- **Fail-fast startup consistency check.** Content-hash of live manifest vs. stored hash. App refuses to start on mismatch.
- **Append-only / version-retained.** A new manifest version inserts new rows; prior versions are never deleted while any instance pins them.

### 12.2 EDM analysis workflow — stages

The single workflow type. Stages in fixed order (skippable but never reorderable):

| # | Stage | Mode | Skippable | Notes |
|---|---|---|---|---|
| 1 | EDM Upload | singleton | yes | Skip if EDM already in IRP. Submits `workflow` or `risk_data_job` to IRP. `irp_exposure_id` backfilled via `backfill_edm` result worker. |
| 2 | Data Validation & Profiling | sequential | yes | DataBridge validation + profiling queries (§10.2). No IRP job. Task executed synchronously on request path; service marks task `succeeded`/`failed` inline after the DataBridge call returns. |
| 3 | Exposure Modification | sequential | yes | DataBridge modification queries (§10.3). No IRP job. Same synchronous completion pattern as Stage 2. |
| 4 | Portfolio Creation | sequential | no | `client.portfolio.create_portfolio()` returns `(portfolio_id, _)` synchronously (201 + Location header). Service writes `irp_portfolio.irp_portfolio_id` inline on the same request. No poller involvement — the ID is known before the response. |
| 5 | Geo-coding & Hazard | parallel | yes | `client.portfolio.submit_geohaz_job()` per portfolio → `workflow` job type |
| 6 | Analysis | parallel | no | `client.analysis.submit_portfolio_analysis_jobs(list)` → `List[int]` (ordered). Task instances ordered by `template_suite_item.position`; job IDs mapped positionally. `resource_uri` per job stored on `irp_job.resource_uri` immediately. |
| 7 | Grouping | sequential | yes | `client.analysis.submit_analysis_grouping_job()` → `grouping_job`. Analyses referenced by name+EDM |
| 8 | Export | parallel | yes | `analysis_export_job` (Parquet) or `rdm_export` (`risk_data_job`). Result worker then pushes to Loss Repository |

All stages support `auto_complete` toggle (default false → parks in `review` when work completes).

### 12.3 Stage review & status model

Per-stage execution status: `not_started → blocked → running → review → complete / canceled`

- `auto_complete=false` (default) → stage goes to `review` when tasks finish; human must complete it
- `auto_complete=true` → stage goes directly to `complete`
- **`ERROR` is a dynamic rollup** (any task failed) — overlays any status; is never a gate
- **`blocked`** is a gate from a validation failure; carries a severity + message for the review panel
- **Complete** advances the workflow. **Cancel** halts the entire workflow (`execution_status → canceled`). No retry/rerun — escape hatch is cancel-and-create-new.

Review queue: home dashboard card + Workflows sidebar item count only `review` + `blocked` stages.

### 12.4 Stage execution modes

- **Singleton** — exactly one task
- **Parallel** — sibling tasks, no intra-stage ordering; all dispatchable at once
- **Sequential** — ordered tasks; may chain to earlier tasks in the same stage

Skipping a stage marks its tasks `skipped` and passes handles through; blocked when downstream references the stage's handles.

### 12.5 Workflow states

`draft → validated → runnable` (then execution states):
- **draft** — being authored; only compose-time checks apply
- **validated** — passed whole-graph validation pass including IRP reference-data checks; gates the `validated` transition
- **runnable** — validated and ready to execute

---

## 13. Feature: Type registry, reference chaining & validation

### 13.1 Handle-type registry

Each producible/consumable type is a row: `code`, `label`, optional `parent_code` (single-parent inheritance for compatibility). Seeded: `edm`, `rdm`, `analysis`, `group`. Nothing in code treats these as special; new chaining needs add rows, not code branches.

**`dlm` and `hd` are NOT handle types.** DLM vs HD is an analysis-profile property (`"HD" in softwareVersionCode`), not a file-level attribute. The homogeneity validator checks cached model profile names, not handle types.

### 13.2 Typed ports & input sources

Every task input resolves to one of three sources:
1. **Inventory item** → pins an immutable `artifact_id` (tagged EDM/RDM file)
2. **Upstream handle** → references a specific upstream `task_instance` output port (prior stage freely; prior task only within a sequential stage)
3. **Literal / reference-table row / parameter** → user value or pinned reference version

A consumer port declares the type set it accepts; the UI offers matching handles as a dropdown.

### 13.3 Type propagation

An output port's emitted type is either **literal** (`analysis` emits `analysis`) or **derived** (`group` emits "same as my inputs' type"). Derived types propagate DLM/HD lineage through group-of-groups. Known structurally at authoring time.

### 13.4 Two-phase validation

- **Compose-time (instant, per-edge):** type compatibility + structural rule. Runs as the user wires.
- **Save-time / validate (whole-graph, may call IRP cache):** graph invariants, gates `draft → validated`.

### 13.5 Structural rule (written once, generic)

An edge P → C is legal iff: C's accepted-type set is compatible with P's emitted type (registry lookup, incl. parent inheritance) AND one of: (a) P is in an earlier stage, or (b) same stage, sequential, P precedes C. Same-stage edges in parallel stages and any backward edge are rejected. Cycle check always runs. This function never grows.

### 13.6 Graph invariants (registered named validators)

- **Homogeneity** — all inputs to a Grouping task must share the same model family. DLM vs HD determined from cached `irp_model_profile.software_version_code`. No live IRP call at validation time.
- **Uniqueness** — no duplicate analysis names within an EDM; no duplicate group names.
- **External validity** — reference-data lookups against local IRP cache: named model profile, output profile, event rate scheme, server, treaties all exist. No trial job submitted.

New constraint = one registered validator function + register it.

### 13.7 Reference chaining vs data lineage

- **Reference chaining** (user-wired, validated) — handles flow downstream: EDM name → Portfolio Creation → Analysis → Grouping. Analysis names reusable in Grouping; group names reusable in subsequent Grouping tasks (sequential stage allows this). Group-of-groups: a group task consumes `{analysis, group}` and emits `group`.
- **Data lineage** (implicit) — produced outputs are artifacts in the §8 model, giving an end-to-end provenance graph. Not user-wired, not validated.

**Handle re-run semantics:** when an upstream task is re-run, downstream tasks that pinned its prior output are marked **stale (needs review)** — never silently re-pointed.

---

## 14. Feature: Execution engine, job tracking & result processing

### 14.1 Task as job (SQL table is the queue)

A task-instance is a **job row** in the Metamodel DB; that table is the queue — no separate queue technology (no Celery/Redis for job submission). The SQL table enforces readiness gates, which IRP cannot (only we know when a task's pinned inputs have resolved).

**Default: single worker, plain dequeue.** IRP already queues and executes; our worker's role is submit-then-hand-off. So the default is one worker process doing `SELECT TOP (1) WHERE status='ready' ORDER BY priority, id` then `UPDATE SET status='running'`. No locking hints needed.

**Documented upgrade:** swap for `SELECT TOP (n) WITH (READPAST, UPDLOCK, ROWLOCK) OUTPUT` for concurrent workers. One-statement change, no schema change.

**Reclaim-stuck sweep** always runs: periodically resets rows stuck in `running` past a timeout back to `ready`.

### 14.2 Readiness gate

Per-task: `blocked → ready → running → succeeded | failed | skipped`. `blocked→ready` computed from whether all bound inputs have resolved (artifacts present + unchanged). The claim query gates on `ready`.

### 14.3 IRP job submission

**Submission is synchronous on the request path.** When an analyst triggers an IRP operation the service calls the IRP API directly, records the returned job ID, and responds immediately. This is the right model because:
- IRP submit calls return quickly (they enqueue work server-side and return a job ID — no waiting)
- The analyst gets immediate confirmation or an error in the same HTTP response
- No benefit to deferring through a queue for a sub-second operation

**On submission failure:** the `irp_job` row is written with `mirrored_status = 'submission_failed'` and `submission_attempt_count` incremented. The `submission_retry` Dramatiq actor picks these up and re-attempts up to `IRP_SUBMISSION_MAX_RETRIES` (default 3) times with backoff. After max retries the job stays `submission_failed` and surfaces as an error on the task.

Each IRP-backed task submits to one of five job types. The `irp_job` row records `job_type` (for poll routing), `external_ref` (IRP's returned integer job id), and `submission_attempt_count`:

| Stage | IRP call | `job_type` |
|---|---|---|
| EDM Create (fresh) | `client.edm.submit_create_edm_job(edm_name, server_name)` | `workflow` |
| EDM .bak Import | `client.edm.submit_edm_import_job(edm_name, file_path, server_name)` | `risk_data_job` |
| EDM Upgrade | `client.edm.submit_upgrade_edm_data_version_job(edm_name, edm_version)` | `workflow` |
| EDM Delete | `client.edm.submit_delete_edm_job(exposure_id)` | `workflow` |
| RDM Import | `client.rdm.submit_rdm_import_job(rdm_name, edm_name, rdm_file_path)` | `risk_data_job` |
| Geo-coding & Hazard | `client.portfolio.submit_geohaz_job(portfolio_name, edm_name, ...)` | `workflow` |
| Analysis (single) | `client.analysis.submit_portfolio_analysis_job(edm_name, portfolio_name, job_name, ...)` → `(job_id, request_body)` | `analysis_job` |
| Analysis (batch) | `client.analysis.submit_portfolio_analysis_jobs(list)` → `List[int]` (ordered job IDs) | `analysis_job` per item |
| Grouping | `client.analysis.submit_analysis_grouping_job(group_name, analysis_names, ...)` | `grouping_job` |
| File Export (Parquet) | `client.analysis.submit_analysis_export_job(analysis_id, loss_details)` | `export_job` |
| RDM Export | `client.rdm.export_analyses_to_rdm(server_name, rdm_name, analysis_names)` | `risk_data_job` |

> **DLM vs HD at submit time:** `event_rate_scheme_name` required for DLM; optional for HD. Detected internally by irp-integration from the model profile's `softwareVersionCode`. The app passes the value (or omits it) based on the cached profile.

> **`exposure_resource_id` must be captured at submission time.** `submit_portfolio_analysis_job()` returns `(job_id, request_body)` where `request_body["resourceUri"]` is the portfolio's IRP resource URI — this IS the `exposure_resource_id` needed later for `get_elt()`, `get_ep()`, etc. Store it in `irp_job.resource_uri` on the `irp_job` row immediately after submission. The analysis result completion response does NOT include this value — if it is not stored at submission time it cannot be recovered without a separate IRP search call.

> **Batch analysis — ordered positional mapping.** `submit_portfolio_analysis_jobs(list)` returns `List[int]` (one job ID per submitted item, same order). When applying a template suite, task instances are ordered by `template_suite_item.position` (ascending). The batch request list is built in the same order. Job IDs are matched back to `task_instance` rows by position: `job_ids[i]` → task at position `i`. The `(stage_instance_id, order_in_stage)` UNIQUE constraint enforces unambiguous ordering. This mapping is written atomically in a transaction immediately after the batch submit call returns.

> **API method signatures** in the table above are from `irp-integration` v0.2.1.dev23. This is a pre-release library. Verify all signatures against the installed version before implementing any IRP-backed stage. Parameter names and return shapes are the most likely points of drift.

### 14.4 The poller

Standalone loop process (`app/poller/run.py`). **Not Dramatiq** — the poller is a batch operation by design. One process, one pass per interval: it queries all non-terminal jobs in a single SELECT, groups them by `job_type`, polls IRP for each, and writes results. A per-message Dramatiq queue would break this natural batching and add unnecessary Redis round-trips.

**Running:** `--loop --interval 30` for dev; cron or a supervised systemd service in production (e.g. `ExecStart=python -m app.poller.run --loop --interval 60`).

**Each pass:**
1. **Query non-terminal jobs** from `WORKBENCH` DB: `WHERE mirrored_status NOT IN ('FINISHED', 'FAILED', 'CANCELLED') AND mirrored_status != 'submission_failed'`
2. **Poll each job** via irp-integration using the **single-status-check** method per `job_type`. The poller **must never call `poll_*_to_completion` methods** — those are blocking loops with 600 000-second timeouts and will freeze the poller process. Use only the single-GET methods:

| `job_type` | Single-status-check method (poller uses this) |
|---|---|
| `workflow` | `client.client.get_workflow(workflow_id)` → `{"status": ..., "progress": ...}` |
| `risk_data_job` | `client.risk_data_job.get_risk_data_job(job_id)` |
| `analysis_job` | `client.analysis.get_analysis_job(job_id)` |
| `grouping_job` | `client.analysis.get_analysis_grouping_job(job_id)` |
| `export_job` | `client.export_job.get_export_job(job_id)` |

> **`poll_*_to_completion` is FORBIDDEN in the poller.** These methods block for up to 600 000 seconds. The `get_*` single-check methods are the right primitives for a batch poller. The `poll_*` blocking variants exist in the library for interactive scripts, not for a production poller loop.

3. **Update `irp_job.mirrored_status`** in `WORKBENCH` DB via `db.execute_command`.
4. **On terminal status:** write one or more `result_work_item` rows (one per `work_type` needed). Mark the `task_instance` as `succeeded` or `failed` (`status == 'FINISHED'` is the only success; `FAILED` and `CANCELLED` are failures).
5. **Update stage/workflow rollups** (task counts, error overlay, status propagation).

**JobStatus vocabulary** (stored as plain string — not a DB enum, so future IRP statuses never crash the poller):
- Non-terminal: `QUEUED`, `PENDING`, `RUNNING`, `CANCEL_REQUESTED`, `CANCELLING`
- Terminal: `FINISHED`, `FAILED`, `CANCELLED`
- App-local: `submission_failed` (never sent to IRP; poller skips these rows)

### 14.5 Result work items & Dramatiq workers

**Two categories of Dramatiq actors:**

**A — Result workers** (triggered by the poller on terminal IRP job status):

The poller writes one `result_work_item` row per `work_type` needed for each completed job. Dramatiq workers run in parallel — multiple work types for the same job can process concurrently.

| `work_type` | Worker responsibility |
|---|---|
| `retrieve_analysis_results` | Call `client.analysis.get_elt/ep/stats/plt()` per perspective code; write Parquet files + `analysis_result_meta` row to `WORKBENCH` DB |
| `push_results_to_loss_repo` | Read Parquet result files; write to `LOSS` DB via `get_connection("LOSS")` |
| `push_rdm_to_loss_repo` | Query broker RDM via DataBridge; write to `LOSS` DB |
| `push_exposure_summary` | Run DataBridge exposure summary queries; write to `EXPOSURE` DB via `get_connection("EXPOSURE")` |
| `notify_analyst` | Post Teams webhook and/or send email on job completion or failure |
| `download_export_file` | Download Parquet export from IRP via `client.export_job.download_export_results()`; write to submission output dir |

**Work item chaining — ordering without a depends_on column.** The poller only writes **head** work items for each completed job (the first in each chain). Each worker, on success, enqueues the next item in the chain as a new `result_work_item` row. This gives ordering without a dependency join at dequeue time:

```
Poller writes on FINISHED:
  retrieve_analysis_results (head)
  notify_analyst (head — independent, runs in parallel)

retrieve_analysis_results worker, on success, writes:
  push_results_to_loss_repo (tail)
```

This means `push_results_to_loss_repo` never races with `retrieve_analysis_results` — it does not exist until `retrieve_analysis_results` succeeds. If `retrieve_analysis_results` fails after Dramatiq retries, the chain stops there; `push_results_to_loss_repo` is never enqueued.

**B — Submission retry actor** (triggered when `irp_job.mirrored_status = 'submission_failed'`):

A separate `submission_retry` Dramatiq actor — not using the `result_work_item` table (different trigger, different lifecycle). It claims by atomically updating `irp_job`: `UPDATE irp_job SET retry_locked_until = DATEADD(minute, 15, GETUTCDATE()), submission_attempt_count = submission_attempt_count + 1 WHERE id = :id AND retry_locked_until < GETUTCDATE() AND submission_attempt_count < :max_retries`. Only one actor wins; the losers skip. It re-attempts the IRP API call and updates `mirrored_status` + `external_ref` on success or leaves `submission_failed` on exhaustion. After `IRP_SUBMISSION_MAX_RETRIES` (default 3) the job surfaces as a permanent failure on the task.

**Worker behavior contract (both categories):**
- Worker sets its status to `running` when it starts (work item row for result workers; `irp_job.mirrored_status` for retry actor)
- On success: sets status to `succeeded`, writes `completed_at`
- On failure: sets status to `failed`, writes `error_detail`; Dramatiq handles retry with exponential backoff
- All workers are idempotent — safe to re-run on Dramatiq retry
- A staleness sweep resets `running` result work items past a timeout back to `pending`

**Dramatiq broker:** Redis. In local dev, Redis runs as a Docker Compose service. Workers start with: `dramatiq app.workers`.

### 14.6 Stage / workflow rollups

Task statuses are leaves. A stage's `exec_status` is event-sourced; its Task/Completed counts and `error` overlay (any task `failed`) roll up from tasks. Workflow current stage = earliest not-`complete` stage; `canceled` stage forces workflow to `canceled`.

### 14.7 Live monitoring

SSE (`sse-starlette`) streams job status updates to the UI as the poller updates `mirrored_status`. The workflow-detail stage list and status-bar activity zone subscribe. nginx must have `proxy_buffering off` on SSE routes. HTMX polling used as fallback for stage-level counts.

---

## 15. Feature: Moody's IRP integration

### 15.1 IRPClient instantiation

`from irp_integration import IRPClient; client = IRPClient()` — reads all config from env vars. No constructor args. Lazy-init with double-checked locking in `get_irp_client()` dependency. Raises HTTP 503 if client cannot connect at first use.

**Auth modes** (auto-selected from env):
- API key: `RISK_MODELER_API_KEY` set → sent in Authorization header
- Bearer login: `RISK_MODELER_TENANT_NAME` + `RISK_MODELER_USERNAME` + `RISK_MODELER_PASSWORD` → client logs in at construction; reactive 401 re-login

Always required: `RISK_MODELER_BASE_URL`, `RISK_MODELER_RESOURCE_GROUP_ID`

### 15.2 IRP metadata sync

"Sync IRP Metadata" rail action fetches and caches IRP reference data into local `irp_*_cache` tables in the Metamodel DB. Feeds workflow authoring dropdowns and the `draft→validated` validation checks.

What is synced:
- `client.reference_data.get_model_profiles()` → `irp_model_profile` (includes `software_version_code` for DLM/HD detection)
- `client.reference_data.get_output_profiles()` → `irp_output_profile`
- `client.reference_data.get_event_rate_schemes()` → `irp_event_rate_scheme`
- `client.reference_data.get_all_simulation_sets()` → `irp_simulation_set`
- `client.reference_data.get_tags()` → `irp_tag`
- `client.reference_data.search_currencies()` → `irp_currency`
- `client.edm.search_database_servers()` → `irp_database_server`
- `client.edm.search_edms()` → `irp_edm_cache` (EDMs already in IRP)

### 15.3 Analysis results retrieval

Analysis results are **REST-only** — never DataBridge. Retrieved per `perspectiveCode` (`GR` = Gross, `GU` = Ground-Up, `RL` = Reinsurance Layer):

- `client.analysis.get_elt(analysis_id, perspective_code, exposure_resource_id)` → ELT records
- `client.analysis.get_ep(analysis_id, perspective_code, exposure_resource_id)` → EP curves (OEP/AEP/CEP/TCE)
- `client.analysis.get_stats(analysis_id, perspective_code, exposure_resource_id)` → AAL/statistics
- `client.analysis.get_plt(analysis_id, perspective_code, exposure_resource_id)` → PLT (**HD only**)

These are called by the `retrieve_analysis_results` Dramatiq worker (§14.5), not on a request path.

`exposure_resource_id` is the portfolio's IRP resource URI. It is **not** returned by the job completion response — it comes from `submit_portfolio_analysis_job()`'s return value (`request_body["resourceUri"]`) and must be stored in `irp_job.resource_uri` at submission time. The `retrieve_analysis_results` worker reads it from there.

### 15.4 DataBridge usage

`client.databridge.execute_query(query, params, connection, database)` → `pd.DataFrame`

Used for: validation reports (§10.2), exposure profiling, exposure modification (§10.3), broker RDM queries (§17.2). Connection: `MSSQL_{NAME}_SERVER/USER/PASSWORD` env vars read by irp-integration.

DataBridge **cannot serve analysis results** — REST only for results.

### 15.5 Portfolio tracking

The `irp_portfolio` table tracks portfolios created during a workflow:
- `irp_exposure_id` — IRP's integer exposureId for the EDM
- `irp_portfolio_id` — IRP's integer portfolioId
- `edm_name`, `portfolio_name`
- `task_instance_id` FK — the Portfolio Creation task that created it

**`create_portfolio()` returns the portfolio ID synchronously** — the IRP endpoint responds with HTTP 201 + a Location header; the library parses this and returns `(portfolio_id, request_body)` before the call returns. The service writes `irp_portfolio.irp_portfolio_id` on the same request path. The poller is not involved in portfolio ID backfill.

Analysis job submission requires both `edm_name` and `portfolio_name` — IRP resolves these to IDs internally.

### 15.6 IRP constraints

- **Built-in retry** inside irp-integration: 5 attempts, exponential backoff for 429/5xx. Do not add another retry layer.
- **Rate limits / concurrency caps:** honored at the poller level; do not submit faster than IRP allows.
- **IRP availability:** hard runtime dependency for IRP-backed stage execution and the `validated` transition's live fallback calls. Workflow authoring stays in `draft` without IRP.
- **Terminal ≠ success:** always inspect `status == 'FINISHED'` before treating a terminal job as successful.

---

## 16. Feature: Results management & repositories

### 16.1 Analysis results in Metamodel DB

When the `retrieve_analysis_results` worker completes, results are stored in the Metamodel DB:
- `analysis_result` — one row per (analysis, perspective_code): AAL, EP curve points, ELT record count
- `elt_record` — individual ELT loss events (may be large; paginated retrieval)
- `ep_curve` — EP curve data points (OEP/AEP/CEP/TCE)
- `plt_record` — PLT records (HD only)

These tables are the source for the results review UI.

### 16.2 Results review UI

Rail: Results. Shows analysis outputs per workflow/submission:
- ELT summary (AAL, max event loss, record count)
- EP curves (plot or table, by perspective)
- AAL by perspective
- PLT (HD only)
- Comparison panel: own analysis results vs. broker RDM results side by side

### 16.3 Loss Repository

The Loss Repository (`LOSS_REPO_URL`) is the downstream destination for finalized loss sets. It is a **separately-connected SQL Server** with a known schema that downstream reporting systems read.

The `push_results_to_loss_repo` Dramatiq worker writes to it after analysis results are retrieved. Schema for the Loss Repository is defined separately and versioned independently. The workbench app has write-only access to designated tables.

### 16.4 Results grouping

After analysis, the analyst may group results by dimension (geography, line of business, etc.) using IRP's grouping API (`submit_analysis_grouping_job`). This is the Grouping stage of the workflow. Groups can themselves be grouped (group-of-groups, supported by irp-integration). Results of grouped analyses are retrieved the same way as individual analyses.

### 16.5 Exposure Repository

The Exposure Repository (`EXPOSURE_REPO_URL`) receives pre-aggregated exposure summaries from Phase A. The `push_exposure_summary` Dramatiq worker writes structured exposure data (total insured value by portfolio/geography/LOB) to the Exposure Repository after the analyst explicitly triggers it from the Data Validation & Profiling stage UI.

---

## 17. Feature: Broker RDM comparison

### 17.1 Purpose

Analysts must compare their own analysis results against the broker's results (provided as an RDM file) and against prior-year benchmarks. The workbench surfaces this comparison directly rather than requiring export and manual Excel work.

### 17.2 Broker RDM import & query

After importing a broker RDM into IRP (§9.2), the analyst can query it via DataBridge (broker results ARE available via DataBridge — unlike own analysis results). Standard queries extract:
- ELT records from the RDM's loss tables
- AAL and EP curve data (from aggregated results tables in the RDM)

These are stored in a `rdm_result` table in the Metamodel DB, tagged by `rdm_id`.

### 17.3 Comparison view

The results review UI shows a comparison panel:
- **Own results** (from `analysis_result`) vs. **broker results** (from `rdm_result`)
- Metrics: AAL, 100-year/250-year/500-year OEP, by perspective code
- Visual: side-by-side table or overlay chart

### 17.4 Push to Loss Repository

Broker results can also be pushed to the Loss Repository (`push_rdm_to_loss_repo` worker) to make them available alongside own results for downstream reporting.

---

## 18. Feature: Notifications

### 18.1 Async job completion notifications

Triggered by the `notify_analyst` Dramatiq worker when a job reaches terminal status. Configured per-submission or per-user preference.

**Channels:**
- **Teams webhook** — post a card to a configured channel with job name, status, and a deep link to the results
- **Email** — SMTP, sent to `assigned_analyst.email`
- **In-app** — a notification item in the status bar (polled via SSE)

**Content:** job name, submission name, final status (FINISHED/FAILED/CANCELLED), timestamp, deep link to the workflow stage.

### 18.2 Configuration

`notification_preference` table per user: `channel` (teams/email/in_app), `enabled`, `on_success`, `on_failure`. Teams webhook URL configured in application settings (per submission or global).

---

## 19. Feature: Global search

**Ctrl/Cmd-J** opens a modal (Alpine.js: open/close, keyboard nav, focus trap). Search-as-you-type via HTMX (`hx-trigger="keyup changed delay:200ms"`). A **provider registry** fans out across result groups:

- **Navigation** — reads the nav manifest; new nav items are searchable automatically
- **Submissions** — name, customer, program
- **EDMs** — EDM name, submission
- **RDMs** — RDM name, submission
- **Workflows** — name, submission, status
- **Templates** — analysis template name, profile name
- **Results** — analysis job name

Adding a searchable type = register one provider. **All providers apply `apply_scope()`** — results are customer-scoped and cannot leak. Start with SQL `LIKE`; move to Full-Text indexes if volume demands.

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

### 20.5 Master-detail layout

List + detail panel recurs (Submissions, Workflows, Results). Built once as a reusable layout.

### 20.6 Feature flags / config

Centralized. First flags: `APP_ENV`, `ENFORCE_SSO`. More will accrue.

### 20.7 Health check

`GET /api/health` → `{status, db_workbench, db_exposure, db_loss, redis, env}`. Checks connectivity to all three DB connections (`get_connection("WORKBENCH")`, `get_connection("EXPOSURE")`, `get_connection("LOSS")`) and Redis. Returns 200 regardless; callers check individual fields.

---

## 21. Build plan

Each iteration ends runnable and demonstrable. Sequencing: infrastructure first; IRP operations before results; repositories last.

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

**Moved in from Iteration 1:** §5.1 (password login, bcrypt, forced password change, `must_change_password` flow), §5.2/§5.3 (OIDC/BFF, PKCE, MSAL, JIT provisioning for PremiumIQ), §5.5 (schema: `user_session`, `login_attempt`, `password_hash`/`must_change_password` on `app_user`), §6.1 (roles, `apply_scope` WORKBENCH guard), §6.4 (admin: Users, password reset, force-logout). **Deferred from Iteration 1:** rate limiting lockout (§5.1.3 — `login_attempt` table created and logged but lockout gate not implemented), customer access admin (§6.4 — deferred to Iteration 2 when submission data exists to scope against).

### Iteration 1 — Domain, file inventory & RLS

**In:** §7 (Customer/Program/Submission, assigned analyst, master-detail, list ergonomics), §8 (directory association, immutable artifacts, scanner, tagging, discrepancies, upload storage), §6.2 (customer-access scoping: `user_customer_access`, `apply_scope()` on Submission list, admin bypass), §6.3 (analyst-centric "my submissions" filter), §6.4 (customer access admin UI — assign/revoke customer access per user).

**Out:** EDM/RDM entities, search, workflow references.

**Exit:** browse customer-scoped submissions with "my" filter; associate a directory, scan, tag EDM/RDM, detect a discrepancy; admin assigns customer access to a user and the scope takes effect immediately on next request.

### Iteration 2 — Domain, file inventory & search framework

**In:** §7 (Customer/Program/Submission, assigned analyst, master-detail, list ergonomics), §8 (directory association, immutable artifacts, scanner, tagging, discrepancies, upload storage), §19 search framework + navigation + submission providers.

**Out:** EDM/RDM entities, workflow references.

**Exit:** browse scoped submissions with "my" filter; associate a directory, scan, tag EDM/RDM, detect a discrepancy; Ctrl/Cmd-J finds nav and submissions.

### Iteration 3 — EDM & RDM entity management

**In:** §9 (EDM entity, RDM entity, EDM/RDM library rail destinations), §14.3 IRP submit for EDM create/import/upgrade/delete and RDM import, §14.4 poller (basic: poll workflow + risk_data_job types), §14.5 Dramatiq worker scaffold + `notify_analyst` worker, §18 notifications.

**Out:** analysis, grouping, results, repositories.

**Exit:** import an EDM from a .bak file; poller mirrors job status; analyst receives a Teams/email notification on completion; EDM shows `ready` status.

### Iteration 4 — Phase A: Validation, profiling & Exposure Repository

**In:** §10 (DataBridge validation queries, profiling, exposure modification), §16.5 Exposure Repository write via `push_exposure_summary` worker.

**Out:** workflow authoring, analysis.

**Exit:** run a validation query set against an imported EDM; view profiling results; push exposure summary to Exposure Repository; re-validate after modification.

### Iteration 5 — Analysis templates & template suites

**In:** §11 (analysis template entity, template suite, batch application to workflow, auto-naming), IRP metadata sync (§15.2), `irp_*_cache` tables seeded.

**Out:** workflow authoring, analysis execution.

**Exit:** create a template suite ("Global 2026 Q1"); apply it to a submission and see 50+ auto-named analysis configs generated; IRP metadata sync populates profile/server dropdowns.

### Iteration 6 — Workflow authoring, type registry & validation

**In:** §12 (definition manifest, instance, stage/mode model, draft→validated→runnable), §13 (type registry, typed ports, propagation, two-phase validation, structural rule, registered invariants), §12.2 workflow stages for all 8 stage types.

**Out:** actual IRP execution (next iteration).

**Exit:** author a workflow; wire reference chaining; compose-time rejection of illegal edges; save-time validate pass (with mocked IRP cache checks); DLM+HD mixing caught; duplicate names caught.

### Iteration 7 — Analysis execution, grouping & results

**In:** §14 (full execution engine: readiness gate, claim loop, reclaim-stuck sweep, all poller job types, result work item queue, all Dramatiq worker types), §15.3 analysis results retrieval, §16.1 results in Metamodel DB, §16.2 results review UI, §16.3 Loss Repository write, §17 broker RDM comparison, §14.7 SSE monitoring.

**Out:** export file download (Iteration 8).

**Exit:** run a full workflow end-to-end (EDM upload → portfolio → geocode → analysis → grouping → results); results appear in review UI; Loss Repository populated; broker RDM comparison side-by-side.

### Iteration 8 — Export, search completion & polish

**In:** §14.3 file export job (`export_job` + `download_export_file` worker), §12.2 Export stage, §19 all remaining search providers (EDM, RDM, workflow, template, results), §18 notification preferences UI.

**Out:** —

**Exit:** export analysis results to Parquet; all search providers working; notification preferences configurable per user.

---

## 22. Adversarial review

- **A1 — Stale handles on re-run.** Re-running an upstream task silently corrupts a downstream task's input. Resolution: downstream pins a specific produced-output version; a re-run marks dependents **stale (needs review)**, never auto-repoints (§13.7).
- **A2 — Skipping a stage with referenced handles.** Grouping references an Analysis handle; Analysis is skipped → unsatisfiable. Resolution: skipping is **blocked** when downstream references the stage's handles, with a clear reason (§12.4).
- **A3 — Discrepancy latency.** A changed file may go undetected between triggers. Resolution: "workflow task attempts to use a file" is always a trigger, so the execution-critical path always re-scans (§8.3). Accepted elsewhere.
- **A4 — Cookie/session vs. live access changes.** Admin changes customer access; active session doesn't reflect it. Resolution: the session holds identity only; roles + scope are read **live from DB on every request** (§5.4). Changes are immediate.
- **A5 — Dev stub can't be killed mid-session.** Resolution: explicitly accepted for local development only. `AUTH_MODE=dev` is gated on `APP_ENV != production` server-side. Audit, loud banner (§5.0).
- **A5a — Password auth is weaker than SSO.** Accepted for v1 MVP. Mitigated by: bcrypt cost factor 12, rate limiting (5 attempts / 15 min per email; 20 / 15 min per IP), `HttpOnly Secure SameSite=Lax` cookie, server-side sessions in WORKBENCH DB, CSRF tokens on all state-changing requests, forced password change on first login, admin-only password reset. Upgrade path to Entra SSO (§5.3) requires no downstream code changes.
- **A6 — Three-DB split makes local dev painful.** One SQL Server Docker container hosts all three databases (`rwb_workbench`, `rwb_exposure`, `rwb_loss`). Three connection strings, one server, three database names. Schema isolation is enforced by database name, not separate servers. No extra infra cost locally. All application processes (app, nginx, Redis, poller, workers) run natively on Linux — no Docker overhead for anything except SQL Server.
- **A7 — Dramatiq worker failure leaves result work item stuck.** Resolution: Dramatiq's built-in retry with backoff. Worker sets `status='running'` before work, `status='failed'` on unrecoverable failure. A sweep job resets `running` items past a staleness threshold.
- **A8 — IRP outage blocks everything.** Resolution: authoring stays in `draft` without IRP; only the `validated` transition and IRP-backed stage execution require it (§15.6). Poller catches up when IRP comes back.
- **A9 — Search leaks across customers.** Resolution: every provider applies `apply_scope()` (§19).
- **A10 — Admin can't see all customers under RLS.** Resolution: `apply_scope()` honors admin bypass (§6.2).
- **A11 — Upload vs. shared-drive store split.** Resolution: one `file_artifact` model, `source` discriminator (§8.2, §8.6).
- **A12 — Detail pages have no manifest node.** Resolution: detail routes declare a home node; breadcrumb walks up from it + appends entity label (§4.2, §4.3).
- **A13 — Nested directory paths across submissions.** `UNIQUE(unc_path)` allows `/a` and `/a/b` on different submissions. Accepted v1 limitation.
- **A14 — Authoring validation vs. execution readiness conflated.** Resolution: explicitly separated — §13 (authoring, graph rules) vs §14.2 (execution, input-resolved gate).
- **A15 — Dramatiq/Redis adds ops complexity.** Accepted; the alternative (polling a SQL queue from the app process) is simpler but does not support per-job-type parallelism or fan-out without entangling the web process. Redis + Dramatiq is the standard pattern for this scale. Redis is stateless; losing it loses in-flight work items but not results already written. Poller re-triggers work items if the status is still `pending` after a staleness window.
- **A16 — Over-generalizing the rule engine.** Hard line: flat registry + single-parent inheritance only; invariants are registered code validators, not a DSL (§13.1, §13.6).
- **A17 — Icon assets.** Dependency logged (§23). Not a code blocker.
- **A18 — `customer_id` denormalization drift.** Set once at creation from the parent chain, never user-editable. Immutable (§2.1).
- **A19 — Loss Repository schema ownership.** The workbench has write-only access to specific tables. Schema is defined and versioned separately (not by Alembic). Breaking schema changes in the Loss Repository require coordination. Mitigated by: write through a thin adapter layer in the Dramatiq worker; the adapter is the single point to update on Loss Repository schema changes.
- **A20 — Analyst submits 150 analysis jobs; IRP rate-limits.** Resolution: irp-integration has built-in retry (5 attempts, exponential backoff). The batch-submit method handles the loop. Do not add another retry layer. The poller polls at an interval; no thundering-herd problem.

---

## 23. Assumptions, decisions & external dependencies

### Locked decisions

- **Three declarative sources of truth** as code manifests, versioned, instance-pinned (§2.1).
- **Three separate database connections** — named `WORKBENCH`, `EXPOSURE`, `LOSS` — resolved via the `db/` package (`MSSQL_{NAME}_*` env vars). One SQL Server Docker container in dev with three databases (`rwb_workbench`, `rwb_exposure`, `rwb_loss`); separate servers in prod (§2.2).
- **Dev environment is Linux-native.** Only SQL Server runs in Docker. App (uvicorn), nginx, Redis, poller, and Dramatiq workers all run as native Linux processes. No Docker Compose wrapping the application stack.
- **Dev DB strategy: drop-create-seed.** Until production cutover, the WORKBENCH schema is managed via a single Alembic revision that drops all tables, recreates them, and seeds kind tables. No migration version accumulation in dev. EXPOSURE and LOSS bootstrapped via idempotent SQL scripts (`python -m app.cli bootstrap-exposure` / `bootstrap-loss`).
- **Connection pooling handled by `db/` package** — `get_engine()` / `get_connection()` cache one pooled engine per named connection. Pool sizing via `MSSQL_POOL_SIZE` / `MSSQL_POOL_MAX_OVERFLOW` (set to 10/20 for 30 concurrent users).
- **Sync-by-default:** plain `def` handlers, FastAPI threadpool; `async def` only for SSE (§2.3).
- **IRP job submission is synchronous on the request path.** Fast IRP submit call returns a job ID immediately. On failure: `submission_failed` status + Dramatiq `submission_retry` actor (§14.3).
- **Poller is a standalone loop process — not Dramatiq.** Batch-queries all non-terminal jobs per pass. Dramatiq would break the natural batching (§14.4).
- **Dramatiq workers for result processing and submission retry only** (§14.5). Redis broker.
- **EDM and RDM are first-class entities** in the Metamodel DB, not just file artifact tags (§9).
- **`file_artifact.name`** initialized as UPPERCASE filename without extension; user-editable; IRP name-check on tag or rename.
- **Analysis templates and template suites** are first-class domain entities; auto-naming from submission context is built-in (§11).
- **v1 auth: username + bcrypt password** (`AUTH_MODE=password`). bcrypt cost 12, rate limiting, server-side sessions in WORKBENCH DB (`user_session` table), CSRF tokens, forced password change on first login, admin-only reset. No Redis dependency for auth. Upgrade to Entra SSO (`AUTH_MODE=oidc`) requires no downstream changes (§5.1, §5.2, §5.3).
- **Session store is WORKBENCH DB** (`user_session` table), not Redis. Sessions survive Redis restarts; active sessions are queryable; admin force-logout is a single UPDATE (§5.1.4).
- **Signed-cookie / server-side session** — cookie holds only the session ID (random 32-byte hex); all identity and role context lives in DB (§5.1.4).
- **App-level RLS** via `apply_scope` + `user_customer_access`; global roles; native SQL Server RLS as later hardening (§6.2).
- **Immutable artifact model**, cheap metadata signature (path+size+mtime), no content hash (§8.2).
- **Workflow definition: manifest canonical, DB is generated projection** — never hand-edited; fail-fast content-hash check + append-only version retention (§12.1a).
- **`dlm`/`hd` are NOT handle types** — analysis-profile property detected from `softwareVersionCode` (§13.1).
- **Analysis results hybrid storage** — Parquet files on disk for row-level data (ELT, EP, PLT); SQL metadata row for summaries and file paths (§16.1).
- **Top-level navigation uses `hx-boost`**, composing with `hx-push-url` (§4.3).
- **Styling extends the ITCSS design system via tokens** — never hardcoded hex (§2.4).

### Open decisions (need team input; do not block early iterations)

- Concrete `role_kind` codes (analyst, admin, viewer?)
- Teams webhook URL: per-submission or global config?
- Notification preferences: per-user opt-in or always-on?
- Loss Repository schema (coordinates with downstream consumers)
- Exposure Repository schema (coordinates with reporting team)
- Idle-timeout durations (sliding + absolute)
- `reference_table` / `parameter` scope — global vs. customer-scoped
- Export format beyond Parquet (CSV? Excel?)

### External dependencies

- **Moody's IRP** — `irp-integration` library (`IRPClient`). Five async job types. Auth via env vars. Rate limits apply.
- **DataBridge** — Moody's cloud SQL Server. ODBC via irp-integration. Used for Phase A validation/profiling/modification and broker RDM queries.
- **Redis** — Dramatiq broker. Required for result workers and notifications.
- **Shared-drive mount** — read-only CIFS/SMB, least-privilege service account (§8.1).
- **Loss Repository** — on-prem SQL Server; app writes via `get_connection("LOSS")`; schema defined in this project (separate from Alembic, coordinated with downstream consumers).
- **Exposure Repository** — on-prem SQL Server; app writes via `get_connection("EXPOSURE")`; schema defined in this project (coordinated with reporting team).
- **Teams webhook URL** — for notifications.
- **Icon SVG source set** committed to `static/icons/`.
- **SQL Server Express** on WSL2 / Docker Desktop for local dev.
