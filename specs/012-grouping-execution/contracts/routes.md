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
  `id, irp_id, display_name, kind (own|broker|group), engine, currency,
  app_analysis_id`. `currency` is the run currency code — own rows and groups
  from `submitted_settings.currency.code`, broker rows from the metadata
  snapshot's currency (the FR-005 rule), `None` when unrecorded;
  `app_analysis_id` is `irp_app_analysis_id`, else the snapshot's
  `appAnalysisId`, else `None`. Ineligible rows (running/failed) are not
  listed (FR-003, US-1 acceptance 2).
- `group_name`: prefilled `CRE_<submission name>_Group` (T-09), editable.
- `inspect_url`: the inspect route below, posted by screen 1's **Next**
  button (`hx-trigger="inspect"`, fired by the Alpine `inspect()` method;
  target `#group-inspection`, indicator `#group-inspect-wait`).
- `finish_url`: the finish route below, posted by screen 1's **Finish**
  button (`hx-trigger="finish"`, fired by the Alpine `finish()` method; same
  target and indicator; enabled with Next, FR-025).
- Currency block on screen 3, inside `#group-currency`: the `currency_block`
  macro from `currency_block.html` with `currency_defaults()` prefill and the
  existing `/edms/execute/vintage-options` cascade (FR-004). Every inspection
  re-renders it out of band with the members' currency (below).
- Propagate detailed output: checkbox on screen 3, checked (FR-005). Risk
  Modeler's Create independent groups checkbox is not carried over (FR-006,
  O-08).
- `blocking` message instead of the form when the submission has fewer than
  two eligible members.

Screen 1 (Members): the group name, a client-side name search over the
pick-list (`data-name` on each row), the pick-list with the grid's rows
pre-checked (each checkbox carries `data-display`, the chip label), "N of M
selected", and beside the list the chips panel (`.bo-picked`): "Nothing
selected yet" or one `.bo-picked__chip` button per checked member, derived
from the checkboxes on every `recompute()`, whose click unticks the row and
fires `change` (FR-022). **Next** enables at ≥2 checked members
and a non-empty name; it empties `#group-inspection`, `#group-summary` and
`#group-sims`, moves to screen 2, and fires the inspect request. **Back**
from screen 2 aborts an in-flight inspect (`htmx:abort`) and empties the same
three targets, so no stale swap can land. `#group-currency` is never emptied
— it keeps the last rendered block until the next inspection replaces it.

Screen 2 (Inspection): the wait state while the request runs, then the
inspect response. **Next** enables on `[data-inspection-ready]` with every
`select[name=event_rate_selection]`, `select[name=simulation_set_selection]`,
and `select[name=simulation_periods_selection]` chosen; it records each
select's partition and chosen label for screen 3's "Schemes chosen",
"Simulation sets chosen", and "Simulation periods chosen" lists and moves on.

Screen 3 (Settings): the summary (`#group-summary`), group simulation periods
(`#group-sims`), and currency block (`#group-currency`) rendered by the
inspect response, the choices made on screen 2, Propagate detailed output. **Group**
submits the form (`hx-swap="none"`) and enables when the currency triple is
chosen and `num_of_simulations` holds a positive integer. Submit errors land
in `#group-submit-errors` at the top of the screen.

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
  names, currencies, and app analysis ids), `common_currency` (the one code every member ran in, else `None`),
  `member_currencies` (the distinct known codes) and `currency_unknown` —
  and `screen: InspectionScreen` from
  `grouping_view.build_inspection_screen(view)`: one `PartitionRow` per
  partition (key, the members' distinct engine versions, member display
  names, `mode`, `SchemeOption`s) and one `ProblemText` per blocking problem.
  The context also carries `simulation_period_options`,
  `default_simulation_periods`, and the `currency_block` context
  (`currency_code_val` = `common_currency` or the env default code, the env
  scheme and vintage, the option lists).

Every 200 response also carries `#group-summary`, `#group-sims`, and
`#group-currency` as `hx-swap-oob` divs, so each inspection resets screen 3;
the 422 carries the first two (emptied) and leaves `#group-currency` as it
was.

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
  and `request_grouping` enforce the choice. When the group output is PLT the
  table gains a Simulation set column. A row carrying
  `simulation_set_selection_required` (an ELT partition of a PLT group) is a
  `<select name="simulation_set_selection" data-partition="<peril> / <region> / <model version>">`
  with an empty first option and one option per `simulation_set_options` in
  package order, none preselected, whose value is the JSON
  `{"peril_code","region_code","model_version","simulation_set_id"}`, text
  `<label> (<periods> periods)` and `data-label` the label (`Simulation set
  <id>` without one). The option's reference `event_rate_scheme_id` is not
  rendered; the scheme and simulation-set selects are independent. A PLT/HD
  row shows one `.insp-resolved` per distinct `pet_id` on its members' PLT
  region facts, reading `<pet_name> (<periods> periods)` — `PET <id>` when
  the `PETMetadata` lookup named no row, and no period text when the region
  carried no period count. A PLT group's table also has a Simulation periods
  column: every row carries
  `<select name="simulation_periods_selection" data-partition="<peril> / <region> / <model version>">`
  over `grouping_service.SIMULATION_PERIOD_OPTIONS`, `DEFAULT_SIMULATION_PERIODS`
  (50,000) selected, whose option value is the JSON
  `{"peril_code","region_code","model_version","simulation_periods"}` and
  whose `data-label` is the formatted count — HD rows included, independent
  of the simulation-set select (FR-019). An ELT group has neither column. Then
  the Treaty mismatches
  section: one `.insp-treaty` per `inspection.warnings` entry with
  `code == "inconsistent_treaty_terms"`, each a heading of the Treaty Number,
  "<n> treaties · <k> analyses" (`k` counts distinct analyses, since one
  analysis can carry two treaties sharing a number), and "Differs on <display
  labels>" (`differing_fields` through `treaty_service.humanize_key`), over an
  `.insp-table--treaty` in Risk Modeler's column order: Analysis (the
  Workbench display name, or the Platform id when the member is unknown),
  Analysis ID (the member's `app_analysis_id`, em dash when the Workbench
  holds none — never the Platform id, FR-020), Treaty ID (em dash when the
  package reports none), Treaty Number, then one
  column per `grouping_view.TREATY_COLUMNS` entry — treatyType, effectiveDate,
  expirationDate, attachmentPoint, occurrenceLimit, riskLimit ("Per Risk
  Limit"), currency — one row per `GroupingProblem.treaties` entry. Values are
  the warning's own analysis-level terms through
  `treaty_service.display_value`, so enum codes read spelled out and dates
  date-truncated; a column whose key is in `differing_fields` carries
  `insp-diff` on its header and its cells. The section ends with the hint that
  mismatches do not stop the grouping; with no mismatch, the one-line
  `.insp-notice--ok`. Then the hidden
  `expected_inspection_fingerprint`, one hidden `inspected_analysis_ids` per
  member, and the `[data-inspection-ready]` marker — gated on blocking
  problems only; warnings never disable Next. `#group-summary` holds the
  group name (`x-text`), the output, a Treaties row (`badge--warning`
  "<n> mismatch(es)" plus the treaty numbers, or "No mismatches"), and the
  members with engine and kind. `#group-sims` holds, for a PLT group, the
  "Group simulation periods" label (an `.exec-section-label`, above the
  control) over the `<select name="num_of_simulations">` listing
  `grouping_service.SIMULATION_PERIOD_OPTIONS` (3,125 … 800,000) with
  `DEFAULT_SIMULATION_PERIODS` (50,000) selected and the target-length hint;
  for an ELT group,
  `<input type="hidden" name="num_of_simulations" value="1">` (FR-019).
  `#group-currency` holds the `currency_block` re-rendered with
  `common_currency` (else the env default code) and a `[data-currency-hint]`
  line: "All members ran in <code>.", "A member's currency is not recorded.
  Defaulting to <default>.", or "Members ran in <codes joined by ' and '>.
  Defaulting to <default>." (FR-004).

## `POST /submissions/{submission_id}/analyses/group/finish`

Form fields: `csrf_token`, `member_ids` (repeated), `group_name` (the whole
form is included; the other fields are ignored).

Behavior — the Finish fast path (FR-025, spec O-14): inspect, then submit in
the same request when nothing is left for the analyst to choose.

- The inspect gate and the Platform read run exactly as for the inspect
  route; failures → `partials/group_inspection.html` with `errors` at 422
  (the Retry button re-inspects).
- `grouping_service.finish_blockers(view, currency_defaults=…)` is
  non-empty when the inspection has blocking problems, any partition carries
  `event_rate_selection_required` or `simulation_set_selection_required`,
  `common_currency` is `None`, or the env
  scheme or vintage default is empty (unset or not in the cache). Treaty
  warnings are not a reason. → 200, the inspection partial with the full
  `_inspection_context` plus `finish_stopped=True`, which renders one
  `.insp-notice--warn` above the facts strip: "Finish could not submit this
  group. Review the inspection and continue with Next." The oob screen-3
  blocks fill as for the inspect route, so Next works unchanged.
- Otherwise `grouping_service.request_grouping` with `currency_code =
  common_currency`, the env `scheme` and `vintage`, `propagate_detailed_output
  = True`, no scheme or simulation-set selections, and the fingerprint and
  analysis ids from this request's inspection. An ELT group posts
  `num_of_simulations = "1"` and no simulation-periods selections; a PLT group
  posts `str(DEFAULT_SIMULATION_PERIODS)` and
  `grouping_service.default_simulation_periods_selections(view)` — one
  `simulation_periods_selection` value per partition at 50,000. A gate
  failure → the inspection partial with `errors` at 422.
- Success → 200 with `partials/group_finish_confirmation.html` and the
  headers `HX-Retarget: #group-modal`, `HX-Reswap: innerHTML`, and the same
  `HX-Trigger` as the submit route (`grouping-submitted` + `rwb:toast`). The
  pane replaces the dialog and stays until Close: "Group submitted",
  "Inspection passed.", then Group name (`requested_group_name` — the plan's
  `group_full_name`, so a collision suffix shows), Output, for a PLT group
  Simulation periods "50,000 — group and every partition", Event-rate
  schemes "No conflicts", Treaties (the summary's badge and treaty numbers,
  or "No mismatches"), Currency "<code> — all members", Propagate detailed
  output "On", and the members with engine and kind.

## `POST /submissions/{submission_id}/analyses/group`

Form fields: `csrf_token`, `member_ids` (repeated, ≥2), `group_name`,
`currency_code`, `currency_scheme`, `currency_vintage`,
`propagate_detailed_output` (checkbox), `num_of_simulations`,
`event_rate_selection` (repeated, JSON option values),
`simulation_set_selection` (repeated, JSON option values),
`simulation_periods_selection` (repeated, JSON option values),
`expected_inspection_fingerprint`, `inspected_analysis_ids` (repeated).

Behavior:

- Gate (`grouping_service.request_grouping`): every member exists, is not
  deleted, belongs to this submission, is finished, and has a Platform
  analysis id; at least two members; the picked ids equal the inspected ids
  ("Members changed since inspection. Inspect again."); fingerprint present
  ("Inspect the members before grouping."); `num_of_simulations` `1` or one
  of `SIMULATION_PERIOD_OPTIONS` ("Choose one of the offered simulation
  period counts." — whether the group is ELT or PLT is the package's check
  at submit); each selection parses
  with `peril_code`, `region_code`, `model_version` (strings) and
  `event_rate_scheme_id` (int) and no partition repeats ("Choose an
  event-rate scheme for every conflicting partition."); each simulation-set
  selection parses the same way with `simulation_set_id` (int) ("Choose a
  simulation set for every partition converted from ELT to PLT."); each
  simulation-periods selection parses the same way with `simulation_periods`
  (int) and every value is one of `SIMULATION_PERIOD_OPTIONS` ("Choose one of
  the offered simulation period counts for every partition." — whether the
  group is PLT, and so takes them, is the package's check at submit);
  `group_name`
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
