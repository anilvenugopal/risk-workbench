# Phase 0 — Research: EDM/RDM Details & Backfill (Iteration 3)

**No Technical Context unknowns.** The stack, dev workflow, `db/` layer, test tiers, project structure, and the async spine (poller + `rwb_job` queue + Dramatiq worker + `irp_gateway`) are all inherited from Iterations 0–2 and the constitution; the spec left **zero `NEEDS CLARIFICATION` markers** (the PRD §9/§12.4/§16.2/§21, FUNCTIONAL_REQUIREMENTS §2.2/§2.3/§7, and DATA_MODEL §5/§6 resolve the behavior). This document records the concrete decisions the spec and DATA_MODEL deliberately leave to planning — each in Decision / Rationale / Alternatives form.

The canonical schema lives in **DATA_MODEL §5 (EDM/RDM/portfolio/treaty), §6 (analysis)**; the FR field lists live in **FUNCTIONAL_REQUIREMENTS §2.2 (per-portfolio + treaty), §2.3/§7 (broker-analysis metadata)**. This research turns them into implementation choices against the *implemented* Iteration-2 code (`app/poller/run.py`, `app/workers/package_jobs.py`, `app/services/irp_gateway.py`) and the *active* `irp-integration` wheel.

---

## R1 — `irp-integration`: the detail-read method surface (+ confirm-against-wheel discipline)

**Decision**: Reach Risk Modeler only through the existing `app/services/irp_gateway.py`, **extended with read methods** for the detail this iteration backfills:

- **Portfolio enumeration for an EDM** — the portfolios that arrived with the EDM.
- **Per-portfolio exposure figures** — location/account/policy counts, perils + sub-perils, geography, currency, record volume (TIV where available). *(Amended 2026-07-23 after wheel/sandbox confirmation: the RM `/metrics` read supplies only the counts + a `perilsExposed` string; **no RM REST endpoint returns TIV/currency/geography/sub-perils at any level**. Those fields come from a **per-EDM DataBridge SQL aggregate** via a new irp-integration method — `client.databridge.get_portfolio_exposure_summary` — one read-only query per EDM. This is a documented exception to the single-item-loop rule below: it is a SQL aggregate, where N per-portfolio queries would just be N ODBC round-trips. Contract + wheel gaps recorded in `docs/IRP_INTEGRATION_FOLLOWUPS.md`.)*
- **Treaty attribute detail** — the treaties on the EDM and their full attribute set (DATA_MODEL §5 names `search_treaties`).
- **Broker-analysis settings/metadata** — engine/model version, engine type (DLM/HD) + version, analysis type/mode, peril (primary/secondary), region, currency, construction, LOB, group type, long-term vs near-term, event-rate scheme / rate vintage, loss amplification (PLA).

All are **single-status / read** calls — never `poll_*_to_completion`, never the poll-inside convenience methods (Article 11). Following the user's standing preference [[prefer-single-irp-endpoints]], the gateway exposes **single-item** reads and the worker **loops app-side** (one portfolio's exposure at a time, one analysis's metadata at a time) rather than a fail-fast batch helper, so one entity's fetch failure does not abort the whole EDM's backfill. The **CI fake** (`tests/unit/fakes/fake_irp.py`) implements the same extended `IRPGateway` protocol.

**Confirm-against-wheel (RESOLVED as a discipline, not a value).** `irp-integration` is pre-release and manager-based (`client.edm` / `.rdm` / `.import_job` / `.risk_data_job` / `.analysis`); its exact method names/signatures for portfolio-exposure, treaty attributes, and analysis metadata are **not yet pinned in code** and MUST be confirmed against the **active** wheel (`make irp-status`) before each is implemented — exactly as spec-003 confirmed its submit/get surface on 2026-07-14. Because the gateway is the **only** importer of the library, a signature change is a one-file edit plus a fake update. Confirmed method names + any missing capability (e.g. an exposure-summary endpoint that does not yet exist, forcing a per-portfolio aggregation) are recorded in **`docs/IRP_INTEGRATION_FOLLOWUPS.md`** (which already tracks the deferred/nice-to-have items from spec 003).

**Rationale**: Article 11 requires IRP behind an interface; the single gateway keeps the backfill worker unit-testable against a fake and quarantines the pre-release version churn. Single-item + app-side loop matches the memory-recorded preference and isolates per-entity failures.

**Alternatives considered**: (a) Call `irp-integration` directly from the worker — rejected: unfakeable in CI, spreads version risk. (b) Pin the method names now from docs — rejected: the wheel is pre-release; confirm against the actual active wheel at implementation. (c) A batch/plural exposure fetch — rejected per [[prefer-single-irp-endpoints]]: a fail-fast plural helper aborts the whole EDM on one bad portfolio.

---

## R2 — Storage shape for backfilled detail: a JSON snapshot cache (the central new decision)

**Decision**: Store the backfilled detail as a **JSON snapshot column** on each entity — `irp_portfolio.exposure_detail`, `irp_treaty.attributes`, `irp_analysis.settings_metadata` (all `NVARCHAR(MAX)`, **nullable**; null ⇒ not-yet-backfilled ⇒ graceful empty state). The entity tables keep their DATA_MODEL §5/§6 **identity/lineage columns** as real columns (`name`, `irp_id`, `edm_id`/`rdm_id`, `as_of`, `deleted_at`, audit); the snapshot column holds the RM detail payload verbatim. Backfill **overwrites the snapshot in place** (idempotent, FR-004) and stamps `as_of` (the trust signal, FR-052). The **EDM-aggregate is derived** from the per-portfolio snapshots in the query layer (R4) — it is **not** a stored column.

The DATA_MODEL §5/§6 tables are thin **identity/lineage records**; they carry **no** exposure figures, treaty attributes, or analysis metadata. The spec's Dependencies flagged exactly this: "WORKBENCH schema additions for the stored detail." The snapshot is the minimal, faithful home for it.

**Rationale**:
- This iteration displays detail as a **read-only textual snapshot** (FR-016) and performs **no internal query/filter/sort** on exposure figures — filtering, pick-lists, and sub-portfolio splitting are **Iteration 4**. So typed/indexed columns buy nothing now and would prematurely guess at RM's evolving payload shape.
- The figures are **external Risk Modeler vocabularies** (perils, sub-perils, geography, currency) that can drift with RM releases — the *same* rationale behind the Article 3 external-status-mirror carve-out. A JSON cache stores RM's payload verbatim; **no internal code path dispatches on these values**, so no kind table is required (and minting one would force a seed migration every time RM adds a peril/region — the crash-risk Article 3 explicitly guards against).
- It is a **read/cache record** (the spec's own Key Entities language for treaty), so idempotent re-backfill is a plain overwrite — no reconciliation of normalized child rows.
- It keeps the single migration lean and **defers the normalized, filterable exposure model to Iteration 4**, which owns splits/breakouts/filters and is far better positioned to design typed columns against real filter needs.

**Alternatives considered**: (a) **Typed columns per figure** (`location_count`, `peril_set`, …) — rejected: premature; guesses RM's shape; no query need this iteration; would be re-designed by Iteration 4. (b) **Separate normalized detail tables** (`portfolio_peril`, `portfolio_geography`, `analysis_setting`, …) — rejected: heavy for a read-only snapshot; Iteration 4 owns the filterable model, so building it now risks throwaway schema. (c) **Store nothing; fetch on view** — rejected: violates FR-018 (no request-path compute / renders regardless of exposure size) and the forward-only-backfill design (SC-007).

---

## R3 — Backfill worker/poller wiring: a forward extension of the completion path

**Decision**: Extend the **existing** Iteration-2 poller→worker completion path — do not add a new poller or a new async spine.

- **EDM detail** — add one `rwb_job_type`, **`backfill_edm_detail`**. In the poller's *existing* `_handle_import_edm_terminal`, on `status == FINISHED`, **also** idempotently enqueue a `backfill_edm_detail` head keyed `(requestor_type='irp_job', requestor_id=<this import_edm irp_job.id>, rwb_job_type='backfill_edm_detail')`. It **coexists** with the existing `upload_rdm` enqueue (different `rwb_job_type` ⇒ distinct row under the `UNIQUE(requestor_type, requestor_id, rwb_job_type)` key). The enqueue is placed **before** the existing `if not rdm_ids: return` guard and is **independent of `package_id`**, so a standalone or EDM-only import still gets its detail backfilled. The `backfill_edm_detail` worker reads the EDM's backfilled `irp_id` (the exposureId set in the same terminal handler), fetches portfolios + per-portfolio exposure + treaties via the gateway (R1), **idempotently upserts** `irp_portfolio` / `irp_treaty` rows with their JSON snapshots (R2), and stamps `as_of`.
- **Broker-analysis metadata** — **extend the existing `_backfill_rdm_analyses_body`** (already enqueued by the poller on `import_rdm` FINISHED and already writing the `irp_analysis` rows) to *also* capture each analysis's `settings_metadata` snapshot. No new `rwb_job_type` and no new poller enqueue — the cleanest forward extension. (If metadata needs a per-analysis `get_analysis` call rather than richer `search_analyses` fields, the same worker makes that single-item read.)

**Rationale**: Article 11/10 — heavy Risk Modeler work runs in the worker on the SQL-backed queue; the poller stays a single-status-check batch that only enqueues on terminal status. Reusing the terminal handler + the idempotent dedup key means backfill inherits the reconciler/heartbeat/retry machinery for free (Article 10). Riding analysis metadata on the existing capture avoids a redundant worker and a second RDM-completion enqueue.

**Alternatives considered**: (a) A dedicated backfill poller/process — rejected: duplicates the async spine; Article 10 keeps one queue. (b) Fetch detail inline in the `upload_edm` worker at submit time — rejected: detail does not exist until the import FINISHES in RM; it must be a completion-triggered follow-up. (c) A new `rwb_job_type` for analysis metadata — rejected: the `import_rdm` FINISHED path already spawns `backfill_rdm_analyses`; extend it.

---

## R4 — EDM-aggregate rollup: derived in the query layer, not stored

**Decision**: The EDM-aggregate figures (FR-040/FR-041/FR-042) are **computed in `edm_service`** from the EDM's per-portfolio `exposure_detail` snapshots at read time: sum location/account/policy counts and record volume; take the **union** of perils/sub-perils; **combine** geography (region/state set) and the currency set; count portfolios. The same derivation feeds both the EDM-page aggregate strip (FR-040) and the submission-page per-EDM package-card line (FR-041, via `package_sync_service.get_package_cards`). When an EDM has no backfilled per-portfolio detail, the rollup returns a **graceful pending/empty** marker, never an error (FR-043).

**Rationale**: FR-042 mandates the aggregate be "derived from the stored per-portfolio detail (a roll-up), not a separate Risk Modeler fetch or a computation on the request path" — but "computation on the request path" refers to *exposure computation against Risk Modeler / large exposure sets*; rolling up a handful (1–25) of small stored JSON snapshots in Python is O(portfolios) over already-fetched cache rows, well within the page-load budget (SC-007). Storing the rollup separately would duplicate state and risk drift from the per-portfolio truth (Article 2 — derive, don't store).

**Alternatives considered**: (a) Store an `irp_edm.aggregate_detail` snapshot alongside the per-portfolio ones — rejected: duplicates derivable state, drifts on partial re-backfill, adds a write with no read benefit. (b) Compute the rollup in the worker and store it — same rejection; the worker already stores the per-portfolio truth the view rolls up.

---

## R5 — Treaty Excel export: server-side `.xlsx` via `openpyxl`

**Decision**: Add **`openpyxl`** to `[project.dependencies]` and generate the treaty workbook **server-side in-process** from the stored `irp_treaty.attributes` snapshots — `treaty_service.build_treaty_workbook(edm_id) -> bytes`. Columns are the **union** of attribute keys across the EDM's treaties (so a wide/heterogeneous set exports cleanly); one row per treaty. The download is an **authenticated GET** `/edms/{id}/treaties.xlsx` returning the bytes with `Content-Disposition: attachment` — **no Risk Modeler call** (reads only stored detail), so it is not an Article-11 concern.

**Rationale**: FR-024 / spec Assumption ("a standard `.xlsx` workbook generated server-side"). `openpyxl` is the standard pure-Python `.xlsx` writer, no system deps, MIT-licensed. Building from the stored snapshot keeps the export off the request-path-to-RM entirely and makes it testable without IRP.

**Alternatives considered**: (a) CSV export — rejected: FR-024 says Excel; a wide treaty set with mixed types reads better as `.xlsx`. (b) Client-side generation — rejected: no SPA (Article 8); the data lives server-side. (c) A heavier engine (`xlsxwriter`/pandas) — rejected: `openpyxl` covers a flat one-sheet workbook with no extra weight.

---

## R6 — Redesigned EDM detail page + broker-analysis view; reachability without new nav

**Decision**: **Redesign the existing** `app/templates/pages/edm_detail.html` (currently a minimal header + source-file + error-recovery form) into the portfolio-primary layout: a **minimal EDM header** (name, status + `as_of`, source file + identifiers, portfolio count — **no cedant/LOB**, FR-011), a **compact aggregate strip** (US4), the **inline per-portfolio table** as the primary content (US1), and a **treaties section** (US2, expand/collapse + horizontal scroll + Excel export). The route `/edms/{edm_id}` (in `app/routers/edms.py`, `_detail`) now passes the full `get_edm_detail(...)` payload. The RDM detail page `/rdms/{rdm_id}` gains the **broker-analyses-grouped-by-`rdm_id`** view with per-analysis settings/metadata (US3). **No new nav nodes** — `irp.edm_library` / `irp.rdm_library` and the detail routes already exist (spec 003 / R12); the redesign changes page *content*, and the pages remain reachable from the libraries and the submission package cards (Article 1 satisfied without a manifest edit).

**Page composition is fixed by `ui.md`** (the UI/page-composition contract), derived from the approved previews `docs/ui_previews/edm_detail.html` (rev 7) and `rdm_detail.html` (rev 3): a single reusable **`.dtable`** expandable-comparison component (frozen identifying column; per-row `<details>` expand; pinned + rail-connected expanded body) renders Portfolios, Treaties, and Broker analyses on both pages; sections default open, row-level drills default closed, rate/event-rate is the one nested sub-drill. The EDM page also carries a **standalone Broker-analyses section** (grouped by source RDM) and **per-portfolio inline analyses** — both keyed on the R9 linkage.

**Rationale**: Article 1 — a page is one nav node + handler + template; this iteration edits the template + handler payload of existing nodes, adding no scattered config. The design record (design note `04` §4–§5, FR §2.2) fixes the layout: per-portfolio primary, aggregate as quick orientation, treaties EDM-level. Scanability is the driver — dense tables over card stacks, identity frozen while scrolling (ui.md).

**Alternatives considered**: (a) A new dedicated portfolio drill-down page — rejected this iteration (spec Assumption): per-portfolio detail is **inline** on the EDM page; the Submission→…→EDM→Portfolio drill-down (the future analysis-launch entry point) lands with the analysis iterations. (b) A new "EDM detail" nav node — rejected: the node already exists; only its content changes.

---

## R7 — Forward-only backfill + graceful empty/failure states

**Decision**: *Automatic* backfill is **forward-only** (FR-003): only imports reaching `FINISHED` after this capability deploys enqueue `backfill_edm_detail` / the extended analysis capture. **No bulk sweep** of previously-imported entities. *(Amended 2026-07-23 post-US1: a per-EDM manual **Sync** action — alternative (b) below, now adopted — re-runs the same worker on demand, keyed `analyst_request` + `edm_id` via `ensure_pending_rwb_job`; it covers pre-capability EDMs and failed fetches.)* Every detail view degrades gracefully:
- **No snapshot** (imported before this capability, or fetch pending/failed) → the per-portfolio section, treaty section, aggregate strip, and package-card line render a **"detail not available — re-import to populate" / pending** state; the EDM's core record (name, status, source file, identifiers) still displays (FR-017/FR-043/SC-006).
- **Backfill fetch fails/times out** → the `backfill_edm_detail` `rwb_job` fails via the existing worker-failure path (it is **recoverable** through the reconciler/retry machinery), the EDM's *ready* status is **not** reverted (FR-005), and the view shows "detail unavailable." Re-fetch is idempotent (overwrites the snapshot, R2).

**Rationale**: Matches the Iteration-3 exit wording ("a newly completed import backfills its detail data automatically") and the spec's forward-only scope call; reuses the Iteration-2 `rwb_job` failure/reconcile machinery so no new recovery mechanism is built.

**Alternatives considered**: (a) One-time bulk backfill of existing entities — rejected (spec scope call): deferred as a later addition; the demo path uses an EDM imported after deploy. (b) A manual per-EDM "refresh" button — originally rejected, **adopted 2026-07-23** as the EDM detail page's Sync action (same worker, as anticipated: "trivial to add later on the same worker").

---

## R8 — Broker-analysis grouping by `rdm_id`; un-emptying the analysis counts

**Decision**: The broker-analysis view lists the `irp_analysis` rows captured in spec 003 (`rdm_id` set), **grouped by `rdm_id`** so a broker analysis applied across M EDMs is shown **once** ("1 broker analysis across 4 EDMs"), not M times (DATA_MODEL §6 — the M rows are handles keyed on `irp_id`, one per (RDM×EDM) pair, sharing one `rdm_id`). Each analysis shows its parsed `settings_metadata` (R3); a group row (`is_group = true`) is displayed as a group (FR-035). This also **un-empties the analysis counts** on the package card and EDM detail (spec 003 D5 rendered them empty because the rows existed only for delete-enumeration) — FR-050. Missing/partial metadata fields render a blank/unavailable state, never an error (US3 acceptance 3).

**Rationale**: FR-030/FR-031/FR-035/FR-050 + DATA_MODEL §6 grouping rule. The rows already exist from spec 003; this iteration surfaces them with metadata and corrects the deliberately-empty counts. Never key on name (RM allows duplicates); group on `rdm_id`, dedup display on it.

**Alternatives considered**: (a) Show one row per (RDM×EDM) pair — rejected: floods the view and misrepresents "one broker analysis" as many (DATA_MODEL §6). (b) Handle the zero-EDM RDM-only case now — rejected: RDM-only import stays deferred (spec 003 D3); every tracked RDM has ≥1 EDM and every broker analysis an `edm_id`.

---

## R9 — Portfolio-to-analysis linkage: capture the RM pointer, resolve the portfolio at read time

**Decision**: Associate each broker analysis with the **portfolio it was run against** (FR-036) by **capturing** Risk Modeler's exposure pointer on the analysis and **resolving** the owning portfolio at read time — *not* by writing a stored FK at backfill time.

- **Capture (worker, at analysis backfill):** RM returns `exposureResourceId` + `exposureResourceType` per analysis. The extended `backfill_rdm_analyses` stores the id in a light typed column **`irp_analysis.exposure_resource_id`** (`NVARCHAR(64)` null) **only when `exposureResourceType == "PORTFOLIO"`**; otherwise it stays null. (The full raw pointer, including the type, is also present in the `settings_metadata` snapshot for fidelity — R2.) This requires the gateway `AnalysisHit` value object, which today **drops** `exposureResourceId`, to carry `exposure_resource_id` + `exposure_resource_type`.
- **Resolve (read services, at view):** the owning portfolio is `irp_portfolio WHERE edm_id = analysis.edm_id AND irp_id = analysis.exposure_resource_id`. `analysis_service` performs this join and returns the resolved portfolio (or `None`). Display precedence (ui.md §4): `is_group` -> **Group**; resolved -> **portfolio name**; else -> **not linked**. Some analyses **will not resolve** (a group's exposure is not a single portfolio; some results reference a non-portfolio resource) — that is a normal *not-linked* state, never an error.

**Rationale**:
- **Import-order safety.** `irp_portfolio` rows are created by `backfill_edm_detail` on `import_edm` FINISHED; analyses are captured by `backfill_rdm_analyses` on `import_rdm` FINISHED (enqueued *after* the EDM finishes). A stored FK written at analysis-backfill time would frequently be null (portfolio not yet present) and need a re-resolution/reconciliation pass. **Read-time resolution is always correct** regardless of which backfill lands first, and a re-import that changes portfolio ids self-heals on the next read.
- **Derive, don't store (Article 2 / consistent with R4).** The linkage is a *derivation* over two already-stored truths (the analysis's captured RM pointer + the portfolio's `irp_id`), exactly like the EDM-aggregate rollup. Storing a resolved FK would duplicate derivable state and drift on partial re-backfill.
- **Typed pointer, not JSON extraction.** Promoting `exposure_resource_id` to a light typed column (like `irp_id`) keeps the resolve join clean across the SQL-Server and SQLite tiers (no `JSON_VALUE`/`json_extract` in the join path); the snapshot still holds RM's verbatim payload.
- **Groups stay simple (this session's UI review).** A group is a **single analysis**; its contributing sub-analyses are **not knowable** from RM, so no member breakdown is displayed and the group's Portfolio cell is just **Group**. Consequently the self-ref `irp_analysis.group_parent_id` (DATA_MODEL §6) has nothing to populate it this iteration and is **deferred**, exactly as `irp_job.irp_portfolio_id` is (data-model §4/§6) — tracked, not silently added.

**Alternatives considered**: (a) **Stored `portfolio_id` FK resolved at write time** — rejected: import-order fragility (portfolio may not exist yet -> null), needs a re-resolution/reconcile step, and drifts on re-import; buys only a marginally simpler read. (b) **Resolve by joining a JSON-extracted key** out of `settings_metadata` — rejected: `JSON_VALUE`/`json_extract` in a cross-tier join path is fragile; a typed pointer column is cleaner and cheap. (c) **Populate `group_parent_id` to model group membership** — rejected: RM does not expose which analyses composed a group; nothing can populate it, so it is deferred (not added this iteration).

---

## Summary of decisions

| # | Area | Decision |
|---|------|----------|
| R1 | IRP read surface | Extend `irp_gateway` with single-item read methods (portfolio enumeration + per-portfolio exposure, treaty attributes, analysis metadata); loop app-side; fake mirrors them; **confirm signatures vs the active wheel before implementing** (pre-release); gaps → `IRP_INTEGRATION_FOLLOWUPS.md`. TIV/geo/currency/sub-perils: per-EDM DataBridge aggregate (`get_edm_exposure_summary`, documented single-item exception) |
| R2 | **Storage shape** | **JSON snapshot cache** columns — `irp_portfolio.exposure_detail` / `irp_treaty.attributes` / `irp_analysis.settings_metadata` (nullable; null = graceful empty); identity columns stay typed; overwrite-in-place idempotent; external RM vocabularies stored verbatim (no kind table); normalized/filterable model deferred to Iteration 4 |
| R3 | Backfill wiring | Forward extension of the completion path: new `backfill_edm_detail` `rwb_job_type` enqueued alongside `upload_rdm` on `import_edm` FINISHED (independent of package/RDMs); analysis metadata rides the **existing** `backfill_rdm_analyses`; idempotent on the existing dedup key |
| R4 | Aggregate rollup | **Derived in `edm_service`** from per-portfolio snapshots (sum counts / union perils / combine geography+currency); feeds both the EDM strip and the submission package-card line; never stored, never fetched on the request path |
| R5 | Treaty export | Add **`openpyxl`**; server-side `.xlsx` from stored treaty snapshots (union of attribute keys); authenticated GET download; no Risk Modeler call |
| R6 | Pages / nav | Redesign the existing `edm_detail.html` (minimal header + aggregate strip + inline per-portfolio table + treaties) and extend `rdm_detail.html` (broker analyses); **no new nav nodes**; no dedicated portfolio page this iteration |
| R7 | Forward-only / graceful | Only post-deploy imports backfill automatically; no bulk sweep; per-EDM manual **Sync** re-runs the worker on demand (amended 2026-07-23); no-snapshot → pending/unavailable state; fetch failure preserves *ready* + recoverable via the existing `rwb_job` machinery; re-fetch idempotent |
| R8 | Broker analyses | Group `irp_analysis` by `rdm_id` (shown once across M EDMs); surface `settings_metadata`; `is_group` shown as a group; un-empty the analysis counts (FR-050); zero-EDM RDM-only stays deferred (D3) |
| R9 | **Portfolio↔analysis linkage** | Capture RM's `exposureResourceId` as a light typed `irp_analysis.exposure_resource_id` (only when type = PORTFOLIO); **resolve the owning portfolio at read time** (join `irp_portfolio` on `edm_id`+`irp_id`) — derived, not a stored FK (import-order safe, R4-style); `is_group` -> "Group", unresolved -> "not linked"; `group_parent_id` deferred (members unknowable); extend gateway `AnalysisHit` to stop dropping the pointer |
