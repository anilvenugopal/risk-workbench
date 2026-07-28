# Implementation Plan: EDM/RDM Details & Backfill (Iteration 3)

**Branch**: `004-edm-rdm-details-backfill` | **Date**: 2026-07-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-edm-rdm-details-backfill/spec.md`

---

## Summary

Iteration 2 (spec 003) made the workbench *do work* against Risk Modeler and tracked the background jobs, but an imported EDM was still a near-empty record — the analyst could see it reached *ready*, not *what is in it*. This iteration closes that gap with **detail and backfill**, and its center of gravity is the **portfolio**, not the EDM: analyses are run against a portfolio, so the redesigned EDM detail page leads with a read-only **inline per-portfolio breakdown** (location/account/policy counts, perils/sub-perils, geography, currency, record volume) — the P1 headline. The EDM itself carries only light context plus a **compact aggregate rollup strip** (P3, quick orientation) that is *derived* from the stored per-portfolio detail; the submission page's spec-003 package cards gain a matching per-EDM aggregate line. **Treaties** (coded at the EDM level) get a full-attribute expand/collapse view with **Excel export**; opening an imported **RDM** surfaces its **broker analyses grouped by `rdm_id`** with each analysis's **settings/metadata** (no loss numbers this iteration).

The mechanism is a **forward extension of the existing Iteration-2 poller→worker completion path**. On an `import_edm` reaching terminal `FINISHED`, the poller — in the *same* handler that already backfills the exposureId and enqueues `upload_rdm` — additionally enqueues a new **`backfill_edm_detail`** `rwb_job`; a worker performs the Risk Modeler REST reads (portfolio enumeration + per-portfolio exposure figures + treaty attributes) and persists them. Broker-analysis **metadata** rides on the *existing* `backfill_rdm_analyses` worker (already enqueued on `import_rdm` FINISHED), extended to also capture each analysis's settings. No detail fetch runs on a web request path, and the poller loop body never performs a fetch (it stays a single-status-check batch). Automatic backfill is **forward-only** — entities that completed before this capability ships stay in a graceful empty state until re-imported or manually synced; there is no bulk sweep. *(Amended 2026-07-23 post-US1: a per-EDM **Sync** action on the EDM detail page re-runs the same `backfill_edm_detail` worker on demand.)*

The central *new* design question this iteration answers (the analog of spec-003's A21) is **where the backfilled detail is stored**: the DATA_MODEL §5/§6 entity tables are thin identity/lineage records with no exposure figures, treaty attributes, or analysis metadata. The resolution (research **R2**) is a **JSON snapshot cache column** on each entity — `irp_portfolio.exposure_detail`, `irp_treaty.attributes`, `irp_analysis.settings_metadata` — displayed as a read-only textual snapshot, never queried/filtered internally this iteration (filtering/splitting is Iteration 4). The EDM-aggregate figures are **derived in the query layer** from those snapshots, never stored and never fetched on the request path.

Technical approach: extend the existing FastAPI + Jinja2 + HTMX stack — redesign the EDM detail page/route, extend the RDM detail page for broker analyses, extend the spec-003 package cards with a per-EDM aggregate line; add one `rwb_job_type` (`backfill_edm_detail`) and extend one existing worker (`backfill_rdm_analyses`); extend the poller's `import_edm` FINISHED handler with one idempotent enqueue; add `irp_portfolio` + `irp_treaty` tables and detail columns to `irp_analysis` in the single `0001_initial.py` revision (drop-create-seed); add `openpyxl` for the treaty `.xlsx` export. Risk Modeler is reached only through `app/services/irp_gateway.py` (fake in CI), extended with the read methods for portfolio/treaty/analysis detail — **confirm-against-active-wheel before implementing** (the wheel is pre-release; gaps tracked in `docs/IRP_INTEGRATION_FOLLOWUPS.md`).

---

## Technical Context

**Language/Version**: Python 3.12 (inherited from Iterations 0–2; `pyproject.toml` `requires-python = ">=3.12"`).

**Primary Dependencies** (existing, reused):
- `fastapi` + `uvicorn[standard]`, `jinja2` + HTMX (Alpine.js for the treaty expand/collapse + per-portfolio table slivers) — server-rendered (Article 8).
- `dramatiq[redis]` + `redis` — the worker tier that runs every Risk Modeler read (Article 10/11); `rwb_job` SQL table stays the queue of record.
- `sqlalchemy>=2.0` (Core) + `pyodbc`, `alembic` — WORKBENCH schema via the `db/` package; single `0001_initial.py` revision.
- **`irp-integration[databridge]`** — the sole path to Risk Modeler, source-switchable PyPI(`0.2.0`, prod default)/TestPyPI/local via `make irp-*`. Reached only through `app/services/irp_gateway.py`. **This iteration adds *read* methods** (portfolio enumeration + per-portfolio exposure figures, treaty attribute detail, broker-analysis settings/metadata) — all single-status/read, never `poll_*_to_completion`. The library is pre-release; **re-confirm every new method + signature against the active wheel (`make irp-status`) before implementing** (research R1).

**New Dependency** (one): **`openpyxl`** — server-side `.xlsx` generation for the treaty export (FR-024). Added to `[project.dependencies]`. No client-side or external service; the workbook is built in-process from stored treaty detail.

**Storage**: SQL Server 2022 (`rwb_workbench`) — **WORKBENCH connection only** this iteration. New: `irp_portfolio`, `irp_treaty` tables (first created here — deferred in spec 003 / research R13); new detail columns on `irp_analysis` (`settings_metadata`, `is_group`, `exposure_resource_id` — the portfolio-linkage pointer, R9; `group_parent_id` deferred); one new `rwb_job_type_kind` seed row (`backfill_edm_detail`). **No EXPOSURE/LOSS access** (results retrieval + Loss Repository are Iteration 6+); **DATABRIDGE never touched.** Risk Modeler holds the source of truth; the workbench stores a **read/cache snapshot** of the detail.

**Testing** (Article 12, three tiers):
- `pytest tests/unit` — SQLite via `db.register_engine` + the **fake IRP** (`tests/unit/fakes/fake_irp.py`, extended with the new read methods): the `backfill_edm_detail` worker (idempotent upsert of portfolios/treaties + JSON snapshot), the extended `backfill_rdm_analyses` metadata capture, the **aggregate-rollup derivation** (sum counts / union perils / combine geography+currency from per-portfolio snapshots), graceful-empty rendering (no snapshot → pending/unavailable state, no error), and the poller's extended `import_edm` FINISHED enqueue (both `upload_rdm` **and** `backfill_edm_detail`, idempotent).
- `pytest tests/sqlserver --run-sqlserver` — real driver: the extended migration builds `irp_portfolio` / `irp_treaty` + the new `irp_analysis` columns with FKs; the `backfill_edm_detail` idempotent upsert (re-run overwrites the snapshot in place, no duplicate portfolio/treaty rows).
- `pytest tests/irp --run-irp` — opt-in sandbox: the real portfolio-enumeration / per-portfolio exposure / treaty-attribute / analysis-metadata read round-trips; an assertion that `poll_*_to_completion` appears nowhere in the new worker/gateway code.

**Target Platform**: Linux server (WSL2 native dev: uvicorn + poller + Dramatiq worker + Redis + SQL Server container; mirrors the `linux-box` / `sqlserver` split).

**Project Type**: Server-rendered web application (FastAPI + Jinja2 + HTMX) with two out-of-process background components (poller, Dramatiq worker). Single project; extends the existing `app/` tree.

**Performance Goals**:
- **EDM detail page renders from stored detail** — never computes exposure figures on the request path, so it meets the normal page-load budget regardless of exposure size, including ~1M+ record portfolios and 25-portfolio EDMs (FR-018 / SC-007). Target < 300 ms p95.
- Backfill is bounded by the worker tier, not the analyst: an EDM's detail is populated within ~1 minute of import completion (SC-001) — one poll interval (~15 s) to enqueue + one worker pass to fetch/persist.
- Treaty `.xlsx` export streams from stored detail (no Risk Modeler call) — target < 1 s for a typical treaty set.

**Constraints**:
- **IRP discipline (Article 11):** the detail *reads* (portfolio/treaty/analysis-metadata) run **only in the worker**, never the web layer and never in the poller loop body; the poller stays single-status-check + enqueue. `poll_*_to_completion` forbidden everywhere. The web layer reads only **stored** detail.
- **Storage shape (research R2):** detail is a **JSON snapshot cache** (`NVARCHAR(MAX)`), nullable (null ⇒ graceful empty state). It stores external Risk Modeler vocabularies (perils/geography/currency) verbatim — no internal code dispatches on them, so no kind table is minted for RM's evolving vocabularies (the same rationale as the Article 3 external-mirror carve-out). Backfill overwrites the snapshot **in place** (idempotent, FR-004).
- **No new event-sourced status (Article 4):** `irp_portfolio`/`irp_treaty` carry no status; `irp_edm.status` is unchanged; the detail cache and its `as_of` are updated in place. `submission.status_code` remains the only event-sourced status.
- **No row-level security (Article 6):** no `customer_id`/scope on `irp_portfolio`/`irp_treaty`/`irp_analysis`; every analyst sees every EDM's detail.
- All SQL via the `db/` safe bound-parameter path (Article 7); the worker's upsert uses `get_connection("WORKBENCH")` + explicit `conn.begin()`. The trusted-script path is **not** used (no EXPOSURE/LOSS writes this iteration). CSRF on the (few) state-changing routes; viewing + Excel export are authenticated GETs; function-level role gating server-side (Article 13).
- **Forward-only automatic backfill (FR-003, amended 2026-07-23):** no bulk sweep; only imports completing after deploy are backfilled automatically. A per-EDM manual **Sync** action re-runs the same worker on demand (keyed `analyst_request` + `edm_id`, revived via `ensure_pending_rwb_job`).

**Scale/Scope**: ~10–30 internal analysts; an EDM carries 1–25 portfolios; a treaty set can be dozens wide. New schema: 2 entity tables (`irp_portfolio`, `irp_treaty`) + 3 columns on `irp_analysis` + 1 kind seed row, folded into the single revision. Code: 1 new `rwb_job_type` worker body + 1 extended worker + 1 poller-handler extension + ~4 gateway read methods + fake mirrors; the redesigned EDM detail page, the RDM broker-analysis view, the package-card per-EDM aggregate line, and the treaty Excel export.

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Article | Title | Status | Notes |
|---------|-------|--------|-------|
| 1 | Navigation Manifest Is the One Versioned Source of Truth | ✅ | **No new nav nodes.** The EDM/RDM library + detail nodes (`irp.edm_library` / `irp.rdm_library`) already exist (spec 003 / R12); this iteration *redesigns the content* of `/edms/{id}` and extends `/rdms/{id}`, reachable from the libraries and the submission package cards. Rail/sidebar/breadcrumb/active-state inherited — no scattered config. |
| 2 | Sequencing Is Derived, Not Stored | ✅ | No stored topology. Backfill is a mechanical follow-up chained off `import_edm` / `import_rdm` FINISHED via the existing name-coupled prerequisite gate (the poller's terminal handler), keyed by `(requestor_type='irp_job', requestor_id=<finished irp_job.id>, rwb_job_type)`. The EDM-aggregate is **derived in code** from stored per-portfolio detail, not a stored rollup. |
| 3 | Categoricals Are Kind Tables, Never Enums — Except External-Status Mirrors | ✅ | New `rwb_job_type_kind` row `backfill_edm_detail` (kind table — closed, app-defined set). `irp_analysis.status_code` stays a kind FK. **The detail JSON snapshots (perils/sub-perils/geography/currency) are external Risk Modeler vocabularies stored verbatim as a cache — no internal code path dispatches on them** — so they are *not* enum literals and correctly avoid a kind table (minting one would need a seed migration every time RM adds a peril/region, the exact crash-risk the Article 3 carve-out guards against). No new status enum literals in code. |
| 4 | Status Is Event-Sourced with Cached Current | ✅ | No new event-sourced status. `irp_portfolio`/`irp_treaty` carry no status column; `irp_edm.status`/`irp_rdm.status` unchanged. The detail cache + `as_of` are plain in-place updates on backfill (idempotent). `submission.status_code` remains the sole event-sourced status. |
| 5 | Mechanical Follow-up Auto-fires; Judgment Waits for a Click | ✅ | The detail backfill is a **direct mechanical consequence** of one import intent (import FINISHED → fetch its detail) and **auto-fires** from the poller/worker — no analyst click. Viewing detail, expanding a treaty, and exporting to Excel are analyst reads, not judgment gates. No judgment step (choosing/splitting portfolios, running analyses) is auto-fired — those are Iteration 4/6. |
| 6 | No Row-Level Security; All Authenticated Analysts See All Deals | ✅ | No `customer_id`/`apply_scope`/scope column on `irp_portfolio`/`irp_treaty`/`irp_analysis` or any detail view. Every authenticated analyst reads every EDM's per-portfolio detail, treaties, and broker analyses. Roles gate functions, not rows. |
| 7 | One Data-Access Package, Two Paths (`/db`) | ✅ | All detail reads/writes go through the `db.execute*` safe bound-parameter path; the worker's idempotent portfolio/treaty upsert uses `db.get_connection("WORKBENCH")` + explicit `conn.begin()`. The trusted-script path (`db.scripts`) is **not** imported (no EXPOSURE/LOSS writes this iteration). |
| 8 | Server-Rendered; No SPA | ✅ | Jinja2 + HTMX: the redesigned EDM detail page, the per-portfolio table, treaty expand/collapse, the RDM broker-analysis view. Alpine.js only for the collapse/expand + horizontal-scroll slivers. The `.xlsx` export is a normal authenticated GET returning a file download. Real URLs; breadcrumb/active-state from the manifest. No client-side app/state store. |
| 9 | Styling Extends ITCSS via Tokens | ✅ | The per-portfolio table, aggregate strip, treaty rows, and broker-analysis cards are styled via named design tokens layered into the ITCSS layers — no hardcoded hex, no flat append-sheets. |
| 10 | The SQL Table Is the Queue; Single Worker by Default | ✅ | `backfill_edm_detail` is a new `rwb_job_type` on the **same** SQL-backed queue (single worker, atomic claim, heartbeat, reconciler) — no new queue machinery. The extended `backfill_rdm_analyses` keeps its existing lifecycle. Idempotent enqueue on the existing `UNIQUE(requestor_type, requestor_id, rwb_job_type)` key. |
| 11 | IRP Polling and Result Work Behind Interface; Submission on Request Path Permitted | ✅ | **Central to this iteration.** Every Risk Modeler detail read (portfolio enumeration, per-portfolio exposure, treaty attributes, analysis metadata) runs in the **Dramatiq worker**, via `irp_gateway`, never the web layer and never in the poller loop body. The poller stays single-status-check + idempotent enqueue. `poll_*_to_completion` forbidden. The web layer reads only stored detail (viewing + Excel export make **no** Risk Modeler call). |
| 12 | Test-First, Three Connected Strategies | ✅ | Unit (SQLite + fake IRP) covers the backfill worker, the extended analysis-metadata capture, the **aggregate-rollup derivation**, graceful-empty rendering, and the poller enqueue extension. SQL-Server tier covers the `irp_portfolio`/`irp_treaty` migration + idempotent upsert. IRP tier (opt-in) covers the real read round-trips and asserts `poll_*_to_completion` absence. |
| 13 | Authentication & Secrets | ✅ | Reuses Iteration-0 auth; viewing + Excel export are authenticated GETs; CSRF unchanged on the existing state-changing routes (no new state-changing route added — backfill is worker-driven). Risk Modeler credentials remain env-sourced via `IRPClient()`; no new secrets. `openpyxl` adds no credential surface. |

**Constitution Check: PASSED — no violations. No Complexity Tracking entries required.**

> Re-check after Phase 1 design: **still PASSED** (see end of plan). The design adds no scoping key and no stored sequence; the detail cache is updated in place (no new event-sourced status); the JSON snapshot correctly avoids a kind table for external RM vocabularies; every Risk Modeler read stays in the worker behind the gateway; the aggregate is a derived query, not a stored rollup.

---

## Project Structure

### Documentation (this feature)

```text
specs/004-edm-rdm-details-backfill/
├── plan.md              ← this file (/speckit-plan output)
├── research.md          ← Phase 0 output (R1–R9: the decisions the spec/DATA_MODEL leave to planning)
├── data-model.md        ← Phase 1 output (irp_portfolio / irp_treaty + irp_analysis detail columns; JSON snapshot shape)
├── ui.md                ← Phase 1 output (page-composition contract; unified .dtable; portfolio↔analysis linkage display; states)
├── quickstart.md        ← Phase 1 output (rebuild + test + manual end-to-end walkthrough)
├── contracts/           ← Phase 1 output
│   ├── data-access.md    ← service/query contract (edm-detail read + rollup, treaty export, broker-analysis view)
│   ├── http-routes.md    ← redesigned EDM detail, RDM broker-analysis view, treaty .xlsx export, package-card line
│   └── worker-poller.md  ← the backfill mechanism: backfill_edm_detail worker, extended backfill_rdm_analyses, poller enqueue
├── checklists/
│   └── requirements.md   ← spec quality checklist (created by /speckit-specify; all pass; portfolio-primacy note)
└── tasks.md             ← Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root — extends the existing Iteration-0/1/2 tree)

```text
app/
├── services/
│   ├── edm_service.py               # EDIT: add get_edm_detail(edm_id) — light EDM context + portfolios
│   │                                #       (parsed exposure_detail) + treaties (parsed attributes) +
│   │                                #       the DERIVED aggregate rollup; graceful-empty when no snapshot
│   ├── portfolio_service.py         # NEW: upsert_portfolio_detail (idempotent), list_portfolios(edm_id),
│   │                                #      the per-portfolio read model + aggregate derivation helper
│   ├── treaty_service.py            # NEW: upsert_treaty_detail (idempotent), list_treaties(edm_id),
│   │                                #      build_treaty_workbook(edm_id) → .xlsx bytes (openpyxl)
│   ├── analysis_service.py          # NEW: list_broker_analyses(rdm_id) + list_edm_analyses(edm_id) grouped by
│   │                                #      rdm_id + parsed settings_metadata + is_group + RESOLVED portfolio
│   │                                #      (read-time join on edm_id+exposure_resource_id, R9); analysis counts (FR-050)
│   ├── rdm_service.py               # EDIT: broker-analysis view read helpers hang off here or analysis_service
│   ├── package_sync_service.py      # EDIT: get_package_cards now includes the per-EDM aggregate line (FR-041)
│   │                                #       + populated analysis counts (FR-050)
│   └── irp_gateway.py               # EDIT: add read methods — list_portfolios / get_portfolio_exposure /
│                                    #       search_treaties (+ attributes) / get_analysis_metadata; extend
│                                    #       AnalysisHit to carry exposure_resource_id + type (stop dropping RM's
│                                    #       exposureResourceId — R9). Single-status/read only;
│                                    #       CONFIRM signatures vs the active wheel (R1). Fake mirrors them.
├── workers/
│   └── package_jobs.py              # EDIT: NEW body _backfill_edm_detail_body + @actor backfill_edm_detail
│                                    #       (fetch portfolios+exposure+treaties, idempotent upsert, stamp as_of);
│                                    #       EXTEND _backfill_rdm_analyses_body to also capture settings_metadata,
│                                    #       is_group + exposure_resource_id (only when type==PORTFOLIO — R9)
├── poller/
│   └── run.py                       # EDIT: in _handle_import_edm_terminal, on FINISHED ALSO idempotently
│                                    #       enqueue backfill_edm_detail (independent of package_id/RDMs) —
│                                    #       coexists with the existing upload_rdm enqueue (distinct rwb_job_type)
├── routers/
│   ├── edms.py                      # EDIT: _detail passes the full get_edm_detail(...) payload (portfolios,
│   │                                #       treaties, aggregate) to the redesigned template
│   ├── rdms.py                      # EDIT: detail renders the broker-analysis-grouped-by-rdm view (US3)
│   └── treaties.py                  # NEW (or fold into edms.py): GET /edms/{id}/treaties.xlsx — authenticated
│                                    #       file download from stored treaty detail (no Risk Modeler call)
├── templates/
│   ├── pages/
│   │   ├── edm_detail.html          # EDIT: REDESIGN — minimal header + aggregate strip + inline per-portfolio
│   │   │                            #       table (w/ linked-analyses expansion) + treaties + standalone
│   │   │                            #       RDM-grouped broker-analyses section (matches ui.md / preview rev 7)
│   │   └── rdm_detail.html          # EDIT: broker-analyses grouped by rdm_id + EDM + Portfolio columns
│   │                                #       (resolved / Group / not-linked) + per-analysis settings/metadata
│   └── partials/
│       ├── portfolio_row.html       # NEW: one portfolio's exposure figures + descriptive Analyses count +
│       │                            #      the inline linked-analyses panel (pinned/rail per ui.md, US3/FR-037)
│       ├── edm_aggregate_strip.html # NEW: the compact EDM-page aggregate rollup strip (US4)
│       ├── treaty_row.html          # NEW: one treaty, collapsed; expandable to full attributes; h-scroll
│       ├── broker_analysis_row.html # NEW: one broker analysis + resolved portfolio + settings/metadata +
│       │                            #      rate sub-drill (US3); shared by RDM page AND EDM standalone section
│       └── package_card.html        # EDIT: add the per-EDM aggregate orientation line (FR-041) + populated
│                                    #       analysis counts (FR-050)
├── static/css/
│   └── details.css                  # NEW: per-portfolio table / aggregate strip / treaty rows / broker-analysis
│                                    #      cards via tokens (Article 9)

alembic/versions/
└── 0001_initial.py                  # EDIT: add irp_portfolio + irp_treaty tables (FKs to irp_edm); add
                                     #       settings_metadata + is_group + exposure_resource_id to irp_analysis (group_parent_id deferred);
                                     #       add exposure_detail (portfolio) / attributes (treaty) JSON columns;
                                     #       seed rwb_job_type_kind row 'backfill_edm_detail'; downgrade in FK order

infra/scripts/
└── seed_db.py                       # EDIT: idempotent MERGE adds 'backfill_edm_detail' to rwb_job_type_kind

pyproject.toml                       # EDIT: add openpyxl to [project.dependencies]

tests/
├── unit/
│   ├── test_backfill_edm_detail.py  # NEW: worker fetches (fake IRP) → upserts irp_portfolio/irp_treaty +
│   │                                #      JSON snapshot; idempotent re-run overwrites in place, no dupes;
│   │                                #      failure preserves EDM 'ready' + recoverable (FR-005)
│   ├── test_edm_detail_rollup.py    # NEW: get_edm_detail derives the aggregate (sum counts / union perils /
│   │                                #      combine geography+currency) from per-portfolio snapshots; graceful
│   │                                #      empty when no snapshot (FR-017/FR-042/FR-043)
│   ├── test_broker_analyses.py      # NEW: list_broker_analyses groups by rdm_id (shown once across M EDMs);
│   │                                #      settings_metadata parsed; missing fields blank not error; portfolio
│   │                                #      linkage resolves at read time (is_group→Group, unmatched→not-linked, R9)
│   ├── test_edm_analyses.py         # NEW: list_edm_analyses groups by rdm_id + buckets linked analyses per
│   │                                #      portfolio (group/unresolved stay standalone-only); resolution order-independent
│   ├── test_treaty_export.py        # NEW: build_treaty_workbook produces a valid .xlsx over the treaty set
│   └── test_poller.py               # EDIT: import_edm FINISHED enqueues BOTH upload_rdm and backfill_edm_detail
│                                    #       (idempotent; standalone/EDM-only import still enqueues the detail head)
└── sqlserver/
    └── test_detail_tables_migration.py # NEW: irp_portfolio/irp_treaty build + irp_analysis new columns;
                                     #       idempotent detail upsert (re-run overwrites snapshot, no dupes)
```

**Structure Decision**: Single server-rendered web app, extending the `app/` package. Detail **reads + rollup** live in `edm_service`/`portfolio_service`/`treaty_service`/`analysis_service` as functions (matching the Iteration-1/2 service style); the **backfill** lives in the Dramatiq worker (`package_jobs.py`) behind `irp_gateway` (fake in CI, Article 12). The poller change is one idempotent enqueue in the existing terminal handler. All new schema folds into the single `0001_initial.py` revision (drop-create-seed); `irp_analysis` gains columns by editing its existing create statement (no `ALTER` — drop-create). The one new dependency (`openpyxl`) is confined to `treaty_service.build_treaty_workbook`.

---

## Complexity Tracking

*No Constitution violations — no entries required.*

---

## Phase 0 — Research

See [research.md](research.md). **No `NEEDS CLARIFICATION` unknowns remained after the spec** — the PRD (§9, §12.4, §16.2, §21), FUNCTIONAL_REQUIREMENTS §2.2/§2.3/§7, DATA_MODEL §5/§6, and the implemented Iteration-2 spine resolve the behavior. Research records the concrete decisions the spec/DATA_MODEL leave to planning: the `irp-integration` read-method surface for detail + confirm-against-wheel discipline (R1); **the storage shape for backfilled detail — the JSON snapshot cache — the central new design decision (R2)**; the backfill worker/poller wiring as a forward extension of the completion path (R3); the derived EDM-aggregate rollup (R4); the treaty `.xlsx` export mechanism + the `openpyxl` dependency (R5); the redesigned EDM detail page + broker-analysis view + no-new-nav reachability (R6); forward-only backfill + graceful empty/failure states (R7); the broker-analysis grouping by `rdm_id` + un-emptying the analysis counts (R8); and the portfolio↔analysis linkage — capture RM's exposure pointer, resolve the owning portfolio at read time (R9).

## Phase 1 — Design & Contracts

- [data-model.md](data-model.md) — the new `irp_portfolio` / `irp_treaty` tables (DATA_MODEL §5 identity + the added `exposure_detail` / `attributes` JSON snapshot columns), the `irp_analysis` detail columns (`settings_metadata`, `is_group`, `exposure_resource_id` — the portfolio-linkage pointer, R9; `group_parent_id` deferred — DATA_MODEL §6), the JSON snapshot field shapes, the `backfill_edm_detail` kind seed, and the migration/seed impact folded into `0001_initial.py`.
- [ui.md](ui.md) — the page-composition contract for the redesigned EDM/RDM detail pages, derived from the approved previews (`docs/ui_previews/edm_detail.html` rev 7, `rdm_detail.html` rev 3): the unified `.dtable` expandable-comparison component, collapse/expand rules, the pinned + rail-connected expanded body, the portfolio↔analysis linkage display, and every empty/pending/failed state.
- [contracts/data-access.md](contracts/data-access.md) — the service/query contract: `edm_service.get_edm_detail` (+ the aggregate derivation + per-portfolio linked analyses), `portfolio_service` / `treaty_service` (upsert + read + workbook), `analysis_service.list_broker_analyses` / `list_edm_analyses` (grouped-by-`rdm_id` + metadata + read-time portfolio resolution, R9), and the extended `irp_gateway` read surface (incl. `AnalysisHit` carrying the exposure pointer).
- [contracts/http-routes.md](contracts/http-routes.md) — the redesigned `/edms/{id}` detail (now incl. per-portfolio linked analyses + a standalone RDM-grouped broker-analyses section, FR-037), the extended `/rdms/{id}` broker-analysis view (EDM + resolved-Portfolio columns), the `/edms/{id}/treaties.xlsx` export download, the package-card per-EDM aggregate line, and the graceful-empty/pending states; CSRF/roles/HTMX conventions (no new state-changing route).
- [contracts/worker-poller.md](contracts/worker-poller.md) — the backfill mechanism made concrete: the `backfill_edm_detail` worker body, the extended `backfill_rdm_analyses` metadata capture, the poller's `import_edm` FINISHED enqueue extension, idempotency, and the forward-only/graceful-failure behavior.
- [quickstart.md](quickstart.md) — rebuild + test + end-to-end manual walkthrough (import an EDM → detail backfills automatically → per-portfolio breakdown + aggregate strip → treaties + Excel export → RDM broker analyses → graceful empty for a pre-capability entity).
- Agent context updated: the `<!-- SPECKIT START/END -->` block in `CLAUDE.md` now points at this plan.

**Post-Design Constitution Re-check: PASSED.** The data model adds two thin identity tables + JSON snapshot cache columns (no scoping key, no stored sequence, no new event-sourced status); the snapshot correctly stores external RM vocabularies without a kind table; the backfill runs in the worker behind the gateway (Article 11) on the existing single-worker SQL queue (Article 10); the aggregate is a derived query, not a stored rollup (Article 2). No violations introduced.

> **§21.0 DB-lifecycle prompt (schema-affecting iteration).** This iteration adds `irp_portfolio` + `irp_treaty` and columns on `irp_analysis`, so it is schema-affecting: choose **Rebuild / Refresh / Skip** for the **WORKBENCH** database before implementing. Recommended: **Rebuild** (`make db-rebuild` — drop-create-seed the single revision), consistent with the pre-cutover single-revision strategy. `EXPOSURE` / `LOSS` are untouched; DATABRIDGE is never touched. Confirm the choice at implement time.
