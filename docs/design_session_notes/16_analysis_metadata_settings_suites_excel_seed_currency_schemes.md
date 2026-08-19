# IRP Workbench — Design Notes: Analysis Metadata View, Templates→"Analysis Settings" & Suites Build, Currency Schemes, Excel Seed / Import-Export

**Source:** Design session, August 18, 2026 (~31 min) — Ben Bailey presenting (PremiumIQ); Cheryl TeHennepe (CIC). *(Ross appeared briefly on video but did not participate.)* Cross-checked against the full transcript. Short working session that executes on the **suites-first** re-sequencing agreed 8/14: Ben demoed the new **analysis-metadata view** in the Workbench and a first working pass at building **templates and suites**, and took Cheryl's input on both. Ben had a hard stop and ended early.
**Status:** Working design notes. Direction agreed: **analysis metadata surfaced in the Workbench (view-only, synced from Risk Modeler)**, moving toward **live/auto-refresh + link-outs**; **show currency *schemes*, not individual currencies**; **rename "template" → "analysis settings"**; **tabbed, admin-only** templates/suites view; **Excel workbook as the seed + bulk-management path** with **export/import (diff-apply)** and **dropdown/data-validation** lists; **UI filters event rate schemes by the chosen model profile (peril + region)** while **import validation is relaxed**; and the **"treaty name pattern" template field is dropped**. Several items are **new reconciliation work against `DATA_MODEL.md`** — currency *schemes* are not modeled (only `irp_currency`), the "analysis settings" rename touches §7 vocabulary, `treaty_name_pattern` removal contradicts §7/O14-8, and the Excel import/export design fleshes out **O14-2** (still not in the §13 seed checklist). Extends `14` (suites-first, template/suite vocabulary, metadata "viewed in Workbench, created in Risk Modeler"), `07` (Analysis Suite origin; currency; auto-naming), `10` (GeoHaz display / no version stamp).
**Related:** `14_analysis_suites_first_geohaz_dlm_hazard_edm_notes.md` (§1 template/suite vocabulary D10, predefined suites D11, run-a-suite D13, metadata "selected not owned"; O14-1 suite admin, O14-2 CSV/Excel export-import, O14-3 region-as-axis, O14-8 LOB+treaties, O14-9 auto-name), `07_analysis_execution_geohaz_currency_accumulation.md` (§2 running analyses, §2.1 metadata selected-not-owned, §2.3 auto-naming, §3 Analysis Suite concept), `10_edm_summary_submissions_geohaz_currency.md` (metadata sync display), `../DATA_MODEL.md` (§7 `analysis_template` / `template_suite` / `template_suite_item` / `analysis_template_tag`; §10 IRP reference cache `irp_model_profile` / `irp_output_profile` / `irp_event_rate_scheme` / `irp_currency` — "Sync IRP Metadata" action; §13 kind-table seed checklist), `../PRD.md` (§11/§14 analysis execution), `../FUNCTIONAL_REQUIREMENTS.md` (analysis-execution section), `../../../../CIC/minutes/Risk_Modeler_Interface_Design_Minutes_8-18-26.md`, `../../../../CIC/transcripts/Risk Modeler Interface _ Design 8-18-26.vtt`

> Decision IDs (**D1**–**D11**) below refer to the tables in the 8/18 minutes. Open-item IDs are **O15-n**. This session **advances** several 8/14 open items — O14-1 (suite admin), O14-2 (CSV/Excel export-import), O14-8 (treaties) — noted inline.
> **Numbering note:** the 8/17 session has no design note yet (there is a `Risk Modeler Interface _ Design 8-17-26.vtt` transcript but no `15_`/minutes). This 8/18 note is filed as `15`; if 8/17 is written up later it slots before this one (renumber or file as `14a`).

---

## 0. TL;DR

Short session executing the suites-first plan. Product-affecting outcomes:

- **Analysis metadata now has a home in the Workbench (D1).** A single view of model profiles, output profiles, event rate schemes, and currencies, pulled from Risk Modeler so they're in one place at template-build and execution time instead of scattered across Risk Modeler tabs + reference data. **View-only** (create in Risk Modeler), **manual sync** like EDM data. → maps onto `DATA_MODEL.md` §10 IRP reference cache + the "Sync IRP Metadata" action.
- **Lean toward LIVE / auto-refresh + link-outs (D2).** Because the metadata sync is fast and the tables are small (Ben: won't be 3,400 profiles), Ben will likely pull it **live / auto-refreshing** rather than requiring a remembered manual sync, and add **buttons out to Risk Modeler** (e.g. to where you create a currency scheme). → reconciles against §10's "populated by Sync IRP Metadata; app never writes otherwise." → O15-1.
- **Show currency *SCHEMES*, not individual currencies (D3).** CIC works in currency **schemes**, not currencies. A currency (e.g. EUR) can appear in several schemes with different FX rates, so the scheme is the selection unit; only ~2–5 schemes will ever exist. Drop the standalone currency list; order the view event-rate-schemes → currency-schemes. → **not modeled today**: §10 has `irp_currency` only, no `irp_currency_scheme`; `analysis_template.currency_code` likely needs a scheme reference. → O15-2 (data-model reconciliation).
- **Rename "template" → "analysis settings" (D5).** Cheryl: "it's really what we're doing." A **template / analysis settings** = model/analysis profile + output profile + event rate scheme + currency + background settings = one Analysis Builder row. A **suite** = a group of those. Keep the two concepts, collapse-to-one later only if overhead demands (D4). → §7 vocabulary reconciliation (the `14` note "locked" the word *template*). → O15-3.
- **Templates/suites live in a tabbed, tabular, ADMIN-ONLY surface (D6).** Two tabs (analysis settings + suites), each a table for sorting/searching; creation gated to admins. → the admin creation surface behind 8/14 **O14-1**.
- **Excel is the seed + bulk path (D7, D8).** Seed the DB on deploy from an **Excel workbook** (templates sheet + suites sheet), and build **export/import** so a populated Excel can be edited and re-imported with **only the diff applied**. → this is the concrete design for 8/14 **O14-2**; `analysis_template`/`template_suite` are still **not** in the §13 seed checklist. → O15-4.
- **Excel uses dropdown / data-validation lists (D9).** Reference-data sheets (model profiles, output profiles, event rate schemes) drive type-ahead + dropdowns so names can't be fat-fingered; a name with no match signals it's wrong.
- **UI filters event rate schemes by the chosen model profile; import validation relaxed (D10).** Selecting a model profile narrows event rate schemes to those valid for it (by **peril + region**) — mirrors Risk Modeler and is directly supported by `irp_event_rate_scheme.peril_code` / `model_region_code` (§10). On Excel **import**, drop the hard incompatibility error (Cheryl not keen; invalid picks are rare; admin task run ~yearly). How to narrow the long list *inside* Excel is left open. → O15-5.
- **"Treaty name pattern" template field is DROPPED (D11).** Ben: "the agents like to suggest fun features I don't think we want… forget treaty name pattern." Templates still carry **tags** (flow onto analysis results). → **contradicts** `DATA_MODEL.md` §7 `analysis_template.treaty_name_pattern` and the 8/14 **O14-8** treaty approach — treaties handled at run time, not as a stored glob. → O15-6.

---

## 1. Analysis-metadata view (D1, D2, D3)

Executes the 8/14 commitment (`14` §1.4) that model/output/accumulation profiles + currency schemes are **viewed in the Workbench, created/edited in Risk Modeler, synced back** — the same "selected, not owned" pattern as `07` §2.1. This session built the first version and refined it.

### 1.1 What it is (D1)

- A single Workbench screen listing **model profiles, output profiles, event rate schemes, and currencies**, consolidated so they're available at template-creation and analysis-execution time rather than scattered across Risk Modeler tabs and separate reference data. Ben built it unprompted ("I don't think you've explicitly asked for this").
- **View-only** — creation/editing stays in Risk Modeler. Data is **materialized in the Workbench DB** (not pulled straight from Risk Modeler to render the page).
- **Maps onto `DATA_MODEL.md` §10 (IRP reference cache):** `irp_model_profile`, `irp_output_profile`, `irp_event_rate_scheme`, `irp_currency` — "Populated by the *Sync IRP Metadata* action; the app never writes to these tables otherwise." The screen is the read surface over that cache.
- Cheryl connected the layout to RiskLink, where **output is not separate from the model profile** — one profile organized by peril (all hurricane together, all windstorm together). Informational; the Workbench keeps model and output profiles as distinct metadata (per §10 / §7 `analysis_profile_name` + `output_profile_name`).
- Current search is basic (one column at a time, not multi-column) and needs enhancement. → O15-7.

### 1.2 Manual sync vs. live / auto-refresh (D2)

- Today it runs a **manual sync** like the EDM data (the §10 "Sync IRP Metadata" action). Consequence Ben flagged: a newly-created Risk Modeler profile must be synced before it can be used in a suite — a multi-step process. Cheryl is fine with a sync button because suites will cover most cases anyway.
- **But** the sync proved fast and the tables are small (won't be thousands of rows), so Ben leans toward making the page **live / auto-refreshing** so there's nothing to remember to sync. → **reconciliation against §10**, which currently says the app never writes these tables outside the Sync action and implies a batch sync, not a live pull. Decide: keep manual sync, add auto-refresh, or pull live. → **O15-1**.
- Add **link-outs to Risk Modeler** — a button to the exact place you'd create the object (Cheryl's example: where you create a currency scheme). → O15-1.

### 1.3 Currency schemes, not currencies (D3)

- Ben had been pulling **individual currencies** (`irp_currency`, §10). Cheryl confirmed CIC creates/uses **currency schemes**, not individual currencies.
- **Why the scheme is the unit:** an analysis is tied to a scheme that's already set; you pull the currency *from within* that scheme, and the same currency (e.g. EUR) can appear in **multiple schemes with different FX rates** — "you have to select your scheme first so that it knows where." A sample scheme ("Daniel Test," code DT) had no currencies attached, illustrating that a scheme is a distinct object from its member currencies.
- **Decision:** replace "currencies" with **currency schemes** in the view; drop the standalone currencies list ("I don't think we need to see currencies" — Cheryl). Order becomes **event rate schemes → currency schemes**. Only ~2–5 schemes will ever exist (a handful of companies whose FX rates CIC matches).
- **Data-model reconciliation (new):** §10 models `irp_currency` (ISO-4217 natural key) but **no `irp_currency_scheme`** table, and §7 `analysis_template.currency_code` stores a bare currency. Selecting-scheme-then-currency implies (a) a new `irp_currency_scheme` reference-cache table synced from Risk Modeler, and (b) `analysis_template` referencing the **scheme** (plus possibly the currency within it), because the FX rate is scheme-dependent. → **O15-2**.

---

## 2. Templates → "analysis settings" & suites (D4, D5, D6, D11)

Executes the 8/14 vocabulary (`14` §1.2, D10) and predefined-suite direction (`14` §1.3, D11 → O14-1). Maps onto `DATA_MODEL.md` §7.

### 2.1 Vocabulary and the rename (D4, D5)

| Concept | 8/18 definition | Data model (§7) |
|---|---|---|
| **Analysis settings** *(was "template")* | One analysis definition = model/analysis profile + output profile + event rate scheme + currency (scheme), plus background settings: min loss threshold, number of max loss events, apply-franchise-deductible, construction/occupancy handling. "One row in Analysis Builder." | `analysis_template` — `analysis_profile_name`, `output_profile_name`, `event_rate_scheme_name`, `currency_code`, `franchise_deductible`, `min_loss_threshold`, `num_max_loss_event`. |
| **Suite** | A group of analysis-settings rows, run together; **order doesn't matter** ("it's just a group… that we can run all together"). | `template_suite` + `template_suite_item` (note: §7 `template_suite_item.position` = "submission order" — reconcile with "unordered", see below). |
| **Tags** | Under-the-hood tags applied to the analysis and its **results** when run. Keep. | `analysis_template_tag` (junction) → `irp_tag` (§10). |
| **Treaty name pattern** | **Dropped** — "too much," an AI-agent-suggested feature CIC doesn't want. | `analysis_template.treaty_name_pattern` (§7) — **conflicts**; see §2.3. |

- **Rename (D5):** "template" → **"analysis settings"** (Cheryl's suggestion; reflects that it's the set of settings for one Analysis Builder row). This **supersedes the `14` note's "locked" template vocabulary** — the entity is the same (`analysis_template`), only the UI label changes, but PRD/FR/UI copy referencing "template" should be reconciled. → **O15-3**.
- **Keep two concepts (D4):** analysis settings vs. suites remain distinct logical objects; Ben will collapse to a single "suite" concept later only if the two-level model proves too much overhead. Matches §7's two-table shape (`analysis_template` + `template_suite`).
- **Ordering nuance:** Ben stated a suite is **unordered** ("it doesn't need to be in order"), while §7 `template_suite_item.position` is labelled "submission order." Minor reconciliation — keep `position` for stable display/round-trip but don't require the analyst to order. → folded into O15-4.

### 2.2 Admin-only, tabbed, tabular surface (D6)

- Creating analysis settings and suites is an **admin-only** activity (carried from the prior week; consistent with §13 `role_kind` = `admin`, `is_admin=true`).
- Present both in a **tabbed view** (like the analysis-metadata screen), each in a **table** for easier sort/search, rather than both on one page.
- The suite screen doubles as a **builder** — filter the analysis-settings list, add/remove rows, name it, save (much less configuration than building each row from scratch). This is the **administration surface** behind 8/14 **O14-1**.

### 2.3 Treaty handling reconciliation (D11)

- Ben dropped **treaty name pattern** as a stored template field. This **contradicts** `DATA_MODEL.md` §7 (`analysis_template.treaty_name_pattern`, "glob/regex to auto-select treaties at submit time") and the 8/14 **O14-8** approach that leaned on `treaty_name_pattern` for run-time treaty selection.
- Reconciliation: treaties are **still part of running a suite** (8/14 D13 — "don't forget treaties"), but the mechanism shifts from a **stored glob on the template** to **explicit treaty selection at run time** (pick portfolios + treaties, then run). Remove/deprecate `treaty_name_pattern` from §7 and re-open O14-8 on that basis. → **O15-6**.

---

## 3. Excel seed & import/export, dropdowns, and event-rate filtering (D7, D8, D9, D10)

This is the concrete design for 8/14 **O14-2** (CSV/Excel export-import) and the seeding half of **O14-1**.

### 3.1 Excel workbook as the seed + bulk-management path (D7, D8)

- **Seed on deploy:** an **Excel workbook with two sheets** — a **templates/analysis-settings sheet** (same fields as the UI) and a **suites sheet** (suite name + which templates belong; a 5-template suite = 5 rows). Used to seed the DB on initial deployment. → the missing seed mechanism: `analysis_template` / `template_suite` are **not** in the §13 kind-table seed checklist, so this is net-new. → **O15-4**.
- **Build both paths (D8):** manual UI creation **and** Excel import/export. Rationale: forms are fine for one or two rows; Excel is easier for ~10+. Ben: "I want to build both so you can go either way."
- **Diff-apply import:** export a fully-populated Excel from the templates/suites page, add a few rows, re-import, and apply **only the difference** — not a full recreate. Cheryl: "That's very cool."
- **DLM profiles still set up manually in Risk Modeler** (Cheryl confirmed). Possible future: make the Excel machine-readable enough to **create model profiles via the API** (Ben hasn't done API model creation yet). → O15-8. Ties to the 8/14 starter-scope note (`14` §1.3, O14-4): a few simple US/Canada/global suites, ~10 profiles each; heavy country-by-country setup is CIC's job.

### 3.2 Dropdown / data-validation lists (D9)

- Cheryl asked for **dropdowns** so users "get what you needed to say every time without people fat-fingering it or having to look stuff up." Ben has built this pattern before.
- Add **reference-data sheets** (list of model profiles, output profiles, event rate schemes — the §10 cache) and pre-populate the relevant columns with those lists: **type-ahead filtering + dropdown selection**; typing a name with **no matching option** signals it's wrong (metadata not present). Cheryl wanted the dropdown caret available for when she doesn't yet know an object's name.
- **Output profiles** are part of the **analysis settings / suite**, not selected separately elsewhere (confirmed).

### 3.3 Event-rate-scheme filtering: UI vs. import (D10)

- **UI:** selecting a model profile **auto-filters event rate schemes to those applicable** to it, on the basis of **peril and region** — mirroring Risk Modeler. Ben already has this logic. **Directly supported by `irp_event_rate_scheme.peril_code` + `model_region_code` (§10).**
- **Import:** Ben had planned to **hard-error** on an incompatible event-rate-scheme / model-profile pair. Cheryl "[didn't] like that as much," so Ben will **relax the import validation**. Reasoning: invalid picks are rare (Cheryl usually knows the scheme she wants → type-ahead suffices), the list is just long/annoying (worse for earthquake, which is often defaulted, than hurricane), and this is an **administrative task run ~once a year**.
- **Open:** how to narrow the long list *inside* Excel — Cheryl floated adding **peril + region** columns as a lookup flag to cull the list to a subset; Ben to circle back. → **O15-5**.

---

## 4. Carried-forward (not re-decided this session)

- **Suite run-time behavior** (default-first, expand-to-deselect perils, failure-tolerant, mixed model types) — settled 8/14 (`14` §1.4, D13/D14); not revisited here. Still governs execution once the admin/build surface lands.
- **Region as a selection axis** (8/14 **O14-3**) — untouched; §7 `region_label` still "display metadata." Currency-scheme work (O15-2) and event-rate filtering (O15-5) both touch region, so reconcile together.
- **Multi-peril breakout in-place option filtering** — still the sign-off blocker from `13` O12-1 (`14` §4); no update this session.
- **Auto-name analyses** (8/14 **O14-9**, `07` §2.3, §7 `auto_name_pattern`) — not discussed 8/18.

---

## 5. Open questions & follow-ups

- **O15-1** — **Analysis-metadata view: manual sync vs. live/auto-refresh, + Risk Modeler link-outs.** Decide whether to keep the §10 "Sync IRP Metadata" batch action, add auto-refresh, or pull live; add link-out buttons to the Risk Modeler creation screens. Reconcile against `DATA_MODEL.md` §10 ("app never writes otherwise"). (§1.2) *Ben.*
- **O15-2** — **Model currency *schemes*.** Add an `irp_currency_scheme` reference-cache table (synced from Risk Modeler, §10) and have `analysis_template` reference the **scheme** (FX rate is scheme-dependent; a currency lives in multiple schemes). Reconcile `analysis_template.currency_code` in §7. (§1.3) *Ben — `DATA_MODEL.md` §7/§10.*
- **O15-3** — **Rename "template" → "analysis settings"** across UI copy; reconcile PRD/FR/`14`-note vocabulary (entity stays `analysis_template`). (§2.1) *Ben.*
- **O15-4** — **Excel seed + import/export design.** Two-sheet workbook (analysis settings + suites) to seed the DB on deploy (add `analysis_template`/`template_suite` seeding — not in §13 checklist); diff-apply re-import; keep `template_suite_item.position` for round-trip while treating suites as unordered for the analyst. (§3.1, §2.1) *Ben.* — advances 8/14 **O14-2**.
- **O15-5** — **Narrowing event-rate schemes inside Excel.** UI filtering by peril+region is set and import validation relaxed; open whether to add peril/region lookup columns in the Excel to cull the list. (§3.3) *Ben / Cheryl.*
- **O15-6** — **Drop `treaty_name_pattern`; move treaty selection to run time.** Remove/deprecate the §7 field; re-scope 8/14 **O14-8** so treaties are chosen when running a suite, not stored as a glob on analysis settings. (§2.3) *Ben — `DATA_MODEL.md` §7.*
- **O15-7** — **Multi-column search/filter on the analysis-metadata view** (currently one column at a time). (§1.1) *Ben.*
- **O15-8** — **API-based creation of DLM model profiles** from a machine-readable Excel (vs. manual Risk Modeler setup); API model creation not yet built/verified. (§3.1) *Ben.*

**Advances (from 8/14):** **O14-1** suite administration — admin-only tabbed/tabular builder surface defined (§2.2); **O14-2** CSV/Excel export-import — concrete two-sheet design with diff-apply + dropdowns (§3); **O14-8** treaties — re-scoped to run-time selection (O15-6).

**Next session:** Thursday, August 20, 2026 — Cheryl available, Wendy not. Ben building the manual UI path first, then Excel seed/import-export; analysis-metadata view (currency-scheme change, link-outs, live refresh) and the renamed "analysis settings" + suites builder are the near-term work. Ben's read: "on the pretty, pretty right track."
