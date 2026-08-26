# Implementation Plan: Analysis Results Sync & Viewing (Iteration 8)

**Branch**: `011-analysis-results` | **Date**: 2026-08-25 | **Spec**: [spec.md](spec.md)

<!-- Technical only. User stories and scope → spec.md. Schema → data-model.md.
     Payloads → contracts/. Endpoint investigation → research.md. Everything
     above the `---` is what a reviewer reads to decide: ten minutes to read. -->

## Plan status

**Ready for tasks:** Yes
**Blocked by:** Nothing.

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
  `error_detail`, `loss_results` untouched, analysis stays FINISHED (O-06). The
  extract snapshots `engineType`/`engineVersion` from the analysis metadata
  payload (FR-021).
- Triggers are worker-side chains, poller untouched: own —
  `backfill_analysis_detail` enqueues retrieval on success; broker —
  `backfill_rdm_analyses` enqueues one retrieval per captured live analysis
  with `loss_results IS NULL`. Broker rows are keyed (`rdm_id`, `irp_id`), so
  once-per-RDM storage and EDM-copy dedup are automatic (US2).
- Retrieval inputs: `analysis_id` = `irp_analysis.irp_id`;
  `exposure_resource_id` = `irp_portfolio.irp_id` for own rows, the stored
  `exposure_resource_id` for broker rows, with one `get_analysis_metadata`
  re-read when it is NULL (T-03 — closes spec O-02 by design).
- Currency column reads `currencyCode` out of the parsed `settings_metadata` in
  the read model — already captured at both backfills; no new column, and no
  JSON extraction in SQL (T-05).
- `analysis_service` read models gain currency, per-perspective AAL and
  standard deviation, results
  state (`pending` / `failed` + reason / `ready`), and the condensed extract;
  a new submission-scoped merged read lists own analyses across the
  submission's EDMs plus its RDM broker groups.
- EDM detail: the "Analyses" and "Broker analyses" sections merge into one
  table — own rows plus expandable RDM group rows (FR-009), one column set for
  both origins (FR-010), and multi-select **View** posting to the dedicated
  page in a new tab. The section summary line gains **Copy table** and **View**
  next to the status filter and Delete; no perspective and no units control
  ride the table (O-12). Delete, status filter, and the 3s self-poll carry over
  unchanged. Nothing outside the analyses sections changes on either page.
- Submission detail gains a Results section rendering the same merged partial
  submission-wide (the only place cross-EDM selection exists, FR-013), with an
  EDM column after Analysis — the same `show_edm` flag the broker row takes.
- Expanded row body is two flex columns that stack when narrow: on the left the
  source line then the **Metadata** and **Analysis settings** groups (O-11,
  FR-022); on the right the condensed results — both EP types × the 6 condensed
  return periods, then AAL and standard deviation as the last two rows, with the
  perspective toggle in the row and no display toggle (FR-011). Values wrap and
  carry `title` tooltips (FR-023).
- Gathering what the expanded row reads (FR-022/FR-024/FR-025): `AnalysisSettings`
  gains `framework` — `_to_display` folds `analysisFramework` into
  `analysis_mode` today, so ELT and the mode compete for one slot;
  `BrokerAnalysis` gains `rm_url` and `created_at` (RM `createDate`) so broker
  rows fill the Risk Modeler and Submitted columns; and own runs get a
  submitted-settings snapshot (T-09) for the four template settings and the
  currency scheme/vintage, which Risk Modeler never returns.
- Dedicated page: `GET /results/analyses?ids=…[&submission=…][&edm=…]` — a
  hidden nav child of `results`; ids order = column order (FR-016, reorder
  controls rewrite the param); perspective is a query param re-rendered over
  HTMX (screen-wide, FR-012); `<title>` carries the submission/EDM name; the
  shell gains an `extra_crumbs` context hook so the page appends
  submission/EDM name crumbs after the manifest chain (FR-014).
- Copy-with-headers (both pages) and the units selector (dedicated page only,
  millions default, FR-017) are Alpine/JS display slivers over `data-value`
  attributes — no server round trip, no recomputation of stored numbers
  (FR-018). The Submitted column is the third such sliver (T-10).
- UI-first: `docs/ui_previews/merged_analyses_table.html` is approved (merged
  table, expanded row, results states, section summary line, empty states).
  `results_ep_table.html` is superseded — its Display and EP-type selectors, its
  Millions/Full units selector, its three-perspective dropdowns and its ELT
  metrics were all dropped on 8/25–8/26; the dedicated page gets its own preview
  approved at the start of Phase 6. **An approved preview is
  guidance, not markup to paste.** Build against the real components and CSS —
  `.dtable` in `app/static/css/details.css`, the status chips, `btn-sm`, the
  section summary line — and extend those when the preview needs something they
  do not have, rather than adding preview-only classes.

## Material changes

| Area | Change |
|---|---|
| Database | `irp_analysis.loss_results` and `irp_analysis.submitted_settings` (both NVARCHAR(MAX), nullable); new `analysis_perspective_kind` + 5 seeds; `rwb_job_requestor_type_kind` + `irp_analysis` seed. Alembic 0001 + seed_db + iteration1_mirror. |
| Worker | New `retrieve_analysis_results` actor; chain enqueues in `backfill_analysis_detail` and `backfill_rdm_analyses`; `_claim_analysis` writes `submitted_settings` (T-09); dispatch registration. |
| Service | `AnalysisSettings.framework`; `BrokerAnalysis.rm_url` + `created_at`; results state, per-perspective AAL and standard deviation, currency, condensed extract, and the submitted-settings read. |
| UI | Merged analyses partial on EDM detail + new submission Results section; two-column expanded row; dedicated results page + nav node + shell `extra_crumbs`; perspective/units/copy/order controls. Nothing outside the analyses sections is touched. |
| Library | Gateway: `get_analysis_stats` / `get_analysis_ep` + FakeIRP counterparts. |

## High-risk technical decisions

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | Retrieval triggers are worker-side chains with queue-level dedup: own analyses chain from `backfill_analysis_detail`, broker from `backfill_rdm_analyses`; jobs keyed `(irp_analysis, analysis_id, retrieve_analysis_results)`; worker skips when `loss_results IS NOT NULL` | Approved | [research.md#R4](research.md#r4--retrieval-trigger-dedup-and-failure-handling-t-01) |
| T-02 | All five perspectives are requested through the wheel, never around it (Article 11). irp-integration 0.6.2 validates `perspective_code` against the full RM vocabulary, so WX and QS pass client-side | Approved | [research.md#R5](research.md#r5--the-wheel-rejects-wx-and-qs-t-02) — the earlier `['GR','GU','RL']` list and the widening that closed it |
| T-03 | `exposure_resource_id` for result calls: own rows use `irp_portfolio.irp_id` (the RM portfolioId the analysis ran against); broker rows use stored `exposure_resource_id`, one `get_analysis_metadata` re-read when NULL (the gateway function, which wraps the wheel's `get_analysis_by_id`) — closes spec O-02 by design; sandbox verifies | Approved | [research.md#R2](research.md#r2--broker-exposure-pointer-o-02) |
| T-04 | `loss_results` is written whole, once, as one JSON document: all 5 perspective keys always present, unproduced perspectives explicitly `null`, `engine_type`/`engine_version` snapshotted (FR-021) | Approved | [contracts/loss-results.md](contracts/loss-results.md) |
| T-05 | Currency comes from `settings_metadata.currencyCode` (documented field, captured at both backfills) — parsed in Python by the read model like every other `settings_metadata` field, never extracted in SQL; no new column | Approved | [research.md#R6](research.md#r6--currency-and-engine-version-sources-t-05) |
| T-06 | Perspectives are a kind table (`analysis_perspective_kind`), not code constants — Article 3 default; worker request list and UI toggles read from it; Gross default = first sort_order | Approved | [data-model.md](data-model.md) |
| T-07 | Dedicated page is one route (`/results/analyses`) under the `results` nav root; entity breadcrumbs are an `extra_crumbs` shell extension appended after the manifest chain; column order = `ids` param order | Approved | [research.md#R7](research.md#r7--dedicated-page-route-breadcrumbs-and-controls-t-07-t-08); [contracts/routes.md](contracts/routes.md) |
| T-08 | An unproduced perspective returns an empty list from `get_stats`/`get_ep` (treated as explicitly empty); any non-2xx is a retrieval failure | Assumed | Sandbox tier confirms; the worker's branch is one `if not rows` either way — [research.md#R7](research.md#r7--dedicated-page-route-breadcrumbs-and-controls-t-07-t-08) |
| T-09 | The expanded row's Analysis settings read a per-analysis snapshot, `irp_analysis.submitted_settings`, written by `_claim_analysis` from the approved plan item it submits. Not the `analysis_template` row: templates are editable, so a later edit would misreport a finished run (AGENTS.md architecture rule 8 — approved plans are immutable). Not the `execute_analysis_batch` `input_data` either — a work order is not a display source, and the read would be a two-hop JSON index lookup per row | Approved | [data-model.md](data-model.md) §1b |
| T-10 | Submitted renders client-side: the server writes UTC into `<time datetime="…Z">` and a JS sliver formats it with `toLocaleString` (FR-024). The server has no way to know the reader's timezone, and the value is display-only | Approved | preview `docs/ui_previews/merged_analyses_table.html` |

---

## Technical Context

<!-- Only what changed or constrains the design. The stack is documented in
     docs/PRD.md §3 (Technology stack & environment); architecture rules in
     .specify/memory/constitution.md. Do not restate either. -->

**New dependencies**: None. The active irp-integration build is 0.6.2
(TestPyPI pin), which accepts every perspective this iteration requests.
**Databases touched**: `rwb_workbench` only — the extract lives on
`irp_analysis`; `rwb_exposure`/`rwb_loss`/DATABRIDGE untouched (results are
REST-only; no Parquet this iteration).

## Constitution Check

*GATE: before Phase 0 research, re-checked after Phase 1 design.*

Reviewed against all 13 articles in `.specify/memory/constitution.md`: **no
violations**.

Material interactions — where an article actively shapes this design:

- **Article 11 (IRP result work behind an interface)**: `get_analysis_stats` /
  `get_analysis_ep` / the `get_analysis_metadata` fallback run only in the
  `retrieve_analysis_results` worker. Every results view reads
  `irp_analysis.loss_results`; no Risk Modeler call serves a page render
  (spec non-negotiable 1). WX and QS reach RM through the wheel's own
  perspective validation, never around it (T-02).
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
  enqueues from both backfills; read models (currency, per-perspective AAL and
  standard deviation, results state incl. failed-with-reason join); routes render merged table,
  inline condensed block, results-pending, and the dedicated page with ids
  ordering and perspective param. FakeIRP gains stats/EP fixtures.
- **SQL Server integration**: migration + seeds land (`loss_results`,
  `analysis_perspective_kind`, requestor kind row); retrieval enqueue dedup
  under the real UNIQUE key; extract write/read round-trip on NVARCHAR(MAX).
- **IRP sandbox**: one own analysis end-to-end — all five perspectives
  (RM's own WX/QS acceptance, T-02), the T-08 empty-perspective shape,
  and the broker pointer (T-03/O-02) against an RDM-imported analysis.
