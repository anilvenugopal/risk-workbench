# Feature Specification: Analysis Run Details in the Expanded Row

**Branch**: `015-analysis-run-details` | **Created**: 2026-09-10

## Status

**Phase:** Approved · **Blocking:** Nothing

## Outcome

An analyst expanding a finished analysis reads what the run resolved on — its event rate scheme, its simulation set, and the treaties it applied — inside the workbench. Today the expanded row offers Event rate scheme and nothing else: blank for every broker-imported analysis and every HD run, and no treaty is named anywhere, so the analyst opens Risk Modeler to answer what the run actually used.

## In scope

- **What the run resolved on** — event rate scheme for broker-imported analyses, where the field reads *not returned* today (#106); simulation set with its simulation periods for HD analyses, which have no event rate scheme (#107); both, per region and peril, for a group of HD and DLM members (#108).
- **The treaties the run applied** (#109) — treaty number, treaty name and currency, for own runs, broker-imported analyses and groups alike.
- **How every value in the row is sourced** (#86) — one documented shape per origin for each field the row and the analyses grid show, the existing settings fields included, so a field never renders in one view and reads as missing in another. The audit of today's sources is the research for this spec.

## Out of scope

- Showing a treaty's limits, attachment point and retention. The row captures them with each applied treaty and renders only number, name and currency (P-02).
- Any change to the collapsed grid, the condensed results block, or the grouping compose screen, which already shows scheme and simulation set; the expanded row reports what the run used and edits nothing.
- Backfilling analyses captured before this change. Dev databases are rebuilt (AGENTS.md, Dev DB Strategy) and a manual RDM sync recaptures broker analyses; nothing here reads Risk Modeler for history.

## Non-negotiable behavior

1. Expanding a row **calls nothing**. Every value is captured when the analysis is imported or finishes, so an expanded row renders in full while Risk Modeler is unreachable.
2. A failed read **blanks the field and continues** — the rule the existing per-analysis metadata read already follows. It never aborts an RDM capture and never fails a finished run.
3. A row shows only fields its run can resolve: **Event rate scheme** for a DLM analysis, **Simulation set** for an HD analysis, **both** for a mixed group; a run with one region and peril shows a single field and a run with several shows one entry per region and peril, whatever its origin (P-08); a run whose partitions were not captured shows a **Run details** label reading *not returned* (P-08); and **no treaty entry at all** — not an empty label — for an analysis that applied none. A treaty read that failed is a different fact and shows a Treaties label reading *not returned* (P-04).
4. A simulation set is named through **PET metadata**. A PET identifier and a simulation-set identifier are different identifiers, so the label an analyst reads describes the PET the run used.
5. A field shown for an analysis **reads the same everywhere**. Currency once rendered in the analyses grid while the Compare guard read it as missing for the same broker analysis (#86); no field in this feature may do that. The Compare modal's metadata line shows the same resolved value the expanded row shows (P-05).

## Open product decisions

| ID | Decision | Status | Where |
|---|---|---|---|
| P-01 | The label is **Simulation set** with its simulation periods, and a group lists one **per region and peril** — matching the grouping compose screen's column in both respects | Approved | issues #107, #108 |
| P-02 | A treaty is listed as its **treaty number, treaty name and the currency the run applied it in**; occurrence limit, risk limit, attachment point and retention are captured with it and not shown in this iteration | Approved | issue #109, [research.md § T-05](research.md#t-05--treaties-applied-treaties-from-the-analysis-treaty-search-the-plan-item-records-the-requested-names) |
| P-03 | A value reports what the run resolved on **when it was captured**; a later rename in Risk Modeler does not change a captured row | Approved | — |
| P-04 | A **failed treaty read** shows a Treaties label reading *not returned*; an analysis that **applied none** shows no Treaties entry at all — the two are told apart | Approved | [research.md § Clarifications](research.md#clarifications) |
| P-05 | The **Compare modal's metadata line** shows what the run resolved on — the event rate scheme for a DLM analysis, the simulation set for an HD analysis, one entry per partition for a group — the same value the expanded row shows | Approved | [research.md § Clarifications](research.md#clarifications) |
| P-06 | Lists are **sorted**: treaties by treaty number, a group's partitions by region code then peril code — never in the order Risk Modeler returned them | Approved | [research.md § Clarifications](research.md#clarifications) |
| P-07 | A group's row lists each treaty **once per distinct treaty id**; a treaty that several members applied appears one time, with no count | Approved | [research.md § Clarifications](research.md#clarifications) |
| P-08 | The row's shape follows the **partition count**, not the origin: one partition renders as a single Event rate scheme or Simulation set field; two or more render as the per-region-and-peril list, group or not. The list, and the state where the partitions were not captured, carry the label **Run details** | Approved | [research.md § Clarifications](research.md#clarifications) |

---

## User Stories

### 1. The broker analysis names its event rate scheme (P1)

The analyst opens a submission whose broker sent an RDM, expands one of the imported analyses, and reads which event rate scheme the broker's run used. Today that field reads *not returned* for every broker analysis, even though the same value appears for the same analysis on the grouping compose screen.

**Acceptance**

1. **Given** a broker-imported analysis captured from an RDM, **When** the analyst expands its row in the submission's RDM analyses section, **Then** Event rate scheme names the scheme.
2. **Given** the same analysis offered as a grouping member, **Then** the scheme named in the expanded row matches the one the compose screen shows.
3. **Given** an RDM whose analyses include one whose scheme cannot be read, **When** the capture runs, **Then** that analysis's Event rate scheme is blank, every other analysis in the RDM is captured with its scheme, and the capture succeeds.

### 2. The HD analysis names its simulation set (P1)

An HD run resolves on a simulation set, not an event rate scheme, so its expanded row reports nothing about what it ran against — the one field on offer reads *not returned*. The analyst expands a finished HD analysis and reads the simulation set and its simulation periods.

**Acceptance**

1. **Given** a finished HD analysis, **When** the analyst expands its row, **Then** Simulation set names the PET the run used, with its simulation periods.
2. **Given** a grid holding HD and DLM analyses, **When** the analyst expands each, **Then** the HD row shows Simulation set, the DLM row still shows Event rate scheme, and neither shows the other's field.
3. **Given** an HD analysis whose PET name did not resolve, **Then** Simulation set names the PET by its id with its simulation periods.
4. **Given** an HD analysis whose partitions were not captured, **Then** a Run details label reads *not returned* and the rest of the expanded row — settings, members, condensed results — renders unchanged.
5. **Given** a finished HD analysis listed in the Compare modal, **Then** its metadata line names the same simulation set the expanded row shows, and a DLM analysis's line names its event rate scheme.

### 3. The mixed group names both (P2)

The analyst groups an HD analysis with a DLM analysis, chooses a simulation set for the DLM partition on the compose screen, and later expands the finished group. The row lists the event rate schemes the group grouped on and the simulation set resolved for each region and peril — the two halves of what the group ran on, in one place. This story ships after story 2: it names simulation sets the same way, so it depends on that naming being settled.

**Acceptance**

1. **Given** a finished group of one HD and one DLM analysis, **When** the analyst expands its row, **Then** the event rate schemes and the simulation sets are both listed, one entry per region and peril in region-code then peril-code order.
2. **Given** that group, **Then** each simulation set listed matches the choice the analyst made for that region and peril on the compose screen.
3. **Given** a group whose members are all DLM, **Then** the row lists its event rate schemes and no simulation set.
4. **Given** a group whose members all resolved on the same single region and peril, **When** the analyst expands its row, **Then** it shows one Event rate scheme or Simulation set field, the same shape a single analysis shows.
5. **Given** an own analysis that resolved on two regions or perils, **When** the analyst expands its row, **Then** it shows one entry per region and peril, the same shape a group shows.

### 4. The row names the treaties the run applied (P2)

An analyst reviewing a finished run wants to know which treaties it applied, and today opens Risk Modeler to find out. The expanded row lists each treaty as its number, name and currency, whether the analysis was executed here, imported from a broker's RDM, or produced by grouping.

**Acceptance**

1. **Given** an analysis executed with two treaties selected, **When** the analyst expands the finished row, **Then** both treaties are listed in treaty-number order, each as its treaty number, treaty name and currency.
2. **Given** a broker-imported analysis known to have a treaty applied, **When** the analyst expands it, **Then** the same treaty number, name and currency appear.
3. **Given** an analysis run with no treaties, **When** the analyst expands it, **Then** the row shows no treaty entry at all.
4. **Given** an analysis whose treaty read failed when it was captured, **When** the analyst expands it, **Then** the row shows a Treaties label reading *not returned*, and the rest of the row renders unchanged.
5. **Given** a finished group whose two members both applied the same treaty, **When** the analyst expands the group's row, **Then** that treaty is listed once.

## Requirements

- **FR-001**: The expanded row of a broker-imported analysis names its event rate scheme.
- **FR-002**: The scheme named for a broker-imported analysis matches the scheme shown for the same analysis on the grouping compose screen.
- **FR-003**: The expanded row of an HD analysis names the simulation set the run resolved on, labelled **Simulation set** (P-01).
- **FR-004**: A simulation set is shown with its simulation periods (P-01).
- **FR-005**: A simulation set label describes the PET the run used; a simulation-set identifier is never substituted for a PET identifier.
- **FR-006**: A DLM analysis shows Event rate scheme and no Simulation set; an HD analysis shows Simulation set and no Event rate scheme.
- **FR-006a**: A run with one partition renders it as a single Event rate scheme or Simulation set field; a run with two or more partitions renders one entry per region and peril, whether the analysis is a group or not (P-08).
- **FR-007**: A group's expanded row lists the event rate schemes it grouped on and the simulation sets resolved for it, per region and peril (P-01), ordered by region code then peril code (P-06).
- **FR-008**: Each simulation set listed for a group matches the choice recorded for that region and peril when the group was composed.
- **FR-009**: A group with no HD member lists event rate schemes only; a group with no DLM member lists simulation sets only.
- **FR-010**: The expanded row lists the treaties the analysis applied, each as its treaty number, treaty name and currency (P-02), ordered by treaty number (P-06), for analyses executed in the workbench, imported from an RDM, and produced by grouping.
- **FR-011**: Treaty number and name are those the run applied, reported as the analysis applied them, not as the exposure database defines them; its occurrence limit, risk limit, attachment point and retention are captured with it and not shown (P-02).
- **FR-011a**: A group's row lists each treaty once, matched by treaty id, however many of its members applied it (P-07).
- **FR-012**: An analysis whose treaty read succeeded and returned no treaties shows no treaty entry; an analysis whose treaty read failed shows a Treaties label reading *not returned* (P-04).
- **FR-013**: Every value in this feature is captured when the analysis is imported or finishes; expanding a row triggers no call to Risk Modeler.
- **FR-014**: A read that fails for one analysis leaves that analysis's field blank, reports no error to the analyst, and leaves every other analysis in the same import or run unaffected.
- **FR-015**: An analysis captured before this change shows the new fields blank until it is recaptured.
- **FR-016**: Every field the expanded row or the analyses grid shows for an analysis is read from one documented shape for that analysis's origin — own run, broker import, or group — and the same field never renders in one view and reads as missing in another (#86).
- **FR-017**: The Compare modal's metadata line reports what the run resolved on with the same rule as the expanded row: event rate scheme for a DLM analysis, simulation set for an HD analysis, one entry per partition for a group (P-05). An HD analysis no longer reads *scheme not returned* there.

## Key Entities

- **Partition** (analysis region): one region-and-peril combination an analysis or group resolved on. A DLM partition resolves an event rate scheme; an HD partition resolves a simulation set. An analysis has one or more, and a group lists the partitions it grouped on.
- **Applied treaty**: a treaty as one analysis applied it, carrying that analysis's terms rather than the exposure database's definition — a run in CAD against a USD treaty reports CAD. Identified by treaty id and stored with its currency, occurrence limit, risk limit, attachment point and retention; a group holds each id once (P-07).

## Success Criteria

- **SC-001**: An analyst reads what any finished analysis resolved on — broker-imported, HD, DLM or group — from the expanded row alone, with no visit to Risk Modeler.
- **SC-002**: Every simulation set an expanded group row lists matches, region and peril for region and peril, the choice made on the compose screen for that group.
- **SC-003**: Expanded rows render in full while Risk Modeler is unreachable.
- **SC-004**: A failed read blanks one field on one analysis; no RDM capture and no finished run fails because of it.
- **SC-005**: For an analysis executed with treaties selected, every treaty it applied is listed in the row by number, name and currency.
- **SC-006**: Each field the expanded row and the analyses grid show has one recorded source per origin, captured from a live Risk Modeler payload, and every view of the same analysis reads it through that source.
