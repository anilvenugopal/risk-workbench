# Phase 0 Research: One-Click Portfolio Breakouts (Iteration 4)

**Spec**: [spec.md](spec.md) | **Date**: 2026-07-29

The spec carries no `[NEEDS CLARIFICATION]` markers. This file records the concrete decisions planning owns, each as *Decision / Rationale / Alternatives considered*. R1 carries a residual-unknowns list that is deliberately **front-loaded as a sandbox spike** — it must close before the worker loop is implemented.

---

## R1 — The slice-creation API shape: **select accounts → create → add-by-IDs** (and the library enhancements)

*(Revised 2026-07-29 after the Risk Modeler LLM companion's conceptual walkthrough — [Splitting a master portfolio into LOB sub portfolios.md](<Splitting a master portfolio into LOB sub portfolios.md>). Its endpoint paths are explicitly illustrative; they are mapped to real, documented Platform operations below and validated against `../knowledge/` and the wheel.)*

**Decision.** There is **no one-shot create-by-filter endpoint** (confirmed: the create body has no filter fields). A slice is created by a **three-call sequence** that mirrors the RM UI's own "filter accounts → select → Add to Portfolio" flow:

1. **Select** — enumerate the **source portfolio's** account IDs matching the slice value, via a documented account read, **paged fully**.
2. **Create** — `POST /platform/riskdata/v1/exposures/{exposureId}/portfolios` → **201** + Location. Already wrapped: `create_portfolio()` (wheel 0.2.1; duplicate-name guard included).
3. **Add** — `PUT /platform/riskdata/v1/exposures/{exposureId}/portfolios/{id}/filtered-accounts` (RM's *Add filtered accounts by portfolio*) with body:

   ```json
   { "selectAll": false, "markedAccounts": [45, 46], "queryFilter": "", "manageExistingAccounts": false }
   ```

   A sibling `PATCH` *Manage accounts by portfolio* also exists on the Platform; the spike's UI-traffic capture confirms which the UI drives and the exact body semantics.

**Validation against `../knowledge/` and the wheel (0.2.1):**

| Step | Endpoint evidence (knowledge base) | Wheel today |
|---|---|---|
| Select | `GET .../portfolios/{id}/accounts` documented (`op-search-portfolio-accounts`; capture `getportfolioaccounts`, 2026-07-23) with `filter` + `sort` params — **but its filter property list is closed** (accountid, accountName, accountNumber, branchName, cedantName, ownerName, producerName, underwriterName): **no LOB, no state**. The EDM-level Platform accounts search (`GET .../exposures/{exposureId}/accounts`) supports `filter` + **`allowDeepFilters`** (documented keyword — Response Filtering guide capture); its property vocabulary is not captured → the U1 lead. | `search_accounts_by_portfolio()` exists but passes **no filter and no pagination** — truncation risk (knowledge: pagination "verify tenant behavior") |
| Create | `op-create-portfolio` documented, sync 201 | `create_portfolio()` ✅ |
| Add | *Add filtered accounts by portfolio* (PUT) reference page fetched 2026-07-30 ([managefilteredaccounts](https://developer.rms.com/platform/reference/managefilteredaccounts)): **200 "Accounts added to portfolio" is the only success status — synchronous, no async/job/workflow mention**. Body schema confirmed (`selectAll` overrides `markedAccounts`; `manageExistingAccounts=true` "markedAccount values are ignored" — ambiguous, → U2). Legacy variants (`PUT /riskmodeler/v1|v2 .../filteredaccounts`) are 202 + workflow job but **legacy riskmodeler endpoints are out of scope by project direction (Platform only)**. A sibling PATCH *Manage accounts by portfolio* exists (TOC; not crawled). | **missing** — the library enhancement |

**Why explicit account IDs (`markedAccounts`) are the primary mode, not `queryFilter`:** (a) **source scoping is exact by construction** — the selection runs against the source portfolio's own account list, so a slice can never grab accounts from elsewhere in the EDM (formerly blocking unknown U3, now closed on the primary path); (b) the `selectAll`/empty-filter footgun (silently cloning the whole EDM into a slice) is structurally unreachable; (c) a wrong selection filter fails **visibly** (zero or wrong accounts, inspectable before anything is written to RM) instead of silently mis-populating a portfolio; (d) it is the flow the RM companion describes and the UI itself performs. The `queryFilter` populate mode stays in the method signature as a **spike-conditional optimization** (one call instead of select+add), adopted only if the spike proves its tokens *and* source scoping.

**Library enhancements (two, both `PortfolioManager`; developed via `make irp-local`, published TestPyPI 0.2.2.dev, pinned before implement completes; final signatures re-confirmed against the active wheel, `make irp-status`):**

1. **Selection read**: `search_accounts_by_portfolio` gains `filter`, `sort`, `limit`, `offset` (all documented params) **plus a fully-paginated variant** (the `search_portfolios_paginated` pattern) — the selection read must never truncate.
2. **Populate write**: `add_filtered_accounts(exposure_id, portfolio_id, *, marked_accounts=None, query_filter="", select_all=False, manage_existing_accounts=False)` + `constants.FILTERED_ACCOUNTS` — wraps the PUT; **synchronous** (doc-verified 200-only, 2026-07-30); raises on any unexpected 202 instead of normalizing; never polls.

If the spike lands selection on the EDM-level deep-filter search, a third read method (`search_accounts` with `allow_deep_filters`) is added **then** — not speculatively.

**Residual unknowns → sandbox spike (must close before the worker is built; codified afterward as `tests/irp/test_filtered_accounts.py`):**

| # | Unknown | Probe |
|---|---|---|
| U1 | **The selection query**: which endpoint + filter tokens select a portfolio's accounts by LOB / by state. The portfolio-accounts GET's documented property list has neither; `allowDeepFilters=true` on the EDM-level search is the lead (LOB lives on policies, `admin1*` on locations — "deep" = child-entity predicates). | Probe `GET .../exposures/{id}/accounts?filter=…&allowDeepFilters=true` with candidate tokens (`lobName`, `LOB Name`, `lineOfBusiness`, `admin1Name`, `admin1Code`) — RM 400s usually name the offending property. **Capture RM UI network traffic** for "Accounts grid → filter by LOB/state → select → Add to Portfolio" — the authoritative answer for both the selection query and the add call's body. |
| U2 | `filtered-accounts` PUT already-member / `manageExistingAccounts` semantics (the 200-sync question is **closed** — reference doc fetched 2026-07-30; the doc's `manageExistingAccounts` description is ambiguous). | Direct sandbox call with a small `markedAccounts` list (confirms the doc in practice); re-PUT the same list to learn already-member behavior; probe `manageExistingAccounts=true` (feeds R7 adopt-then-populate). |
| U3 | ~~queryFilter source-portfolio scoping~~ — **closed on the primary path by construction**; open only for the queryFilter optimization. | Only if pursuing the optimization: portfolio predicate under `allowDeepFilters`. |
| U4 | State filter vocabulary for the **selection** filter: names (`Florida`), codes (`FL`), or either (feeds R6). | Read the captured UI filter; cross-check both tokens against a known EDM. |
| U5 | Account-bucketing confirmation: does a LOB/state selection return **whole accounts** with *any* matching policy/location — so a mixed-value account is selected into every matching slice? (Structurally implied: portfolios are collections of accounts.) | Split a known mixed-LOB / multi-state account in sandbox; confirm it appears in every matching selection and slice. |
| U6 | Portfolio-accounts pagination (limit/offset not in the captured parameter list; "verify tenant behavior"). | Page a >100-account portfolio; verify the paginated variant collects all. |

Plus the RM portfolio **name-length** spot-check (feeds R4's 200-char cap).

**Rationale.** Every step of the primary path is a documented Platform operation the knowledge base already carries evidence for; the one genuine unknown — the selection query — fails safe (visibly, before any RM write) and is answerable in hours via UI traffic capture. The wheel reuses its tested create; the new surface is one read upgrade + one write wrapper.

**Alternatives considered.** (a) One-shot `create_portfolio_by_filter` — **does not exist**; the create body has no filter fields and no such Platform operation is documented. (b) `queryFilter`-primary populate (this R1's pre-revision shape) — demoted: filter tokens *and* source scoping both unverified there, and a filter bug silently mis-populates RM; retained as a spike-conditional optimization behind the same gateway seam. (c) Client-side bucketing (page all accounts, resolve each account's LOB/state via per-account child reads) — rejected: N+1 reads per slice, and the needed attributes (policy LOBs, location states) are not in the portfolio-accounts response's verified fields. (d) Bypassing the library with raw `client.request()` from the app — rejected: Moody's schema knowledge belongs in the integration library (Art. 11 spirit; established FOLLOWUPS practice).

---

## R2 — Execution locus: the fan-out runs as a `run_breakout` `rwb_job` worker

**Decision.** The confirm POST validates the gate and the summary-freshness check (R5), idempotently enqueues one **`run_breakout`** `rwb_job` (`input_data = {edm_id, portfolio_id, dimension}`), and returns immediately; the Dramatiq worker executes the loop (per-slice: select accounts → create → add-by-IDs → upsert row), records per-slice outcomes in `output_data`, and on completion idempotently enqueues `backfill_edm_detail`. The EDM page's existing 3-second self-poll (`live` flag) surfaces slices as they land and the outcome banner at the end.

**Rationale.** (a) Per-slice latency is unverified (the add step is doc-verified synchronous, but a synchronous add of many accounts can still be slow) and now includes paging the selection read; a 40-state fan-out on the request path could hold an HTTP request for minutes. (b) Every existing multi-call IRP loop lives in the worker tier with per-item failure isolation — four precedents in `package_jobs.py`. (c) `rwb_job` gives the loop atomic claim, heartbeat/reconciler recovery for a wedged run, idempotent enqueue (`UNIQUE(requestor_type, requestor_id, rwb_job_type)`), and `input_data`/`output_data` as the durable audit record. (d) Article 11 *permits* request-path submission but does not require it; workers already submit imports. (e) The UI needs no new machinery — the self-poll and toast conventions already exist.

**Alternatives considered.** (a) Request-path loop (the PRD §10A.5 sketch) — rejected: acceptable for ≤5 sub-second creates, indefensible at 40 slices × unknown latency; no recovery if uvicorn dies mid-loop. (b) One rwb_job per slice — rejected: explodes queue rows, loses the single per-breakout outcome record, and ordering/coalescing adds complexity a single-loop job doesn't have. (c) SSE progress — rejected: the 3-second self-poll already exists and is the shipped pattern (spec 004).

---

## R3 — Lineage schema: three columns on `irp_portfolio` + a kind table; no first-class breakout table

**Decision.** `irp_portfolio` gains `source_portfolio_id` (Uuid NULL, self-FK), `breakout_dimension_code` (NVARCHAR NULL, FK → `breakout_dimension_kind.code`), `breakout_value` (NVARCHAR(256) NULL) — all NULL for broker-arrived portfolios. New kind table `breakout_dimension_kind` (`code` PK, `label`, `sort_order`; seeds `lob`, `state`). Filtered unique index `UNIQUE(source_portfolio_id, breakout_dimension_code, breakout_value) WHERE source_portfolio_id IS NOT NULL AND deleted_at IS NULL` — the slice-idempotency key. Breakout-created rows populate `inserted_by` (first writer to do so; worker receives the actor id in `input_data`).

**Rationale.** Lineage is provenance the display, idempotency, and audit all need; three nullable columns keep the entity thin (DATA_MODEL §5 style). The dimension is an app-defined closed set the code dispatches on → kind table (Article 3 default); the value is external exposure vocabulary stored verbatim → plain column (the spec-004 snapshot rationale). The filtered unique index makes "detect already-created slices" a constraint, not a convention.

**Alternatives considered.** (a) A first-class `breakout` table (header + slice rows) — rejected: it would duplicate what `rwb_job.input_data/output_data` + lineage columns already record, and nothing queries a breakout as an entity (Article 1: boring, one-place-to-change). Revisit only if breakout history/re-run UX becomes a feature. (b) A JSON `breakout_filter` snapshot column — rejected: the two directed breakouts are fully described by dimension+value; a filter-spec blob is the custom-filter-builder's concern (out of scope). (c) Recording lineage only in the name — rejected: names are analyst-visible and collision-suffixed; parsing them is fragile.

---

## R4 — Naming: deterministic `{source} - {value}` with deterministic collision suffixing

**Decision.** Slice name = `"{source_name} - {value}"`, trimmed; if it collides with any existing portfolio name in the EDM (or another planned slice), append ` (2)`, ` (3)`, … deterministically (lowest free suffix at plan time). `portfolio_number` is left empty (the wheel defaults it to the name, truncated to 20 chars); `description` = auto-generated sentence naming the source, dimension, and value (searchable in RM). App cap 200 chars (NVARCHAR(256) column; RM's own limit spot-checked in the sandbox spike). The plan builder computes names as a **pure function** of (source name, values, existing names) so preview and worker agree, and re-running yields the same names (idempotency depends on it).

**Rationale.** No naming convention exists anywhere in the repo for sub-portfolios (exhaustively confirmed); the analyst-recognizable "source - value" pattern is what lineage looks like *inside* Risk Modeler, where the lineage columns don't exist. Determinism is what lets adopt-by-name reconciliation work.

**Alternatives considered.** (a) The EDM `TY{yy}{mm}_…` convention — rejected: that names *databases*, not portfolios, and encodes submission attributes irrelevant to a slice. (b) User-editable names in the preview — rejected: one-click semantics; the custom builder can offer that later. (c) UUID-suffixed names — rejected: unreadable in RM, defeats the "recognizable slice set" purpose.

---

## R5 — Value enumeration & gate composition

**Decision.** Enumeration reads the **stored** spec-004 summary only: `irp_portfolio.exposure_detail → summary.lines_of_business` / `summary.states` (defensive parse, exactly like existing readers). Gate rule (one testable function in `breakout_service`, FR-002/003): *EDM exists ∧ not deleted ∧ status `ready` ∧ source portfolio exists ∧ not deleted ∧ summary present ∧ (per dimension) ≥ 2 distinct values*. Missing summary → disabled-with-reason pointing at the existing per-EDM **Sync**; < 2 values → dimension disabled ("only one value present"). The confirm POST re-checks the gate server-side (409 + re-rendered fragment on failure — the `packages.py` precedent) **and verifies summary freshness** (FR-002a, clarified 2026-07-30): the source portfolio's current RM `stampDate` — read at submit time via `search_portfolios` (Article 2's name-resolution pattern; the flow's one web-layer RM call) — must equal the stamp the `backfill_edm_detail` worker captured alongside the summary (stamp-to-stamp equality, no wall-clock comparison; capture happens before the DataBridge read so the stamp is conservative). Mismatch or missing stamp → refusal pointing at Sync, **no `rwb_job` row**. `stampDate` was validated 2026-07-30 as an updated-at equivalent (updating underlying portfolio data changes it); RM exposes no explicit updated-at.

**Rationale.** Article 11 forbids request-path DataBridge/RM reads; the summary is already the page's source of truth for exactly these values, and its `as_of`/Sync trust model is shipped. The gate must live in one place with unit tests (Article 12 names it a must-test) — there is no central gate module yet, so `breakout_service` is that place for this op.

**Alternatives considered.** (a) Live DataBridge read at modal-open — constitution violation (worker-side only). (b) A fresh enumeration inside the worker (values could have drifted from the preview) — rejected: the confirm approves a *specific slice list*; the worker recomputes the plan from the same stored summary (identical inputs → identical plan), so what runs is what was approved unless a Sync intervened — and the freshness check guarantees the approval itself was made against current RM data. (A worker-side stamp re-check was considered and dropped: the confirm-to-run window is seconds, and the zero-match per-slice failure already backstops residual drift loudly.)

---

## R6 — State values: stored `COALESCE(Admin1Name, Admin1Code)` vs. the RM filter vocabulary

**Decision.** Treat the stored state string as **display value** and resolve the **selection-filter token** question in the R1 sandbox spike (U4: does the account-selection filter want `admin1Name`, `admin1Code`, and in which spelling — the summary today stores a name when present, else a code). If the selection needs the *other* form (or both), extend `sql/databridge/portfolio_states.sql` **additively** to return name *and* code per state (readers parse defensively; additive JSON change is spec-004-compatible), populated on the next Sync/backfill. The plan builder then carries `(display_value, filter_value)` per slice; `breakout_value` stores the display value.

**Rationale.** This is the one place the enumeration source and the selection contract can disagree; deciding the mapping *before* implement (spike) avoids shipping a geography breakout that silently creates empty slices. On the marked-accounts primary path a token mismatch fails **visibly** (zero accounts selected → the slice fails loudly, nothing mis-populated in RM) — but it would still fail, so the mapping must be settled either way.

**Alternatives considered.** (a) Assume `FL`-style codes work — rejected: unverified, and the stored value is a *name* when `Admin1Name` is populated. (b) Normalize stored values to codes via a static US-state map — rejected: geography is not US-only and RM's vocabulary is the authority; a hardcoded map is exactly the "pre-defined region constant" the design record warns against.

---

## R7 — Idempotent re-run & adopt-by-name (no rollback)

**Decision.** Per slice, the worker: (1) skips if a live `irp_portfolio` row already matches the lineage key (source, dimension, value); (2) otherwise creates + populates + inserts the row. If `create_portfolio` fails on the **duplicate-name guard** (slice exists in RM but not app-side — the documented at-least-once window, or a pre-existing same-named portfolio), the worker resolves the existing RM portfolio by name (`search_portfolios`), **adopts** it (inserts the lineage row with that `irp_id`) — and, because the worker holds the slice's freshly computed account IDs, it then **re-runs the add step** so an adopted-but-empty portfolio (a crash between create and add) is healed, not left hollow. If U2 shows re-adding already-member accounts is unsafe, the fallback is recording the outcome as `adopted (verify contents)` rather than guessing. Re-enqueue for a partially failed breakout goes through `ensure_pending_rwb_job` (the analyst-re-request path that revives terminal jobs). Partial success completes the job as `succeeded` with per-slice outcomes (the `_upload_rdm_body` precedent); the job fails outright only when *zero* slices succeed.

**Rationale.** No delete exists (MVP + wheel), so forward-only reconciliation is the only recovery shape; every convention here (per-item isolation, partial-success semantics, revive-on-request) already exists in `package_jobs.py`.

**Alternatives considered.** (a) Failing the whole job on first slice error — rejected: orphans nothing but retries everything, and contradicts the loop-precedent semantics. (b) Deleting app rows for slices whose populate failed — rejected: the RM portfolio still exists; the row + outcome is the honest state.

---

## R8 — UI mechanics

**Decision.** Entry point: a **Break out** action on each portfolio row (visible when the EDM is `ready`; disabled-with-reason otherwise — the `sync_form`/disabled-button conventions). `GET /edms/{edm_id}/portfolios/{pid}/breakout` returns the modal fragment (Alpine open/close, the `package_modal.html` precedent): dimension choice (LOB / State, each enabled per gate), slice-list preview (value → generated name), count, and the two disclosures (account-bucketing overlap — sharpened wording for state; blank-value exclusion). `POST` (CSRF) confirms: gate re-check → enqueue → return the EDM body partial; the existing self-poll takes over (breakout-in-flight indicator riding the `live` flag; completion toast/banner from per-slice outcomes; failures surfaced via the `rwb:toast` + `.form-banner--error` conventions). Lineage display: a badge/sub-line on slice rows ("↳ from *{source}* · LOB: *Homeowners*"), chained lineage showing the immediate source only. **A rendered HTML preview of the modal (including disabled/empty/partial-failure states) is produced from `docs/ui_previews/_scaffold.html` and approved before wiring** — this is a real new interactive surface (docs/UI_WORKFLOW.md rule 1); the row-badge edits are derivative and need no preview.

**Rationale.** Every mechanism named is already shipped and conventionalized; the feature adds one modal and row decorations, not new UI machinery.

**Alternatives considered.** (a) A dedicated breakout page — rejected: the PRD anchors the action on the current-split view; a page adds a nav node for a modal's worth of content. (b) `hx-confirm` instead of a modal — rejected: the preview *is* the feature's judgment surface (slice list + disclosures); a browser confirm can't carry it.

---

## R9 — PRD/documentation pass (part of this iteration)

**Decision.** Update, in the same branch: PRD §23 O6-1/O6-2 register + §10A.5 open-question callout (record the 2026-07-29 product direction: geography breakout ships; account-bucketed keep-whole-account semantics accepted and disclosed; no toggle awaited), PRD §21 Iteration-4 entry (scope narrowed to the two one-click breakouts; filtered builder + complement/"do the opposite" become fast-follows), and `docs/IRP_INTEGRATION_FOLLOWUPS.md` (new entry: `add_filtered_accounts` + the filtered/paginated `search_accounts_by_portfolio` upgrade shipped in-house; note the `filtered-accounts` endpoint findings incl. the doc-verified sync response, the Platform-endpoints-only direction, and the deep-filter selection lead). FUNCTIONAL_REQUIREMENTS §3 gets a pointer note, not a rewrite (it remains the fuller FR surface for the follow-on slices).

**Rationale.** The constitution's source-of-truth doctrine: specs/plans must stay consistent with PRD/DATA_MODEL; leaving the "blocked" notes standing would contradict this spec. DATA_MODEL §5 changes ride with data-model.md (lineage columns + kind table).

**Alternatives considered.** Deferring the doc pass to a later cleanup — rejected: spec 004's practice (and the O6 register's visibility) makes stale blockers actively misleading to the next iteration's planner.
