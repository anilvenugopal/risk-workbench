# Risk Workbench — Functional Requirements

**Status:** Draft for design discussion · Living document, edited line-by-line in design sessions.

## How to use this document

Plain statements of what the workbench is and does, organized by workflow area. **Each row is exactly one requirement — one fact or one capability.** Requirements are deliberately not combined; if a statement has two independent parts, it is two rows. `Requirement` = a single literal statement · `Implementation` = current delivery state · `Notes` = brief clarification, examples, or the field list a requirement enumerates.

**Implementation status:** `Implemented` = the current application satisfies the requirement · `Partial` = it satisfies only part of the requirement · `Not implemented` = it does not currently satisfy the requirement. For negative requirements and explicit exclusions, `Implemented` means the application honors the stated constraint.


**Scope:** MVP picks up at import into Risk Modeler / DataBridge onward — files arrive already unzipped, attached, and named by the existing SQL workflow. We are designing toward the future state where the workbench also absorbs front-end data setup (zip import, naming inferred from the directory). In/out markers on §3–§8 come from the June 2026 `CReWorkflow_Expanded` review, the July 2026 design sessions (7/7, 7/9, 7/14, 7/16), and the 8/4 and 8/5 2026 sessions.

**Basis:** This revision folds in the July 2026 design sessions, the **August 4, 2026 V1 demo review** — the first functional session of the delivery phase, and a largely *subtractive* one: it overturned several §2.2 rows outright (TIV, the EDM-level aggregate block, portfolio-attributed analyses, the full treaty grid) — and the **August 5, 2026 session**, which closed the treaty summary field set, replaced the single-step RDM import with an orchestrated DataBridge workflow, and added §8 Notifications. Rows a session changed are marked **Reversed** / **Removed** / **Added** / **Changed** / **Narrowed** with its date. Source detail lives in `design_session_notes/03`–`09`. Open questions raised there appear inline as callouts and must be resolved before the PRD locks the affected area.

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

The **Submission** is the deal and the only user-facing container for EDMs and
RDMs. EDMs and RDMs are global Risk Modeler resources that can each relate to
several Submissions without being copied or re-imported.

The August 12 review retained Submission as the top-level deal and removed the
intermediate grouping concept. Design note 12 records the decision.

**Submission**

| Requirement | Implementation | Notes |
|---|---|---|
| The Submission is the top-level object; it represents the deal. | Implemented |  |
| There is no customer or program level above the Submission. | Implemented |  |
| A Submission is one cedant's treaty of a given type at a given inception. | Implemented | e.g. "6/1/2026 CAT XOL for Cedant A." |
| A Submission has a name of its own, distinct from the cedant name. | Implemented | **Added 8/4.** The name on the contract is often not the name of the submission — an MGA writing on behalf of an insurer puts both names in the submission name but only one on the contract: "I think it's right to keep them separate." Today the name is a copy/paste from CRM. |
| A Submission is displayed by its name plus cedant name + treaty type + inception date. | Implemented | Readable identifiers analysts recognize at a glance. |
| A Submission has these attributes: name, cedant name, treaty type, inception date, treaty year, directory path, CRM ID(s). | Implemented | Directory path = the deal's shared-drive folder. |
| A Submission has exactly one treaty type. | Implemented | **Added 8/4.** Multi-select was floated and rejected: one submission carries one treaty type. The same EDM or RDM can relate to submissions with different treaty types. |
| Treaty year defaults to the inception year and stays editable. | Implemented | **Changed 8/5.** The 8/4 wording was "derived from the inception date… never free-text entry." Picking an inception date fills the treaty year, and the server fills it on save when the field is left blank, so nobody types it in the normal case. Reverses design note 08 §2.1 D4, which carries the reason and the open re-confirmation with CIC. |
| The cedant field is workbench-side only; it is not linked to the Moody's/EDM cedant field. | Implemented | **Added 8/4.** There is no exposure data at submission-creation time to link against — cedant arrives *with* the exposure. "Totally fine, I just wanted to make sure." The label needs disambiguating: "cedent" is also a specific EDM field. |
| The cedant field is a typeahead over existing cedants. | Implemented | A keyboard-navigable menu (arrows, Enter, Escape) replacing the `<datalist>` the 8/4 session rejected as "not what you think in terms of an auto populate drop down type of deal." Matches anywhere in the name, so "fam" finds "American Family Mutual"; free text is still accepted, since there is no cedant registry. Both this menu and the "links to" typeahead wait for the second character (`submission_service.MIN_SUGGEST_TERM`). |
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
| A Submission has a status: Active, Hold, Completed, or Cancelled. | Partial | **Changed 8/5.** `submission_status_kind` seeds three codes; **Hold** is new. Hold is "not cancelled, not complete, but… we're not working on it really actively right now" — e.g. a week waiting on data from a broker. It means no updates needed while the deal stays in the queue, and it takes the submission out of the daily digest (§8). Preferred over a per-submission "silence notifications" toggle. |
| Submission status can be reopened from Completed or Cancelled back to Active. | Implemented | Transitions are reversible; not a one-way door. |
| Every status transition records a reason. | Implemented | Retained as a history trail. |
| The status history trail is collapsible. | Partial | **Added 8/4.** Repeated transitions otherwise produce "a huge block here in the submission page." |
| A Submission is never deleted. | Implemented | Cancelled is the "not happening" outcome in place of a delete. |
| A Submission can be copied into a pre-populated create form. | Not implemented | **Added 8/4 (Cheryl).** "A lot of the data that we're going to have on that submission page is going to be duplicative… it'd be nice to be able to just copy it, make the edits that I need, update the CRM ID." The result is a **new** Submission, not an edit of the original. |
| A Submission can link to a related Submission. | Implemented | UI label is **"Links to"** (8/4, superseding "Previous" from 7/14) — the relationship is a link to a related submission, not necessarily a renewal; the team also jumps back to compare. The analyst picks the related deal by name from a typeahead over name and cedant; the column is `submission.links_to_submission_id`. Link may come from the treaty system later. |
| The Submission's directory is chosen with a folder browser and validated, not typed. | Not implemented | **Added 8/4.** "Rather than just have a random free text field like I could put anything in here." The Submission is hard-linked to the folder. |
| The directory-path link opens a file browser at the deal's folder. | Partial | Useful jump-off, but the working/BAK file is often not in that folder — see §2.1. |

> **Parked — CRM integration.** A future phase, not MVP. CRM holds the submission name, company/cedant name, inception date, and CRM ID: "especially based on that CRM ID, we could pull a lot of data in." Today the submission name is copy/pasted from CRM by hand. (8/4)

**Submission data relationships**

| Requirement | Implementation | Notes |
|---|---|---|
| A Submission can relate directly to any number of EDMs and RDMs. | Not implemented | The submission page shows separate EDM and RDM tables. |
| An EDM can relate to several Submissions. | Not implemented | Every association points to the same workbench and Risk Modeler EDM. |
| An RDM can relate to several Submissions. | Not implemented | Every association points to the same workbench RDM and its captured broker analyses. |
| Relating an existing EDM or RDM to another Submission does not copy or re-import it. | Not implemented | Only the association is inserted. |
| Removing an EDM or RDM from a Submission deletes only the association. | Not implemented | Physical deletion from Risk Modeler is outside the MVP. |
| Completed and Cancelled Submissions reject EDM/RDM association changes. | Not implemented | Reopen the Submission to Active before adding or removing data. |
| A Submission does not assert an EDM-to-RDM relationship. | Not implemented | Naming conventions identify the intended business relationship. |
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
| A Risk Modeler name collision blocks import until the user renames. | Implemented | Checked against Risk Modeler by API before import. |
| Naming convention: `TY{YY}{MM}_{Cedant}_{InforceDate}_{Version}_{EDM\|RDM}`. | Implemented | Add a distinguisher (business unit, region, treaty type) when databases would otherwise collide. |
| The name is auto-populated from the chosen import file name. | Implemented |  |
| The name is editable in place before import. | Implemented | On collision, the user adds a suffix. |
| An EDM/RDM has a status: Pending, Uploading, Ready, Upload Failed. | Implemented |  |
| An EDM/RDM has one optional plain-text note of at most 250 characters. | Implemented | The note is shared wherever the EDM/RDM appears. |
| EDM/RDM notes can be edited in place in the Submission EDM and RDM tables. | Implemented | Double-click the note or select Edit; Enter saves, Shift+Enter inserts a line break, and Escape cancels. |
| Rename-on-reattach is supported (~15% of the time). | Not implemented | e.g. mid-deal M&A — reattach/rename exposure for the acquiring company; also keeping test versions (v1 vs v2) separate. |
| On duplicate-and-rename, the workbench offers to auto-delete the old copy. | Not implemented |  |

**Finding work**

| Requirement | Implementation | Notes |
|---|---|---|
| The homepage opens to a Submissions list. | Partial | Sorted by owner and last-updated; surfaces the logged-in user's own work first (7/14). 8/4 reopened **what else belongs on the landing page** — it is empty today and the call is unmade (design note 08, O8-4). |
| Submissions can be searched by name. | Implemented | **Added 8/4** — was missing entirely: "that is a need to add." |
| Submissions can be filtered by cedant name, treaty type, inception date, owner, and CRM ID. | Implemented | The list carries eight filters: name search, cedant, CRM ID, owner, status, treaty type, inception, treaty year. The cedant filter matches a fragment of the name rather than the whole value. **Added 8/9 (D16):** status, treaty type and owner each take several values, picked as chips from a menu that stays open, and treaty year takes several years typed one at a time. Values OR within a filter and AND across filters; twenty values per filter is the cap. Owner still defaults to the signed-in analyst and its menu narrows in place as the analyst types. |
| Submissions can be sorted. | Implemented | **Added 8/9 (D15):** Name, Cedant, Inception and Year are click-to-sort headers carrying a caret; clicking the sorted column flips it. One column at a time. The default is inception date descending. Applied filters and the page survive a sort, and the order rides in the URL. |
| Multi-term search uses AND semantics, not OR. | Implemented | **Added 8/4.** "There must be 1000 companies that have American in the name" — typing "American Family" must not return every company containing "American." Applies to the name search, the cedant filter, and the "links to" picker: each typed word must appear, matched as a substring and no fuzzier than that. |
| Global search runs across Submissions, EDMs, RDMs, analyses, jobs, and results. | Not implemented |  |
| Search, sort, and filter are available on every list section. | Not implemented | 7/14: wanted on portfolios, treaties, analyses, and results — not just the submissions list. |

**Navigation & drill-down**

| Requirement | Implementation | Notes |
|---|---|---|
| Navigation from a Submission goes directly to its EDMs and RDMs. | Not implemented | The actual work happens at the EDM level (7/14). |
| An EDM drills down to its portfolios. | Implemented | Analyses run against a portfolio, not a whole EDM. |
| The effective contextual navigation depth is Submission → EDM → Portfolio. | Not implemented | The portfolio drill-down is also the entry point for kicking off analyses. |
| An EDM opened from a Submission links back to that Submission and offers the Submission's other EDMs. | Not implemented | The contextual URL names the Submission; the direct EDM Library URL has no Submission context. |

---

## 2. Data Setup

Getting a deal's data into the workbench and seeing what's in it.

### 2.1 EDM / RDM Import

| Requirement | Implementation | Notes |
|---|---|---|
| The user can create a Submission. | Implemented | A Submission can exist without EDMs or RDMs. |
| An Active Submission can import a new EDM or RDM. | Not implemented | The new entity and its Submission association are saved before background import begins. |
| An Active Submission can add an existing EDM or RDM. | Not implemented | Every live resource not already related to the Submission is eligible. The empty state distinguishes no available resources before a search from no search matches. |
| Adding an existing EDM or RDM creates no Risk Modeler job. | Not implemented | The action inserts only `submission_edm` or `submission_rdm`. |
| A Submission shows separate, always-visible EDM and RDM tables. | Implemented | Each table has its own empty state and add action. Name, Status, and count are sortable independently; the selected orders remain during polling. These short tables do not have search. Risk Modeler links appear only for ready EDMs/RDMs. A table refreshes while a listed import or subsequent detail backfill is pending or running. |
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
| Imports run in the background. | Implemented | The analyst can leave the page and keep working — avoids Risk Modeler's slow, blocking upload. |
| All selected files can be submitted for import at once. | Partial | The system runs ~10 concurrently and auto-dequeues the rest as slots free up; chunking gives no benefit (7/14). |
| A delete-after-transfer checkbox removes the temporary BAK file once imported. | Not implemented | Otherwise temp files accumulate on the share forever. |
| Import status is tracked per EDM/RDM. | Implemented |  |
| An EDM already in Risk Modeler can be linked to a Submission without re-importing. | Partial | **Moody's IRP › Sync from Risk Modeler** first creates the workbench EDM row; the Submission add-existing action then inserts only the association. |
| All EDMs are browsable in an EDM Library. | Implemented | Includes workbench-created and orphaned. |
| All RDMs are browsable in an RDM Library. | Implemented | Includes workbench-created and orphaned. |

**RDM import route** (8/5)

Risk Modeler's two upload routes are not equivalent, and the difference was demoed live on the same RDM. Uploading **through Risk Modeler** creates analysis entities only, stores no database on DataBridge, is not SQL-queryable, and **omits** exposure name, currency scheme/vintage, event rate scheme/name, minimum loss threshold, franchise deductible, construction, and LOB term. Uploading **through DataBridge** stores a queryable database that retains them, but cannot be linked to an EDM and does not show results in the Risk Modeler Results tab. Importing DataBridge→Risk Modeler afterwards does **not** restore the attributes — so no single Risk Modeler route yields them.

| Requirement | Implementation | Notes |
|---|---|---|
| An RDM is imported to DataBridge first, not directly to Risk Modeler. | Not implemented | **Reversed 8/5.** Today `submit_rdm_import` is a single Risk Modeler apply that targets an EDM — the route that drops the attributes above. |
| The imported RDM is queried by SQL for its full analysis detail. | Not implemented | **Added 8/5.** The step that recovers what Risk Modeler will not return. |
| The captured analysis attributes are saved to the workbench database. | Not implemented | **Added 8/5.** |
| The RDM is imported from DataBridge into Risk Modeler after the SQL capture. | Not implemented | **Added 8/5.** So results appear in the Results tab. In the RM UI the path is Results → the caret beside "Results" → Import from DataBridge. |
| The RDM is deleted from DataBridge once it is in Risk Modeler. | Not implemented | **Added 8/5.** RDMs run larger than EDMs and keeping both copies wastes space: "you can always export everything back out to an RDM, so… I don't think we need to keep it in two places." Losing the DataBridge EDM↔RDM association costs nothing — that link is already untrustworthy (§2.2 trust rule). |
| The four import steps run behind one user action. | Not implemented | **Added 8/5.** The analyst imports an RDM from a Submission or the RDM Library; the orchestration is not exposed. |
| The workbench database is the source of truth for analysis attributes Risk Modeler does not retain. | Not implemented | **Added 8/5.** Exposure name, currency scheme/vintage, event rate scheme/name, minimum loss threshold, franchise deductible, construction, LOB term. The event rate scheme matters most — "that's just key information we're going to want to know." |
| Attributes read from DataBridge SQL are shown even though SQL fields are editable by anyone. | Not implemented | **Added 8/5.** Acknowledged and accepted: the capture happens **before** the data reaches Risk Modeler, and "if it's there, we want to use it, want to return it to the user." |

> **Open — which attributes actually survive the DataBridge step.** Event rate scheme is validated. Minimum loss threshold, franchise deductible, construction, LOB term, currency scheme/vintage, and exposure name are assumed and unconfirmed. Ben to validate. (Design note 09, O9-3.)

> **Open — RDM replacement lifecycle.** A replacement RDM arriving weeks later
> ("you need to get rid of the old stuff and put the new one in"), data arriving
> in stages, and the variant where CIC reads an RDM without ever importing it
> into Risk Modeler have no design yet. (Design note 09, O9-5.)

> **Open — ask Moody's what the intended workflow is.** Why the two upload routes differ, and why the certified UI will not populate attributes that exist in the underlying data. This broker workflow — receive results, use some, run others, combine at the end — is "a very common workflow," and CIC wants to confirm it is not missing an intended path. (Design note 09, O9-2.)

> **Open question — BAK vs MDF vs zip import path:** whether import points at BAK files in a shared BAK directory, or selects MDF/zip files from a network drive (varying by cedant/submission/treaty year), is unresolved. (Design note 04 §8.2, O4-2.)

**CSV import — deprioritized outlier for MVP.** CSV-for-EDM is MRI (accounts + locations files together), rare for their data. CSV-for-results is HD/PLT output, not natively supported for RDM import in Risk Modeler. Open question: what can Risk Modeler actually *do* with a CSV result once imported — if it's view-only, there's no point (design note 03 §8, OQ-6). Push cedants/brokers to provide RDMs instead.

### 2.2 Exposure Details Viewing

Rolled up so the analyst doesn't click through Risk Modeler — a fast textual snapshot. **The 8/4 review was subtractive here:** three built columns — the per-portfolio analyses count, TIV, and the EDM-level aggregate block — were removed as untrustworthy or misleading, and the space repurposed for information CIC actually uses.

**Scope of the roll-up**

| Requirement | Implementation | Notes |
|---|---|---|
| A map is not needed. | Implemented | A fast textual snapshot is what's wanted. |
| The existing Power BI exposure dashboards are not rebuilt. | Implemented |  |
| No EDM-level aggregate roll-up is shown. | Implemented | **Reversed 8/4.** The aggregate counts double-count: "you can have the same locations duplicated for wind and for severe convective storm… saying that I've got double the amount of locations isn't really double the amount of locations… I don't think that that summary gives me much." `partials/edm_aggregate_strip.html` and `portfolio_service.aggregate_exposure` are removed. Voids spec 004 US4 / FR-040 / FR-041 / FR-042 / FR-043. |
| Figures are shown per portfolio, inside a specific EDM. | Implemented | Analyses run on a portfolio, not the EDM (7/14). The per-portfolio table is the valuable part — "having just the list of portfolios and… some information about them is really great in one place because you can't do that today in RiskLink." |

> **Trust rule (8/4) — metadata originating outside CIC's environment cannot be trusted.** There is no reliable way to tie an RDM analysis to a specific EDM portfolio: "there actually is no way to tie an RDM analysis to a specific EDM portfolio that you can trust." A false link was demoed live — a US EQ analysis attributed to a USFL portfolio ("clearly they're wrong… it's not possible"). Attribution is defensible only when the EDMs/RDMs never left CIC's environment. Analyses from a linked RDM may be **listed** on the EDM page, never **attributed to a portfolio**. This narrows spec 004 FR-036 / PRD §21 Iteration 3 — see §7.

**EDM header**

| Requirement | Implementation | Notes |
|---|---|---|
| The EDM header shows name, status, source path on the share, Risk Modeler ID, and job ID. | Implemented |  |
| The number of portfolios in the EDM is shown. | Implemented | Sometimes 1 portfolio, sometimes 25. |
| An EDM opened from a Submission shows that Submission as its only context link. | Not implemented | See §1 Navigation & drill-down. Other Submission associations do not appear in the navigation trail. |
| The EDM header shows the as-of / last-synced timestamp. | Implemented |  |
| A manual Sync action re-reads the EDM from Risk Modeler. | Implemented |  |
| An EDM and RDM each have one optional shared note, editable from the detail page by any authenticated analyst. | Implemented | The note is limited to 250 characters. Blank input clears it. Submission tables show the complete wrapped note; library tables do not show notes. A stale editor identifies the newer note and requires a second save to replace it. Detail polling pauses while the editor is open. |

**Per-portfolio figures**

| Requirement | Implementation | Notes |
|---|---|---|
| Location, account, and policy counts are shown per portfolio. | Implemented |  |
| Perils covered are shown. | Implemented | Sub-perils are an analysis-settings attribute (§7), not an exposure attribute (2026-07-28). |
| Lines of business are shown per portfolio. | Implemented | Read from LOBDET joined through the policy table, so only values **actually present in the data** are returned — a cedant's LOB table may carry 30–40 values where the delivered data uses two (8/4, confirmed: "that's what we want"). |
| Countries are shown, ahead of states. | Implemented | **Added 8/4.** "I actually want to see countries, not states" — the immediate question is "this is labelled US EQ; is it actually US only?" `portfolio_countries.sql` reads `Address.CountryCode` (fallback `CountryRMSCode`); the EDM Address table has no country-*name* column, so the cell shows codes (US, CA). |
| States / state-equivalents are shown as a separate column. | Implemented | **Added 8/4** — geography split into two columns, country first. |
| Geography collapses to a single value when there is one, and to a count or "multi-country" when there are many. | Implemented | The single value when there is one; otherwise "multi-country (N)" for countries and "N states" for states. Named regions are **not** the collapse value: regions are treaty-dependent, not fixed constants (§3); named regionality stays a treaty-scoped concept in the breakout flow. Confirmed 8/7 (D3): Wendy read the collapse rule off the live column — "if it says New Zealand, that means only New Zealand unless it says multi" — and accepted it as built. |
| The list of currencies present is shown. | Implemented | Even "multiple currencies" is useful — "like that's a win." |
| Currency is never converted or aggregated. | Implemented | **Reversed 8/4.** "As soon as you put currency in, then people are going to say, how did you get that? What was the rate that you used?" Nothing converts, and the cross-portfolio currency union went with the aggregate block above — currencies now appear only per portfolio. Currency defaulting/handling for *analysis* is specified in §4. |
| Currencies are gathered from every place currency lives in the EDM. | Implemented | **Reworked 8/7 (D5).** "You can't just pull it from one spot" — the previous read of `loccvg.VALUECUR` alone under-reported. `portfolio_currencies.sql` now scans 39 currency columns across the 10 tables reached from a portfolio's accounts and locations (policy, polcvg, loccvg, hdsteppolicy, and the six per-peril detail tables), folds case, and returns the unique set. Cession currency (`reinsinf`) and treaty currency (`trtydesc`, shown on the treaty table) are out — the column reports what the exposure is valued in. `agport.EXPOSCUR` has no join to `portinfo`. Source: CIC's currency-check queries (Wendy, 8/9), closing design note 10 O8-2. |
| Total insured value is not shown. | Implemented | **Removed 8/4.** Currency conversion makes the figure indefensible — "too many questions that come into how'd you get that." The TIV column and `EdmAggregate.total_tiv` are gone; `portfolio_total_tiv.sql` was replaced by `portfolio_list.sql`, a plain `portinfo` enumeration that still seeds every portfolio (including account-less ones) into the DataBridge summary. |
| Analyses are not counted or attributed per portfolio. | Implemented | **Removed 8/4.** See the trust rule above. The Analyses column, the per-portfolio inline analyses panel, and the `exposureResourceId` linkage note are gone. The worker still captures `exposure_resource_id`; nothing reads it. |
| Record volume is shown. | Implemented | So the analyst doesn't accidentally run a ~1M-record portfolio thinking it's ~20K, and can schedule large runs (e.g. start a 4M-record run overnight). |
| Reinsurance/treaties associated with the EDM are shown. | Implemented | LOB and cedant come from the EDM details. |
| Truncated value lists expand in place. | Implemented | **Added 8/4.** Expanding a portfolio row reveals the full lines-of-business, countries, states, and currencies lists (the expander freed by removing the per-portfolio analyses panel). Each list is capped at 100 values with a "+N more not shown" tail. |

**Free-text field caps** (8/4)

| Requirement | Implementation | Notes |
|---|---|---|
| A free-text descriptor field with more than ~500 distinct values is not saved into the roll-up. | Implemented | Line of business is the known case: a completely user-defined descriptor that does not affect analysis, which cedants populate with "10s of thousands of different and unique values" — account numbers, underwriter names. "If it's over 500 values, we're not going to save it out." The gateway drops a lines-of-business list over 500 distinct values before the summary is stored; the cell renders "—". |
| Front-end expansion of a value list is capped around 100. | Implemented | The portfolio-row expander shows the first 100 values per list and states how many are not shown. |
| No elegant handling is required for the pathological case. | Implemented | Explicit guidance: "It doesn't need some elegant options that we go through… I don't want you to overthink that scenario." |

> **Open — which other fields share this pathology?** "There's other fields like that in this EDM as well." Ben to compile the list with Wendy and Cheryl. Also pending: performance-test the LOB JSON storage at ~10,000 key-value pairs. (Design note 08, O8-2.)

**Freshness** (8/4)

| Requirement | Implementation | Notes |
|---|---|---|
| Roll-up figures are read from the workbench database, not fetched live from Moody's per page load. | Implemented | Live API calls per page load are an unacceptable performance overhead. |
| The accepted cost of backfill is drift when someone edits directly in Risk Modeler. | Implemented | Managed by the last-synced timestamp, the manual Sync action, and freshness validation below. |
| Freshness is validated before any action that depends on current exposure. | Not implemented | Sub-portfolio creation is the first such action (§3). |
| A sync can be scoped to the EDM's treaties alone. | Not implemented | **Added 8/5.** `sync_detail` re-runs the whole `backfill_edm_detail`. Editing one treaty in Risk Modeler and then syncing a large EDM to pick it up is "a drag"; treaties carry little data, so a treaty-scoped sync is fast, and treaties affect neither portfolio nor RDM data. Ben on feasibility: "under the hood… each of these is addressed individually, so that could work." |
| A sync can be scoped to the EDM's portfolios alone. | Not implemented | **Added 8/5.** Same reasoning in the other direction — portfolio data is the large, slow part. |
| A scoped sync does not advance the EDM's last-synced timestamp. | Not implemented | **Added 8/5.** "I wouldn't want to update the last-sync timestamp for an EDM if we just sync the treaties and not the portfolios." The freshness indicator must reflect what was actually refreshed. |

> **Open — scoped sync validation, and what the freshness gate reads.** Ben to confirm the treaty-only and portfolio-only syncs are feasible and side-effect-free. If treaties and portfolios refresh independently, the pre-action gate above must read the freshness of the data the action depends on rather than one EDM-wide stamp. (Design note 09, O9-1.)

**Treaty viewing** (7/14, revised 8/4, field set closed 8/5)

| Requirement | Implementation | Notes |
|---|---|---|
| The treaty list is condensed by default. | Implemented | **Reversed 8/4.** The full attribute grid is "a lot to look at… just a lot of information with that scrolling." |
| The treaty section represents only the treaties contained in the EDM. | Implemented | **Added 8/5.** Treaties in an EDM and treaties listed in an RDM are different sets — "EDMs and RDMs are not guaranteed to be connected." Treaties attached to an analysis are seen by opening that analysis, where they were attached at the time it ran. |
| The condensed view shows this field set. | Implemented | **Changed 8/5**, closing the "which ~3 fields" question — the columns are now name/number (labeled), type, attachment point, attachment basis, occurrence limit, risk limit, cedent, lines of business, currency, effective dates. Exposure level did not fit (O9-4, next rows); attachment basis was added back 8/7 (D9). |
| Risk limit and occurrence limit are both shown, each labeled. | Implemented | **Added 8/5.** One limit alone is not interpretable: "I would definitely put the risk limit and the occurrence limit — tick both in there." Two labeled columns replace the coalesced "Limit". |
| Treaty name and treaty number are both shown, even when identical. | Implemented | **Confirmed 8/5.** They typically hold the same value, and in the demo neither was labeled well enough to tell which was which. "I wouldn't take one away — that's the only thing." The number now carries a "No." label under the name. |
| No percentage-share field is shown in the condensed view. | Implemented | **Removed 8/5.** One share percentage misleads — "you really need to see all the percentages to interpret what it's doing, not just one… showing one doesn't give you enough information." Four columns is too many for a condensed view, so show none and let the analyst open Risk Modeler. All percentages remain on expand. |
| Cedent and line of business show the first available value(s), not the full list. | Implemented | **Added 8/5.** Both can be long and multi-valued; the condensed view reports presence, not granularity — the first value plus a "+N" count. The full list is on expand. |
| Attachment basis is a condensed column of its own. | Implemented | **Added 8/7 (D9)**, reversing the 8/5 O9-4 call that dropped it for width. Basis is risks-attaching vs. losses-occurring — distinct from attachment *point*, which sits beside it: "can add attachment basis to the… table headers." The column costs 130px, paid for by trimming effective dates and the type column's floor; the treaty table goes 1270px → 1370px. |
| Exposure level is shown if it fits. | Implemented | **Added 8/5**, resolved via O9-4: it does not fit — Wendy ranked it last ("if it fits, that's great, I just think we're going to run out of real estate"). "Applies at" (attachment level) stays in the expanded grid, spelled out. |
| Full treaty attribute detail is available on expand. | Implemented | Preserves the 7/14 intent — all attributes reachable to catch mis-coding rather than blindly trusting it ("sometimes people put the wrong thing in the wrong field") — via expand rather than default density. |
| Treaties expand and collapse. | Implemented | Few treaties shown expanded; many collapse to focus one at a time. |
| Treaty attribute labels are spelled out, not Risk Modeler's cryptic codes. | Implemented | The documented enum codes spell out everywhere they render: attachment level ACCT/LOC/POL/PORT → Account/Location/Policy/Portfolio, attachment basis L/R → Losses occurring/Risks attaching, treaty type CATA→Catastrophe etc. (create-treaty reference). "Lobs" renders as Lines of Business. |
| Timestamps are shown at the granularity Risk Modeler actually uses. | Implemented | "These timestamps are ugly" — ISO date-time strings date-truncate in the expanded grid as well as the summary columns. The Excel export stays verbatim. |
| Duplicated-looking treaty attributes are reconciled or removed. | Implemented | RM's rows carry both spellings of the identity fields (`id`/`treatyId`, `name`/`treatyName`, `number`/`treatyNumber`). When the pair agrees the grid shows only the `treaty*` one; a diverging pair stays visible — the grid exists for mis-coding checks. |
| Treaties can be exported to Excel. | Implemented | For extreme cases with too much to render cleanly. |
| Treaty setup is shown at the EDM level. | Implemented | Treaties can be applied at portfolio, account, or policy level; results are spit out to the RDM. |
| For a per-risk treaty, the losses subject to the treaty are the interest — not the portfolio itself. | Not implemented | No loss numbers this iteration. |
| Inuring reinsurance is identified and removed to get the net perspective. | Not implemented | The models do this. No loss numbers this iteration. |

> **The condensed view is governed by space, and the field set is ranked.** Cheryl: "the thing is not to replicate this table in there because you've got the button to take us here… whatever fits on the screen, and if you have to scroll, then just go into Risk Modeler and look at it." **Risk limit, occurrence limit, attachment point, and treaty type must be there**; Wendy called everything past them "gravy," agreed as the target but first to yield. Removing the share columns and the over-granular timestamps is what pays for cedent, line of business, attachment basis, and exposure level. Whether the last two fit is answerable only after that cleanup (design note 09, O9-4).

*(Treaty creation/editing is a pass-through to Risk Modeler — §5.)*

### 2.3 Loss Results Viewing

Reviewing broker-provided results and settings. Full results review and delivery in §7.

| Requirement | Implementation | Notes |
|---|---|---|
| Broker-provided (RDM) analyses can be reviewed before running own analyses. | Implemented | The review establishes what was already run so the analyst can decide what still needs running. |
| Each broker result shows its analysis settings/metadata. | Implemented | Full metadata list in §7. e.g. rate, perils/sub-perils, loss amplification on/off, detail level saved. |
| Each analysis shows its loss values, not metadata alone. | Not implemented | **Added 8/5.** The page carries portfolio, model version, type, analysis mode, primary and secondary peril, region setting, and currency — no losses. Which attributes and which result data matter was agreed separately by Ben and Cheryl. |
| Each analysis has a pass-through link to Risk Modeler. | Not implemented | **Added 8/5.** Mirrors the treaty pattern for deeper investigation — "I like having the pass-through, that's nice." |
| Each analysis has an on-screen summary alongside the pass-through link. | Partial | **Added 8/5.** Metadata renders today; the summary is not designed against the loss values above. |
| The portfolio an imported RDM's analysis ran against is not shown at all. | Implemented | **Narrowed 8/4.** That attribution is untrustworthy for anything that left CIC's environment — see the §2.2 trust rule and §7. The Portfolio column is removed from the RDM page and the EDM page rather than shown as plain text: with nothing reading the resolution, "— not linked" only explained an absence the analyst never needed to see. |
| The results view helps the analyst decide how much work a given RDM even needs. | Implemented | Sometimes the broker already provides what's needed and the analyst only pushes losses to the repository — no remodeling. |

**RDMs on the EDM detail page** (8/5)

| Requirement | Implementation | Notes |
|---|---|---|
| An EDM opened from a Submission lists every RDM related to the same Submission. | Not implemented | An RDM with no captured analyses still appears with an empty group. The direct EDM Library page does not infer a Submission or show Submission-scoped RDMs. |
| Each RDM section on the contextual EDM page loads its stored analyses when expanded. | Not implemented | The initial response renders the RDM identity and count only; no Risk Modeler read runs in the web process. |
| Displaying an RDM beside an EDM does not assert a link between them. | Not implemented | Membership in the same Submission is the only relationship: "EDM 1 isn't related to EDM 2 other than they're related to the same submission." Consistent with the §2.2 trust rule. |

> **Why every RDM on every EDM.** Wendy's case is a paired book — an in-force and a projected EDM with an in-force and a projected RDM: "I like to see the landscape to say, were the same analyses run, because… if you have 12 analyses in one, you have 12 analyses in the other." No two-EDM/two-RDM example exists in the app yet, so the layout is unreviewed.

**Out of scope for MVP:** policy-level detail; PDFs and other non-modeling exhibits (File Explorer suffices).

---

## 3. Data Modification

Re-shaping exposure to match treaty terms before analysis. This cannot be done in the current workflow tool (done in RiskLink today, which is slow); Risk Modeler makes it fast and synchronous, so it becomes a *preferred* path.

> **Build record (2026-08-05; rev. 2026-08-12, spec 005 P-19 rev.).** One-click breakouts shipped in Iteration 4 with four quick dimensions — line of business, state, country, and peril — plus custom groups (analyst-defined filter sets): see `specs/005-subportfolio-breakouts/spec.md` and PRD §10A.5/§21. The 2026-07-29 product direction resolved the commercial-geo open question below (Risk Modeler assigns whole accounts; accepted and disclosed in the preview). The filtered sub-portfolio builder and the complement split are fast-follows.

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
| One-click breakout by line of business creates one sub-portfolio per LOB. | Implemented | Simplest case; unaffected by the commercial-geo problem below. |
| One-click breakout by state/country creates one sub-portfolio per geography. | Implemented | State/state-equivalent and country grain (spec 005). |
| One-click breakout by peril creates one sub-portfolio per peril. | Implemented | P-19 rev. 2026-08-12 promoted peril to a quick dimension. |
| One-click complement split ("X vs. not-X") creates one portfolio for selected states and one for everything else. | Not implemented | e.g. Northeast and everything-not-Northeast, from a single action. |
| Breakouts sum to 100% of the source portfolio. | Not implemented | Not "run the whole thing, then a subset, and subtract" — that's messy. |
| A "do the opposite" option produces the complement of a defined filter without re-coding it. | Not implemented | Define "Florida mobile home" once and also get "everything that's not Florida mobile home." |
| Portfolio-creation granularity is capped at state/country. | Not implemented | Finer than that (CRESTA, ZIP) is saved as output, not a portfolio — "too much to manage." |
| Regions are not pre-defined constants. | Not implemented | "Northeast" is defined by the treaty / how the cedent writes the business; contracts define coverage arbitrarily. |

> **Open question — commercial-policy geographic split (blocking for the geography breakouts).** Splitting a multi-location commercial policy geographically breaks its financial structure and can double-count in a complement split (keep-all-locations behavior). Whether Risk Modeler keeps all locations or only matching ones — and whether it exposes a toggle — is unconfirmed; a RiskLink "checkbox" recollection is not load-bearing. Output-side alternative: write losses to the state level and let the model allocate back. Ben investigating RM behavior; Cheryl polling the team for the preferred default. (Design note 06 §3, O6-1/O6-2.)

Peril splitting ships as a quick breakout dimension (spec 005 P-19 rev., 2026-08-12) — verify separately whether RM adds a missing peril.

**Out of scope for MVP:** update/change data elements; data validation reports; exposure profiling / loading summaries to the Exposure Repository; **merge/combine portfolios** (recombination happens on results, not exposure — design note 06 §4).

---

## 4. Analysis Configuration

Setting up analyses. Configuring a worldwide contract by hand is the #1 analyst pain point (50–150+ combinations).

**Profiles & settings**

| Requirement | Implementation | Notes |
|---|---|---|
| Standard model (DLM) profiles are selected from a pre-compiled list. | Implemented | Profiles are created and managed in Risk Modeler; the workbench selects, it does not own profile management. Delivered as the template builder over the synced metadata cache (Iteration 6, spec 009). |
| Multiple model profiles can be selected for one portfolio/treaty combination. | Not implemented | One profile per template; choosing several templates (or a suite) in one execution covers this (Iteration 7). |
| Output profiles are selected from a pre-compiled list. | Implemented | Also created and managed in Risk Modeler. |
| User-defined (UD) profiles are supported and selectable. | Implemented | Naming convention `UD` + initials, e.g. UDCT. |
| Profile lists can be filtered. | Implemented | Substring filtering over the local cache (spec 009 FR-006) — "just get to UDCT." |
| An event-rate scheme is configurable per analysis. | Implemented | Per template since Iteration 6. People are "very picky" about it. |
| Franchise deductible is an exposed per-analysis toggle. | Implemented | A template-builder setting. Deal-specific; the team wants direct access — the exception to holding advanced settings constant. |
| Unrecognized construction / occupancy type is an exposed per-analysis toggle. | Implemented | Template setting: "Skip location during analysis" or "Treat as unknown". |
| Min loss threshold and max loss event are surfaced in the template builder, pre-filled with defaults. | Implemented | **Changed 8/18 (spec 009 FR-005).** Was "stay at defaults, not surfaced." |
| Tags can be set per analysis. | Implemented | Per template, stored as names; Risk Modeler resolves and creates tags at submit time. |
| Loss/analysis currency is confirmed at submit time, per suite. | Not implemented | **Changed 8/20 (note 17 D4/D5; reverses spec 009 P-10).** Templates store no currency. The execution modal confirms currency + currency scheme + scheme vintage per chosen suite (once for a template run); mixed-currency books run as separate regional suites (spec 010 P-15, Iteration 7). |
| Currency defaults pre-fill the submit-time pickers and never advance on their own. | Not implemented | **Added 8/20 (note 17 D6/D7).** Pinned env vars (`DEFAULT_ANALYSIS_CURRENCY_CODE`/`_SCHEME`/`_VINTAGE`) edited by ops; no admin UI/RBAC in MVP; CIC — not the system — flips the default when currencies update. The submit block stays `{code, scheme, vintage, asOfDate}`, `asOfDate` from the chosen vintage's effective date (spec 010 P-16). |
| A custom currency scheme can be selected when one exists. | Implemented | The workbench does not build or import schemes — those are built in Risk Modeler. |
| Treaties are selected explicitly at run time, in the execution modal. | Not implemented | **Changed 8/18 (spec 009 P-09) and 8/20.** The template-stored name pattern is dropped; the analyst picks treaties by name when executing (§5, Iteration 7). |
| DLM requires an event-rate scheme. | Implemented | Determined by the model profile, not the file; enforced at template save. |
| HD makes the event-rate scheme optional. | Implemented | Determined by the model profile, not the file. |
| Model, output, and accumulation profiles and currency schemes are viewed in the workbench, created and edited in Risk Modeler, and synced back. | Implemented | **Added 8/14.** The analysis-metadata screen: five tabs (model profiles, output profiles, event-rate schemes, currencies, currency schemes with vintages); same pattern as EDM data — selected, not owned. Accumulation profiles await a tabled irp-integration read (spec 009 FR-001). |
| Event-rate schemes are selected, never authored. | Implemented | **Added 8/14.** CIC does not create custom event rates. Admins can hide schemes from the pickers (spec 009 P-13). |

**Templates & suites**

| Requirement | Implementation | Notes |
|---|---|---|
| A template is one analysis definition: analysis/model profile + output profile + event-rate scheme, plus analysis settings. | Implemented | **Changed 8/14; shipped Iteration 6 (spec 009); currency removed 8/20 (note 17 D4).** "One row in Analysis Builder." The event rate is auto-populated — required for DLM, optional for HD/Accumulation. Currency is confirmed at submit time, per suite (spec 010 P-15). |
| A suite is an unordered set of templates. | Implemented | **Changed 8/18 (spec 009 P-08)** — was "ordered". No item order, no per-item settings; a template appears at most once per suite. e.g. "Global 2026 Q1." |
| A suite is defined primarily by region and output level. | Implemented | **Added 8/14.** Both conveyed by the suite's name, not stored fields (spec 009 P-03); the other settings are standardized within the suite. |
| Suites are predefined, not freeform user-built. | Implemented | **Added 8/14.** "We want them hard-coded" — predefined suites are how CIC enforces consistent settings. Create/edit/delete is admin-role-gated (spec 009 P-01); every analyst can view. Exceptions drop to the long list or Risk Modeler. |
| Suites and templates are maintained on an administration page. | Implemented | **Added 8/14.** Models and countries change. Nothing is seeded — starter suites (US, Canada, US+Canada, global) are set up manually on this page (spec 009 P-02); duplicate-and-edit (P-12) is the fast path. |
| Suites and templates can be exported and imported as CSV/Excel. | Not implemented | **Deferred 8/19 (spec 009 P-02).** Nice-to-have; the worked design is retained in `specs/009-template-suites/contracts/transfer-workbook.md`. |
| A suite may mix DLM, HD, and accumulation templates. | Implemented | **Changed 8/14.** Replaces "DLM and accumulation kept in separate suites" — separation is now a convention, not a rule. US wildfire is HD-only; Japan has DLM and HD suites. |
| Line of business is a further suite axis carrying different settings. | Implemented | **Added 8/14.** Property / auto / workers comp; carried via template tags and naming convention — no dedicated LOB field. |
| Applying a suite generates all its analyses at once. | Not implemented | **Changed 8/20.** Submission happens directly from the execution modal — one analysis per selected portfolio × template, no separate review page (§5). A chosen suite can be expanded in the modal to deselect templates first. |
| Analysis names are auto-generated. | Not implemented | Typing a name every time is a pain. **Convention locked 8/20:** portfolio name + template name (resolves O7-3); the per-template pattern was dropped 8/18. |
| An analysis can be renamed, including after it has run. | Not implemented |  |

> **Resolved 8/20 — auto-naming convention (O7-3).** Analysis names follow a fixed rule: **portfolio name + template name** — the template name already conveys profile, region, and peril, so nothing is configurable. Risk Modeler caps analysis names at 64 characters: the submitted name is truncated from the right to fit, and the workbench stores the full untruncated name on `irp_analysis`.

**Out of scope for MVP:** profile management (created and managed in Risk Modeler); schedule and stagger analyses (add only if RM makes it easy).

---

## 5. Analysis Execution

Running the work and tracking it — including GeoHaz and treaty setup.

**Geocoding & hazard**

| Requirement | Implementation | Notes |
|---|---|---|
| Hazard lookup can be launched from the EDM/portfolio summary page against one or more selected portfolios. | Implemented | One geohaz job per portfolio, one parameter set per launch. Spec 007 (Iteration 5), FR-001. |
| Hazard lookup starts with one click and no parameter modal. | Implemented | Every launch uses the standard DLM parameters. Spec 007, FR-002. |
| Geocoding is not re-run by default. | Implemented | Broker geocoding is preserved — Cheryl has never re-geocoded in this role. Spec 007, FR-005. |
| Re-geocoding, if ever needed, is done intentionally inside the model. | Not implemented | Not a workbench action. |
| Hazard lookup uses the configured `HAZARD_DATA_VERSION`. | Implemented | v25 as of now. Spec 007, FR-002. |
| Hazard lookup uses DLM (non-HD). | Implemented | Spec 007, FR-002. |
| Previously looked-up locations are not skipped. User-defined hazard values are overwritten. | Implemented | "The more comprehensive the data, the better." Spec 007, FR-002. |
| Hazard lookup runs earthquake and windstorm. | Implemented | Fixed for the one-click DLM launch. Spec 007, FR-002. |
| Running an inapplicable peril returns zero for that layer, not a failure. | Implemented | e.g. earthquake on a windstorm book — Risk Modeler behavior; the workbench submits both perils and displays whatever `tasks[].output.summary` reports. |
| The hazard job returns a summary of locations looked up per layer. | Implemented | Shown as Result in the expanded portfolio row. Spec 007, FR-022/FR-023. |
| The summary page shows, per portfolio, whether hazard lookup has been run through the workbench, with in-line status of any running geohaz job. | Implemented | The portfolios table's Hazard Version column; app-side execution history from `irp_job`. Spec 007, FR-011/FR-012. |
| The portfolios table displays the raw `hazardVersion`, and RM's stamp is never read to gate anything. | Implemented | Reverses the earlier "no geocode/hazard version stamp is displayed" row (2026-08-19): the stamp is displayed, and the unchanged half is that it never gates anything. The Hazard Version column shows the job's in-line status while non-terminal, then the raw stored `hazardVersion` (empty when absent); stamp origin is still O8-1. Spec 007, FR-013 (P-03). |

> **Open questions — hazard for HD / enhanced risk data.** Whether hazard retrieval must be run ahead of time for HD models is unconfirmed (O7-1). Enhanced risk data is not used today and may be HD-only; availability and whether CIC will want it is being checked (O7-2). Cheryl investigating both. (Design note 07 §1.3.)
>
> **Open questions — version stamp / lineage detail.** Where RM's geocode/hazard stamp comes from and what it gates (O8-1, Cheryl/team with Moody's); what execution detail the workbench records and displays per hazard lookup (O8-3, Ben — settled at Iteration 5 spec time). (Design note 10 §2/§7.)

**Treaty & reinsurance editing — pass-through**

| Requirement | Implementation | Notes |
|---|---|---|
| Adding or editing a treaty is a pass-through to Risk Modeler. | Not implemented | The workbench does not rebuild the RM treaty editor. Reconfirmed 8/4 — "you don't need to spend your time trying to reinvent the wheel… It's a data entry situation." The workbench treaty list links directly out to the Risk Modeler treaties page. |
| Adding or editing reinsurance is a pass-through to Risk Modeler. | Not implemented | Confirmed again 7/16 — "a perfect scenario for that." |
| A pass-through opens the Risk Modeler editor in a new window; the analyst edits and saves there, returns, and the page refreshes. | Not implemented | General pass-through pattern (design note 04 §7): where the workbench would only re-skin RM, hand off to RM. |

**Running & tracking**

> **Build order (8/20).** Suite execution — running one or more template suites against one or more selected portfolios — ships first; single-template execution follows through the same modal; loss-number retrieval for executed analyses comes later in the iteration (PRD §21 Iteration 7).

| Requirement | Implementation | Notes |
|---|---|---|
| A single analysis can be submitted against a portfolio. | Not implemented | Delivered as single-template execution — the second phase of Iteration 7, after suite execution. |
| Multiple portfolios can be selected and run in one action. | Not implemented | Cheryl recently had to rerun the same data across 6 portfolios one at a time. |
| All analyses in a suite (50–150+) can be batch-submitted in one action. | Not implemented |  |
| Running a suite is: select portfolios, open the execution modal, pick suite(s), confirm currency, pick treaties, submit. | Not implemented | **Added 8/14, changed 8/20** (was "select portfolios and treaties, pick the suite, go"). The default path needs no inspection of the constituent rows — "I'm trying to go fast." Currency is pre-filled from the pinned defaults; the analyst is forced to look at it every run (note 17 D4). |
| Execution starts from one or more selected portfolios. | Not implemented | **Added 8/20.** Portfolio-first: the analyst multi-selects portfolios on the EDM detail page, then chooses Execute Suite or Execute Template. |
| The execution modal lists suites (or templates) with a simple search. | Not implemented | **Added 8/20.** |
| Several suites — or several templates — can be chosen in one execution. | Not implemented | **Added 8/20.** No dedup across suites (dropped later the same day): a template in two chosen suites submits once per suite, with that suite's currency. |
| Currency (analysis currency + scheme + vintage) is confirmed in the execution modal, per chosen suite. | Not implemented | **Added 8/20 (note 17 D4/D5; spec 010 P-15/P-16).** Pre-filled from pinned env-var defaults; a template run confirms one currency for the execution; mixed books run as separate regional suites. |
| Analyses submitted from a submission context carry the submission's name as a Risk Modeler tag. | Not implemented | **Added 8/20 (note 17 D12 direction; spec 010 P-17).** Analysts aren't always in the workbench — the tag makes analyses findable in the platform and via API. |
| Suites and templates cannot be mixed in one execution. | Not implemented | **Added 8/20.** One kind at a time. |
| Submit is disabled until at least one suite or template is chosen. | Not implemented | **Added 8/20.** |
| Treaties are selected in the execution modal. | Not implemented | **Added 8/20.** Explicitly, by name, at run time (spec 009 P-09); the selection applies to every submitted analysis. |
| One analysis is submitted per selected portfolio × selected template of each chosen suite. | Not implemented | **Added 8/20.** Submission is direct from the modal — no separate review page. |
| An analysis record is written when its job is submitted and updated with settings/metadata when the job completes. | Not implemented | **Added 8/20.** `irp_analysis` written at submit; metadata backfilled on completion. Loss numbers are a later phase of Iteration 7 (§7). |
| A failure to submit is surfaced to the analyst immediately. | Not implemented | **Added 8/20.** The failed submission takes the retry path (PRD §14.3); never a silent drop. |
| A suite can be expanded to deselect individual templates before submitting. | Not implemented | **Added 8/14; placed inside the execution modal 8/20.** e.g. flood not covered by the treaty; the rest still auto-queues. |
| A suite analysis that fails because the data lacks its peril is expected, not an error. | Not implemented | **Added 8/14.** No loss = no charge; run it all, deal with failures at the end. |
| Every suite-analysis failure is surfaced with its reason. | Not implemented | **Added 8/14.** The job summary says the peril wasn't present — never silently ignored. |
| Accumulation analyses can be run. | Not implemented | In scope, with accumulation-specific settings; output detail in §7. |
| Job status is tracked live and auto-refreshed, per deal and overall. | Not implemented | Only progress useful to the modeler is shown. |
| Two job classes are tracked: IRP jobs and workbench jobs. | Not implemented | **Added 8/4.** IRP jobs are submitted to Moody's and polled for state; workbench jobs cover uploads and other wrapping/independent work. |
| Jobs target an EDM, RDM, portfolio, or analysis and may record the requesting Submission. | Not implemented | The requesting Submission is provenance only and never selects or duplicates the execution target. |
| Heavy work never runs on the request path. | Not implemented | The user is never blocked on an upload. |
| An activity indicator distinguishes actively-running from stuck. | Not implemented | **Added 8/4.** Status text alone gives too little insight: "as an analyst… we are antsy… we just want to get things moving through and we're going to want to see, oh, is it stuck? Is it happening? Is anything going on?" |
| The jobs view shows which Submission each job belongs to. | Not implemented | **Added 8/5.** So that when "we see something failed, we know which submission was that for." |
| The jobs view can be filtered by Submission. | Not implemented | **Added 8/5.** The bar is beating RiskLink on presentation: "even in RiskLink today you have to filter for your jobs and go find it." |
| A queued job is shown as queued. | Not implemented | **Added 8/5.** |
| A job's position in the queue is not shown. | Not implemented | **Added 8/5.** Wanted but hard, and accepted as a deferral — the cloud does its own job leveling, so position matters less than it did on-prem. |
| Throughput is monitored — locations analyzed per day. | Not implemented |  |
| Submission failure is distinct from run failure. | Not implemented |  |
| Failed jobs can be retried. | Not implemented |  |
| The analyst is not emailed when an individual job completes. | Not implemented | **Reversed 8/5.** Per-job success email was the starting proposal and was rejected as inbox clutter that gets ignored. Replaced by the daily digest — §8. |
| The analyst is emailed immediately when a job fails. | Not implemented | **Changed 8/5.** Errors never aggregate and never wait for the digest — §8. |
| Only operations whose inputs exist are offered. | Not implemented | EDM → portfolio → analysis → grouping. Prevents starting work that can't succeed. |

**Executed analyses on the EDM page** (8/20)

| Requirement | Implementation | Notes |
|---|---|---|
| User-executed analyses appear in their own section on the EDM detail page. | Not implemented | **Added 8/20.** Presented like the broker-analysis review (§2.3) — settings/metadata per analysis — but with no RDM grouping; there is no RDM in play. |
| Each executed analysis shows the portfolio it ran against. | Not implemented | **Added 8/20.** Trustworthy here — the workbench submitted it; the §2.2 trust rule concerns data that left CIC's environment (§7). |
| Executed analyses update live on the page as their jobs move through statuses. | Not implemented | **Added 8/20.** Same live-refresh treatment as import jobs. |

**Out of scope for MVP:** cedant ID check/creation; checking treaty coding accuracy (manual — treaty display in §2.2 helps the analyst catch it); **bulk treaty creation from CSV/Excel** — deprioritized 8/4: not something CIC does today, and "whether I do it in Excel or I do it in Risk Modeler, I basically have to do the same steps either way. At least there's some error checking that happens in Risk Modeler." Whether Risk Modeler even supports it is unknown and now academic (design note 08, O8-7).

---

## 6. Grouping

Combining and breaking out results across dimensions. A group is an analysis in
Risk Modeler (`irp_analysis` with `is_group = true`), created by
`submit_analysis_grouping_job`.

> **Design record (2026-08-10 → 08-27; design notes 11 §3, 15 §4, 17 §4).**
> Grouping is a first-class flow — pick finished analyses, configure, run —
> replacing Risk Modeler's three-dot "enter analysis to group" entry.
> Event-rate-scheme reconciliation is automated by irp-integration
> (`build_region_peril_simulation_set`; write-up:
> `grouping-and-event-rate-schemes.md` in the IRP workspace root, next to this
> repo). **CIC has not reviewed the grouping implementation** — the walkthrough
> Ben owes (design note 11 O11-2, note 15 O15-7) is still open.

| Requirement | Implementation | Notes |
|---|---|---|
| The analyst selects which finished analyses or groups to combine. | Not implemented | Prerequisite gate: members exist and are `FINISHED`. |
| The grouping pick-list is scoped to the current submission. | Not implemented | Checkboxes on the EDM detail page. **Corrected 8/20** from EDM-scoped to submission-scoped; members can span EDMs and RDMs within the submission. |
| Analyses carry a submission-level IRP tag, applied at submit time. | Not implemented | **Decided 8/27**: an IRP tag (queryable in the platform and via API), not a Workbench-only association — grouping candidates stay findable when the analyst isn't in the Workbench. Extends §4 "Tags can be set per analysis." |
| Currency, currency scheme, and vintage are chosen at group-submit time. | Not implemented | Same picker and env-var defaults as analysis submission (§5, spec 009 P-11). |
| Event-rate-scheme selection is automated — the analyst never picks a scheme. | Partial | irp-integration resolves `regionPerilSimulationSet` across members, covering differing DLM schemes and DLM+HD mixes; Risk Modeler's manual pre-step (Convert event rate and loss: copy + `_event` + new rates, no rerun) is not required. Hurricane is the case that matters (80/20, note 15 §4.3). Library-side built; CIC review pending (O11-2 / O15-7). |
| Mixing DLM and HD analyses in one group is supported. | Partial | **Reversed 8/27** — previously listed as an invalid grouping. irp-integration reconciles ELT and PLT members in one `regionPerilSimulationSet` and forces `simulateToPLT`. |
| "Propagate detailed output" defaults ON; the analyst may turn it off. | Not implemented | Exactly what detail is retained (state-level, per-treaty) is still open — note 11 O11-1. |
| "Create independent groups" defaults OFF. | Not implemented | "We're never going to want to turn those on." |
| Invalid groupings show error messaging. | Not implemented | e.g. an event-rate scheme that cannot be resolved across members. |
| Nested grouping (groups of groups) is supported. | Not implemented |  |
| A group is treated like any other analysis. | Not implemented | Viewed and exported the same way; the results grid discloses a group via the Engine column, not the name (note 20 D8). |
| Group names are auto-generated from the deal. | Not implemented | Exact shape open — whether the `CRE` prefix / `_n` conventions (§5) apply. |
| Groups appear on the submission-level results page. | Not implemented | Groups (and cross-EDM analyses) exist only at submission level (note 19 D14). |
| The analyst controls the left-to-right ordering of analyses and groups in the results view. | Not implemented | Note 19 D15 — "I want to see my analyses left to right and my group at the end, or my group at the beginning." |

**Out of scope for MVP:** create ELTs by zone / county / country (done in SQL or the old tool today); **opt-in end-of-suite auto-grouping** (Wendy, 8/20) — offer to auto-group a suite run's results in the suite's currency; deferred, the mixed-currency case is unresolved.

---

## 7. Results Management

Reviewing, comparing, and delivering finalized results. Volume is highly variable (4 to 100+ analyses per submission).

**Organizing & displaying results**

| Requirement | Implementation | Notes |
|---|---|---|
| Results are displayed grouped under the RDM that produced them. | Not implemented | No EDM-to-RDM relationship is inferred from sharing a Submission. |
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

---

## 8. Notifications

New section, designed 8/5. Nothing here is built — `config.py` holds unused SMTP and channel placeholders.

Two rules shape it. **Errors never aggregate**: any failure emails immediately, because a job expected to run ten hours is not checked at thirty minutes. **Success never emails per job**: per-job success mail floods the inbox and gets ignored, so it is replaced by one morning digest. The jobs view (§5) carries the in-between state an analyst wants during the day — Wendy: "a workbench, sort of by definition, you should kind of have it open on your desktop… I like the idea of just flipping over to this view where I just see my jobs."

**Channel & target**

| Requirement | Implementation | Notes |
|---|---|---|
| V1 notifications are email. | Not implemented | Other channels are a later phase — see the parked note below. |
| Email goes to the individual owner of the Submission. | Not implemented | Ownership is the soft "my submissions" marker from §1, used here as the recipient — not as an access gate. |
| Shared mailboxes are not a notification target. | Not implemented | Rejected on how CIC works: "we virtually never have multiple people working on the same submission," plus mailbox clutter — "I don't want to know what 12 other people are doing" — and Microsoft storage cost. |

**Failures**

| Requirement | Implementation | Notes |
|---|---|---|
| Any failure sends an email immediately, whatever the operation. | Not implemented | "Errors are always the most important thing to be notified about." |
| Failure emails are sent at the lowest level of granularity. | Not implemented | Not rolled up to the submission, not held for the digest: "if I'm expecting something to run for 10 hours, I'm not going to go in and check on it in 30 minutes. Like if it fails 30 minutes in, I want an e-mail notification." |

**Daily digest**

| Requirement | Implementation | Notes |
|---|---|---|
| One digest email is sent each morning. | Not implemented | Morning rather than end-of-day because work is often started before leaving for the evening. Placeholder ~8:00 AM Central. |
| The digest covers only the recipient's active Submissions. | Not implemented | "Once you have either marked the submission cancelled or complete, we will not bombard you with all of the historical things." |
| A Submission on Hold is excluded from the digest. | Not implemented | Hold is the lever for a deal parked while waiting on a broker — §1. |
| The digest reports, per Submission, how many jobs are outstanding. | Not implemented | With detail on which ones. |
| The digest reports, per Submission, when all jobs are complete. | Not implemented | The prompt to act: "you're free to go do something on it." |
| The digest flags any Submission that has failures. | Not implemented | The per-failure emails already went out; the flag stops a failure sitting unnoticed on a parked deal. |

> **Rejected 8/5 — one email per Submission when its last outstanding job completes.** Designed in the session and dropped. Most non-analysis work is sequential, one task at a time, so an "everything is done" trigger fires after every task: "let's just say I kicked off GeoHaz. I can't do anything else until the hazard retrieval is done… technically there's nothing else that's queued because I wouldn't have queued anything up after that until that stage is done." The premise fails for parked work too — "do I need a notification that I'm not doing anything? Probably not, because I know I'm not doing it." The digest carries the same content on a daily cadence instead. (Design note 09 §6.3.)

> **Open — the digest send time.** Placeholder ~8:00 AM Central, not decided. (Design note 09, O9-6.)

> **Open — what counts as an active Submission for digest purposes.** The edge case: a Submission whose only open item is job failures the analyst has deliberately parked — "do it every day for two weeks, I get an e-mail telling me that those job failures are there… when do they fall off?" Hold is the primary lever; the remaining rules are unwritten. (Design note 09, O9-7.)

> **Parked — channels beyond email.** A workbench notification icon/badge like Teams ("can notification be on the front page of the workbench?"), Teams messages ("certainly when you're traveling"), and an opt-in completion email for one time-critical job — "I have a really important job that I am waiting for… notify me when that job is done. I don't need to know the 20 other things I sent today." All beyond V1. (Design note 09, O9-8.)
