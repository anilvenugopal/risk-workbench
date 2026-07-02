# User Provisioning Guide

## CLI Tool

All provisioning actions below can be performed via the interactive CLI:

```bash
make wsl-user-setup          # WSL2 / local
./infra/scripts/run_user_setup   # directly, or on the production server
```

The menu has four options:

| Option      | What it does |
|-------------|--------------|
| `provision` | Select an access-pending OIDC user and assign them a role |
| `create`    | Create a new user — OIDC pre-provisioned or password account |
| `reset`     | Reset the password for a password-auth user |
| `list`      | Show all users and their current roles |

The tool reads `infra/.env` for database credentials and works identically in WSL2 and on the production server (where `.env` holds production values).

---

This document covers the three ways a user can be provisioned in Risk Workbench, and the manual SQL steps needed to bootstrap the very first admin account.

---

## Bootstrapping the First Admin (Manual SQL)

Before any admin exists in the application, you must promote the first user directly in the database. This is a one-time setup step.

**Connection details:**

| Field    | Value                        |
|----------|------------------------------|
| Host     | `localhost`                  |
| Port     | `1433`                       |
| Database | `rwb_workbench`              |
| Username | `sa`                         |
| Password | `Clarity0821!`               |
| Driver   | SQL Server (jTDS or Microsoft) |

**Steps:**

1. The target user must first sign in via Microsoft Entra (OIDC) so their `app_user` row is created.
2. Connect to the database and run:

```sql
DECLARE @uid UNIQUEIDENTIFIER = (
    SELECT id FROM app_user WHERE email = 'user@example.com'
);
INSERT INTO user_role (user_id, role_code, inserted_by)
VALUES (@uid, 'admin', @uid);
```

Replace `user@example.com` with the Entra email of the user to promote.

3. On their next page load or sign-in, the user will have admin access.

> Once at least one admin exists, all subsequent provisioning can be done through the **Administration → Users** UI.

---

## Flow 1: Admin Pre-Provisions an OIDC User

Use this flow for all PremiumIQ / Microsoft Entra users. The admin sets up the account before the user ever signs in.

**Steps (Admin):**

1. Go to **Administration → Users → New User**.
2. Under **Microsoft Entra (OIDC) account**, enter:
   - Display name
   - Email address (must exactly match the user's Entra / Microsoft account email)
   - Role (`analyst` or `admin`)
3. Click **Provision OIDC user**.

**What happens on the user's first sign-in:**

1. User clicks **Sign in with Microsoft** and authenticates with Entra.
2. The callback looks up the user by Entra OID. If not found, it falls back to email.
3. The pre-provisioned record is found by email; the Entra OID is linked to it.
4. The user lands directly in the shell — no "access pending" screen.

**Idempotent:** Running the provisioning form a second time for the same email address is safe — the existing record is used and the role assignment is not duplicated.

---

## Flow 2: JIT OIDC Sign-In (No Pre-Provisioning)

If a user signs in with Microsoft and no matching record exists (by OID or email), an account is automatically created with no roles.

**What the user sees:**

- An "Access pending" page appears immediately after sign-in.
- The user can complete their profile (display name, optional password) while waiting.
- They cannot access any application content until an admin assigns a role.

**Steps (Admin):**

1. Go to **Administration → Users**.
2. Find the user (they appear with `roles: none`).
3. Click their row → **Assign role**.

The user will gain access on their next request (no re-login required if their session is still active).

---

## Flow 3: Admin Creates a Password Account

For local or service accounts that do not use Microsoft Entra.

**Steps (Admin):**

1. Go to **Administration → Users → New User**.
2. Under **Password account**, enter display name, email, and an initial password.
3. Click **Create password user**.

The account is created with `must_change_password = true`. On first login, the user is redirected to a change-password screen before accessing anything else.

**Password requirements:** minimum 12 characters, at least one uppercase letter, one lowercase letter, and one number.

---

## User Self-Service (Access Pending)

While waiting for role assignment (Flow 2), users can update their profile:

- **Display name** — how their name appears in the shell status bar
- **Password** (optional) — sets a password fallback if `AUTH_MODE=password` is enabled

This is available on the `/auth/access-pending` page immediately after OIDC sign-in.

---

## Role Reference

| Code      | Label         | Access level          |
|-----------|---------------|-----------------------|
| `analyst` | Analyst       | Standard application access |
| `admin`   | Administrator | All content + Administration section |

A user can hold multiple roles. The highest-privilege role is displayed in the status bar.
