# IRP Workbench — Design Notes: Analysis Execution — GeoHaz, Running Analyses, Suites, Currency & Accumulation

**Source:** Design session, July 16, 2026 (Ben Bailey, Cheryl TeHennepe) — walkthrough of the RM UI for each capability, then how the workbench should streamline it. Cross-checked against the full transcript.
**Status:** Working design notes. Hazard-lookup defaults, the **analysis-suite** concept, and the **currency-defaulting rule** are agreed as direction; several items are Cheryl's take-backs to her team (standard suite/profile definitions, HD hazard, enhanced risk data, accumulation settings).
**Related:** `06_exposure_modification_subportfolios.md`, `05_analysis_results_metadata_and_comparison.md`, `../DATA_MODEL.md` (§7 templates/suites, §10 reference cache), `../PRD.md` (§11, §14), `../../../minutes/IRP_Workbench_Design_Minutes_7-16-26.md`, `../../../transcripts/IRP_Workbench_Design_7-16-26.vtt`

---

## 0. TL;DR

This is the "meatier" action functionality past import. Ben's recurring goal: **collapse RM's many-click workflows into single-action buttons** where the pattern is predictable.

- **Geocoding & hazard:** offer **hazard lookup only** — **never re-geocode by default** (broker geocoding is preserved). Sensible defaults; running an inapplicable peril returns **zero, not a failure**.
- **Running analyses:** select **multiple portfolios + a model profile + an output profile** and **run once** (batch); **auto-generate analysis names**; profiles are **selected from a standard list**, not owned by the workbench.
- **Analysis Suite** (Ben's concept, both excited): a pre-configured set of **(model/analysis profile, output profile, event-rate scheme)** combinations — the "**big three**" — solving the "global book" pain where every model is set up one at a time.
- **Currency:** default the analysis currency to the **exposure's native currency when it's one-to-one**, general default **USD**; use the **latest currency scheme** on rerun.
- **Accumulation analysis:** in scope, its own settings, kept in **separate suites** from DLM.

---

## 1. Geocoding & hazard lookup

### 1.1 Hazard lookup only — preserve broker geocoding

- Exposures typically arrive **already geocoded**; **never re-geocode by default.** Cheryl: "I can't think of a single time in this role that I've re-geocoded data."
- Re-geocoding, if ever needed, is done **intentionally inside the model** — not a workbench action. Simpler for the user, too.

### 1.2 Hazard-lookup defaults (agreed)

| Setting | Default |
|---|---|
| Data version | **Latest** (v25 as of now). |
| Model type | **DLM (non-HD)** for the most part. |
| Missing locations | **Don't skip; overwrite.** |
| Perils | **Earthquake and windstorm selected by default**, with the ability to **toggle**. |

- Running a peril that doesn't apply (e.g. earthquake on a windstorm book) simply returns **zero** for that layer — **not a failure case.** The job returns a **summary of locations looked up per layer.**
- Guiding convention: "the more comprehensive the data, the better."

### 1.3 Open (Cheryl to investigate)

- Whether **hazard retrieval must be done ahead of time for HD models** — RM shows an HD option for hazards and there appears to be **simulating at the hazard level**. She has a question out, not yet asked.
- **Enhanced risk data** — not an option today and not currently used ("we will not, to date, be turning on the enhanced risk data"); **may be HD-only.** CIC licenses everything, so Cheryl will check availability and whether they'll want it.

---

## 2. Running analyses

### 2.1 Standard model & output profiles — selected, not owned

- CIC will maintain a **pre-compiled set of standard model profiles and output profiles** (formerly one object, now two). Users **select from the standard list.**
- **The workbench does NOT own profile management** (out of scope) — profiles are created/managed **in Risk Modeler**; the workbench just **selects** them. (Consistent with DATA_MODEL: `analysis_template` stores profile *names*; profiles live in the IRP reference cache §10.)
- Support **user-defined profiles** (naming convention `UD` + initials, e.g. **UDCT**) and **filtering** when the list grows long — "if I could just get to UDCT… now I have all my own user-defined profiles."

### 2.2 Batch runs across portfolios

- Select **multiple portfolios** + a model profile + an output profile and **hit go once.** Cheryl's recent example: she had to **rerun the same data across 6 portfolios**, one at a time.

### 2.3 Auto-generated analysis names

- Typing an analysis name every time is "a pain." **Auto-generate from a naming convention** — e.g. **portfolio name + NT (near-term) vs LT (long-term) + stochastic vs. historical event-rate scheme.** **Not finalized this session**, flagged as a big time-saver. (Maps to DATA_MODEL `analysis_template.auto_name_pattern`.)

### 2.4 Analysis settings that matter

| Setting | Handling |
|---|---|
| **Model/analysis profile** and **output profile** | Configurable at the **individual-analysis or suite level.** |
| **Event-rate set/scheme** | People are "very picky"; must be configurable. One of the **big three** (§3). |
| **Franchise deductible** | **Deal-specific** — a **toggle the team wants access to** (the exception to "hold advanced settings constant"). |
| **Unrecognized construction / occupancy type** | A **toggle the team wants access to.** |
| **Min loss threshold, max loss event** | Can **stay at defaults** — held constant. |

---

## 3. Analysis Suite (Ben's concept)

Solves the **"global book"** pain case, where you must set up every model one at a time (50–150+ combinations).

- A **suite is configured beforehand**: a set of unique combinations of **(model/analysis profile, output profile, event-rate scheme)** — Ben's **"big three"** settings.
- Then the user selects portfolios and **"run suite X against these."**
- (Maps directly to DATA_MODEL `template_suite` / `template_suite_item` and `analysis_template`, which are **in MVP** per the practice-lead call — the batch problem is the #1 analyst pain point.)
- **Keep DLM and accumulation analyses in separate suites** — see §5.

---

## 4. Currency

### 4.1 Where currency is assigned + the defaulting rule

- Currency is assigned in the **analysis builder** (per analysis, via an analysis-currency dropdown); changing it affects **only the selected analyses.** A US book defaulting to Euros is wrong.
- **Rule agreed:** default the analysis currency to the **exposure's native currency when it's one-to-one** (a single currency in the exposure); the general default is **USD.**
- **Context:** CIC's current workflow **converts everything to USD** (with a conversion chart). In RiskLink, exposure stays in **native currency** and can be **mixed**. Global books often arrive in **native currency per region**, forcing an explicit **output-currency** choice.
- RM's large, "in-your-face" currency selector is **more intuitive** than RiskLink's easy-to-miss lower-right toggle (which was a global setting that could **bleed** — e.g. Japanese yen — into the next deal). Prefer the explicit, per-analysis selector.

### 4.2 Currency scheme (exchange-rate vintage)

- A **currency scheme = the exchange rate at a point in time.**
- **Default to the latest / most current currency scheme when rerunning.** (Cheryl: "if we're rerunning something, we probably want the most up-to-date currency scheme." Ben briefly floated matching the broker-provided scheme; resolved to *latest*.)
- Ability to **import a custom currency scheme** is less common; CIC would **build it in Risk Modeler itself**, and the **workbench only needs to allow selection** of it — it does not build/import schemes.

---

## 5. Accumulation analysis

- **In scope**, with accumulation-specific settings.
- **Output perspectives:** **gross** and **pre-cat net** (keep **RL**). **Ground-up stays in** — but note this is a **UI limitation, not a preference**: Ben "can't even get rid of ground up" in the RM UI ("might be able to do it differently… when interacting with the API"). Record as a constraint.
- **Purpose:** understand **allocated values** for large commercial structures — e.g. a **$1M policy over $50M of buildings**; accumulation runs the full financial structure (deductibles, attachment points) and shows how the policy limit **allocates by geographic area.**
- **Keep DLM and accumulation analyses in separate suites** — don't combine them. Grabbing a separate accumulation is not a big deal, and it's far less common.
- Cheryl **hasn't yet exercised RM's accumulation analysis** and **will spend time on it** to confirm settings.

---

## 6. Reinsurance edits — pass-through (recap)

Confirmed again this session: **adding/editing reinsurance is a pass-through to Risk Modeler** — "a perfect scenario for that." No further questions. (See the pass-through pattern in `04` §7.)

---

## 7. Test data (logistics)

- Ben has **plenty of test data**; on Cheryl's suggestion he'll also look at the **RMS/Moody's-provided sample/training data** (wide variety, "set up a little goofy," ~30 years old) — **no rush.**

---

## 8. Open questions

- **O7-1:** **HD hazard retrieval** — must it be run ahead of time? (§1.3) *Cheryl investigating.*
- **O7-2:** **Enhanced risk data** — availability (HD-only?) and whether CIC will use it. (§1.3) *Cheryl investigating.*
- **O7-3:** Finalize the **auto-naming convention** for analyses (§2.3).
- **O7-4:** Confirm the **standard suite / model-profile definitions** with Cheryl's group (§2.1/§3).
- **O7-5:** Confirm **accumulation settings** once Cheryl has exercised RM's accumulation analysis (§5); confirm whether ground-up can be dropped via the API.
