# Feature Specification: Analysis Results Sync & Viewing (Iteration 8)

**Branch**: `011-analysis-results` | **Created**: 2026-08-25 | **Revised**: 2026-08-25 (design session 19)

## Status
**Phase:** Ready for implementation — spec, plan and tasks complete; `/speckit-analyze` findings closed 2026-08-26. | **Blocking:** nothing — O-01 closed 2026-08-25 (REST endpoints, live response captures in `research.md#R3`), and irp-integration 0.6.2 closed the T-02 perspective-code dependency 2026-08-26.

## Outcome
An analyst reads the loss numbers of any finished analysis — own executed or broker-provided via RDM — inside the workbench: AAL, standard deviation, and OEP/AEP losses at fixed return periods, per financial perspective, with no analyst action needed to fetch them. One merged table lists own and broker analyses together, and a dedicated results page shows several analyses side by side.

## In scope
- Automatic retrieval of a bounded results extract — AAL, standard deviation, and OEP/AEP losses at 11 fixed return periods, per perspective (GR, RL, WX, QS, GU) — when an own analysis finishes and when an RDM import completes (once per RDM). A few KB per analysis; never row-level data.
- One merged analyses table on the submission Results section and the EDM detail page: own and broker analyses together, origin derived from `rdm_id`, broker rows grouped under the RDM that produced them. One column set for both pages and both origins — Portfolio · Template · Peril · Region · Engine · Currency · AAL · Status · Submitted · Risk Modeler — with an EDM column after Template on the submission page only. A broker analysis has one name, so its name cell takes both the Portfolio and Template tracks.
- The expanded analysis row in two columns: metadata and settings on the left in two named groups (O-11), the condensed results on the right — both EP types at once, perspective toggle in the row, no display toggle.
- Capturing what the expanded row needs: the framework value as its own field, the settings each run was submitted with, and — for broker analyses — the Risk Modeler link and the broker's own run date.
- A dedicated results page reached by multi-select from both the submission page and the EDM detail page, opened in a new browser tab, showing N analyses side by side.
- Copy table with headers, a ones/thousands/millions units selector on the dedicated page, and user-controlled left-to-right ordering.

## Out of scope
- ELT, PLT, and full-EP-curve retention — row-level data exists only for the export iteration; the 8/26 export-requirements session decides that machinery (design note 19 O19-12). ELT-derived metrics (max event loss, record count) are therefore not viewable.
- The comparison cart / percent-difference view (designed in note 19 §6; next iteration), grouping (Iteration 9), Loss Repository export (Iteration 11), pushing broker results anywhere.
- TCE (arrives in the same EP-curve response but is not stored or shown), EP-curve graphs, return-period editing (a Risk Modeler pass-through).
- Any page change outside the analyses tables. In scope: the merged table, its section summary line (status filter, Copy table, Delete, View), the expanded row, and the dedicated results page. The EDM detail header, its portfolio and geohazard sections, the submission header and every other section stay as they are.

## Non-negotiable behavior
1. No analyst action triggers retrieval, and retrieval never runs while a page request waits — it is worker-side in both the own and broker cases. Result views read stored data only; no Risk Modeler call serves a page render.
2. The stored extract is bounded: AAL, standard deviation, and 22 curve points (11 return periods × OEP/AEP) per perspective. The workbench never stores ELTs, PLTs, or full EP curves for viewing.
3. Broker results are stored once per RDM source analysis — on the broker `irp_analysis` row keyed (`rdm_id`, `irp_id`) — shared by every EDM copy; sharing a submission never implies an EDM-to-RDM relationship.
4. A failed retrieval never changes the analysis's FINISHED status — the run succeeded (spec 010 P-14).
5. Displayed numbers are the retrieved numbers; the workbench never recomputes or interpolates losses.
6. A perspective the analysis did not produce is stored as explicitly empty and displayed as absent — it is not a retrieval failure.

## Open product decisions
| ID | Decision | Status | Where |
|---|---|---|---|
| O-01 | Retrieval is the REST stats + EP-curve endpoints, worker-side, storing the extracted subset. Full EP-curve response measured at ~1.2MB per perspective (10,004 points × 4 EP types per call) — parsed in the worker, only the subset stored. Export-job mechanism deferred to the export iteration. | Approved | `research.md#R3` |
| O-02 | Broker exposure pointer: the retrieval worker passes RM's own reported pointer — stored `exposure_resource_id` for broker rows (one `get_analysis_metadata` re-read when NULL), `irp_portfolio.irp_id` for own rows. Verified in the IRP-sandbox tier. | Approved (plan T-03) | `research.md#R2` |
| O-03 | Stored return periods: 5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000 (the expanded display set). Condensed display subset: 50, 100, 250, 500, 1000, 10000. Fixed sets, not user-editable. | Approved | design note 19 D7, 2026-08-25 |
| O-04 | TCE is out of viewing scope. Only OEP and AEP are stored, although TCE-OEP/TCE-AEP arrive in the same response. | Approved | 2026-08-25 |
| O-05 | Broker within-RDM result identity: results live on the broker `irp_analysis` row keyed (`rdm_id`, `irp_id`) — the analysis-name-as-key question moves to the export iteration. | Approved | DATA_MODEL §9 |
| O-06 | Retrieval failure carries spec 010 P-14 unchanged: the retrieval job is marked failed with its reason, interrupted work recovers automatically, views show results-pending, the analysis stays FINISHED. | Approved | spec 010 P-14 |
| O-07 | Financial perspectives: GR, RL, WX, QS, GU (closes note 19 O19-4). Default perspective on every results view: Gross. | Approved | 2026-08-25 |
| O-08 | Results-view layout follows the 8/25 design session, built against Cheryl's Excel comparison view. | Approved | design note 19 §2–§6 |
| O-09 | N-up viewing guideline ~10 analyses, enforced softly — selection is never blocked; past the guideline the table scrolls horizontally (no pagination). | Approved | `research.md#clarifications` (2026-08-26) |
| O-10 | After the dedicated view opens in a new tab, selections in the originating tab reset. | Approved | `research.md#clarifications` (2026-08-26) |
| O-11 | Expanded-row field set, in two named groups in this order — **Metadata**: engine version, analysis type, peril, subperil, framework, event rate scheme, analysis template; **Analysis settings**: currency (code, scheme, vintage), min loss threshold, franchise deductible, unrecognized construction and occupancy. Four fields the expansion shows today are dropped — Construction, Line of business, Term, Loss amplification (PLA) — to be re-added as the team asks for them; Engine type and Region move to the merged table's Engine and Region columns. | Approved | preview approved 2026-08-26 — `docs/ui_previews/merged_analyses_table.html` |
| O-12 | The merged table carries no perspective control and no units control: AAL reads Gross in millions, the perspective toggle sits inside the expanded row, and the units selector belongs to the dedicated results page where analyses sit side by side. | Approved | same preview |

---

## User Stories

### 1. Read a finished analysis's loss numbers (P1)

The analyst runs a suite (spec 010) and waits for an analysis to finish. Without doing anything else, its loss numbers appear: AAL, standard deviation, and OEP/AEP losses at the fixed return periods, switchable across the five perspectives, in the expanded analysis row.

**Acceptance**

1. **Given** an executed analysis that reaches FINISHED, **When** retrieval completes, **Then** its AAL, standard deviation, and OEP/AEP losses at the 11 stored return periods are readable in the expanded analysis row for each perspective the analysis produced.
2. **Given** a perspective the analysis did not produce, **Then** it is stored as explicitly empty and displayed as absent, and the retrieval is not marked failed.
3. **Given** a FINISHED analysis whose retrieval has not completed, **Then** its views show results-pending.
4. **Given** a retrieval that fails, **Then** the retrieval work is marked failed with its reason, the analysis stays FINISHED, and its views keep showing results-pending.
5. **Given** results already stored for an analysis, **When** the retrieval trigger fires again, **Then** nothing is duplicated or re-fetched.

### 2. Broker loss numbers appear on RDM import (P2)

The analyst imports a broker RDM. With no further action, each broker analysis's loss numbers are retrieved and appear wherever that analysis is listed. The extract is stored once per RDM source analysis regardless of how many EDM copies the bundle produced. Reading the broker's numbers early tells the analyst how much work the RDM even needs (design note 05 §1).

**Acceptance**

1. **Given** an RDM import that completes, **Then** broker result retrieval starts automatically with no analyst action.
2. **Given** a bundle producing several EDM copies of one RDM, **Then** the extract is stored once per (`rdm_id`, `irp_id`) broker analysis row, and every copy's broker rows show the same numbers.
3. **Given** a later import of another EDM copy of the same RDM, **Then** retrieval does not run again and nothing is duplicated.
4. **Given** any broker results view, **Then** no broker analysis is attributed to a portfolio (the §2.2 trust rule stands).

### 3. One merged analyses table with inline results (P2)

The analyst opens the analyses table on the submission Results section or the EDM detail page. Own and broker analyses appear in one table: own rows directly (identifiable by the `CRE_` prefix and no RDM), broker rows grouped under the RDM that produced them, expandable. Each row shows currency and AAL; expanding a row shows how the analysis was run on the left — metadata and analysis settings in two named groups — and the condensed results on the right, both EP types at the 6 condensed return periods, with a perspective toggle in the row. The analyst copies the table with headers into Excel.

**Acceptance**

1. **Given** a submission with own executed analyses and an associated RDM, **Then** one table lists both, broker analyses grouped under an expandable RDM row, own-vs-broker readable from the row itself.
2. **Given** a ready analysis row, **Then** it shows its currency (captured at analysis-detail backfill) and its Gross AAL in millions; no return-period column and no perspective or units control appear at table level.
3. **Given** an expanded analysis row, **Then** the left column names the source (portfolio or RDM, plus the analysis id) and lists the Metadata and Analysis settings groups of O-11, and the right column renders OEP and AEP losses at the condensed set (FR-005) together, followed by AAL and standard deviation, with a perspective toggle and no condensed/expanded display toggle.
4. **Given** a field the analysis's origin does not supply — a template setting on a broker analysis, for one — **Then** the field is listed and reads as not returned, rather than being hidden.
5. **Given** a value longer than its column — an event rate scheme name, for one — **Then** it wraps to as many lines as it needs, and every truncated cell carries the full value as a tooltip.
6. **Given** the Submitted column, **Then** it reads as date, time to the second, and AM/PM in the reader's own timezone.
7. **Given** a rendered results table, **When** the analyst uses copy, **Then** the table lands in the clipboard with headers, pasteable into Excel.

### 4. View several analyses on the dedicated results page (P3)

The analyst multi-selects analyses — from the submission page (which alone can reach cross-EDM analyses) or from the EDM detail page — and chooses View. A dedicated results page opens in a new browser tab: expanded view by default (all 11 return periods), both EP types, one column per analysis, a perspective dropdown applying to the whole page. The browser tab is titled with the submission or EDM name; breadcrumbs lead back to where the analyst came from.

**Acceptance**

1. **Given** analyses selected on either the submission page or the EDM detail page, **When** the analyst chooses View, **Then** the dedicated page opens in a new browser tab with one column per selected analysis, expanded return periods, both EP types.
2. **Given** entry from the EDM detail page, **Then** breadcrumbs retain the EDM and the submission; **Given** entry from the submission page, **Then** breadcrumbs retain the submission only.
3. **Given** the dedicated page, **Then** the browser tab title carries the submission or EDM name — never a generic app title.
4. **Given** more analyses selected than the ~10 guideline, **Then** selection is not blocked and the table scrolls horizontally.
5. **Given** the dedicated page, **When** the analyst switches perspective, **Then** every displayed analysis follows (screen-wide, not per column).
6. **Given** several analyses displayed, **Then** the analyst controls their left-to-right order.

## Requirements

- **FR-001**: Loss results for an own executed analysis are retrieved automatically when the analysis finishes; no analyst action is involved.
- **FR-002**: Loss results for broker analyses are retrieved automatically when their RDM import completes, once per RDM source analysis.
- **FR-003**: The retrieval fetches, per perspective (GR, RL, WX, QS, GU): AAL, standard deviation, and the OEP/AEP losses at the 11 stored return periods (O-03). Nothing else is stored — no ELT, no PLT, no full curve.
- **FR-004**: A perspective the analysis did not produce is recorded as explicitly empty (distinguishing "fetched, nothing there" from "not fetched yet") and displayed as absent.
- **FR-005**: Return-period display sets are fixed: expanded = all 11 stored points; condensed = 50, 100, 250, 500, 1000, 10000. Editing return periods or interpolation passes through to Risk Modeler.
- **FR-006**: Re-firing a retrieval trigger is harmless: an analysis with stored results is neither duplicated nor re-fetched.
- **FR-007**: A failed retrieval is recorded with its reason; the analysis stays FINISHED; interrupted retrieval work is recovered automatically (spec 010 P-14).
- **FR-008**: Views for an analysis whose results have not arrived show results-pending.
- **FR-009**: Own and broker analyses are listed in one merged table on the submission Results section and the EDM detail page; own-vs-broker is derived from `rdm_id` (no stored origin column), and broker analyses stay grouped under an expandable row for the RDM that produced them.
- **FR-010**: The merged table's columns are Portfolio, Template, Peril, Region, Engine, Currency, AAL, Status, Submitted, and Risk Modeler, with an EDM column after Template on the submission page only. Peril and region read as codes (`WS`, `NA`); the analysis type and the full analysis name are in the expanded row, not the table; a broker row's single name spans the Portfolio and Template tracks. No return-period column, and the AAL-only display mode is dropped. AAL reads Gross in millions; the table itself carries no perspective control and no units control (O-12). Currency is captured when the analysis detail is backfilled.
- **FR-011**: Expanding an analysis row shows two columns: on the left the source (portfolio or RDM, and the analysis id) followed by the O-11 field groups; on the right the condensed results — both EP types at once, then AAL and standard deviation, a perspective toggle in the row, no condensed/expanded display toggle. The two columns stack when the row is too narrow to hold both.
- **FR-012**: Every results view offers perspective switching across GR, RL, WX, QS, GU, with Gross the default. On the dedicated page the selection applies to the whole screen; in the merged table it applies to the expanded row that holds it.
- **FR-013**: A dedicated results page is reached by multi-selecting analyses and choosing View, from both the submission page and the EDM detail page (the submission page alone reaches cross-EDM analyses and groups).
- **FR-014**: The dedicated page opens in a new browser tab; the selection in the originating tab resets (O-10). Breadcrumbs: entry from the EDM retains EDM and submission; entry from the submission retains submission only. The browser tab title carries the submission or EDM name.
- **FR-015**: The dedicated page defaults to the expanded return-period set with both EP types, plus AAL and standard deviation, one column per analysis. Selection count is not hard-blocked: past the ~10 guideline (O-09) the table scrolls horizontally.
- **FR-016**: The analyst controls the left-to-right order of analyses (and, later, groups) on the dedicated page.
- **FR-017**: Displayed units never switch automatically. The dedicated results page carries a ones/thousands/millions selector (millions default); the merged table's AAL column is fixed at millions.
- **FR-018**: Any rendered results table can be copied to the clipboard with headers, pasteable into Excel.
- **FR-019**: Numbers and tables only — no EP-curve graph.
- **FR-020**: Broker analyses are never attributed to a portfolio in results views.
- **FR-021**: Every stored result records the engine and model version that produced it — for broker analyses captured during retrieval, since nothing else supplies it. The comparison iteration must show which software version each side was run in (design note 18 O18-10); this iteration is the only chance to capture it.
- **FR-022**: The expanded row lists every O-11 field for both origins. A field the origin does not supply — the analysis template and the four analysis settings on a broker analysis — is listed and reads as not returned, never hidden.
- **FR-023**: A value longer than its column wraps to as many lines as it needs; every cell that truncates carries the full value as a tooltip.
- **FR-024**: Submitted reads as date, time to the second, and AM/PM in the reader's own timezone. Own rows show the submit request time; broker rows show the analysis's Risk Modeler create date.
- **FR-025**: Broker analysis rows carry a Risk Modeler link built the same way own rows build theirs. (Which of Risk Modeler's two analysis identifiers that link uses is design note 19 O19-3 and is not settled here — fixing it fixes both origins at once.)

## Key Entities

- **Loss results extract**: the bounded per-analysis record retrieval writes — for each perspective (GR, RL, WX, QS, GU): AAL, standard deviation, and OEP/AEP losses at the 11 stored return periods, plus the engine/model version. Lives on the analysis row (own analyses) or the broker analysis row keyed (`rdm_id`, `irp_id`) (broker), shared by every EDM copy.
- **Submitted analysis settings**: what an own run was actually submitted with — currency (code, scheme, vintage), event rate scheme, min loss threshold, franchise deductible, and the unrecognized construction/occupancy choice. Recorded per analysis at submit time, not read back from the analysis template, so editing a template later never changes what a finished run reports (AGENTS.md architecture rule 8 — approved plans are immutable). Broker analyses have none of it.

## Success Criteria

- **SC-001**: Within 10 minutes of an analysis finishing, its loss numbers are readable in the workbench with zero analyst actions.
- **SC-002**: Importing an RDM containing N analyses yields N loss results extracts, each stored once regardless of how many EDM copies exist.
- **SC-003**: The analyst reads AAL, standard deviation, and OEP/AEP return-period losses for any result, and switches perspective, without leaving the page they are on.
- **SC-004**: Every analysis of a submission is reachable — no listing cap.
- **SC-005**: An analysis whose retrieval failed is identifiable with its failure reason while its run status still reads as successful.
- **SC-006**: The analyst opens five state-level analyses side by side on the dedicated page and pastes the table into Excel with headers intact.
