# Data Model: Application Shell, Navigation & Authentication

**Date**: 2026-07-01  
**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)  
**Source**: `docs/DATA_MODEL.md §1 (Auth & business spine)` — canonical definitions; this document is the Iteration 0 subset with implementation notes.

All tables live in `rwb_workbench` (WORKBENCH connection). Managed by Alembic revision `0001_initial.py`.

---

## Tables in scope for Iteration 0

### `app_user`

Represents a provisioned user, regardless of auth mode.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `id` | UNIQUEIDENTIFIER | NOT NULL | PK; `NEWID()` default |
| `entra_oid` | NVARCHAR(255) | NULL | Unique when set; identifies OIDC accounts |
| `email` | NVARCHAR(255) | NOT NULL | UNIQUE; case-insensitive lookup on login |
| `display_name` | NVARCHAR(255) | NOT NULL | From Entra `preferred_username` or admin input |
| `password_hash` | NVARCHAR(255) | NULL | bcrypt hash (cost 12); NULL for OIDC accounts |
| `must_change_password` | BIT | NOT NULL | Default 0; set 1 when admin creates/resets password |
| `is_active` | BIT | NOT NULL | Default 1; soft-disable without delete |
| `last_login_at` | DATETIME2 | NULL | Updated on every successful auth |
| `inserted_at` | DATETIME2 | NOT NULL | Server default `GETUTCDATE()` |
| `updated_at` | DATETIME2 | NOT NULL | Bumped on every write |

**Constraints:**
- `UNIQUE(entra_oid)` where `entra_oid IS NOT NULL` (partial unique index)
- `UNIQUE(email)`
- Exactly one of `entra_oid` / `password_hash` is non-NULL for active users — enforced by application logic, not a DB constraint

**Invariant:** `password_hash IS NULL` → OIDC account; `entra_oid IS NULL` → password account.

---

### `user_session`

One row per active login session. The session cookie value is the `id`.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `id` | CHAR(64) | NOT NULL | PK; 32 random bytes encoded as lowercase hex |
| `user_id` | UNIQUEIDENTIFIER | NOT NULL | FK → `app_user.id` |
| `created_at` | DATETIME2 | NOT NULL | Session creation time; basis for absolute timeout |
| `last_active_at` | DATETIME2 | NOT NULL | Updated on every authenticated request; basis for idle timeout |
| `expires_at` | DATETIME2 | NOT NULL | `created_at + SESSION_ABSOLUTE_TIMEOUT` (default 24h); never sliding |
| `invalidated_at` | DATETIME2 | NULL | Set by logout or admin force-logout; session rejected immediately |
| `ip_address` | NVARCHAR(45) | NULL | IPv4 or IPv6; recorded at creation |
| `user_agent` | NVARCHAR(512) | NULL | Truncated to 512 chars |
| `inserted_at` | DATETIME2 | NOT NULL | Server default |

**Session validity check (in order):**
1. `id` exists in table
2. `invalidated_at IS NULL`
3. `last_active_at > NOW() - SESSION_IDLE_TIMEOUT` (default 8h)
4. `expires_at > NOW()`

**After valid check:** `UPDATE user_session SET last_active_at = GETUTCDATE() WHERE id = :id`

---

### `login_attempt`

Append-only audit log. One row per login attempt (success and failure). Never updated.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `id` | UNIQUEIDENTIFIER | NOT NULL | PK |
| `email` | NVARCHAR(255) | NOT NULL | As submitted; not FK (may not match an `app_user`) |
| `auth_mode` | NVARCHAR(16) | NOT NULL | `'password'` or `'oidc'` |
| `success` | BIT | NOT NULL | 1 = authenticated; 0 = failure |
| `failure_reason` | NVARCHAR(255) | NULL | `'invalid_password'`, `'account_not_found'`, `'oidc_state_mismatch'`, etc. |
| `ip_address` | NVARCHAR(45) | NULL | |
| `user_agent` | NVARCHAR(512) | NULL | |
| `at` | DATETIME2 | NOT NULL | Server default `GETUTCDATE()` |

**Note:** Rate limiting (per-email/per-IP lockout) is deferred. The table is created and populated; the lockout gate is not implemented in Iteration 0.

---

### `role_kind`

Kind table: vocabulary of roles. Seeds: `analyst`, `admin`.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `code` | NVARCHAR(50) | NOT NULL | PK (e.g. `'analyst'`, `'admin'`) |
| `label` | NVARCHAR(255) | NOT NULL | Display label |
| `sort_order` | INT | NOT NULL | Ordering in UI |
| `is_admin` | BIT | NOT NULL | Default 0; true → `apply_scope()` bypass |
| `inserted_at` | DATETIME2 | NOT NULL | Server default |

**Seeds (inserted in `0001_initial.py` upgrade):**

| code | label | sort_order | is_admin |
|------|-------|------------|----------|
| `analyst` | Analyst | 10 | 0 |
| `admin` | Administrator | 20 | 1 |

---

### `user_role`

Junction: one row per role assigned to a user. No `user_role` rows → no access (fail-closed).

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `user_id` | UNIQUEIDENTIFIER | NOT NULL | PK part; FK → `app_user.id` |
| `role_code` | NVARCHAR(50) | NOT NULL | PK part; FK → `role_kind.code` |
| `inserted_at` | DATETIME2 | NOT NULL | Server default |
| `inserted_by` | UNIQUEIDENTIFIER | NULL | FK → `app_user.id`; the admin who assigned the role |

**PK:** `(user_id, role_code)` composite.

---

### `user_customer_access`

Junction: customer access scoping per user. Created in this iteration; not populated until Iteration 2.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `user_id` | UNIQUEIDENTIFIER | NOT NULL | PK part; FK → `app_user.id` |
| `customer_id` | UNIQUEIDENTIFIER | NOT NULL | PK part; FK → `customer.id` |
| `inserted_at` | DATETIME2 | NOT NULL | Server default |
| `inserted_by` | UNIQUEIDENTIFIER | NULL | FK → `app_user.id` |

**PK:** `(user_id, customer_id)` composite.

---

## Tables declared but not populated in Iteration 0

The following tables from `docs/DATA_MODEL.md` are created in `0001_initial.py` (so FK references work) but receive no data and have no Iteration 0 UI:

- `customer` — created empty; no UI in Iteration 0
- `program` — created empty; FK to `customer`

These are excluded from this document's detailed definitions; see `docs/DATA_MODEL.md §1` for their full schemas.

---

## Entity relationships (Iteration 0 subset)

```
app_user ──< user_session         (one user → many sessions)
app_user ──< user_role            (one user → many roles)
role_kind ──< user_role           (one role_kind → many assignments)
app_user ──< user_customer_access (one user → many customer access rows)
app_user ──< login_attempt        (via email match; not FK)
```

---

## State transitions

### `user_session` lifecycle

```
[created] → last_active_at updated on each request
          → invalidated_at set on logout or admin force-logout → [rejected]
          → expires_at passed → [rejected]
          → last_active_at + IDLE_TIMEOUT passed → [rejected]
```

Session is NOT event-sourced (Article 4 does not apply — session is not a business lifecycle entity). Direct `UPDATE` on `last_active_at` and `INSERT/UPDATE` on `invalidated_at` are correct.

### `app_user.must_change_password`

```
[true] → user submits new password meeting requirements → [false]
       → admin resets password → [true]
```

Direct `UPDATE` on `must_change_password` is correct — not a status entity; no audit requirement beyond `login_attempt` logging.

---

## Session validation pseudo-code

```python
def validate_session(session_id: str) -> AppUser | None:
    row = db.execute(
        "SELECT s.*, u.* FROM user_session s JOIN app_user u ON s.user_id = u.id "
        "WHERE s.id = :id AND s.invalidated_at IS NULL "
        "  AND s.last_active_at > DATEADD(SECOND, -:idle, GETUTCDATE()) "
        "  AND s.expires_at > GETUTCDATE()",
        {"id": session_id, "idle": SESSION_IDLE_TIMEOUT_SECONDS},
        connection="WORKBENCH",
    )
    if not row:
        return None
    db.execute(
        "UPDATE user_session SET last_active_at = GETUTCDATE() WHERE id = :id",
        {"id": session_id},
        connection="WORKBENCH",
    )
    return build_current_user(row[0])
```
