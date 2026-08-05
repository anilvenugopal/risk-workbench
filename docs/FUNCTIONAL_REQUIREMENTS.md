# Risk Workbench — Functional Requirements

**Status:** Draft for design discussion · Living document, edited line-by-line in design sessions.

## How to use this document

Plain statements of what the workbench is and does, organized by workflow area. **Each row is exactly one requirement — one fact or one capability.** Requirements are deliberately not combined; if a statement has two independent parts, it is two rows. `Requirement` = a single literal statement · `Implementation` = current delivery state · `Notes` = brief clarification, examples, or the field list a requirement enumerates.

**Implementation status:** `Implemented` = the current application satisfies the requirement · `Partial` = it satisfies only part of the requirement · `Not implemented` = it does not currently satisfy the requirement. For negative requirements and explicit exclusions, `Implemented` means the application honors the stated constraint.


**Scope:** MVP picks up at import into Risk Modeler / DataBridge onward — files arrive already unzipped, attached, and named by the existing SQL workflow. We are designing toward the future state where the workbench also absorbs front-end data setup (zip import, naming inferred from the directory). In/out markers on §3–§7 come from the June 2026 `CReWorkflow_Expanded` review, the July 2026 design sessions (7/7, 7/9, 7/14, 7/16), and the 8/4/2026 demo review.

**Basis:** This revision folds in the July 2026 design sessions and the **August 4, 2026 V1 demo review** — the first functional session of the delivery phase, and a largely *subtractive* one: it overturned several §2.2 rows outright (TIV, the EDM-level aggregate block, portfolio-attributed analyses, the full treaty grid). Rows the session changed are marked **Reversed 8/4** / **Removed 8/4** / **Added 8/4**. Source detail lives in `design_session_notes/03`–`08`. Open questions raised there are surfaced inline as callouts and must be resolved before the PRD locks the affected area.

---

## 0. Access & Identity

| Requirement | Implementation | Notes |
|---|---|---|
| Analysts sign in with SSO (Entra) in production. | Implemented |  |
| Username/password login is a development-only fallback. | Implemented | Never reachable in production. |
| Every authenticated analyst sees every Submission. | Implemented | No row-level access control; roles gate functions, never rows. |
| Submission ownership is a soft "my submissions" marker, not an access gate. | Implemented | See §1. |

---

## 1. Data Organization

Two objects: **Submission** (the deal, top level) and **Package** (an EDM/RDM set, bottom level).

> **Open question — Submission vs. Project:** whether the top-level object is a "submission" (mirrors the directory structure) or a more flexible "project," or whether these attributes just live on the package with no object above it. Deferred to wireframes; the requirements below apply either way. (Design note 03 §2–§4, OQ-1/OQ-2.) **8/4:** the demo used Submission throughout and CIC worked with it fluently without raising the alternative — settled in practice, not formally closed.

> **Open question — does "Package" survive?** The 7/14 review flagged that "package" is not CIC terminology and may add too much organizational overhead; EDMs/RDMs could attach directly to the Submission instead. Held as the working baseline because the import flow works either way. **8/4 partially resolves this:** the *mechanism* is confirmed — a Package points at one physical copy of the data and attaches to many Submissions without duplicating it ("OK, got it") — but the *vocabulary* is not. "Packages" is new terminology for CIC, and the "big package discussion" plus terminology onboarding was deferred for time. (Design note 04 §9, 03 OQ-1, 08 O8-5.)

**Submission**

| Requirement | Implementation | Notes |
|---|---|---|
| The Submission is the top-level object; it represents the deal. | Implemented |  |
| There is no customer or program level above the Submission. | Implemented |  |
| A Submission is one cedant's treaty of a given type at a given inception. | Implemented | e.g. "6/1/2026 CAT XOL for Cedant A." |
| A Submission has a name of its own, distinct from the cedant name. | Implemented | **Added 8/4.** The name on the contract is often not the name of the submission — an MGA writing on behalf of an insurer puts both names in the submission name but only one on the contract: "I think it's right to keep them separate." Today the name is a copy/paste from CRM. |
| A Submission is displayed by its name plus cedant name + treaty type + inception date. | Implemented | Readable identifiers analysts recognize at a glance. |
| A Submission has these attributes: name, cedant name, treaty type, inception date, treaty year, directory path, CRM ID(s). | Implemented | Directory path = the deal's shared-drive folder. |
| A Submission has exactly one treaty type. | Implemented | **Added 8/4.** Multi-select was floated and rejected: one submission carries one treaty type. Treaty-type multiplicity lives at the *data* level — the same data serving a cat treaty and a per-risk treaty is handled by a shared Package, not a multi-valued field. |
| Treaty year defaults to the inception year and stays editable. | Implemented | **Changed 8/5.** The 8/4 wording was "derived from the inception date… never free-text entry." Picking an inception date now fills the treaty year, and the server fills it on save when the field is left blank, so nobody types it in the normal case. It remains editable because a December inception is often written into the following treaty year. Reverses design note 08 §2.1 D4 — re-confirm with CIC. |
| The cedant field is workbench-side only; it is not linked to the Moody's/EDM cedant field. | Implemented | **Added 8/4.** There is no exposure data at submission-creation time to link against — cedant arrives *with* the exposure. "Totally fine, I just wanted to make sure." The label needs disambiguating: "cedent" is also a specific EDM field. |
| The cedant field is a typeahead over existing cedants. | Implemented | A keyboard-navigable menu (arrows, Enter, Escape) replacing the `<datalist>` the 8/4 session rejected as "not what you think in terms of an auto populate drop down type of deal." Matches anywhere in the name, so "fam" finds "American Family Mutual"; free text is still accepted, since there is no cedant registry. |
| Name, cedant, treaty type, and inception date are required at creation. | Implemented | Confirmed 8/4 — "seems fair." |
| Required fields are marked explicitly in the form. | Implemented | **Added 8/4.** Name, cedant, treaty type and inception date carry an asterisk under a "* Required" legend, and a failed submit now names each bad field under its own input instead of returning one combined message. |
| CRM ID is the only guaranteed-unique attribute. | Partial | Everything else can overlap. |
| The descriptive attributes (cedant, treaty type, inception) can legitimately collide across distinct deals. | Implemented | e.g. a regional cat and a corporate cat — same cedant, same inception, both "cat" — differing only by CRM ID. |
| The workbench does not enforce uniqueness on the descriptive attributes. | Implemented | Identity rests on a surrogate key; a non-blocking "similar deal already exists" warning guards against accidental re-creation (PRD §7.2b). Copy-a-submission (below) deliberately produces near-duplicates, so this warning must stay non-blocking. |
| A Submission can be tagged with zero or more CRM IDs. | Partial | CRM ID = a contract. Standard case is one; multiple contracts can share one Submission. Optional soft reference. Reaffirmed 8/4 — the "one CRM ID" remark that session was the argument for single-select treaty type, not a change to CRM cardinality. |
| CRM detail is shown on the submission page. | Not implemented | **Added 8/4** — does not exist today. |
| A Submission is assignable to a user (owner). | Implemented | Enables a "My submissions" filter/sort. |
| Submission ownership can be reassigned. | Implemented | On the submission detail page. |
| Submission ownership does not control access. | Implemented | All users see all Submissions (§0). |
| A Submission has a status: Active, Completed, or Cancelled. | Implemented | Set manually. Three statuses only — confirmed 8/4. |
| Submission status can be reopened from Completed or Cancelled back to Active. | Implemented | Transitions are reversible; not a one-way door. |
| Every status transition records a reason. | Implemented | Retained as a history trail. |
| The status history trail is collapsible. | Partial | **Added 8/4.** Repeated transitions otherwise produce "a huge block here in the submission page." |
| A Submission is never deleted. | Implemented | Cancelled is the "not happening" outcome in place of a delete. |
| A Submission can be copied into a pre-populated create form. | Not implemented | **Added 8/4 (Cheryl).** "A lot of the data that we're going to have on that submission page is going to be duplicative… it'd be nice to be able to just copy it, make the edits that I need, update the CRM ID." The result is a **new** Submission, not an edit of the original. |
| A Submission can link to a related Submission. | Implemented | UI label is **"Links to"** (8/4, superseding "Previous" from 7/14) — the relationship is a link to a related submission, not necessarily a renewal; the team also jumps back to compare. The analyst picks the related deal by name from a typeahead over name and cedant; the column is `submission.links_to_submission_id`. Link may come from the treaty system later. |
| The Submission's directory is chosen with a folder browser and validated, not typed. | Not implemented | **Added 8/4.** "Rather than just have a random free text field like I could put anything in here." The Submission is hard-linked to the folder. |
| The directory-path link opens a file browser at the deal's folder. | Partial | Useful jump-off, but the working/BAK file is often not in that folder — see §2.1. |

> **Parked — CRM integration.** A future phase, not MVP. CRM holds the submission name, company/cedant name, inception date, and CRM ID: "especially based on that CRM ID, we could pull a lot of data in." Today the submission name is copy/pasted from CRM by hand. (8/4)

**Package**

| Requirement | Implementation | Notes |
|---|---|---|
| A Package associates a set of EDMs with the RDMs that go with them. | Implemented | "This EDM goes with these RDMs." Mostly a creation-time grouping; not displayed constantly through the app. |
| A Package can be any combination of EDMs and RDMs. | Implemented | One or many EDMs, one or many RDMs, EDM-only, or RDM-only. e.g. 4 EDMs + 1 RDM is valid. |
| A Package requires at least one EDM or RDM. | Implemented |  |
| A Submission has one or more Packages. | Implemented |  |
| A Package can be shared across Submissions. | Implemented | Same exposure and results reused across deals. |
| A Package points to one physical copy of the EDM/RDM data. | Implemented | **Confirmed 8/4.** Attaching a Package to another Submission does **not** duplicate the data — "same, same underlying data is the idea." This is the mechanism that serves CIC's real case of "one set of data that goes with multiple submissions." |
| An existing Package can be attached to an additional Submission after creation. | Not implemented | "Add existing package" — demoed as a concept 8/4, not yet built. |
| A Package is unique in its own right, not owned by any single Submission. | Implemented | Its EDM/RDM names are likewise unique. |
| Work against a shared Package propagates to every Submission that shares it. | Implemented | Analyses are hard-coupled to the EDM in Risk Modeler. Accepted and often preferred. **Confirmed 8/4** (was provisional — design note 03 §5). |
| No duplicate EDMs are created just for tracking. | Implemented | If isolation is genuinely needed, add a new portfolio within one EDM. |
| Multi-select of EDMs/RDMs is supported. | Implemented | Select several at once to move or import them together. |

**EDM / RDM**

| Requirement | Implementation | Notes |
|---|---|---|
| An EDM is an exposure database. | Implemented |  |
| An RDM is a results (losses) database. | Implemented | Importing an RDM into Risk Modeler creates Analysis objects, not an RDM object. |
| An EDM/RDM is created from a source file: `.bak` or `.mdf`. | Implemented | Zip in the future state. BAK is the path of least resistance — the naming convention is already embedded in the file name. |
| An EDM/RDM name must be unique in Risk Modeler (global). | Implemented |  |
| An EDM/RDM name must be unique on the on-prem SQL server. | Implemented |  |
| The workbench checks name uniqueness before import and surfaces an error on collision. | Implemented |  |
| A Risk Modeler name collision blocks the Package until the user renames. | Implemented | Checked against Risk Modeler by API at package creation — a block, not a warning (8/4). |
| Naming convention: `TY{YY}{MM}_{Cedant}_{InforceDate}_{Version}_{EDM\|RDM}`. | Implemented | Add a distinguisher (business unit, region, treaty type) when databases would otherwise collide. |
| The name is auto-populated from the chosen import file name. | Implemented |  |
| The name is editable in place before import. | Implemented | On collision, the user adds a suffix. |
| An EDM/RDM has a status: Pending, Uploading, Ready, Upload Failed. | Implemented |  |
| Rename-on-reattach is supported (~15% of the time). | Not implemented | e.g. mid-deal M&A — reattach/rename exposure for the acquiring company; also keeping test versions (v1 vs v2) separate. |
| On duplicate-and-rename, the workbench offers to auto-delete the old copy. | Not implemented |  |

**Finding work**

| Requirement | Implementation | Notes |
|---|---|---|
| The homepage opens to a Submissions list. | Partial | Sorted by owner and last-updated; surfaces the logged-in user's own work first (7/14). 8/4 reopened **what else belongs on the landing page** — it is empty today and the call is unmade (design note 08, O8-4). |
| Submissions can be searched by name. | Not implemented | **Added 8/4** — missing entirely: "that is a need to add." |
| Submissions can be filtered by cedant name, treaty type, inception date, owner, and CRM ID. | Partial | Cedant, treaty type, and inception are primary; CRM ID is secondary/retrievable. |
| Submissions can be sorted. | Partial |  |
| Multi-term search uses AND semantics, not OR. | Not implemented | **Added 8/4.** "There must be 1000 companies that have American in the name" — typing "American Family" must not return every company containing "American." Tolerant enough to be useful, but "it can get too fuzzy is my point." |
| Global search runs across Submissions, Packages (EDMs, RDMs), analyses, jobs, and results. | Not implemented |  |
| Search, sort, and filter are available on every list section. | Not implemented | 7/14: wanted on portfolios, treaties, analyses, and results — not just the submissions list. |

**Navigation & drill-down**

| Requirement | Implementation | Notes |
|---|---|---|
| Navigation is three-tier: Submission → Package → EDM. | Implemented | The actual work happens at the EDM level (7/14). |
| An EDM drills down to its portfolios. | Implemented | Analyses run against a portfolio, not a whole EDM. |
| The effective navigation depth is Submission → Package → EDM → Portfolio. | Implemented | The portfolio drill-down is also the entry point for kicking off analyses. |
| An EDM page shows its parent Package and Submission as clickable links. | Implemented | **Added 8/4.** Upward navigation context — "I don't know where I am" without it. Lets the analyst hop back to the Package and into another EDM "without starting all over again from the top," which matters because a Submission can hold any combination (one EDM and three RDMs, three EDMs and one RDM). |

---

## 2. Data Setup

Getting a deal's data into the workbench and seeing what's in it.

### 2.1 EDM / RDM Import

| Requirement | Implementation | Notes |
|---|---|---|
| The user can create a Submission. | Implemented | A Submission can exist without a Package. |
| The user can create a Package. | Implemented |  |
| A Package is created by choosing its EDM/RDM combination. | Implemented |  |
| A Package is created by choosing the Submission(s) it lives in. | Implemented |  |
| The new-package flow opens to name the package and browse for files. | Implemented | The package name does not default to the submission name (7/14). |
| The analyst browses network shares and selects file(s). | Implemented |  |
| File browsing is restricted to network drives — never the local machine. | Implemented | Both the analysts' machines and the app are connected to the shares. |
| Two network locations are supported: a working drive and an archive drive. | Partial | e.g. M = client/working drive, L = archive/BAK drive; the archive is not organized the same way and may split into in-force / projected subfolders. |
| The file browser supports folder-tree navigation. | Implemented |  |
| The file browser supports search by file name. | Not implemented | Especially valuable for BAK files stored under non-recognizable names. |
| The file browser supports free navigation to any location. | Not implemented | The linked deal folder is often not where the working/BAK file lives. |
| Multi-select of files is supported. | Partial | Pick several at once. |
| The workbench does not move or reorganize files on the shares. | Implemented | It reads source files in place. |
| EDM vs RDM classification is inferred from the naming convention. | Implemented | ~90% of names contain "EDM," "RDM," or "results." |
| The inferred EDM/RDM classification can be flipped in place. | Implemented | Corrects the type when the name isn't reliable. |
| The name is auto-populated from the file and editable before import. | Implemented |  |
| An error is shown if the name is not unique in Risk Modeler or on-prem SQL. | Implemented | The analyst adds a suffix. |
| The analyst can Save a Package without syncing to Risk Modeler. | Implemented | Stages the selection while still gathering files across directories. |
| The analyst can Save & Sync to import the Package into Risk Modeler (DataBridge). | Implemented |  |
| Imports run in the background. | Implemented | The analyst can leave the page and keep working — avoids Risk Modeler's slow, blocking upload. |
| All selected files can be submitted for import at once. | Partial | The system runs ~10 concurrently and auto-dequeues the rest as slots free up; chunking gives no benefit (7/14). |
| A delete-after-transfer checkbox removes the temporary BAK file once imported. | Not implemented | Otherwise temp files accumulate on the share forever. |
| Import status is tracked per EDM/RDM. | Implemented |  |
| Import status is tracked per Package. | Partial | Package-level status tells us when it is ready to work on. |
| An EDM/RDM already in Risk Modeler can be linked to a Submission without re-importing. | Implemented | "Orphaned" databases not created by the workbench — select them into a Package. |
| All EDMs are browsable in an EDM Library. | Implemented | Includes workbench-created and orphaned. |
| All RDMs are browsable in an RDM Library. | Implemented | Includes workbench-created and orphaned. |

> **Open question — BAK vs MDF vs zip import path:** whether import points at BAK files in a shared BAK directory, or selects MDF/zip files from a network drive (varying by cedant/submission/treaty year), is unresolved. (Design note 04 §8.2, O4-2.)

**CSV import — deprioritized outlier for MVP.** CSV-for-EDM is MRI (accounts + locations files together), rare for their data. CSV-for-results is HD/PLT output, not natively supported for RDM import in Risk Modeler. Open question: what can Risk Modeler actually *do* with a CSV result once imported — if it's view-only, there's no point (design note 03 §8, OQ-6). Push cedants/brokers to provide RDMs instead.

### 2.2 Exposure Details Viewing

Rolled up so the analyst doesn't click through Risk Modeler — a fast textual snapshot. **The 8/4 review was subtractive here:** three built columns — the per-portfolio analyses count, TIV, and the EDM-level aggregate block — were removed as untrustworthy or misleading, and the space repurposed for information CIC actually uses.

**Scope of the roll-up**

| Requirement | Implementation | Notes |
|---|---|---|
| A map is not needed. | Implemented | A fast textual snapshot is what's wanted. |
| The existing Power BI exposure dashboards are not rebuilt. | Implemented |  |
| No EDM-level aggregate roll-up is shown. | Not implemented | **Reversed 8/4.** The aggregate counts double-count: "you can have the same locations duplicated for wind and for severe convective storm… saying that I've got double the amount of locations isn't really double the amount of locations… I don't think that that summary gives me much." Still shipped as `partials/edm_aggregate_strip.html` (spec 004 US4). |
| Figures are shown per portfolio, inside a specific EDM. | Implemented | Analyses run on a portfolio, not the EDM (7/14). The per-portfolio table is the valuable part — "having just the list of portfolios and… some information about them is really great in one place because you can't do that today in RiskLink." |

> **Trust rule (8/4) — metadata originating outside CIC's environment cannot be trusted.** There is no reliable way to tie an RDM analysis to a specific EDM portfolio: "there actually is no way to tie an RDM analysis to a specific EDM portfolio that you can trust." A false link was demoed live — a US EQ analysis attributed to a USFL portfolio ("clearly they're wrong… it's not possible"). Attribution is defensible only when the EDMs/RDMs never left CIC's environment. Analyses from a linked RDM may be **listed** on the EDM page, never **attributed to a portfolio**. This narrows spec 004 FR-036 / PRD §21 Iteration 3 — see §7.

**EDM header**

| Requirement | Implementation | Notes |
|---|---|---|
| The EDM header shows name, status, source path on the share, Risk Modeler ID, and job ID. | Implemented |  |
| The number of portfolios in the EDM is shown. | Implemented | Sometimes 1 portfolio, sometimes 25. |
| The EDM header shows its parent Package and Submission as clickable links. | Not implemented | **Added 8/4.** See §1 Navigation & drill-down. |
| The EDM header shows the as-of / last-synced timestamp. | Implemented |  |
| A manual Sync action re-reads the EDM from Risk Modeler. | Implemented |  |

**Per-portfolio figures**

| Requirement | Implementation | Notes |
|---|---|---|
| Location, account, and policy counts are shown per portfolio. | Implemented |  |
| Perils covered are shown. | Implemented | Sub-perils are an analysis-settings attribute (§7), not an exposure attribute (2026-07-28). |
| Lines of business are shown per portfolio. | Implemented | Read from LOBDET joined through the policy table, so only values **actually present in the data** are returned — a cedant's LOB table may carry 30–40 values where the delivered data uses two (8/4, confirmed: "that's what we want"). |
| Countries are shown, ahead of states. | Not implemented | **Added 8/4.** "I actually want to see countries, not states" — the immediate question is "this is labelled US EQ; is it actually US only?" No country is queried today; `portfolio_states.sql` returns Admin1 only. |
| States / state-equivalents are shown as a separate column. | Partial | **Added 8/4** — geography splits into two columns, country first. Today a single Geography column carries states. |
| Geography collapses to a single value when there is one, and to a count or "multi-country" when there are many. | Not implemented | Named regions are **not** the collapse value: regions are treaty-dependent, not fixed constants (§3). Named regionality stays a treaty-scoped concept in the breakout flow; the roll-up collapse is open with Cheryl (design note 08, O8-1). |
| The list of currencies present is shown. | Implemented | Even "multiple currencies" is useful — "like that's a win." |
| Currency is never converted or aggregated. | Partial | **Reversed 8/4.** "As soon as you put currency in, then people are going to say, how did you get that? What was the rate that you used?" Nothing converts today, but the EDM aggregate strip still unions currencies across portfolios — goes with the aggregate block above. Currency defaulting/handling for *analysis* is specified in §4. |
| Currencies are gathered from every place currency lives in the EDM. | Partial | Policy/PORTINFO, location, and location-coverage level — "you can't just pull it from one spot." Today only `loccvg.VALUECUR`. CIC's validation-report SQL enumerating all ~15 fields is inbound (design note 08, O8-3). |
| Total insured value is not shown. | Not implemented | **Removed 8/4.** Currency conversion makes the figure indefensible — "too many questions that come into how'd you get that." The TIV column and `portfolio_total_tiv.sql` are still live. |
| Analyses are not counted or attributed per portfolio. | Not implemented | **Removed 8/4.** See the trust rule above. The Analyses column and the `exposureResourceId` linkage note are still live. |
| Record volume is shown. | Implemented | So the analyst doesn't accidentally run a ~1M-record portfolio thinking it's ~20K, and can schedule large runs (e.g. start a 4M-record run overnight). |
| Reinsurance/treaties associated with the EDM are shown. | Implemented | LOB and cedant come from the EDM details. |
| Truncated value lists expand in place. | Not implemented | **Added 8/4.** Lines of business, geography, and currencies all cut off mid-list today: "whether you have hovering capabilities or you have to click in or whatever… to be able to expand some of those lists." |

**Free-text field caps** (8/4)

| Requirement | Implementation | Notes |
|---|---|---|
| A free-text descriptor field with more than ~500 distinct values is not saved into the roll-up. | Not implemented | Line of business is the known case: a completely user-defined descriptor that does not affect analysis, which cedants populate with "10s of thousands of different and unique values" — account numbers, underwriter names. "If it's over 500 values, we're not going to save it out." |
| Front-end expansion of a value list is capped around 100. | Not implemented |  |
| No elegant handling is required for the pathological case. | Implemented | Explicit guidance: "It doesn't need some elegant options that we go through… I don't want you to overthink that scenario." |

> **Open — which other fields share this pathology?** "There's other fields like that in this EDM as well." Ben to compile the list with Wendy and Cheryl. Also pending: performance-test the LOB JSON storage at ~10,000 key-value pairs. (Design note 08, O8-2.)

**Freshness** (8/4)

| Requirement | Implementation | Notes |
|---|---|---|
| Roll-up figures are read from the workbench database, not fetched live from Moody's per page load. | Implemented | Live API calls per page load are an unacceptable performance overhead. |
| The accepted cost of backfill is drift when someone edits directly in Risk Modeler. | Implemented | Managed by the last-synced timestamp, the manual Sync action, and freshness validation below. |
| Freshness is validated before any action that depends on current exposure. | Not implemented | Sub-portfolio creation is the first such action (§3). |

**Treaty viewing** (7/14, revised 8/4)

| Requirement | Implementation | Notes |
|---|---|---|
| The treaty list is condensed by default. | Implemented | **Reversed 8/4.** The full attribute grid is "a lot to look at… just a lot of information with that scrolling." |
| The condensed view carries ~3 significant fields beyond the identifiers. | Partial | Enough to notice something unexpected — "oh, there's a quota share in there. I wasn't expecting to see a quota share." Seven summary columns today, which is the scrolling complaint. Which three is open (design note 08, O8-6). |
| Full treaty attribute detail is available on expand. | Implemented | Preserves the 7/14 intent — all attributes reachable to catch mis-coding rather than blindly trusting it ("sometimes people put the wrong thing in the wrong field") — via expand rather than default density. |
| Treaties expand and collapse. | Implemented | Few treaties shown expanded; many collapse to focus one at a time. |
| Treaty attribute labels are spelled out, not Risk Modeler's cryptic codes. | Partial | e.g. resolve attachment level vs. attachment basis; "loc" = location. |
| Timestamps are shown at the granularity Risk Modeler actually uses. | Partial | "These timestamps are ugly" — currently too granular. Summary columns are date-truncated; the expanded grid still shows raw values. |
| Duplicated-looking treaty attributes are reconciled or removed. | Not implemented | Some attributes appear twice in the demoed grid; the attribute map is stored verbatim. |
| Treaties can be exported to Excel. | Implemented | For extreme cases with too much to render cleanly. |
| Treaty setup is shown at the EDM level. | Implemented | Treaties can be applied at portfolio, account, or policy level; results are spit out to the RDM. |
| For a per-risk treaty, the losses subject to the treaty are the interest — not the portfolio itself. | Not implemented | No loss numbers this iteration. |
| Inuring reinsurance is identified and removed to get the net perspective. | Not implemented | The models do this. No loss numbers this iteration. |

*(Treaty creation/editing is a pass-through to Risk Modeler — §5.)*

### 2.3 Loss Results Viewing

Reviewing broker-provided results and settings. Full results review and delivery in §7.

| Requirement | Implementation | Notes |
|---|---|---|
| Broker-provided (RDM) analyses can be reviewed before running own analyses. | Implemented |  |
| Each broker result shows its analysis settings/metadata. | Implemented | Full metadata list in §7. e.g. rate, perils/sub-perils, loss amplification on/off, detail level saved. |
| The portfolio an imported RDM's analysis ran against is not shown as a link. | Partial | **Narrowed 8/4.** That attribution is untrustworthy for anything that left CIC's environment — see the §2.2 trust rule and §7. |
| The results view helps the analyst decide how much work a given RDM even needs. | Implemented | Sometimes the broker already provides what's needed and the analyst only pushes losses to the repository — no remodeling. |

**Out of scope for MVP:** policy-level detail; PDFs and other non-modeling exhibits (File Explorer suffices).

---

## 3. Data Modification

Re-shaping exposure to match treaty terms before analysis. This cannot be done in the current workflow tool (done in RiskLink today, which is slow); Risk Modeler makes it fast and synchronous, so it becomes a *preferred* path.

| Requirement | Implementation | Notes |
|---|---|---|
| Sub-portfolios are created by filtering an EDM's exposure. | Not implemented | To match terms the broker didn't break out — e.g. isolate a state with a different retention, or exclude a line of business. |
| The current portfolio split is visible before deciding how to re-group. | Not implemented |  |
| Filter values are picked from the real values present in the portfolio. | Not implemented | Not free-text — "people put crazy things in the LOB field," and typing them exactly is messy. |
| Sub-portfolio creation is synchronous. | Not implemented | Fastest operation in the whole flow (HTTP 201, no job). |
| Exposure freshness is validated before sub-portfolio creation. | Not implemented | **Added 8/4.** The roll-up is backfilled rather than live (§2.2 Freshness), so a stale read must not drive a portfolio split. This is the first action that depends on current exposure. |

**Single-click breakouts** (7/16, Ben to build)

| Requirement | Implementation | Notes |
|---|---|---|
| One-click breakout by line of business creates one sub-portfolio per LOB. | Not implemented | Simplest case; unaffected by the commercial-geo problem below. |
| One-click breakout by state/country creates one sub-portfolio per geography. | Not implemented |  |
| One-click complement split ("X vs. not-X") creates one portfolio for selected states and one for everything else. | Not implemented | e.g. Northeast and everything-not-Northeast, from a single action. |
| Breakouts sum to 100% of the source portfolio. | Not implemented | Not "run the whole thing, then a subset, and subtract" — that's messy. |
| A "do the opposite" option produces the complement of a defined filter without re-coding it. | Not implemented | Define "Florida mobile home" once and also get "everything that's not Florida mobile home." |
| Portfolio-creation granularity is capped at state/country. | Not implemented | Finer than that (CRESTA, ZIP) is saved as output, not a portfolio — "too much to manage." |
| Regions are not pre-defined constants. | Not implemented | "Northeast" is defined by the treaty / how the cedent writes the business; contracts define coverage arbitrarily. |

> **Open question — commercial-policy geographic split (blocking for the geography breakouts).** Splitting a multi-location commercial policy geographically breaks its financial structure and can double-count in a complement split (keep-all-locations behavior). Whether Risk Modeler keeps all locations or only matching ones — and whether it exposes a toggle — is unconfirmed; a RiskLink "checkbox" recollection is not load-bearing. Output-side alternative: write losses to the state level and let the model allocate back. Ben investigating RM behavior; Cheryl polling the team for the preferred default. (Design note 06 §3, O6-1/O6-2.)

**Out of scope for MVP:** peril splitting / peril-specific portfolios (no longer needed — "we don't have to split it up by peril"; verify whether RM adds a missing peril); update/change data elements; data validation reports; exposure profiling / loading summaries to the Exposure Repository; **merge/combine portfolios** (recombination happens on results, not exposure — design note 06 §4).

---

## 4. Analysis Configuration

Setting up analyses. Configuring a worldwide contract by hand is the #1 analyst pain point (50–150+ combinations).

**Profiles & settings**

| Requirement | Implementation | Notes |
|---|---|---|
| Standard model (DLM) profiles are selected from a pre-compiled list. | Not implemented | Profiles are created and managed in Risk Modeler; the workbench selects, it does not own profile management. |
| Multiple model profiles can be selected for one portfolio/treaty combination. | Not implemented |  |
| Output profiles are selected from a pre-compiled list. | Not implemented | Also created and managed in Risk Modeler. |
| User-defined (UD) profiles are supported and selectable. | Not implemented | Naming convention `UD` + initials, e.g. UDCT. |
| Profile lists can be filtered. | Not implemented | When the list grows long — "just get to UDCT." |
| An event-rate scheme is configurable per analysis. | Not implemented | People are "very picky" about it. |
| Franchise deductible is an exposed per-analysis toggle. | Not implemented | Deal-specific; the team wants direct access — the exception to holding advanced settings constant. |
| Unrecognized construction / occupancy type is an exposed per-analysis toggle. | Not implemented |  |
| Min loss threshold and max loss event stay at defaults. | Not implemented | Held constant, not surfaced. |
| Tags can be set per analysis. | Not implemented |  |
| Loss/analysis currency is selected per analysis. | Not implemented | Assigned in the analysis builder; changing it affects only the selected analyses. |
| Analysis currency defaults to the exposure's native currency when the exposure is one-to-one. | Not implemented | A single currency in the exposure. |
| Analysis currency defaults to USD otherwise. | Not implemented | General default; a US book must not default to Euros. |
| The latest currency scheme is used by default when rerunning. | Not implemented | Currency scheme = the exchange rate at a point in time. |
| A custom currency scheme can be selected when one exists. | Not implemented | The workbench does not build or import schemes — those are built in Risk Modeler. |
| Treaties are selected by name or pattern. | Not implemented |  |
| DLM requires an event-rate scheme. | Not implemented | Determined by the model profile, not the file. |
| HD makes the event-rate scheme optional. | Not implemented | Determined by the model profile, not the file. |

**Templates & suites**

| Requirement | Implementation | Notes |
|---|---|---|
| Model profile, output profile, and event-rate scheme are the "big three" configured settings. | Not implemented | These define an Analysis Suite. |
| Analysis templates can be saved. | Not implemented |  |
| Templates are collected into suites. | Not implemented | e.g. "Global 2026 Q1." |
| An Analysis Suite is a pre-configured set of (model profile, output profile, event-rate scheme) combinations. | Not implemented | Solves the "global book" pain of setting up every model one at a time. |
| Applying a suite generates all its analyses at once. | Not implemented | Ready to review and adjust before submitting. |
| DLM and accumulation analyses are kept in separate suites. | Not implemented | Don't combine them (§5, §7). |
| Analysis names are auto-generated. | Not implemented | Typing a name every time is a pain. |
| An analysis can be renamed, including after it has run. | Not implemented |  |

> **Open question — auto-naming convention.** The draft convention draws on portfolio name + near-term/long-term + event-rate scheme, but is not finalized. (Design note 07 §2.3, O7-3.)

**Out of scope for MVP:** profile management (created and managed in Risk Modeler); schedule and stagger analyses (add only if RM makes it easy).

---

## 5. Analysis Execution

Running the work and tracking it — including GeoHaz and treaty setup.

**Geocoding & hazard**

| Requirement | Implementation | Notes |
|---|---|---|
| Hazard lookup can be run on a portfolio. | Not implemented |  |
| Geocoding is not re-run by default. | Not implemented | Broker geocoding is preserved — Cheryl has never re-geocoded in this role. |
| Re-geocoding, if ever needed, is done intentionally inside the model. | Not implemented | Not a workbench action. |
| Hazard lookup defaults to the latest data version. | Not implemented | v25 as of now. |
| Hazard lookup defaults to DLM (non-HD). | Not implemented |  |
| Missing locations are not skipped; they are overwritten. | Not implemented | "The more comprehensive the data, the better." |
| Earthquake and windstorm perils are selected by default. | Not implemented | Toggleable. |
| Running an inapplicable peril returns zero for that layer, not a failure. | Not implemented | e.g. earthquake on a windstorm book. |
| The hazard job returns a summary of locations looked up per layer. | Not implemented |  |

> **Open questions — hazard for HD / enhanced risk data.** Whether hazard retrieval must be run ahead of time for HD models is unconfirmed (O7-1). Enhanced risk data is not used today and may be HD-only; availability and whether CIC will want it is being checked (O7-2). Cheryl investigating both. (Design note 07 §1.3.)

**Treaty & reinsurance editing — pass-through**

| Requirement | Implementation | Notes |
|---|---|---|
| Adding or editing a treaty is a pass-through to Risk Modeler. | Not implemented | The workbench does not rebuild the RM treaty editor. Reconfirmed 8/4 — "you don't need to spend your time trying to reinvent the wheel… It's a data entry situation." The workbench treaty list links directly out to the Risk Modeler treaties page. |
| Adding or editing reinsurance is a pass-through to Risk Modeler. | Not implemented | Confirmed again 7/16 — "a perfect scenario for that." |
| A pass-through opens the Risk Modeler editor in a new window; the analyst edits and saves there, returns, and the page refreshes. | Not implemented | General pass-through pattern (design note 04 §7): where the workbench would only re-skin RM, hand off to RM. |

**Running & tracking**

| Requirement | Implementation | Notes |
|---|---|---|
| A single analysis can be submitted against a portfolio. | Not implemented |  |
| Multiple portfolios can be selected and run in one action. | Not implemented | Cheryl recently had to rerun the same data across 6 portfolios one at a time. |
| All analyses in a suite (50–150+) can be batch-submitted in one action. | Not implemented |  |
| Accumulation analyses can be run. | Not implemented | In scope, with accumulation-specific settings; output detail in §7. |
| Job status is tracked live and auto-refreshed, per deal and overall. | Not implemented | Only progress useful to the modeler is shown. |
| Two job classes are tracked: IRP jobs and workbench jobs. | Not implemented | **Added 8/4.** IRP jobs are submitted to Moody's and polled for state; workbench jobs cover uploads and other wrapping/independent work. |
| Jobs are associated with a parent Submission, Package, or EDM. | Not implemented | **Added 8/4.** Not one flat global list — Moody's "just stacks everything up," and "there's no point in replicating it exactly like" that. |
| Heavy work never runs on the request path. | Not implemented | The user is never blocked on an upload. |
| An activity indicator distinguishes actively-running from stuck. | Not implemented | **Added 8/4.** Status text alone gives too little insight: "as an analyst… we are antsy… we just want to get things moving through and we're going to want to see, oh, is it stuck? Is it happening? Is anything going on?" |
| Throughput is monitored — locations analyzed per day. | Not implemented |  |
| Submission failure is distinct from run failure. | Not implemented |  |
| Failed jobs can be retried. | Not implemented |  |
| The analyst is notified when a job completes. | Not implemented |  |
| The analyst is notified when a job fails. | Not implemented |  |
| Only operations whose inputs exist are offered. | Not implemented | EDM → portfolio → analysis → grouping. Prevents starting work that can't succeed. |

**Out of scope for MVP:** cedant ID check/creation; checking treaty coding accuracy (manual — treaty display in §2.2 helps the analyst catch it); **bulk treaty creation from CSV/Excel** — deprioritized 8/4: not something CIC does today, and "whether I do it in Excel or I do it in Risk Modeler, I basically have to do the same steps either way. At least there's some error checking that happens in Risk Modeler." Whether Risk Modeler even supports it is unknown and now academic (design note 08, O8-7).

---

## 6. Grouping

Combining and breaking out results across dimensions.

| Requirement | Implementation | Notes |
|---|---|---|
| The analyst selects which analyses to group. | Not implemented |  |
| Invalid groupings show error messaging. | Not implemented | e.g. mixing DLM and HD analyses. |
| Nested grouping (groups of groups) is supported. | Not implemented |  |
| A group is treated like any other analysis. | Not implemented | Viewed and exported the same way. |
| Group names are auto-generated from the deal. | Not implemented |  |

**Out of scope for MVP:** create ELTs by zone / county / country (done in SQL or the old tool today).

---

## 7. Results Management

Reviewing, comparing, and delivering finalized results. Volume is highly variable (4 to 100+ analyses per submission).

**Organizing & displaying results**

| Requirement | Implementation | Notes |
|---|---|---|
| Results are displayed grouped under the RDM that produced them. | Not implemented | Matches the current workflow; the EDM↔RDM relationship is already implied by the package. |
| Broker results are deduped by RDM. | Not implemented | (`rdm_id`.) |
| Portfolio↔analysis linking is not solved. | Implemented | Deliberately deferred — it doesn't exist today either; analysts rely on naming conventions and broker documentation. **Scope note:** this is the results-comparison linking (which analyses to line up own-vs-broker), still deferred. It is *distinct* from showing the **portfolio an analysis ran against** (§2.3 metadata), which Iteration 3 surfaces by resolving Risk Modeler's `exposureResourceType = PORTFOLIO` exposure pointer (spec 004 FR-036; PRD §21 Iteration 3) — **now narrowed, see the next row.** |
| The resolved exposure pointer is trusted only for analyses CIC ran itself. | Partial | **Narrowed 8/4.** For imported/broker RDMs the pointer is untrustworthy: "there actually is no way to tie an RDM analysis to a specific EDM portfolio that you can trust," and a false link was demoed live (a US EQ analysis attributed to a USFL portfolio). Trustworthy only if the EDMs/RDMs never left CIC's environment; not displayed as a link otherwise. See the §2.2 trust rule. |
| Up to ~5 analyses are consumable on screen. | Not implemented | Default density guideline, not a hard cap. |
| More than ~5 analyses are exportable / drill-down. | Not implemented |  |
| Full listing / full drill-down of all analyses remains available. | Not implemented | The on-screen cap must not become a step back from RiskLink, which lists them all. |

**Metrics & metadata**

| Requirement | Implementation | Notes |
|---|---|---|
| ELT summary is shown: AAL, max event loss, record count. | Not implemented |  |
| AAL / pure premium is shown. | Not implemented |  |
| Standard deviation is shown. | Not implemented |  |
| Return-period loss numbers are shown. | Not implemented | Indicative set: 1000, 500, 250, 100, and ~20–25 year — exact points to be confirmed (O5-2). |
| OEP and AEP are both shown. | Not implemented | Cheryl uses OEP more. |
| A TCE (tail conditional expectation) toggle is available. | Not implemented | Not routinely used, but nice to toggle. |
| An EP-curve graph is not required. | Implemented | "The drawing's not important… I want the numbers." |
| PLT is shown (HD only). | Not implemented |  |
| Results can be switched between financial perspectives. | Not implemented | Perspective switching is essential — Gross, Ground-Up, Reinsurance Layer / net; "look at it from however you ran it." |
| Analysis metadata is shown alongside results. | Not implemented | See the metadata list below; reused for broker-result review (§2.3). |

Analysis metadata list (design note 05 §2): engine / model version · engine type (DLM vs HD) and version · analysis type / mode · peril (primary and secondary) · region · currency · construction · line of business · group type · long-term vs near-term · event-rate scheme / rate vintage · loss amplification (PLA). *Rate/event-rate detail lives one drill-down deeper than the rest (RiskLink "analysis summary" vs the main grid).*

> **Open question — event-rate scheme round-trip.** The event-rate scheme does not appear to survive a Risk Modeler export → re-import (exactly the broker scenario); near-term/long-term and rate vintage both matter. Ben investigating how to recover/carry it, and whether "vintage" is even a first-class RM concept. (Design note 05 §3, O5-1.)

**Comparison**

| Requirement | Implementation | Notes |
|---|---|---|
| Own and broker results can be viewed together. | Not implemented | Multiple analyses in one view. |
| Analyses can be compared side-by-side. | Not implemented | Ben has a prior comparison engine to build on. |
| The side-by-side comparison includes a percent-difference column. | Not implemented | e.g. CIC vs. broker — saves the manual Excel step. |

**Editing return periods — pass-through**

| Requirement | Implementation | Notes |
|---|---|---|
| Editing return periods / interpolation is a pass-through to Risk Modeler. | Not implemented | A subset of business needs return periods at specific loss intervals; same pattern as treaty edit (§5). |

**Accumulation results**

| Requirement | Implementation | Notes |
|---|---|---|
| Accumulation output perspectives are gross and pre-cat net. | Not implemented | Reinsurance-layer (RL) retained. |
| Ground-up is currently included in accumulation output. | Not implemented | A Risk Modeler UI constraint, not a preference; possibly droppable via the API (O7-5). |
| Accumulation shows how a policy limit allocates by geographic area. | Not implemented | e.g. a $1M policy over $50M of buildings across several states. |

**Delivery**

| Requirement | Implementation | Notes |
|---|---|---|
| Results can be copied / pasted out. | Not implemented |  |
| ELTs are uploaded to the Loss Repository for downstream reporting. | Not implemented | Losses, financial perspective, and metadata. Open question: how to move data from DataBridge to the Loss Repository. |

**Out of scope for MVP:** **Post-Analysis Treaty (PATE)** — adding a cat treaty onto broker results after the fact and re-simulating; a rare fringe case, portfolio-level only, deferred (design note 05 §6, O5-4); formal loss validation against broker/cedant (confirm the informal multi-analysis view is enough); visual compare in RiskLink / copy to Excel; pushing broker results to the Loss Repository; loading exposure summaries to the Exposure Repository; carrying CRM ID tags through to the repository upload (Future); uploading loss sets to Analyze Re (separate API).
