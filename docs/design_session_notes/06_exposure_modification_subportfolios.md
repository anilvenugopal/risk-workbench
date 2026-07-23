# IRP Workbench — Design Notes: Exposure Modification & Sub-Portfolio Breakouts

**Source:** Design session, July 16, 2026 (Ben Bailey, Cheryl TeHennepe) — walkthrough of Risk Modeler's manual sub-portfolio flow, then how the workbench should streamline it. Cross-checked against the full transcript.
**Status:** Working design notes. The three **single-click breakout actions** are agreed for Ben to build; the **commercial geographic-split behavior** is a flagged investigation with a team poll pending (Cheryl); **merge** is de-scoped.
**Related:** `04_navigation_page_layout_and_ui_patterns.md`, `07_analysis_execution_geohaz_currency_accumulation.md`, `../DATA_MODEL.md` (§5, `irp_portfolio`), `../PRD.md` (§10.3, §14.3), `../../../minutes/IRP_Workbench_Design_Minutes_7-16-26.md`, `../../../transcripts/IRP_Workbench_Design_7-16-26.vtt`

---

## 0. TL;DR

Sub-portfolio creation — re-shaping exposure to match treaty terms before analysis — **cannot be done in the current workflow tool** (it's done in RiskLink today, which is painful and slow). Risk Modeler makes it **fast and synchronous**, so it becomes a *preferred* path rather than a last resort. The workbench's job is to collapse RM's many-click flow into **single-action buttons** for the predictable cases:

1. **By line of business** — one sub-portfolio per LOB (the simplest case, "super easy").
2. **By state / country** — one sub-portfolio per geography.
3. **"X vs. not-X" complement split** — pull out one or more states (e.g. Florida, or a Northeast group) into one portfolio and everything else into another, from a single action.

**Granularity cap: state / country.** Anything finer (CRESTA, ZIP) is **saved as output**, not as a portfolio — "it's just too much to manage." Peril splitting is **no longer needed.** A **merge/combine** action was proposed and **de-scoped**.

The one real complication is **commercial policies spanning many states** (§3), which breaks clean geographic splitting.

---

## 1. Why this matters more here than today

- **Not possible in the current workflow tool.** "We can't do that in our workflow tool… we have to do it in RiskLink today."
- **RiskLink is painful/slow** for it — every new portfolio creates "a million new records and a million new tables" (multi-relational DB indexing). Risk Modeler is "nice and fast" — "switching to this design naturally makes it a lot faster."
- **Portfolio manipulation is synchronous and the fastest thing in the whole flow.** (Confirms DATA_MODEL: `create_portfolio()` is a synchronous HTTP 201, no job.)
- **It substitutes for back-end SQL.** Today analysts often avoid sub-portfolios (slow in RiskLink) and instead **write losses out by state and use SQL** to build grouped results/ELTs — and they're **faster in SQL than in the UI** today. That back-end SQL manipulation **won't be as easy** in the new tool, so **fast portfolio creation becomes the preferred path**. (This is a deliberate workflow shift, not just a feature.)

---

## 2. The RM manual flow (reference) and the desired single-click behavior

**RM manual flow:** right-click **Portfolios → New Portfolio** → number/name → add tags → **Filter** → build/apply a filter (e.g. LOB = Caribbean flood) → **Apply**. No need to save the filter to the tenant unless it will be reused. Filters support **saved/reusable filters** and full **AND/OR logic trees**. Clean, but "a lot of clicks."

**Filter criteria that matter — LOB and geography** (the two "down-the-middle" cases):

| Dimension | Detail |
|---|---|
| **Line of business** | Values are **populated from what's actually in the portfolio** (from the EDM data) — so the UI must **pick from real values, not free-text**: "sometimes people put crazy things in that line of business field, and to have to type it exactly how they put it in is messy." One policy = one LOB, so all its locations travel with it cleanly. |
| **Geography** | RM shows **real place names** (country, state name, state code), not raw admin-1/admin-2 codes. Selection level "0" returns everything. |

> LOB and cedant data arrive with the EDMs; **cedant is sometimes populated, sometimes not.**

### 2.1 Agreed single-action breakouts (Ben to build)

- **(a) By line of business** — break a portfolio into N sub-portfolios, one per LOB. Simplest case. *(Unaffected by the commercial problem in §3.)*
- **(b) By state / country** — break into N sub-portfolios by geography.
- **(c) "X vs. not-X" complement split** — one or more states into one portfolio, everything else into another, from a single action.

**Design preferences:**
- Breakouts should **sum to 100%** — Cheryl wants "my portfolios to equal 100% in the end," not "run the whole portfolio, then a subset, and subtract, because that's messy."
- **"Do the opposite" option** — define the logic once and get the complement without re-coding it (e.g. "Florida mobile home" **and** "everything that's not Florida mobile home").
- **Granularity cap: state / country.** Finer than that → save as **output**, not a portfolio.
- **Peril splitting is no longer needed** — "we really don't have to split it up by peril."

### 2.2 The Northeast / treaty example (why complement splits exist)

A treaty may cover only the **Northeast**, but the cedent sends the **whole book** because everything applies to the retention. Cheryl must run **two portfolios** — Northeast **and** everything-not-Northeast — to see the treaty's risk correctly.

**Regions are not constant.** "Northeast" is defined by the treaty / how the cedent writes the business, so regions **cannot be pre-defined as a fixed constant** in the tool. (CIC has internal region definitions, but contracts define coverage however they want.) Another example: a treaty that excludes Florida, or covers Florida-only because "they think it makes their reinsurance cheaper." Cheryl: "these aren't super common examples, but they do happen."

---

## 3. Commercial-policy geographic-split problem (flagged for investigation)

Cheryl's "monkey wrench": geographic splitting is clean for **personal lines** (one policy → one house) but problematic for **commercial** business, where **one policy** (e.g. a Walmart policy) covers buildings spread across **many states**.

- **Splitting a multi-location commercial policy geographically breaks the financial structure.** A ~$5,000 Florida building will never trigger a **$20,000 policy deductible** on its own — "it would have to be some combination of a Florida, North Carolina, South Carolina event" to hit it.
- **Two possible behaviors** when filtering geographically:
  - **(a) keep ALL locations** tied to a policy if **at least one** location meets the criteria, or
  - **(b) keep ONLY the matching locations.**
- **The "keep-all-locations" behavior double-counts in a complement split.** With behavior (a), a "keep Florida" filter on a 100-building policy (one building in FL) pulls **all 100** into the Florida portfolio; the not-Florida run then picks up **the same account/exposures a second time.** This directly conflicts with the "sum to 100%" goal. Cheryl: "no way is perfect when you want to split something geographically but the policy is not geographically split."
- **The RiskLink "checkbox" is an UNCONFIRMED recollection.** Cheryl first said RiskLink had a checkbox governing (a) vs (b), then second-guessed herself: "maybe I'm getting Verisk and Risk Link and Moody's mixed up… maybe Risk Link always takes all the locations." **Do not treat "replicate the RiskLink toggle" as a firm requirement.** She **does not see this checkbox in Risk Modeler** — Moody's may have changed the methodology or set a default; "I just don't know what that decision is."
- **LOB splitting is unaffected** — one policy = one LOB, so all locations travel with it cleanly. The problem is **geography-specific.**
- **Output-side alternative.** When the exposure-side split is too messy, handle it on the **output side**: write losses to the **state level** and let the model **allocate back** ("roll up at the policy level and then allocate back to the state"). Cheryl leans this way — "we try really hard not to create sub-portfolios if we don't have to."

**Actions:** Ben to **investigate what RM does under the hood** (keep-all vs keep-matching, and whether RM exposes any toggle — "if there's even an option, which I don't think there is"). Cheryl to **poll her team** on the preferred default and whether a user toggle is wanted, then note Ben.

---

## 4. Merge / combine portfolios — de-scoped

**Not in the minutes.** Ben floated a merge action ("pick two portfolios… merge these"). Cheryl **deprioritized it to near-zero**: "I'd put that down the list… it's more likely we need to break things out than put them back together." On reflection she couldn't find a use case: "I really don't have a need to do it… I would just run them, split, **group the results, not the exposure**, because that process is easy to do."

**Disposition:** recombination happens on **results/output**, not exposure — record merge as **out of scope**.

---

## 5. Relationship to analysis execution

Sub-portfolio creation feeds directly into running analyses (batch across the resulting portfolios) and into grouping results back together. See `07_analysis_execution_geohaz_currency_accumulation.md` (running analyses, suites) and FR §6 (grouping).

---

## 6. Open questions

- **O6-1 (blocking for breakout (b)/(c)):** Commercial geographic split — what does RM do under the hood (keep-all vs keep-matching), does it expose a toggle, and what default does CIC want? (§3) *Ben investigating; Cheryl polling the team.*
- **O6-2:** Confirm the "keep-all-locations" RiskLink checkbox history is **not** load-bearing before designing any toggle around it (§3).
- **O6-3:** Confirm state/country as the firm granularity cap for portfolio creation (finer → output only) (§2.1).
