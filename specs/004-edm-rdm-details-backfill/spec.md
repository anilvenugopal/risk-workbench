# Feature Specification: EDM/RDM Details & Backfill (Iteration 3)

**Feature Branch**: `004-edm-rdm-details-backfill`

**Created**: 2026-07-23

**Status**: Draft

**Input**: User description: "the 004 spec, implementing 'Iteration 3' from docs/PRD.md — EDM/RDM details & backfill. Scope per docs/PRD.md §21 (Iteration 3 build-plan entry), §9 (EDM/RDM entities and the redesigned EDM detail view), §2.2 of FUNCTIONAL_REQUIREMENTS.md (Exposure Details Viewing), §12.4 (treaty viewing at the EDM level), §2.3 / §16.2 (broker RDM analysis + settings viewing), and the extension of the Iteration-2 poller/worker completion path to backfill entity detail on import completion; schema per DATA_MODEL.md §5 (irp_edm / irp_portfolio / irp_treaty) and §6 (irp_analysis). Post-import backfill of exposure and treaty detail from Risk Modeler, a redesigned EDM detail page that lets an analyst understand imported exposure without clicking through Risk Modeler, and viewing of broker analyses and their settings — placed first so the analyst can *understand* imported exposure and broker results before acting on them."

## Overview

Iteration 2 (spec 003) made the workbench *do work against Risk Modeler* — importing EDMs and RDMs, assembling packages, and tracking the background jobs. But an imported EDM was still a near-empty record: the analyst could see it reached *ready*, but not *what is in it*. To decide anything — which portfolio to analyze, whether the broker already delivered what's needed, whether a treaty is mis-coded — the analyst still had to click through Risk Modeler.

This iteration closes that gap with **detail and backfill**. When an import completes, the workbench fetches the entity's detail from Risk Modeler in the background and stores it; a **redesigned EDM detail page** then presents that detail with no click into Risk Modeler.

**The center of gravity of that page is the portfolio, not the EDM.** Analyses are run against a portfolio, not a whole EDM ("I don't run it over a whole EDM. I'm running it on a portfolio that's within the EDM" — Cheryl, 7/14), so the working view is a read-only **inline per-portfolio breakdown**: for each portfolio, its location/account/policy counts, the perils and sub-perils it covers, its geography, its currency, and its record volume. That is what tells the analyst which portfolio to run, whether a peril they expect (e.g. winter storm) is missing and must be added, and whether a portfolio is ~20K records or ~1M — so a large run can be scheduled overnight rather than hogging capacity mid-day. The **EDM itself carries only light context** — name, status, last-synced, source file and identifiers, and portfolio count — plus a **compact aggregate rollup strip** for quick orientation and a **per-EDM orientation line on the submission page**. **Treaties**, which are coded at the EDM level, get their own full-attribute view (expand/collapse, horizontal scroll, Excel export) so the analyst can catch mis-coding rather than trust it. On the results side, opening an imported RDM surfaces its **broker analyses and their settings/metadata**, so the analyst can gauge how much work a given RDM even needs.

It is placed first in the post-import sequence deliberately: the analyst must *understand* imported exposure and broker results — portfolio by portfolio — before shaping portfolios (Iteration 4), running hazard lookup (Iteration 5), or executing analyses (Iteration 6).

The mechanism is a **forward extension of the existing Iteration-2 completion path**: the poller detects an import reaching a terminal `FINISHED` status and enqueues a background work item; a worker performs the Risk Modeler REST fetch (EDM per-portfolio figures, treaty attributes, and broker-analysis metadata) and persists the detail. No detail fetch ever runs on a web request path, and the poller loop body never does the fetch itself (it remains a single-status-check batch). Detail is **backfilled forward only** — imports that complete after this capability ships are populated automatically; entities imported earlier stay in a graceful empty state until re-imported.

## Clarifications

### Session 2026-07-23

No blocking `[NEEDS CLARIFICATION]` markers were required — the PRD (§9, §12.4, §16.2, §21), FUNCTIONAL_REQUIREMENTS §2.2/§2.3/§7, and DATA_MODEL.md resolve most behavior. Three scope-boundary decisions were taken this session (they set this iteration's edges against Iterations 2, 4, 6, and 9):

- **Q: How do EDMs/RDMs imported *before* this capability ships get their detail data?** → **A: Forward-only.** Only imports that reach terminal `FINISHED` *after* this capability is deployed are backfilled, via the extended poller/worker completion path. Entities imported in Iteration 2 remain without detail (a graceful empty state) until re-imported. There is **no bulk one-time sweep and no per-entity manual "refresh from Risk Modeler" action** this iteration; both remain available as a later addition if needed. This aligns with the Iteration-3 exit wording "a newly completed import backfills its detail data automatically."

- **Q: Does the EDM detail page show per-portfolio figures, given the interactive current-split view and sub-portfolio creation are Iteration 4 (§10A.3)?** → **A: Yes, read-only.** The EDM detail page enumerates the portfolios that arrived with the EDM and shows a **read-only** per-portfolio exposure breakdown. The interactive current-split view, filter pick-lists, and sub-portfolio/breakout **creation** remain Iteration 4 — this iteration builds only the portfolio enumeration and per-portfolio figure display, with no create/edit actions. *(See the follow-up session below for the emphasis correction that makes this the page's primary content.)*

- **Q: How deep does broker RDM result viewing go, given Iteration 2 did not retrieve result data (analysis counts render empty, spec 003 D5)?** → **A: Analyses + settings only.** This iteration surfaces the **broker analysis list** (the `irp_analysis` rows captured at RDM-import completion, `rdm_id` set) grouped by `rdm_id`, and each analysis's **settings/metadata** (the §16.2/FR §7 metadata list). The actual **loss numbers** — ELT/EP/AAL, standard deviation, return-period losses, OEP/AEP/TCE points, PLT, and their hybrid Parquet + `analysis_result_meta` storage and the `retrieve_analysis_results` worker — are **deferred to a later iteration**. **This narrows the Iteration-3 exit phrase "broker loss results,"** which is read here as "broker analyses and their settings/metadata." Perspective switching and the numbers-focused review UI land with the results work.

### Session 2026-07-23 (follow-up — portfolio-level primacy)

A review of the design record corrected the *emphasis* of this spec. The **per-portfolio exposure breakdown — not the EDM-aggregate rollup — is the primary content of the EDM detail page**, because analyses are run against a portfolio, not a whole EDM. Sources: design note `04_navigation_page_layout_and_ui_patterns.md` TL;DR ("Portfolio-level detail … is wanted *per portfolio*, not just EDM-aggregate"), §4 (Cheryl: "I don't run it over a whole EDM. I'm running it on a portfolio that's within the EDM"), and §5 (EDM-aggregate figures are "quick orientation"; the per-portfolio breakdown is "needed once inside a specific EDM"); and FUNCTIONAL_REQUIREMENTS §2.2 ("Per-portfolio figures are shown once inside a specific EDM"). Decisions taken:

- **Per-portfolio detail is the P1 headline (was P3).** The redesigned EDM detail page leads with a read-only **inline** per-portfolio table (per-portfolio location/account/policy counts, perils/sub-perils, geography, currency, record volume). This is where the analyst decides which portfolio to run and spots a missing peril or an oversized (~1M-record) portfolio. **No dedicated portfolio drill-down page this iteration** — the figures are inline on the EDM page; the Submission→…→EDM→Portfolio drill-down page (which later becomes the analysis-launch entry point) lands with the analysis iterations.

- **EDM-aggregate rollup is demoted to quick orientation (was P1, now P3), shown in both places.** A compact aggregate strip at the top of the EDM page **and** a per-EDM orientation line in the submission detail page's package rows (extending the spec-003 package cards). The aggregate is a **roll-up of the same backfilled per-portfolio detail**, not a separate fetch or computation.

- **EDM header is minimal.** Name, status + last-synced (`as_of`), source file + identifiers, and portfolio count. Cedant and line of business are **not** in the EDM header — cedant is a submission-level attribute (a submission is one cedant's treaty), and LOB surfaces per portfolio (it is also a breakout dimension in Iteration 4).

- **Treaties remain an EDM-level section** (unchanged; §12.4 / design note §6): treaty setup is coded at the EDM level.

### Session 2026-07-23 (follow-up — portfolio↔analysis linkage & page composition)

A UI-alignment pass (rendered previews `docs/ui_previews/edm_detail.html` rev 7 / `rdm_detail.html` rev 3, iterated to approval) fixed the page composition and surfaced one substantive scope addition. Decisions taken:

- **Broker analyses are linked to the portfolio they ran against, and appear on the EDM page.** Each analysis carries Risk Modeler's exposure pointer (`exposureResourceId` where `exposureResourceType = PORTFOLIO`), which resolves to the owning portfolio. The EDM detail page therefore shows analyses in **two** places: **inline under each portfolio** (the analyses run against it) and a **standalone section grouped by source RDM** carrying the resolved portfolio per row. This is a *display + linkage* addition to US3; the RDM page (also US3) shows the same analyses with an added EDM column. The linkage is **resolved at read time** (not a stored FK) and is import-order safe. *(Confirmed with the approver 7/23: "we need to associate analyses with the Portfolio they were run against; that linkage is important … it is possible for some imported analysis results will not link to a Portfolio.")*

- **Some analyses will not link — that is normal, not an error.** A **group** (`is_group = true`) is a **single analysis** shown with Portfolio = **"Group"**; its contributing sub-analyses are **not knowable** from Risk Modeler, so no member breakdown is shown (groups appear only in the standalone section, never inside a portfolio). Any analysis whose exposure pointer does not resolve to a known portfolio shows **"— not linked."** Consequently the `irp_analysis.group_parent_id` column (DATA_MODEL §6) is **deferred** (nothing populates it this iteration).

- **Page composition is fixed in `ui.md`.** A single reusable expandable-comparison table (`.dtable`: frozen identifying column, per-row expand, pinned + rail-connected expanded body) renders Portfolios, Treaties, and Broker analyses on both pages; sections default open, row-level drills default closed, with the rate/event-rate detail one drill deeper. Records are **not** a separate column (records == locations). All read-only (FR-014).

### Consequences carried from spec 003

- **Analysis counts un-emptied.** Spec 003 D5 rendered the package-card analysis counts **empty** (the captured `irp_analysis` rows existed only for delete-enumeration). This iteration surfaces those rows, so analysis counts on the package card and EDM detail now render populated.
- **RDM-only import stays deferred (spec 003 D3).** Every tracked RDM this iteration has ≥1 EDM and every broker analysis has an `edm_id` set. The broker-analysis view must not *assume* a single owning EDM for the RDM (an RDM has no `edm_id`; its analyses carry it), but it does not need to handle the zero-EDM RDM-only case, which remains out of scope until the library change lands.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Understand each portfolio's exposure inside an EDM (Priority: P1)

An analyst has imported an EDM (Iteration 2). Analyses are run against a portfolio, not the whole EDM, and an EDM may carry anywhere from 1 to 25 portfolios. Instead of opening Risk Modeler, the analyst opens the EDM in the workbench and sees, inline, a read-only breakdown of every portfolio in it: for each portfolio, its location/account/policy counts, the perils and sub-perils it covers, its geography, its currency, and its record volume. From that they can tell which portfolio to run an analysis against, whether a peril they expect (e.g. winter storm) is missing and must be added, and whether a portfolio is ~20K records or ~1M — so a large run can be scheduled overnight rather than hogging capacity mid-day. The EDM header shows only light context (name, status, last-synced, source file, identifiers, portfolio count). All of it was fetched from Risk Modeler automatically when the import completed — the analyst did nothing to trigger it.

**Why this priority**: This is the reason the iteration is placed first and the heart of the redesigned page. The analyst's work happens at the portfolio level ("I don't run it over a whole EDM. I'm running it on a portfolio that's within the EDM," 7/14); without per-portfolio understanding they cannot choose what to analyze, catch a missing peril, or avoid launching an oversized run. The inline per-portfolio breakdown is the irreducible MVP slice of the iteration.

**Independent Test**: Import an EDM with multiple portfolios and let it reach *ready*; open its detail page and confirm every portfolio is listed inline with its own exposure figures (location/account/policy counts, perils/sub-perils, geography, currency, record volume) populated from Risk Modeler — as a textual snapshot, no map, no Power BI rebuild — and with no create/edit/split control offered.

**Acceptance Scenarios**:

1. **Given** an EDM import that Risk Modeler reports as `FINISHED` after this capability is deployed, **When** the poller observes the terminal status, **Then** a background work item is enqueued and a worker fetches the EDM's per-portfolio exposure detail from Risk Modeler and persists it — without blocking any web request and without the poller loop performing the fetch itself.
2. **Given** an EDM with N portfolios whose detail has been backfilled, **When** the analyst opens the redesigned EDM detail page, **Then** all N portfolios are listed inline, each showing its location/account/policy counts, perils and sub-perils, geography, currency, and record volume — as a textual snapshot, with no map.
3. **Given** the per-portfolio breakdown, **When** the analyst looks for a way to create, filter, or split a sub-portfolio, **Then** no such control is present — sub-portfolio creation, one-click breakouts, filter pick-lists, and the interactive current-split view are Iteration 4.
4. **Given** an EDM that reports zero portfolios, **When** the analyst opens it, **Then** the portfolio section shows a clear "no portfolios" state rather than being blank or erroring.
5. **Given** an EDM imported before this capability shipped (no backfilled detail), **When** the analyst opens its detail page, **Then** the page renders a graceful "detail not available — re-import to populate" state, and the EDM's core record (name, status, source file, identifiers) still displays.
6. **Given** a backfill fetch that fails or times out against Risk Modeler, **When** the analyst opens the EDM detail page, **Then** the per-portfolio section shows a clear "detail unavailable" state, the EDM's *ready* status is not reverted, and the fetch is recoverable through the existing job-failure/retry machinery.
7. **Given** an EDM with a very large portfolio (~1M+ records), **When** the analyst opens the detail page, **Then** it renders within the normal page-load budget because the figures are read from stored detail, never computed on the request path.

---

### User Story 2 - Review treaty setup on an EDM (Priority: P2)

An analyst wants to confirm how reinsurance is coded on an EDM before trusting it — Cheryl wants every treaty attribute visible because "sometimes people put the wrong thing in the wrong field." They open the EDM, see the treaties associated with it at the EDM level, expand one to read its full attribute detail, scroll horizontally through a wide attribute set in the compact view, and for an EDM with too many treaties to render cleanly, export the whole treaty set to Excel.

**Why this priority**: Catching a mis-coded treaty before analysis is a core "understand before acting" safeguard and a direct FR §2.2 requirement. It is a distinct, high-value slice that builds on the same backfill path as US1 but is independently demonstrable.

**Independent Test**: Open an EDM that has treaties; confirm the treaties show at the EDM level with full attribute detail, expand and collapse individually, scroll horizontally when the attribute set is wide, and export to Excel in one action — with no create/edit action offered.

**Acceptance Scenarios**:

1. **Given** an EDM with associated treaties whose detail has been backfilled, **When** the analyst opens the EDM detail page, **Then** the treaties are shown at the EDM level, most collapsed, with the ability to expand any one to its full attribute detail.
2. **Given** a treaty with a wide attribute set, **When** it is shown in the compact (collapsed-row) view, **Then** the attribute columns scroll horizontally without breaking the page layout.
3. **Given** an EDM with many treaties, **When** the analyst chooses to export, **Then** the full treaty set is exported to an Excel file.
4. **Given** the treaty view, **When** the analyst looks for a way to change a treaty, **Then** no create/edit control is present — treaty create/edit is a Risk Modeler pass-through delivered in a later iteration, and this view is read-only.

---

### User Story 3 - Review broker (RDM) analyses and their settings (Priority: P2)

An analyst received a broker RDM in a package and imported it (Iteration 2). Before running any of their own analyses, they open the RDM to see what the broker already ran: the list of broker analyses and, for each, its settings/metadata — engine and model version, engine type (DLM vs HD), analysis type, peril, region, currency, construction, line of business, group type, long-term vs near-term, event-rate scheme, and loss amplification. Crucially, each analysis shows **which portfolio it was run against**, so the analyst can see, on the EDM page, the analyses **linked to each portfolio** (inline) as well as the whole broker set **grouped by source RDM** — and judge how much work the RDM even needs (sometimes the broker already provides what's needed).

**Why this priority**: Broker-result review is the second pillar of "understand before acting" and directly supports the delivery decision (remodel vs. just push losses). The portfolio linkage is what lets the analyst read a broker result *in the context of the portfolio it covers* — the same context in which they choose what to run (US1). It is independently testable and, per this session's scope call, limited to analyses + settings + portfolio linkage (no loss numbers).

**Independent Test**: Open an imported RDM that produced broker analyses; confirm the analyses are listed and grouped by their source RDM, each showing its settings/metadata backfilled from Risk Modeler and the **portfolio it ran against** (or "Group" / "— not linked" where it doesn't resolve). On the EDM page, confirm each portfolio's linked analyses appear inline and the full broker set appears in a standalone RDM-grouped section — with no loss numbers, no own-vs-broker comparison, and no analysis execution required.

**Acceptance Scenarios**:

1. **Given** an imported RDM whose broker analyses were captured at import completion, **When** the analyst opens the RDM (or an analysis detail), **Then** the broker analyses are listed and **grouped by their source RDM** (e.g. "1 broker analysis across 4 EDMs" shown once, not four times).
2. **Given** a broker analysis, **When** its detail is shown, **Then** its settings/metadata (engine/model version, engine type + version, analysis type/mode, peril primary+secondary, region, currency, construction, LOB, group type, long-term vs near-term, event-rate scheme / rate vintage, loss amplification) are displayed, with rate/event-rate detail available one drill-down deeper.
3. **Given** a broker analysis whose metadata is not yet backfilled or partially available from Risk Modeler, **When** its detail is shown, **Then** the available fields render and missing fields show a graceful blank/unavailable state rather than an error.
4. **Given** the broker-analysis view, **When** the analyst looks for loss numbers or a comparison against their own results, **Then** none are shown — loss numbers and own-vs-broker comparison are explicitly deferred to later iterations.
5. **Given** a broker analysis that Risk Modeler ran against a portfolio (its `exposureResourceType` is `PORTFOLIO`), **When** its detail is shown, **Then** it is associated with the owning portfolio (resolved from the exposure pointer), the portfolio name is shown and links to it, and on the EDM page the analysis appears **inline under that portfolio**.
6. **Given** the EDM detail page, **When** the analyst views it, **Then** broker analyses appear both inline under each portfolio (only the analyses linked to that portfolio) **and** in a standalone section grouped by source RDM that carries the resolved portfolio per row.
7. **Given** a broker analysis that is a group (`is_group = true`), or one whose exposure pointer does not resolve to a known portfolio, **When** its detail is shown, **Then** it appears as a **single** row with Portfolio = **"Group"** (for a group; no member breakdown) or **"— not linked"** (for an unresolved one), shown only in the standalone section, never inside a portfolio, and never as an error.

---

### User Story 4 - Quick-orientation aggregate rollup (Priority: P3)

An analyst scanning a submission wants a fast read on each EDM before drilling in, and once inside an EDM wants a one-line roll-up before reading the portfolio detail. On the submission detail page, each EDM's package row shows a per-EDM aggregate orientation line (total counts, portfolio count, perils, record volume). On the EDM detail page, a compact aggregate strip at the top rolls up the same figures across the EDM's portfolios. Neither is where the work happens — they are quick orientation that sits above the per-portfolio detail (US1).

**Why this priority**: The aggregate rollup is genuinely useful for orientation but is not where analysts work — the per-portfolio breakdown (US1) carries the real value. Design note §5 places aggregate figures as "quick orientation" at the higher level, with the per-portfolio breakdown as the detail "once inside a specific EDM." It is cheap (a roll-up of US1's backfilled data) and independently demonstrable once the per-portfolio detail exists.

**Independent Test**: Open a submission with imported EDMs and confirm each package row shows a per-EDM aggregate line; open an EDM and confirm the compact aggregate strip rolls up its portfolios' figures — both derived from stored detail, both showing a graceful pending state when detail is not yet backfilled.

**Acceptance Scenarios**:

1. **Given** an EDM whose per-portfolio detail has been backfilled, **When** the analyst opens its detail page, **Then** a compact aggregate strip shows rolled-up figures (total location/account/policy counts, portfolio count, union of perils/sub-perils, combined geography, currency set, total record volume) above the per-portfolio breakdown.
2. **Given** a submission with one or more imported EDMs, **When** the analyst opens the submission detail page, **Then** each EDM's package row shows a per-EDM aggregate orientation line, extending the spec-003 package cards.
3. **Given** an EDM with no backfilled detail, **When** its aggregate strip or submission-page line would render, **Then** it shows the same graceful pending/empty state as the rest of the detail, never an error.
4. **Given** the aggregate figures, **When** they are displayed, **Then** they are derived from the stored per-portfolio detail (a roll-up), not computed by a separate Risk Modeler fetch on the request path.

---

### Edge Cases

- **Entity imported before this capability (forward-only).** Detail is not retroactively fetched; the per-portfolio section and the aggregate show "not available — re-import to populate," never an error.
- **Detail fetch fails / Risk Modeler unavailable.** The entity keeps its *ready* status; its detail shows an "unavailable" state; the fetch is recoverable (idempotent re-fetch) via existing job-failure handling.
- **Backfill runs more than once for the same entity** (retry, re-import). Persisting detail is idempotent — the stored detail is updated in place, never duplicated.
- **EDM with a very large portfolio (~1M+ records).** The detail page reads a pre-fetched stored summary, so it renders as fast as a small EDM — figures are never computed on the request path.
- **EDM with 25 portfolios.** The inline per-portfolio table lists all of them without a layout break; density guidance applies but the full list stays available (no silent truncation).
- **EDM with a single portfolio.** The per-portfolio row and the aggregate rollup are near-identical; both are still shown (the aggregate strip is thin and non-duplicative in intent).
- **Multi-currency EDM/portfolio.** All currencies present are shown per portfolio and combined into the aggregate currency set (currency defaulting for analysis is a later iteration's concern).
- **Inapplicable peril / zero coverage for a peril.** Shown as covered-or-not per the Risk Modeler data; a zero layer is a value, not an error.
- **Treaty with an unusually wide attribute set / an EDM with dozens of treaties.** Compact view scrolls horizontally; treaties collapse by default; Excel export handles the extreme case.
- **A broker analysis that is a group (`is_group = true`).** Shown as a single row with Portfolio = "Group" in the standalone section only — no member breakdown (unknowable from Risk Modeler); grouped-result numbers are still out of scope this iteration.
- **A broker analysis whose exposure does not resolve to a portfolio.** Shown as "— not linked" in the standalone section (never inside a portfolio); a normal state, never an error.
- **An EDM portfolio with no linked analyses.** Its "Analyses" indicator reads "None"; the row still lists its exposure figures.
- **The same broker RDM applied across M EDMs.** The broker analyses are grouped by `rdm_id` and shown once, not M times.

## Requirements *(mandatory)*

### Functional Requirements

**Backfill mechanism (foundational — shared by US1–US4)**

- **FR-001**: On an EDM or RDM import job reaching terminal `FINISHED` after this capability is deployed, the system MUST automatically initiate a background fetch of that entity's detail from Risk Modeler — for an EDM, its per-portfolio exposure figures and treaty attributes; for an RDM, its broker-analysis settings/metadata — extending the Iteration-2 poller→worker completion path.
- **FR-002**: The detail fetch MUST run in the background worker tier — never on a web request path, and never inside the poller loop body (the poller remains a single-status-check batch that enqueues a work item on terminal status).
- **FR-003**: Backfill MUST be **forward-only**: only imports completing after deployment are backfilled. The system MUST NOT run a bulk sweep of previously-imported entities, and no per-entity manual "refresh from Risk Modeler" action is provided this iteration.
- **FR-004**: Persisting backfilled detail MUST be idempotent — re-running the fetch for the same entity (retry, re-import) updates the stored detail in place without creating duplicates.
- **FR-005**: A failed or timed-out detail fetch MUST NOT revert the entity's *ready* status; the failure MUST be recoverable through the existing job-failure/retry machinery, and any detail view MUST render a clear "unavailable" state instead of erroring.

**EDM detail page & per-portfolio exposure detail — the primary post-import view (US1)**

- **FR-010**: The redesigned EDM detail page MUST be the primary post-import view of an EDM, replacing the minimal Iteration-2 EDM detail, reachable from the EDM Library and from the submission's package cards.
- **FR-011**: The EDM header MUST show the EDM's name, status, last-synced (`as_of`) trust signal, source file, and identifiers, and the portfolio count — and MUST NOT place cedant or line of business in the header (cedant is a submission-level attribute; LOB surfaces per portfolio).
- **FR-012**: The EDM detail page MUST present, as its primary content, an **inline read-only list of every portfolio** in the EDM.
- **FR-013**: For each portfolio, the system MUST show its location, account, and policy counts; perils and sub-perils covered; geography (regions and/or states, or a CIC-defined region label); currency; and record volume. **Record volume == location count** — it is surfaced via the Locations figure, not a separate "Records" column (ui.md §2). (Total insured value MAY be shown per portfolio where available from Risk Modeler.)
- **FR-014**: The per-portfolio view MUST be read-only — it MUST NOT offer sub-portfolio creation, one-click breakouts, filter pick-lists, or the interactive current-split view (all Iteration 4).
- **FR-015**: An EDM reporting zero portfolios MUST show a clear "no portfolios" state.
- **FR-016**: The exposure detail MUST be a fast textual snapshot — no map, and the existing Power BI exposure dashboards MUST NOT be rebuilt.
- **FR-017**: For an EDM with no backfilled detail (imported before this capability, or fetch pending/failed), the page MUST render a graceful pending/empty state and still display the EDM's core record (name, status, source file, identifiers).
- **FR-018**: The detail page MUST read pre-fetched stored detail and MUST NOT compute exposure figures on the request path, so it renders within the normal page-load budget regardless of exposure size (including ~1M+ record portfolios).

**Treaty viewing at the EDM level (US2)**

- **FR-020**: Treaty setup MUST be shown at the EDM level, listing the treaties associated with the EDM.
- **FR-021**: Each treaty MUST show its full attribute detail (every attribute), so the analyst can catch mis-coding rather than trusting it blindly.
- **FR-022**: Treaties MUST expand and collapse individually — few shown expanded, many collapsed to focus one at a time.
- **FR-023**: Wide treaty attribute sets MUST scroll horizontally in the compact view without breaking page layout.
- **FR-024**: The full treaty set MUST be exportable to an Excel file in one action.
- **FR-025**: The treaty view MUST be read-only — no create/edit control is offered (treaty create/edit is a Risk Modeler pass-through delivered in a later iteration).

**Broker (RDM) analyses and settings viewing (US3)**

- **FR-030**: Opening an imported RDM MUST list its broker analyses (the captured `irp_analysis` rows with `rdm_id` set), **grouped by source RDM** so a broker analysis applied across M EDMs is shown once rather than M times.
- **FR-031**: Each broker analysis MUST show its settings/metadata: engine/model version; engine type (DLM vs HD) and version; analysis type/mode; peril (primary and secondary); region; currency; construction; line of business; group type; long-term vs near-term; event-rate scheme / rate vintage; and loss amplification (PLA) — with rate/event-rate detail available one drill-down deeper than the rest.
- **FR-032**: Broker analysis settings/metadata MUST be backfilled from Risk Modeler on the same forward-only, import-completion-triggered path as EDM detail — captured when the RDM import reaches terminal FINISHED, never on a web request path.
- **FR-033**: Broker **loss result numbers** — ELT summary (AAL, max event loss, record count), standard deviation, return-period losses, OEP/AEP/TCE points, PLT — and their storage (`analysis_result_meta` + Parquet) and the `retrieve_analysis_results` worker MUST NOT be built this iteration; they are deferred.
- **FR-034**: The broker-analysis view MUST be standalone — no own-vs-broker side-by-side comparison (later iteration) and no own executed results (later iteration).
- **FR-035**: A broker analysis that is a group (`is_group = true`) MUST be displayed as a **single** row marked as a group with Portfolio = "Group". Its member breakdown MUST NOT be shown — a group is one analysis and its contributing sub-analyses are not available from Risk Modeler this iteration; groups appear only in the standalone (RDM-grouped) section, never inside a portfolio.
- **FR-036**: Each broker analysis MUST be associated with the **portfolio it was run against**, resolved from Risk Modeler's exposure pointer (`exposureResourceId` where `exposureResourceType = PORTFOLIO`) to the owning `irp_portfolio`. The association MUST be **resolved at read time** (matched on the owning EDM + Risk Modeler portfolio id), not gated on backfill ordering. The linkage MAY be absent — a group analysis and any analysis whose exposure pointer does not resolve to a known portfolio MUST be shown as **"— not linked"** (a group as "Group"), never as an error.
- **FR-037**: The EDM detail page MUST surface the broker analyses linked to the EDM in **two** views: **inline under each portfolio** (only the analyses linked to that portfolio, as a per-portfolio expansion) and a **standalone list grouped by source RDM** carrying the resolved portfolio per analysis. The per-portfolio "Analyses" indicator MUST show a descriptive count of the linked analyses (e.g. "2 broker analyses", "None").

**EDM-aggregate quick-orientation rollup (US4)**

- **FR-040**: The EDM detail page MUST show a **compact aggregate rollup strip** above the per-portfolio breakdown, rolling up the per-portfolio figures (total location/account/policy counts, portfolio count, union of perils/sub-perils, combined geography, currency set, total record volume — record volume == total locations, per FR-013) for quick orientation.
- **FR-041**: The submission detail page MUST show a **per-EDM aggregate orientation line** in each EDM's package row, extending the spec-003 package cards.
- **FR-042**: The aggregate figures MUST be derived from the stored per-portfolio detail (a roll-up), not a separate Risk Modeler fetch or a computation on the request path.
- **FR-043**: When an EDM's detail is not backfilled, the aggregate strip and the submission-page line MUST show the same graceful pending/empty state as the rest of the detail, never an error.

**Cross-cutting**

- **FR-050**: Analysis counts on the package card and EDM detail (rendered empty in Iteration 2) MUST now render populated from the captured broker-analysis rows.
- **FR-051**: All detail views MUST follow the shell conventions (breadcrumb as a function of manifest position, `hx-boost` navigation, status-bar last-action) established in Iteration 0.
- **FR-052**: Detail views MUST surface the entity's `as_of` last-confirmed-against-Risk-Modeler trust signal where detail is shown; results metadata, being immutable, needs no drift signal.

### Key Entities *(include if feature involves data)*

- **Portfolio (`irp_portfolio`)**: the **primary unit** of the EDM detail page — a named exposure view within an EDM, arriving with the EDM. This iteration enumerates every portfolio and stores its per-portfolio exposure figures (location/account/policy counts, perils/sub-perils, geography, currency, record volume) for read-only inline display; it does not create, filter, or split them (Iteration 4).
- **EDM (`irp_edm`) detail**: the EDM's light identity/context (name, status, `as_of`, source file, identifiers, portfolio count) plus the stored per-portfolio detail it owns. The EDM-aggregate rollup shown for orientation is a **roll-up of that per-portfolio detail**, not a separately stored figure. Populated forward-only on import completion.
- **Treaty (`irp_treaty`)**: reinsurance belonging to an EDM, referenced by name — a read/cache record. This iteration backfills its full attribute detail for the EDM-level treaty view and Excel export; it does not create or edit treaties.
- **Broker analysis (`irp_analysis`, `rdm_id` set)**: an analysis produced by importing a broker RDM (captured in Iteration 2 for delete-enumeration). This iteration backfills and surfaces its settings/metadata, grouped by `rdm_id`, and **links it to the portfolio it ran against** — captured as Risk Modeler's `exposureResourceId` (type `PORTFOLIO`) on the row and resolved to the owning `irp_portfolio` at read time (not a stored FK). A group is a single analysis with no member breakdown; an unresolved analysis is "not linked." Own analyses (`rdm_id` null) and all loss result data are out of scope.
- **Backfill work item (`rwb_job`)**: the background unit that performs a detail fetch, enqueued by the poller when an import reaches terminal `FINISHED`; idempotent, recoverable, decoupled from the `irp_job` it followed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of EDM imports that reach *ready* after this capability is deployed have their per-portfolio exposure detail populated on the detail page automatically, with no analyst action, within roughly one minute of the import completing.
- **SC-002**: An analyst can read every portfolio's key exposure figures (location/account/policy counts, perils/sub-perils, geography, currency, record volume) for an imported EDM entirely in the workbench — zero clicks into Risk Modeler — and from them choose which portfolio to run and spot a missing peril or an oversized portfolio.
- **SC-003**: For an EDM with N portfolios, all N are listed inline with their per-portfolio exposure figures (no silent truncation of the list).
- **SC-004**: An analyst can view every attribute of every treaty on an EDM in-app and export the full treaty set to Excel in a single action.
- **SC-005**: An analyst can see the broker analyses for an imported RDM and each analysis's settings/metadata in the workbench — zero clicks into Risk Modeler — and use them to judge how much work the RDM needs.
- **SC-006**: A detail fetch failure or a not-yet-backfilled entity never produces a broken or errored detail page — a clear unavailable/pending state is shown 100% of the time, and the entity's *ready* status is preserved.
- **SC-007**: The EDM detail page renders within the normal page-load budget regardless of exposure size (including ~1M+ record portfolios), because figures are served from stored detail rather than computed on view.
- **SC-008**: On the submission detail page, each imported EDM shows a per-EDM aggregate orientation line, and the EDM detail page shows a compact aggregate strip — both derived from the stored per-portfolio detail.
- **SC-009**: For every broker analysis Risk Modeler ran against a portfolio, the analyst can see which portfolio it covers — inline under that portfolio on the EDM page and in the standalone RDM-grouped section — while groups and analyses with no resolvable portfolio are clearly shown as "Group" / "— not linked" rather than mislinked or errored.

## Assumptions

- **Per-portfolio detail is the primary content, read-only (this session's follow-up scope call).** The EDM detail page leads with an inline per-portfolio breakdown; the EDM-aggregate rollup is demoted to a quick-orientation strip (EDM page) plus a per-EDM line (submission page), both roll-ups of the same stored per-portfolio detail. The interactive split-view, filter pick-lists, and sub-portfolio/breakout creation are Iteration 4.
- **No dedicated portfolio drill-down page this iteration.** Per-portfolio figures are shown inline on the EDM page; the Submission→…→EDM→Portfolio drill-down page (which becomes the analysis-launch entry point) is built with the analysis iterations.
- **Broker loss numbers deferred (this session's scope call).** "Broker loss results" in the Iteration-3 exit criterion is read as "broker analyses and their settings/metadata." ELT/EP/AAL/return-period numbers, `analysis_result_meta`, Parquet storage, the `retrieve_analysis_results` worker, and perspective switching land with the results iteration.
- **Forward-only backfill (this session's scope call).** No bulk sweep and no manual per-entity refresh this iteration; the demo path for "open an imported EDM and see its exposure detail" uses an EDM imported after this capability ships.
- **RDM-only import remains deferred (spec 003 D3).** Every tracked RDM has ≥1 EDM and every broker analysis has an `edm_id`; the zero-EDM RDM-only case is not handled this iteration.
- **Detail is sourced from Risk Modeler REST, not DataBridge.** DataBridge validation/profiling/exposure-modification (Phase A, §10) is out of MVP; per-portfolio figures, EDM roll-ups, treaty attributes, and analysis metadata all come from Risk Modeler REST endpoints.
- **Treaty detail is cached and displayed from `irp_treaty`** (a read/cache record), populated by the backfill; live `search_treaties` is how the cache is populated/refreshed. No pass-through edit exists yet to invalidate it.
- **Excel export produces a standard `.xlsx` workbook** generated server-side.
- **This iteration builds on the Iteration-0 shell and the Iteration-2 EDM/RDM entities, libraries, poller, and Dramatiq worker scaffold**; it replaces the minimal Iteration-2 EDM detail with the redesigned page and extends the spec-003 submission package cards with a per-EDM aggregate line.
- **Out of scope entirely**: own-analysis execution and results, sub-portfolio/geohaz/analysis/grouping operations, treaty create/edit pass-through, broker side-by-side comparison, and Loss Repository export (each scheduled in a later iteration).

## Dependencies

- **Iteration 2 (spec 003)** delivered and in place: EDM/RDM import, the poller for `import_edm`/`import_rdm`, the Dramatiq worker scaffold, the `backfill_rdm_analyses` capture of `irp_analysis` rows, package cards, the EDM/RDM libraries, the submission detail page / package cards this iteration extends with a per-EDM aggregate line, and the minimal EDM detail page this iteration redesigns.
- **`irp-integration` active wheel** — confirm the Risk Modeler REST method surface for (a) portfolio enumeration and per-portfolio exposure figures (the primary payload), (b) EDM roll-up figures (or derive them from the per-portfolio data), (c) treaty attribute detail (`search_treaties` plus full attributes), and (d) broker analysis settings/metadata, against the *active* wheel (`make irp-status`) before implementing; the library is pre-release and its signatures move. New confirmed methods and any gaps are tracked in `docs/IRP_INTEGRATION_FOLLOWUPS.md`.
- **WORKBENCH schema additions** for the stored detail (per-portfolio figures, treaty attributes, analysis metadata, EDM light-context) and any new `rwb_job_type` for the detail-backfill worker(s). The §21.0 DB-lifecycle prompt applies at planning time: choose **Rebuild / Refresh / Skip** for the `WORKBENCH` database (this iteration touches only `WORKBENCH`; `EXPOSURE`/`LOSS` are untouched; DATABRIDGE is never touched).
- **Server-side Excel (`.xlsx`) generation** capability for the treaty export.
- **Risk Modeler availability** at backfill time — a hard runtime dependency for the fetch; when unavailable, entities remain viewable in their pre-backfill/empty state and the fetch retries (§15.6).
