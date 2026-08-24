# IRP Workbench — Design Notes: Submissions Search/Sort, Sub-Portfolio Breakout Refinements & Results Grouping

**Source:** Design session, August 10, 2026 (Ben Bailey, Wendy Hayes, Cheryl TeHennepe — Cheryl joined ~10 min in) — demo-and-refine pass over the redesigned submissions search/list page, the sub-portfolio breakout flow, and a first substantive pass at results grouping. Cross-checked against the full transcript. Fifth session of the series (continues Aug 4/5/6/7).
**Status:** Working design notes. The **search-match semantics**, **submission-list filters/sort**, **duplicate-submission warning**, the **sub-portfolio breakout refinements** (country dimension, name-as-typed, block duplicate names, criteria/lineage surfacing), and the **grouping defaults** (currency, propagate/independent) are agreed as direction. The custom-breakout **"builder" UX** is a prototype to review. The **automated event-rate-scheme selection** exists and is loss-validated but **needs a walkthrough + sign-off** before it's final. Extends `06` (subportfolio breakouts), `04`/`10` (submissions list), and `07` (analysis/currency; grouping context).
**Related:** `04_navigation_page_layout_and_ui_patterns.md` (§2 submissions list, §6 treaty display/export), `06_exposure_modification_subportfolios.md` (breakout actions, granularity cap), `07_analysis_execution_geohaz_currency_accumulation.md` (§4 currency defaulting; grouping), `10_edm_summary_submissions_geohaz_currency.md` (§4 submissions search, Aug 7), `../DATA_MODEL.md` (`irp_portfolio`, submission), `../PRD.md`, `../FUNCTIONAL_REQUIREMENTS.md` (§2 exposure/breakout, §6 grouping), `../../../../CIC/minutes/Risk_Modeler_Interface_Design_Minutes_8-10-26.md`, `../../../../CIC/transcripts/Risk Modeler Interface _ Design 8-10-26.vtt`

---

## 0. TL;DR

A refine pass with no single headline feature — Ben demoed progress since Friday on three areas and collected direction on each. Load-bearing, product-affecting outcomes:

- **Submissions search semantics (the anchor discussion):** name/cedent search matches **each typed term as a case-insensitive "contains," AND-combined across terms** — not exact-phrase, not prefix, not OR. "American" hits the token anywhere in the name; "Spinnaker Arrowhead" returns names containing **both** words anywhere, in any order, not necessarily adjacent. This is what makes the **carrier + MGA "two names in one name"** case findable without a naming convention.
- **Submission list controls:** add **status / type / owner / year** filters with **multi-select**; **single-column** click-sort on name/cedent/inception/year (multi-column deferred); **multiple treaty years** selectable, invalid years rejected.
- **Duplicate-submission detection = warn, not block:** on creation, if a new submission matches an existing one on **name + cedent** (or ≥2 key attributes), surface "you already have a similar submission" and let the analyst narrow by treaty type — but **still allow creation** (same name + cedent, different treaty type is legitimate).
- **Sub-portfolio breakouts (extends `06`):** add **country** as a first-class geography dimension (quick + custom), separable or grouped (US/Canada, North America); **name = exactly what the user types** (drop the base-name prefix, no pre-population); **block duplicate names**; **surface each breakout's criteria + "From" lineage**; keep the accounts count. Rename "group" → **"breakout"** (grouping means *loss* grouping at CIC).
- **Custom-breakout "builder" (prototype):** live in-place preview of selections **before** they commit to the cart; once added, a breakout is locked (remove + recreate to edit). Ben to prototype.
- **Results grouping (first pass):** currency **version** defaults latest w/ override; output **currency** defaults **USD** (or the sole result currency); **propagate detailed output ON** by default, **independent groups OFF**; **automate event-rate-scheme selection** so the user doesn't pick it, ideally folding the rate-version update *into* grouping.

---

## 1. Submissions search / list page

Extends `10` §4 and `04` §2. The Aug 7 session established contains-search + AND-across-fields + click-sort + multi-select filters; this session **pinned down the exact match semantics** against CIC's real MGA use cases and added the creation-time duplicate guard.

### 1.1 Search-match semantics (settled)

| Behavior | Decision | Detail / rationale |
|---|---|---|
| Per-term matching | **Case-insensitive "contains" (substring)** | "American" returns names with the token at the **start or mid-string** — a substring match, not exact-token, not prefix. |
| Multiple terms in one field | **AND-combined** | "Spinnaker Arrowhead" = name contains **Spinnaker** AND contains **Arrowhead**, anywhere, any order, **not necessarily adjacent** — not an exact phrase, not an OR. Ben: *"Does the name contain Arrowhead and Spinnaker? rather than any match on Arrowhead or any match on Spinnaker."* |
| Earlier "phrase / entire string" phrasing | **Superseded** | Ben initially described it as matching "the entire string," then corrected and demoed the per-word AND-of-contains behavior, confirmed live twice and again with Cheryl. Record the AND-of-contains behavior as the decision. |

**Why it matters (the MGA case):** CIC cedents are frequently **MGAs writing on behalf of carriers**, so both a carrier (e.g. American Family) and an MGA (e.g. Arrowhead) land in the submission name — **not adjacent, in no reliable order**, and the cedent field could be Arrowhead, Spinnaker, or United Fire depending on who signed the contract. AND-of-contains lets an analyst pin the specific carrier↔MGA relationship without depending on an exact naming convention. Wendy is "very sensitive to searching" because CIC's current CRM search is poor; the confirmed behavior is what she needs ("as long as I understand it, then I can work with it").

### 1.2 List filters & sort

| Control | Decision |
|---|---|
| Filter fields | Add **status, type, owner, year** (on top of free-text / name / cedent / CRM-ID search). |
| Multi-select | **Yes** on the fixed-option filters. **Treaty year** and **treaty type** multi-select confirmed. |
| Treaty year | **Multiple selectable**; **invalid / nonexistent years rejected**; filters clearable. |
| Column sort | **Single-column** click-sort on name / cedent / inception date / year. **Multi-column deferred** — "one field is sufficient" (Wendy); trivial to add later if real usage demands it. |

### 1.3 Duplicate-submission detection on creation (warn, not block)

- On creating a submission, if it matches an existing one on **name + cedent** (or **at least two key attributes**), surface **"you already have a similar submission"** and let the analyst **narrow by treaty type** to inspect the match.
- **Do not block creation** — "same submission name, same cedent, different treaty type" is a legitimate case; the guard exists to catch *accidental* duplicates (analyst forgot to search first), not to prevent intentional ones.
- Ben's own call; can be removed. **UI gap:** the "you already have…" warning is not obviously clickable — to fix. → O11-4.

---

## 2. Sub-portfolio breakouts (refinements)

Extends `06` (the three single-click breakout actions, granularity cap = state/country). This session refined **geography, naming, validation, lineage display, and the interaction model**. The `06` open questions on commercial geographic splitting (O6-1/O6-2) are unchanged by this session.

### 2.1 Geography & breakout modes

| Item | Decision | Detail / rationale |
|---|---|---|
| Breakout modes | **Keep both** quick and custom | Quick = auto one sub-portfolio per unique LOB or per unique state; custom = assemble LOBs + geographies into a cart, name each, stack several, execute together. Cheryl: keep quick breakout ("for a line of business, it's nice to have it"). |
| **Country dimension** | **Add — quick *and* custom** | State breakout is near-useless for international books; **country is the meaningful cut**. Support US-vs-Canada **separate** and **grouped** (North America / global). Cheryl: "for international business, country would be a much more useful geographical breakout." Adds a grouping dimension the model + UI must support. |

### 2.2 Naming & validation

| Item | Decision | Detail / rationale |
|---|---|---|
| Sub-portfolio **name** | **Exactly what the user types** — no base-portfolio prefix, no pre-population | CIC keeps its own per-project/per-analyst naming conventions and relies on default sort order; auto-prefixing breaks both. Ben: "I'll drop that completely… whatever you put as the name." 40-char name limit stands. |
| **Duplicate names** | **Block on creation** | Risk Modeler technically permits duplicate names *and* numbers (uniqueness is an internal incrementing ID), but CIC has no case for duplicates — refuse and prompt for a different name. Wendy + Cheryl agreed. A deliberate divergence from RM's permissiveness. |
| Portfolio **number** | **Derive from the name, truncated at 20 chars** | The old generated `P4-G-<random>` scheme wasn't useful at a glance and had no technical necessity (number max 20, name max 40). Low effort either way. *(Minor, but changes the stored identifier.)* |
| Terminology | **"group" → "breakout"** | "Group" is loaded — grouping means **loss** grouping at CIC; exposures live in **portfolios**. Use "breakout" / "custom breakouts" / "breakout name." *(Vocabulary, but avoids a genuine domain-term collision.)* |

### 2.3 Criteria lineage & display

- **Store and surface each breakout's criteria** — LOB(s), country, states, currencies — on the table row, plus a **"From" reference** (with hover) naming the base portfolio it was derived from. Add LOB/country to the breakout header.
- **Distinguish Workbench-created portfolios from EDM-native ones** (via the programmatic portfolio number / notes).
- **Keep the accounts count** — a valuable "did I do this right?" signal (Wendy: expecting 10,000 accounts and seeing 27 tells her she missed a filter).

### 2.4 Custom-breakout "builder" UX *(prototype)*

- Add a **live, in-place preview** of current selections **before** "Add" commits them to the cart, so the analyst sees the query taking shape — a shift from "commit blind, then inspect" to "see as you build."
- Once **Add** is hit, the breakout is **locked** (remove + recreate to change).
- **Long LOB/state lists must be alpha-sorted** (LOB by name, states by abbreviation) so selections are findable; keep the display clean (single name; LOB list + state list side by side).
- Ben to **prototype and review next session** (concern: keeping it from getting too busy with many breakouts). → O11-5.

### 2.5 Peril

- All current test portfolios are **single-peril**; the peril breakout option exists but is **untested**. Ben to build a mixed-peril test portfolio (create in RM, pull accounts across perils) and verify. → O11-3.

---

## 3. Results grouping (first substantive pass)

New territory for the design series (grouping was referenced in `07`/FR §6 but not yet designed). Ben flagged grouping as **one of the harder pieces to build — "harder than analysis."** Entry point in Risk Modeler is unintuitive (three-dot menu → "enter analysis to group"); the Workbench should make it a first-class flow: pick analysis results (from the results screen or a specific RDM), choose a grouping selection, configure, run. No limitation across RDMs.

### 3.1 Currency

| Setting | Default | Detail / rationale |
|---|---|---|
| Currency **version** | **Latest**, with override | CIC does **custom / point-in-time conversions** for some clients, so the override must exist — but latest is the sensible default so the analyst rarely configures it. |
| Output **currency** | **USD**, or the sole currency if uniform | USD is the bulk of the book (Cheryl: "I hate to be too US-centric, but that is the bulk of our business"); if all selected results share one currency (e.g. all JPY), default to that. User can change. |
| Currency options list | RMS-provided schemes **+ any custom schemes** the user created | Same for analysis and grouping. |

### 3.2 Grouping settings

| Setting | Default | Detail / rationale |
|---|---|---|
| **Propagate detailed output** | **ON** (user may turn off) | Retain state-level / per-treaty detail through grouping rather than portfolio-level only. Determines what detail survives grouping — a data-fidelity default with downstream reporting impact. Exact definition of what's retained still needed. → O11-1. |
| **Create independent groups** | **OFF** | "We're never going to want to turn those on." |

### 3.3 Event-rate-scheme handling *(needs sign-off — highest-leverage item)*

- The **event-rate-scheme chooser** is blocked when combining two like **DLM** analyses (same rate scheme) and unblocks for **DLM + HD** combinations.
- **Common CIC case:** grouping two North-America-windstorm **DLM** analyses with **different** rate schemes — e.g. a broker's **RiskLink 23** run vs. CIC's **RiskLink 25** run — is **not allowed** until the rate scheme is reconciled **at the analysis level** before grouping (RM won't do the rate-scheme update and the grouping in one step).
- **Design intent:** the Workbench **automates** rate-scheme selection so the user doesn't pick it (gathers the relevant schemes programmatically and passes them to the grouping job), and **ideally folds the rate-version update into grouping** — Cheryl: "definitely be a win," it removes a manual, error-prone pre-step.
- Ben has **validated his method against manual Risk Modeler grouping** (comparing grouped losses) and is confident, but wants to **walk the team through the logic for sign-off** before it's locked, and to describe how the DLM+HD case is handled. → O11-2.

---

## 4. Open questions

- **O11-1** — **"Propagate detailed output" definition** — pin down exactly what detail it retains (state-level, per-treaty) so the default-ON behavior can be finalized and documented (§3.2). *Cheryl / Wendy.*
- **O11-2 (highest-leverage)** — **Event-rate-scheme automation sign-off** — Ben to document and walk through the automated rate-scheme selection, including the DLM + HD case and differing-rate-scheme reconciliation, and confirm whether the rate-version update can fold into the grouping step (§3.3). *Ben → team.*
- **O11-3** — **Multi-peril breakout unverified** — build a mixed-peril test portfolio and confirm the peril breakout option before it's considered done (§2.5). *Ben.*
- **O11-4** — **Duplicate-submission warning** — implement the non-blocking match (name + cedent / ≥2 attributes) and make the warning clearly clickable; confirm the exact attribute set that triggers it (§1.3). *Ben.*
- **O11-5** — **Custom-breakout "builder" prototype** — build the live-preview interaction and review; watch clutter with many breakouts (§2.4). *Ben.*
