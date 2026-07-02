# Feature Specification: Application Shell, Navigation & Authentication

**Feature Branch**: `001-app-shell-nav-auth`

**Created**: 2026-07-01

**Status**: Draft

**Input**: User description: "Iteration 0: Application shell, navigation, and auth"

---

## What This Iteration Covers

Iteration 0 delivers the complete structural and security foundation that every subsequent iteration builds on. It covers three distinct areas that must ship together because auth gates access to the shell:

**1 — Application shell and navigation**
The IDE-style shell (rail, sidebar, main area, top bar, status bar) driven by a single code manifest. Adding a page is one manifest node + one handler + one template. Breadcrumbs, active-state, and search visibility all derive from the manifest.

**2 — Authentication: password login and OIDC login**
Both auth modes are live. An unauthenticated user lands on the login page and chooses their path:
- **Password login** — for provisioned internal accounts (e.g. John Doe, a contractor or analyst without a PremiumIQ Entra account). Admin-created, bcrypt-hashed, CSRF-protected.
- **OIDC login** — for anyone with a `@premiumiq.com` Entra account. Click "Sign in with Microsoft", authenticate via Entra, land in the app. No admin pre-registration needed for the account itself (though roles must be assigned before they can do anything).

Both paths produce an identical `CurrentUser` object. Everything downstream — role gates, RLS, audit — is auth-mode-agnostic.

**3 — User provisioning**
Two provisioning workflows, one per auth mode:
- **Password accounts (JIT by admin)**: Admin creates the account in the admin UI, sets a temporary password, and the user is forced to change it on first login.
- **OIDC accounts (JIT on first sign-in)**: A PremiumIQ user signs in via Entra for the first time. An `app_user` row is created automatically. The user sees an "access pending" screen until an admin assigns a role.

**Sign-out** takes the user back to the login page in both modes. For OIDC, the Entra-side session is also cleared.

**What you will have at the end of this iteration:**

- The shell renders and is navigable; all seven rail destinations work
- `GET /auth/login` shows the login page with password form and "Sign in with Microsoft" button
- An analyst with a password account can log in, is forced to change a temporary password on first login, and can sign out
- A PremiumIQ user can click "Sign in with Microsoft", authenticate via Entra, and land in the app (or see "access pending" if no role is assigned)
- Admin can create a password account (name, email, temporary password) via the admin UI
- Session management: sliding idle timeout (8h), absolute cap (24h), HTMX-aware expiry redirect, admin force-logout
- CSRF protection on all state-changing requests
- Health check, schema rebuild, error pages, shared UI patterns — all as before

**How to verify you got it:**

1. Open `http://localhost:8000` unauthenticated — redirected to `/auth/login`
2. Log in with a password account — lands in the shell; status bar shows the user's name
3. Sign out — redirected to `/auth/login`; session cookie is gone; pressing Back does not restore the session
4. Click "Sign in with Microsoft" — redirected to Entra; sign in with `avenugopal@premiumiq.com`; land in the shell (or "access pending" if no role assigned)
5. Admin creates a new password user "John Doe" via the admin UI; John logs in with the temporary password; is immediately forced to the change-password screen; sets a new password; lands in the shell
6. A new PremiumIQ user signs in via OIDC for the first time; `app_user` row is created; they see "access pending"; admin assigns a role; on next login they reach the shell
7. Leave a session idle for longer than the timeout; return and try to navigate — redirected to `/auth/login` (HTMX requests get `HX-Redirect`, not a fragment of the login page)

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Password login and sign-out (Priority: P1)

An analyst with a provisioned password account opens the app, logs in with their email and password, uses the application, and signs out. The session is invalidated on sign-out and cannot be resumed by pressing Back.

**Why this priority**: The shell is gated by auth. Without login, nothing else in the iteration can be demonstrated or tested.

**Independent Test**: Create an `app_user` row directly in the database (or via seed) with a bcrypt-hashed password. Log in via the form. Verify the session cookie is set and a `user_session` row exists. Sign out. Verify the cookie is cleared and `user_session.invalidated_at` is set. Press Back — the app redirects to `/auth/login`, not back into the shell.

**Acceptance Scenarios**:

1. **Given** an unauthenticated user navigates to any protected URL, **When** the request arrives, **Then** they are redirected to `GET /auth/login` with the original URL preserved as a `next` parameter
2. **Given** the login form is submitted with correct credentials, **When** authentication succeeds, **Then** an `HttpOnly Secure SameSite=Lax` session cookie is set, a `user_session` row is written, and the user is redirected to the originally-requested URL or home
3. **Given** the login form is submitted with an incorrect password, **When** authentication fails, **Then** the form is re-rendered with a generic error ("Invalid email or password") — the message never reveals whether the email exists
4. **Given** a signed-in user clicks "Sign out", **When** `POST /auth/logout` is processed, **Then** `user_session.invalidated_at` is set, the cookie is cleared, and the user is redirected to `GET /auth/login`
5. **Given** a user signs out and presses the browser Back button, **When** the browser re-requests the prior page, **Then** the app rejects the expired session and redirects to `/auth/login` — no content is served from the signed-out session

---

### User Story 2 — OIDC login via Microsoft (Priority: P1)

A PremiumIQ analyst clicks "Sign in with Microsoft", authenticates with their `@premiumiq.com` Entra account, and lands in the application. No one needs to pre-register their account — it is created automatically on first sign-in.

**Why this priority**: OIDC is the primary production auth path. It must work from day one so it can be tested throughout development.

**Independent Test**: With `AUTH_MODE=oidc` and valid Entra credentials in `.env`, click "Sign in with Microsoft". Verify the browser redirects to `login.microsoftonline.com`. Authenticate. Verify the callback creates (or finds) an `app_user` row with `entra_oid` set and no `password_hash`. Verify the session cookie is set and the user lands in the app.

**Acceptance Scenarios**:

1. **Given** a user clicks "Sign in with Microsoft" on the login page, **When** the OIDC flow begins, **Then** the browser is redirected to the Entra authorization endpoint with a PKCE code challenge and a random `state` parameter
2. **Given** the user completes Entra authentication, **When** the callback arrives at `GET /auth/callback`, **Then** the `state` parameter is validated, the authorization code is exchanged for tokens server-side, and the `oid` and `email` claims are extracted from the ID token
3. **Given** no `app_user` exists with the returned `oid`, **When** the callback processes a new identity, **Then** a new `app_user` row is inserted with `entra_oid`, `email`, `display_name`, `is_active=true`, `password_hash=null` — and the user sees the "access pending" screen
4. **Given** an `app_user` exists with the returned `oid`, **When** the callback processes a returning identity, **Then** `last_login_at` is updated, a new `user_session` row is written, and the user is redirected to home (or the originally-requested URL)
5. **Given** the `state` parameter on callback does not match what was stored before the redirect, **When** the callback validates state, **Then** the flow is aborted with an error — no session is created
6. **Given** a signed-in OIDC user clicks "Sign out", **When** `POST /auth/logout` is processed, **Then** the local session is invalidated and the browser is redirected to the Entra logout endpoint, which then redirects back to `/auth/login`

---

### User Story 3 — Admin provisions a password account for John Doe (Priority: P1)

An admin opens the Administration section, creates a new user account for a contractor (John Doe, `john.doe@external.com`) who does not have a PremiumIQ Entra account. The admin sets a temporary password. John logs in, is immediately forced to change the password, and then lands in the application.

**Why this priority**: Password accounts are the only path for non-Entra users. Without provisioning, those users cannot access the system at all.

**Independent Test**: As an admin, navigate to Administration → Users → New User. Fill in name, email, temporary password. Submit. Verify `app_user` row created with `must_change_password=true`. Log in as John Doe with the temporary password. Verify immediate redirect to `/auth/change-password`. Submit a new password meeting requirements. Verify `must_change_password=false` and redirect to home.

**Acceptance Scenarios**:

1. **Given** an admin navigates to Administration → Users, **When** they click "New User" and submit a valid name, email, and temporary password, **Then** an `app_user` row is created with `must_change_password=true` and a bcrypt-hashed password (cost factor 12)
2. **Given** John Doe logs in for the first time with the temporary password, **When** authentication succeeds, **Then** the app redirects immediately to `GET /auth/change-password` before any other page is accessible
3. **Given** John is on the change-password screen, **When** he submits a new password meeting requirements (12+ chars, upper, lower, digit), **Then** `password_hash` is updated, `must_change_password` is set to false, and he is redirected to the home page
4. **Given** John is on the change-password screen, **When** he submits a password that does not meet requirements, **Then** the form is re-rendered with specific validation feedback and `must_change_password` remains true
5. **Given** John attempts to access any route other than `/auth/change-password` while `must_change_password=true`, **When** the request arrives, **Then** he is redirected back to the change-password screen — no content is accessible until the password is changed
6. **Given** the admin sets a new temporary password for an existing user (password reset), **When** the admin submits the reset form, **Then** `password_hash` is updated and `must_change_password` is set back to true — the user must change it on next login

---

### User Story 4 — OIDC JIT provisioning for a new PremiumIQ user (Priority: P2)

A new PremiumIQ employee (`newanalyst@premiumiq.com`) signs in via Microsoft for the first time. Their account is created automatically. They see an "access pending" message explaining that an admin needs to assign their role. An admin then assigns the `analyst` role. On next sign-in, the user reaches the application.

**Why this priority**: OIDC provisioning must be fail-closed — auto-provision is convenient, but a newly provisioned user must not be able to access data before a human makes a deliberate authorization decision.

**Independent Test**: Sign in with a PremiumIQ account that has no corresponding `app_user` row. Verify an `app_user` row is created with no `user_role` rows. Verify the "access pending" screen is shown. Assign the `analyst` role via the admin UI. Sign in again — verify access to the shell. Verify that during the "access pending" period, no protected content was reachable by manipulating URLs.

**Acceptance Scenarios**:

1. **Given** a PremiumIQ user with no `app_user` row signs in via OIDC, **When** the callback creates the account, **Then** the new `app_user` row has no `user_role` rows and no `user_customer_access` rows
2. **Given** the newly provisioned user's session is established, **When** they are redirected after callback, **Then** they see an "access pending" screen explaining that an admin must assign their role — not the application shell
3. **Given** a provisioned-but-unroled user tries to navigate directly to any application URL, **When** the role gate evaluates their session, **Then** they see the "access pending" screen — no content is accessible
4. **Given** an admin assigns the `analyst` role to the pending user, **When** the user signs in again (new session), **Then** they reach the application shell and see data scoped to their customer access
5. **Given** an `app_user` row exists for an `entra_oid` from a previous sign-in, **When** the same user signs in via OIDC again, **Then** no duplicate `app_user` row is created — the existing row is matched by `entra_oid`

---

### User Story 5 — Session expiry and HTMX-aware redirect (Priority: P2)

An analyst leaves the application idle for longer than the session timeout. When they return and try to perform an action, they are redirected to the login page — not shown a fragment of the login page swapped into a content area.

**Why this priority**: HTMX requests that receive a login-page redirect are a common failure mode in HTMX apps. Without the `HX-Redirect` header, the login form renders inside the main content area (broken UI). This must be correct from the first day sessions exist.

**Independent Test**: Create a session row with `last_active_at` older than `SESSION_IDLE_TIMEOUT`. Make a standard browser request and an HTMX request. Verify the standard request receives a 302 redirect to `/login`. Verify the HTMX request receives a 200 with `HX-Redirect: /auth/login` header (not a 302, not a login-page fragment).

**Acceptance Scenarios**:

1. **Given** a session's `last_active_at` is older than `SESSION_IDLE_TIMEOUT` (default 8h), **When** a full-page request arrives, **Then** the app returns HTTP 302 to `/auth/login`
2. **Given** a session's `last_active_at` is older than `SESSION_IDLE_TIMEOUT`, **When** an HTMX request arrives (identified by `HX-Request: true` header), **Then** the app returns HTTP 200 with header `HX-Redirect: /auth/login` so HTMX performs a full-page redirect rather than swapping the login form into a fragment
3. **Given** a valid session is used, **When** each authenticated request completes, **Then** `user_session.last_active_at` is updated, resetting the idle timer
4. **Given** a session has existed longer than `SESSION_ABSOLUTE_TIMEOUT` (default 24h) regardless of activity, **When** any request arrives, **Then** the session is rejected and the user is redirected to login — no sliding extension overrides the absolute cap
5. **Given** an admin sets `invalidated_at` on a user's `user_session` row (force-logout), **When** the next request from that session arrives, **Then** the session is rejected immediately and the user is redirected to login

---

### User Story 6 — Shell renders and is navigable (Priority: P1)

An authenticated user sees the complete IDE-style shell. Clicking rail icons shows the correct sidebar. Clicking sidebar links swaps content. The browser URL stays truthful so deep-links, refresh, and bookmarks all work.

**Why this priority**: The shell is the container for everything else. It must work correctly once auth is in place.

**Independent Test**: Log in as any user. Verify the shell renders with all seven rail icons. Click each icon — correct sidebar appears. Click sidebar links — content swaps, breadcrumb updates, browser URL updates. F5 reloads the same view. Status bar shows the signed-in user's name.

**Acceptance Scenarios**:

1. **Given** an authenticated user navigates to `/`, **When** the shell renders, **Then** the rail, top bar, status bar, and main content area are all visible; the status bar left zone shows the signed-in user's display name
2. **Given** the shell is open, **When** a user clicks any of the seven rail icons, **Then** the correct sidebar opens and that rail icon is highlighted as active
3. **Given** a sidebar link is clicked, **When** the HTMX swap completes, **Then** the main content updates, the breadcrumb reflects the new location, and the browser address bar shows the correct URL
4. **Given** a user navigates to a URL directly (deep-link or bookmark), **When** the page loads, **Then** the shell renders with the correct rail icon active, correct breadcrumb, and correct sidebar open — no browser history required
5. **Given** the browser has JavaScript disabled, **When** a user clicks a nav link, **Then** a full-page navigation completes successfully — the same content loads, no JS-only dependencies

---

### User Story 7 — Navigation manifest drives all structure (Priority: P1)

All navigation structure — rail, sidebar, breadcrumbs, active state, search visibility, role gates — derives from a single code manifest. Adding a page is one manifest node.

**Why this priority**: The manifest is the keystone. If it is not the single source of truth from day one, navigation code accumulates in N places and becomes unmaintainable.

**Independent Test**: Add a test manifest node. Verify it appears in the rail or sidebar without any other code change. Remove it — it disappears. Add a node with `roles: ["admin"]` — verify it is hidden from non-admin users.

**Acceptance Scenarios**:

1. **Given** a node is added to the manifest with `parent=null` and `rail_icon`, **When** the app restarts, **Then** a new rail icon appears and clicking it shows the correct sidebar
2. **Given** a child node is added to an existing rail destination, **When** the app restarts, **Then** the item appears in the sidebar and its breadcrumb is `Rail Root > New Page`
3. **Given** a nav node has `roles: ["admin"]`, **When** a non-admin user's session is evaluated, **Then** that node does not appear in the rail or sidebar — no additional code is required to implement the gate
4. **Given** a detail route declares a home manifest node, **When** the detail page renders, **Then** the breadcrumb is `Root > Section > Entity Name` — the entity name appended to the manifest path, not derived from browser history
5. **Given** a nav node has `searchable: false`, **When** future search iterates the manifest, **Then** that node is excluded from navigation search results automatically

---

### User Story 8 — Health check and database connectivity (Priority: P2)

A developer verifies that all three database connections and Redis are reachable in a single HTTP request.

**Independent Test**: `GET /api/health` with all services running — all fields green. Stop Redis — `redis` field shows error, DB fields remain green. HTTP 200 in both cases.

**Acceptance Scenarios**:

1. **Given** all services are running, **When** `GET /api/health` is requested, **Then** response is `{"status":"ok","db_workbench":"ok","db_exposure":"ok","db_loss":"ok","redis":"ok","env":"development"}` and HTTP 200
2. **Given** Redis is stopped, **When** `GET /api/health` is requested, **Then** `"redis"` is an error string, DB fields remain `"ok"`, response is still HTTP 200
3. **Given** the WORKBENCH database is unreachable, **When** `GET /api/health` is requested, **Then** `"db_workbench"` is an error string, response is HTTP 200, and no credentials appear in the error

---

### User Story 9 — Schema rebuild in one command (Priority: P2)

A developer runs `make wsl-db-rebuild` and gets a fresh, fully-seeded database — including all auth tables — ready for the next session.

**Acceptance Scenarios**:

1. **Given** SQL Server is running with existing data, **When** `make wsl-db-rebuild` is confirmed, **Then** all three databases are dropped and recreated; auth tables (`app_user`, `user_session`, `login_attempt`, `user_role`, `user_customer_access`) are created and kind tables seeded
2. **Given** the rebuild completes, **When** `make wsl-app` is run, **Then** the app starts and `/api/health` is green
3. **Given** the rebuild has run once, **When** it is run a second time, **Then** the result is identical — the command is fully idempotent

---

### Edge Cases

- What happens when a password login is attempted for an account with `AUTH_MODE=oidc` set and `password_hash=null`? → The login form returns "This account uses Microsoft sign-in" — never attempts bcrypt on a null hash
- What happens when an OIDC callback arrives with a `state` that does not match the pre-auth session? → The flow is aborted, no session is created, user sees a generic error
- What happens when the OIDC callback receives a valid token but the `email` claim is absent? → The callback fails with a clear server-side error; Step 4 of the Entra setup guide (`docs/ENTRA_SETUP.md`) explains how to add the email claim
- What happens when a `must_change_password=true` user accesses `/auth/change-password` directly after setting a new password in another tab? → The second tab finds `must_change_password=false` and redirects to home — not a double-change error
- What happens if the Entra token exchange fails (network error, expired code)? → The user is redirected to `/auth/login` with a generic error; the partial OIDC state (PKCE verifier, state) is cleared from the pre-auth session
- What happens when a nav node's handler raises an unhandled exception? → 500 page renders inside the shell; stack trace never exposed; error logged server-side
- What happens when `hx-boost` is active and a navigation target returns a redirect (e.g. to login)? → HTMX follows the redirect; if it returns `HX-Redirect`, HTMX performs a full-page redirect — the login page is never swapped into a content fragment

---

## Requirements *(mandatory)*

### Functional Requirements

**Authentication — login page:**
- **FR-001**: `GET /auth/login` MUST render a login page whose content is determined by `AUTH_MODE`: `password` → password form only; `oidc` → "Sign in with Microsoft" button only; `both` → password form and "Sign in with Microsoft" button. No inactive UI elements are shown for a mode that is not configured
- **FR-002**: The login page MUST be accessible without a session cookie; all other routes MUST redirect unauthenticated requests to `/auth/login` with the original URL preserved
- **FR-003**: The login page MUST NOT render inside the application shell — it has its own minimal layout

**Authentication — password login:**
- **FR-004**: `POST /auth/login` MUST look up `app_user` by email (case-insensitive), verify the submitted password against `password_hash` using bcrypt (cost factor 12), and on success create a `user_session` row and set an `HttpOnly Secure SameSite=Lax` cookie containing only the session ID (random 32-byte hex, 64-char encoded)
- **FR-005**: On password authentication failure, the login form MUST be re-rendered with a generic error message that does not indicate whether the email address exists in the system
- **FR-006**: An `app_user` with `password_hash=null` who attempts password login MUST receive the message "This account uses Microsoft sign-in" — bcrypt MUST NOT be invoked on a null hash
- **FR-007**: CSRF tokens MUST be required on `POST /auth/login`, `POST /auth/logout`, and `POST /auth/change-password`; a mismatch MUST return HTTP 403

**Authentication — OIDC login:**
- **FR-008**: Clicking "Sign in with Microsoft" MUST initiate an authorization-code flow with PKCE: generate a code verifier + challenge, store a random `state` value in the pre-auth session, and redirect to the Entra authorization endpoint
- **FR-009**: `GET /auth/callback` MUST validate the `state` parameter before processing the token exchange; a mismatch MUST abort the flow and redirect to `/auth/login` with an error
- **FR-010**: The token exchange MUST be server-side (BFF pattern); ID token and access token MUST NOT be stored in the session row, the cookie, or any client-accessible storage — they are discarded after `oid` and `email` are extracted
- **FR-011**: On successful OIDC callback, if `email` is absent from the ID token, the flow MUST fail with a logged server-side error and a user-facing "Sign-in configuration error — contact your administrator" message

**Session management:**
- **FR-012**: `user_session.last_active_at` MUST be updated on every authenticated request
- **FR-013**: A session MUST be rejected when `last_active_at` is older than `SESSION_IDLE_TIMEOUT` (default 8h, configurable via env var)
- **FR-014**: A session MUST be rejected when `now() > expires_at` (`created_at + SESSION_ABSOLUTE_TIMEOUT`, default 24h) regardless of activity
- **FR-015**: When a session is rejected on an HTMX request (identified by `HX-Request: true`), the response MUST be HTTP 200 with header `HX-Redirect: /auth/login` — never a 302 that causes HTMX to swap the login page into a content fragment
- **FR-016**: `POST /auth/logout` MUST set `user_session.invalidated_at`, clear the session cookie, and for OIDC sessions redirect to the Entra logout endpoint with a `post_logout_redirect_uri` pointing back to `/auth/login`; for password sessions redirect directly to `/auth/login`
- **FR-017**: An admin MUST be able to force-invalidate any `user_session` row by setting `invalidated_at`; the invalidation MUST take effect on the next request from that session

**User provisioning — password accounts:**
- **FR-018**: An admin MUST be able to create a new `app_user` via the Administration → Users → New User form, supplying display name, email, and a temporary password; the password MUST be stored as a bcrypt hash (cost factor 12) with `must_change_password=true`
- **FR-019**: When a user with `must_change_password=true` authenticates successfully, they MUST be redirected to `GET /auth/change-password` and MUST NOT be able to access any other route until they have set a new password
- **FR-020**: `POST /auth/change-password` MUST enforce password requirements: minimum 12 characters, at least one uppercase letter, one lowercase letter, one digit; requirements MUST be checked server-side regardless of any client-side validation
- **FR-021**: On successful password change, `must_change_password` MUST be set to false and the user redirected to home
- **FR-022**: An admin MUST be able to reset a user's password via the admin UI; doing so MUST set a new temporary password hash and set `must_change_password=true`

**User provisioning — OIDC accounts:**
- **FR-023**: On first OIDC sign-in for an unrecognised `oid`, the app MUST insert a new `app_user` row with `entra_oid`, `email`, `display_name` (from `preferred_username` claim if available, else email prefix), `is_active=true`, `password_hash=null`; no `user_role` or `user_customer_access` rows MUST be created automatically
- **FR-024**: A newly OIDC-provisioned user with no `user_role` rows MUST see an "access pending" screen on every request; they MUST NOT be able to access any application content until a role is assigned
- **FR-025**: On subsequent OIDC sign-ins, the app MUST match by `entra_oid` and MUST NOT create a duplicate `app_user` row

**Shell layout:**
- **FR-026**: The application shell MUST render an IDE-style layout with five zones: left rail, sidebar, main content, top bar (breadcrumb + search placeholder + Help), status bar
- **FR-027**: The status bar left zone MUST show: environment badge (visually prominent in dev/local, subdued in production), signed-in user display name, active role
- **FR-028**: The home rail destination MUST render without a sidebar (full-width main); all other rail destinations MUST show a sidebar

**Navigation manifest:**
- **FR-029**: All navigation structure MUST be declared in a single code manifest; each node declares: `key`, `label`, `parent`, `rail_icon`, `route`, `template`, `breadcrumb_label`, `searchable`, `roles`
- **FR-030**: Rail, sidebar, breadcrumbs, active-state, and role-based visibility MUST all derive from the manifest — no navigation logic outside it
- **FR-031**: Adding a new page MUST require only one manifest node + one handler + one template; no other code changes
- **FR-032**: Nodes with `roles` restrictions MUST be hidden from users without those roles — the manifest is the single place to declare this

**Navigation transport:**
- **FR-033**: Top-level navigation MUST use `hx-boost` with `hx-push-url` so the address bar stays truthful; navigation MUST degrade gracefully without JavaScript

**Health check:**
- **FR-034**: `GET /api/health` MUST return HTTP 200 with per-field status for `db_workbench`, `db_exposure`, `db_loss`, `redis`, and `env`; no credentials or stack traces in the response

**Error states:**
- **FR-035**: HTTP 404 on full-page requests MUST render inside the shell; HTTP 404 on HTMX requests MUST return a fragment-safe partial
- **FR-036**: HTTP 500 MUST log the error server-side and return a user-friendly message; no tracebacks exposed in any environment

**Schema and seed:**
- **FR-037**: The Alembic `0001_initial.py` revision MUST create the following Iteration 0 tables: `app_user`, `user_session`, `login_attempt`, `role_kind`, `user_role`, `user_customer_access`, `customer`, `program`; and seed `role_kind` with `analyst` and `admin` rows. (Full table manifest is `docs/DATA_MODEL.md §12.1`; only `role_kind` from the kind-table checklist in `§13` is in Iteration 0 scope.)
- **FR-038**: `make wsl-db-rebuild` MUST drop and recreate all three app databases, run the Alembic revision, and seed data — including at minimum one `role_kind` row for `analyst` and one for `admin`

### Key Entities

- **`app_user`**: A provisioned user. Key attributes: `id`, `email`, `display_name`, `entra_oid` (nullable — set for OIDC accounts), `password_hash` (nullable — set for password accounts), `must_change_password`, `is_active`. Exactly one of `entra_oid` or `password_hash` is set for any active user; having both is invalid
- **`user_session`**: An active login session. Key attributes: `id` (the cookie value, 64-char hex), `user_id`, `created_at`, `last_active_at`, `expires_at`, `invalidated_at`, `ip_address`, `user_agent`
- **`login_attempt`**: Append-only audit row for every login attempt (success and failure). Used for rate limiting and audit
- **`user_role`**: Junction: one row per role assigned to a user. The absence of any row means the user has no access
- **`role_kind`**: Vocabulary of roles. Seeds: `analyst`, `admin`. The `admin` role carries `is_admin=true` which bypasses `apply_scope()`

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user with a password account can complete the full login → forced password change → access shell → sign out flow in under 2 minutes on first use
- **SC-002**: A PremiumIQ user clicking "Sign in with Microsoft" completes the OIDC round-trip (redirect to Entra, authenticate, return to app) in under 10 seconds on a normal network connection
- **SC-003**: A new PremiumIQ user who has never signed in before is auto-provisioned and sees the "access pending" screen within the same sign-in flow — zero manual steps needed before the account exists
- **SC-004**: An admin can provision a new password account (name, email, temporary password) in under 60 seconds via the admin UI
- **SC-005**: Every page in the authenticated shell has a real, stable URL — refresh, bookmark, and deep-link all produce the same view with zero reliance on browser history
- **SC-006**: An HTMX request made after session expiry receives `HX-Redirect: /auth/login` — the login form is never rendered inside a content fragment; verified by checking the response headers directly
- **SC-007**: `GET /api/health` responds in under 2 seconds and independently reports the status of all three database connections and Redis
- **SC-008**: `make wsl-db-rebuild` completes in under 3 minutes and leaves the app startable with a clean database on the first attempt

---

## Assumptions

- `AUTH_MODE` controls what the login page shows and which paths are active. Three valid values: `password` (form only), `oidc` (Microsoft button only), `both` (both options). `both` is the recommended dev default. Switching mode is a one-env-var change with no code changes required. If `AUTH_MODE` includes OIDC (`oidc` or `both`), the `ENTRA_*` env vars must be set
- The CSS design system files from `docintel/ui/src/styles` are committed to the repository. If they need to be sourced separately, that is a prerequisite resolved before implementation begins
- SVG icons for the seven rail destinations are available or placeholder icons are used; final icons are committed before the iteration is considered complete
- Entra app registration ("Governance", PremiumIQ tenant) is complete with redirect URI `http://localhost:8000/auth/callback` and `User.Read` permission granted. The `email` optional claim (Step 4 in `docs/ENTRA_SETUP.md`) must be added to the ID token before OIDC login can work end-to-end
- Rate limiting (per-email and per-IP lockout from PRD §5.1.3) is deferred from this iteration. The `login_attempt` table is created and all attempts are logged, but the lockout check is not implemented. This keeps the auth implementation focused; rate limiting is a follow-on hardening task
- `infra/scripts/reset_db.py` and `infra/scripts/seed_db.py` are created as part of this iteration — they do not yet exist
- EXPOSURE and LOSS schema tables are not defined yet. Their bootstrap scripts create the databases as empty for now; the Alembic revision targets WORKBENCH only
- The `apply_scope()` WORKBENCH-only guard in `db/scope.py` is implemented in this iteration even though row-scoping is not used in any Iteration 0 UI
- The admin UI in this iteration covers only user management (create user, assign role, reset password, force-logout). Customer access management (`user_customer_access`) is deferred to Iteration 2 when customer/submission data exists to scope against
