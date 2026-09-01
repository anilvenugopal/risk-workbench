# IRP Workbench — Design Notes: Breakout "Builder" & Cascading Filters, Caribbean Geography, Peril Breakouts, Tagging/Ownership & Rate-Scheme Carry-Over

**Source:** Design session, August 11, 2026 (Ben Bailey, Wendy Hayes, Cheryl TeHennepe — all present from the start) — a demo-and-refine pass focused almost entirely on the **sub-portfolio breakout** experience, plus a long **tagging / data-management / ownership** discussion and a carry-over on the **event-rate-scheme** metadata problem. Cross-checked against the full transcript and the `risk-workbench-005` worktree as of this date. **Sixth** session of the series (continues Aug 4/5/6/7/10).
**Status:** Working design notes. **Aligned as direction:** the breakout **two-pane "builder"** layout, **cascading/dependent filters**, **quick-breakout-by-peril**, **peril-name display**, the **Caribbean (CB) country/state mapping**, **reassign-based ownership**, **API-key auth**, and **auto-tagging packages**. **Explicitly deferred:** tagging *implementation* (own design session first) and "**create-portfolio-from-all-exposures**" (merge) → future state. **Still open / needs sign-off:** the **event-rate-scheme** retrieval (Moody's follow-up) carried from `11`. Extends `11` (§2 breakouts, §3 grouping) and `06` (breakout actions, granularity cap).
**Related:** `11_submissions_search_subportfolio_breakouts_grouping.md` (§2 breakout builder/naming/lineage, §3.3 rate scheme — O11-2/O11-3/O11-5 advanced here), `06_exposure_modification_subportfolios.md` (breakout actions, geography cap), `07_analysis_execution_geohaz_currency_accumulation.md` (grouping/currency), `../DATA_MODEL.md` (`irp_portfolio`, exposure summary `breakout_values`), `../PRD.md`, `../FUNCTIONAL_REQUIREMENTS.md` (§2 exposure/breakout), `../pm/2026-08-09_spec005_followon_plan.md`, code: `app/services/breakout_service.py`, `app/templates/partials/breakout_modal.html`, `app/templates/partials/portfolio_row.html`, `app/services/analysis_service.py`, `../../../../CIC/minutes/Risk_Modeler_Interface_Design_Minutes_8-11-26.md`, `../../../../CIC/transcripts/Risk Modeler Interface _ Design 81126.vtt`

---

## 0. TL;DR

A refine pass on breakouts with no single headline feature; the product-affecting outcomes and their **code deltas** (full list in §7):

- **Breakout modal → two-pane, fixed size (D1).** Move to selection options on the **left**, the running selection as a **list** on the **right** (replacing the current pill/chip stack), and a **fixed** modal size (Ben dislikes the modal resizing with content). *Current code renders a single stacked pane (pills → checkbox lists → "picked" chips) in `breakout_modal.html`.*
- **Cascading / dependent filters (D2).** Picking one dimension (e.g. LOB) reduces the other option lists to only valid combinations and **updates per-option account counts**; invalid options are **greyed out / not selectable, not removed**. Kills impossible picks (e.g. New Zealand Hurricane, which isn't in the model) and the need for after-the-fact error text. *Not implemented — checkbox dimensions are independent today; counts are static.*
- **Quick-breakout-by-peril (D3) + peril names (D4).** Add peril as a **quick** dimension (today it's grouping-only, `_QUICK_DIMENSIONS = {lob, state, country}`, P-19) and **display the peril name, not the code**.
- **Caribbean special case (D5).** When the **RMS country code = CB**, map **CB → country** and the **island-level code → state**, via a dedicated SQL path, so both "whole Caribbean" and "single island" workflows work. *No Caribbean handling exists anywhere in `app/` today.*
- **Ownership via reassign (D7), API-key auth (D8), auto-tag packages (D9–D11) — but hold tagging implementation (D12).** Reassign is a **cosmetic** app-DB ownership change (Risk Modeler stays under the shared API-key identity); **never tag on username**; auto-tag EDMs/RDMs when a package is added and untag on removal. Tagging conventions/lifecycle need their **own** session before build. *None of reassign / tags / api-key ownership exists in the codebase yet.*
- **Merge-to-one-portfolio = future state (D13).** "Create a portfolio from all exposures" (combine several breakouts into one) is out of the 80/20 for v1. Offer low-effort **rename / delete** and an **"open in Risk Modeler"** deep link now (D14–D15).
- **Event-rate-scheme (carry-over, O11-2).** The scheme is **not exposed** on imported analyses (only on *grouped* ones / via a Data Bridge workaround). Cheryl to **contact the Moody's team**; Ben to join. *Code corroborates: `AnalysisSettings.event_rate_scheme` exists but is documented blank-on-missing.*

---

## 1. Sub-portfolio breakout "builder" (layout + interaction)

Advances `11` §2.4 (the builder prototype, O11-5). This session reviewed the prototype and set concrete UX direction.

### 1.1 Two-pane, fixed-size modal (D1 — aligned)

| Item | Decision | Detail / rationale |
|---|---|---|
| Layout | **Two panes:** selection options **left**, current selection as a **list** **right** | Replaces the current stacked pills/checkbox/"picked-chips" arrangement. Cheryl: "it'll be really easy to differentiate it, select it. You get more room for a longer list." |
| Sizing | **Fixed modal size** | Ben dislikes the modal growing/shrinking with content. Fixed footprint regardless of how many values/breakouts. |
| List content | One name; **LOB list and state list side-by-side, alpha-sorted** | Clean, scannable; carries forward `11`'s alpha-sort requirement. |

**Current code:** `app/templates/partials/breakout_modal.html` already has a live selection preview — the `bo-picked` chip row derived from ticked checkboxes (so part of "see as you build" exists) — but it's a **single vertical pane** (`bo-pills` → `bo-checks` → `bo-picked` → cart). The change is to re-lay-out into two fixed panes and swap the chip cloud for a right-hand **list**. No service change required for layout.

### 1.2 Cascading / dependent filters (D2 — aligned; **not trivial**, Ben to build)

- Selecting a value in one dimension **filters the other dimensions' option lists to only valid combinations**, and **recomputes per-option account counts** to match the narrowed set.
- Unavailable options are **greyed out / non-selectable** — **not removed** (Ben: "I don't want to get rid of options completely… I want them to be unavailable"; Cheryl: "just make them not selectable").
- **Why:** in the demo a breakout returned fewer countries than expected and a Japan-Earthquake breakout failed outright; cascading selection prevents picking combinations the data can't satisfy and removes the need for after-the-fact explanation. Ben: "not a trivial change, but… I think I can do that."

**Current code:** the checkbox dimensions in `breakout_modal.html` are **independent** — ticking a country does not filter the LOB/state lists, and `bo-check__accounts` shows the **static** per-value account count from the stored exposure summary (`breakout_service` `Coverage`/`values`). Implementing D2 needs the option lists + counts to become **conditional on the current selection** (server-recomputed from the summary on each change, or precomputed cross-tabs), plus a disabled/greyed render state for now-invalid values.

---

## 2. Caribbean country/state special case (D5 — aligned)

The most substantive **new** technical item. RMS represents the Caribbean unusually, and the breakout geography must special-case it.

| Item | Decision | Detail / rationale |
|---|---|---|
| The quirk | RMS groups **all** Caribbean islands under one **RMS country code, "CB"**, with the individual island names in a **separate** field | Only the Caribbean behaves this way. CIC's two real workflows: run the **whole** Caribbean (all ~30 islands — don't hand-pick each) **or** a **single** island (e.g. Puerto Rico). |
| Mapping | When RMS country code = **CB**: **CB → country value**, **island-level code → state value** | Dedicated SQL path for CB. Cheryl confirmed the mapping ("Yes… Correct"). Example: Puerto Rico + USVI → country = 1 (CB), states = 2 (PRI, VIR). |
| Source field | Country from `country_code` (ISO-2A/3A) **first**, coalescing to `country_rms_code` when empty (D6) | Cheryl: with `country_code` first "you've alleviated the problem" for the general case; using the ISO code instead of the RMS code would collapse (e.g.) Jamaica's regions, which they don't want — hence the CB exception layered on top. |

**Current code:** **no Caribbean / CB / rms-code handling exists** in `app/` (grep is empty outside `docs/design_session_notes/06`). Breakout geography today is: `country` from the DataBridge summary as **codes** ("US", "CA" — `portfolio_row.html`: "dbo.Address has no country-name column"), and `state` = **Admin1Code** with **Admin1Name** label (`breakout_service` `Coverage.value/label`, P-12). D5 adds a CB branch to the country/state derivation and a special SQL path; it also interacts with D2 (the CB values must flow through the cascading filter correctly).

---

## 3. Peril breakouts (D3, D4 — aligned)

| Item | Decision | Detail / rationale |
|---|---|---|
| Quick-breakout-by-peril | **Add peril as a quick dimension** | Reaffirms the prior-week point that breakouts should be peril-specific by default. Ben: "breaking into peril-specific portfolios is key." Cheryl: "I agree." Keep peril in **custom** breakouts too. |
| Peril name | **Show the peril *name*, not the code** | The custom-breakout peril column currently shows an unlabeled code; Wendy/Cheryl: "we need the name" (e.g. Earthquake). |
| Why peril matters | Analysts must keep perils **separate** | Wendy: an unfiltered EDM returns both the EQ and wind records for the same exposures — "that's just not how we run analyses" (worse for EQ + fire-following, which share a peril code). The Workbench already helps by breaking out from an **already-filtered** portfolio, but the peril **option** must remain available in both quick and custom. |

**Current code:** peril is **grouping-only** today — `_QUICK_DIMENSIONS = frozenset({"lob", "state", "country"})` and the quick pane iterates `dimensions if d.quick` with the comment "peril is grouping-only (P-19): no portfolio_number letter, no run_breakout_peril." D3 means adding peril to the quick set (and a `_DIMENSION_LETTER` entry / a `run_breakout_peril` path). For D4, `Coverage.label` is populated for states (Admin1Name) but there's **no peril-name label** — peril values render as bare codes; the fix maps peril code → display name in the breakout value builder.

---

## 4. Data management & error surfacing

### 4.1 Portfolio data-management actions (D13–D15)

| Item | Decision | Detail / rationale |
|---|---|---|
| Rename / delete portfolio | **Add — low effort** | Both are easy and useful (rename in place; delete). Ben offered them as low-effort adds. |
| Merge portfolios / "from all exposures" | **Future state, not v1 (D13)** | Cheryl's case — combine several by-state breakouts into one hurricane portfolio to run together — isn't supported by the copy-and-edit breakout flow and falls outside the 80/20 (Wendy: "does it have to be in version one? I don't know"). Automated analysis/grouping may reduce the need. Note it; possibly poll the team (Doug does this). |
| "Open in Risk Modeler" deep link (D14) | **Add at multiple levels** | Mirror the existing treaty-section "take me there" button at the portfolio (and other) levels. Ben: "super easy to put everywhere." |

### 4.2 Notifications / error surfacing (follow-up)

- A **breakout error banner persisted through a page refresh** in the demo — needs fixing.
- Direction: fix **toasts** (currently hard to see, ephemeral, and don't fire on failures) and/or build an **in-app notification center** (a bell/icon with messages + errors); Anil has a technical approach. Use the failed-breakout case as a test scenario.

**Current code:** durable **per-row** breakout failure lines already exist (`portfolio_row.html` `bo-row-error`, FR-012 — server-rendered, survive refresh). The gap is the **modal/global** error surface (stuck banner) and a general notification center — not yet built.

---

## 5. Tagging, ownership & authentication

A long, mostly exploratory discussion. Direction agreed; **implementation deliberately deferred** (D12).

| Item | Decision | Detail / rationale |
|---|---|---|
| Ownership model (D7) | **Reassign in the Workbench; cosmetic** | Reassign changes ownership in the **application DB only** — in Risk Modeler everything stays under the shared identity. Workflow: an admin/assistant sets up data + creates the submission (they own it), then **reassigns** to the analyst, and it appears in the analyst's submissions list. Wendy: "reassign is what we need in the workbench." |
| Auth (D8) | **Single "Workbench API key" to Risk Modeler** | Not per-user credentials. All Workbench-created EDMs/jobs are attributed to that key. (Ben had suggested per-user username/password originally; "Anil shut that down.") |
| Don't tag on username (D9) | **Use CRM ID / cedent / field combinations** | Because ownership is cosmetic and setup is often done by someone else, a username is a poor tag. Cheryl expects the working tag to be a **combination** — treaty year, inception date, cedent, treaty type — enough to narrow a list to <10. |
| Tag surface (D10) | **Keep tag management in Risk Modeler, not the Workbench** | Tagging is implicit/abstracted. On **reassign**, owner tags on the EDMs/RDMs are swapped automatically under the hood (synchronous, fast). |
| Auto-tag packages (D11) | **Tag on package add, untag on package remove** | Adding a data package to a submission tags its EDMs/RDMs with the submission's identifier; removing it strips the tag. Standalone EDM imports (no submission) may stay untagged. |
| Implementation (D12) | **Hold; convene a dedicated tagging-design session** | The functional side (what the tags are, when created/updated/removed) is the hard part; the implementation is low-effort and can follow. Ben is capturing ideas now. |

**Current code:** **none of this exists yet** — no `reassign`, `assigned_to`, `owner`, `api_key`, or `tag` machinery in `app/` (grep confirms). Ownership/tagging is greenfield and correctly parked behind the dedicated session (D12). When built, reassign lands in the submissions surface (`submission_service` / `routers/submissions.py`) with the tag-swap hook, and auto-tagging in the package sync path (`package_sync_service`).

---

## 6. Event-rate-scheme metadata & results scope (carry-over)

Continues `11` §3.3 / **O11-2** — the highest-leverage grouping item.

- The **event rate scheme is not exposed** through the normal analysis-import path. It shows only on **grouped** analyses (the group config's rate-scheme picker) and via a **Data Bridge** workaround that leaves two copies of the data (an RDM on Data Bridge, queryable by SQL, plus the results in Risk Modeler). Cheryl confirmed live: two imported analyses showed **no rate scheme** in the analysis detail, but grouping them revealed both as "RMS 2023 stochastic event rates" — so it's stored, just not surfaced (the field renders blank in the RM UI), likely a background join.
- Wendy believes the rate-scheme **ID** is on the analysis record and must be linked to another table for the name, but couldn't locate it live ("it's definitely somewhere, I just don't know where"); Jeff has a query that assembles full analysis details.
- **Action:** Cheryl will **reach out to the Moody's team** to pin down where the field lives and what happens to it on upload; Ben offered to join for the technical details. → **O12-6** (continues O11-2).
- **Results scope:** Ben distinguished **viewing loss results in the Workbench** from **extraction into the loss repository** (not yet designed). Wendy: the extraction *format* is stable (same columns) while the *options* vary (treaty vs. gross loss, grouped vs. single analysis). Results to be taken up **tomorrow or Thursday**, reviewed against CIC's current workflow tool.

**Current code:** corroborates the problem — `app/services/analysis_service.py` already models `AnalysisSettings.event_rate_scheme` / `rate_vintage` (read from `eventRateScheme` / `rateScheme` / `eventRateSchemeNames`), but the module notes "term / PLA / event-rate fields have **NO documented source** and stay [blank]." So the display slot exists; the data path does not. Grouping automation itself is still in the planned sequence diagrams (`docs/sequence_diagrams/planned/composite/group_results.md`, `.../granular/grouping.md`), not built.

---

## 7. Implementation deltas — what needs to change / be built

Consolidated view (aligned direction → current state → change). "Absent" = no supporting code found in the worktree.

| # | Item | Current code state | Change needed | Where |
|---|---|---|---|---|
| D1 | Two-pane, fixed-size breakout modal | Single stacked pane (pills → checks → picked chips) | Re-lay-out to left options / right list; fix modal size; swap chips for a list | `templates/partials/breakout_modal.html` (+ `static/css/details.css`/`app.css`) |
| D2 | Cascading filters + grey-out + live counts | Independent checkbox dimensions; **static** per-value counts | Filter other dimensions on selection; recompute counts; render invalid values disabled (not removed) | `breakout_modal.html`, `services/breakout_service.py` (value/coverage build), likely a preview route |
| D3 | Quick-breakout-by-peril | Peril **grouping-only** (`_QUICK_DIMENSIONS = {lob, state, country}`, P-19) | Add peril to quick set; add `_DIMENSION_LETTER`/`run_breakout_peril` path | `services/breakout_service.py`, `workers/portfolio_jobs.py` |
| D4 | Peril **name** display | Peril renders as bare code; `Coverage.label` only set for states (Admin1Name) | Map peril code → display name in the value builder | `services/breakout_service.py`, `breakout_modal.html` |
| D5 | Caribbean (CB) country/state mapping | **Absent** | CB branch: CB → country, island code → state; dedicated SQL path | `services/breakout_service.py` (+ breakout SQL / DataBridge query) |
| D6 | Country source `country_code` first, coalesce `country_rms_code` | Country = summary codes; no explicit coalesce/CB rule | Confirm/implement coalescing so the CB exception is correct | breakout geography derivation |
| D7 | Reassign-based ownership (cosmetic) | **Absent** | App-DB owner field + reassign action; appears in analyst's list | `services/submission_service.py`, `routers/submissions.py` |
| D8 | Single Workbench API key to RM | (Test build uses username/password under the hood per Ben) | Confirm API-key identity for all RM calls | `services/irp_gateway.py` / config |
| D9–D11 | Tagging (no username; auto-tag packages; swap on reassign) | **Absent** | Build after conventions set; tag on package add/remove; swap owner tags on reassign | `services/package_sync_service.py`, `services/edm_service.py` |
| D12 | Tagging **held** | n/a | Dedicated design session first (conventions + lifecycle) | — |
| D13 | Merge / "from all exposures" | Copy-and-edit breakout only | **Future state** — do not build for v1 | — |
| D14 | "Open in Risk Modeler" deep links | Exists in treaty section only | Add at portfolio (and other) levels | `templates/partials/portfolio_row.html` + others |
| D15 | Rename / delete portfolio | Not present | Low-effort adds | `routers/portfolios.py`, `services/portfolio_service.py` |
| — | Modal/global error surface + notifications | Per-row breakout errors persist; no global surface | Fix stuck banner; toasts; in-app notification center | `breakout_modal.html`, `static/js/app.js` |
| O12-6 | Event-rate-scheme retrieval | `AnalysisSettings` slot exists, blank-on-missing | Blocked on Moody's answer (where the field lives) | `services/analysis_service.py` / `irp_gateway.py` |

**Already satisfied this cycle (no action):** sub-portfolio **name-as-typed** + **duplicate-name block** (name-check route + `name_collision.html`, P-24/P-25); **criteria + "From" lineage** on the row (`portfolio_row.html`, FR-014 rev. 2026-08-11); **country** as a first-class breakout dimension; the **live selection preview** (chips — to be reshaped into the D1 list).

---

## 8. Open questions

- **O12-1** — **Cascading-filter mechanics (D2).** Server-recompute per change vs. precomputed cross-tabs; exact greyed/disabled render; interaction with CB values (§1.2, §2). *Ben.* (Advances O11-5.)
- **O12-2** — **Caribbean SQL path (D5).** Confirm the CB detection rule and the country/state field mapping against real Caribbean data (Puerto Rico single-island vs. whole-CB); verify Jamaica-region granularity isn't lost (§2). *Ben / Cheryl.*
- **O12-3** — **Peril breakout end-to-end (D3/D4).** Build the quick-by-peril path and peril-name labels; verify on a mixed-peril test portfolio (§3). *Ben.* (Continues O11-3.)
- **O12-4** — **Tag conventions & lifecycle (D9–D12).** Dedicated session: what the tag is (CRM ID / cedent / field combination), and when it's created / updated / removed; then implement (§5). *Ben / Wendy / Cheryl.*
- **O12-5** — **Merge / "from all exposures" demand (D13).** Confirm whether it's needed post-automation; possibly poll the team (§4.1). *Wendy / Cheryl / team.*
- **O12-6 (highest-leverage)** — **Event-rate-scheme retrieval.** Cheryl to contact the Moody's team for where the rate scheme is stored / how to pull it and what happens on upload; Ben to join; unblocks grouping automation (§6). *Cheryl → Moody's; Ben.* (Continues O11-2.)
- **O12-7** — **Global error surface / notifications.** Fix the stuck breakout banner; decide toasts vs. an in-app notification center (§4.2). *Ben (Anil on approach).*
