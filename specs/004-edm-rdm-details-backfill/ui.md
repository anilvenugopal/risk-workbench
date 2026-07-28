# Contract — UI / Page Composition (Iteration 3)

The page-composition contract for the two redesigned detail pages this iteration builds:
the **EDM detail page** (`/edms/{id}`, US1/US2/US3/US4) and the **RDM detail page** (`/rdms/{id}`, US3).
It fixes layout, the reusable table component, the collapse/expand rules, the portfolio↔analysis
linkage display, and every empty/pending/failed state — so implementation follows the aligned design
with no guesswork.

**Approved previews (the source of truth for pixels/markup):**
- `docs/ui_previews/edm_detail.html` (rev 7)
- `docs/ui_previews/rdm_detail.html` (rev 3)

Both are self-contained renders built from `docs/ui_previews/_scaffold.html`, inlining the real design
tokens and reusing the real app classes. They were iterated to a 👍 before this contract was written.
Implementation templates MUST match them; when a detail here and a preview disagree, the **preview wins**
and this file is corrected.

**Guiding principle (design record):** *scanability and quick access to information is KEY.* Wide,
comparable data is presented as **dense tables the eye can scan**, not card stacks; a frozen identifying
column keeps context while scrolling; drill-downs are one click and stay visually tied to their row.

**Compliance:** Jinja2 + HTMX, server-rendered, no SPA (Article 8). Collapse/expand is native
`<details>/<summary>` (renders server-side, works with `hx-boost`, needs no JS); Alpine.js is used only
for small client slivers (a treaty's horizontal-scroll affordance). All styling is **token-only** via ITCSS
(Article 9) — no hardcoded colors. Every view is available to any authenticated analyst — **no row scoping**
(Article 6). No page makes a Risk Modeler call — all read **stored** detail (Article 11).

---

## 1. The unified `.dtable` — one expandable comparison table

A single reusable component renders every wide, comparable list on both pages: **Portfolios**, **Treaties**,
and **Broker analyses**. It is the convention for all wide tables this iteration.

- **Frozen identifying column.** The first column (portfolio / treaty / analysis name) is
  `position:sticky; left:0` inside an `overflow:auto` shell, so the row's identity stays visible while the
  analyst scrolls the rest of the columns sideways.
- **Column grid from an inline `--cols`.** The header and each row's `<summary>` are CSS grids sharing an
  inline `grid-template-columns:var(--cols)` custom property (and a `min-width`), so header and rows always
  align and each table declares its own column widths in one place.
- **Per-row expand via native `<details>`.** Each row is a `<details class="drow">` whose `<summary>` is the
  grid row; expanding reveals a detail body. No JS — it renders and toggles server-side and offline.
- **Sticky header.** The column header is `position:sticky; top:0` within the shell.

### 1.1 Expanded-body behavior — pinned + rail-connected (the reviewed fix)

The expanded body (`.drow__body`) MUST:

- **Pin to the visible width** — `position:sticky; left:0; width:100cqw` against a `container-type:inline-size`
  shell. It stays put when the analyst scrolls the parent table's columns sideways (it does **not** slide away),
  and its width is the shell's *visible* width. If the body's own content overflows, **it** scrolls
  independently rather than dragging the parent table.
- **Read as a subsection of its row** — the open row's `<summary>` and its frozen name cell are tinted
  (`--color-brand-faint`), and the body carries a **navy left rail** (`border-left:3px solid var(--color-brand)`)
  that lands directly under the pinned name column. Panels that list analyses carry a caption naming the parent
  (e.g. *"Broker analyses linked to Meridian Primary 2026"*).

> Graceful degradation: if `100cqw`/`container-type` is unavailable in a target engine, the body still
> anchors left (`sticky; left:0`) at full width — usable, just not width-fitted.

### 1.2 Collapse rule (uniform across both pages)

- **Sections default OPEN** (`<details class="sec" open>`) — Portfolios, Treaties, Broker analyses are all
  expanded on load; the analyst sees everything without hunting.
- **Row-level drills default CLOSED** — a portfolio's inline analyses panel, a treaty's full-attribute grid,
  an analysis's full settings grid.
- **The rate / event-rate detail is the ONE nested default-closed sub-drill** (`<details class="drill">`),
  one level deeper than the settings grid (FR-031).

---

## 2. EDM detail page (`/edms/{id}`) — composition top to bottom

Payload: `edm_service.get_edm_detail(id)` (see `contracts/data-access.md`). Section order is fixed.

1. **Breadcrumb + minimal header (FR-011).** Breadcrumb from manifest position (FR-051). Header shows
   name, status chip, `as_of` last-synced trust signal (FR-052), source file (`.bak` path, truncated with a
   full-path `title`), Risk Modeler / creating-job identifiers, and portfolio count. **MUST NOT show cedant
   or line of business.**

2. **Compact 3-domain rollup strip (FR-040 / US4).** A dense, always-visible fact grid rolling up the
   per-portfolio snapshots: Portfolios, Locations, Accounts/Policies, Perils, Lines of business, Geography,
   Currency, plus two domain cells — **Treaties** (count + kinds) and **Broker analyses** (`N · M RDMs · K linked`).
   Derived, never stored, never a request-path fetch (FR-042).

3. **Portfolios section (US1) — the headline.** A `.dtable`:
   `Portfolio (frozen) · Locations · Accounts · Policies · Perils · Lines of business · Geography · Currency ·
   TIV · Analyses`. **No Records column** (records == locations). The **Analyses** cell is a descriptive count of the
   analyses linked to that portfolio — *"2 broker analyses"*, *"1 broker analysis"*, or *"None"*.
   Expanding a portfolio row (default closed) reveals the **pinned inline analyses panel** (§1.1): a trimmed
   mini-table `Analysis · RDM · Type · Peril · Region · Engine · Rate` listing **only the analyses linked to
   that portfolio**, with a *"Full settings ↓ in the Broker analyses table"* link. Read-only — no
   create/edit/split/filter control (FR-014); textual snapshot, no map (FR-016).

4. **Treaties section (US2).** A `.dtable`:
   `Treaty (frozen) · Type · Applies at · Currency · Attachment · Limit · Share · Effective`. An
   **"⭳ Export to Excel"** action sits in the section (links to `/edms/{id}/treaties.xlsx`), **not** inside a
   row. Expanding a treaty (default closed) reveals the **full attribute grid** (every field) for mis-coding
   checks (FR-021). Wide attribute sets scroll horizontally without breaking layout (FR-023). Read-only
   (FR-025).

5. **Broker analyses section (US3) — standalone, grouped by source RDM.** A `.dtable`:
   `Analysis (frozen) · Portfolio · Type · Peril · Region · Currency · Engine · Term · PLA · Rate`. Rows are
   grouped under a **`RDM` divider row** per source RDM (name + `#irp_id` + "Open RDM →"), so an analysis
   applied across M EDMs shows once (FR-030). The **Portfolio** column shows the resolved portfolio name
   (a link), **Group**, or **— not linked** (§4). Expanding a row (default closed) reveals its full settings
   grid + the rate/event-rate sub-drill. A short linkage/scope note precedes the table (portfolio linkage;
   groups; no loss numbers this iteration — FR-033).

**Placement of analyses is deliberate:** the per-portfolio inline panel is the *per-portfolio focus* (what ran
against this one), and the standalone section is the *compare-everything* place; the same analyses appear in
both, with the standalone carrying full metadata.

---

## 3. RDM detail page (`/rdms/{id}`) — composition

Payload: `analysis_service.list_broker_analyses(rdm_id=...)`.

1. **Breadcrumb + header** — name, status, `as_of`, source file, RM RDM id, and a count line
   (*"6 broker analyses across 2 EDMs"*).
2. **Broker analyses section (US3).** A `.dtable`:
   `Analysis (frozen) · EDM · Portfolio · Type · Peril · Region · Currency · Engine · Term · PLA · Rate`.
   An **EDM** column is present because one RDM's analyses can span several EDMs. Portfolio column semantics
   are identical to the EDM page (§4). Expand → full settings grid + rate/event-rate sub-drill (FR-031).
   A short "settings/metadata only this iteration — no loss numbers" note precedes the table (FR-033/FR-034).

The EDM page and the RDM page render the **same** analyses with the **same** columns and the **same** expand
behavior — the RDM page adds the EDM column (cross-EDM view); the EDM page frames them per-portfolio.

---

## 4. Portfolio↔analysis linkage — display semantics

Each broker analysis is associated with the **portfolio it was run against** (FR-036). The linkage is resolved
at read time from the analysis's captured `exposure_resource_id` (the RM `exposureResourceId`, stored only when
`exposureResourceType = PORTFOLIO`) joined to `irp_portfolio` on `(edm_id, irp_id)` — see research **R9** /
`data-model.md §4/§6`. The **Portfolio** column renders with this precedence:

| Condition | Cell |
|---|---|
| `is_group = true` | **Group** (info badge) — a group is a single analysis; its member breakdown is **not** available this iteration |
| resolves to a known `irp_portfolio` | the **portfolio name**, as a link |
| otherwise (no/again-unresolved exposure ref, non-portfolio resource) | **— not linked** (muted, with a `title` explaining no resolvable exposure) |

- **Groups are shown ONLY in the standalone Broker analyses section** (never inside a portfolio's inline
  panel), as a single row with Portfolio = **Group**. Expanding a group shows its own settings; Portfolio is
  noted as *"Group — combines multiple analyses; member breakdown not available."*
- **Inline per-portfolio panels contain ONLY clearly-linked analyses** — so there is no per-row "Link" column
  inside a portfolio (every row there is linked by definition).
- **Not-linked analyses appear only in the standalone section**, never inside a portfolio.

---

## 5. States (every section, both pages) — never an error

Forward-only backfill means "not yet available" is a normal state (SC-006). The header's core record always
renders (FR-017).

**EDM page:**
- **Still importing** — detail/rollup appear within ~1 min of import finishing.
- **Imported before this capability (forward-only)** — *"Detail not available. Imported before exposure
  backfill shipped. Re-import to populate."*
- **Backfill fetch failed (recoverable)** — *"Exposure detail unavailable."* The EDM stays *ready*; the
  fetch retries via the existing job machinery (FR-005); `as_of` dot goes amber.
- **Zero portfolios (FR-015)** — a clear *"No portfolios in this EDM."*
- **A portfolio with no linked analyses** — the Analyses cell reads *"None"*; the row still expands to an
  empty-but-labeled panel or simply shows no panel.

**RDM page:**
- **Settings not yet backfilled** — appear within ~1 min of the RDM import finishing.
- **No broker analyses** — *"No broker analyses in this RDM."*
- **Imported before this capability** — *"Settings not available. Re-import to populate."*
- **Partial metadata** — present fields render; missing fields show *"not provided"* / *"Missing"*
  (Rate); the row still renders (FR-031 / US3 acceptance 3).

---

## 6. Reusable pieces → template mapping

| Preview element | App template (per tasks.md) |
|---|---|
| EDM page shell (header + rollup slot + sections) | `app/templates/pages/edm_detail.html` |
| One portfolio row + its inline analyses panel | `app/templates/partials/portfolio_row.html` |
| One treaty row + full-attribute expand | `app/templates/partials/treaty_row.html` |
| Aggregate rollup strip | `app/templates/partials/edm_aggregate_strip.html` |
| One broker-analysis row + settings/rate drill | `app/templates/partials/broker_analysis_row.html` |
| RDM page shell + broker-analyses table | `app/templates/pages/rdm_detail.html` |
| `.dtable` / rollup / states styling (token-only) | `app/static/css/details.css` |

The `.dtable` CSS (shell + `--cols` grid + frozen column + pinned/railed body + open-row tint + the `.drill`
sub-drill) is shared across all three tables on both pages; define it once in `details.css` and reuse.
