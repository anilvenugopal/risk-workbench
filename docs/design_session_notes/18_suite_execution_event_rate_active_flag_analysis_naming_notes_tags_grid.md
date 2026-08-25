# IRP Workbench — Design Notes: Suite Execution Demoed, Event-Rate-Scheme Active Flag, Analysis Naming (`CRE` + Notes + `_n`), Auto-Tags, and the Analyses Grid

**Source:** Design session, August 21, 2026 (~72 min) — Ben Bailey presenting (PremiumIQ); Cheryl TeHennepe, Wendy Hayes (CIC). Cross-checked against the full transcript. This session finally ran **demo "part 2"** that `17` (8/20) never reached: duplicating templates/suites, then **actually executing a suite** end-to-end and watching jobs land in the analyses grid. The balance of the hour was spent on that grid — filtering, deleting, naming, notes, tags — and the session closed by handing **results viewing** to Monday 8/24. Continues `17` (8/20 currency/duplicate/grouping) and `16` (8/18 analysis metadata + suites).
**Status:** Working design notes. Direction agreed: the **event-rate-scheme over-listing is diagnosed and resolved** with a **Workbench-side active/inactive flag** (manually curated, **event rate schemes only**, periodic not one-time maintenance); **duplicate-and-edit is built and accepted** for templates *and* suites; the **analyses grid** is **grouped by status with failures on top**, date-descending, **filtered** (not searched), with a **platform link-out** and **multi-select delete** that **cascades to Risk Modeler** where a real analysis exists; **analysis names get a `CRE` prefix**, a **delimiter**, and **`_n`** (not `(n)`) duplicate suffixes; a **new free-text notes field is captured at submit time** and applied to every analysis in the batch; **tags are confirmed and will be auto-applied from context** (submission, peril, region); and **deal status / "bound" / retention is explicitly deferred** pending a CRM link. Reconciliation is substantial: **`irp_event_rate_scheme` needs a Workbench-owned visibility flag without breaking §10's "sync-only writes" invariant**; **`irp_analysis` records nothing about *how* an analysis was run** — no template FK, no currency, no treaties, no notes — which is now load-bearing because currency (`17` O17-2) and treaties (`16` O16-6) both moved to submit time; **there is no analysis-level tag table** (only `analysis_template_tag`); **there is no failure-reason field** to satisfy the "surface failures with a reason" requirement raised twice now (`14` D13, again here); and **analysis delete needs a decision between request-path and worker execution**. **Closes `17` O17-11** (event-rate list mismatch) and **the duplicate half of `17` O17-4**. **Does not advance `17` O17-6** (grouping) — the planned grouping topic was displaced by execution and the grid. Extends `17` (duplicate-and-edit O17-4; event-rate mismatch O17-11; submission tag O17-6; submit-time currency O17-2), `16` (analysis metadata D1/D2, event-rate filtering O16-5, treaty-name-pattern drop O16-6), `15` (event-rate grouping O15-6, technical write-up O15-7), `14` (suites-first, run-a-suite D13 "failures must be surfaced with a reason", auto-name O14-9, treaties O14-8), `07` (§2.3 auto-naming), `05` (results metadata + comparison).
**Related:** `17_submit_time_currency_vintages_no_suites_of_suites_duplicate_grouping.md` (§2 submit-time currency O17-2, §3.2 duplicate-not-Excel O17-4, §4 grouping/submission tag O17-6 + event-rate mismatch O17-11), `16_analysis_metadata_settings_suites_excel_seed_currency_schemes.md` (§1 analysis-metadata view D1/D2, metadata filtering O16-5/O16-7, §2.3 treaty-name-pattern drop O16-6), `15_geohaz_dlm_closeout_edm_rdm_notes_tables_event_rate_grouping.md` (§4 event-rate grouping O15-6, technical write-up O15-7), `14_analysis_suites_first_geohaz_dlm_hazard_edm_notes.md` (§1 template/suite vocabulary, run-a-suite D13 failures-with-a-reason, auto-name O14-9, treaties O14-8), `07_analysis_execution_geohaz_currency_accumulation.md` (§2 running analyses, §2.3 auto-naming), `05_analysis_results_metadata_and_comparison.md` (results views + broker-vs-own comparison — reactivated as the Monday topic), `../DATA_MODEL.md` (§4 `submission` status/dates/`submission_crm_id`; §6 `irp_analysis`; §7 `analysis_template` — `auto_name_pattern`, `peril_code`, `region_label`, `analysis_template_tag`; §9 `analysis_result_meta`; §10 `irp_event_rate_scheme` / `irp_model_profile` / `irp_tag` — "Sync IRP Metadata"; §13 seed checklist; §14 open decisions), `../PRD.md` (§11/§14 analysis execution), `../FUNCTIONAL_REQUIREMENTS.md` (analysis-execution section), `.specify/memory/constitution.md` (Art. 5 — IRP submission on the request path permitted, polling/result work in workers), `../../../../CIC/minutes/Risk_Modeler_Interface_Design_Minutes_8-21-26.md`, `../../../../CIC/transcripts/Risk Modeler Interface _ Design 82126.vtt`

> Decision IDs (**D1**–**D13**) below refer to the tables in the 8/21 minutes. Open-item IDs are **O18-n**.
> *Transcript note: this session's auto-transcription is unusually lossy — it drops words mid-sentence throughout, so quotes are short and some detail is reconstructed from surrounding context rather than lifted verbatim. Speaker tags are reliable this time (all three participants are separately attributed), unlike `17`. Term interpretations reflect context: "parallel region" / "pale region" = **peril region**; "risk length" / "Riskling 25" = **RiskLink** (v25); "bottle composer" = **Model Composer**; "movies" = **Moody's**; "the CDM" = **EDM**; "sweets" = **suites**; "Siri" = **CRE**; "memory schemes" = **event rate schemes**. The "Michael Young" and other custom event rate schemes referenced live in **Ben's** Moody's tenant, not CIC's. Names referenced but not present: Ross (ops/VM), Randy (SQL Server DB admin).*

---

## 0. TL;DR

First session with the analysis engine actually running. Product-affecting outcomes:

- **Duplicate-and-edit is BUILT and accepted (closes half of `17` O17-4).** Ben duplicated a template (renamed, swapped the event rate scheme), then duplicated a suite. Cheryl: "Very nice." Excel stays tabled; the near-term bulk-management story is settled.
- **Event-rate-scheme over-listing DIAGNOSED and RESOLVED with a Workbench-side active flag (D1, D2, D3).** Root cause: Ben syncs the **full** scheme list and filters only by **peril + region**; Risk Modeler layers on a **model-version-code** filter whose mapping Moody's publishes **only as a downloadable Excel sheet** — no API attribute. Ingesting that Excel was considered and **rejected**. Instead: a manually-curated **active/inactive** flag controls what users see. **Closes `17` O17-11.** → **O18-1**.
- **Curation is periodic, not one-time (D2), and the flag is scoped to event rate schemes ONLY (D3).** Wendy corrected Cheryl's "one-time setup" framing — schemes ship and retire, and the version you want changes year to year, so revisit **yearly or less often**. Ben asked about extending the flag to model profiles/currencies; Wendy: "I don't think we want to go there." Do **not** generalize it across §10. → **O18-1**.
- **Suite execution demoed end-to-end.** A USEQ suite against selected portfolios + two treaties, **currency chosen at execution time** (validating `17` D4/O17-2 in practice), produced **nine jobs from three templates** into a polling status table kept separate from other job types. → confirms the §8 job model; no change.
- **Analyses grid: group by status with failures on top, date-descending, filter (not search), platform link-out (D4, D5).** Wendy's orientation problem ("Which job is this? Did I run it?") and her failure-triage need ("I just want to see three failed") drove this. Ben chose **group-by** over sort so failures cluster. → **O18-5**.
- **Failure reasons are not surfaced — second time this has been raised.** The demoed failure showed nothing useful: "we don't really have any information … we're not pulling in right now the proper [error], but we'll make that known." `14` **D13** already required that expected peril/portfolio mismatches fail **with a reason**. There is no field for it in §6. → **O18-6**.
- **Multi-select delete of analyses, cascading to Risk Modeler (D6, D7).** Two paths: a real Moody's analysis → delete **there and** in the Workbench; a failed-to-submit record → Workbench row only. Ben called it "a synchronous operation, we don't need a job for it" — that needs reconciling against the spec-003 RDM-delete precedent (synchronous, but **in a worker**). A separate "failed job queue" was proposed by Cheryl and **rejected** — grouping plus clear-them covers it. → **O18-7**.
- **Analysis naming settled in three parts (D8, D10, D11).** Prepend **`CRE`** (so CIC's analyses group apart from broker-originated ones in the platform/RDM); add a **delimiter** between portfolio name and template name; change the duplicate suffix from **`(1)`** to **`_1`**. Blocking duplicate submissions was considered and **rejected** — Risk Modeler allows them, and identically-named neighbours are a useful cleanup signal. The **64-character limit is unverified** and Wendy thinks it's too short (profile names alone run ~50 chars). → **O18-2**, **O18-3**.
- **A free-text NOTES field is captured at submit time and applied to the whole batch (D9).** Solves same-templates/different-treaties producing identical names — Wendy's quota-share-then-cap-treaty example. Short by design (Wendy ~25 chars, Cheryl "5x10", Ben proposed ~100 — unsettled). Nothing models this today. → **O18-4**.
- **Distinguishing detail belongs in GRID COLUMNS and the description, not the name (D11).** Cheryl: don't "shove" deal and profile into one string. Ben accepted — build out columns and enrich the description (profile, treaties, event rate scheme). **But the Workbench doesn't currently store what was actually run** — see the load-bearing gap below.
- **Tags confirmed and will be auto-applied from context (D12).** `submission:<name>` demoed; **peril and region** endorsed as the first auto-tag set; tags shown in the grid. **Merges with `17` O17-6's submission-level tag** — same mechanism. There is **no analysis-level tag table** (only `analysis_template_tag`). → **O18-8**.
- **Deal status / "bound" / retention DEFERRED as an explicit future request (D13).** Without a CRM integration nothing sets "bound" automatically and CIC won't backfill manually today; a Workbench-only flag also wouldn't help anyone working directly in Risk Modeler. The underlying need is real (1-year deals, some 6-month; records held ~3–5 years after pricing; today's only handle is a RiskLink DB named "Treaty 24") — the cheap **capture-now** items are worth taking. → **O18-9**.
- **Results viewing is the Monday 8/24 workstream, and CIC is sending screenshots first.** Ben wants an **analysis results view** plus a **submission-wide view** (the basis for reporting), and confirmed **broker-vs-own comparison** is on the list. Moody's own "exposure and analysis results detail" report is all metadata and useless. Wendy proposed, and Cheryl agreed, to **send screenshots of the RiskLink views they use today** so Ben builds from real artifacts. Reactivates `05`. → **O18-10**, **O18-11**.

**The load-bearing gap this session exposed:** with **currency** moved to submit time (`17` O17-2) and **treaties** moved to run time (`16` O16-6), and now **notes** added at submit time (D9), **`irp_analysis` (§6) records nothing about how an analysis was actually run** — no template FK, no currency/scheme/vintage, no treaty list, no notes. Every grid-column, description-enrichment, and results-comparison request from this session depends on that record existing. → **O18-3** (make it exist).

---

## 1. Duplicate-and-edit — built and accepted (closes `17` O17-4 duplicate half)

Ben opened by demoing the capability agreed the previous day, and it needed no design discussion.

- **Templates:** duplicate → the copy takes the source name plus a suffix → rename ("2023 rates") → change the **event rate scheme** → save. Deleting the copy is equally direct.
- **Suites:** duplicate an existing suite (Ben started on USEQ, switched to HU) and incorporate the copy into the suite structure.
- Cheryl's "Very nice" closed the topic; the group did not revisit it.
- **Reconciliation:** **closes the duplicate-and-edit half of `17` O17-4.** The Excel import/export deferral (the other half) stands unchanged — revisit ~September. `analysis_template` / `template_suite` remain out of the §13 seed checklist; **manual creation plus duplicate** is the confirmed go-live path.

---

## 2. Event-rate-scheme over-listing — root cause and the active flag (D1, D2, D3)

The longest technical thread of the session, and a direct carryover from `17` §4 / **O17-11**. Worth recording the mechanism in full, because the fix is a deliberate retreat from a programmatic solution.

### 2.1 What we learned — why the Workbench list is longer than Risk Modeler's

- The Workbench syncs the **full** event-rate-scheme list from the API into §10 `irp_event_rate_scheme`, then filters that cached list by **peril + region** (`peril_code`, `model_region_code`) for the chosen model profile.
- **Risk Modeler applies a further filter the Workbench cannot replicate.** Model profiles carry a model/data version — Ben pointed at a profile reading **"last model update version 18.0"** — and event rate schemes carry a **`model version code`** (he showed one reading **9**). Moody's maintains the mapping of *which schemes are valid for which model version*, and publishes it **only as a downloadable Excel sheet**. There is **no API attribute** exposing it. Ben: "I cannot validate programmatically … the one [thing] I can't read programmatically."
- Ben confirmed the diagnosis by working it through the **Moody's chatbot**, which returned the missing-attribute explanation.
- **Symptom:** obsolete **model-version-9** schemes appear in the Workbench dropdown alongside the current **version-17** ones. Ben's own tenant additionally shows custom schemes ("the Michael Young ones") created by other users of the shared test environment.
- **Version numbers do not track software releases.** Cheryl: RiskLink 25 legitimately runs a **2017** rate set because the rates haven't been updated since — "it doesn't line up with the software release." Wendy made the same point about up-to-date schemes not carrying the current version number. **Do not build any inference that ties scheme version to software version.**
- **Live tenant check.** In CIC's analysis-run dropdown Cheryl sees a short list — "I have five options and that's it": the version-17 sets, plus an 18 with induced seismicity, plus time-independent and sensitivity variants. She initially couldn't find the version-9 entries and doubted CIC even had **Model Composer**; Wendy noted Moody's now ships it by default where it used to be a separate tool. Opening Model Composer, Cheryl found them: "I now see … they're in there."
- **Wendy's read, which the group adopted:** the extra entries are **product-default schemes** surfaced through Model Composer, CIC doesn't use Model Composer or author custom rate schemes today, so they only ever want the small default set — **but** if CIC starts building custom schemes, more will appear and the list will need managing. This is why the fix must be maintainable rather than a one-off cleanup.

### 2.2 Rejected option: ingest the Moody's Excel mapping

Ben raised, and the group did not take up, incorporating the downloadable Excel sheet as a maintained data source ("keep it as a data source … incorporate that missing link"). It would restore the programmatic filter but adds a manually-refreshed external dependency on a file Moody's controls. **Recorded as considered-and-rejected** so it isn't re-litigated; it remains the fallback if manual curation proves unworkable.

### 2.3 Decision: a Workbench-side active/inactive flag (D1)

- Every scheme is still **synced and stored**; a **Workbench-owned active flag** controls what the picker shows. Ben: "it could be a workbench side active flag … at your discretion." Wendy, on pulling the full list into a Workbench table: "then you indicate active or inactive, to determine what the users see." Ben's assessment: "That would be pretty easy … I feel like that's appropriate remediation to this issue."
- Surface: a **display/hide checkbox** on the event-rate-scheme rows of the analysis-metadata view (`16` §1 / `17` §1.1) — the first **write** affordance on a screen that `17` **D1** established as read-only reference. Note this tension explicitly in the read/write matrix (`17` O17-7): the flag is **Workbench state about an IRP object**, not an edit to the IRP object.

### 2.4 Decision: periodic maintenance, not one-time (D2)

Cheryl framed it as "a one-time setup situation." Wendy pushed back and the group took her version: new schemes ship, old ones fall off the back end, and the version CIC wants changes year to year — "So it's not a one-time thing is my point." Agreed shape: **tick the wanted schemes at go-live**, then revisit **yearly or less often** as regular maintenance. Cheryl: "it's just part of regular [maintenance]." This matters for the fix's design — the curation UI must be usable by an analyst a year later, not a one-shot seed script.

### 2.5 Decision: event rate schemes only (D3)

Ben asked whether the same flag should apply to other analysis metadata — model profiles, currencies. Wendy: **"I don't think we want to go there."** Ben accepted immediately: "that's easy from a development standpoint … we'll just start with that particular issue." **Do not generalize the flag across §10.**

### 2.6 Reconciliation — **O18-1**

The flag collides with a stated §10 invariant and needs a deliberate design choice:

> §10: *"Populated by the 'Sync IRP Metadata' action; the app never writes to these tables otherwise."*

Two options:

- **(a) Add `is_active` to `irp_event_rate_scheme`.** Simplest read path, but it makes a sync-owned table user-writable and the **sync upsert must be taught to preserve the column** — a silent-data-loss risk if sync is ever changed to delete-and-recreate.
- **(b) A separate Workbench-owned side table** — e.g. `event_rate_scheme_visibility (irp_id PK, is_active, updated_by, updated_at)` — keyed on the scheme's `irp_id`, joined at read time. **Recommended:** it preserves §10's invariant intact, survives any re-sync strategy, and gives the curation an audit trail (who hid what, when) that a year-later maintainer will want.

Secondary, worth taking while we're here: **cache the scheme's `model_version_code`** on `irp_event_rate_scheme` (it is visible in the API/UI — Ben read "9" off a scheme) and confirm whether `irp_model_profile.software_version_code` already carries, or can carry, the profile's **model/data version** ("18.0"). Neither closes the gap on its own — the *mapping* is the Excel-only part — but caching both makes the curation UI legible ("this is a v9 scheme, you're on v18") and leaves the door open to a coarse automatic pre-filter later.

**Closes `17` O17-11.** Also partially serves `16` **O16-5** (metadata filtering) and reduces the sting of **O17-9** (the dropdown auto-populate bug), though **O17-9 remains a real bug and is not fixed by this**.

---

## 3. Suite execution — demo part 2 (validates `17` O17-2 in practice)

The run that `17` never got to. No new decisions, but it confirms several prior ones are correct in the built system.

- Flow exercised: from a submission holding several EDMs → start a fresh run → select an **EQ-peril suite** → choose portfolios from the available list → pick specific templates → **set the currency at execution time** → select **two treaties** for the run → submit.
- Result: **nine jobs from three templates**, landing in a status table that refreshes on a poll as jobs progress. Analyses have **their own section**, deliberately separate from other job types.
- **What this validates:** `17` **D4/O17-2** (currency as a submit-time parameter) works in the built flow; `16` **O16-6** (treaties selected at run time, not via `treaty_name_pattern`) likewise. `DATA_MODEL.md` §7 `treaty_name_pattern` removal is still outstanding and this run is further evidence for it.
- One historical row in the grid had **failed** — the peril/region mismatch case that `14` **D13** established as an *expected*, not erroneous, outcome. Its presentation is the problem (§5.2).

---

## 4. Analysis naming, notes, and what actually gets recorded (D8, D9, D10, D11)

Three separate changes plus one structural realisation.

### 4.1 The `CRE` prefix (D8)

- Every Workbench-submitted analysis name is **prefixed `CRE`**. Purpose: on the **platform / in an RDM**, CIC's own analyses group and sort apart from broker-originated ones. Cheryl drove it — "I also for the name would like … [CRE] in front of it … group my stuff together."
- Wendy's caveat, recorded because it bounds the benefit: broker analyses arrive **via RDM**, so they aren't intermingled with CIC's in the first place — "those are only analyses that we would [run]." She had no objection: "I'm fine with CRE." Cheryl's counter is that it still helps *"when it comes time to group"* in the RDM and platform. Ben: "I think it's a good idea to put that CRE in front."
- A **delimiter** is also needed — today's name concatenates portfolio name and template name with nothing between them.

### 4.2 Duplicate names: `_1`, not `(1)` (D10)

- Risk Modeler **permits** same-named analyses; Ben's current code appends `(1)`. Changing to `_1`, `_2`. Cheryl: "put a two at the end … I think that's kind of standard."
- **Blocking duplicate submission was considered and rejected.** Ben offered it as the alternative; Cheryl preferred allowing them, because three identically-named rows in a row are a useful signal — "I'm going to be like, I don't need all those" — and they sort together for cleanup. This pairs with multi-select delete (§5.3).

### 4.3 The 64-character limit is unverified (D8 discussion)

Ben believes the Risk Modeler analysis-name limit is **64 characters** but flagged it as unconfirmed: "This could also be wrong … I'll test it." Wendy immediately doubted it — CIC has names longer than that, and **"profile name alone is like 50 characters."** Since the generated name is `CRE` + portfolio name + delimiter + template name (and template names *are* model profile names), 64 is almost certainly binding. Whatever the true limit, the auto-name generator needs an explicit **truncation or validation rule**; today it has neither.

### 4.4 Notes field at submit time (D9)

- **New concept.** A **free-text note** captured on the submission form and applied to **every analysis in the batch** — for both **suite execution and individual template execution**.
- **The problem it solves** (Wendy): run one set of treaties against a suite, then a second set against the same suite, and you get two batches of identically-named analyses with no way to tell them apart. "This is a quota share treaty, this is a cap treaty."
- **Short by design.** Wendy: "25 characters at most or something," explicitly not a paragraph. Cheryl: "5x10, whatever is good enough for me … that's enough that I can differentiate." Ben proposed "100 characters maximum or something." **Length unsettled** — recommend landing near CIC's number (≤50) rather than Ben's, since both clients independently asked for *short*.
- Cheryl liked it on its own merits — "The notes field is interesting" — and noted it doubles as **a sorting/grouping key**.

### 4.5 Detail belongs in columns, not the name (D11) — and the gap underneath it

- Cheryl's structural point: RiskLink gives you portfolio name in one field and DLM profile name in another, and you use the combination. Rather than "trying to shove" both into one string, keep them as **separate fields** — "do you have … a separate field [that] differentiates."
- Ben agreed and reframed the fix: "Making it readable on the workbench is [the answer] … we can add all sorts of attributes to this table." He committed to **building out the grid columns** and **enriching the description** with profile, treaties, and event rate scheme.
- Both then flagged the opposite risk — Ben: "I think the description is pretty long"; Cheryl: "Do we want all that? I don't know … a little stopped on this one." Left to iterate: "All of that is pretty easy to change."
- **The gap this exposes.** Every one of those columns is a *run-time* fact, and `DATA_MODEL.md` §6 `irp_analysis` stores **none** of them. There is no `template_id`, no currency/scheme/vintage, no treaty list, no notes — only `name`, `irp_id`, `status_code`, `edm_id`/`rdm_id`, `group_parent_id`, and the creating job id. Before `17`, currency at least lived on the template; now it doesn't, and treaties never did. **Nothing in the system records what was actually run.** That blocks the enriched description, the grid columns, the notes field, and — critically — the broker-vs-own **results comparison** Wendy asked for in §6. → **O18-3**.

### 4.6 Reconciliation — **O18-2**, **O18-3**, **O18-4**

- **O18-2 (naming):** `analysis_template.auto_name_pattern` (§7, Jinja2, "evaluated against submission context") is the right home for the prefix and delimiter — default it to something like `CRE_{{ portfolio }}_{{ template }}`. The `_n` collision suffix is **app-side submit logic**, not part of the pattern. Add a name-length guard once the true limit is confirmed. Advances `14` **O14-9** and `07` §2.3.
- **O18-3 (run record):** add the missing run-parameter record — at minimum `irp_analysis.template_id` FK plus the submit-time parameters (analysis currency, currency scheme, vintage, treaty names, notes). Treaties are many-per-analysis, so likely a child table. This is the **prerequisite** for D11's grid columns and for §6's comparison view.
- **O18-4 (notes):** decide **where the note lives**. Workbench-only is simplest, but the same argument that drives the submission tag (`17` O17-6 — analysts aren't in the Workbench 100% of the time, so it must be findable in the platform and via API) applies equally here. Recommend evaluating whether Risk Modeler's analysis **description** field can carry it at submit, with a local copy cached for the grid. Settle the length with CIC.

---

## 5. The analyses grid — orientation, triage, delete (D4, D5, D6, D7)

Most of the session's remaining feedback. Mostly view-layer, with two real gaps underneath.

### 5.1 Orientation and triage (D4, D5)

- **Wendy's core complaint is orientation.** Coming back to the grid later: "Which job is this? Did I run it? [Is this] one I ran a month ago?" She asked for **date sorting** and for a way to **get to the platform** from a row — "if I need to go to the platform, I needed a way to get to the platform."
- **Filtering beats search.** Wendy: "No, I don't need search." Cheryl: "maybe not search as much as filtering." Their mental model is RiskLink: open an RDM, filter for FF, filter for HU. Ben's initial "search, sort, filter" was narrowed to **sort + filter**.
- **Failures on top.** Wendy: after kicking off twenty analyses "I just want to see three failed … immediately," along with why. Ben first proposed sorting failures to the **bottom**; both clients wanted them at the **top** (Cheryl: "We have failed at the top, I think"). Ben converged on **group-by rather than sort** so failures cluster as a block: "group by failed, sort by time, [most recent first] … and we can also filter by status."
- Cheryl's caveat on scale: this only matters once lists are long — "you've got really, really long lists" — which they will be, given a suite run produces nine-plus analyses at a time. Wendy: "especially [with] suites of things going on."
- **Reconciliation:** view-layer only; `irp_analysis.status_code` (§6 kind table: `pending`/`running`/`ready`/`error`) already supports grouping and filtering, and `inserted_at` supports the sort. The platform link-out needs `irp_analysis.irp_id`, which "resolves only after FINISHED" — so **the link must be conditional**, consistent with the `15`/`14` rule that links don't render until populated. → **O18-5**.

### 5.2 Failure reasons are still not surfaced — **second raising**

- The demoed failure showed the analyst nothing actionable. Ben: "we don't really have any information … we're not pulling in right now the proper [error], but we'll make that known." Wendy's expectation is explicit and specific: the grid should say **"no locations."**
- `14` **D13** already established this as a requirement: peril/portfolio mismatches are **expected, not errors** — a broad suite deliberately fails inapplicable sub-analyses, no loss means no charge — **"but failures must be surfaced with a reason."** This is now the second session to raise it.
- **Reconciliation:** §6 `irp_analysis` has `status_code` but **no failure-reason field**. The reason originates on the IRP side and must be captured by the poller when a job reaches `FAILED` and propagated to the analysis row (or read through the `irp_job` relationship in §8). Constitution Art. 5 puts that squarely in the **poller/worker**, never a route handler. Treat this as a requirement with an FR behind it, not a UI nicety. → **O18-6**.

### 5.3 Multi-select delete, with cascade (D6)

- **Cheryl's driver is hygiene:** "these 10 jobs failed and I expected them … I don't need to have them in my list." Interaction: tick, tick, tick, delete. Wendy confirmed it's common practice — you didn't get the result you wanted, delete and re-run with different treaties. Cheryl: "We try to keep it cleaned up … you go back and you look at something and [realise] that didn't need to be in there," with the obvious caveat that you check the failure was expected first.
- **Two scenarios, both confirmed by both clients:**
  1. **A real analysis exists on Moody's** → delete removes it **on the platform and** in the Workbench database. Ben asked directly whether the Moody's analysis should go too; Cheryl: "Yes."
  2. **A failed-to-submit record** (no Moody's analysis) → delete removes the **Workbench record only**.
  Either way the row leaves the grid.
- Ben: **"synchronous operation. We don't need a job for it."**
- **Reconciliation — this needs a second look. → O18-7.** The library's per-analysis delete *is* synchronous (`analysis.delete_analysis(id)`, per the 2026-07-14 change-log entry), which supports Ben's read, and constitution Art. 5 permits IRP **submission** on the request path. But **spec 003's RDM delete — the closest precedent — runs its synchronous IRP calls in a `delete_rdm` worker**, not on the request path. A multi-select delete is **N sequential synchronous HTTP calls**; ten ticked rows is ten round-trips inside one request. Recommend a `delete_analysis` **`rwb_job`** (add to the §13 `rwb_job_type_kind` seed) for the batch case, matching the RDM precedent, even if a single-row delete stays inline. Also settle **soft vs hard**: §6 already carries `deleted_at`, so the natural implementation is a soft delete plus a grid filter — but Cheryl's intent ("cleans it up") is satisfied either way, and soft-delete preserves the audit trail.

### 5.4 Rejected: a separate failed-job queue (D7)

Cheryl floated parking expected failures somewhere separate — "would it make sense to put those in a [separate queue]?" — reasoning that a failure with nothing stored on the platform is just a message to the user. The group concluded that **grouping and sorting by status (D4) plus the ability to clear them (D6) covers it** without a second surface. Wendy: "maybe sort by date and status." **Recorded as considered-and-rejected.**

---

## 6. Tags, deal status, and retention (D12, D13)

### 6.1 Tags confirmed, auto-population agreed (D12)

- Ben demoed tags applied three ways: **manually** on an analysis, **inherited from a template** (he had two tags on a template), and **auto-added by the Workbench** — he had thrown the submission name on as a tag and proposed a structured **`submission:<name>`** form. "I can automatically add things like [that] based on the context."
- Cheryl endorsed it and named the first set: **peril and region** — "the peril, the region, all those things make great sense" — "as a first start."
- Tags will be **displayed in the Workbench grid** to help identify what an analysis was for.
- **This merges with `17` O17-6.** The submission-level tag that `17` specified for grouping (so analyses are findable in the platform and via API when the analyst isn't in the Workbench) is the *same* mechanism as the `submission:<name>` auto-tag demoed here. Treat them as one workstream.
- **Reconciliation — O18-8.** §7 models `analysis_template_tag` (template → `irp_tag`) but there is **no analysis-level tag table**. Needed: either an `irp_analysis_tag` cache, or reliance on IRP-side tags applied at submit with a local cache for grid display — and per `17` O17-6, the **IRP-side** option is the one that satisfies the find-it-in-the-platform requirement. Note also that §7 currently describes `peril_code` and `region_label` as *"display metadata; used in auto-naming"* — with D12 they become **inputs to tagging as well**, so their status upgrades from display-only and their population needs to be reliable.

### 6.2 Deal status / "bound" — deferred, but capture the cheap parts (D13)

- Cheryl extended tagging to **submission status**: tag an analysis as belonging to a **bound** deal. Wendy connected it to **event response** — with bound deals tagged you could run a Hurricane Irma footprint across the **100 bound** portfolios rather than all **200 priced** ones. "I wouldn't want to do it for the 200."
- **Wendy's blocker is organisational, not technical:** "Without an integration to our CRM system … where it comes automatically" nothing sets the flag, so someone must go back and mark deals bound after the fact — "we're just not doing that right now." Cheryl agreed: "you just can't do it at this point … it's something we would have to do later."
- Ben added a design constraint: it "can't be a workbench side flag" if the value needs to be visible to someone working directly in Risk Modeler — unlike the event-rate active flag (§2), which is purely a Workbench display concern.
- **The retention thread underneath it.** Wendy laid out the real long-term need: deals are typically **one year**, some **six months**; records are retained roughly **three to five years after pricing**; and today the only handle is a RiskLink database named "Treaty 24" — "I know I can toss it." She wants to be able to say *any deal priced before this date, purge or archive it.* She was explicit this is **"a future sort of request"** and that Ben doesn't have to manage the whole lifecycle — the ask is to **capture the data now** so the purge becomes possible later. Ben: "That's very useful context … easy stuff to build."
- **Reconciliation — O18-9 (deferred, but scope the capture).** §4 `submission` already has `inception_date`, `treaty_year`, `renews_from_submission_id`, `submission_crm_id`, and an event-sourced `submission_status_code` (`ACTIVE` / `COMPLETED` / `CANCELLED`). Gaps: **no `BOUND` state** (and note Wendy's own uncertainty about whether bound is a status or a separate flag — "maybe it's a status"; Ben asked the same question and got no firm answer), **no expiration date or term length** (needed for the 6-month vs 12-month split), and **no purge/archive policy hook**. Recommend taking only the cheap capture-now items — **`expiration_date` or `term_months`** on `submission` — and leaving bound-status and archival out of MVP per D13. The `submission_crm_id` field is the eventual hook for the CRM integration Wendy named as the real unblocker.

---

## 7. Results viewing — Monday's workstream (reactivates `05`)

Ten minutes at the end, setting up 8/24. No decisions, but a clear brief and one concrete action.

- **What Ben intends to build before Monday:** an **analysis results view**, and a **submission-wide view** that becomes "the basis for" reporting. Plus grid cleanup and surfacing details not shown today, such as **treaties**.
- **Moody's own report is not the answer.** Ben pulled up the "exposure and analysis results detail" report live: "There's all metadata … that's really not helpful at all."
- **Results belong on their own page.** Cheryl: results will "open up a … different page," not be crammed into the grid — the grid's job is enough detail to know *which* analysis you're looking at ("a little more detail without [opening] that"), and the depth lives elsewhere. Especially true for comparison.
- **Broker-vs-own comparison** is confirmed on the list. Cheryl: possible in Risk Modeler today but "ugly … just a bunch of columns," and "not easy to look at it and say, oh [there's the difference]."
- **Wendy's comparison requirement is specific and is a data gap:** a useful side-by-side has to show **which software version each side was run in** — a broker running **RiskLink 25** versus another version — plus a quick **short PML** view for eyeballing whether two results are "horrendously different."
- **The action that matters: CIC sends screenshots first.** Wendy proposed and Cheryl agreed to send **screenshots of the views they use today** — an analysis view and a couple of comparison views — "right away," so Ben adapts from real artifacts rather than guessing, and can say up front what isn't feasible. Wendy: "before we send Ben off to do a bunch of [work]." Ben: "I would gladly take that."
- **Reconciliation:**
  - **O18-10 (comparison needs run-version data).** §9 `analysis_result_meta` carries `analysis_name`, `perspective_code`, `aal`, record counts and Parquet paths — but **no software/model version**. Own analyses could derive it from the template's model profile *if* **O18-3** lands; **broker** analyses arrive via RDM import and would need it captured at enumeration. Without this, Wendy's stated comparison requirement cannot be met. Reconcile with `05`.
  - **O18-11 (build the views).** Analysis results view + submission-wide view, seeded by CIC's screenshots. Depends on **O18-3** for the "what was actually run" columns.

---

## 8. Carried-forward (not advanced this session)

- **Grouping (`17` O17-6 / `15` O15-6).** 8/20 named grouping as the 8/21 topic; it was **displaced** by execution and the grid and did **not** come up. The submission-tag half advanced only indirectly via D12 (§6.1). Still owed, along with Ben's **technical write-up** to Cheryl (`15` O15-7).
- **Currency "on the fly"** (`17`, the other 8/21 agenda item) was not revisited as a topic — but the demo (§3) exercised submit-time currency selection, which is the substance of it.
- **Event-rate-scheme dropdown auto-populate bug (`17` O17-9)** — not fixed; the active flag reduces list noise but does not make the list populate on model-profile selection.
- **Accumulation (`17` O17-10)** — not discussed.
- **"Template" vs "analysis settings" naming (`17` O17-8)** — not revisited; Ben used "template" throughout this session, which is weak evidence for settling on it.
- **Excel import/export (`17` O17-4, remaining half)** — still tabled, revisit ~September.
- **Currency scheme/vintage tables (`17` O17-1), env-var defaults (O17-3), unique names (O17-5), read/write matrix (O17-7), Risk Modeler non-admin visibility (O17-12), currency cadence (O17-13)** — all unchanged and outstanding. **O17-7 now needs an amendment** for the active-flag write affordance (§2.3).
- **`treaty_name_pattern` removal (§7, `16` O16-6)** — still outstanding; this session's run flow is further evidence.
- **Multi-peril breakout in-place option filtering** — still the sign-off blocker from `13` O12-1; no update.

---

## 9. Open questions & follow-ups

- **O18-1** — **Event-rate-scheme active/inactive flag (Workbench-owned).** Store every synced scheme; gate picker visibility on a manually-curated flag. **Prefer a side table** (`event_rate_scheme_visibility`, keyed on `irp_id`, with `updated_by`/`updated_at`) over an `is_active` column on `irp_event_rate_scheme`, to preserve §10's "sync-only writes" invariant and survive any re-sync strategy; if the column route is taken, the sync upsert **must** preserve it. Surface as a display/hide checkbox on the analysis-metadata view. Scope: **event rate schemes only** (D3). While here, cache the scheme's **`model_version_code`** and confirm the model profile's data/model version, so the curation UI is legible and a coarse pre-filter stays possible. **Closes `17` O17-11**; partially serves `16` O16-5. (§2) *Ben — `DATA_MODEL.md` §10; CIC — curate the initial list.*
- **O18-2** — **Analysis naming: `CRE` prefix, delimiter, `_n` collision suffix, length guard.** Default `analysis_template.auto_name_pattern` (§7) to `CRE` + portfolio + delimiter + template; implement the `_1`/`_2` suffix as app-side submit logic (**not** blocking); add truncation/validation once the real Risk Modeler limit is confirmed. Advances `14` O14-9 / `07` §2.3. (§4.1–4.3) *Ben.*
- **O18-3** — **Record what was actually run on `irp_analysis` (§6). ⚑ Prerequisite for most of this session.** Today §6 stores no `template_id`, currency/scheme/vintage, treaty list, or notes — yet currency moved to submit time (`17` O17-2), treaties to run time (`16` O16-6), and notes are new (D9). Add a template FK plus a run-parameter record (treaties likely a child table). Without it, D11's grid columns, the enriched description, the notes field, and the results comparison (O18-10/O18-11) are all unbuildable. (§4.5) *Ben — `DATA_MODEL.md` §6/§7.*
- **O18-4** — **Submit-time notes field.** Free text captured on the submission form, applied to every analysis in the batch (suite **and** individual template execution). **Decide where it lives:** Workbench-only vs written to the Risk Modeler analysis **description** at submit (the `17` O17-6 argument — findable in the platform / via API — applies here too), with a local cache for the grid. **Settle the length with CIC** — both clients asked for short (~25–50); Ben proposed 100. (§4.4) *Ben + Cheryl/Wendy.*
- **O18-5** — **Analyses grid: group-by-status (failures first), date-descending, status filter, platform link-out.** View-layer on existing §6 fields; filtering over search per D5. Link-out must be **conditional on `irp_analysis.irp_id` being resolved** (post-FINISHED only). (§5.1) *Ben.*
- **O18-6** — **Surface analysis failure reasons. ⚑ Second raising.** `14` D13 required expected peril/portfolio mismatches to fail **with a reason**; the demo showed none. §6 has no failure-reason field. Capture the IRP failure detail in the **poller/worker** (constitution Art. 5 — never a route handler) and propagate it to the analysis row or read it through §8 `irp_job`. Target message quality: Wendy's "no locations." Should carry an FR. (§5.2) *Ben.*
- **O18-7** — **Multi-select analysis delete + cascade — decide request-path vs worker.** Behaviour agreed (D6): platform analysis exists → delete on Moody's **and** locally; failed-to-submit → local row only. Ben called it synchronous/no-job, and `analysis.delete_analysis(id)` is indeed synchronous — but **spec 003's RDM delete runs its synchronous IRP calls in a worker**, and a multi-select is N sequential round-trips in one request. Recommend a `delete_analysis` **`rwb_job`** for the batch case (add to the §13 `rwb_job_type_kind` seed), single-row possibly inline. Also settle **soft (`deleted_at`, already in §6) vs hard** delete. (§5.3) *Ben.*
- **O18-8** — **Analysis-level tags + auto-population — merge with `17` O17-6.** No analysis-level tag table exists (only §7 `analysis_template_tag`). Add an `irp_analysis_tag` cache and/or apply **IRP-side** tags at submit (the option that satisfies findability in the platform / via API). Auto-tag set: **`submission:<name>`, peril, region**. Note that §7 `peril_code` / `region_label` change from "display metadata" to **tagging inputs**, so their population must be reliable. Display tags in the grid. (§6.1) *Ben + Cheryl.*
- **O18-9** — **Deal status ("bound"), term dates, and retention — DEFERRED; take the cheap capture now.** Per D13, bound-status tagging waits on a CRM integration (Wendy: "we're just not doing that right now") and can't be a Workbench-only flag if Risk Modeler users need it. Unresolved even in principle: **is "bound" a status or a separate flag?** — both Ben and Wendy raised it, neither settled it. Recommend adding only **`expiration_date` or `term_months`** to §4 `submission` (1-year default, 6-month cases exist) so purge/archive-by-pricing-date is possible later; leave `BOUND` state and archival out of MVP. `submission_crm_id` is the eventual CRM hook. (§6.2) *Ben (ideas) / CIC (CRM decision).*
- **O18-10** — **Capture the software/model version per analysis, for comparison.** Wendy's broker-vs-own comparison requires showing which version each side ran (e.g. RiskLink 25 vs other). §9 `analysis_result_meta` has no version field; own analyses can derive it via O18-3, broker analyses need it captured at RDM-import enumeration. Reconcile with `05`. (§7) *Ben.*
- **O18-11** — **Build the analysis results view + submission-wide (reporting-basis) view, seeded by CIC screenshots.** Results open on their own page, not in the grid. Depends on **O18-3**. Reactivates `05`. (§7) *Ben.*
- **O18-12** — **CIC to send screenshots of current RiskLink results views.** An analysis view plus a couple of comparison views, "right away," so Ben builds from real artifacts before Monday. **Highest-priority client action from this session.** (§7) *Cheryl / Wendy.*
- **O18-13** — **Confirm the Risk Modeler analysis-name character limit.** Ben believes 64 and flagged it as unverified; Wendy believes CIC has longer names and notes profile names alone run ~50 chars. Determines whether O18-2 needs truncation. (§4.3) *Ben.*
- **O18-14** — **Amend the analysis-metadata read/write matrix (`17` O17-7).** `17` D1 set that screen as read-only; D1 here adds the **first write affordance** on it. Record the distinction: the active flag is **Workbench state about an IRP object**, not an edit to the IRP object, and the screen still never writes §10 outside the sync. (§2.3) *Ben.*

**Advances / closes (from prior sessions):** `17` **O17-11** event-rate list mismatch — **CLOSED** (root-caused as the Excel-only model-version-code mapping; resolved by O18-1); `17` **O17-4** — **duplicate-and-edit half CLOSED** (built + accepted), Excel half still deferred; `17` **O17-2** submit-time currency — **validated in the running demo**, and its downstream consequence surfaced as O18-3; `17` **O17-6** submission tag — **merged into O18-8**, but the grouping half **did not advance** (displaced this session); `17` **O17-7** read/write matrix — **needs amendment** (O18-14); `17` **O17-9** dropdown auto-populate — **not fixed**; `16` **O16-5** metadata filtering — partially served by O18-1; `16` **O16-6** treaty-at-run-time — reinforced by the demo, `treaty_name_pattern` removal still outstanding; `14` **D13** failures-with-a-reason — **raised a second time, still unbuilt** (O18-6); `14` **O14-9** auto-naming — advanced by O18-2; `05` results/comparison — **reactivated** as Monday's workstream (O18-10/O18-11).

**Next session:** **Monday, August 24, 2026** — **results viewing**: the analysis results view, the submission-wide view, and broker-vs-own comparison. Naming and grid layout to be re-reviewed once built ("We'll look at this again on Monday"). CIC sends the RiskLink screenshots beforehand. Wendy left for another call at the end of this session; Cheryl and Ben closed out immediately after.
