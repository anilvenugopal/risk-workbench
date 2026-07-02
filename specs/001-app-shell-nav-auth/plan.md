# Implementation Plan: Application Shell, Navigation & Authentication

**Branch**: `001-app-shell-nav-auth` | **Date**: 2026-07-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-app-shell-nav-auth/spec.md`

---

## Summary

Iteration 0 delivers the complete structural and security foundation: IDE-style application shell driven by a single nav manifest, two auth modes (password + OIDC via Entra), session management, admin user provisioning, and the Alembic `0001_initial.py` schema covering all auth tables. All seven rail destinations render; all protected routes are gated. The shell degrades gracefully without JavaScript. HTMX-aware session expiry (`HX-Redirect`) is implemented from day one.

---

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**:
- `fastapi` + `uvicorn[standard]` — web server
- `jinja2` — server-side templates
- `itsdangerous` — signed session cookies (session ID only)
- `bcrypt` — password hashing (cost factor 12)
- `msal` — OIDC / Microsoft Entra authorization-code + PKCE flow
- `sqlalchemy>=2.0` (Core only, no ORM) — DB engine/pool via `db/` package
- `pyodbc` + ODBC Driver 18 — SQL Server connectivity
- `alembic` — WORKBENCH schema migrations
- `python-multipart` — form parsing (login, change-password)
- `dramatiq[redis]` — worker queue (Dramatiq workers are out of scope for Iteration 0; broker configured but not used by auth)
- HTMX (CDN or vendored) — partial-page swaps, `hx-boost`, `HX-Redirect`
- Alpine.js (CDN or vendored) — minimal client slivers (dropdown, mobile collapse)

**Storage**:
- SQL Server 2022 (`rwb_workbench`) — all auth tables, session state, user management
- Redis — Dramatiq broker; not used by auth in Iteration 0

**Testing**:
- `pytest` + `httpx` (unit tests, SQLite in-process via `register_engine`)
- `pytest --run-sqlserver` (SQL Server integration; real driver, real migrations)

**Target Platform**: Linux server (WSL2 native dev mode: uvicorn + SQL Server container)

**Project Type**: Server-rendered web application (FastAPI + Jinja2 + HTMX)

**Performance Goals**:
- Login round-trip (password): < 500ms p95
- OIDC callback processing (after Entra redirect): < 200ms server-side
- Shell render (authenticated): < 300ms p95
- `GET /api/health`: < 2 seconds (SC-007)

**Constraints**:
- No SPA; every page has a real URL; JS-disabled fallback works (Article 8)
- Session cookie: HttpOnly, Secure, SameSite=Lax; contains session ID only (Article 13)
- No secrets in code or VCS (Article 13)
- Scope predicates use bound parameters only (Article 6)
- `db/` safe path for all auth DB access (Article 7)
- All SQL via `db/` package; no raw connection strings in application code (Article 7)

**Scale/Scope**: ~10 internal users in initial deployment; pool sizing `MSSQL_POOL_SIZE=10`, `MSSQL_POOL_MAX_OVERFLOW=20` for 30 concurrent users

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Article | Title | Status | Notes |
|---------|-------|--------|-------|
| 1 | Manifest-Driven Extensibility | ✅ | Nav manifest is the single source of truth for rail, sidebar, breadcrumbs, active state, and RBAC visibility (FR-029–FR-032). Adding a page = one manifest node + one handler + one template. |
| 2 | Manifest Canonical; DB Is Generated Projection | N/A | No workflow definition manifests in Iteration 0. Nav manifest is a Python code dict — not projected to DB. |
| 3 | Categoricals Are Kind Tables — Except External-Status Mirrors | ✅ | `role_kind` is a kind table. Auth uses no IRP status columns. Carve-out not triggered in Iteration 0. |
| 4 | Status Is Event-Sourced with Cached Current | N/A | No lifecycle status entities in Iteration 0. Session management uses direct writes (session is not a status machine). |
| 5 | Generic Stage Review (No HITL Stage Type) | N/A | No workflow stages in Iteration 0. |
| 6 | Customer Isolation on Parameterized Path Only | ✅ | `apply_scope()` guard implemented in `db/scope.py` (already exists). Auth routes do not scope against customer data. `user_customer_access` table is created but not used until Iteration 2. |
| 7 | One Data-Access Package, Two Paths (`/db`) | ✅ | All auth DB reads/writes go through `db.execute()` / `db.get_connection()`. No ORM models. No raw connection strings in route handlers. |
| 8 | Server-Rendered; No SPA | ✅ | FastAPI + Jinja2 + HTMX. Top-level nav uses `hx-boost`. Login page is standalone layout. Every page has a real URL. JS-disabled fallback required (US-6 scenario 5). |
| 9 | Styling Extends ITCSS via Tokens | ✅ | Shell uses design tokens (`--surface-rail`, `--surface-sidebar`, etc.). No hardcoded hex values. CSS appended into correct ITCSS layers. Login page uses same token set. |
| 10 | SQL Table Is the Queue; Single Worker by Default | N/A | No task queue in Iteration 0. Dramatiq broker configured; no actors registered yet. |
| 11 | IRP Polling and Result Work Behind Interface; Submission on Request Path Permitted | N/A | No IRP calls in Iteration 0. |
| 12 | Test-First, Three Connected Strategies | ✅ | Unit tests (SQLite): auth service, CSRF, session expiry logic, manifest traversal. SQL Server tests: migration idempotency, session lifecycle, bcrypt round-trip. IRP tier: N/A this iteration. |
| 13 | Authentication & Secrets | ✅ | OIDC (Entra) is primary; password is gated fallback (`AUTH_MODE`). Session cookie = session ID only. CSRF on all state-changing requests. Idle timeout handled for HTMX via `HX-Redirect`. No secrets in code or VCS. |

**Constitution Check: PASSED — no violations. No Complexity Tracking entries needed.**

---

## Project Structure

### Documentation (this feature)

```text
specs/001-app-shell-nav-auth/
├── plan.md              ← this file
├── research.md          ← Phase 0 output (below)
├── data-model.md        ← Phase 1 output (below)
├── quickstart.md        ← Phase 1 output (below)
├── contracts/           ← Phase 1 output (below)
│   ├── nav-manifest.md
│   ├── session-cookie.md
│   └── htmx-partial.md
└── tasks.md             ← Phase 2 output (/speckit-tasks — not yet created)
```

### Source Code

```text
app/
├── auth/
│   ├── __init__.py
│   ├── middleware.py        # session load + idle/absolute expiry; HX-Redirect guard
│   ├── csrf.py             # CSRF token generation + validation
│   ├── password.py         # bcrypt verify/hash helpers
│   ├── oidc.py             # MSAL auth-code + PKCE; state generation; callback exchange
│   └── provisioning.py     # JIT user create/lookup for OIDC; password account create
├── routers/
│   ├── auth.py             # GET /login, POST /auth/login, GET /auth/callback,
│   │                       # POST /auth/logout, GET/POST /auth/change-password
│   ├── admin.py            # GET/POST /admin (users list + new user + reset + force-logout)
│   ├── shell.py            # GET / (home), GET /submissions, ... (stub handlers)
│   └── health.py           # GET /api/health (full: workbench + exposure + loss + redis)
├── services/
│   └── auth_service.py     # password login, session create/validate/invalidate, user lookup
├── nav/
│   ├── __init__.py
│   └── manifest.py         # NODES + helper functions (rail_nodes, breadcrumb, children, …)
├── templates/
│   ├── base/
│   │   ├── shell.html      # IDE-style shell: rail + sidebar + main + topbar + statusbar
│   │   ├── login.html      # standalone login layout (no shell)
│   │   └── error.html      # 404 / 500; shell-embedded vs fragment
│   ├── auth/
│   │   ├── login_page.html       # login form + OIDC button; AUTH_MODE-conditional
│   │   ├── change_password.html  # forced change-password form
│   │   └── access_pending.html   # OIDC-provisioned user, no role yet
│   ├── admin/
│   │   ├── users.html            # user list + new user modal
│   │   └── user_detail.html      # reset password + role assign + force-logout
│   └── pages/
│       ├── home.html             # home (full-width, no sidebar)
│       ├── submissions.html      # stub
│       ├── workflows.html        # stub
│       ├── results.html          # stub
│       ├── templates.html        # stub
│       ├── irp.html              # stub
│       └── account.html          # stub
├── static/
│   ├── css/
│   │   ├── tokens.css            # design tokens layer
│   │   ├── shell.css             # rail + sidebar + main + topbar + statusbar
│   │   └── auth.css              # login / change-password / access-pending
│   └── icons/
│       ├── home.svg
│       ├── submissions.svg
│       ├── workflows.svg
│       ├── results.svg
│       ├── templates.svg
│       ├── moodys.svg
│       ├── administration.svg
│       └── user.svg
├── config.py                     # (updated: auth_mode, entra_* fields, computed properties)
└── main.py                       # (updated: lifespan startup, include routers, error handlers)

alembic/
└── versions/
    └── 0001_initial.py           # (amended: all WORKBENCH tables per data-model.md §1)

infra/
└── scripts/
    ├── reset_db.py               # drop + recreate all 3 app DBs; run Alembic; seed
    └── seed_db.py                # insert kind table rows + optional dev fixtures

tests/
├── unit/
│   ├── test_auth_service.py      # session lifecycle, bcrypt, must_change_password gate
│   ├── test_csrf.py              # token generation + validation
│   ├── test_nav_manifest.py      # rail_nodes, breadcrumb, children, role filtering
│   └── test_session_expiry.py    # idle timeout, absolute cap, HTMX-Redirect path
└── sqlserver/
    └── test_auth_migration.py    # migration idempotency, session insert/invalidate
```

---

## Complexity Tracking

*(No violations — no entries required.)*
