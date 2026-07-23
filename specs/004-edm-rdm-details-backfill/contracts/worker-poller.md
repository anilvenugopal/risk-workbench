# Contract — Backfill Worker & Poller Extension (Iteration 3)

The out-of-process half of the iteration, where every Risk Modeler **detail read** happens (Article 11). This is a **forward extension of the Iteration-2 completion path** (research R3): one new `rwb_job` worker body, one extended worker, and one idempotent enqueue added to the poller's existing `import_edm` terminal handler. No new poller, no new async spine.

**Process boundaries (Article 11 / Article 10) — unchanged from spec 003:**
- **Poller** — `app/poller/run.py`, standalone; never imported by a route handler. Single-status-check `get_*_job` only; `poll_*_to_completion` forbidden. This iteration adds **one enqueue** to `_handle_import_edm_terminal`.
- **Dramatiq worker** — `app/workers/package_jobs.py`; consumes `rwb_job` rows; performs every Risk Modeler detail read. Single worker by default; the existing claim → heartbeat → complete lifecycle (`runtime.run_job`) is reused verbatim.
- **Web layer** — reads only **stored** detail; enqueues nothing new; makes no Risk Modeler call (including the Excel export).
- All Risk Modeler access goes through `app/services/irp_gateway.py` (fake in CI).

---

## IRP gateway — new read methods (extend the confirmed surface)

Added to `irp_gateway.py` and the `IRPGateway` protocol; the CI fake mirrors them. **All single-status / read — never `poll_*_to_completion`.** Single-item where practical; the worker loops app-side ([[prefer-single-irp-endpoints]]). **Confirm each name + signature against the active wheel (`make irp-status`) before implementing — the wheel is pre-release (R1); log confirmations/gaps in `docs/IRP_INTEGRATION_FOLLOWUPS.md`.**

| Op | Gateway method (illustrative; confirm vs wheel) | Returns |
|---|---|---|
| Enumerate an EDM's portfolios | `list_portfolios(*, edm_irp_id: int) -> list[PortfolioHit]` | id + name per portfolio |
| Per-portfolio exposure figures | `get_portfolio_exposure(*, portfolio_irp_id: int) -> ExposureDetail` | counts / perils+sub-perils / geography / currency / record volume / (tiv?) |
| Treaty attribute detail for an EDM | `search_treaties(*, edm_irp_id: int) -> list[TreatyDetail]` | name + irp_id + full attribute map per treaty (§5) |
| Broker-analysis settings/metadata | extend `AnalysisHit` with a metadata map, **or** `get_analysis_metadata(*, analysis_id: int) -> AnalysisMetadata` | the FR-031/§7 settings map |

- **Gateway discipline (unchanged):** expose ONLY read/`search`/`get` single-status methods; NEVER `poll_*_to_completion` or the poll-inside convenience methods.
- **Value objects** (gateway-owned, like `SubmitResult`/`AnalysisHit`): `PortfolioHit`, `ExposureDetail`, `TreatyDetail`, `AnalysisMetadata` — plain dataclasses the worker serializes to the JSON snapshot (R2). The worker stores RM's payload **verbatim** in the snapshot; the value objects are the typed hand-off, not a normalized model.
- **Fake (`tests/unit/fakes/fake_irp.py`):** returns canned portfolio/treaty/analysis-metadata payloads so the backfill worker, the rollup, and the graceful-empty paths are all unit-testable without IRP.

---

## 1. `backfill_edm_detail` worker body (NEW `rwb_job_type`)

```
receive rwb_job(backfill_edm_detail) for edm_id (input_data: {edm_id, package_id?})
  → claim + heartbeat (runtime.run_job — unchanged lifecycle)
  → edm = edm_service.get_edm(edm_id); if missing or edm.irp_id is None → JobResult.ok(skipped)
      (irp_id is the exposureId the poller backfilled at import FINISHED; without it there is
       nothing to fetch — a pre-capability/never-finished EDM stays in the graceful empty state)
  → portfolios = gateway.list_portfolios(edm_irp_id=int(edm.irp_id))
    for each p in portfolios:                              # app-side loop (single-item reads)
        exposure = gateway.get_portfolio_exposure(portfolio_irp_id=int(p.irp_id))
        portfolio_service.upsert_portfolio_detail(edm_id=edm_id, irp_id=p.irp_id,
            name=p.name, exposure_detail=exposure, as_of=now)   # idempotent overwrite (R2)
  → treaties = gateway.search_treaties(edm_irp_id=int(edm.irp_id))
    for each t in treaties:
        treaty_service.upsert_treaty_detail(edm_id=edm_id, irp_id=t.irp_id,
            name=t.name, attributes=t.attributes, as_of=now)     # idempotent overwrite
  → JobResult.ok(portfolios=len(portfolios), treaties=len(treaties))
  on any gateway failure: JobResult.fail(...) — the rwb_job is recoverable via the reconciler/
    retry machinery; the EDM's 'ready' status is NOT touched (FR-005); the detail view shows
    "detail unavailable" until a successful re-run overwrites the snapshot (idempotent).
```
- **Idempotent** (FR-004): every upsert keys on `UNIQUE(edm_id, irp_id)` (fallback `(edm_id, name)`), so a redelivery / reconciler re-run / re-import overwrites `exposure_detail`/`attributes`/`as_of` in place — never a duplicate row.
- **Per-entity isolation** ([[prefer-single-irp-endpoints]]): one portfolio's failed exposure read is logged and skipped; the rest of the EDM still backfills (a partial snapshot is better than none, and re-run completes it).
- The upserts run through `db.get_connection("WORKBENCH")` + `conn.begin()` (Article 7); no transaction is held across a gateway round-trip (Article 11 — fetch first, then persist).

Registered exactly like the existing actors: a `_backfill_edm_detail_body` function + `@dramatiq.actor(max_retries=0) def backfill_edm_detail(...)` wrapper + an entry in the `_BODIES` map for the synchronous unit-tier drain.

---

## 2. Extended `backfill_rdm_analyses` (existing worker — add metadata capture)

The existing body (spec 003, `_backfill_rdm_analyses_body`) captures the pair's `irp_analysis` rows on `import_rdm` FINISHED and rolls `irp_rdm.status` up to `ready`. **Extension:** for each captured analysis, also fetch and store its `settings_metadata` (R3/R8) — either from richer `search_analyses` fields already returned, or via a single-item `gateway.get_analysis_metadata(analysis_id)`. The `is_group` flag is set from the payload. Still idempotent on `UNIQUE(rdm_id, edm_id, irp_id)`; a re-run overwrites `settings_metadata` in place. **No new poller enqueue and no new `rwb_job_type`** — this rides the existing `import_rdm` FINISHED → `backfill_rdm_analyses` chain.

---

## 3. Poller extension (`_handle_import_edm_terminal`) — one idempotent enqueue

The existing handler, on `status == FINISHED`, backfills the EDM's exposureId + flips it to `ready`, then (for a package member with RDMs) enqueues `upload_rdm`. **Add one enqueue**, placed **before** the existing `if not rdm_ids: return` guard and **independent of `package_id`**, so a standalone or EDM-only import also backfills its detail:

```python
# after edm_service.backfill_on_terminal(... status=READY ...):
if status == "FINISHED":
    rwb_job_service.enqueue_rwb_job(
        requestor_type="irp_job", requestor_id=job["id"],
        rwb_job_type="backfill_edm_detail",
        input_data={"edm_id": str(job["irp_edm_id"]),
                    "package_id": (str(job["package_id"]) if job.get("package_id") else None)},
        conn=conn)
# ... existing upload_rdm enqueue (only for package members with RDMs) continues unchanged ...
```
- **Coexists** with the `upload_rdm` enqueue: same `(requestor_type='irp_job', requestor_id=<this job.id>)` but a **different `rwb_job_type`**, so the `UNIQUE(requestor_type, requestor_id, rwb_job_type)` key admits both as distinct rows.
- **Idempotent** (SC / FR-004): a re-poll of the same terminal job re-inserts nothing (dedup hit) — no double backfill.
- Both heads are delivered by the poller's existing `_dispatch_pending` sweep (the poller runs in its own process, so heads it enqueues are dispatched here, not at enqueue time — unchanged from spec 003).
- **Non-FINISHED terminal** (`FAILED`/`CANCELLED`): unchanged — the EDM flips to `error`, and **no** `backfill_edm_detail` is enqueued (there is no detail to fetch).

Analysis metadata needs **no** poller change — it rides the existing `_handle_import_rdm_terminal` → `backfill_rdm_analyses` enqueue.

---

## 4. The auto-fire vs click-gated line (Article 5, explicit per op)

| Step | Trigger | Kind |
|---|---|---|
| Import a file / assemble a package (Iteration 2) | analyst click | **judgment — waits for a click** |
| `import_edm` FINISHED → `backfill_edm_detail` | poller (mechanical) | **auto-fires** |
| `import_rdm` FINISHED → `backfill_rdm_analyses` (now also metadata) | poller (mechanical) | **auto-fires** |
| View EDM detail / expand a treaty / export treaties to Excel | analyst read | **not a judgment gate — a read of stored detail** |
| Choose/split portfolios, run GeoHaz, run analyses | analyst click (Iteration 4/6) | **judgment — waits for a click (out of scope here)** |

Detail backfill is a **direct mechanical consequence** of one import intent, so it auto-fires; every judgment step that acts *on* the detail (splitting portfolios, launching analyses) stays a later iteration behind an explicit click.

---

## 5. Test obligations (Article 12)

Unit tier (SQLite + **fake IRP**):
- **`backfill_edm_detail`** (`test_backfill_edm_detail`): fetches (fake) → upserts `irp_portfolio`/`irp_treaty` + JSON snapshot + `as_of`; a re-run **overwrites in place** (no duplicate rows); a gateway failure fails the `rwb_job` but leaves the EDM `ready` (recoverable, FR-005); a single portfolio's failed exposure read does not abort the rest.
- **Extended `backfill_rdm_analyses`**: `settings_metadata` written per captured analysis; idempotent with the existing pair capture.
- **Poller** (`test_poller`, extended): `import_edm` FINISHED enqueues **both** `upload_rdm` and `backfill_edm_detail`; idempotent on re-poll; a standalone/EDM-only import (`package_id` null / no RDMs) still enqueues `backfill_edm_detail`; a `FAILED` terminal enqueues neither backfill.

IRP tier (`--run-irp`, opt-in): the real `list_portfolios` / `get_portfolio_exposure` / `search_treaties` / analysis-metadata round-trips; an assertion that `poll_*_to_completion` and the poll-inside convenience methods appear nowhere in the new worker/gateway code.
