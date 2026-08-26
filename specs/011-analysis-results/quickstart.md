# Quickstart — verifying Analysis Results Sync & Viewing (spec 011)

Prerequisites: the Docker stack up (`make dev-up`, developer-run), DB rebuilt
after the schema edits (`make db-rebuild` — destructive, developer's call),
and for live retrieval an irp-integration build carrying the T-02
`PERSPECTIVE_CODES` widening (`make irp-status` to confirm the source).

## Test tiers

```bash
uv run pytest tests/unit        # any host shell; SQLite + FakeIRP
make test-sql                   # linux-box; migration/seeds + dedup + round-trip
make shell && uv run pytest tests/irp --run-irp   # sandbox: WX/QS, T-08, broker pointer
```

## US1 — own analysis loss numbers (P1)

1. From an EDM with portfolios, Execute Template (spec 010) and wait for
   FINISHED (~minutes, 3s self-poll).
2. Expect, with **no further action**: the row's Currency and AAL columns fill
   in (≤10 min, SC-001). Until then the row shows results-pending.
3. Expand the row: OEP + AEP at 50/100/250/500/1000/10000, perspective toggle
   GR/RL/WX/QS/GU, Gross default; a perspective the analysis did not produce
   shows as absent, not as an error.
4. Failure path: point the sandbox creds at an invalid tenant (or kill the
   worker mid-retrieval and let the reconciler reclaim) — the analysis stays
   FINISHED/ready, the retrieval `rwb_job` ends `failed` with `error_detail`,
   and the row keeps showing results-pending with the reason (SC-005).
5. Re-trigger check: re-run the RDM sync / re-fire the backfill — no second
   retrieval job, no changed `loss_results` (FR-006):
   `SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='retrieve_analysis_results' AND requestor_id='<analysis-id>'` → 1.

## US2 — broker results on RDM import (P2)

1. Import a broker RDM (or bundle) into a submission.
2. Expect: after `backfill_rdm_analyses` completes, one
   `retrieve_analysis_results` job per broker analysis, then AAL/currency on
   every broker row — zero clicks.
3. Import a second EDM copy of the same RDM: no new retrieval jobs, identical
   numbers on both copies (SC-002) —
   `SELECT rdm_id, irp_id, loss_results FROM irp_analysis WHERE rdm_id='<rdm>'`
   shows one row per source analysis.
4. No broker row anywhere names a portfolio (FR-020).

## US3 — merged table (P2)

1. Open the EDM detail: **one** Analyses section — own rows directly (`CRE_`
   prefix, no RDM), broker rows under expandable RDM group rows.
2. Open the submission detail: the new Results section lists the same merged
   shape across all the submission's EDMs and RDMs.
3. Switch the section perspective: AAL column and every expanded inline block
   follow; the 3s poll does not reset the selection.
4. Copy: the table lands in Excel with headers intact (SC-006). Units
   selector: values never auto-switch; ones/thousands/millions apply
   on selection (millions default).

## US4 — dedicated results page (P3)

1. Multi-select 3–5 analyses (mix own + broker) on either page → **View**.
2. Expect: new browser tab, URL `/results/analyses?ids=…`, one column per
   analysis in selection order, all 11 return periods, both EP types; the tab
   title is the submission/EDM name; breadcrumbs = submission (+ EDM when
   entered from the EDM page) and link back; selections in the originating
   tab are cleared.
3. Reorder columns via the controls — the `ids` param and the columns move.
4. Switch perspective — every column follows (screen-wide).
5. Select >10 analyses: nothing blocks; the table scrolls horizontally
   (FR-015).

Contracts: [contracts/routes.md](contracts/routes.md),
[contracts/worker-poller.md](contracts/worker-poller.md),
[contracts/loss-results.md](contracts/loss-results.md). Schema:
[data-model.md](data-model.md).
