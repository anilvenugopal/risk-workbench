# Contract — retrieval worker & chain enqueues (spec 011)

The poller is untouched. Both triggers are worker-side chain enqueues; the new
actor lives in `app/workers/analysis_jobs.py` and registers in `_BODIES` and
`app/workers/dispatch.py` like every spec-010 actor.

## 1. Chain enqueues (T-01)

**Own analyses — `_backfill_analysis_detail_body`** (analysis_jobs.py), after
the successful UPDATE that stamps `irp_id`/`settings_metadata`/`ready`:

```python
rwb_job_service.enqueue_rwb_job(
    requestor_type="irp_analysis", requestor_id=analysis_id,
    rwb_job_type="retrieve_analysis_results",
    input_data={"analysis_id": analysis_id})
```

**Broker analyses — `_backfill_rdm_analyses_body`** (entity_jobs.py), after the
capture transaction commits: for every live `(rdm_id, irp_id)` row of this RDM
with `loss_results IS NULL`, the same enqueue keyed on that row's
`irp_analysis.id`. Rows that already carry results enqueue nothing (US2-3:
re-import of another EDM copy is a no-op).

Both paths are idempotent twice over: `enqueue_rwb_job` dedups on
`UNIQUE(requestor_type, requestor_id, rwb_job_type)` (FR-006), and the worker
skips when results already exist.

## 2. `retrieve_analysis_results` actor

`@rwb_actor(max_retries=0)` — its own `retrieve_analysis_results` Dramatiq queue
and worker process (CR-004) — with the standard `runtime.run_job` wrapper
(claim → heartbeat → body → complete; reconciler recovers interruption —
FR-007's automatic recovery).

Body, in order:

1. Load the analysis row. Missing → `ok(skipped="analysis missing")`.
   `loss_results IS NOT NULL` → `ok(skipped="results already stored")`
   (FR-006). `irp_id IS NULL` → `fail("analysis has no RM id")`.
2. Resolve `exposure_resource_id` (T-03):
   - own (`rdm_id IS NULL`): `irp_portfolio.irp_id` via the `irp_portfolio_id`
     FK;
   - broker: `irp_analysis.exposure_resource_id`;
   - NULL → one `irp_gateway.get_analysis_metadata(analysis_id=irp_id)`
     re-read for its pointer (and the engine fields when `settings_metadata`
     is also NULL); still NULL → `fail("no exposure pointer")`.
3. For each code in `analysis_perspective_kind` (sort order):
   `get_analysis_stats(...)` + `get_analysis_ep(...)`. Empty lists from both →
   the perspective is explicitly empty (T-08). Any exception →
   `fail(f"...: {exc}")` — no partial write.
4. Build the extract ([loss-results.md](loss-results.md)) — pure function,
   unit-tested against the captured fixtures — and write it with one UPDATE
   (`loss_results`, `updated_at`).
5. `ok(perspectives_with_data=n, stats_rows={code: len(rows)})` — the stats row
   count per perspective, so a response carrying more than one row is visible in
   `rwb_job.output_data` (contracts/loss-results.md).

Failure semantics (O-06 / spec 010 P-14): the rwb_job row ends `failed` with
`error_detail`; `irp_analysis.status_code` and the FINISHED run status are
never touched; views keep showing results-pending plus the reason (SC-005).
A terminal `failed` row is never resurrected by the dedup key — no automatic
re-retrieval (unchanged 010 deferral).

## 3. Volume

2 calls × 5 perspectives = 10 RM calls per analysis; each EP response
~1.2 MB, parsed in the worker, ~3 KB stored. An RDM with N analyses fans out
as N independent rwb_jobs — per-analysis isolation, one failure never blocks
the rest (mirrors the per-item rule of `execute_analysis_batch`).
