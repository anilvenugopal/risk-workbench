# Implementation Plan: Analysis Results Sync & Viewing (Iteration 8)

**Branch**: `011-analysis-results` | **Date**: 2026-08-25 | **Spec**: [spec.md](spec.md)

<!-- Technical only. User stories and scope → spec.md. Schema → data-model.md.
     Payloads → contracts/. Endpoint investigation → research.md. Everything
     above the `---` is what a reviewer reads to decide: ten minutes to read. -->

## Plan status

**Ready for tasks:** Yes
**Blocked by:** Nothing. One external prerequisite rides as a task: T-02's
irp-integration change (widen `PERSPECTIVE_CODES`) must ship before the IRP
sandbox tier can pass for WX/QS; app work proceeds against FakeIRP meanwhile.

## Design summary

- `irp_analysis` gains one nullable JSON column, `loss_results` — the bounded
  per-perspective viewing extract ([data-model.md](data-model.md), contract in
  [contracts/loss-results.md](contracts/loss-results.md)). No other results
  storage; §9's Parquet/`analysis_result_meta` machinery stays unbuilt (export
  iteration).
- New kind table `analysis_perspective_kind` seeds GR / RL / WX / QS / GU
  (Gross first = default); the retrieval worker and every perspective control
  read codes, labels, and order from it.
- `rwb_job_requestor_type_kind` gains `irp_analysis`: retrieval jobs are keyed
  `(irp_analysis, <analysis uuid>, retrieve_analysis_results)`, so the queue's
  UNIQUE key is the FR-006 dedup and views join the same key for the SC-005
  failure reason.
- New Dramatiq actor `retrieve_analysis_results` (workers/analysis_jobs.py):
  per perspective calls `get_analysis_stats` + `get_analysis_ep` (new gateway
  functions over the wheel's `get_stats`/`get_ep`), filters EP rows to
  epType OEP/AEP (TCE dropped, O-04), array-looks-up the 11 return periods
  (exact match — all present in the 10,004-point curve, research R3), and
  writes `loss_results` in one UPDATE. Empty response for a perspective →
  explicitly-empty entry (FR-004); any call failure → job `failed` with
  `error_detail`, `loss_results` untouched, analysis stays FINISHED (O-06).
- The extract snapshots `engineType`/`engineVersion` from the analysis
  metadata payload (FR-021).
- Triggers are worker-side chains, poller untouched: own —
  `backfill_analysis_detail` enqueues retrieval on success; broker —
  `backfill_rdm_analyses` enqueues one retrieval per captured live analysis
  with `loss_results IS NULL`. Broker rows are keyed (`rdm_id`, `irp_id`), so
  once-per-RDM storage and EDM-copy dedup are automatic (US2).
- Retrieval inputs: `analysis_id` = `irp_analysis.irp_id`;
  `exposure_resource_id` = `irp_portfolio.irp_id` for own rows, the stored
  `exposure_resource_id` for broker rows, with one `get_analysis_by_id` re-read
  when it is NULL (T-03 — closes spec O-02 by design).
- Currency column reads `settings_metadata ->> 'currencyCode'` in the read
  model — already captured at both backfills; no new column (T-05).
- `analysis_service` read models gain currency, per-perspective AAL, results
  state (`pending` / `failed` + reason / `ready`), and the condensed extract;
  a new submission-scoped merged read lists own analyses across the
  submission's EDMs plus its RDM broker groups.
- EDM detail: the "Analyses" and "Broker analyses" sections merge into one
  table — own rows plus expandable RDM group rows (FR-009), Currency and AAL
  columns added (FR-010), a section-wide perspective select riding the section
  URL like the existing status filter, and multi-select **View** posting to the
  dedicated page in a new tab. Delete, status filter, and the 3s self-poll
  carry over unchanged.
- Submission detail gains a Results section rendering the same merged partial
  submission-wide (the only place cross-EDM selection exists, FR-013).
- Expanded row body renders the condensed results inline: both EP types × the
  6 condensed return periods, no display toggle (FR-011).
- Dedicated page: `GET /results/analyses?ids=…[&submission=…][&edm=…]` — a
  hidden nav child of `results`; ids order = column order (FR-016, reorder
  controls rewrite the param); perspective is a query param re-rendered over
  HTMX (screen-wide, FR-012); `<title>` carries the submission/EDM name; the
  shell gains an `extra_crumbs` context hook so the page appends
  submission/EDM name crumbs after the manifest chain (FR-014).
- Units selector (ones/thousands/millions, millions default) and
  copy-with-headers are Alpine/JS display slivers over data-value attributes —
  no server round trip, no recomputation of stored numbers (FR-017/FR-018).
- UI-first: the EP table preview exists (docs/ui_previews/results_ep_table.html);
  the merged-grid preview is derivative of the existing analyses grid — built
  directly; the dedicated page reuses the approved EP-table layout.

## Material changes

| Area | Change |
|---|---|
| Database | `irp_analysis.loss_results` (NVARCHAR(MAX), nullable); new `analysis_perspective_kind` + 5 seeds; `rwb_job_requestor_type_kind` + `irp_analysis` seed. Alembic 0001 + seed_db + iteration1_mirror. |
| Worker | New `retrieve_analysis_results` actor; chain enqueues in `backfill_analysis_detail` and `backfill_rdm_analyses`; dispatch registration. |
| UI | Merged analyses partial on EDM detail + new submission Results section; inline condensed results; dedicated results page + nav node + shell `extra_crumbs`; perspective/units/copy/order controls. |
| Library | irp-integration: widen `PERSPECTIVE_CODES` (T-02). Gateway: `get_analysis_stats` / `get_analysis_ep` + FakeIRP counterparts. |

## High-risk technical decisions

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | Retrieval triggers are worker-side chains with queue-level dedup: own analyses chain from `backfill_analysis_detail`, broker from `backfill_rdm_analyses`; jobs keyed `(irp_analysis, analysis_id, retrieve_analysis_results)`; worker skips when `loss_results IS NOT NULL` | Approved | [research.md#R4](research.md#r4--retrieval-trigger-dedup-and-failure-handling-t-01) |
| T-02 | The active wheel (0.6.0rc2) hard-rejects WX/QS client-side (`PERSPECTIVE_CODES = ['GR','GU','RL']`) — irp-integration must widen the list to the full RM perspective vocabulary; no app-side workaround exists (Article 11 forbids bypassing the wheel) | Approved | [research.md#R5](research.md#r5--the-wheel-rejects-wx-and-qs-t-02); filed as [irp-integration#28](https://github.com/premiumiq/irp-integration/issues/28) — the one external dependency of this iteration |
| T-03 | `exposure_resource_id` for result calls: own rows use `irp_portfolio.irp_id` (the RM portfolioId the analysis ran against); broker rows use stored `exposure_resource_id`, one `get_analysis_by_id` re-read when NULL — closes spec O-02 by design; sandbox verifies | Approved | [research.md#R2](research.md#r2--broker-exposure-pointer-o-02) |
| T-04 | `loss_results` is written whole, once, as one JSON document: all 5 perspective keys always present, unproduced perspectives explicitly `null`, `engine_type`/`engine_version` snapshotted (FR-021) | Approved | [contracts/loss-results.md](contracts/loss-results.md) |
| T-05 | Currency comes from `settings_metadata.currencyCode` (documented field, captured at both backfills) — read-model extraction, no new column | Approved | [research.md#R6](research.md#r6--currency-and-engine-version-sources-t-05) |
| T-06 | Perspectives are a kind table (`analysis_perspective_kind`), not code constants — Article 3 default; worker request list and UI toggles read from it; Gross default = first sort_order | Approved | [data-model.md](data-model.md) |
| T-07 | Dedicated page is one route (`/results/analyses`) under the `results` nav root; entity breadcrumbs are an `extra_crumbs` shell extension appended after the manifest chain; column order = `ids` param order | Approved | [research.md#R7](research.md#r7--dedicated-page-route-breadcrumbs-and-controls-t-07-t-08); [contracts/routes.md](contracts/routes.md) |
| T-08 | An unproduced perspective returns an empty list from `get_stats`/`get_ep` (treated as explicitly empty); any non-2xx is a retrieval failure | Assumed | Sandbox tier confirms; the worker's branch is one `if not rows` either way — [research.md#R7](research.md#r7--dedicated-page-route-breadcrumbs-and-controls-t-07-t-08) |

---

## Technical Context

<!-- Only what changed or constrains the design. The stack is documented in
     docs/PRD.md §3 (Technology stack & environment); architecture rules in
     .specify/memory/constitution.md. Do not restate either. -->

**New dependencies**: None app-side. irp-integration needs one change
(T-02, widen `PERSPECTIVE_CODES`) and a new build; source-switchable via
`make irp-local` / `make irp-testpypi` during development.
**Databases touched**: `rwb_workbench` only — the extract lives on
`irp_analysis`; `rwb_exposure`/`rwb_loss`/DATABRIDGE untouched (results are
REST-only; no Parquet this iteration).

## Constitution Check

*GATE: before Phase 0 research, re-checked after Phase 1 design.*

Reviewed against all 13 articles in `.specify/memory/constitution.md`: **no
violations**.

Material interactions — where an article actively shapes this design:

- **Article 11 (IRP result work behind an interface)**: `get_analysis_stats` /
  `get_analysis_ep` / the `get_analysis_by_id` fallback run only in the
  `retrieve_analysis_results` worker. Every results view reads
  `irp_analysis.loss_results`; no Risk Modeler call serves a page render
  (spec non-negotiable 1). The wheel's perspective validation is why T-02 is a
  wheel change, not an app bypass.
- **Article 5 (mechanical follow-up auto-fires)**: retrieval is the mechanical
  consequence of "run finished" / "RDM imported" — enqueued by the backfill
  workers with no analyst click. Viewing N-up is the judgment step and waits
  for the View click.
- **Article 3 (kind tables)**: `analysis_perspective_kind` and the
  `irp_analysis` requestor kind are seeded kind rows. `perspectiveCode` /
  `epType` strings inside `loss_results` mirror RM's response vocabulary
  (data, not internal enum literals).
- **Article 10 (SQL queue)**: retrieval is a standard `rwb_job` — atomic
  claim, heartbeat, reconciler recovery is the FR-007 "interrupted work
  recovers automatically". A failed retrieval stays terminal (never
  resurrected by dedup), which is exactly O-06's contract.
- **Article 8 (server-rendered)**: perspective switching is an HTMX fragment
  re-render with a query param (screen-wide by construction); Alpine covers
  only units formatting, clipboard copy, and column reorder; the dedicated
  page has a real URL whose `ids` order is the display order.
- **Article 1 (nav manifest)**: the dedicated page is one nav node + one
  handler + one template; the `extra_crumbs` hook is a shell-template
  extension, not a second breadcrumb source (structure still derives from the
  manifest chain).
- **Article 2 (sequencing derived)**: no retrieval state machine — the chain
  is an idempotent enqueue at completion; "results pending" is computed from
  `loss_results IS NULL` + the retrieval job row, never stored.

## Project Structure

### Documentation (this feature)

```text
specs/011-analysis-results/
├── plan.md              # This file
├── research.md          # R1–R3 (spec phase) + R4–R7 (this plan)
├── data-model.md        # loss_results column, perspective kind, requestor kind seed
├── quickstart.md        # Per-story verification
├── contracts/
│   ├── loss-results.md  # The extract JSON contract
│   ├── worker-poller.md # Retrieval worker, chain enqueues, failure handling
│   ├── routes.md        # Merged section, submission Results section, dedicated page
│   └── irp-gateway.md   # get_analysis_stats / get_analysis_ep + FakeIRP
└── tasks.md             # /speckit-tasks output (not created by /speckit-plan)
```

### Source Code (changed directories only)

```text
app/
├── routers/edms.py                  # merged analyses section (perspective param, View)
├── routers/submissions.py           # Results section fragment
├── routers/shell.py                 # /results/analyses dedicated page
├── services/analysis_service.py     # merged read models, extract/currency extraction
├── services/irp_gateway.py          # get_analysis_stats / get_analysis_ep
├── workers/analysis_jobs.py         # retrieve_analysis_results; chain in backfill_analysis_detail
├── workers/entity_jobs.py           # chain in backfill_rdm_analyses
├── workers/dispatch.py              # new actor registration
├── nav/manifest.py                  # results.analyses hidden node
├── templates/base/shell.html        # extra_crumbs hook
├── templates/pages/results_analyses.html        # dedicated page (new)
├── templates/partials/              # merged analyses section + row partials,
│                                    # inline condensed results, results toolbar
└── static/js/app.js                 # units/copy/reorder slivers

alembic/versions/0001_initial.py     # loss_results, analysis_perspective_kind, requestor seed
infra/scripts/seed_db.py             # same seeds
tests/iteration1_mirror.py           # SQLite DDL + seeds mirror
tests/unit/  tests/sqlserver/  tests/irp/
docs/DATA_MODEL.md                   # already revised (spec phase)
```

**Structure Decision**: existing single-app layout; no new top-level
directories. New files: one page template, two-to-three partials, one contract
set.

## Complexity Tracking

No constitution violations to justify.

## Testing

<!-- Strategy by tier. Not a test-file inventory. -->

- **Unit**: extract builder against the captured response shapes (stats
  `purePremium`/`totalStdDev`; EP epType filtering incl. TCE-drop; 11-point
  lookup; empty perspective → explicit null); worker idempotency
  (`loss_results` set → skip; enqueue dedup on re-fired triggers); chain
  enqueues from both backfills; read models (currency, per-perspective AAL,
  results state incl. failed-with-reason join); routes render merged table,
  inline condensed block, results-pending, and the dedicated page with ids
  ordering and perspective param. FakeIRP gains stats/EP fixtures.
- **SQL Server integration**: migration + seeds land (`loss_results`,
  `analysis_perspective_kind`, requestor kind row); retrieval enqueue dedup
  under the real UNIQUE key; extract write/read round-trip on NVARCHAR(MAX).
- **IRP sandbox**: one own analysis end-to-end — all five perspectives after
  the T-02 wheel change (WX/QS acceptance), the T-08 empty-perspective shape,
  and the broker pointer (T-03/O-02) against an RDM-imported analysis.
