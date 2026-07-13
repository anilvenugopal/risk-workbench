# Quickstart — Validate Submission & Package (Iteration 1)

A runnable validation guide proving the iteration works end-to-end. It references
[data-model.md](data-model.md) and [contracts/](contracts/) rather than duplicating them.
Implementation detail (SQL bodies, template markup, full test suites) belongs in `tasks.md`
and the implementation phase — not here.

## Prerequisites

- Iteration 0 in place (auth, shell, nav, `db/` package, `0001_initial.py`).
- Dev DB reachable (`make sqlserver-up` for WSL2 native, or `make dev-up` for the full stack).
- `.env` has the `MSSQL_WORKBENCH_*` vars (see `db/README.md`).

## 1. Rebuild the schema (drop-create-seed)

The Iteration-1 tables + kind seeds are folded into the single revision, and the CR-003 dead
tables are removed (data-model §9). Rebuild is **destructive** (dev only):

```bash
make db-rebuild        # drop + recreate 3 app DBs, run 0001_initial, seed
```

**Expected:** no error; `submission`, `submission_crm_id`, `submission_status_event`,
`submission_status_kind`, `treaty_type_kind`, `package`, `submission_package`, `irp_edm`,
`irp_rdm` exist; `customer` / `program` / `user_customer_access` **do not**; seeds present
(`submission_status_kind` = ACTIVE/COMPLETED/CANCELLED; `treaty_type_kind` = 6 provisional codes).

## 2. Unit tests (SQLite — no external deps)

```bash
pytest tests/unit
```

**Expected — new/changed coverage passes** (maps to the contract's test obligations):
- `test_submission_service.py` — create (+ initial ACTIVE event), My/All list, each filter and
  combination, `find_similar` (name-match **and** attribute-match, empty for a new deal),
  status transitions + history, reopen from COMPLETED **and** CANCELLED, same-status no-op,
  read-only gate raises `SubmissionClosed`, stale-write raises `ConcurrencyConflict`,
  no delete function exists.
- `test_package_service.py` — `create_package([])` raises `EmptyPackageError` (FR-024/SC-008);
  `package_member_count` counts across both child tables; one package attaches to two submissions.
- `test_no_scope.py` — `db` exposes no `apply_scope`/`scoped_execute`; no import of `db.scope`
  succeeds; no query string references `customer_id` (SC-010).
- **Cleanup regressions green:** `test_scope.py` is gone; `test_db_package.py` / `test_db_config.py`
  no longer import or test scope; `test_shell_routes.py` no longer asserts a customer count.

## 3. SQL-Server integration tests

```bash
pytest tests/sqlserver --run-sqlserver
```

**Expected:** `test_submission_migration.py` — migration builds all tables/FKs incl. the
self-renewal CHECK; seeds present; the **event-sourced status transaction is atomic**
(`submission_status_event` insert + cached `submission.status_code` stamp commit/rollback
together).

## 4. Manual walkthrough (the analyst's day-to-day)

Log in (dev fixture `admin@example.com`), then:

1. **Create** — `/submissions/new` → enter name, cedant, treaty type, inception → save.
   *Expect:* redirect to detail; status **ACTIVE**; you are the owner. (US1 / SC-001)
2. **My vs All** — land on `/submissions/mine` (default) → your new deal shows. Toggle to
   `/submissions` (All) → still shows, plus deals owned by others; open one owned by another
   analyst → fully viewable. (US2 / SC-002)
3. **Filter** — narrow by cedant, treaty type, inception; combine two → only matching rows. (SC-003)
4. **Duplicate warning** — create a second submission with the **same** name (or same
   cedant+type+inception) → non-blocking "a similar deal already exists" warning → "Create
   anyway" → second deal created with its own id. (US5 / SC-006)
5. **CRM tags** — on an ACTIVE deal, add two CRM ids, edit one, remove one; create a deal with
   none → zero tags is valid. (US4 / SC-007)
6. **Status + read-only gate** — set the deal to **COMPLETED** → history records it; the detail
   view goes read-only; try to edit a field or a CRM tag → **blocked**. **Reopen** → ACTIVE →
   editable again. Separately set another to **CANCELLED** → also read-only → **Reopen** works
   (recovery path). Confirm **no delete** action exists anywhere. (US3 / SC-004/SC-005/SC-012)
7. **Reassign** — hand a deal to another analyst → it leaves your "My Submissions" and appears
   in theirs, still fully visible to everyone. (FR-005a / SC-011)
8. **Concurrency** — open the same deal in two tabs, save an edit in tab 1, then save in tab 2 →
   tab 2 gets a "this deal changed — reload" conflict, not a silent overwrite. (SC-009)

## 5. Package structure (developer-facing this iteration)

No package UI yet (FR-028). Validate via the data-access layer / tests only: a package holds
multiple EDM/RDM members, a zero-member package is rejected, and one package attaches to two
submissions (§2 `test_package_service.py`). Any submission-detail "packages" list is read-only
placeholder.

## Done when

- `make db-rebuild` clean; `pytest tests/unit` and `pytest tests/sqlserver --run-sqlserver` green.
- The manual walkthrough matches the expected outcomes above (SC-001…SC-012).
- No `customer`/`program`/`scope` construct remains in schema, `db/`, or tests (SC-010).
