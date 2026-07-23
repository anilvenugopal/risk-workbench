# Risk Workbench — Functional Requirements

**Status:** Draft for design discussion · Living document, edited line-by-line in design sessions.

## How to use this document

Plain statements of what the workbench is and does, organized by workflow area. **Each row is exactly one requirement — one fact or one capability.** Requirements are deliberately not combined; if a statement has two independent parts, it is two rows. `Requirement` = a single literal statement · `Notes` = brief clarification, examples, or the field list a requirement enumerates.

**Scope:** MVP picks up at import into Risk Modeler / DataBridge onward — files arrive already unzipped, attached, and named by the existing SQL workflow. We are designing toward the future state where the workbench also absorbs front-end data setup (zip import, naming inferred from the directory). In/out markers on §3–§7 come from the June 2026 `CReWorkflow_Expanded` review and the July 2026 design sessions (7/7, 7/9, 7/14, 7/16).

**Basis:** This revision folds in the July 2026 design sessions. Source detail lives in `design_session_notes/03`–`07`. Open questions raised there are surfaced inline as callouts and must be resolved before the PRD locks the affected area.

---

## 1. Data Organization

Two objects: **Submission** (the deal, top level) and **Package** (an EDM/RDM set, bottom level).

> **Open question — Submission vs. Project:** whether the top-level object is a "submission" (mirrors the directory structure) or a more flexible "project," or whether these attributes just live on the package with no object above it. Deferred to wireframes; the requirements below apply either way. (Design note 03 §2–§4, OQ-1/OQ-2.)

> **Open question — does "Package" survive?** The 7/14 review flagged that "package" is not CIC terminology and may add too much organizational overhead; EDMs/RDMs could attach directly to the Submission instead. Held as the working baseline because the import flow works either way; to be resolved at the next wireframe review. (Design note 04 §9, 03 OQ-1.)

**Submission**

| Requirement | Notes |
|---|---|
| The Submission is the top-level object; it represents the deal. | |
| There is no customer or program level above the Submission. | |
| A Submission is one cedant's treaty of a given type at a given inception. | e.g. "6/1/2026 CAT XOL for Cedant A." |
| A Submission is displayed by cedant name + treaty type + inception date. | Readable identifiers analysts recognize at a glance. |
| A Submission has these attributes: cedant name, treaty type, inception date, treaty year, directory path, CRM ID(s). | Directory path = the deal's shared-drive folder. |
| Treaty year is derived from the inception date. | |
| CRM ID is the only guaranteed-unique attribute. | Everything else can overlap. |
| The descriptive attributes (cedant, treaty type, inception) can legitimately collide across distinct deals. | e.g. a regional cat and a corporate cat — same cedant, same inception, both "cat" — differing only by CRM ID. |
| The workbench does not enforce uniqueness on the descriptive attributes. | Identity rests on a surrogate key; a non-blocking "similar deal already exists" warning guards against accidental re-creation (PRD §7.2b). |
| A Submission can be tagged with zero or more CRM IDs. | CRM ID = a contract. Standard case is one; multiple contracts can share one Submission. Optional soft reference. |
| A Submission is assignable to a user (owner). | Enables a "My submissions" filter/sort. |
| Submission ownership does not control access. | All users see all Submissions. |
| A Submission has a status: Active, Completed, or Cancelled. | Set manually. |
| Submission status can be reopened from Completed or Cancelled back to Active. | |
| A Submission is never deleted. | Cancelled is the "not happening" outcome in place of a delete. |
| A Submission can link to the prior Submission it continues from. | UI label is **"Previous," not "Renews from"** (7/14) — the team also jumps back to compare when it isn't a formal renewal. Link may come from the treaty system. |
| The directory-path link opens a file browser at the deal's folder. | Useful jump-off, but the working/BAK file is often not in that folder — see §2.1. |

**Package**

| Requirement | Notes |
|---|---|
| A Package associates a set of EDMs with the RDMs that go with them. | "This EDM goes with these RDMs." Mostly a creation-time grouping; not displayed constantly through the app. |
| A Package can be any combination of EDMs and RDMs. | One or many EDMs, one or many RDMs, EDM-only, or RDM-only. e.g. 4 EDMs + 1 RDM is valid. |
| A Package requires at least one EDM or RDM. | |
| A Submission has one or more Packages. | |
| A Package can be shared across Submissions. | Same exposure and results reused across deals. |
| A Package is unique in its own right, not owned by any single Submission. | Its EDM/RDM names are likewise unique. |
| Work against a shared Package propagates to every Submission that shares it. | Analyses are hard-coupled to the EDM in Risk Modeler. Accepted and often preferred; provisional (design note 03 §5). |
| No duplicate EDMs are created just for tracking. | If isolation is genuinely needed, add a new portfolio within one EDM. |
| Multi-select of EDMs/RDMs is supported. | Select several at once to move or import them together. |

**EDM / RDM**

| Requirement | Notes |
|---|---|
| An EDM is an exposure database. | |
| An RDM is a results (losses) database. | Importing an RDM into Risk Modeler creates Analysis objects, not an RDM object. |
| An EDM/RDM is created from a source file: `.bak` or `.mdf`. | Zip in the future state. BAK is the path of least resistance — the naming convention is already embedded in the file name. |
| An EDM/RDM name must be unique in Risk Modeler (global). | |
| An EDM/RDM name must be unique on the on-prem SQL server. | |
| The workbench checks name uniqueness before import and surfaces an error on collision. | |
| Naming convention: `TY{YY}{MM}_{Cedant}_{InforceDate}_{Version}_{EDM\|RDM}`. | Add a distinguisher (business unit, region, treaty type) when databases would otherwise collide. |
| The name is auto-populated from the chosen import file name. | |
| The name is editable in place before import. | On collision, the user adds a suffix. |
| An EDM/RDM has a status: Pending, Uploading, Ready, Upload Failed. | |
| Rename-on-reattach is supported (~15% of the time). | e.g. mid-deal M&A — reattach/rename exposure for the acquiring company; also keeping test versions (v1 vs v2) separate. |
| On duplicate-and-rename, the workbench offers to auto-delete the old copy. | |

**Finding work**

| Requirement | Notes |
|---|---|
| The homepage opens to a Submissions list. | Sorted by owner and last-updated; surfaces the logged-in user's own work first (7/14). |
| Submissions can be filtered by cedant name, treaty type, inception date, owner, and CRM ID. | Cedant, treaty type, and inception are primary; CRM ID is secondary/retrievable. |
| Submissions can be sorted. | |
| Global search runs across Submissions, Packages (EDMs, RDMs), analyses, jobs, and results. | |
| Search, sort, and filter are available on every list section. | 7/14: wanted on portfolios, treaties, analyses, and results — not just the submissions list. |

**Navigation & drill-down**

| Requirement | Notes |
|---|---|
| Navigation is three-tier: Submission → Package → EDM. | The actual work happens at the EDM level (7/14). |
| An EDM drills down to its portfolios. | Analyses run against a portfolio, not a whole EDM. |
| The effective navigation depth is Submission → Package → EDM → Portfolio. | The portfolio drill-down is also the entry point for kicking off analyses. |

---

## 2. Data Setup

Getting a deal's data into the workbench and seeing what's in it.

### 2.1 EDM / RDM Import

| Requirement | Notes |
|---|---|
| The user can create a Submission. | A Submission can exist without a Package. |
| The user can create a Package. | |
| A Package is created by choosing its EDM/RDM combination. | |
| A Package is created by choosing the Submission(s) it lives in. | |
| The new-package flow opens to name the package and browse for files. | The package name does not default to the submission name (7/14). |
| The analyst browses network shares and selects file(s). | |
| File browsing is restricted to network drives — never the local machine. | Both the analysts' machines and the app are connected to the shares. |
| Two network locations are supported: a working drive and an archive drive. | e.g. M = client/working drive, L = archive/BAK drive; the archive is not organized the same way and may split into in-force / projected subfolders. |
| The file browser supports folder-tree navigation. | |
| The file browser supports search by file name. | Especially valuable for BAK files stored under non-recognizable names. |
| The file browser supports free navigation to any location. | The linked deal folder is often not where the working/BAK file lives. |
| Multi-select of files is supported. | Pick several at once. |
| The workbench does not move or reorganize files on the shares. | It reads source files in place. |
| EDM vs RDM classification is inferred from the naming convention. | ~90% of names contain "EDM," "RDM," or "results." |
| The inferred EDM/RDM classification can be flipped in place. | Corrects the type when the name isn't reliable. |
| The name is auto-populated from the file and editable before import. | |
| An error is shown if the name is not unique in Risk Modeler or on-prem SQL. | The analyst adds a suffix. |
| The analyst can Save a Package without syncing to Risk Modeler. | Stages the selection while still gathering files across directories. |
| The analyst can Save & Sync to import the Package into Risk Modeler (DataBridge). | |
| Imports run in the background. | The analyst can leave the page and keep working — avoids Risk Modeler's slow, blocking upload. |
| All selected files can be submitted for import at once. | The system runs ~10 concurrently and auto-dequeues the rest as slots free up; chunking gives no benefit (7/14). |
| A delete-after-transfer checkbox removes the temporary BAK file once imported. | Otherwise temp files accumulate on the share forever. |
| Import status is tracked per EDM/RDM. | |
| Import status is tracked per Package. | Package-level status tells us when it is ready to work on. |
| An EDM/RDM already in Risk Modeler can be linked to a Submission without re-importing. | "Orphaned" databases not created by the workbench — select them into a Package. |
| All EDMs are browsable in an EDM Library. | Includes workbench-created and orphaned. |
| All RDMs are browsable in an RDM Library. | Includes workbench-created and orphaned. |

> **Open question — BAK vs MDF vs zip import path:** whether import points at BAK files in a shared BAK directory, or selects MDF/zip files from a network drive (varying by cedant/submission/treaty year), is unresolved. (Design note 04 §8.2, O4-2.)

**CSV import — deprioritized outlier for MVP.** CSV-for-EDM is MRI (accounts + locations files together), rare for their data. CSV-for-results is HD/PLT output, not natively supported for RDM import in Risk Modeler. Open question: what can Risk Modeler actually *do* with a CSV result once imported — if it's view-only, there's no point (design note 03 §8, OQ-6). Push cedants/brokers to provide RDMs instead.

### 2.2 Exposure Details Viewing

Rolled up so the analyst doesn't click through Risk Modeler — a fast textual snapshot.

| Requirement | Notes |
|---|---|
| A map is not needed. | A fast textual snapshot is what's wanted. |
| The existing Power BI exposure dashboards are not rebuilt. | |
| EDM-aggregate figures are shown on the submission/EDM overview. | Quick orientation. |
| Per-portfolio figures are shown once inside a specific EDM. | Analyses run on a portfolio, not the EDM (7/14). |
| Location, account, and policy counts are shown. | |
| The number of portfolios in the EDM is shown. | Sometimes 1 portfolio, sometimes 25. |
| Perils and sub-perils covered are shown. | |
| Geography is shown: region(s), state(s), or a CIC-defined region. | e.g. "Southeast," "Florida only." |
| Currency is shown. | Currency defaulting/handling for analysis is specified in §4. |
| Record volume is shown. | So the analyst doesn't accidentally run a ~1M-record portfolio thinking it's ~20K, and can schedule large runs (e.g. start a 4M-record run overnight). |
| Reinsurance/treaties associated with the EDM are shown. | LOB and cedant come from the EDM details. |

**Treaty viewing** (7/14)

| Requirement | Notes |
|---|---|
| Full treaty attribute detail is shown. | Cheryl wants all attributes visible to catch mis-coding, not blindly trust it ("sometimes people put the wrong thing in the wrong field"). |
| Treaties expand and collapse. | Few treaties shown expanded; many collapse to focus one at a time. |
| Wide treaty attribute sets scroll horizontally in the compact view. | |
| Treaties can be exported to Excel. | For extreme cases with too much to render cleanly. |
| Treaty setup is shown at the EDM level. | Treaties can be applied at portfolio, account, or policy level; results are spit out to the RDM. |
| For a per-risk treaty, the losses subject to the treaty are the interest — not the portfolio itself. | |
| Inuring reinsurance is identified and removed to get the net perspective. | The models do this. |

*(Treaty creation/editing is a pass-through to Risk Modeler — §5.)*

### 2.3 Loss Results Viewing

Reviewing broker-provided results and settings. Full results review and delivery in §7.

| Requirement | Notes |
|---|---|
| Broker-provided (RDM) analyses can be reviewed before running own analyses. | |
| Each broker result shows its analysis settings/metadata. | Full metadata list in §7. e.g. rate, perils/sub-perils, loss amplification on/off, detail level saved, portfolio it ran against. |
| The results view helps the analyst decide how much work a given RDM even needs. | Sometimes the broker already provides what's needed and the analyst only pushes losses to the repository — no remodeling. |

**Out of scope for MVP:** policy-level detail; PDFs and other non-modeling exhibits (File Explorer suffices).

---

## 3. Data Modification

Re-shaping exposure to match treaty terms before analysis. This cannot be done in the current workflow tool (done in RiskLink today, which is slow); Risk Modeler makes it fast and synchronous, so it becomes a *preferred* path.

| Requirement | Notes |
|---|---|
| Sub-portfolios are created by filtering an EDM's exposure. | To match terms the broker didn't break out — e.g. isolate a state with a different retention, or exclude a line of business. |
| The current portfolio split is visible before deciding how to re-group. | |
| Filter values are picked from the real values present in the portfolio. | Not free-text — "people put crazy things in the LOB field," and typing them exactly is messy. |
| Sub-portfolio creation is synchronous. | Fastest operation in the whole flow (HTTP 201, no job). |

**Single-click breakouts** (7/16, Ben to build)

| Requirement | Notes |
|---|---|
| One-click breakout by line of business creates one sub-portfolio per LOB. | Simplest case; unaffected by the commercial-geo problem below. |
| One-click breakout by state/country creates one sub-portfolio per geography. | |
| One-click complement split ("X vs. not-X") creates one portfolio for selected states and one for everything else. | e.g. Northeast and everything-not-Northeast, from a single action. |
| Breakouts sum to 100% of the source portfolio. | Not "run the whole thing, then a subset, and subtract" — that's messy. |
| A "do the opposite" option produces the complement of a defined filter without re-coding it. | Define "Florida mobile home" once and also get "everything that's not Florida mobile home." |
| Portfolio-creation granularity is capped at state/country. | Finer than that (CRESTA, ZIP) is saved as output, not a portfolio — "too much to manage." |
| Regions are not pre-defined constants. | "Northeast" is defined by the treaty / how the cedent writes the business; contracts define coverage arbitrarily. |

> **Open question — commercial-policy geographic split (blocking for the geography breakouts).** Splitting a multi-location commercial policy geographically breaks its financial structure and can double-count in a complement split (keep-all-locations behavior). Whether Risk Modeler keeps all locations or only matching ones — and whether it exposes a toggle — is unconfirmed; a RiskLink "checkbox" recollection is not load-bearing. Output-side alternative: write losses to the state level and let the model allocate back. Ben investigating RM behavior; Cheryl polling the team for the preferred default. (Design note 06 §3, O6-1/O6-2.)

**Out of scope for MVP:** peril splitting / peril-specific portfolios (no longer needed — "we don't have to split it up by peril"; verify whether RM adds a missing peril); update/change data elements; data validation reports; exposure profiling / loading summaries to the Exposure Repository; **merge/combine portfolios** (recombination happens on results, not exposure — design note 06 §4).

---

## 4. Analysis Configuration

Setting up analyses. Configuring a worldwide contract by hand is the #1 analyst pain point (50–150+ combinations).

**Profiles & settings**

| Requirement | Notes |
|---|---|
| Standard model (DLM) profiles are selected from a pre-compiled list. | Profiles are created and managed in Risk Modeler; the workbench selects, it does not own profile management. |
| Multiple model profiles can be selected for one portfolio/treaty combination. | |
| Output profiles are selected from a pre-compiled list. | Also created and managed in Risk Modeler. |
| User-defined (UD) profiles are supported and selectable. | Naming convention `UD` + initials, e.g. UDCT. |
| Profile lists can be filtered. | When the list grows long — "just get to UDCT." |
| An event-rate scheme is configurable per analysis. | People are "very picky" about it. |
| Franchise deductible is an exposed per-analysis toggle. | Deal-specific; the team wants direct access — the exception to holding advanced settings constant. |
| Unrecognized construction / occupancy type is an exposed per-analysis toggle. | |
| Min loss threshold and max loss event stay at defaults. | Held constant, not surfaced. |
| Tags can be set per analysis. | |
| Loss/analysis currency is selected per analysis. | Assigned in the analysis builder; changing it affects only the selected analyses. |
| Analysis currency defaults to the exposure's native currency when the exposure is one-to-one. | A single currency in the exposure. |
| Analysis currency defaults to USD otherwise. | General default; a US book must not default to Euros. |
| The latest currency scheme is used by default when rerunning. | Currency scheme = the exchange rate at a point in time. |
| A custom currency scheme can be selected when one exists. | The workbench does not build or import schemes — those are built in Risk Modeler. |
| Treaties are selected by name or pattern. | |
| DLM requires an event-rate scheme. | Determined by the model profile, not the file. |
| HD makes the event-rate scheme optional. | Determined by the model profile, not the file. |

**Templates & suites**

| Requirement | Notes |
|---|---|
| Model profile, output profile, and event-rate scheme are the "big three" configured settings. | These define an Analysis Suite. |
| Analysis templates can be saved. | |
| Templates are collected into suites. | e.g. "Global 2026 Q1." |
| An Analysis Suite is a pre-configured set of (model profile, output profile, event-rate scheme) combinations. | Solves the "global book" pain of setting up every model one at a time. |
| Applying a suite generates all its analyses at once. | Ready to review and adjust before submitting. |
| DLM and accumulation analyses are kept in separate suites. | Don't combine them (§5, §7). |
| Analysis names are auto-generated. | Typing a name every time is a pain. |
| An analysis can be renamed, including after it has run. | |

> **Open question — auto-naming convention.** The draft convention draws on portfolio name + near-term/long-term + event-rate scheme, but is not finalized. (Design note 07 §2.3, O7-3.)

**Out of scope for MVP:** profile management (created and managed in Risk Modeler); schedule and stagger analyses (add only if RM makes it easy).

---

## 5. Analysis Execution

Running the work and tracking it — including GeoHaz and treaty setup.

**Geocoding & hazard**

| Requirement | Notes |
|---|---|
| Hazard lookup can be run on a portfolio. | |
| Geocoding is not re-run by default. | Broker geocoding is preserved — Cheryl has never re-geocoded in this role. |
| Re-geocoding, if ever needed, is done intentionally inside the model. | Not a workbench action. |
| Hazard lookup defaults to the latest data version. | v25 as of now. |
| Hazard lookup defaults to DLM (non-HD). | |
| Missing locations are not skipped; they are overwritten. | "The more comprehensive the data, the better." |
| Earthquake and windstorm perils are selected by default. | Toggleable. |
| Running an inapplicable peril returns zero for that layer, not a failure. | e.g. earthquake on a windstorm book. |
| The hazard job returns a summary of locations looked up per layer. | |

> **Open questions — hazard for HD / enhanced risk data.** Whether hazard retrieval must be run ahead of time for HD models is unconfirmed (O7-1). Enhanced risk data is not used today and may be HD-only; availability and whether CIC will want it is being checked (O7-2). Cheryl investigating both. (Design note 07 §1.3.)

**Treaty & reinsurance editing — pass-through**

| Requirement | Notes |
|---|---|
| Adding or editing a treaty is a pass-through to Risk Modeler. | The workbench does not rebuild the RM treaty editor. |
| Adding or editing reinsurance is a pass-through to Risk Modeler. | Confirmed again 7/16 — "a perfect scenario for that." |
| A pass-through opens the Risk Modeler editor in a new window; the analyst edits and saves there, returns, and the page refreshes. | General pass-through pattern (design note 04 §7): where the workbench would only re-skin RM, hand off to RM. |

**Running & tracking**

| Requirement | Notes |
|---|---|
| A single analysis can be submitted against a portfolio. | |
| Multiple portfolios can be selected and run in one action. | Cheryl recently had to rerun the same data across 6 portfolios one at a time. |
| All analyses in a suite (50–150+) can be batch-submitted in one action. | |
| Accumulation analyses can be run. | In scope, with accumulation-specific settings; output detail in §7. |
| Job status is tracked live and auto-refreshed, per deal and overall. | Only progress useful to the modeler is shown. |
| Throughput is monitored — locations analyzed per day. | |
| Submission failure is distinct from run failure. | |
| Failed jobs can be retried. | |
| The analyst is notified when a job completes. | |
| The analyst is notified when a job fails. | |
| Only operations whose inputs exist are offered. | EDM → portfolio → analysis → grouping. Prevents starting work that can't succeed. |

**Out of scope for MVP:** cedant ID check/creation; checking treaty coding accuracy (manual — treaty display in §2.2 helps the analyst catch it).

---

## 6. Grouping

Combining and breaking out results across dimensions.

| Requirement | Notes |
|---|---|
| The analyst selects which analyses to group. | |
| Invalid groupings show error messaging. | e.g. mixing DLM and HD analyses. |
| Nested grouping (groups of groups) is supported. | |
| A group is treated like any other analysis. | Viewed and exported the same way. |
| Group names are auto-generated from the deal. | |

**Out of scope for MVP:** create ELTs by zone / county / country (done in SQL or the old tool today).

---

## 7. Results Management

Reviewing, comparing, and delivering finalized results. Volume is highly variable (4 to 100+ analyses per submission).

**Organizing & displaying results**

| Requirement | Notes |
|---|---|
| Results are displayed grouped under the RDM that produced them. | Matches the current workflow; the EDM↔RDM relationship is already implied by the package. |
| Broker results are deduped by RDM. | (`rdm_id`.) |
| Portfolio↔analysis linking is not solved. | Deliberately deferred — it doesn't exist today either; analysts rely on naming conventions and broker documentation. **Scope note:** this is the results-comparison linking (which analyses to line up own-vs-broker), still deferred. It is *distinct* from showing the **portfolio an analysis ran against** (§2.3 metadata), which Iteration 3 *does* surface by resolving Risk Modeler's `exposureResourceType = PORTFOLIO` exposure pointer (spec 004 FR-036; PRD §21 Iteration 3). |
| Up to ~5 analyses are consumable on screen. | Default density guideline, not a hard cap. |
| More than ~5 analyses are exportable / drill-down. | |
| Full listing / full drill-down of all analyses remains available. | The on-screen cap must not become a step back from RiskLink, which lists them all. |

**Metrics & metadata**

| Requirement | Notes |
|---|---|
| ELT summary is shown: AAL, max event loss, record count. | |
| AAL / pure premium is shown. | |
| Standard deviation is shown. | |
| Return-period loss numbers are shown. | Indicative set: 1000, 500, 250, 100, and ~20–25 year — exact points to be confirmed (O5-2). |
| OEP and AEP are both shown. | Cheryl uses OEP more. |
| A TCE (tail conditional expectation) toggle is available. | Not routinely used, but nice to toggle. |
| An EP-curve graph is not required. | "The drawing's not important… I want the numbers." |
| PLT is shown (HD only). | |
| Results can be switched between financial perspectives. | Perspective switching is essential — Gross, Ground-Up, Reinsurance Layer / net; "look at it from however you ran it." |
| Analysis metadata is shown alongside results. | See the metadata list below; reused for broker-result review (§2.3). |

Analysis metadata list (design note 05 §2): engine / model version · engine type (DLM vs HD) and version · analysis type / mode · peril (primary and secondary) · region · currency · construction · line of business · group type · long-term vs near-term · event-rate scheme / rate vintage · loss amplification (PLA). *Rate/event-rate detail lives one drill-down deeper than the rest (RiskLink "analysis summary" vs the main grid).*

> **Open question — event-rate scheme round-trip.** The event-rate scheme does not appear to survive a Risk Modeler export → re-import (exactly the broker scenario); near-term/long-term and rate vintage both matter. Ben investigating how to recover/carry it, and whether "vintage" is even a first-class RM concept. (Design note 05 §3, O5-1.)

**Comparison**

| Requirement | Notes |
|---|---|
| Own and broker results can be viewed together. | Multiple analyses in one view. |
| Analyses can be compared side-by-side. | Ben has a prior comparison engine to build on. |
| The side-by-side comparison includes a percent-difference column. | e.g. CIC vs. broker — saves the manual Excel step. |

**Editing return periods — pass-through**

| Requirement | Notes |
|---|---|
| Editing return periods / interpolation is a pass-through to Risk Modeler. | A subset of business needs return periods at specific loss intervals; same pattern as treaty edit (§5). |

**Accumulation results**

| Requirement | Notes |
|---|---|
| Accumulation output perspectives are gross and pre-cat net. | Reinsurance-layer (RL) retained. |
| Ground-up is currently included in accumulation output. | A Risk Modeler UI constraint, not a preference; possibly droppable via the API (O7-5). |
| Accumulation shows how a policy limit allocates by geographic area. | e.g. a $1M policy over $50M of buildings across several states. |

**Delivery**

| Requirement | Notes |
|---|---|
| Results can be copied / pasted out. | |
| ELTs are uploaded to the Loss Repository for downstream reporting. | Losses, financial perspective, and metadata. Open question: how to move data from DataBridge to the Loss Repository. |

**Out of scope for MVP:** **Post-Analysis Treaty (PATE)** — adding a cat treaty onto broker results after the fact and re-simulating; a rare fringe case, portfolio-level only, deferred (design note 05 §6, O5-4); formal loss validation against broker/cedant (confirm the informal multi-analysis view is enough); visual compare in RiskLink / copy to Excel; pushing broker results to the Loss Repository; loading exposure summaries to the Exposure Repository; carrying CRM ID tags through to the repository upload (Future); uploading loss sets to Analyze Re (separate API).
