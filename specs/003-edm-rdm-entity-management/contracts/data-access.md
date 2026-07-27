# Contract — Data-Access / Service Layer (Iteration 2)

The developer-facing interface this iteration exposes. Functions (not classes, matching `auth_service.py` / the Iteration-1 services) across new modules in `app/services/`. Every function persists through the `db/` **safe bound-parameter path**; the transactional writes (`rwb_job` atomic claim, idempotent chained insert, EDM/RDM soft-delete on package finalize) use `db.get_connection("WORKBENCH")` + explicit `conn.begin()`. Signatures are the contract; types are illustrative Python.

**Risk Modeler is reached only through `app/services/irp_gateway.py`** — never `irp-integration` directly (Article 11 / research R1). The web layer calls **submit/search** gateway methods indirectly (via services that enqueue workers); it never calls `get_*` / `poll_*` / result-retrieval — those belong to the poller and workers (see [worker-poller.md](worker-poller.md)).

Shared typed errors (raised by services, mapped to HTTP by routers) — extends `app/services/errors.py`:
- `SubmissionClosed` — action on a non-ACTIVE submission (inherited gate, FR-025) → 409.
- `EmptyPackageError` — package would have zero members (FR-017), **or has no EDM member for a sync (RDM-only Save-and-Sync — deferred, D3/FR-016)** → 422. *(Iteration 1; D3 reuse 2026-07-14)*
- `ConcurrencyConflict` — optimistic-concurrency marker mismatch on a name/package edit (FR-039) → 409. *(Iteration 1)*
- `InvalidSourceFile` — a browse selection is outside `SHARED_DRIVE_ROOT`, missing, or not a file (FR-008/FR-009) → 422.
- `JobSubmitError` — a Risk Modeler submit failed on the request path *(only used if a submit is ever done inline; this iteration defers all submits to workers, so services raise this only from the retry/replace helpers that touch the gateway)*.

> **Name collision IS an error** *(amended 2026-07-27 — issue #17)*: `NameCollisionError` → 422, raised before anything is persisted or enqueued (FR-012 / research R8 as amended). Only the fail-open case (Risk Modeler unreachable) is a non-error payload — the save proceeds and the caller renders a warning.

---

## `irp_gateway` (the IRP interface — fake in CI)

```python
# Thin wrapper over irp-integration 0.2.0 (manager-based). The ONLY module that imports it.
# Confirmed surface → contracts/worker-poller.md "IRP gateway — confirmed method surface".
# Re-confirm signatures only if the active source is switched off 0.2.0 (R1).

def submit_edm_import(*, edm_name: str, source_file_path: str,
                      server_name: str = "databridge-1") -> SubmitResult:
    """edm.submit_edm_import_job(...) → (irp_job_id, request_body). Caller stores
    request_body['resourceUri'] on irp_job_resource immediately."""

def submit_rdm_import(*, rdm_name: str, edm_name: str,
                      source_file_path: str) -> SubmitResult:
    """rdm.submit_rdm_import_job(...) → (irp_job_id, request_body). edm_name is REQUIRED
    in 0.2.0 — review-only / no-EDM import is deferred (D3)."""

def submit_delete_edm(*, exposure_id: int) -> SubmitResult:
    """edm.submit_delete_edm_job(exposure_id) → pollable irp_job id (async)."""

def delete_analysis(*, analysis_id: int) -> None:
    """analysis.delete_analysis(id): SYNCHRONOUS single-analysis delete, no irp_job.
    delete_rdm loops this over the pair's irp_analysis rows (D2/R6)."""

def search_analyses(*, filter: str) -> list[AnalysisHit]:
    """analysis.search_analyses(filter='sourceRdmName="…" AND exposureName="…"').
    backfill_rdm_analyses uses this to capture irp_analysis rows (D2)."""

def get_import_job(irp_id: int) -> JobStatus: ...          # import_edm & import_rdm (shared getter)
def get_risk_data_job(irp_id: int) -> JobStatus: ...       # delete_edm getter
def search_edms(*, filter: str) -> list[EdmHit]: ...       # EDM name-collision check
def search_imported_rdms(*, filter: str) -> list[RdmHit]: ...   # RDM name-collision check
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

def check_name_collision(name: str) -> CollisionCheck:
    """search_edms(name), cached ~30s in-process (issue #11). A hit blocks the save
    (FR-012 as amended 2026-07-27 — issue #17); checked=False means the gateway
    couldn't answer — the caller fails open with a warning."""

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
    applied_edm_ids MUST be non-empty — review-only / no-EDM import is deferred (D3);
    one apply per EDM (worker-submitted). Same validation + blocking collision
    check as edm_service (issue #17). Broker results are one logical source across
    EDMs (FR-002; no per-EDM duplication)."""

def check_name_collision(name: str) -> list[str]: ...     # search_imported_rdms
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
    head per EDM (requestor_type='analyst_request', requestor_id=package_id). Every
    apply targets an EDM (D3): an RDM-only package (no EDM) is REJECTED with
    EmptyPackageError — review-only sync is deferred. IDEMPOTENT: re-run skips
    members already ready/in-flight, re-enqueues only unstarted/errored ones (dedup
    key). Rejects an empty package (EmptyPackageError)."""

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
- `import_edm`/`import_rdm` create the entity + enqueue the worker but make **no** Risk Modeler call on the request path (FR-042); an RDM import requires ≥1 target EDM, and an RDM-only `save_and_sync` is rejected with `EmptyPackageError` (review-only deferred — D3; SC-004).
- `check_name_collision` returns colliding names and never raises/blocks (SC-005).
- `browse`/`validate_selection` reject out-of-root and non-file paths (FR-008/FR-009).
- `list_edms`/`list_rdms`/`list_jobs` apply no row scoping — all rows visible to any actor (SC-009).

SQL-Server tier: atomic claim rowcount 1→0 under contention; idempotent chained insert on the UNIQUE key (data-model §9).
