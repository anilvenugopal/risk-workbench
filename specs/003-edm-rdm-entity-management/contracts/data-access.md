# Contract — Data-Access / Service Layer (Iteration 2)

The developer-facing interface this iteration exposes. Functions (not classes, matching `auth_service.py` / the Iteration-1 services) across new modules in `app/services/`. Every function persists through the `db/` **safe bound-parameter path**; the transactional writes (`rwb_job` atomic claim, idempotent chained insert, EDM/RDM soft-delete on package finalize) use `db.get_connection("WORKBENCH")` + explicit `conn.begin()`. Signatures are the contract; types are illustrative Python.

**Risk Modeler is reached only through `app/services/irp_gateway.py`** — never `irp-integration` directly (Article 11 / research R1). The web layer calls **submit/search** gateway methods indirectly (via services that enqueue workers); it never calls `get_*` / `poll_*` / result-retrieval — those belong to the poller and workers (see [worker-poller.md](worker-poller.md)).

Shared typed errors (raised by services, mapped to HTTP by routers) — extends `app/services/errors.py`:
- `SubmissionClosed` — action on a non-ACTIVE submission (inherited gate, FR-025) → 409.
- `EmptyPackageError` — package would have zero members (FR-017) → 422. *(Iteration 1)*
- `ConcurrencyConflict` — optimistic-concurrency marker mismatch on a name/package edit (FR-039) → 409. *(Iteration 1)*
- `InvalidSourceFile` — a browse selection is outside `SHARED_DRIVE_ROOT`, missing, or not a file (FR-008/FR-009) → 422.
- `JobSubmitError` — a Risk Modeler submit failed on the request path *(only used if a submit is ever done inline; this iteration defers all submits to workers, so services raise this only from the retry/replace helpers that touch the gateway)*.

> **Name collision is NOT an error** — it is a non-blocking warning payload (FR-012 / research R8); services return it, routers render it, nothing is raised.

---

## `irp_gateway` (the IRP interface — fake in CI)

```python
# Thin wrapper over irp-integration. The ONLY module that imports it.
# Re-confirm every signature against the INSTALLED wheel before use (R1).

def submit_edm_import(*, name: str, source_file_path: str) -> SubmitResult:
    """submit_edm_import_job(...) → (irp_job_id, request_body). Caller stores
    request_body['resourceUri'] on irp_job_resource immediately (R1)."""

def submit_rdm_import(*, name: str, source_file_path: str,
                      edm_name: str | None) -> SubmitResult:
    """submit_rdm_import_job(...). edm_name=None → review-only apply (no EDM)."""

def submit_delete_edm(*, edm_irp_id: int) -> SubmitResult:
    """submit_delete_edm_job(...) → pollable irp_job id (async; polled like import)."""

def delete_rdm_analyses(*, rdm_name: str) -> None:
    """SYNCHRONOUS: resolve the RDM's analyses by rdmName and delete them inline.
    No irp_job. Returns only when the delete has completed (R6)."""

def get_import_job(irp_id: str) -> JobStatus: ...          # single-status-check
def get_delete_edm_job(irp_id: str) -> JobStatus: ...      # single-status-check (confirm getter, R1)
def search_edms(name: str) -> list[EdmHit]: ...            # name-collision check
def search_rdms(name: str) -> list[RdmHit]: ...
```

> The poller/workers depend on this interface; `tests/unit` injects a **fake** implementing it (Article 12). `get_*` methods are single-status-check only — `poll_*_to_completion` is never wrapped (Article 11).

---

## `edm_service`

```python
def import_edm(*, name: str, source_file_path: str, package_id: UUID | None,
               actor_id: UUID) -> ImportResult:
    """Create an irp_edm (status='pending_import') and enqueue the work that submits
    the import. STANDALONE import (package_id=None) enqueues an upload_edm rwb_job
    directly (requestor_type='analyst_request', requestor_id=irp_edm.id); the WORKER
    performs submit_edm_import (FR-042 — no Risk Modeler call on the request path).
    Validates source_file_path is within SHARED_DRIVE_ROOT and is a file (else
    InvalidSourceFile). Returns the collision warning (if any) alongside the id."""

def check_name_collision(name: str) -> list[str]:
    """search_edms(name); return existing IRP names that collide. Empty = clear.
    Non-blocking (FR-012) — the caller renders a warning, never blocks."""

def list_edms(*, package_id: UUID | None = None) -> list[EdmRow]:
    """Every EDM (library = no filter), or one package's EDMs. NO row scoping
    (FR-037 / Article 6) — all analysts see all EDMs."""

def get_edm(edm_id: UUID) -> Edm | None: ...

def replace_source_file(*, edm_id: UUID, new_source_file_path: str,
                        expected_updated_at: datetime, actor_id: UUID) -> None:
    """FR-046: update source_file_path (validated) and re-enqueue the import for a
    FAILED/errored EDM. Optimistic-concurrency checked (FR-039)."""

def retry_import(*, edm_id: UUID, actor_id: UUID) -> None:
    """FR-045: re-enqueue a single EDM's upload_edm head (idempotent on the dedup
    key). No-op if already ready/in-flight."""
```

## `rdm_service`

```python
def import_rdm(*, name: str, source_file_path: str, package_id: UUID | None,
               applied_edm_ids: list[UUID] = (), actor_id: UUID) -> ImportResult:
    """Create an irp_rdm (status='pending_import') and enqueue its apply work.
    applied_edm_ids empty → REVIEW-ONLY (a single apply with no EDM, FR-002/FR-016);
    otherwise one apply per EDM (worker-submitted). Same validation + non-blocking
    collision warning as edm_service. Broker results are one logical source across
    EDMs (FR-002; no per-EDM duplication)."""

def check_name_collision(name: str) -> list[str]: ...     # search_rdms
def list_rdms(*, package_id: UUID | None = None) -> list[RdmRow]: ...   # no scoping
def get_rdm(rdm_id: UUID) -> Rdm | None: ...
def replace_source_file(...): ...                          # FR-046, as edm_service
def retry_import(*, rdm_id: UUID, actor_id: UUID) -> None: ...          # FR-045
```

## `package_sync_service` (builds on Iteration-1 `package_service`)

```python
def save_package(*, package_id: UUID | None, name: str | None,
                 members: list[MemberSpec], actor_id: UUID,
                 expected_updated_at: datetime | None = None) -> SaveResult:
    """FR-013/FR-014: persist the package + per-member names; run the collision check
    for each member; DO NOT submit anything to Risk Modeler. Enforces >=1 member
    (EmptyPackageError). Optimistic-concurrency on edit (FR-039). Returns per-member
    collision warnings."""

def save_and_sync(*, package_id: UUID, actor_id: UUID) -> None:
    """FR-015/FR-042/FR-044: record the initial pending work items and RETURN
    IMMEDIATELY — no Risk Modeler call on the request path. Enqueues one upload_edm
    head per EDM (requestor_type='analyst_request', requestor_id=package_id); a
    review-only RDM (no EDM) enqueues one upload_rdm head directly. IDEMPOTENT:
    re-run skips members already ready/in-flight, re-enqueues only unstarted/errored
    ones (dedup key). Rejects an empty package (EmptyPackageError)."""

def delete_package(*, package_id: UUID, actor_id: UUID) -> None:
    """FR-019/FR-021: enqueue reverse-order removals — one delete_rdm head per RDM
    (SYNCHRONOUS worker; no irp_job), or one delete_edm head per EDM when the package
    has no RDMs. Returns immediately. The RDM->EDM fan-in and package soft-delete are
    handled app-side/poller-mediated by the workers (worker-poller.md). No hard delete."""

def retry_member(*, package_id: UUID, member_id: UUID, member_kind: str,
                 actor_id: UUID) -> None:
    """FR-045: re-enqueue exactly one member's operation head (idempotent)."""

def get_package_cards(submission_id: UUID) -> list[PackageCard]:
    """FR-022/FR-023: per-package card data — upload progress, member EDM status chip
    + RDM status chip, source_file_path(s), and job counts (all/active/failed) scoped
    to the package's members. Portfolio-summary/analysis counts are EMPTY this
    iteration (R13). No rolled-up package status (FR-018 — members carry their own)."""
```

## Job modules — split by table

The two job tables have different lifecycles and writer populations, so each has its
own write-side module; the cross-table **read** views live in a third. Split by *who
writes the table*, not by feature:

- `rwb_job_service` — the internal work queue (Article 10: "the SQL table *is* the
  queue"). Written by the web layer (*enqueue*) and the worker (*claim/complete*).
- `irp_job_service` — the bridge to an async Risk Modeler op (Article 11). Written by
  the worker (*record at submit*) and the poller (*status updates*, see worker-poller.md).
- `job_query` — read-only views that **union** `irp_job` + `rwb_job` (the Jobs list and
  the per-package counts). Belongs to neither write-service; keeping it separate stops
  either write-module from importing the other's table.

> **Transaction boundary:** chaining writes *both* tables atomically — a worker completes
> its `rwb_job` **and** records the `irp_job` in one transaction; the poller updates an
> `irp_job` **and** idempotently enqueues the next `rwb_job`. These functions are thin
> per-table statements that accept an explicit `conn`; the **worker/poller owns the
> `db.get_connection("WORKBENCH")` + `conn.begin()`** that spans both (contract intro).
> That is why the split costs nothing — no single transaction is broken by it.

### `rwb_job_service` (the queue — Article 10)

```python
def enqueue_rwb_job(*, requestor_type: str, requestor_id: UUID, rwb_job_type: str,
                    input_data: dict, actor_id: UUID | None) -> UUID | None:
    """IDEMPOTENT insert on UNIQUE(requestor_type, requestor_id, rwb_job_type)
    (FR-043 / SC-014). Returns None if the row already exists (dedup hit)."""

def claim_rwb_job(*, rwb_job_id: UUID, worker_id: str) -> bool:
    """Atomic: UPDATE ... SET status_code='running', claimed_by=:wid
    WHERE id=:id AND status_code='pending'. False if rowcount 0 (already claimed)."""

def complete_rwb_job(*, rwb_job_id: UUID, status: str,
                     output_data: dict | None, error_detail: str | None) -> None:
    """Set succeeded/failed + payload + completed_at (in-place, Article 4)."""
```

### `irp_job_service` (the async-op bridge — Article 11)

```python
def record_submitted_irp_job(*, package_id: UUID | None, irp_job_type: str,
                             irp_edm_id: UUID | None, irp_rdm_id: UUID | None,
                             irp_id: str, resource_uri: str | None,
                             payload: dict, response: dict, actor_id: UUID) -> UUID:
    """Worker-side: write irp_job (status='QUEUED', irp_id set) + any irp_job_resource
    (resource_uri captured at submit, R1). On submit failure the worker instead writes
    status='SUBMISSION FAILED', irp_id=null (FR-029)."""

# The poller's in-place irp_job status transitions (mirror get_*_job → status, backfill
# irp_id + completed_at on terminal) also live here — see worker-poller.md §3.
```

### `job_query` (read-only union views — spans both tables)

```python
def list_jobs(*, submission_id: UUID | None = None, package_id: UUID | None = None,
              status: str | None = None, job_type: str | None = None) -> list[JobRow]:
    """FR-032/FR-033: the Jobs list, filtered by the shared vocabulary
    (submission/package/status/job_type). Unknown params ignored; each is a bound
    predicate. NO row scoping (Article 6). Union of irp_job + rwb_job as the view
    layer needs (see http-routes)."""

def package_job_counts(package_id: UUID) -> JobCounts:
    """all / active / failed counts scoped to a package's members (FR-023/FR-024);
    the query behind package_sync_service.get_package_cards."""
```

## `shared_drive`

```python
def browse(path: str | None) -> DirListing:
    """FR-009/FR-011/R11: live read-only listing under SHARED_DRIVE_ROOT. path=None
    (or a submission's directory_path) seeds the start. Rejects traversal outside the
    root (InvalidSourceFile). No cached inventory."""

def validate_selection(path: str) -> str:
    """Resolve + confirm path is within SHARED_DRIVE_ROOT and is a file; return the
    canonical path to store on the member. Raises InvalidSourceFile otherwise."""
```

## `notification_service`

```python
def notify(*, notice_kind: str, ref: NotifyRef, outcome: str,
           actor_id: UUID | None) -> None:
    """Worker-side (notify_analyst actor): dispatch to the configured channel(s)
    (Teams/email/desktop, R10). Two flavors, per FR-030 / SC-003 / Q1 (2026-07-13):
      • notice_kind='action_complete' — ONE message when an analyst action (a standalone
        import / package sync / package delete) reaches a fully-terminal state — a standalone
        import is anchored per imported entity this iteration (batch grouping deferred); `ref`
        identifies the action, `outcome` summarizes it (e.g. "8 ready, 2 failed").
      • notice_kind='member_failure' — one message per failed member operation; `ref`
        identifies the member. NEVER one per successfully-completed member job.
    Never called from the web/poller (they only enqueue the notify_analyst rwb_job)."""
```

---

## Test obligations (Article 12 / references data-model §9)

Unit tier (SQLite + **fake IRP**):
- `save_and_sync` enqueues one `upload_edm` per EDM; re-run is idempotent (skips ready/in-flight); empty package → `EmptyPackageError` (SC-006/SC-012/SC-013).
- `enqueue_rwb_job` dedups on the composite key; a duplicate trigger returns `None` and inserts nothing (SC-014).
- `claim_rwb_job` returns True once then False (atomic claim); reconciler reclaim tested in `test_rwb_job_queue` (Article 10).
- `import_edm`/`import_rdm` create the entity + enqueue the worker but make **no** Risk Modeler call on the request path (FR-042); review-only RDM path (SC-004).
- `check_name_collision` returns colliding names and never raises/blocks (SC-005).
- `browse`/`validate_selection` reject out-of-root and non-file paths (FR-008/FR-009).
- `list_edms`/`list_rdms`/`list_jobs` apply no row scoping — all rows visible to any actor (SC-009).

SQL-Server tier: atomic claim rowcount 1→0 under contention; idempotent chained insert on the UNIQUE key (data-model §9).
