# IRP Workbench — Design Notes: V1 Demo Review — Submissions, Packages, EDM Roll-Up & Treaty Display

**Source:** Design session, August 4, 2026 (Ben Bailey presenting, Anil Venugopal — PremiumIQ; Wendy Hayes, Cheryl TeHennepe — CIC; Wendy hard stop ~50 min of ~58). **First live demo of the risk-workbench V1** to the CIC functional team — walk-the-screen, stop-for-comment. Cross-checked against the full transcript.
**Status:** Working design notes. The **subtractive** roll-up decisions (drop TIV, drop the EDM-level aggregate block, drop per-portfolio analyses counts) are **firm**. Geography/currency display, the condensed treaty view, the submission-form corrections, and the backfill + job-model architecture are **agreed as direction**. **Regionality definitions**, the **three condensed treaty fields**, the **landing page**, and the **package terminology discussion** remain open.
**Related:** `03_data_organization_open_questions_and_findings.md`, `04_navigation_page_layout_and_ui_patterns.md`, `05_analysis_results_metadata_and_comparison.md`, `06_exposure_modification_subportfolios.md`, `../FUNCTIONAL_REQUIREMENTS.md` (§1, §2.1–§2.3, §3, §5, §7), `../DATA_MODEL.md` (§4 Submission & Package, §5 EDM/RDM/Portfolio/Treaty, §8 IRP & RWB jobs), `../PRD.md` (§4, §7, §9, §12, §14, §19, §21), `../../../../CIC/minutes/Risk_Modeler_Interface_Design_Minutes_8-4-26.md`, `../../../../CIC/transcripts/Risk Modeler Interface _ Design 8-4-26.vtt`

> Decision and change-request IDs (**D1**–**D21**, **CR1**–**CR20**) below refer to the tables in the 8/4 minutes.

---

## 0. TL;DR

The first demo landed well — Cheryl: *"You made great progress, Ben. This is awesome."* — but the substance was **subtractive as much as additive**. Three things Ben built were rejected as untrustworthy or misleading, and the freed space is being repurposed for information CIC actually uses.

- **Removed from the EDM roll-up:** the **per-portfolio analyses count** (the EDM-portfolio↔RDM-analysis link cannot be trusted — D8), the **TIV column** (currency conversion makes it indefensible — D9), and the **EDM-level aggregate summary block** (it double-counts — D12).
- **Added in their place:** a **countries** column ahead of states (D11), **currencies as a list, never converted** (D10), and **package/submission breadcrumbs** as clickable navigation context at the top of the EDM page (D13).
- **Two CIC structural rules drove most of this** (§1): the same EDM/RDM data legitimately serves **multiple submissions with different treaty types**, and **any metadata originating outside CIC's environment cannot be trusted.**
- **Submissions:** name and cedant stay **separate fields** (D3); search must be **AND across terms, not OR** (D5); treaty type stays **single-select** (D6); **copy-a-submission** is a new Cheryl ask (CR10).
- **Treaties:** Cheryl **walked back** the 7/14 "show every attribute" position — the full grid is too much. **Condensed list plus ~3 significant fields**, detail on expand (D17).
- **Architecture confirmed:** roll-up data is **backfilled into the workbench DB**, not fetched live, managed by a last-synced stamp + manual Sync + **freshness validation before exposure-dependent actions** (D19); **two job classes** hung off a parent entity rather than a flat stack (D20).

---

## 1. The two structural rules from CIC

These recurred all session and are the reason behind several otherwise-unrelated decisions. Record them as standing constraints.

**(a) One set of data legitimately serves multiple submissions with different treaty types.** Cheryl's real-world case: *"one set of data that goes with multiple submissions."* The same exposure backs a cat treaty and a per-risk treaty. This is why **treaty type stays single-select at the submission level** (D6) — the multiplicity lives at the *data* level, not the submission level — and why a **package points at one physical copy attachable to many submissions** (D7).

**(b) Metadata that arrives from outside CIC's own environment cannot be trusted.** Two instances hit this session: the **EDM-portfolio-to-RDM-analysis link** (D8) and the **line-of-business field** (D14/D15). The corollary: a derived link is only trustworthy if the EDMs/RDMs **never left CIC's environment**.

> **Impact on shipped work.** Rule (b) narrows spec 004 FR-036 / PRD §21 Iteration 3, which surfaces the portfolio an analysis ran against by resolving Risk Modeler's `exposureResourceType = PORTFOLIO` pointer. That resolution is only defensible for **analyses CIC ran itself**; for imported/broker RDMs it must not be presented as a link. See §4.1.

---

## 2. Submissions

### 2.1 Identity & fields

| Point | Detail |
|---|---|
| **Submission name is its own field, separate from cedant name** (D3) | Wendy: the name on the contract is often **not** the name of the submission — an **MGA writing on behalf of an insurer** puts both names in the submission name but only one on the contract. *"I think it's right to keep them separate."* Today the submission name is a **copy/paste from CRM**. |
| **Cedant stays a workbench-side field** (D2) | Deliberately **not** hard-linked to the Moody's/EDM cedant field: there is **no exposure data at submission-creation time** to link against — cedant arrives *with* the exposure. Cheryl: *"Totally fine, I just wanted to make sure,"* while flagging that the label is confusing because **"cedent" is also a specific EDM field**. Disambiguating the label is worth doing; the spelling used throughout the workbench and codebase is **cedant**. |
| **Treaty type stays single-select** (D6) | Anil floated multi-select; Cheryl explained why it doesn't fit — one submission carries **one CRM ID and one treaty type**. The same-data-two-treaties case is handled by D7 + CR10 instead, not by multi-select. **CRM ID cardinality itself is unchanged: zero or more** (FR §1) — Cheryl's "one CRM ID" was the argument for single-select treaty type, not an audit of CRM cardinality. |
| **Required fields: name, cedant, treaty type, inception date** (D4) | Cheryl: *"seems fair."* With the caveat that **treaty year is derived, not entered** (CR5) — already the stated requirement; the implementation lagged. **Amended 8/5:** treaty year now *defaults* to the inception year and stays editable. Picking an inception date fills it, and the server fills a blank one on save, so nobody types it in the normal case — but a December inception is often written into the following treaty year, which a read-only derived field cannot express. Built this way; **re-confirm with CIC**, since it softens D4. |
| **Ownership is a soft link** | All authenticated users see all submissions; owner is reassignable on the detail page. An all-vs.-**owned-by-me** quick filter was demoed and accepted. |

### 2.2 Status & lifecycle (D1)

- **Three statuses only: Active, Complete, Cancelled.** Anil confirmed the set; Wendy accepted. **Superseded 8/5:** a fourth status, **Hold**, was added for a submission that is parked but still in the queue (`09` §6.5).
- **Transitions are reversible** — a cancelled submission can be reopened to active. *Not a one-way door.*
- **Each transition records a reason**, and the trail is retained.
- **CR9:** the trail must be **collapsible** — repeated transitions otherwise produce *"a huge block here in the submission page."*

### 2.3 Search (D5, CR1–CR3)

- **Search by submission name is missing entirely.** Ben caught it live: *"that is a need to add."* (CR1)
- **Multi-word search must be AND, not OR.** Wendy's driver: *"There must be 1000 companies that have American in the name."* Typing "American Family" must not return every company containing "American." Ben's initial framing as fuzzy matching was corrected — Wendy: *"it can get too fuzzy is my point."* Cheryl: *"It should be an and, not an or."* (CR2)
- **Search and filter by CRM ID**, and **show CRM detail on the submission page** — neither exists today (CR3, Ben self-identified).
- Wendy offered to demo how bad CIC's current CRM search is, as the anti-pattern.

### 2.4 Create-form corrections

| # | Change |
|---|---|
| CR4 | **Mark required fields explicitly** (asterisks or equivalent). Submitting an empty form returns a bare "cedant is required" — Ben: *"that's not clear… we need to make that very explicit."* |
| CR5 | **Auto-populate treaty year from the inception date** rather than free text. |
| CR6 | **Replace the free-text shared-drive path with a folder browser + validation**, and **hard-link the submission to the folder** — *"rather than just have a random free text field like I could put anything in here."* |
| CR7 | **Upgrade the cedant auto-suggest** — it doesn't read or behave like a typeahead: *"not what you think in terms of an auto populate drop down type of deal."* |
| CR8 | **Rename "renews from" → "links to."** The relationship is a link to a related submission, not necessarily a renewal. *"I think what we actually wanted to call this was 'links to' or something like that."* **Supersedes the 7/14 "Previous" label.** |
| CR9 | **Clean up CRM ID entry** — the permanently visible "add CRM ID" text box is *"a little ugly… I don't absolutely love that."* |

### 2.5 Copy a submission — new ask (CR10, Cheryl)

*"A lot of the data that we're going to have on that submission page is going to be duplicative… it'd be nice to be able to just copy it, make the edits that I need, update the CRM ID."*

- Opens a **pre-populated create form**; the result is a **new** submission, **not** an edit of the original. Confirmed explicitly.
- Note the interaction with the existing **non-blocking "similar deal already exists" warning** (PRD §7.2b): copying deliberately produces near-duplicates, so that warning must stay non-blocking.

---

## 3. Packages

- **D7 — a package points to one physical copy of the EDM/RDM data and can be attached to multiple submissions; no duplication.** Wendy asked explicitly whether attaching a package duplicates the data; Ben: *"Same, same underlying data is the idea."* Wendy: *"OK, got it."* This **confirms** the previously-provisional FR row *"work against a shared package propagates to every submission that shares it"* (design note `03` §5).
- **CR11 — "Add existing package"**: attaching an already-uploaded package to another submission. Demoed as a concept, **not yet implemented**.
- **D21 — EDM/RDM names must be unique in Risk Modeler; a collision blocks the package.** Checked against Risk Modeler by **API at package creation**; the user must rename before the upload proceeds.
- **CR12 — add search to the shared-drive browser.** Cheryl: *"Yes, please."* (Already a stated FR requirement; now confirmed against the running app.)
- The **package-creation flow** as demoed and accepted: browse the real share (mounted on the app server) → select EDMs and RDMs → uniqueness check by API → EDM-vs-RDM auto-detected from the filename and **overridable** → **Save and Sync**, which queues background jobs (upload to S3 → import) and returns the user immediately.

> **Terminology is not settled.** D7 settles the *mechanism*, not the vocabulary. Ben flagged he *"has more thoughts on"* packages and on onboarding CIC to the term — **"packages" is new vocabulary for CIC** — but the session ran out of time. The **"big package discussion"** is deferred (O8-5). The FR open question on whether "Package" survives is therefore **partially** resolved only.

---

## 4. EDM roll-up — what came out, what goes in

### 4.1 D8 — do not tie RDM analyses to specific EDM portfolios

Wendy, unprompted: *"there actually is no way to tie an RDM analysis to a specific EDM portfolio that you can trust."* Trustworthy **only** if the EDMs/RDMs never left CIC's environment.

- Ben had **independently reached the same conclusion** and demoed a **false link** live: a **US EQ analysis attributed to a USFL portfolio** — *"clearly they're wrong… it's not possible."*
- **Disposition:** the **per-portfolio analyses count column is removed** (CR16). Analyses from a linked RDM may still be *listed* on the EDM page, but **not attributed to a specific portfolio**.
- This is the **results-comparison linking** problem from FR §7 reappearing on the exposure side. It stays deferred there, and now the exposure-side display must not imply a link either.

### 4.2 D9 — TIV column removed

Currency conversion makes the number indefensible. Wendy: *"as soon as you put currency in, then people are going to say, how did you get that? What was the rate that you used?… I'm not sure it's necessary."* Cheryl agreed: *"too many questions that come into how'd you get that."*

### 4.3 D12/D17 — the EDM-level aggregate block is dropped; the per-portfolio table is kept

Cheryl on why the aggregate is worse than nothing: the counts **double-count** — *"you can have the same locations duplicated for wind and for severe convective storm… saying that I've got double the amount of locations isn't really double the amount of locations… I don't think that that summary gives me much."*

The **per-portfolio detail is the valuable part.** Wendy: *"having just the list of portfolios and… some information about them is really great in one place because you can't do that today in RiskLink."* (Anil also flagged **two of the aggregate counts as wrong** and will fix them regardless — CR17.)

### 4.4 Geography and currency

| Decision | Detail |
|---|---|
| **D11 — geography splits into two columns: countries and states/state-equivalents, country first** | Wendy: *"I actually want to see countries, not states"* — the immediate question is *"this is labelled US EQ; is it actually US only?"* Cheryl wants **regionality** where it beats a country (Caribbean, Pacific Northwest, or a single state like Tennessee), and agrees *"multi-country / global"* is sufficient for global books. Display collapses to a single value when there is one. |
| **Resolution on the region collapse** | Named regions **cannot** be the collapse value: regions are **treaty-dependent, not fixed constants** (design note `06` §2.2, and FR §3). The roll-up therefore collapses to **"multi-country" / a count of states**; named regions stay a **treaty-scoped** concept in the breakout flow. Confirm with Cheryl (O8-1). |
| **D10 — currency: report presence, never convert** | Keep a **list of currencies found** — or simply *"multiple currencies."* Genuinely useful because currency lives at **policy, location, and location-coverage** level and *"you can't just pull it from one spot."* Wendy: *"even if it came back and said multiple currencies, like that's a win."* Ben confirmed his query already spans **PORTINFO, LOCCVG, and property**. |
| **Currency field enumeration inbound** | Wendy is sending the **SQL query enumerating every place currency lives in an EDM** — *"it's probably like 15 fields"* — used in CIC's validation reports today (O8-3). |

### 4.5 Line of business, and the free-text pathology

- **D14 — LOB is read from LOBDET joined to the policy table.** Ben showed the query; Cheryl and Wendy both confirmed — *"that's what we want."* This solves Cheryl's case where a cedant's LOB table carries **30–40 values but the delivered data only uses two**: joining through policy returns **only what is actually in the data**.
- **D15 — pathological free-text fields get a hard cap, not elegant handling.** Wendy warned LOB is a **completely user-defined descriptor that does not affect analysis**, and cedants populate it with *"10s of thousands of different and unique values"* — account numbers, underwriter names. Her guidance: *"if it's over 500 values, we're not going to save it out. However you want to handle it. It doesn't need some elegant options that we go through."* Explicitly: **"I don't want you to overthink that scenario."**
- **Actions (CR19):** Ben to **performance-test the LOB JSON storage at ~10,000 key-value pairs**, cap storage above ~500 distinct values, and cap the front-end expansion around **100**.
- **Open:** other EDM fields share this pathology. Wendy: *"there's other fields like that in this EDM as well."* Ben: *"Maybe we can also think about other fields that you're familiar with that have those similar types of scenarios… I'll keep that as a note."* (O8-2)

### 4.6 D13 — the freed space becomes navigation context

Anil's proposal, endorsed immediately by Wendy — *"I don't know where I am. That was my next [question]"* … *"Perfect."*

- Show **which package and which submission** this EDM belongs to, as **clickable links**, so an analyst can hop back to the package and into another EDM *"without starting all over again from the top."*
- **Necessary** because a submission can hold any combination — one EDM and three RDMs, three EDMs and one RDM. (CR14)
- **CR15 — truncated lists expand in place.** Lines of business, geography, and currencies are all cut off mid-list today. Cheryl: *"whether you have hovering capabilities or you have to click in or whatever… to be able to expand some of those lists."* Ben's plan: repurpose the existing analyses expander for the full non-summary lists.

---

## 5. Treaties

- **D16 — treaty create/edit stays a pass-through to Risk Modeler.** Reconfirmed from July. Ben: *"Tree[aty] creation and editing is filling out a bunch of fields… I don't want to rebuild that functionality in our UI because it's going to be exactly the same."* Cheryl: *"you don't need to spend your time trying to reinvent the wheel… It's a data entry situation."* The demoed treaty list links **directly out to the Risk Modeler treaties page**.
- **D17 — keep a condensed treaty list in the workbench; drop most of the detail.** **This walks back the 7/14 "show every attribute" position.** Cheryl found the full grid *"a lot to look at… just a lot of information with that scrolling."* Wendy: *"I like the fact that the treaties are listed here. I don't think we need to have all of the detail, but I might in your collapsed view add like 3 fields"* — enough to notice something unexpected, e.g. *"oh, there's a quota share in there. I wasn't expecting to see a quota share."*
  - The 7/14 intent (catch mis-coding rather than blindly trust the data) is preserved **via expand**, not via default density. **The field set was agreed 8/5** and came out wider than three — four core fields plus context (`09` §1, closing O8-6).
- **CR18 — clean up the grid** before condensing: **timestamps are too granular** for what Risk Modeler actually uses (*"these timestamps are ugly"*), some attributes look **duplicated**, and cryptic labels need resolving — **attachment level vs. attachment basis**, **"loc" = location**.
- **D18 — bulk treaty creation from CSV/Excel is deprioritized.** Ben floated it as a custom value-add. Cheryl: not something they do today, and *"whether I do it in Excel or I do it in Risk Modeler, I basically have to do the same steps either way. At least there's some error checking that happens in Risk Modeler."* **Further down the list.** Whether RM even supports it is unknown and now academic (O8-7).

---

## 6. Architecture confirmations

### 6.1 D19 — roll-up data is backfilled, not fetched live

Confirmed as the architecture for the EDM/RDM pages: **live API calls per page load would be a performance overhead.** The cost is **drift** when someone edits directly in Risk Modeler, managed by three things:

1. an **as-of / last-synced timestamp** shown on the page,
2. a **manual Sync** action, and
3. **validation of freshness before any action that depends on current exposure** — **sub-portfolio creation being the first such action.**

The demoed EDM header carries: name, status, source path on the share, Risk Modeler ID, job ID, portfolio count, last-synced timestamp, and the Sync button.

### 6.2 D20 — two job classes, hung off a parent entity

- **IRP jobs** — submitted to Moody's, polled for state. **Workbench jobs** — uploads and other wrapping/independent work.
- Jobs are **associated with a submission, package, or EDM**, not presented as one flat global list. Anil: *"there's no point in replicating it exactly like"* Moody's, where Cheryl noted *"it just stacks everything up."*
- **Heavy work stays off the request path** so the user is never blocked on an upload. Cheryl: *"Yeah, that's great."*
- **CR13 — a clearer "actively running" indicator.** Status text alone gives too little insight. Cheryl: *"as an analyst… we are antsy… we just want to get things moving through and we're going to want to see, oh, is it stuck? Is it happening? Is anything going on?"*

### 6.3 Shell & access (as demoed)

Username/password login is **development only — SSO/Entra in production**. Left sidebar: submissions, Moody's data lists (EDMs, RDMs), results, workflows/jobs, plus **placeholders** for analysis templates and template suites. The user info block is not yet populated. **The landing page is empty** and its content is an open call — *"We can make a call on kind of what's appropriate to put in here"* (CR20, O8-4).

---

## 7. Deferred / parked

| Item | Note |
|---|---|
| **The "big package discussion"** | Ben has more thoughts on packages and on onboarding CIC to the terminology; ran out of time (O8-5). |
| **CRM integration** | Parked as a **future phase**. Wendy: *"Not part of what we're doing here, but you could see that being very easily integrated."* CRM holds submission name, company/cedant name, inception date, and CRM ID; Cheryl: *"especially based on that CRM ID, we could pull a lot of data in."* |
| **Analysis templates / template suites pages** | Still placeholders — *"fill those out at a later point."* Requirements themselves are already captured (FR §4). |
| **Workflows / jobs tab** | Acknowledged as *"another topic to cover separately."* |

**Cadence note.** Anil flagged that the team deliberately needs to run **weeks ahead** on functionality because *"we're going to have to spend a lot of time arm wrestling"* with the CIC infrastructure constraints raised at kickoff.

**Next session:** Wednesday, August 5, 8:00 AM Central — **sub-portfolio creation** (Ben nearly finished; *"you can probably check it out tomorrow"*), then pick back up from treaties.

---

## 8. Open questions

- **O8-1:** **Regionality definitions** for the geography roll-up. Cheryl raised regions twice (Caribbean, Pacific Northwest) as more useful than raw state lists, but regions are **treaty-dependent and cannot be fixed constants** (`06` §2.2). Working resolution: the roll-up collapses to **"multi-country" / a state count**; named regions stay treaty-scoped in breakouts. *Confirm with Cheryl.* (§4.4)
- **O8-2:** **Which other EDM fields share LOB's free-text pathology?** *Ben, with Wendy/Cheryl.* (§4.5)
- **O8-3:** **Currency field enumeration** — Wendy to send the SQL listing every place currency lives in an EDM (~15 fields). *Wendy.* (§4.4)
- **O8-4:** **What belongs on the landing page.** Currently empty. (§6.3)
- **O8-5:** **Does "Package" survive as CIC-facing vocabulary?** D7 settles the mechanism; the terminology and onboarding discussion is deferred. Carries forward `03` OQ-1 / `04` §9. (§3)
- **O8-6:** ~~Which ~3 fields go in the condensed treaty view?~~ **Closed 8/5** — the field set was worked through column by column and agreed. See `09` §1. (§5)
- **O8-7:** Whether **Risk Modeler supports treaty creation from CSV.** Cheryl: *"I don't know in Moody's if we're able to or not."* **Academic** given D18. (§5)
