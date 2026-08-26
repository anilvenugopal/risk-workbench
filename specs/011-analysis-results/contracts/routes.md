# Contract — routes & fragments (spec 011)

All GET; results viewing has no state-changing request. Every fragment reads
stored data only — no RM call serves a render (spec non-negotiable 1).

## 1. Merged analyses section — EDM detail

`GET /edms/{edm_id}/analyses` (existing section endpoint, extended)

- Query: `status` (existing filter), **`perspective`** (new; default `GR`).
  Both ride the section/poll URL so the 3s self-poll never resets them.
- Renders the **merged** section replacing today's separate "Analyses" and
  "Broker analyses" sections (FR-009): own rows (existing
  `executed_analysis_row` behavior: checkbox, status, failure reason, delete,
  settings expansion) plus one expandable group row per submission RDM whose
  broker rows lazy-load exactly as today
  (`/submissions/{sid}/edms/{eid}/rdms/{rid}/analyses`, now carrying
  `perspective` through).
- New columns on every analysis row: **Currency**, **AAL** (selected
  perspective; `—` when the perspective is empty); no return-period column
  (FR-010). Results state per row: ready (AAL shown) / results-pending /
  results-pending + failure reason tooltip (SC-005).
- Row expansion adds the inline condensed block (FR-011): OEP and AEP at
  50/100/250/500/1000/10000, section-wide perspective, no display toggle.
- Toolbar: perspective select (labels/order from `analysis_perspective_kind`),
  **View** button — a `target="_blank"` GET form to the dedicated page with
  the checked ids in check order; after submit the checkboxes reset (O-10).
  Existing Execute/Delete/status-filter controls carry over unchanged.

## 2. Submission Results section

`GET /submissions/{submission_id}/analyses` (new fragment endpoint)

- Same merged partial, submission-wide: own analyses across every EDM of the
  submission (rows gain an EDM column here), broker groups for every related
  RDM. Same `perspective` param, same View form (`submission=` context only).
- Included in `submission_detail.html` as a new Results section; self-polls
  every 3s only while any listed analysis or retrieval is live (same pattern
  as the EDM section).

## 3. Dedicated results page

`GET /results/analyses?ids=<uuid,uuid,…>[&submission=<id>][&edm=<id>][&perspective=GR]`

- Nav: hidden child node `results.analyses` under the `results` rail root
  (pattern: `submissions.detail`). One node + one handler + one template.
- `ids` order = column order (FR-016); reorder controls rewrite the param and
  re-request. No hard count limit — past ~10 columns the table scrolls
  horizontally in its `overflow-x` shell (FR-015 / O-09).
- Expanded return periods (all 11), both EP types, one column per analysis;
  per-analysis header shows name, currency, and results state
  (pending/failed rows render as a pending column, never dropped — FR-008).
- `perspective` re-renders over HTMX, screen-wide (FR-012). Units selector
  (ones/thousands/millions, millions default) and copy-with-headers (TSV to
  clipboard) are client-side over `data-value` attributes (FR-017/FR-018).
- `{% block title %}` = the submission or EDM name (FR-014). Breadcrumbs:
  manifest chain ("Results") + `extra_crumbs` appended by the handler —
  `edm=` present → submission crumb then EDM crumb; else submission crumb
  only; both link back (FR-014).
- Broker columns never show a portfolio (FR-020); own columns may.
- Unknown/deleted ids render as an absent-analysis notice, not a 500.

## 4. Shell extension

`app/templates/base/shell.html`: render an optional `extra_crumbs`
(`[{label, route}]`) after `nav.breadcrumb`. Pages that do not pass it are
unaffected.
