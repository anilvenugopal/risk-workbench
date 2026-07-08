# IRP Workbench — Design Notes: How CIC Actually Organizes Its Data (As-Is) + Tagging Requirements

**Source:** Design session, July 7, 2026, reconciled with prior discovery (exposure/loss repositories, DataBridge, CRM).
**Status:** Working analysis — foundation for the Submission/Package structure decision.
**Related:** `01_data_model_and_workbench_organization.md`, `../minutes/IRP Workbench Design Session Minutes - 7-7-2026.md`

---

## 0. TL;DR — the vocabulary is the problem

The CIC team used three words as if they were the same thing. They are not, and this is the root of the confusion:

- **Contract** — a *legal* object (the reinsurance treaty as executed).
- **CRM ID** (a.k.a. "CRM submission ID") — the *unique key* CRM assigns. **CRM keys this at the contract level — one CRM ID per contract.**
- **"Submission"** — used two different ways in the room:
  - **(a) CRM's formal record** = one per contract (so effectively = CRM ID).
  - **(b) The cat team's working unit** = the *deal* an analyst sits down to model, which may span several contracts but produces **one set of modeling data**.

When Wendy said "the submission tells us everything" she meant sense (b), the working deal. When Cheryl said "the CRM ID is really per contract" she meant sense (a). **Both are true; they're describing different layers.** The workbench needs to model both and keep them straight.

**One-line mental model:**
> An analyst works on a **deal** (a cedent's treaty renewal at a given inception). That deal produces **one set of modeling data** (one or more EDM/RDM packages). The deal is *tracked for the business* by **one or more CRM IDs** (one per legal contract). Exposures and losses are **tagged with those CRM IDs** so results can be tied back.

---

## 0.5 KEY CONSTRAINT — CRM ID is manual, optional, and cannot be programmatically resolved

**There is no CRM (or treaty-system) integration in scope.** This has three consequences that ripple through the whole model:

1. **CRM ID is entered by hand** and may be **absent or mistyped**. It cannot be the reliable backbone of the data model.
2. **Attributes cannot be derived from the CRM ID.** Cedent, treaty type, inception date, treaty year, etc. must be **captured directly** (ideally parsed from the naming convention / directory, plus analyst input) — the workbench is the *system of record* for this cat-modeling metadata, not a CRM mirror.
3. **The renewal link (expiring submission ID) is probably also manual** unless the treaty system turns out to be readable — do not assume it's free (see O-d).

**The irony to raise with CIC:** Cheryl rejected tagging on database names because they're "prone to user error" — but a hand-typed, unenforced CRM ID is *exactly* that. So the **robust organizing identity must be cedent + treaty type + inception** (kept consistent via autocomplete/reuse), with **CRM ID demoted to an optional reference tag** whose main job is downstream tie-back to the exposure/loss repositories.

---

## 1. Where the data physically lives (system map)

| System | What it holds | Key / identifier | Notes |
|--------|---------------|------------------|-------|
| **CRM** | Submission/contract records; the "larger relationship" (parent co., subsidiaries, which entities buy reinsurance across multiple submissions); legacy customer ID | **CRM submission ID (per contract)** | System of record for treaty type, inception date, expiration date, cedent. |
| **Treaty system** (under rebuild, Ross owns) | Broader hierarchy objects; **expiring submission ID** (prior-year renewal link) | Expiring submission ID → current submission ID | Renewal relationship is *already* maintained here — no manual linking needed. |
| **Exposure repository** (on-prem SQL) | Uploaded/aggregated exposures | **Tagged with CRM ID / submission ID** | NOT keyed by the SQL database name. |
| **Loss repository** (on-prem SQL) | Uploaded loss results | **Tagged with CRM ID / submission ID** | Same tagging discipline as exposure repo. |
| **RiskLink → Risk Modeler** | EDMs (exposure) and RDMs (results) | Database name (RiskLink) / RM internal IDs | RiskLink = alphabetical browse ("scroll to the A's"). Risk Modeler has no such browse — data must be "tamed" by the workbench. |
| **File shares** | Raw incoming EDM/RDM files (BAK, MDF, CSV) | **One directory per submission** | This is where an analyst first receives and stages a deal's data. |
| **Power BI** | Exposure dashboards | — | Already exists — don't rebuild. |

**Takeaway:** in CIC's *back-end systems*, the CRM ID is the key that threads through CRM, the exposure repo, and the loss repo. **But the workbench cannot reach those systems (see §0.5)** — so from the workbench's perspective, CRM ID is a hand-entered reference value, not a live key. The identity the workbench can actually rely on is what the analyst supplies and what the **file share already encodes**: the file share is organized by *deal* (per-submission directory) with the naming convention (`TY{YY}{MM}_{Cedent}`) carrying cedent / month / treaty year. That — not the CRM ID — is the dependable organizing signal.

---

## 2. Entity glossary (as CIC uses them)

- **Cedent (client / customer):** the insurer ceding risk to CIC (e.g., Allstate, American Family). Recognized by **name**. There *is* a legacy "customer ID," but it's flawed (parent/sub/group tangles) and the team wants to stop leaning on it.
- **Program:** loosely = all the reinsurance a cedent buys. Deliberately vague/"loosey-goosey" — a cedent buys different treaty types at different times of year, with or without an MGA. **CIC does not really use this level anymore; out of scope for the workbench.**
- **Contract:** the legal treaty object. Drives how CRM records things.
- **CRM ID:** unique per contract. The tagging key.
- **Deal / working submission:** the analyst's unit of work — a specific cedent's specific treaty at a specific inception (e.g., "6/1/2026 CAT XOL for Allstate"). Produces **one set of modeling data.**
- **Data package (EDM/RDM pair):** the lowest-level modeling object. EDM = exposure; RDM = analysis results. Either can be absent (RDM-only / EDM-only), and either can arrive as BAK/MDF **or CSV**.
- **Portfolio:** a grouping of exposure within an EDM. One EDM may contain 1 or 25+ portfolios. Analysts often **re-portfolio** to match treaty terms.

---

## 3. The relationships — and why the schema is hard to draw

Here is the actual cardinality, with the concrete examples the team gave:

```
Cedent (client)
  │  1 ── *  (a cedent has many contracts over time / treaty types)
  ▼
Contract  ═══ 1:1 ═══  CRM ID        ← CRM keys at this level
  │
  │   *──*  (many-to-many, both directions — see below)
  │
  ▼
Data Package (EDM/RDM pair)
  │  1 ── *  (a package's EDM has many portfolios)
  ▼
Portfolio ──► exposure summary + analysis results
```

**The deal ("working submission") sits across the middle:** it bundles *one or more CRM IDs* together with *one or more data packages* that all share one set of modeling.

### The four cases (from plain-vanilla to the outliers)

1. **Plain vanilla (~the 80% case):** 1 deal → 1 CRM ID/contract → 1 EDM/RDM pair. *Model this first.*
2. **Multiple contracts, one modeling set:** e.g., a CAT XOL where the **main layers** are one contract (CRM ID #1) and a separately-negotiated **top layer** is another contract (CRM ID #2) — but it's all modeled once. → **many CRM IDs → one data package.**
3. **Multiple packages, one deal:** a complex/global cedent sends data from several sub-organizations, each with its own database → **one deal → many EDMs and many RDMs.**
4. **One package, multiple deals/CRM IDs:** the **same exposure base** is reused for different reinsurance types (brokers won't send identical data twice) → **one data package → many CRM IDs.**

⇒ The relationship between **CRM IDs and data packages is many-to-many.** That is why it can't be drawn as a clean tree, and why a rigid parent→child hierarchy will fight the real data.

### Degenerate package shapes (must be supported)
- **EDM + RDM pair** — the normal case, "they go together."
- **RDM only** — provider won't share exposure; can't re-run analyses, only review provided losses. Outlier but real.
- **EDM only** — side case, rare.
- **CSV instead of RDM/EDM** — ELTs/PLTs (and sometimes exposure) arrive as CSV, occasionally because they're too big for an RDM.

---

## 4. Tagging requirements (the actual "make it tie back" rules)

The heart of what CIC needs — **re-framed for the manual/optional CRM-ID reality (§0.5):**

1. **Exposures and loss results must be *taggable* with one or more CRM IDs — captured, not enforced.**
   - Today, "every exposure that gets uploaded, every loss result that gets uploaded, gets tagged with the CRM ID / the submission ID." That tagging is what lets results tie back.
   - But since the CRM ID is hand-entered, the workbench should **allow it to be blank, added later, and edited** — not block work on it.
   - Because of the many-to-many reality (§3, cases 2 & 4), when present the tag must be a **set of CRM IDs**, not a single value.

2. **The *reliable* identity is cedent + treaty type + inception — not the CRM ID.**
   - Cheryl rejected database names as tie-back keys because they're "prone to user error." A hand-typed, unenforced CRM ID has the same weakness. So organization and search must lean on the **cedent / treaty type / inception** identity (kept consistent via autocomplete and reuse of prior deals), with CRM ID as a best-effort reference value layered on top.

3. **Human-facing attributes are captured directly (cannot be derived).**
   - Cedent name, treaty type, inception date, expiration date, treaty year — the workbench **cannot pull these from CRM** (§0.5). Capture them by **parsing the naming convention / directory name** and letting the analyst confirm/complete them. The workbench is the system of record for this metadata.

4. **Where CRM ID actually matters → soft-gate there, not at creation.**
   - The one place the CRM ID is genuinely required today is **uploading to the exposure/loss repositories** (that's where the tag lives). Recommendation: leave CRM ID optional throughout, but **warn/prompt for it at the repository-upload step** if missing. Confirm with CIC whether repo upload still depends on it (**O-c/O-e**).

5. **Renewal linkage is likely manual too.**
   - The **expiring submission ID** lives in the treaty system, but with no treaty-system integration (§0.5) the workbench probably can't read it. Plan for renewal linking to be **manual or inferred** (match cedent + treaty type across treaty years). Confirm with Ross (**O-d**).

6. **Association happens at the package level, from a per-submission directory.**
   - Files land in a per-deal directory. The workbench should let the analyst pick the EDM(s)/RDM(s)/CSV(s) in that directory, declare which is exposure vs. results, pair them, and (optionally) attach the CRM ID tag(s).

---

## 5. What analysts use to *find* and *recognize* work

Distinct from the tagging key, this is how humans navigate:

- **Primary filters (confirmed):** inception date · cedent name · treaty type. These three narrow any list to a handful.
- **Naming convention (already in use):** `TY{YY}{MM}_{Cedent}` → e.g. `TY2604_AmericanFamily` (treaty year 2026, June inception, American Family). Encodes renewal status, year, month, cedent at a glance.
- **Design implication:** since the CRM ID isn't a live/reliable key (§0.5), the **naming-convention label + those three filters ARE the primary key the workbench organizes and searches on.** The workbench should generate its own stable internal ID per deal/package, capture the human attributes up front (parsed + confirmed), and treat CRM ID as optional reference metadata.

---

## 6. So how do we make organizing data *easy*? (recommended model)

The reality above points to a clear, low-friction model:

**Primary container = the deal ("submission" in the analyst's sense).**
- It's what analysts already think in ("I'm working on the 6/1 Allstate CAT XOL").
- It maps 1:1 to the per-submission file directory they already keep.
- It carries the human attributes (cedent, treaty type, inception, treaty year) used for search.

**Inside a deal: one or more data packages (EDM/RDM pairs).**
- Package is the lowest-level working object; exposure + losses viewed together.
- Supports the degenerate shapes (RDM-only, EDM-only, CSV).

**CRM IDs are *optional tags* (a many-to-many association), not a level in a tree.**
- Attach zero, one, or many CRM IDs to the deal and/or to individual packages; blank is allowed and can be filled in later.
- When present, tagging a package propagates to the exposures and losses it contains → satisfies the repository tagging rule at upload time.
- Human attributes are **captured/parsed on the deal itself** (not derived from the CRM ID — §0.5).

**Why tagging beats a strict hierarchy:** every "outlier" the team raised (multiple contracts on one package, one package across multiple deals, multiple packages per deal) is a *natural* many-to-many association but a *broken* parent-child tree. Modeling CRM ID as an optional tag set makes the outliers ordinary instead of exceptional — and it degrades gracefully when the CRM ID is missing.

**How this makes it easy for the analyst:** the deal is created straight from the incoming file directory — the workbench parses `TY{YY}{MM}_{Cedent}` to pre-fill treaty year, month, and cedent; the analyst confirms treaty type and inception; CRM ID is an optional field they fill when/if they have it. Autocomplete on cedent names and a "renew from last year" action keep values consistent without any CRM lookup.

**Explicitly out of scope (per CIC):** Program level, and — pending confirmation — the legacy Customer ID as a navigational level.

---

## 7. The one decision we need CIC to confirm

Everything above hinges on a single question:

> **Is the workbench's "submission" the CRM record (one per contract), or the analyst's deal (one set of modeling data that may span several contracts / CRM IDs)?**

- **Recommended: the deal.** Model "submission" = deal, with CRM IDs as many-to-many tags. This matches how analysts work and browse, matches the per-submission file directory, and turns the outliers into normal cases. CRM-level (per-contract) reporting is still fully served because every package/loss/exposure carries its CRM ID tag(s).
- **Alternative: the CRM record.** Simpler 1:1 with CRM, but it fragments a single modeling effort across multiple "submissions" and doesn't match analyst navigation — we'd be fighting the data.

If CIC confirms "the deal," the §6 model is ready to turn into a concrete schema and UI proposal for Thursday.

---

## 8. Open items feeding this decision

- **O-a:** Confirm submission = deal vs. CRM record (§7). *Blocking.*
- **O-b:** Do we retain the legacy Customer ID anywhere (Ross noted some groups may still use it) or fully drop it from the workbench?
- **O-c:** When a single package is shared across multiple CRM IDs (case 4), how do we want results/losses attributed on export — duplicated per CRM ID, or referenced once and fanned out?
- **O-d:** With no treaty-system integration (§0.5), how do we handle the renewal / expiring-submission-ID link — manual entry, or inferred by matching cedent + treaty type across years? (Ross to advise; also whether the treaty rebuild changes the CRM-ID-per-contract assumption.)
- **O-e:** Does pushing to the exposure/loss repositories still *require* the CRM ID tag? If yes → soft-gate at the upload step (§4.4). If the workbench doesn't feed the repos, CRM ID is purely an optional annotation.
- **O-f:** Confirm there is genuinely no read-only path to CRM/treaty (even a nightly export or shared view). Any such path would let us *validate* or *auto-fill* attributes and would materially improve data quality — worth a direct ask before we commit to fully-manual capture.
