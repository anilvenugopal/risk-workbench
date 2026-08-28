# Implementation Plan: Analysis Comparison (Iteration 10)

**Branch**: `013-analysis-comparison` | **Date**: 2026-08-27 | **Spec**: [spec.md](spec.md)

<!-- Technical only. User stories and scope → spec.md. Schema → data-model.md.
     Payloads → contracts/. Endpoint investigation → research.md. Everything
     above the `---` is what a reviewer reads to decide: ten minutes to read. -->

## Plan status

**Ready for tasks:** Yes
**Blocked by:** Nothing.

## Design summary

- No schema change, no worker change, no gateway change, no new dependency:
  the feature reads `irp_analysis.loss_results`, `submitted_settings`, and
  `settings_metadata` — all written by spec 011 — and renders them.
- The merged analyses section's summary bar gains **Compare** beside View, on
  both entry points (submission Results section, EDM detail). It needs no row
  selection and is always available; the modal reports the case where fewer
  than two of the scope's analyses have retrieved results (T-05, FR-001).
- Compare fetches a modal fragment over HTMX into a `#compare-modal-mount`
  outside the self-polling section (breakout-modal precedent, so the 3s poll
  never removes an open modal). One modal route per analyses-fragment family:
  `/submissions/{sid}/analyses/compare`, `/edms/{eid}/analyses/compare`,
  `/submissions/{sid}/edms/{eid}/analyses/compare` — all one shared handler
  ([contracts/routes.md](contracts/routes.md) §1).
- The fragment lists the table-at-hand's analyses via a new read model,
  `analysis_service.list_comparable_analyses` — own rows then broker rows in
  table order, each with display name, origin, run currency, and results
  state. Rows still retrieving or failed are listed but not tickable (FR-002).
- The cart is an Alpine sliver inside the fragment (T-02): tick order marks
  the first pick *base* (FR-003); Add pair refuses a currency mismatch or an
  unrecorded currency, naming both (FR-005); the cart caps at 5 pairs
  (FR-004); Compare opens `window.open` on the built URL — the View button's
  new-tab pattern. No server round trip serves a pair-add; the page render is
  the enforcement (T-01).
- New page: `GET /results/comparison?pairs=<base>:<second>,…[&submission=…]
  [&edm=…][&perspective=…][&ep_type=…]` — one hidden nav node
  (`results.comparison`, label "Comparison"), one handler in
  `app/routers/shell.py`, one template (Article 1). Breadcrumbs and tab title
  follow the results page's `extra_crumbs` mechanism verbatim (FR-007).
- The route re-validates every pair at render and drops offenders whole with
  the FR-015 notice: unresolvable side, identical ids, unrecorded or unequal
  currencies, pairs beyond the first 5. A hand-typed mixed-currency pair
  therefore never renders arithmetic (SC-003); no surviving pairs → the empty
  state (P-06).
- A new service read, `list_comparison_pairs`, resolves pairs on the
  `list_results_columns` query and extends the column model with `engine`
  (the extract's `engine_type`/`engine_version` snapshot, spec 011 FR-021)
  and `run_currency` (own: `submitted_settings.currency.code`; broker:
  `settings_metadata.currencyCode` — T-03/T-04, FR-005/FR-011).
- Percent change is computed in the read model per displayed row —
  (second − base) / base for the 11 return periods, AAL, and standard
  deviation — rendered server-side as a signed one-decimal percent; absent
  side or zero/missing base → em dash, never an error and never `inf` (T-06,
  FR-009/FR-014).
- The page template renders one shared return-period column and three columns
  per pair (base, second, % Chg) labelled with analysis names; each side's
  header sub-line carries its run currency and engine (FR-008/FR-011). AAL
  and Std dev sit below the curve rows, outside the EP-type selection
  (FR-010).
- Toolbar = the results page's, verbatim in pattern: perspective and EP type
  re-render `#comparison-view` over HTMX with the `pairs` param carried
  (screen-wide by construction, FR-012); units and Copy table are the
  existing `data-units-select` / `data-copy-table` slivers — loss cells carry
  `data-unit-value`, percent cells none, so units never rescale a percent
  (FR-013, T-06).
- Styling extends `.ep` / `.res-toolbar` in `app/static/css/details.css` and
  the `.modal` components via tokens (Article 9); the approved preview
  `docs/ui_previews/analysis_comparison.html` is guidance, not markup.

## Material changes

| Area | Change |
|---|---|
| Database | None — no migration, no seeds, no new columns. |
| Worker | None. |
| Service | `analysis_service`: `ResultsColumn` gains `engine` + `run_currency`; new `list_comparison_pairs` and `list_comparable_analyses`. |
| UI | Compare button + modal mount on both entry points; compare-modal partial + Alpine cart; `/results/comparison` route, hidden nav node, page template; small `details.css`/`components.css` extensions. |
| Library | None. |

## High-risk technical decisions

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | Page contract `GET /results/comparison?pairs=base:second,…`; server re-validates every pair at render (resolve, distinct, recorded+equal currencies, cap 5) and drops offenders with the FR-015 notice — the render, not the modal, enforces SC-003 | Approved | [research.md#R1](research.md#r1--page-contract-get-resultscomparisonpairs-with-server-re-validation-t-01) |
| T-02 | The cart is an Alpine sliver in a server-fetched modal fragment mounted outside the 3s-polling section; pair-add guards run client-side off `data-currency`/state attributes; no server round trip per add | Approved | [research.md#R2](research.md#r2--the-cart-is-an-alpine-sliver-in-a-server-fetched-modal-fragment-t-02) |
| T-03 | Run currency per side: own = `submitted_settings.currency.code`, broker = `settings_metadata.currencyCode`; unrecorded → listed, not pairable (P-05); the same value labels the column header | Approved | [research.md#R3](research.md#r3--currency-and-engine-sources-per-side-t-03-t-04) |
| T-04 | Engine/model version per side from the extract's `engine_type`/`engine_version` snapshot (spec 011 T-04/FR-021), never `settings_metadata` and never re-fetched | Approved | [research.md#R3](research.md#r3--currency-and-engine-sources-per-side-t-03-t-04) |
| T-05 | Modal lists the scope via new `list_comparable_analyses` composing the table's existing reads; the modal's own row list, not a separate count, reports the fewer-than-two case | Approved | [research.md#R4](research.md#r4--modal-data-and-compare-enablement-t-05) |
| T-06 | Percent change computed server-side per displayed row; zero/missing base or absent side → em dash; percent cells carry no `data-unit-value`, so the units sliver never rescales them | Approved | [research.md#R5](research.md#r5--percent-change-is-server-computed-the-slivers-are-untouched-t-06) |

---

## Technical Context

<!-- Only what changed or constrains the design. The stack is documented in
     docs/PRD.md §3 (Technology stack & environment); architecture rules in
     .specify/memory/constitution.md. Do not restate either. -->

**New dependencies**: None. No irp-integration call is added or changed.
**Databases touched**: `rwb_workbench` only, read-only — every read is over
existing `irp_analysis` columns and the existing `rwb_job` failed-retrieval
join. `rwb_exposure` / `rwb_loss` / DATABRIDGE untouched.

## Constitution Check

*GATE: before Phase 0 research, re-checked after Phase 1 design.*

Reviewed against all 13 articles in `.specify/memory/constitution.md`: **no
violations** (re-checked after Phase 1 design — unchanged).

Material interactions — where an article actively shapes this design:

- **Article 11 (IRP work behind an interface)**: no Risk Modeler call exists
  anywhere in this feature — the page, the modal, and the enablement count
  read stored extracts and snapshots only (spec FR-016, non-negotiable 3).
- **Article 8 (server-rendered)**: the comparison page is a real URL whose
  `pairs` param is the whole page state; perspective/EP switches are HTMX
  fragment re-renders carrying that param (screen-wide by construction,
  FR-012); Alpine is confined to the modal cart and the existing units/copy
  slivers. This article is why the page is GET-opened, not POST-opened (T-01).
- **Article 1 (nav manifest)**: one hidden nav node (`results.comparison`) +
  one handler + one template; breadcrumbs derive from the manifest chain plus
  the existing `extra_crumbs` shell hook — no second breadcrumb source.
- **Article 7 (one data-access package)**: all new reads are
  `db.execute` bound-parameter queries in `analysis_service`; the `pairs`
  param is parsed to UUIDs before any query sees it.
- **Article 9 (ITCSS tokens)**: percent/base-tag/cart styles extend
  `details.css` and the modal components through existing tokens; no
  hardcoded hex, no preview classes pasted.
- **Article 3 (kind tables)**: no new categorical. Perspectives keep reading
  `analysis_perspective_kind`; the OEP/AEP pair stays the results page's
  existing code-level vocabulary (it mirrors RM's `epType` response values).
- **Article 12 (test-first)**: the pair validator (a point-of-action
  validator, §13.3) and the percent-change math get unit coverage before the
  templates render them — see Testing.

## Project Structure

### Documentation (this feature)

```text
specs/013-analysis-comparison/
├── plan.md              # This file
├── research.md          # R1–R5
├── data-model.md        # View models only — no schema change
├── quickstart.md        # Per-story verification
├── contracts/
│   └── routes.md        # Compare modal fragments + /results/comparison
└── tasks.md             # /speckit-tasks output (not created by /speckit-plan)
```

### Source Code (changed directories only)

```text
app/
├── routers/edms.py                  # compare-modal fragment (EDM + contextual)
├── routers/submissions.py           # compare-modal fragment (submission scope)
├── routers/shell.py                 # /results/comparison handler
├── services/analysis_service.py     # engine/run_currency, comparable list,
│                                    # pair resolution + % change, ready count
├── nav/manifest.py                  # results.comparison hidden node
├── templates/pages/results_comparison.html   # dedicated page (new)
├── templates/partials/analyses_merged_section.html  # Compare button
├── templates/partials/compare_modal.html      # modal + Alpine cart (new)
├── templates/pages/edm_detail.html / submission_detail.html  # modal mount
└── static/css/details.css, components.css     # pct cell, base tag, cart rows

tests/unit/                          # see Testing
```

**Structure Decision**: existing single-app layout; no new top-level
directories. New files: one page template, one modal partial, one contract
file.

## Complexity Tracking

No constitution violations to justify.

## Testing

<!-- Strategy by tier. Not a test-file inventory. -->

- **Unit**: pair parsing and render-time validation (unresolvable side,
  self-pair, mixed and unrecorded currencies, sixth-pair truncation, drop
  notice vs. empty state); percent-change math (sign, zero base, missing
  base, absent perspective on either side); read models (engine and
  run-currency extraction per origin, comparable-analyses list order and
  tickability, results-ready count deduping broker handles); route renders
  (comparison page with 1–5 pairs, perspective/EP params, crumbs and tab
  title per entry point, modal fragment on all three scopes, Compare button
  enablement).
- **SQL Server integration**: nothing new — no migration, no seeds, no new
  write path; the existing suite is unaffected.
- **IRP sandbox**: N/A — the feature makes no IRP call.
