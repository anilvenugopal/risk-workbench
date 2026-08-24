# IRP Workbench — Design Notes: Packages Removed / Submission Reorg, Breakout Option-Filtering, EDM Notes & GeoHaz Hazard-Lookup

**Source:** Design session, August 13, 2026 (Ben Bailey presenting — PremiumIQ; Wendy Hayes, Cheryl TeHennepe — CIC; hard stop ~54 min on CIC's standing Thursday team call). **Day-after review of the "retire packages" prototype** committed at the close of the Aug 12 session, plus refinements to sub-portfolio breakouts and a first look at GeoHaz. *Anil Venugopal (PremiumIQ) referenced — he reviewed the build with Ben beforehand and raised the duplicate-file idea (§3.4) — but was not on the call.* Cross-checked against the full transcript. Eighth session of the series (continues Aug 4/5/6/7/10/11/12).
**Status:** Working design notes. The **packages-removed submission model** (EDM/RDM lists, shared-drive folder picker, EDM dropdown + breadcrumbs, shared-not-duplicated EDMs, detach-on-remove), the **Caribbean CB breakout** (built), **duplicate-breakout hashing**, the **EDM notes field**, **duplicate-name blocking**, **sorting-not-search** on the lists, and **per-section auto-refresh** are agreed as direction / built. The **multi-peril in-place option filtering** is the one **sign-off blocker** and is a prototype-in-progress. **GeoHaz / hazard-lookup** opened but the run-UX and the DLM-vs-HD storage question stay open. Realizes the Aug 12 decision to remove the packages entity; extends `06`/`11` (breakouts), `08`/`10` (submissions, EDM summary), `07`/`10` (GeoHaz/hazard lookup).
**Related:** `06_exposure_modification_subportfolios.md` (breakout actions, granularity cap), `11_submissions_search_subportfolio_breakouts_grouping.md` (§2 breakout refinements, §2.2 duplicate-name block), `08_v1_demo_review_edm_rollup_and_submissions.md` (packages/submissions, EDM roll-up), `10_edm_summary_submissions_geohaz_currency.md` (§2 GeoHaz display, §4 submissions list), `07_analysis_execution_geohaz_currency_accumulation.md` (§1 hazard lookup), `../DATA_MODEL.md` (Submission & Package — Package now retired; EDM/RDM/Portfolio), `../PRD.md`, `../FUNCTIONAL_REQUIREMENTS.md` (§2 exposure/breakout, §2.x submission/EDM summary), `../../../../CIC/minutes/Risk_Modeler_Interface_Design_Minutes_8-13-26.md`, `../../../../CIC/transcripts/Risk Modeler Interface _ Design 81326.vtt`

> Decision IDs (**D1**–**D12**) below refer to the tables in the 8/13 minutes. Note: the Aug 11 and Aug 12 sessions have minutes in `CIC/minutes/` but no dedicated notes doc in this folder yet — this doc chains from `11` (Aug 10) and folds in the 8/12 "retire packages" decision it reviews.

---

## 0. TL;DR

A demo-and-refine pass over a three-item agenda — sub-portfolio breakouts, the packages-removed submission page, and a first GeoHaz look — all three reached, GeoHaz only in the final ~8 minutes. Load-bearing, product-affecting outcomes:

- **Packages are gone — and it landed well.** The submission page is now two straight tables (EDMs, RDMs) with no package layer (D1). Cheryl: "a lot cleaner… looks great"; Wendy: "more the way we think about it." This realizes the Aug 12 decision and **retires the Package entity from the data model.**
- **EDM/RDM data is shared across submissions, not duplicated** (D7): one underlying dataset, edits propagate, and **"remove" only detaches** an EDM/RDM from a submission (no delete elsewhere or in Risk Modeler). Cuts the cat + per-risk (PPR) CRM-ID mix-ups Cheryl has hit.
- **Submission gets a shared-drive home directory** (D5) picked by browsing the drive; the import file-browser then opens there. An **EDM-name dropdown + corrected breadcrumbs** (D6) handle in-submission navigation.
- **Caribbean CB breakout is built** (D2): a CB portfolio → one country (CB) + island states (PRI, VIR); mixed US+CB portfolios break out by country. SQL CASE-statement handling.
- **Multi-peril empty-breakout — the one sign-off blocker.** A presence check now blocks submitting an empty breakout (find-first-record, not a full count), but the real ask — **filtering the selectable options in place** so you can't pick Japan and still see New Zealand states — is not done. Ben: "I'll consider that a blocker for signing off on this." → O12-1.
- **Duplicate *breakouts* blocked by hashing the underlying data**, not just the name (D3); duplicate **names** blocked on upload for EDMs, RDMs, and portfolios (D10).
- **Small, copy-able EDM notes field** (~255 char, deliberately not ~1000), stored only in the Workbench, in the name↔status white space (D9). "The easiest thing you've ever asked for."
- **Lists get sorting, not search** (D8, short lists ~8–10); **per-section auto-refresh** ~every 5s until jobs finish, with clearer statuses to add (D11).
- **GeoHaz (first pass):** guard hazard-lookup versions to the EDM's data version, trim options to the current baseline (25 / HD — "point in time," no backward retrieval), and prefill standard defaults; simplify toward one run action. Open: does running **HD overwrite the DLM hazard or store separately** (D12) → O12-3.

---

## 1. Packages removed — the submission becomes the container

Realizes the Aug 12 decision (prototype built that afternoon, reviewed here). The Package entity is removed; a submission is now two straight tables — **EDMs** and **RDMs** — with the EDM detail view showing **all RDMs in the submission** (no package scoping). Reception was strongly positive (Cheryl: "It's a lot cleaner… it looks great"; Wendy: "It's more the way we think about it"). **Treat removal as final** and reconcile `DATA_MODEL.md`, the FR tracker, and any spec that references packages.

| Item | Decision | Detail / rationale |
|---|---|---|
| **Packages entity** | **Removed (D1)** | Packages existed to relate EDMs↔RDMs down to the analysis level; that association was unreliable and already removed, so packages served no purpose. CIC matches EDMs/RDMs by naming convention and found packages confusing. Removing the entity simplifies the model. |
| **Submission layout** | **Two table sections: EDMs and RDMs (D1)** | Straight lists per submission. EDM detail shows all RDMs in the submission. |
| **Shared-drive home directory** | **Pick by browsing the drive (D5)** | The submission directory field (previously inert text) now browses the shared drive to set a root folder; the EDM/RDM import browser opens **directly in that folder** instead of at the drive root. Folder-only view when choosing. |
| **In-submission navigation** | **EDM-name dropdown + corrected breadcrumbs (D6)** | An EDM dropdown swaps between EDMs within the submission; breadcrumbs reflect the submission navigated from. Liked live ("very nice"); Ben may relocate the dropdown to the top later. |
| **Add EDM/RDM** | **Import-new *or* add-existing** | "Add an EDM" covers importing a brand-new database and adding an existing one already synced from Risk Modeler. **Add-existing currently EDM-only** — extend to RDMs. → O12-6. |
| **Shared, not duplicated (D7)** | **One underlying dataset across submissions** | An EDM can belong to multiple submissions off one dataset — editing it in one is reflected in the others. |
| **Remove = detach (D7)** | **Removing only detaches from the submission** | No delete elsewhere or in Risk Modeler; other submissions keep the EDM. Cleans up the cat + per-risk (PPR) two-submission pattern and reduces CRM-ID mistakes. |

---

## 2. Sub-portfolio breakouts (Caribbean built; multi-peril filtering still open)

Extends `06` (the single-click breakout actions, granularity cap) and `11` §2 (geography/naming/lineage refinements). This session **confirmed the Caribbean CB special case as built** and worked the multi-peril "no matching accounts" case — which produced the session's only explicit sign-off blocker.

### 2.1 Caribbean CB — built (D2)

- A **CB** (RMS Caribbean grouping) portfolio now resolves to **one country (CB)** with **island-level states (PRI, VIR)** — replacing the old "multiple countries per island / Admin-1 states" behavior. Quick and custom breakouts operate on those states.
- **Mixed portfolios work:** a US-flood portfolio holding both CB and USA breaks out by country into CB and USA, counted distinctly.
- Implemented at the **SQL level via CASE statements** on the CB country code (rather than duplicating every state-handling script). Accepted as a **standing exception** (continues the Aug 11/12 CB work).

### 2.2 Multi-peril empty breakout — presence check now, in-place filtering next *(sign-off blocker)*

- Filtering the option lists **in place** is heavy under the hood, so Ben did **not** go that route yet. Instead, an **on-add validation** checks whether *any* account matches the selected filters and **blocks submit** with "no account matches the filters," while still letting the analyst **name the breakout and add it to the cart** first. The name check is debounced ~300 ms after typing.
- Accepted as a **stopgap, not the end state**. Cheryl: a good first step, "better than clicking create and finding out at the end," but if a long selection (say 10 of 50 LOBs) comes back empty, having to manually uncheck everything is cumbersome.
- **Wendy's developer point (adopted):** the check only needs the **presence of one matching record**, not a full count — so it can return fast. → also feeds O12-2 (load-test).
- **Ben's red line:** the option lists themselves must be correct — "we chose Japan, but we have New Zealand states — that doesn't make any sense." He will **filter the selectable options in place based on prior selections** and treats this as **a blocker for signing off** on the breakout flow. → O12-1.

### 2.3 Duplicate breakouts (D3)

- "Already created (2)" is driven by a **hash of the underlying data**, not just the name — re-creating a breakout whose data is identical does nothing, keeping names unique within the EDM.
- A breakout **grabs the matching accounts into a new portfolio** (it does not first duplicate records — same records, grouped differently). True portfolio duplication (copy-then-modify) is a separate need and stays on the nice-to-have list, alongside rename/delete/duplicate portfolio actions ("why make you go to Risk Modeler to do that?").

### 2.4 Experience polish — tabled to September

- The two-column layout (selections on the right) and **grouping/sorting the state list under its country** (rather than jumbling CB and US states) are deferred. Ben wants the end-to-end flow done by end of month and would pick these up in September from his running nice-to-have list. → O12-5.

---

## 3. EDM/RDM list ergonomics, notes & guards

Extends `08`/`10` (EDM summary, submissions list). Refinements to the new per-submission tables.

### 3.1 Sort vs. search (D8)

- Lists are expected to be **short** (typically 8–10, unlikely to reach 25), so **sorting is worth adding but name search is not needed here**. (Distinct from the deferred app-wide search pass — §5.)

### 3.2 Columns / metadata

- No extra columns needed — naming convention plus click-through to the EDM is enough (Cheryl: "our naming convention should help… then when you want more detail, you go into the EDM").

### 3.3 EDM notes field (D9)

- Add a **small free-text note per EDM** in the white space between name and status: **~255 characters, deliberately not ~1000**, kept short so it stays a quick note and doesn't become a replacement for CIC's OneNote project record.
- **Copy-and-paste-able** (and possibly **hoverable**) so it drops easily into OneNote. **Stored only in the Workbench** (not written back to Risk Modeler) — the team must be aware this is the only place it lives.
- Ben: "the easiest thing you've ever asked for" — can build today. → follow-up (build).

### 3.4 Duplicate-name and duplicate-file guards

- **Duplicate-name blocking on upload (D10)** for **EDMs, RDMs, and portfolios** — Risk Modeler permits same-named RDMs, which CIC finds error-prone; the Workbench blocks it. Confirmed and appreciated.
- **Anil's duplicate-*file* idea (tabled):** warn (not block) when the same underlying file is re-uploaded for a new EDM/RDM. After discussion the group judged the existing name-taken message sufficient — a warning would add complexity (need to surface the existing EDM's state so the user can decide). Ben kept a note. → O12-7.

### 3.5 Auto-refresh & statuses (D11)

- Tables **refresh individual sections on an interval (~every 5 s)** until all jobs complete — import job → "ready," then a second Workbench roll-up job populates the portfolio/account counts — rather than reloading the whole page. Same pattern applies wherever there's live tracking (breakouts, GeoHaz).
- **Add clearer statuses** — "pending import" is ambiguous (is the job running?); an explicit "Uploading" state would help. Also consider hiding the Risk-Modeler-ID column until import completes. → follow-up (build).

---

## 4. Dev conveniences (context)

- **Sync existing EDMs from Risk Modeler into the Workbench** — added so data survives the pre-go-live schema wipes (Ben builds one canonical migration for go-live rather than appending dozens, so structural changes drop all data). Synced EDMs land in the library with no submission and no source file; simple name search; page needs a performance pass. Framed as mostly a dev convenience — "a feature you might never use, but if you need it, it's there."
- A parallel **add-existing for RDMs** was requested as the same kind of convenience. → O12-6.

---

## 5. GeoHaz / hazard lookup (first pass, ~8 min)

Extends `07` §1 (hazard lookup only, never re-geocode) and `10` §2 (no on-screen version stamp; offer a from-screen hazard-lookup action). Ben used a quick run (select all portfolios, hazard-lookup only, no re-geocode) to surface the design questions rather than settle them.

| Item | Decision / direction (D12) | Detail / rationale |
|---|---|---|
| **Version compatibility** | **Guard to the EDM's data version** | Picking a hazard/geocode version out of sync with the data version (here **data version 22**) throws a warning, which Ben has always hard-blocked. The UI should **offer only compatible versions** and refuse an out-of-sync submit — not rely on a naive stamp read. |
| **Version options shown** | **Trim to the current baseline** | CIC never runs hazard retrieval **backward**, so only **25-and-newer / HD** ("point in time") need appear; the one real choice is **DLM vs. HD** (Wendy). |
| **Prefilled defaults** | **Standard settings, prefilled** | Earthquake + windstorm; geocode **deselected**; **override** user-defined hazard values; **do not skip** locations with a previous hazard lookup. (Agreed by Ben + Cheryl.) |
| **Run UX** | **Simplify toward one action** | Move away from a screen + second window toward a single run button (or a DLM/HD dropdown), to save clicks. → O12-4. |
| **Peril mismatch** | **Non-blocking** | Earthquake lookup on a hurricane portfolio doesn't fail — it reports "0 locations processed." |
| **DLM vs. HD storage** | **OPEN** | Does running HD **overwrite** the DLM hazard, or are they **separate fields**? Ben expects both can run without overwrite (displayed comma-separated, e.g. "23, HD") — to test and confirm next session. → O12-3. |

- A stray **"HD unavailable"** label Wendy spotted was acknowledged as a demo/example glitch, not intended behavior. → part of O12-4.

---

## 6. Open questions & follow-ups

- **O12-1 (sign-off blocker)** — **Filter breakout options in place** based on prior selections (e.g., after choosing Japan, don't offer New Zealand states). Ben did surface-level attempts; working it today, hopes to show tomorrow (§2.2). *Ben.*
- **O12-2** — **Load-test the empty-breakout presence check** against realistic volumes (~10M records × 20 LOBs × 50 countries); confirm it uses find-first-presence rather than a full count so "you can't hit go until it confirms records" stays fast (§2.2). *Ben (with CIC).*
- **O12-3** — **GeoHaz: does HD overwrite DLM, or separate fields?** Drives whether the run control is one button or a DLM/HD choice. Ben to test today, show next session (§5). *Ben.*
- **O12-4** — **Finalize the GeoHaz run UX** — single button vs. DLM/HD dropdown; confirm the version list trims to 25+/HD; lock the prefilled defaults; fix the "HD unavailable" label (§5). *Ben / CIC.*
- **O12-5** — **Breakout screen experience polish** — two-column layout with selections on the right; group/sort states under their country. September, after the core flow (§2.4). *Ben.*
- **O12-6** — **Add-existing for RDMs** — extend the sync/add-existing convenience beyond EDMs (§1, §4). *Ben.*
- **O12-7** — **Duplicate-file upload warning (tabled)** — Anil's idea; group judged the name-taken message sufficient. Noted, not scheduled (§3.4). *Ben.*
- **O12-8** — **Dedicated search/sort/filter pass across the app** — deferred until the core end-to-end flow is done; handled as one consistent item (~1 week out) (§4). *Ben.*

**Committed builds (agreed, not open):** the small EDM notes field (§3.3) and clearer import statuses (§3.5) — both slated as quick follow-ups, the notes field "today."
