# Quickstart & Validation Guide: Application Shell, Navigation & Authentication

**Date**: 2026-07-01  
**Feature**: [spec.md](spec.md) | **Data model**: [data-model.md](data-model.md)

This guide walks through how to verify that Iteration 0 is complete and working. Covers both developer setups (Docker / WSL2 native).

---

## Prerequisites

### Docker setup (Windows / partner)

1. Start everything: `make start`
2. Rebuild schema: `make db-rebuild`

### WSL2 native setup (primary dev)

1. Start SQL Server + Redis: `make wsl-start`
2. Rebuild schema: `make wsl-db-rebuild`
3. Start the app: `make wsl-app` (uvicorn --reload on :8000; keep this terminal open)

### Both setups

`infra/.env` must contain:
- `AUTH_MODE=both`
- `SESSION_SECRET_KEY=<64-char hex>`
- `ENTRA_CLIENT_ID`, `ENTRA_TENANT_ID`, `ENTRA_CLIENT_SECRET` — real Entra values
- `ENTRA_REDIRECT_URI=http://localhost:8000/auth/callback`

Entra email claim must be configured (Step 4 in `docs/ENTRA_SETUP.md`) before OIDC login will work end-to-end.

---

## Health check

```bash
curl http://localhost:8000/api/health
```

Expected:
```json
{"status":"ok","db_workbench":"ok","db_exposure":"ok","db_loss":"ok","redis":"ok","env":"development"}
```

All fields must be `"ok"` before proceeding. If not, check container/service state.

---

## Scenario 1 — Unauthenticated redirect

1. Open `http://localhost:8000` (or `http://localhost` for Docker) with no session cookie
2. Expected: redirected to `/auth/login`
3. Login page shows both password form and "Sign in with Microsoft" button (`AUTH_MODE=both`)
4. Login page does NOT render inside the shell (no rail, no sidebar)

---

## Scenario 2 — Password login → shell → sign-out

1. After `db-rebuild` / `wsl-db-rebuild`, a dev seed user is available (see `infra/scripts/seed_db.py` for credentials)
2. Submit the login form with the seeded credentials
3. Expected: redirected to `/`; shell renders; status bar shows the user's display name
4. Click each of the 7 rail icons — correct sidebar appears; breadcrumb updates; URL changes
5. Navigate directly to `http://localhost:8000/submissions` — deep-link loads correctly
6. Click Account → Sign out
7. Expected: redirected to `/auth/login`; cookie cleared
8. Press browser Back → redirected to `/auth/login` (session is gone)

---

## Scenario 3 — Wrong password

1. Submit the login form with a valid email and wrong password
2. Expected: form re-renders with "Invalid email or password"
3. No session cookie set; message does not reveal whether the email exists

---

## Scenario 4 — Forced password change (John Doe flow)

1. As admin, navigate to Administration → Users → New User
2. Fill in name, email, temporary password → submit
3. Verify `app_user` row created with `must_change_password=1`
4. Sign out; log in as John Doe with the temporary password
5. Expected: immediately redirected to `/auth/change-password`
6. Try navigating to `/submissions` directly → redirected back to `/auth/change-password`
7. Submit a weak password (< 12 chars) → form re-renders with validation errors
8. Submit a valid password (12+ chars, upper, lower, digit) → redirected to home
9. Sign out; log in again with the new password → lands in shell (no forced change)
10. Admin resets John's password → `must_change_password` is set back to `1`

---

## Scenario 5 — OIDC login (requires live Entra)

1. Click "Sign in with Microsoft" on the login page
2. Expected: browser redirects to `login.microsoftonline.com`
3. Authenticate with a `@premiumiq.com` account
4. Expected: callback → shell renders; status bar shows Entra display name
5. `app_user` row has `entra_oid` set, `password_hash` NULL
6. Sign out → local session cleared; Entra logout endpoint hit; redirected to `/auth/login`

---

## Scenario 6 — OIDC JIT provisioning (new user, no role)

1. Use a PremiumIQ Entra account that has no `app_user` row (or delete the existing one)
2. Complete OIDC flow
3. Expected: `app_user` row created with no `user_role` rows; "access pending" screen shown
4. Navigate directly to `/submissions` → still "access pending", not the shell
5. As admin, assign the `analyst` role
6. Sign out; sign in again via OIDC → lands in the shell

---

## Scenario 7 — Session expiry (HTMX-aware)

1. Expire the active session in SQL Server:
   ```sql
   UPDATE user_session
   SET last_active_at = DATEADD(HOUR, -10, GETUTCDATE())
   WHERE id = '<value from rwb_session cookie>';
   ```
2. With the browser open (stale cookie), click a sidebar link (HTMX request)
3. Expected: full-page redirect to `/auth/login` — NOT the login form swapped into the content area
4. Check DevTools → Network: response has `HX-Redirect: /auth/login` header and HTTP 200

---

## Scenario 8 — Schema rebuild idempotency

```bash
# WSL2
make wsl-db-rebuild
make wsl-db-rebuild   # second run must succeed identically

# Docker
make db-rebuild
make db-rebuild
```

`GET /api/health` must be green after both runs.

---

## Running tests

### Unit tests (no SQL Server needed)

```bash
# WSL2
make wsl-test

# Docker
make test
```

### SQL Server integration tests

```bash
# WSL2
make wsl-start       # ensure SQL Server is running
make wsl-test-sql

# Docker
make test-sql
```

---

## What to check if something fails

| Symptom | Check |
|---------|-------|
| Login page shows only one auth option | `AUTH_MODE` in `.env` — must be `both` to show both |
| OIDC callback: "email claim missing" | Step 4 in `docs/ENTRA_SETUP.md` — email optional claim not added |
| OIDC `state` mismatch error | Pre-auth cookie expired (> 5 min); retry. If consistent, check `SESSION_SECRET_KEY` |
| "Invalid email or password" for seeded user | Password stored as plain text? Must be bcrypt hash at cost 12 |
| Shell renders but status bar is empty | `display_name` not set on `app_user` row |
| Rail icon missing | SVG not in `app/static/icons/<name>.svg` |
| HTMX request: login form swapped into content area | `HX-Redirect` missing from expired-session middleware response |
| `db-rebuild` fails on second run | Check `DROP TABLE IF EXISTS` / `IF NOT EXISTS` guards in reset script |
| Workers or poller fail to start | Separate terminals: `make wsl-worker`, `make wsl-poller` (not needed for Iteration 0) |
