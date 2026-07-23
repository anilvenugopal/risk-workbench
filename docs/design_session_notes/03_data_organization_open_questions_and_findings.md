# IRP Workbench — Design Notes: Data Organization — Open Questions & Findings

**Source:** Design session, July 9, 2026 (Ben Bailey, Wendy Hayes, Cheryl TeHennepe)
**Status:** The top-level data organization is **still open.** This session surfaced firm findings at the package/EDM/RDM level but did **not** settle how work is organized above the package. Ben committed to bringing wireframes with organization options to the next session; nothing about the submission/project/grouping model was decided. **Follow-up (7/14, see `04`):** the wireframe review adopted layout **Option C** and a working **submission → package → EDM → portfolio** navigation, but **reopened the package's necessity** — "package" is not CIC terminology and Ben is "feeling like it adds too much organizational overhead" (Cheryl agreed). The top-level model and the package object both remain **open** (OQ-1/OQ-2, §9).
**Related:** `01_data_model_and_workbench_organization.md`, `02_cic_data_organization.md`, `04_navigation_page_layout_and_ui_patterns.md`, `05_analysis_results_metadata_and_comparison.md`, `06_exposure_modification_subportfolios.md`, `07_analysis_execution_geohaz_currency_accumulation.md`, `../DATA_MODEL.md`, `../CR/CR_03__SUBMISSION_PACKAGE_MODEL.md`, `../../../minutes/IRP_Workbench_Design_Minutes_7-9-26.md`

---

## 1. Purpose

Record the data-model-relevant facts and decisions from the July 9 session, and — more importantly — state precisely **what about the data organization is still unresolved.** The central organizing abstraction (is there a top-level object? what is it? what identifies it?) was actively debated and left open. The firmer findings sit *below* that question, at the package and EDM/RDM level.

**Caveat on the currently-documented model.** `DATA_MODEL.md` (and `CR-003`) document a locked *internal* decision: "submission = the deal, CRM IDs as flat tags, package many-to-many." That decision was made by the PremiumIQ analyst + practice lead on 2026-07-07. **CIC has not ratified it, and this July 9 session reopened its core premises** (§2, §3). The documented model should be read as a proposal under review, not a settled fact, until the organization questions below are closed with CIC.

---

## 2. The central open question: is there a top-level object at all, and what is it?

Ben put the fundamental question on the table directly and it was **not answered**:

> "Does it make sense to even have a submission concept to organize the work, or does that become too much of a data management overhead? Should we just pull down attributes like cedant name, treaty type, inception date, and CRM ID at the package level — rather than have a submission concept at all?" — Ben

Three distinct organizing shapes were floated and none was chosen:

- **(a) No top-level object** — attributes (cedant, treaty type, inception, CRM ID) live directly on the data package; organization is by filtering, not by a container.
- **(b) "Submission"** — closer to the CRM record and the file-share directory structure.
- **(c) "Project"** — a looser, analyst-facing container. Wendy consistently reached for this: *"it's still in my brain a treaty year 2026 for [cedant] with these four treaty types. And then that sets my work path."*

Wendy explicitly declined to adjudicate the label — *"it doesn't matter what we call it or call it a submission, call it a project, call it whatever"* — which is accommodation, **not** resolution. Ben closed the topic by committing to wireframes: *"do we want to go submission route [closer to the directory structure] … or project route [a little more flexible]?"* → **deferred to visuals at the next session.**

**Supporting evidence — the file share already has this shape.** The shared-drive navigation mirrors a *submission → package → EDM/RDM* hierarchy almost exactly:

```
Client  →  Treaty Year  →  Submission  →  submission contents
                                              └── "cat modeling"  →  [model vendor, e.g. "RMS"]
                                                            └── package folders
                                                                  └── zip files  (one zip == one EDM/RDM)
```

*(Refined from the transcript: there is a **"cat modeling" folder** as the consistent entry point — Wendy "always start[s] by going to the cat modeling folder" — and beneath it data is organized **by model vendor**, of which "RMS" is one. The minutes' flat "RMS folder" understated this.)*

**Navigation reality — the linked directory is often NOT where the working file is.** Edited/BAK data frequently lives on a **separate archive drive** ("goes to this L drive instead of this M drive… not organized in the same file folder structure"), sometimes split further into **in-force / projected** subfolders when there are many databases. So a single "open the deal folder" link is useful but insufficient: the file browser must support **free navigation to any location + file-name search** (Wendy: "as long as we can just navigate to wherever we need to be"). The 7/14 file-browse UI (two drives, folder-tree + search, BAK-name search) is detailed in `04` §8.

Two design-relevant consequences:
- The **"submission route" (option b) is not a new abstraction** — it re-expresses an organization analysts already browse. That is a point in its favor relative to a looser "project."
- The physical tree implies a **grouping level between the submission and the individual EDM/RDM** — the folders inside "RMS" — which lines up with the proposed **package** object. So the package level has independent, real-world support beyond the modeling argument in §5–§6.

*(Ordering: **Client is the top level, Treaty Year sits beneath it.** The minutes §V, transcribed from the session audio, had these two reversed — this navigation is the accurate one.)*

**Design impact:** the choice among (a)/(b)/(c) changes whether there is a top-level table, what its grain is, and how everything below hangs off it. This is the load-bearing decision and it is open — but the file-share evidence tilts toward a real submission level with a package level beneath it.

---

## 3. What "submission" means, and whether a level sits above it (terminology collision)

The word "submission" was used two ways in the room, and the two do not describe the same grain:

- **CIC's usage (July 9):** a submission ≈ **one contract ≈ one CRM ID**. Wendy: a cat XOL, a cat aggregate, and a top-and-drop with the same cedant and inception date are *"three different contracts … three different submissions [in CRM] … but you really just get one package."* The analyst then groups those submissions into a single mental unit she calls a **project**.
- **The documented model's usage (`DATA_MODEL.md`/CR-003):** a submission **is the deal** that may span multiple CRM IDs, with CRM IDs as flat tags on it.

These are different: CIC's account implies a grouping level (**project/deal**) sitting **above** submissions (**≈ CRM IDs**), i.e. a potential *three*-tier picture (project → submission/CRM-ID → package). The documented model collapses that to *two* tiers (submission-as-deal → package) with CRM IDs as tags rather than a level. **Which one is right is unresolved**, and it directly affects whether CRM ID is a level, a tag, or the key of a mid-tier object.

Related unresolved sub-point — **priority hierarchy among treaty types.** Wendy described an informal precedence the model does not currently represent: *"the cat treaty is always at the top … that's the one we kind of log things under."* If work is logged/rolled up under a "lead" treaty type, that ordering may need to be a modeled attribute, not just display sugar.

---

## 4. What identifies a submission — the uniqueness problem

If a top-level/mid-level object exists, we need a key for it. The session established that **the natural attributes do not provide one**:

- **CRM ID is the only guaranteed-unique attribute.** Cheryl: *"the CRM ID is the one piece that will always be unique for us"*; everything else can overlap.
- **All other attributes can collide.** A cedant can buy a regional cat and a corporate cat that incept the same day — *"same cat treaty type, same inception date, same cedant name"* — differing only by CRM ID (Cheryl).
- **But CRM ID is manual, optional, and not integrated** (established in notes 02 §0.5; unchanged here). So keying on it is fragile, yet nothing else is unique.

**Design tension (unresolved):** the only unique identifier is the one we can least rely on to be present/correct. Options implied but not decided: (i) generate an internal surrogate key and treat CRM ID as optional reference; (ii) key on cedant + treaty type + inception and accept that it is *not* actually unique (Cheryl noted collisions are handled today only by naming-convention text); (iii) require CRM ID at some gate. None was chosen. Ben's tentative landing — *"it's really just the CRM ID"* — was immediately softened by Cheryl to *"the only piece that's guaranteed to be unique"*, i.e. uniqueness ≠ a usable key.

---

## 5. Package sharing & propagation — a provisional decision, with a known escape hatch

Decided **"for now,"** explicitly flagged as revisitable:

- A **package is shared across submissions/projects** (same exposure + loss results reused).
- Because analyses are **hard-coupled to the EDM in Risk Modeler**, work done on a shared EDM **propagates to — is visible in — every submission that shares it.** The team accepted this and often prefers it: Wendy — *"it's okay if we see each other's work. And actually, that might be preferred."*
- Ben explicitly left an exit: *"we'll go with that for now. If we see that becoming an issue … [the fix] would probably involve duplicating the data before we do any work."*
- **No copy-on-write / no duplicate EDMs for tracking.** Wendy: *"if we truly can use the same EDM and the same results for three submissions, I don't need that stuff in triplicate."* If isolation is genuinely needed, the preferred move is to **duplicate exposure *within* one EDM (a new portfolio)** rather than create a second EDM (Cheryl) — a second EDM is reserved for temporary/testing or renaming cases (§7).

**Design impact:** the sharing semantics (propagate vs. isolate) are a modeled behavior, and the current answer is "propagate, provisionally." Any later shift to isolation implies data duplication, which changes the write path.

---

## 6. Firm findings at the package / EDM / RDM level

These were clarified with enough confidence to treat as design inputs (subject to the still-open container question above).

### 6.1 EDM/RDM cardinality — **any combination** (corrects a prior assumption)
- A package is any combination of EDMs and RDMs: **1 EDM ↔ many RDMs, many EDMs ↔ 1 RDM, EDM-only, RDM-only**, with **at least one** of the two required. "One, multiple, any combination" (Cheryl); "EDM only, RDM only … any" (Wendy).
- This **corrects Ben's working assumption** that one EDM has at most one RDM: *"currently baked into my design is an assumption that one EDM would have one RDM maximum … which is clearly not the case."*
- A single package can be, e.g., 4 EDMs + 1 RDM. Reasons multiple RDMs arrive together: **treaty type, line of business, region, or size splits** (e.g., auto and personal lines separated because they are too big combined). The accompanying broker documentation explains the split — *"there's a document that comes in and says this is what is in each of these RDMs."*
- **Multi-select is required** at import: select several EDM/RDMs from a directory and act on them together (Cheryl: *"take all five of these and move them"*).

### 6.2 EDM/RDM naming
- Names must be **globally unique in Risk Modeler**, and are **already unique on the on-prem SQL server** (Wendy: *"it can't be on the SQL server and have the same name"*).
- Current convention (Cheryl): `TY{treaty-year}{month}_{cedant abbrev}_{in-force month/year}_{model version}_{version}_{EDM|RDM}`, with **extra identifiers appended** (business unit, region, treaty type) when otherwise-identical databases would collide.
- Broker-provided files may be named anything; the standard name is applied at SQL-attach time — so in the middle-out flow the name usually **already exists** on the file the workbench receives.
- Agreed behaviors for the import form: **auto-populate the name from the chosen file name, editable in place before submit**; run a **uniqueness/collision check** and let the analyst **append a suffix** on collision. (Wendy/Cheryl emphasized: *"make sure it can be edited."*)

### 6.3 Import mechanics
- **BAK/MDF is the primary path** and is "easy"; **CSV is a separate, harder case** (§8). Ben is inclined to **also accept zip and infer the naming convention** as a future-state hook, though the middle-out MVP receives files already unzipped/attached/named.
- **Uploads run in the background**, deliberately to hide Risk Modeler's slow, blocking file-upload UX; the analyst can leave the page.
- **Files live on the shared drive by default; never on a local machine** — Wendy: *"we would never put a SQL database on a local machine."* An EDM/RDM therefore records the shared-drive path it came from.
- **Delete-after-transfer** (new requirement): the analyst needs a checkbox to delete the temporary BAK file after it is transferred, because BAKs are created only to move data into DataBridge and otherwise *"you have thousands of files sitting out there forever"* (Wendy).

### 6.4 Rename / duplicate as real workflows
- **Reattach-and-rename happens ~15% of the time** (Wendy): the leading case is **M&A mid-deal** (cedant A is acquired by cedant B, so exposure is re-pointed/renamed for B); also **test variants** (start from one EDM, keep v1 and v2). The model/UI must support duplicating and renaming an EDM, optionally deleting the original.

---

## 7. Scope note affecting the model

- **Middle-out MVP.** Front-end data setup and validation stay in the legacy workflow tool + on-prem SQL; the workbench owns **import to Risk Modeler / DataBridge onward.** This means the workbench typically receives databases that are already attached, named, and (if edited) re-BAK'd — which is why the naming and delete-after-transfer behaviors above are framed around an already-named BAK. Ben is nonetheless inclined to *design* for the future state where the workbench absorbs the front end.

---

## 8. Open item: CSV results (low priority, needs investigation)

- **CSV for exposure** = a multi-relational import (account file + locations file) — rare for CIC's book.
- **CSV for results** = HD model results / PLTs shared as CSV because the recipient lacks HD models; **not natively importable as an RDM** in Risk Modeler.
- The blocking question is **capability, not schema**: *what can Risk Modeler actually do with a CSV result once imported?* Wendy: *"if you can't do anything with it once it's in there, then there's no point in importing it";* Cheryl: *"if all we can do is look at it, we can look at it other ways."*
- **Actions:** Ben to investigate RM's CSV-result import capability; add to the **Moody's question list**; and push cedants/brokers to supply RDMs instead of CSV. Treated as an outlier / post-MVP by the team.

---

## 9. Open questions carried forward (design-blocking first)

- **OQ-1 (blocking):** Is there a top-level organizing object, and is it "submission," "project," or nothing (attributes-on-package)? (§2) *Also unresolved at the 7/14 review: whether the **`package` object** survives at all, or EDMs/RDMs attach directly to the submission — Ben leans toward reducing organizational overhead (`04` §9). The import flow works either way.*
- **OQ-2 (blocking):** Does a grouping level sit *above* per-CRM-ID submissions (three-tier project → submission → package), or is "submission = deal" with CRM as flat tags (two-tier)? Resolve the terminology collision before schema. (§3)
- **OQ-3:** Given CRM ID is the only unique attribute but is manual/optional, what is the durable identity/key for the organizing object? (§4)
- **OQ-4:** Does the treaty-type precedence ("cat treaty at the top") need to be modeled, or is it display-only? (§3)
- **OQ-5:** Is "propagate work across shared packages" the durable rule, or will we need per-submission isolation (implying data duplication)? (§5)
- **OQ-6:** CSV result import — capability unknown; pending RM investigation + Moody's. (§8)

**Reconciliation note:** OQ-1–OQ-4 pressure the assumptions currently written into `DATA_MODEL.md`/CR-003. Those documents should not be treated as ratified until these are closed with CIC.
