# Route Contracts: Grouping Execution (spec 012)

All routes CSRF-protected, HTMX-rendered. Patterns follow the analysis-execute
modal (`app/routers/edms.py` execute routes).

## `GET /submissions/{submission_id}/analyses/group`

Renders the compose dialog into `#group-modal`: one `<form>` with three
screens as Alpine `x-show` panes on a `step` state — Members, Inspection,
Settings — and a step indicator in the header. Hidden panes still serialize,
so the final POST carries every screen and Back never loses a screen's inputs.

Query params: `analysis_ids` (repeated, optional) — the grid's ticked rows,
pre-checked in the pick-list.

Context:

- `members`: every eligible member of the submission — own analyses with
  `status_code='ready'`, broker analyses, finished groups — each with
  `id, irp_id, display_name, kind (own|broker|group), engine`. Ineligible
  rows (running/failed) are not listed (FR-003, US-1 acceptance 2).
- `group_name`: prefilled `CRE_<submission name>_Group` (T-09), editable.
- `inspect_url`: the inspect route below, posted by screen 1's **Next**
  button (`hx-trigger="inspect"`, fired by the Alpine `inspect()` method;
  target `#group-inspection`, indicator `#group-inspect-wait`).
- Currency block on screen 3: the `currency_block` macro from
  `execute_analysis_modal.html` with `currency_defaults()` prefill and the
  existing `/edms/execute/vintage-options` cascade (FR-004).
- Propagate detailed output: checkbox on screen 3, checked (FR-005). Risk
  Modeler's Create independent groups checkbox is not carried over (FR-006,
  O-08).
- `blocking` message instead of the form when the submission has fewer than
  two eligible members.

Screen 1 (Members): the group name, a client-side name search over the
pick-list (`data-name` on each row), the pick-list with the grid's rows
pre-checked, and "N of M selected". **Next** enables at ≥2 checked members
and a non-empty name; it empties `#group-inspection`, `#group-summary` and
`#group-sims`, moves to screen 2, and fires the inspect request. **Back**
from screen 2 aborts an in-flight inspect (`htmx:abort`) and empties the same
three targets, so no stale swap can land.

Screen 2 (Inspection): the wait state while the request runs, then the
inspect response. **Next** enables on `[data-inspection-ready]` with every
`select[name=event_rate_selection]` chosen; it records the chosen scheme
labels for screen 3's "Schemes chosen" list and moves on.

Screen 3 (Settings): the summary (`#group-summary`) and simulation count
(`#group-sims`) rendered by the inspect response, the schemes chosen, the
currency block, Propagate detailed output. **Group** submits the form
(`hx-swap="none"`) and enables when the currency triple is chosen and
`num_of_simulations` is a positive integer. Submit errors land in
`#group-submit-errors` at the top of the screen.

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
  PLT group, else 1) — and `screen: InspectionScreen` from
  `grouping_view.build_inspection_screen(view)`: one `PartitionRow` per
  partition (key, the members' distinct engine versions, member display
  names, `mode`, `SchemeOption`s) and one `ProblemText` per blocking problem.

Every response, the 422 included, also carries `#group-summary` and
`#group-sims` as `hx-swap-oob` divs, so each inspection resets screen 3.

Fragment states, all rendered into `#group-inspection`:

- **Error** (422): one `.insp-notice--block` with the error list and a
  **Retry** button that fires the inspect again. The oob divs are empty.
- **Blocked** (`inspection.blocking_problems` non-empty): the "These members
  cannot be grouped" notice — one paragraph per problem, the members listed
  by display name — above the facts strip and the partition table. No hidden
  fields and empty oob divs, so Next stays disabled.
- **Ready**: the facts strip ("Group output ELT|PLT", member count, scheme
  mismatch count, treaty mismatch count); the partition table with Peril and Region codes, Model
  version as `<engine versions> · <model version>`, member display names,
  and the Event-rate scheme cell by row `mode` —
  `choose` (`event_rate_selection_required`) is a
  `<select name="event_rate_selection" data-partition="<peril> / <region> / <model version>">`
  with an empty first option and one option per `event_rate_scheme_options`
  whose value is the JSON
  `{"peril_code","region_code","model_version","event_rate_scheme_id"}`, text
  `<label> (<n> members)` and `data-label` the label (`opt.label`, or
  `Scheme <id>` without one); `resolved` (one option) shows that label;
  `none` shows an em dash. The select carries no `required` attribute — a
  hidden required select would block the browser's submit; the Alpine gate
  and `request_grouping` enforce the choice. Then the Treaty mismatches
  section: one `.insp-notice--warn` per `inspection.warnings` entry with
  `code == "inconsistent_treaty_terms"` — title "Treaty Number <n> has
  different loss-affecting terms in <k> members", "Differing terms: <display
  labels>" (`differing_fields` through `treaty_service.humanize_key`), the
  member display names as a list, and "Treaty IDs: <ids>" when `treaty_ids`
  is non-empty — followed by the hint that mismatches do not stop the
  grouping; with no mismatch, the one-line `.insp-notice--ok`. Members and
  treaty ids are listed unpaired. Then the hidden
  `expected_inspection_fingerprint`, one hidden `inspected_analysis_ids` per
  member, and the `[data-inspection-ready]` marker — gated on blocking
  problems only; warnings never disable Next. `#group-summary` holds the
  group name (`x-text`), the output, a Treaties row (`badge--warning`
  "<n> mismatch(es)" plus the treaty numbers, or "No mismatches"), and the
  members with engine and kind. `#group-sims` holds, for a PLT group,
  `<input type="number" name="num_of_simulations" min="1">` prefilled with
  the suggestion and the "Largest member: <n>" hint; for an ELT group,
  `<input type="hidden" name="num_of_simulations" value="1">` (FR-019).

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
  failures collected → `partials/group_submit_errors.html` (one
  `.modal-error` block) at 422 with `HX-Retarget: #group-submit-errors` and
  `HX-Reswap: innerHTML`, nothing persisted (SC-005). The dialog keeps its
  state: the analyst fixes the input on screen 3 or goes Back.
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
