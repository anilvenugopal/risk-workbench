# Contract — routes & fragments (spec 011)

All GET; results viewing has no state-changing request. Every fragment reads
stored data only — no RM call serves a render (spec non-negotiable 1).

## 1. Merged analyses section — EDM detail

`GET /edms/{edm_id}/analyses` (existing section endpoint, extended)

- Query: `status` (existing filter) only. It rides the section/poll URL so the
  3s self-poll never resets it. No `perspective` param: the toggle lives inside
  the expanded row (O-12).
- Renders the **merged** section replacing today's separate "Analyses" and
  "Broker analyses" sections (FR-009): own rows (existing
  `executed_analysis_row` behavior: checkbox, status, failure reason, delete,
  settings expansion) plus one expandable group row per submission RDM whose
  broker rows lazy-load exactly as today
  (`/submissions/{sid}/edms/{eid}/rdms/{rid}/analyses`).
- Columns: Portfolio · Template · Peril · Region · Engine · **Currency** ·
  **AAL** · Status · Submitted · Risk Modeler; no return-period column
  (FR-010). Peril and region are codes; a broker row's one name spans the
  Portfolio and Template tracks and is followed by a hidden span so the
  Copy-table TSV stays rectangular. AAL is
  Pre-Cat Net in millions and carries the row's results state: the number when ready,
  `retrieving…` while the retrieval is queued or running, `retrieval failed`
  with the reason beside the status chip (SC-005), `—` when the run has not
  finished or the perspective is empty.
- Submitted is emitted as `<time datetime="…Z">` in UTC and formatted in the
  browser — date, time to the second, AM/PM in the reader's zone (FR-024,
  T-10).
- Row expansion is two columns (FR-011/FR-022): left the source line — the full
  analysis name on own rows — and the Metadata group (O-11), right the selected EP
  type at 50/100/250/500/1000/10000 followed by AAL and standard deviation as
  the last two rows, with the perspective and EP-type toggles in the row and no
  display toggle.
- Section summary line: status filter, **Copy table**, **Delete**, **View**.
  View is a `target="_blank"` GET form to the dedicated page with the checked
  ids in check order; after submit the checkboxes reset (O-10). Delete disables
  whenever a broker row is ticked. Existing Execute and status-filter controls
  carry over unchanged.

## 2. Submission Results section

`GET /submissions/{submission_id}/analyses` (new fragment endpoint)

- Same merged partial, submission-wide: own analyses across every EDM of the
  submission (an **EDM** column is inserted after Template here; broker rows
  read `—`), broker groups for every related RDM. Same `status` param, same
  View form (`submission=` context only).
- Included in `submission_detail.html` as a new Results section; self-polls
  every 3s only while any listed analysis or retrieval is live (same pattern
  as the EDM section).

## 3. Dedicated results page

`GET /results/analyses?ids=<uuid,uuid,…>[&submission=<id>][&edm=<id>][&perspective=GR][&ep_type=OEP]`

- Nav: hidden child node `results.analyses` under the `results` rail root
  (pattern: `submissions.detail`). One node + one handler + one template.
- `ids` order = column order (FR-016); reorder controls rewrite the param and
  re-request `#results-view` over HTMX, pushing the new URL. No hard count limit — past ~10 columns the table scrolls
  horizontally in its `overflow-x` shell (FR-015 / O-09).
- Expanded return periods (all 11) for the selected `ep_type` (`OEP` default,
  `AEP`), then AAL and standard deviation as the last two rows, one column per
  analysis — one cell per column per row, no merged cells;
  per-analysis header shows name, currency, and results state
  (pending/failed rows render as a pending column, never dropped — FR-008).
- `perspective` and `ep_type` each re-render over HTMX, screen-wide
  (FR-011/FR-012); each select includes the other's value so a swap keeps both. Units selector
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
