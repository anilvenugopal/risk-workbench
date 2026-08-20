# Route Contracts: Analysis Execution (spec 010)

All state-changing routes take `csrf_token` and validate it. Every route below exists in
two variants where the EDM detail page does: standalone (`/edms/{edm_id}/...`) and
submission-contextual (`/submissions/{submission_id}/edms/{edm_id}/...`); the contextual
variant additionally stamps `irp_job.requested_from_submission_id`. Fragments render
shell-less via the router's `_partial` helper.

## Execution modal

### `GET .../edms/{edm_id}/execute`

Returns the modal fragment (injected into an empty host div, `submission_entity_add_modal`
pattern).

Query params:
- `kind` — `suite` | `template` (which list the modal shows; never both — FR-002)
- `portfolio_ids` — repeated; the selection made on the portfolio table (FR-001)

Fragment contents:
- Search input filtering the suite/template list client-side or via a re-GET with `q`
  (300ms debounce, `submission_entity_candidates` pattern).
- `kind=suite`: one checkbox row per live suite; a chosen suite expands (`<details>`)
  into its template list, every template checked by default, deselectable (FR-003).
  Deselection touches nothing in `template_suite_item`.
- `kind=template`: one checkbox row per live template.
- Treaty picker: checkbox per `irp_treaty` row of this EDM (names from the local cache;
  zero selected is valid — gross run, FR-004).
- Selected portfolios shown read-only and carried as hidden `portfolio_ids` inputs.
- Submit button disabled (Alpine) until ≥1 suite/template chosen **and** ≥1 template
  remains selected (FR-002, edge case "every template deselected").

Errors: EDM not `ready`, no portfolios given, or no live suites/templates → the fragment
renders the blocking message in place of the form (prerequisite gate, FR-001).

### `POST .../edms/{edm_id}/execute`

Form fields: `kind`, `portfolio_ids` (repeated), `template_ids` (repeated — the final
deduplicated template set as ticked in the modal; for `kind=suite` the browser posts the
union of checked templates across expanded suites), `suite_ids` (repeated, `kind=suite`
only — validated non-empty, not persisted), `treaty_names` (repeated).

Server behavior (`analysis_execution_service.request_execution`):
1. Gate: dedupe `portfolio_ids` and `template_ids` server-side (FR-005 must not depend
   on the browser posting a clean set); EDM exists and `ready`; every `portfolio_id`
   belongs to the EDM; every `template_id` is live; `kind=suite` ⇒ ≥1 `suite_id`; ≥1
   template; every treaty name exists in `irp_treaty` for this EDM. Any failure → 422
   re-render of the modal with the message (`hx-on::before-swap` keeps 409/422 swaps).
2. Compose the plan **once** (FR-012): mint `execution_id`, snapshot each template's
   stored values (including the currency block with `asOfDate` derived from
   `irp_currency_scheme_vintage.effective_date` — T-03), portfolio ids+names, treaty
   names, actor, EDM name, optional submission id.
3. `enqueue_rwb_job(requestor_type='analyst_request', requestor_id=execution_id,
   rwb_job_type='execute_analysis_batch', input_data=plan)` + `dispatch(...)`.
4. Respond 204 with `HX-Trigger: execution-submitted`; the modal closes and the
   user-executed section re-polls (P-11 — no waiting in the modal).

No IRP call happens on this request path.

## User-executed analyses section

Rendered inside `partials/edm_detail_body.html` on both page variants — no new route;
the existing 3s body self-poll delivers the live updates (T-11). The server-side `live`
flag adds: any `irp_analysis` of this EDM whose joined latest `irp_job` is non-terminal,
or whose `status_code` is `pending`/`running`.

Row contract (new `partials/executed_analysis_row.html`, modeled on
`broker_analysis_row.html`): full name (`full_name`), portfolio name, status chip
(derived: latest `irp_job.status`, with `SUBMISSION FAILED` shown as "Failed to submit ·
attempt n/max"), and when failed the `failure_reason`. Expanded: the settings grid once
`settings_metadata` is backfilled; the loss-numbers fragment (below) once results exist.
No RDM grouping (FR-013). Visible to every analyst (Article 6).

## Loss numbers fragment (loss phase)

### `GET /analyses/{analysis_id}/losses?perspective=GR|GU|RL`

Lazy-loaded into the expanded executed-analysis row (`hx-trigger="toggle[...] once"`
pattern), with perspective tabs re-GETting the fragment. Renders from
`analysis_result_meta` plus the EP Parquet read on demand (T-13): ELT summary (AAL,
max event loss, record count), standard deviation, return-period losses, OEP and AEP
tables; a PLT block only when `has_plt` (FR-017). Perspectives with no meta row render
as absent tabs, not errors (T-15). Numbers only — no chart.

## Treaty pass-through (FR-018, P-08)

No new server route. The treaty section gains "Add / edit in Risk Modeler ↗"
(`_rm_datasource_url(edm.name, "treaties")`, `target="_blank"` — the existing link,
now offered for create as well). An Alpine sliver marks the page when the link is
clicked and, on the next `window` focus, POSTs the existing `.../sync` route once; the
3s poll then shows the refreshed treaties. No `irp_job`, no job-monitor entry.

## Job monitor (T-12, Proposed)

### `GET /workflows/irp-jobs` (+ `/workflows/irp-jobs/table` fragment)

Replaces the stub body with a read-only table over `irp_job` (newest first, capped):
type, entity/analysis name, status chip, submitted-by (`inserted_by` → user), submitted
at, attempts. 3s self-poll on the fragment. No actions.

## Portfolio table changes (existing routes)

`partials/portfolio_row.html` gains a checkbox (`name="portfolio_ids"`,
`syncPicks()` pattern); the Portfolios section header gains **Execute Suite** /
**Execute Template** buttons, disabled until ≥1 checked, offered only when the EDM is
`ready` and ≥1 portfolio exists (FR-001). Buttons `hx-get` the modal fragment with the
checked ids.
