# Tasks: Application Shell, Navigation & Authentication

**Input**: Design documents from `specs/001-app-shell-nav-auth/`

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Data model**: [data-model.md](data-model.md)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no blocking dependencies)
- **[US#]**: Which user story this task belongs to
- Exact file paths are included in every task description

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project skeleton, static assets, and CSS tokens that every subsequent phase depends on.

- [x] T001 Amend `alembic/versions/0001_initial.py` — create all WORKBENCH tables: `app_user`, `user_session`, `login_attempt`, `role_kind`, `user_role`, `user_customer_access`, `customer`, `program`; seed `role_kind` rows (`analyst`, `admin`) per `data-model.md`. Note: `customer` and `program` are created as empty shells — FK constraints referencing `submission`-side tables are deferred until those tables exist in a later iteration.
- [x] T002 [P] Create `infra/scripts/reset_db.py` — drop and recreate `rwb_workbench`, `rwb_exposure`, `rwb_loss` (never DATABRIDGE), invoke Alembic `upgrade head`
- [x] T003 [P] Create `infra/scripts/seed_db.py` — insert `role_kind` seeds and optional dev fixture user (bcrypt hash at cost 12, `must_change_password=False`, role=`analyst`)
- [x] T004 [P] Create `app/static/css/tokens.css` — design token layer (`--surface-rail`, `--surface-sidebar`, `--color-danger`, etc.) following ITCSS structure; no hardcoded hex values
- [x] T005 [P] Create `app/static/css/shell.css` — IDE-style five-zone layout (rail, sidebar, main, topbar, statusbar) using tokens only
- [x] T006 [P] Create `app/static/css/auth.css` — login page, change-password, and access-pending layouts using tokens only
- [x] T007 [P] Add placeholder SVG icons to `app/static/icons/` — one file per rail destination: `home.svg`, `submissions.svg`, `workflows.svg`, `results.svg`, `templates.svg`, `moodys.svg`, `administration.svg`, `user.svg`

**Checkpoint**: `make wsl-db-rebuild` succeeds twice (idempotent); `GET /api/health` reports `db_workbench: ok`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before any user story begins — session middleware, CSRF, auth helpers, nav manifest, and the FastAPI app wiring.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T008 Create `app/nav/manifest.py` — `NODES` list (all Iteration 0 rail + sidebar + detail nodes per `contracts/nav-manifest.md`); implement `rail_nodes()`, `children()`, `breadcrumb()`, `top_ancestor()`, `default_child_key()`, `searchable_nodes()`, `visible_nodes(user_roles)` helpers
- [x] T009 [P] Create `app/auth/csrf.py` — double-submit cookie CSRF token generation (`generate_csrf_token()`) and validation (`validate_csrf_token()`) using `itsdangerous.URLSafeTimedSerializer`; set cookie name `rwb_csrf`
- [x] T010 [P] Create `app/auth/password.py` — `hash_password(plain: str) -> str` (bcrypt cost 12), `verify_password(plain: str, hashed: str | None) -> bool` (returns `False` immediately when `hashed` is `None`), `validate_password_requirements(plain: str) -> list[str]` (12+ chars, upper, lower, digit)
- [x] T011 Create `app/auth/middleware.py` — `SessionMiddleware` that: reads `rwb_session` cookie, queries `user_session` with idle + absolute + invalidated checks via `db.execute()`, returns `HX-Redirect: /auth/login` (HTTP 200) for HTMX requests or `302` for standard requests on expiry, updates `last_active_at` on valid session, attaches `CurrentUser` to `request.state` (FR-012, FR-013, FR-014, FR-015)
- [x] T012 [P] Create `app/services/auth_service.py` — `create_session(user_id, ip, user_agent) -> str` (insert `user_session` row, return 64-char hex ID), `invalidate_session(session_id)` (set `invalidated_at`), `get_user_by_email(email) -> dict | None`, `get_user_by_oid(oid) -> dict | None`, `log_attempt(email, auth_mode, success, reason, ip, user_agent)`
- [x] T013 Create `app/templates/base/shell.html` — IDE-style shell Jinja2 template: rail (from `visible_nodes()`), sidebar (from `children()`), topbar (breadcrumb + search placeholder + Help link), main content block, statusbar (env badge + display name + role); uses `hx-boost` on nav links. Note: HTMX source — use CDN in dev (`<script src="https://unpkg.com/htmx.org@..."></script>`) or vendor the file to `app/static/js/htmx.min.js` for offline dev; decide before T013 and record the choice in a comment in `shell.html`.
- [x] T014 [P] Create `app/templates/base/error.html` — 404/500 error template that renders inside the shell for full-page requests; fragment-safe partial for HTMX requests (detect via template variable passed from handler)
- [x] T015 Update `app/main.py` — add lifespan startup (verify WORKBENCH connectivity via health check query), register `SessionMiddleware`, include all routers from `app/routers/`, add 404 and 500 exception handlers that respect HTMX vs full-page context

**Checkpoint**: App starts (`make wsl-app`), `GET /api/health` is fully green, and navigating to `/` unauthenticated redirects to `/auth/login`.

---

## Phase 3: User Story 1 — Password Login & Sign-out (Priority: P1) 🎯 MVP

**Goal**: An analyst with a provisioned password account can log in, use the shell, and sign out. Session is invalidated on sign-out; Back button does not restore it.

**Independent Test**: Seed a user via `infra/scripts/seed_db.py`. Submit login form. Verify `user_session` row + cookie set. Sign out. Verify `invalidated_at` set and cookie cleared. Press Back → redirected to `/auth/login`.

### Implementation

- [x] T016 [US1] Create `app/templates/auth/login_page.html` — login page layout (no shell); renders password form, OIDC button, or both based on `password_auth_enabled` / `oidc_auth_enabled` Jinja context vars; includes CSRF hidden field; no inactive UI for disabled modes (FR-001, FR-003)
- [x] T017 [US1] Create `app/routers/auth.py` — implement `GET /auth/login` (renders login page; redirect to `/` if already authenticated) and `POST /auth/login` (CSRF check → case-insensitive email lookup → `null` hash guard → bcrypt verify → `log_attempt()` → on success: `create_session()` + set `rwb_session` cookie + redirect to `next` param or `/`; on failure: re-render with generic error) (FR-002, FR-004, FR-005, FR-006, FR-007). Note: `next` param open-redirect guard (T053) must be implemented here alongside consumption — validate that `next` is a relative path (starts with `/`, no `//`, no scheme) before redirecting.
- [x] T018 [US1] Implement `POST /auth/logout` in `app/routers/auth.py` — CSRF check → `invalidate_session()` → clear `rwb_session` cookie → for password sessions: `RedirectResponse("/auth/login")`; for OIDC sessions: redirect to Entra logout endpoint with `post_logout_redirect_uri` (FR-016)
- [x] T019 [US1] Create `app/routers/shell.py` — stub handlers for all 7 rail destinations (`GET /`, `GET /submissions`, `GET /workflows`, `GET /results`, `GET /templates`, `GET /irp`, `GET /account`); each requires `CurrentUser` from `request.state`; returns appropriate stub template
- [x] T020 [US1] Create stub page templates in `app/templates/pages/` — `home.html` (full-width, no sidebar), `submissions.html`, `workflows.html`, `results.html`, `templates.html`, `irp.html`, `account.html` (each renders inside shell with correct breadcrumb context)
- [x] T021 [P] [US1] Write unit tests in `tests/unit/test_auth_service.py` — cover: `verify_password` returns `False` on null hash, `verify_password` correct/incorrect, `validate_password_requirements` edge cases, session creation returns 64-char hex, `invalidate_session` sets timestamp

**Checkpoint**: Password login → shell renders with all 7 rail icons → sign out → Back button rejected. All US1 unit tests pass (`make wsl-test`).

---

## Phase 4: User Story 2 — OIDC Login via Microsoft (Priority: P1)

**Goal**: A PremiumIQ analyst clicks "Sign in with Microsoft", authenticates with Entra, and lands in the app. Account is created on first sign-in.

**Independent Test**: With `AUTH_MODE=oidc`, click "Sign in with Microsoft". Browser redirects to `login.microsoftonline.com`. Authenticate. Callback creates `app_user` with `entra_oid` set, `password_hash=null`. Session cookie set. User lands in shell.

### Implementation

- [x] T022 [US2] Create `app/auth/oidc.py` — `build_auth_url() -> tuple[str, str, str]` (returns auth URL, state, code_verifier using `msal.ConfidentialClientApplication`), `exchange_code(code, state, code_verifier) -> dict` (server-side token exchange; extract `oid` + `email` from ID token claims; raise if `email` absent — FR-011), `build_logout_url() -> str` (Entra end-session endpoint with `post_logout_redirect_uri`)
- [x] T023 [US2] Implement `GET /auth/oidc-login` in `app/routers/auth.py` — call `build_auth_url()`, store `state` + `code_verifier` in signed `rwb_oidc_state` cookie (5-min TTL, `itsdangerous`), redirect to Entra authorization URL (FR-008)
- [x] T024 [US2] Implement `GET /auth/callback` in `app/routers/auth.py` — validate `state` from `rwb_oidc_state` cookie (mismatch → delete cookie + redirect `/auth/login?error=state_mismatch`, FR-009) → call `exchange_code()` → `get_user_by_oid()` → if found: update `last_login_at` + `create_session()` + redirect to home; if not found: insert `app_user` (FR-023) + redirect to `/auth/access-pending`; delete `rwb_oidc_state` cookie in all paths (FR-010, FR-025)
- [x] T025 [US2] Create `app/templates/auth/access_pending.html` — standalone page (no shell) explaining the user's OIDC account was created and an admin must assign a role; no shell navigation accessible (FR-024)
- [x] T026 [P] [US2] Create `app/auth/provisioning.py` — `jit_provision_oidc_user(oid, email, display_name) -> str` (insert `app_user`; return new `id`); called from callback handler

**Checkpoint**: With live Entra credentials, OIDC flow completes end-to-end. New accounts land on access-pending. Returning accounts land in shell. `state` mismatch aborts cleanly.

---

## Phase 5: User Story 3 — Admin Provisions Password Account (Priority: P1)

**Goal**: Admin creates a password account for John Doe. John is forced to change the temporary password on first login before accessing anything.

**Independent Test**: Admin → Administration → Users → New User → submit. Verify `app_user` row with `must_change_password=1`. Log in as John → redirected to `/auth/change-password`. Try `/submissions` → back to change-password. Submit new password → home.

### Implementation

- [x] T027 [US3] Create `app/routers/admin.py` — `GET /admin/users` (list all `app_user` rows; requires `is_admin=True` on `CurrentUser`), `GET /admin/users/new` (render new-user form), `POST /admin/users/new` (CSRF check → validate name/email/password → `hash_password()` → insert `app_user` with `must_change_password=True` → redirect to user list) (FR-018)
- [x] T028 [US3] Add admin routes to `app/routers/admin.py` — `POST /admin/users/{id}/reset-password` (CSRF check → `hash_password(new_temp)` → update `password_hash` + set `must_change_password=True` → redirect to user detail, FR-022), `POST /admin/users/{id}/assign-role` (CSRF check → insert `user_role` row → redirect), `POST /admin/users/{id}/force-logout` (CSRF check → `invalidate_session()` for all active sessions of user → redirect, FR-017)
- [x] T029 [US3] Create `app/templates/admin/users.html` — user list table (name, email, role, last login, active flag) + "New User" button; renders inside shell under Administration section
- [x] T030 [US3] Create `app/templates/admin/user_detail.html` — user detail: display info + Reset Password form + Assign Role form + Force Logout button; all forms have CSRF fields
- [x] T031 [US3] Implement `GET /auth/change-password` and `POST /auth/change-password` in `app/routers/auth.py` — `GET`: require authenticated session with `must_change_password=True`; `POST`: CSRF check → `validate_password_requirements()` → `hash_password()` → update `password_hash` + set `must_change_password=False` → redirect to `/` (FR-019, FR-020, FR-021)
- [x] T032 [US3] Create `app/templates/auth/change_password.html` — standalone form (no shell); shows password requirements; inline error display on validation failure
- [x] T033 [US3] Update `app/auth/middleware.py` — add `must_change_password` gate: if session is valid and `must_change_password=True` and request path is not `/auth/change-password` or `/auth/logout`, redirect to `/auth/change-password` (FR-019)
- [x] T034 [P] [US3] Write unit tests in `tests/unit/test_auth_service.py` — cover: `validate_password_requirements` all failure modes (too short, no upper, no lower, no digit), `hash_password` produces bcrypt hash, admin-only route returns 403 for non-admin `CurrentUser`

**Checkpoint**: Full John Doe flow works: provisioned by admin → forced change → new password → shell. Admin reset puts `must_change_password` back to `True`.

---

## Phase 6: User Story 4 — OIDC JIT Provisioning, Fail-Closed (Priority: P2)

**Goal**: New PremiumIQ users are auto-provisioned on first OIDC sign-in with no roles assigned. They see "access pending" until an admin assigns a role. Zero content is accessible during the pending period.

**Independent Test**: Delete `app_user` for a PremiumIQ account. Sign in via OIDC. Verify `app_user` created with no `user_role` rows. Verify "access pending" shown. Direct URL `/submissions` → still access-pending. Admin assigns role → next sign-in reaches shell.

### Implementation

- [x] T035 [US4] Add role gate to `app/auth/middleware.py` — after session is valid and `must_change_password` gate passes: if `CurrentUser.role_codes` is empty and request path is not `/auth/access-pending` or `/auth/logout`, redirect to `/auth/access-pending`; add `GET /auth/access-pending` route in `app/routers/auth.py` that renders `access_pending.html` (FR-024)
- [x] T036 [US4] Add `GET /admin/users/{id}` to `app/routers/admin.py` — show user detail page with role assignment and force-logout actions; used by admin to promote pending OIDC users
- [x] T037 [P] [US4] Write unit tests in `tests/unit/test_auth_service.py` — cover: `jit_provision_oidc_user` inserts row with correct fields; no duplicate on second call with same `oid` (function is idempotent via `get_user_by_oid` check — FR-025); empty role list triggers access-pending redirect path

**Checkpoint**: New OIDC users land on access-pending. Direct URL manipulation during pending shows access-pending, not content. After role assignment, next session reaches shell.

---

## Phase 7: User Story 5 — Session Expiry & HTMX-Aware Redirect (Priority: P2)

**Goal**: Expired sessions redirect to login for both standard and HTMX requests. HTMX requests get `HX-Redirect` (HTTP 200), not a 302 that swaps the login form into a content fragment.

**Independent Test**: Manually expire `last_active_at` in DB. Make standard request → 302 to `/auth/login`. Make HTMX request (`HX-Request: true`) → 200 with `HX-Redirect: /auth/login` header.

### Implementation

- [x] T038 [US5] Verify and harden `app/auth/middleware.py` HTMX detection — confirm all three expiry paths (idle timeout, absolute timeout, `invalidated_at`) return `Response(status_code=200, headers={"HX-Redirect": "/auth/login"})` when `HX-Request: true`; standard requests return `RedirectResponse("/auth/login", 302)` (FR-012, FR-013, FR-014, FR-015, FR-017)
- [x] T039 [P] [US5] Write unit tests in `tests/unit/test_session_expiry.py` — cover: idle timeout detection returns correct response type for HTMX vs non-HTMX, absolute timeout detection, `invalidated_at` rejection, `last_active_at` updated on valid request (FR-012, SC-006)
- [x] T040 [P] [US5] Write unit tests in `tests/unit/test_csrf.py` — cover: CSRF token generated and validated correctly, expired token rejected, tampered token rejected, mismatch returns 403

**Checkpoint**: `HX-Redirect` header confirmed in browser DevTools Network tab for expired-session HTMX requests. All session/CSRF unit tests pass.

---

## Phase 8: User Story 6 — Shell Renders and Is Navigable (Priority: P1)

**Goal**: Authenticated user sees the complete IDE-style shell. All 7 rail icons work. Sidebar links swap content. URL is always truthful. JS-disabled fallback works.

**Independent Test**: Log in. Click all 7 rail icons. Click sidebar links. F5 reloads same view. Navigate directly via URL (deep-link). Disable JS — full-page nav still works.

### Implementation

- [x] T041 [US6] Update `app/templates/base/shell.html` — wire breadcrumb rendering (call `breadcrumb(current_key)` from manifest helper, render as `Rail > Section > Page`), active-state highlighting on rail and sidebar (compare `top_ancestor(current_key)` to each rail node), sidebar title from `node.sidebar_title` (FR-026, FR-027, FR-028)
- [x] T042 [US6] Add `hx-push-url` to all sidebar links in `app/templates/base/shell.html` — sidebar links use `hx-get` + `hx-target="#main-content"` + `hx-push-url="true"`; fallback `href` set to the same route for JS-disabled navigation (FR-033)
- [x] T043 [US6] Implement status bar in `app/templates/base/shell.html` — left zone: env badge (CSS class `env--dev` vs `env--prod` based on `APP_ENV`), `current_user.display_name`, active role (FR-027). Multi-role rule: when a user has multiple roles, show the highest-privilege one — `is_admin=true` role first; among non-admin roles, lowest `sort_order` wins. "No role" is shown only on the access-pending path.
- [x] T044 [P] [US6] Write unit tests in `tests/unit/test_nav_manifest.py` — cover: `rail_nodes()` returns 7 top-level nodes + 1 bottom; `children("submissions")` returns correct subset; `breadcrumb("submissions.all")` returns `[home, submissions, all]`; `visible_nodes(["analyst"])` hides admin-only nodes; `visible_nodes(["admin"])` shows all

**Checkpoint**: Shell renders with correct rail, sidebar, breadcrumb, and status bar. Browser Back/Forward work. F5 on any page reloads correctly. All nav unit tests pass.

---

## Phase 9: User Story 7 — Navigation Manifest Is Single Source of Truth (Priority: P1)

**Goal**: Adding one manifest node is sufficient to add a page. Role gates, breadcrumbs, and search visibility all derive from the manifest with no other code changes required.

**Independent Test**: Add a test node to `app/nav/manifest.py`. Verify it appears in rail/sidebar without other changes. Add `roles: ["admin"]` — hidden from analyst. Remove the node — disappears.

### Implementation

- [x] T045 [US7] Verify `app/nav/manifest.py` `visible_nodes()` is called in `app/templates/base/shell.html` for both rail and sidebar rendering — no hardcoded route lists anywhere in templates or handlers; all rail/sidebar entries generated from manifest (FR-029, FR-030, FR-031, FR-032)
- [x] T046 [US7] Add `app/nav/__init__.py` — export `get_nav_context(current_user, current_key) -> dict` that returns `{rail, sidebar, breadcrumb, active_key}` for template rendering; used by every shell route handler to pass nav context to templates
- [x] T047 [US7] Update all shell route handlers in `app/routers/shell.py` — each handler calls `get_nav_context(current_user, key)` and passes result to template; no nav logic in route handlers (FR-030)

**Checkpoint**: Add a test node to manifest → appears in app. Remove it → gone. Role-gated node invisible to analyst, visible to admin. No handler or template changes needed to add/remove nodes.

---

## Phase 10: User Story 8 — Health Check (Priority: P2)

**Goal**: `GET /api/health` independently reports WORKBENCH, EXPOSURE, LOSS, and Redis status. Always returns HTTP 200. No credentials in response.

**Independent Test**: All services up → all fields `"ok"`. Stop Redis → `"redis"` is error string, DBs remain `"ok"`.

### Implementation

- [x] T048 [US8] Create `app/routers/health.py` — `GET /api/health`: execute a minimal ping query against each of the three DB connections (`SELECT 1`) wrapped in try/except; ping Redis with `redis.ping()`; return `{"status":"ok","db_workbench":...,"db_exposure":...,"db_loss":...,"redis":...,"env": settings.app_env}`; HTTP 200 always; no credentials or stack traces in error strings (FR-034, SC-007)
- [x] T049 [US8] Register `app/routers/health.py` in `app/main.py` — include health router; health endpoint MUST be reachable without authentication (exempt from session middleware)

**Checkpoint**: `curl http://localhost:8000/api/health` returns full JSON with all-ok when services are running. Stopping individual services produces per-field errors, HTTP 200 still returned.

---

## Phase 11: User Story 9 — Schema Rebuild in One Command (Priority: P2)

**Goal**: `make wsl-db-rebuild` (and `make db-rebuild` for Docker) drops and recreates all three app databases, runs the Alembic revision, and seeds data. Fully idempotent.

**Independent Test**: `make wsl-db-rebuild` twice. Second run succeeds identically. App starts and health check is green after both runs.

### Implementation

- [x] T050 [US9] Finalize `infra/scripts/reset_db.py` — connect as SA, `DROP DATABASE IF EXISTS` + `CREATE DATABASE` for `rwb_workbench`, `rwb_exposure`, `rwb_loss` only; never touch DATABRIDGE; print progress; exit non-zero on failure (FR-038)
- [x] T051 [US9] Finalize `infra/scripts/seed_db.py` — insert `role_kind` rows (`analyst`, `admin`); insert one dev fixture `app_user` (email: `admin@example.com`, bcrypt hash, `must_change_password=False`, role=`admin`); idempotent (`IF NOT EXISTS` / `MERGE` or delete-then-insert) (FR-037)
- [x] T052 [US9] Write SQL Server integration test in `tests/sqlserver/test_auth_migration.py` — connect to live SQL Server; run `alembic upgrade head`; verify all auth tables exist; verify `role_kind` seeds present; verify second `alembic upgrade head` is idempotent (no error)

**Checkpoint**: `make wsl-db-rebuild` succeeds on second run. `make wsl-app` starts cleanly. `GET /api/health` green. SQL Server integration test passes (`make wsl-test-sql`).

---

## Phase 12: Polish & Cross-Cutting Concerns

**Purpose**: Error pages, HTMX 404/500 handling, CSRF on all state-changing forms, `next` parameter preservation, edge-case hardening.

- [x] T053 [P] Implement `GET /auth/login` `next` parameter preservation — when redirect to login occurs, append `?next=<original_url>` (URL-encoded); on successful login, redirect to `next` value (validate it is a relative path to prevent open redirect)
- [x] T054 [P] Wire 404 and 500 handlers in `app/main.py` — full-page requests render `app/templates/base/error.html` inside shell (if user is authenticated) or standalone; HTMX requests return fragment partial with just the error block (FR-035, FR-036)
- [x] T055 [P] Add CSRF hidden field to all state-changing forms — `POST /auth/login`, `POST /auth/logout`, `POST /auth/change-password`, `POST /admin/users/new`, `POST /admin/users/{id}/reset-password`, `POST /admin/users/{id}/assign-role`, `POST /admin/users/{id}/force-logout` (FR-007). Verification pass: after all forms are implemented, grep for `<form method="post"` and confirm every match has a CSRF hidden field. Any POST handler without a CSRF check is a blocking bug.
- [x] T056 [P] Add Jinja2 template globals in `app/main.py` — inject `settings.password_auth_enabled`, `settings.oidc_auth_enabled`, `settings.app_env`, CSRF token generator so all templates can access them without explicit handler passing
- [x] T058 [P] Add Workflows sidebar children to `app/nav/manifest.py` and stub routes/templates — add manifest nodes `workflows.active`, `workflows.review`, `workflows.irp_jobs`, `workflows.rwb_jobs`, `workflows.exceptions` (see `contracts/nav-manifest.md`); add stub route handlers in `app/routers/shell.py` for `GET /workflows/active`, `/workflows/review`, `/workflows/irp-jobs`, `/workflows/rwb-jobs`, `/workflows/exceptions`; add stub templates `app/templates/pages/workflows_active.html`, `workflows_review.html`, `workflows_irp_jobs.html`, `workflows_rwb_jobs.html`, `workflows_exceptions.html`. The RWB Jobs page is a read-only monitoring view (`workflows.rwb_jobs`) and must appear in the Workflows sidebar — it will show queued work from the `rwb_job` table in later iterations; in Iteration 0 it renders a stub with the correct shell chrome and breadcrumb `Workflows > RWB Jobs`.
- [ ] T057 Run full quickstart validation per `quickstart.md` — execute all 8 scenarios manually; confirm SC-001 through SC-008 are met; document any deviations

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately; T001 blocks T015 (app startup)
- **Phase 2 (Foundational)**: Depends on Phase 1 — T008–T015 BLOCK all user story phases
- **Phase 3 (US1 — Password login)**: Depends on Phase 2 — MVP; validates the full auth + shell stack
- **Phase 4 (US2 — OIDC)**: Depends on Phase 2; independent of Phase 3 (different handler paths)
- **Phase 5 (US3 — Admin provisioning)**: Depends on Phase 3 (admin sees the shell; password flow required)
- **Phase 6 (US4 — OIDC JIT)**: Depends on Phase 4 (extends the OIDC callback path)
- **Phase 7 (US5 — Session expiry)**: Depends on Phase 2 (middleware already exists; this phase hardens it)
- **Phase 8 (US6 — Shell nav)**: Depends on Phase 3 (needs login to verify shell)
- **Phase 9 (US7 — Manifest)**: Depends on Phase 8 (verifies manifest drives the shell built in US6)
- **Phase 10 (US8 — Health check)**: Depends on Phase 2 (app wiring); independent of auth user stories
- **Phase 11 (US9 — DB rebuild)**: Depends on Phase 1 (scripts created there); can be finalized any time
- **Phase 12 (Polish)**: Depends on all user story phases complete

### Parallel Opportunities Within Phases

**Phase 1**: T002, T003, T004, T005, T006, T007 can all run in parallel after T001 is staged (T001 has no blocker).

**Phase 2**: T009, T010, T012, T014 can run in parallel once T008 (manifest) is done; T011 (middleware) depends on T012 (auth_service); T013 (shell template) depends on T008 (manifest); T015 (main.py) depends on T011 + T013.

**Phase 3 (US1)**: T016 (template) and T021 (tests) can run in parallel with T017–T020 (handlers/templates).

**Phase 4 (US2)**: T022 (oidc.py) and T026 (provisioning.py) can run in parallel; T023–T024 depend on T022.

---

## Parallel Example: Foundation (Phase 2)

```
Start T008 (manifest) immediately
  └─ T009 (csrf.py)         ← parallel with T010, T012, T014
  └─ T010 (password.py)     ← parallel with T009, T012, T014
  └─ T012 (auth_service.py) ← parallel with T009, T010, T014
  └─ T014 (error.html)      ← parallel with T009, T010, T012
  └─ T013 (shell.html)      ← after T008; parallel with T009, T010, T012
  └─ T011 (middleware.py)   ← after T012 (auth_service); parallel with T013
  └─ T015 (main.py)         ← after T011 + T013
```

---

## Implementation Strategy

### MVP First (US1 + Shell + Manifest = Phases 1–3 + 8–9)

1. Complete Phase 1: Setup (T001–T007)
2. Complete Phase 2: Foundational (T008–T015) — CRITICAL gate
3. Complete Phase 3: US1 — Password login (T016–T021)
4. Complete Phase 8: US6 — Shell nav (T041–T044)
5. Complete Phase 9: US7 — Manifest (T045–T047)
6. **STOP and VALIDATE**: Password login → shell → sign-out → all 7 rail icons → JS-disabled nav
7. Demo-ready: a password account holder can use the full application shell

### Full Iteration 0

After MVP, continue in this order (single developer):

- Phase 4 (US2 — OIDC) → Phase 6 (US4 — JIT provisioning)
- Phase 5 (US3 — Admin provisioning) → Phase 7 (US5 — Session expiry hardening)
- Phase 10 (US8 — Health) → Phase 11 (US9 — DB rebuild)
- Phase 12 (Polish)

### Parallel Team Strategy (2 developers)

After Phase 2 (Foundational) is complete:

- **Developer A**: Phase 3 (US1) → Phase 5 (US3) → Phase 8 (US6) → Phase 9 (US7)
- **Developer B**: Phase 4 (US2) → Phase 6 (US4) → Phase 7 (US5) → Phase 10 (US8) → Phase 11 (US9)
- Both: Phase 12 (Polish) together

---

## Notes

- `[P]` tasks touch different files and have no unresolved dependencies within their phase
- `[US#]` label maps each task to its user story for traceability against spec.md
- Each phase ends with an independently verifiable checkpoint
- No test tasks are included (not requested in spec); unit tests are included where they directly validate a spec requirement
- `make wsl-db-rebuild` must be run before starting any phase that requires a live database
- Commit after each phase checkpoint to keep the branch bisectable
- Rate limiting and customer access admin are explicitly out of scope for this iteration (see spec.md Assumptions)
