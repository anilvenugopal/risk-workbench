# Feature Specification: Treaty-Level (TY) Loss Results Export

**Branch**: `016-ty-perspective-export` | **Created**: 2026-09-16

## Status

**Phase:** Built 2026-09-16 (plan.md, tasks.md T001–T017), amended 2026-09-17 for the treaty selector (note 31 D23–D26, tasks.md T018–T026) and 2026-09-21 for the zero-loss treaty filter (note 32 D8–D11, tasks.md T027–T033). Unit tier green. Ben clicked through the zero-loss filter on the running stack 2026-09-21 (quickstart.md Story 1 step 8, Story 3 step 1) with irp-integration 0.10.0rc1 installed. The SQL Server tier and the sandbox pytest checks of the treaties endpoint and the treaty-scoped stats read are unverified until someone runs them (quickstart.md).
**Blocking:** O-04 (exposure value when a treaty's rows are combined) is built as the largest value and stays open; Cheryl and Wendy owe Cheng's aggregation query. Builds on spec 014 as amended by the 2026-09-15 session (one flat exports table, decimal model version, export-wide engine-version override).

## Outcome

An analyst exports the treaty-level loss results (TY) of a finished analysis into CIC's loss repository from the same export form as GU, GR, RL, and RP, and gets one loaded data set per treaty. Today per-layer treaty losses reach the repository only through RiskLink and CIC's workflow tool, because the working excess perspective sums the layers and cannot tell layer one from layer two.

## In scope

- TY as a choice in the export form's perspective list, offered when every selected analysis has at least one treaty that took TY loss.
- Per analysis at TY: the treaties Risk Modeler applied when the analysis ran that took TY loss, each listed with its type, risk limit, attachment point, and occurrence limit. A treaty that took no loss is absent from the list (P-13). The analyst ticks the treaties to export and types the data name of each one while its terms are on screen (P-03, P-06, P-12).
- Per analysis: one treaty-level loss table requested from Risk Modeler, one data set written per ticked treaty, each treaty's rows combined by event (P-11). Each treaty data set is classified, corrected, and loaded exactly as a portfolio-level data set is in 014.
- Treaty number, treaty name, and Risk Modeler's analysis-time treaty IDs recorded on every treaty data set; manifest rows, exports table rows, status filter, Retry, and Close at treaty grain.
- Informative text on the form when TY is picked: one loss set per ticked treaty, per analysis.

## Out of scope

- Aggregating one treaty's losses across analyses in the Workbench. The analyst creates the analysis group in Risk Modeler first (spec 012) and exports the group; Risk Modeler does the aggregation (P-01).
- TY in the results view.
- Treaty terms anywhere but the export form's cart. The EDM treaty grid keeps the detail it already shows, and the Workbench stores no term of its own.
- HD analyses at TY (014 O-08). WX and QS stay unexported (014 FR-003).

## Non-negotiable behavior

1. 014's four rules hold for every treaty data set: never rewritten, losses never altered, historical classification read from the lookup, each data set all rows or nothing.
2. A treaty is identified by its treaty number and treaty name: the treaty the analyst ticked is matched to the rows Risk Modeler wrote into the loss table by those two values. Treaties are never matched by name across analyses, and the analysis-time treaty ID is recorded for traceability only, never used as the identity.
3. Within one analysis, the rows of one treaty are combined by event into one loss row (P-11). The Workbench never combines rows across treaties or across analyses: an export of N analyses at TY, each with M ticked treaties, writes N × M data sets.
4. A TY export whose loss table holds no treaty rows fails with a message naming TY and loads nothing.

## Open product decisions

| ID | Decision | Status | Where |
|---|---|---|---|
| P-01 | Group first. The Workbench does no cross-analysis treaty aggregation. An analyst who wants one data set per treaty across several analyses groups them in Risk Modeler (spec 012) and exports the group. Risk Modeler exports one archive per analysis and does not aggregate a multi-analysis treaty export (Cheryl's test, 2026-09-10). The alternative (aggregate on export) and its trade-offs are recorded in research.md, per Anil's request that both paths stay documented. | Approved | note 29 D15–D19, 2026-09-11 |
| P-02 | The Workbench calls TY a perspective and offers it in the same list as GU, GR, RL, RP, mirroring Risk Modeler's results view label. The export request's output level (treaty instead of portfolio) is set by the Workbench and never shown to the analyst. | Approved | note 28 D1, O28-1; user, 2026-09-16 |
| P-03 | The analyst picks the treaties to export, per analysis: each cart row lists that analysis's treaties as tick boxes, nothing is ticked until the analyst ticks it, and an all / none link per analysis ticks or clears that analysis's list. Two analyses in one export take different picks. A treaty nobody ticked is not written, whatever the loss table holds. | Approved | note 31 D23; user, 2026-09-17 |
| P-04 | Several analyses may be exported at TY in one export; each yields its own treaty data sets, and each is ticked on its own (P-03). When TY is picked the form says: one loss set per ticked treaty, per analysis. | Approved | note 29 D23, 2026-09-11 |
| P-05 | TY is offered only when every selected analysis has at least one treaty that took TY loss, by the same intersection rule as 014 FR-003: one selected analysis without such a treaty removes TY for the whole selection. Whether a treaty took loss is read from what the Workbench recorded about the run at results retrieval; the template's output profile is not consulted. | Approved | note 28 D2, D3; note 32 D8; user, 2026-09-16, 2026-09-21 |
| P-06 | The data name of a treaty data set is typed per treaty, in the cart, beside the terms it encodes: the analyst reads a 3x2 off the risk limit and attachment point and types it there and then. Blank means `{analysis name} {treaty number}`, which the field shows as its placeholder. Cheryl's reason: *"it is keeping your head into the treaty information at the time that you're interacting with it… if Cheng hadn't put that name on there, there was no way for me to get back to that information."* | Approved | note 31 D24, D20; user, 2026-09-17 |
| P-07 | Each treaty data set's header row carries Perspective `TY`, the analysis's ID, name, and description, and the same client, treaty inception, CRM ID, vintage, currency, vendor, server, and database as a portfolio-level row. Treaty number, name, and IDs are recorded on the export record, since the repository's header table has no treaty column. | Approved | note 28 §7; 014 FR-015; user, 2026-09-16 |
| P-08 | The financial perspective sent with a TY request is a fixed value chosen in the plan and documented there, not an analyst input. Evidence: the sample export requested GU, GR, RL, RP at treaty output level and Risk Modeler returned one TY file. | Approved | note 28 D16, D17; sample archive `26325601_…`; user, 2026-09-16 |
| P-09 | The export's manifest and the exports table hold one row per ticked treaty per analysis at TY, beside the one-row-per-analysis grain of the other perspectives. The rows exist from the moment the analyst clicks Export, each with its own status, last change time, data ID, counts, Retry, and Close. A failure before the loss table is read (request rejected, download failed) shows on every treaty row of that analysis. | Approved | note 29 D21, note 31 D23; user, 2026-09-17 |
| P-10 | The form's cart shows no AAL for TY (the value exists only per treaty). Each loaded treaty row in the exports table shows the AAL of its own loss rows, the sum over its events of rate times loss. | Approved | 014 P-20; user, 2026-09-16 |
| P-12 | Each treaty in the cart shows its type, risk limit, attachment point, and occurrence limit, in that order — the risk limit beside the attachment point, because that pair is how an analyst reads a 3x2. Amounts use the loss format the Workbench already shows (millions to one decimal, thousands separated below a million, `—` when Risk Modeler reports none) and carry no currency symbol. Cheryl asked for it cheap: *"It's an identifier. We're going to rename it more than likely in the end anyway."* | Approved | note 31 D25, D26; user, 2026-09-17 |
| P-11 | Within one analysis, all rows of one treaty (by treaty number and name, whatever their treaty IDs) are combined per event ID before classification: loss summed, independent standard deviation summed, correlated standard deviation the square root of the sum of squares, the event's rate kept. A treaty and event that appear once are written unchanged. | Approved | note 29 D22 (Cheryl); note 28 D6; user, 2026-09-16 |
| P-13 | The cart lists only the treaties that took TY loss. A treaty that took none is filtered out, not shown with a zero: its absence is the analyst's flag that the analysis needs a look before export. The Workbench does not say why a treaty took no loss; Cheryl's two ordinary cases (an auto portfolio under a per-risk attachment too high to reach, a treaty that applies to another member's portfolio) are valid zeros the analyst diagnoses in Risk Modeler. | Approved | note 32 D8, D10, D11; Cheryl, Ben 2026-09-18 |
| O-05 | Whether Risk Modeler reports a group analysis's treaties at all. | Closed 2026-09-21 | The treaties endpoint and the treaty-scoped stats read answer for a group as for a single analysis (Ben, 2026-09-21; plan O-01, research R8) |
| O-04 | Exposure value when a treaty's rows are combined: Cheryl says it is the treaty's maximum, not additive, and does not know how Cheng handles it. Until his query is read the Workbench keeps the largest exposure value among the combined rows. | Open | note 29 D22, O29-8 |

---

## User Stories

### 1. Export an analysis's treaty losses (P1)

From a submission's analyses page the analyst opens Export, ticks a finished analysis that ran with two per-risk layers, and finds TY in the perspective list. Picking TY lists both layers with their type, risk limit, attachment point, and occurrence limit. The analyst ticks both, reads 3x2 off the first layer's terms and types it as that layer's data name, leaves the second blank, picks the client, confirms treaty inception and CRM ID, enters the vintage, and clicks Export. The Workbench requests the treaty-level loss table and loads two data sets, one per layer, each named as the analyst named it.

**Acceptance**

1. **Given** a finished analysis with at least one treaty that took TY loss, **When** the analyst ticks it on the export form, **Then** TY is among the perspective choices beside the codes it also has results for.
2. **Given** two ticked analyses of which one has no treaty that took TY loss (run without treaties, or every treaty took none), **Then** TY is not offered (P-05).
3. **Given** TY is picked, **Then** each cart row lists that analysis's treaties that took TY loss with their terms and nothing ticked, the form shows the text "one loss set per ticked treaty, per analysis", and Export stays refused until every selected analysis has a treaty ticked (P-03, P-04, P-12).
4. **Given** an accepted TY export of one analysis with PR1 and PR2 both ticked, **When** processing finishes, **Then** the repository holds exactly two header rows, each with perspective TY, each with only that treaty's events split into the stochastic and historical tables, and every loss value equals what Risk Modeler produced.
5. **Given** the loaded data sets, **Then** each data name is the one typed for that treaty, or the analysis name and treaty number when it was left blank (P-06), and the export record holds the treaty IDs, number, and name for each.
6. **Given** a TY export of two analyses that share the same two treaties, both ticked in each, **Then** four data sets are loaded and none merges rows from two analyses (non-negotiable 3).
7. **Given** an analysis whose treaty-level loss table comes back with no treaty rows, **Then** that analysis fails with a message naming TY, nothing for it reaches the repository, Retry is offered, and other analyses in the export are unaffected (non-negotiable 4).
8. **Given** an analysis run with two treaties of which one took no TY loss, **When** TY is picked, **Then** the cart lists the other treaty alone, with no zero and no explanation (P-13).

### 2. Follow treaty data sets in the exports table (P1)

The analyst opens the submission's exports table after a TY export. Each treaty is its own row, showing the treaty number and name beside the analysis it came from, its status, data ID, and the same counts a portfolio-level row shows. A failed treaty row can be retried or closed on its own. The status filter treats treaty rows like analysis rows.

**Acceptance**

1. **Given** a TY export whose loss table has been read, **Then** the exports table shows one row per treaty for that analysis, each with the analysis name, treaty number, treaty name, status, last change time, data ID, and the rows staged, stochastic, historical, exposure raised, and standard deviation zeroed counts (P-09).
2. **Given** a TY export whose Risk Modeler request was rejected or whose download failed, **Then** every ticked treaty row of that analysis carries the failure message (P-09).
3. **Given** a loaded treaty row, **Then** it shows the AAL of its own loss rows, and the cart on the export form showed no AAL for that analysis at TY (P-10).
4. **Given** the Failed filter, **Then** a failed treaty row is listed and a loaded treaty row of the same analysis is not.
5. **Given** a treaty row, **When** the analyst opens the loaded data set in the workflow tool by its data ID, **Then** the data name and the Workbench row agree on treaty number and name.

### 3. Export a group at TY for one data set per treaty across perils (P2)

The analyst has five peril analyses in one EDM that all ran with the same two treaties and wants one loss set per treaty across all five, as the workflow tool gives them today. In the Workbench they first create the analysis group (spec 012), then export the group at TY. The Workbench reads the group's one treaty-level table, combines each treaty's rows by event, and loads one data set per treaty.

**Acceptance**

1. **Given** a finished group whose members were run with treaties, **Then** the group is offered on the export form and TY is among its perspective choices.
2. **Given** the group's two treaties ticked and the group exported at TY, **When** processing finishes, **Then** exactly one data set per ticked treaty is loaded, each event's loss row combining that treaty's rows under every treaty ID the members minted (P-11), and no per-member data set is written.
3. **Given** the five member analyses exported at TY without grouping, both treaties ticked in each, **Then** ten data sets are loaded and the form's text has already told the analyst so (P-01, P-04).

## Requirements

- **FR-001**: The export form offers TY in the perspective list when every selected analysis has at least one treaty that took TY loss, by the same rule that gates the other codes (P-05). Whether a treaty took loss is read per analysis from what the Workbench recorded at results retrieval; the template's output profile is not consulted.
- **FR-002**: When TY is picked the form shows a line stating that the export writes one loss set per ticked treaty, per analysis, and every cart row lists its analysis's treaties that took TY loss as tick boxes — treaty number, treaty name, type, risk limit, attachment point, occurrence limit (P-12) — with an all / none link over that analysis's list. A treaty that took no loss is absent, not shown as zero (P-13). Nothing is ticked until the analyst ticks it, a ticked treaty shows the data name field of FR-009, and Export is refused while any selected analysis has no treaty ticked (P-03). The form asks for no financial perspective (P-08).
- **FR-003**: A TY export accepts one or more analyses, own, broker, or group alike (P-04, 014 FR-001). The Workbench never combines analyses; an analyst wanting one data set per treaty across analyses groups them first in Risk Modeler (P-01).
- **FR-004**: For each analysis at TY the Workbench requests the treaty-level loss table from Risk Modeler, downloads it when finished, and keeps the archive permanently as in 014 FR-007. The archive check of 014 FR-008 applies, and the loss table must be a treaty-level table.
- **FR-005**: Each ticked treaty is one data set, holding the rows of the analysis's treaty-level loss table whose treaty number and treaty name match it as written in the table; no name matching, trimming, or inference is applied (non-negotiable 2). Those rows are then combined per event as FR-017 states. A treaty in the table that nobody ticked is not written.
- **FR-006**: Each treaty data set records the treaty number, treaty name, and every analysis-time treaty ID found for the treaty in the table (one for a single analysis, one per member for a group) on the export record, beside the values 014 FR-006 already records for the analysis.
- **FR-007**: A treaty-level loss table with zero treaty rows fails every treaty row of that analysis at stage with a message naming TY. A ticked treaty the table does not hold fails that treaty's row alone, naming the treaty. Nothing is loaded and a header row with no loss rows is never written (non-negotiable 4, 014 P-12). The per-treaty failure is the fallback for a treaty list captured before the loss flag existed or a loss table that disagrees with the stats read at retrieval; the cart's filter (P-13) keeps a zero-loss treaty from being ticked in the first place.
- **FR-008**: Each treaty data set is classified (014 FR-011, FR-012), corrected (014 FR-013), and loaded (014 FR-014) exactly as a portfolio-level data set, as one unit: all committed or none. One treaty's failure never affects another treaty or another analysis.
- **FR-009**: The header row of a treaty data set carries perspective `TY`, the analysis's ID, name, and description, and the export's client, treaty inception, CRM ID, vintage, currency, vendor, model version, server, and database (P-07). Its data name is what the analyst typed for that treaty on the form, or `{analysis name} {treaty number}` when they left it blank (P-06).
- **FR-010**: Historical classification, the two corrections, the export-wide engine-version override, and the decimal model version (014 as amended 2026-09-15) apply to every treaty data set in the export exactly as to a portfolio-level data set.
- **FR-011**: The export's manifest and the exports table hold one row per ticked treaty of an analysis at TY, from the moment the export is requested. A treaty row shows the analysis name, treaty number, treaty name, status, last change time, data ID, the five counts of 014 FR-018, and the failure message when failed (P-09).
- **FR-012**: The status filter, Retry, and Close of 014 FR-017 and FR-019 work per treaty row. Retry of a treaty row resumes from the last completed step for that treaty and never re-downloads an archive already held. Close records who closed it and when.
- **FR-013**: The repeat-export warning of 014 FR-004 treats TY like any other perspective: an analysis already exported at TY is warned about, and a repeat writes a new data set for every ticked treaty (014 P-17).
- **FR-014**: A loaded treaty data set is never repeated: a re-run of a loaded treaty writes nothing and reports success (014 FR-021 at treaty grain).
- **FR-015**: Each treaty data set is traceable from the export identifier to its analysis, its Risk Modeler export job, and its Workbench jobs (014 FR-023), and from its data ID back to the treaty number and name.
- **FR-016**: The form's cart shows no AAL for an analysis at TY; a loaded treaty row in the exports table shows the AAL of its own loss rows, the sum over its events of rate times loss (P-10).
- **FR-017**: Before classification, the rows of one treaty in one analysis are combined per event ID into one loss row: loss summed, independent standard deviation summed, correlated standard deviation the square root of the sum of squares, the event's rate kept, exposure value the largest of the combined rows until O-04 is closed (P-11). A treaty and event that appear once are written unchanged.

## Key Entities

- **Treaty data set**: one treaty of one analysis within an export, identified by treaty number and treaty name, carrying its analysis-time treaty IDs, its own status, data ID, counts, and error. The unit of load, Retry, and Close at TY; a portfolio-level export has one data set per analysis, a TY export has one per ticked treaty.
- **Treaty-level loss table**: the file Risk Modeler returns for one analysis at treaty output level, holding every treaty's events in one table with the treaty ID, number, and name on every row.
- **Treaty identity**: the treaty number and treaty name as defined in the EDM and copied into the loss table. The treaty ID on the same rows is minted when the analysis runs and differs between runs of the same treaty; in a group's table one treaty can carry several treaty IDs, one per member.
- **Analysis group**: a Risk Modeler analysis that combines member analyses (spec 012). The route to one treaty data set across perils; exported like any other analysis.
- **Data header row**, **stochastic loss rows**, **historical loss rows**, **historical event lookup**, **client**: as in spec 014.

## Success Criteria

- **SC-001**: An analyst exports treaty-level losses to the repository entirely from the Workbench, choosing the treaties and naming each data set on the one screen that shows their terms, with no RiskLink or workflow-tool step before the load.
- **SC-002**: 100% of loaded TY exports hold exactly one header row per ticked treaty, and each data set's stochastic plus historical row count equals that treaty's distinct events in the loss table.
- **SC-003**: Every treaty data set can be told apart from its siblings by its data name and its exports-table row alone, without opening Risk Modeler.
- **SC-004**: 0 data sets merge rows from two treaties or two analyses.
- **SC-005**: A failed treaty data set is diagnosed from its row's message and recovered with one Retry, without affecting the other treaties of the same analysis.
