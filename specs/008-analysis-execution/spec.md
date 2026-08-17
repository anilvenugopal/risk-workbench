# Feature Specification: Analysis Execution (Iteration 6)

**Feature Branch**: `008-analysis-execution`

**Created**: 2026-08-14

**Status**: Draft

**Input**: User description: "The Analysis Execution feature, outlined in the PRD and Functional Requirements; Iteration 6 of the Build Plan in the PRD"

## Review

Iteration 6 lets the analyst run analyses from the workbench. A new **Reference Data page** under the Moody's IRP rail shows the cached model profiles, output profiles, event-rate schemes, and currency schemes, with a per-row link out to Risk Modeler for editing and one **Sync All** button that refreshes the cache. On the EDM summary page the analyst selects one or more portfolios and clicks **Run Analysis**; a **configuration modal** opens with one pending analysis per portfolio — name auto-populated and editable, model profile, output profile, event-rate scheme, currency, currency scheme, treaties from the EDM, and the two exposed toggles. Confirming submits one analysis job per row and the analyst gets a per-row outcome immediately.

Submitted analyses appear in a **User Analyses** section on the EDM summary page — separate from broker-provided analyses — with live status while they run. On completion, loss numbers are retrieved automatically and shown on the analysis detail; broker RDM loss numbers land through the same retrieval machinery, once per RDM.

**Decisions**

| ID | Decision | Status | Source |
|---|---|---|---|
| P-01 | One Reference Data page under the Moody's IRP rail: sections for model profiles, output profiles, event-rate schemes, and currency schemes; per-row Risk Modeler edit links; one Sync All button; per-section last-synced timestamp | Approved | Approver 2026-08-14; PRD §15.2 |
| P-02 | Sync All covers what this iteration consumes: model profiles, output profiles, event-rate schemes, currency schemes, currencies. Simulation sets, tags, and database servers sync when their consuming feature lands; the EDM list keeps its own sync page | Approved | Approver 2026-08-14 |
| P-03 | Launch: portfolio multi-select on the EDM summary page → Run Analysis → configuration modal with one pending analysis per selected portfolio; nothing submits until the analyst confirms | Approved | PRD §10C.1 |
| P-04 | Auto-name = portfolio name + model profile + event-rate scheme, space-separated; the scheme token is omitted for an HD analysis with no scheme; the name regenerates as pick-lists change until the analyst edits it, after which the edit wins. Closes PRD O7-3 | Approved | Approver 2026-08-14 |
| P-05 | Per-analysis fields: model + output profile (filterable, from the cache); event-rate scheme (required for DLM, optional for HD, driven by the model profile); currency (native when the exposure holds exactly one currency, else USD); currency scheme (latest vintage default); treaties (zero or more from the EDM's treaties); franchise-deductible and unrecognized construction/occupancy toggles. Min loss threshold and max loss event stay at defaults, not shown | Approved | PRD §10C.2, §11.1a |
| P-06 | A failed submission stays in the modal with its error for rename/resubmit; rows already submitted stand. The automatic submission-retry batch stays future work | Approved | Approver 2026-08-14; consistent with spec 007 |
| P-07 | Submitted analyses appear in a User Analyses section on the EDM summary page, listed separately from broker-provided analyses, with live status | Approved | PRD §10C.4 |
| P-08 | Loss numbers land this iteration for own **and** broker analyses: retrieved on own-analysis completion, once per RDM for broker analyses; summary metrics stored with row-level data on disk; the analysis detail shows the retrieved summary | Approved | PRD §15.3, §16.1, §17.2; change log 2026-07-23 |
| P-09 | Treaty add/edit remains a Risk Modeler pass-through — hand off to the RM editor, no workbench job; on return the treaty view re-reads from Risk Modeler | Approved | PRD §12.4 |
| T-01 | Submission is synchronous on the request path — one submit call per pending analysis, immediate per-row confirmation or error; everything needed for later result retrieval is captured at submit time | Approved | PRD §14.3; Constitution Art. 11 |
| T-02 | Live status is pushed to the User Analyses section via server-sent events; the existing polling refresh elsewhere (geohaz column) is untouched | Approved | PRD §14.7; spec 007 T-01 |
| O7-1 | Whether HD models need hazard retrieval run ahead of time | Deferred | PRD-owned (Cheryl); hazard lookup stays optional and does not gate analysis |

**In**: the Reference Data page with Sync All and RM edit links; the five-type reference cache; portfolio multi-select + Run Analysis + configuration modal on the EDM summary page; auto-populated editable names (P-04); per-row synchronous submission with modal resubmit on failure; background tracking of analysis jobs; the User Analyses section with live status; automatic loss-number retrieval and storage for own and broker analyses; the result summary on the analysis detail; treaty add/edit pass-through completion.

**Out**: analysis templates and suites, tags per analysis (Iteration 7); grouping (Iteration 8); the broker comparison view (Iteration 9); Loss Repository push and results export (Iteration 10); accumulation analyses (settings unconfirmed, PRD O7-5); the automatic submission-retry batch (P-06); simulation-set / tag / database-server sync (P-02); the job-monitor pages (placeholders remain).

**Exit**: Sync All populates the cache and the Reference Data page shows the four types with RM edit links; select multiple portfolios, configure the batch in the modal (names auto-populated and editable, profiles/scheme/currency/treaties per analysis), and submit in one action; the submitted analyses appear in the User Analyses section with live status; a treaty edit hands off to the RM editor and the refreshed view reflects it; a wedged result-retrieval job is recovered automatically; loss numbers are retrieved and appear on the analysis detail view.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sync and view IRP reference data (Priority: P1)

An analyst opens the Reference Data page from the Moody's IRP rail and sees four sections — model profiles, output profiles, event-rate schemes, currency schemes — each listing the cached rows and when that section was last synced. One **Sync All** click refreshes every section (plus the currency list behind the scenes). A profile or scheme that needs changing is edited in Risk Modeler via the row's link; the workbench never edits reference data.

**Why this priority**: The reference cache feeds every pick-list in the configuration modal — nothing else in this iteration works without it — and the page is independently useful: analysts see the standard profiles and schemes without opening Risk Modeler.

**Independent Test**: On a workbench with an empty cache, click Sync All; confirm all four sections fill with the rows Risk Modeler holds, each section shows a fresh last-synced timestamp, and a row's edit link opens that object in Risk Modeler.

**Acceptance Scenarios**:

1. **Given** an empty reference cache, **When** the analyst clicks Sync All, **Then** model profiles, output profiles, event-rate schemes, and currency schemes are fetched from Risk Modeler and listed in their sections, each with a fresh last-synced timestamp.
2. **Given** a populated page, **When** the analyst follows a row's edit link, **Then** that profile or scheme opens in Risk Modeler; after editing there and re-syncing, the workbench row reflects the change.
3. **Given** a Sync All where one type's fetch fails, **When** the sync completes, **Then** the succeeded sections show fresh rows and timestamps, the failed section reports the error and keeps its previous rows and timestamp, and the page stays usable.
4. **Given** an empty cache, **When** the analyst views the page, **Then** each empty section prompts to sync rather than rendering as an error.

---

### User Story 2 - Configure and launch analyses from the EDM summary page (Priority: P2)

The analyst opens an EDM's summary page, selects one or more portfolios, and clicks **Run Analysis**. A configuration modal opens with one pending analysis per selected portfolio. For each row the analyst picks the model profile and output profile from the synced lists (filterable — typing "UDCT" narrows to the user-defined profiles), an event-rate scheme (mandatory for a DLM profile, optional for HD), the currency (pre-set to the exposure's own currency when it has exactly one, USD otherwise), the currency scheme (pre-set to the latest vintage), zero or more treaties from the EDM's treaty set, and the two toggles — franchise deductible and unrecognized construction/occupancy. The analysis name fills itself in from the portfolio, model profile, and event-rate scheme as they are picked, and can be overwritten. Confirming submits one analysis per row; each row immediately shows submitted or failed-with-reason, and failed rows stay editable for resubmission.

**Why this priority**: This is the iteration — the single-action batch launch that replaces configuring analyses one at a time in Risk Modeler (the design-session pain case was rerunning the same data across 6 portfolios one at a time).

**Independent Test**: With a synced cache and an EDM holding two portfolios and a treaty, select both portfolios, configure both rows in the modal without typing a name, submit, and confirm two analysis jobs were created with the auto-generated names and the chosen settings, with immediate per-row confirmation.

**Acceptance Scenarios**:

1. **Given** an EDM with three portfolios, **When** the analyst selects two and clicks Run Analysis, **Then** the modal opens with exactly two pending analyses, one per selected portfolio.
2. **Given** a pending analysis where the analyst picks model profile `UDCT` and event-rate scheme `EQ_NT_STOCH` on portfolio `FL_Wind_2026`, **When** the picks are made, **Then** the name reads `FL_Wind_2026 UDCT EQ_NT_STOCH`; picking a different scheme regenerates it, but once the analyst types their own name it stops regenerating.
3. **Given** a pending analysis with an HD model profile and no event-rate scheme, **When** the name generates, **Then** the scheme token is omitted and the row is submittable; **Given** a DLM profile and no scheme, **Then** the row cannot be submitted and says why.
4. **Given** an exposure holding exactly one currency, **When** the modal opens, **Then** that currency is pre-selected; **Given** an exposure holding several currencies, **Then** USD is pre-selected — and either default can be changed to any cached currency.
5. **Given** cached currency-scheme vintages, **When** the modal opens, **Then** the vintage with the latest effective date is pre-selected and the others are selectable.
6. **Given** an EDM with treaties, **When** the analyst configures a row, **Then** treaties are picked from the EDM's treaty set only (no free text) and zero treaties is valid; an EDM with no treaties shows an empty picker, not an error.
7. **Given** a confirmed modal of four rows where Risk Modeler rejects the third for a duplicate name, **When** submission finishes, **Then** rows one, two, and four are submitted and stand, row three shows the failure reason, and the analyst renames it and resubmits it from the modal.
8. **Given** Risk Modeler unreachable at row 2 of 5, **When** submission stops, **Then** row 1 stands, rows 2–5 show failed-with-reason and remain fully editable for resubmission; nothing retries in the background.
9. **Given** an EDM with no portfolios, or a reference cache with no model profiles, **When** the analyst tries to launch, **Then** the action is unavailable and points at the missing prerequisite (select a portfolio / sync reference data).

---

### User Story 3 - Watch launched analyses in User Analyses (Priority: P3)

After confirming the modal, the analyst stays on the EDM summary page. A **User Analyses** section lists every analyst-run analysis on this EDM — name, key settings, who submitted it and when, and its live status — separate from the broker-provided analyses section. Status moves from queued through running to finished or failed without the analyst reloading the page.

**Why this priority**: The section is where a launch lands and where the analyst waits out a run; without it they would tab back to Risk Modeler to watch, defeating the point.

**Independent Test**: Launch two analyses, remain on the page, and confirm both appear in User Analyses immediately with their names and settings, their statuses advance without a manual reload, and the broker analyses section is unchanged.

**Acceptance Scenarios**:

1. **Given** a confirmed launch, **When** the modal closes, **Then** the submitted analyses appear in the User Analyses section immediately with their names and submitted status.
2. **Given** running analyses, **When** Risk Modeler advances a job's status, **Then** the User Analyses row updates without a manual page reload.
3. **Given** an analysis Risk Modeler reports as failed, **When** the analyst views the section, **Then** the row shows failed — a visible terminal state, not an error page — and the portfolio can be launched again.
4. **Given** an EDM with broker-provided analyses, **When** analyses are launched, **Then** they never mix into the broker analyses section, and broker analyses never appear under User Analyses.

---

### User Story 4 - Read retrieved loss numbers on the analysis detail (Priority: P4)

When an analysis finishes, the workbench retrieves its loss numbers automatically — no analyst action — and the analysis detail view gains a results summary per financial perspective: average annual loss, standard deviation, maximum event loss, record count, return-period losses, and the OEP/AEP points. Broker analyses gain the same summary: their loss numbers are retrieved once per broker RDM and shown on each of that RDM's analyses. The analyst reads the numbers in the workbench instead of opening Risk Modeler.

**Why this priority**: The numbers are the payoff of a run, but they have no value until launches (US2) and tracking (US3) exist; broker loss numbers were deferred from Iteration 3 to land with this same machinery.

**Independent Test**: Let a launched analysis finish; open its detail view and confirm the summary metrics appear per perspective without any analyst action. Import (or re-sync) a broker RDM and confirm its analyses show the same summary, retrieved once for the RDM.

**Acceptance Scenarios**:

1. **Given** a launched analysis that reaches finished, **When** the analyst opens its detail view, **Then** the results summary shows the retrieved metrics per perspective, with no analyst action in between.
2. **Given** a broker RDM whose analyses were imported, **When** retrieval completes, **Then** each of that RDM's analyses shows the same loss numbers, retrieved once for the RDM, not once per analysis copy.
3. **Given** an analysis whose results are not yet retrieved (still running, retrieval in flight, or retrieval failed), **When** the analyst opens its detail view, **Then** the results area shows a normal not-yet-available state, never an error page.
4. **Given** a retrieval job whose worker dies mid-run, **When** the recovery pass next runs, **Then** the job is picked up again and the results still land without operator intervention.

---

### Edge Cases

- A composed auto-name may exceed Risk Modeler's analysis-name length limit (portfolio names alone run to 40 characters). The limit is confirmed at plan time and the modal enforces it once known; until then an over-long name fails that row at submit, editable and resubmittable like any other submission failure.
- A portfolio renamed or removed in Risk Modeler between page load and submit shows as a visible per-row submission failure (spec 007 precedent), not a crash.
- A profile or scheme deleted in Risk Modeler after the last sync fails the row at submit with Risk Modeler's reason; the analyst re-syncs and resubmits.
- A portfolio with an analysis already running can be launched again — several analyses per portfolio is the normal case (different model profiles), unlike geohaz where re-launch is blocked.
- Two pending rows configured identically (same portfolio, profile, scheme) generate the same name; Risk Modeler's uniqueness rejection fails the second row, which the analyst renames.
- Sync All while a previous Sync All is still running: the page reports the sync in progress rather than starting a second.

## Requirements *(mandatory)*

### Functional Requirements

**Reference data**

- **FR-001**: A Reference Data page under the Moody's IRP rail MUST show four sections — model profiles, output profiles, event-rate schemes, currency schemes — each listing the cached rows and the section's last-synced timestamp.
- **FR-002**: A single Sync All action MUST refresh the cached model profiles, output profiles, event-rate schemes, currency schemes, and currencies in one click, reporting per-type outcomes; a type whose fetch fails keeps its previous rows and timestamp.
- **FR-003**: Each profile and scheme row MUST link out to Risk Modeler for editing; the workbench MUST NOT create or edit reference data.
- **FR-004**: An empty cache MUST render as a prompt to sync — on the page and wherever a pick-list depends on it — never as an error.

**Launch**

- **FR-005**: The EDM summary page MUST support selecting one or more portfolios and launching Run Analysis on the selection; the action requires an EDM with at least one portfolio selected.
- **FR-006**: The configuration modal MUST present one pending analysis per selected portfolio; nothing is submitted until the analyst confirms.
- **FR-007**: Each pending analysis name MUST auto-populate as "portfolio name + model profile + event-rate scheme" (space-separated; scheme omitted when an HD analysis has none), regenerating as picks change until the analyst edits the name, after which the edit is kept.
- **FR-008**: Model profile and output profile MUST be selected per row from the synced cache, with the lists filterable by typed text (e.g. "UDCT" narrows to user-defined profiles).
- **FR-009**: The event-rate scheme MUST be required when the selected model profile is DLM and optional when it is HD, driven by the cached profile's software version; a DLM row without a scheme MUST NOT be submittable and MUST say why.
- **FR-010**: The currency MUST default per row to the exposure's own currency when the exposure holds exactly one, and to USD otherwise; any cached currency MUST be selectable.
- **FR-011**: The currency scheme MUST default to the cached vintage with the latest effective date; other cached vintages MUST be selectable.
- **FR-012**: Treaties MUST be picked per row from the EDM's treaty set only — zero or more, no free-text entry.
- **FR-013**: Each row MUST expose exactly two advanced toggles — franchise deductible and unrecognized construction/occupancy; min loss threshold and max loss event stay at their defaults and are not shown.
- **FR-014**: Confirming the modal MUST submit one analysis to Risk Modeler per pending row, synchronously, with each row's success or failure-with-reason shown immediately.
- **FR-015**: The analysis name MUST be checked against Risk Modeler for uniqueness at submit; a duplicate fails that row with the reason.
- **FR-016**: A failed row MUST stay in the modal, editable, and resubmittable; rows already submitted stand. There is no background submission retry.
- **FR-017**: Everything needed to later retrieve an analysis's results MUST be recorded at submission time — the portfolio's resource identifier is returned only by the submit call and is otherwise unrecoverable.

**Tracking & User Analyses**

- **FR-018**: Submitted analyses MUST be tracked in the background to a terminal status without any analyst action.
- **FR-019**: A User Analyses section on the EDM summary page MUST list the EDM's analyst-run analyses — name, key settings, submitting analyst, submitted-at, status — separately from broker-provided analyses.
- **FR-020**: Status shown in the User Analyses section MUST update without a manual page reload.

**Results**

- **FR-021**: When an analyst-run analysis finishes successfully, its loss numbers MUST be retrieved automatically per financial perspective and stored — summary metrics queryable, row-level records on disk.
- **FR-022**: Broker RDM loss numbers MUST be retrieved through the same machinery, once per RDM, with the stored result shared by every analysis that RDM produced.
- **FR-023**: The analysis detail view MUST show the retrieved summary per perspective — average annual loss, standard deviation, maximum event loss, record count, return-period losses, OEP/AEP points; results not yet available render as a normal empty state, never an error.
- **FR-024**: A retrieval job whose worker dies mid-run MUST be recovered and re-run automatically, without operator intervention.

**Treaties**

- **FR-025**: Adding or editing a treaty MUST hand off to the Risk Modeler editor — no workbench job, no in-app editor; on return, refreshing the treaty view re-reads the treaty from Risk Modeler.

### Key Entities

- **Model profile / output profile / event-rate scheme (cached)**: a reference row synced from Risk Modeler — name, Risk Modeler identity, and (for model profiles) the software-version marker that decides DLM vs HD; read-only in the workbench.
- **Currency scheme (cached)**: an exchange-rate vintage — scheme code, vintage label, effective date; the latest effective date is the analysis default. **Currency (cached)**: the selectable currency codes.
- **Pending analysis**: a modal row — portfolio, name, model profile, output profile, event-rate scheme, currency, currency scheme, treaties, two toggles. Exists only until submitted or abandoned.
- **Analysis job**: one submission to Risk Modeler — status lifecycle from submitted to finished/failed, plus the resource identifier captured at submit for result retrieval.
- **User analysis**: an analyst-run analysis on an EDM, distinguished from a broker analysis by having no broker-RDM source.
- **Analysis result summary**: one record per (own analysis or broker RDM, financial perspective) — the summary metrics plus pointers to the row-level files on disk.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: One click refreshes all reference data; the four displayed types show current Risk Modeler rows and fresh last-synced timestamps.
- **SC-002**: An analyst launches analyses on 6 portfolios in one modal pass in under 2 minutes, typing zero analysis names (the design-session pain case was 6 portfolios configured one at a time).
- **SC-003**: Auto-generated names are unique within any batch whose rows differ in portfolio, model profile, or event-rate scheme.
- **SC-004**: A launched analysis appears in User Analyses immediately, and a status change in Risk Modeler reaches the page within 30 seconds without a reload.
- **SC-005**: The loss numbers of every successfully completed analysis — analyst-run and broker — are readable in the workbench without opening Risk Modeler.
- **SC-006**: A mid-batch submission failure loses no work: submitted rows stand, and each failed row is resubmittable from the modal with at most the fix plus one click.

## Assumptions

- The poller, the app-side job queue with its claim/heartbeat/reconciler recovery, and worker auto-discovery already exist (Iterations 2–5) and are reused; this spec adds the analysis job type to the poller and the result-retrieval worker to the queue, not new infrastructure.
- Treaty viewing and the "Edit in Risk Modeler" deep-link already exist on the EDM summary page; FR-025 closes only what is missing (an add-treaty link and the on-return refresh, if absent).
- The currency default (FR-010) reads the exposure currency set the workbench already detects; widening detection to every currency-bearing level (PRD O8-2) belongs to the summary page, not this feature.
- The modal offers catastrophe (portfolio) analyses only; accumulation analyses stay out until their settings are confirmed (PRD O7-5). Tags are not exposed — templates own tags (Iteration 7); analyses submit without tags.
- The job-monitor pages stay placeholders; monitoring in this iteration is the User Analyses section.
- Risk Modeler's analysis-name length limit is confirmed at plan time; the modal enforces it once known.
- Any authenticated user may launch analyses and sync reference data; no role gate (spec 007 precedent — roles gate functions, and none is defined for this).
- Analyses launched by any analyst appear in User Analyses — "user" means analyst-run (not broker-provided), not "mine only".
