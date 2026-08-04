# Phase 0 Research: One-Click Portfolio Breakouts (Iteration 4)

**Spec**: [spec.md](spec.md) | **Date**: 2026-07-29 | **Revised**: 2026-08-03

The spec carries no `[NEEDS CLARIFICATION]` markers. This file records the concrete decisions planning owns, each as *Decision / Rationale / Alternatives considered*.

Everything R1 once deferred to a sandbox spike is closed. The evidence is in
[probe-findings.md](probe-findings.md) — the live probe run of 2026-08-03, every
API count cross-checked against Data Bridge. This file cites its findings as
`W-nn`; it does not restate them.

---

## R1 — Composing a sub-portfolio: **select account ids → create → add by id**

*(Rewritten 2026-08-03 against the probe run. The pre-probe version built on `add_filtered_accounts` and `allowDeepFilters`; both lost — see Alternatives.)*

**Decision.** Risk Modeler has **no create-by-filter endpoint** (the create body has no filter fields, and no such Platform operation is documented). A sub-portfolio is composed from three calls, mirroring what the Risk Modeler UI itself does:

1. **Select** — the source portfolio's account ids matching the breakout value.
2. **Create** — `create_portfolio()` → HTTP 201, synchronous.
3. **Add** — `manage_portfolio_accounts(accounts_to_add=[…])` → PATCH, HTTP 200, synchronous, returns `{"addAccounts": {"completed": n, "total": m}}`.

**The selection read differs per dimension**, because LOB is not filterable on any Risk Data operation while state is:

| Dimension | Selection |
|---|---|
| LOB | Read the source portfolio's account ids, then `search_policies_paginated(filter='accountId IN (…)')` and group client-side on `policy["lob"]["lobName"]`. **One pass yields every LOB at once** — the whole fan-out shares one read. |
| State | `search_locations_paginated(filter='accountId IN (…) AND admin1Code = "TX"')` — one read per state, filtered server-side. Account id at `row["location"]["property"]["accountId"]` (W-15). |

Both forms scope to the source portfolio by **listing its account ids**, because no portfolio predicate exists: `portfolioId`, `portfolioName`, `portInfoId`, `portfolio`, and `portfolioNumber` are all rejected on `getAccounts` (W-6). That forces **chunking** — the filter travels in the URL and dies at HTTP 431 around 4,872 *characters* (not a fixed id count, so a book with 7-digit account ids fits roughly half as many per request). The worker chunks by composed filter length against a named constant, conservatively below the measured ceiling: 431 is a header-size limit, so the bearer token shares the budget and the ceiling is not a constant across tenants.

**What the library provides** (verified against `portfolio.py` / `utils.py` at `a04e3d7`, not against prose — [contracts/irp-library.md](contracts/irp-library.md) is the full contract):

| Method | What this feature depends on |
|---|---|
| `search_accounts_by_portfolio_paginated` | the source portfolio's account ids |
| `search_policies_paginated` | the LOB selection read |
| `search_locations_paginated` | the state selection read |
| `create_portfolio` | synchronous 201; raises `IRPValidationError` on a duplicate name, a name over 40 characters, or a number over 20 |
| `manage_portfolio_accounts` | synchronous 200; `completed`/`total`; idempotent (W-9) |
| `search_portfolios` | the confirm-time `stampDate` read (R5) and adopt-by-number (R7) |

Three things the library deliberately leaves to this repo: it **does not chunk**, it **does not shorten names** (both name fields raise instead of truncating), and `allow_deep_filters` is gone from the public signature so the deep-filter path cannot be taken by accident.

**Residual unknowns — all closed** (the spike that gated this entry is done, T-08):

| # | Unknown | Resolution |
|---|---|---|
| U1 | Which endpoint + tokens select a portfolio's accounts by LOB / by state | `searchPolicies` + client-side grouping for LOB; `searchLocations` + `admin1Code` for state (W-7). The `allowDeepFilters` lead is dead — zero rows with HTTP 200 where the truth is 272 (W-7) |
| U2 | `filtered-accounts` already-member / `manageExistingAccounts` semantics | Moot: the PUT is not the add method. `manage_portfolio_accounts` (PATCH) is idempotent, and `completed < total` on a re-run is healthy, not an error (W-9) |
| U3 | `queryFilter` source scoping | Closed permanently — no filter names a source portfolio (W-6), so the one-call form cannot express "the TX accounts of portfolio 1" |
| U4 | State filter vocabulary | `admin1Code`. `admin1Name` returns zero rows until GeoHaz runs (W-12) and is a separate exposure attribute, not a rendering of the code (W-18) → R6 |
| U5 | Account bucketing | Confirmed on both dimensions against a purpose-built multi-LOB book: one matching location or policy admits the whole account, all of its locations *and* all of its policies (W-3, W-11) |
| U6 | Pagination | Record offset on this tenant; `paginate_search` now **raises** rather than returning a list it cannot show is complete (W-14) |
| — | RM portfolio name length | **40 characters**, boundary confirmed exactly: 40 creates, 41 rejects. `portfolio_number` caps at 20 (W-2, W-13) → R4 |

**Rationale.** Every call is a documented Platform operation, both writes are synchronous (200 is the only success status; any other 2xx raises), and the whole sequence was run end to end against two real state breakouts and a purpose-built multi-LOB book with every count cross-checked in Data Bridge (W-1, W-11). The one place the design has to be careful is under-selection: a short selection read produces a sub-portfolio missing accounts and reports success. The library converts that into an exception (W-14), and the worker compares the populated portfolio against the persisted plan (R10) rather than trusting response counts.

**Alternatives considered.** (a) `add_filtered_accounts` (PUT `.../filtered-accounts`) as the add step — **rejected**: it returns `{}`, so the worker cannot tell an empty populate from a full one without a read-back, while the PATCH reports `completed`/`total`. Worse, `manageExistingAccounts=true` returns HTTP 200 and adds nothing at all, so it is a mode switch, not the heal-a-partial-write option it reads as (W-1). (b) `getAccounts` with `allowDeepFilters=true` as the state selection — **rejected**: zero rows with HTTP 200 at all nine scope sizes tested where Data Bridge and `searchLocations` both say 272; scope size, filter length, and vocabulary were all ruled out as causes (W-7). (c) One-shot `create_portfolio_by_filter` — does not exist. (d) `queryFilter` one-call populate — **closed permanently**, not deferred (U3 above); this retires T-09. (e) Filtering policies by `lobId` instead of grouping client-side — rejected: HTTP 500, not a clean 400 (W-15). (f) Client-side bucketing via per-account child reads — rejected: N+1 per sub-portfolio, and one `searchPolicies` pass already yields every LOB. (g) Raw `client.request()` from the app — rejected: Moody's schema knowledge belongs in the integration library (Article 11).

---

## R2 — Execution locus: the fan-out runs as a `run_breakout_*` `rwb_job` worker

**Decision.** The confirm POST validates the gate and the summary-freshness check (R5), persists the approved plan and idempotently enqueues one `run_breakout_lob` / `run_breakout_state` `rwb_job` (R10), and returns immediately; the Dramatiq worker executes the loop (per sub-portfolio: select account ids → create → add by id → upsert row), records per-sub-portfolio outcomes in `output_data`, and on completion idempotently enqueues `backfill_edm_detail`. The EDM page's existing 3-second self-poll (`live` flag) surfaces sub-portfolios as they land and the outcome banner at the end.

**Rationale.** (a) A 40-state fan-out is 40 create+add pairs plus one chunked selection read per state; on the request path that holds an HTTP request for minutes. (b) Every existing multi-call IRP loop lives in the worker tier with per-item failure isolation — four precedents in `package_jobs.py`. (c) `rwb_job` gives the loop atomic claim, heartbeat/reconciler recovery for a wedged run, idempotent enqueue (`UNIQUE(requestor_type, requestor_id, rwb_job_type)`), and `input_data`/`output_data` as the durable audit record. (d) Article 11 *permits* request-path submission but does not require it. (e) The UI needs no new machinery — the self-poll and toast conventions already exist.

**Alternatives considered.** (a) Request-path loop (the PRD §10A.5 sketch) — rejected: indefensible at 40 sub-portfolios, and no recovery if uvicorn dies mid-loop. (b) One `rwb_job` per sub-portfolio — rejected: explodes queue rows and loses the single per-breakout outcome record. (c) SSE progress — rejected: the 3-second self-poll already exists and is the shipped pattern (spec 004).

---

## R3 — Lineage schema: three columns on `irp_portfolio` + a kind table; no first-class breakout table

**Decision.** `irp_portfolio` gains `source_portfolio_id` (Uuid NULL, self-FK), `breakout_dimension_code` (NVARCHAR NULL, FK → `breakout_dimension_kind.code`), `breakout_value` (NVARCHAR(256) NULL) — all NULL for broker-arrived portfolios. New kind table `breakout_dimension_kind` (`code` PK, `label`, `sort_order`; seeds `lob`, `state`). Filtered unique index `UNIQUE(source_portfolio_id, breakout_dimension_code, breakout_value) WHERE source_portfolio_id IS NOT NULL AND deleted_at IS NULL` — the idempotency key. Breakout-created rows populate `inserted_by` (first writer to do so; the worker receives the actor id in `input_data`).

`breakout_value` stores the value the **filter** uses: `Admin1Code` for state (R6), `LOBNAME` for LOB. It is not a display string — see R6 for why the state name cannot serve as the identity.

**Rationale.** Lineage is provenance the display, idempotency, and audit all need; three nullable columns keep the entity thin (DATA_MODEL §5 style). The dimension is an app-defined closed set the code dispatches on → kind table (Article 3 default); the value is external exposure vocabulary stored verbatim → plain column (the spec-004 snapshot rationale). The filtered unique index makes "detect already-created sub-portfolios" a constraint, not a convention.

**Alternatives considered.** (a) A first-class `breakout` table (header + child rows) — rejected: it would duplicate what `rwb_job.input_data`/`output_data` + the lineage columns already record, and nothing queries a breakout as an entity (Article 1). Revisit only if breakout history/re-run UX becomes a feature. (b) A JSON `breakout_filter` snapshot column — rejected: the two directed breakouts are fully described by dimension + value; a filter-spec blob is the custom-filter-builder's concern (out of scope). (c) Recording lineage only in the name — rejected: names are truncated and collision-suffixed (R4), so parsing them is fragile.

---

## R4 — Naming: the source portfolio name is what gets truncated, and the number is the stable handle

*(Rewritten 2026-08-03: the 200-character cap this entry was designed against does not exist — the real cap is 40, and `portfolio_number` is a second constrained identifier, not a free field.)*

**Decision.** Two identifiers per generated sub-portfolio, both pure functions of inputs that are known at preview time, both persisted in the approved plan (R10).

**Name — at most 40 characters** (W-2: 40 creates, 41 rejects):

```
{truncated source portfolio name} - {breakout value}
```

The breakout value is kept whole and the **source name absorbs the truncation**, because the value is what the analyst scans a generated set for. Source budget = `40 − 3 (" - ") − len(value) − 4`, the last 4 characters reserved so a ` (2)`…` (9)` collision suffix always fits without a second truncation pass. The source name is never cut below 4 characters; a value long enough to demand that is itself truncated from the right, which is safe because the name is not the identity (below). Collision suffixing is unchanged: the lowest free ` (2)`, ` (3)`… against every existing portfolio name in the EDM and every other planned name in the run.

**Number — at most 20 characters** (W-13):

```
P{source portfolio RM id}-{S|L}-{token}
```

Token = the breakout value uppercased with non-alphanumerics removed; if it exceeds the remaining budget, the last 6 characters become 6 hex digits of `sha256(value)` so two long LOB names sharing a prefix cannot collide. Never Python's `hash()` — it is salted per process.

**The number, not the name, is the identity.** `search_portfolios` filters on `portfolioNumber` (W-17), so adopt-an-existing-sub-portfolio resolves on it (R7). This is what makes name truncation harmless: the name depends on what else exists in the EDM at the moment it is computed, while the number depends only on the source portfolio's RM id, the dimension, and the breakout value — none of which change between a preview and a re-run.

The Risk Modeler `description` carries the source portfolio name, dimension, and breakout value **verbatim and untruncated**, so nothing the name loses is lost outright, and lineage stays searchable inside Risk Modeler where the lineage columns do not exist.

**Rationale.** No naming convention for sub-portfolios exists anywhere in the repo (exhaustively confirmed). The 40-character cap bites hardest on LOB, where values are words rather than codes — `TY2607 Cedant Book - General Liability` is 38 characters with a source name of only 18, and real broker-supplied portfolio names run longer than that. Truncating the source rather than the value keeps the readable end readable. Determinism is what lets adoption and idempotent re-runs work at all.

**Alternatives considered.** (a) Keep the composed name and **refuse** breakouts whose longest name exceeds 40 characters — rejected: it would refuse an ordinary LOB breakout of a normally-named portfolio outright, leaving the analyst no way to proceed. (b) A short generated prefix instead of the source name (`ZZFX_LOB_…`) — rejected: unreadable in the Risk Modeler portfolio list, which is exactly where the generated set has to be recognizable. (c) Failing a value too long to name — rejected once adoption moved to the number: truncating the value costs nothing, since the full value lives in `breakout_value` and the `description`, and two truncated-alike values are separated by the collision suffix. (d) Leaving `portfolio_number` to the library default — no longer possible: the default is the name, capped at 20 against the name's 40, and `create_portfolio` now raises instead of truncating (W-13). Every composed name of interest is over 20 characters; `usfl_commercial - TX` is exactly 20, so the very next character breaks it. (e) The EDM `TY{yy}{mm}_…` convention — rejected: that names *databases*. (f) User-editable names in the preview — rejected: one-click semantics; the custom builder can offer that later.

---

## R5 — Value enumeration & gate composition

**Decision.** Enumeration reads the **stored** spec-004 summary only: `irp_portfolio.exposure_detail → summary.breakout_values[dimension]` (defensive parse, exactly like existing readers — R11 defines the shape). Gate rule (one testable function in `breakout_service`, FR-002/003): *EDM exists ∧ not deleted ∧ status `ready` ∧ source portfolio exists ∧ not deleted ∧ summary carries `breakout_values` ∧ (per dimension) ≥ 2 distinct values*. A summary without `breakout_values` at all — every summary backfilled before this iteration — is treated as absent: disabled-with-reason pointing at the existing per-EDM **Sync** (R11 explains why this is the safe reading rather than a fallback). Fewer than 2 values → that dimension disabled ("only one value present").

The confirm POST re-checks the gate server-side (409 + re-rendered fragment on failure — the `packages.py` precedent) **and verifies summary freshness** (FR-002a): the source portfolio's current RM `stampDate` — read at submit time via `search_portfolios` (Article 2's name-resolution pattern; the flow's one web-layer RM call) — must equal the stamp the `backfill_edm_detail` worker captured alongside the summary. Stamp-to-stamp equality, no wall-clock comparison; capture happens before the DataBridge read so the stamp is conservative. Mismatch or missing stamp → refusal pointing at Sync, **no `rwb_job` row**. `stampDate` was validated 2026-07-30 as an updated-at equivalent (updating underlying portfolio data changes it); RM exposes no explicit updated-at.

**Rationale.** Article 11 forbids request-path DataBridge/RM reads; the summary is already the page's source of truth for exactly these values, and its `as_of`/Sync trust model is shipped. The gate must live in one place with unit tests (Article 12 names it a must-test) — there is no central gate module yet, so `breakout_service` is that place for this op.

**Alternatives considered.** (a) Live DataBridge read at modal-open — constitution violation (worker-side only). (b) Re-enumerating inside the worker — rejected, and now structurally impossible: the worker executes the persisted plan (R10). (c) Reading the pre-change `states` key when `breakout_values` is missing — rejected: those values are a mixed vocabulary of names and codes (R11), so filtering one as a code selects nothing and reports success.

---

## R6 — State values are `Admin1Code`; `Admin1Name` is a separate attribute carried as a label

*(Resolved 2026-08-03. This entry previously deferred the vocabulary question to the spike.)*

**Decision.** `Admin1Code` is the selection filter value, the stored `breakout_value`, the token in the generated name and number, and the value the analyst sees. `Admin1Name` travels alongside as a **nullable display label** and is **never synthesized from the code**.

`sql/databridge/portfolio_states.sql` changes accordingly: it returns `Admin1Code` as the value and `MAX(Admin1Name)` as the label, groups by the code, and tests the **code** in the `WHERE`. The `COALESCE(NULLIF(Admin1Name,''), Admin1Code)` expression is removed from both places it appears.

**Rationale — four findings, each independently sufficient.**

1. **`admin1Name` returns zero rows until GeoHaz runs** (W-12). Neither MRI source file populates `STATE`, so the name is a geocoding output, not an import field. Filtering on it produces empty sub-portfolios reported as success for any portfolio geocoded after the breakout — reachable through ordinary sequencing, not a broken EDM.
2. **The name and the code are different exposure attributes**, not two renderings of one value (W-18), so neither is derivable from the other. `Admin1Code` is populated on 100% of the 1,052,720 addresses across the three sandbox EDMs and on 100% of the locations reachable from a portfolio; the reverse case — a name with no code, which would make an account unselectable — does not occur. The asymmetry has a cause (code from import, name from geocoding), which is why it should hold beyond the sandbox.
3. **A name-based value breaks re-runs** (W-16). The same portfolio's states read `TX` before geocoding and `TEXAS` after. A re-run's `create_portfolio` would not collide with the earlier name, so it would *succeed*, leaving two Risk Modeler portfolios for one source + dimension + value. The lineage-key skip (FR-011) prevents that only if `breakout_value` holds the code.
4. **The `COALESCE` invents values.** On `night_edm` it returns 196 states where grouping by code returns 195: two addresses carry `NY` with no name, producing a bare `NY` alongside the `NEW YORK` every other New York address produces. A state breakout today would offer the analyst a duplicate New York and create a sub-portfolio nobody wants (W-19). On `usfl_onFS` the codes are numeric (`200` Puerto Rico, `010` St Croix), so the two vocabularies are not even the same shape (W-8).

**Alternatives considered.** (a) Keep the stored value as a display string and map it to a filter token at selection time — rejected: the mapping does not exist in either direction (finding 2), and the display string is not stable (finding 3). (b) Normalize to codes with a static US-state map — rejected: geography is not US-only, RM's vocabulary is the authority, and this is exactly the "pre-defined region constant" the design record warns against. (c) Show the analyst the name and store the code — rejected as needless divergence: the label is absent on un-geocoded portfolios, so the preview would sometimes show codes anyway. The label renders next to the code where it exists.

---

## R7 — Idempotent re-run & adopt-by-number (no rollback)

**Decision.** Per sub-portfolio, the worker: (1) skips if a live `irp_portfolio` row already matches the lineage key (source, dimension, value); (2) otherwise creates, populates, and inserts the row. If `create_portfolio` raises the duplicate-name `IRPValidationError`, the worker resolves the existing portfolio by **`portfolioNumber`** via `search_portfolios` (W-17), **adopts** it (lineage row with that `irp_id`), and re-runs the add step so an adopted-but-empty portfolio — a crash between create and add — is healed rather than left hollow. Re-adding already-member accounts is safe (W-9), so the heal runs unconditionally. If the number search returns more than one portfolio, that sub-portfolio fails with a recorded reason instead of adopting an arbitrary hit.

Re-enqueue for a partially failed breakout goes through `ensure_pending_rwb_job` (the analyst-re-request path that revives terminal jobs). Partial success completes the job as `succeeded` with per-sub-portfolio outcomes (the `_upload_rdm_body` precedent); the job fails outright only when *zero* sub-portfolios succeed. No rollback anywhere: the app never deletes a created portfolio.

**Rationale.** No delete exists (MVP + library), so forward-only reconciliation is the only recovery shape; every convention here already exists in `package_jobs.py`. Adoption resolves on the number rather than the name because the number is the only one of the two that is stable across runs (R4). A pre-check by number is also required rather than exception parsing: `IRPValidationError` covers an over-long name and an over-long number too (W-10), so catching it is not the same as "the name is taken".

**Alternatives considered.** (a) Adopt by name — rejected: the name depends on truncation and collision suffixing, both of which depend on what else exists in the EDM when the name is computed (R4). (b) Recording the outcome as `adopted (verify contents)` instead of healing — no longer needed; the conditional this entry once carried ("if re-adding already-member accounts turns out to be unsafe") is retired by W-9. (c) Failing the whole job on the first error — rejected: retries everything and contradicts the loop-precedent semantics. (d) Deleting app rows whose populate failed — rejected: the RM portfolio still exists; the row plus its outcome is the honest state.

---

## R8 — UI mechanics

**Decision.** Entry point: a **Break out** action on each portfolio row (visible when the EDM is `ready`; disabled-with-reason otherwise — the `sync_form`/disabled-button conventions). `GET /edms/{edm_id}/portfolios/{pid}/breakout` returns the modal fragment (Alpine open/close, the `package_modal.html` precedent): dimension choice (LOB / State, each enabled per gate), the preview list (value, label where present, generated name, account count), the count, the computed overlap statement, and the blank-value disclosure. `POST` (CSRF) confirms: gate re-check → freshness check → persist the plan → enqueue → return the EDM body partial; the existing self-poll takes over (breakout-in-flight indicator riding the `live` flag; completion toast/banner from per-sub-portfolio outcomes; failures via the `rwb:toast` + `.form-banner--error` conventions). Lineage display: a badge/sub-line on generated rows ("↳ from *{source}* · State: *TX*"), chained lineage showing the immediate source only. **A rendered HTML preview of the modal (including disabled/empty/partial-failure states) is produced from `docs/ui_previews/_scaffold.html` and approved before wiring** — this is a real new interactive surface (docs/UI_WORKFLOW.md rule 1); the row-badge edits are derivative and need no preview.

**Rationale.** Every mechanism named is already shipped and conventionalized; the feature adds one modal and row decorations, not new UI machinery.

**Alternatives considered.** (a) A dedicated breakout page — rejected: the PRD anchors the action on the per-portfolio table; a page adds a nav node for a modal's worth of content. (b) `hx-confirm` instead of a modal — rejected: the preview *is* the feature's judgment surface (the list plus the overlap statement); a browser confirm cannot carry it.

---

## R9 — PRD/documentation pass (part of this iteration)

**Decision.** Update, in the same branch: PRD §23 O6-1/O6-2 register + §10A.5 open-question callout (record the 2026-07-29 product direction: geography breakout ships; account-bucketed whole-account semantics accepted and disclosed; no toggle awaited), PRD §21 Iteration-4 entry (scope narrowed to the two one-click breakouts; filtered builder + complement/"do the opposite" become fast-follows), and `docs/IRP_INTEGRATION_FOLLOWUPS.md` (the shipped library methods, the Platform-endpoints-only direction, and the three dead ends worth not re-researching: `allowDeepFilters`, the `filtered-accounts` PUT, and `lobId` filtering). FUNCTIONAL_REQUIREMENTS §3 gets a pointer note, not a rewrite.

**Rationale.** The constitution's source-of-truth doctrine: specs and plans must stay consistent with PRD/DATA_MODEL; leaving the "blocked" notes standing would contradict this spec.

**Alternatives considered.** Deferring the doc pass to a later cleanup — rejected: spec 004's practice (and the O6 register's visibility) makes stale blockers actively misleading to the next iteration's planner.

---

## R10 — The confirmed plan is persisted, not recomputed

*(New 2026-08-03. The pre-probe plan had the worker recompute the plan from the stored summary and justified it as "identical inputs reproduce the confirmed plan". That justification is false.)*

**Decision.** The confirm POST writes the approved plan into `rwb_job.input_data` — one entry per sub-portfolio carrying the breakout value, the display label, the generated name, and the generated number — and the worker **executes that list**. It does not re-enumerate the summary and does not recompute names.

The worker still resolves account ids at execution time (they are not part of what the analyst approved, and a stale id list would be worse than a fresh one), and it verifies the populated portfolio against the plan's account-id list rather than trusting `manage_portfolio_accounts`' response counts — `completed` counts ids *newly added*, so a healthy re-run reports `completed 0` and must not be read as a failure (W-9).

**Rationale.** AGENTS.md rule 8 (approved plans are immutable — a project rule, not a constitution article): when an async operation follows a user confirmation, the worker executes the plan the user approved. Recomputation is only equivalent if every input is stable, and one is not — **collision suffixing reads the existing portfolio names in the EDM**, which are not part of the stored summary and which the run itself changes. A re-run after a partial failure recomputes against names that now include the sub-portfolios the first run created, so the recomputed name for a value can differ from the approved one. Persisting the plan also makes the audit record exact: `input_data` is what was approved, `output_data` is what happened to each entry.

**Alternatives considered.** (a) Recompute from the summary plus current names — rejected above; this is the rule-8 violation. (b) Persist only the values and recompute names — rejected: the names are the part that is unstable. (c) Persist the account ids too — rejected: the selection is not what the analyst approved, ids can legitimately change between confirm and run, and the freshness check (R5) already refuses a confirm made against drifted data.

---

## R11 — The stored summary gains `breakout_values` and `account_total`

*(New 2026-08-03. Carries P-12's migration consequence and P-13's overlap arithmetic.)*

**Decision.** `backfill_edm_detail` writes two new keys per portfolio into the spec-004 summary, and the state list becomes codes:

```jsonc
{
  "portfolio_name": "usfl_commercial",
  "total_tiv": 30437380495.0,
  "states": ["CA", "FL", "TX"],            // now Admin1Code (was COALESCE(name, code))
  "lines_of_business": ["FLD Comm"],       // unchanged
  "currencies": ["USD"],
  "account_total": 1701,                   // NEW — the overlap denominator
  "breakout_values": {                     // NEW — the enumeration source
    "state": [{"value": "TX", "label": "TEXAS", "accounts": 220}],
    "lob":   [{"value": "FLD Comm", "label": null, "accounts": 25}]
  }
}
```

`breakout_values` is keyed by `breakout_dimension_kind.code`, so the gate, the preview, and the worker index it by dimension with no per-dimension branch. `label` is `Admin1Name` where the EDM has it and null otherwise (R6); for LOB the value is its own label and the key is null.

**Its absence is the staleness signal.** A summary backfilled before this change has no `breakout_values`, and its `states` list holds the mixed name/code vocabulary the `COALESCE` produced (R6 finding 4). Those values cannot be reinterpreted as codes, so the gate treats a missing `breakout_values` as a missing summary and points at Sync — P-04's existing behaviour, reached one step earlier. No migration or backfill of old snapshots is needed, and no summary is ever read as though it were something it is not.

The displayed `states` list switches to codes as a consequence of P-12, in `portfolio_row.html` and the EDM aggregate strip. Pre-change snapshots keep showing names until the next Sync; both render.

**Cost:** +1.44 seconds on `backfill_edm_detail` for the largest book in the sandbox (248,732 accounts, 780,273 addresses), measured warm on repeat runs (W-19). The request path is untouched — the preview reads the stored summary exactly as the page does today, with more keys in the JSON.

**Rationale.** The preview has to state the overlap **for the portfolio being broken out**: the same state breakout produces 1.0× inflation on a book with no multi-state accounts and 6.6× TIV inflation on `usfl_edm_small` portfolio 1 (W-4). A fixed warning is wrong in both directions — it cries wolf on the first and understates the second. The counts are the cheapest honest measure, and the arithmetic is `Σ accounts over values` versus `account_total`.

**Alternatives considered.** (a) Overwrite `states` with codes and use no new key — rejected: pre-change and post-change summaries would be indistinguishable, which is precisely the case that filters a name as a code and creates nothing. (b) Per-value **TIV** as well, so the preview quantifies exposure inflation rather than account inflation — deferred: it needs `exposure_metrics` joined into the counted query, which is untimed, and account overlap is the honest number available now. The preview therefore states account overlap and says plainly that exposure inflation can exceed it, since the accounts that appear in several sub-portfolios tend to be the largest (W-4: 1.27× account inflation alongside 6.6× TIV inflation on the same portfolio). (c) Computing the counts on the request path from the stored value lists — impossible: a DISTINCT list carries no multiplicity. (d) A live DataBridge read at preview time — constitution violation (Article 11).

---

## Clarifications

Q&A history behind the spec's Open product decisions table. The spec carries the decision rows (P-nn); this section carries the exchanges.

### Session 2026-07-29

- **Q: PRD §10A.5 / the Iteration-4 build-plan entry mark the geography breakout as blocked on the commercial-policy geographic-split open question (O6-1/O6-2). Does it ship here?** → **A: Yes — the geography breakout is in scope by product direction (this session).** The scheduling block is lifted. The behavioral substance of O6-1 is settled by the documented account-bucketing semantics: Risk Modeler assigns **whole accounts** to matching sub-portfolios ("keep all", at account grain — there is no keep-only-matching-locations mode in the native filter, and no toggle is built or awaited). The consequence — a multi-state account lands in full in every state sub-portfolio it touches — is **disclosed in the breakout preview**, not silently accepted. The complement split, where double-counting bites hardest, is out of this spec regardless. The PRD's O6-1/O6-2 register and §10A.5 blocked-note are updated to record this direction as part of this iteration's documentation pass (R9). → spec **P-02**
- **Q: The full §10A feature set includes the custom filtered sub-portfolio builder, complement splits, and "do the opposite". Are they in?** → **A: No — one-click LOB and geography breakouts only (product direction, this session).** The filtered builder, complement split, and "do the opposite" are follow-on features; what this spec builds (account selection + sub-portfolio creation, value enumeration, lineage, naming, gate) is what they will reuse. → spec **P-01**
- **Q: What grain does the geography breakout use — state, country, or both?** → **A: State only this iteration.** The granularity cap is state/country (§10A.2, O6-3), but the only geography enumeration source that exists is the per-portfolio **states** read from the Iteration-3 exposure summary (`countries` was explicitly dropped — "no country-level read; geography = states", IRP_INTEGRATION_FOLLOWUPS §6c). A by-country breakout follows when a country-level enumeration read exists; it is the same action shape with a different attribute. → spec **P-03**
- **Q: Where do the breakout's pick-list values come from, given DataBridge reads are worker-side only (constitution Art. 11)?** → **A: From the stored per-portfolio exposure summary backfilled by Iteration 3.** No live DataBridge or RM read runs on the request path to enumerate values. Consequence: a portfolio whose summary has not been backfilled cannot be broken out — the action is disabled with a reason and the analyst is pointed at the existing per-EDM **Sync** action (the recovery path spec 004 built). Staleness follows the same trust model as the rest of the detail page (`as_of` signal + manual Sync). → spec **P-04**
- **Q: Is a breakout blocked when the source has only one distinct value?** → **A: Yes — nothing to break out.** A one-value breakout would produce a single sub-portfolio identical to the source (account bucketing makes it a full copy). The action is available but the dimension is offered disabled-with-reason when the stored summary shows fewer than two distinct values. → spec **P-05**

### Session 2026-07-30

- **Q: FR-008 said a zero-match sub-portfolio fails and creates nothing, but an edge-case note said a drifted value "produces an empty-but-valid portfolio in RM" — which is it, and how is summary staleness handled?** → **A: Fail that sub-portfolio, create nothing (FR-008 stands; the edge-case note was corrected) — and staleness is blocked up front by a freshness check at confirm.** Risk Modeler's `stampDate` (returned by Search Portfolios; validated 2026-07-30 as an updated-at equivalent) is captured alongside the stored summary at backfill; the confirm POST re-reads it via `search_portfolios` and refuses the breakout when it differs from the captured stamp or no stamp is stored — 409 with "Sync the EDM, then retry", and **no `rwb_job` row is created**. Stamp-to-stamp equality only, never a wall-clock comparison. No worker-side stamp re-check (the confirm-to-run window is seconds): the zero-match per-sub-portfolio failure remains the run-time backstop — it also catches a selection-token regression, which the freshness check cannot. Per-sub-portfolio failures are reported as a completion toast plus a persistent per-row error line rendered from the latest terminal breakout job's outcomes — it survives refresh, needs no dismissal state, and is removed by being superseded by the next terminal run for that portfolio + dimension. → spec **P-06** (behavior in FR-002a / FR-008 / FR-012)

### Session 2026-07-31

- **Q: The spec never states what a generated sub-portfolio is actually named — align on the name and make it explicit.** → **A: The R4 pattern is promoted into the spec as P-10:** name = `{source portfolio name} - {value}`, trimmed, with the lowest free ` (2)`, ` (3)`… suffix on collision; names are a pure function of (source name, values, existing names) so preview and worker agree. *(The character cap and the `portfolio_number` half of this decision were both wrong — superseded by P-11, session 2026-08-03.)* → spec **P-10**
- **Q: The geography breakout must accommodate global portfolios — the wording should be explicit that the grain is "state or state-equivalent".** → **A: Wording amended throughout the spec (P-03): the geography dimension is state or state-equivalent — the exposure's first-level administrative division.** For non-US exposure the same Moody's attribute applies (a Canadian province or German Land occupies the same field as a US state), so a global portfolio breaks out through the same action with no separate mode. This is a wording clarification, not a behavior change. → spec **P-03** (wording in FR-004, FR-005, user story 2)

### Session 2026-08-03

The probe run closed the T-08 spike. Three decisions follow from it; the evidence for each is in [probe-findings.md](probe-findings.md) Part 5.

- **Q: Risk Modeler caps portfolio names at 40 characters, not 200, and `portfolio_number` is a separate 20-character identifier that no longer defaults safely. What gets truncated?** → **A: The source portfolio name gets truncated; the breakout value is kept whole; the full source name goes in the Risk Modeler `description`. `portfolio_number` is composed separately from the source portfolio's RM id, the dimension, and the value — and adoption resolves on the number, not the name.** The number is the only identifier stable across runs, because the name depends on collision suffixing against whatever else exists in the EDM at the moment it is computed. Format and budgets in R4. → spec **P-11** (supersedes the cap and `portfolio_number` halves of P-10)
- **Q: The state summary stores `COALESCE(Admin1Name, Admin1Code)`. Which value does the breakout use?** → **A: The code, everywhere — filter value, stored `breakout_value`, name and number token, and what the analyst sees.** `Admin1Name` and `Admin1Code` are different exposure attributes, neither derivable from the other; the name is written by geocoding and is absent until GeoHaz runs, so filtering on it silently creates empty sub-portfolios. `Admin1Name` is carried as a nullable display label and never synthesized. Because existing summaries hold a mixed vocabulary of names and codes, the codes are written under a new `breakout_values` key whose absence marks a summary as stale — the gate then refuses and points at Sync (P-04's behaviour). Four supporting findings in R6; the summary shape in R11. → spec **P-12**
- **Q: Overlap ranges from 1.0× to 6.6× depending on the source portfolio. Can the preview state the real number instead of a fixed warning, and what does that cost?** → **A: Yes — both summary queries gain a per-value account count and a portfolio account total, measured at +1.44 seconds on the `backfill_edm_detail` worker job for the largest book in the sandbox, with no change to the request path.** The preview states account overlap for the portfolio being broken out, and says plainly that exposure inflation can exceed it. Per-value TIV is deferred (R11 alternative b). → spec **P-13**

### Session 2026-08-04

- **Q: The confirm POST carries only `dimension` and `csrf_token`, so `request_breakout` composes the plan a second time rather than receiving the one the modal displayed. Collision suffixing reads every portfolio name in the EDM, so a portfolio created between the preview and the confirm can change a suffix. Is the recomposition acceptable, or must the displayed plan travel with the confirm?** → **A: Recompose at confirm and accept the window.** The previewed name is indicative; the generated `portfolio_number` is the identity, and it is composed only from the source portfolio's RM id, the dimension, and the breakout value — none of which the window can change (P-11/R4). So adoption and idempotent re-run are unaffected by a differing suffix, and the analyst-visible content of the preview that carries judgment — the set of values, the account counts, the overlap statement — reads the source portfolio's stored summary and cannot change either. Echoing the plan through the form was rejected: it puts generated names in analyst-controllable input and buys nothing the number does not already give. A short-lived draft record was rejected as a lifecycle to build and clean up for the same result. AGENTS.md rule 8 is satisfied at the point the plan is persisted — from there the worker executes it verbatim (R10). → spec **P-14** (behavior in FR-006b)
- **Q: Nothing bounds the fan-out. A global portfolio's state breakout can run to several hundred values, and under Article 10 the single `rwb_job` worker runs it, so EDM imports and backfills queued behind it wait. Is there a cap?** → **A: No cap; above a named threshold of 25 sub-portfolios the preview states that the run takes several minutes and holds the background job queue.** A hard cap was rejected: it contradicts P-03's global portfolios and leaves the analyst no in-app path on a book that legitimately has 200 administrative divisions. Saying nothing was rejected too — the queue-occupancy consequence is real and the analyst confirming the run is the only person positioned to weigh it against the EDM import waiting behind it. The threshold sits above SC-003's typical breakout (≤ 15) so ordinary LOB runs stay quiet, and below a full US state breakout (~50), which is the common case where the wait is worth stating. It is one constant, tunable without a spec change. → spec **P-15** (behavior in FR-006c)
- **Q: The gate checks for an in-flight `run_breakout_*` job but not for an in-flight `backfill_edm_detail`, and Sync is what rewrites the summary the preview and the recomposed plan both read. A Sync landing in the preview→confirm window changes the value set and the account counts under the analyst. Is that covered?** → **A: No, and both halves are now required — the gate disables the action while a detail refresh for the EDM is pending or running, and the confirm refuses when the stored summary's `as_of` no longer matches the one the preview carried.** The FR-002a `stampDate` check does not catch it: a re-backfill that leaves the Risk Modeler portfolio untouched writes back an equal stamp, so the check passes on a summary that changed. The gate condition is the cheap half and matches what `edm_service.sync_detail` already does to itself (skip while a backfill head is pending or running); the `as_of` comparison is the half that actually closes the window, because a refresh can start and finish entirely after the preview renders. Relying on the stamp check alone was rejected for that reason, and the gate alone was rejected because it narrows the window without closing it. Together they make FR-006b's invariant true: everything the analyst judged from is held constant, and only the collision suffix can move. → spec **P-16** (behavior in FR-002 / FR-002b)
- **Q: P-08 (audit uses existing conventions, no audit-log table) is the one decision still standing at Assumed. Confirm or build the table?** → **A: Confirmed — no audit-log table.** Every field FR-015 names already has a home: the actor in the breakout job's `input_data.actor_id` and the business-event log line, the timestamp on the job row, the source portfolio in `requestor_id`, the dimension in `rwb_job_type`, and the per-sub-portfolio outcomes in `output_data.sub_portfolios`; generated portfolios also carry the confirming analyst in `inserted_by`. Nothing prunes or ages out `rwb_job` rows, so the record is durable without a second copy. A dedicated table was rejected under Article 1 — it would duplicate `output_data` and add a write path for data nothing queries as an entity, the same reasoning that rejected a first-class breakout table in R3. → spec **P-08** (Assumed → Approved; FR-015 now names the five sources)

### Carried from the design record

Standing decisions restated for this feature, not new ones:

- **Breakouts loop the single create call app-side** — one create per sub-portfolio, so each outcome is captured individually and one failure doesn't orphan the rest (sequence diagrams README: "Multi-item composites loop the single IRP endpoint app-side").
- **Regions are not pre-defined constants** (§10A.2). Irrelevant to this spec's two actions (each sub-portfolio is a single value), but it is why no "Northeast" preset appears anywhere.
- **Portfolio deletion is out of MVP** (§10A.7) and the library has no delete method — a breakout has **no rollback**; partial failure is handled by idempotent re-run, not by deleting created portfolios (R7). → spec **P-07**
- **No job/batch rows for synchronous ops** (PRD §14.3): both the create and the add are synchronous (probe-confirmed — no 202 and no workflow URL appeared anywhere), so generated portfolios persist with no `irp_job` rows and no poller involvement.
- **Audit uses the app's existing conventions** — structured business-event logging with the actor id (pinned by `test_business_event_logs.py`), per-row provenance (`inserted_by`/`updated_by`), and the breakout job row's `input_data`/`output_data` (R2, R10). There is no audit-log table in this app and this spec does not introduce one. → spec **P-08**
- **Why the overlap is accepted at all** — the design record (Cheryl's confirmation of Risk Modeler's behaviour, the treaty-structure driver, the "sum to 100%" preference it collides with) is in [probe-findings.md](probe-findings.md) Part 4, the only copy of that material.
