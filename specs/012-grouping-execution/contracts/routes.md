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
  `id, irp_id, display_name, kind (own|broker|group), engine`. Ineligible
  rows (running/failed) are not listed (FR-003, US-1 acceptance 2).
- `group_name`: prefilled `CRE_<submission name>_Group` (T-09), editable.
- `inspect_url`: the inspect route below, posted by the **Inspect members**
  button (`hx-include="closest form"`, target `#group-inspection`).
- Currency block: the `currency_block` macro from
  `execute_analysis_modal.html` with `currency_defaults()` prefill and the
  existing `/edms/execute/vintage-options` cascade (FR-004).
- `propagate_detailed_output`: checkbox, checked (FR-005). Risk Modeler's
  Create independent groups checkbox is not carried over (FR-006, O-08).
- The treaty notice under the member list: "Treaty terms that share a Treaty
  Number are not compared. Inconsistent terms can produce unexpected grouped
  results." (FR-020).
- `blocking` message instead of the form when the submission has fewer than
  two eligible members.

The Group button is disabled until the form holds an inspection result
(`[data-inspection-ready]`), every `select[name=event_rate_selection]` has a
value, `num_of_simulations` is a positive integer, the name is non-empty, and
the currency triple is chosen. Changing a member checkbox empties
`#group-inspection`.

## `POST /submissions/{submission_id}/analyses/group/inspect`

Form fields: `csrf_token`, `member_ids` (repeated).

Behavior — Platform reads only, nothing persisted:

- Gate (`grouping_service.inspect_grouping`): at least two distinct members,
  each eligible and carrying a Platform analysis id. Failures →
  `partials/group_inspection.html` with `errors` at 422.
- `irp_gateway.inspect_grouping(analysis_ids=…)`. A Platform read failure →
  the same partial with `errors = ["Inspection failed: <cause>"]` at 422.
- Success → `partials/group_inspection.html` at 200 with
  `view: GroupingInspectionView` — `inspection` (the package
  `GroupingInspection`), `members` (Platform id → `GroupMember`, for display
  names), `suggested_num_of_simulations` (largest PLT member `periods` for a
  PLT group, else 1).

Fragment states:

- **Blocked** (`inspection.blocking_problems` non-empty): one warning box per
  problem — message, "Members: <display names>", "Partition: peril · region ·
  model version" and "PET IDs: …" when present. No hidden fields, so Group
  stays disabled.
- **Ready**: "Group output: ELT" or "Group output: PLT (members are simulated
  to a PLT)"; a member table (display name, framework, peril · region · model
  version, scheme id or PET id + periods per region); one required
  `<select name="event_rate_selection">` per partition with
  `event_rate_selection_required`, an empty first option and one option per
  `event_rate_scheme_options` whose value is the JSON
  `{"peril_code","region_code","model_version","event_rate_scheme_id"}`;
  `<input type="number" name="num_of_simulations" min="1">` prefilled with
  the suggestion and a PLT/ELT hint (FR-019); hidden
  `expected_inspection_fingerprint`, one hidden `inspected_analysis_ids` per
  member, and the `[data-inspection-ready]` marker.

The `#group-inspection` div allows the 422 swap
(`hx-on::before-swap`), so gate errors render in place.

## `POST /submissions/{submission_id}/analyses/group`

Form fields: `csrf_token`, `member_ids` (repeated, ≥2), `group_name`,
`currency_code`, `currency_scheme`, `currency_vintage`,
`propagate_detailed_output` (checkbox), `num_of_simulations`,
`event_rate_selection` (repeated, JSON option values),
`expected_inspection_fingerprint`, `inspected_analysis_ids` (repeated).

Behavior:

- Gate (`grouping_service.request_grouping`): every member exists, is not
  deleted, belongs to this submission, is finished, and has a Platform
  analysis id; at least two members; the picked ids equal the inspected ids
  ("Members changed since inspection. Inspect again."); fingerprint present
  ("Inspect the members before grouping."); `num_of_simulations` an integer
  > 0 ("Enter a simulation count greater than zero."); each selection parses
  with `peril_code`, `region_code`, `model_version` (strings) and
  `event_rate_scheme_id` (int) and no partition repeats ("Choose an
  event-rate scheme for every conflicting partition."); `group_name`
  non-empty and free among live group names of the submission (the `_n`
  suffix is applied automatically on collision, not an error); currency
  triple resolves in the cache (same `_validate_currency` rules). All
  failures collected → 422 re-render of the whole dialog with the error list
  (`HX-Retarget: #group-modal`), nothing persisted (SC-005). The inspection
  fragment is dropped by the re-render; the analyst inspects again.
- Success: persist the approved plan as one `submit_grouping` `rwb_job`,
  dispatch, respond `204` with
  `HX-Trigger: {"grouping-submitted": {...}, "rwb:toast": …}`. The toast is
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
