# IRP Workbench — Design Notes: Treaty Summary, RDM Import via DataBridge, Multi-RDM Display & Notifications

**Source:** Design session, August 5, 2026 (Ben Bailey presenting; Wendy Hayes, Cheryl TeHennepe — CIC; Anil Venugopal — PremiumIQ, joined for the notifications discussion; Cheryl hard stop at ~55 of 59 min). Second consecutive functional session, continuing from August 4. Cross-checked against the full transcript.
**Status:** Working design notes. The **treaty summary field set** is closed (it resolves `08` O8-6). The **treaty section scope**, the **orchestrated RDM import**, **multi-RDM display**, and the **notification model** are agreed as direction. Pending Ben's validation: the **scoped sync** (D9/D10) and the **full attribute set retrievable from DataBridge** (only event rate scheme is confirmed). The **digest send time** and the definition of an **active submission** are open.
**Related:** `08_v1_demo_review_edm_rollup_and_submissions.md`, `05_analysis_results_metadata_and_comparison.md`, `../FUNCTIONAL_REQUIREMENTS.md` (§1, §2.2, §2.3, §5, §7), `../DATA_MODEL.md` (§4 Submission & Package, §5 EDM/RDM/Portfolio/Treaty, §6 Analysis, §8 IRP & RWB jobs), `../PRD.md` (§7, §12, §14, §21), `../../../../CIC/minutes/Risk_Modeler_Interface_Design_Minutes_8-5-26.md`, `../../../../CIC/transcripts/Risk Modeler Interface _ Design 8-5-26.vtt`

> Decision and change-request IDs (**D1**–**D22**, **CR1**–**CR13**) below refer to the tables in the 8/5 minutes. IDs prefixed with the note number (e.g. `08` D17) refer to the August 4 session.

---

## 0. TL;DR

Where August 4 was subtractive, August 5 was mostly **settling open items and designing two new areas** — the RDM import route and notifications.

- **The treaty summary field set is closed** (§1). Both limits are shown and labeled; **all percentage-share fields come out**; treaty name and number both stay. This resolves `08` O8-6 — and lands on **four core fields plus context**, not the "3 fields" `08` D17 sketched.
- **The treaty section shows only the treaties in the EDM** (§2). Treaties attached to an analysis are a separate thing, seen by opening the analysis. EDMs and RDMs are not guaranteed to be connected.
- **Sync gets scoped variants** — treaty-only and portfolio-only (§3) — and a scoped sync **must not advance the EDM's last-synced timestamp**. Pending validation.
- **RDM import becomes an orchestrated two-step workflow** (§4): DataBridge → query by SQL → Risk Modeler → delete from DataBridge. The **workbench app database becomes the source of truth** for the analysis attributes Risk Modeler drops, because they are still missing even after a DataBridge→Risk Modeler import.
- **Every EDM detail page shows every RDM in the package**, with expand/collapse per RDM (§5).
- **Notifications** (§6): **errors always email immediately** at the lowest granularity; **success does not** — it is replaced by a **morning daily digest of active submissions**. A per-submission "all jobs done" email was designed and then rejected. A **Hold status** joins Active/Complete/Cancelled.

---

## 1. Treaty summary — the field set is closed

`08` D17 left "which ~3 fields" open (O8-6). The session worked through the demoed grid column by column and closed it.

**The governing rule is space, stated by Cheryl:** *"the thing is not to replicate this table in there because you've got the button to take us here… whatever fits on the screen, and if you have to scroll, then just go into Risk Modeler and look at it."* The table gives **orientation**; Risk Modeler holds the detail and all add/edit.

### 1.1 The agreed fields (D3)

| Field | Note |
|---|---|
| **Treaty name** and **treaty number** | Both, even when identical — which they typically are. Ben couldn't tell which was which in the demo; Wendy: *"I wouldn't take one away — that's the only thing."* (D4) |
| **Treaty type** | Core. |
| **Risk limit** | **New.** (D2) |
| **Occurrence limit** | The single limit shown in the demo **is** the occurrence limit and was unlabeled. Label it. (D2, CR1) |
| **Attachment point** | Core. |
| **Cedent(s)** | First available value(s) only. (D6) |
| **Line(s) of business** | First available value(s) only. Cheryl expects room for it once the grid is cleaned up. (D6) |
| **Inception** and **expiration dates** | Confirmed as demoed. |
| **Currency** | Wendy: *"Currency is good."* |
| **Attachment basis**, **exposure level** | Include **if they fit** after cleanup. (D7, CR3) |

**Core vs. context.** Wendy's ranking: **risk limit, occurrence limit, attachment point, treaty type** are the ones that must be there — *"I think everything else is gravy."* The gravy is agreed as the target, but it yields to the core fields when space runs out.

### 1.2 What comes out

- **All percentage-share fields** (D5, CR2). One share percentage is misleading — Wendy: *"you really need to see all the percentages to interpret what it's doing, not just one… showing one doesn't give you enough information."* Four columns is too many for a condensed view, so show none and let the analyst open Risk Modeler.
- The **granular timestamps** flagged as *"ugly"* on August 4 (`08` CR18). Cheryl: *"Clean up the data, gives us a little more real estate"* — that cleanup is what pays for line of business, attachment basis, and exposure level.

### 1.3 Multi-valued fields: presence, not expansion (D6)

Cedent and line of business can both be long lists. Wendy does not want the granularity here — *"I just like to see that it's populated and there's something there that I should be looking for."* Display the first available value(s). This is the same treatment `08` §4.6 (CR15) gives the roll-up's truncated lists, and the expander pattern applies if an analyst wants the rest.

---

## 2. The treaty section represents the EDM, not the analyses (D8)

Wendy drew the distinction unprompted: **treaties that arrive in the EDM and treaties listed in the RDM are two different sets** — *"EDMs and RDMs are not guaranteed to be connected."*

- The treaty section pulls **what is in the exposure data**. Ben: *"This section is supposed to represent simply the treaties that were included in the exposure data."* — Wendy: *"Right, exactly."*
- Treaties attached to an analysis are seen **by opening that analysis**, where they were attached **at the time the analysis was run**. Wendy: *"they're not physically connected."*

This is `08` rule (b) and `08` D8 applied to treaties rather than to portfolios: a link that did not originate inside CIC's environment is not presented as a link.

---

## 3. Scoped sync — treaties only, portfolios only (D9, D10, CR5)

Cheryl's ask, from watching a treaty edited in Risk Modeler and then a full EDM sync run to pick it up: *"Is it possible to create a sync… just at the treaty level? If this is a really big EDM, is that going to take a little bit of time to sync everything when really the only change you made was at the treaty level?"*

- **The size argument.** Cheryl: treaties are small, *"so that should be a really fast sync. But the portfolio stuff could be large and could end up being a little bit of a drag."*
- **Why it is viable.** Treaties do not affect portfolio data or RDM data. Ben: *"under the hood… each of these is addressed individually, so that could work."*
- **The same reasoning gives a portfolio-only sync**, raised by Ben for the case where the change was made to portfolios rather than treaties.
- **D10 — a scoped sync must not advance the EDM's last-synced timestamp.** Ben: *"I wouldn't want to update the last-sync timestamp for an EDM if we just sync the treaties and not the portfolios."* The freshness indicator has to reflect what was actually refreshed.

**Design implication to check during validation:** `08` D19 makes freshness a **gate** before exposure-dependent actions — sub-portfolio creation first among them. If treaties and portfolios can be refreshed independently, that gate must read the **portfolio** freshness, not a single EDM-wide stamp. Carried as O9-1.

---

## 4. Broker-provided analyses and the RDM import route

### 4.1 The two upload routes are not equivalent (D12)

Demoed live, side by side, on the same RDM.

| | Upload through **Risk Modeler** | Upload through **DataBridge** |
|---|---|---|
| What it creates | Analysis entities only | A database on DataBridge |
| Stored on DataBridge | No — the UI shows database storage as **platform** and the DataBridge option is greyed out | Yes |
| Queryable by SQL | No | Yes |
| Results visible in the Risk Modeler Results tab | Yes | **No** |
| Can be linked to an EDM at upload | Yes | No |
| Retains exposure name, currency scheme/vintage, **event rate scheme**/name, minimum loss threshold, franchise deductible, construction, LOB term | **No** | **Yes** (event rate scheme validated; the rest assumed — O9-3) |

Two further observations from the demo:

- **The asymmetry is inside Risk Modeler's own upload screen.** Choosing to upload an **EDM** offers a storage choice; choosing an **RDM** does not. Cheryl: *"That's very bizarre."*
- **Importing DataBridge→Risk Modeler does not restore the attributes.** Ben imported the DataBridge RDM into Risk Modeler and the analysis summary was **still missing them**. So Risk Modeler cannot be the source of truth for them under any route.

Cheryl on which one matters most: *"the only one that bothers me about that is the rate scheme… that's just key information we're going to want to know."*

### 4.2 The orchestrated workflow (D11, D13, CR6)

One user action — create a package, upload an RDM — runs four steps behind the scenes:

1. **Import the RDM to DataBridge.**
2. **Query the imported RDM by SQL** for the full analysis detail, and **save it to the workbench app database.**
3. **Import the RDM from DataBridge into Risk Modeler**, so results appear in the Results tab. Cheryl confirmed the path exists in the UI: Results → the caret next to "Results" → **Import from DataBridge**.
4. **Delete the RDM from DataBridge.**

**Why delete (D13).** RDMs can be larger than EDMs, and keeping them in both places wastes space. Cheryl: *"you can always export everything back out to an RDM, so… I don't think we need to keep it in two places."* Losing the direct EDM↔RDM association on DataBridge costs nothing, since `08` D8 already rules out tying analyses to EDM portfolios.

**Why this is acceptable to CIC.** Wendy framed it as the point of the whole exercise: *"we're going to just interrogate the data"* before it reaches Risk Modeler. She also raised a variant — an RDM CIC only reads, never importing it into Risk Modeler at all, letting the file sit on the share as long as needed. Not designed; carried as O9-5.

### 4.3 The app database owns the attributes Risk Modeler drops (D12)

The attributes are captured at step 2 and returned to the user from the workbench, not from Risk Modeler.

**The mutability caveat, raised and dismissed.** Wendy: SQL fields *"you can change any field, say anything you want at any time"* — likely why Moody's withholds them in the certified cloud UI, where a cloud-run analysis can be certified as unedited. CIC's position is unchanged: capture them **before** the data reaches Risk Modeler and show them anyway — *"if it's there, we want to use it, want to return it to the user."*

### 4.4 Analysis display (CR7, CR8, D14)

- **Enhance the "broker-provided analysis review" description** in the functional requirements. Today it says only that the analyses and their settings must be reviewed to decide what still needs running.
- **Add the analysis data values** — the actual losses. The page currently shows metadata only (portfolio name, model version, type, analysis mode, primary and secondary peril, region setting, currency). Ben and Cheryl had already agreed offline which attributes and which result data matter; **not yet implemented.**
- **Add a pass-through link to Risk Modeler per analysis, plus an on-screen summary**, mirroring the treaty pattern. Cheryl: *"I like having the pass-through, that's nice."*

---

## 5. Multiple EDMs and RDMs in one package (D15, D16, CR9)

**Every RDM in a package is viewable in the context of every EDM in that package.** Two EDMs and two RDMs means each EDM's detail page displays both RDMs.

- **Why.** Wendy wants the whole landscape to confirm the **same analyses were run** across paired books: *"let's say I have an in-force and a projected EDM, and then I have an in-force RDM and a projected RDM… were the same analyses run, because you really should in that scenario have the same — if you have 12 analyses in one, you have 12 analyses in the other."*
- **What the pairing is not.** Cheryl: *"EDM 1 isn't related to EDM 2 other than they're related to the same submission."* Pairs are likely in practice, but the only real relationship is shared membership in the submission — no derived EDM↔RDM link is implied by showing them together.
- **D16 — expand/collapse per RDM.** Both expanded for the full picture, or collapse one to concentrate on the other. Cheryl: *"Yeah, I think that works."*
- **Not demoed.** No two-EDM/two-RDM example exists in the app yet; Ben committed to building one so the layout can be reviewed.

**Scenarios Wendy raised and left open** (O9-5): a **replacement RDM arriving two weeks later** because something changed — *"you need to get rid of the old stuff and put the new one in"* — and **data arriving late** (*"we couldn't give you the global data now, we'll give it to you later"*), where she would again want to see everything together. Her own caution: *"it's just goofy, trying to plan for every possible scenario."*

---

## 6. Notifications

The longest single discussion of the session, and the one that moved furthest from its starting point.

### 6.1 Target: the individual owner of the submission (D17)

Anil laid out the choice — individuals or shared mailboxes. Mailboxes reduce duplicate copies on the mail server (a Microsoft storage cost), but Wendy ruled them out on how CIC actually works: *"we virtually never have multiple people working on the same submission"* and *"I don't want to know what 12 other people are doing."* **V1 sends email to the submission owner's individual address.**

### 6.2 Errors: always, immediately, at the lowest granularity (D18)

Anil: *"any failure, regardless of what operation that is, will result in an e-mail."* Wendy's driver is the long-running job: *"if I'm expecting something to run for 10 hours, I'm not going to go in and check on it in 30 minutes. Like if it fails 30 minutes in, I want an e-mail notification."* And, plainly: *"Errors are always the most important thing to be notified about."*

This is the one rule that does not aggregate. Everything else does.

### 6.3 Two success models were designed and rejected

Recording both, because the digest only makes sense against them.

- **Per-job success email.** Rejected immediately. Ben raised it as the easiest implementation; Cheryl: *"I think people are gonna ignore them… that's going to be a lot of pinging that clutters up your e-mail pretty quickly."* Wendy: *"you start sending notifications for everything, people will ignore it."*
- **One email per submission when the last outstanding job completes.** Anil's proposal, and Ben's summary of it: *"everything on my submission is complete, I don't have any more outstanding jobs… you can go check your submission and see where it stands."* **Cheryl's counter-example killed it:** most non-analysis work is sequential, one task at a time — *"let's just say I kicked off GeoHaz. I can't do anything else until the hazard retrieval is done… technically there's nothing else that's queued because I wouldn't have queued anything up after that until that stage is done."* An "everything is done" trigger fires after **every** task in that pattern. Cheryl also rejected the premise for parked work: *"do I need a notification that I'm not doing anything? Probably not, because I know I'm not doing it."*

### 6.4 The morning daily digest (D19, D20, CR11)

Wendy's proposal, accepted by Cheryl (*"it works with how we work a little bit better"*) and Anil.

- **One email, in the morning** — placeholder ~8:00 AM Central, time open (O9-6). Morning rather than end-of-day because *"a lot of times we kick off stuff before we go home."*
- **Scoped to the recipient's active submissions.** Anil: *"once you have either marked the submission cancelled or complete, we will not bombard you with all of the historical things."* (D20)
- **Per submission, two states:** how many jobs are **outstanding** (with detail on which), or **all jobs complete** — Ben: *"which would basically mean you're free to go do something on it."*
- **Plus a failure flag per submission.** The per-error emails already went out; the digest flags which submissions have failures so nothing sits unnoticed.

It fits the real working pattern both CIC attendees described: two to four submissions in flight at once, parked and resumed. Wendy: *"we're bouncing around all over the place."*

### 6.5 A Hold status (D21, CR12)

Cheryl needs a state that is *"not cancelled, not complete, but… we're not working on it really actively right now"* — her case is waiting a week on data from a broker. Hold means **no updates needed but still in the queue**, and it takes the submission out of the digest.

Ben considered a per-submission "silence notifications" toggle and concluded *"a hold status is more appropriate."* This extends `08` D1's three-status model to four: **Active, Hold, Complete, Cancelled.**

### 6.6 The jobs view carries the load the emails don't (D22, CR13)

The digest works because the jobs view is where an analyst actually checks state. Wendy: *"a workbench, sort of by definition, you should kind of have it open on your desktop… I like the idea of just flipping over to this view where I just see my jobs."*

- **Two job types**, consistent with `08` D20: **workbench jobs** and **Moody's/IRP jobs**. A single user action such as an import contains several of each.
- **Add the submission each job belongs to**, and filter on it. Anil: so that when *"we see something failed, we know which submission was that for."*
- **Beat RiskLink on presentation**, which is the whole bar: *"even in RiskLink today you have to filter for your jobs and go find it."*
- **Queued state:** showing that a job **is queued** is straightforward and in scope. Showing its **position in the queue** is not — Ben flagged it as hard, Wendy accepted (*"That's fine"*), noting the cloud does its own job leveling so position matters less than it did on-prem. (O9-8)

---

## 7. Open questions

- **O9-1: Scoped sync validation.** Confirm the treaty-only and portfolio-only syncs are feasible, that neither advances the EDM last-synced timestamp, and that the pre-action freshness gate (`08` D19) reads the freshness of the data the action depends on rather than one EDM-wide stamp. *Ben.* (§3)
- **O9-2: Reach out to Moody's about the intended RDM/analysis workflow.** Why the two upload routes differ, and why the certified UI will not populate attributes that exist in the underlying data. Wendy: this broker workflow — receive results, use some, run others, combine at the end — is *"a very common workflow"*, and she wants to confirm CIC is not missing an intended path. *Wendy / Ben.* (§4.1)
- **O9-3: Which attributes survive the DataBridge/SQL step.** Event rate scheme is confirmed. Confirm minimum loss threshold, franchise deductible, construction, LOB term, currency scheme/vintage, and exposure name. *Ben.* (§4.1)
- **O9-4: Do attachment basis and exposure level fit the treaty summary?** Answerable only after the grid cleanup recovers space. If they do not fit, they drop. *Ben.* (§1.1)
- **O9-5: RDM lifecycle inside a package.** A replacement RDM arriving weeks later, data arriving in stages, and Wendy's read-only variant where an RDM is never imported into Risk Modeler at all. No design yet. *Ben / CIC.* (§4.2, §5)
- **O9-6: The daily digest send time.** Placeholder ~8:00 AM Central. *Ben / CIC.* (§6.4)
- **O9-7: What counts as an "active submission" for the digest.** The edge case Wendy named: a submission whose only open item is unresolved job failures she has deliberately parked — *"do it every day for two weeks, I get an e-mail telling me that those job failures are there… when do they fall off?"* Hold (D21) is the primary lever; the remaining rules are unwritten. *Wendy / Ben.* (§6.4)
- **O9-8: Notification channels beyond email.** Wendy parked two: a **workbench notification icon/badge** like Teams (*"can notification be on the front page of the workbench?"*), and **Teams messages** — valuable *"certainly when you're traveling."* Related: her request to **opt one time-critical job into its own completion email** (*"I have a really important job that I am waiting for… notify me when that job is done. I don't need to know the 20 other things I sent today"*). Both are beyond V1 email. (§6.4, §6.6)

**Resolved from August 4:** `08` **O8-6** (which fields go in the condensed treaty view) is closed by §1.

**Next session:** timing not fixed. Cheryl is *"pretty open"* and asked Wendy to coordinate against her calendar. Likely agenda: review the rebuilt treaty summary and the analysis/RDM display against these decisions, then continue through the remaining Risk Modeler functional areas. Ben's own note: *"plenty of work to do on analysis results and RDM details in terms of display."*
