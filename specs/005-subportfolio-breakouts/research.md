# Phase 0 Research: One-Click Portfolio Breakouts (Iteration 4)

**Spec**: [spec.md](spec.md) | **Date**: 2026-07-29 | **Revised**: 2026-08-03

The spec carries no `[NEEDS CLARIFICATION]` markers. This file records the concrete decisions planning owns, each as *Decision / Rationale / Alternatives considered*.

Everything R1 once deferred to a sandbox spike is closed. The evidence is in
[probe-findings.md](probe-findings.md) — the live probe run of 2026-08-03, every
API count cross-checked against Data Bridge. This file cites its findings as
`W-nn`; it does not restate them.

---

## R1 — Composing a sub-portfolio: **select account ids → create → add by id**

*(Rewritten 2026-08-03 against the probe run; the pre-probe version built on `add_filtered_accounts` and `allowDeepFilters`, both lost — see Alternatives. Selection revised 2026-08-05: the REST selection failed on the first real-scale run, W-20 — see Alternatives (h).)*

**Decision.** Risk Modeler has **no create-by-filter endpoint** (the create body has no filter fields, and no such Platform operation is documented). A sub-portfolio is composed from three calls, mirroring what the Risk Modeler UI itself does:

1. **Select** — the source portfolio's account ids matching the breakout value.
2. **Create** — `create_portfolio()` → HTTP 201, synchronous.
3. **Add** — `manage_portfolio_accounts(accounts_to_add=[…])` → PATCH, HTTP 200, synchronous, returns `{"addAccounts": {"completed": n, "total": m}}`.

**The selection read is one parameterized DataBridge query per run** (`sql/databridge/breakout_lob_accounts.sql`; the state script arrives with T045), executed worker-side through the wheel's DataBridge executor — the same path the exposure summary uses. The script takes `{{ portfolio_id }}` (RM's portfolioId **is** `portacct.PORTINFOID`) and returns `(Value, AccountId)` pairs; `ACCGRPID` is the id `manage_portfolio_accounts` accepts as `accountId` (confirmed 2026-08-05). Two properties fall out of using the summary script's own joins:

- the selection vocabulary is **byte-identical** to the stored summary the analyst approved from — no REST-vs-DataBridge spelling drift can select zero rows against a fresh summary;
- the one-matching-policy-admits-the-whole-account bucketing (W-3/W-11) holds by construction (`DISTINCT ACCGRPID` over the policy join).

The read is all-or-nothing: any DataBridge failure raises and the worker fails the job with nothing created — the W-14 rule (never proceed on a result that cannot be shown complete), enforced by a single set-based query instead of pagination proofs. The composition **read-back** is the same mechanism (`portfolio_member_count.sql`): one scalar count of the new portfolio's members, because the paginated REST enumeration cannot verify a portfolio past 100,000 accounts (W-20). A count that does not equal the ids sent raises: FR-008 asks for exactly the selected accounts, so an under- or over-populated sub-portfolio fails and gets no lineage row. Its Risk Modeler portfolio stays (P-07 deletes nothing) and the re-run adopts it on its number and re-adds, which heals a partial add.

**What the library provides** (verified against `portfolio.py` / `utils.py` at `a04e3d7`, not against prose — [contracts/irp-library.md](contracts/irp-library.md) is the full contract):

| Method | What this feature depends on |
|---|---|
| `databridge.execute_query_from_file` | the selection read and the read-back count (`{{ param }}` substitution, injection-safe) |
| `create_portfolio` | synchronous 201; raises `IRPValidationError` on a duplicate name, a name over 40 characters, or a number over 20 |
| `manage_portfolio_accounts` | synchronous 200; `completed`/`total`; idempotent (W-9) |
| `search_portfolios` | the confirm-time `stampDate` read (R5) and adopt-by-number (R7) |
| `edm.search_edms` | the EDM's physical `databaseName` for the DataBridge connection (cached per exposure) |

Two things the library deliberately leaves to this repo: it **does not shorten names** (both name fields raise instead of truncating), and `allow_deep_filters` is gone from the public signature so the deep-filter path cannot be taken by accident.

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

**Rationale.** Both writes are documented, synchronous Platform operations (200/201 the only success statuses; any other 2xx raises), validated end to end against two real state breakouts and a purpose-built multi-LOB book with every count cross-checked in Data Bridge (W-1, W-11). The selection runs where the same data already lives as SQL: the summary scripts execute against `night_edm` (248,732 accounts) in ~1–2 seconds (W-19), where the REST selection could not complete at all (W-20). Under-selection stays the failure to guard: a short selection produces a sub-portfolio missing accounts and reports success — a set-based query cannot return a partial page sequence, and the read-back count compares against the persisted plan (R10) rather than trusting response counts.

**Alternatives considered.** (a) `add_filtered_accounts` (PUT `.../filtered-accounts`) as the add step — **rejected**: it returns `{}`, so the worker cannot tell an empty populate from a full one without a read-back, while the PATCH reports `completed`/`total`. Worse, `manageExistingAccounts=true` returns HTTP 200 and adds nothing at all, so it is a mode switch, not the heal-a-partial-write option it reads as (W-1). (b) `getAccounts` with `allowDeepFilters=true` as the state selection — **rejected**: zero rows with HTTP 200 at all nine scope sizes tested where Data Bridge and `searchLocations` both say 272; scope size, filter length, and vocabulary were all ruled out as causes (W-7). (c) One-shot `create_portfolio_by_filter` — does not exist. (d) `queryFilter` one-call populate — **closed permanently**, not deferred (U3 above); this retires T-09. (e) Filtering policies by `lobId` instead of grouping client-side — rejected: HTTP 500, not a clean 400 (W-15). (f) Client-side bucketing via per-account child reads — rejected: N+1 per sub-portfolio. (g) Raw `client.request()` from the app — rejected: Moody's schema knowledge belongs in the integration library (Article 11). (h) **REST selection** (the 2026-08-03 decision: `search_accounts_by_portfolio_paginated` for the source ids, then a chunked `accountId IN (…)` policy/location scan) — **rejected 2026-08-05 after failing at the US1 checkpoint**: the wheel's account search refuses past 100,000 records because completeness can no longer be proven (W-20 — the portfolio holds 248,732 accounts), and even absent the ceiling the chunked scan is ~670 URL-filtered, paginated round trips per breakout. The probe validated the sequence only on books of a few hundred accounts. The W-6 no-portfolio-predicate finding and the W-14 completeness rule shaped that design; both are moot for a portfolio-scoped SQL query.

---

## R2 — Execution locus: the fan-out runs as a `run_breakout_*` `rwb_job` worker

**Decision.** The confirm POST validates the gate and the summary-freshness check (R5), persists the approved plan and idempotently enqueues one `run_breakout_lob` / `run_breakout_state` `rwb_job` (R10), and returns immediately; the Dramatiq worker executes the loop (per sub-portfolio: select account ids → create → add by id → upsert row), records per-sub-portfolio outcomes in `output_data`, and on completion idempotently enqueues `backfill_edm_detail`. The EDM page's existing 3-second self-poll (`live` flag) surfaces sub-portfolios as they land and the outcome banner at the end.

**Rationale.** (a) A 40-state fan-out is 40 create+add pairs plus one chunked selection read per state; on the request path that holds an HTTP request for minutes. (b) Every existing multi-call IRP loop lives in the worker tier with per-item failure isolation — four precedents in `package_jobs.py`. (c) `rwb_job` gives the loop atomic claim, heartbeat/reconciler recovery for a wedged run, idempotent enqueue (`UNIQUE(requestor_type, requestor_id, rwb_job_type)`), and `input_data`/`output_data` as the durable audit record. (d) Article 11 *permits* request-path submission but does not require it. (e) The UI needs no new machinery — the self-poll and toast conventions already exist.

**Alternatives considered.** (a) Request-path loop (the PRD §10A.5 sketch) — rejected: indefensible at 40 sub-portfolios, and no recovery if uvicorn dies mid-loop. (b) One `rwb_job` per sub-portfolio — rejected: explodes queue rows and loses the single per-breakout outcome record. (c) SSE progress — rejected: the 3-second self-poll already exists and is the shipped pattern (spec 004).

---

## R3 — Lineage schema: three columns on `irp_portfolio` + a kind table; no first-class breakout table

**Decision.** `irp_portfolio` gains `source_portfolio_id` (Uuid NULL, self-FK), `breakout_dimension_code` (NVARCHAR NULL, FK → `breakout_dimension_kind.code`), `breakout_value` (NVARCHAR(256) NULL) — all NULL for broker-arrived portfolios. New kind table `breakout_dimension_kind` (`code` PK, `label`, `sort_order`; seeds `lob`, `state`). Filtered unique index `UNIQUE(source_portfolio_id, breakout_dimension_code, breakout_value) WHERE source_portfolio_id IS NOT NULL AND deleted_at IS NULL` — the idempotency key. Breakout-created rows populate `inserted_by` (first writer to do so; the worker receives the actor id in `input_data`).

`breakout_value` stores the value the **filter** uses: `Admin1Code` for state (R6), `LOBNAME` for LOB. It is not a display string — see R6 for why the state name cannot serve as the identity.

The three columns move together and never move again: `portfolio_service` stamps lineage on a row that has none (a backfill enumerated the Risk Modeler portfolio before the breakout recorded it) and refuses a row that already carries a **different** lineage. Reassigning one would take the generated portfolio out of its own source's traceability and show it under another value, so the write fails and the worker records that sub-portfolio as failed.

**Rationale.** Lineage is provenance the display, idempotency, and audit all need; three nullable columns keep the entity thin (DATA_MODEL §5 style). The dimension is an app-defined closed set the code dispatches on → kind table (Article 3 default); the value is external exposure vocabulary stored verbatim → plain column (the spec-004 snapshot rationale). The filtered unique index makes "detect already-created sub-portfolios" a constraint, not a convention.

**Alternatives considered.** (a) A first-class `breakout` table (header + child rows) — rejected: it would duplicate what `rwb_job.input_data`/`output_data` + the lineage columns already record, and nothing queries a breakout as an entity (Article 1). Revisit only if breakout history/re-run UX becomes a feature. (b) A JSON `breakout_filter` snapshot column — rejected: the two directed breakouts are fully described by dimension + value; a filter-spec blob is the custom-filter-builder's concern (out of scope). *(Superseded 2026-08-09 for custom grouping: the filter builder became a product requirement and its member-filter JSON lives on the new `breakout_group` table — see the session entry and T-12. The R3 triple itself is unchanged.)* (c) Recording lineage only in the name — rejected: names are truncated and collision-suffixed (R4), so parsing them is fragile.

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

Token = the breakout value verbatim when it is already uppercase alphanumerics that fit the remaining budget. Otherwise the last 6 characters of the token are 6 hex digits of `sha256(value)`. Both normalizing steps are lossy — dropping non-alphanumerics and uppercasing map `AB`, `A-B`, `a b`, and ` AB` onto one token, and truncation merges two long LOB names sharing a prefix — so any value the token cannot carry verbatim is hashed rather than truncated into a neighbour's number. Distinct values must keep distinct numbers: the number is what adoption resolves on (R7), and two sub-portfolios sharing one make every later adoption ambiguous, which fails both of them (FR-011). Never Python's `hash()` — it is salted per process.

Collision suffixing compares **casefolded** names, because Risk Modeler rejects a duplicate name without distinguishing case: an existing `SOURCE - TX` has to push a planned `source - TX` to ` (2)` or the create fails on a name the analyst already approved.

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

## R11 — The stored summary gains `breakout_values`, `account_total`, and `breakout_coverage`

*(New 2026-08-03. Revised 2026-08-05: the overlap figures are measured per account, not derived from the per-value counts. Carries P-12's migration consequence and P-13's overlap statement.)*

**Decision.** `backfill_edm_detail` writes three new keys per portfolio into the spec-004 summary, and the state list becomes codes:

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
  },
  "breakout_coverage": {                   // NEW (2026-08-05) — the measured overlap
    "state": {"covered": 1624, "multi_value": 252},
    "lob":   {"covered": 1690, "multi_value": 311}
  }
}
```

`breakout_values` is keyed by `breakout_dimension_kind.code`, so the gate, the preview, and the worker index it by dimension with no per-dimension branch. `label` is `Admin1Name` where the EDM has it and null otherwise (R6); for LOB the value is its own label and the key is null.

`breakout_coverage` is keyed the same way and read the same way. Its absence is **not** a staleness signal — a summary written between 2026-08-03 and the revision has `breakout_values` but no coverage, and is perfectly usable for a breakout; the preview simply falls back to the qualitative disclosure, as it already does when `account_total` is missing. A Sync fills it in.

**The absence of `breakout_values` is the staleness signal.** A summary backfilled before this change has no `breakout_values`, and its `states` list holds the mixed name/code vocabulary the `COALESCE` produced (R6 finding 4). Those values cannot be reinterpreted as codes, so the gate treats a missing `breakout_values` as a missing summary and points at Sync — P-04's existing behaviour, reached one step earlier. No migration or backfill of old snapshots is needed, and no summary is ever read as though it were something it is not.

The displayed `states` list switches to codes as a consequence of P-12, in `portfolio_row.html` and the EDM aggregate strip. Pre-change snapshots keep showing names until the next Sync; both render.

**Cost:** +1.44 seconds on `backfill_edm_detail` for the largest book in the sandbox (248,732 accounts, 780,273 addresses), measured warm on repeat runs (W-19). The request path is untouched — the preview reads the stored summary exactly as the page does today, with more keys in the JSON.

**Rationale.** The preview has to state the overlap **for the portfolio being broken out**: the same state breakout produces 1.0× inflation on a book with no multi-state accounts and 6.6× TIV inflation on `usfl_edm_small` portfolio 1 (W-4). A fixed warning is wrong in both directions — it cries wolf on the first and understates the second. Account counts are the cheapest honest measure.

**The arithmetic this decision originally carried was wrong, and is replaced (2026-08-05).** It read `repeats = Σ accounts over the dimension's values − account_total`, floored at zero, with `partition = (repeats == 0)`. Two independent errors:

1. `Σ accounts` counts **memberships**, not accounts. An account carrying three states adds 2 to the difference but is one repeating account, so the figure overstates the number the disclosure names.
2. `account_total` counts **every** account in the portfolio, while the per-value counts only see accounts that carry a value — `portfolio_states.sql` inner-joins Property and Address and filters `Admin1Code IS NOT NULL`, and `portfolio_lines_of_business.sql` needs a policy with a non-blank `LOBNAME`. Accounts carrying no value at all are in the denominator and in no numerator.

The two errors cancel. A portfolio of 1,701 accounts where 100 carry a state yields `repeats = 0`, and the preview stated "None of this portfolio's 1,701 accounts match more than one state — the sub-portfolios partition the source cleanly" while 1,601 accounts landed in no sub-portfolio. Fifty uncovered accounts alongside fifty excess memberships reported the same clean partition. The unit test `test_overlap_never_negative` asserted the first case as correct, reading the shortfall as a stale count.

Both figures are now measured per account by `portfolio_state_coverage.sql` / `portfolio_lob_coverage.sql`, which repeat their summary script's joins and filter and group by account: `multi_value` accounts land in more than one sub-portfolio, and `account_total − covered` accounts land in none. `partition` requires both to be zero. FR-007 asks for exactly these two numbers and SC-002 promises the coverage one, so renaming the old field to "excess memberships" would have satisfied neither.

**Alternatives considered.** (a) Overwrite `states` with codes and use no new key — rejected: pre-change and post-change summaries would be indistinguishable, which is precisely the case that filters a name as a code and creates nothing. (b) Per-value **TIV** as well, so the preview quantifies exposure inflation rather than account inflation — deferred: it needs `exposure_metrics` joined into the counted query, which is untimed, and account overlap is the honest number available now. The preview therefore states account overlap and says plainly that exposure inflation can exceed it, since the accounts that appear in several sub-portfolios tend to be the largest (W-4: 1.27× account inflation alongside 6.6× TIV inflation on the same portfolio). (c) Computing the counts on the request path from the stored value lists — impossible: a DISTINCT list carries no multiplicity, which is the same reason the 2026-08-05 revision needs its own queries. (d) A live DataBridge read at preview time — constitution violation (Article 11). (e) Keeping the arithmetic and renaming `repeats` to `excess_memberships` — rejected 2026-08-05: cheaper, but it answers neither FR-007's question nor SC-002's, and the clean-partition claim would have to be dropped rather than corrected. (f) One coverage script emitting both dimensions with a `Dimension` column — rejected: it would put dimension codes in SQL, where `_SELECTION_SCRIPTS` already keeps them in Python.

---

## R14 — Peril values are `loccvg.PERIL` codes, code-only display, grouping-only

*(New 2026-08-10, from the W-21 probe run — the Workstream-3 prerequisite for
custom grouping.)*

**Decision.** The peril dimension enumerates `loccvg.PERIL` through the same
three-script shape as state: `portfolio_perils.sql` (per-value account
counts), `portfolio_peril_coverage.sql` (the two FR-007 counts),
`breakout_peril_accounts.sql` (the selection read), all joining
`portacct → Property (LOCID) → loccvg` — the join path
`portfolio_currencies.sql` already proved. The stored value is the numeric
code **stringified** (`"1"`, `"2"`); `label` is always null and every display
renders the code, because the EDM carries no code→name lookup (W-21) and P-12
forbids synthesizing one. Peril is **grouping-only** (P-19): it gets a noun
and the three scripts, but no `_DIMENSION_LETTER` entry, no
`run_breakout_peril` job type, and no worker actor — `DimensionEligibility`
carries `quick=False` and the quick-mode chooser, `modal_context`'s
dimension selection, and `request_breakout` all exclude it. Sub-peril detail
(fire-following, flood) rides its parent peril's coverage rows (W-21), so
`loccvg` alone is the honest enumeration source.

**Rationale.** D14 asks for peril AND geography / peril AND LOB — the group
model's intersection — and D16 rejected one-per-peril splitting as
combinatorial bloat; D15 leans profile-side for pure-peril filtering. The
numeric code is the only selection vocabulary the EDM holds, and the
name/code split that burned state (R6/W-16) cannot recur when no name exists
at all.

**Alternatives considered.** (a) A hardcoded RMS code→mnemonic map (1=EQ,
2=HU, …) — rejected: exactly the "pre-defined constant" the design record
warns against, and a P-12 violation (a synthesized label that geocoding-style
vocabulary drift would falsify). Revisit only if an authoritative in-EDM or
API lookup appears. (b) Deriving perils from the detail tables
(eqdet/hudet/…) — rejected: sub-peril detail rides parent perils (W-21), so
detail-table presence over-enumerates what a coverage filter can select.
(c) Standalone peril quick mode — deferred pending the D15 team validation
(P-19); the add is the registration lockstep (job-type seed + actor +
letter) on top of what ships here.

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

- **T-02 closed on the published wheel (implement-time note).** PR #21 published to TestPyPI as `irp-integration==0.3.0`; the repo is pinned to it (`irp-testpypi` group, user direction at implement start — supersedes the `make irp-local` instruction in T001, and T060's re-pin is done up front). All six consumed signatures verified identical to [contracts/irp-library.md](contracts/irp-library.md) against the installed 0.3.0 wheel via `inspect.signature` — no drift. `create_portfolio`'s duplicate-name check confirmed client-side (a name lookup before the POST) and raising the same `IRPValidationError` class as the length violations, as W-10 recorded.
- **Q: The confirm POST carries only `dimension` and `csrf_token`, so `request_breakout` composes the plan a second time rather than receiving the one the modal displayed. Collision suffixing reads every portfolio name in the EDM, so a portfolio created between the preview and the confirm can change a suffix. Is the recomposition acceptable, or must the displayed plan travel with the confirm?** → **A: Recompose at confirm and accept the window.** The previewed name is indicative; the generated `portfolio_number` is the identity, and it is composed only from the source portfolio's RM id, the dimension, and the breakout value — none of which the window can change (P-11/R4). So adoption and idempotent re-run are unaffected by a differing suffix, and the analyst-visible content of the preview that carries judgment — the set of values, the account counts, the overlap statement — reads the source portfolio's stored summary and cannot change either. Echoing the plan through the form was rejected: it puts generated names in analyst-controllable input and buys nothing the number does not already give. A short-lived draft record was rejected as a lifecycle to build and clean up for the same result. AGENTS.md rule 8 is satisfied at the point the plan is persisted — from there the worker executes it verbatim (R10). → spec **P-14** (behavior in FR-006b)
- **Q: Nothing bounds the fan-out. A global portfolio's state breakout can run to several hundred values, and under Article 10 the single `rwb_job` worker runs it, so EDM imports and backfills queued behind it wait. Is there a cap?** → **A: No cap; above a named threshold of 25 sub-portfolios the preview states that the run takes several minutes and holds the background job queue.** A hard cap was rejected: it contradicts P-03's global portfolios and leaves the analyst no in-app path on a book that legitimately has 200 administrative divisions. Saying nothing was rejected too — the queue-occupancy consequence is real and the analyst confirming the run is the only person positioned to weigh it against the EDM import waiting behind it. The threshold sits above SC-003's typical breakout (≤ 15) so ordinary LOB runs stay quiet, and below a full US state breakout (~50), which is the common case where the wait is worth stating. It is one constant, tunable without a spec change. → spec **P-15** (behavior in FR-006c)
- **Q: The gate checks for an in-flight `run_breakout_*` job but not for an in-flight `backfill_edm_detail`, and Sync is what rewrites the summary the preview and the recomposed plan both read. A Sync landing in the preview→confirm window changes the value set and the account counts under the analyst. Is that covered?** → **A: No, and both halves are now required — the gate disables the action while a detail refresh for the EDM is pending or running, and the confirm refuses when the stored summary's `as_of` no longer matches the one the preview carried.** The FR-002a `stampDate` check does not catch it: a re-backfill that leaves the Risk Modeler portfolio untouched writes back an equal stamp, so the check passes on a summary that changed. The gate condition is the cheap half and matches what `edm_service.sync_detail` already does to itself (skip while a backfill head is pending or running); the `as_of` comparison is the half that actually closes the window, because a refresh can start and finish entirely after the preview renders. Relying on the stamp check alone was rejected for that reason, and the gate alone was rejected because it narrows the window without closing it. Together they make FR-006b's invariant true: everything the analyst judged from is held constant, and only the collision suffix can move. → spec **P-16** (behavior in FR-002 / FR-002b)
- **P-15 copy amended at UI-preview review (2026-08-04).** The large-fan-out statement now says only that the run takes several minutes. The original copy also explained that the run occupies the background job queue so EDM imports and detail refreshes queued behind it wait; the reviewer cut the explanation — the intent is to reduce the impact rather than disclose it, and in the interim the analyst is not owed the mechanism. The consequence itself is unchanged and stays documented in [contracts/worker-poller.md](contracts/worker-poller.md) (one breakout job holds the single `rwb_job` worker for its duration). → spec **P-15**, **FR-006c**
- **Q: P-08 (audit uses existing conventions, no audit-log table) is the one decision still standing at Assumed. Confirm or build the table?** → **A: Confirmed — no audit-log table.** Every field FR-015 names already has a home: the actor in the breakout job's `input_data.actor_id` and the business-event log line, the timestamp on the job row, the source portfolio in `requestor_id`, the dimension in `rwb_job_type`, and the per-sub-portfolio outcomes in `output_data.sub_portfolios`; generated portfolios also carry the confirming analyst in `inserted_by`. Nothing prunes or ages out `rwb_job` rows, so the record is durable without a second copy. A dedicated table was rejected under Article 1 — it would duplicate `output_data` and add a write path for data nothing queries as an entity, the same reasoning that rejected a first-class breakout table in R3. → spec **P-08** (Assumed → Approved; FR-015 now names the five sources)

### Session 2026-08-05

- **Q: Non-US geography values are numeric `Admin1Code`s, so a Caribbean breakout names its sub-portfolios `cbhu - 200` and the UI shows `010, 020, 030, 200` — should names and display use `Admin1Name` instead?** → **A: Product decision — the name token and every display use the stored display label when the summary carries one, falling back to the code; nothing else changes.** The filter value, the stored `breakout_value`, the `portfolio_number` token, the sort order, and the idempotency keys (FR-011's lineage match, the `uq_irp_portfolio_breakout` index) all stay `Admin1Code` — the label is nullable and non-unique, so it can decorate but never select or dedupe. One rule, no code-shape heuristic, with three consequences accepted: (1) US names change too — `usfl - FL` becomes `usfl - Florida` once geocoding writes the label; (2) a breakout run before geocoding names by code, and a re-run after geocoding does **not** rename it (idempotency matches on the value and skips it); (3) the lineage badge and the geography column resolve the label at read time from the source portfolio's stored summary — nothing new is stored, and a miss (source pruned, value gone from a rewritten summary, no label yet) falls back to the code, which is what rendered before. The Risk Modeler description keeps the raw value and adds the label beside it (`… by Geography (state): 200 (Puerto Rico)`), so both vocabularies stay searchable in Risk Modeler. Storing a label column on `irp_portfolio` was rejected: the label is cosmetic, the fallback is exactly today's rendering, and the read model already fetches every row of the EDM — the lookup is in-memory. → spec **P-12** (revised), **P-10**/**P-11** (wording), behavior in FR-010/FR-014

- **Q: The overlap statement says "N of this portfolio's M accounts match more than one state" and claims a clean partition when `Σ per-value counts == account_total`. Does that arithmetic measure either thing?** → **A: No, and it is replaced by two counts measured per account at Sync.** `Σ counts − account_total` mixes memberships against accounts (an account in three states adds 2) with a denominator that includes accounts carrying no value at all (which no per-value count sees), and the two errors cancel: a 1,701-account portfolio where 100 accounts carry a state reported a clean partition while 1,601 accounts landed in no sub-portfolio. FR-007 asks how many accounts land in more than one sub-portfolio and SC-002 promises that every account lands in at least one, so both are now read from `summary.breakout_coverage[dimension]` — `multi_value` and `account_total − covered` — and the partition claim requires both to be zero. Renaming the field to "excess memberships" and weakening the copy was rejected: it answers neither requirement. Full reasoning and the two errors in R11. → **P-13** (revised), behavior in FR-007

### Session 2026-08-09

Follow-on work from the Aug 6 CIC demo (minutes: `Risk_Modeler_Interface_Design_Minutes_8-6-26.md`; execution plan: `docs/pm/2026-08-09_spec005_followon_plan.md`).

- **Q: D11 — the demo audience read the breakout disclosures as a wall of text. What survives the cut?** → **A: One quantified line per disclosure; the counts stay mandatory, the explanation goes.** The overlap line reads `Warning: overlapping accounts — N of M accounts match more than one {dimension} and are included in full in each matching sub-portfolio` (zero repeats → "No overlapping accounts — …"; no measured coverage → the qualitative sentence alone); the blank-value line reads `N of M accounts carry no {dimension} value and are left out` (zero → "None left out — …"; no coverage → qualitative). Nothing in the measurement machinery changes — `compute_overlap`, the coverage scripts, and P-13's per-account counting stand. **Deleted prose, and why it was there:** the exposure-inflation sentence ("the inflation in exposure can exceed the inflation in account count — the accounts appearing in several sub-portfolios tend to be the largest", from W-4's 1.27× accounts vs 6.6× TIV finding) and the geography multi-state paragraphs (the O6-1 account-bucketing consequence stated per P-02). Both were written when the whole-account semantics were new to the room; the Aug 6 demo showed CIC understands the bucketing and resolves overlap loss-side, so the count alone carries the judgment and the mechanism explanation lives in this file and probe-findings.md Part 4, not in the modal. → spec **P-21** (FR-007 revised)

- **Q: D14 asks for breakouts by peril AND geography or peril AND LOB — does peril ship as a third quick-mode dimension?** → **A: No — peril ships inside custom grouping only (P-19).** The group model is exactly what D14 describes (select FL + peril 2 → one portfolio); the minutes never ask for a one-per-peril breakout, D16 rejected peril splitting as combinatorial bloat, and D15 leans profile-side for pure-peril filtering — so quick mode keeps lob/state as demoed, and standalone peril quick mode is **Deferred** pending the D15 team validation. Probe findings and the enumeration design in **R14**/W-21; O-01 closed there, O-02 (code-only display) rides the grouping-modal preview approval. → spec **P-19**

- **Q: D12/D13/D17 — analysts want "these three LOBs → one portfolio". What shape does custom grouping take?** → **A: A cart of named groups in the breakout modal; each group is one `rwb_job`; quick mode survives alongside.** A group holds selected values per dimension — **OR within a dimension, AND across dimensions** (`state IN (FL, GA) AND peril IN (2)`); quick mode stays single-dimension (P-20). Group identity is the **canonical member set**, hashed to a 12-hex `group_key` (dimensions and values sorted, deduped): the same members re-confirmed under a new name **adopt** the existing group — no rename, no duplicate (P-22) — and the generated number is `P{rm id}-G-{key token}`, so adoption resolves exactly as quick mode's does. Overlapping groups **warn, never block** (P-18 — CIC resolves overlap loss-side); the cart's per-row note is a may-overlap heuristic (groups sharing a selected value), honest because disjoint filters can still share a multi-value account. Preview counts are upper bounds — "up to N accounts", min over dimensions of the summed per-value counts (P-23); exact counts arrive in completion outcomes. One breakout episode per portfolio, either direction: a live cart blocks a quick confirm and vice versa (FR-020). One job per group (not one per cart) keeps the per-group idempotent re-run, the durable per-group error line, and `ensure_pending_rwb_job` revival — the cart is reconstructed for display through the shared `cart_id`. → spec **P-17**, **P-18**, **P-20**, **P-22**, **P-23** (FR-018–021)
- **Technical shape (T-12–T-15), and one supersession.** New `breakout_group` table — one row per (source, member set), `UNIQUE(source_portfolio_id, group_key)`, the row's UUID as the job's `requestor_id` (T-13: `rwb_job.requestor_id` is a Uuid column, so a composite string key failed) — **supersedes R3's "no filter-spec storage" (alternative b)**: the filter builder R3 deferred is now a product requirement, and the stored `filters` JSON is the approved plan, not a convenience blob. Generated rows keep the R3 triple with dimension `custom` and the group_key as `breakout_value`, so `uq_irp_portfolio_breakout` is untouched; the label/filters live only on the group row (one source of truth). Group selection is **app-side set algebra** over the existing per-dimension DataBridge reads — union within, intersect across (T-14); a combined SQL query was rejected: dynamic IN-lists through the templater, a new probe, and no correctness gain over intersecting probe-verified selections. The modal's custom pane is an Alpine sliver over server-rendered rows (T-15): one fetch renders every dimension's checkbox list (`x-show` pills — the per-dimension `hx-get` swap quick mode uses would discard ticked state), "Add group" posts to a server-side group-preview route, and the confirm re-validates every posted value against the stored summary. → plan **T-12**, **T-13**, **T-14**, **T-15**

- **Q: The demo hit a `uq_irp_portfolio_breakout` violation twice — break out → delete the sub-portfolios in Risk Modeler → sync → break out again, and every later sync of the EDM failed. Root cause?** → **A: The re-breakout inserted a ghost twin of each soft-deleted lineage row, and the next sync's resurrect-by-name revived the ghost into a duplicate live key.** The chain: `prune_missing` soft-deletes the RM-deleted sub-portfolios; the re-breakout regenerates the identical names (the collision universe is live-only) and, because `_write_generated`'s pre-check and its unique-violation recovery both read live rows only, inserts a second row per triple (the filtered index ignores dead rows, and the new RM ids miss `uq_irp_portfolio_edm_irp`); the next sync's `_snapshot_prune` resurrects the dead row by its **name** match, putting two live rows on one `(source_portfolio_id, breakout_dimension_code, breakout_value)` key — the violation fires inside `prune_missing`, uncaught, failing that sync and every one after it. Secondary defects found with it: `_UPDATE_BY_NAME` had no `deleted_at IS NULL` filter (a new RM portfolio reusing a dead sub-portfolio's name would stamp its irp_id onto the dead row and stay invisible), and `_write_generated`'s recovery re-select was live-only (a dead-row violation surfaced as `RuntimeError`, not `skipped_existing`). **Fix — reclaim in place (T-16):** `_write_generated` resolves against rows *including soft-deleted* — (edm, irp_id) identity first, lineage triple second — and reclaims a dead match (`deleted_at` cleared, new irp_id/name stamped onto the same row; a dead row holding a *different* lineage still refuses); `prune_missing` passes `AND source_portfolio_id IS NULL` into the resurrect-by-name leg (irp_id match unchanged); the snapshot upsert's name match is live-only. Hard-deleting the pruned rows instead was rejected: soft-delete is the table-wide convention, the dead row preserves provenance, and reclaim keeps the row id stable for anything holding it. Regression tests: `tests/unit/test_breakout_prune_rerun.py` (the full demo sequence), reclaim/refuse/revive cases in `test_portfolio_lineage.py`. → plan **T-16**, behavior in FR-011

### Session 2026-08-10

Design session (notes: `docs/design_session_notes/11_submissions_search_subportfolio_breakouts_grouping.md` §2.2).

- **Q: Generated group names prefix the source portfolio name (`usfl_commercial - Coastal HU`) and suffix on collision — CIC keeps its own naming conventions and relies on default sort order. What changes?** → **A: The name is exactly the label the analyst types — no prefix, no pre-population, no collision suffix; a duplicate name is refused instead.** With the name analyst-entered, silent suffixing is wrong-by-design (the analyst asked for that exact name), so duplicates **block**: an as-you-type check plus an Add-time block, against the workbench rows, the cart's earlier rows, and Risk Modeler within the EDM — the EDM import name-check pattern (`name_check` TTL cache, fail-open when Risk Modeler is unreachable; the confirm-time compose and the worker's duplicate-name adopt/fail are the backstops). Risk Modeler itself permits duplicate names; blocking is a deliberate divergence (Ben, Wendy, Cheryl agreed — CIC has no case for duplicates). An adopted member set keeps its approved name (its name IS its own portfolio) and is exempt from the check, which also keeps the P-22 re-confirm/heal path open. Quick-mode composed names are untouched — nothing is typed there. → spec **P-24**, **P-25** (FR-018)
- **Q: Re-adding a member set that already had a `breakout_group` row put the row's stored name in the cart instead of the name just typed. Keep the P-22 no-rename rule?** → **A: No — the cart always shows and the row always takes the name as typed; adoption keeps only the row identity (no duplicate row, no duplicate portfolio).** The stored-name adoption existed for an already-created portfolio, but stale stored names surfacing over what the analyst typed reads as a bug (Ben, 2026-08-10), and there is no deployed data to stay compatible with. A member set's own approved name is still never refused (the re-confirm heal path), and an already-created portfolio keeps its Risk Modeler name — the run skips it; only the row's label/name/number move. → spec **P-22 rev.** (FR-018, FR-019)
- **Q: The group number `P{rm id}-G-{key hash}` "wasn't useful at a glance" (Ben) and had no technical necessity. Replacement?** → **A: The number is the name truncated to 20 characters.** The number's one job — resolving the Risk Modeler portfolio in the duplicate-name adopt path (FR-011) — survives: a portfolio created by a prior run of the same group carries the same truncated name. Two names sharing their first 20 characters produce the same number; Risk Modeler permits duplicate numbers, and an ambiguous adopt already fails that entry with a recorded reason rather than adopting arbitrarily — accepted. `group_key` remains the group identity for row adoption (P-22), and rows approved before this session keep their stored composed name and `P…-G-…` number — the approved plan is immutable (rule 8). `_DIMENSION_LETTER` loses its `custom` entry; quick-mode numbers (`P{rm id}-{S|L}-{token}`) are unchanged. Supersedes the naming/number half of the 2026-08-09 grouping entry. → spec **P-26** (FR-019)

### Session 2026-08-11

Follow-up to note 11 §2.3 (criteria + "From" lineage surfacing).

- **Q: Note 11 §2.3 put each breakout's criteria and "From" reference on the table row — the shipped `bo-lineage` badge crowded the name column and hid the custom-group filters in a hover tooltip. Where does lineage display live?** → **A: In the expanded row only (Ben, 2026-08-11).** Expanding a generated sub-portfolio shows its base portfolio and its breakout criteria in the Risk Modeler description format — `Line of business IN (Homeowners)` for a quick breakout, the AND-joined filter set (`lob IN (a, b) AND state IN (x, y)`) for a custom group — above the value lists; the collapsed table row carries no badge, and base rows render nothing. The criteria string is composed in the template from what the row already carries (dimension label, value display label, `breakout_group.filters`) — no new columns, no new reads. Quick-mode names (`{source} - {value}`) still mark those rows at a glance; custom-group rows are analyst-named (P-24) and identify only when expanded — accepted. → spec **P-27** (FR-014 rev., US3)
- **Q: With the badge gone, a collapsed row no longer says a portfolio came from a breakout at all — the name convention was not enough (Ben, 2026-08-11). What comes back?** → **A: A `Breakout` marker, and only that.** It sits on the name cell's sub-line beside `Portfolio #{id}`, so it takes no extra row height and cannot push the name to a second line; its hover title names the base portfolio. The criteria stay in the expanded panel — the thing that crowded the column was the `↳ from {source} · {dim}: {value}` string and its tooltip, not the marker.

### Carried from the design record

Standing decisions restated for this feature, not new ones:

- **Breakouts loop the single create call app-side** — one create per sub-portfolio, so each outcome is captured individually and one failure doesn't orphan the rest (sequence diagrams README: "Multi-item composites loop the single IRP endpoint app-side").
- **Regions are not pre-defined constants** (§10A.2). Irrelevant to this spec's two actions (each sub-portfolio is a single value), but it is why no "Northeast" preset appears anywhere.
- **Portfolio deletion is out of MVP** (§10A.7) and the library has no delete method — a breakout has **no rollback**; partial failure is handled by idempotent re-run, not by deleting created portfolios (R7). → spec **P-07**
- **No job/batch rows for synchronous ops** (PRD §14.3): both the create and the add are synchronous (probe-confirmed — no 202 and no workflow URL appeared anywhere), so generated portfolios persist with no `irp_job` rows and no poller involvement.
- **Audit uses the app's existing conventions** — structured business-event logging with the actor id (pinned by `test_business_event_logs.py`), per-row provenance (`inserted_by`/`updated_by`), and the breakout job row's `input_data`/`output_data` (R2, R10). There is no audit-log table in this app and this spec does not introduce one. → spec **P-08**
- **Why the overlap is accepted at all** — the design record (Cheryl's confirmation of Risk Modeler's behaviour, the treaty-structure driver, the "sum to 100%" preference it collides with) is in [probe-findings.md](probe-findings.md) Part 4, the only copy of that material.
