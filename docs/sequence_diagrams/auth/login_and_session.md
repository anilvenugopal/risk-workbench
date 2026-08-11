# Execution Flow — Sign In, Stay In, Sign Out (001 US1 / US2 / US5)

Two ways in — **Microsoft (OIDC)** for real deployments and a **password** fallback that is
gated to non-production — and one way out. Once in, every subsequent request is validated by
middleware that knows how to redirect an HTMX request as well as a browser navigation.

**Purely workbench.** The only external system is the identity provider, and only during the
OIDC round trip.

Code: `auth.login_submit` / `oidc_login` / `oidc_callback` / `logout`;
`app.auth.middleware.SessionMiddleware`; `auth_service.create_session` / `validate_session` /
`invalidate_session`.

**Classification:** entirely **sync**. No RM call, no `rwb_job`, no worker, no poller.

## Records written

| Action | # | Table | Row / change | Process |
|---|---|---|---|---|
| Password login | 1 | `login_attempt` | INSERT — email, method, success/failure + reason, IP, user-agent (**every attempt, including failures**) | 🟦 request |
| Password login | 1a | `login_attempt` | ⚠️ INSERT — a *second* failure row when the email is unknown; `login_submit` calls `fail("account_not_found")` twice. Current behaviour, not intent — see the boundary note below | 🟦 request |
| Password login | 2 | `app_user` | UPDATE `last_login_at` | 🟦 request |
| Password login | 3 | `user_session` | INSERT — the session; the cookie carries **only its id** | 🟦 request |
| OIDC callback | 1 | `app_user` | UPDATE — link `entra_oid` to an admin-pre-provisioned record matched by email (only when its `entra_oid` is NULL) | 🟦 request |
| OIDC callback | 2 | `app_user` | INSERT — **JIT provision** when no record matches at all; **`user_role` is deliberately NOT written** — the missing role is what makes gate 3 fail closed (see [user administration](user_administration.md)) | 🟦 request |
| OIDC callback | 3 | `login_attempt` + `app_user.last_login_at` + `user_session` | as above | 🟦 request |
| Logout | 1 | `user_session` | UPDATE — invalidated; the cookie is cleared and the IdP logout URL is built | 🟦 request |

## Sequence — the two ways in

```mermaid
sequenceDiagram
    actor User
    participant App as App (route)
    participant DB as WORKBENCH DB
    participant IdP as Microsoft Entra

    rect rgb(238,244,255)
        Note over User,DB: PASSWORD — a gated v1 fallback, never reachable in production
        User->>App: POST /auth/login (email, password, CSRF)
        App->>DB: SELECT app_user by email
        App->>App: verify_password (hash compare)
        alt bad credentials
            App->>DB: INSERT login_attempt (failure + reason)
            Note over App,DB: ⚠️ unknown email writes TWO rows, and skips verify_password<br/>entirely — a timing oracle the duplicate call was meant to close
            App-->>User: 200 — the form with a generic error
        else ok
            App->>DB: INSERT login_attempt (success) + UPDATE last_login_at
            App->>DB: INSERT user_session
            App-->>User: 302 → next, Set-Cookie rwb_session=<session id ONLY>
        end
    end

    rect rgb(238,244,255)
        Note over User,IdP: OIDC — the real path
        User->>App: GET /auth/oidc-login
        App->>App: initiate_flow — build the authorize URL
        App-->>User: 302 → Entra, Set-Cookie rwb_oidc_state (signed, 5-min max age)
        User->>IdP: authenticate
        IdP-->>User: 302 → /auth/callback?code=…&state=…
        User->>App: GET /auth/callback
        App->>App: unseal rwb_oidc_state
        alt cookie missing / signature bad / older than 5 min
            App-->>User: 302 → /auth/login?error=state_missing|state_expired
            Note over App,DB: _abort — app log only, NO login_attempt row
        end
        App->>IdP: complete_flow — exchange the code for tokens
        alt exchange failed or no email claim
            App-->>User: 302 → /auth/login?error=…
            Note over App,DB: _abort — app log only, NO login_attempt row
        end
        IdP-->>App: claims (oid, email, name)
        App->>DB: SELECT app_user by entra_oid
        alt no OID match but an email match with entra_oid IS NULL
            App->>DB: UPDATE app_user — link entra_oid (admin pre-provisioned)
        end
        alt user known
            App->>DB: UPDATE last_login_at + INSERT login_attempt + INSERT user_session
            App-->>User: 302 → /   (rwb_oidc_state deleted)
        else nobody matches
            App->>DB: jit_provision_oidc_user — INSERT app_user (NO role)
            App->>DB: INSERT login_attempt + INSERT user_session
            App-->>User: 302 → /auth/access-pending
        end
    end
```

## Sequence — every request after that

```mermaid
sequenceDiagram
    actor User
    participant MW as SessionMiddleware
    participant App as App (route)
    participant DB as WORKBENCH DB

    rect rgb(238,244,255)
        Note over User,DB: THREE GATES, in order, on every non-public request
        User->>MW: any request
        alt /static/* or a public auth path or /api/health
            MW->>App: pass straight through
        else
            MW->>MW: read the rwb_session cookie
            alt no cookie
                MW-->>User: HTMX → 200 + HX-Redirect:/auth/login?next=… · else 302
            end
            MW->>DB: validate_session — idle timeout, absolute timeout, invalidated?
            alt invalid or expired
                MW-->>User: HX-Redirect / 302 → /auth/login
            end
            alt gate 2 — must_change_password
                MW-->>User: → /auth/change-password (logout + that page exempt)
            end
            alt gate 3 — no roles assigned
                MW-->>User: → /auth/access-pending (fail CLOSED)
            end
            MW->>App: request.state.user = CurrentUser (+ user_id on the access log)
        end
    end

    rect rgb(238,244,255)
        Note over User,DB: SIGN OUT
        User->>App: POST /auth/logout (CSRF)
        App->>DB: invalidate_session
        App->>App: build the IdP logout URL
        App-->>User: 302 → IdP logout, cookie cleared — Back button cannot resume
    end
```

---

**Boundaries worth noting**

- **The cookie contains a session id and nothing else** (Article 13). No claims, no roles, no
  user id — so every request re-reads the session and the user's current roles, and an admin's
  role change or force-logout takes effect on the *next* request rather than the next login.
- **HTMX changes how a redirect has to be expressed.** A 302 to an HTMX request would be
  followed by the browser and swapped into a fragment — so the middleware returns **200 with
  `HX-Redirect`** instead, and the client navigates. Every auth redirect in the app goes through
  the one `_redirect_response` helper for exactly this reason (001 US5).
- **The role gate fails closed.** A JIT-provisioned OIDC user is authenticated but has no role,
  so gate 3 pins them to `/auth/access-pending` until an admin assigns one. Being able to sign in
  is not being able to do anything. `jit_provision_oidc_user` writes `app_user` and **nothing
  else** — the absent `user_role` row *is* the mechanism.
- **The unknown-email branch is doubly wrong today.** `login_submit` calls
  `fail("account_not_found")` twice (the first return value is discarded), so an unknown email
  writes two `login_attempt` rows. The comment on that line says the extra call exists to
  prevent a timing oracle, but `fail()` never verifies a hash — so a known email still costs a
  full password verify and an unknown one does not. Documented here as current behaviour, not
  as intent.
- **The gates are ordered, and the exemptions are minimal.** `must_change_password` is checked
  before roles, and the only paths exempt from both are logout, change-password, and
  access-pending — enough to get out or get fixed, nothing else.
- **`AUTH_MODE=password` is a gated v1 fallback and must never be reachable in production.** It
  exists so the app can run without an IdP in dev; the OIDC path is the real one.
- **Every *password* attempt and every *successful* OIDC callback is recorded**, with IP and
  user-agent. That is the audit surface for "who tried to get in" — but it has a hole: the
  `_abort` paths in `oidc_callback` (state missing/expired, token exchange failed, no email
  claim) log to the **application logger only** and write no `login_attempt` row. A failed
  OIDC sign-in therefore leaves no database evidence.
- **The OIDC state cookie is signed and short-lived** (5 minutes, `itsdangerous`), and is deleted
  on every exit from the callback — success, provision, or abort. A replayed or stale callback
  aborts to the login page with a named reason rather than half-completing.
- **Logout invalidates server-side, not just client-side.** Clearing the cookie alone would leave
  a valid session id in the database; `invalidate_session` is what makes the Back button useless
  (001 US1).
