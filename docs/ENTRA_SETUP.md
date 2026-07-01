# Entra ID Setup Guide — OIDC Authentication

This guide covers the complete Entra ID configuration for the Risk Workbench OIDC integration.
Steps 1–3 are already complete. Steps 4–7 still need to be done.

**App registration name:** Governance  
**Tenant:** PremiumIQ (PREMIUMIQ.COM)  
**App (client) ID:** e2e1c2d1-c25e-4daa-9faf-65a07ea94460  
**Directory (tenant) ID:** 4dcbd443-2dae-4065-b806-17d9c7781f58

---

## Status

| Step | Description | Status |
|---|---|---|
| 1 | App registered in PremiumIQ tenant | ✅ Done |
| 2 | Client ID and tenant ID noted | ✅ Done |
| 3 | Client secret created | ✅ Done |
| 4 | Token configuration — add `email` optional claim | ⬜ Needed |
| 5 | Front-channel logout URL | ⬜ Production only — requires HTTPS |
| 6 | Assignment required = Yes | ⬜ Needed |
| 7 | Production redirect URI (`https://`) | ⬜ When deploying |

---

## Steps already complete

### Step 1 — App registration

The app is registered as **Governance** in the PremiumIQ tenant, single-tenant, with a Web platform redirect URI.

**Redirect URI configured:** `http://localhost:8000/auth/callback`

`http://` is valid for `localhost` in Entra — this exception is intentional and allows local development without TLS. Any non-localhost production URI must use `https://`.

### Step 2 — Client ID and tenant ID

Both values are noted and set in `infra/.env`:

```
ENTRA_CLIENT_ID=e2e1c2d1-c25e-4daa-9faf-65a07ea94460
ENTRA_TENANT_ID=4dcbd443-2dae-4065-b806-17d9c7781f58
```

### Step 3 — Client secret

A client secret has been created and set in `infra/.env` as `ENTRA_CLIENT_SECRET`.

**Important:** Client secrets expire. When this secret expires, the app will fail silently at the OIDC exchange step (users will see a login error with no obvious cause). Set a calendar reminder for the expiry date.

### Step 4 (already configured) — API permission: User.Read

`User.Read` (delegated, Microsoft Graph) is granted for PremiumIQ. This gives the ID token access to the user's basic profile. The `email` claim still needs to be added separately via Token configuration (Step 4 below) — `User.Read` alone does not guarantee it appears in the ID token.

---

## Steps still needed

### Step 4 — Add `email` optional claim to the ID token

**Why this matters:** The OIDC callback receives an ID token from Entra. The app extracts the `email` claim from that token and uses it to look up or create an `app_user` row. Without the `email` optional claim explicitly added, Entra may omit it from the ID token even though `User.Read` is granted. The app will fail at user matching with a confusing "no email in token" error.

**How to do it:**

1. Go to [Azure Portal → App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **<APPLICATION_NAME>**
3. In the left menu, click **Token configuration**
4. Click **+ Add optional claim**
5. Token type: select **ID** (not Access, not SAML)
6. Check the box next to **email**
7. Click **Add**
8. A prompt will appear: *"To use the email claim, you need to add the openid permission to the application's required permissions."* Click **Turn on the Microsoft Graph email permission (recommended)** if it appears, then click **Add** to confirm

After saving, the ID token returned during login will include an `email` field containing the user's UPN (e.g. `avenugopal@premiumiq.com`).

**Optional — also add `preferred_username`:**

Repeat steps 4–8, choosing `preferred_username` instead of `email`. This gives a display name for the user. Not required for the app to work, but useful for `app_user.display_name` auto-population on first login.

**Verify:** After saving, the Token configuration page should show:

```
Optional claims
ID token
  email
  preferred_username  (if added)
```

---

### Step 5 — Front-channel logout URL

**Skip this step for local development — configure at production deployment only.**

Entra requires HTTPS for the front-channel logout URL. Unlike the redirect URI (where `http://localhost` is a special-cased exception), there is no localhost exception for front-channel logout. Attempting to save `http://localhost:8000/auth/logout` will be rejected by the portal.

**Why this matters (for production):** Front-channel logout is a back-channel notification Entra sends *to your app* when the user's Entra-wide session ends — for example, when they sign out of another app in the tenant. Without it, the Risk Workbench session may persist after an Entra-wide logout. For local dev this scenario never arises (only one app, one session), so the omission has no practical consequence.

**The sign-out flow your app controls still works without this.** When the analyst clicks "Sign out" in the app, the app: (1) invalidates `user_session` locally, (2) clears the cookie, (3) redirects the browser to Entra's logout endpoint with a `post_logout_redirect_uri`. This is the normal sign-out path and does not depend on the front-channel logout URL.

**How to configure it at production deployment:**

1. Go to **App registrations → Governance → Authentication (Preview)**
2. Click the **Settings** tab
3. Find **Front-channel logout URL**
4. Enter: `https://{app_hostname}/auth/logout`
5. Click **Save**

**Note:** `post_logout_redirect_uri` (where Entra sends the browser after its own session is cleared) is passed as a query parameter in the logout redirect from the app code — it is not configured in the portal.

---

### Step 6 — Restrict access: Assignment required = Yes

**Why this matters:** By default, any user in the PremiumIQ tenant can sign in to this app via Entra — even users who have no `app_user` row and no roles assigned. The app handles this correctly (it auto-provisions `app_user` with no roles, so they see an "access denied" page), but it is better to block at the Entra layer so uninvited users never reach the app at all.

**How to do it:**

This setting is on the **Enterprise application**, not the App registration. They are different blades.

1. Go to [Azure Portal → Enterprise applications](https://portal.azure.com/#view/Microsoft_AAD_IAM/StartboardApplicationsMenuBlade/~/AppAppsPreview)
2. Search for **Governance** and click it
3. In the left menu, click **Properties**
4. Find **Assignment required** — set it to **Yes**
5. Click **Save**
6. Now go to **Users and groups** (in the same left menu)
7. Click **+ Add user/group**
8. Select the users or groups who should have access, click **Select**, then **Assign**

After this change, any PremiumIQ tenant user not on the assignment list will receive an Entra error page ("You are not authorized to access this application") before the app ever sees the request.

**For development:** Add yourself (`avenugopal@premiumiq.com`) to the assignment list before enabling this, or you will lock yourself out.

---

### Step 7 — Production redirect URI (when deploying)

When the app is deployed to a production host, add a second redirect URI for `https://`:

1. Go to **App registrations → Governance → Authentication (Preview)**
2. Under **Redirect URI configuration**, click **Add Redirect URI**
3. Platform: **Web**
4. URI: `https://{app_hostname}/auth/callback`
5. Click **Save**

Keep the `http://localhost:8000/auth/callback` entry — it is still needed for local development.

Also update `ENTRA_REDIRECT_URI` in the production environment's `.env` to the `https://` URI.

---

## Environment variables summary

All OIDC variables are in `infra/.env` (never committed to git):

```ini
AUTH_MODE=oidc                          # switch to this when testing OIDC; keep 'password' for default dev

ENTRA_CLIENT_ID=e2e1c2d1-c25e-4daa-9faf-65a07ea94460
ENTRA_TENANT_ID=4dcbd443-2dae-4065-b806-17d9c7781f58
ENTRA_CLIENT_SECRET=<your secret value>
ENTRA_REDIRECT_URI=http://localhost:8000/auth/callback
```

`AUTH_MODE=password` remains the default for local development until OIDC is fully wired in code (Iteration 1). Set `AUTH_MODE=oidc` to test the OIDC flow once Step 4–6 above are complete and the code is implemented.

---

## How the OIDC flow works (reference)

```
Browser                    App (FastAPI)              Entra
  |                            |                        |
  |  GET /auth/login           |                        |
  |--------------------------->|                        |
  |                            | generate PKCE verifier |
  |                            | store state in session |
  |  302 → Entra login URL     |                        |
  |<---------------------------|                        |
  |                                                     |
  |  User enters PremiumIQ credentials                  |
  |---------------------------------------------------->|
  |                                                     |
  |  302 → /auth/callback?code=...&state=...            |
  |<----------------------------------------------------|
  |                            |                        |
  |  GET /auth/callback        |                        |
  |--------------------------->|                        |
  |                            | validate state         |
  |                            | MSAL token exchange    |
  |                            |----------------------->|
  |                            |   ID token             |
  |                            |<-----------------------|
  |                            | extract oid + email    |
  |                            | upsert app_user        |
  |                            | create user_session    |
  |                            | set session cookie     |
  |  302 → / (home)            |                        |
  |<---------------------------|                        |
```

Key security properties:
- The browser never sees the authorization code after it is consumed
- Tokens (ID token, access token) are never stored — discarded after `oid` and `email` are extracted
- The session cookie contains only the session ID (random 64-char hex) — no identity claims
- Authorization (roles, customer access) is read from the WORKBENCH database on every request — never from token claims

---

## Troubleshooting

**"AADSTS50011: The redirect URI specified in the request does not match"**  
The `ENTRA_REDIRECT_URI` in `.env` does not match what is registered in the Entra portal. They must be byte-for-byte identical, including trailing slashes. Current registered value: `http://localhost:8000/auth/callback`.

**"AADSTS700016: Application with identifier was not found"**  
`ENTRA_CLIENT_ID` or `ENTRA_TENANT_ID` is wrong. Verify both against the Overview page of the Governance app registration.

**"No email claim in ID token"**  
Step 4 (Token configuration → add `email` optional claim) has not been completed. The `oid` claim alone is not enough to match an `app_user` by email.

**"AADSTS50105: The signed in user is not assigned to a role"**  
Step 6 (Assignment required = Yes) is enabled but the current user is not in the Users and groups assignment list. Add the user under Enterprise applications → Governance → Users and groups.

**"invalid_client: The provided client secret keys are expired"**  
The client secret has expired. Create a new one in Certificates & secrets, update `ENTRA_CLIENT_SECRET` in `.env`, and restart the app. Calendar the new expiry immediately.

**Redirect loop after login**  
Usually a mismatch between the session cookie domain and the redirect URI host. In development this means the app is being accessed via a hostname other than `localhost` (e.g. `127.0.0.1`). Access it via `http://localhost:8000` exactly.
