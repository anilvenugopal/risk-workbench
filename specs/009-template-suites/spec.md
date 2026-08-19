# Feature Specification: Analysis Templates & Template Suites — Definition & Administration (Iteration 6)

**Feature Branch**: `009-template-suites`

**Created**: 2026-08-17

**Status**: Draft

**Input**: User description: "Analysis templates and template suites — definition and administration (PRD §11, Iteration 6; design note 14 D9–D14). An analysis template is one analysis definition ('one row in Analysis Builder'): analysis/model profile + output profile + event-rate scheme (auto-populated; required for DLM, optional for HD) + currency, plus optional settings. A template suite is an ordered set of templates, defined primarily by region + output level; suites may mix DLM, HD, and accumulation templates. Suites are predefined/admin-maintained, not freeform user-built: an administration surface, seeded starter suites (US, Canada, US+Canada, global — ~10 templates each), and CSV/Excel export + import. Also in scope: IRP metadata sync (§15.2) and an analysis-metadata screen. OUT of scope: analysis submission/execution, the suite run flow, grouping, results — those are Iteration 7."

## Review summary

**What the user can do when this ships.** An analyst opens the analysis-metadata screen — one page with four tabs — and sees the Risk Modeler reference data that configures an analysis: model profiles (each marked DLM, HD, or Accumulation), output profiles, event-rate schemes, and currency schemes with their vintages — synced on demand into the workbench, filterable, read-only (profiles are created and edited in Risk Modeler, never here). An administrator opens the suite-administration page and creates **analysis templates** (one analysis definition: model profile + output profile + event-rate scheme + currency selection — a required currency plus an optional currency-scheme/vintage pair, blank meaning Risk Modeler's default resolved when the analysis is submitted (P-10) — plus analysis settings) and composes them into **template suites** (a named, unordered set of templates). Four starter suites — US, Canada, US+Canada, Global — arrive seeded. A suite built in one environment exports to a spreadsheet file and imports into another environment without manual rebuilding.

**What this feature does NOT do.** No analysis is submitted or executed. The suite *run* flow (pick portfolios + treaties, pick the suite, go; expand-to-deselect), grouping, and results are Iteration 7 (PRD §11.3a, §21). The workbench never creates or edits profiles, schemes, or currencies in Risk Modeler.

**Business rules that shape the design** (design note 14, D10/D11/D14):

- A template is "one row in Analysis Builder"; a suite is a set of templates — **unordered** ("it's just a group… that we can run all together", design note 16 §2.1) — conceived by **region + output level**, both conveyed by the suite's name, not stored as separate fields (P-03). Suites are how CIC enforces consistent settings — predefined and administered, not freeform user-built.
- A DLM template **requires** an event-rate scheme; for an HD or Accumulation profile it is **optional**. The DLM/HD/Accumulation classification comes from the cached model profile, not from the user.
- A suite **may mix** DLM, HD, and accumulation templates (US wildfire is HD-only; Japan has DLM and HD suites). Keeping DLM and accumulation apart is a convention, not an enforced rule.
- Templates and suites are **global** — every analyst sees every template and suite; authorship is recorded but grants nothing.

**Decisions**:

| ID | Decision | Status |
|---|---|---|
| P-01 | Template/suite create-edit-delete and import are gated to the **admin role**; every analyst can view templates, suites, and metadata, and any analyst can run the metadata sync and export. | Approved |
| P-02 | Starter suites are seeded with indicative settings; CIC replaces the contents via the admin page or import when the US/Canada default-settings list arrives (O14-4). Seeding does not wait for that list. | Approved |
| P-03 | Region is **not** a stored attribute anywhere — the suite's or template's name identifies its region (and output level). Resolves O14-3: no suite-level region field, and no `region_label`/`peril_code` on templates (dropped 2026-08-18 with auto-naming). | Approved |
| P-04 | An import file applies **all-or-nothing**: every error in the file is reported in one pass; nothing is applied on any error. | Approved |
| P-05 | Import matches existing templates and suites **by name** and updates them; unmatched names are created. | Approved |
| P-06 | Metadata sync stores at most the first 16 characters of each currency name, matching Risk Modeler's create-currency limit even when legacy currency names exceed it. | Approved — **reinstated 2026-08-18** (briefly superseded the same day when currencies were thought droppable; P-07 as amended keeps the currency cache) |
| P-07 | The workbench caches and uses **all three currency objects — currencies, currency schemes, and currency-scheme vintages** — because analysis submission requires a specific value for each: the submit-time currency block is `{code, scheme, vintage, asOfDate}`, with `asOfDate` derived from the chosen vintage's effective date. A template therefore stores a currency (always) and a currency scheme + scheme vintage (as an optional pair — P-10, 2026-08-19: blank pair = Risk Modeler default, resolved at submit time). Schemes remain the display unit (design note 16 D3: CIC works in schemes; a currency appears in several schemes with different FX rates; ~2–5 schemes will ever exist): the metadata screen's fourth tab lists currency schemes with their vintages (tab order: model profiles, output profiles, event-rate schemes, currency schemes); individual currencies stay cached for the builder's pick list but get no tab of their own (D3 — "I don't think we need to see currencies"). The scheme and vintage reads exist in the irp-integration working copy (unreleased — being finished separately); the built currency sync stays and the schemes/vintages sync + tab swap in when they ship. | Approved 2026-08-18, amended same day (store all three, not schemes-instead-of-currencies) |
| P-08 | Suites are **unordered** (design note 16 §2.1): suite items carry no position/order and no per-item portfolio-name override — a suite is a plain set of templates. | Approved 2026-08-18 |
| P-09 | **Treaty-name pattern is dropped** from templates (design note 16 D11/O15-6): treaties are selected explicitly at run time in Iteration 7, never stored as a pattern on a template. | Approved 2026-08-18 |
| P-10 | **Currency scheme + vintage are an optional pair** on a template — both set or both empty; the currency itself is always required. An empty pair means "use Risk Modeler's default", displayed as **"Default"** wherever the template is shown, and resolved at **submit time** (Iteration 7): the default currency scheme (`isDefault` and active), then that scheme's latest vintage by effective date. Resolution must happen workbench-side because the submission API never defaults these values — a full `{code, scheme, vintage, asOfDate}` block is always sent. Submit-time (not save-time) resolution keeps "default" templates evergreen when a new vintage lands. When a scheme **is** chosen, a vintage is required (the builder pre-selects the scheme's latest by effective date, changeable); a scheme with no vintages cannot be saved. Currency-in-scheme membership (the scheme must carry an FX rate for the chosen currency) is **not validated** — the admin is trusted to pair them correctly; a mispairing surfaces at submit time in Iteration 7. | Approved 2026-08-19 |

**How to verify.** Sync metadata against the IRP sandbox and see profiles/schemes/currency schemes on the metadata screen; create a DLM template (event-rate scheme enforced) and an HD template (optional); compose a mixed suite; confirm the four starter suites exist after `make db-rebuild`; export all suites, import into a rebuilt database, and diff — identical.

## Clarifications

### Session 2026-08-18

- Q: When import updates an existing suite by name, does the suite's item list get replaced wholesale by the file's list, or merged with locally present items kept? → A: Replace wholesale — the imported suite's item list becomes exactly the file's list. *(Amended by the 2026-08-18 design session: items carry no order or override.)*
- Q: What is the export file format — multi-sheet Excel workbook, single denormalized CSV, or a pair of CSVs? → A: Excel workbook (.xlsx) with one sheet per entity kind (templates; suite items with suite name + template name).

### Session 2026-08-18 (design session, note 16)

- Currency selection is **scheme-first** (D3 → P-07): the metadata screen shows currency schemes (with vintages), not a standalone currency list. *Amended later the same day:* all **three** currency objects — currencies, currency schemes, and scheme vintages — are cached and stored on templates, because analysis submission requires a specific value for each (`{code, scheme, vintage, asOfDate}`). The scheme/vintage reads are being finished in irp-integration separately.
- Suites are **unordered**; the `Position` and `Portfolio Name Override` suite-item concepts are dropped (P-08).
- The optional **treaty-name pattern** template field is dropped (D11 → P-09); treaties are picked at run time in Iteration 7.
- Q: Are templates that belong to no suite included in export? → A: Yes — "export all" writes every template to the templates sheet, whether or not a suite references it.
- Q: When a sync is requested while another sync is running, is the second request rejected, queued, or coalesced? → A: Rejected — refused with a "sync already in progress" message; never interleaved, never queued.

### Session 2026-08-19

- Q: Are currency scheme and vintage required on every template? → A: No — they are an **optional pair** (P-10). Currency is always required. Blank pair = Risk Modeler's default, shown as "Default" and resolved at submit time in Iteration 7 (the submission API never defaults — confirmed: values must always be provided). If a scheme is chosen, a vintage is required, pre-selected to the scheme's latest by effective date; a scheme with no vintages blocks the save.
- Q: Do the currency pick lists query Risk Modeler live as the user types? → A: No — they filter the **local cache** with substring matching (LIKE semantics), like every other pick list (FR-006). The Risk Modeler `where_clause` reads are exact-match lookups, wrong for type-ahead; they belong to the sync (`isActive=True` filters) and to submit-time default resolution (`isDefault=True AND isActive=True`, latest vintage by effective date).
- Q: Is currency-in-scheme membership validated (the scheme must carry an FX rate for the chosen currency)? → A: Not now — deliberately deferred (P-10). Currency is a minor part of this feature; the admin is assumed to have set up the vintage correctly. A mispairing fails at submit time in Iteration 7.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sync and view analysis metadata (Priority: P1)

An analyst (or admin preparing to build templates) clicks "Sync IRP Metadata". The workbench fetches the analysis reference data from Risk Modeler — model profiles, output profiles, event-rate schemes, and the currency data (currencies, currency schemes, and scheme vintages) — and stores it locally. The analysis-metadata screen is a single page with four tabs; the fourth lists currency schemes with their vintages (individual currencies are cached for the template builder but not tabbed, P-07). Each tab lists its set filterable ("just get to UDCT"), each model profile carries a DLM, HD, or Accumulation marker, and the last-synced time is shown. Everything is read-only: these are created and edited in Risk Modeler and synced back — the same "selected, not owned" pattern as EDM data.

**Why this priority**: Every other capability in this feature consumes this cache — the template builder's dropdowns, DLM/HD detection, and import validation all read it. It also has standalone value: today the analyst opens Risk Modeler just to check what profiles exist.

**Independent Test**: Run the sync against the IRP sandbox; open the metadata screen and confirm the four tabs match Risk Modeler's profile/scheme/currency-scheme lists, filter a long profile list down to a UD profile, and confirm no create/edit control exists.

**Acceptance Scenarios**:

1. **Given** an empty reference cache, **When** the analyst runs "Sync IRP Metadata", **Then** model profiles (with the data that classifies each as DLM, HD, or Accumulation), output profiles, event-rate schemes, currencies, currency schemes, and scheme vintages are fetched from Risk Modeler and stored, and the screen shows when the sync ran.
2. **Given** a populated cache, **When** the analyst opens the analysis-metadata screen, **Then** the four sets appear as four tabs of one page, each listed read-only with per-list filtering, and each model profile shows a DLM, HD, or Accumulation marker.
3. **Given** Risk Modeler is unreachable, **When** the sync runs, **Then** the failure is reported, the previously synced cache remains intact, and the last-synced time is unchanged.
4. **Given** a re-sync after profiles changed in Risk Modeler, **When** it completes, **Then** the lists reflect the current Risk Modeler state (additions appear, removals disappear from the pick lists).

---

### User Story 2 - Create and administer templates and suites (Priority: P2)

An administrator opens the suite-administration page. They create an analysis template by picking a model profile, an output profile, and a currency from the synced lists; optionally they also pick a currency scheme — which requires a vintage, pre-selected to the scheme's latest by effective date — and when they leave the scheme blank the template shows "Default" and Risk Modeler's default scheme + latest vintage are resolved when the analysis is submitted (P-10); the event-rate scheme field is required when the chosen profile is DLM and optional otherwise, pre-filled when a default is determinable. The builder also surfaces the analysis settings — min loss threshold, number of max-loss events, franchise deductible, unrecognized-occupancy treatment — plus optional tags. They then compose a suite: name it (the name conveys region and output level, P-03) and add templates — an unordered group, mixing DLM, HD, and accumulation templates freely. After a database rebuild, four starter suites (US, Canada, US+Canada, Global — ~10 templates each) are already present to edit rather than build from scratch.

**Why this priority**: This is the feature — the objects the Iteration-7 run flow will execute. It depends on User Story 1's cache for its pick lists.

**Independent Test**: On the administration page, create a DLM template (save blocked until an event-rate scheme is chosen), an HD template (saves without one), and a suite containing both plus an accumulation template; confirm the starter suites exist and are editable.

**Acceptance Scenarios**:

1. **Given** the synced cache, **When** the admin creates a template, **Then** model profile, output profile, event-rate scheme, currency scheme, scheme vintage, and currency are chosen from cached values only (filterable pick lists, no free text for these fields); currency scheme and vintage may both be left empty (P-10), but never just one of them.
2. **Given** a chosen model profile that is DLM, **When** the admin tries to save without an event-rate scheme, **Then** the save is rejected with a message naming the rule; **Given** an HD or Accumulation profile, **Then** the template saves without one.
3. **Given** a saved template, **When** the admin adds it to a suite, **Then** the suite lists it, and the same template cannot be added to the same suite twice.
4. **Given** a suite containing DLM, HD, and accumulation templates, **When** it is saved, **Then** no mixing error is raised.
5. **Given** a template referenced by one or more suites, **When** the admin tries to delete it, **Then** deletion is blocked and the referencing suites are named.
6. **Given** a rebuilt database, **When** anyone views the suite list, **Then** the US, Canada, US+Canada, and Global starter suites are present with their templates.
7. **Given** a signed-in analyst without the admin role, **When** they view the administration page, **Then** templates and suites are visible but create/edit/delete actions are not offered, and direct attempts are rejected.
8. **Given** a re-sync that removed a model profile still referenced by a template, **When** anyone views that template, **Then** it is flagged as referencing a value no longer in Risk Modeler — kept and editable, never silently deleted.
9. **Given** a template saved with no currency scheme, **When** anyone views it, **Then** its scheme and vintage display as "Default" (Risk Modeler's default scheme and latest vintage, resolved at submit time in Iteration 7 — P-10).
10. **Given** a chosen currency scheme that has no vintages, **When** the admin tries to save, **Then** the save is rejected with a message naming the scheme (a vintage is required whenever a scheme is chosen).

---

### User Story 3 - Move suites between environments (Priority: P3)

An administrator exports suites — with every template they contain — to a spreadsheet file that opens in Excel. In another environment (Ben's build environment → CIC's), an administrator imports that file: existing templates and suites with matching names are updated, new ones are created, and the whole file applies all-or-nothing with every error reported in one pass.

**Why this priority**: Without it, every suite built during development is rebuilt by hand in CIC's environment (design note 14 O14-2). Valuable, but only once Stories 1–2 produce something to move.

**Independent Test**: Export all suites, run `make db-rebuild` (or import into a second environment), import the file, and confirm the suites and templates match the originals field-for-field.

**Acceptance Scenarios**:

1. **Given** existing suites, **When** the admin exports, **Then** the produced Excel workbook contains every suite, its items, and the full field set of every template — one sheet per entity kind (templates; suite items).
2. **Given** an export file, **When** it is imported into an environment where none of the names exist, **Then** all templates and suites are created exactly as exported (every template field preserved).
3. **Given** an export file naming templates/suites that already exist, **When** it is imported, **Then** matching names are updated in place and no duplicates are created; an updated suite's items become exactly the file's items — locally present items absent from the file are removed.
4. **Given** a file with any invalid row (missing required field, duplicate name within the file, DLM template without an event-rate scheme), **When** it is imported, **Then** nothing is applied and every error in the file is reported with its row.
5. **Given** an imported template naming a model profile not present in the local cache, **When** the import completes, **Then** the template is created and flagged as unresolved (the cache may simply not be synced yet), not rejected.

---

### Edge Cases

- Sync runs while another sync is in progress → second request is refused with a "sync already in progress" message; runs never interleave.
- A template's cached profile/scheme/currency/vintage value disappears or is renamed on re-sync → template flagged unresolved (US2 scenario 8), pick lists show only current values, saved values are never silently rewritten.
- Suite with zero templates → allowed while composing, visibly marked empty (it cannot do anything until Iteration 7 anyway).
- Duplicate template or suite name at save time → rejected with a message (names are the import matching key, P-05).
- An HD template *with* an event-rate scheme → allowed (optional, not forbidden).
- A currency vintage supplied without a currency scheme (import or direct POST) → rejected; the pair is set or empty together (P-10).
- A template's chosen currency has no FX rate in its chosen (or the default) scheme → not detected here (membership deliberately unvalidated, P-10); it fails at submit time in Iteration 7.
- Import file with columns from a newer/older export layout → unknown columns reported as errors (all-or-nothing, P-04).
- Two admins edit the same suite concurrently → last save wins; no locking in this iteration.
- Every suite is deleted and the seed later runs again (environment rebuild) → the four starter suites are re-created; resurrecting deleted starter suites is acceptable (the seed skips only when a live suite exists — decided 2026-08-18).

## Requirements *(mandatory)*

### Functional Requirements

**Metadata sync & analysis-metadata screen**

- **FR-001**: An on-demand "Sync IRP Metadata" action MUST fetch from Risk Modeler and store locally the analysis reference sets: model profiles — including the data that classifies each as DLM, HD, or Accumulation — output profiles, event-rate schemes, currencies, currency schemes, and currency-scheme vintages (P-07 — analysis submission needs a specific value from each of the three currency sets).
- **FR-002**: The sync MUST be repeatable: each run replaces the cached sets with the current Risk Modeler state and records when it ran; a failed run MUST leave the previous cache intact and report the failure. A sync requested while another is running MUST be refused with a "sync already in progress" message — runs never interleave.
- **FR-003**: The analysis-metadata screen MUST be a single page with four tabs — model profiles, output profiles, event-rate schemes, currency schemes, in that order — each tab listing its cached set read-only with per-list filtering and the last-synced time; the currency-schemes tab lists each scheme with its vintages (vintage code and effective date), while individual currencies are cached but not tabbed (P-07/D3); it MUST offer no create or edit for profiles or schemes.
- **FR-004**: Each model profile MUST display a DLM, HD, or Accumulation marker derived from its cached data (never chosen by the user), on the metadata screen and in the template builder.

**Analysis templates**

- **FR-005**: An admin MUST be able to create an analysis template with:
  - **Required**: name; model profile; output profile; currency (P-07 — submission's currency block is `{code, scheme, vintage, asOfDate}`; the as-of date is derived from the vintage's effective date at submit time, not stored); event-rate scheme (required when the profile is DLM, optional otherwise; when both the chosen profile and scheme are present in the cache, the scheme's peril/region must match the profile's — the same rule Risk Modeler enforces at submit).
  - **Optional pair (P-10)**: currency scheme + scheme vintage — both set or both empty. Empty = Risk Modeler's default, displayed "Default", resolved at submit time in Iteration 7 (default scheme, then its latest vintage by effective date — the submission API never defaults, so the workbench resolves). When a scheme is chosen a vintage is required; the builder pre-selects the scheme's latest by effective date, and a scheme with no vintages blocks the save, naming the scheme.
  - **Analysis settings**, surfaced in the builder with defaults: min loss threshold (numeric, two decimal places); number of max-loss events (integer); enable franchise deductible (yes/no); unrecognized occupancy types (one of "Skip location during analysis" or "Treat as unknown").
  - **Optional**: tags. (Treaty-name pattern dropped, P-09.)
- **FR-006**: Model profile, output profile, event-rate scheme, currency scheme, scheme vintage, and currency MUST be chosen from cached values via filterable pick lists — no free text for these fields, and filtering is substring matching over the local cache, never a live Risk Modeler query per keystroke (2026-08-19 clarification). Tags are entered as names (with autocomplete over names already used on templates): Risk Modeler resolves tag names at analysis submit time and creates missing tags, so there is no tag pick list to cache.
- **FR-007**: The event-rate scheme field SHOULD pre-fill automatically when a default is determinable from the chosen model profile; the admin can always change it, and FR-005 validation applies to the saved value.
- **FR-008**: Template names MUST be unique; a duplicate name MUST reject the save with a message.
- **FR-009**: Templates MUST be global (visible to every analyst) with authorship recorded; editing MUST re-apply FR-005/FR-006 validation.
- **FR-010**: Deleting a template referenced by any suite MUST be blocked, naming the referencing suites.
- **FR-011**: A template whose saved profile/scheme/currency/vintage value is absent from the current cache MUST be flagged as unresolved — kept and editable, never silently changed or deleted.

**Template suites**

- **FR-012**: An admin MUST be able to create a suite with a unique name and an unordered set of templates (P-08 — no item order, no per-item settings); the name conveys region and output level (P-03 — no separate region field); a template appears at most once per suite.
- **FR-013**: A suite MUST accept DLM, HD, and accumulation templates together — no mixing restriction.
- **FR-014**: Suites MUST be global with authorship recorded; template/suite create-edit-delete and import MUST require the admin role, while viewing, sync, and export are available to every analyst (P-01).
- **FR-015**: Deployment/seeding MUST install four starter suites — US, Canada, US+Canada, Global (~10 templates each, indicative settings per P-02) — editable and deletable like any other suite.

**Export & import**

- **FR-016**: An admin or analyst MUST be able to export — always everything, no per-suite selection: every suite with its items and the full field set of every template (including templates that belong to no suite, so a standalone template moves between environments too), as a single Excel workbook (.xlsx) with one sheet per entity kind: a templates sheet (full field set) and a suite-items sheet (suite name, template name).
- **FR-017**: An admin MUST be able to import such a file: names matching existing templates/suites update them, new names create them (P-05). An updated suite's item list is replaced wholesale — after import it contains exactly the file's items; locally present items absent from the file are removed.
- **FR-018**: Import MUST validate the whole file and apply it all-or-nothing; on any error (missing required field, value of the wrong type, duplicate name within the file, DLM template without an event-rate scheme, a scheme whose peril/region does not match its template's profile when both are in the cache, a currency vintage without a currency scheme, a vintage that does not belong to its row's scheme when both resolve in the cache, unknown column or missing sheet) it MUST apply nothing and report every error with its sheet and row (P-04). A row with a currency scheme but a blank vintage is filled with the scheme's latest cached vintage at import (mirroring the builder pre-fill); when the scheme is absent from the cache or has no cached vintages, the blank vintage is an error — an explicit vintage is required (P-10).
- **FR-019**: An imported value absent from the local cache (e.g. a profile not yet synced) MUST NOT reject the import; the template is created and flagged unresolved per FR-011.
- **FR-020**: An export followed by an import into an empty environment MUST reproduce every suite and template field-for-field, including suite membership.

### Key Entities

- **Analysis template**: one analysis definition ("one row in Analysis Builder") — name, model profile, output profile, event-rate scheme, currency (required) plus an optional currency-scheme/vintage pair (empty = "Default", P-10), min loss threshold, number of max-loss events, franchise deductible, unrecognized-occupancy treatment, tags, authorship. Global. (`analysis_template`, `analysis_template_tag` — DATA_MODEL §7.)
- **Template suite**: a named, unordered set of templates; the name conveys region and output level (P-03). Global, admin-maintained, seeded starter set. (`template_suite`.)
- **Suite item**: the membership of a template in a suite. (`template_suite_item`.)
- **Analysis reference cache**: locally stored Risk Modeler reference data — model profiles (with the DLM/HD/Accumulation classification), output profiles, event-rate schemes, currencies, currency schemes, currency-scheme vintages — plus when it was last synced. (`irp_*` cache tables — PRD §15.2.)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An analyst can answer "which profiles/schemes/currency schemes exist and which profiles are DLM, HD, or Accumulation" from the workbench alone — zero Risk Modeler visits — once a sync has run.
- **SC-002**: An admin can build a 10-template suite from synced metadata in under 15 minutes.
- **SC-003**: 100% of saved DLM templates carry an event-rate scheme; a save violating the rule is rejected at save time.
- **SC-004**: A suite exported from one environment and imported into another reproduces every template and suite field, and every suite's membership, with zero manual rework (field-for-field diff is empty).
- **SC-005**: The four starter suites are present and openable immediately after a database rebuild, with no manual setup.
- **SC-006**: Every unresolved reference (profile/scheme missing from the cache) is visible on the template it affects — none are silently dropped or altered.

## Assumptions

- The admin role already exists and role-gating a page/action is established capability (PRD §6); no new role is introduced.
- The IRP sandbox exposes enough reference data (profiles, schemes, currency schemes) to exercise sync and template creation end-to-end.
- The currency-scheme and scheme-vintage reads exist in the irp-integration working copy (`search_currency_schemes`, `search_currency_scheme_vintages`, `get_latest_currency_scheme_vintage`) but are not yet in a released build — they are being finished in a separate effort (the same cross-repo pattern as the T-06 validation utility). The already-shipped currency read/cache stays; the sync adds schemes and vintages when the release lands.
- The synced model-profile data distinguishes DLM, HD, and Accumulation. HD detection uses the profile's software version (PRD §11.4); how Accumulation profiles are identified is confirmed in the plan.
- Starter-suite contents use indicative settings until Cheryl's US/Canada default-settings list arrives (O14-4); the list changes seeded *data*, not this feature's behavior (P-02).
- The suite *run* flow and treaty selection happen at run time in Iteration 7; templates store no treaty-name pattern (dropped 2026-08-18, P-09/O15-6) and no auto-name pattern (dropped 2026-08-18) — how Iteration 7 names generated analyses is decided there (O7-3/O14-9).
- Whether a default event-rate scheme is determinable per model profile (FR-007) depends on what the synced reference data carries; the plan resolves it with a spike, and "no pre-fill" is an acceptable outcome — FR-007 is SHOULD, not MUST.
- The admin picks a currency the chosen (or default) currency scheme actually carries an FX rate for; the workbench does not validate membership (deferred, P-10) and a mispairing fails at analysis submit in Iteration 7. A Risk Modeler tenant is assumed to always have a default currency scheme with at least one vintage; if not, submitting a "Default" template fails cleanly at run time.
- The analysis settings in FR-005 (min loss threshold, number of max-loss events, franchise deductible, unrecognized occupancy types) open pre-filled with defaults; the specific default values are confirmed in the plan.
- Sync scope here is the analysis reference data (FR-001); the other §15.2 sets (simulation sets, database servers, EDM cache, tags) sync when the feature that consumes them lands. Tags specifically have no list-all read in the integration library today — Risk Modeler resolves and creates tags by name at submit time — so template tags are stored as names (FR-006).
