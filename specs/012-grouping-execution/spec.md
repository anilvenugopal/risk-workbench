# Feature Specification: Grouping

**Branch**: `012-grouping-execution` | **Created**: 2026-08-27

<!-- Product only. Design → plan.md. Schema → data-model.md. Evidence and
     rejected options → research.md. Everything above the `---` is what a
     reviewer reads to decide: keep it under 40 lines. -->

## Status

**Phase:** Draft
**Blocking:** Nothing

## Outcome

An analyst combines finished analyses and groups within a submission into a single grouped result. The analyst inspects the members, resolves any event-rate-scheme conflict by choosing a scheme, sets the simulation periods, and submits — or, when nothing is left to choose, presses Finish on the first screen and the Workbench inspects and submits in one step. The group runs as a tracked job and then behaves like any other analysis: same results views, same retrieval.

## In scope

- Compose flow: pick finished analyses or groups scoped to the current submission, inspect, configure, run (replaces Risk Modeler's three-dot "enter analysis to group" entry); a Finish fast path that inspects and submits with every setting defaulted when no choice is left (O-14)
- Currency / currency scheme / vintage chosen at group-submit time — same picker as analysis submission, the currency prefilled with the members' common currency (O-10)
- Propagate detailed output setting (default ON)
- Inspection of the selected members with an explicit event-rate-scheme choice for each conflicting peril/region/model-version partition; PLT/ELT output classification; simulation periods from Risk Modeler's fixed list (O-11)
- The selected members shown as a chips panel beside the pick-list (O-13); the Risk Modeler app analysis id shown wherever an analysis id is displayed (O-12)
- Groupability validation with error messaging for unresolvable member sets
- Nested grouping (groups of groups); submission-level IRP tag on every Workbench-submitted individual analysis (groups carry no tag; O-07)
- Grouping tracked as a job; group rows in the results views (Engine column disclosure, submission-level results page, left-to-right ordering)

## Out of scope

- Opt-in end-of-suite auto-grouping (deferred — mixed-currency case unresolved, FR §6)
- Results-view ordering rework: drag-and-drop reordering and the O20-10 presentation defects stay with the results-view work

## Non-negotiable behavior

1. Event-rate schemes are chosen by the analyst when members conflict; the Workbench never selects one. Choices are limited to schemes the members use. In a PLT group, the simulation set of each ELT partition is also the analyst's choice, made independently of the scheme.
2. Only finished analyses and groups are selectable as members; a submit with unmet prerequisites is blocked and the block is visible.
3. A group is treated like any other analysis — viewed and retrieved the same way; the results grid discloses a group via the Engine column, not the name.
4. The member pick-list is scoped to the current submission; members may span EDMs and RDMs within it.
5. Currency, scheme, and vintage are confirmed by the analyst at each group submit with env-var defaults — never stored configuration.
6. Loss-affecting treaty terms sharing a Treaty Number are compared at inspection; every mismatch is shown as a table of the compared treaties with the values that differ, and never blocks the submit.

## Open product decisions

| ID | Decision | Status | Where |
|---|---|---|---|
| O-01 | Group names follow the `CRE_` analysis-naming conventions (underscore delimiter, `_n` collision suffix), auto-generated from the deal, prefilled in the compose dialog and editable before submit | Approved | PRD §16.4; decided 2026-08-27 |
| O-02 | Exactly what detail "propagate detailed output" retains (state-level, per-treaty) | Deferred | PRD O11-1 — the setting is passed through; definition owed at the CIC walkthrough, which gates the US2 checkpoint (tasks.md) |
| O-03 | Compose starts from the merged analyses grid on both the submission page and the EDM detail page (the grids are identical; the pick-list is submission-scoped either way) | Approved | note 17 §4, note 20 D1; decided 2026-08-27 |
| O-04 | This spec owns group rows in the existing results views only; the ordering rework (drag-and-drop) and the O20-10 presentation defects stay with the results-view work | Approved | note 19 D15, note 20 O20-10; decided 2026-08-27 |
| O-05 | The submission-level IRP tag value is the bare submission name — 011's current behavior kept for now; a structured `submission:<name>` prefix may be revisited | Approved | research.md T-06 and Clarifications, decided 2026-08-27 |
| O-06 | The analyst picks the scheme per conflicting partition, no default preselected (note 22 O22-1); the simulation periods are a compose input for a PLT group (O-11), 1 for an ELT group | Approved | research.md Clarifications 2026-09-02; CIC walkthrough of the dialog still owed (PRD O11-2 / O15-7) |
| O-07 | Groups are submitted without the submission tag: the platform grouping job schema has no tag field and no endpoint tags an analysis after creation. Member analyses still carry the tag. Revisit if Moody's adds a tagging endpoint | Approved | research.md T-07 and Clarifications, decided 2026-08-27 |
| O-08 | Risk Modeler's Create independent groups checkbox is not carried over: CIC never enables it, and the results views already show a group beside its member analyses. The compose settings are the currency block, Propagate detailed output, and the simulation count | Approved | research.md T-08 and Clarifications, decided 2026-08-27 |
| O-09 | Members are identified by Platform analysis ID (`irp_analysis.irp_id`; names duplicate tenant-wide, note 22 O22-16). Inspection runs before submit and blocks with structured problems; submission re-inspects and fails the job with `inspection_changed` when facts changed. Treaty term mismatches are listed and never block (FR-020) | Approved | research.md T-03, T-10 and Clarifications 2026-09-02, 2026-09-03 |
| O-10 | The group currency is prefilled with the currency code every member ran in; when the codes differ or a member's currency is unknown, the env default (USD) is prefilled. Scheme and vintage stay the env defaults either way. Neither Risk Modeler's empty-and-blocked field nor a fixed USD default | Approved | note 26 D7; research.md Clarifications 2026-09-04; decided 2026-09-04 |
| O-11 | Group simulation periods are chosen from Risk Modeler's fixed list — 3,125, 6,250, 12,500, 25,000, 50,000, 100,000, 200,000, 400,000, 800,000 — with 50,000 preselected regardless of the members' PLT lengths; the largest member length is shown as a hint only | Approved | note 26 D18; research.md Clarifications 2026-09-04; decided 2026-09-04 |
| O-12 | Wherever the Workbench shows an analysis id to the analyst — the expanded analysis row, the inspection treaty table — it is Risk Modeler's app analysis id (`irp_app_analysis_id`, the id the Risk Modeler UI shows), read from the column, else the stored metadata's `appAnalysisId`, else an em dash; never the Platform `analysisId` | Approved | note 26 D15; decided 2026-09-04; further placements held by CIC for the 9/4 call |
| O-13 | The compose dialog's selected members are shown as a chips panel beside the pick-list, each chip removable; the pick-list keeps its order and its unselected rows (CIC's move-ticked-rows-to-top ask is met by the panel) | Approved | note 26 D13/D14; decided 2026-09-04 |
| O-14 | Finish on the Members screen inspects and submits in one request with every setting defaulted (members' currency, env scheme and vintage, Propagate ON, ELT). It stops — landing on the Inspection screen with one generic notice — when the inspection blocks, a partition needs a scheme or simulation-set choice, the group output is PLT, the members' currency codes differ or one is unknown, or the env scheme/vintage default is missing. Treaty mismatches do not stop it. Success replaces the dialog with a confirmation pane that stays until Close, plus the existing toast and grid refresh | Approved | note 26 D8–D10; research.md Clarifications 2026-09-04; decided 2026-09-04 |

---

## User Stories

### 1. Compose and run a group (P1)

The analyst opens a submission whose analyses have finished, selects two or more of them with checkboxes, and starts the group compose. The dialog lists the selected members as chips beside the pick-list, and after inspection shows the currency the members ran in (or the default when they differ), the default scheme and vintage, and Propagate detailed output ON. The analyst inspects the members, confirms and submits — or presses Finish on the Members screen, and the Workbench inspects and submits at once when nothing is left to choose. The grouping appears in job monitoring; when it completes, the group appears among the submission's analyses as a finished analysis.

**Acceptance**

1. **Given** a submission with two or more finished analyses that ran in one currency, **When** the analyst selects them, opens the group compose, and inspects, **Then** the selected members appear as removable chips beside the pick-list, the currency is prefilled with that currency, scheme and vintage with the env-var defaults, and Propagate detailed output is ON. With members in different currencies, or a member whose currency is unknown, the currency is prefilled with the env default and the hint says so.
2. **Given** an analysis that is running or failed, **When** the analyst builds the member selection, **Then** that analysis cannot be selected as a member.
3. **Given** a submitted grouping, **When** it reaches a terminal status, **Then** the job's success or failure (with reason) is visible in job monitoring.
4. **Given** a grouping that finished, **When** the analyst views the submission's analyses, **Then** the group is listed as a finished analysis.
5. **Given** a finished group, **When** the analyst composes a new group, **Then** the existing group is selectable as a member (nested grouping).
6. **Given** two or more finished ELT analyses that ran in one currency, share their event-rate schemes, and have no blocking problem, **When** the analyst presses Finish on the Members screen, **Then** the group is submitted with that currency, the env scheme and vintage, Propagate ON, and no simulation choice, and the dialog is replaced by a confirmation pane naming the group, its settings, and its members, which stays until Close. With a scheme conflict, a PLT output, a blocked inspection, or differing or unknown currencies, Finish lands on the Inspection screen with a notice, nothing is submitted, and Next continues as usual.

### 2. Resolve a scheme conflict at compose time (P2)

CIC's common case: two North-America windstorm DLM analyses run under different rate schemes (a broker's RiskLink 23 vs CIC's RiskLink 25), or a DLM and an HD analysis together. In Risk Modeler this requires a manual convert-event-rate copy step before grouping. In the Workbench the analyst selects the members, inspects them, picks one of the members' schemes where they conflict, and submits.

**Acceptance**

1. **Given** two finished DLM analyses with different event-rate schemes, **When** the analyst inspects them, **Then** the dialog shows one choice per conflicting partition listing only the members' schemes, and Group stays disabled until every choice is made.
2. **Given** a finished DLM analysis and a finished HD analysis, **When** the analyst inspects them, **Then** the dialog shows the group output as PLT and the simulation periods dropdown with 50,000 preselected and the largest member PLT length as a hint.
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
- **FR-004**: Currency, currency scheme, and vintage are chosen at group-submit time with the same picker as analysis submission. After inspection the currency is prefilled with the code every member ran in — own analyses and groups from their submit-time settings, broker analyses from their Risk Modeler metadata — and with the env default when the codes differ or a member's currency is unknown; a hint states which. Scheme and vintage are prefilled from the env defaults (O-10).
- **FR-005**: Propagate detailed output is a compose-time setting, default ON; the setting is passed through to the platform, and what detail it retains is deferred to O-02.
- **FR-006**: The compose-time settings are the currency block, Propagate detailed output, and the simulation periods; Risk Modeler's Create independent groups checkbox is not carried over (O-08).
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
- **FR-019**: For a PLT group the analyst chooses the simulation periods — the target group PLT length — from Risk Modeler's fixed list (3,125, 6,250, 12,500, 25,000, 50,000, 100,000, 200,000, 400,000, 800,000) with 50,000 preselected; the hint states the largest member PLT length. For an ELT group the dialog shows no choice and submits 1. A submit with any other value is blocked (O-06, O-11).
- **FR-020**: The inspection screen shows one table per mismatched Treaty Number, in Risk Modeler's column order — analysis name, app analysis id (O-12; em dash when the Workbench holds none), treaty id, Treaty Number, treaty type, effective and expiration date, attachment point, occurrence limit, per risk limit, currency — one row per treaty as applied to its member, with the differing columns marked. Risk Modeler compares more terms than it shows, so the heading names every differing term by display name. The facts strip shows the mismatch count and the settings summary shows the count and the treaty numbers. Mismatches never block the submit.
- **FR-021**: When the group output is PLT, the analyst chooses a simulation set for each ELT peril/region/model-version partition from the platform's sets for that partition, shown by name and period count; no set is preselected, the choice is independent of the event-rate scheme, and a PLT/HD partition keeps the PET its members ran on, named with its period count and not offered as a choice. A partition with no available set blocks the inspection.
- **FR-022**: The compose dialog's Members screen shows the selected members as a chips panel beside the pick-list, one chip per ticked member with a remove control that unticks it; the pick-list keeps its order and its unselected rows (O-13).
- **FR-023**: The expanded analysis row's Analysis id is the Risk Modeler app analysis id — the column, else the metadata snapshot's `appAnalysisId`, else an em dash — for own, broker, and group rows alike; the Platform analysis id is never shown there (O-12).
- **FR-024**: A group's Event rate scheme value in the expanded analysis row (one scheme per member region/peril) is capped at five lines with an ellipsis; the full list stays available on hover.
- **FR-025**: The Members screen offers Finish beside Next, enabled under the same conditions. Finish inspects the members and, when the inspection has no blocking problem, no partition needs an event-rate scheme or simulation-set choice, the group output is ELT, every member ran in one known currency code, and the env scheme and vintage defaults resolve, submits the group in the same request with that currency, the env scheme and vintage, Propagate detailed output ON, and simulation periods 1; the dialog is replaced by a confirmation pane (inspection passed, group name, output, schemes, treaty mismatches, currency, members) that stays until Close, and the toast and grid refresh happen as for Group. Otherwise Finish lands on the Inspection screen with one generic notice, submits nothing, and the analyst continues with Next. Treaty mismatches never stop Finish (O-14).

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
