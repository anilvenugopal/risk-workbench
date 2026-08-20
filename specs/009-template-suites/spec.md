# Feature Specification: Analysis Templates & Template Suites — Definition & Administration (Iteration 6)

**Feature Branch**: `009-template-suites`

**Created**: 2026-08-17

**Status**: Draft

**Input**: User description: "Analysis templates and template suites — definition and administration (PRD §11, Iteration 6; design note 14 D9–D14). An analysis template is one analysis definition ('one row in Analysis Builder'): analysis/model profile + output profile + event-rate scheme (auto-populated; required for DLM, optional for HD) + currency, plus optional settings. A template suite is an ordered set of templates, defined primarily by region + output level; suites may mix DLM, HD, and accumulation templates. Suites are predefined/admin-maintained, not freeform user-built: an administration surface, seeded starter suites (US, Canada, US+Canada, global — ~10 templates each), and CSV/Excel export + import. Also in scope: IRP metadata sync (§15.2) and an analysis-metadata screen. OUT of scope: analysis submission/execution, the suite run flow, grouping, results — those are Iteration 7."

## Review summary

**What the user can do when this ships.** An analyst opens the analysis-metadata screen — one page with five tabs — and sees the Risk Modeler reference data that configures an analysis: model profiles (each marked DLM, HD, or Accumulation), output profiles, event-rate schemes, currencies, and currency schemes with their vintages — synced on demand into the workbench, filterable, read-only (profiles are created and edited in Risk Modeler, never here). An administrator opens the suite-administration page and creates **analysis templates** (one analysis definition: model profile + output profile + event-rate scheme + analysis settings — no currency: the currency block is chosen at analysis submit time in Iteration 7, P-11) and composes them into **template suites** (a named, unordered set of templates). A Duplicate button on any template or suite saves an identical copy named `<name> (copy)` and opens it for editing (P-12).

**What this feature does NOT do.** No analysis is submitted or executed. The suite *run* flow (pick portfolios + treaties, pick the suite, go; expand-to-deselect), grouping, and results are Iteration 7 (PRD §11.3a, §21). Currency selection — analysis currency, currency scheme, and scheme vintage, chosen at the suite level with overridable env-var defaults — happens at submit time in Iteration 7 too (design note 17 D4–D7); templates and suites store no currency values. The workbench never creates or edits profiles, schemes, or currencies in Risk Modeler. No content is seeded and there is no Excel export/import — initial setup is manual via the admin page; the Excel-based transfer flow is a nice-to-have enhancement (P-02, design retained in `contracts/transfer-workbook.md`).

**Business rules that shape the design** (design note 14, D10/D11/D14):

- A template is "one row in Analysis Builder"; a suite is a set of templates — **unordered** ("it's just a group… that we can run all together", design note 16 §2.1) — conceived by **region + output level**, both conveyed by the suite's name, not stored as separate fields (P-03). Suites are how CIC enforces consistent settings — predefined and administered, not freeform user-built.
- A DLM template **requires** an event-rate scheme; for an HD or Accumulation profile it is **optional**. The DLM/HD/Accumulation classification comes from the cached model profile, not from the user.
- A suite **may mix** DLM, HD, and accumulation templates (US wildfire is HD-only; Japan has DLM and HD suites). Keeping DLM and accumulation apart is a convention, not an enforced rule.
- Templates and suites are **global** — every analyst sees every template and suite; authorship is recorded but grants nothing.

**Decisions**:

| ID | Decision | Status |
|---|---|---|
| P-01 | Template/suite create-edit-delete is gated to the **admin role**; every analyst can view templates, suites, and metadata, and any analyst can run the metadata sync. | Approved |
| P-02 | **Excel export/import and starter-suite seeding are out of MVP scope** — nothing is seeded and there is no transfer file; initial setup (including any starter suites) is manual via the admin page. The Excel-based flows are a nice-to-have enhancement; the worked design is retained in `contracts/transfer-workbook.md`. | Deferred 2026-08-19 — reaffirmed 2026-08-20 (note 17 D9: revisit ~next month; duplicate-and-edit P-12 is the near-term path) |
| P-03 | Region is **not** a stored attribute anywhere — the suite's or template's name identifies its region (and output level). Resolves O14-3: no suite-level region field, and no `region_label`/`peril_code` on templates (dropped 2026-08-18 with auto-naming). | Approved |
| P-06 | Metadata sync stores at most the first 16 characters of each currency name, matching Risk Modeler's create-currency limit even when legacy currency names exceed it. | Approved — **reinstated 2026-08-18** (briefly superseded the same day when currencies were thought droppable; P-07 as amended keeps the currency cache) |
| P-07 | The workbench caches **all three currency objects — currencies, currency schemes, and currency-scheme vintages** — for the metadata screen and for Iteration 7's submit-time currency picker: the submit-time currency block is `{code, scheme, vintage, asOfDate}`, with `asOfDate` derived from the chosen vintage's effective date, and the picker's lists read this cache. Schemes are a display unit (design note 16 D3: CIC works in schemes; a currency appears in several schemes with different FX rates): the metadata screen lists currency schemes with their vintages (tab order: model profiles, output profiles, event-rate schemes, currencies, currency schemes — the Currencies tab stays alongside Currency Schemes, reversed 2026-08-19, user-corrected). The scheme and vintage reads shipped in `irp-integration==0.6.0rc2` (released & pinned 2026-08-19). Nothing on a template references the currency cache (P-11). | Approved 2026-08-18; template storage removed 2026-08-20 (P-11) |
| P-08 | Suites are **unordered** (design note 16 §2.1): suite items carry no position/order and no per-item portfolio-name override — a suite is a plain set of templates. | Approved 2026-08-18 |
| P-09 | **Treaty-name pattern is dropped** from templates (design note 16 D11/O15-6): treaties are selected explicitly at run time in Iteration 7, never stored as a pattern on a template. | Approved 2026-08-18 |
| P-11 | **Currency is removed from templates and suites entirely** (design note 17 D4/D5/D7, reversing P-10 — the full history of the three currency flips lives in research.md R13): analysis currency, currency scheme, and scheme vintage are chosen at analysis **submit time** in Iteration 7 — at the suite level, pre-filled from overridable env-var defaults (USD; latest RMS scheme; most-recent currently-effective vintage) — never stored as template or suite configuration, so templates never go stale when a new scheme or vintage releases and CIC, not the system, decides when the default flips. `analysis_template` drops `currency_code`, `currency_scheme_code`, and `currency_vintage`; the builder loses its three currency fields. The currency cache and both metadata tabs stay (P-07). | Approved 2026-08-20 |
| P-12 | **Duplicate-and-edit** (design note 17 D9, the near-term path while Excel stays deferred, P-02): every template and suite detail page offers an admin-only **Duplicate** action that immediately saves an identical copy — a template copy repeats every field value and tag; a suite copy contains the same templates (membership copied; the templates themselves are shared, never deep-copied) — named `<name> (copy)`, with a counter on collision (`<name> (copy 2)`) and the base name truncated when needed to fit 200 characters, then opens the copy's edit screen. Model swaps are done by duplicating the *template*, editing the copy, and swapping it into the suite. | Approved 2026-08-20 (user-confirmed: save-immediately, then edit) |

**How to verify.** Sync metadata against the IRP sandbox and see profiles/schemes/currency schemes on the metadata screen; create a DLM template (event-rate scheme enforced) and an HD template (optional); compose a mixed suite; duplicate the suite and remove a template from the copy.

## Clarifications

### Session 2026-08-18 (design session, note 16)

- Currency selection is **scheme-first** (D3 → P-07): the metadata screen shows currency schemes (with vintages), not a standalone currency list. *Amended later the same day:* all **three** currency objects — currencies, currency schemes, and scheme vintages — are cached and stored on templates, because analysis submission requires a specific value for each (`{code, scheme, vintage, asOfDate}`). The scheme/vintage reads are being finished in irp-integration separately. *Reversed 2026-08-19 (user-corrected):* the metadata screen keeps its standalone Currencies tab too — D3's "not tabbed" call didn't hold once it was built and seen.
- Suites are **unordered**; the `Position` and `Portfolio Name Override` suite-item concepts are dropped (P-08).
- The optional **treaty-name pattern** template field is dropped (D11 → P-09); treaties are picked at run time in Iteration 7.
- Q: When a sync is requested while another sync is running, is the second request rejected, queued, or coalesced? → A: Rejected — refused with a "sync already in progress" message; never interleaved, never queued.

### Session 2026-08-19

- Q: Are currency scheme and vintage required on every template? → A: **Yes — both required** (P-10; initially ruled an optional pair, reversed later the same day). NULL is never stored for either, and no default-resolution logic runs at submit time — every template carries a concrete currency, scheme, and vintage, and Iteration 7 submits them as stored (the submission API never defaults — confirmed: values must always be provided). The vintage is pre-selected to the chosen scheme's latest by effective date; a scheme with no vintages blocks the save.
- Q: Do the currency pick lists query Risk Modeler live as the user types? → A: No — they filter the **local cache** with substring matching (LIKE semantics), like every other pick list (FR-006). The Risk Modeler `where_clause` reads are exact-match lookups, wrong for type-ahead; they belong to the sync (`isActive=True` filters).
- Q: Is currency-in-scheme membership validated (the scheme must carry an FX rate for the chosen currency)? → A: Not now — deliberately deferred (P-10). Currency is a minor part of this feature; the admin is assumed to have set up the vintage correctly. A mispairing fails at submit time in Iteration 7.
- Excel export/import and starter-suite seeding are **out of MVP scope** (P-02): initial setup is manual; the Excel flows are a nice-to-have enhancement whose design is retained in `contracts/transfer-workbook.md`.

*The currency answers in this session were superseded 2026-08-20 (note 17 D4 → P-11): templates no longer store any currency value.*

### Session 2026-08-20 (design session, note 17)

- **Currency is removed from templates entirely** (D4 → P-11, reversing P-10 and the template-storage half of P-07): analysis currency, scheme, and vintage become submit-time parameters in Iteration 7, applied at the suite level with env-var defaults. The currency cache and both metadata tabs stay — they serve the metadata view now and the Iteration-7 picker later.
- **Duplicate-and-edit** (D9 → P-12). Q: Does Duplicate save immediately or open a pre-filled unsaved form? → A: Saves immediately — the copy exists as soon as the button is pressed — then its edit screen opens. Q: Naming convention? → A: `<name> (copy)`, counter appended on collision.
- Name uniqueness (D10) was confirmed as already built (FR-008/FR-012, filtered unique indexes on live rows); no-suites-of-suites (D8) confirms the existing one-level `template_suite` → `template_suite_item` shape. Neither changes a requirement.
- The event-rate-scheme pick list must populate as soon as a model profile is chosen — the 8/20 demo showed it blank until a character was typed (O17-9). Fixed in this feature under FR-007.
- Excel import/export stays deferred (P-02), revisit ~next month; duplicate-and-edit plus manual setup is the go-live path (D9/D11 — direct SQL edits of non-validated fields are an ops affordance, no code here).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sync and view analysis metadata (Priority: P1)

An analyst (or admin preparing to build templates) clicks "Sync IRP Metadata". The workbench fetches the analysis reference data from Risk Modeler — model profiles, output profiles, event-rate schemes, and the currency data (currencies, currency schemes, and scheme vintages) — and stores it locally. The analysis-metadata screen is a single page with five tabs; the fourth lists currencies, the fifth lists currency schemes with their vintages (P-07, reversed 2026-08-19 to keep both). Each tab lists its set filterable ("just get to UDCT"), each model profile carries a DLM, HD, or Accumulation marker, and the last-synced time is shown. Everything is read-only: these are created and edited in Risk Modeler and synced back — the same "selected, not owned" pattern as EDM data.

**Why this priority**: Every other capability in this feature consumes this cache — the template builder's dropdowns and DLM/HD detection read it. It also has standalone value: today the analyst opens Risk Modeler just to check what profiles exist.

**Independent Test**: Run the sync against the IRP sandbox; open the metadata screen and confirm the five tabs match Risk Modeler's profile/scheme/currency/currency-scheme lists, filter a long profile list down to a UD profile, and confirm no create/edit control exists.

**Acceptance Scenarios**:

1. **Given** an empty reference cache, **When** the analyst runs "Sync IRP Metadata", **Then** model profiles (with the data that classifies each as DLM, HD, or Accumulation), output profiles, event-rate schemes, currencies, currency schemes, and scheme vintages are fetched from Risk Modeler and stored, and the screen shows when the sync ran.
2. **Given** a populated cache, **When** the analyst opens the analysis-metadata screen, **Then** the five sets appear as five tabs of one page, each listed read-only with per-list filtering, and each model profile shows a DLM, HD, or Accumulation marker.
3. **Given** Risk Modeler is unreachable, **When** the sync runs, **Then** the failure is reported, the previously synced cache remains intact, and the last-synced time is unchanged.
4. **Given** a re-sync after profiles changed in Risk Modeler, **When** it completes, **Then** the lists reflect the current Risk Modeler state (additions appear, removals disappear from the pick lists).

---

### User Story 2 - Create and administer templates and suites (Priority: P2)

An administrator opens the suite-administration page. They create an analysis template by picking a model profile and an output profile from the synced lists; the event-rate scheme field is required when the chosen profile is DLM and optional otherwise, its pick list populated with the profile's peril/region matches as soon as the profile is chosen, pre-filled when a default is determinable. The builder also surfaces the analysis settings — min loss threshold, number of max-loss events, franchise deductible, unrecognized-occupancy treatment — plus optional tags. There are no currency fields: the currency block is chosen at submit time in Iteration 7 (P-11). They then compose a suite: name it (the name conveys region and output level, P-03) and add templates — an unordered group, mixing DLM, HD, and accumulation templates freely. To create a variant, they press **Duplicate** on an existing template or suite: an identical copy named `<name> (copy)` is saved and its edit screen opens (P-12) — duplicate the global suite and remove the US templates, or duplicate a template and swap its model profile. Suites are built by hand: nothing is seeded (P-02).

**Why this priority**: This is the feature — the objects the Iteration-7 run flow will execute. It depends on User Story 1's cache for its pick lists.

**Independent Test**: On the administration page, create a DLM template (save blocked until an event-rate scheme is chosen), an HD template (saves without one), a suite containing both plus an accumulation template, and a duplicate of the suite with one template removed.

**Acceptance Scenarios**:

1. **Given** the synced cache, **When** the admin creates a template, **Then** model profile, output profile, and event-rate scheme are chosen from cached values only (filterable pick lists, no free text for these fields), and the form offers no currency, currency-scheme, or vintage field (P-11).
2. **Given** a chosen model profile that is DLM, **When** the admin tries to save without an event-rate scheme, **Then** the save is rejected with a message naming the rule; **Given** an HD or Accumulation profile, **Then** the template saves without one.
3. **Given** a saved template, **When** the admin adds it to a suite, **Then** the suite lists it, and the same template cannot be added to the same suite twice.
4. **Given** a suite containing DLM, HD, and accumulation templates, **When** it is saved, **Then** no mixing error is raised.
5. **Given** a template referenced by one or more suites, **When** the admin tries to delete it, **Then** deletion is blocked and the referencing suites are named.
6. **Given** a signed-in analyst without the admin role, **When** they view the administration page, **Then** templates and suites are visible but create/edit/delete actions are not offered, and direct attempts are rejected.
7. **Given** a re-sync that removed a model profile still referenced by a template, **When** anyone views that template, **Then** it is flagged as referencing a value no longer in Risk Modeler — kept and editable, never silently deleted.
8. **Given** an existing template, **When** the admin presses Duplicate, **Then** a copy named `<name> (copy)` is saved with identical settings and tags and its edit screen opens; duplicating the same template again yields `<name> (copy 2)` (P-12).
9. **Given** an existing suite, **When** the admin presses Duplicate, **Then** a copy named `<name> (copy)` is saved containing the same templates (the templates themselves are not copied) and its edit screen opens; removing a template from the copy leaves the original suite unchanged.

---

### Deferred: Move suites between environments (Excel export/import)

Out of MVP scope (P-02). Moving suites between environments (design note 14 O14-2) is done manually for now; the Excel export/import flow — including reference-data dropdowns in the workbook — is a nice-to-have enhancement whose worked design is retained in `contracts/transfer-workbook.md`.

---

### Edge Cases

- Sync runs while another sync is in progress → second request is refused with a "sync already in progress" message; runs never interleave.
- Any of a template's three saved reference values (the FR-011 list) disappears or is renamed on re-sync → template flagged unresolved (US2 scenario 7), pick lists show only current values, saved values are never silently rewritten.
- Suite with zero templates → allowed while composing, visibly marked empty (it cannot do anything until Iteration 7 anyway).
- Duplicate template or suite name at save time → rejected with a message.
- An HD template *with* an event-rate scheme → allowed (optional, not forbidden).
- Duplicate of a template or suite whose name is near the 200-character limit → the base name is truncated so `<name> (copy N)` fits; the copy still saves.
- Duplicate of an unresolved template → allowed; values copy as-is and the copy carries the same unresolved flags (FR-011).
- Duplicate pressed by a non-admin (direct POST) → rejected like every other mutation (P-01).
- Two admins edit the same suite concurrently → last save wins; no locking in this iteration.

## Requirements *(mandatory)*

### Functional Requirements

**Metadata sync & analysis-metadata screen**

- **FR-001**: An on-demand "Sync IRP Metadata" action MUST fetch from Risk Modeler and store locally the analysis reference sets: model profiles — including the data that classifies each as DLM, HD, or Accumulation (the accumulation-profile read is tabled in irp-integration, plan T-02; until it ships the sync ingests no Accumulation rows and synced profiles classify DLM/HD) — output profiles, event-rate schemes, currencies, currency schemes, and currency-scheme vintages (P-07 — the three currency sets feed the metadata screen now and Iteration 7's submit-time currency picker later).
- **FR-002**: The sync MUST be repeatable: each run replaces the cached sets with the current Risk Modeler state and records when it ran; a failed run MUST leave the previous cache intact and report the failure. A sync requested while another is running MUST be refused with a "sync already in progress" message — runs never interleave.
- **FR-003**: The analysis-metadata screen MUST be a single page with five tabs — model profiles, output profiles, event-rate schemes, currencies, currency schemes, in that order — each tab listing its cached set read-only with per-list filtering and the last-synced time; the currency-schemes tab lists each scheme with its vintages (vintage code and effective date); it MUST offer no create or edit for profiles or schemes. Each tab links to its Risk Modeler screen ("Open in Risk Modeler", new tab) where one exists — paths and tenant-base rule in contracts/routes.md.
- **FR-004**: Each model profile MUST display a DLM, HD, or Accumulation marker derived from its cached data (never chosen by the user), on the metadata screen and in the template builder. The derivation and display of all three values ship now, but the Accumulation value depends on the tabled accumulation read (FR-001): until it lands, no synced profile shows Accumulation. A profile whose cached data carries no software version shows no marker — the classification is unknown and the DLM scheme-required rule does not apply.

**Analysis templates**

- **FR-005**: An admin MUST be able to create an analysis template with:
  - **Required**: name; model profile; output profile; event-rate scheme (required when the profile is DLM, optional otherwise; when both the chosen profile and scheme are present in the cache, the scheme's peril/region must match the profile's — the same rule Risk Modeler enforces at submit). A template stores no currency value: the submit-time currency block (`{code, scheme, vintage, asOfDate}`) is supplied at analysis submit in Iteration 7 (P-11).
  - **Analysis settings**, surfaced in the builder with defaults: min loss threshold (numeric, two decimal places); number of max-loss events (integer); enable franchise deductible (yes/no); unrecognized occupancy types (one of "Skip location during analysis" or "Treat as unknown").
  - **Optional**: tags. (Treaty-name pattern dropped, P-09.)
- **FR-006**: Model profile, output profile, and event-rate scheme MUST be chosen from cached values via filterable pick lists — no free text for these fields, and filtering is substring matching over the local cache, never a live Risk Modeler query per keystroke (2026-08-19 clarification). Tags are entered as semicolon-separated names — the first tag autocompletes over names already used on templates: Risk Modeler resolves tag names at analysis submit time and creates missing tags, so there is no tag pick list to cache.
- **FR-007**: The event-rate scheme pick list MUST populate with the chosen model profile's peril/region matches as soon as the profile is chosen — no keystroke needed (O17-9, 2026-08-20) — and SHOULD pre-fill automatically when a default is determinable; the admin can always change it, and FR-005 validation applies to the saved value.
- **FR-008**: Template names MUST be unique; a duplicate name MUST reject the save with a message.
- **FR-009**: Templates MUST be global (visible to every analyst) with authorship recorded; editing MUST re-apply FR-005/FR-006 validation.
- **FR-010**: Deleting a template referenced by any suite MUST be blocked, naming the referencing suites.
- **FR-011**: A template whose saved reference value — model profile, output profile, or event-rate scheme — is absent from the current cache MUST be flagged as unresolved — kept and editable, never silently changed or deleted.

**Template suites**

- **FR-012**: An admin MUST be able to create a suite with a unique name and an unordered set of templates (P-08 — no item order, no per-item settings); the name conveys region and output level (P-03 — no separate region field); a template appears at most once per suite.
- **FR-013**: A suite MUST accept DLM, HD, and accumulation templates together — no mixing restriction.
- **FR-014**: Suites MUST be global with authorship recorded; template/suite create-edit-delete MUST require the admin role, while viewing and sync are available to every analyst (P-01).

*FR-015 – FR-020 (starter-suite seeding, Excel export & import) are out of MVP scope — deferred as a nice-to-have enhancement (P-02); the export/import design is retained in `contracts/transfer-workbook.md`.*

**Duplicate**

- **FR-021**: An admin MUST be able to duplicate any template or suite (P-12): the Duplicate action immediately saves an identical copy — a template copy repeats every field value and tag; a suite copy contains the same templates (membership copied; templates shared, never deep-copied) — named `<name> (copy)`, with a counter appended on name collision (`<name> (copy 2)`) and the base name truncated when needed to fit the 200-character limit, then opens the copy's edit screen. Duplicate requires the admin role (P-01).

### Key Entities

- **Analysis template**: one analysis definition ("one row in Analysis Builder") — name, model profile, output profile, event-rate scheme, min loss threshold, number of max-loss events, franchise deductible, unrecognized-occupancy treatment, tags, authorship. No currency values (P-11). Global. (`analysis_template`, `analysis_template_tag` — DATA_MODEL §7.)
- **Template suite**: a named, unordered set of templates; the name conveys region and output level (P-03). Global, admin-maintained. (`template_suite`.)
- **Suite item**: the membership of a template in a suite. (`template_suite_item`.)
- **Analysis reference cache**: locally stored Risk Modeler reference data — model profiles (with the DLM/HD/Accumulation classification), output profiles, event-rate schemes, currencies, currency schemes, currency-scheme vintages — plus when it was last synced. (`irp_*` cache tables — PRD §15.2.)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An analyst can answer "which profiles/schemes/currency schemes exist and which profiles are DLM, HD, or Accumulation" from the workbench alone — zero Risk Modeler visits — once a sync has run.
- **SC-002**: An admin can build a 10-template suite from synced metadata in under 15 minutes.
- **SC-003**: 100% of saved DLM templates carry an event-rate scheme; a save violating the rule is rejected at save time.
- **SC-006**: Every unresolved reference (profile/scheme missing from the cache) is visible on the template it affects — none are silently dropped or altered.
- **SC-007**: Duplicating a template or suite yields a saved copy identical to the original except its name — zero re-entry of settings — immediately open for editing.

*SC-004 (Excel export/import round-trip) and SC-005 (starter suites present after a rebuild) were removed with the P-02 deferral; the numbering gap is intentional.*

## Assumptions

- The admin role already exists and role-gating a page/action is established capability (PRD §6); no new role is introduced.
- The IRP sandbox exposes enough reference data (profiles, schemes, currency schemes) to exercise sync and template creation end-to-end.
- The currency-scheme and scheme-vintage reads (`search_currency_schemes`, `search_currency_scheme_vintages`, `get_latest_currency_scheme_vintage`) shipped in `irp-integration==0.6.0rc2`, released and pinned 2026-08-19 (the same cross-repo pattern as the T-06 validation utility). The already-shipped currency read/cache stays; the sync adds schemes and vintages on top.
- The synced model-profile data distinguishes DLM and HD; HD detection uses the profile's software version (PRD §11.4). Accumulation profiles arrive via a separate irp-integration read that is **tabled** (plan T-02, deferred 2026-08-18): until it ships, the sync ingests no Accumulation rows, no synced profile carries the Accumulation marker, and the marker's Accumulation branch is exercised only by tests.
- The suite *run* flow and treaty selection happen at run time in Iteration 7; templates store no treaty-name pattern (dropped 2026-08-18, P-09/O15-6) and no auto-name pattern (dropped 2026-08-18) — how Iteration 7 names generated analyses is decided there (O7-3/O14-9).
- Whether a default event-rate scheme is determinable per model profile (FR-007) depends on what the synced reference data carries; the plan resolves it with a spike, and "no pre-fill" is an acceptable outcome — the pre-fill half of FR-007 is SHOULD, not MUST.
- Submit-time currency selection — suite-level application, env-var defaults (USD; latest RMS scheme; most-recent currently-effective vintage), CIC-controlled default flips, regional suites for mixed-currency books — is Iteration-7 scope (design note 17 D4–D7); this feature only keeps the currency cache that picker will read.
- The analysis settings in FR-005 (min loss threshold, number of max-loss events, franchise deductible, unrecognized occupancy types) open pre-filled with defaults; the specific default values are confirmed in the plan.
- Sync scope here is the analysis reference data (FR-001); the other §15.2 sets (simulation sets, database servers, EDM cache, tags) sync when the feature that consumes them lands. Tags specifically have no list-all read in the integration library today — Risk Modeler resolves and creates tags by name at submit time — so template tags are stored as names (FR-006).
