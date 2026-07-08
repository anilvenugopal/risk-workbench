# IRP Workbench — Design Notes: Data Model & Workbench Organization

**Source:** Design session, July 7, 2026 (Ben Bailey, Anil Venugopal, Wendy Hayes, Cheryl TeHennepe, Ross Konell)
**Status:** Working design notes — to be refined into a proposed structure and reviewed at the next session (Thursday)
**Related:** `../minutes/IRP Workbench Design Session Minutes - 7-7-2026.md`, `../deliverables/04_solution_architecture.md`

---

## 1. Purpose of these notes

Capture the design decisions, open questions, and rationale coming out of the first IRP Workbench design session. Focus is on **how work is organized in the workbench** (the object model / hierarchy) and **what detail the workbench must surface** at the exposure and analysis-result levels. This is the input for a concrete proposed data structure to bring back to CIC.

---

## 2. Decisions & working conclusions

| # | Decision | Rationale | Confidence |
|---|----------|-----------|------------|
| D1 | **Submission is the top-level organizing entity** the workbench exposes to cat modelers. | The submission tells the team everything they need (treaty type, inception, expiration). Contract is a legal artifact; program/customer add confusion. | High (Wendy, Cheryl, Ross agreed) |
| D2 | **Drop Program and Customer as hierarchy levels** in the workbench (at least for MVP). | Program definition is convoluted; customer ID is a flawed legacy identifier used only by the cat team. | High |
| D3 | **EDM/RDM pair = "data package" = lowest-level object.** Exposure + losses are viewed together. | Matches how analysts actually work; "exposures and losses go together, however they come in." | High |
| D4 | **Primary search/filter dimensions: inception date, cedent (client) name, treaty type.** | These three narrow any list to a manageable set; they are already the basis of CIC's naming convention. | High |
| D5 | **Identify/track by CRM submission ID under the hood, but display human-readable names.** | IDs are static and reliable for tracking results; names (via naming convention) are what analysts recognize and sort/filter on. | High |
| D6 | **Support multiple EDMs and multiple RDMs per submission**, plus RDM-only and EDM-only degenerate cases. | Complex/global clients and reluctant data providers create these variants regularly. | High |
| D7 | **Support CSV-based results and exposure**, not only RDM/EDM (MDF/BAK). | ELTs/PLTs increasingly arrive as CSV (sometimes too big for an RDM). | High |
| D8 | **Workbench is cat-modeling-focused**; no need to render PDFs / non-modeling exhibits. | Goal is to speed the modeling process, not replace File Explorer. | High |
| D9 | **Deliver portfolio-level rollup summaries** (counts, perils, geography, currency, volume) without click-through. | Risk Modeler's deep navigation is a known deficit; analysts need fast re-orientation. | High |

---

## 3. Proposed object model (draft)

```
Submission  (CRM submission context; top-level browse/search entity)
  │   key attrs: inception date, cedent name, treaty type,
  │              treaty year, naming-convention label (e.g. TY2604_ClientName),
  │              expiring submission ID (renewal link, from treaty system)
  │
  ├── one or more CRM IDs / contracts  (many-to-many with data packages)
  │
  └── Data Package(s)   ← lowest-level working object
        ├── EDM  (exposure)          [may be BAK/MDF or CSV; may be absent → RDM-only]
        └── RDM  (analysis results)  [may be BAK/MDF or CSV/ELT/PLT; may be absent → EDM-only]
              └── Portfolio(s)
                    ├── exposure summary (counts, perils, geography, currency, volume)
                    └── analysis results (metadata + drill-down)
```

**Cardinality notes (why the schema is hard to draw):**
- Plain-vanilla case: `1 submission → 1 CRM ID/contract → 1 EDM/RDM pair`. Model this first.
- A submission can hold **multiple** EDMs and **multiple** RDMs.
- **Multiple CRM IDs (contracts) can map to one EDM/RDM pair** (e.g., CAT XOL main layers + a separately-contracted top layer).
- **One EDM/RDM pair can map to multiple CRM IDs** (same exposure base reused across reinsurance types).
- ⇒ CRM ID ↔ data package is effectively **many-to-many**.

**Recommendation:** treat the association between CRM IDs/contracts and data packages as a tagging relationship (as CIC does today in the exposure/loss repositories), rather than a strict tree.

---

## 4. The "Project" container — open design question

Risk Modeler (and competitors) offer a **project** concept: a user-defined container that can group anything (a client, a treaty year, a submission plus its adjusted databases, etc.).

**Appeal:**
- Natural home for a submission plus all derived/adjusted data (new portfolios, industry data, re-portfolioed exposure).
- Supports the renewal-centric workflow (this year's + last year's work living together for visual comparison).
- On-prem's "pull-forward and upgrade every year" burden is gone, so historical data can be organized more freely.

**Risks / tension:**
- Large clients (e.g., Allstate) may have dozens of treaties incepting at different times → a single client-lifetime project becomes unwieldy.
- CIC currently leans toward a **treaty-year / submission-scoped** project rather than client-lifetime.

**Open question (O1):** Should "project" == submission (per treaty year), or a broader client container with strong in-project filtering? Leaning: **submission-scoped project** with filtering, but confirm with CIC. The expiring-submission-ID link (from the treaty system) can provide the prior-year comparison without forcing a client-lifetime container.

---

## 5. Naming convention (authoritative reference)

`TY{YY}{MM}_{ClientName}` — e.g. `TY2604_AmericanFamily`

- `TY26` = treaty year 2026
- `04` / `06` = inception month (April / June, etc.)
- `_{ClientName}` = cedent

Encodes renewal status, treaty year, inception month, and cedent at a glance. The workbench should parse/leverage this convention for display and filtering, while keying on the CRM submission ID internally.

---

## 6. Exposure detail — portfolio-level summary spec (draft)

Surface these **at the portfolio level** (rolled up, no drill-down required):

- **Counts:** locations, accounts, policies; number of portfolios within the EDM.
- **Perils / sub-perils** covered.
- **Geography:** high-level — region(s) or state(s), or CIC-defined region (e.g., "Southeast," "Florida only"). **No map required.**
- **Currency.**
- **Volume / record counts** (so analysts know, e.g., ~1M vs ~20K records before running).
- Treaties associated with the EDM.

**Design intent:** a "quick hit" snapshot enabling instant re-orientation. Do **not** rebuild CIC's existing Power BI exposure dashboards.

**Re-grouping support:** analysts often need to **re-portfolio** exposure before analysis to match treaty terms the broker didn't break out (e.g., isolate one state with a different retention; exclude a line of business). The workbench should make it easy to see the current split and create alternate portfolio groupings prior to running analyses.

---

## 7. Analysis-results detail — spec (draft)

Metadata to surface per analysis result:
- **Rate**
- **Perils run** and **sub-perils run**
- **Loss amplification** on/off
- **Detail level** losses are saved at
- **Portfolio (or portfolio group)** the analysis was run against

**Volume handling (highly variable — 4 to 100+):**
- ≤ ~5 results: consumable directly on screen.
- \> ~5 results: provide export and/or a summarized view with drill-down, while still conveying the overall "package."

**Out of scope here:** policy-level detail (obtained earlier in the process, rarely needed in the workbench) and user-defined fields (purely text tags for filtering/sub-portfolios; do **not** affect losses).

**Noted difficulty:** rolling up exposure is straightforward; the **harder** design problem is presenting analysis results in relation to portfolios in a digestible way.

---

## 8. Constraints, context & non-goals

- **Don't reinvent the wheel:** the move to Risk Modeler should reuse existing building blocks and avoid unnecessary disruption — but legacy constraints ("we did it that way because we had to") should be questioned now that everything is in one cloud environment.
- **Risk Modeler navigation gap:** no simple alphabetical browse like Risk Link; data access must be "tamed" via the workbench.
- **Treaty rebuild dependency:** CIC is separately rebuilding the treaty/CRM hierarchy. Broader objects (program, customer, contract relationships) can be sourced from the treaty system later; the workbench should not hard-code a hierarchy above submission yet.
- **Existing tagging model:** exposures and losses are already tagged with CRM ID / submission ID in the exposure and loss repositories — align the workbench's association model with this.
- **Future platform:** possible future support for Verisk Touchstone / Synergy Studio; design shouldn't preclude it but MVP is Risk Modeler only.

---

## 9. Open questions to resolve

- **O1** — "Project" scope: submission-scoped vs. client-lifetime container? (see §4)
- **O2** — Do we still persist/display the legacy customer identifier anywhere, or fully retire it from the workbench? (Ross flagged some groups may still be using customer ID — pending a separate CIC conversation.)
- **O3** — Exact on-screen vs. export threshold for analysis-result counts (working assumption: ~5).
- **O4** — How to visually represent many-to-many CRM ID ↔ data package associations in the UI without overwhelming the analyst.
- **O5** — Which portfolio-summary fields are cheap to compute from the EDM directly vs. require enrichment (ties into earlier Data Bridge / reference-data discussions).

---

## 10. Next actions (PremiumIQ)

1. Produce a **proposed data-organization diagram** (submission → data package) reflecting §3, starting from the plain-vanilla case and layering permutations.
2. Draft **portfolio-level exposure summary** and **analysis-result** view specs (§6, §7) for review.
3. Confirm the **"project" container** direction with CIC (§4/O1).
4. Bring the above to the **Thursday (~1:30 PM Central)** design session.
