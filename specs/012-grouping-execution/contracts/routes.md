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
- `propagate_detailed_output`: checkbox, checked (FR-005). The only setting
  besides the currency block — Risk Modeler's Create independent groups
  checkbox is not carried over (FR-006, O-08).
- `blocking` message instead of the form when the submission has fewer than
  two eligible members.

## `POST /submissions/{submission_id}/analyses/group`

Form fields: `csrf_token`, `member_ids` (repeated, ≥2), `group_name`,
`currency_code`, `currency_scheme`, `currency_vintage`,
`propagate_detailed_output` (checkbox).

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
  `HX-Trigger: {"grouping-submitted": true, "rwb:toast": …}`. The toast is
  the analyst's immediate confirmation, and the `rwb_job` is visible in the
  RWB jobs monitor at once; the group `irp_analysis` row does not exist until
  the worker claims the job (normally within seconds), so the merged grid
  shows it from claim onward through its existing polling.

## `POST /submissions/{submission_id}/analyses/delete`

Multi-select delete from the submission's Results grid. Form fields:
`csrf_token`, repeated `analysis_ids`. Same cascade, validation, and response
as the EDM route (spec 010 `contracts/routes.md` — Risk Modeler delete first,
local soft delete on success, `204` with
`HX-Trigger: {"analyses-changed": true, "rwb:toast": …}`, `422` banner on a
validation failure).

What differs is the candidate set: `delete_submission_analyses` validates
against `list_submission_executed_analyses`, so a batch may span every EDM of
the deal and may include group rows. A group carries `submission_id` and no
`edm_id`, so this is the only route that can delete one. Broker rows are not
in the candidate set — posting one is rejected like any unrelated id, and the
grid's Delete button already disables while a broker row is ticked.

Member rows in `irp_analysis_group_member` are retained when either a group or
one of its members is deleted (data-model §2).

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
