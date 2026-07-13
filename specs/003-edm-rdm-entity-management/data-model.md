# Phase 1 — Data Model: EDM/RDM Entity Management & Jobs (Iteration 2)

Derived from **DATA_MODEL.md §5, §6, §8, §13** (the schema source of truth) and constrained by the spec. This is the concrete set of tables the single `alembic/versions/0001_initial.py` revision **adds** for Iteration 2, *on top of* the Iteration-0 auth tables and the Iteration-1 submission/package/`irp_edm`/`irp_rdm` tables already created there. No table is dropped; `irp_edm`/`irp_rdm` already carry their full DATA_MODEL §5 shape — this iteration only **exercises** their previously-inert columns (`status`, `irp_id`, `source_file_path`, `created_by_irp_job_irp_id`, `as_of`, `server_name`, `deleted_at`).

**Conventions** (DATA_MODEL §2): singular `snake_case`; `id` is a UUID surrogate PK generated app-side (`uuid4()`, bound param; `NEWID()` default retained as fallback); `*_code` FK → matching `*_kind`; `*_id` FK → entity; entity tables carry `inserted_at`/`updated_at`/`inserted_by`/`updated_by`; kind / junction / event / heartbeat tables carry `inserted_at` (+ `inserted_by` where a user is responsible) only. Types shown are SQL Server (`DATETIME2`, `NVARCHAR`, `Uuid`, `INT`); the SQLite unit tier maps these via SQLAlchemy.

**Out of scope this iteration** (research R13): `irp_analysis`, `irp_analysis_status_kind`, `irp_portfolio`, `irp_treaty`, analysis templates/suites, `analysis_result_meta`, `result_export`, the IRP reference cache (§10), and Phase A validation (§11). RDM import creates analyses **in Risk Modeler**, but the app tracks none locally.

---

## 1. New kind tables (with seeds — DATA_MODEL §13)

All are `code` PK / `label` / `sort_order` / `inserted_at`, FK-referenced (Article 3). Seeded in-migration (mirroring the Iteration-1 kind seeds) **and** via idempotent `MERGE` in `seed_db.py`.

### `irp_job_type_kind` — IRP async operation types
**Seed:** `import_edm`, `import_rdm`, `delete_edm`, `geohaz`, `analysis`, `grouping`, `export`.
> Exercised this iteration: `import_edm`, `import_rdm`, `delete_edm`. The rest are seeded (closed app-defined set) but produce no jobs until later iterations. **There is no `delete_rdm` job type** — RDM delete is synchronous and creates no `irp_job` (A21 / research R6).

### `irp_job_resource_type_kind` — typed submit-payload resource
**Seed:** `portfolio` (only value confirmed today).

### `rwb_job_type_kind` — app-side worker types
**Seed:** `upload_edm`, `upload_rdm`, `retrieve_analysis_results`, `download_export_file`, `push_results_to_loss_repo`, `notify_analyst`, `delete_rdm`, `delete_edm`.
> Exercised this iteration: `upload_edm`, `upload_rdm`, `delete_rdm`, `delete_edm`, `notify_analyst`. The three result/export types are seeded but idle until Iteration 6.

### `rwb_job_requestor_type_kind` — chaining trigger discriminator
**Seed:** `irp_job`, `analyst_request`, `rwb_job`.

### `rwb_job_status_kind` — app-side work lifecycle
**Seed:** `pending`, `running`, `succeeded`, `failed`.

> **Plain-string status columns (NOT kind tables), Article 3 carve-out:** `irp_job.status`, `irp_edm.status`, `irp_rdm.status` — all mirror IRP-controlled vocabularies that can drift; an unknown value must not crash the poller.

---

## 2. `irp_job` — one tracked IRP async operation (grain = package)

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | Uuid | PK | app-generated |
| `package_id` | Uuid | null | FK → `package.id`; the package this job groups under (grain is package, not submission) |
| `irp_edm_id` | Uuid | null | FK → `irp_edm.id`; entity lineage (EDM import; apply target) |
| `irp_portfolio_id` | Uuid | null | FK → `irp_portfolio.id` — **column present for schema fidelity; unused this iteration** (no `irp_portfolio` table yet → created **without** this FK, see note) |
| `irp_rdm_id` | Uuid | null | FK → `irp_rdm.id`; entity lineage (RDM import/apply) |
| `irp_job_type` | NVARCHAR(50) | not null | FK → `irp_job_type_kind.code` |
| `irp_id` | NVARCHAR(64) | null | IRP's integer job id **as string**; null until submit succeeds |
| `status` | NVARCHAR(50) | not null | **plain string** (carve-out); RM-mirrored + app-local (§ vocabulary below); default `UNSUBMITTED` |
| `last_submission_payload` | NVARCHAR(MAX) | null | JSON — latest submit request |
| `last_submission_response` | NVARCHAR(MAX) | null | JSON — RM's response to that submit |
| `last_completion_result` | NVARCHAR(MAX) | null | JSON — terminal poll response (FINISHED/FAILED) |
| `submission_attempt_count` | INT | not null | default 0 (`submission_retry` backoff) |
| `submitted_at` | DATETIME2 | null | |
| `completed_at` | DATETIME2 | null | |
| `last_tracked_at` | DATETIME2 | null | null until first poll; records active tracking (Article 4) |
| `inserted_at`/`updated_at` | DATETIME2 | not null | defaults `GETUTCDATE()` |
| `inserted_by`/`updated_by` | Uuid | null | FK → `app_user.id` |

**Entity-lineage population by type** (DATA_MODEL §8): EDM import → `irp_edm_id`; **RDM import/apply → `irp_rdm_id` + `irp_edm_id`, one job per EDM the RDM applies to** (review-only omits `irp_edm_id`); EDM delete → `irp_edm_id`. All carry `package_id`.

**`status` vocabulary** (plain string, in-place `UPDATE` — never event-sourced):
- RM non-terminal: `PENDING` / `QUEUED` / `RUNNING` / `CANCEL_REQUESTED` / `CANCELING`
- RM terminal: `FINISHED` (**only** success) / `FAILED` / `CANCELED`
- App-local non-terminal: `UNSUBMITTED` / `SUBMITTING` / `BLOCKED` — **reserved/future**: this iteration creates an `irp_job` only at submit time with `QUEUED`, so these pre-submit states (and the `UNSUBMITTED` column default) are not produced yet; do not build a state machine expecting them
- App-local terminal: `SUBMISSION FAILED` (never reached RM — no `irp_id`; distinct from `FAILED`)

**Constraints/indexes:** FKs as above; index `(status)` and `(irp_job_type, status)` for the poller's batch-by-type non-terminal scan; index `package_id` for card job-counts.

> **`irp_portfolio_id` note:** DATA_MODEL §8 defines this FK, but `irp_portfolio` is not created until a later iteration (R13). To keep the single migration self-consistent, `irp_job` is created **without** the `irp_portfolio_id` column this iteration; it is added alongside `irp_portfolio` when portfolios arrive. (`irp_edm_id`/`irp_rdm_id` reference tables that already exist.) This is the one deliberate deviation from the §8 column list, recorded here and in research R13.

---

## 3. `irp_job_resource` — typed submit payload (the resource URI)

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | Uuid | PK | app-generated |
| `irp_job_id` | Uuid | not null | FK → `irp_job.id` |
| `resource_type` | NVARCHAR(50) | not null | FK → `irp_job_resource_type_kind.code` |
| `resource_uri` | NVARCHAR(1024) | not null | **captured at submit time** — RM's completion response omits it (irp-integration note: store `request_body["resourceUri"]` immediately) |
| `inserted_at` | DATETIME2 | not null | default `GETUTCDATE()` |

- One-per-job today (`portfolio` only); multiplicity kept open (DATA_MODEL §14 open item). Index `irp_job_id`.

---

## 4. `rwb_job` — app-side queued work (the SQL queue, Article 10)

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | Uuid | PK | app-generated |
| `requestor_type` | NVARCHAR(50) | not null | FK → `rwb_job_requestor_type_kind.code` — trigger discriminator |
| `requestor_id` | Uuid | not null | id of the trigger (**no DB FK** — target varies by type: package / irp_job / rwb_job) |
| `rwb_job_type` | NVARCHAR(50) | not null | FK → `rwb_job_type_kind.code` |
| `status_code` | NVARCHAR(50) | not null | FK → `rwb_job_status_kind.code`; **in-place update**; default `pending` |
| `input_data` | NVARCHAR(MAX) | null | JSON — the work order (e.g. member id, source path, package id) |
| `output_data` | NVARCHAR(MAX) | null | JSON — produced on success |
| `error_detail` | NVARCHAR(MAX) | null | set on failure |
| `attempt_count` | INT | not null | default 0 |
| `claimed_by` | NVARCHAR(128) | null | worker id (set on atomic claim) |
| `submitted_at` | DATETIME2 | null | |
| `completed_at` | DATETIME2 | null | |
| `inserted_at`/`updated_at` | DATETIME2 | not null | defaults `GETUTCDATE()` |
| `inserted_by`/`updated_by` | Uuid | null | FK → `app_user.id` (nullable — poller/worker-enqueued rows have no user) |

**Dedup / chaining key (the A21 idempotency backbone):**
`UNIQUE (requestor_type, requestor_id, rwb_job_type)` — every chained enqueue is an idempotent insert against this key, so a re-poll / redelivery / reconciler re-enqueue cannot create a duplicate work item (FR-043 / SC-014 / research R4).

**Worker lifecycle** (DATA_MODEL §8 / Article 10 / research R3):
- **Claim** atomically: `UPDATE rwb_job SET status_code='running', claimed_by=:wid, submitted_at=:now WHERE id=:id AND status_code='pending'`; `rowcount 0` ⇒ already claimed ⇒ exit.
- **Heartbeat** via daemon thread → upsert `rwb_job_heartbeat` every `RWB_HEARTBEAT_INTERVAL_SECS`.
- **Complete**: set `succeeded`/`failed` + `output_data`/`error_detail` + `completed_at`; on success, idempotently insert any chained tail rows.
- **Reclaim**: the poller's reconciler resets rows whose heartbeat is older than `RWB_HEARTBEAT_STALE_SECS` back to `pending`.

**Indexes:** the UNIQUE key above; `(status_code)` for the pending-sweep/claim; `(requestor_type, requestor_id)` for chaining lookups.

---

## 5. `rwb_job_heartbeat` — per-job liveness (one row per job)

| Column | Type | Null | Notes |
|---|---|---|---|
| `rwb_job_id` | Uuid | PK | FK → `rwb_job.id`; **UNIQUE — one row per job**, upserted |
| `worker_id` | NVARCHAR(128) | not null | the worker currently holding the job |
| `heartbeat_at` | DATETIME2 | not null | stamped every `RWB_HEARTBEAT_INTERVAL_SECS` |

- The reconciler compares `heartbeat_at` to `now - RWB_HEARTBEAT_STALE_SECS` to detect a dead worker and reclaim the job (Article 10).

---

## 6. `irp_edm` / `irp_rdm` — columns now exercised (no schema change)

Both tables already exist with their full §5 shape (Iteration 1). This iteration **uses** — with **no `ALTER`** — the previously-inert columns:

| Column | How it is used now |
|---|---|
| `source_file_path` | set at import from the shared-drive browse; **updated** on source-file replacement (FR-046) |
| `name` | analyst-provided; name-collision checked via `search_edms/rdms` (FR-012) |
| `irp_id` | backfilled by the poller on import `FINISHED` (FR-006) |
| `created_by_irp_job_irp_id` | the import job's IRP id, stamped when the entity is created |
| `status` | plain-string lifecycle: `pending_import` → `importing` → `ready` / `error` → `delete_pending` → `deleted` (FR-004); `irp_rdm.status` is the **combined rollup** of its apply jobs |
| `server_name` (EDM only) | IRP DataBridge server, when returned |
| `as_of` / `deleted_at` | drift signal / soft delete (soft-deleted on package delete completion, FR-021) |

- `irp_rdm` still has **no `edm_id`** — an RDM applies to every EDM in its package (full grid), associations live in the apply `irp_job` rows (DATA_MODEL §5).

---

## 7. Relationships (Iteration-2 additions)

```text
package        1──∞ irp_job            (package_id — nullable; job grain = package)
irp_edm        1──∞ irp_job            (irp_edm_id — nullable; entity lineage)
irp_rdm        1──∞ irp_job            (irp_rdm_id — nullable; entity lineage)
irp_job_type_kind          1──∞ irp_job          (irp_job_type)
irp_job        1──∞ irp_job_resource   (submit resource(s))
irp_job_resource_type_kind 1──∞ irp_job_resource (resource_type)
rwb_job_type_kind          1──∞ rwb_job          (rwb_job_type)
rwb_job_requestor_type_kind 1──∞ rwb_job         (requestor_type)
rwb_job_status_kind        1──∞ rwb_job          (status_code)
rwb_job        1──0..1 rwb_job_heartbeat         (heartbeated by worker)
```

- **`irp_job` and `rwb_job` are fully decoupled — no FK between them.** The bridge is the poller writing an `rwb_job` keyed by `requestor_id = <finished irp_job.id>` (research R2/R4), not a foreign key.
- No `customer`/scope column anywhere (Article 6). Ownership reaches a submission only transitively: `irp_job.package_id → submission_package → submission`.

---

## 8. Migration & seed impact (single revision — drop-create-seed)

**`alembic/versions/0001_initial.py`** — extend the one existing revision (after the Iteration-1 tables, in FK order):
- **Add kind creates + seeds:** `irp_job_type_kind`, `irp_job_resource_type_kind`, `rwb_job_type_kind`, `rwb_job_requestor_type_kind`, `rwb_job_status_kind` (with the §13 seed rows inline).
- **Add entity creates:** `irp_job` (FKs to `package`/`irp_edm`/`irp_rdm`/`irp_job_type_kind`; **without** `irp_portfolio_id`, §2 note), `irp_job_resource`, `rwb_job` (+ the `UNIQUE(requestor_type, requestor_id, rwb_job_type)` constraint), `rwb_job_heartbeat`.
- **Add indexes:** `irp_job (irp_job_type, status)`, `irp_job (status)`, `irp_job (package_id)`; `irp_job_resource (irp_job_id)`; `rwb_job (status_code)`, `rwb_job (requestor_type, requestor_id)`; the `rwb_job` UNIQUE key.
- **Downgrade:** drop the new tables in reverse FK order (heartbeat → rwb_job → irp_job_resource → irp_job → the five kind tables), ahead of the existing Iteration-1 drops.
- **No `ALTER`** on `irp_edm`/`irp_rdm` — their columns already exist (§6).

**`infra/scripts/seed_db.py`** — add idempotent `MERGE` seeds for the five new kind tables (same pattern as the existing `role_kind`/`submission_status_kind`/`treaty_type_kind` MERGEs), so a re-seed without a full rebuild stays correct.

**Dev DB strategy:** Rebuild (`make db-rebuild`) — drop, recreate, migrate, seed. Single revision until production cutover (FR-040). Run the DB-lifecycle prompt (Rebuild / Refresh / Skip) for WORKBENCH before this schema-affecting work.

---

## 9. Test obligations (Article 12 — cross-referenced in contracts/)

Unit tier (SQLite via `register_engine`):
- **Prerequisite gate / chaining (Article 2 mandate):** fan-out (`import_edm` FINISHED → one `upload_rdm`); fan-in (`delete_edm` enqueued only when all RDM removals `deleted`); idempotent — a duplicate trigger never double-inserts (SC-014).
- **`rwb_job` state machine (Article 10 mandate):** atomic claim (second claimant gets rowcount 0); heartbeat upsert; reconciler reclaims a stale `running` row to `pending`.
- Per-pair sync set: an EDM+RDM package yields one `upload_edm` per EDM and one apply per (EDM × RDM) pair (SC-006).
- Delete ordering: RDM removals (synchronous) precede EDM removals (async jobs); EDM removal not submitted until all RDM removals `deleted` (SC-007).
- Idempotent re-sync skips `ready`/in-flight members; per-member retry enqueues exactly one head; source-file replacement updates `source_file_path` (SC-013).
- Jobs-list filter parsing over the shared `submission/package/status/job_type` vocabulary; unknown params ignored.
- Poller (fake IRP): terminal `FINISHED` backfills `irp_id` + flips entity `status`; `SUBMISSION FAILED` distinct from `FAILED`.

SQL-Server tier (`--run-sqlserver`):
- Extended migration builds the `irp_job`/`rwb_job` families + all FKs + the `rwb_job` UNIQUE key; seeds present.
- The atomic claim `UPDATE … WHERE status_code='pending'` returns rowcount 1 then 0 under contention; the idempotent chained insert on the UNIQUE key raises/absorbs the duplicate exactly once.

IRP tier (`--run-irp`, opt-in): real submit + single-status `get_*_job` for import and `delete_edm`; the synchronous RDM analysis-entity delete.
