# Implementation Plan: One-Click Portfolio Breakouts by LOB & Geography (Iteration 4)

**Branch**: `005-subportfolio-breakouts` | **Date**: 2026-07-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/005-subportfolio-breakouts/spec.md`

---

## Summary

Iteration 3 (spec 004) made imported exposure legible — the EDM detail page leads with a read-only per-portfolio table whose figures (counts, perils, **lines of business**, **geography/states**, currency, TIV) are backfilled into `irp_portfolio.exposure_detail` JSON snapshots. Iteration 4 adds the first *act* on that understanding: **two one-click breakout actions** on a source portfolio — **by line of business** and **by geography (state)** — each fanning the portfolio out into one sub-portfolio per distinct value via Risk Modeler's account operations. Scope is deliberately narrower than PRD §10A: no custom filter builder, no complement split, no "do the opposite" (follow-on slices over this iteration's machinery). The current-split view already exists (spec 004's portfolio table); this iteration hangs the actions off it.

Two facts drive the design. **First, the capability gap is in the library, not the app — and RM has no one-shot create-by-filter** (research **R1**, revised after the RM LLM companion's conceptual walkthrough and validated against `../knowledge/`): the shape is a **three-call sequence** mirroring the RM UI's own flow — **select** the source portfolio's matching account IDs (documented portfolio-accounts read, paged fully), **create** the empty portfolio (the already-wrapped `create_portfolio`, sync 201), then **add** the selected accounts by ID via `PUT .../portfolios/{id}/filtered-accounts` (`markedAccounts` mode — source scoping exact by construction, failures visible before anything is written to RM). The library enhancements — the filtered/paginated selection read and the populate wrapper (`add_filtered_accounts`) — are built in-house with this iteration, developed against the local checkout (`make irp-local`) and published to TestPyPI. The **selection-query tokens** for LOB/state are the one real unknown (the portfolio-accounts filter property list is closed; EDM-level `allowDeepFilters` is the documented lead); the add step is doc-verified **200-synchronous** (managefilteredaccounts reference, fetched 2026-07-30 — the library raises on any unexpected 202) — a front-loaded **sandbox spike** (probe + RM UI traffic capture) closes the selection question and the already-member semantics before the worker is built; a `queryFilter` one-call populate remains a spike-conditional optimization only. **Second, the fan-out loop runs in the worker tier, not the request path** (research **R2**): RM's per-slice create latency is unverified and a state fan-out can be 40+ slices, so the confirm POST enqueues a new **`run_breakout`** `rwb_job`; the Dramatiq worker loops one create per slice with per-item failure isolation (the four existing loop precedents in `package_jobs.py`), upserting each `irp_portfolio` row as it lands — and the EDM page's existing 3-second self-poll shows slices appearing live. On completion the worker auto-enqueues the existing `backfill_edm_detail` job so new slices acquire their own figures (mechanical follow-up, Article 5); the analyst's judgment step is the **preview + confirm** modal, which lists every slice (value + collision-safe generated name) and discloses the account-bucketing overlap before anything is created.

Value enumeration never touches Risk Modeler or DataBridge on the request path (Article 11): the distinct LOBs/states come from the **stored** spec-004 summary (`exposure_detail.summary.lines_of_business` / `.states`). A missing summary or a single-value dimension gates the action off (disabled-with-reason → Sync). The confirm POST additionally verifies **summary freshness** (clarified 2026-07-30): the source portfolio's current RM `stampDate` — read via `search_portfolios`, the Article 2 submit-time name-resolution pattern and the flow's one web-layer RM call — must equal the stamp the backfill captured alongside the summary; mismatch → 409 refusal pointing at Sync, **no `rwb_job` row created**. Lineage is first-class: `irp_portfolio` gains `source_portfolio_id` (self-FK), `breakout_dimension_code` (new `breakout_dimension_kind` kind table — Article 3), and `breakout_value`, giving the portfolio list its slice grouping, giving re-runs their idempotency key, and giving the audit trail its durable trace. Partial failure has no rollback (no portfolio delete exists in MVP or the wheel): re-running the breakout is idempotent — existing slices are detected by lineage, and a slice that exists in RM but not app-side (the documented at-least-once window) is **adopted by name**, not duplicated.

---

## Technical Context

**Language/Version**: Python 3.12 (inherited; `pyproject.toml` `requires-python = ">=3.12"`).

**Primary Dependencies** (existing, reused — **no new app dependency this iteration**):
- `fastapi` + `uvicorn[standard]`, `jinja2` + HTMX (Alpine.js only for the breakout-modal sliver, mirroring `package_modal.html`) — server-rendered (Article 8).
- `dramatiq[redis]` + `redis` — the worker tier that runs the breakout loop; `rwb_job` SQL table stays the queue of record (Article 10).
- `sqlalchemy>=2.0` (Core) + `pyodbc`, `alembic` — WORKBENCH schema via the `db/` package; single `0001_initial.py` revision.
- **`irp-integration[databridge]`** — the sole path to Risk Modeler, reached only through `app/services/irp_gateway.py`. **This iteration makes two changes to the library itself** (research R1): (1) the selection read — `search_accounts_by_portfolio` gains its documented `filter`/`sort`/pagination params plus a fully-paginated variant (the current method truncates); (2) one *write* method — `add_filtered_accounts(exposure_id, portfolio_id, marked_accounts=…, query_filter=…, select_all=…)` (+ `FILTERED_ACCOUNTS` endpoint constant), wrapping RM's `PUT .../portfolios/{id}/filtered-accounts` with explicit account IDs as the primary mode. Slice creation composes select → the existing `create_portfolio` → add — developed in the local checkout (`make irp-local`), published to TestPyPI, and pinned before implement completes. The library is pre-release; **re-confirm the final signatures against the active wheel (`make irp-status`) before implementing** the gateway seam.

**Storage**: SQL Server 2022 (`rwb_workbench`) — **WORKBENCH connection only**. New: three lineage columns on `irp_portfolio` (`source_portfolio_id` self-FK, `breakout_dimension_code` FK, `breakout_value`) + a filtered unique index for slice idempotency; new `breakout_dimension_kind` kind table (seed rows `lob`, `state`); two new `rwb_job_type_kind` seed rows (`run_breakout_lob`, `run_breakout_state` — one per dimension so the idempotent-enqueue key `UNIQUE(requestor_type, requestor_id, rwb_job_type)` distinguishes them per portfolio; both dispatch the same worker body). **No EXPOSURE/LOSS access. DataBridge is NOT touched this iteration** — enumeration reads the stored spec-004 summary; the existing `backfill_edm_detail` worker refreshes slice figures afterward (one edit: it now captures the portfolio's RM `stampDate` alongside the summary it writes — the FR-002a freshness anchor, stored in `exposure_detail`, no new column).

**Testing** (Article 12, three tiers):
- `pytest tests/unit` — SQLite via `db.register_engine` + the fake IRP (extended with `create_sub_portfolio` incl. duplicate-name behavior): the prerequisite gate (must-test per Article 12), the slice-plan builder (value enumeration from stored summary, deterministic naming + collision suffixing), the `run_breakout` worker body (per-slice isolation, partial failure, idempotent re-run, adopt-by-name reconciliation, completion enqueue of `backfill_edm_detail`), lineage display read model, and the routes (gate-disabled states, CSRF, HTMX fragments).
- `pytest tests/sqlserver --run-sqlserver` — real driver: migration builds the lineage columns/kind table/filtered unique index; slice upsert + lineage uniqueness under the real driver.
- `pytest tests/irp --run-irp` — opt-in sandbox: the real select → create → add round-trip (**the R1 selection-token, already-member-semantics, and bucketing verification lives here**), plus the architecture guard extended over the new worker/gateway code (`poll_*_to_completion` absent).

**Target Platform**: Linux server (WSL2 native dev: uvicorn + poller + Dramatiq worker + Redis + SQL Server container; mirrors the `linux-box` / `sqlserver` split).

**Project Type**: Server-rendered web application (FastAPI + Jinja2 + HTMX) with two out-of-process background components (poller, Dramatiq worker). Single project; extends the existing `app/` tree, plus a scoped enhancement in the sibling `irp-integration` library repo.

**Performance Goals**:
- Breakout **preview** renders from stored data only (no external call) — normal page-load budget, < 300 ms p95.
- A typical breakout (≤ 15 slices) completes worker-side and is reflected in the portfolio list within **30 s** of confirm (SC-005); large fan-outs (40+ slices) complete reliably with per-slice outcomes.
- Slices appear **live** as created via the existing 3-second self-poll — the analyst never stares at a spinner with no signal.

**Constraints**:
- **IRP discipline (Article 11):** the creation loop runs **only in the worker**, via `irp_gateway`; the web layer's single RM touch is the confirm-time `search_portfolios` freshness read (Article 2's submit-time name-resolution pattern — never a `get_*` poll); the preview reads stored data and the confirm otherwise only enqueues. The add step is doc-verified synchronous (no RM-side job, no poller involvement); the library raises on an unexpected 202 so a behavior change fails loudly. `poll_*_to_completion` forbidden everywhere.
- **Judgment waits for a click (Article 5):** nothing is created until the analyst confirms the previewed slice list; the post-breakout detail refresh is mechanical follow-up and auto-fires.
- **No rollback:** portfolio deletion is out of MVP and absent from the wheel. Recovery = idempotent re-run (lineage detection + adopt-by-name for the at-least-once window).
- All SQL via the `db/` safe bound-parameter path (Article 7); slice upsert uses `get_connection("WORKBENCH")` + explicit `conn.begin()` where multi-statement. The trusted-script path is not used. CSRF on the confirm POST; function-level role gating server-side (Article 13).
- **Enumeration source is the stored summary** — freshness is enforced at confirm (stamp-to-stamp `stampDate` equality, FR-002a): a stale summary refuses the breakout up front instead of proceeding. A slice that still selects zero accounts at run time (residual drift in the confirm-to-run window, or a selection-token regression) **fails that slice and creates nothing** — never an empty RM portfolio (spec FR-008; clarified 2026-07-30).

**Scale/Scope**: ~10–30 internal analysts; a source portfolio yields 2–~40 slices (LOBs are typically < 10; states can reach 40+). Schema: 3 columns + 1 kind table + 2 seed rows + 1 filtered index, folded into the single revision. Code: 2 library changes (selection-read upgrade + populate method + constant), 1 gateway write seam + fake mirror, 1 new worker module/actor, 1 slice-plan/gate service, 2 routes (modal GET + confirm POST), 1 modal partial + portfolio-row lineage/action edits, and the PRD documentation pass (O6-1/O6-2 register, §10A.5, §21).

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Article | Title | Status | Notes |
|---------|-------|--------|-------|
| 1 | Navigation Manifest Is the One Versioned Source of Truth | ✅ | **No new nav nodes.** The breakout lives on the existing `/edms/{id}` page (nav key `irp.edm_library`); its two routes are fragment/action endpoints under the same node. Rail/sidebar/breadcrumb inherited — no scattered config. |
| 2 | Sequencing Is Derived, Not Stored | ✅ | The prerequisite gate (EDM ready + portfolio + stored summary with ≥ 2 values) is **computed in code** in one testable place — no stored stage. Coupling stays name/id-based against RM; the confirm-time freshness read (`search_portfolios` → `stampDate`) is exactly this article's submit-time name-resolution pattern. Lineage columns record *provenance* (which slice came from what), not a sequence/DAG — no orchestration reads them. |
| 3 | Categoricals Are Kind Tables, Never Enums — Except External-Status Mirrors | ✅ | `breakout_dimension_kind` (rows `lob`, `state`) is a **kind table** — an app-defined closed set the code dispatches on (exactly the Article 3 default). `breakout_value` stays a plain NVARCHAR: it stores external exposure vocabulary (LOB names, state names) verbatim — same rationale as the spec-004 snapshot. New `rwb_job_type_kind` seed rows `run_breakout_lob`/`run_breakout_state` (kind table, closed set). |
| 4 | Status Is Event-Sourced with Cached Current | ✅ | No new status columns at all. `irp_portfolio` still carries no status; breakout progress is the `rwb_job` row (status updated in place, per Article 4's carve-down) + slices appearing. `submission.status_code` remains the sole event-sourced status. |
| 5 | Mechanical Follow-up Auto-fires; Judgment Waits for a Click | ✅ | **Central to this iteration.** The fan-out is judgment → it waits for the explicit confirm click on a preview listing every slice + the overlap disclosure. The post-completion `backfill_edm_detail` enqueue is a direct mechanical consequence of the breakout → auto-fires. Explicit per op, as required. |
| 6 | No Row-Level Security; All Authenticated Analysts See All Deals | ✅ | No scoping key anywhere new. Any authenticated analyst can run a breakout on any EDM's portfolio; roles gate the function only. `inserted_by` on slice rows is provenance, not an access gate. |
| 7 | One Data-Access Package, Two Paths (`/db`) | ✅ | All reads/writes via `db.execute*` bound-parameter path; the worker's slice upsert follows the `_snapshot_upsert` conventions. The trusted-script path is **not** imported (no DataBridge use this iteration at all). |
| 8 | Server-Rendered; No SPA | ✅ | Jinja2 + HTMX: modal fragment GET, confirm POST returning the body partial, live progress via the **existing** self-poll (no SSE, no new client state). Alpine only for the modal open/close sliver (the `package_modal` precedent). Real URLs; no new nav. |
| 9 | Styling Extends ITCSS via Tokens | ✅ | Breakout modal + lineage badges styled via existing tokens/layers (`details.css`, `components.css` conventions) — no hardcoded hex, no append-sheets. |
| 10 | The SQL Table Is the Queue; Single Worker by Default | ✅ | `run_breakout_lob`/`run_breakout_state` are new `rwb_job_type`s on the **same** SQL-backed queue (atomic claim, heartbeat, reconciler — a wedged loop is reclaimed). Idempotent enqueue on the existing `UNIQUE(requestor_type, requestor_id, rwb_job_type)` key. A long fan-out occupying the single worker briefly delays other jobs — accepted at this scale; the documented concurrency upgrade path is unchanged. |
| 11 | IRP Polling and Result Work Behind Interface; Submission on Request Path Permitted | ✅ | Creation calls run **worker-side** via `irp_gateway` (permitted — Art. 11 allows request-path submission but does not require it; workers already submit imports). The web layer's single RM call is the confirm-time `search_portfolios` freshness read (submit-time name resolution per Article 2 — not a `get_*` poll, not result retrieval). The add step is doc-verified 200-sync — no `irp_job` rows, poller untouched; the library raises on an unexpected 202. Never `poll_*_to_completion`. DataBridge untouched. |
| 12 | Test-First, Three Connected Strategies | ✅ | The **prerequisite gate** (named must-test) and the slice-plan/naming function get unit tests; worker loop + idempotent re-run + adopt-by-name unit-tested against the fake; migration + filtered unique index in the SQL Server tier; the real select→create→add round-trip in the opt-in IRP tier (where R1's sandbox verification is codified). |
| 13 | Authentication & Secrets | ✅ | Confirm POST carries CSRF (`validate_csrf_token` pattern); modal GET is an authenticated fragment; idle-timeout HTMX handling inherited. RM credentials remain env-sourced via `IRPClient()`; no new secrets. |

**Constitution Check: PASSED — no violations. No Complexity Tracking entries required.**

> Re-check after Phase 1 design: **still PASSED** (see end of plan).

---

## Project Structure

### Documentation (this feature)

```text
specs/005-subportfolio-breakouts/
├── plan.md              ← this file (/speckit-plan output)
├── research.md          ← Phase 0 output (R1–R9: slice-creation API shape, execution locus, lineage, naming, …)
├── data-model.md        ← Phase 1 output (lineage columns, breakout_dimension_kind, seeds, filtered index)
├── quickstart.md        ← Phase 1 output (rebuild + test + manual end-to-end walkthrough)
├── contracts/           ← Phase 1 output
│   ├── irp-library.md    ← the selection-read + add_filtered_accounts enhancement contract (irp-integration repo)
│   ├── data-access.md    ← gate + slice-plan + lineage read model + slice upsert service contract
│   ├── http-routes.md    ← breakout modal GET + confirm POST + gate-disabled/graceful states + UI notes
│   └── worker-poller.md  ← run_breakout worker: loop, per-slice isolation, idempotent re-run, adopt-by-name,
│                            completion enqueue of backfill_edm_detail; poller untouched (add step doc-verified sync)
└── tasks.md             ← Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root — extends the existing tree; one scoped change in the sibling library repo)

```text
../../IRP/irp-integration/irp_integration/          # sibling library repo (make irp-local while developing)
├── portfolio.py                     # EDIT: search_accounts_by_portfolio gains filter/sort/limit/offset
│                                    #   (documented params) + NEW search_accounts_by_portfolio_paginated
│                                    #   — the slice SELECTION read; must never truncate (R1/U6).
│                                    # NEW METHOD: add_filtered_accounts(exposure_id, portfolio_id, *,
│                                    #   marked_accounts=None, query_filter="", select_all=False,
│                                    #   manage_existing_accounts=False) — wraps
│                                    #   PUT .../portfolios/{id}/filtered-accounts (R1); marked_accounts
│                                    #   (explicit IDs) is the PRIMARY mode; doc-verified 200-sync
│                                    #   (managefilteredaccounts, 2026-07-30); raises on unexpected 202.
│                                    #   Slice creation composes: selection read → EXISTING
│                                    #   create_portfolio (empty container, sync 201) → add.
│                                    #   No change to create_portfolio itself.
└── constants.py                     # NEW: FILTERED_ACCOUNTS = '/platform/riskdata/v1/exposures/
                                     #      {exposureId}/portfolios/{id}/filtered-accounts'

app/
├── services/
│   ├── irp_gateway.py               # EDIT: add create_sub_portfolio(...) write seam (select→create→add,
│   │                                #       + result value object) + populate_sub_portfolio (adopt healing);
│   │                                #       confirm signatures vs active wheel; fake mirrors both
│   ├── breakout_service.py          # NEW: the one testable home for — prerequisite gate rule (FR-002/003),
│   │                                #      value enumeration from stored summary, slice-plan builder
│   │                                #      (deterministic names + collision suffixing vs current portfolio
│   │                                #      list), enqueue of run_breakout (idempotent), plan recompute for
│   │                                #      the worker, per-slice outcome read model
│   └── portfolio_service.py         # EDIT: lineage-aware list_portfolios (slice ↔ source association for
│                                    #       display); insert/adopt slice row helpers (lineage + inserted_by)
├── workers/
│   └── portfolio_jobs.py            # NEW (auto-discovered *_jobs.py): actors run_breakout_lob /
│                                    #      run_breakout_state (shared body; loader dispatches by name) —
│                                    #      recompute plan,
│                                    #      loop create_sub_portfolio per slice w/ per-item try/except,
│                                    #      upsert row per success, adopt-by-name on duplicate, record
│                                    #      per-slice outcomes in output_data, business-event logs,
│                                    #      completion → idempotent enqueue backfill_edm_detail (FR-013)
├── routers/
│   └── portfolios.py                # NEW: GET  /edms/{edm_id}/portfolios/{pid}/breakout   (modal fragment,
│                                    #        dimension pre-checks → available/disabled-with-reason states)
│                                    #      POST /edms/{edm_id}/portfolios/{pid}/breakout   (CSRF; gate re-check;
│                                    #        enqueue; returns EDM body partial → live poll takes over)
│                                    #      nav key irp.edm_library (no new nav node)
├── templates/partials/
│   ├── breakout_modal.html          # NEW: dimension choice + slice-list preview (value → generated name) +
│   │                                #      count + account-bucketing/blank-value disclosures + confirm;
│   │                                #      disabled-with-reason states (no summary → Sync pointer; <2 values)
│   ├── portfolio_row.html           # EDIT: breakout action entry point; lineage badge (dimension + value,
│   │                                #       "from {source}"); chained lineage renders sanely
│   └── edm_detail_body.html         # EDIT: breakout-in-flight indicator riding the existing `live` self-poll;
│                                    #       per-slice outcome summary (toast/banner) on completion
├── static/css/details.css           # EDIT: lineage badge + modal slice-list styles via tokens (Article 9)

alembic/versions/
└── 0001_initial.py                  # EDIT: irp_portfolio += source_portfolio_id (self-FK),
                                     #       breakout_dimension_code (FK), breakout_value; filtered UNIQUE
                                     #       (source, dimension, value) WHERE source IS NOT NULL AND
                                     #       deleted_at IS NULL; NEW breakout_dimension_kind (+ seeds lob/state);
                                     #       rwb_job_type_kind seeds 'run_breakout_lob'/'run_breakout_state'
infra/scripts/seed_db.py             # EDIT: idempotent MERGE for the two new seed sets

tests/
├── unit/
│   ├── test_breakout_gate.py        # NEW: gate truth table (EDM state × portfolio × summary × value count)
│   ├── test_breakout_plan.py        # NEW: enumeration from stored summary; deterministic naming; collision
│   │                                #      suffixing; blank-value exclusion note; <2-value refusal
│   ├── test_run_breakout_worker.py  # NEW: loop vs fake IRP; per-slice isolation; partial failure ⇒ job result
│   │                                #      + outcomes; idempotent re-run creates only missing; adopt-by-name;
│   │                                #      completion enqueues backfill_edm_detail; business-event logs
│   ├── test_breakout_routes.py      # NEW: modal states; CSRF; gate 409-style refusal; enqueue idempotency
│   └── test_edm_detail_rollup.py    # EDIT: lineage-aware portfolio list read model
├── sqlserver/
│   └── test_detail_tables_migration.py # EDIT: lineage columns/kind table/filtered index build + uniqueness
└── irp/
    └── test_filtered_accounts.py    # NEW (opt-in): real RM round-trip — select + create + add; selection
                                     #      tokens for LOB/state (U1); already-member semantics (U2); state vocabulary
                                     #      (U4); account-bucketing (U5); pagination (U6) — codifies R1's
                                     #      sandbox spike
```

**Structure Decision**: Single server-rendered web app, extending the `app/` package in the established style: the gate + slice-plan logic concentrated in a new `breakout_service.py` (one testable place, FR-003), the fan-out loop in a new auto-discovered worker module `portfolio_jobs.py` behind `irp_gateway` (fake in CI), two fragment/action routes in a new `portfolios.py` router under the existing nav node, and all schema folded into the single `0001_initial.py` revision (drop-create-seed). The library enhancement is a scoped, contract-documented change in the sibling `irp-integration` repo, developed via `make irp-local` and pinned from TestPyPI before implement completes.

---

## Complexity Tracking

*No Constitution violations — no entries required.*

---

## Phase 0 — Research

See [research.md](research.md). The spec left no `[NEEDS CLARIFICATION]` markers; research records the concrete decisions planning owns: **R1** — the slice-creation API shape (select accounts → create → add-by-IDs, validated against the RM LLM companion's conceptual flow, `../knowledge/`, and the wheel) and the two library enhancements (selection-read upgrade + `add_filtered_accounts`), with the residual unknowns pinned to a sandbox-verification task; **R2** — execution locus: worker-side `run_breakout` `rwb_job` (why not the request path); **R3** — lineage schema (`source_portfolio_id` + `breakout_dimension_kind` + `breakout_value` + filtered unique index) and why no first-class breakout table; **R4** — deterministic naming + collision suffixing; **R5** — value enumeration from the stored summary + gate composition; **R6** — the state-value ↔ RM-filter-vocabulary mapping risk (stored `COALESCE(Admin1Name, Admin1Code)` vs. what the filter expects) and its mitigation; **R7** — idempotent re-run + adopt-by-name reconciliation (no rollback); **R8** — UI mechanics (modal preview, live progress via existing self-poll, lineage display) + the UI_WORKFLOW preview obligation; **R9** — the PRD documentation pass (O6-1/O6-2 register, §10A.5 blocked-note, §21 Iteration-4 narrowing).

## Phase 1 — Design & Contracts

- [data-model.md](data-model.md) — the `irp_portfolio` lineage columns, `breakout_dimension_kind` kind table + seeds, `rwb_job_type_kind` seed `run_breakout`, the filtered unique index (idempotency key), `inserted_by` population, and the migration/seed impact folded into `0001_initial.py`.
- [contracts/irp-library.md](contracts/irp-library.md) — the two enhancement contracts for the sibling repo: the filtered/paginated `search_accounts_by_portfolio` selection read, and `add_filtered_accounts` (signature, endpoint constant, request-body shape `selectAll`/`markedAccounts`/`queryFilter`/`manageExistingAccounts`, marked-accounts-primary rationale, error semantics, doc-verified 200-sync response with raise-on-unexpected-202), plus the sandbox spike checklist that closes R1's residual unknowns (selection tokens, already-member semantics, state vocabulary, bucketing, pagination).
- [contracts/data-access.md](contracts/data-access.md) — `breakout_service` contract: gate rule (single testable function), enumeration + slice-plan builder (pure, reused by preview and worker), enqueue semantics, lineage-aware portfolio read model, slice insert/adopt helpers.
- [contracts/http-routes.md](contracts/http-routes.md) — modal GET + confirm POST: fragments, CSRF, gate-refusal behavior (409 + re-rendered fragment, the `packages.py` precedent), disabled-with-reason states, live-progress and completion surfacing; UI notes for the modal + lineage badges (rendered preview per docs/UI_WORKFLOW.md before wiring — new interactive surface).
- [contracts/worker-poller.md](contracts/worker-poller.md) — the `run_breakout` worker body: plan recompute, per-slice loop with failure isolation, adopt-by-name, per-slice outcomes in `output_data`, completion enqueue of `backfill_edm_detail`; no poller involvement (add step doc-verified sync).
- [quickstart.md](quickstart.md) — rebuild + test + manual end-to-end walkthrough (import an EDM → open it → break a portfolio out by LOB → watch slices appear → figures backfill → re-run idempotency + partial-failure demo → geography breakout with the overlap disclosure).
- Agent context updated: the `<!-- SPECKIT START/END -->` block in `CLAUDE.md` points at this plan.

**Post-Design Constitution Re-check: PASSED.** The design adds provenance columns (not a stored sequence), a kind table for an app-defined closed set, one worker type on the existing queue, and a gateway write seam — no scoping key, no new status machinery, no web-layer RM call, no DataBridge touch. No violations introduced.

> **§21.0 DB-lifecycle prompt (schema-affecting iteration).** This iteration adds columns to `irp_portfolio`, a kind table, and seed rows — schema-affecting: choose **Rebuild / Refresh / Skip** for the **WORKBENCH** database before implementing. Recommended: **Rebuild** (`make db-rebuild`), consistent with the pre-cutover single-revision strategy. EXPOSURE/LOSS untouched; DATABRIDGE never touched. Confirm the choice at implement time.
