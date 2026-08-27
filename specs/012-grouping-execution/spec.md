# Feature Specification: Grouping

**Branch**: `012-grouping-execution` | **Created**: 2026-08-27

<!-- Product only. Design → plan.md. Schema → data-model.md. Evidence and
     rejected options → research.md. Everything above the `---` is what a
     reviewer reads to decide: keep it under 40 lines. -->

## Status

**Phase:** Draft
**Blocking:** Nothing

## Outcome

An analyst combines finished analyses and groups within a submission into a single grouped result, without Risk Modeler's manual event-rate-scheme pre-step. The group runs as a tracked job and then behaves like any other analysis: same results views, same retrieval.

## In scope

- Compose flow: pick finished analyses or groups scoped to the current submission, configure, run (replaces Risk Modeler's three-dot "enter analysis to group" entry)
- Currency / currency scheme / vintage chosen at group-submit time — same picker and env-var defaults as analysis submission
- Propagate detailed output (default ON) and Create independent groups (default OFF) settings
- Automated event-rate-scheme resolution across members, including differing DLM schemes and DLM+HD mixes
- Groupability validation with error messaging for unresolvable member sets
- Prerequisite gate: members must exist and be finished
- Nested grouping (groups of groups)
- Submission-level IRP tag applied to every Workbench-submitted individual analysis at submit time (groups carry no tag; O-07)
- Grouping tracked as a job in the existing job monitoring views
- Group-facing results hooks: Engine column disclosure, groups on the submission-level results page, user-controlled left-to-right ordering

## Out of scope

- Results export to the Loss Repository (Iteration 10)
- Broker RDM comparison (Iteration 9)
- Opt-in end-of-suite auto-grouping (deferred — mixed-currency case unresolved, FR §6)
- Creating ELTs by zone / county / country (done in SQL or the old tool today)
- Accumulation results (PRD §16.4a)
- Results-view ordering rework: drag-and-drop reordering and the O20-10 presentation defects (units reset on reorder, selections lost in a new tab) stay with the results-view work

## Non-negotiable behavior

1. Event-rate schemes are never user-picked; the system resolves them across members automatically.
2. Only finished analyses and groups are selectable as members; a submit with unmet prerequisites is blocked and the block is visible.
3. A group is treated like any other analysis — viewed and retrieved the same way; the results grid discloses a group via the Engine column, not the name.
4. The member pick-list is scoped to the current submission; members may span EDMs and RDMs within it.
5. Currency, scheme, and vintage are confirmed by the analyst at each group submit with env-var defaults — never stored configuration.

## Open product decisions

| ID | Decision | Status | Where |
|---|---|---|---|
| O-01 | Group names follow the `CRE_` analysis-naming conventions (underscore delimiter, `_n` collision suffix), auto-generated from the deal, prefilled in the compose dialog and editable before submit | Approved | PRD §16.4; decided 2026-08-27 |
| O-02 | Exactly what detail "propagate detailed output" retains (state-level, per-treaty) | Deferred | PRD O11-1 — the setting is passed through; definition owed with the CIC walkthrough |
| O-03 | Compose starts from the merged analyses grid on both the submission page and the EDM detail page (the grids are identical; the pick-list is submission-scoped either way) | Approved | note 17 §4, note 20 D1; decided 2026-08-27 |
| O-04 | This spec owns group rows in the existing results views only; the ordering rework (drag-and-drop) and the O20-10 presentation defects stay with the results-view work | Approved | note 19 D15, note 20 O20-10; decided 2026-08-27 |
| O-05 | The submission-level IRP tag value is the bare submission name — 011's current behavior kept for now; a structured `submission:<name>` prefix may be revisited | Approved | research.md T-06 and Clarifications, decided 2026-08-27 |
| O-06 | CIC sign-off on the automated event-rate-scheme resolution | Deferred | PRD O11-2 / O15-7 — validated against manual Risk Modeler grouping; walkthrough owed |
| O-07 | Groups are submitted without the submission tag: the platform grouping job schema has no tag field and no endpoint tags an analysis after creation. Member analyses still carry the tag. Revisit if Moody's adds a tagging endpoint | Approved | research.md T-07 and Clarifications, decided 2026-08-27 |
| O-08 | If the platform rejects single-member grouping jobs (the emulation for Create independent groups ON), the setting is dropped from the compose dialog entirely | Approved | research.md T-08 and Clarifications, decided 2026-08-27 |

---

## User Stories

### 1. Compose and run a group (P1)

The analyst opens a submission whose analyses have finished, selects two or more of them with checkboxes, and starts the group compose. The dialog shows currency, currency scheme, and vintage prefilled from defaults, Propagate detailed output ON, and Create independent groups OFF. The analyst confirms and submits. The grouping appears in job monitoring; when it completes, the group appears among the submission's analyses as a finished analysis.

**Acceptance**

1. **Given** a submission with two or more finished analyses, **When** the analyst selects them and opens the group compose, **Then** currency, scheme, and vintage are prefilled from the env-var defaults, Propagate detailed output is ON, and Create independent groups is OFF.
2. **Given** an analysis that is running or failed, **When** the analyst builds the member selection, **Then** that analysis cannot be selected as a member.
3. **Given** a submitted grouping, **When** it reaches a terminal status, **Then** the job's success or failure (with reason) is visible in job monitoring.
4. **Given** a grouping that finished, **When** the analyst views the submission's analyses, **Then** the group is listed as a finished analysis.
5. **Given** a finished group, **When** the analyst composes a new group, **Then** the existing group is selectable as a member (nested grouping).

### 2. Mixed members group without a manual pre-step (P2)

CIC's common case: two North-America windstorm DLM analyses run under different rate schemes (a broker's RiskLink 23 vs CIC's RiskLink 25), or a DLM and an HD analysis together. In Risk Modeler this requires a manual convert-event-rate copy step before grouping. In the Workbench the analyst just selects the members and submits; the system resolves the event-rate schemes.

**Acceptance**

1. **Given** two finished DLM analyses with different event-rate schemes, **When** the analyst groups them, **Then** the group submits and finishes without the analyst choosing a scheme or performing any pre-step.
2. **Given** a finished DLM analysis and a finished HD analysis, **When** the analyst groups them, **Then** the group submits and finishes.
3. **Given** a member set whose event-rate schemes cannot be resolved, **When** the analyst attempts to submit, **Then** an error names the cause and nothing is submitted.

### 3. Groups in the results views (P3)

The analyst treats a finished group like any other analysis: it appears on the submission-level results page, its results open the same way, and the Engine column is what discloses that a row is a group. When viewing several analyses and a group side by side, the analyst controls the left-to-right order so the group sits at the start or end rather than in the middle.

**Acceptance**

1. **Given** a finished group, **When** the analyst opens the submission-level results page, **Then** the group is listed alongside individual analyses and its results open the same way.
2. **Given** the analyses grid, **When** the analyst scans the Engine column, **Then** group rows are distinguishable there even though the name does not disclose it.
3. **Given** a results view with analyses and a group selected, **When** the analyst reorders columns, **Then** the chosen left-to-right order is applied.

### 4. Workbench analyses findable outside the Workbench (P3)

Analysts are not always in the Workbench. Every individual analysis the Workbench submits carries a submission-level tag in the Moody's platform, so an analyst working in the platform directly can query the submission's analyses and still find grouping candidates. Groups themselves carry no tag — the platform grouping job accepts none (O-07) — but their member analyses remain findable.

**Acceptance**

1. **Given** an individual analysis submitted from the Workbench, **When** it is inspected in the Moody's platform or via its API, **Then** it carries a queryable submission-level tag.

## Requirements

- **FR-001**: The analyst can select two or more finished analyses or groups within a submission and compose a grouping from them, starting from the merged analyses grid on either the submission page or the EDM detail page (O-03).
- **FR-002**: The member pick-list is scoped to the current submission; members may span EDMs and RDMs within the submission.
- **FR-003**: Only finished members are selectable; a grouping submitted with unmet prerequisites is blocked and the blocked state is visible to the analyst.
- **FR-004**: Currency, currency scheme, and vintage are chosen at group-submit time with the same picker and env-var defaults as analysis submission.
- **FR-005**: Propagate detailed output is a compose-time setting, default ON.
- **FR-006**: Create independent groups is a compose-time setting, default OFF. ON is emulated with one single-member grouping job per member (research T-08); if the sandbox check finds the platform rejects single-member grouping jobs, the setting is dropped from the compose dialog entirely (O-08).
- **FR-007**: Event-rate schemes are resolved automatically across members; the analyst never picks one.
- **FR-008**: DLM and HD analyses may be mixed in one grouping.
- **FR-009**: A member set whose event-rate schemes cannot be resolved is rejected with an error naming the cause, before anything is submitted.
- **FR-010**: The group name is auto-generated from the deal following the `CRE_` naming conventions, prefilled in the compose dialog, and editable by the analyst before submit (O-01).
- **FR-011**: A grouping runs as a tracked job: its status, completion, and failure reason are visible in the same job monitoring views as imports and analyses.
- **FR-012**: A finished group is recorded as an analysis of the submission and listed in the analyses grid like any other finished analysis.
- **FR-013**: A group's results are viewed and retrieved exactly as an individual analysis's results are.
- **FR-014**: The Engine column discloses that a row is a group; the name does not.
- **FR-015**: Groups appear on the submission-level results page.
- **FR-016**: The existing left-to-right ordering control in the results view works on group columns the same as on analysis columns; the drag-and-drop rework and O20-10 defects are out of scope here (O-04).
- **FR-017**: Every Workbench-submitted individual analysis carries a submission-level IRP tag whose value is the submission name (O-05), applied at submit time and queryable in the platform and via API. Groups carry no tag — the platform grouping job accepts none (O-07).
- **FR-018**: Groups can be members of other groups (nested grouping).

## Key Entities

- **Group**: an analysis flagged as a group; created by a grouping, owned by a submission, treated like any other analysis in every view.
- **Grouping job**: the tracked unit of work that submits the group to the Moody's platform and follows it to a terminal status.
- **Submission tag**: the platform-side tag linking every Workbench-submitted individual analysis back to its submission; groups carry none (O-07).

## Success Criteria

- **SC-001**: An analyst composes and submits a group from a submission's finished analyses without leaving the Workbench or performing any step in Risk Modeler.
- **SC-002**: Grouping members with differing event-rate schemes — including a DLM+HD mix — requires zero manual pre-steps (versus Risk Modeler's copy-and-convert step today).
- **SC-003**: 100% of individual analyses submitted from the Workbench after this feature ships are findable in the Moody's platform by their submission tag.
- **SC-004**: A finished group's results are reachable through the same views, in the same number of steps, as an individual analysis's results.
- **SC-005**: Every invalid grouping attempt (unfinished members, unresolvable schemes) is stopped before submission with an error that names the cause.
