# Feature Specification: GeoHaz Execution (Iteration 5)

**Feature Branch**: `007-geohaz-execution`

**Created**: 2026-08-12

**Status**: Draft

**Input**: User description: "Iteration 5 of the PRD -- geohaz execution. This should be created as spec 007"

## Review

Iteration 5 adds the workbench's first Risk Modeler *operation* on existing exposure: **hazard lookup (GeoHaz)**, launched from the EDM summary page. The analyst selects one or more portfolios and clicks Run hazard lookup; the workbench submits the standard DLM parameter set without opening a modal, submits one geohaz job per selected portfolio, tracks each through the existing poller, shows in-line per-portfolio status on the summary page, and on completion records and displays Risk Modeler's completion summary. Hazard lookup is **hazard lookup only** — geocoding is never re-run — and it is **optional**: no analysis gate requires it.

The page shows Risk Modeler's stored `hazardVersion` for each portfolio and never uses the value to gate an action. The analyst also sees a running hazard job's status and the most recent run's parameters and result.

**Decisions**

| ID | Decision | Status | Source |
|---|---|---|---|
| P-01 | Hazard lookup launches from the EDM summary page against one or more selected portfolios; one geohaz job per portfolio, one parameter set per launch | Approved | Design session 2026-08-07; PRD §10B.1 |
| P-02 | Hazard lookup is one click with no modal. Every launch uses the configured hazard data version, DLM, earthquake + windstorm, skip locations with previous hazard lookup = no, and overwrite user-defined hazard values = yes | Approved | Design session 2026-08-14, D4; briefly changed to the literal `latest` 2026-08-19, reverted same day — Risk Modeler rejects `latest` (research R6) |
| P-03 | The portfolios table displays the raw `hazardVersion` returned by Get Portfolio Metadata; the value does not gate an action | Approved | Approver direction, 2026-08-17; supersedes the 2026-08-07 decision |
| P-04 | Hazard lookup is optional and never an analysis prerequisite; the launch gate is EDM + ≥1 portfolio | Approved | PRD §10B.5, §13.1 |
| P-05 | Settles PRD O8-3 — what the workbench saves per lookup and shows for the most recent run. Saved at submit: the parameter set, launching analyst, and submit timestamp. Saved at completion: terminal status, completion timestamp, and the `tasks[].output.summary` string. The portfolio details display the saved summary string without parsing it | Approved | Updated from captured response and approver direction 2026-08-13 |
| P-06 | A portfolio with a non-terminal geohaz job cannot be included in a new launch; its row shows the running job's status | Approved | This spec (prevents accidental double submission); approved 2026-08-12 |
| P-07 | Display: the portfolios table's final column is **"Hazard Version"** — the job's in-line status while a lookup is non-terminal, then the portfolio's raw stored `hazardVersion`. An absent or empty value displays empty. The expanded portfolio row shows a right-hand column for the most recent run's Data Version, Model Family, Hazard Layers, both hazard lookup options, and Result (`completion_summary`) | Approved | Approver direction, 2026-08-17; supersedes the 2026-08-13 terminal states |
| T-01 | Per-portfolio status refreshes by polling the workbench; SSE live push is Iteration 6 | Approved | PRD §10B.4, §14.7 |
| O7-1 | Whether HD models need hazard retrieval run ahead of time | Deferred | PRD-owned (Cheryl); defaults are DLM and lookup is optional, so it does not gate this feature |

**In**: one-click multi-portfolio launch with the standard DLM parameters; one geohaz job per portfolio tracked by the poller; a final "Hazard Version" column in the portfolios table (in-line job status / stored `hazardVersion`) with the per-lookup detail in the expanded portfolio row; Risk Modeler's completion summary string; the EDM + portfolio prerequisite gate.

**Out**: analysis execution, grouping, results (Iteration 6+); geocoding (never a workbench action); SSE live job status (Iteration 6 — polling refresh suffices); enhanced risk data (PRD O7-2, not used today); portfolio deletion.

**Exit** (PRD §21): select two portfolios on the EDM summary page and run hazard lookup with the default parameters; both jobs are tracked via the poller with in-line per-portfolio status; on completion the Risk Modeler summary string is shown and each portfolio shows it has been hazard-looked-up through the workbench; the gate requires a portfolio before geohaz is enabled.

## Clarifications

### Session 2026-08-13

- Q: FR-006 promises recovery "through the existing submission-failure retry machinery", but that retry batch was never built (poller stub). What is the recovery path for a failed submission? → A: Manual relaunch — a submission failure is terminal and visible, and the portfolio is immediately launchable again; the automatic retry batch stays future work.
- Q: Who is allowed to launch hazard lookup (roles gate functions per the constitution)? → A: Any authenticated user; no role gate — the lookup record captures the launching analyst.
- Q: Is launching allowed while the EDM is busy (mid-import or portfolio sync running), when portfolio rows can change under the selection? → A: Yes — the gate stays EDM + ≥1 portfolio; a portfolio renamed or removed before submit surfaces as a visible failed submission, relaunchable.

### Session 2026-08-14

- Q: Does the analyst need to review or change DLM hazard parameters? → A: No. Remove the modal and launch in one click with the standard parameters: first configured data version, DLM, earthquake + windstorm, skip previous hazard off, and overwrite user-defined values on.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Launch hazard lookup on selected portfolios (Priority: P1)

An analyst working an EDM opens its summary page, selects one or more portfolios, and clicks Run hazard lookup. The workbench uses the configured hazard data version, DLM, earthquake + windstorm, skip locations with previous hazard lookup off, and overwrite user-defined hazard values on. One parameter set applies to every selected portfolio. The workbench submits one geohaz job per portfolio and confirms in the same interaction; the analyst never opens Risk Modeler. Broker geocoding is preserved.

**Why this priority**: The launch is the iteration — everything else (status and latest-run details) exists to track what the launch submitted. It is the from-screen action the 2026-08-07 design session approved ("Ability to execute hazard lookup from the screen — yes").

**Independent Test**: On an EDM with two or more portfolios, select two, click Run hazard lookup once, and confirm two geohaz jobs were created — one per portfolio, same standard parameter set — with immediate confirmation and no Risk Modeler interaction.

**Acceptance Scenarios**:

1. **Given** an EDM with two or more portfolios, **When** the analyst selects two and launches hazard lookup with the defaults, **Then** two geohaz jobs are submitted — one per selected portfolio, both carrying the same parameter set — and each portfolio's "Hazard Version" column shows its job's in-line status.
2. **Given** one or more selected portfolios, **When** the analyst clicks Run hazard lookup, **Then** the lookup starts without opening a modal and uses the configured hazard data version, DLM, earthquake + windstorm, Skip locations with previous hazard lookup off, and Overwrite user-defined hazard values on.
3. **Given** an EDM with no portfolios, **When** the analyst views the summary page, **Then** the hazard-lookup action is disabled (the prerequisite gate requires EDM + ≥1 portfolio).
4. **Given** a launch across five portfolios where submission fails for the third, **When** the launch completes, **Then** the jobs already submitted stand, the remaining portfolios are still attempted, the failed submission is visible as failed for that portfolio, and that portfolio is immediately launchable again.
5. **Given** a portfolio with a non-terminal geohaz job, **When** the analyst composes a new launch, **Then** that portfolio cannot be included and its row shows the running job's status.

---

### User Story 2 - See lookup status and latest details per portfolio (Priority: P2)

After launching, the analyst stays on the EDM summary page. The portfolios table's final column is **"Hazard Version"**. The column displays the job's in-line status while a lookup runs and otherwise displays the raw `hazardVersion` stored in the portfolio's metadata snapshot. An absent or empty `hazardVersion` displays empty. The column refreshes without a manual reload. When the analyst expands a portfolio row, a column to the right of the exposure value lists shows the most recent run's Data Version, Model Family, Hazard Layers, both hazard lookup options, and Result. The `hazardVersion` does not gate an action.

**Why this priority**: The status and metadata display lets the analyst see which hazard versions Moody's stores for each portfolio.

**Independent Test**: Sync an EDM and confirm each final "Hazard Version" cell displays the raw `hazardVersion`. Launch a lookup on one portfolio; confirm the cell shows the job's status, updates without a manual reload, and displays the refreshed `hazardVersion` on completion.

**Acceptance Scenarios**:

1. **Given** submitted geohaz jobs, **When** the analyst remains on the EDM summary page, **Then** each launched portfolio's "Hazard Version" column shows the job's in-line status, updated by polling the workbench, without a manual page reload and without any fetch to Risk Modeler on the page-render path.
2. **Given** a portfolio whose lookup completed through the workbench, **When** the analyst views the portfolios table, **Then** its "Hazard Version" column displays the refreshed raw `hazardVersion`, and expanding the portfolio row shows the most recent run's Data Version, Model Family, Hazard Layers, both hazard lookup options, and Result.
3. **Given** a portfolio hazard-looked-up directly in Risk Modeler, **When** the analyst syncs and views the EDM, **Then** its column displays Risk Modeler's stored `hazardVersion` and its expanded row shows no workbench lookup details.
4. **Given** an absent or empty `hazardVersion`, **When** the analyst views the portfolio, **Then** the "Hazard Version" cell is empty.
5. **Given** a portfolio whose geohaz job Risk Modeler reports as failed, **When** the analyst views it, **Then** its column displays the last stored `hazardVersion`, the expanded row shows the failed lookup when it is the most recent run, the portfolio's own state is unchanged, and a new lookup can be launched on it.

---

### User Story 3 - Read the completion summary (Priority: P3)

When a lookup completes, the analyst reads Risk Modeler's `tasks[].output.summary` string inside the expanded portfolio row alongside the submitted parameters. The workbench does not parse or rewrite the sentence.

**Why this priority**: The summary is the payoff of a completed lookup and the substance of the P-05 lineage record, but it has no value until launches (US1) and tracking (US2) exist.

**Independent Test**: Let a lookup complete; expand the portfolio row and confirm the Risk Modeler completion summary displays with the parameter set.

**Acceptance Scenarios**:

1. **Given** a lookup that reached terminal success, **When** the analyst expands the portfolio row, **Then** the lookup's record shows `tasks[].output.summary` alongside the submitted parameter set.
2. **Given** the summary says a layer processed zero locations, **When** the summary is shown, **Then** the original Risk Modeler text remains unchanged.
3. **Given** a completion response without a task output summary, **When** the record is shown, **Then** the summary displays as unavailable, never an error page.
4. **Given** a portfolio looked up more than once through the workbench, **When** the analyst expands its row, **Then** only the most recent lookup's parameters and result are displayed.

---

### Edge Cases

- **EDM with zero portfolios**: the hazard-lookup action is disabled by the prerequisite gate; nothing errors.
- **Submission failure mid-launch**: each portfolio's submission is independent — earlier jobs stand, later portfolios are still attempted, the failed one is marked failed and the analyst relaunches it (it never reached Risk Modeler, so relaunching cannot duplicate a job).
- **Portfolio with a running lookup**: excluded from new launches (P-06); once the job is terminal — success or failure — the portfolio is launchable again.
- **Inapplicable peril**: the layer reports zero locations; zero is a value, never a failure.
- **Job fails in Risk Modeler**: the portfolio's latest-run details show the failed lookup when it is most recent; no portfolio, EDM, or submission status changes; analysis is never blocked by it.
- **Completion response missing `tasks[].output.summary`**: the record keeps status, parameters, analyst, and timestamps; the summary shows as unavailable.
- **Lookup run directly in Risk Modeler**: invisible to the workbench by design — the displayed details come from workbench `irp_job` rows, not from a Risk Modeler lookup.
- **Repeat lookups on one portfolio**: allowed once the prior job is terminal; the newest `irp_job` replaces the previously displayed details.
- **Launch while the EDM is mid-import or mid-sync**: allowed — the gate is EDM + ≥1 portfolio only. A portfolio renamed or removed between selection and submit surfaces as that portfolio's failed submission (visible, relaunchable); the other portfolios in the launch are unaffected.

## Requirements *(mandatory)*

### Functional Requirements

**Launch (US1)**

- **FR-001**: The EDM summary page MUST let the analyst select one or more of the EDM's portfolios and launch hazard lookup once for the selection; the workbench MUST submit one geohaz job per selected portfolio. Launching is open to every authenticated user — no role gate; the lookup record captures who launched it.
- **FR-002**: Clicking Run hazard lookup MUST submit immediately without a modal. Every launch MUST use the configured hazard data version, model family DLM, earthquake and windstorm, skip locations with previous hazard lookup off, and overwrite user-defined hazard values on.
- **FR-003**: One launch carries one parameter set: the submitted parameters MUST apply identically to every portfolio selected in that launch.
- **FR-004**: The hazard-lookup action MUST be enabled only when the EDM and at least one portfolio exist (prerequisite gate, PRD §13.1), and MUST be disabled otherwise.
- **FR-005**: Hazard lookup MUST NOT re-run geocoding — broker geocoding is preserved.
- **FR-006**: Each submitted job MUST be recorded as a geohaz-type IRP job carrying the parameter set it was submitted with. A submission failure for one portfolio MUST NOT undo jobs already submitted or prevent the remaining portfolios in the launch from being attempted; the failed submission is terminal and visible, and that portfolio MUST be immediately launchable again (recovery is relaunch — an automatic submission-retry batch is future work, not this feature).
- **FR-007**: A portfolio with a non-terminal geohaz job MUST NOT be includable in a new launch (P-06); a portfolio whose latest geohaz job is terminal MUST be launchable again.
- **FR-024**: The portfolios table header MUST carry a select-all checkbox that selects every eligible (P-06) portfolio for launch.

**Status & latest details on the summary page (US2)**

- **FR-010**: Non-terminal geohaz jobs MUST be tracked by the existing poller through single-status checks — never polled to completion, and never checked or fetched on a web request path.
- **FR-011**: The portfolios table on the EDM summary page MUST carry **"Hazard Version"** as its final column (P-07). The column MUST display **SUBMITTING** while the worker sends a job, the Risk Modeler job status while the job is non-terminal, and the raw stored `hazardVersion` otherwise. An absent or empty `hazardVersion` MUST display empty.
- **FR-012**: While a geohaz job is non-terminal, its status in the column MUST refresh by polling the workbench without a manual page reload (no live push — SSE is Iteration 6).
- **FR-013**: EDM sync MUST store the Get Portfolio Metadata response, including `hazardVersion`, in `irp_portfolio.exposure_detail`. The workbench MUST NOT use `hazardVersion` to gate an action (P-03).
- **FR-014**: A geohaz job that fails — in submission or in Risk Modeler — MUST be visible in the portfolio's expanded details when it is the most recent run, with Result showing the terminal status (Failed / Cancelled / Submission failed) in place of a completion summary; it MUST NOT replace the column's stored `hazardVersion`, MUST NOT change the portfolio's or EDM's own state, and MUST NOT block a later launch on that portfolio.
- **FR-015**: Hazard lookup MUST NOT be a prerequisite for analysis: no prerequisite gate may require a geohaz job to have run (P-04).

**Completion record (US3)**

- **FR-020**: When a geohaz job finishes successfully, the workbench MUST copy `tasks[].output.summary` from the completion response into the geohaz `irp_job` and refresh the portfolio's Get Portfolio Metadata snapshot in the background — never on a web request path.
- **FR-021**: The per-lookup record MUST comprise (P-05): the submitted parameter set, launching analyst, submitted and completed timestamps, terminal status, and completion summary string.
- **FR-022**: Expanding a portfolio row MUST show a column to the right of Lines of business, Countries, States, and Currencies. The column MUST show only the most recent lookup's Data Version, Model Family, Hazard Layers, Skip locations with previous hazard lookup, Overwrite user-defined hazard values, and Result. Result MUST use `completion_summary` when the most recent lookup succeeded, or the terminal status (Failed / Cancelled / Submission failed) when it did not.
- **FR-023**: The workbench MUST display the completion summary without parsing or rewriting it. A missing summary MUST render as unavailable and MUST NOT cause a page error.

### Key Entities

- **Geohaz job**: one hazard-lookup operation against one portfolio, recorded as an IRP job (`irp_job`, `irp_job_type = geohaz`) and tracked by the poller like every other IRP job. Carries the parameter set it was submitted with. How the submission reaches Risk Modeler is a plan decision (plan T-02); the analyst gets confirmation in the same interaction either way.
- **Hazard-lookup record**: the P-05 lineage for one completed (or failed) lookup — parameter set, launching analyst, submitted/completed timestamps, terminal status, and completion summary. The workbench stores it on the `irp_job` row.
- **Portfolio**: the target of a lookup and the row the summary page hangs status and latest-run details on. Unchanged structurally — a lookup never alters the portfolio's own state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An analyst can select two portfolios on an EDM summary page and launch hazard lookup for both with one click, no modal, and zero clicks into Risk Modeler.
- **SC-002**: While lookups run, each selected portfolio shows its own lookup status on the summary page, and the status reaches its terminal state without the analyst manually reloading the page.
- **SC-003**: Every portfolio with a workbench lookup displays the most recent run's stored Risk Modeler summary and parameters.
- **SC-004**: On an EDM with no portfolios the hazard-lookup action cannot be invoked; on an EDM with one portfolio it can.
- **SC-005**: A failed lookup is visible as failed on its portfolio and blocks nothing — the same portfolio can be looked up again, and no analysis gate is affected.
- **SC-006**: Every terminal "Hazard Version" cell matches the portfolio's stored raw `hazardVersion`, including an empty value, and no workbench action is gated by the value.

## Assumptions

- **P-05 settles PRD O8-3.** The workbench saves the parameter set, analyst, submit timestamp, terminal status, completion timestamp, and `tasks[].output.summary`. The portfolio displays the most recent run's saved summary string unchanged.
- **Data version is a single configured value, not hard-coded.** `HAZARD_DATA_VERSION` (currently `25.0`) is used at launch time; Risk Modeler has no `"latest"` resolution, so the value is bumped by config edit as Moody's ships new versions (research R6).
- **One launch is scoped to one EDM.** Portfolio selection and the launch button live on a single EDM's summary page; cross-EDM launches are not offered.
- **Concurrent lookups on one portfolio are prevented** (P-06). Risk Modeler may permit them, but a second concurrent lookup on the same portfolio has no analyst value and invites accidental double submission.
- **HD hazard lookup is outside MVP.** Hazard lookup launched by the workbench is DLM-only. Enhanced risk data (O7-2) is not a launch parameter.
- **History is workbench-only by design.** The page shows the workbench's own execution record; it does not attempt to reconstruct lookups run directly in Risk Modeler.

## Dependencies

- **Spec 004's EDM summary page** — the portfolios table gains the selection and final "Hazard Version" column; the most recent lookup details reuse the table's existing per-row expand.
- **Iteration 2's job machinery** — `irp_job` tracking, the `rwb_job` queue and workers, and the poller; the poller's routing gains the geohaz single-status check. (Submission mechanics are plan T-02; failed-submission recovery is relaunch per the 2026-08-13 clarification.)
- **`irp-integration` active wheel** — confirm the geohaz submit parameter set and that terminal responses continue returning `tasks[].output.summary` against the active wheel (`make irp-status`).
- **WORKBENCH schema** — storage for the P-05 per-lookup record, including `completion_summary`. The §21.0 DB-lifecycle prompt applies at planning: choose Rebuild / Refresh / Skip for `WORKBENCH`; `EXPOSURE`/`LOSS` are untouched and DATABRIDGE is never in schema scope.
