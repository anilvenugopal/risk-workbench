# Feature Specification: One-Click Portfolio Breakouts by LOB & Geography (Iteration 4)

**Feature Branch**: `005-subportfolio-breakouts`

**Created**: 2026-07-29

**Status**: Draft

**Input**: User description: "Iteration 4 (spec-005): one-click actions to break Portfolios out by LOB and Geography (sub-portfolio breakouts). Scope is limited to those one-click breakout actions only. Per docs/PRD.md §21 (Iteration 4 build-plan entry) and §10A (portfolio management), docs/FUNCTIONAL_REQUIREMENTS.md §3 (single-click breakouts), design note 06 (exposure modification & sub-portfolios), and the sequence diagrams composite/create_subportfolios_by_lob.md and granular/create_subportfolio.md. A prior spec-005 draft was discarded (based on an outdated design and repo state); this spec is written fresh against the post-spec-004 codebase."

## Overview

Iteration 3 (spec 004) made imported exposure *legible*: the EDM detail page leads with a read-only per-portfolio breakdown — counts, perils, lines of business, geography (states), currency, TIV — backfilled from Risk Modeler and DataBridge. Iteration 4 lets the analyst *act* on that understanding: reshape exposure into sub-portfolios that match treaty terms the broker didn't break out ("isolate a state with a different retention, or exclude a line of business" — FR §3), without RiskLink and without waiting.

**This spec is deliberately narrower than the full PRD §10A surface.** Per the product direction that opened this iteration (2026-07-29), it covers **only the two one-click breakout actions**:

- **Break out by line of business** — one sub-portfolio per LOB present in the source portfolio.
- **Break out by geography (state)** — one sub-portfolio per state present in the source portfolio.

The single custom-filtered sub-portfolio builder (§10A.4's attribute/operator/value UI), the complement split ("X vs. not-X"), and "do the opposite" are **out of this spec** — they are follow-on slices over the same machinery. The current-split view is **already shipped** (spec 004's per-portfolio table on the EDM detail page is exactly §10A.3's "see the current split before deciding how to re-group"); this iteration hangs the breakout actions off that existing table rather than building a new view.

A breakout is an **app-side loop, one slice per distinct value present in the source portfolio**: for each value, select the source portfolio's accounts matching it, create one sub-portfolio in Risk Modeler, and add exactly those accounts to it — the programmatic mirror of the RM UI's own "filter accounts → select → Add to Portfolio" flow (conceptual walkthrough from the Risk Modeler LLM companion, mapped to real Platform operations in planning research). Each generated portfolio is persisted as an ordinary `irp_portfolio` row, now carrying **breakout lineage** (source portfolio + dimension + value) so the analyst can see which slices came from what. The values offered are the **real values present in the portfolio** — read from the exposure summary already backfilled by Iteration 3, never free-text ("people put crazy things in the LOB field" — FR §3). Nothing is created until the analyst reviews the slice list and confirms — judgment waits for a click (constitution Article 5); after creation, refreshing the EDM's per-portfolio detail is mechanical follow-up and auto-fires.

**Two Risk Modeler realities shape the requirements and are disclosed to the analyst rather than hidden:**

1. **Portfolios are account-bucketed.** An RM portfolio is a collection of **whole accounts** — a slice includes the entire account (all its policies and locations) if *any* policy/location on the account matches the slice value (sequence diagram `create_subportfolios_by_lob.md`; memory `moodys-portfolio-filter-lob`; structurally confirmed by the RM account model). Slices therefore **cover 100% of the source** (every account lands in at least one slice — the FR §3 "sum to 100%" goal read as coverage) but mixed-value accounts appear **in full in every slice they match** — slices can overlap and cannot be made "pure". This is inherent RM behavior, not an app choice.
2. **The shipped `create_portfolio()` creates an *empty* portfolio, and the library today can neither filter nor fully page a portfolio's accounts, nor add accounts to a portfolio.** The account **selection** read and the **add-accounts** write are **irp-integration enhancements built as part of this iteration** (the library is owned in-house; RM has no one-shot create-by-filter — confirmed in planning research). Both steps are synchronous — the create returns HTTP 201 and the add is doc-verified 200-only (resolved in planning research, 2026-07-30) — so slice creation involves no RM-side job and no poller.

## Clarifications

### Session 2026-07-29

- **Q: PRD §10A.5 / Iteration-4 build-plan entry mark the geography breakout as blocked on the commercial-policy geographic-split open question (O6-1/O6-2). Does it ship here?** → **A: Yes — the geography breakout is in scope by product direction (this session).** The scheduling block is lifted. The behavioral substance of O6-1 is settled by the documented account-bucketing semantics: RM assigns **whole accounts** to matching slices ("keep all", at account grain — there is no keep-only-matching-locations mode in the native filter, and no toggle is built or awaited). The consequence — a multi-state account lands in full in every state slice it touches — is **disclosed in the breakout preview**, not silently accepted. The complement split, where double-counting bites hardest, is out of this spec regardless. *(The PRD's O6-1/O6-2 register and §10A.5 blocked-sub-item note should be updated to record this direction as part of this iteration's documentation pass.)*

- **Q: The full §10A surface includes the custom filtered sub-portfolio builder, complement splits, and "do the opposite". Are they in?** → **A: No — one-click LOB and geography breakouts only (product direction, this session).** The filtered builder, complement split, and "do the opposite" are follow-on slices; the machinery this spec builds (account selection + slice creation, value enumeration, lineage, naming, gate) is what they will reuse.

- **Q: What grain does the geography breakout use — state, country, or both?** → **A: State only this iteration.** The granularity cap is state/country (§10A.2, O6-3), but the only geography enumeration source that exists is the per-portfolio **states** read from the Iteration-3 exposure summary (`countries` was explicitly dropped — "no country-level read; geography = states", IRP_INTEGRATION_FOLLOWUPS §6c). A by-country breakout follows when a country-level enumeration read exists; it is the same action shape with a different attribute.

- **Q: Where do the breakout's pick-list values come from, given DataBridge reads are worker-side only (constitution Art. 11)?** → **A: From the stored per-portfolio exposure summary backfilled by Iteration 3** (distinct `lines_of_business` and `states` per portfolio). No live DataBridge or RM read runs on the request path to enumerate values. Consequence: a portfolio whose summary has not been backfilled cannot be broken out — the action is disabled with a reason and the analyst is pointed at the existing per-EDM **Sync** action (the recovery path spec 004 built). Staleness follows the same trust model as the rest of the detail page (`as_of` signal + manual Sync).

- **Q: Is a breakout blocked when the source has only one distinct value?** → **A: Yes — nothing to break out.** A one-value breakout would produce a single slice identical to the source (account-bucketing makes it a full copy). The action is available but the dimension is offered disabled-with-reason when the stored summary shows fewer than two distinct values.

### Session 2026-07-30

- **Q: FR-008 said a zero-match slice fails and creates nothing, but the Edge Cases said a drifted value "produces an empty-but-valid portfolio in RM" — which is it, and how is summary staleness handled?** → **A: Fail the slice, create nothing (FR-008 stands; the edge case is corrected) — and staleness is blocked up front by a freshness check at confirm.** Risk Modeler's `stampDate` (returned by Search Portfolios; validated 2026-07-30 as an updated-at equivalent — updating underlying portfolio data changes it) is captured alongside the stored summary at backfill; the confirm POST re-reads it via `search_portfolios` (the submit-time name-resolution pattern, constitution Art. 2) and refuses the breakout when it differs from the captured stamp or no stamp is stored — 409 with "Sync the EDM, then retry", and **no `rwb_job` row is created**. Stamp-to-stamp equality only, never a wall-clock comparison (no skew sensitivity). No worker-side stamp re-check (the confirm-to-run window is seconds; concurrent RM-side editing is not an expected usage pattern): the zero-match per-slice failure remains the run-time backstop — it also catches a selection-token regression, which the freshness check cannot. Per-slice failures surface as a completion toast plus a persistent per-row error line rendered from the latest terminal breakout job's outcomes — it survives refresh, needs no dismissal state, and is removed by being superseded by the next terminal run for that portfolio + dimension (Sync → re-run for drift; plain re-run for transient RM failures).

### Carried from the design record (not new decisions)

- **Breakouts loop the single create call app-side** — one create per slice, so each slice's outcome is captured individually and one failure doesn't orphan the rest (sequence diagrams README: "Multi-item composites loop the single IRP endpoint app-side").
- **Regions are not pre-defined constants** (§10A.2). Irrelevant to this spec's two actions (each slice is a single value), but it is why no "Northeast" preset appears anywhere.
- **Portfolio deletion is out of MVP** (§10A.7) and the library has no delete method — a breakout has **no rollback**; partial failure is handled by idempotent re-run, not by deleting created slices.
- **No job/batch rows for synchronous ops** (PRD §14.3): the account-add step is synchronous (doc-verified 2026-07-30 — see plan/research R1), so slices persist with no `irp_job` rows and no poller involvement.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One-click breakout by line of business (Priority: P1)

An analyst has an imported EDM whose portfolio mixes lines of business the treaty structure needs separated. On the EDM detail page's portfolio table, they open the breakout action on the source portfolio and choose **By line of business**. The workbench shows the distinct LOB values actually present in that portfolio (from the stored exposure summary — no typing), the sub-portfolio name each slice will get, and the standing account-bucketing disclosure (mixed-LOB accounts appear in full in every slice they match). The analyst confirms; the workbench creates one sub-portfolio per LOB in Risk Modeler and each new portfolio appears in the EDM's portfolio list, linked back to its source. Their exposure figures fill in via the same backfill that populated the rest of the page.

**Why this priority**: The LOB breakout is the PRD's explicitly unblocked, ship-first case ("the LOB breakout is unblocked and ships here" — §21 Iteration 4) and the simplest (LOB is policy-level; one policy = one LOB). It exercises every piece of new machinery — account selection, slice creation + population, value enumeration, naming, lineage, gate, partial-failure handling — end to end.

**Independent Test**: On an EDM with a backfilled multi-LOB portfolio, run the LOB breakout and confirm one sub-portfolio per distinct LOB is created in Risk Modeler and listed on the EDM page with source lineage — with no free-text entry anywhere in the flow.

**Acceptance Scenarios**:

1. **Given** an imported, ready EDM whose source portfolio has a backfilled exposure summary listing N ≥ 2 distinct LOBs, **When** the analyst opens the breakout action and chooses By line of business, **Then** the preview lists exactly those N values with the generated sub-portfolio name for each, and nothing is created yet.
2. **Given** the preview, **When** the analyst confirms, **Then** N sub-portfolios are created in Risk Modeler — each containing exactly the source portfolio's accounts matching its LOB — and N new `irp_portfolio` rows appear in the EDM's portfolio list, each carrying its Risk Modeler portfolio id and lineage (source portfolio, dimension = LOB, value).
3. **Given** a completed breakout, **When** the EDM detail page is viewed after the follow-up detail refresh, **Then** the new sub-portfolios show their own exposure figures (the refresh auto-fires; the analyst does not have to click Sync).
4. **Given** the preview's disclosure, **When** the analyst reads it, **Then** it states that accounts matching more than one value appear in full in every slice they match, and that all slices together cover the entire source portfolio.
5. **Given** a source portfolio whose stored summary shows fewer than two distinct LOBs, **When** the analyst opens the breakout action, **Then** the LOB dimension is offered disabled with the reason ("only one line of business present").
6. **Given** a source portfolio with no backfilled exposure summary, **When** the analyst looks at the breakout action, **Then** it is disabled with a reason pointing at the EDM's Sync action rather than failing later.
7. **Given** an EDM that already contains a portfolio named like a would-be slice, **When** the breakout runs, **Then** the generated name is made collision-safe (deterministic suffix) rather than failing Risk Modeler's duplicate-name guard.

---

### User Story 2 - One-click breakout by geography (state) (Priority: P2)

An analyst needs a cedant's book split by state — e.g. a treaty carries a different retention for one state. From the same breakout action they choose **By geography (state)**; the workbench shows the states actually present in the portfolio (from the stored summary), the name each slice will get, and the account-bucketing disclosure — which matters most here, because a commercial account with locations in several states lands **in full** in each of those state slices. On confirm, one sub-portfolio per state is created and listed with lineage, exactly as in the LOB case.

**Why this priority**: Geography is the second of the two directed breakouts and the one the PRD had gated on O6-1/O6-2 — now unblocked by product direction with the account-bucketing semantics accepted and disclosed. It shares all machinery with US1; what it adds is the state attribute mapping and the sharper disclosure.

**Independent Test**: On an EDM with a backfilled multi-state portfolio, run the geography breakout and confirm one sub-portfolio per distinct state is created and listed with lineage; confirm the preview carries the multi-state-account disclosure.

**Acceptance Scenarios**:

1. **Given** a source portfolio whose stored summary lists M ≥ 2 distinct states, **When** the analyst chooses By geography (state), **Then** the preview lists exactly those M states with generated names, nothing created until confirm.
2. **Given** the preview, **When** the analyst confirms, **Then** M sub-portfolios are created — each containing exactly the source portfolio's accounts matching its state — persisted and listed with lineage (dimension = state, value), same as US1.
3. **Given** a multi-state commercial account in the source, **When** slices are created, **Then** that account's full exposure appears in every state slice it touches — and the preview's disclosure said so before the analyst confirmed.
4. **Given** the granularity cap (§10A.2), **When** the analyst looks for finer-than-state options (CRESTA, ZIP) or a country grain, **Then** none are offered — state is the only geography grain this iteration.

---

### User Story 3 - Generated slices are identifiable: lineage, naming, and audit (Priority: P3)

An analyst (or a colleague opening the EDM later) can tell at a glance which portfolios are breakout slices, of which source, by which dimension — from the portfolio list itself and from each slice's generated name. The breakout action is recorded in the audit trail (who ran it, when, source portfolio, dimension, slice outcomes).

**Why this priority**: Lineage is what keeps a 25-portfolio EDM legible after two breakouts and is the hook later iterations (batch analysis over slices, grouping results) build on. It is cheap once US1 exists but is not itself the reason the iteration ships.

**Independent Test**: After running a breakout, confirm the EDM portfolio list distinguishes generated slices and shows their source; confirm names follow the documented pattern; confirm an audit record exists for the action.

**Acceptance Scenarios**:

1. **Given** an EDM where a breakout has run, **When** the analyst views the portfolio list, **Then** generated slices are visibly associated with their source portfolio and show their dimension + value (e.g. grouped under or badged with the source), while broker-arrived portfolios are unchanged.
2. **Given** a generated slice, **When** its name is read, **Then** it follows the documented deterministic pattern (source name + value, collision-suffixed when needed) so it is recognizable in Risk Modeler too, where lineage columns don't exist.
3. **Given** a completed (or partially failed) breakout, **When** the audit trail is consulted, **Then** it records the actor, timestamp, source portfolio, dimension, and per-slice outcomes.

---

### Edge Cases

- **Stored summary missing or stale.** No summary → breakout disabled with reason + pointer to Sync (never a mid-flow failure). Stale summary (portfolio changed in RM since backfill) → caught at confirm by the freshness check (FR-002a): the source portfolio's current RM `stampDate` must equal the stamp captured when the summary was backfilled; a mismatch refuses the confirm (409, no job row created) with "Sync the EDM, then retry". Residual drift inside the confirm-to-run window is caught by the zero-match slice failure (FR-008) — never a silently created empty portfolio.
- **Fewer than two distinct values.** Dimension disabled with reason ("only one value present") — a one-slice breakout is a full copy of the source (see Clarifications).
- **Blank/unassigned values.** The enumeration source excludes blank LOB/state values (they are scrubbed in the summary SQL). Exposure whose value is blank is therefore in **no** slice — the preview carries a standing note that unassigned-value exposure is not captured, so "covers the whole source" is honest. (A remainder slice is complement-split territory — out of scope.)
- **Partial failure mid-loop.** Slice k of N fails (RM error, timeout): already-created slices **stay** (no rollback — no delete exists); the per-slice outcome is reported; re-running the same breakout is **idempotent** — it detects already-existing slices (by lineage/name) and creates only the missing ones.
- **Duplicate names.** A would-be slice name colliding with any existing portfolio in the EDM gets a deterministic collision suffix; re-runs resolve to the same name (idempotency depends on it).
- **Concurrent/repeated clicks.** Double-submitting the same confirmed breakout must not create duplicate slices — same idempotency mechanism (existing-slice detection) applies.
- **Large value sets.** A source with many values (e.g. 40+ states) creates that many portfolios: the preview shows the full list and count before confirm (no silent truncation); creation reports progress/outcome per slice rather than appearing hung; completion within the success-criteria budget.
- **Source portfolio deleted in RM between preview and confirm.** The create calls fail cleanly per-slice with the RM error surfaced; nothing is half-written app-side without a corresponding RM outcome recorded.
- **Breakout of a breakout.** A generated slice is an ordinary portfolio; once its own summary is backfilled it can itself be broken out (lineage chains). Nothing prevents this; the portfolio list must render chained lineage sanely.

## Requirements *(mandatory)*

### Functional Requirements

**Prerequisite gate & entry point**

- **FR-001**: The breakout action MUST hang off the existing per-portfolio table on the EDM detail page (spec 004) — no new page. It MUST be offered per source portfolio.
- **FR-002**: The prerequisite gate (§13.1 pattern, computed in code per constitution Art. 2) MUST enable the breakout op only when the EDM is imported/ready and the source portfolio exists; the per-dimension option MUST additionally require a stored exposure summary for the source portfolio with ≥ 2 distinct values for that dimension. Ineligible states MUST render disabled-with-reason (missing summary points at the EDM Sync action), never a hidden control or a mid-flow error.
- **FR-002a**: At confirm — and only there; the modal renders from stored state — the system MUST verify the stored summary is fresh: the source portfolio's current Risk Modeler `stampDate` (read via `search_portfolios`, the submit-time name-resolution pattern of constitution Art. 2 — never a `get_*` poll) MUST equal the `stampDate` captured when the summary was backfilled. On mismatch, or when no stamp is stored, the confirm MUST be refused with a reason pointing at Sync and **no breakout job row may be created**. To anchor this, the spec-004 summary backfill captures the portfolio's `stampDate` alongside the summary it writes.
- **FR-003**: The gate rule MUST be computed in code (this is the Iteration-4 gate work item from PRD §21), with unit tests (constitution Art. 12 names the gate as a must-test). There is no central gate module today — existing ops gate via service-side guard functions plus template-side disabled states; whether this slice follows that pattern or introduces a small shared gate helper is a plan decision, but the rule itself MUST live in one testable place, not be duplicated across templates.

**Breakout dimensions & value enumeration**

- **FR-004**: The system MUST offer exactly two one-click breakout dimensions this iteration: **line of business** and **geography at state grain**. No custom filter builder, no complement split, no "do the opposite", no country/CRESTA/ZIP grain.
- **FR-005**: The values fanned out over MUST be the distinct values actually present in the source portfolio, read from the **stored** per-portfolio exposure summary backfilled by Iteration 3 (`lines_of_business`, `states`). The request path MUST NOT query DataBridge or Risk Modeler to enumerate values (constitution Art. 11), and the analyst MUST NOT be able to type a value (pick-list only, FR §3).

**Preview, confirmation & disclosure**

- **FR-006**: Before anything is created, the system MUST present a preview: the dimension, every slice to be created (value + generated portfolio name), the slice count, and the disclosures of FR-007. Creation MUST NOT begin until the analyst explicitly confirms (constitution Art. 5 — judgment waits for a click).
- **FR-007**: The preview MUST disclose, in plain language: (a) accounts matching more than one value are assigned **in full to every slice they match** (account-bucketed filter; slices can overlap and are not "pure"); (b) together the slices cover the entire source portfolio **except** exposure whose value is blank/unassigned, which lands in no slice. For the geography dimension the multi-state-account consequence MUST be called out explicitly.

**Slice creation**

- **FR-008**: On confirm, the system MUST create one sub-portfolio per value, populated with **exactly the source portfolio's accounts matching the slice value** (selection scoped to the source portfolio, never the whole EDM) — an app-side loop, one slice at a time, so each slice has an individually captured outcome. A slice whose selection matches zero accounts MUST fail that slice with a recorded reason and create nothing for it — the run-time backstop behind the FR-002a freshness check, and the visible failure mode for a selection-filter regression (which returns zero accounts against a perfectly fresh summary).
- **FR-009**: Each created slice MUST be persisted as an `irp_portfolio` row carrying its Risk Modeler portfolio id and its breakout lineage: source portfolio, dimension, and value. Both the create (HTTP 201) and the add (doc-verified 200) are synchronous, so the id is written at creation time with no `irp_job` row and no poller involvement (constitution Art. 11). Where the creation loop itself runs (request path vs. an `rwb_job` worker) is a plan decision; either locus satisfies this requirement.
- **FR-010**: Generated slice names MUST follow a deterministic documented pattern derived from the source portfolio name and the slice value, MUST respect Risk Modeler's name constraints, and MUST be made collision-safe against every existing portfolio in the EDM by deterministic suffixing (RM's duplicate-name guard rejects collisions). Re-computing the name for the same source+dimension+value MUST yield the same result (idempotent re-runs depend on it).
- **FR-011**: A breakout MUST be idempotent at the slice level: re-running the same source+dimension breakout MUST detect already-created slices (by lineage) and create only the missing ones — the recovery path for partial failure and double-submission. There is NO rollback: created slices are never deleted by the app (portfolio deletion is out of MVP and the library has none).
- **FR-012**: Per-slice outcomes (created / failed with reason / already existed) MUST be reported to the analyst at completion, and a partial failure MUST leave the app state consistent with Risk Modeler (every row written corresponds to a real RM portfolio). Failure reporting MUST be durable-state-derived: beyond the transient completion toast, failed slices render as an error line on the source portfolio's row — derived from the latest terminal breakout job's outcomes, surviving refresh and navigation, with no dismissal state — until superseded by the next terminal run for that portfolio + dimension (Sync → re-run for drift; plain re-run for transient failures).

**After creation**

- **FR-013**: On breakout completion (including partial success), the system MUST auto-enqueue the existing EDM detail refresh (the spec-004 backfill worker) so new slices acquire their exposure figures without analyst action — mechanical follow-up auto-fires (constitution Art. 5). The web request MUST NOT wait on the refresh.
- **FR-014**: The EDM portfolio list MUST visibly associate each generated slice with its source portfolio and show its dimension + value; broker-arrived portfolios render unchanged. Chained lineage (breakout of a slice) MUST render sanely.
- **FR-015**: The breakout action MUST be audited: actor, timestamp, source portfolio, dimension, and per-slice outcomes.

**Cross-cutting**

- **FR-016**: The breakout flow MUST follow the shell conventions (real URLs, `hx-boost` nav, CSRF on the state-changing POST, idle-timeout handling) and the graceful-empty doctrine everywhere a summary may be missing.
- **FR-017**: Everything else on the EDM page stays read-only as shipped in spec 004 — this iteration adds the breakout actions and lineage display, and MUST NOT add portfolio edit/delete/merge or any other §10A.7 out-of-scope capability.

### Key Entities *(include if feature involves data)*

- **Portfolio (`irp_portfolio`)**: gains **breakout lineage** — a nullable self-reference to the source portfolio plus the breakout dimension and slice value (null for broker-arrived portfolios and, per DATA_MODEL, still no `created_by_irp_job` lineage — creation is synchronous). The dimension is an app-defined closed set → kind table per constitution Art. 3 (exact shape in data-model.md).
- **Breakout (the action, not necessarily a stored aggregate)**: a confirmed fan-out of one source portfolio over one dimension; its durable trace is the lineage on the generated `irp_portfolio` rows plus the audit record. Planning decided lineage + audit only (research R3) — no first-class breakout table.
- **Exposure summary (stored, spec 004)**: the read source for value enumeration (`lines_of_business`, `states` per portfolio); its absence gates the action. This spec adds one thing to its capture: the source portfolio's RM `stampDate` at backfill time — the comparison anchor for the FR-002a freshness check.
- **Prerequisite gate rule**: the Create-subportfolio row of the §13.1 gate ("EDM + ≥1 portfolio exist", extended per FR-002), computed in code.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From the EDM detail page, an analyst can produce one sub-portfolio per distinct LOB of a source portfolio in a single confirmed action — no Risk Modeler UI, no RiskLink, no free-text entry anywhere in the flow.
- **SC-002**: The same holds for geography at state grain, with the multi-state-account overlap disclosed before confirmation.
- **SC-003**: 100% of slice values offered come from values actually present in the source portfolio's stored summary; 0 free-text value entry paths exist.
- **SC-004**: Together the created slices cover the entire source portfolio (every account appears in ≥ 1 slice), with the blank-value exception disclosed — the FR §3 "sum to 100%" goal, honestly stated for an account-bucketed filter.
- **SC-005**: A typical breakout (≤ 15 slices) completes and is reflected in the portfolio list within 30 seconds of confirmation; larger fan-outs (40+ slices) complete reliably with per-slice outcomes visible.
- **SC-006**: A partial failure never leaves app state inconsistent with Risk Modeler, and re-running the breakout completes the missing slices without duplicating existing ones — demonstrable by killing the loop mid-run and re-running.
- **SC-007**: The prerequisite gate enables/disables the op correctly from entity state alone (imported EDM, portfolio presence, summary presence, ≥ 2 values) — the PRD Iteration-4 exit criterion, verified by unit tests. The confirm POST additionally verifies summary freshness against Risk Modeler (`stampDate` match, FR-002a) before any job row is created.
- **SC-008**: After a breakout, generated slices show their exposure figures on the EDM page without any analyst action beyond the breakout itself (auto-fired refresh), within the same time bound as the spec-004 post-import backfill.

## Assumptions

- **The slice-creation capability is an irp-integration enhancement delivered with this iteration.** The active wheel (0.2.1, TestPyPI; local checkout identical) can create only an *empty* portfolio, cannot filter or fully page a portfolio's accounts, and cannot add accounts to a portfolio. The library is owned in-house; the enhancements (the filtered/paginated account-selection read + an add-accounts method wrapping RM's "Add filtered accounts by portfolio") are developed against the local checkout (`make irp-local`) and published to TestPyPI before implement completes. The flow is the RM LLM companion's conceptual walkthrough (select accounts → create portfolio → add accounts) mapped to real, documented Platform operations; the residual unknowns (selection filter tokens; already-member semantics of the add step) are planning-research/sandbox-spike items.
- **Source scoping is exact by construction**: the selection read runs against the **source portfolio's own account list**, so a slice can never contain accounts from elsewhere in the EDM. (The earlier worry that an RM-side filter might prove EDM-wide only applies solely to a possible one-call optimization and cannot change this spec's behavior.)
- **The dimension attributes for account selection** (LOB: policy-level, candidate tokens `lobName`/`LOB Name`/`lineOfBusiness`; state: location-level, `admin1Name`/`admin1Code`) are not in the documented portfolio-accounts filter list — the exact selection query is confirmed in research/sandbox spike (RM UI traffic capture is the authoritative probe). A wrong token fails visibly (zero accounts selected), never by silently mis-populating a slice.
- **Value enumeration = stored spec-004 summary.** No new enumeration read is built; `lines_of_business` and `states` per portfolio already ship. (The summary's PORTINFOID↔RM-portfolioId join caveat carries over unchanged.)
- **`stampDate` is the freshness anchor (FR-002a).** Risk Modeler exposes no explicit updated-at; the `stampDate` attribute returned by Search Portfolios / Get Portfolio by ID behaves as one (validated 2026-07-30: updating underlying portfolio data changes it). The backfill captures it with the summary; the confirm compares stamp-to-stamp equality — no wall-clock comparison anywhere, so clock skew cannot produce a false verdict.
- **Geography is state-grain only** until a country-level read exists; complement/"do the opposite"/custom filter builder are out (Clarifications).
- **No portfolio deletion, no rollback** (§10A.7; library has no delete). Idempotent re-run is the recovery path.
- **The UI surface is small and derivative** (an action + modal preview over the existing spec-004 table, plus lineage display in existing rows). Per docs/UI_WORKFLOW.md, a quick rendered preview of the breakout modal (including its disabled/empty/partial-failure states) is expected before wiring — it is a new interactive surface, not a copy tweak.
- **Audit uses the app's existing conventions** — structured business-event logging with the actor id (pinned by `test_business_event_logs.py`) plus per-row provenance (`inserted_by`/`updated_by`) and, if the loop runs as an `rwb_job`, that row's `input_data`/`output_data`. There is no audit-log table in this app and this spec does not introduce one.
- **The PRD documentation pass** (O6-1/O6-2 register update recording the 2026-07-29 direction, §10A.5 blocked-note update, §21 Iteration-4 scope note narrowing to the two breakouts) happens within this iteration.
