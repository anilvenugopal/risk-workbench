# Contract — routes & fragments (spec 011)

All GET except §5. Every fragment reads stored data only — no RM call serves
a render (spec non-negotiable 1); §5 enqueues and dispatches, nothing more.

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
  analysis. No cell spans columns, so every analysis keeps its own column;
  the Return period header and a column's results-state message do span rows
  (`rowspan`), and copy-with-headers pads spanned cells so the TSV stays
  aligned. Per-analysis header shows name, currency, and results state
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

## 5. Retry a failed results retrieval (FR-007, T-11)

`POST /results/analyses/{analysis_id}/retry`

- Form: `csrf_token` only (the row's button uses `hx-include="#analyses-csrf"`,
  the hidden input the merged section already renders for Delete). The
  analysis id is the path; no submission or EDM scope — one route serves the
  submission Results section, the EDM section and the lazy-loaded broker rows.
- Rendered by the Retry control in `analysis_row_macros.html` (`status_cell`,
  own and broker rows) and the expanded row's failed paragraph, only while the
  row's results state is `failed`. `hx-swap="none"`,
  `onclick="event.stopPropagation()"` like the Delete button and rm-link.
- Behaviour (`analysis_service.retry_results_retrieval`): the analysis must
  exist, be undeleted and have `loss_results IS NULL`; then
  `rwb_job_service.ensure_pending_rwb_job(requestor_type="irp_analysis",
  requestor_id=<analysis id>, rwb_job_type="retrieve_analysis_results",
  input_data={"analysis_id": <analysis id>}, actor_id=<user>)` followed by
  `dispatch.dispatch`. The key is the one `finalize_analysis` and
  `backfill_rdm_analyses` enqueue under, so the failed row itself is reset —
  same `id`, `attempt_count` + 1, `error_detail`/`output_data`/`completed_at`
  cleared, new correlation id. The worker runs unchanged and still skips when
  results are already stored.
- Responses:
  - `204` with `HX-Trigger: {"analyses-changed": true, "rwb:toast":
    {"message": "Results retrieval queued.", "type": "success"}}` — the
    section refetches and the AAL cell reads `retrieving…`; `is_live`
    resumes the 3s poll until the numbers land.
  - `204` with the same `analyses-changed` and `"rwb:toast": {"message":
    "Results retrieval is already running.", "type": "warning"}` when the
    primitive returns `None` (head already `pending`/`running`). The refetch
    shows the true state.
  - `404` when the analysis id is unknown or deleted; `422` banner (surfaced as
    a toast by `htmx:responseError`, the Delete pattern) when results are
    already stored — a stale page clicked after the numbers landed.
  - `204` + `HX-Refresh: true` on an invalid CSRF token (every analyses POST).
- Not offered on `/results/analyses` or `/results/comparison`: those pages
  have no `#analyses-csrf` input and no `analyses-changed` listener.
