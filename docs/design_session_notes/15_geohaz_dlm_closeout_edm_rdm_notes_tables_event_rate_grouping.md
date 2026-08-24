# IRP Workbench — Design Notes: GeoHaz DLM Hazard-Lookup Closed for v1, EDM/RDM Notes & Navigation Finalized, Table Standardization, Event-Rate-Scheme Grouping (Hurricane)

**Source:** Design session, August 17, 2026 (~36 min) — Ben Bailey presenting (PremiumIQ); Cheryl TeHennepe, Wendy Hayes (CIC). Cross-checked against the full transcript. Tenth session of the series (continues Aug 4/5/6/7/10/11/12/13/14). A short, mostly demo-and-review call: Ben **closed out GeoHaz DLM hazard-lookup and the packages-removal / EDM-notes work "for version one,"** pulled **table standardization** into the current PR, and took Cheryl's testing findings on **event-rate schemes** ahead of building grouping. Executes on the close-out direction set 8/14; the suites-first build itself is picked up the next session (see `16`).
**Status:** Working design notes. Direction agreed: **DLM hazard lookup is done for v1** (one-click, no modal; hazard-version tag on the "hazard looked up" column sourced from jobs; submitted/running status feedback; a UI-refresh bug fixed live); **accept a blank "most recent hazard lookup" + footnote** for lookups run directly in Risk Modeler (Risk Modeler jobs can't be queried by the entity they ran against, so no reliable "latest external lookup" — **no query-by-entity workaround now**); **EDM notes finalized and RDM brought to parity** (notes + submission breadcrumb / cross-entity navigation); **submission-list notes stay expanded** (nuance on `14` D2); **table standardization pulled into the current work before merge** rather than deferred; **broader UI polish deferred** to post-end-to-end, with a **button-vs-link consistency pass** flagged for later; **results/data export tabled** to the latter half of the week; and **event-rate-scheme changes on grouping scoped to hurricane** via Risk Modeler's **Convert event rate and loss**. Mostly executes/confirms `14`; the new reconciliation items are the **jobs-query-by-entity limit** and **RDM notes as a stored surface**. Extends `14` (DLM one-click D4/D5, jobs-sourced hazard column D6, EDM notes D1/D2), `13` (packages removal, EDM notes, GeoHaz first pass), `07` (hazard-lookup-only; running analyses).
**Related:** `14_analysis_suites_first_geohaz_dlm_hazard_edm_notes.md` (§2.1 DLM one-click D4/D5, §2.2 jobs-sourced hazard column D6, §2.3 HD deferred + O12-3, §3.1 EDM notes D1/D2, §1 suites-first D9), `13_packages_removal_breakouts_edm_notes_geohaz.md` (§1 packages removed, §3.3 EDM notes, §5 GeoHaz first pass, O12-1 breakout blocker), `07_analysis_execution_geohaz_currency_accumulation.md` (§1 hazard-lookup-only never re-geocode, §2 running analyses), `10_edm_summary_submissions_geohaz_currency.md` (§2 GeoHaz display / no on-screen version stamp), `../DATA_MODEL.md` (§6 EDM notes field; §7 `analysis_template.event_rate_scheme_name`; §8 `irp_job` / `rwb_job` summary reporting; §10 `irp_event_rate_scheme.peril_code` / `model_region_code`), `../PRD.md` (§11, §14 analysis execution), `../FUNCTIONAL_REQUIREMENTS.md` (§2 EDM/submission), `sequence_diagrams/planned/composite/group_results.md` + `sequence_diagrams/planned/granular/grouping.md` (grouping flow), `sequence_diagrams/planned/composite/run_geohaz.md`, `../../../../CIC/minutes/Risk_Modeler_Interface_Design_Minutes_8-17-26.md`, `../../../../CIC/transcripts/Risk Modeler Interface _ Design 81726.vtt`

> Decision IDs (**D1**–**D8**) below refer to the tables in the 8/17 minutes. Open-item IDs are **O15-n**. Continues from `14` (8/14); the 8/18 session that builds on this is `16`.
> *Transcript note: the auto-transcription garbles EDM ("CDM/VDM/idiom"), RDM ("RMS" when paired with EDM), and "Workbench" ("Word"); "23 → 25 / 2025 stochastic" = 2023/2025 stochastic rate event sets. "Convert event rate and loss" is Cheryl reading the actual Risk Modeler option name and is rendered as-is. The "CRM ID" text box (§3.2) is transcribed uncertainly and left as heard.*

---

## 0. TL;DR

Short close-out-and-review session. Product-affecting outcomes:

- **DLM hazard lookup is DONE for version one (D1).** The one-click run (no modal), the **hazard-version tag** replacing the old Workbench-only yes/no column, and the **submitted → running** status feedback all work; Ben caught and **fixed a live bug** where the "most recent hazard lookup" details weren't refreshing on the UI after a Workbench-executed run (the data was correct). Still **DLM-only, not HD** (HD stays deferred — `14` D7/§2.3). Confirms/executes `14` D4/D5/D6.
- **Accept a blank "most recent hazard lookup" + footnote for out-of-Workbench runs (D2).** Risk Modeler **jobs can't be queried by the entity they were run against**, so the Workbench can't reliably surface the *latest* hazard lookup for a portfolio hazard-looked-up directly in Risk Modeler. Details populate **only from Workbench runs**; otherwise the section shows a footnote ("no hazard lookup has been run through the workbench"). Both CIC reps accepted — the info is available in Risk Modeler, the analyst is in their own project, re-hazarding will mostly happen in the Workbench: "the exception, not the rule." **No "creative" query-by-entity workaround now.** → **O15-2**; depends on `14` **O14-6** (RiskLink GeoHaz/rate-scheme fields blank on export).
- **EDM notes finalized and RDM brought to parity (D3).** Edit **in place** in the submission table (double-click; enter/escape); **hidden-by-default** view/hide control on the EDM detail page; RDM pages now get the **same notes surface and the same submission breadcrumb / cross-entity navigation** as EDMs (previously absent). Cap **250 characters**. Extends `14` D1/D2 from EDM onto RDM. → **O15-3** (RDM notes as a stored field / nav parity — reconcile §6/§7).
- **Submission-list notes stay expanded (D4).** Cheryl values notes **in the submission list** — with several EDMs you can spot "don't use this one" without clicking in — so they stay visible there (no collapse), while the **EDM-detail** section is the one that collapses (`14` D2). Acceptable because notes are expected to be short.
- **Table standardization pulled into the current PR (D5).** The EDM and RDM notes columns don't align. Ben will move the submission-page tables to **fixed-width, aligned columns** (status capped short, notes variable) and align them across EDM/RDM — as part of **this** work, **before merging**, rather than deferring with the rest of the polish. Applies as a pattern across all tables.
- **Broader UI polish deferred; button-vs-link consistency pass flagged (D6).** White space, the "CRM ID" text-box/add-button section, the out-of-order Moody's data tab, the counterintuitive "EDM library" naming, and the button/link question wait until an **end-to-end workflow** exists. Wendy's framing: late in the project, run a **consistency check** with a shared definition of "what is a button vs. a clickable link, and when to use each." → **O15-5**.
- **Results / data export tabled (D8)** to the latter half of the week (build likely next week) rather than designed now. → **O15-8**.
- **Event-rate-scheme change on grouping is a HURRICANE concern (D7).** Cheryl's testing: Risk Modeler only offers a rate-scheme choice **at grouping time when the grouped analyses have MIXED schemes**; matching schemes must be changed **before or after** grouping, via **Convert event rate and loss** (copies the analysis, appends "_event," assigns new rates, visible in the UI, **no rerun** unless the model itself changed). It's driven by newer event rates than a broker supplied — canonically bumping **2023 → 2025 stochastic wind** rates — and applies effectively only to **hurricane** (the 80-20 rule). Grouping isn't built yet; align on its design later in the week (~Thu 8/20). Ben still **owes Cheryl a write-up** of how he handles this on the technical side (API job submission). → **O15-6**, **O15-7**. Maps to `irp_event_rate_scheme.peril_code`/`model_region_code` (§10) and the planned grouping flow.

---

## 1. GeoHaz / hazard lookup — DLM closed for v1

Executes `14` §2.1–§2.2 (DLM one-click, hard-block on data-version incompatibility, jobs-sourced hazard-version column) and `07` §1 (hazard-lookup-only, never re-geocode). This session **built, demoed, and signed off** the DLM path.

### 1.1 One-click DLM run + status feedback (D1)

- The multi-step flow is collapsed to a **single button** run from the top right against one or more selected portfolios: latest data version, both perils, fixed options, **no modal** — exactly the `14` D4 default set. **DLM hazard only, not HD.**
- The old **Workbench-only yes/no "hazard looked up" column** is replaced by the **actual hazard-version tag** (as seen in Risk Modeler), moved to the right-hand side to keep the portfolio metadata clean. Populated version = a lookup ran; **blank = none** (subject to the RiskLink-export caveat, §1.2 / `14` O14-6).
- **Status feedback** confirmed working: clicking go moves the job **submitting → submitted** (job handed to Risk Modeler; the Workbench then polls Risk Modeler statuses), with **resubmission blocked while it runs** — "something that lets the user know something's still happening" (Cheryl). Shown under the hazard-version field.
- **Live bug fixed:** mid-demo the "most recent hazard lookup" details didn't populate after a fresh Workbench run. Ben traced it to a **UI-refresh** issue (the data in the DB was correct) and fixed it on the call, then declared DLM hazard lookup **done for version one**. → **O15-1** (verify the fix holds through merge).

### 1.2 "Most recent hazard lookup": accept the footnote, no query-by-entity (D2)

- The open question from 8/14: for a portfolio that carries a hazard **version** but has **no Workbench lookup details** (because it was hazard-looked-up directly in Risk Modeler and synced back), can the Workbench query Risk Modeler jobs to always show the **latest**?
- **Finding:** Risk Modeler **jobs cannot be queried by the entity they were run against** — which is exactly the key this would need. A workaround is possible but "creative," not direct, and Ben would rather not spend time on it with more immediate work outstanding.
- **Decision:** populate the details **only from Workbench-executed lookups**; otherwise show a footnote ("no hazard lookup has been run through the workbench"). Cheryl and Wendy both accepted: the analyst can read those details in Risk Modeler, it's the analyst returning to their **own** project (not someone backtracking), and re-hazarding will mostly happen in the Workbench — "the exception, not the rule." Cheryl: if Moody's answers the **question already sent up** (`14` O14-6) a cleaner path may open later.
- **Reconciliation:** this is a limit of the `irp_job` / `rwb_job` query surface (§8), not the hazard model. A blank hazard-version column still ≠ "no hazard retrieval ever happened," because incoming RiskLink data may simply not populate those fields on export (`14` O14-6). → **O15-2**.

### 1.3 Jobs-sourced hazard-version column (recap `14` D6)

- The column is fed by **querying jobs — Workbench-submitted and user-submitted** — so it can't drift from reality, and version metadata can likely be pulled **live** rather than through the heavier sync path (`14` §2.2). Unchanged this session; the §1.2 limit is specifically about pulling **full job/result details** by entity, not the version tag.

---

## 2. EDM / RDM notes & navigation finalized

Closes the notes thread from `13` §3.3 and `14` §3.1, and — new this session — extends it onto **RDM**.

### 2.1 EDM notes — final shape (recap `14` D1/D2)

- **Edit in place** in the submission table: double-click the notes column (enter to save, escape to cancel); an edit button and **bulleted formatting** are also available. Cap **250 characters** (line count follows character count; the section can be expanded and copied).
- On the **EDM detail** page the notes are **hidden by default** behind a view/hide control, so they don't consume the space normally used by the source-file section ("we don't have notes taking a bunch of room on this page; we can still see them if we want them").

### 2.2 RDM parity — notes + navigation (new) (D3)

- Ben applied to **RDM** pages the **same notes surface** and the **same navigation** he'd built for EDMs — the **submission breadcrumb** (the submission you navigated from) and **cross-entity movement** (bounce between the RDMs/EDMs in the submission). This hadn't been done before; he noticed RDM notes were also missing and added them.
- **Reconciliation:** confirm the data model carries **RDM notes** as a stored field alongside the EDM notes field (§6) and that RDM detail supports the submission-scoped navigation. If §6/§7 only model EDM notes, add the RDM equivalent. → **O15-3**.

### 2.3 Submission-list notes stay expanded (D4)

- Wendy questioned whether always-expanded notes eat too much room in the submission list. Cheryl argued the **list is the most valuable place** for notes — with six EDMs you can flag "don't use this one, it's the wrong one" without clicking in — so they stay visible there; the **collapse** behavior belongs on the EDM detail page (§2.1 / `14` D2). Acceptable because notes are expected to be short, not giant bulleted lists. Ben: "I'll leave it."
- **Discoverability caveat:** Cheryl noted the view/hide-notes affordance **isn't obviously clickable** on screen; Ben offered to make it a real button; Cheryl declined to be nitpicky ("people will learn it pretty quick"). Folded into the consistency pass (§3.2, O15-5).

---

## 3. Table standardization, UI polish & packages close-out

### 3.1 Table standardization — do it now, before merge (D5)

- Wendy's callout: the EDM notes column and the RDM notes column **don't align** in the grid. Root cause — the tables are currently **fixed-width**; longer EDM names widen that column and push the others out of alignment.
- **Direction:** move to columns with sensible behavior — **status capped short** (never long), **notes variable-width** — and **align** the equivalent columns across the EDM and RDM tables. Both Wendy and Ben want the visual consistency.
- **Timing:** because Ben is already deep in this submission/EDM/RDM page, he'll do the **table cleanup as part of this work, before merging the PR** — not deferred with the rest of the polish. Applies as a **standard** across all their tables (they currently have inconsistent styling, which is "not a good thing from a product or a code perspective"). → **O15-4**.

### 3.2 Broader UI polish deferred + consistency check (D6)

- Lower-priority items parked until an **end-to-end workflow** is built/executed/demoed: excessive **white space**, the **"CRM ID" text-box/add-button** section (takes too much room), the **out-of-order Moody's data tab**, the misleadingly named **"EDM library"** (it holds only Workbench EDMs), placeholders, and the button-vs-link question.
- **Consistency check (Wendy):** the "should it be a button or a link" question will recur across the app; near the end of the project run a pass defining **what a button does vs. a clickable link and when to use each**, so affordances aren't arbitrary. Ben agreed there needs to be a UX "bible" for it. → **O15-5**.
- Ben noted he's spending most of his time **under the hood** (database, business logic, job handling) and likes how clean that is; the alignment/standardization items are acknowledged as **valid UI-cleanup work**, just sequenced after function.

### 3.3 Packages removal — close-out summary

- Ben's summary: **packages are gone**; EDMs and RDMs sit on the submission; **notes added**; **good navigation** between submissions and their EDMs/RDMs — "things are a lot simpler now… line up a lot more with the way the team currently does business." He's "really happy to have gotten rid of that." Cheryl: "I agree. Looks good." (Consistent with `13` §1 / `14` §3.2; the grouped country/state/peril view and the Data Bridge whitelisting item from `14` O14-5 were not revisited.)

---

## 4. Grouping & event-rate schemes — hurricane-scoped (D7)

Cheryl raised this proactively so it wouldn't be forgotten before Ben builds grouping (none is built yet). Maps onto `irp_event_rate_scheme` (§10) and the planned grouping flow (`sequence_diagrams/planned/.../grouping.md`, `.../group_results.md`).

### 4.1 When Risk Modeler lets you choose a rate scheme at grouping

- **Matching schemes → no choice at group time.** If all analyses being grouped share the **same event rate set**, Risk Modeler does **not** offer a rate-scheme choice during grouping; you must change it **before or after** grouping — never as part of the grouping step.
- **Mixed schemes → forced choice.** If the grouped analyses have a **mix** of rate schemes, Risk Modeler makes you pick from the schemes **appropriate for that peril/region**, set at grouping time. So the choice appears "when they have to [offer it], but not if they don't have to."

### 4.2 The "Convert event rate and loss" mechanism

- Changing rates **does not require a rerun**: the Risk Modeler option **Convert event rate and loss** makes a **copy** of the analysis (keeps the original), **appends "_event"** to the name, assigns the **new rates**, and the new rates are **visible in the UI**. "You don't have to rerun anything, you just assign new rates. It's really fast."
- A **rerun is only needed if the model itself changed** (new model information) — in which case you rerun and apply the updated rates anyway.

### 4.3 Scope, frequency, and the API-logic follow-up

- **Driver:** event rates being updated to something **more current** than a broker provided — canonically bumping older **2023 stochastic** wind rate sets to **2025** with no change to the loss-adjustment scheme. The selected object can be **an analysis or a group**.
- **Scope = hurricane.** Ben: "hurricane is the only scenario where this becomes an issue." Cheryl only uses event-rate updates for **hurricane** models; Wendy allowed earthquake (time-independent vs. time-dependent) could in theory need it but "we don't do that very often at all." Conclusion for the Workbench: **focus on hurricane — "the 80-20 rule for sure."** Frequency: "not uncommon," though not every time.
- **Follow-up:** Ben **owes Cheryl a clear write-up** of how he's handling the event-rate-scheme scenario on the **technical side (submitting jobs via API)**, so they can review how it maps to Risk Modeler's Convert-event-rate-and-loss behavior. Grouping design to be aligned **later in the week (~Thursday 8/20)**. → **O15-6** (build grouping, hurricane-scoped), **O15-7** (Ben's technical write-up).

---

## 5. Carried-forward (not re-decided this session)

- **Suites-first build** (`14` D9) and the **templates/suites** vocabulary/administration — the main thread; picked up and advanced the next session (`16`, 8/18). Not the focus 8/17.
- **HD hazard lookup / auto-duplicate workflow** — still **post-MVP "version 2.0"** (`14` D7/§2.3); DLM-only for now.
- **Multi-peril breakout in-place option filtering** — still the sign-off blocker from `13` **O12-1** (`14` §4); no update. Ben is deprioritizing it in favor of GeoHaz/analysis for this stretch.
- **Grouped country/state/peril view + Data Bridge whitelisting** (`14` **O14-5**) — not revisited 8/17.
- **RiskLink GeoHaz / rate-scheme fields blank on export** (`14` **O14-6**) — still open; gates whether a blank hazard-version column can be fully trusted (§1.2).

---

## 6. Open questions & follow-ups

- **O15-1** — **Verify the "most recent hazard lookup" UI-refresh fix holds through merge.** Details weren't populating after a Workbench run; fixed live, DLM hazard lookup considered done for v1 (§1.1). *Ben.*
- **O15-2** — **Jobs query-by-entity limit.** Risk Modeler jobs can't be queried by the entity they ran against, so there's no reliable "latest external hazard lookup." Accepted the footnote for MVP; revisit the "creative" workaround only if worthwhile and if Moody's answers `14` **O14-6**. Reconcile against the `irp_job`/`rwb_job` query surface (§8). (§1.2) *Ben / Cheryl.*
- **O15-3** — **RDM notes + navigation parity.** Confirm the data model stores **RDM notes** alongside EDM notes (§6) and that RDM detail carries the submission breadcrumb / cross-entity nav; add the RDM equivalent if only EDM is modeled. (§2.2) *Ben — reconcile `DATA_MODEL.md` §6/§7.*
- **O15-4** — **Table standardization (in the current PR).** Fixed-width, aligned columns (status capped, notes variable); align EDM vs. RDM note columns; unify table styling across the page before merge, as a pattern for all tables. (§3.1) *Ben.*
- **O15-5** — **UI-polish backlog + button/link consistency guide.** White space, the "CRM ID" section, Moody's data tab ordering, "EDM library" naming, and a shared definition of button vs. clickable link (incl. the view/hide-notes affordance). After an end-to-end workflow exists. (§2.3, §3.2) *Ben (later).*
- **O15-6** — **Build grouping, hurricane-scoped.** Implement event-rate-scheme change on grouping per Risk Modeler's Convert-event-rate-and-loss behavior (copy + "_event" + new rates, no rerun unless the model changed); scope to hurricane (80-20). Align design ~Thursday 8/20. Maps to `irp_event_rate_scheme.peril_code`/`model_region_code` (§10) and the planned grouping flow. (§4) *Ben + Cheryl.*
- **O15-7** — **Ben's technical write-up of the event-rate-scheme handling** (API job submission) for Cheryl to review against Risk Modeler behavior. (§4.3) *Ben → Cheryl.*
- **O15-8** — **Results / data export design** — tabled to the latter half of the week; build likely next week. (§0, D8) *Ben + CIC.*

**Deferred (agreed, off critical path):** query-by-entity workaround for the latest external hazard lookup (unless `14` O14-6 unblocks it) → O15-2; broader UI polish + button/link guide → post-end-to-end (O15-5); HD hazard lookup → post-MVP (`14` D7).

**Next session:** **Tuesday, August 18, 2026** — Ben + Cheryl, ~30 min (Ben ended 8/17 early to build ahead of it); this is written up as `16`. No session **Wednesday 8/19** (Ben unavailable). **Thursday 8/20** — **Cheryl only** (Wendy's schedule full), the likely checkpoint to align on grouping. Week goal: analysis **templates + suites** (definition + execution) and, if possible, **grouping** to a demoable state; **results export** later in the week. Ben's read on 8/17: "very productive."
