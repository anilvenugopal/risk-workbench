# IRP Workbench — Design Notes: Suites-First Re-sequencing, Templates & Suites, GeoHaz DLM One-Click / HD Deferred, EDM Notes Finalized

**Source:** Design session, August 14, 2026 (~76 min) — Ben Bailey presenting (PremiumIQ); Wendy Hayes, Cheryl TeHennepe (CIC). Cross-checked against the full transcript. Ninth session of the series (continues Aug 4/5/6/7/10/11/12/13). Ben closed out the packages-removal / submission-page work, settled GeoHaz hazard-lookup for DLM, and — the load-bearing outcome — **re-sequenced the build to do analysis suites before individual analysis execution** after CIC described how they actually think about running analyses.
**Status:** Working design notes. **DLM one-click hazard lookup**, the **HD-hazard deferral to post-MVP**, the **jobs-sourced hazard-version column**, the **EDM-notes finalization** (250 char, edit-in-place, collapsible detail), and the **suites-first re-sequencing** are agreed as direction. The **template/suite vocabulary** (template = one analysis config; suite = set of templates) is confirmed and maps onto the existing `DATA_MODEL.md` §7 model, but **suite administration/seeding, CSV-Excel export-import, and promoting region to a selection axis are new reconciliation items**. Several suite specifics (default settings per region, LOB handling, exact admin surface) are Cheryl/Wendy take-backs for Monday. Extends `07` (analysis execution, suites, currency, accumulation — the origin of the suite concept), `13` (packages removal, GeoHaz first pass, EDM notes), `10` (GeoHaz display).
**Related:** `07_analysis_execution_geohaz_currency_accumulation.md` (§1 hazard-lookup defaults, §2 running analyses, §3 Analysis Suite concept, §2.3 auto-naming), `13_packages_removal_breakouts_edm_notes_geohaz.md` (§1 packages removed, §3.3 EDM notes, §5 GeoHaz first pass, O12-3 HD-overwrite), `10_edm_summary_submissions_geohaz_currency.md` (§2 GeoHaz display, no on-screen version stamp), `06_exposure_modification_subportfolios.md` (breakout blocker carried forward), `../DATA_MODEL.md` (§7 `analysis_template` / `template_suite` / `template_suite_item` / `analysis_template_tag`; §6 EDM notes; §13 seed checklist), `../PRD.md` (§11, §14 analysis execution), `../FUNCTIONAL_REQUIREMENTS.md` (§2 EDM/submission, analysis-execution section), `../../../../CIC/minutes/Risk_Modeler_Interface_Design_Minutes_8-14-26.md`, `../../../../CIC/transcripts/Risk Modeler Interface _ Design 81426.vtt`

> Decision IDs (**D1**–**D14**) below refer to the tables in the 8/14 minutes. Open-item IDs are **O14-n**. GeoHaz open item **O12-3** (does HD overwrite DLM) from the 8/13 notes is effectively resolved-by-deferral here — see §2.3.

---

## 0. TL;DR

Closing pass on packages/submissions and GeoHaz, then a first substantive pass at analysis execution that changed the build order. Load-bearing, product-affecting outcomes:

- **Build sequence reversed: SUITES FIRST (D9).** After CIC described working in outcomes ("pick these portfolios, run the US suite"), Ben concluded "my development timeline is wrong — let's do suites first," feasible because he's only at the metadata stage. Individual analysis execution becomes the substrate under suites rather than the first deliverable. → affects implementation order, not the §7 schema.
- **Templates vs. suites vocabulary locked (D10).** A **template** = analysis/model profile + output profile + event rate (auto-populated) + currency (+ optional settings) = one Analysis Builder row = one `analysis_template`. A **suite** = an ordered set of templates = `template_suite` + `template_suite_item`. Suites are defined primarily by **region + output level**.
- **Suites are PREDEFINED / hard-coded, not freeform user-built (D11).** Cheryl: "we want them hard-coded" — predefined suites are how CIC enforces consistent settings. Ship a starter set + an **administration surface** (models/countries change). This needs a **seed/admin mechanism** (not in the §13 seed checklist) and a **CSV/Excel export-import** to move suites from Ben's env into CIC's — both new. → O14-1, O14-2.
- **Suites can MIX model types (D14).** US wildfire is HD-only while most US perils are DLM; Japan has both → "Japan DLM suite" and "Japan HD suite" coexist. Validates `event_rate_scheme_name` being **required for DLM, optional for HD** in `analysis_template`.
- **Run-a-suite = hit-go-on-default + optional expand-to-deselect perils (D13).** Default path is one click, no row inspection; an expandable list lets the analyst deselect perils (e.g. flood not covered by treaty) and still auto-queue the rest. **Treaties** are part of the run flow. Peril mismatches are **expected, not errors** (no loss = no charge) but must be **surfaced with a reason** (D14).
- **GeoHaz DLM = one-click, no modal (D4).** Latest version (25), both perils (EQ + WS), override user-defined values, skip-previous as agreed; runs the same every time. **Hard-block** submits incompatible with the EDM's **data version** (D5).
- **HD hazard lookup DEFERRED to "version 2.0" / post-MVP (D7).** The auto-duplicate-portfolio idea is liked but HD isn't in CIC's current workflow and isn't required except inland flood — not enough certainty to build now. For MVP, HD hazard runs in Risk Modeler and syncs back. Data duplication is a user **choice**, never automatic (D8).
- **"Hazard looked up" column → hazard-version tag queried live from jobs (D6)**, Workbench-submitted AND user-submitted, so it can't drift. Blank = no re-hazard.
- **EDM notes finalized (D1, D2):** edit **in place** in the submission table, **no rich-text formatting**, EDM-detail notes section **collapsible/collapsed by default**, cap **250 char** (supersedes the "~255" figure in `13` §3.3).

---

## 1. Analysis execution → templates & suites (the re-sequencing)

Extends `07` §2–§3, where the **Analysis Suite** concept originated (a pre-configured set of the "big three" — model/analysis profile, output profile, event-rate scheme — to solve the global-book pain of setting up 50–150+ combinations one at a time). This session turned that concept into the **next thing to build** and pinned down its shape.

### 1.1 Why the order changed (D9)

- Ben had planned: build individual analysis execution first, as the foundation, then suites on top. He was still at the **metadata stage** (viewing model/output/accumulation profiles + currency schemes).
- CIC reframed the problem as one of translation. Cheryl: she thinks in outcomes ("US output by county, flood") and today must "explode that thought" into model profiles, perils, regions, and output selections by hand — the Workbench's opportunity is to make the **thought the top level** and let the product do the exploding. Wendy: "pick these five portfolios, pick these three treaties, run the Canada suite" — one step where RiskLink is twenty.
- Ben's response: "my development timeline is wrong. Let's do suites first." → **build order flips**; individual execution becomes the substrate. No `DATA_MODEL.md` §7 change, but it re-prioritizes `template_suite` / `template_suite_item` work ahead of standalone submission.

### 1.2 Template vs. suite — vocabulary and mapping (D10)

| Concept | This session's definition | Data model (§7) |
|---|---|---|
| **Template** | One analysis definition: analysis/model profile + output profile + event rate (auto-populated) + currency scheme, plus optional additional settings. "One row in Analysis Builder." | `analysis_template` — `analysis_profile_name`, `output_profile_name`, `event_rate_scheme_name`, `currency_code`, `franchise_deductible`, `min_loss_threshold`, `num_max_loss_event`. |
| **Suite** | An ordered set of templates. Defined **primarily by region + output level**; other settings standardized within. | `template_suite` (name, e.g. "Global 2026 Q1") + `template_suite_item` (suite_id, template_id, `position`, `portfolio_name_override`). |
| **Defining axes** | **Region** and **output level** are the two big choices; the rest is standardized. LOB (property / auto / work comp) is a further axis carrying different settings. | `region_label` + `output_profile_name`; LOB via `analysis_template_tag` and/or naming convention. |
| **Treaties** | Part of how a suite runs — "don't forget treaties." | `treaty_name_pattern` (glob/regex, auto-selects treaties at submit time). |
| **Auto-naming** | Pre-populate the analysis name from a naming convention (Ben's first Analysis-Builder improvement). Continues `07` §2.3. | `auto_name_pattern` (Jinja2). |

- **Reconciliation flag — region as a *selection* axis.** §7 currently comments `region_label` as "display metadata; used in auto-naming." This session treats region as one of the **two defining/selection axes** of a suite ("what region are you running"). Consider whether region needs to be a first-class selection field rather than display-only. → O14-3.

### 1.3 Predefined / hard-coded suites + administration (D11)

- Cheryl: "I think we want them hard-coded." Predefined suites are how CIC controls consistency — "are you running the US defaults with our settings?" Users are told to **use what's available**; only the exception path drops to a long list / Risk Modeler.
- The §7 model already makes suites **global** (visible to all; `created_by` = authorship only), which fits. What's **new / not yet modeled**:
  - A **seed or admin-maintained set** of standard suites. `template_suite` is **not** in the §13 kind-table seed checklist — MVP needs either seeded rows or an admin surface (Wendy: "you create the administrative page… or we can do it"). Ben will build suite administration **comprehensively, then pare down**. → O14-1.
  - A **CSV/Excel export + import** for suites/profiles, because there's no way to move a suite Ben builds in his environment into CIC's — otherwise CIC rebuilds by hand. Ben offered to build export + import; Cheryl confirmed "you could do that for profiles." → O14-2.
- **Starter scope (Wendy):** a few simple, testable suites — US, Canada, US+Canada, global — ~10 profiles each, not 100. The heavy country-by-country setup is CIC's job, not Ben's. Cheryl to draft the **US/Canada default-settings list** to keep Ben's build aligned. → O14-4.

### 1.4 Running a suite — default-first, expandable, failure-tolerant (D13, D14)

- **Default path is one click:** select portfolios (+ treaties), pick the suite, hit go — no need to inspect the constituent rows. Wendy: "if I pick the US default suite, I don't need to see all the rows… I'm trying to go fast."
- **Optional expand-to-deselect:** expand the suite to a peril/profile list and deselect what you don't want (e.g. flood not covered by the treaty), still auto-queuing the rest. Ben: "that's a great idea" — sees the bottom of the run panel expanding into a list.
- **Mixed model types in one suite (D14):** a suite may contain DLM, HD, and accumulation templates together (US wildfire HD-only alongside DLM perils; Japan DLM vs HD suites). Reinforces `event_rate_scheme_name` nullable-for-HD in `analysis_template`, and keeps the `07` §5 guidance (DLM vs accumulation often separate suites) as a *convention*, not a hard rule.
- **Peril/portfolio mismatch is expected, not an error (D14):** running a broad suite against data lacking a peril fails those sub-analyses ("no locations match the criteria") and generates no loss → no charge. CIC strongly prefers "just run it all, deal with the failures at the end" over interrogating a large database first. **But** failures must be **surfaced with a reason** (job summary tells you the peril wasn't present) — not silently ignored; Ben: "surfacing the errors is important… I'm just not going to freak out that a job failed." Ties to `irp_job` / `rwb_job` summary reporting (§8).
- **Accumulation, model/output/currency metadata:** confirmed **in MVP** — model profiles, output profiles, accumulation profiles, currency schemes are **viewed in the Workbench, created/edited in Risk Modeler, synced back** (same pattern as EDM data; consistent with `07` §2.1 "selected, not owned"). Event rates are metadata you **select**, not create (CIC won't author custom event rates). → dedicated "analysis metadata" screen.

---

## 2. GeoHaz / hazard lookup — DLM settled, HD deferred

Extends `13` §5 (first GeoHaz pass; guard-to-data-version, trim to baseline, prefill defaults, DLM-vs-HD storage open) and `07` §1 (hazard-lookup-only, never re-geocode). This session **settles the DLM run** and **defers HD**.

### 2.1 DLM one-click hazard lookup (D4, D5)

- CIC always runs the **latest DLM (v25)** and never goes backward, so the version picker and the Risk Modeler-style modal add no value. Collapse to a **single button** that runs the same every time: latest data version, **earthquake + windstorm**, **override user-defined values**, **do not skip** previously-looked-up locations. (Matches the `13` §5 / `07` §1.2 default set.)
- **Hard-block incompatible submits (D5):** choosing a hazard/geocode version out of sync with the EDM's **data version** throws a warning; Ben refuses it rather than passing it through. The **data version** governs (schema version updates to 25 on upload regardless of the source EDM's age). Backward one-off cases are handled directly in Risk Modeler.

### 2.2 "Hazard looked up" column from jobs (D6)

- Replace the current Workbench-only yes/no tag with the actual **hazard-version tag**, sourced by **querying jobs — both Workbench-submitted and user-submitted** — so the column can't drift from reality. Version metadata can likely be pulled **live** rather than through the heavier sync path. Blank field = no re-hazard was done. Cheryl: "that'd be great."
- Caveat carried from `13`/`10`: incoming RiskLink data shows nothing for GeoHaz / rate scheme in the UI — the team believes those fields simply **aren't populated on export**; Cheryl still chasing confirmation. So a blank hazard version ≠ "no hazard retrieval ever happened." → O14-6.

### 2.3 HD hazard lookup — deferred to post-MVP (D7, D8) *(resolves O12-3 by deferral)*

- HD models do hazard lookup **inside the analysis**, except **inland flood** — the one place a standalone HD hazard lookup matters. Ben proposed a one-step HD flow that **auto-duplicates** the selected portfolio (append "HD" to the name) and runs HD hazard against the copy.
- The group **liked the idea but deferred it**: HD isn't in CIC's current workflow, HD versioning is variable (see §2.4), HD hazard isn't required except for flood, and auto-duplicating every portfolio would "blow up the information" (Cheryl). **Data duplication must be a user choice, never automatic (D8)** — wanted mainly for model-transition comparisons and US inland flood.
- **MVP behavior:** HD hazard is run in **Risk Modeler** and synced back; the Workbench **surfaces the resulting HD portfolio and its hazard details**. The `07` O7-1 question (must HD hazard retrieval be run ahead of time?) is answered "not for MVP scope."
- On the `13` O12-3 storage question: Ben's research says hazard values are **not overwritten unless "override user-defined hazard values" is checked** (running DLM 23 then 25 keeps both). Because HD is deferred, this no longer gates MVP — recorded, but off the critical path. → O12-3 closed-by-deferral.

### 2.4 Moody's HD versioning (context, verify)

- Cheryl characterized Moody's HD versioning as **cumulative and not forward-compatible**: selecting a version when creating an HD profile changes which peril models appear. Example — Japan modeled at HD 2.0, New Zealand EQ introduced at HD 3.0 — at HD 3.0 you see New Zealand but must drop back to 2.0 for Japan; users must know when each model was last updated. This is why hazard-layer options shifted as Ben switched HD versions. Wendy wants it **verified**. → O14-7. (Reinforces why HD run-control is deferred.)

---

## 3. Packages/submission close-out & EDM notes finalized

Closes the packages-removal thread from `13` §1/§3 ("merge these changes and be done with them for the time being").

### 3.1 EDM notes — final shape (D1, D2)

- **Edit in place** in the submission table (both Wendy and Cheryl said yes); **no rich-text formatting** preserved (Wendy: not necessary). Both edit surfaces (table + EDM detail) coexist.
- **EDM-detail notes section becomes collapsible, collapsed by default,** and compact — notes are most useful in the submission list (to tell EDMs apart when choosing), less so once inside an EDM, and shouldn't force scrolling past them to reach portfolio info. Ideally fits existing white space rather than a full horizontal band.
- **Cap = 250 characters** (supersedes the "~255" in `13` §3.3). Stored only in the Workbench (§6 EDM notes field; not written to Risk Modeler).

### 3.2 Submission page (built / closing out)

- **RiskLink link renders only once populated (D3)** — "you can't open a link that doesn't exist." Third of the packages-era submission changes; packages now fully removed and merged.
- **Grouped country/state/peril view built** (LOB → country → that country's states → perils underneath) to replace flat state/country lists — but **couldn't be demoed live**: a **Data Bridge whitelisting issue** blocked the summary information. Ben to check next week. → O14-5.
- Sorting on the EDM/RDM lists (name/status/portfolio count) is in; consistent with `13` §3.1 (sort, not search).

---

## 4. Carried-forward items (not re-decided this session)

- **Multi-peril breakout in-place option filtering** — still the `13` O12-1 sign-off blocker; Ben "no update," still working it. He plans to **deprioritize breakouts for Monday** in favor of GeoHaz and analysis (can't do all three at once). Still a sign-off blocker — track it. See `13` §2.2 / `06`.
- **Event-rate-scheme visibility on import** (`03`/`13` open) — resurfaced: no GeoHaz/rate-scheme info shows for incoming RiskLink data; same investigation. → folded into O14-6.

---

## 5. Open questions & follow-ups

- **O14-1** — **Suite administration + seeding.** Build the suite-administration surface (comprehensive, then pare down); decide predefined-seed vs. admin-page split. `template_suite` is not in the §13 seed checklist — add a seed/admin mechanism for standard suites (§1.3). *Ben (for Monday 8/17).*
- **O14-2** — **CSV/Excel export + import for suites/profiles** so suites built in Ben's environment move into CIC's rather than being rebuilt by hand (§1.3). *Ben.*
- **O14-3** — **Promote `region_label` from display-metadata to a selection axis** (or confirm current modeling is sufficient), since region is one of the two defining choices of a suite (§1.2). *Ben — reconcile `DATA_MODEL.md` §7.*
- **O14-4** — **US/Canada default-settings list** to align Ben's suite build with CIC defaults; start from simple US / Canada / US+Canada / global test suites (~10 profiles each) (§1.3). *Cheryl → Ben, for Monday.*
- **O14-5** — **Grouped country/state/peril view** — confirm end-to-end once the **Data Bridge whitelisting** issue is resolved (blocked the live demo) (§3.2). *Ben.*
- **O14-6** — **Why does incoming RiskLink data show nothing for GeoHaz / rate scheme?** Team believes the fields aren't populated on export; Cheryl chasing confirmation. Drives whether a blank hazard-version column can be trusted (§2.2, §2.3). *Cheryl / Ben.*
- **O14-7** — **Verify Moody's HD version ↔ peril-model associations** (cumulative, non-forward-compatible; Japan 2.0 vs. New Zealand 3.0). Based on profile-creation behavior; Wendy wants it verified (§2.4). *Cheryl / Wendy.*
- **O14-8** — **LOB + treaties in suites** — LOB (property/auto/work comp) carries different settings; handle via `analysis_template_tag` / naming convention. Ensure `treaty_name_pattern` covers the run-time treaty selection (§1.2, §1.4). *Ben.*
- **O14-9** — **Auto-name analyses** from a naming convention (`auto_name_pattern`); continues `07` O7-3 (§1.2). *Ben.*

**Deferred (agreed, off critical path):** HD hazard lookup / auto-duplicate workflow → post-MVP "version 2.0" (§2.3, D7); breakout experience polish → September (`13` O12-5).

**Resolved-by-deferral:** `13` **O12-3** (HD-overwrites-DLM storage) — moot for MVP since HD hazard is deferred (§2.3).
