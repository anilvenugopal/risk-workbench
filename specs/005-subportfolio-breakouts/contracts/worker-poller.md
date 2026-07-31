# Contract: worker — `run_breakout_lob` / `run_breakout_state` (R2, R7)

**Module**: `app/workers/portfolio_jobs.py` (NEW — auto-discovered `*_jobs.py`; two actors, one shared body; actor name == `rwb_job_type`, loader convention)

Runs under the standard `runtime.run_job` lifecycle: atomic claim → heartbeat → complete/fail; reclaimed by the reconciler if wedged (Article 10). All RM access via `irp_gateway` (Article 11); `poll_*_to_completion` forbidden (architecture-guard test extends over this module).

---

## Body: `_run_breakout_body(job) -> JobResult`

```
input_data: {edm_id, portfolio_id, dimension, actor_id}     (data-model §4)

1. Load EDM + source portfolio; re-check minimal invariants (rows live, EDM has irp_id).
   Gone/invalid → JobResult.fail (graceful; nothing half-written).
2. plan = breakout_service.compute_plan_for_run(portfolio_id, dimension)
   — recomputed from the stored summary + current names (never trusted from input_data).
   Empty/one-value plan (summary changed since confirm) → fail with reason; no slices created.
3. FOR EACH slice IN plan:                                   # per-item isolation — the
     a. find_slice(source, dimension, value) live?           #   _backfill_edm_detail_body /
        → outcome 'skipped_existing'; continue               #   _upload_rdm_body precedents
     b. gateway.create_sub_portfolio(...)                    # select → create → add (R1);
        - zero-accounts-selected error (summary drift) →     #   selection runs FIRST, so
            outcome 'failed' (reason recorded); continue     #   nothing is written to RM
        - duplicate-name error from the create step →
            hit = gateway.find_portfolio_by_name(exposure, slice.name)
            hit → portfolio_service.adopt_slice(...) +
                  gateway.populate_sub_portfolio(...)        # adopt-then-populate (R7) —
                  outcome 'adopted'                          #   heals a create-then-crash
                                                             #   empty portfolio; guarded by
                                                             #   U2 already-member semantics
            no hit → outcome 'failed' (name conflict without a findable owner — logged)
        - other errors → outcome 'failed' (logger.warning; continue)
     c. success → portfolio_service.insert_slice(..., irp_id=result.portfolio_irp_id)
        — row upserted IMMEDIATELY per slice (fetch-then-persist; no transaction across a
          round-trip), so the page's self-poll shows slices as they land; account_count
          recorded in the slice outcome
4. output_data = breakout_service.summarize_outcomes(...)    (counts + per-slice detail)
5. Completion (any slices succeeded): idempotently enqueue backfill_edm_detail for the EDM
   (requestor the breakout job row — distinct from the poller's import-keyed enqueue), so
   slices acquire figures without analyst action (FR-013). Record backfill_enqueued.
6. JobResult: succeeded when ≥ 1 slice created/adopted/skipped-existing (partial success =
   success with outcomes — the _upload_rdm_body semantics); fail only when zero succeeded.
   Business-event logs throughout (actor id from input_data): requested/created/adopted/
   failed per slice + completion summary (FR-015).
```

## Ordering & crash-safety notes

- **RM call first, row second** (step 3c): a crash between them leaves a slice in RM without a row — exactly the at-least-once window `package_jobs.py` documents; the re-run heals it via adopt-by-name (R7). Never the reverse order (a row without an RM portfolio would lie).
- **No rollback anywhere** — created slices persist through any failure; recovery is re-run (`ensure_pending_rwb_job` revives the terminal row; recomputed plan marks existing slices `skipped_existing`).
- **No stamp re-check in the worker** (clarified 2026-07-30): the confirm-time FR-002a freshness gate covers staleness; the confirm-to-run window is seconds, concurrent RM-side editing is not an expected usage pattern, and residual drift degrades loudly via the zero-match per-slice failure — never silently.
- **Double-delivery safe**: dispatch is wake-up only; the claim query serializes; the filtered unique index + `find_slice` make slice creation idempotent even under a reclaimed-then-redelivered job.
- One breakout job occupies the single worker for its duration (≤ 15 slices × 3 RM calls each — select/create/add — still comfortably inside the 30 s SC-005 budget at sub-second calls; 40+ slices or large selection paging runs longer and simply keeps heartbeating). Accepted at current scale (plan, Article 10 note).

## Poller involvement — none

The Platform `filtered-accounts` PUT is doc-verified **200 sync** ([managefilteredaccounts](https://developer.rms.com/platform/reference/managefilteredaccounts), fetched 2026-07-30; no async/job path documented): the poller is untouched and no `irp_job` rows exist for breakouts. The library raises on any unexpected 202 ([irp-library.md](irp-library.md)), so an RM behavior change surfaces as a loud per-slice failure — never a silently untracked job.

## Test surface

`test_run_breakout_worker.py` (unit, fake IRP + SQLite):
- happy path: N slices → N rows with lineage + `inserted_by`; outcomes correct; `backfill_edm_detail` enqueued once.
- per-slice isolation: slice k fails → k−1 rows persist, job `succeeded`, outcome `failed` recorded.
- zero accounts selected for a slice (summary drift): outcome `failed` with reason, **no create call made**, loop continues.
- idempotent re-run: existing slices `skipped_existing`; only missing created; names identical across runs.
- adopt-by-name: fake raises duplicate-name → adoption path; row carries the found `irp_id`; `populate_sub_portfolio` invoked (adopt-then-populate).
- zero-success → `JobResult.fail`; plan-empty (summary drifted) → fail, nothing created.
- business-event log assertions (`test_business_event_logs.py` conventions).
