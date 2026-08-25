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
  Deselection touches nothing in `template_suite_item`. Each chosen suite shows its own
  **currency block** — analysis currency, currency scheme, scheme vintage (FR-019) —
  pre-filled from the `DEFAULT_ANALYSIS_CURRENCY_*` settings (FR-020, T-19); the vintage
  select lists only the chosen scheme's vintages (`irp_currency_scheme_vintage` rows for
  that `currency_scheme_code`); a default that is unset or absent from the cache leaves
  that picker unselected.
- `kind=template`: one checkbox row per live template, plus a single currency block for
  the execution (same pre-fill rules).
- Treaty picker: checkbox per `irp_treaty` row of this EDM (names from the local cache;
  zero selected is valid — gross run, FR-004).
- Selected portfolios shown read-only and carried as hidden `portfolio_ids` inputs.
- Submit button disabled (Alpine) until ≥1 suite/template chosen, ≥1 template remains
  selected (FR-002, edge case "every template deselected"), **and** every visible
  currency block has all three values chosen (FR-020).

Errors: EDM not `ready`, no portfolios given, or no live suites/templates → the fragment
renders the blocking message in place of the form (prerequisite gate, FR-001).

### `POST .../edms/{edm_id}/execute`

Form fields: `kind`, `portfolio_ids` (repeated). For `kind=suite`, per chosen suite:
the suite's checked templates as `templates_{suite_id}` (repeated) and its currency
block as `currency_code_{suite_id}` / `currency_scheme_{suite_id}` /
`currency_vintage_{suite_id}` — currency is per suite (FR-019), so template selections
post grouped by suite, not as one union. For `kind=template`: `template_ids` (repeated)
plus a single `currency_code` / `currency_scheme` / `currency_vintage`. Both kinds:
`treaty_names` (repeated).

Server behavior (`analysis_execution_service.request_execution`):
1. Gate: EDM exists and `ready`; every `portfolio_id` belongs to the EDM; every posted
   template is live and (for `kind=suite`) belongs to its suite; `kind=suite` ⇒ ≥1 suite
   with ≥1 template still selected; every currency block complete, with `code` in
   `irp_currency`, `scheme` in `irp_currency_scheme`, and an
   `irp_currency_scheme_vintage` row for `(scheme, vintage)` (FR-019/FR-020 — the
   membership of currency *in* scheme stays unvalidated, edge case list); every treaty
   name exists in `irp_treaty` for this EDM. Any failure → 422 re-render of the modal
   with the message (`hx-on::before-swap` keeps 409/422 swaps).
2. Compose the plan **once** (FR-012): mint `execution_id`, one item per suite×template
   selection (`item_no` ordinal) snapshotting the template's stored values plus its
   suite's confirmed currency block (`asOfDate` derived from the chosen vintage's
   `effective_date` — T-03) and `tag_names` extended with the submission's name when a
   submission context exists (FR-021, T-20), portfolio ids+names, treaty names, actor,
   EDM name, optional submission id.
3. `enqueue_rwb_job(requestor_type='analyst_request', requestor_id=execution_id,
   rwb_job_type='execute_analysis_batch', input_data=plan)` + `dispatch(...)`.
4. Respond 204 with `HX-Trigger: execution-submitted`; the modal closes and the
   user-executed section re-polls (P-11 — no waiting in the modal).

No IRP call happens on this request path.

## User-executed analyses section

Rendered inside `partials/edm_detail_body.html` on both page variants; the section is
its own polling fragment (`GET /edms/{edm_id}/analyses` and
`GET /submissions/{submission_id}/edms/{edm_id}/analyses`), self-polling every 3s while
`live` — any `irp_analysis` of this EDM whose joined latest `irp_job` is non-terminal,
or whose `status_code` is `pending`/`running`. Both GETs accept `?status=` clamped to
`failed` / `in_progress` / `ready` (P-18); the filter is baked into the poll URL so a
swap never resets it. Rows render in three fixed groups — Failed, In progress, Ready —
date-descending within each.

Row contract (`partials/executed_analysis_row.html`, modeled on
`broker_analysis_row.html`): delete checkbox on `is_deletable` rows, full name
(`full_name`) with an "RM ↗" link once `irp_id` is backfilled, portfolio name, template
name, status chip (derived: latest `irp_job.status`, with `SUBMISSION FAILED` shown as
"Failed to submit · attempt n/max") with the `failure_reason` when failed, and the
localized submit time. Expanded: the settings grid once `settings_metadata` is
backfilled; the loss-numbers fragment (below) once results exist. No RDM grouping
(FR-013). Visible to every analyst (Article 6).

### `POST /edms/{edm_id}/analyses/delete` (+ `/submissions/{submission_id}/edms/{edm_id}/analyses/delete`)

Multi-select delete of terminal analyses (P-19, FR-023/FR-024). Form fields:
`csrf_token`, repeated `analysis_ids`. The whole batch is validated up front (every id
must resolve on this EDM and be deletable) — any violation, or an empty selection,
returns 422 whose banner text surfaces as a toast. Per row: Risk Modeler delete first
(synchronous, `irp_gateway.delete_analysis` — permitted on the request path like
submits, Article 11), local soft delete (`deleted_at`) on success; a row whose RM
delete fails is kept and counted in the warning toast. Success → 204 with
`HX-Trigger: {"analyses-changed": true, "rwb:toast": …}` — a distinct event from
`execution-submitted`, so a delete never clears portfolio ticks or starts the
post-execute re-fire loop.

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

## Job monitor (T-12)

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
