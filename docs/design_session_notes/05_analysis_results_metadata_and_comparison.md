# IRP Workbench — Design Notes: Analysis Results, Metadata & Comparison

**Source:** Design session, July 14, 2026 (Ben Bailey, Cheryl TeHennepe). Cross-checked against the full transcript.
**Status:** Working design notes. The **group-by-RDM** decision and the **analysis-metadata list** are agreed; **event-rate-scheme round-trip loss** and **editable return periods** carry open/investigation items; **Post-Analysis Treaty (PATE)** is captured as a deferred fringe case.
**Related:** `04_navigation_page_layout_and_ui_patterns.md`, `01_data_model_and_workbench_organization.md`, `../DATA_MODEL.md` (§6, §9), `../PRD.md` (§16), `../../../minutes/IRP_Workbench_Design_Minutes_7-14-26.md`, `../../../transcripts/IRP_Workbench_Design_7-14-26.vtt`

---

## 0. TL;DR

Ben's own framing from 7/7 held up: rolling up **exposure** is easy; the **hard** problem is presenting **analysis results in relation to portfolios**. The 7/14 resolution is to **stop trying to solve portfolio↔analysis linking** (it doesn't exist today either) and instead **group results by the RDM that produced them** — which matches the current workflow and is already implied by EDM + RDMs sharing a package.

The rest of the session pinned down **which analysis attributes/metrics matter**, that the **numbers matter more than the EP-curve graph**, that **financial-perspective switching is essential**, and that **side-by-side comparison** (with a percent-difference column) is a high-value feature Ben can build on prior work.

---

## 1. No portfolio↔analysis linking — group by RDM instead

- **The linkage does not exist today.** Ben asked how analyses are correlated to exposure; Cheryl: "It doesn't." They rely on **naming conventions** plus a **document that links results back to the EDM**.
- **Grouped / independent-group results often carry no detail at all** — "all we get are the losses" — so even today the linkage can be genuinely unknown ("we have zero idea what it was run against").
- **Linking analyses back to specific portfolios is NOT a problem the workbench needs to solve** — it's rare, not the "80% down the middle," and Cheryl gets what she needs from documentation/naming while inside the product.
  > *Nuance (transcript):* Ben initially over-claimed he could auto-link ("I can automatically know that these analysis results… are for this portfolio"), then corrected himself a minute later ("what we don't have is which portfolio was this analysis run against"). The optimistic auto-link framing collapsed into the group-by-RDM decision below.
- **Decision: display results grouped under the RDM that produced them.** Because EDM + RDMs live in the same package, their relationship is already implied. Cheryl: "that makes sense to me." (This is the source-of-truth for the DATA_MODEL "broker results dedup by `rdm_id`" design — §6/§9.)

**An RDM may not need importing into Risk Modeler at all.** Sometimes the broker already provides what's needed and Cheryl only has to **push the loss results to CIC's repository** — "I have a process for doing that today that will still work," independent of any remodeling. The results view should help her decide **how much she even needs to do** with a given RDM.

---

## 2. Analysis metadata / settings to display

All deemed important; the analyst reads these to understand and to **reconcile CIC's internal pricing standard against what the broker provided** (Cheryl: "what we use internally for pricing… isn't always the same as what brokers will provide to us").

| Attribute | Notes |
|---|---|
| **Engine / model version** | e.g. v23 vs v25 — "just because they ran it in 23 doesn't mean they used the 23 rates." Version and rates are independent. |
| **Engine type** (DLM vs HD) and version | |
| **Analysis type / mode** | |
| **Peril — primary and secondary** | e.g. storm surge on/off. |
| **Region** | |
| **Currency** | |
| **Construction** | |
| **Line of business** | |
| **Group type** | |
| **Long-term vs. near-term** | |
| **Event-rate scheme / rate vintage** | See §3 — stored but buried; also the round-trip problem. |
| **Loss amplification (PLA)** | Whether it was turned on, and **consistently across analyses** — "Is it turned on for everything? Was it turned on the same for all of them?" |

**RiskLink grid vs. "analysis summary" (where the data lives).** In RiskLink the main results grid shows: description, model version, group type, construction, cedant, line of business, engine, DLM, date type, region, mode, currency, status. The **event-rate / rate specifics are NOT in that grid** — you must open the per-analysis **"analysis summary"** (shown at the **gross** perspective by default) to see rates. **Design implication:** rate data lives **one drill-down deeper** than everything else.

---

## 3. Event-rate scheme — storage, display, and the round-trip problem

- The rate set **is stored** (in the RDM analysis table / RiskLink "analysis summary") but is **not surfaced in the main display screens** — you must open each analysis to see it.
- **Investigation (Ben):** the **event-rate scheme does not survive an RM export → re-import round-trip** — exactly the broker scenario. Near-term vs. long-term and rate **vintage** both matter, so this loss is a real problem. Ben to investigate how to recover/carry it. (Open sub-question: whether "vintage" is even a first-class Risk Modeler concept.)
- **Don't over-engineer multi-rate-set display.** Multiple event-rate sets on one analysis *can* occur (one region's rate less current than another's, run together), but Cheryl couldn't recall it happening and called it "a display issue" — "this is more common that you have a single rate set." Treat multi-rate-set as an edge case, not a core display mode.

---

## 4. Metrics (losses)

| Metric | Detail |
|---|---|
| **Return periods** | A handful of points: roughly **1000, 500, 250, 100, and ~20–25**. Cheryl does **not** need the full curve — "in my head, I can draw that curve." *(Transcript note: she named two slightly different sets across the session — "500, 250, 100" and "1000/1500, 250, 100, ~20–25"; take the set as indicative, confirm the exact points.)* |
| **AAL / pure premium** | |
| **Standard deviation** | |
| **Financial perspective switching** | **Essential.** Gross, net, working access — "look at it from however you ran it." |
| **OEP and AEP** | Both available. Cheryl uses **OEP** more; **TCE** not routinely used but nice to toggle. |
| **EP-curve graph** | **Not needed** — "the drawing's not important… I want the numbers." |

### 4.1 Editable return periods / interpolation → pass-through

RM/RiskLink can **interpolate** a loss at a typed return period on the fly. Cheryl first called this non-critical, then **walked it back**: a **subset of business needs return periods at specific loss intervals.** Resolution: **pass through to Risk Modeler** ("if you need to edit return periods, click this button, we'll throw you into Risk Modeler") rather than build it — Ben estimated at least a medium dev effort. (See the pass-through pattern in `04` §7.)

---

## 5. Analysis comparison (side-by-side) — high value

- Cheryl finds **RiskLink's** side-by-side comparison **better than Risk Modeler's** ("they actually made this worse in Risk Modeler"). Today she works around RM by copying analyses out or using a **SQL query**.
- Ben has **previously built a comprehensive analysis-comparison engine** he can lean on.
- **Requested enhancement:** a **percent-difference column** in the side-by-side view (e.g. CIC vs. broker) to **save the manual Excel step**.
- Own and broker results should be viewable **together** — multiple analyses in one view (consistent with FR §7).

---

## 6. Post-Analysis Treaty (PATE) — deferred fringe case

**Not recorded in any minutes; surfaced only in the transcript.** Capturing it so it isn't lost.

- Risk Modeler offers a **post-analysis treaty option** (the vendor "likes to call PATE"): you can **add a cat treaty onto broker-provided analysis results after the fact and re-process / re-simulate** to get updated losses.
- **Constraint:** only works for treaties that apply at the **portfolio level.** "If it applies at the location and policy account level… it can't go back and redo all of that work."
- **Use case:** group scenarios where individual insurers' **own cat treaties must come out (as inuring)** before the group-wide reinsurance applies.
- **Rarity/priority:** explicitly a **fringe case** — "that's really rare… not the most common thing we do."
- **Disposition:** Ben deferred it — "I'll probably leave that for the super [later tier]." Record as **out of MVP scope**; there is an associated "add a cat treaty" affordance on the broker-analysis view if it is ever built.

---

## 7. Volume handling (carried forward, still valid)

From 7/7 (§7 of the exposure/results discussion) and unchanged: analysis counts per submission are **highly variable (4 to 100+)**. Working guideline: **≤5 consumable on screen; >5 exportable / drill-down**, while still conveying the overall package.

> *Caveat (7/7 transcript):* Cheryl can already list **all** analyses in RiskLink today. The ≤5-on-screen guideline must **not** become a step back — **full drill-down / full listing must remain available**; the on-screen cap is about default density, not a hard limit. Cheryl hedged the threshold ("I could be wrong").

---

## 8. Open questions

- **O5-1** — Recover/carry the **event-rate scheme** across RM export→re-import; confirm whether "vintage" is an RM concept (§3). *Ben investigating.*
- **O5-2** — Confirm the **exact return-period set** to display by default (§4).
- **O5-3** — Confirm the **default financial perspective** in the results view (RiskLink analysis-summary defaults to gross; §2/§4).
- **O5-4** — Confirm PATE stays out of MVP scope (§6).
