# Phase 0 — Research: Submission & Package Domain Model (Iteration 1)

**No Technical Context unknowns.** The stack, dev workflow, DB layer, test tiers, and project structure are all inherited unchanged from Iteration 0 (`specs/001-app-shell-nav-auth/plan.md`) and the constitution. There were therefore **zero `NEEDS CLARIFICATION` markers** to resolve. This document instead records the concrete design decisions the implementation hangs on, each in Decision / Rationale / Alternatives form, plus the full cleanup surface (R8) — which is materially wider than the spec's enumerated FR-032 list.

---

## R1 — Optimistic concurrency on `submission`

**Decision**: Use `submission.updated_at` (a `DATETIME2` stamped on every write) as the version marker. The detail/edit view carries the value it read; every `UPDATE` is `... WHERE id = :id AND updated_at = :read_value`, and the write also sets `updated_at` to a fresh `GETUTCDATE()`/app timestamp. `rowcount == 0` ⇒ someone else wrote first ⇒ raise a typed `ConcurrencyConflict` the route turns into a non-destructive "this deal changed since you opened it — reload and re-apply" banner (HTTP 409 for HTMX; the user's input is preserved for re-entry).

**Rationale**: This is exactly the mechanism DATA_MODEL §2 prescribes for analyst-editable rows; it needs no extra column and composes with the existing `db.execute_command` rowcount return. It satisfies FR-031 / SC-009 (no silent lost update) without pessimistic locking.

**Alternatives considered**: (a) A dedicated integer `version` column — rejected: `updated_at` already exists and DATA_MODEL names it the marker. (b) Last-write-wins — rejected: violates FR-031. (c) Row locks / `SELECT ... FOR UPDATE` — rejected: doesn't fit the stateless request/HTMX model and adds contention.

**Applies to**: `submission` field edits, owner reassignment, and CRM-tag mutations that are gated by submission state. Append-only inserts (`submission_status_event`, `submission_crm_id` adds) are exempt (DATA_MODEL §2).

---

## R2 — Event-sourced status write pattern

**Decision**: A status change is a single transaction opened with `get_connection("WORKBENCH")` + explicit `conn.begin()`: (1) `INSERT submission_status_event (submission_id, status_code, reason, inserted_by, at)`; (2) `UPDATE submission SET status_code = :new, updated_at = :now WHERE id = :id AND updated_at = :read_value`. Both statements share the one transaction; a rollback on either leaves current + history consistent. `execute_command` (single-statement autocommit) MUST NOT be used here.

**Reopen semantics**: `COMPLETED → ACTIVE` and `CANCELLED → ACTIVE` are ordinary transitions recorded as further events (FR-011). No transition precondition is ever checked (FR-012). Setting a submission to its current status is recorded as a no-change event and never errors (spec Edge Cases).

**Rationale**: Mandated verbatim by Article 4 and DATA_MODEL §2/§32. Keeps O(1) reads off the cached `status_code` while preserving a lossless audit trail (FR-013 / SC-004).

**Alternatives considered**: (a) In-place `UPDATE submission.status_code` only — rejected: no history, violates Article 4. (b) Recompute current status from events on read — rejected: Article 4 forbids recompute-on-read in the hot path. (c) Trigger-maintained cache — rejected: hidden control flow; the two-statement transaction is explicit and testable.

**The status change also re-checks the optimistic-concurrency marker** (R1) so a status flip can't silently clobber a concurrent field edit.

---

## R3 — Read-only gate for closed submissions

**Decision**: Enforce the "COMPLETED/CANCELLED ⇒ read-only" rule (FR-015, clarified) **server-side in the service layer**, not merely in the UI. Every mutating service function (`update_submission`, `reassign_owner`, CRM add/edit/remove, and — in Iteration 2 — package create/sync/delete) first loads the current `status_code` and raises a typed `SubmissionClosed` error unless status is `ACTIVE`. The only state-changing operation permitted on a non-ACTIVE submission is `set_status(... → ACTIVE)` (reopen). Templates additionally hide/disable edit affordances when status != ACTIVE, but the server check is authoritative.

**Rationale**: FR-015/SC-012 require the gate to be enforced *this* iteration for fields and CRM tags (the PRD was reconciled to this "fully read-only" reading). Enforcing in the service layer means the HTTP layer, tests, and any future caller all inherit it; UI-only hiding would be bypassable.

**Alternatives considered**: (a) UI-only disable — rejected: not a real gate; fails SC-012's "100% blocked". (b) A DB CHECK/trigger — rejected: the rule is about *who may act when*, not a data-shape invariant; belongs in the service layer with the concurrency check.

---

## R4 — Non-blocking duplicate warning

**Decision**: On create and on rename, run one lookup that returns existing submissions matching **either** the same `name` **or** the same (`cedant_name`, `treaty_type_code`, `inception_date`) triple (FR-004, clarified). If any match, the response carries a non-blocking warning fragment listing the look-alikes; the analyst confirms and the write proceeds unchanged. The warning never blocks and never mangles the name. Implemented as a two-step HTMX flow: the first submit returns the warning partial with a "Create anyway" confirm; a hidden `confirmed=1` field (or an Alpine confirm) lets the second submit skip re-warning. A brand-new submission with no matches saves on the first submit.

**Rationale**: Matches DATA_MODEL §4 (same UX as the EDM/RDM name-collision check) and the spec's US5 / SC-006 (warn in 100% of look-alike cases, block in 0%). Keying the "attribute" arm on cedant+type+inception (not name) catches the real peak-season case where two genuine deals collide on attributes but the analyst hasn't named them identically.

**Alternatives considered**: (a) Hard uniqueness on name — rejected: FR-003 forbids it; surrogate `id` is the key. (b) Warn on name only — rejected: misses attribute-only look-alikes (US5). (c) Auto-suffixing the name — rejected: the spec explicitly wants no label-mangling.

---

## R5 — Package ≥1-member invariant (app-enforced)

**Decision**: The "a package always has ≥1 member" rule (FR-024) is enforced in `package_service`, not by a column CHECK, because membership spans two child tables (`irp_edm.package_id` + `irp_rdm.package_id`). Concretely: `create_package(members=[...])` requires a non-empty member list and writes the package + stamps `package_id` on each member in one transaction; a member-removal that would empty the package is rejected (or soft-deletes the package, per R-note below). A helper `package_member_count(package_id)` sums both child tables. This invariant is covered by a dedicated unit test (FR-024 / SC-008).

**Empty-package handling**: Persisting a package with zero members is rejected outright. Removing the last member is treated as a soft-delete of the package (`deleted_at` stamped) rather than leaving a zero-member row — consistent with the no-hard-delete posture (FR-027). The precise "remove last member" affordance is Iteration-2 behavior; this iteration only needs the invariant + the count helper + tests.

**Rationale**: DATA_MODEL §4 states this explicitly ("app-enforced invariant, not a column CHECK"). A single two-column CHECK cannot express membership across two tables.

**Alternatives considered**: (a) A CHECK constraint — impossible across two tables. (b) A DB trigger counting both tables — rejected: hidden logic, hard to unit-test on SQLite; the service-layer function is directly testable in the unit tier.

---

## R6 — Cedant autocomplete without a cedant table

**Decision**: `cedant_name` stays a plain `NVARCHAR` on `submission` (no cedant registry, DATA_MODEL §4). Autocomplete is a `SELECT DISTINCT cedant_name FROM submission WHERE cedant_name LIKE :prefix + '%' ORDER BY cedant_name` served to an HTMX-driven suggestion list (or a `<datalist>` for the JS-off path). Purely a consistency aid; it introduces no entity.

**Rationale**: FR-006 requires autocomplete over existing values *without* a separate registry. `DISTINCT` over the column is the whole mechanism.

**Alternatives considered**: A `cedant` table with FK — rejected explicitly by DATA_MODEL §4 and the spec ("deliberately not its own table").

---

## R7 — Owner-filtered list, defaulting to the current analyst (no RLS)

**Decision**: `GET /submissions` computes rows with an ordinary predicate: `WHERE assigned_analyst_id = :owner`, or no owner predicate at all. The `owner` query parameter carries an `app_user.id` and picks between them — absent means the signed-in analyst, empty means every owner. There is **no scope wrapper** and no admin bypass: every analyst can list every deal and open any of them (FR-019/FR-020, SC-002). Reassignment (FR-005a) is a plain `UPDATE assigned_analyst_id` (gated by R3 + R1), which moves the deal between owner filters without touching visibility (SC-011).

**Rationale**: Article 6 — `assigned_analyst_id` is a soft filter, never an access gate. Building it as a plain predicate (not via the removed `apply_scope`) is the whole point of the CR-003 cleanup. Filtering on the id rather than `app_user.display_name` is what keeps two analysts with the same name apart.

**Alternatives considered**: Reusing `apply_scope` with `assigned_analyst_id` — rejected: that helper is being deleted, and re-introducing a scope wrapper (even a soft one) invites the RLS pattern back. A plain `WHERE` is clearer and honestly non-restrictive. A separate `/submissions/mine` route with a My/All toggle — built first, then removed: once the Owner filter listed every analyst, the route and the toggle were a second control over the same predicate, and the two disagreed on `/submissions/mine` (route owner AND picked owner returned nothing).

---

## R8 — Cleanup surface (FR-032/FR-033) — **wider than the spec enumerated**

**Decision**: Remove every reference to the CR-003 dead constructs, then rebuild drop-create-seed. The spec's FR-032 named: the `customer`/`program`/`user_customer_access` tables, `db/scope.py`, `tests/unit/test_scope.py`, the `apply_scope`/`scoped_execute` exports in `db/__init__.py`, `db/README.md`, and `tests/unit/test_db_package.py`. A codebase grep found **additional live references that would break at rebuild** and must be in scope:

| File | What references the dead construct | Action |
|---|---|---|
| `alembic/versions/0001_initial.py` | creates `customer`, `program`, `user_customer_access` (+ downgrade drops) | drop those creates; add Iteration-1 tables |
| `db/scope.py` | the helper itself | **delete** |
| `db/__init__.py` | `from .scope import ...`, `__all__` entries, docstring example (`scoped_execute`, `customer_ids`) | remove import, exports, fix docstring |
| `db/execute.py` | docstring examples reference `apply_scope`, `customer_id`, `customer ids` | reword docstring (cosmetic but avoids dangling refs) |
| `db/README.md` | `db.scope` module row, RLS `scoped_execute` example, `scope.py` file listing, "multi-tenant tables" line | remove all |
| `tests/unit/test_scope.py` | entire file tests `apply_scope`/`scoped_execute` | **delete** |
| `tests/unit/test_db_package.py` | imports `apply_scope, scoped_execute`; scope test block; `customer_id` fixture column | drop the imports + scope tests; keep the safe-path tests |
| `tests/unit/test_db_config.py` | an `apply_scope` test block (lines ~120–162) | **remove that block** |
| `app/routers/shell.py` | `home()` runs `SELECT COUNT(*) FROM customer` | replace with a submission count (or drop the stat) |
| `app/templates/pages/home.html` | renders `customer_count` | update to match the shell.py change |
| `tests/unit/test_shell_routes.py` | `test_customer_count_in_page` asserts the customer count | retarget to the submission count or remove |

**Explicitly OUT of cleanup scope**: `reference/` (`serve.py`, `mock_data.py`) — this is the runnable clickable UX mock (the constitution's "mock" source-of-truth reference), not application code. Its in-memory "customer/program" fields are illustrative and touch no database. Leave it untouched.

**Rationale**: FR-032 says "no customer tier and no row-scoping mechanism remains **anywhere in the codebase**" and FR-033 says the data-access layer must "expose no customer/program/scope constructs." A partial removal that left `shell.py`'s `customer` query or the `test_db_config.py` scope block would fail the rebuild or the unit suite. SC-010 is verified partly by the *absence* of these constructs, so the sweep must be complete.

**Alternatives considered**: Removing only the spec-enumerated files — rejected: `make db-rebuild` would drop `customer` while `home()` still queries it (500 on the home page), and `pytest tests/unit` would fail on the orphaned scope tests. The grep-verified list is the real surface.

---

## R9 — Renewal self-reference prevention

**Decision**: Prevent a submission from naming itself as its own renewal (spec Edge Cases, FR-007) with an app-level guard in `submission_service` (`renews_from_submission_id != id`), backed by a table `CHECK (renews_from_submission_id IS NULL OR renews_from_submission_id <> id)` where the dialect supports it. Linking a renewal to a COMPLETED/CANCELLED submission is allowed (it's a historical relationship, not a live-state constraint).

**Rationale**: FR-007 requires the self-link be prevented; a CHECK makes it a data invariant and the app guard gives a clean error message. Note the CHECK is only added at *create/edit* — the `id` is app-generated (R11) so it is known before insert.

**Alternatives considered**: App-only guard with no CHECK — acceptable fallback (SQLite unit tier ignores the semantic anyway), but the CHECK is cheap defense-in-depth on SQL Server.

---

## R10 — Inception filter granularity

**Decision**: Filtering "by inception" supports both an exact `inception_date` match and a `treaty_year` grouping (the `TY{yy}` renewal-year bucket). The precise UI control (date picker vs. year dropdown vs. both) is a design detail settled during implementation, not a scope question (spec Assumptions). The query layer accepts either an exact date or a year and appends the matching bound predicate.

**Rationale**: Spec Assumptions explicitly defer the granularity to design; FR-021 only requires that the inception filter narrows correctly and combines with cedant/treaty-type.

**Alternatives considered**: Date-only — rejected: analysts group by treaty year during renewals (DATA_MODEL §4). Year-only — rejected: loses exact-date precision. Support both.

---

## R11 — UUID primary-key generation

**Decision**: Generate `submission.id` (and other UUID PKs the app inserts: `package.id`, `submission_crm_id.id`, `submission_status_event.id`) **app-side** with `uuid.uuid4()` passed as a bound parameter. Keep the migration's `server_default=NEWID()` as a server-side safety net, but the repository always supplies the id explicitly.

**Rationale**: The unit tier runs on SQLite via `register_engine`, which has no `NEWID()`. Generating ids in Python means one repository code path works identically on SQLite (unit) and SQL Server (integration), and the freshly-created row's id is known immediately for the response/redirect (needed by the create flow and the renewal self-ref CHECK in R9). This mirrors how the app already needs deterministic ids for event-sourced writes.

**Alternatives considered**: Rely on `NEWID()` default + `OUTPUT INSERTED.id` — rejected: not portable to the SQLite unit tier and splits the code path. `NEWSEQUENTIALID()` — rejected: only meaningful as a column default and still not portable.

---

## Summary of decisions

| # | Area | Decision |
|---|---|---|
| R1 | Concurrency | `updated_at` version marker; `WHERE id AND updated_at`; rowcount-0 → 409 conflict |
| R2 | Status | Event-sourced: insert event + stamp cached column in one `conn.begin()` txn; reopen from either closed state; no preconditions |
| R3 | Read-only gate | Service-layer check (status must be ACTIVE) on all mutations; UI hides affordances too |
| R4 | Duplicate warning | Name **or** cedant+type+inception match → non-blocking warn + confirm; never blocks |
| R5 | Package invariant | ≥1 member enforced in `package_service` across both child tables; unit-tested |
| R6 | Cedant autocomplete | `SELECT DISTINCT cedant_name` — no cedant table |
| R7 | Owner filter | Plain `assigned_analyst_id` predicate; no scope wrapper, no bypass |
| R8 | Cleanup | Full grep-verified surface (11 files), incl. `shell.py`/`test_db_config.py`/etc.; `reference/` excluded |
| R9 | Renewal self-ref | App guard + CHECK `renews_from_submission_id <> id` |
| R10 | Inception filter | Exact date **and** treaty-year grouping; control is a design detail |
| R11 | UUID PKs | App-side `uuid4()` bound param; `NEWID()` default retained as fallback |
