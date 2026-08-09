# IRP Workbench — Design Notes: EDM Summary & Submissions List Refinements — GeoHaz Display, Currency Detection, Search/Sort

**Source:** Design session, August 7, 2026 (Ben Bailey, Wendy Hayes, Cheryl TeHennepe) — live walkthrough of the redesigned EDM/portfolio summary page and the submissions search/list page, validating real EDM data against each. Cross-checked against the full transcript. Fourth session of the week (continues Aug 4/5/6).
**Status:** Working design notes. The summary-page trims, the **GeoHaz on-screen policy** (display no version stamp; offer hazard-lookup action), the **comprehensive currency-detection rule**, and the **submissions search/sort/filter** behavior are agreed as direction. Two items need external confirmation (geocode/hazard "stamp" origin with Moody's; CIC's full currency-field list from Wendy). Reaffirms and extends `07` (GeoHaz/currency) and `04` (submissions list, treaty display).
**Related:** `04_navigation_page_layout_and_ui_patterns.md` (§2 submissions list, §6 treaty display, §7 pass-through), `05_analysis_results_metadata_and_comparison.md`, `06_exposure_modification_subportfolios.md` (subportfolio breakout, Aug 6), `07_analysis_execution_geohaz_currency_accumulation.md` (§1 hazard lookup, §4 currency), `../DATA_MODEL.md`, `../PRD.md`, `../FUNCTIONAL_REQUIREMENTS.md` (§2.x exposure/summary), `../../../../CIC/minutes/Risk_Modeler_Interface_Design_Minutes_8-7-26.md`, `../../../../CIC/transcripts/Risk Modeler Interface _ Design 8-7-26.vtt`

---

## 0. TL;DR

A data-validation and screen-refinement session (no single headline feature). Ben walked the redesigned **EDM/portfolio summary page** and the **submissions list**, validating live data and collecting change requests against each. Load-bearing outcomes:

- **EDM summary page trims:** drop the **EDM rollup** and the **TIV column**; add a **countries column**; keep the **portfolio count**; filter **lines of business to only those present** in the portfolios; show EDM lineage (package/submission) as **lists, not a one-to-one breadcrumb**; collapse **RDM** rows; **stop relating broker analyses to portfolios**; add **attachment basis** to the treaty headers.
- **GeoHaz display policy:** the Workbench **assumes geocoded + hazard-retrieved exposure**, shows **no geocode/hazard version info on-screen**, but **offers a from-screen hazard-lookup action**. A live analysis on data with no version stamp **ran fine**, confirming the stamp is not load-bearing for already-geocoded data. (Extends `07` §1.)
- **Currency detection must be comprehensive:** the current single-source read is "too naive." Scan **all** currency-bearing locations (policy level, location-coverage level — ~5 places) and return the **unique set**. (Extends `07` §4.)
- **Submissions list:** **contains/fuzzy** text search; **multiple search fields combine as AND**; **click-sortable** columns (default **inception date descending**, single-column baseline); **multi-select filters** on fixed-option fields (status, owner, treaty type, treaty year); **owner defaults to the logged-in user**; **copy/paste-able CRM ID**. Page purpose is "**to get to a submission … not for viewing.**"
- **Data volume / backfill:** **no historical backfill** — add submissions as you go. Volume is modest (**< ~1,000/year, ~600–700**); CIC is not decommissioning RiskLink.
- **Operational:** the recurring **MDF → DataBridge** import failure (stalls ~22%) **resolved itself mid-call**; intermittency to be raised with Moody's.

---

## 1. EDM / portfolio summary page — validation & changes

Ben validated the page against a live EDM and collected changes. The guiding principle from prior sessions held: surface what the analyst needs to orient and catch problems, cut the rest.

| Change | Decision | Detail / rationale |
|---|---|---|
| **EDM rollup** | **Remove** | Adds no value — go straight from the metadata section to the portfolios. |
| **TIV column** | **Remove** | |
| **Countries column** | **Add** | Needed for international books (see §1.1). |
| **Portfolio count** | **Keep** | Still listed in the metadata section. |
| **Lines of business** | **Filter to relevant only** | Show only LOBs actually present in the portfolios, not the full LOB table. Cheryl: "I like that we [reduced] the noise of the lines of business … you're just pulling up the lines that are relevant to the portfolios. That's great." Full list still reachable by clicking into the table row. |
| **EDM lineage** | **List, not breadcrumb** | An EDM can belong to multiple packages and (future state) multiple submissions; list package(s) and submission(s) in the metadata section. Ben: "It'll be more like a list rather than a breadcrumb, because it's not going to be a one-to-one mapping." |
| **RDM rows** | **Collapsible** | Collapse within the table when there are several. |
| **Broker-provided analyses** | **No longer related to portfolios** | Remove that column from both tables. |
| **Treaty headers** | **Add "attachment basis"** | Distinct from attachment *point* — basis is risks-attaching vs. losses-occurring. Belongs on the treaty summary. |
| **Treaty LOB list** | **Keep full, even if long** | 61 LOBs on one treaty is realistic — cedents "just select everything that's available" rather than clicking individual lines. Preserve as-is. |

### 1.1 States vs. countries (international books)

- **Collapsed country view:** a single country name means **only that country** *unless it says "multi."* Wendy confirmed the reading; Ben: "Correct."
- **Sub-country geography** (cities/provinces/cantons) is drawn from the **same exposure attribute (Admin1)** as US states, so it appears for non-US too. It is **acceptable but low-value** — Cheryl: "what I care about is the countries up above … when it's an international book, I want to know what countries." Leaving the sub-level in is "fine either way."
- Empty **state** value on a Benelux portfolio was noted as **expected** for international, not a defect.

---

## 2. GeoHaz — geocode/hazard version stamp & on-screen handling

Extends `07` §1 (hazard lookup only, never re-geocode by default). This session settled how geocode/hazard should surface on the summary screen.

- **The problem Ben raised:** the test portfolio arrived with **no geocode-version or hazard-version stamp** in RM metadata, and he worried an analysis would fail. Two possibilities: (1) RM does a **naive check** on the stamp and refuses to run — "that's a problem"; or (2) RM inspects the **underlying attributes** (lat-long, parcel-level geocode) — "that's okay."
- **Live test result:** Ben **ran the analysis and it succeeded** — "clearly that metadata stamp doesn't matter because the exposure was geocoded." Cheryl confirmed the data was geocoded to parcel level and noted **even Moody's own Japan industry exposure DB lacks the stamp** yet is usable. Wendy's caveat: it *would* matter when starting from un-geocoded CSV text (a broker would never send un-geocoded data — they couldn't have run their analysis).

**Agreed policy (Ben's summary, both endorsed):** *"Geocode and hazard information on the screen — no. Ability to execute hazard lookup from the screen — yes."*

| Aspect | Decision |
|---|---|
| Display geocode/hazard **version** on the summary screen | **No** — it's "nice to have, not a must-have." Cheryl can bound the hazard version from the RiskLink version (a RiskLink 23 EDM can't hold RiskLink 25 retrieval). |
| **Re-geocode** incoming data by default | **No** — reaffirms `07` §1.1. |
| Offer a **from-screen hazard-lookup** action (one or more selected portfolios), with lineage of what was run | **Yes** |
| Rely on a **naive stamp read** to gate anything | **No** — Ben to research what hazard-execution detail can be surfaced meaningfully. |

---

## 3. Currency detection

Extends `07` §4 (currency defaulting rule at analysis time). This is a distinct concern: **detecting** the set of currencies present in an exposure for the summary display.

- Currency can be defined in **multiple places** — policy level and location-coverage level, "maybe like 5 places." A single-table read under-reports.
- **Rule:** the summary must **scan the full set of currency-bearing locations and return the unique set** of currencies. Ben: "this is too naive. We need to be checking the full set of spots where we can have currencies defined and finding all the unique currencies."
- Wendy is aware of exactly which levels/fields to check and CIC has a query for it. **She will send Ben the currency-check query and the additional fields.** → O8-2.

---

## 4. Submissions search / list page

Extends `04` §2 (submissions list). Framed against how much CIC's current CRM search "sucks" (can't combine name + submission filters; over-matches on common tokens; no useful sort). The Workbench already addresses most of it.

| Behavior | Decision |
|---|---|
| Text/cedent search | **Contains (fuzzy), case-handled.** "American" matches anywhere; "Test American" narrows. |
| Multiple search fields | **Combine as AND** — "these combination of these fields are all AND statements." |
| Column sort | **Click-to-sort with up/down caret**, default **inception date descending**. **Single-column** is the baseline (one or two at most) — multi-column sort not worth over-engineering. Wendy: "you sort of have to balance the effort with the benefit." |
| Fixed-option fields (**status** [active / on hold / canceled / +1], **owner**, **treaty type**, **treaty year**) | Use **multi-select filter dropdowns** rather than column sorting. Multi-select confirmed for treaty year and treaty type. |
| Default owner filter | **Logged-in user's own submissions**, with the ability to filter to any/other owners. (Consistent with `04` §2 ownership model — soft owner, not an access gate.) |
| **CRM ID** cell | **Must be copy/paste-able**, not a navigate-away hyperlink. Ben to fix ("it's not that bad"). |
| Export to Excel | Already added for treaties (per `04` §6). |

**Page purpose (Cheryl):** "the point of this is to get to a submission, to click on and go into it. It's **not for viewing**." This scoped the sort/filter ambition down deliberately.

---

## 5. Data volume & initial-load strategy

- **No historical backfill of submissions — add as you go.** Wendy: "we'll add as we go. I don't think we're going to backfill … I'm not sure there's a big enough payoff," because CIC is **not decommissioning** its RiskLink hardware/servers (the history stays reachable there).
- **Volume:** **under ~1,000 submissions/year (~600–700).** The platform is built to handle far more; this is not a scale concern.

---

## 6. Operational: MDF → DataBridge upload failure (recap)

Not a Workbench design item, but it opened the call and affects the import path assumption (Workbench defaults uploads to DataBridge via the Platform APIs; see `04` §8 import flow).

- Cheryl's **EDM master data file (MDF) uploads to DataBridge failed repeatedly** through Risk Modeler — the *upload* completed but the **EDM import job stalled ~22%** then errored (two attempts died at the same point). Direct-to-Platform upload worked; only the DataBridge path failed.
- Timing on one test: raw S3 upload ~4 min for both MDF and BAK, but the **MDF import job ran ~16–17 min vs. ~4 min for a similarly sized BAK.**
- Ben raised, then discounted, the theory that RM's use of the **`/riskmodeler/v1` APIs** (vs. the newer **Platform APIs** the Workbench uses) is the root cause.
- **Resolved itself mid-call** — Cheryl's MDF uploaded to DataBridge that day. The **non-reproducibility** is itself a concern; Cheryl to report back to Moody's (Zach Estes). → O8-1.

---

## 7. Open questions

- **O8-1** — **Geocode/hazard "version stamp" origin & behavior** — confirm with Moody's where the stamp comes from and exactly what it gates, so the Workbench neither depends on nor is tripped by a naive read (§2). Also close the loop on the intermittent MDF→DataBridge failure (§6). *Cheryl / team.*
- **O8-2** — **Full currency-field list** — Wendy to send CIC's currency-check query and the ~5 levels/fields where currency must be read; Ben to implement comprehensive detection (§3). *Wendy → Ben.*
- **O8-3** — **Hazard-execution detail/lineage** — determine what meaningful hazard information can be surfaced/captured for the from-screen hazard lookup, given a naive stamp read is insufficient (§2). *Ben.*
- **O8-4** — Confirm the exact current **submission count** to validate the no-backfill / volume assumptions (§5). *Wendy.*
