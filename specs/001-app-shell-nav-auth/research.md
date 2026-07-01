# Research: Application Shell, Navigation & Authentication

**Date**: 2026-07-01  
**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

No `NEEDS CLARIFICATION` markers were present in the spec. This document records design decisions made during planning and the rationale behind each.

---

## Decision 1 — Auth session storage: DB table vs Redis

**Decision**: `user_session` rows in SQL Server (`rwb_workbench`), not Redis.

**Rationale**: The spec requires admin force-logout (FR-017), absolute expiry independent of sliding activity (FR-014), and per-session audit (`ip_address`, `user_agent`). These require durable, queryable session state. Redis is stateless and loses in-flight data on restart (documented in `docs/DATA_MODEL.md`). A DB-backed session table is trivially joinable for admin force-logout and audit.

**Alternatives considered**: Redis with a hash per session ID. Rejected: force-logout requires scanning/deleting a specific key; absolute expiry requires a separate `created_at` field; Redis loss drops active sessions unexpectedly.

---

## Decision 2 — OIDC library: `msal` vs `authlib`

**Decision**: `msal` (Microsoft Authentication Library for Python).

**Rationale**: Already in `pyproject.toml`. Purpose-built for Entra ID / Azure AD. Handles PKCE code challenge generation, token exchange, and the authorization URL construction. `authlib` is more general but adds a dependency with no benefit for a single-IdP setup.

**Alternatives considered**: `authlib` (generic OIDC client). Rejected: additional dependency; `msal` is already present and Entra-specific.

---

## Decision 3 — CSRF protection mechanism

**Decision**: Double-submit cookie pattern with `itsdangerous.URLSafeTimedSerializer` (already a dependency via `itsdangerous`).

**Rationale**: `itsdangerous` is already present for session cookie signing. The double-submit pattern works without server-side state: a signed token is placed in both a cookie and a hidden form field; the server verifies they match and are not expired. No Redis or DB access needed in the CSRF check hot path.

**Alternatives considered**: Synchronizer token pattern (store token in `user_session`). Rejected: adds a DB read on every form render and a write on every submission; overkill for a low-traffic internal tool.

---

## Decision 4 — Entra PKCE state storage: pre-auth session vs signed cookie

**Decision**: Store PKCE `code_verifier` and `state` in a short-lived signed cookie (via `itsdangerous`), not in the `user_session` table.

**Rationale**: At the time the user clicks "Sign in with Microsoft", no `user_session` row exists yet (the user is not authenticated). The state must persist through the Entra redirect round-trip. A signed cookie with a short TTL (~5 minutes) holds the PKCE verifier and state without any DB write before authentication. On callback, the cookie is validated and discarded.

**Alternatives considered**: Server-side pre-auth session in Redis or DB. Rejected: requires a DB/Redis write for every login attempt (including abandoned ones); adds dependency on Redis availability during auth flow.

---

## Decision 5 — HTMX session expiry: `HX-Redirect` header implementation

**Decision**: A FastAPI middleware (`app/auth/middleware.py`) inspects every request after loading the session. If the session is expired and the request has `HX-Request: true`, the middleware returns `Response(status_code=200, headers={"HX-Redirect": "/auth/login"})` immediately, bypassing the route handler.

**Rationale**: HTMX interprets a 302 response as a content swap, rendering the login form inside the content fragment — broken UI. Returning 200 with `HX-Redirect` triggers a full-page redirect from HTMX's JS. This is the documented HTMX pattern for session expiry.

**Alternatives considered**: Return 401 or 403 and configure HTMX to handle it client-side. Rejected: requires Alpine.js or HTMX extension config to intercept 401; adds client-side state. The middleware approach is fully server-controlled.

---

## Decision 6 — Password requirements enforcement

**Decision**: Minimum 12 characters, at least one uppercase, one lowercase, one digit. Enforced server-side on `POST /auth/change-password`. Client-side validation is additive/optional (not required for spec compliance).

**Rationale**: FR-020 mandates server-side enforcement. The requirements are standard for internal tools and match the PRD. No complexity added; a single regex suffices.

**Alternatives considered**: NIST SP 800-63B recommends length over complexity rules. Not applied here — the spec explicitly lists complexity rules (upper, lower, digit) and the user confirmed them.

---

## Decision 7 — Nav manifest: Python dict vs DB table

**Decision**: Python `NODES` list in `app/nav/manifest.py` (code manifest). Not projected to DB tables.

**Rationale**: Article 1 requires a code manifest. The `reference/nav_manifest.py` mock is the reference implementation. Navigation structure changes require a code deploy anyway (new handler + template); storing it in DB adds sync complexity with zero benefit. The RBAC (`roles`) gate in the manifest is evaluated at render time against the session user's `user_role` rows.

**Alternatives considered**: DB table for nav nodes, loaded at startup. Rejected: manifest is a code artifact (Article 1 "versioned code manifests"); DB projection is only required for workflow definitions (Article 2) which are not in scope here.

---

## Decision 8 — `password_hash` column for OIDC-only accounts

**Decision**: `app_user.password_hash` is nullable. OIDC-provisioned users have `password_hash=NULL`. Attempting password login for such accounts returns "This account uses Microsoft sign-in" (FR-006) without invoking bcrypt.

**Rationale**: `bcrypt.checkpw` on a `None` value raises an exception; the guard must be explicit. The error message is specific enough to redirect the user without revealing account metadata (the email existence disclosure risk is acceptable here because the login form itself is not an enumeration vector — it's an internal tool).

**Alternatives considered**: Store a sentinel hash (e.g. `!` prefix like `/etc/shadow`). Rejected: adds indirection with no security benefit; a NULL check is unambiguous.

---

## Decision 9 — Admin role bypass of `apply_scope()`

**Decision**: `role_kind.is_admin = true` for the `admin` role. Auth middleware checks `user_role` rows; if any joined `role_kind` has `is_admin=true`, the `CurrentUser` object sets `is_admin=True`, bypassing `apply_scope()`.

**Rationale**: DATA_MODEL.md specifies this flag. The admin must see all customers/submissions to manage users and force-logout sessions. Bypassing scope at the role level (not the route level) is explicit and auditable.

**Alternatives considered**: Separate admin DB user/connection. Rejected: architectural overhead; the role flag is already in the data model.

---

## Decision 10 — `make wsl-db-rebuild` implementation

**Decision**: `infra/scripts/reset_db.py` connects to SQL Server as SA, drops and recreates `rwb_workbench`, `rwb_exposure`, and `rwb_loss` (never `DATABRIDGE`), then invokes `alembic upgrade 0001` and calls `infra/scripts/seed_db.py`.

**Rationale**: The spec (FR-037, FR-038) requires idempotent rebuild. The existing `infra/scripts/bootstrap_db.py` does not run Alembic or seed. Two new scripts are cleaner than patching bootstrap.

**Alternatives considered**: Extend `bootstrap_db.py`. Rejected: bootstrap is for initial container setup; reset/seed are dev-workflow commands. Separation of concerns.
