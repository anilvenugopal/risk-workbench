# Phase 1 — Data Model: Submission & Package (Iteration 1)

Derived from **DATA_MODEL.md §4–§5** (the schema source of truth) and constrained by the spec. This is the concrete set of tables the single `alembic/versions/0001_initial.py` revision creates for Iteration 1, *in addition to* the auth tables Iteration 0 already defines (`app_user`, `role_kind`, `user_role`, `user_session`, `login_attempt`) and *after removing* the CR-003 dead tables (`customer`, `program`, `user_customer_access`).

**Conventions** (DATA_MODEL §2): singular `snake_case`; `id` is a UUID surrogate PK; `*_code` FK → matching `*_kind`; `*_id` FK → entity; entity tables carry `inserted_at`/`updated_at`/`inserted_by`/`updated_by`; kind / junction / event tables carry `inserted_at` (+ `inserted_by` where a user is responsible) only. UUID PKs are generated app-side (`uuid4()`, research R11); `server_default NEWID()` is retained as a fallback. Types shown are the SQL Server types (`DATETIME2`, `NVARCHAR`, `Uuid`, `DATE`, `INT`, `BIT`); the SQLite unit tier maps these via SQLAlchemy.

---

## 1. Kind tables (with seeds)

### `treaty_type_kind` — deal treaty-type vocabulary
| Column | Type | Notes |
|---|---|---|
| `code` | NVARCHAR(50) | **PK** |
| `label` | NVARCHAR(255) | not null |
| `sort_order` | INT | not null |
| `inserted_at` | DATETIME2 | not null, default `GETUTCDATE()` |

**Seed (FR-030 — provisional, pending CIC confirmation):**
`cat_xol` (Cat XoL), `quota_share` (Quota Share), `surplus` (Surplus), `per_risk_xol` (Per-Risk XoL), `aggregate_xol` (Aggregate XoL), `stop_loss` (Stop Loss). Changing this list is a reference-data edit, not a schema change.

### `submission_status_kind` — submission lifecycle vocabulary
| Column | Type | Notes |
|---|---|---|
| `code` | NVARCHAR(50) | **PK** |
| `label` | NVARCHAR(255) | not null |
| `sort_order` | INT | not null |
| `inserted_at` | DATETIME2 | not null, default `GETUTCDATE()` |

**Seed:** `ACTIVE` (10), `COMPLETED` (20), `CANCELLED` (30). Exactly these three (FR-010).

---

## 2. `submission` — the deal (top-level entity)

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | Uuid | PK | app-generated `uuid4()` |
| `assigned_analyst_id` | Uuid | not null | FK → `app_user.id`; **soft owner** (drives "My Submissions" only — never an access gate, Article 6 / FR-005/FR-019) |
| `name` | NVARCHAR(255) | not null | naming-convention label, e.g. `TY2604_AmericanFamily`; **NOT unique** (FR-002/FR-003) — `id` is the key |
| `cedant_name` | NVARCHAR(255) | not null | plain string, autocomplete over existing values (FR-006/R6); no cedant table |
| `treaty_type_code` | NVARCHAR(50) | not null | FK → `treaty_type_kind.code` (FR-008) |
| `inception_date` | DATE | not null | primary filter (FR-021) |
| `treaty_year` | INT | null | parsed from `TY{yy}`; renewal-year grouping (R10) |
| `renews_from_submission_id` | Uuid | null | FK → `submission.id` (self-ref); manual renewal link (FR-007) |
| `directory_path` | NVARCHAR(1024) | null | per-deal shared-drive staging dir |
| `status_code` | NVARCHAR(50) | not null | FK → `submission_status_kind.code`; **cached current** (Article 4); default `ACTIVE` on create |
| `inserted_at` | DATETIME2 | not null | default `GETUTCDATE()` |
| `updated_at` | DATETIME2 | not null | default `GETUTCDATE()`; **optimistic-concurrency version marker** (R1 / FR-031) |
| `inserted_by` | Uuid | null | FK → `app_user.id` |
| `updated_by` | Uuid | null | FK → `app_user.id` |

**Constraints:**
- FK `assigned_analyst_id`, `inserted_by`, `updated_by`, `renews_from_submission_id` (self), `treaty_type_code`, `status_code`.
- `CHECK (renews_from_submission_id IS NULL OR renews_from_submission_id <> id)` — no self-renewal (FR-007 / R9).
- **No** `UNIQUE(name)` (FR-003 — the CR-003-era uniqueness is dropped) and **no** `customer_id`/scope column (Article 6).

**Indexes:** `assigned_analyst_id` (My filter), `cedant_name` (filter + autocomplete `DISTINCT`), `treaty_type_code`, `inception_date`.

**State model** (`status_code`): `ACTIVE ⇄ COMPLETED`, `ACTIVE ⇄ CANCELLED` — every edge allowed, no precondition (FR-011/FR-012); reopen from either closed state (FR-011). No `DELETE` path exists (FR-014). Written only via the event-sourced transaction (§4 below).

---

## 3. `submission_crm_id` — CRM-ID tag set (0..N per submission)

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | Uuid | PK | app-generated |
| `submission_id` | Uuid | not null | FK → `submission.id` |
| `crm_id` | NVARCHAR(255) | not null | plain, **unvalidated** free text (FR-018); blank/whitespace not stored (Edge Cases) |
| `inserted_at` | DATETIME2 | not null | default `GETUTCDATE()` |
| `inserted_by` | Uuid | null | FK → `app_user.id` |

- Zero tags is valid (FR-016/FR-018). Duplicate identical tags on one deal are permitted (unvalidated by design). Add/edit/remove allowed only while the parent submission is `ACTIVE` (FR-017/FR-015 via the R3 gate). Append-only inserts are concurrency-exempt (R1).

---

## 4. `submission_status_event` — append-only status history

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | Uuid | PK | app-generated |
| `submission_id` | Uuid | not null | FK → `submission.id` |
| `status_code` | NVARCHAR(50) | not null | FK → `submission_status_kind.code`; the status transitioned **to** |
| `reason` | NVARCHAR(1024) | null | free text, mainly for `CANCELLED` |
| `at` | DATETIME2 | not null | default `GETUTCDATE()` |
| `inserted_by` | Uuid | null | FK → `app_user.id` |

**Event-sourced write (Article 4 / R2):** every status change is one transaction — `INSERT submission_status_event` **and** `UPDATE submission SET status_code=…, updated_at=…` — opened with `get_connection("WORKBENCH")` + explicit `conn.begin()`. Never `execute_command`. History is immutable and never overwritten (FR-013 / SC-004). Creation writes the initial `ACTIVE` event in the same transaction as the `submission` insert.

---

## 5. `package` — bundle of EDM/RDM members (structure only)

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | Uuid | PK | app-generated |
| `name` | NVARCHAR(255) | null | optional bundle label |
| `deleted_at` | DATETIME2 | null | **soft delete** (FR-027) |
| `inserted_at` | DATETIME2 | not null | default `GETUTCDATE()` |
| `updated_at` | DATETIME2 | not null | default `GETUTCDATE()` |
| `inserted_by` | Uuid | null | FK → `app_user.id` |
| `updated_by` | Uuid | null | FK → `app_user.id` |

- **No** `edm_id`/`rdm_id` on the package (DATA_MODEL §4) — membership lives on the child rows. **No** status column (members carry their own).
- **≥1-member invariant** is **app-enforced** (FR-024 / R5), not a column CHECK — membership spans two tables. Verified by a unit test.

---

## 6. `submission_package` — deal ↔ package M:N join

| Column | Type | Null | Notes |
|---|---|---|---|
| `submission_id` | Uuid | not null | FK → `submission.id` |
| `package_id` | Uuid | not null | FK → `package.id` |
| `inserted_at` | DATETIME2 | not null | default `GETUTCDATE()` |
| `inserted_by` | Uuid | null | FK → `app_user.id` |

- **Composite PK `(submission_id, package_id)`** (DATA_MODEL §12). A package may attach to many submissions and a submission hold many packages (FR-025 / SC-008).

---

## 7. `irp_edm` / `irp_rdm` — member tables (schema only this iteration)

Created with their full DATA_MODEL §5 column shape so the `package_id` FK target and the canonical schema exist now; **only `id`, `name`, and `package_id` are exercised this iteration.** All import / Risk Modeler entity management, status transitions, and the other columns are inert until Iteration 2 (FR-026/FR-028). No `irp_job`/`irp_analysis`/`irp_portfolio`/`irp_treaty` tables are created this iteration (Iteration 2+).

### `irp_edm`
| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | Uuid | PK | app-generated |
| `package_id` | Uuid | null | FK → `package.id` — **bundle membership** (FR-023/FR-026: an EDM may have no package) |
| `source_file_path` | NVARCHAR(1024) | null | `.bak/.mdf/.csv` origin |
| `name` | NVARCHAR(255) | not null | IRP EDM name |
| `irp_id` | INT | null | backfilled by poller (Iteration 2) |
| `created_by_irp_job_irp_id` | NVARCHAR(64) | null | creating job's IRP id (string; no FK) |
| `as_of` | DATETIME2 | null | drift signal |
| `server_name` | NVARCHAR(255) | null | IRP DataBridge server |
| `status` | NVARCHAR(50) | null | **plain string** (Article 3 carve-out — mirrors IRP EDM lifecycle); unused this iteration |
| `deleted_at` | DATETIME2 | null | soft delete |
| `inserted_at`/`updated_at` | DATETIME2 | not null | defaults `GETUTCDATE()` |
| `inserted_by`/`updated_by` | Uuid | null | FK → `app_user.id` |

### `irp_rdm`
| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | Uuid | PK | app-generated |
| `package_id` | Uuid | null | FK → `package.id` — bundle membership (FR-023/FR-026) |
| `source_file_path` | NVARCHAR(1024) | null | origin file |
| `name` | NVARCHAR(255) | not null | IRP RDM name |
| `irp_id` | INT | null | backfilled by poller (Iteration 2) |
| `created_by_irp_job_irp_id` | NVARCHAR(64) | null | no FK |
| `as_of` | DATETIME2 | null | drift signal |
| `status` | NVARCHAR(50) | null | **plain string** (Article 3 carve-out — combined rollup of apply jobs); unused this iteration. **No `edm_id`** (DATA_MODEL §5) |
| `deleted_at` | DATETIME2 | null | soft delete |
| `inserted_at`/`updated_at` | DATETIME2 | not null | defaults `GETUTCDATE()` |
| `inserted_by`/`updated_by` | Uuid | null | FK → `app_user.id` |

---

## 8. Relationships (Iteration-1 subset)

```text
app_user 1──∞ submission            (assigned_analyst_id — soft owner)
submission 1──∞ submission_status_event   (status history; append-only)
submission 1──∞ submission_crm_id         (0..N CRM tags)
submission ∞──1 treaty_type_kind          (treaty_type_code)
submission ∞──1 submission_status_kind    (status_code — cached current)
submission 0..1─∞ submission              (renews_from_submission_id — self-ref)
submission ∞──∞ package                   (via submission_package, composite PK)
package 1──∞ irp_edm                      (package_id — nullable; ≥1 member app-enforced across both)
package 1──∞ irp_rdm                      (package_id — nullable)
```

No `customer`/`program` tier above `submission`; no `customer_id` anywhere (Article 6).

---

## 9. Migration & seed impact (single revision — drop-create-seed)

**`alembic/versions/0001_initial.py`** — in the one existing revision:
- **Remove:** `customer`, `program`, `user_customer_access` creates + their downgrade drops (FR-032).
- **Add creates:** `treaty_type_kind`, `submission_status_kind`, `submission`, `submission_crm_id`, `submission_status_event`, `package`, `submission_package`, `irp_edm`, `irp_rdm` (order respects FK dependencies: kinds → `package` → `submission` → children/join → `irp_edm`/`irp_rdm`).
- **Add seeds (in-migration, mirroring the `role_kind` seed):** `submission_status_kind` (ACTIVE/COMPLETED/CANCELLED); `treaty_type_kind` (the six provisional codes).
- **Downgrade:** drop the new tables in reverse FK order.

**`infra/scripts/seed_db.py`** — add idempotent `MERGE` seeds for `submission_status_kind` and `treaty_type_kind` (same pattern as the existing `role_kind` MERGE), so a re-seed without a full rebuild stays correct.

**Dev DB strategy:** Rebuild (`make db-rebuild`) — drop, recreate, migrate, seed. No incremental migration (single revision until production cutover).
