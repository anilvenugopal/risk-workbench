# Route Contracts: Grouping Execution (spec 012)

All routes CSRF-protected, HTMX-rendered. Patterns follow the analysis-execute
modal (`app/routers/edms.py` execute routes).

## `GET /submissions/{submission_id}/analyses/group`

Renders the compose dialog into `#group-modal`.

Query params: `analysis_ids` (repeated, optional) — the grid's ticked rows,
pre-checked in the pick-list.

Context:

- `members`: every eligible member of the submission — own analyses with
  `status_code='ready'`, broker analyses, finished groups — each with
  `id, display_name, kind (own|broker|group), engine, edm_name`. Ineligible
  rows (running/failed) are not listed (FR-003, US-1 acceptance 2).
- `group_name`: prefilled `CRE_<submission name>_Group` (T-09), editable.
- Currency block: the `currency_block` macro from
  `execute_analysis_modal.html` with `currency_defaults()` prefill and the
  existing `/edms/execute/vintage-options` cascade (FR-004).
- `propagate_detailed_output`: checkbox, checked (FR-005).
- `create_independent_groups`: checkbox, unchecked (FR-006); rendered only
  after the T-08 sandbox verification passes — if the platform rejects
  single-member grouping jobs the checkbox is dropped entirely (O-08).
- `blocking` message instead of the form when the submission has fewer than
  two eligible members.

## `POST /submissions/{submission_id}/analyses/group`

Form fields: `csrf_token`, `member_ids` (repeated, ≥2), `group_name`,
`currency_code`, `currency_scheme`, `currency_vintage`,
`propagate_detailed_output` (checkbox), `create_independent_groups`
(checkbox).

Behavior:

- Gate (`grouping_service`): every member exists, is not deleted, belongs to
  this submission, is finished; at least two members; `group_name` non-empty
  and free among live group names of the submission (the `_n` suffix is
  applied automatically on collision, not an error); currency triple resolves
  in the cache (same `_validate_currency` rules). All failures collected →
  422 re-render of the dialog with the error list
  (`HX-Retarget: #group-modal`), nothing persisted (SC-005).
- Success: persist the approved plan as one `submit_grouping` `rwb_job`,
  dispatch, respond `204` with
  `HX-Trigger: {"grouping-submitted": true, "rwb:toast": …}`. The merged grid
  and job views pick the new rows up through their existing polling.

## Merged grid changes (`analyses_merged_section.html`)

- Summary bar gains **Group** (`data-group-analyses`), enabled when ≥2 rows
  are ticked; opens the GET route above with the ticked ids. Rendered only
  when the section has submission context (submission page and
  submission-contextual EDM page — T-12).
- Group rows render via `executed_analysis_row.html` with: Portfolio/Template
  cells empty (a group has neither), EDM cell empty, **Engine cell "Group"**
  (FR-014), Currency/AAL/Status/Submitted/Risk Modeler as for any analysis.
  Group rows are selectable for View (FR-015) and for further grouping
  (FR-018), and deletable when `is_deletable`.

## `GET /results/analyses` — no contract change

Group ids arrive in `ids` like any analysis id; `list_results_columns`
resolves them (no EDM filter), and the existing neighbour-swap ordering
control covers group columns (FR-016). Left-to-right order remains the `ids`
param order.
