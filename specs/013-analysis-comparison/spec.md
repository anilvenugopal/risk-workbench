# Feature Specification: Analysis Comparison (Iteration 10)

**Branch**: `013-analysis-comparison` | **Created**: 2026-08-27

## Status

**Phase:** Draft
**Blocking:** Nothing

## Outcome

An analyst compares any two finished analyses — own-executed or broker-provided — as pairs with a percent-change column, on a dedicated page, instead of exporting both to Excel and computing the difference by hand. Own-vs-broker is the headline case; own-vs-own and broker-vs-broker pairs use the same view.

## In scope

- **Compare** as its own action on the merged analyses table, on both entry points (submission Results section and EDM detail page); it needs no prior row selection.
- A modal that collects pairs into a **cart**: exactly two analyses per pair, up to 5 pairs, drawn only from the table at hand and only from analyses with retrieved results.
- **Selection order is the contract**: the first analysis picked is the base and the first column; percent change follows that order.
- The **mixed-currency pair guard**: two analyses run in different currencies cannot be paired — refused at pair-add time, never converted.
- A **dedicated comparison page in a new browser tab**, under the dedicated results page's rules (breadcrumbs by entry point, tab title carries the submission or EDM name).
- Per pair: base column, second column, **% Chg** — AAL, standard deviation, and the selected EP type's losses at the 11 fixed return periods, with currency and engine/model version shown per side.
- Screen-wide **perspective / EP type / units** controls and **copy table with headers**, as on the dedicated results page.

## Out of scope

- Cross-submission pairs — pairs come from the merged table the analyst is on; year-over-year comparison stays served by copy-with-headers (PRD §16.2 D13).
- Currency conversion — mixed pairs are blocked, never converted.
- A base analysis compared against many (considered and rejected, note 19 §6.2).
- EP-curve graphs or overlay charts — numbers only (PRD §16.2).
- Saving or naming comparisons for later; the cart exists while composing, the page renders the pairs it was opened with.
- Loss Repository export (Iteration 11); pushing broker results anywhere; PATE; formal loss validation (PRD §17.4).

## Non-negotiable behavior

1. Comparing is **strictly pairwise**: every comparison is exactly two analyses, and the first picked is the base.
2. A pair whose analyses ran in **different currencies is refused at pair-add time**; the workbench never converts currency. (N-up *viewing* keeps showing mixed currencies unconverted — viewing makes no arithmetic claim; comparison computes a percent change.)
3. The comparison page **reads stored results only** — no Risk Modeler call serves the render (spec 011 rule carried).
4. Displayed losses are the retrieved numbers; the only computed figure is percent change, **(second − base) / base**, per displayed row.
5. Perspective, EP type, and units apply **screen-wide to every pair**, never per pair.
6. A perspective one side did not produce is **displayed as absent** — base numbers shown, no percent change, never an error (spec 011 rule carried).
7. A pair whose analysis no longer resolves is **dropped whole** with a notice; the pairs that resolve render normally.

## Open product decisions

| ID | Decision | Status | Where |
|---|---|---|---|
| P-01 | Compare needs no prior row selection; it is enabled whenever the table holds two or more analyses with retrieved results | Approved | preview `docs/ui_previews/analysis_comparison.html`, 2026-08-27 |
| P-02 | Cart limit: 5 pairs — a layout limit, enforceable; the number is a proposal never confirmed with CIC | Approved | note 19 D18 / O19-8 |
| P-03 | Percent change is (second − base) / base, shown per return period and for AAL and standard deviation; AAL and standard deviation sit outside the EP-type selection | Approved | note 19 D19; note 20 D12/D13; preview §3 |
| P-04 | One analysis may appear in any number of pairs; a pair of an analysis with itself is refused | Approved | clarified 2026-08-27 |
| P-05 | An analysis with no recorded run currency cannot be paired — the guard needs both currencies known | Approved | clarified 2026-08-27 |
| P-06 | Comparisons are not persisted; a hand-typed or stale URL whose pairs do not resolve shows the empty state, not an error | Approved | preview §5–§6, 2026-08-27 |

---

## User Stories

### 1. Compare two finished analyses (P1)

The analyst has two finished analyses with loss numbers in the merged analyses table (spec 011) — user-executed, broker-provided via RDM, or one of each; the pairing does not distinguish. Choosing **Compare**, the analyst ticks one analysis first (marked *base*), the other second, adds the pair, and opens the comparison. A new browser tab shows the two side by side with a percent-change column — the manual Excel step is gone.

**Acceptance**

1. **Given** a submission analyses table holding two analyses with retrieved results in the same currency — user-executed or broker-provided, in any combination, **When** the analyst picks Compare, ticks one analysis then the other, adds the pair, and opens the comparison, **Then** a dedicated page opens in a new browser tab with the first-ticked analysis as the base in the first column, the other second, and % Chg = (second − base) / base at each of the 11 return periods.
2. **Given** the comparison page, **Then** each analysis column header carries the analysis name with the currency and engine/model version that side ran (spec 011 FR-021).
3. **Given** the comparison page, **Then** AAL and standard deviation appear with their own percent change and do not change when the EP type changes.
4. **Given** entry from the submission page, **Then** breadcrumbs retain the submission and the tab title carries the submission name; entry from the EDM detail page retains EDM and submission and the tab title carries the EDM name.
5. **Given** the modeling platform is unreachable, **When** the comparison page renders, **Then** every number still displays — the page reads stored results only.

### 2. Several pairs on one screen, one set of controls (P2)

The analyst builds a cart of pairs — say own-vs-broker and own-vs-own (a before/after) — and reads them all on one page. Perspective, EP type, and units are chosen once for the whole screen, and the table copies into Excel with headers.

**Acceptance**

1. **Given** two pairs in the cart, **When** the analyst opens the comparison, **Then** both pairs render on one page, each as three columns labelled with analysis names — no per-pair label, no row-header column beyond the shared return-period column.
2. **Given** five pairs in the cart, **When** the analyst tries to add a sixth, **Then** the add is refused; comparing the five is not.
3. **Given** a rendered comparison, **When** the analyst switches perspective or EP type, **Then** every pair on the screen updates together; the default view is Pre-Cat Net, OEP, millions.
4. **Given** a pair whose base produced the selected perspective and whose second did not, **Then** the base numbers show, the second reads as absent, and no percent change is shown.
5. **Given** any rendered comparison table, **When** the analyst copies it with headers, **Then** it pastes into a spreadsheet with column headers intact.

### 3. The guards: currency, missing results, vanished analyses (P2)

The pairing rules are enforced where the analyst acts, with a reason, never silently.

**Acceptance**

1. **Given** two analyses run in different currencies, **When** the analyst ticks both in the Compare modal, **Then** adding the pair is refused with a message naming the currency mismatch, and no converted number ever appears.
2. **Given** an analysis still retrieving results or whose retrieval failed, **Then** it is listed in the modal but not selectable.
3. **Given** an open comparison page holding a pair whose analysis has since been deleted, **When** the page renders, **Then** that whole pair is dropped with a notice naming the missing analysis, and the remaining pairs render normally.
4. **Given** a comparison page with no pairs, or none that resolve, **Then** it shows an empty state pointing the analyst back to Compare on a submission or EDM page.

## Requirements

- **FR-001**: Compare is its own action on the merged analyses table, offered on both entry points — the submission Results section and the EDM detail page — and enabled whenever the table holds two or more analyses with retrieved results, independent of row selection (P-01).
- **FR-002**: The Compare modal lists the analyses of the table at hand — own and broker alike — and offers for pairing only those with retrieved results; analyses still retrieving or failed are listed but not selectable.
- **FR-003**: A pair is exactly two analyses; the first ticked is marked *base* in the modal. Selection order, not list order, fixes base, column order, and percent-change direction.
- **FR-004**: Pairs collect into a cart of at most 5 (P-02). Adding a sixth pair is refused; opening the comparison with one to five pairs is allowed.
- **FR-005**: Two analyses whose run currencies differ, or either of whose currency is unrecorded (P-05), cannot form a pair — refused at pair-add time with the reason. Own-analysis currency is the run currency captured at submission; broker-analysis currency is the currency captured at analysis-detail backfill.
- **FR-006**: One analysis may appear in any number of pairs; an analysis cannot be paired with itself (P-04).
- **FR-007**: The comparison renders on a dedicated page in a new browser tab. Breadcrumbs follow the entry point (submission entry retains the submission; EDM entry retains EDM and submission) and the browser tab title carries the submission or EDM name.
- **FR-008**: Each pair renders as three columns — base, second, % Chg — labelled with analysis names, against one shared return-period column for the whole table; no per-pair label.
- **FR-009**: Percent change is (second − base) / base, shown at each displayed return period and for AAL and standard deviation (P-03).
- **FR-010**: Rows are AAL, standard deviation, and the selected EP type's losses at the 11 fixed return periods (spec 011 O-03 expanded set). AAL and standard deviation sit outside the EP-type selection.
- **FR-011**: Each analysis column header shows that side's currency and the engine/model version it ran (spec 011 FR-021).
- **FR-012**: Perspective (GR, RL, WX, QS, GU; default Pre-Cat Net), EP type (OEP or AEP, one at a time; default OEP), and units (ones/thousands/millions; default millions) are screen-wide controls applying to every pair.
- **FR-013**: Copy table with headers works on the comparison page as on the dedicated results page.
- **FR-014**: A perspective one side did not produce displays as absent for that side with no percent change; it is never presented as an error.
- **FR-015**: A pair whose analysis no longer resolves is dropped whole, with a notice naming the missing analysis; remaining pairs render. A page with no resolvable pairs shows an empty state directing the analyst to Compare on a submission or EDM page.
- **FR-016**: The comparison page reads stored result extracts only; no call to the modeling platform serves the render.

## Key Entities

- **Comparison pair**: an ordered pair of finished analyses (base, second) drawn from one merged analyses table, sharing a run currency. Not persisted — it exists in the cart and in the opened page.
- **Comparison cart**: the pairs collected in the Compare modal, at most 5, discarded once the comparison opens or the modal closes.

## Success Criteria

- **SC-001**: An analyst compares their own result with the broker's entirely inside the workbench — from the merged analyses table to a rendered percent-change column in under a minute, with no export and no spreadsheet arithmetic.
- **SC-002**: Up to five pairs read on one screen, and one perspective or EP-type change updates every pair at once.
- **SC-003**: 100% of mixed-currency pairing attempts are refused at pair-add time; no converted figure ever appears anywhere in the feature.
- **SC-004**: Comparison pages render fully while the modeling platform is unreachable.
- **SC-005**: Every rendered comparison shows which engine/model version each side ran — the "ran in 23 doesn't mean 23 rates" gap (note 18 O18-10) is closed for this view.
