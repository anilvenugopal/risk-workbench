# Feature Specification: Analysis Templates & Template Suites — Definition & Administration (Iteration 6)

**Feature Branch**: `009-template-suites`

**Created**: 2026-08-17

**Status**: Draft

**Input**: User description: "Analysis templates and template suites — definition and administration (PRD §11, Iteration 6; design note 14 D9–D14). An analysis template is one analysis definition ('one row in Analysis Builder'): analysis/model profile + output profile + event-rate scheme (auto-populated; required for DLM, optional for HD) + currency, plus optional settings. A template suite is an ordered set of templates, defined primarily by region + output level; suites may mix DLM, HD, and accumulation templates. Suites are predefined/admin-maintained, not freeform user-built: an administration surface, seeded starter suites (US, Canada, US+Canada, global — ~10 templates each), and CSV/Excel export + import. Also in scope: IRP metadata sync (§15.2) and an analysis-metadata screen. OUT of scope: analysis submission/execution, the suite run flow, grouping, results — those are Iteration 7."

## Review summary

**What the user can do when this ships.** An analyst opens the analysis-metadata screen — one page with four tabs — and sees the Risk Modeler reference data that configures an analysis: model profiles (each marked DLM, HD, or Accumulation), output profiles, event-rate schemes, and currencies — synced on demand into the workbench, filterable, read-only (profiles are created and edited in Risk Modeler, never here). An administrator opens the suite-administration page and creates **analysis templates** (one analysis definition: model profile + output profile + event-rate scheme + currency, plus analysis settings) and composes them into **template suites** (a named, ordered set of templates). Four starter suites — US, Canada, US+Canada, Global — arrive seeded. A suite built in one environment exports to a spreadsheet file and imports into another environment without manual rebuilding.

**What this feature does NOT do.** No analysis is submitted or executed. The suite *run* flow (pick portfolios + treaties, pick the suite, go; expand-to-deselect), grouping, and results are Iteration 7 (PRD §11.3a, §21). The workbench never creates or edits profiles, schemes, or currencies in Risk Modeler.

**Business rules that shape the design** (design note 14, D10/D11/D14):

- A template is "one row in Analysis Builder"; a suite is an ordered set of templates conceived by **region + output level** — both conveyed by the suite's name, not stored as separate fields (P-03). Suites are how CIC enforces consistent settings — predefined and administered, not freeform user-built.
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

**How to verify.** Sync metadata against the IRP sandbox and see profiles/schemes/currencies on the metadata screen; create a DLM template (event-rate scheme enforced) and an HD template (optional); compose a mixed suite; confirm the four starter suites exist after `make db-rebuild`; export all suites, import into a rebuilt database, and diff — identical.

## Clarifications

### Session 2026-08-18

- Q: When import updates an existing suite by name, does the suite's item list get replaced wholesale by the file's list, or merged with locally present items kept? → A: Replace wholesale — the imported suite's item list becomes exactly the file's list; order and overrides come from the file.
- Q: What is the export file format — multi-sheet Excel workbook, single denormalized CSV, or a pair of CSVs? → A: Excel workbook (.xlsx) with one sheet per entity kind (templates; suite items with suite name, order, override).
- Q: Are templates that belong to no suite included in export? → A: Yes — "export all" writes every template to the templates sheet, whether or not a suite references it.
- Q: When a sync is requested while another sync is running, is the second request rejected, queued, or coalesced? → A: Rejected — refused with a "sync already in progress" message; never interleaved, never queued.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sync and view analysis metadata (Priority: P1)

An analyst (or admin preparing to build templates) clicks "Sync IRP Metadata". The workbench fetches the four sets of analysis reference data from Risk Modeler — model profiles, output profiles, event-rate schemes, currencies — and stores it locally. The analysis-metadata screen is a single page with four tabs, one per set. Each tab lists its set filterable ("just get to UDCT"), each model profile carries a DLM, HD, or Accumulation marker, and the last-synced time is shown. Everything is read-only: these are created and edited in Risk Modeler and synced back — the same "selected, not owned" pattern as EDM data.

**Why this priority**: Every other capability in this feature consumes this cache — the template builder's dropdowns, DLM/HD detection, and import validation all read it. It also has standalone value: today the analyst opens Risk Modeler just to check what profiles exist.

**Independent Test**: Run the sync against the IRP sandbox; open the metadata screen and confirm the four tabs match Risk Modeler's profile/scheme/currency lists, filter a long profile list down to a UD profile, and confirm no create/edit control exists.

**Acceptance Scenarios**:

1. **Given** an empty reference cache, **When** the analyst runs "Sync IRP Metadata", **Then** model profiles (with the data that classifies each as DLM, HD, or Accumulation), output profiles, event-rate schemes, and currencies are fetched from Risk Modeler and stored, and the screen shows when the sync ran.
2. **Given** a populated cache, **When** the analyst opens the analysis-metadata screen, **Then** the four sets appear as four tabs of one page, each listed read-only with per-list filtering, and each model profile shows a DLM, HD, or Accumulation marker.
3. **Given** Risk Modeler is unreachable, **When** the sync runs, **Then** the failure is reported, the previously synced cache remains intact, and the last-synced time is unchanged.
4. **Given** a re-sync after profiles changed in Risk Modeler, **When** it completes, **Then** the lists reflect the current Risk Modeler state (additions appear, removals disappear from the pick lists).

---

### User Story 2 - Create and administer templates and suites (Priority: P2)

An administrator opens the suite-administration page. They create an analysis template by picking a model profile, output profile, and currency from the synced lists; the event-rate scheme field is required when the chosen profile is DLM and optional otherwise, pre-filled when a default is determinable. The builder also surfaces the analysis settings — min loss threshold, number of max-loss events, franchise deductible, unrecognized-occupancy treatment — plus an optional treaty-name pattern and tags. They then compose a suite: name it (the name conveys region and output level, P-03) and add templates in order — mixing DLM, HD, and accumulation templates freely. After a database rebuild, four starter suites (US, Canada, US+Canada, Global — ~10 templates each) are already present to edit rather than build from scratch.

**Why this priority**: This is the feature — the objects the Iteration-7 run flow will execute. It depends on User Story 1's cache for its pick lists.

**Independent Test**: On the administration page, create a DLM template (save blocked until an event-rate scheme is chosen), an HD template (saves without one), and a suite containing both plus an accumulation template; reorder the suite; confirm the starter suites exist and are editable.

**Acceptance Scenarios**:

1. **Given** the synced cache, **When** the admin creates a template, **Then** model profile, output profile, event-rate scheme, and currency are chosen from cached values only (filterable pick lists, no free text for these fields).
2. **Given** a chosen model profile that is DLM, **When** the admin tries to save without an event-rate scheme, **Then** the save is rejected with a message naming the rule; **Given** an HD or Accumulation profile, **Then** the template saves without one.
3. **Given** a saved template, **When** the admin adds it to a suite, **Then** the suite lists it in order, the order can be changed, a per-item portfolio-name override can be set, and the same template cannot be added to the same suite twice.
4. **Given** a suite containing DLM, HD, and accumulation templates, **When** it is saved, **Then** no mixing error is raised.
5. **Given** a template referenced by one or more suites, **When** the admin tries to delete it, **Then** deletion is blocked and the referencing suites are named.
6. **Given** a rebuilt database, **When** anyone views the suite list, **Then** the US, Canada, US+Canada, and Global starter suites are present with their templates.
7. **Given** a signed-in analyst without the admin role, **When** they view the administration page, **Then** templates and suites are visible but create/edit/delete actions are not offered, and direct attempts are rejected.
8. **Given** a re-sync that removed a model profile still referenced by a template, **When** anyone views that template, **Then** it is flagged as referencing a value no longer in Risk Modeler — kept and editable, never silently deleted.

---

### User Story 3 - Move suites between environments (Priority: P3)

An administrator exports suites — with every template they contain — to a spreadsheet file that opens in Excel. In another environment (Ben's build environment → CIC's), an administrator imports that file: existing templates and suites with matching names are updated, new ones are created, and the whole file applies all-or-nothing with every error reported in one pass.

**Why this priority**: Without it, every suite built during development is rebuilt by hand in CIC's environment (design note 14 O14-2). Valuable, but only once Stories 1–2 produce something to move.

**Independent Test**: Export all suites, run `make db-rebuild` (or import into a second environment), import the file, and confirm the suites and templates match the originals field-for-field.

**Acceptance Scenarios**:

1. **Given** existing suites, **When** the admin exports, **Then** the produced Excel workbook contains every selected suite, its ordered items, and the full field set of every template referenced — one sheet per entity kind (templates; suite items).
2. **Given** an export file, **When** it is imported into an environment where none of the names exist, **Then** all templates and suites are created exactly as exported (order, overrides, and every template field preserved).
3. **Given** an export file naming templates/suites that already exist, **When** it is imported, **Then** matching names are updated in place and no duplicates are created; an updated suite's items become exactly the file's items — locally present items absent from the file are removed.
4. **Given** a file with any invalid row (missing required field, duplicate name within the file, DLM template without an event-rate scheme), **When** it is imported, **Then** nothing is applied and every error in the file is reported with its row.
5. **Given** an imported template naming a model profile not present in the local cache, **When** the import completes, **Then** the template is created and flagged as unresolved (the cache may simply not be synced yet), not rejected.

---

### Edge Cases

- Sync runs while another sync is in progress → second request is refused with a "sync already in progress" message; runs never interleave.
- A template's cached profile/scheme/currency disappears or is renamed on re-sync → template flagged unresolved (US2 scenario 8), pick lists show only current values, saved values are never silently rewritten.
- Suite with zero templates → allowed while composing, visibly marked empty (it cannot do anything until Iteration 7 anyway).
- Duplicate template or suite name at save time → rejected with a message (names are the import matching key, P-05).
- An HD template *with* an event-rate scheme → allowed (optional, not forbidden).
- Import file with columns from a newer/older export layout → unknown columns reported as errors (all-or-nothing, P-04).
- Two admins edit the same suite concurrently → last save wins; no locking in this iteration.

## Requirements *(mandatory)*

### Functional Requirements

**Metadata sync & analysis-metadata screen**

- **FR-001**: An on-demand "Sync IRP Metadata" action MUST fetch from Risk Modeler and store locally the four reference sets: model profiles — including the data that classifies each as DLM, HD, or Accumulation — output profiles, event-rate schemes, and currencies.
- **FR-002**: The sync MUST be repeatable: each run replaces the cached sets with the current Risk Modeler state and records when it ran; a failed run MUST leave the previous cache intact and report the failure. A sync requested while another is running MUST be refused with a "sync already in progress" message — runs never interleave.
- **FR-003**: The analysis-metadata screen MUST be a single page with four tabs — model profiles, output profiles, event-rate schemes, currencies — each tab listing its cached set read-only with per-list filtering and the last-synced time; it MUST offer no create or edit for profiles, schemes, or currencies.
- **FR-004**: Each model profile MUST display a DLM, HD, or Accumulation marker derived from its cached data (never chosen by the user), on the metadata screen and in the template builder.

**Analysis templates**

- **FR-005**: An admin MUST be able to create an analysis template with:
  - **Required**: name; model profile; output profile; currency; event-rate scheme (required when the profile is DLM, optional otherwise).
  - **Analysis settings**, surfaced in the builder with defaults: min loss threshold (numeric, two decimal places); number of max-loss events (integer); enable franchise deductible (yes/no); unrecognized occupancy types (one of "Skip location during analysis" or "Treat as unknown").
  - **Optional**: treaty-name pattern, tags.
- **FR-006**: Model profile, output profile, event-rate scheme, and currency MUST be chosen from cached values via filterable pick lists — no free text for these fields. Tags are entered as names (with autocomplete over names already used on templates): Risk Modeler resolves tag names at analysis submit time and creates missing tags, so there is no tag pick list to cache.
- **FR-007**: The event-rate scheme field SHOULD pre-fill automatically when a default is determinable from the chosen model profile; the admin can always change it, and FR-005 validation applies to the saved value.
- **FR-008**: Template names MUST be unique; a duplicate name MUST reject the save with a message.
- **FR-009**: Templates MUST be global (visible to every analyst) with authorship recorded; editing MUST re-apply FR-005/FR-006 validation.
- **FR-010**: Deleting a template referenced by any suite MUST be blocked, naming the referencing suites.
- **FR-011**: A template whose saved profile/scheme/currency value is absent from the current cache MUST be flagged as unresolved — kept and editable, never silently changed or deleted.

**Template suites**

- **FR-012**: An admin MUST be able to create a suite with a unique name and an ordered set of templates; the name conveys region and output level (P-03 — no separate region field); items MUST be re-orderable and MAY carry a per-item portfolio-name override; a template appears at most once per suite.
- **FR-013**: A suite MUST accept DLM, HD, and accumulation templates together — no mixing restriction.
- **FR-014**: Suites MUST be global with authorship recorded; template/suite create-edit-delete and import MUST require the admin role, while viewing, sync, and export are available to every analyst (P-01).
- **FR-015**: Deployment/seeding MUST install four starter suites — US, Canada, US+Canada, Global (~10 templates each, indicative settings per P-02) — editable and deletable like any other suite.

**Export & import**

- **FR-016**: An admin or analyst MUST be able to export suites (selected or all) with their ordered items and the full field set of every referenced template, as a single Excel workbook (.xlsx) with one sheet per entity kind: a templates sheet (full field set) and a suite-items sheet (suite name, order, template name, portfolio-name override). "Export all" additionally writes every template that belongs to no suite to the templates sheet, so a standalone template moves between environments too.
- **FR-017**: An admin MUST be able to import such a file: names matching existing templates/suites update them, new names create them (P-05). An updated suite's item list is replaced wholesale — after import it contains exactly the file's items in the file's order with the file's overrides; locally present items absent from the file are removed.
- **FR-018**: Import MUST validate the whole file and apply it all-or-nothing; on any error (missing required field, value of the wrong type, duplicate name within the file, DLM template without an event-rate scheme, unknown column or missing sheet) it MUST apply nothing and report every error with its sheet and row (P-04).
- **FR-019**: An imported value absent from the local cache (e.g. a profile not yet synced) MUST NOT reject the import; the template is created and flagged unresolved per FR-011.
- **FR-020**: An export followed by an import into an empty environment MUST reproduce every suite and template field-for-field, including item order and overrides.

### Key Entities

- **Analysis template**: one analysis definition ("one row in Analysis Builder") — name, model profile, output profile, event-rate scheme, currency, min loss threshold, number of max-loss events, franchise deductible, unrecognized-occupancy treatment, treaty-name pattern, tags, authorship. Global. (`analysis_template`, `analysis_template_tag` — DATA_MODEL §7.)
- **Template suite**: a named, ordered set of templates; the name conveys region and output level (P-03). Global, admin-maintained, seeded starter set. (`template_suite`.)
- **Suite item**: the ordered membership of a template in a suite, with optional portfolio-name override. (`template_suite_item`.)
- **Analysis reference cache**: locally stored Risk Modeler reference data — model profiles (with the DLM/HD/Accumulation classification), output profiles, event-rate schemes, currencies — plus when it was last synced. (`irp_*` cache tables — PRD §15.2.)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An analyst can answer "which profiles/schemes/currencies exist and which profiles are DLM, HD, or Accumulation" from the workbench alone — zero Risk Modeler visits — once a sync has run.
- **SC-002**: An admin can build a 10-template suite from synced metadata in under 15 minutes.
- **SC-003**: 100% of saved DLM templates carry an event-rate scheme; a save violating the rule is rejected at save time.
- **SC-004**: A suite exported from one environment and imported into another reproduces every template and suite field with zero manual rework (field-for-field diff is empty).
- **SC-005**: The four starter suites are present and openable immediately after a database rebuild, with no manual setup.
- **SC-006**: Every unresolved reference (profile/scheme missing from the cache) is visible on the template it affects — none are silently dropped or altered.

## Assumptions

- The admin role already exists and role-gating a page/action is established capability (PRD §6); no new role is introduced.
- The IRP sandbox exposes enough reference data (profiles, schemes, currencies) to exercise sync and template creation end-to-end.
- The synced model-profile data distinguishes DLM, HD, and Accumulation. HD detection uses the profile's software version (PRD §11.4); how Accumulation profiles are identified is confirmed in the plan.
- Starter-suite contents use indicative settings until Cheryl's US/Canada default-settings list arrives (O14-4); the list changes seeded *data*, not this feature's behavior (P-02).
- The suite *run* flow and treaty resolution at run time happen in Iteration 7; this feature only stores the `treaty_name_pattern` value. Templates carry no auto-name pattern (dropped 2026-08-18) — how Iteration 7 names generated analyses is decided there (O7-3/O14-9).
- Whether a default event-rate scheme is determinable per model profile (FR-007) depends on what the synced reference data carries; the plan resolves it with a spike, and "no pre-fill" is an acceptable outcome — FR-007 is SHOULD, not MUST.
- The analysis settings in FR-005 (min loss threshold, number of max-loss events, franchise deductible, unrecognized occupancy types) open pre-filled with defaults; the specific default values are confirmed in the plan.
- Sync scope here is the analysis reference data (FR-001); the other §15.2 sets (simulation sets, database servers, EDM cache, tags) sync when the feature that consumes them lands. Tags specifically have no list-all read in the integration library today — Risk Modeler resolves and creates tags by name at submit time — so template tags are stored as names (FR-006).
