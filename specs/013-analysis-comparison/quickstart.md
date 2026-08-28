# Quickstart — Verifying Analysis Comparison

## Prerequisites

- Stack up (`make dev-up` — the developer starts it, not an agent) with a
  submission holding at least two analyses whose results are retrieved
  (`loss_results` set): easiest is one own-executed analysis (spec 010) plus
  one broker RDM import (spec 004/011) on the same submission, run in the
  same currency. For the currency guard, one more analysis run in a different
  currency (or a broker analysis whose `settings_metadata` carries another
  `currencyCode`).
- Unit tier from any host shell: `uv run pytest tests/unit`.

## Story 1 — Compare two finished analyses

1. Open the submission → Results section. **Compare** sits beside View,
   enabled without ticking any row (FR-001).
2. Choose Compare: the modal lists the table's analyses, own and broker
   alike; tick one (it is marked *base*), tick another, **Add pair**, then
   **Compare 1 pair**.
3. A new browser tab opens `/results/comparison?pairs=…`. Verify:
   - First-ticked analysis is the first column; **% Chg** =
     (second − base) / base at each of the 11 return periods (FR-003/FR-009).
   - Each column header carries that side's currency and engine/model version
     (FR-011) — the SC-005 check.
   - AAL and Std dev rows carry their own percent change and do not move when
     the EP type changes (FR-010).
   - Breadcrumbs retain the submission; the tab title carries the submission
     name. From an EDM entry, EDM and submission crumbs and the EDM name
     (FR-007).
4. Stop the poller/workers (or disconnect from Risk Modeler) and refresh the
   comparison page: every number still renders — stored extracts only
   (FR-016, SC-004).

## Story 2 — Several pairs, one set of controls

1. Build two pairs in the cart (e.g. own-vs-broker and own-vs-own) and open
   the comparison: both pairs on one page, three columns each, one shared
   return-period column, no per-pair label (FR-008).
2. Add pairs to 5; a sixth **Add pair** is refused; **Compare 5 pairs** works
   (FR-004).
3. Switch perspective, then EP type: every pair updates together; defaults on
   open are Pre-Cat Net · OEP · millions (FR-012). Switch units: loss numbers
   rescale, percent values do not.
4. Pick a perspective only one side produced: base numbers show, the other
   side reads absent, % Chg is a dash — no error (FR-014).
5. **Copy table**, paste into a spreadsheet: headers intact (FR-013).

## Story 3 — The guards

1. In the modal, tick two analyses run in different currencies: **Add pair**
   is refused with a message naming both currencies; nothing is converted
   (FR-005, SC-003). The ticks stay for re-picking.
2. An analysis still retrieving (or whose retrieval failed) is listed but not
   tickable (FR-002).
3. Open a comparison, delete one paired analysis from its EDM page, refresh
   the comparison tab: that pair is dropped whole with a notice naming the
   missing analysis; remaining pairs render (FR-015).
4. Hand-type `/results/comparison` with no `pairs`, with garbage ids, or with
   a mixed-currency pair: the empty state (or the drop notice + surviving
   pairs) — never an error page, never a converted or cross-currency figure
   (P-06, T-01, SC-003).

## Test commands

| Tier | Command | Covers |
|---|---|---|
| Unit | `uv run pytest tests/unit` | Pair validation, percent math, read models, route renders (plan.md Testing) |
| SQL Server | — | Nothing new this iteration (no schema change) |
| IRP sandbox | — | N/A (no IRP call) |
