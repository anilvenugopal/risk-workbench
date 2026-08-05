# Contract: worker — `run_breakout_lob` / `run_breakout_state` (R2, R7, R10)

**Module**: `app/workers/portfolio_jobs.py` (NEW — auto-discovered `*_jobs.py`; two actors, one shared body; actor name == `rwb_job_type`, loader convention)

Runs under the standard `runtime.run_job` lifecycle: atomic claim → heartbeat → complete/fail; reclaimed by the reconciler if wedged (Article 10). All RM access via `irp_gateway` (Article 11); `poll_*_to_completion` forbidden (architecture-guard test extends over this module).

*(Revised 2026-08-03 after the probe run. Two things this file used to specify are now wrong and are corrected below: step 2 recomputed the plan inside the worker — AGENTS.md rule 8 forbids it, the worker executes the plan persisted at confirm (T-10/R10); and adoption resolved on the portfolio name — it resolves on `portfolioNumber` (T-07/P-11).)*

---

## Body: `_run_breakout_body(job) -> JobResult`

```
input_data: {edm_id, portfolio_id, dimension, actor_id, plan}     (data-model §4)
             plan = the approved list, one entry per sub-portfolio:
                    {value, label, name, number, accounts}

1. Load EDM + source portfolio; re-check minimal invariants (rows live, EDM has irp_id).
   Gone/invalid → JobResult.fail (graceful; nothing half-written).
2. plan = breakout_service.load_approved_plan(job.input_data)
   — read verbatim from input_data. The worker NEVER re-enumerates values, re-reads the
     summary, or recomputes names: collision suffixing depends on the portfolio names in
     the EDM, which this run itself changes (AGENTS.md rule 8 / T-10).
   Empty or unparseable plan → fail with reason; nothing created.
3. selection = gateway.select_breakout_accounts(edm_name, exposure, source, dimension,
                                                [e.value for e in plan])
   — hoisted out of the loop: ONE portfolio-scoped DataBridge query resolves every
     value at once (R1, revised 2026-08-05 — the REST selection could not complete
     on a 248,000-account portfolio, W-20). All-or-nothing: its failure →
     JobResult.fail; nothing has been written to RM at this point. errors_by_value
     stays on the seam for per-value-capable implementations.
4. FOR EACH entry IN plan:                                   # per-item isolation — the
     a. find_generated(source, dimension, value) live?       #   _backfill_edm_detail_body /
        → outcome 'skipped_existing'; continue               #   _upload_rdm_body precedents
     b. selection.errors_by_value[value]  → outcome 'failed' (read error recorded, W-14);
                                             continue — never proceed on a short id list
        selection.accounts_by_value[value] empty
                                          → outcome 'failed' (zero-match, FR-008);
                                             continue — NO create call, no empty portfolio
     c. gateway.create_sub_portfolio(name=entry.name, number=entry.number,
                                     description=<full, untruncated: source · dimension ·
                                     value>, account_ids=…)  # create → add → read back
        - duplicate-name error from the create step →
            hits = gateway.find_portfolio_by_number(exposure, entry.number)
            exactly 1 → portfolio_service.adopt_generated(...) +
                        gateway.populate_sub_portfolio(...)  # adopt-then-populate (R7) —
                        outcome 'adopted'                    #   heals a create-then-crash
                                                             #   empty portfolio; safe per
                                                             #   W-9 already-member semantics
            0 or >1  → outcome 'failed' (no findable owner / ambiguous number — FR-011
                       refuses to adopt an arbitrary hit); logged
        - other errors → outcome 'failed' (logger.warning; continue)
     d. success → portfolio_service.insert_generated(..., irp_id=result.portfolio_irp_id)
        — row upserted IMMEDIATELY per entry (fetch-then-persist; no transaction across a
          round-trip), so the page's self-poll shows generated portfolios as they land;
          result.account_count recorded in the outcome
5. output_data = breakout_service.summarize_outcomes(...)    (counts + per-entry detail)
6. Completion (any succeeded): idempotently enqueue backfill_edm_detail for the EDM
   (requestor the breakout job row — distinct from the poller's import-keyed enqueue), so
   generated portfolios acquire figures without analyst action (FR-013). Record
   backfill_enqueued.
7. JobResult: succeeded when ≥ 1 entry created/adopted/skipped-existing (partial success =
   success with outcomes — the _upload_rdm_body semantics); fail only when zero succeeded.
   Business-event logs throughout (actor id from input_data): requested/created/adopted/
   failed per sub-portfolio + completion summary (FR-015).
```

## Ordering & crash-safety notes

- **`completed < total` from the add is not a failure.** `manage_portfolio_accounts` counts ids *newly* added, so a healthy re-run reports `completed 0` (W-9). Success is decided by reading the portfolio back — a DataBridge member count compared against the ids sent (R1, revised 2026-08-05) — which is what `create_sub_portfolio` returns as `account_count`.
- **RM call first, row second** (step 4d): a crash between them leaves a sub-portfolio in RM without a row — exactly the at-least-once window `package_jobs.py` documents; the re-run heals it via adopt-by-number (R7). Never the reverse order (a row without an RM portfolio would lie).
- **No rollback anywhere** — created sub-portfolios persist through any failure; recovery is re-run (`ensure_pending_rwb_job` revives the terminal row; the persisted plan marks existing lineage rows `skipped_existing`). A re-run executes the **same** stored plan, so names and numbers are identical across runs by construction, not by luck.
- **No stamp re-check in the worker** (clarified 2026-07-30): the confirm-time FR-002a freshness gate covers staleness; the confirm-to-run window is seconds, concurrent RM-side editing is not an expected usage pattern, and residual drift degrades loudly via the zero-match per-sub-portfolio failure — never silently. That failure is also the visible mode for a selection-filter regression, which the freshness check cannot catch (a regression returns zero accounts against a perfectly fresh summary).
- **Double-delivery safe**: dispatch is wake-up only; the claim query serializes; the filtered unique index + `find_generated` make creation idempotent even under a reclaimed-then-redelivered job.
- One breakout job occupies the single worker for its duration. A fan-out shares one selection query (~1–2s even on a 248,000-account book, W-19) and then runs create + chunked add + count read-back per sub-portfolio. At ≤ 15 values with sub-second calls that stays inside the 30-second SC-003 budget; a large book's add step (one PATCH per 1,000 accounts) runs longer and simply keeps heartbeating. The `run_breakout_*` actors set a 60-minute dramatiq `time_limit` (the 10-minute default is reachable); `runtime.run_job` marks a time-limit kill `failed` so the reconciler cannot re-dispatch it into the same kill.

## Poller involvement — none

Both RM writes are synchronous and probe-confirmed: `create_portfolio` returns 201 and `manage_portfolio_accounts` returns 200 — no `202` and no workflow URL appeared on any call in the probe run. The poller is untouched and no `irp_job` rows exist for breakouts. The library raises on an unexpected 202 ([irp-library.md](irp-library.md)), so an RM behavior change surfaces as a loud per-sub-portfolio failure — never a silently untracked job.

## Test surface

`test_run_breakout_worker.py` (unit, fake IRP + SQLite):
- happy path: N entries → N rows with lineage + `inserted_by`; outcomes correct; `backfill_edm_detail` enqueued once.
- **executes the persisted plan**: a stored plan whose names differ from what a recompute would produce is run verbatim; no summary read happens in the worker.
- per-item isolation: entry k fails → k−1 rows persist, job `succeeded`, outcome `failed` recorded.
- zero accounts selected for a value: outcome `failed` with reason, **no create call made**, loop continues.
- selection read failure (the single DataBridge query raising): the job fails with nothing created.
- a per-value error seeded in `errors_by_value`: that entry fails, the rest run.
- `completed 0` from the add on a re-run is read as success, not failure (W-9).
- idempotent re-run: existing lineage rows `skipped_existing`; only missing ones created; names identical across runs.
- adopt-by-number: fake raises duplicate-name → one hit adopts (row carries the found `irp_id`, `populate_sub_portfolio` invoked); zero hits and multiple hits both fail that entry.
- zero-success → `JobResult.fail`; empty/unparseable plan → fail, nothing created.
- business-event log assertions (`test_business_event_logs.py` conventions).
