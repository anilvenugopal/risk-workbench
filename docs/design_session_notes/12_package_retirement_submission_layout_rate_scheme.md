# IRP Workbench — Design Notes: Retiring Packages, Submission-Centric EDM/RDM Layout & Rate-Scheme Data Access

**Source:** Design session, August 12, 2026 (Ben Bailey presenting — PremiumIQ; Wendy Hayes, Cheryl TeHennepe — CIC; **Ross Konell** — CIC, new to the sessions, being walked through the app; Cheryl hard stop at ~55 min for a 10:00 call). A package-management demo that turned into a structural decision. Cross-checked against the full transcript. **Seventh session** of the demo-and-refine series (continues Aug 4/5/6/7/10/11).
**Status:** Working design notes. The **decision to retire the "packages" concept** is a same-day call — Ben will **prototype removal this afternoon and review tomorrow**; treat it as agreed *direction* pending the prototype, not shipped. The **submission-centric EDM/RDM layout** (two table sections, collapse-by-default, EDM-name dropdown, submission-scoped RDM display, lazy-load) is agreed direction contingent on the same prototype. The **Caribbean `CB` special case is built** (SQL-level). The **event-rate-scheme data-access** thread is **open and partly contested** (Ben vs. Cheryl on whether the field even lands on a normal import) — needs Cheryl's test + a Moody's answer. **GeoHazard** and the first **results** pass were on today's agenda but **not reached** — carried forward. Resolves design-note `08` **O8-5** and pushes `../DATA_MODEL.md` §14 **OQ-2**. Extends `08` (§3 Packages), `11` (breakouts/grouping), `04`/`10` (submissions list/nav).
**Related:** `08_v1_demo_review_edm_rollup_and_submissions.md` (§3 Packages, §4.6 nav context, O8-5), `11_submissions_search_subportfolio_breakouts_grouping.md` (§1.1 search semantics, §3 grouping, O11-2), `04_navigation_page_layout_and_ui_patterns.md` (§2 submissions list), `09_treaty_summary_rdm_import_and_notifications.md` (§ RDM lifecycle, O9-5), `../DATA_MODEL.md` (§4 Submission & Package, §5 EDM/RDM/Portfolio/Treaty, §8 IRP & RWB jobs, §12 manifest, §14 OQ-2), `../FUNCTIONAL_REQUIREMENTS.md` (§1 Data Organization — Package block, three-tier nav; §2.1 import; §2.2 EDM detail / package-scoped RDM display; §6 Grouping), `../PRD.md`, `../../specs/002-submission-package-domain/` (directly reshaped), `../../../../CIC/minutes/Risk_Modeler_Interface_Design_Minutes_8-12-26.md`, `../../../../CIC/minutes/Risk_Modeler_Interface_Design_Minutes_8-11-26.md` (immediately-prior session; no design note yet), `../../../../CIC/transcripts/Risk Modeler Interface _ Design 8-12-26.vtt`

> Decision IDs (**D1**–**D10**) below refer to the tables in the **8/12 minutes**. Transcript garble is interpreted per the note at the top of the 8/12 minutes (EDM/RDM for "idioms/RMS," Data Bridge for "data breach," rate scheme for "right/rake scheme," etc.).

---

## 0. TL;DR

A package-management demo (built since the 8/11 session) exposed that the **package** entity no longer earns its place, and Ben committed to removing it. The session never reached GeoHazard or results — it stayed stuck in data-management, which Ben named as the frustration: *"we can't get past the data-management stuff and… get into the actually-doing-stuff stuff."*

- **Retire packages (D1).** The concept existed to relate EDMs↔RDMs down to the analysis level ("this analysis came from this portfolio"); that association was already removed as unreliable, so packages no longer serve a purpose. CIC never used them (they match by **naming convention**), finds them confusing, and doesn't need sub-bucketing inside a submission. Ben: *"I'm going to go this afternoon and prototype getting rid of packages altogether… They don't do too much. They are confusing."* **This resolves `08` O8-5** ("does 'Package' survive as CIC-facing vocabulary?") — the answer is stronger than a rename: **remove the entity.**
- **The submission becomes the container (D2–D7).** EDMs and RDMs shown as **two table sections** on the submission page, **collapsed by default**; clicking an EDM shows **all RDMs in the submission** (no longer package-scoped); an **EDM-name dropdown** hops between EDMs in the same submission; the breadcrumb points at the **one submission navigated from** (not every submission the EDM belongs to); **lazy-load** analysis detail on expand. Cheryl on the navigation: *"Seems very clean."*
- **Fill a glaring gap (D7).** You still can't **import a brand-new EDM/RDM directly into an existing submission** — today you import standalone, then search and associate. "Add an EDM" must cover both new import and add-existing.
- **Add-existing search (D8).** The add-existing list is standalone databases (not tied to any submission); **name-based fuzzy "contains" search** (same semantics settled in `11` §1.1) plus **pagination** is sufficient — Cheryl: *"you could just type co… and you should get the sample co."*
- **Caribbean `CB` is built (D9).** SQL-level CASE handling maps country code `CB` → country, island codes → state. Accepted as a standing exception. Cheryl: *"It's just an exception we have to deal with."*
- **Single source-of-truth data model (D10).** Pre-go-live schema changes wipe demo data (no incremental migrations until go-live); Ben built an **EDM-sync from Risk Modeler** to avoid re-importing after each wipe.
- **Event rate scheme is unresolved and contested (§4).** Ben suspects it isn't populated on a normal import; Cheryl argues it must be (grouping detects differing rate schemes). Normally-imported RDMs aren't on **Data Bridge** → unqueryable via SQL. Cheryl will test + chase Moody's (GeoHazard + rate scheme).

---

## 1. Retiring the "packages" concept *(the headline — prototype to review)*

### 1.1 Why packages are going away

The package was introduced to **relate EDMs and RDMs — especially down to the analysis level** ("this analysis result was produced from this portfolio"). That linkage has already been removed as unreliable (`08` §4.1 / D8 — an RDM analysis can't be trusted to a specific EDM portfolio unless the data never left CIC's environment). With its reason for existing gone, the package is left doing very little:

| Argument | Detail (from the room) |
|---|---|
| **No functional payload** | Ben: the package "doesn't do too much"; under the hood "package doesn't matter." Its original job (EDM↔RDM↔analysis lineage) was already stripped out. |
| **CIC matches by naming convention, not forced association** | Cheryl: *"I can click on any EDM and any RDM in RiskLink and it doesn't matter if they match or they don't match… those activities are completely separate."* There is "no forced mechanism of saying this goes with this" today, and CIC doesn't want one. |
| **No need for sub-bucketing within a submission** | Even distinct data (e.g. a Europe set and a North-America set) doesn't need separate packages — differentiate by name. Cheryl's "books on a shelf" analogy: *"I'm okay with having just one container to put everything in"*; the titles tell them apart. |
| **New, confusing vocabulary** | Packages are a concept CIC doesn't use today; onboarding them to it was always an open cost (`08` O8-5). |
| **Modest volume doesn't justify a tier** | Most complex cases ~**10–15 EDMs and fewer RDMs**; commonly 1 EDM/1 RDM or 2/2. Cheryl: *"It's not hundreds."* A grouping tier buys little at that scale. |

**Decision (D1):** Ben prototypes removing the package entity **this afternoon** for review **tomorrow**. He's clear-eyed about the cost and unbothered: *"It's a big change… but it's worth it,"* and *"I don't really care. That's going to be way better."* Cheryl endorsed it and apologized that it undoes prior work; she expects it to **streamline the UX** and make downstream tasks (re-associating revised data to a submission) easier.

**Scope discipline for the first cut.** Ben will keep the initial change focused on removing the entity — *"I might just leave it for the immediate time being to get the package concept out the door. I'll just show all the RDMs in the submission for any EDM"* — collapsible, with the richer layout (§2) layered on after.

> **Impact on shipped work — this is a domain-model change, not a UI tweak.**
> - **`../DATA_MODEL.md` §4 (Submission & Package)** is the blast radius: the `package` table, the `submission_package` M:N join (composite PK), and the nullable `package_id` FKs on `irp_edm`/`irp_rdm` all lose their reason to exist. Membership/reuse must re-anchor on the **submission** (EDM/RDM → submission directly, or a submission-scoped association), and the *"reuse is reuse of the package"* rule (§4) needs a submission-level replacement.
> - **`../DATA_MODEL.md` §8** — `irp_job` grain is currently the **package** (nullable `package_id`, per the manifest and §8 chaining, A21). Job lineage must regrain to **submission + EDM/RDM** without a package parent. The `upload_edm`/`upload_rdm`/`delete_edm` chaining is described in package terms and needs restating.
> - **`../DATA_MODEL.md` §14 OQ-2** ("two-tier vs. three-tier: submission = deal with CRM as flat tags, vs. a grouping tier above") is effectively **decided toward the flatter model** — no package tier. Close the terminology collision when ratifying.
> - **`../FUNCTIONAL_REQUIREMENTS.md` §1** — the entire **Package** block (a package associates EDMs/RDMs, requires ≥1 member, is shared across submissions, points at one physical copy, etc.), the **three-tier navigation** rows ("Submission → Package → EDM"), and the **create-package** rows are superseded or must be rewritten against the submission. Per the traceability rules these are baseline FR IDs — **don't renumber**; mark superseded with the 8/12 rationale and reconcile the Excel tracker separately.
> - **spec `002-submission-package-domain`** is reshaped by its own name. The submission half stands; the package half needs revisiting.
> - **Resolves `08` O8-5** and closes the `03`-era OQ-1/OQ-2 package-terminology thread in the retire direction.

---

## 2. Submission-centric EDM/RDM layout *(agreed direction, contingent on the §1 prototype)*

With the package tier gone, the **submission page** carries the EDMs and RDMs directly, and the **EDM detail page** gets new in-submission navigation.

### 2.1 Submission page

| Item | Decision | Detail / rationale |
|---|---|---|
| **Two table sections** (D2) | EDMs and RDMs as separate tables | Name, portfolio count, and possibly a **global-vs-consolidation** flag ("is it global or a consolidation of the countries?" — Ben believes that distinction matters to CIC). Keep it simple initially; refine later. Ben can also pull the old EDM roll-up top-of-screen figures (e.g. portfolio count) into the rows. |
| **Collapse by default** (D3) | Lists collapsed on load, expand on demand | Cheryl: *"Can we have the default view be the collapsed view?"* — with a long list it's more obvious there's more without scrolling. Ben: "You can easily do that." |
| **All RDMs shown per EDM** (D2) | Clicking an EDM shows **every RDM in the submission**, not a package subset | This is the submission-scoped replacement for the package-scoped rule. Cheryl also wants to **toggle which RDM is displayed** as the analyst, not have it fixed. |
| **Actions = add + remove** (D7) | "Add an EDM" covers **import-new *or* add-existing**; remove EDMs/RDMs | Remove supported; removing the last member of a (soon-to-be-former) package soft-deletes it — behavior carries to whatever the container becomes. |

> **Impact — `../FUNCTIONAL_REQUIREMENTS.md` §2.2:** the row *"Every RDM in a Package is displayed on every EDM detail page in that Package"* (`list_edm_analyses` is package-scoped) becomes **submission-scoped** — every RDM in the **submission** shows on every EDM page in the submission. The "packageless EDM falls back to analyses applied against it" caveat becomes the general case.

### 2.2 EDM detail page navigation

| Item | Decision | Detail / rationale |
|---|---|---|
| **EDM-name dropdown** (D4) | Turn the EDM name into a dropdown that switches between EDMs **in the same submission** | Swaps the **portfolios/treaties** section while the **RDM/analysis** section stays (same submission). Lets an analyst "bop around between exposure data easily" without returning to the submission page. Populated from the submission's EDMs, with a simple name filter. Cheryl: *"A drop-down, yeah… Seems very clean."* Deliberately **not** a "list of other EDMs" section — conceptually an EDM doesn't contain EDMs. |
| **Proper breadcrumb** (D5) | Breadcrumb = the **one submission navigated from** | Today's breadcrumb wrongly lists **every** submission the EDM belongs to (the added-by-a-plus logic). With packages gone it should be a real trail back to the source submission; the multi-submission membership list moves elsewhere. |
| **Lazy-load for performance** (D6) | Load only RDM **metadata** up front; fetch analysis detail on expand | Ben: makes performance "a completely not a concern" even at ~10–15 RDMs. He'll still do the due-diligence load test. |

> **Impact — `../FUNCTIONAL_REQUIREMENTS.md` §1 & §2.2 nav rows:** "An EDM page shows its parent **Package** and Submission as clickable links" collapses to **submission-only**; the "every owning submission renders as a link, oldest first" behavior is replaced by the single source-submission breadcrumb (D5), with the full membership list relocated.

### 2.3 Add-existing search (D8)

The "add existing EDM/RDM" list is **databases not tied to any submission** (standalone imports). It can be long, so:

- **Name-based fuzzy "contains" search** — matches at the beginning, middle, or end (the same semantics settled for submissions search in `11` §1.1). Cheryl: *"you could just type co here… and you should get the sample co."*
- **Pagination** for long lists.
- Wendy floated **tick-box filters** ("everything not in this package / in a different package / not in any"); Ben floated searching by **submission attributes** (name, cedent, CRM ID). **Both deprioritized** — Cheryl judged name search sufficient because uploading a database *outside* the submission process will be rare (*"I just can't think of too many instances where we're going to upload an EDM or an RDM outside… but it could happen"*).

**The glaring gap (D7).** You can add an *existing* standalone database, but you **cannot import a brand-new EDM/RDM straight into an existing submission** — today you'd import it standalone, then search and associate it. Ben called this "a glaring" gap; Cheryl confirmed importing directly would be "much easier" than upload-then-search-then-associate. Fixing it is part of the submission "add" action.

> **Impact — `../FUNCTIONAL_REQUIREMENTS.md` §2.1:** aligns with the Aug-5 row *"The four import steps run behind one user action"* (currently Not implemented) and with the `09` O9-5 open item on RDM lifecycle (replacement/staged data). The direct-into-submission import is the missing front door.

---

## 3. Caribbean `CB` (built) & the single-source-of-truth data model

### 3.1 Caribbean `CB` special case — implemented (D9)

The `CB` handling designed on 8/11 is **built**, at the **SQL level**, using CASE statements that detect country code **`CB`** and map it **`CB` → country, island-level codes → state** (rather than duplicating every SQL script for the Caribbean and pushing the branch into app logic). A live breakout returned **island-level codes** (e.g. Puerto Rico) instead of lower-level states, as intended. Accepted as a standing exception — Cheryl: *"It's just an exception we have to deal with."* It "makes it a little uglier, but it works."

> **Impact — `../DATA_MODEL.md` §5 / breakout SQL:** the `CB` branch is a real divergence in the portfolio/geography queries (continues `11` §2.1 country dimension and the Caribbean handling from the 8/11 minutes). Worth a one-line note wherever the geography roll-up / breakout SQL is documented so the CASE branch isn't mistaken for a bug.

### 3.2 Why demo data keeps disappearing (D10)

Ben is building **one canonical source of truth** for the data model rather than stacking migrations (*"we would have 50 migrations by now, and that just becomes really messy"*). Consequence: **pre-go-live structural changes drop all data** ("stop all the services, run a database migration, and we're going to lose all of our data"). After go-live, enhancements use **real** migrations. To stop losing hours to re-imports, Ben built (the day before) a **sync of existing EDMs from Risk Modeler / Moody's into the Workbench** as a dev convenience — which is also the third EDM-creation path (alongside package creation and standalone import) flagged in prior sessions.

---

## 4. Event rate scheme & grouping data access *(open — highest-leverage, contested)*

Carrying `11` §3.3 / O11-2. The question: the **event rate scheme** is needed metadata, but where does it live and does it survive a normal import?

**The disagreement (unresolved).**
- **Ben:** *"I don't even think that that data is coming in when we do this… I don't think the event rate scheme is getting populated anywhere"* on a standard import.
- **Cheryl:** *"I disagree — because when you group, it knows if you have two different rate schemes."* If grouping can detect two analyses with differing rate schemes, the value must be stored somewhere. She's seen it surface as "RMS 2023 stochastic event rates" only on **grouped** analyses.

**The access constraint Ben established.** A normally-imported **RDM is not stored on Data Bridge**, so its analysis results are **unqueryable via SQL** and live only in the platform. The **only** way Ben could query the data was by routing the RDM through **Data Bridge** explicitly — which he did, to validate the data is reachable. Cheryl's takeaway: the queries her team has are only useful if the RDM is on Data Bridge; her colleague's script (below) assumes that.

**What Cheryl brings.**
- She has already **emailed Moody's** about **GeoHazard** and the **rate scheme**, telling them "these things don't come through," and asked how to surface a field that's stored but not returned.
- She'll send Ben a colleague's **SQL query** that ties the background tables together for an RDM to expose full analysis details — the rate scheme lives across **two tables that link back to another table**.
- She'll **run a controlled test**: two analyses with different rate schemes → import → attempt to group → observe (she suspects the UI may assume RiskLink 2023; notes rate scheme is really only relevant for hurricane; and she's constrained by not being able to reach an **MDF**).
- She suspects the value is exposed in the **grouping APIs**, "because that's where we see it in the UI."

**Ben's angle.** He has reverse-engineered **platform APIs** to enumerate event rate schemes and pull analyses, but still needs a proper design discussion of **how grouping works** before the automated rate-scheme selection (`11` §3.3) can be locked.

> **Impact — `../FUNCTIONAL_REQUIREMENTS.md` §6 (Grouping) and `11` §3.3 / O11-2:** the automated event-rate-scheme selection depends on resolving both (a) whether the field is populated on import and (b) how to reach it (Data Bridge vs. grouping API). Until then the §3.3 automation stays *validated-but-unsigned-off*.

---

## 5. Not reached / carried forward

The 8/11 session set today's agenda as package management, **GeoHazard**, the final breakout changes, and the first **results** pass. Package management and the CB breakout landed; **GeoHazard and results did not** — the session ran out of runway (data-loss friction on the migration + Cheryl's 10:00 call). Both roll to the next session.

- **GeoHazard demo** — deferred (continues `07` §, `10` §4).
- **Results — first substantive pass** — viewing loss results in the Workbench, and separately extraction into the loss repository — deferred (flagged as hard in the 8/11 minutes; ties into analysis execution).
- **Grouping walkthrough** — still owed (§4, O11-2).

**Next session:** the following day (Thursday, Aug 13, 2026 — daily cadence, Wendy holding the slot through the week). Likely agenda: review the **packages-removed prototype** and rebuilt submission page, **GeoHazard**, **results**, the **grouping** discussion, and any early **Moody's** answer on the rate scheme.

---

## 6. Open questions

- **O12-1 (structural)** — **Ratify the package retirement.** Confirm the prototype, then land the data-model change: drop `package` / `submission_package` / `package_id` FKs, re-anchor EDM/RDM reuse on the submission, and regrain `irp_job` off the package (§1; DATA_MODEL §4/§8/§14 OQ-2). *Ben → team.*
- **O12-2** — **Submission-scoped RDM display + performance.** Confirm "all RDMs in the submission on every EDM page," the collapse-by-default + lazy-load behavior, and the EDM-switch dropdown against real volumes (§2). *Ben.*
- **O12-3** — **Direct import into a submission.** Build the missing "import a brand-new EDM/RDM straight into an existing submission" path (the glaring gap), unifying it with add-existing under one "add" action (§2.3). *Ben.*
- **O12-4** — **Add-existing search semantics.** Standalone-only filter + fuzzy name "contains" + pagination; confirm it reuses `11` §1.1 semantics and that attribute/tick-box filters stay deferred (§2.3). *Ben.*
- **O12-5 (highest-leverage, carries O11-2)** — **Event-rate-scheme populated on import?** Resolve the Ben/Cheryl disagreement via Cheryl's two-different-rate-schemes test and the Moody's response; determine the retrieval path (Data Bridge queryability vs. grouping API) and whether the field is dropped or merely not returned on upload (§4). *Cheryl (test + Moody's); Ben (API side).*
- **O12-6** — **How grouping works.** Hold the dedicated grouping walkthrough needed to lock the automated rate-scheme selection (§4; `11` §3). *Ben / Cheryl.*
- **O12-7 (terminology cleanup)** — With packages retired, sweep `FUNCTIONAL_REQUIREMENTS.md` §1/§2, `DATA_MODEL.md` §4/§8/§12, `PRD.md`, and spec `002` for package language and reconcile the Excel tracker's affected rows (no baseline-ID renumbering). *Ben / PM.* (§1 impact box)
