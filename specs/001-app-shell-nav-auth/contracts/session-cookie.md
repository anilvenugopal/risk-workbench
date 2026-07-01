# Contract: Session Cookie

**Date**: 2026-07-01  
**Feature**: [../spec.md](../spec.md)

---

## Cookie properties

| Property | Value |
|----------|-------|
| Name | `rwb_session` |
| Value | 64-char lowercase hex string (32 random bytes via `secrets.token_hex(32)`) |
| HttpOnly | Yes |
| Secure | Yes (set `True` always; dev uses HTTP but the cookie flag is set) |
| SameSite | `Lax` |
| Path | `/` |
| Max-Age | Not set (session cookie; expires when browser closes) |
| Domain | Not set (host-only) |

**What is NOT in the cookie:** user ID, email, roles, customer access — none. The session ID is the only value. Everything else is looked up from `user_session` + `app_user` + `user_role` on each request.

---

## Pre-auth OIDC state cookie

A separate short-lived signed cookie holds PKCE state during the Entra redirect round-trip.

| Property | Value |
|----------|-------|
| Name | `rwb_oidc_state` |
| Value | `itsdangerous.URLSafeTimedSerializer` signed payload containing `{state, code_verifier}` |
| HttpOnly | Yes |
| Secure | Yes |
| SameSite | `Lax` |
| Max-Age | 300 seconds (5 minutes) |
| Path | `/auth/callback` |

The cookie is deleted immediately after callback processing (success or failure).

---

## Session validation sequence

```
Request arrives
  │
  ├─ Read `rwb_session` cookie value (session_id)
  │   ├─ Missing → unauthenticated; redirect to /login (or HX-Redirect if HTMX)
  │
  ├─ Query user_session WHERE id=:session_id AND invalidated_at IS NULL
  │     AND last_active_at > NOW()-IDLE_TIMEOUT AND expires_at > NOW()
  │   ├─ No row → session expired/invalid; redirect to /login
  │
  ├─ UPDATE user_session SET last_active_at=NOW() WHERE id=:session_id
  │
  ├─ Build CurrentUser from user_session + app_user + user_role rows
  │
  └─ Attach CurrentUser to request.state; call route handler
```

---

## HTMX-aware expiry

When session validation fails (any step above) and `HX-Request: true` header is present:

```
Response(
    status_code=200,
    headers={"HX-Redirect": "/auth/login"},
    content=b"",
)
```

HTMX interprets this as a full-page redirect instruction, NOT a content swap. This prevents the login form from being swapped into a content fragment.

For non-HTMX requests: standard `RedirectResponse("/auth/login", status_code=302)`.
