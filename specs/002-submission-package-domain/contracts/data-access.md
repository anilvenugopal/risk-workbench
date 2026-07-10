# Contract — Data-Access Layer (Repository)

The **primary interface** this iteration exposes (FR-029; US6 is explicitly developer-facing). These are functions (not classes, matching `app/services/auth_service.py`) living in `app/services/submission_service.py` and `app/services/package_service.py`. Every function persists through the `db/` safe bound-parameter path; the one transactional write (status change / create) uses `db.get_connection("WORKBENCH")` + explicit `conn.begin()`. Signatures below are the contract; types are illustrative Python.

Shared typed errors (raised by the service layer, mapped to HTTP by the router):
- `SubmissionClosed` — a mutation was attempted on a non-ACTIVE submission (R3 / FR-015) → HTTP 409/redirect with message.
- `ConcurrencyConflict` — optimistic-concurrency marker mismatch (R1 / FR-031) → HTTP 409, input preserved.
- `SelfRenewalError` — `renews_from_submission_id == id` (R9 / FR-007).
- `EmptyPackageError` — package would have zero members (R5 / FR-024).

---

## `submission_service`

### Create / read / list

```python
def create_submission(
    *, name: str, cedant_name: str, treaty_type_code: str, inception_date: date,
    treaty_year: int | None = None, renews_from_submission_id: UUID | None = None,
    directory_path: str | None = None, actor_id: UUID, confirmed: bool = False,
) -> CreateResult:
    """Create an ACTIVE submission owned by `actor_id`.
    - Generates id app-side (R11); writes the submission AND the initial ACTIVE
      status event in one transaction (R2).
    - Runs the duplicate check (find_similar). If matches exist and not `confirmed`,
      returns CreateResult(created=False, warnings=[...similar...]) WITHOUT writing
      (FR-004 non-blocking). Caller re-submits with confirmed=True to proceed.
    - Raises SelfRenewalError only via edit; on create the id is new so N/A.
    Returns CreateResult(created=True, submission_id=…) on success.
    """

def get_submission(submission_id: UUID) -> Submission | None:
    """Full detail incl. cached status_code. No access restriction (FR-019)."""

def list_submissions(
    *, owner_id: UUID | None = None,        # set → "My Submissions"; None → "All"
    cedant_name: str | None = None,
    treaty_type_code: str | None = None,
    inception_date: date | None = None,
    treaty_year: int | None = None,
) -> list[SubmissionRow]:
    """Master list. owner_id is a PLAIN predicate (assigned_analyst_id = owner_id),
    NOT a scope wrapper (R7 / Article 6). Filters combine (AND) as bound predicates
    (FR-021). All submissions are visible to every analyst regardless of owner."""

def find_similar(
    *, name: str, cedant_name: str, treaty_type_code: str, inception_date: date,
    exclude_id: UUID | None = None,
) -> list[SubmissionRow]:
    """Return submissions matching name OR (cedant+treaty_type+inception) (FR-004/R4).
    exclude_id skips the row being renamed. Never raises; empty list = no look-alikes."""

def cedant_suggestions(prefix: str, limit: int = 10) -> list[str]:
    """SELECT DISTINCT cedant_name … LIKE prefix% (FR-006/R6). No cedant table."""
```

### Edit / reassign (gated + concurrency-checked)

```python
def update_submission(
    *, submission_id: UUID, expected_updated_at: datetime, actor_id: UUID,
    confirmed: bool = False, **fields,
) -> UpdateResult:
    """Edit mutable fields (name, cedant, treaty_type, inception, treaty_year,
    directory_path, renews_from). 
    - Raises SubmissionClosed unless current status is ACTIVE (R3/FR-015).
    - Raises SelfRenewalError if renews_from_submission_id == submission_id (R9).
    - On rename/attr change, runs find_similar; unconfirmed match → UpdateResult with
      warnings and no write (FR-004).
    - UPDATE … WHERE id=:id AND updated_at=:expected_updated_at; rowcount 0 →
      ConcurrencyConflict (R1/FR-031). Stamps updated_at + updated_by."""

def reassign_owner(*, submission_id: UUID, new_owner_id: UUID,
                   expected_updated_at: datetime, actor_id: UUID) -> None:
    """Any analyst may reassign (FR-005a). Plain UPDATE assigned_analyst_id, gated by
    R3 (ACTIVE) + R1 (concurrency). Changes My-view membership only; never visibility
    (SC-011)."""
```

### Status lifecycle (event-sourced)

```python
def set_status(*, submission_id: UUID, to_status: str, reason: str | None,
               expected_updated_at: datetime, actor_id: UUID) -> None:
    """Transition to ACTIVE / COMPLETED / CANCELLED. No precondition (FR-012).
    One transaction (R2): INSERT submission_status_event + UPDATE cached status_code
    (+ updated_at concurrency check, R1). to_status == current is a recorded no-op,
    never an error (Edge Cases). Reopen = set_status(to='ACTIVE') from COMPLETED or
    CANCELLED (FR-011). There is NO delete function (FR-014)."""

def get_status_history(submission_id: UUID) -> list[StatusEvent]:
    """Full immutable history, newest first (FR-013)."""
```

### CRM tags (gated; append-only inserts)

```python
def add_crm_id(*, submission_id: UUID, crm_id: str, actor_id: UUID) -> UUID: ...
def edit_crm_id(*, crm_tag_id: UUID, crm_id: str, actor_id: UUID) -> None: ...
def remove_crm_id(*, crm_tag_id: UUID, actor_id: UUID) -> None: ...
def list_crm_ids(submission_id: UUID) -> list[CrmTag]: ...
"""All three mutations raise SubmissionClosed unless the parent submission is ACTIVE
(FR-017/FR-015/R3). Blank/whitespace crm_id is rejected (not stored). No format
validation (FR-018). Duplicate identical tags permitted."""
```

---

## `package_service`

```python
def create_package(*, name: str | None,
                   edm_ids: list[UUID] = (), rdm_ids: list[UUID] = (),
                   actor_id: UUID) -> UUID:
    """Create a package and stamp package_id on the given members, in one transaction.
    Raises EmptyPackageError if edm_ids and rdm_ids are both empty (FR-024/R5)."""

def package_member_count(package_id: UUID) -> int:
    """COUNT over irp_edm + irp_rdm where package_id = :id AND deleted_at IS NULL.
    The basis for the ≥1-member invariant test (FR-024/SC-008)."""

def add_member(*, package_id: UUID, member_id: UUID, member_kind: str,  # 'edm'|'rdm'
               actor_id: UUID) -> None:
    """Set package_id on an irp_edm/irp_rdm row (FR-023)."""

def remove_member(*, package_id: UUID, member_id: UUID, member_kind: str,
                  actor_id: UUID) -> None:
    """Clear package_id. If this empties the package, soft-delete the package
    (deleted_at) rather than leave a zero-member row (R5/FR-027)."""

def attach_to_submission(*, submission_id: UUID, package_id: UUID,
                         actor_id: UUID) -> None:
    """Insert submission_package (composite PK). Idempotent on the pair. A package may
    attach to many submissions; a submission to many packages (FR-025/SC-008)."""

def detach_from_submission(*, submission_id: UUID, package_id: UUID) -> None: ...

def soft_delete_package(*, package_id: UUID, actor_id: UUID) -> None:
    """Stamp deleted_at (FR-027). No hard delete."""

def get_packages_for_submission(submission_id: UUID) -> list[Package]: ...
```

> **Iteration-1 boundary (FR-028):** `package_service` provides *structure* operations only. No shared-drive browse, no Risk Modeler name-collision check, no create/sync/delete IRP jobs, no submission-detail package cards. Those are Iteration 2, built on these functions.

---

## Test obligations (Article 12 / FR-024 / FR-029)

Unit tier (SQLite via `register_engine`):
- `create_submission` writes submission + initial ACTIVE event atomically; owner set; status ACTIVE.
- `list_submissions(owner_id=A)` returns only A's; `list_submissions(owner_id=None)` returns all; every filter and combination narrows correctly (SC-002/SC-003).
- `find_similar` warns on name-match and on attribute-match; returns empty for a genuinely new deal (SC-006).
- `set_status` records history for every transition; reopen from COMPLETED **and** CANCELLED; same-status is a no-op; no delete function exists (SC-004/SC-005).
- Read-only gate: `update_submission` / `reassign_owner` / CRM mutations raise `SubmissionClosed` when status != ACTIVE (SC-012).
- Optimistic concurrency: stale `expected_updated_at` raises `ConcurrencyConflict` (SC-009).
- `create_package([])` raises `EmptyPackageError`; `package_member_count` correct across both child tables; one package attaches to two submissions (SC-008).
- No-scope regression: `db` exposes no `apply_scope`/`scoped_execute`; no query references `customer_id` (SC-010).

SQL-Server tier (`--run-sqlserver`):
- Migration builds all tables + FKs + the self-renewal CHECK; seeds present.
- Event-sourced status transaction is atomic (event + cached column) and rolls back together on failure.
