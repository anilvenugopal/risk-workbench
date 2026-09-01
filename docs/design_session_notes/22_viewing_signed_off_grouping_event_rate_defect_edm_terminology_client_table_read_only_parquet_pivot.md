# IRP Workbench — Design Notes: Viewing and Comparison Signed Off, Grouping Produces the Wrong Numbers, EDMs Contain Exposures Only, the Client Table Reverses to Read-Only, and Parquet Replaces the Paginated API

**Source:** Design session, August 28, 2026 (~80 min against a one-hour slot — Ben flagged being "13 minutes over" and Cheryl stayed on, "you're my last meeting of the day") — Ben Bailey (PremiumIQ) demoing, Cheryl TeHennepe (CIC). **Wendy Hayes was not present**, out for the week as `19`, `20` and `21` recorded. Cross-checked against the full transcript (1,298 cues). Two halves: **the first ~40 minutes are the Workbench demo `21` did not get** — viewing, comparison, grouping and the analyses grid, the first build shown since 8/26 — and **the last ~40 minutes return to loss export**, with answers Cheryl brought back from Cheng against the asks `21` **O21-13** left open.
**Status:** Working design notes. **Viewing and comparison are effectively signed off** — Cheryl's verdict was "all of this looks great … I think the experience is great," and her only change request in forty minutes was to right-justify the return periods, which closes `19` **O19-8** and **O19-9** (the caps and the selection-order contract were both confirmed with the client for the first time) and confirms `20` **O20-6** and **O20-8** as built and accepted. **Grouping is the opposite: the experience passes and the output is wrong.** The Workbench passed **both** event rate schemes into a group where the manual Risk Modeler flow presents **one**, and gross pure-premium losses came out **~$8M apart** — Cheryl's explanation is that grouping across two rate sets lists the same event twice with two rates instead of aggregating, so the AAL looks deceptively close while "your EP curve will look wonky." **The event rate scheme is a group input, not something to derive**, and FR §6's single "invalid groupings show error messaging (e.g. mixing DLM and HD)" row now has a second, more likely failure mode it does not describe. Three further outcomes reshape prior notes: **`21` D2–D4 are REVERSED — the client table is read-only**, because exposure work stays in the workflow tool and Cheng creates the client ID there, which retracts `21` **O21-1**'s INSERT requirement and leaves `18` **O18-1**'s `workbench_is_active` as the *only* carve-out to the "app never writes to the reference cache" invariant; **Ben abandoned the paginated API for Parquet**, a job request returning one Parquet file per perspective, pre-fetched so `21` **D18**'s integrity check runs on demand — which **settles** `20` **O20-11**'s Parquet question and `21` **O21-9**'s retrieval economics in one move; and **the stochastic/historical blocker cracked open mid-session** when Ben clicked an event in the Risk Modeler UI, watched the network calls and found **reference data APIs** returning event type, narrowing `21` **O21-5** from "unresolved" to "resolved by retroactive enrichment, pending volume." Cheryl also issued a **terminology correction with a UI consequence**: EDMs contain exposures only, an analysis never "exists in" an EDM, and what the Workbench shows is a relationship. Separately, and worth acting on: **`18` O18-3 / `20` O20-1 — the five-session run-facts blocker — appears to have been CLOSED by specs 010/011** (`irp_analysis` now carries `irp_portfolio_id`, `analysis_template_id`, `full_name`, `submitted_settings` and `loss_results`), which is why today's grid could show Portfolio, Template and Currency at all. **Closes** `19` **O19-8**, **O19-9**; **resolves** `20` **O20-3** by removal (Ben declined the analysis-type code map). **Advances** `20` **O20-11**, **O20-15**, `21` **O21-5**, **O21-9**; **retracts** `21` **O21-1**; **refines** `21` **O21-4**. **Does not advance** `18` **O18-4** (notes field — **fourth** session unraised), **O18-6**, **O18-10**, `20` **O20-4** (region granularity — second session untouched), **O20-7**, **O20-14** (HD), `21` **O21-8**, **O21-12**.
**Related:** `21_export_walkthrough_workflow_tool_ruled_out_client_table_perspective_validity_stochastic_historical_blocker.md` (§4 client table O21-1, §6 perspectives O21-3, §7 TY O21-4, §8 review checks O21-8, §10 event type O21-5, §11 ELT economics O21-9, §12 reference data O21-6, §15 asks O21-13), `20_results_views_demoed_grid_columns_name_split_ep_type_selection_export_first_requirements.md` (§ grid columns O20-1/O20-2/O20-3, region O20-4, EP type O20-6, currency guard O20-8, defects O20-10, export O20-11/O20-12/O20-15), `19_loss_results_viewing_no_elt_live_fetch_view_vs_compare_merged_grid.md` (§ view-vs-compare O19-8/O19-9, results page O19-7, AAL column O19-2, presentation O19-10, deployment O19-15), `18_suite_execution_event_rate_active_flag_analysis_naming_notes_tags_grid.md` (§ run facts O18-3, naming O18-2, grid O18-5, notes field O18-4), `17_submit_time_currency_vintages_no_suites_of_suites_duplicate_grouping.md` (§ grouping O17-6), `15_geohaz_dlm_closeout_edm_rdm_notes_tables_event_rate_grouping.md` (O15-6 build grouping), `../DATA_MODEL.md` (§1 named connections; §6 `irp_analysis` — `is_group`, `group_parent_id`, `irp_portfolio_id`, `analysis_template_id`, `full_name`, `loss_results`, `submitted_settings`; §8 `irp_job_type_kind` — `grouping`/`export`; §9 `analysis_result_meta` + `elt_record_count` + `perspective_code` + `result_export`; §13 seed checklist; §14 open decisions), `../FUNCTIONAL_REQUIREMENTS.md` (§6 Grouping — all five rows; §7 Results Management — Organizing & displaying, Comparison, Delivery, MVP out-of-scope), `../pm/FR_SIGNOFF.md`, `../../specs/011-analysis-results/spec.md` (O-07 perspective set, O-11 expanded row, FR-009/FR-013/FR-016; grouping explicitly deferred to "Iteration 9"), `../../specs/010-analysis-execution/`, `.specify/memory/constitution.md` (Art. 5 — result work in workers; Art. 8 — a finished run reports what it actually ran with), `../../../../CIC/minutes/Risk_Modeler_Interface_Design_Minutes_8-28-26.md`, `../../../../CIC/transcripts/Risk Modeler Interface _ Design 8-28-26.vtt`

> Decision IDs (**D1**–**D32**) below refer to the tables in the 8/28 minutes. Open-item IDs are **O22-n**.
> *Transcript note: the auto-transcription remains lossy and garbles the same terms consistently. Interpretations reflect context: "OAP" = **OEP**; "working access" = **working excess (`WX`)**; "event array scheme" = **event rate scheme**; "region parallel simulation set" = **region-peril simulation set**; "Rich Mueller" = **Risk Modeler**; "arcade file" = **Parquet file**; "data branch" = **Data Bridge**; "EOT" = **ELT**; "AL" = **AAL**; "pale region engine" = **peril / region / engine**; "Permis event info" = the **RMS event info** database; "you type last tables" / "lost details" / "ultimate levels" = **loss tables / loss details / output levels**; "the wrist size" = **risk size**; "these trees" = **these treaties**; "payroll" = **peril**; "CRE" = **Cincinnati Re**. Stray single-word cues transcribed as names ("Kim", "Karen", "Nick", "Ben", "Good morning", "Hello?") are artifacts of short interjections and carry no content — do not read them as a third participant. **One term is load-bearing and interpreted rather than certain:** the required API request attribute Ben reverse-engineers for grouping is rendered "region parallel simulation set" and is read throughout as **region-peril simulation set**; it is the parameter at the centre of **O22-1** and should be confirmed against the Risk Modeler API contract by name before any fix is written. Names referenced but not present: **Cheng** (CIC — owns the workflow tool; Cheryl consulted him between sessions and returned with D25, which is the first time an `O21-13` ask has come back answered), **Wendy Hayes** (out), **Nagi** (VDI / deployment).*

---

## 0. TL;DR

The first session in three where a build was shown, and the contrast between the two halves is the story: everything Cheryl looked at in the results views she accepted, and the one thing she looked at *underneath* — grouping — was wrong by eight million dollars.

- **Viewing and comparison: signed off. ⚑** Forty minutes, one change request ("can we make the return periods right justified?"), and a verdict — "all of this looks great … I think the experience is great." Defaults (`RL` + OEP) confirmed (D1), uncapped multi-view accepted (D2), the column-group shading accepted as the legibility fix (D3), copy-to-clipboard confirmed unformatted / in ones / full precision (D6), the 5-pair comparison cap accepted (D7), the selection-order base contract explained and accepted (D8), cross-currency comparison blocked and accepted (D9). **Closes `19` O19-8 and O19-9; confirms `20` O20-6 and O20-8 as built.**
- **THE DEFECT — Workbench grouping does not match Risk Modeler (D13). ⚑⚑** Ben grouped one stochastic and one historical analysis. The manual Risk Modeler flow showed **one** rate-scheme row with historical defaulted; Workbench passed **both** through. "Even just the pure premium gross losses off by 8 million." Cheryl: "**You can't group with two different rate sets** … the same event listed twice with two different event rates. And what we want is 1 event with the losses aggregated and one event rate." And the tell: "your average daily loss is going to look really similar … It's going to be close, but it's not going to be the same. **And your EP curve will look wonky.**" → **O22-1**.
- **Root area, in Ben's words:** he auto-selects the event rate scheme and computes the "required API request attribute called region peril simulation set" by "basically reverse engineer[ing] how Risk Modeler builds that list" from the members' model profiles and rate schemes — an implementation "built for a specific use case … that I don't think is working properly for what we want to do **because we did not get to pick event rate scheme at all**." → **O22-1**.
- **The validation method is agreed and should go in the spec (D14).** Ben: "run examples manually and then run them in Workbench and compare the outputs." Cheryl: "Yep, that's what I would do." → **O22-1**, **O22-2**.
- **Grouping is unspecced and absent from the main line.** Spec 011 defers grouping to "Iteration 9"; `app/` has no group-creation path (`is_group` is derived from Risk Modeler payloads for broker enumeration only). Meanwhile **FR §6's five rows are all "Not implemented"** and one of them — "invalid groupings show error messaging (e.g. mixing DLM and HD analyses)" — now needs the *event rate scheme* case, which is both likelier and silent. → **O22-2**.
- **A group needs more than an analysis does (D16, D17, D18).** Cheryl: "the other thing that we really will want in a group that we don't need in an individual analysis is **the list of the analyses that were included in the group**" — driving case, picking up a colleague's work: "Wendy's off on vacation, I have to look at something she did. I don't know inherently from looking at that group what's in it." Editing membership is wanted but explicitly a later round. And group metadata is currently mis-sourced — "some are missing, so we need to pull it from the right spot." **FR §6's "a group is treated like any other analysis" is therefore wrong as written.** → **O22-3**.
- **Group builder: name sort and search (D15).** Cheryl: sorting alone would do, search helps cull — "somewhere you might have 50 or 75 analyses to weed through. Well, that's kind of ugly." Note this does **not** contradict `18` **O18-5**'s "I don't need search" ruling, which was about the analyses grid where filtering does the work. → **O22-4**.
- **TERMINOLOGY, with a UI consequence (D23). ⚑** Cheryl: "**the EDMs contain exposures only** … in the risk link world, they never contain losses … to say that an analysis exists in an EDM … it would never exist in an EDM. It would always exist in an RDM in RiskLink." Her reason was communication: "if you use that phraseology with other analysts, **they'll be completely lost** … I just want to make sure that as we're communicating this out, that people can come along with what it is we're trying to do." Ben accepted and owes copy on the EDM screen: these analyses were **run against portfolios in** this EDM. → **O22-5**.
- **Groups belong on the EDM page, in line (D24).** Today a group created from the EDM page vanishes, because groups relate only to submissions. Ben will query groups whose member analyses relate to the EDM. On presentation Cheryl floated a separate group section; Ben showed the engine column already distinguishes them with the group name spanning Portfolio+Template; Cheryl: "I'm fine with that. I'm fine with that." → **O22-5**.
- **`18` O18-3 / `20` O20-1 looks CLOSED — confirm and retire it. ⚑** Five consecutive notes carried "record what was actually run on `irp_analysis`" as the blocking gap. §6 now has `irp_portfolio_id`, `analysis_template_id`, `full_name`, `submitted_settings` (currency code/scheme/vintage, event rate scheme, thresholds) and `loss_results`. That is why today's grid could render Portfolio, Template, Currency and AAL at all, and why `20` **O20-8**'s mixed-currency guard worked on screen. → **O22-6**.
- **Analysis-type abbreviation resolved by removal (D20). Closes `20` O20-3.** Ben declined to build the code map — "that's a map I'd have to manually maintain" — and moved analysis type into the expanded detail instead. No objection. Note spec 011 **O-11** already lists analysis type in the expanded row, so this is a confirmation, not a change.
- **Treaties join the analysis view (D21) — the last dependent of the run-facts gap.** Cheryl: "that would be helpful to see if treaties were applied. Yes, I agree." §6 records no applied-treaty set; `21` **O21-4** already needed it for the TY export, and it now has a viewing consumer too. → **O22-7**.
- **Submitted-vs-returned metadata: REJECTED (D22).** Ben wanted the developer-facing check — "what I submitted and what the actual analysis returned." Cheryl declined: "I'm not sure that I feel like it needs to be there. I feel like the analysis settings feeds that results information … **I have naive confidence that the model is doing what it's supposed to do.**" The irony worth recording: `submitted_settings` **already exists** in §6 for exactly this purpose (Article 8). Record the display as a deliberate non-requirement so it is not re-proposed. → **O22-8**.
- **THE REVERSAL — the client table is read-only (D25). ⚑ Retracts `21` O21-1's write requirement.** Cheryl, after consulting Cheng: "I think we need to view the client table. **I don't think we have to add clients.** … in the workflow that we talked about, we're going to do all the exposure work … in Cheng's tool, the one we have right now, we're going to continue to use that, **and we would have to set up a client ID at that point**. … And in the case that somebody needs to do that, they can go into workflow tool and do it or SQL and do it. It's fine. **So we can take that off the list.**" → **O22-9**.
- **Cheng's add-client logic, recorded for schema shape only:** last ID + 1, with a convention that IDs above ~8,000 are test entries excluded from the running number ("our client list doesn't go that long … we assume it's a testing group"); the `is valid` flag "we just set that to a default that is manually maintained off the system," confirming `21` **D5**.
- **PARQUET replaces the paginated API (D28). ⚑ Settles `20` O20-11 and `21` O21-9.** Ben: "We were looking at the API yesterday, which … **I'm going to not do that.** … I figured out how to download those Parquet files properly." A job request names the analysis, the **ELT at portfolio output level** and the **perspective codes**; a zip comes back with one Parquet file per perspective, queryable "programmatically with Python in a very similar way that we query databases." → **O22-10**.
- **Pre-fetching removes the review wait (D29). Resolves the sharp edge `21` O21-9 left.** "I'm going to have this data ready regardless **so that we don't have to do the waiting** … If you want to do the validate step like you do in the workflow tool, then we can do that **on demand**. We don't have to wait." Cheryl: "Okay. It's just there." → **O22-10**.
- **THE BLOCKER CRACKS — reference data APIs return event type (D30). ⚑ Narrows `21` O21-5/O21-6.** Ben first confirmed the negative twice: not in the Parquet ("the event type is not here for me"), and **not selectable on export** — "Loss tables or the ELT output level, **it doesn't give you options** … So I can't say add Type, if I could, then it would be solved." Then, mid-session, he clicked into an event in the Risk Modeler UI, saw its type, watched further calls fire and landed on it: "**Reference, reference tables, there it is.** … I use reference data APIs. I can figure this out on my own. I might not have to query this table, **I can clearly get this via API**." → **O22-11**.
- **The plan is retroactive enrichment, and Cheryl's cheaper idea does not work.** She suggested "just include type as one of the fields" in the extract; Ben established live that the extract has no field selection, so: "get the Parquet file, get every event in the Parquet file. **Use the API to get every type for every event.** Update the Parquet file itself with a new column that has the type for every event. And then use that." Open cost: "I literally need to get the type for **every single one**." → **O22-11**.
- **Business rules will not be trusted as the split (D31).** Moody's AI assistant told Ben an EP ELT is stochastic-only and that analysis type, model profile and event rate scheme all matter. Ben: "I don't trust him 100% … **we shouldn't trust these business rules.** The right thing to do is actually do the event validation and split." Cheryl corrected a conflation that had been in play since `21`: the historical **rates** Ben has been running are "historical meaning **long-term event rates**," nothing to do with historical **events**.
- **The reference-data disagreement is unresolved and needs Moody's, not inference.** Cheryl: "I don't actually believe that those reference tables exist on Data Bridge unless we have them build them. And we can do that … **I could be completely wrong.**" Ben: "Those reference tables **have to exist somewhere**. … they definitely live on the cloud and they definitely are used when you run an analysis … Whether or not it's available to you is a question of are you a Data Bridge product user." He cannot see `event info` in his Data Bridge SQL access despite it being documented alongside the EDM and RDM schemas — "I think it's a matter of my permissions." → **O22-11**.
- **TY's source located (D26). Refines `21` O21-4.** Cheryl: "that TY perspective is actually from a RiskLink table … There's a treaty table that has the sum of all the treaty losses, TY, in it, and it's **`RDM_TREATY`**. That's where the losses live in RiskLink. So there should be an API call to get that data back." Names are codes and join to a treaty-description table; she demonstrated the lookup live, pulling attachment, treaty type and name for a treaty ID. → **O22-12**.
- **TY deferred again — this time at Ben's request (D27).** "There's just a lot of things going on, so I appreciate your explanation, but **it didn't really land for me**." Cheryl set the order: "Why don't we get through this with the portfolio level, and then maybe at that point we can circle back to the treaty perspective … it'll be a matter of just **substituting where you're getting the data from**, and everything else should be the same." Consistent with `21` D12's "part B".
- **A test case that takes no loss is blocking two validations. ⚑** Ben's two WX treaties return nothing; on screen they checked lines of business, attachment, 100%, effective date — "looks like it should probably work" — and ruled currency out. Cheryl owns it: "let me take a look at that data and I can provide to you a treaty that I know will take loss." Consequence: the missing TY Parquet is **not** evidence TY is broken — "I wouldn't assume that it's not working yet until we figure out how to get losses out." → **O22-14**.
- **Scope, unchanged but restated (D32).** Cheryl: "we probably have to get through DLM and then we've got to figure out what we're going to do with the HD stuff **because we're not set up for the HD results right now**." And on the extract's `scope` field: "we want property. **We have to talk about workers comp later.**" `20` **O20-14** untouched.
- **A live bug with a modelling tail.** Some group submissions failed because Ben resolves members **by name**: "I'm constantly rebuilding my database, losing my workbench data, resubmitting analyses with the same names, which is valid in Risk Modeler. I have duplicate names in these EDMs." Fix is to use analysis IDs, which he holds — but this is the first *live* consequence of `20` **O20-2**'s unresolved `_n`-suffix question. → **O22-16**.
- **Deployment is now the stated priority.** "That's why we need to get deployment done next week and get you guys in it so we can get a full month … of testing and feedback." Ben will chase Nagi "today, and try to get something set up with him on Tuesday or very early Monday … I'm gonna start being annoying about it." Cheryl: "I know, it's hard to get on his calendar." `19` **O19-15** unchanged and now urgent.
- **Next week booked; halfway point.** Both out Monday 8/31. Tue 9/1 11:30 ET, Wed 9/2 10–11 ET, Thu 9/3 3 ET, Fri 9/4 10:30–11 ET. Ben sending invites. His own accounting: "What have we done in one month, and we have that exact same amount of time left … We cranked out a ton of work"; a couple of days behind his target of finishing the end-to-end flow by today. Cheryl: "That's why you have an aggressive schedule though, so that when you're behind, you're actually on time."

**The shape of the reconciliation this session forces:** `21` left the export design with four unresolved cost centres — how to get a full ELT, how to split it, where the reference data lives, and a client table the app had to write. **Three of the four move today, and two of them move by getting cheaper.** Parquet plus pre-fetching collapses `21` **O21-9** into an implementation detail and vindicates §13's "Read Parquet; write to LOSS DB" framing; the reference data APIs mean **O21-6**'s "fifth named connection vs. snapshot into the Workbench" question may not need answering at all, because the join can be an API call rather than a SQL connection; and the client table stops being a write target entirely, which retracts the second carve-out to the reference-cache invariant that `21` **O21-1** introduced and returns `18` **O18-1**'s `workbench_is_active` to being the only one. **What replaces them is a build defect in a feature that has no spec.** Grouping has been carried as an open item since `15` **O15-6**, was deferred out of spec 011 to "Iteration 9," is absent from the merged main line, and has now been demoed to the client with wrong numbers — while FR §6 still describes it in five unimplemented rows, one of which names the wrong invalid-grouping case. The sequencing that follows is: **fix and spec grouping** (**O22-1**, **O22-2**), because it is the only thing shown to the client that produces incorrect output; then **confirm and retire the run-facts blocker** (**O22-6**), because five notes have carried it and the schema appears to have moved past it; then export.

---

## 1. What shipped from `19`/`20` — most of the results-view backlog, and it was accepted

`21` recorded a session where nothing was demoed. This one is the correction, and the delivery rate is high.

**Delivered and accepted today:**

- **Portfolio and Template as separate columns** (`20` **D4** / **O20-1**'s dependent). Cheryl: "Oh yeah. I like that." → D19.
- **The full analysis name in the expanded view** — §6's `full_name`, exactly as spec 010 T-04 models it.
- **Column-group shading and vertical rules** (`19` **O19-10** / the `20` density pass). "I added some more grey colour here … there's like a clear bolded bar differentiation between the EP curves and the AAL standard deviation." Cheryl: "I agree." → D3. Horizontal rules were not added and were not asked for.
- **One EP type at a time, OEP default, AAL and standard deviation alongside** (`20` **D11** / **O20-6**). Cheryl: "No, OEP is great." → D1.
- **Copy-to-clipboard without the merged header** (`20` **O20-6**'s stated motivation). "Given we're only viewing one EP type at a time … we don't run into that issue that we used to have." Cheryl: "I don't think it needs to come over formatted." → D6.
- **The mixed-currency comparison guard** (`20` **D17** / **O20-8**). Ben demoed the block; Cheryl: "Yep." → D9.
- **The comparison builder** (`19` **D17–D20** / **O19-8**, **O19-9**) — cart view, 5-pair cap, percent-change column, base by selection order. First time the client has seen it. → D7, D8.
- **Uncapped N-up viewing with sideways scroll** (`19` **O19-8**'s unconfirmed cap). "I don't really have a cap on the number I'm viewing." Cheryl: "Yeah, it's great." → D2.
- **Analysis type relocated out of the grid** — resolves `20` **O20-3** by removal. → D20.

**Not delivered, deliberately:**

- **Drag-and-drop reordering** (`19` **D15** / **O19-10**). Ben proposed deferring "a couple weeks from now, or like a nice to have, just to focus on the other core functionality." Cheryl: "We have a way to move them around. That's great. Drag and drop would be better. I agree. **It doesn't need to take priority over other things.**" → D4.

**Still not raised:**

- **`18` O18-4 — the submit-time notes field. FOURTH consecutive session unraised.** `19` called it one session overdue, `20` two, `21` three. It has not been spoken aloud since `18`. At this point the question is not "when is it built" but **"is it still wanted"** — put it on an agenda or retire it.
- **`20` O20-4 — region granularity.** A second full session without progress, after `20` flagged it as the one open design question of that session. Region is a column on the grid Cheryl accepted today, so the coarse value is shipping by default.
- **`20` O20-7 — analyst attribution ("run by").** In spec 011 **O-11**'s expanded-row list; not mentioned today.
- **`20` O20-10's defects** — the units-reset and lost-selection issues. Not mentioned; and a **new** navigation defect joins the list (§7.3, **O22-15**).

---

## 2. Viewing and comparison — signed off (D1–D9)

Recorded in full because this is the first client sign-off on the results layer, and because the acceptance is broad enough to close two `19` open items.

### 2.1 The setup

Ben opened on a submission carrying several EDMs, **eleven executed analyses including a couple of cross-EDM groups**, and the RDM Cheryl supplied — the first time the merged grid (`19` **D11** / **O19-6**) has been exercised at that mix. "I don't think I've made any other changes since we demoed a couple days ago" outside submissions and analysis results.

### 2.2 Defaults, density, legibility (D1, D2, D3)

- **`RL` (pre-cat net) and OEP are the defaults, in both the full and condensed views** — confirming `20` **D9** and **D11**. Ben offered AEP; Cheryl: "No, OEP is great." OEP and AEP remain mutually exclusive in both places.
- **No cap on the number viewed.** Columns condense to a minimum width then scroll sideways; Ben showed billions-scale values fitting. Cheryl: "Yeah, it's great." **This confirms `19` O19-8's viewing cap as "none," which had been Ben's ~10 soft guideline and was never agreed with CIC.**
- **Currency renders per column** and, for viewing, is not a factor — "USD versus CAD for viewing … that's not a factor." Consistent with `20` **D17**'s asymmetry (viewing permits mixed currency, comparison does not) and with FR §7's currency column.
- **Shading accepted as the legibility fix.** Grey banding top and bottom produces "a clear … bolded bar differentiation between the EP curves and the AAL standard deviation." Cheryl: "I agree."

### 2.3 Copy-to-clipboard (D6)

Confirms `19` **D13** and closes the merged-header defect `20` **D11** was partly motivated by. Behaviour agreed: **unformatted, in ones, full decimal precision**, with display rounding independent of the export. Ben: "It's always exported in ones, also with decimals … there's a lot of decimals that are obviously not being displayed here in the Excel … but it's sending the full data." Cheryl: "I don't think it needs to come over formatted. It doesn't need, I mean, none of that, right? … Perfectly fine."

Note this makes FR §7's "displayed units never auto-switch; a ones/thousands/millions selector controls them" (`19` **D16**) a **display-only** requirement — the clipboard is always ones. Worth stating explicitly in the FR row, because "in millions with the decimal points" was Cheryl's phrasing for the *display* and could be read as applying to the paste.

### 2.4 Comparison (D7, D8, D9)

- **The 5-pair cap holds.** Ben capped it "almost for visual reasons. I'm not opposed to expanding it if we think we need it." Cheryl: "Nope." **Closes the second half of `19` O19-8.**
- **The base contract survived contact with the client — after a misunderstanding worth recording.** Cheryl assumed she could not control the base because she could not reorder the list: "What happens if your base is after your comparison in the list? Can you add them one at a time so that you can choose which one is the base? **Because I can't affect this sort order.** So if I want historical as my base and stochastic as my comparison, is there a way for me to do that?" Ben: selection order, and the base is tagged. Cheryl: "Ohh, cool. Yep, I see that now … That's cool. Very, very good." **Closes `19` O19-9 — but the misunderstanding is the design signal: the contract is invisible until the tag appears.** Whatever affordance marks the base must be legible *at selection time*, not only after the comparison renders.
- **Percent change is (comparison − base) ÷ base × 100**, restated by Ben as "actual minus expected over expected" and spot-checked live by Cheryl against the numbers ("420 to 410 is a decrease. Yep. Looks good.").
- **Cross-currency comparison is blocked**, demoed, accepted.

### 2.5 Reconciliation — **O22-13** (perspectives) and the sign-off itself

The FR rows this session satisfies are in §7 **Organizing & displaying results** and **Comparison**, all currently "Not implemented": the merged table, the currency/AAL columns, the inline condensed row, the units selector, the left-to-right ordering, "up to ~5 analyses are consumable on screen" (contradicted — viewing is uncapped and Cheryl approved it; the row is a density guideline, so reword rather than delete), and all three Comparison rows. **Move them to Implemented / Partial as the build lands, and record the 8/28 sign-off against them** — `pm/FR_SIGNOFF.md` is where section sign-off is tracked, and this is the first section with a clean client acceptance.

**One vocabulary item did not move.** Ben demoed WX and QS rendering empty — "None of these have working excess or quota share perspectives available. So we just see an empty." §6's `loss_results` and §9's `perspective_code` both enumerate **GR / RL / WX / QS / GU** (spec 011 **O-07**); `21` **D9** established that **`RP` (post-cat)** exists in the platform, is needed for export, and is not exposed for viewing. **`RP` is still not in the set, and today's demo did not surface it.** → **O22-13**.

---

## 3. Grouping — the experience passes, the output does not (D13, D14) ⚑⚑

The most consequential twenty minutes of the session.

### 3.1 What Ben demoed

Select analyses → click group → add more if wanted → rename → pick currency → submit. Around it:

- **Default group name = `CRE` + submission name (D10).** Ben disliked it — "what I think is a pretty poor default naming convention … because the group doesn't live on the EDM. We could group analyses across EDMs." Cheryl waved it off: "I wouldn't worry about that. **I think people are going to rename it no matter what you pick.** The fact that we can go in and name it whatever we want, perfectly fine." **FR §6's "group names are auto-generated from the deal" is therefore satisfied at a low bar** — auto-generate anything, make it editable.
- **"Propagate detailed output" kept as a toggle; "create independent groups" removed and hard-coded false (D11).** Cheryl confirmed what the second one does: "All that does is removes all the details underneath. So you don't see anything about how you grouped anything. It just keeps the results and nothing else." Ben: "that sounds like it wouldn't be too useful."
- **Group currency is selected at group creation** — consistent with `17` **D1**/**O17-2**'s submit-time currency model.
- **Status rendering is broken but may not be needed (D12).** "It's not rendering it in real time properly. I don't know why. I need to fix that." Cheryl, watching the speed: "It's pretty fast though … maybe it's not needed to have a status because it goes really quick." Ben was cautious — "kind of contrary to my previous experience, but also my previous experiences were not grouping" — so treat this as *deprioritised*, not *dropped*.

### 3.2 The defect

Ben ran the same pair of analyses two ways. The pair: identical except for the event rate scheme — one stochastic, one historical. Cheryl, immediately: "Yeah, so you have different rate sets."

- **Manual Risk Modeler flow:** add to analysis group → the next page shows **not two rows** but "only one with a default chosen for us, **which is the historical**. And we can obviously change it to whatever we want." Ben kept the default and ran it.
- **Workbench flow:** "there's two event rate schemes included in this list, which is the two event rate schemes for those two analyses."
- **Result:** "only one of that rate scheme is included here in the manual Risk Modeler flow **versus both in the Workbench flow**. And if we look at the losses, **they're different** … even just the pure premium gross losses **off by 8 million**."

### 3.3 Why it is wrong — Cheryl's explanation

This is the domain fact the fix has to encode, and it is not in any document today:

> "Which event rate set did you pick when you grouped? Because **you do have to select one. You can't group with two different rate sets.** … if, in fact, you kept both rate sets, historical and stochastic, and tried to group, what happens is you would have **the same event listed twice with two different event rates**. And what we want is **1 event with the losses aggregated and one event rate**. So you have to choose one or the other, or you're not actually aggregating."

And the reason it is dangerous rather than merely wrong:

> "Your average daily loss is going to look really similar, because you're still considering all the events and those rates aren't going to be wildly different. It's going to be close, but it's not going to be the same. **And your EP curve will look wonky.**"

**Read that as an acceptance-test specification.** An AAL comparison will not catch this class of error; the EP curve will. Any validation of grouping must compare the **curve**, not the headline number.

### 3.4 What Ben will do

- "I need to go and reinvestigate. Because when I built this implementation a while back **for the specific need** … it served me, I guess, fine up until now. We also wanted to basically just **replicate the Risk Modeler manual flow with just defaults** — not changing the event rate that gets populated when we go to create the group."
- "So I need to revalidate what the Risk Modeler logic is … **revisit that underlying event rate scheme / region peril simulation set** building. Make sure it's still in line with how Risk Modeler is doing it today."
- His own verdict: "**Workbench and grouping is not producing the right output.**"
- **Validation method (D14):** "run examples manually and then run them in Workbench and compare the outputs." Cheryl: "Yep, that's what I would do."

### 3.5 Reconciliation — **O22-1**, **O22-2**

Three separate problems sit under one defect, and they should not be conflated:

1. **The event rate scheme is a group INPUT, not a derivation.** Risk Modeler presents one, defaulted, changeable. Workbench auto-selects and passes through whatever the members had. The fix is a **selection with a default**, not better inference — and the default Risk Modeler chose in the demo was the historical scheme, which is worth confirming as a rule rather than a coincidence of that pair.
2. **The region-peril simulation set is reverse-engineered.** Ben derives a required API attribute by inferring how Risk Modeler assembles it from members' model profiles and rate schemes. That inference is undocumented, unversioned and now suspect. Either source it from the API/contract or record the derivation with its assumptions and a test that fails when Risk Modeler changes. **The attribute name itself is transcript-garbled — confirm it against the API contract first** (transcript note).
3. **Nothing in the model or the docs says a group has one rate scheme.** §6 models a group as an `irp_analysis` row with `is_group = true` and members via `group_parent_id` self-ref — structurally fine, but it records no group-level run parameters. `submitted_settings` (spec 011) is documented as "own analyses only" and holds exactly the field in question (event rate scheme) for a *run*. **Decide whether a group row writes `submitted_settings` too** — it is the natural home for the chosen scheme and currency, and Article 8's "a finished run must keep reporting what it actually ran with" applies to a group at least as strongly. This is also the answer to **D18**'s "some are missing, so we need to pull it from the right spot" (§4.3).

And the governance problem underneath: **grouping has never been specced.** `15` **O15-6** asked for it to be built; `17` **O17-6** scoped the pick-list to the submission; `19`, `20` and `21` each recorded it as not designed, not demoed, not mentioned; spec 011 explicitly defers it to "Iteration 9"; and `app/` on the merged main line has no group-creation path at all. It has now been shown to the client, twice specified indirectly (ordering in `19` **D15**, the engine column in `20`), and found defective. **Write the spec before the fix**, and put D13's failure mode and D14's validation method in it.

---

## 4. The group as an object (D16, D17, D18)

Cheryl's additions were all about the group *after* it exists, which is the part no prior note has covered.

### 4.1 Member list — required (D16)

> "The other thing that we really will want in a group that we don't need in an individual analysis is **the list of the analyses that were included in the group**."

Ben: "Yes, okay … we do need that." The driving scenario is coverage, not curiosity:

> "If I'm coming in, say, you know, Wendy's off on vacation, I have to look at something she did. **I don't know inherently from looking at that group what's in it.** So having that list tells me, okay, she included hurricane and earthquake and blah blah blah."

**Structurally free** — §6's `group_parent_id` self-ref already holds membership. This is a view requirement, not a schema one.

### 4.2 Editing — wanted, later (D17)

> "And then an ability to edit that group. Would be fantastic if we can get there, **if we can't in the first round, not worried about it** because it's fast to create groups. So if you look at it, you're like, oh shoot, I put analysis A in and I didn't want analysis A. I could just go back and do it again … I could delete this group and I can add a different group and do it again. That's fine."

Ben asked what editing means; Cheryl: "adding and subtracting analyses **to the group, which is why I want to be able to see the list**." So the member list is the prerequisite, and editing is the follow-on — sequence them that way.

**Note the semantics question nobody raised:** an edited group must be re-run in Risk Modeler (membership changes the aggregation), so "edit" is really "re-submit with a different member set." Decide whether that reuses the row or supersedes it, and how it interacts with the fact that the group's results already exist.

### 4.3 Group metadata is mis-sourced (D18)

Opening a group shows the same metadata screen as an analysis. Ben: "needs work … the attributes are the same. **But some are missing, so we need to pull it from the right spot.** It needs to be accommodated for where it comes from for a group."

That is the same problem as **O22-1**'s third point: a group's run parameters (event rate scheme, currency, and whatever the region-peril simulation set resolves to) have no recorded home. Solve both together.

### 4.4 The builder list (D15)

> "I think just sorting for the name would be all we would need. You know, our naming conventions … would put, for example, all the series together. … maybe the simple search would be nice to cull the list down a little bit in that instance. Because that list could get a little unwieldy in some instances … somewhere you might have **50 or 75 analyses** to weed through. Well, that's kind of ugly."

Ben: "I'll add searching and sorting for the name."

**Do not read this as reversing `18` O18-5.** That ruling ("filtering >> search — I don't need search") was about the analyses grid, where status filtering and grouping do the work. The group builder is a flat multi-select pick-list with no filters, at a scale FR §7 already anticipates ("4 to 100+ analyses per submission"). Different surface, different answer.

### 4.5 Reconciliation — **O22-3**, **O22-4**

**FR §6's "A group is treated like any other analysis — viewed and exported the same way" is now wrong as written.** Viewing a group needs *more* than viewing an analysis (the member list) and its metadata comes from a different place. Amend the row rather than deleting it: the *results* of a group are consumed like any analysis; the *object* is not.

Also unexamined and still in FR §6: **"Nested grouping (groups of groups) is supported."** Nothing in `15`–`22` has confirmed CIC wants it, and `17` **D3** rejected the analogous suites-of-suites on the grounds that AIR's version caused confusion. **Ask before building it.**

---

## 5. The analyses grid (D19, D20, D21)

- **Portfolio and Template are separate columns (D19).** Delivered from `20` **D4**. Cheryl: "Oh yeah. I like that."
- **The full name lives under the expanded view** — §6's `full_name`.
- **Analysis type moved out of the grid rather than abbreviated (D20).** Ben: "I did not add a short version like EP, **because that's a map I'd have to manually maintain**. So I just took it out and put it in here." No objection. **Closes `20` O20-3 by removal** — record it as a deliberate decision so the code map is not proposed again. Spec 011 **O-11** already places analysis type in the expanded row, so the implementation and the spec agree.
- **Treaties are not in the view and should be (D21).** Ben: "We don't have treaties in this view, which I was thinking would be helpful." Cheryl: "Oh, that would be helpful to see if treaties were applied. Yes, I agree."

**Treaties are the one genuinely missing run fact.** §5 records `irp_treaty` per EDM and notes analyses reference treaties **by name**; §6 records no applied-treaty set. `21` **O21-4** already needed it for the TY export (where it gates *selection*); today adds a viewing consumer, and Cheryl's own diagnostic workflow depends on it — see §6. → **O22-7**.

---

## 6. Submitted vs. returned metadata — rejected (D22)

Ben's proposal, from the developer's seat:

> "This data is sourced almost from two different places … there's the analysis settings that we sent in the request, and then there's the results. … So I would like to see, for me personally — **am I doing this right? Have I built it right?** — what I submitted with and what the actual analysis returned. So like, for example, let's say I tried to submit with two treaties and then the analysis result doesn't have any treaties applied to it for some reason. Like I would be able to see that if I had those two sets."

Cheryl declined, and her reasoning matters more than the answer:

> "I'm not sure that I feel like it needs to be there. I feel like the analysis settings feeds that results information. So the one instance that's interesting that you mentioned is about the treaties. I mean, **we do have this happen where you apply treaties and you get no treaty loss, which is usually an error.** It's not because they don't take loss, it's because you set something up wrong. … but I can just go to the chart there and flip to working excess results. And **if I see zero, then I know, oops, something went wrong**, and I have to go back and fix it. … I understand from your perspective why you want to make sure that what you submitted is actually what you get out. I get that. **I have naive confidence that the model is doing what it's supposed to do and what I tell it to do it's going to actually do.**"

Two things to record:

1. **The display is a non-requirement, deliberately.** §6's `submitted_settings` already exists for precisely this (spec 011; Article 8 — "a finished run must keep reporting what it actually ran with"). The data is captured; it will not be surfaced. Write that down so it is not re-proposed as an oversight, and note the residual: **it is still available for support and debugging**, which is arguably where Ben wanted it anyway.
2. **Cheryl's fallback diagnostic is the WX perspective showing zero** — which depends on the perspective toggle and, ideally, on the treaties column from **D21**. Her "I know, oops" workflow is exactly the case Ben was trying to serve, and the cheaper way to serve it is to show which treaties were applied (**O22-7**), not to show the request payload. → **O22-8**.

---

## 7. EDMs contain exposures only (D23, D24) ⚑

### 7.1 The correction

Ben described groups as living, or not living, in EDMs. Cheryl stopped him — twice apologising for it, and both times giving the same reason.

> "One terminology thing that we'll have to kind of get straight is: **the EDMs contain exposures only.** So in the RiskLink world, they never contain losses. So to say that an analysis exists in an EDM … it's a little bit of a confusing statement when you say that, because it **would never exist in an EDM. It would always exist in an RDM in RiskLink.**"

And the platform-side complication she supplied herself:

> "They don't ever exist in an RDM because we're not writing them out to an RDM. We're just writing it out to the platform. It's associated with exposures from a specific EDM or several EDMs. … So within the RiskLink or Risk Modeler construct, we're just taking losses from either an RDM or that we ran and only exist on the platform, putting them together. And then they should just exist with the analyses, but **they're not tied to specific exposures from an EDM**. So I understand that. But I think they should show up under the analysis listing."

Her reason:

> "I'm not trying to be super nitpicky, but if you use that phraseology with other analysts, **they'll be completely lost** when you say that, because in their minds they've got a certain construct. … I just want to make sure that as we're communicating this out, that people can come along with what it is we're trying to do."

Ben agreed without friction and reframed it correctly: "the analyses don't live in the EDMs, but they **do have a relationship** to the EDMs … as a result of what portfolio we executed them against. So it's just a relationship. **That's not where the data really lives.** And that's what we're also mimicking here." He took an action on the wording: "we can hone in that description of what analyses are we looking at when we're on an EDM screen … what is that clear explanation?"

### 7.2 Groups on the EDM page (D24)

The concrete gap: a group **submitted from the EDM page does not appear there**, because "I'm only relating groups to submissions." Ben's fix: "for groups — because I am recording on the Workbench side as well which Workbench analyses go into this group — I can query on an EDM page any groups that were made up of analyses that are related to this EDM we're looking at."

On presentation, Cheryl floated separation — "Maybe we need a group section … you have an analysis section, you have an RDM section. Do we have a group section and do all the groups just live in their own world?" — and Ben showed the alternative: the **engine** column already distinguishes group from analysis, with the group name spanning the Portfolio and Template columns. Cheryl: "**I'm fine with that. I'm fine with that.**"

Implementation note Ben flagged: the engine column is not currently on the EDM screen, so in-line differentiation needs it added there.

### 7.3 A navigation defect, and Cheryl's standing request

Ben surfaced both himself:

- **Breadcrumbs are wrong.** "We have a results placeholder section that doesn't have anything. And we have analyses, which is … not a link, it's nothing."
- **Contextual back-navigation is lost.** "We navigated from the contextual EDM screen in the context of this submission, but we've been sent back to the non-contextual, just the EDM library one. So if we've navigated from a submission EDM, we should navigate back to that submission EDM screen." Fix in progress.

This belongs to `19` **O19-7** (dedicated results page: entry points, new tab, breadcrumbs, tab titles) and joins `20` **O20-10**'s defect list. → **O22-15**.

And Cheryl's own, which is not a defect but a process request:

> "Because we're working things in chunks, my ability to actually think through start to finish a whole navigation … some of the navigation is a little bit lost on me. … **At some point we'll need to go back to the beginning and probably just work through that navigation beginning to end again.**"

Ben: "I'll take any feedback on how to improve the navigation once you try it out for yourself" — which ties it to deployment (§11). → **O22-17**.

### 7.4 Reconciliation — **O22-5**

This is a **documentation and UI-copy** item, not a schema one — §6's `edm_id` is already correct (a nullable FK expressing origin, with own-vs-broker derived from `rdm_id`). What needs to change is language: the EDM detail page's description of what its analyses list contains, and the vocabulary used in the FR, the specs and any user-facing help. **The rule to encode: an analysis is *related to* an EDM because it ran against a portfolio in it; it does not live there.** Apply it to `20`'s "EDM column after Template on the submission page" and to spec 011 FR-009's merged-table wording when either is next edited.

---

## 8. The client table reverses to read-only (D25) ⚑

`21` **D2–D6** established the client table as a second CIC-owned production table the Workbench must **read and INSERT into**, opened the export with a search-or-create flow, and generated an ask for the `add new client` insert statement. Cheryl consulted Cheng and came back with a simpler answer.

> "The client table, as I was talking through with Cheng — **I think we need to view the client table. I don't think we have to add clients.** … in the workflow that we talked about, we're going to do all the exposure work in … Cheng's tool, the one we have right now, we're going to continue to use that, and **we would have to set up a client ID at that point**. … And in the case that somebody needs to do that, they can go into workflow tool and do it or SQL and do it. It's fine. **So we can take that off the list.**"

**The reasoning is a workflow fact, not a technical one**, which is why it is durable: the client ID is created upstream, during exposure work, in a tool CIC is keeping. By the time the Workbench reaches the export step, the client already exists.

Recorded for schema shape only, since the Workbench no longer performs it — Cheng's add-client logic:

- "He literally just takes the last one, adds one to it, and then you can fill out the name. I mean, it's that easy."
- With a test-data convention: "he makes some assumption that if the number is over 8,000 or whatever — our client list doesn't go that long — so if it's over like 8,000, we assume it's a testing group and you don't consider that for what your next running number is."
- And the `is valid` flag: "we just set that to a default that is manually maintained off the system. Not a big deal." **Confirms `21` D5** from the other direction — not "the insert need not send it" but "nothing sends it."

### Reconciliation — **O22-9**

1. **Retract `21` O21-1's write half.** The client table is a **read-only foreign reference**, in the same repository database as the loss tables, reached over the `LOSS` connection. `bootstrap-loss` still needs to mirror it for local development, but only for reads.
2. **The reference-cache invariant returns to one carve-out.** `21` **O21-1** framed the client INSERT as a second exception alongside `18` **O18-1**'s `workbench_is_active` (spec 009 P-13/FR-022). That exception is withdrawn. **`workbench_is_active` is again the only column the app writes to a synced/foreign surface** — worth restating in `DATA_MODEL.md` §14, because a second carve-out would have made it a category rather than an exception.
3. **`21` O21-1's remaining questions still stand, narrowed:** where the *selected* client ID is recorded against the export (still unanswered — §9's `result_export` holds only `delivery_code` and `location`), and whether the client search is scoped/filtered by anything or is a raw name search across the table. The second determines whether the export screen can pre-select, and is now cheaper to answer because it is a read.
4. **`20` O20-12's client-ID gap stays dissolved, for a better reason.** `21` dissolved it by making the value foreign; today it is foreign *and* read-only. **Do not add a client ID to `submission`** — one submission can push under different client IDs across treaty types (`21` **D6**).
5. **The `add new client` insert statement is no longer needed.** Remove it from `21` **O21-13**'s outstanding-asks list. Note what this means for that list overall: **this is the first O21-13 ask to come back answered**, and it came back by *removing* a requirement.

---

## 9. TY: `RDM_TREATY`, and deferral at Ben's request (D26, D27)

### 9.1 Where treaty losses live

`21` **O21-4** left TY as a second export mode with no known source. Cheryl supplied it:

> "That TY perspective is actually from a RiskLink table. I forgot that there's — we were looking at the **port table** that shows all the results by portfolio, which has the breakout: ground up, gross, working excess, pre-cat net, all of that. There's a **treaty table** that has the sum of all the treaty losses, TY, in it, and it's **`RDM_TREATY`**. That's where the losses live in RiskLink. So **there should be an API call to get that data back.**"

She demonstrated it live: the same ELT shape as the portfolio side, but "specifically for the treaties that were run" — analysis ID, **treaty ID**, event ID, and the TY perspective code. On naming: "Moody's does this thing where the table name tells you what the ID is … it's all code based in that `RDM_TREATY` and you have to link it up to this **treaty description** field in RiskLink to be able to get the name of the treaty and the losses together in one area to be able to add them up **across analyses**." She then ran the join on screen, pulling attachment, treaty type and name for a treaty ID.

And the connection back to Workbench grouping — which is *also* the naming collision `21` **O21-4** warned about:

> "So if we were group — you know, the grouping feature that you're doing right now is what happens **behind the scenes** when we're looking at treaty losses."

Ben confirmed his own extract does not reach it: he requested TY as a perspective and no TY file came back, and Cheryl's read is structural — "you won't, if you're pulling from this port table … that `get_elt` is probably pulling from the equivalent. It's pulling the **portfolio level results, not the treaty level results**. So there has to be some separate call."

### 9.2 The deferral

Ben was candid:

> "Since we're over time — the TY perspective. To be honest, there's just a lot of things going on, so I appreciate your explanation, but **it didn't really land for me**. So I can review what we said about it yesterday and supplement that with what we said about it today."

Cheryl set the sequencing, and gave the reason the deferral is safe:

> "Why don't we get through this with the **portfolio level**, and then maybe at that point we can circle back to the treaty perspective. **It's an important piece for us for sure**, but all this other stuff needs to get figured out regardless of what perspective we're looking for. And I think it'll be a matter of just **substituting where you're getting the data from, and everything else should be the same.**"

### 9.3 Reconciliation — **O22-12**

**Refines `21` O21-4** rather than replacing it. What is new: the **source table (`RDM_TREATY`)**, the **description join** for treaty names, and the confirmation that the portfolio-level extract cannot reach it — so TY needs its own retrieval path, not another perspective code on the same request. What is unchanged: cross-EDM aggregation is impossible, the EDM boundary needs enforcing as a hard block, and CIC's repository-side "group losses" must not be conflated with Workbench analysis grouping.

What Cheryl's framing adds is a **design constraint worth banking**: if the only difference between the portfolio path and the treaty path is where the data comes from, then the export pipeline should be built with the *source* as a seam — a retrieval strategy behind one transform/validate/commit flow. Building it that way now costs little; discovering it later costs a rewrite. And note the dependency: TY selection needs **which treaties were applied to an analysis** (**O22-7**), which the model still does not record.

---

## 10. Parquet replaces the paginated API (D28, D29) ⚑

### 10.1 The reversal

`21` spent §11 measuring the cost of paginated ELT retrieval — 1,000 rows per call, no row count returned, blind limit/offset probing — and left `20` **O20-11**'s "is Parquet on the path" question open. Ben closed it overnight:

> "After our discussion yesterday, I figured out a better way to get this information. We were looking at the API yesterday, which … **I'm going to not do that.** And my initial approach of Parquet format — I figured out how to download those Parquet files properly. So I'm going to go that route."

The mechanism:

> "I'm going to have Parquet files that represent the losses, which are kind of just like tables in a flat text format that we can query programmatically with Python in a very similar way that we query databases. … The way I get the data that I want is by **submitting a job via an API and sending which perspective codes I want**. … For this analysis: give me the results in Parquet format, give me the **ELT at the portfolio output level**, and give me these perspectives."

The response: "It comes back to me in a big zip file" containing, per perspective, a Parquet file which is the ELT. Ben confirmed the request options live while checking whether event type could be added: "Settings → additional outputs → loss details, that's where we pick what we want. Loss tables or the ELT output level … Perspective codes … schema version not required, it would use the latest by default."

### 10.2 Pre-fetching, and the wait that disappears

This is the part that resolves `21` **O21-9**'s sharpest consequence — that `21` **D18**'s loss-greater-than-exposure check needs the full ELT *before* the review screen renders (note: **D-IDs in this note are 8/28's; the 8/27 decisions are prefixed `21`**):

> "They used to definitely be [slow] … Maybe they were always fast, but it doesn't matter how fast they are. **I'm going to have this data ready regardless so that we don't have to do the waiting.** Like, you know, I was saying, we have to wait to export the data to do this validation. I'm going to have it ready. So when you're ready, it'll be similar experience as workflow tool. … The loss-is-greater-than-exposure validation — **I can do that**. So we don't have to wait to be able to do that. If you want to do the validate step like you do in the workflow tool, then we can do that **on demand**."

Cheryl: "Okay. It's just there."

### 10.3 Reconciliation — **O22-10**

1. **`20` O20-11's Parquet question is answered: yes.** §13's "Read Parquet; write to LOSS DB" framing and §9's Parquet layout are the design. `21` **O21-9(c)** predicted this would tip that way; it has.
2. **`21` O21-9's retrieval economics are moot for ELTs**, but the underlying facts are not. The job is asynchronous — request, wait, download a zip — so **the pre-fetch trigger is the live question**: on analysis completion (eager, for every analysis and every perspective, most of which are never exported) or on some earlier signal than the export click (lazy but not last-minute). `20` **O20-15** settled "lazy"; today moves the trigger earlier again. **Decide it explicitly**, and note the storage consequence: pre-fetching for a submission with 100+ analyses × several perspectives is a lot of Parquet under `{submission_outputs_dir}`.
3. **§9's `elt_record_count "from get_elt() response"` is still wrong, for a new reason.** `21` **O21-9** flagged that the API returns no count; now the ELT does not come from `get_elt()` at all. **Re-source it as a post-read count of the Parquet file, or drop it.** Same for FR §7's superseded "ELT summary … record count" row — it is already struck for viewing, but the export path still needs a count and now has a cheap way to get one.
4. **`result_export.location` ("file path (Parquet) or SQL ref") now has to hold both, for the same export** — the Parquet intermediate and the repository target. That reinforces `21` **O21-7**'s unanswered question about `result_export`'s grain.
5. **`irp_job_type_kind` already seeds `export`** — and §13's note that this is the *Risk-Modeler-side* export, distinct from `push_results_to_loss_repo`, is now exactly right and load-bearing: the Parquet job **is** a Risk Modeler export. Keep the two names apart in the seed table and in code.
6. **Still unmeasured:** Parquet job latency for a large ELT and for a PLT, the zip size, and whether the job endpoint rate-limits differently from the paginated one. `21` **O21-9(d)** asked for this against §1's 30-concurrent-user pooling target; it is still not done, and the answer now matters for the pre-fetch decision rather than for the export click.

---

## 11. Event type — the blocker cracks open (D30, D31) ⚑

### 11.1 The negative, confirmed twice

`21` **O21-5** left this as *the* blocker. Ben closed off two of the three routes on screen before finding a fourth.

- **Not in the Parquet.** "Similar to the API, these are the attributes that I get for a Parquet file. I do have the **event ID**. And I have that **exposure value**. Of course, I have the **loss value**, so I can do that validation exposure versus loss, but … **the event type is not here for me.**"
- **Not selectable on export.** Cheryl proposed the cheap fix — "you'd also want to just include **type** as one of the fields. And then when you bring it back into Workbench for an ELT, you only keep stochastic, and for the historical, you only keep the historical ones. But hopefully it would be **1 export**." Ben checked it live and it does not exist: "I can't pick what attributes go into them. … **Loss tables or the ELT output level, it doesn't give you options** … So I can't say add Type. **If I could, then it would be solved.**" Cheryl: "Well, that's too bad."
- **Not queryable in SQL, at least not by Ben.** "I can't query the event info table. This could just be a problem with my current SQL access to Data Bridge, but that event info table that would help me map events to [type] — I don't have access. … I don't see the event info table, **which is documented** here, then for database schema, alongside EDM and RMS … That needs to be a question that I follow up on with the Moody team."

### 11.2 The find

Then, unplanned, with thirteen minutes of overrun already on the clock:

> "Something interesting that I just saw here is that **we can go click into this event and see its type**. So let's see how it figured that out. … What I want to see is if I click this and click this, do more API calls happen? **Yes.** … Oh yeah, there you go."

Cheryl, watching: "It's tied to that event ID … which is linked to that event info table in SQL." And then, reading the call: "**Reference, reference tables, there it is.**"

Ben: "I use **reference data APIs**. I can figure this out on my own. I might not have to query this table — **I can clearly get this via API.**"

### 11.3 The plan, and its cost

> "Which is actually what my first thought was: get the Parquet file, get every event in the Parquet file. **Use the API to get every type for every event** in the Parquet file. **Update the Parquet file itself with a new column** that has the type for every event. And then use that subsequently."

The open cost is volume, and Ben named it immediately: "How big is this list? … **I literally need to get the type for every single one.** So what's the right way to do that?" The call he found was per-event ("It's asking for a specific event"), which is the worst case; whether a bulk or filtered reference endpoint exists is unknown.

### 11.4 The disagreement that is still open

Cheryl does not believe the reference tables exist on the platform at all:

> "Those were all reference tables that got populated as part of RiskLink that maybe were used as part of the on-prem solution, but they're really not needed, and the platform doesn't talk to Data Bridge unless you tell it it has to. … **So I don't actually believe that those reference tables exist on Data Bridge unless we have them build them.** And we can do that — we can have them build them and stick them on Data Bridge. **I could be completely wrong.**"

With a fallback she volunteered, consistent with `21` **O21-6(b)**: "maybe we don't need [Moody's to compile them] because we have them from **RiskLink 25**. We could just export those tables and have them live someplace else. That would be okay."

Ben's position:

> "**Those reference tables have to exist somewhere.** Data Bridge is a product. It's a product that exposes … Moody's has tons of SQL. They've got tons of databases that live on AWS that power all of the IRP products, right? Those event info reference tables need to exist somewhere. So they definitely live on the cloud and they definitely **are used when you run an analysis**. … Whether or not it's available to you is a question of are you a Data Bridge product user … or it's the Risk Modeler product that adds that **abstraction layer**. … So I would be very confident to say that those reference data tables exist. Can you query them as a Data Bridge product licensee? … I think this event info database exists. **I think it's a matter of my permissions that I can't query it right now.**"

Cheryl agreed on the underlying point — "I agree" — while holding her position on Data Bridge specifically. Both landed in the same place: ask Moody's. Cheryl: "**I feel like there's a solution to this, like that they have something. This is not a unique request.**"

### 11.5 The cross-check Cheryl owns

Because she has the RiskLink access Ben does not:

> "If you can put one of those ELT Parquet files … **in a CSV format, because I don't use Python** — I can compare it to the event info table that exists in RiskLink today and see if it has historical events in it. That's easy enough for me to do. It can't be that big because this was only Hawaii. … There was only one historical event in there or something."

Ben: "Yeah, I'll send you this. It'll be done in a second."

**This is a cheap, decisive test of the hypothesis `21` O21-5 could not settle** (that an ELT is stochastic-only by definition). If the ELT contains that one historical event, the hypothesis is dead and the split is mandatory. If it does not, option (1) from `21` **O21-5** is live and **O21-6** may never need answering.

### 11.6 Reconciliation — **O22-11**

**Narrows `21` O21-5 from unresolved to resolved-pending-volume**, and **substantially de-risks `21` O21-6**: if event type comes from a Risk Modeler **reference data API**, the RiskLink 25 reference tables may not need a fifth named connection *or* a snapshot into the Workbench — at least not for this purpose. Note the residual: `21` **O21-8**'s catalog check, if it survives, still wants reference data, and Cheryl's own doubt about that check ("maybe we don't need to do that anymore") is unresolved and was not raised today.

What to settle, in order: **(a)** Cheryl's CSV cross-check — cheapest, and it may collapse the whole problem. **(b)** Whether a bulk/filtered reference endpoint exists, or whether it really is one call per event; that decides whether enrichment is viable at ELT scale. **(c)** Moody's answer on `event info` — permissions or absence — which settles the disagreement in §11.4 and determines whether a SQL join stays available as a fallback. **(d)** Where the enriched Parquet lives and whether the type column is written back into the file (Ben's stated plan) or held separately; writing back mutates a file whose schema §9 says comes from the live API DataFrames.

**And a design rule from D31 worth keeping:** the split is validated against a source, never inferred from analysis type, model profile or event rate scheme, however plausible the rule looks. Ben: "we shouldn't trust these business rules."

---

## 12. The test treaty that takes no loss

A small thread with a disproportionate blocking radius.

Ben's standing test applies two working-excess treaties and the WX perspective keeps coming back empty. On screen they worked it: the portfolio's lines of business against the treaty's ("looks like those are in the treaty"), attachment, "hundred percent," effective date — Cheryl: "Looks like it should probably work. But you're not getting any treaty losses?" Currency ruled out: "should do the conversion. It shouldn't be an issue." Her list of remaining candidates: "Could be the risk size, could be — I mean, yeah, there's a variety of things."

She took the action: "**So I'll take a look at that and I'll tell you how to edit that treaty so that you get losses.** … My apologies for that. I should have checked that. … **Not a good test case if you don't get any losses.**"

Ben's counter is worth recording, because it is the other reading: "Or a good test case to — we might be doing something wrong in Workbench. So I'll run this afterwards manually and see."

**Why it blocks two things.** First, WX is one of the five perspectives spec 011 **O-07** enumerates, and nothing has ever validated it end to end. Second, and more urgently, it invalidates the TY negative: Ben requested TY in the Parquet extract and got no TY file, and Cheryl caught the inference immediately —

> "If you remember, though, we didn't get the treaty to take any loss. So it's possible you just don't have any treaty losses. **I wouldn't assume that it's not working yet** until we figure out how to get losses out. And then we should run it on that test case. … Because I saw it in the list. TY was in there."

Ben: "Good point. Good point." → **O22-14**.

---

## 13. Scope — DLM, HD, workers' compensation (D32)

Restated, not advanced. On HD:

> "I think we probably have to get through **DLM** and then we've got to figure out what we're going to do with the HD stuff, **because we're not set up for the HD results right now**."

On the extract's `scope` field, which Ben surfaced while reading the perspective table ("Scope is — what does scope mean here? Property or workers' compensation"):

> "Yep, **we want property**. We have to talk about **workers comp later**, but right now we want property."

`20` **O20-14** (HD export and HD metrics both undefined) is unchanged and remains the largest unscoped area. Workers' comp is now an explicit deferral with a named owner-less follow-up — note that `19` recorded Wendy adding Ground Up to the default perspectives specifically for checking treaty application "especially on the work comp side," so the two threads will meet.

---

## 14. Deployment, schedule and status

- **Deployment is now the stated priority.** Ben: "That's why we need to get **deployment done next week** and get you guys in it so we can get a full month of — or as much of the last month as we can of — testing and feedback." And: "I'm gonna follow up with **Nagi** today, and try to get something set up with him on Tuesday or very early Monday. I can also accommodate if he can. I'm gonna start being annoying about it — get this application deployed, so we can get rolling." Cheryl: "**I know, it's hard to get on his calendar.**" `19` **O19-15** unchanged; the Ross/Nagi name check is **still owed** across four notes.
- **Deployment is also the unblocker for Cheryl's navigation request** (§7.3) and for her own caveat: "I'm sure I'll have some feedback as we go through the whole thing."
- **Next week booked on the call; Ben sending invites.** Both out Monday 8/31.

  | Day | ET | Central | Note |
  |---|---|---|---|
  | Tue Sept 1 | 11:30 am | 10:30 am | Cheryl and Wendy both available; an earlier 2:00 pm ET slot dropped for a conflict |
  | Wed Sept 2 | 10:00–11:00 am | 9:00–10:00 am | |
  | Thu Sept 3 | 3:00 pm | 2:00 pm | Cheryl's calendar tight until later in the day |
  | Fri Sept 4 | 10:30–11:00 am | 9:30–10:00 am | Deliberately early — holiday weekend |

- **Status, both sides.** Cheryl: "I think we've made a lot of progress. I mean, the stuff at the end is the hard stuff, is the hardest part of it … This is the harder chunk, I think, to decode, just because **there's just a knowledge gap there that has to get filled**." Ben: "What have we done in one month, and we have that exact same amount of time left … **We cranked out a ton of work** … We're like a couple days behind my aggressive schedule of finish the entire end-to-end flow by today." Cheryl: "**That's why you have an aggressive schedule though, so that when you're behind, you're actually on time.**"

---

## 15. Carried-forward (not advanced this session)

- **`18` O18-3 / `20` O20-1 — record what was actually run on `irp_analysis`. ⚑ Apparently CLOSED, and carried here only until confirmed.** §6 now holds `irp_portfolio_id`, `analysis_template_id`, `full_name`, `submitted_settings` and `loss_results`. Today's grid rendered Portfolio, Template, Currency and AAL, and the mixed-currency guard worked — none of which was possible when `21` recorded this as a fifth-consecutive-session blocker. **The one genuine remaining gap is applied treaties** (**O22-7**). → **O22-6**.
- **`18` O18-4 — submit-time notes field. FOURTH session unraised.** Not mentioned since `18`. Decide whether it survives.
- **`18` O18-6 — legible failure reasons.** Unchanged. Note today produced a new failure class worth surfacing legibly: group submission failing on a duplicate-name lookup (**O22-16**).
- **`18` O18-10 — per-analysis software/model version.** Unchanged. Still load-bearing for both comparison and (per `21` §12) reference-data linkage by event ID + model version.
- **`18` O18-13 — the 64-character limit.** Unchanged (sourced, unverified).
- **`18` O18-7 — multi-select delete, request-path vs worker.** Unchanged.
- **`19` O19-2 — the cached AAL grid column.** **Confirmed again** — AAL was on screen throughout and Cheryl used it. Two consecutive positive signals now, after `20` flagged its silence.
- **`19` O19-3 — deep link / `irp_analysis.irp_id`.** Unchanged; §6 now distinguishes `irp_id` (API `analysisId`) from `irp_app_analysis_id` (the web UI's id the grid links out with), so this may also be closed — **check it alongside O22-6**.
- **`19` O19-5 / `05` O5-2 — return periods.** Enumerated in FR §7 (expanded 5/10/25/50/100/250/500/1000/2000/5000/10000; condensed 50/100/250/500/1000/10000) and in §6's `loss_results`. Today's only return-period comment was cosmetic (D5).
- **`19` O19-7 — dedicated results page breadcrumbs and tab titles.** **Regressed into a live defect** — see §7.3 and **O22-15**.
- **`19` O19-10 — results-view presentation.** Copy confirmed (D6); units selector not exercised; drag-and-drop deferred (D4); left-to-right ordering not exercised.
- **`19` O19-13 — FR §7 rows.** (b) **PLT** undiscussed for a **fourth** time; (c) **TCE toggle** still undiscussed.
- **`20` O20-2 — two analysis names and where `_n` lands.** **First live consequence today** (**O22-16**), otherwise unchanged.
- **`20` O20-4 — region granularity.** **Second consecutive session without progress.** The coarse value is shipping by default in an accepted grid.
- **`20` O20-7 — analyst attribution.** In spec 011 O-11; not mentioned.
- **`20` O20-9 — column alignment as a UI invariant.** Not discussed; the merged grid was accepted, so treat it as holding.
- **`20` O20-10 — results-view defects.** Units-reset and lost-selection unmentioned; **one new member added** (**O22-15**).
- **`20` O20-14 — HD.** Restated, not advanced (§13).
- **`21` O21-3 — perspective validity, intersection, one per batch.** Not revisited for export. The viewing side showed empty WX/QS as designed; **`RP` still absent from the vocabulary** (**O22-13**).
- **`21` O21-8 — export-time integrity checks.** Not revisited. D29 makes the loss>exposure check *cheap*, which is progress on feasibility but not on specification: the corrective write is still unrecorded, the catalog check's fate is still undecided, and **Cheng's list of silent corrections is still outstanding**.
- **`21` O21-12 — the two garbled load-bearing terms** ("position value" ≈ `PERSPVALUE`; "EXP value plus the correlated depth" ≈ `EXPVALUE` + correlated standard deviation). Unchanged and still unconfirmed — and note the Parquet attribute list Ben read out today ("event ID … exposure value … loss value") is consistent with both readings without settling either.
- **`21` O21-13 — capture the artifacts and get Cheng into a session.** **Partially advanced, and in an interesting way:** Cheryl consulted Cheng between sessions and returned with D25, which is the first of these asks to come back answered. The rest are outstanding: the schema for the tables the Workbench writes/references, confirmation that no further tables need writing, treaty-grouping details, and the silent-corrections list. The `add new client` insert statement is **withdrawn** (**O22-9**). **A captured session with Cheng remains the highest-value follow-up.**
- **`17` O17-6 / `15` O15-6 — grouping.** No longer "not mentioned" — see §3 and §4. Now the session's largest item.
- **`05` O5-1 event-rate round-trip**, **`05` O5-4 / PATE**, **`16` O16-6**, **`17` O17-7/8/9**, **multi-peril breakout in-place option filtering (`13` O12-1)** — all unchanged.

---

## 16. Open questions & follow-ups

- **O22-1** — **Grouping produces wrong results: the event rate scheme is a group INPUT, not a derivation. ⚑⚑ THE DEFECT.** D13/D14. Workbench passed both members' rate schemes into a group where the manual Risk Modeler flow presents one (historical defaulted in the demo); gross pure-premium losses differed by **~$8M**. Cheryl's rule, which nothing in the docs currently states: "**You can't group with two different rate sets** … the same event listed twice with two different event rates. And what we want is 1 event with the losses aggregated and one event rate," and the diagnostic — AAL "will be close, but it's not going to be the same. **And your EP curve will look wonky.**" Three sub-problems, to be fixed separately: **(a)** make the event rate scheme a **selection with a default** in the group flow, and confirm whether Risk Modeler's default (historical, in this pair) is a rule or a coincidence; **(b)** the **region-peril simulation set** is reverse-engineered from members' model profiles and rate schemes — source it from the API contract or document the derivation with a test that fails when Risk Modeler changes, and **confirm the attribute's actual name first** (transcript-garbled); **(c)** decide where a group's chosen run parameters are **recorded** — `submitted_settings` (§6) is documented "own analyses only" and holds exactly this field, and Article 8's "a finished run reports what it actually ran with" applies to a group; this is also the fix for **O22-3**'s mis-sourced metadata. **Acceptance test (D14):** run matched examples manually in Risk Modeler and in Workbench and compare — **and compare the EP curve, not the AAL**. (§3) *Ben — `DATA_MODEL.md` §6, FR §6.*
- **O22-2** — **Grouping has no spec, is absent from the main line, and FR §6 names the wrong failure mode. ⚑** Carried since `15` **O15-6**; scoped to the submission by `17` **O17-6**; deferred out of spec 011 to "Iteration 9"; no group-creation path exists in `app/` on the merged main (`is_group` is derived from Risk Modeler payloads for broker enumeration only) — and it has now been demoed to the client with incorrect output. **Write the spec before the fix.** It must carry: D13's failure mode and D14's validation method (**O22-1**); the member list and edit semantics (**O22-3**); sort/search (**O22-4**); EDM-page listing (**O22-5**); and the group's relationship to `irp_job_type_kind`'s existing `grouping` seed. **Amend FR §6's five rows:** "invalid groupings show error messaging" currently reads "e.g. mixing DLM and HD analyses" — **add the event-rate-scheme case, which is likelier and silent**; "a group is treated like any other analysis" is wrong as written (**O22-3**); "group names are auto-generated from the deal" is satisfied at a low bar (D10 — auto-generate anything, make it editable); and **"nested grouping (groups of groups) is supported" has never been confirmed with CIC — ask before building it**, given `17` D3 rejected the analogous suites-of-suites. (§3, §4) *Ben — FR §6, new spec.*
- **O22-3** — **A group needs more than an analysis: member list (required), editing (later), metadata sourced correctly.** D16/D17/D18. The member list is **required** — Cheryl's coverage scenario ("I don't know inherently from looking at that group what's in it") — and is structurally free, since §6's `group_parent_id` self-ref already holds membership; this is a view requirement. **Editing** (adding/removing analyses) is explicitly a later round because groups are cheap to delete and rebuild, and the member list is its prerequisite; decide the semantics nobody raised — an edited group must be **re-run**, so is that the same row superseded or a new one, and what happens to the existing results? **Group metadata is currently mis-sourced** ("some are missing, so we need to pull it from the right spot") — same answer as **O22-1(c)**. (§4) *Ben — `DATA_MODEL.md` §6, FR §6.*
- **O22-4** — **Group builder: name sort and simple search — and this does not reverse `18` O18-5.** D15. Cheryl: sorting by name alone would cover it (their naming conventions group a series together); search helps cull "50 or 75 analyses to weed through." `18` **O18-5**'s "filtering >> search — I don't need search" was about the **analyses grid**, where status filtering and grouping do the work; the builder is a flat multi-select pick-list with no filters at a scale FR §7 already anticipates ("4 to 100+ analyses per submission"). Different surface, different answer — record both so neither is applied to the other. (§4.4) *Ben.*
- **O22-5** — **EDMs contain exposures only; an analysis is *related to* an EDM, never *in* it — and groups list there in line. ⚑** D23/D24. Cheryl's correction, with her reason: "if you use that phraseology with other analysts, **they'll be completely lost**." **Not a schema change** — §6's `edm_id` is already correct — but a **language rule** for UI copy, the FR, the specs and anything user-facing: *these analyses were run against portfolios in this EDM*. Ben owes the EDM screen's description. Separately, **groups created from the EDM page currently vanish** because groups relate only to submissions; fix by querying groups whose member analyses relate to the EDM, and present them **in line** (Cheryl: "I'm fine with that"), distinguished by the **engine** column with the group name spanning Portfolio+Template — which requires adding the engine column to the EDM screen, where it does not exist today. Apply the language rule to `20`'s "EDM column after Template" and spec 011 **FR-009** when either is next edited. (§7) *Ben — FR §1/§7, spec 011.*
- **O22-6** — **`18` O18-3 / `20` O20-1 appears CLOSED — confirm and retire it. ⚑** Five notes carried "record what was actually run on `irp_analysis`" as the blocking gap behind the grid columns, the currency column, the mixed-currency guard and the name split. §6 now holds `irp_portfolio_id`, `analysis_template_id`, `full_name`, `submitted_settings` (currency code/scheme/vintage, event rate scheme, thresholds) and `loss_results`, and today's demo rendered every dependent successfully. **Walk the dependent list and close what is closed:** run currency (`17` O17-2), the grid currency column (`19` O19-10e), Portfolio + Template (`20` D4), the mixed-currency guard (`20` D17), perspective validity (`21` O21-3 — check whether `loss_results`' explicit-empty-per-perspective convention already answers it), treaty selection (`21` O21-4 — **not** closed, see **O22-7**). While there, check `19` **O19-3**: §6's split of `irp_id` from `irp_app_analysis_id` looks like the deep-link fix. **Leaving a closed blocker open is as costly as missing an open one** — it has shaped the framing of four notes. *Ben — `DATA_MODEL.md` §6, prior notes.*
- **O22-7** — **Record which treaties were applied to an analysis — now the last genuine run-facts gap, with two consumers.** D21: Cheryl wants applied treaties as a column ("that would be helpful to see if treaties were applied. Yes, I agree"), and it is also her own diagnostic route for the case she described in D22 (treaties applied, no treaty loss → "oops, something went wrong"). `21` **O21-4** needs the same fact for TY export, where it gates **selection** rather than display. §5 records `irp_treaty` per EDM and notes analyses reference treaties **by name**; §6 records nothing. Decide where it lands — `submitted_settings` already captures the submitted plan item, so applied treaties may belong there for own analyses, with broker rows null as usual. (§5, §6, §9) *Ben — `DATA_MODEL.md` §5/§6.*
- **O22-8** — **Submitted-vs-returned metadata display: a deliberate NON-requirement. Record it so it is not re-proposed.** D22. Ben wanted the developer-facing check; Cheryl declined — "I feel like the analysis settings feeds that results information … **I have naive confidence that the model is doing what it's supposed to do**" — and named her cheaper diagnostic: flip to the WX results and see zero. The data **already exists** (`submitted_settings`, §6, spec 011, Article 8); only the display is out of scope, and it remains available for support and debugging. Serve Ben's actual case through **O22-7** instead. (§6) *Ben — FR §7 metadata rows.*
- **O22-9** — **RETRACT `21` O21-1's write requirement: the client table is read-only. ⚑** D25. Cheryl, after consulting Cheng: "I think we need to view the client table. **I don't think we have to add clients** … we're going to do all the exposure work in Cheng's tool … and we would have to set up a client ID at that point. … **So we can take that off the list.**" Consequences: **(a)** the client table is a **read-only foreign reference** over the `LOSS` connection; `bootstrap-loss` mirrors it for local reads only; **(b)** **`18` O18-1's `workbench_is_active` is again the ONLY carve-out** to the "app never writes to the reference cache" invariant — restate that in §14, because a second carve-out would have made it a category; **(c)** the **`add new client` insert statement is withdrawn** from `21` O21-13's ask list; **(d)** still open from O21-1: **where the selected client ID is recorded against the export** (§9's `result_export` holds only `delivery_code` and `location` — same answer as `21` **O21-7**), and **whether the client search is scoped/filtered or a raw name search**, which decides whether the export screen can pre-select; **(e)** `20` **O20-12**'s client-ID gap stays dissolved — **do not add a client ID to `submission`** (`21` D6: one submission can push under different client IDs across treaty types). (§8) *Ben — `DATA_MODEL.md` §1/§9/§14.*
- **O22-10** — **Parquet is the ELT path; decide the pre-fetch trigger; re-source `elt_record_count`. ⚑ Settles `20` O20-11 and `21` O21-9.** D28/D29. A job request naming the analysis, the **ELT at portfolio output level** and the **perspective codes** returns a zip with one Parquet file per perspective; Ben will **pre-fetch** so the loss>exposure validation runs on demand ("we don't have to do the waiting"). Decide: **(a)** the **pre-fetch trigger** — on analysis completion (eager, for perspectives that may never be exported) vs. some signal earlier than the export click; `20` **O20-15** settled "lazy," `21` **O21-9** moved it before the review screen, today moves it earlier again, and the storage consequence at 100+ analyses × several perspectives under `{submission_outputs_dir}` needs sizing; **(b)** **§9's `elt_record_count "from get_elt() response"` is wrong twice over** — no count is returned *and* the ELT no longer comes from `get_elt()` — re-source it as a post-read count of the Parquet or drop it; **(c)** `result_export.location` must now hold both the Parquet intermediate and the repository target for one export, reinforcing `21` **O21-7**'s grain question; **(d)** keep `irp_job_type_kind`'s **`export`** (the Risk-Modeler-side Parquet job — that seed is now exactly right) distinct from `push_results_to_loss_repo` in the seed table and in code; **(e)** still unmeasured: job latency for a large ELT and a PLT, zip size, and rate limits against §1's 30-concurrent-user pooling target. (§10) *Ben — `DATA_MODEL.md` §9/§13, FR §7.*
- **O22-11** — **Event type comes from Risk Modeler's reference data APIs; enrich the Parquet retroactively. ⚑ Narrows `21` O21-5, de-risks `21` O21-6.** D30/D31. Confirmed negatives: type is **not in the Parquet**, **not selectable on export** ("Loss tables or the ELT output level, it doesn't give you options … If I could, then it would be solved"), and `event info` is **not visible in Ben's Data Bridge SQL** despite being documented. Confirmed positive: clicking an event in the Risk Modeler UI fires **reference data API** calls that return its type — "I can clearly get this via API." Plan: "get the Parquet file, get every event in it. Use the API to get every type for every event. **Update the Parquet file itself with a new column.**" Settle in order: **(a)** **Cheryl's CSV cross-check** — Ben sends one ELT as CSV (she does not use Python; the analysis is Hawaii-only and small, and she recalls one historical event) and she compares it against RiskLink's event info table; this is cheap and may collapse the problem, because if the ELT contains that historical event the "ELT is stochastic-only" hypothesis is dead; **(b)** whether a **bulk or filtered** reference endpoint exists, or it really is one call per event — "I literally need to get the type for every single one" — which decides viability at ELT scale; **(c)** **Moody's answer on `event info`**: permissions or absence, which settles the §11.4 disagreement (Cheryl: the reference tables were RiskLink artifacts and Moody's would have to build them, fallback export from RiskLink 25 and host elsewhere; Ben: they must exist in Moody's cloud because analyses use them, and this is a licensing/permissions question) and determines whether a SQL join survives as a fallback; **(d)** whether the type column is written back into the Parquet — which mutates a file whose schema §9 says comes from the live API DataFrames — or held alongside. **Design rule from D31: validate the split against a source; never infer it from analysis type, model profile or event rate scheme.** Also record Cheryl's correction: historical **rates** are long-term event rates and have nothing to do with historical **events**. If (a) or (c) resolves well, **`21` O21-6 may not need answering at all** — except for `21` O21-8's catalog check, if that survives. (§11) *Ben + Cheryl + Moody's.*
- **O22-12** — **TY's source is `RDM_TREATY` plus a treaty-description join, and it needs its own retrieval path. Refines `21` O21-4.** D26/D27. The portfolio-level extract cannot reach treaty losses — "it's pulling the portfolio level results, not the treaty level results. So there has to be some separate call." `RDM_TREATY` holds the summed treaty losses under the TY perspective code, keyed analysis ID + treaty ID + event ID; treaty identifiers are codes and join to a **treaty description** table for names. Cheryl also confirmed the conceptual mapping: "the grouping feature that you're doing right now is what happens behind the scenes when we're looking at treaty losses" — which is exactly the naming collision `21` **O21-4(e)** warned about; keep CIC's repository-side "group losses" and Workbench analysis grouping apart. **Deferred at Ben's request** ("it didn't really land for me") with Cheryl's sequencing: portfolio level first, "it'll be a matter of just substituting where you're getting the data from, and everything else should be the same." **Bank that as a design constraint: build the export pipeline with the retrieval source as a seam**, one transform/validate/commit flow behind swappable retrieval — cheap now, a rewrite later. Depends on **O22-7** for treaty selection. (§9) *Ben + Cheryl.*
- **O22-13** — **`RP` is still missing from the perspective vocabulary.** §6's `loss_results` and §9's `perspective_code` both enumerate **GR / RL / WX / QS / GU** (spec 011 **O-07**). `21` **D9** established that **`RP` (post-cat)** exists in the platform, is not exposed for viewing, and is needed for export — Cheryl called it "one we definitely need to have." Today's demo showed WX and QS rendering empty as designed and did not surface RP. Decide: expose it for viewing, or record the omission deliberately; either way **the export path needs it**, and `perspective_code` staying a plain string (`21` **O21-3**) is the right call precisely because the vocabulary is Moody's and still moving. Reword FR §7's "Results can be switched between financial perspectives" row, which lists only the five. (§2.5) *Ben — `DATA_MODEL.md` §6/§9, spec 011 O-07, FR §7.*
- **O22-14** — **Get a test treaty that takes loss — it blocks two validations. ⚑** Ben's two WX treaties return no losses; lines of business, attachment, 100% and effective date all checked out on screen, and currency was ruled out (CAD converts). Cheryl owns it: "I'll take a look at that and I'll tell you how to edit that treaty so that you get losses … Not a good test case if you don't get any losses." Ben's counter-reading stands too — "we might be doing something wrong in Workbench. So I'll run this afterwards manually and see." **Two things wait on it:** (1) the **WX perspective** has never been validated end to end, and (2) the **missing TY Parquet is not evidence TY is broken** — Cheryl: "I wouldn't assume that it's not working yet until we figure out how to get losses out. And then we should run it on that test case." (§12) *Cheryl + Ben.*
- **O22-15** — **Breadcrumbs and contextual back-navigation defects; and walk the navigation end to end.** Ben surfaced both: the results breadcrumb is an **empty placeholder** and "analyses" is not a link; and navigating back from a **submission-context** EDM screen lands on the **non-contextual EDM library** rather than the submission's EDM screen ("So I have that fixed in progress"). Belongs to `19` **O19-7** and joins `20` **O20-10**'s defect list. Separately, **Cheryl's standing request**: "at some point we'll need to go back to the beginning and probably just work through that navigation beginning to end again … some of the navigation is a little bit lost on me" — because she has only ever seen it in chunks. **Schedule that walkthrough once the app is deployed and she can drive it herself** (Ben: "I'll take any feedback on how to improve the navigation once you try it out for yourself"). (§7.3) *Ben + Cheryl.*
- **O22-16** — **Group submission resolves members by NAME and collides — the first live cost of `20` O20-2.** Ben: "Some of these groups failed to submit because I'm looking up the analysis members **by name**, which I don't need to do because I have the analysis IDs under the hood. … I'm constantly rebuilding my database, losing my Workbench data, resubmitting analyses with the same names, **which is valid in Risk Modeler**. I have duplicate names in these EDMs." The immediate fix is to use IDs. The durable point is that **`20` O20-2's unresolved `_n`-suffix question now has a live failure attached**: §6's `uq_irp_analysis_live_edm_name` makes (`edm_id`, `name`) unique among live rows, so this specific collision is a dev-environment artifact — but the general rule stands, **names are never keys** (§9 says so explicitly), and any other name-based lookup should be audited for the same mistake. Also worth surfacing legibly per `18` **O18-6**: a group that fails this way currently just fails. (§3.1) *Ben — `DATA_MODEL.md` §6/§9.*
- **O22-17** — **Deployment is the critical path, and now has a second dependent. ⚑ `19` O19-15 unchanged across four notes.** Ben: "we need to get deployment done next week and get you guys in it so we can get a full month … of testing and feedback"; chasing Nagi "today … Tuesday or very early Monday. I'm gonna start being annoying about it." Cheryl: "it's hard to get on his calendar." Beyond the original prerequisites (VDI, key store, SSO mechanism, the Nagi/Randy coordination), deployment now gates **Cheryl's end-to-end navigation review** (**O22-15**) and her own caveat that she expects feedback once she can use the whole thing. The **Ross/Nagi name check is still owed** — four notes. *Ben.*

**Advances / closes (from prior sessions):** `19` **O19-8** view and comparison caps — **CLOSED**: viewing is uncapped and Cheryl approved it; the 5-pair comparison cap was offered for expansion and declined ("Nope"); `19` **O19-9** comparison semantics — **CLOSED**: the selection-order base contract was explained and accepted, with a recorded warning that it is invisible until the base tag renders; `20` **O20-3** analysis-type abbreviation — **RESOLVED BY REMOVAL** (Ben declined the code map, "a map I'd have to manually maintain"; type moved to the expanded row, matching spec 011 O-11); `20` **O20-6** one EP type at a time and `20` **O20-8** the mixed-currency guard — **BUILT AND ACCEPTED**; `20` **O20-11** whether Parquet is on the path — **ANSWERED: yes** (**O22-10**); `20` **O20-15** the ELT materialization trigger — **moved earlier again**, from "lazy at export" to "pre-fetched," which changes the question from *when* to *what it costs to hold* (**O22-10**); `20` **O20-12** export parameters — **client ID now foreign AND read-only** (**O22-9**); expiration date still missing and still unmentioned; `21` **O21-1** the client table as a write target — **RETRACTED** (D25), returning `18` O18-1's `workbench_is_active` to being the only carve-out to the reference-cache invariant (**O22-9**); `21` **O21-4** TY as a second export mode — **REFINED**: source located (`RDM_TREATY` + description join), separate call confirmed necessary, deferral re-affirmed and now at Ben's request (**O22-12**); `21` **O21-5** the event-type blocker — **NARROWED to resolved-pending-volume** via reference data APIs, with a cheap decisive cross-check on Cheryl's side (**O22-11**); `21` **O21-6** RiskLink reference data with no named connection — **DE-RISKED**, possibly moot if the API route holds, though `21` O21-8's catalog check would still want it (**O22-11**); `21` **O21-9** ELT retrieval economics — **SETTLED** by Parquet + pre-fetching, and `elt_record_count`'s stated source is now wrong twice over (**O22-10**); `21` **O21-13** capture the artifacts and get Cheng in — **PARTIALLY ADVANCED**: Cheryl consulted Cheng between sessions and returned with D25, the **first of these asks to come back answered**, and it came back by removing a requirement; the rest are outstanding and one is withdrawn; `18` **O18-3** / `20` **O20-1** run facts — **APPARENTLY CLOSED by specs 010/011** and carried only until confirmed, with applied treaties the one genuine remainder (**O22-6**, **O22-7**); `17` **O17-6** / `15` **O15-6** grouping — **no longer merely unbuilt: demoed, defective, and still unspecced** (**O22-1**, **O22-2**); `18` **O18-4** the notes field — **FOURTH session unraised**; `20` **O20-4** region granularity — **second consecutive session without progress**, and the coarse value is now shipping inside an accepted grid; `19` **O19-2** the cached AAL column — **confirmed again**, second positive signal; `19` **O19-7** results-page breadcrumbs — **regressed into a live defect** (**O22-15**); FR **§6 Grouping** — all five rows unimplemented and **one of them names the wrong invalid-grouping case** (**O22-2**); FR **§7 Delivery** — still one row, still drastically under-specified (`21` **O21-10**), and untouched today.

**Next session:** **Tuesday, September 1, 11:30 ET / 10:30 Central** — the first of four booked for the week (Wed 9/2 10–11 ET, Thu 9/3 3 ET, Fri 9/4 10:30–11 ET); Ben is sending the invites, and **Wendy is available from Tuesday**, which unblocks anything needing her judgement — notably HD (`20` **O20-14**) and the export scope decisions. Both parties are out Monday 8/31. The sequencing that today sets is not really optional: **fix and spec grouping first** (**O22-1**, **O22-2**) — it is the only thing shown to the client that produces wrong numbers, and it has no spec to fix it against; **run Cheryl's CSV cross-check immediately** (**O22-11a**), because it is a day of elapsed time on her side and may collapse the event-type problem entirely; **confirm and retire the run-facts blocker** (**O22-6**), because five notes have been framed around a gap the schema appears to have closed. Everything else — the pre-fetch trigger (**O22-10**), the treaty test case (**O22-14**), TY (**O22-12**) — follows those. Running in parallel and now the stated priority: **deployment** (**O22-17**), which Ben wants done next week so the final month of the engagement is CIC using the application rather than watching it.
