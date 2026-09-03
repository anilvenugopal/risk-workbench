# Feature Specification: Grouping

**Branch**: `012-grouping-execution` | **Created**: 2026-08-27

<!-- Product only. Design → plan.md. Schema → data-model.md. Evidence and
     rejected options → research.md. Everything above the `---` is what a
     reviewer reads to decide: keep it under 40 lines. -->

## Status

**Phase:** Draft
**Blocking:** Nothing

## Outcome

An analyst combines finished analyses and groups within a submission into a single grouped result. The analyst inspects the members, resolves any event-rate-scheme conflict by choosing a scheme, sets the simulation count, and submits. The group runs as a tracked job and then behaves like any other analysis: same results views, same retrieval.

## In scope

- Compose flow: pick finished analyses or groups scoped to the current submission, inspect, configure, run (replaces Risk Modeler's three-dot "enter analysis to group" entry)
- Currency / currency scheme / vintage chosen at group-submit time — same picker and env-var defaults as analysis submission
- Propagate detailed output setting (default ON)
- Inspection of the selected members with an explicit event-rate-scheme choice for each conflicting peril/region/model-version partition; PLT/ELT output classification; positive simulation count
- Groupability validation with error messaging for unresolvable member sets
- Nested grouping (groups of groups); submission-level IRP tag on every Workbench-submitted individual analysis (groups carry no tag; O-07)
- Grouping tracked as a job; group rows in the results views (Engine column disclosure, submission-level results page, left-to-right ordering)

## Out of scope

- Opt-in end-of-suite auto-grouping (deferred — mixed-currency case unresolved, FR §6)
- Results-view ordering rework: drag-and-drop reordering and the O20-10 presentation defects stay with the results-view work

## Non-negotiable behavior

1. Event-rate schemes are chosen by the analyst when members conflict; the Workbench never selects one. Choices are limited to schemes the members use.
2. Only finished analyses and groups are selectable as members; a submit with unmet prerequisites is blocked and the block is visible.
3. A group is treated like any other analysis — viewed and retrieved the same way; the results grid discloses a group via the Engine column, not the name.
4. The member pick-list is scoped to the current submission; members may span EDMs and RDMs within it.
5. Currency, scheme, and vintage are confirmed by the analyst at each group submit with env-var defaults — never stored configuration.
6. Loss-affecting treaty terms sharing a Treaty Number are compared at inspection; every mismatch is listed with the treaty number, differing terms, and members, and never blocks the submit.

## Open product decisions

| ID | Decision | Status | Where |
|---|---|---|---|
| O-01 | Group names follow the `CRE_` analysis-naming conventions (underscore delimiter, `_n` collision suffix), auto-generated from the deal, prefilled in the compose dialog and editable before submit | Approved | PRD §16.4; decided 2026-08-27 |
| O-02 | Exactly what detail "propagate detailed output" retains (state-level, per-treaty) | Deferred | PRD O11-1 — the setting is passed through; definition owed at the CIC walkthrough, which gates the US2 checkpoint (tasks.md) |
| O-03 | Compose starts from the merged analyses grid on both the submission page and the EDM detail page (the grids are identical; the pick-list is submission-scoped either way) | Approved | note 17 §4, note 20 D1; decided 2026-08-27 |
| O-04 | This spec owns group rows in the existing results views only; the ordering rework (drag-and-drop) and the O20-10 presentation defects stay with the results-view work | Approved | note 19 D15, note 20 O20-10; decided 2026-08-27 |
| O-05 | The submission-level IRP tag value is the bare submission name — 011's current behavior kept for now; a structured `submission:<name>` prefix may be revisited | Approved | research.md T-06 and Clarifications, decided 2026-08-27 |
| O-06 | The analyst picks the scheme per conflicting partition, no default preselected (note 22 O22-1); the simulation count is a compose input, prefilled with the largest member PLT length for a PLT group and 1 for an ELT group | Approved | research.md Clarifications 2026-09-02; CIC walkthrough of the dialog still owed (PRD O11-2 / O15-7) |
| O-07 | Groups are submitted without the submission tag: the platform grouping job schema has no tag field and no endpoint tags an analysis after creation. Member analyses still carry the tag. Revisit if Moody's adds a tagging endpoint | Approved | research.md T-07 and Clarifications, decided 2026-08-27 |
| O-08 | Risk Modeler's Create independent groups checkbox is not carried over: CIC never enables it, and the results views already show a group beside its member analyses. The compose settings are the currency block, Propagate detailed output, and the simulation count | Approved | research.md T-08 and Clarifications, decided 2026-08-27 |
| O-09 | Members are identified by Platform analysis ID (`irp_analysis.irp_id`; names duplicate tenant-wide, note 22 O22-16). Inspection runs before submit and blocks with structured problems; submission re-inspects and fails the job with `inspection_changed` when facts changed. Treaty term mismatches are listed and never block (FR-020) | Approved | research.md T-03, T-10 and Clarifications 2026-09-02, 2026-09-03 |

---

## User Stories

### 1. Compose and run a group (P1)

The analyst opens a submission whose analyses have finished, selects two or more of them with checkboxes, and starts the group compose. The dialog shows currency, currency scheme, and vintage prefilled from defaults, and Propagate detailed output ON. The analyst inspects the members, confirms and submits. The grouping appears in job monitoring; when it completes, the group appears among the submission's analyses as a finished analysis.

**Acceptance**

1. **Given** a submission with two or more finished analyses, **When** the analyst selects them and opens the group compose, **Then** currency, scheme, and vintage are prefilled from the env-var defaults and Propagate detailed output is ON.
2. **Given** an analysis that is running or failed, **When** the analyst builds the member selection, **Then** that analysis cannot be selected as a member.
3. **Given** a submitted grouping, **When** it reaches a terminal status, **Then** the job's success or failure (with reason) is visible in job monitoring.
4. **Given** a grouping that finished, **When** the analyst views the submission's analyses, **Then** the group is listed as a finished analysis.
5. **Given** a finished group, **When** the analyst composes a new group, **Then** the existing group is selectable as a member (nested grouping).

### 2. Resolve a scheme conflict at compose time (P2)

CIC's common case: two North-America windstorm DLM analyses run under different rate schemes (a broker's RiskLink 23 vs CIC's RiskLink 25), or a DLM and an HD analysis together. In Risk Modeler this requires a manual convert-event-rate copy step before grouping. In the Workbench the analyst selects the members, inspects them, picks one of the members' schemes where they conflict, and submits.

**Acceptance**

1. **Given** two finished DLM analyses with different event-rate schemes, **When** the analyst inspects them, **Then** the dialog shows one choice per conflicting partition listing only the members' schemes, and Group stays disabled until every choice is made.
2. **Given** a finished DLM analysis and a finished HD analysis, **When** the analyst inspects them, **Then** the dialog shows the group output as PLT and a simulation count prefilled from the members.
3. **Given** a member set that cannot be grouped, **When** the analyst inspects it, **Then** the dialog names the problem with the members, partition, and PET IDs involved, and nothing is submitted.
4. **Given** member facts that changed between inspection and submit, **When** the grouping job runs, **Then** it fails with a reason telling the analyst to inspect again, and no group is created.

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
- **FR-005**: Propagate detailed output is a compose-time setting, default ON; the setting is passed through to the platform, and what detail it retains is deferred to O-02.
- **FR-006**: The compose-time settings are the currency block, Propagate detailed output, and the simulation count; Risk Modeler's Create independent groups checkbox is not carried over (O-08).
- **FR-007**: When members of one peril/region/model-version partition use different event-rate schemes, the analyst chooses one of the members' schemes for that partition before submitting; no scheme is preselected and the Workbench never chooses one (O-06).
- **FR-008**: DLM and HD analyses may be mixed in one grouping.
- **FR-009**: An invalid grouping is stopped with an error naming the cause: unfinished, foreign, or missing members are blocked by the Workbench, and inspection blocks a member set the platform cannot group before anything reaches the platform; a submission-time re-inspection failure fails the grouping job with a failure reason naming the cause (O-09).
- **FR-010**: The group name is auto-generated from the deal following the `CRE_` naming conventions, prefilled in the compose dialog, and editable by the analyst before submit (O-01).
- **FR-011**: A grouping runs as a tracked job: its status, completion, and failure reason are visible in the same job monitoring views as imports and analyses.
- **FR-012**: A finished group is recorded as an analysis of the submission and listed in the analyses grid like any other finished analysis.
- **FR-013**: A group's results are viewed and retrieved exactly as an individual analysis's results are.
- **FR-014**: The Engine column discloses that a row is a group; the name does not.
- **FR-015**: Groups appear on the submission-level results page.
- **FR-016**: The existing left-to-right ordering control in the results view works on group columns the same as on analysis columns; the drag-and-drop rework and O20-10 defects are out of scope here (O-04).
- **FR-017**: Every Workbench-submitted individual analysis carries a submission-level IRP tag whose value is the submission name (O-05), applied at submit time and queryable in the platform and via API. Groups carry no tag — the platform grouping job accepts none (O-07).
- **FR-018**: Groups can be members of other groups (nested grouping).
- **FR-019**: The simulation count is a positive integer. For a PLT group the analyst confirms it at compose time as the target group PLT length, prefilled with the largest member PLT length; for an ELT group the dialog shows no count and submits 1 (O-06).
- **FR-020**: The inspection screen lists each treaty term mismatch — the Treaty Number, the differing loss-affecting terms by display name, the members carrying it, and the treaty ids — and shows the mismatch count on the facts strip; the settings summary shows the count and the treaty numbers. Mismatches never block the submit.

## Key Entities

- **Group**: an analysis flagged as a group; created by a grouping, owned by a submission, treated like any other analysis in every view.
- **Grouping job**: the tracked unit of work that submits the group to the Moody's platform and follows it to a terminal status.
- **Inspection**: the read-only check of the selected members that classifies the group output (ELT or PLT), lists the partitions whose event-rate schemes conflict, lists treaty term mismatches, and either blocks with named problems or returns a fingerprint the submit must match.
- **Submission tag**: the platform-side tag linking every Workbench-submitted individual analysis back to its submission; groups carry none (O-07).

## Success Criteria

- **SC-001**: An analyst composes and submits a group from a submission's finished analyses without leaving the Workbench or performing any step in Risk Modeler.
- **SC-002**: Grouping members with differing event-rate schemes — including a DLM+HD mix — requires no step outside the Workbench; a scheme conflict is one dropdown choice in the compose dialog (versus Risk Modeler's copy-and-convert step today).
- **SC-003**: 100% of individual analyses submitted from the Workbench after this feature ships are findable in the Moody's platform by their submission tag.
- **SC-004**: A finished group's results are reachable through the same views, in the same number of steps, as an individual analysis's results.
- **SC-005**: Every invalid grouping attempt fails with an error naming the cause: unfinished, foreign, or missing members are stopped by the Workbench; a member set the platform cannot group is blocked at inspection with the problem, members, and partition named; facts that changed after inspection fail the grouping job with the cause in its failure reason.
