# Feature Specification: Analysis Execution — Suite & Single-Template Runs (Iteration 7)

**Feature Branch**: `010-analysis-execution`

**Created**: 2026-08-20

**Status**: Draft

**Input**: User description: "Spec 010 -- Analysis Execution. See details in Iteration 7 of the Build Plan in the PRD and the relevant Functional Requirements"

## Review summary

**What the analyst can do when this ships.** On an EDM detail page, the analyst multi-selects portfolios and clicks **Execute Suite** (or **Execute Template**). A modal lists the suites (or templates) with a simple search; the analyst picks one or more — suites and templates never mix in one execution — optionally expands a chosen suite to deselect templates that don't apply, picks treaties by name, and submits. The workbench submits **one analysis per selected portfolio × template** (templates deduplicated across the chosen suites), each auto-named **portfolio name + template name** (right-truncated to Risk Modeler's 64-character analysis-name cap; the full name is kept). Each analysis is recorded the moment it is submitted and appears in a **user-executed analyses section of the EDM detail page**, showing the portfolio it ran against, updating live as its job moves through statuses; its settings/metadata fill in when the job completes. Treaty create/edit hands off to the Risk Modeler editor in a new window. In the final phase of the iteration, loss numbers (AAL, return-period losses, OEP/AEP, standard deviation, PLT for HD) are retrieved automatically for finished analyses and shown on the analysis detail views, switchable across Gross / Ground-Up / Reinsurance-Layer perspectives.

**What this feature does NOT do.** No grouping (Iteration 8), no broker side-by-side comparison (Iteration 9), **no broker (RDM) loss-number retrieval** (P-12 — own executed analyses only; broker retrieval lands with the work that consumes it), no Loss Repository export (Iteration 10), no hazard lookup (shipped separately, Iteration 5 — and never an analysis prerequisite), no in-app treaty editor (pass-through only), no EP-curve graph, no editing of templates or suites (executed as defined by spec 009), **no workbench cancellation** (P-13 — a submitted run executes in full; jobs are cancelled in Risk Modeler's own UI and mirrored back as CANCELLED).

**Delivery is phased** (P-09): suite execution first, single-template execution second, loss-number retrieval third, and the job-monitor listing as the final phase. Each phase is separately verifiable.

**Business rules that shape the design** (PRD §11.3a, §14, design note D13/D14):

- **Run it all, deal with failures at the end** — a broad suite run against data lacking a peril is expected to fail those analyses ("no locations match the criteria"; no loss = no charge). Every failure carries its reason; nothing is silently dropped.
- **Submission failure and run failure are different things** — a submission that never reached Risk Modeler is retried automatically; a run Risk Modeler executed and failed is reported with its reason, never auto-resubmitted.
- **Templates are executed exactly as stored** — the submitted analysis uses the template's stored model profile, output profile, event-rate scheme, currency/scheme/vintage, and analysis settings with no defaulting or recomputation at submit time (spec 009 P-10; constitution: approved plans are immutable).
- **The portfolio column is trustworthy here** — the workbench submitted these analyses itself; the §2.2 trust rule concerns data that left CIC's environment.

**Decisions**:

| ID | Decision | Status |
|---|---|---|
| P-01 | Execution is portfolio-first: multi-select portfolios on the EDM detail page, then Execute Suite / Execute Template. | Approved (PRD §11.3a, 2026-08-20) |
| P-02 | Several suites — or several templates — per execution, never mixed; templates deduplicated across chosen suites; Submit disabled until at least one is chosen. | Approved (PRD §11.3a) |
| P-03 | A chosen suite expands inside the modal to deselect individual templates; no separate review page. Deselection applies to that execution only. | Approved (D13) |
| P-04 | Treaties are picked in the modal, explicitly, by name, at run time; the selection applies to every analysis in the execution. | Approved (spec 009 P-09) |
| P-05 | Fixed analysis naming: portfolio name + template name, right-truncated to the 64-character cap; the full name is stored. | Approved (PRD §2.6, 2026-08-20) |
| P-06 | Peril/portfolio mismatch is expected, not an error; every failed analysis is reported with its reason. | Approved (D14) |
| P-07 | An analysis record is written at submission and backfilled with settings/metadata when its job completes. | Approved (PRD §11.3a, 2026-08-20) |
| P-08 | Treaty create/edit is a Risk Modeler pass-through: new window, edit and save there, return, page refreshes. No tracked job. | Approved (FR §5, reconfirmed 2026-08-04) |
| P-09 | Phased delivery: suite execution → single-template execution → loss-number retrieval → job-monitor listing. | Approved (PRD §21 Iteration 7; listing phase added 2026-08-20) |
| P-10 | Rerun naming: when the fixed name already exists, a numeric suffix is appended within the 64-character cap. Reruns never block, and analysis names stay unique — Iteration 8 grouping resolves member analyses by name. The stored full name carries the same suffix. | Approved 2026-08-20 |
| P-11 | Background submit: clicking Submit closes the modal immediately; the confirmed run (portfolios × templates + treaties) is persisted and submitted in the background, each analysis appearing in the user-executed section as its submission lands. Navigating away never abandons the run. | Approved 2026-08-20 |
| P-12 | Broker (RDM) loss-number retrieval is out of this spec's scope — retrieval here covers own executed analyses only. (PRD §17.2 anticipates broker retrieval once the machinery exists; it ships with the work that consumes it, not here.) | Deferred 2026-08-20 |
| P-13 | No workbench cancellation: once Submit is clicked the run executes in full. An analysis is cancelled only in Risk Modeler's own UI; the workbench mirrors CANCELLED (treated as a failure). | Approved 2026-08-20 |
| P-14 | Loss-retrieval failure follows the standard background-job handling: the retrieval job is marked failed with its reason, interrupted work is recovered automatically (FR-015), and the detail view shows results-pending until numbers arrive. Automatic backoff retry and a retrieval-failed display are deferred to a later iteration. The analysis stays FINISHED — the run succeeded. | Approved 2026-08-20 |

**How to verify.** Select several portfolios, run a suite from the modal, and watch one auto-named analysis per portfolio × template appear in the EDM page's user-executed section and move through statuses live; deselect a template inside an expanded suite and see it excluded; force a peril mismatch and read its reason; run a single template the same way; edit a treaty via the Risk Modeler pass-through and see the refreshed view reflect it; after the loss-retrieval phase, open a finished analysis and read its loss numbers per perspective.

## Clarifications

### Session 2026-08-20

- Q: Does loss-number retrieval cover broker (RDM) analyses too, now that the machinery exists (PRD §17.2 says "from Iteration 7"; the Iteration 7 In-list says own analyses)? → A: **Not in this spec** (P-12). Retrieval here covers own executed analyses only; broker retrieval ships with the work that consumes it.
- Q: Re-executing the same portfolio × template produces a duplicate fixed name — block, allow duplicates, or suffix? → A: **Unique suffix** (P-10): append a counter within the 64-character cap. Reruns never block, and names stay unique for Iteration 8 grouping's name-based resolution.
- Q: For a large run (150+ submissions), does the analyst wait in the modal until every submission returns? → A: **No — background submit** (P-11): the modal closes on Submit, the confirmed run is persisted and executed exactly as approved, and analyses appear in the user-executed section as each submission lands.
- Q: Can the analyst cancel anything from the workbench after Submit — an in-flight analysis, or the unsubmitted remainder of a background run? → A: **No** (P-13). No workbench cancellation; individual jobs are cancelled in Risk Modeler's own UI and the workbench mirrors CANCELLED.
- Q: What does the analyst see when loss-result retrieval fails for a FINISHED analysis, and how does it recover? → A: **Standard job-failure handling** (P-14, amended 2026-08-20): the retrieval job is marked failed with its reason; interrupted retrievals recover via the FR-015 machinery; the detail view shows results-pending until numbers arrive. Automatic backoff retry and a retrieval-failed display are deferred to a later iteration. The analysis status stays FINISHED.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run template suites against selected portfolios (Priority: P1)

The analyst opens an EDM detail page, selects one or more portfolios in the portfolio table, and clicks **Execute Suite**. A modal lists the suites with a simple search; they choose one or more suites, optionally expand a suite and deselect templates that don't apply (e.g. flood not covered by the treaty), pick the treaties the analyses should apply (by name), and click Submit — which is disabled until at least one suite is chosen. The workbench submits one analysis per selected portfolio × template, deduplicating templates shared across the chosen suites, naming each by the fixed rule (portfolio name + template name). Submission failures are reported immediately and retried automatically; analyses Risk Modeler runs and fails are reported with their reason and never block the rest.

**Why this priority**: The suite run is the reason templates and suites exist — it turns the #1 analyst pain point (50–150+ manually configured analyses per contract, portfolios rerun one at a time) into one action.

**Independent Test**: With an imported EDM holding several portfolios and the suites created in Iteration 6, run one suite against two portfolios and confirm one submitted analysis per portfolio × template, correctly named, each with its own tracked job.

**Acceptance Scenarios**:

1. **Given** an EDM with 3 portfolios and a suite of 10 templates, **When** the analyst selects the 3 portfolios, opens Execute Suite, picks the suite, and submits, **Then** 30 analyses are submitted, each named portfolio name + template name (right-truncated to 64 characters; full name kept), each tracked as its own job.
2. **Given** two chosen suites that share a template, **When** the run is submitted, **Then** the shared template produces one analysis per portfolio, not two.
3. **Given** a chosen suite expanded in the modal with 2 of its 10 templates deselected, **When** the run is submitted, **Then** only the 8 remaining templates submit, and the suite definition itself is unchanged.
4. **Given** the modal open with no suite chosen, **Then** Submit is disabled.
5. **Given** treaties picked in the modal, **When** the run is submitted, **Then** every submitted analysis applies exactly those treaties.
6. **Given** a template with stored model profile, output profile, event-rate scheme, currency + scheme + vintage, and analysis settings, **When** its analysis is submitted, **Then** the submission carries exactly the stored values — nothing defaulted or recomputed.
7. **Given** Risk Modeler is unreachable for one submission in the loop, **When** the run is submitted, **Then** that analysis is marked as failed-to-submit with the failure visible to the analyst immediately, it is retried automatically up to the configured maximum, and the other analyses are unaffected.
8. **Given** a suite containing a flood template run against data with no flood exposure, **When** the flood analysis fails in Risk Modeler, **Then** its failure reason (e.g. "no locations match the criteria") is shown against that analysis, and sibling analyses continue unaffected.
9. **Given** a large run (say 150 analyses), **When** the analyst clicks Submit, **Then** the modal closes immediately, submissions proceed in the background exactly as confirmed, analyses appear in the user-executed section as each submission lands, and navigating away does not abandon the run (P-11).

---

### User Story 2 - Track executed analyses on the EDM detail page (Priority: P2)

Executed analyses appear in a user-executed analyses section on the EDM detail page — presented like the broker-analysis sections, but with no RDM grouping and with the portfolio each analysis ran against shown. Each analysis appears as soon as it is submitted and its status updates live on the page as its job moves through statuses, without a manual refresh. When a job completes, the analysis shows its settings/metadata; when it fails, it shows the reason.

**Why this priority**: "Run it all, deal with failures at the end" only works if the analyst can see, at a glance, what ran, what failed, and why — without leaving the EDM page or refreshing.

**Independent Test**: Submit a small run, watch the section populate immediately, watch statuses change live as jobs progress, and confirm settings/metadata appear on completion and a reason appears on failure.

**Acceptance Scenarios**:

1. **Given** a run just submitted, **When** the analyst views the EDM detail page, **Then** every submitted analysis is listed in the user-executed section with its full name, its portfolio, and its current status.
2. **Given** analyses in flight, **When** their jobs change status, **Then** the section updates live without a manual page refresh — the same treatment as import jobs.
3. **Given** a completed analysis, **When** the analyst views it, **Then** its settings/metadata (as run in Risk Modeler) are shown.
4. **Given** a failed analysis, **When** the analyst views it, **Then** its failure reason is shown.
5. **Given** a background step interrupted mid-work (e.g. a worker dies while backfilling metadata), **When** the recovery pass runs, **Then** the step is picked up and completed without analyst or developer action.
6. **Given** analyses submitted by another analyst against the same EDM, **When** any analyst views the page, **Then** those analyses are visible too (all analysts see all work).

---

### User Story 3 - Run individual templates (Priority: P3)

The analyst selects portfolios and clicks **Execute Template**. The same modal opens, listing templates instead of suites, with the same search; several templates can be chosen, treaties are picked the same way, and Submit behaves identically — one analysis per portfolio × template. Suites and templates are never mixed in one execution.

**Why this priority**: The exception path — a one-off analysis or a template outside any suite — reuses the suite-run machinery; it ships second per the phasing (P-09).

**Independent Test**: Select one portfolio, choose Execute Template, pick two templates, submit, and confirm two correctly-named analyses.

**Acceptance Scenarios**:

1. **Given** 2 selected portfolios, **When** the analyst chooses Execute Template, picks 3 templates, and submits, **Then** 6 analyses are submitted with the same naming, tracking, and failure handling as a suite run.
2. **Given** the Execute Template modal, **Then** no suite is offered in it (and no template list in Execute Suite's initial list) — one kind per execution.
3. **Given** the modal open with no template chosen, **Then** Submit is disabled.

---

### User Story 4 - View loss numbers for executed analyses (Priority: P4)

After an executed analysis finishes, the workbench retrieves its loss results automatically in the background — per financial perspective (Gross, Ground-Up, Reinsurance Layer) — and stores them. The analysis detail views (the Iteration-3 views, extended) then show the numbers: ELT summary (AAL / pure premium, max event loss, record count), standard deviation, return-period losses, OEP and AEP, and PLT for HD analyses — switchable by perspective. Numbers, not a plotted curve.

**Why this priority**: Loss numbers are the product of the run, but retrieval is deliberately the last phase (P-09) — execution and tracking must work first, and results viewing extends views that already exist.

**Independent Test**: Run one analysis to completion, then open its detail view and read AAL, return-period losses, and OEP/AEP per perspective; confirm a PLT appears only for an HD analysis.

**Acceptance Scenarios**:

1. **Given** an analysis that reaches FINISHED, **When** retrieval completes, **Then** its loss numbers are stored and viewable without the analyst requesting retrieval.
2. **Given** stored results, **When** the analyst opens the analysis detail view, **Then** ELT summary (AAL, max event loss, record count), standard deviation, return-period losses, and OEP/AEP are shown, and the analyst can switch between Gross, Ground-Up, and Reinsurance-Layer perspectives.
3. **Given** an HD analysis, **Then** PLT data is retrieved and viewable; **Given** a DLM analysis, **Then** no PLT is offered.
4. **Given** a failed analysis, **Then** no retrieval is attempted and the analysis still shows its failure reason.
5. **Given** a FINISHED analysis whose loss-result retrieval fails, **Then** the retrieval job is marked failed with its reason, the detail view shows results-pending, and the analysis status stays FINISHED (P-14). An interrupted retrieval is recovered and completed automatically (FR-015).

---

### Edge Cases

- Every template of every chosen suite deselected → nothing to submit; Submit disabled (equivalent to no suite chosen).
- Zero treaties picked → allowed; the analyses run without treaty application (gross of reinsurance).
- The same portfolio × template combination re-executed → the fixed name collides; a numeric suffix is appended within the 64-character cap and the rerun proceeds (P-10). Never blocked, never a silent duplicate.
- A portfolio renamed or deleted in Risk Modeler between selection and submit → the name-based submission fails for that portfolio; reported as a submission failure with its reason, retried per FR-010.
- A template flagged unresolved (spec 009 FR-011 — a stored reference value disappeared on re-sync) chosen for execution → submission proceeds with the stored values and Risk Modeler's rejection is reported as that analysis's failure reason; the workbench does not pre-block.
- A mispaired currency/scheme (membership deliberately unvalidated in spec 009, P-10) → fails at submit; reported with Risk Modeler's reason.
- An analysis cancelled directly in Risk Modeler → mirrored as CANCELLED; treated as a failure (only FINISHED is success), shown as such. Risk Modeler's UI is the only cancellation path — the workbench offers none (P-13).
- Submission retry exhausts its maximum → the analysis stays failed-to-submit, visible with its reason; no silent disappearance.
- The suite or template list is edited by an admin while the modal is open → the execution uses the selection as submitted; no re-read mid-run.
- An accumulation-profile template in a suite → runs like any other template; its output shape is handled at results viewing.
- Loss-result retrieval fails for a FINISHED analysis → the retrieval job is marked failed with its reason; the detail view shows results-pending; the analysis stays FINISHED (P-14). Automatic backoff retry is deferred.

## Requirements *(mandatory)*

### Functional Requirements

**Starting an execution (US1, US3)**

- **FR-001**: The EDM detail page's portfolio table MUST support selecting one or more portfolios and MUST offer **Execute Suite** and **Execute Template** actions for the selection. The actions are offered only when the EDM and at least one portfolio exist (the prerequisite gate); hazard lookup is never required.
- **FR-002**: The execution modal MUST list suites (for Execute Suite) or templates (for Execute Template) with a simple search; MUST allow choosing several; MUST never mix suites and templates in one execution; and MUST keep Submit disabled until at least one suite/template is chosen (and at least one template remains selected after deselection).
- **FR-003**: A chosen suite MUST expand inside the modal into its template list, allowing individual templates to be deselected for this execution only — the suite definition is never modified. There is no separate review page.
- **FR-004**: The modal MUST allow picking zero or more of the EDM's treaties, by name; the picked treaties apply to every analysis in the execution.

**Submission (US1, US3)**

- **FR-005**: Submit MUST produce exactly one analysis per selected portfolio × template combination, with templates deduplicated across the chosen suites.
- **FR-006**: Each submitted analysis MUST carry exactly the template's stored values — model profile, output profile, event-rate scheme (when stored), currency + currency scheme + scheme vintage (with the as-of date derived from the stored vintage's effective date), analysis settings, and tags — with no defaulting, substitution, or recomputation at submit time.
- **FR-007**: Each analysis MUST be named by the fixed rule: portfolio name + template name, truncated from the right to Risk Modeler's 64-character analysis-name cap; the full untruncated name MUST be stored. The analyst never types an analysis name. When the name already exists (a rerun of the same portfolio × template), a numeric suffix MUST be appended within the 64-character cap so the submitted name is unique; the stored full name carries the same suffix (P-10). A rerun is never blocked by the name check.
- **FR-008**: An analysis record MUST be written the moment its job is submitted — carrying its full name, portfolio, template, and originating submission context — and MUST be visible to analysts immediately.
- **FR-009**: When an analysis job completes, its record MUST be backfilled with the settings/metadata of the analysis as run.
- **FR-010**: A submission that never reached Risk Modeler MUST be recorded as failed-to-submit (distinct from a run failure), reported to the analyst immediately, and retried automatically up to a configured maximum with backoff; after the maximum it remains visible as failed-to-submit. One failed submission MUST NOT stop the rest of the run.
- **FR-011**: Every analysis Risk Modeler ran and failed MUST show its failure reason (e.g. the peril was not present in the data). A peril/portfolio mismatch is an expected outcome, not an application error, and MUST NOT affect sibling analyses.
- **FR-012**: Clicking Submit MUST close the modal immediately; the confirmed run — the exact portfolios × templates and treaties as approved — MUST be persisted and submitted in the background, with each analysis appearing in the user-executed section as its submission lands (P-11). The run MUST complete exactly as confirmed even if the analyst navigates away or closes the browser, and MUST NOT lose or recompute any part of the approved selection.

**Tracking (US2)**

- **FR-013**: The EDM detail page MUST show a user-executed analyses section listing every workbench-executed analysis for that EDM — presented like the broker-analysis sections but with no RDM grouping — each showing its full name, the portfolio it ran against, and its current status.
- **FR-014**: The user-executed section MUST update live as jobs change status, without a manual page refresh — the same live treatment as import jobs. Analysis jobs MUST appear in the `/workflows/irp-jobs` job listing with the same attribution as other tracked jobs; that page is a stub today, and the minimal read-only listing ships as the final phase of this iteration.
- **FR-015**: Background steps interrupted mid-work (a worker that dies holding a claimed step) MUST be recovered automatically and completed without analyst or developer intervention.

**Loss-number retrieval (US4 — last phase, P-09)**

- **FR-016**: When an executed analysis reaches FINISHED, the workbench MUST retrieve its loss results automatically in the background, per financial perspective (Gross, Ground-Up, Reinsurance Layer), and store a per-perspective summary plus the row-level data. No analyst action triggers retrieval. Retrieval covers own executed analyses only; broker (RDM) results are out of this spec (P-12). A failed retrieval follows the standard background-job handling: the retrieval job is marked failed with its reason, interrupted work recovers per FR-015, and the detail view shows results-pending until the numbers arrive — automatic backoff retry is deferred to a later iteration (P-14). The analysis status stays FINISHED throughout.
- **FR-017**: The executed-analysis detail views MUST show the retrieved numbers — ELT summary (AAL / pure premium, max event loss, record count), standard deviation, return-period losses (indicative set 1000 / 500 / 250 / 100 / ~20–25 year), and OEP and AEP — switchable by perspective; PLT MUST be shown for HD analyses only. No EP-curve graph is required.

**Treaty create/edit pass-through (P-08)**

- **FR-018**: Adding or editing a treaty MUST open the Risk Modeler editor in a new window; on return, the workbench treaty view MUST re-read from Risk Modeler and reflect the change. A pass-through edit creates no tracked job and never appears in the job monitor.

### Key Entities

- **Executed analysis**: one analysis the workbench submitted — full untruncated name, the portfolio and template it came from, originating submission context, lifecycle status, settings/metadata (backfilled on completion), failure reason when failed. A durable record from the moment of submission.
- **Analysis job**: the tracked unit for one submitted analysis — carries the Risk Modeler job identity, status (mirrored from Risk Modeler, or the failed-to-submit state), and submission attempt count. One per analysis in a run; there is no persisted "run" or batch entity — a run is just the set of jobs it created.
- **Analysis result summary** *(last phase)*: per (analysis, perspective) — the summary loss numbers (AAL, max event loss, record count, standard deviation, return-period losses, OEP/AEP points) plus references to the stored row-level data (ELT, EP curves, PLT for HD).
- **Template / suite** *(existing, spec 009)*: read-only inputs to execution; never modified by running them.
- **Treaty** *(existing)*: referenced by name at run time; created/edited only in Risk Modeler via pass-through.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An analyst runs a full suite (10+ templates) against several portfolios in **one modal interaction** — no per-analysis configuration, no analysis names typed, no separate review step.
- **SC-002**: Re-running the same work across 6 portfolios — previously 6 separate one-at-a-time runs — is one action.
- **SC-003**: 100% of submitted analyses appear in the user-executed section immediately on submission, and status changes appear on the page without a manual refresh.
- **SC-004**: 100% of failures — submission-side and run-side — are visible to the analyst with a reason; zero silent drops, including when retries are exhausted.
- **SC-005**: A 150-analysis run submits completely without analyst babysitting: transient submission failures recover automatically, and interrupted background steps recover without intervention.
- **SC-006**: After the loss-retrieval phase, an analyst reads AAL, return-period losses, and OEP/AEP for a finished analysis in every perspective it was run in, without leaving the workbench.

## Assumptions

- **Treaty selection is optional** — an execution with zero treaties runs the analyses without treaty application. The prerequisite gate's "(+ named treaties)" applies only when the analyses are meant to apply reinsurance.
- **The user-executed section shows all workbench-executed analyses for the EDM**, regardless of which analyst ran them — consistent with global visibility (no row-level security).
- **The job-monitor pages are stubs today** (`/workflows/irp-jobs`, `/workflows/rwb-jobs`); a minimal read-only `irp_job` listing ships as the final phase of this iteration so analysis jobs are visible (FR-014). The status bar stays a placeholder.
- **Accumulation-profile templates execute through the same path** as DLM/HD templates; accumulation-specific output shapes matter at results viewing, not at submission.
- **The exact return-period point set is indicative** (1000 / 500 / 250 / 100 / ~20–25 year) pending O5-2 (owned by the PRD); the retrieval stores what Risk Modeler returns.
- **Suite/template administration is unchanged** — this feature reads templates and suites as spec 009 shipped them (unordered suites, all-required currency triple, no per-item settings).
- **Grouping, comparison, and export build on these records later** (Iterations 8–10); nothing in this feature precludes a group being treated as an analysis.
