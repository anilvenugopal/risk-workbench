# IRP Workbench — Design Notes: Navigation, Page Layout & UI Patterns

**Source:** Design session, July 14, 2026 (Ben Bailey, Cheryl TeHennepe) — live wireframe / prototype walkthrough. Cross-checked against the full transcript.
**Status:** Working design notes. First UI review with CIC; Cheryl's reactions were about *how fast information can be assimilated*, not final sign-off. Layout **Option C** and the **three-tier navigation** are agreed as the working direction; the **"package"** object is under active question (see §9, and open questions in `03_data_organization_open_questions_and_findings.md`).
**Related:** `01_data_model_and_workbench_organization.md`, `03_data_organization_open_questions_and_findings.md`, `05_analysis_results_metadata_and_comparison.md`, `../DATA_MODEL.md` (§4–§6), `../PRD.md` (§4, §7), `../../../minutes/IRP_Workbench_Design_Minutes_7-14-26.md`, `../../../transcripts/IRP_Workbench_Design_7-14-26.vtt`

---

## 0. TL;DR

The 7/14 session was the first time CIC saw the UI. Ben drove four tabs (one live prototype, three throwaway wireframe options); Cheryl reacted in real time. The load-bearing outcomes:

- **Three-tier navigation** — *submissions list → submission detail → EDM detail*, with a **fourth drill-down to the portfolio** — because analyses are run against a portfolio, not a whole EDM.
- **Layout Option C** for the submission detail page — submission attributes compact at the top; **expandable/collapsible package rows** revealing EDM/RDM detail.
- A general **pass-through-to-Risk-Modeler pattern**: where the workbench would only re-skin RM (treaty editing, return-period editing), open RM in a new window, let the user edit/save there, return, and refresh — do **not** rebuild RM.
- **Portfolio-level** detail (counts, perils, geography) is wanted *per portfolio*, not just EDM-aggregate.
- **Search / sort / filter on every section** (portfolios, treaties, analyses, results).
- The **"package"** concept "is not CIC's terminology" and may be over-organization — flagged as open.

This doc captures the navigation, page layout, display, and import-flow UI. Analysis-results content (metrics, comparison, grouping) is in `05`.

---

## 1. App shell — sign-in & homepage

| Item | Detail |
|---|---|
| Sign-in | Landing page currently has username/password. **SSO scaffolding is in place but untested.** (Transcript is internally muddled here — "haven't had the scaffolding for SSO, but haven't tested it yet"; treat as *scaffolded, not yet verified*.) Logo contributed by Anil. |
| Homepage | Currently empty. Envisioned to surface **the logged-in user's own work** — their submissions — on arrival. |

---

## 2. Submissions list page

The least contentious page (Cheryl: "less contentious than the other pieces").

| Requirement | Notes |
|---|---|
| Simple table of submissions | Columns are the agreed attributes: **cedant, treaty type, inception date**, plus **owners**. |
| Ownership model | **Everyone can view everything**; certain people are the **main owner(s)** of a submission. The homepage filters to "your work." (Matches DATA_MODEL: `assigned_analyst_id` is a soft owner, not an access gate.) Ben's example named "you versus Ross versus whoever" — ownership can be any user. |
| Search, sort, filter | On the list. |

---

## 3. Submission detail page — layout Option C (selected)

Three layouts were shown; **Option C won**.

- **A / B** pushed submission details into prominent headers and packed too much into single table rows — Ben disliked A, found B cluttered.
- **Option C** keeps submission details **compact at the top** ("I don't think these are things that need their own headline… the packages is what's important") with **expandable/collapsible package rows** that reveal EDM/RDM detail. Cheryl: "Definitely the third one is easier… easier to look at it and assimilate that information."

### Header fields (Option C top block)

| Field | Verdict | Notes |
|---|---|---|
| "Renews from" | **Rename → "Previous."** | Not everything is a renewal; the team still wants to jump back to a prior analysis to compare even when it isn't a formal renewal. "That language will cause some people to stumble." |
| CRM ID tag(s) + status (in progress / complete) | **Approved.** | |
| Directory path | **Liked.** | But see §8 — the linked directory often is *not* where the working file lives. |
| Created date | **Liked.** | |
| Link to previous submission | **Liked.** | The "Previous" link above. |

---

## 4. Three-tier navigation + portfolio drill-down

- Navigation is **submission → package → EDM**, and **the actual work happens at the EDM level** — "to do anything you click into a package / individual EDM row." (Ben: "a three-tiered approach, submission, package, and then our actual work is at the EDM level.")
- **Go one level deeper — to portfolios.** Cheryl: "that's what we're going to care about when we're running analyses. I don't run it over a whole EDM. I'm running it on a portfolio that's within the EDM." The portfolio drill-down is also the entry point for kicking off analyses.

So the effective depth is **submission → package → EDM → portfolio**.

---

## 5. Portfolio-level exposure detail (key theme)

The current wireframes showed location/account/policy counts and perils/geographies at the **EDM-aggregate** level. Agreement: these belong **at the portfolio level too**.

| Rule | Rationale |
|---|---|
| EDM-aggregate figures are fine on the higher-level (submission) page. | Quick orientation. |
| Per-portfolio breakdown is needed **once inside a specific EDM**. | You run analyses on a portfolio, not the EDM. |
| Surface per portfolio: **location / account / policy counts, perils, geography.** | Field set matches the 7/7 exposure-detail spec (FR §2.2). Same rollup thinking, now confirmed at the **portfolio grain**. |

**Why portfolio counts matter (Cheryl):**
- **Workload / scheduling** — a 4-million-record portfolio might be deliberately started at 1 a.m. rather than hogging capacity mid-day. "I'm managing the work I'm doing based on the number of locations." (This echoes the 7/7 safety point: don't accidentally launch an oversized analysis.)
- **Content awareness** — naming conventions don't always reveal what's inside a portfolio. Surfacing geography, peril, and location count tells the analyst e.g. "this is a US book," or "there's no winter storm — I need to add it as a peril."

---

## 6. Treaty display

| Requirement | Notes |
|---|---|
| Show **full treaty attribute detail**. | Treaties carry many attributes; Cheryl wants them **all** visible because they affect how the analysis is run — and because "sometimes people put the wrong thing in the wrong field." She needs to **catch mis-coding**, not blindly trust it. (In the transcript she quantified: "90% of the time you know how certain fields are going to be coded, but sometimes people put the wrong thing in the wrong field.") |
| **Expand/collapse (node) behavior.** | If there are three treaties, show them expanded; if many, collapse to focus one at a time. |
| Compact view uses **horizontal scroll** for wide attribute sets. | A layout mechanic Ben showed for long treaty rows. |
| **Export to Excel** for extreme cases. | When there's too much to render cleanly. |

**Reinsurance / portfolio–treaty relationship (correction Cheryl flagged).** Ben's summary was EDM = exposures/portfolios, RDM = broker analysis results run against those portfolios. Cheryl added the missing reinsurance piece:
- For a **per-risk treaty**, the interest is not the portfolio itself but **the losses subject to that treaty.**
- Where **inuring reinsurance** exists, it must be identifiable and **removed to get the net perspective** ("I want my losses subject to the treaty… then I have to take that reinsurance out, which the models do for me").
- Treaty **setup lives at the EDM level** and can be applied at different exposure levels (portfolio, account, policy). Results are spit out to the RDM; those analyses are what CIC preserves.

Treaty **creation/editing** is a **pass-through to Risk Modeler** — see §7.

---

## 7. Pass-through-to-Risk-Modeler pattern (general)

A recurring decision: where the workbench would only present the *same* RM information a *different* way, don't rebuild it — hand off to RM.

- Cheryl was firm: "I really don't want you to spend time trying to reconfigure what exists in Risk Modeler today… if all you're doing is a different way to spit back the exact same information, then let's just have a pass through."
- **Mechanism:** a button **opens the RM editor in a new window**; the user edits and saves there, returns to the workbench, and the page **refreshes** to pick up the change.
- **Confirmed pass-through candidates:** **treaty add/edit** (§6), and **return-period / interpolation editing** (see `05` §4). Reinsurance edits are a pass-through too (confirmed again 7/16).

---

## 8. Import & file-browse flow (7/14 refinements)

Builds on the import mechanics in `03` §6.3. New UI detail from this session:

| Requirement | Notes |
|---|---|
| **New-package modal.** | Opens to **name** the package and **browse** for files. Default-to-submission-name was deemed **inappropriate** as the package name. |
| **Network drives only — never the local machine.** | Both the analysts' machines and the app are connected to the shares. |
| **Two drives**: an **archive** drive and the **working** drive (with subfolders). | Consistent with the 7/9 "M = client drive, L = archive/BAK drive" reality (see `03` and §8.1 below). |
| **Both folder-tree navigation and search-by-file-name.** | Search is especially valuable for **BAK files**, stored under non-recognizable names: "I typically search for my file name, and then it takes me to the right folder." |
| **Multi-select import.** | Pick several files at once. |
| **EDM vs RDM classification** inferred from the naming convention (~90% of the time the name contains "EDM"/"RDM"/"results"), with an **editable in-place flip** to correct the type when the name isn't reliable. | Cheryl: "Nice… I like that." |
| **Save vs Save-and-Sync.** | **Save** stages the selection **without** syncing to RM (useful while still gathering files across directories); **Save and Sync** imports to RM immediately. |
| **Background / queued imports.** | Ben's guidance: **submit everything at once** (e.g. 20 EDMs/RDMs) — the system runs ~10 concurrently and **auto-dequeues** the rest as slots free up, so chunking gives no benefit and avoids manual re-triggering. Cheryl liked assembling everything in one spot before syncing. |
| **Drag-and-drop reordering rejected.** | Ben considered it for the staging screen: "Reordering, like a drag and drop, is probably just not as easy as just flipping a button here." |

### 8.1 Navigation reality (carried from 7/9 transcript — corrects the "single directory link" framing)

The clean "open the deal's folder" link is real but **insufficient on its own**:

- The shared-drive tree has a **model-vendor layer**: cedant → treaty year → submission → **cat modeling → [model vendor, e.g. RMS]** → package folders → zips. RMS is *one vendor folder among potentially others*; Wendy "always start[s] by going to the cat modeling folder."
- **Edited / BAK data frequently is NOT in the client directory.** It lives on a **separate archive drive** ("goes to this L drive instead of this M drive… not organized in the same file folder structure"), sometimes further split into **in-force / projected** subfolders when there are many databases.
- **Requirement:** the file browser must support **free navigation to any location + file-name search**, not just a jump to one linked folder. Wendy: "as long as we can just navigate to wherever we need to be."

### 8.2 Still open

- **BAK vs MDF vs zip** import path is unresolved: (a) point at **BAK files** in a shared BAK directory, or (b) select **MDF / zip** files from a network drive (varying by cedant / submission / treaty year). "Two options of which we have not yet come up with which one makes perfect sense yet."

---

## 9. The "package" concept — under question (flag)

- **"Package" is not CIC's common terminology** and would need socializing. When Cheryl asked why a group of EDMs/RDMs needs its own name, Ben conceded he's still working it out and is **"feeling like it adds too much organizational overhead."** Cheryl agreed.
- Ben's rationale for the concept: a **logical grouping** of related exposure (EDMs) and the broker results (RDMs) that go with them — something CIC manages **today purely through naming conventions**.
- **Open:** keep a named `package` object, or add EDMs/RDMs directly to a submission. **The import flow works either way.**
- **Disposition for this doc set (per direction: "hold + flag as open"):** the schema and requirements keep `package` as the working baseline; this tension is recorded as an open question in `03_data_organization_open_questions_and_findings.md` (OQ-1/OQ-2) and re-confirmed here. Resolve at the next wireframe review.

---

## 10. Sample data (logistics)

- Ben requested representative (ideally sanitized) EDM/RDM data for testing — **start smaller for development, stress-test with larger sets later.** He can also **extrapolate/scale** a smaller real set to synthesize larger volumes.
- NDA coverage to be confirmed with **Tim**; **Cheryl to check with Wendy** on sharing real data and, if not permitted, produce a realistic substitute.

---

## 11. Open questions

- **O4-1** — Confirm the top-level organization and whether the named `package` object survives (§9; OQ-1/OQ-2 in `03`). *Blocking for schema ratification.*
- **O4-2** — BAK vs MDF vs zip import path (§8.2).
- **O4-3** — SSO: scaffolded but untested; confirm the production path (§1).
- **O4-4** — Exact per-portfolio field set to surface in the EDM drill-down (align with the 7/7 exposure-rollup spec).
