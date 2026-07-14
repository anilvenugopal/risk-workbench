# Quickstart — Validate EDM/RDM Entity Management & Packages (Iteration 2)

A runnable validation guide proving the iteration works end-to-end. It references
[data-model.md](data-model.md), [contracts/](contracts/), and [research.md](research.md)
rather than duplicating them. Implementation detail (SQL bodies, template markup, full
test suites) belongs in `tasks.md` and the implementation phase — not here.

## Prerequisites

- Iterations 0–1 in place (auth, shell, nav, `db/` package, `0001_initial.py` with the submission/package/`irp_edm`/`irp_rdm` tables).
- Dev DB reachable (`make sqlserver-up` for WSL2 native, or `make dev-up` for the full stack).
- **Redis reachable** (worker broker) and the **background components running** — poller + Dramatiq worker (`make dev-up` starts them; natively, run the poller and worker processes alongside `make native-dev`).
- `.env` has the `MSSQL_WORKBENCH_*` vars **plus** the new Iteration-2 vars: `SHARED_DRIVE_ROOT`, `RWB_HEARTBEAT_INTERVAL_SECS`, `RWB_HEARTBEAT_STALE_SECS`, `IRP_SUBMISSION_MAX_RETRIES`, `POLL_INTERVAL_SECS`, the notification channel settings, and the `IRPClient()` env vars (see `infra/.env.example`).
- A shared-drive path mounted read-only at `SHARED_DRIVE_ROOT` with a sample `.bak`/`.mdf`/`.csv`.
- **CI needs no real Risk Modeler**: the unit tier runs against a **fake IRP** (research R1/Article 12). The `--run-irp` tier is opt-in and needs sandbox credentials.

## 1. Rebuild the schema (drop-create-seed)

The Iteration-2 tables + kind seeds are folded into the single revision (data-model §8). Rebuild is **destructive** (dev only); run the Rebuild/Refresh/Skip prompt for WORKBENCH first:

```bash
make db-rebuild        # drop + recreate 3 app DBs, run 0001_initial, seed
```

**Expected:** no error; the new tables exist — `irp_job`, `irp_job_resource`, `rwb_job`, `rwb_job_heartbeat`, and the five kind tables (`irp_job_type_kind`, `irp_job_resource_type_kind`, `rwb_job_type_kind`, `rwb_job_requestor_type_kind`, `rwb_job_status_kind`); seeds present (`irp_job_type_kind` = import_edm/import_rdm/delete_edm/geohaz/analysis/grouping/export — **no** `delete_rdm`; `rwb_job_type_kind` includes upload_edm/upload_rdm/delete_rdm/delete_edm/notify_analyst; `rwb_job_status_kind` = pending/running/succeeded/failed). The Iteration-1 tables are unchanged; `irp_edm`/`irp_rdm` gain **no** new columns (data-model §6).

## 2. Unit tests (SQLite + fake IRP — no external deps)

```bash
pytest tests/unit
```

**Expected — new coverage passes** (maps to the contracts' test obligations):
- `test_rwb_job_queue.py` — atomic claim returns True then False; heartbeat upsert; reconciler reclaims a stale `running` row (Article 10 mandate).
- `test_job_chaining.py` — `import_edm` FINISHED enqueues exactly one `upload_rdm`, fanning out to one apply per RDM; a duplicate trigger never double-enqueues (Article 2 mandate / SC-014).
- `test_package_sync_service.py` — one `upload_edm` per EDM + one apply per (EDM × RDM) pair; idempotent re-sync skips ready/in-flight; empty package rejected (SC-006/SC-012/SC-013).
- `test_delete_ordering.py` — RDM removals (synchronous, no `irp_job`) precede EDM removals (async `delete_edm` jobs); `delete_edm` enqueued only when all RDMs `deleted`; package soft-delete fires once (SC-007).
- `test_edm_service.py` / `test_rdm_service.py` — import creates the entity + enqueues the worker with **no** Risk Modeler call on the request path; review-only RDM path; collision warning is non-blocking; replace-file updates `source_file_path` (SC-004/SC-005/SC-013).
- `test_poller.py` — terminal FINISHED backfills `irp_id` + flips entity status + enqueues the dependent head; `SUBMISSION FAILED` ≠ `FAILED`.
- `test_jobs_filter.py` — the shared `submission/package/status/job_type` vocabulary parses from the query string; unknown params ignored (SC-008).

## 3. SQL-Server integration tests

```bash
pytest tests/sqlserver --run-sqlserver
```

**Expected:** `test_job_tables_migration.py` — the extended migration builds the `irp_job`/`rwb_job` families with all FKs and the `rwb_job` `UNIQUE(requestor_type, requestor_id, rwb_job_type)`; seeds present; the **atomic claim** `UPDATE … WHERE status_code='pending'` returns rowcount 1 then 0 under contention; the **idempotent chained insert** on the UNIQUE key absorbs a duplicate exactly once.

## 4. (Optional) IRP sandbox tests

```bash
pytest tests/irp --run-irp
```

**Expected:** real submit + single-status getters (`get_import_job` for `import_edm`/`import_rdm`, `get_risk_data_job` for `delete_edm`); the synchronous `delete_analysis` calls; and an assertion that `poll_*_to_completion` (and the poll-inside convenience methods) appear nowhere in the poller/workers (Article 11).

## 5. Manual walkthrough (the analyst's day-to-day)

Log in (dev fixture `admin@example.com`), with the poller + worker running, then:

1. **Import an EDM (US1)** — `/edms/import` → **browse** the shared drive (seeded from the submission's directory if you came from one) → select a `.bak` → name it → Import. *Expect:* the EDM appears in `pending_import`/`importing`, an `import_edm` `irp_job` is tracked, **the page never blocks** (SC-002), and within one poll interval it flips to `ready` with an `irp_id` recorded (SC-001). A bad file ends in `error`.
2. **Import an RDM (US2)** — import one **applied** to the ready EDM, and separately a **review-only** RDM (no EDM). *Expect:* each produces a tracked import; the review-only one still creates the broker analyses in Risk Modeler for later review (SC-004).
3. **Assemble a package (US3)** — on an ACTIVE submission, open the package modal → browse + multi-select member files → name each member. *Expect:* the **name-collision warning** highlights any name already in Risk Modeler but **never blocks** (SC-005); the ≥1-member rule holds (SC-012).
4. **Save vs Save-and-Sync (US3)** — **Save** persists names and submits nothing; **Save and Sync** returns immediately and the card shows a queued/syncing state (SC-014). *Expect:* exactly one `upload_edm` per EDM and one apply per (EDM × RDM) pair, each apply starting only after its target EDM's upload finishes (SC-006) — check the Jobs list.
5. **Package cards (US5)** — the submission detail shows one **full-width card per package** with upload progress, EDM + RDM status chips, source file path(s), and all/active/failed **job counts**; portfolio/analysis areas render **empty** (R13). Click a job count → lands on the Jobs list **pre-filtered to that package** (SC-008).
6. **Jobs list + notifications (US6)** — open the Jobs list, apply filters via the URL (`submission`/`package`/`status`/`job_type`); confirm clearable chips and that **refresh, bookmark, and back/forward preserve the filter** (SC-008). Watch a job advance **live** (SSE) without refreshing (SC-001). When a multi-member sync finishes you get **one action-completion notification** (not one per member), plus a **member-failure notification** for any member that failed, on the configured channel (SC-003).
7. **Recover from a bad member (US3)** — for a `FAILED` member: **replace its source file** and retry, **retry the single member**, and **re-run Save-and-Sync**. *Expect:* re-sync never re-submits an already-`ready` member (idempotent, SC-013).
8. **Delete a package (US4)** — Delete a synced both-package. *Expect:* removals run **RDM-before-EDM** — RDM removals are **synchronous** (no `irp_job`), EDM removals are **async** `delete_edm` jobs; deleting an EDM cascades to its analyses, deleting an RDM removes only that RDM's broker analyses (SC-007). When the last member is gone, the members + package row are **soft-deleted** — confirm **no hard-delete** exists anywhere.
9. **Libraries (US7)** — open the **EDM library** and **RDM library**; confirm they list **every** entity across all submissions to any analyst (no scoping, SC-009), expose an import entry point, and show each entity's import status.
10. **Closed-submission gate (US5)** — set the submission COMPLETED → package create/sync/delete are **blocked** (read-only); **Reopen** → available again (SC-011).

## Done when

- `make db-rebuild` clean; `pytest tests/unit` and `pytest tests/sqlserver --run-sqlserver` green.
- The manual walkthrough matches the expected outcomes above (SC-001…SC-014).
- No Risk Modeler submit occurs from a web request handler (all submits in workers), and no `poll_*_to_completion` exists in the poller (Article 11 / SC-014).
- No `customer`/scope construct appears on any EDM/RDM/package/job (Article 6 / FR-041).
