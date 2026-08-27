# Quickstart — verifying Analysis Results Sync & Viewing (spec 011)

Prerequisites: the Docker stack up (`make dev-up`, developer-run), DB rebuilt
after the schema edits (`make db-rebuild` — destructive, developer's call),
and irp-integration 0.6.2 or newer for live retrieval (`make irp-status`).

## Test tiers

```bash
uv run pytest tests/unit        # any host shell; SQLite + FakeIRP
make test-sql                   # linux-box; migration/seeds + dedup + round-trip
make shell && uv run pytest tests/irp --run-irp   # sandbox: WX/QS, T-08, broker pointer
```

## US1 — own analysis loss numbers (P1)

1. From an EDM with portfolios, Execute Template (spec 010) and wait for
   FINISHED (~minutes, 3s self-poll).
2. Expand the row. Expect, with **no further action** in between, the loss
   numbers already there (≤10 min from FINISHED, SC-001) — expanding is the only
   click, and nothing on the page asked for a retrieval. Until they arrive the
   expansion reads results-pending. (The Currency and AAL *columns* come with the
   merged table in US3; the collapsed row is unchanged in US1.)
3. In the expansion — left: the source line carrying the full analysis name,
   then Metadata (engine version, analysis type, subperil, framework, event rate
   scheme, unrecognized construction and occupancy, run by) — the construction
   and occupancy value matches what the run was submitted with, and editing the
   template afterwards does not change it. Fields today's expansion shows that
   are gone by decision (O-11): Construction, Line of business, Term, Loss
   amplification (PLA), currency, min loss threshold, franchise deductible.
   Engine type, Region, Peril, Portfolio and Template are not gone — they read
   from the merged table's columns. Right: OEP + AEP at
   50/100/250/500/1000/10000, then AAL and Std dev, with the perspective toggle
   GR/RL/WX/QS/GU, Gross default; a perspective the analysis did not produce
   shows as absent, not as an error.
4. Narrow the window until the two columns stack, and check that a long event
   rate scheme name wraps instead of clipping (FR-023).
5. Failure path: point the sandbox creds at an invalid tenant (or kill the
   worker mid-retrieval and let the reconciler reclaim) — the analysis stays
   FINISHED/ready, the retrieval `rwb_job` ends `failed` with `error_detail`,
   and the expansion keeps showing results-pending with the reason (SC-005).
6. Re-trigger check: re-run the RDM sync / re-fire the backfill — no second
   retrieval job, no changed `loss_results` (FR-006):
   `SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='retrieve_analysis_results' AND requestor_id='<analysis-id>'` → 1.

## US2 — broker results on RDM import (P2)

1. Import a broker RDM (or bundle) into a submission.
2. Expect: after `backfill_rdm_analyses` completes, one
   `retrieve_analysis_results` job per broker analysis, then the same loss
   numbers in every broker row's expansion — no click but the expand itself.
   (AAL and currency as *columns* arrive with the merged table in US3.)
3. Import a second EDM copy of the same RDM: no new retrieval jobs, identical
   numbers on both copies (SC-002) —
   `SELECT rdm_id, irp_id, loss_results FROM irp_analysis WHERE rdm_id='<rdm>'`
   shows one row per source analysis.
4. Expand a broker row: the analysis template and all four analysis settings are
   listed and read as not returned (FR-022); the row carries a Risk Modeler link
   and a Submitted date from the broker's own run (FR-024/FR-025).
5. No broker row anywhere names a portfolio (FR-020).

## US3 — merged table (P2)

1. Open the EDM detail: **one** Analyses section — own rows directly (their
   portfolio and template, no RDM), broker rows under expandable RDM group
   rows. Columns read Portfolio · Template · Peril · Region · Engine ·
   Currency · AAL · Status · Submitted · Risk Modeler, with no perspective and no units control on the
   table. The Currency and AAL columns now carry the numbers US1 and US2 put in
   the expansions — Gross, in millions — and read `retrieving…` /
   `retrieval failed` / `—` for the other three results states.
2. Open the submission detail: the new Results section lists the same merged
   shape across all the submission's EDMs and RDMs, with an EDM column after
   Template. Add up the analyses listed on each of the submission's EDM detail
   pages and expand every RDM group here: the Results section lists all of them,
   with no cap, no pagination and no "showing N of M" (SC-004).
3. Submitted reads in your own timezone with seconds and AM/PM; change the
   machine's timezone and reload to confirm it follows (FR-024).
4. Nothing outside the analyses sections changed on either page.
5. Copy: the table lands in Excel with headers intact (SC-006).

## US4 — dedicated results page (P3)

1. Multi-select 3–5 analyses (mix own + broker) on either page → **View**.
2. Expect: new browser tab, URL `/results/analyses?ids=…`, one column per
   analysis in selection order, all 11 return periods for the selected EP type,
   and AAL + Std dev as the last two rows; the tab
   title is the submission/EDM name; breadcrumbs = submission (+ EDM when
   entered from the EDM page) and link back; selections in the originating
   tab are cleared.
3. Reorder columns via the controls — the `ids` param and the columns move.
4. Switch perspective, then EP type — every column follows both (screen-wide),
   and AAL and Std dev stay put across the EP-type switch.
5. Units selector: values never auto-switch; ones/thousands/millions apply on
   selection (millions default) — this page is the only place it exists.
6. Select >10 analyses: nothing blocks; the table scrolls horizontally
   (FR-015).

Contracts: [contracts/routes.md](contracts/routes.md),
[contracts/worker-poller.md](contracts/worker-poller.md),
[contracts/loss-results.md](contracts/loss-results.md). Schema:
[data-model.md](data-model.md).
