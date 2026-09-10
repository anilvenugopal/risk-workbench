# Contract — Routes (Loss Results Export)

All routes live in `app/routers/submissions.py`, query `rwb_workbench` and the
loss repository through `db.execute`/`db.execute_command` with bound
parameters (Article 7), and make no Risk Modeler call (Article 11). POST
routes carry the CSRF token (Article 13). Any authenticated analyst may use
them (spec P-11). Manifest reads append `db.read_uncommitted_hint("LOSS")`
(T-25).

## 1. Export button

`partials/analyses_merged_section.html` gains **Export** beside Compare and
View in the `<summary>` bar of the submission-scoped section only
(`analyses_base` starts with `/submissions/`): a plain link to §2. No row
selection is required; the form does its own.

## 2. Export form page

```
GET /submissions/{submission_id}/exports/new
```

Nav node `submissions.export_new` (hidden; crumb "Export" under the
submission). Renders `pages/submission_export_new.html` with:

- The submission's exportable analyses (data-model.md §7
  `ExportableAnalysis`), own and group rows first, then broker rows grouped by
  RDM name, each a checkbox. A row that cannot be exported (non-integer
  `irp_app_analysis_id`, no results) is listed disabled
  with the reason.
- The §3 fragment rendered once with no selection: perspective select
  disabled, no data-name fields.
- Client `<select>` from `dbo.Client` where `ActiveFlag = 'Y'`, ordered by
  name, required. Inactive clients are not offered.
- Treaty inception (date, default `submission.inception_date`), CRM ID (text,
  default the submission's first `submission_crm_id.crm_id`), data vintage
  (date, blank).
- Export button, disabled until at least one analysis is ticked and a
  perspective and client are chosen.

A submission that does not resolve renders the gone-notice partial.

## 3. Selection fragment

```
GET /submissions/{submission_id}/exports/new/fields
    ?analysis_ids=<uuid>[&analysis_ids=<uuid>…][&perspective=<code>]
```

Triggered by `hx-get` on the analysis list (`hx-trigger="change"`,
`hx-include` the checkboxes and the perspective select, `hx-target`
`#export-fields`). Renders `partials/export_form_fields.html`:

- Perspective `<select>`: codes in `EXPORT_PERSPECTIVE_CODES`, in that order,
  that every selected analysis has in `loss_results.perspectives` (FR-003).
  The current choice is kept when it survives the intersection; otherwise no
  option is selected. Empty intersection → the select is disabled with the
  message "The selected analyses share no exportable perspective".
- One optional text input `data_name[<analysis_id>]` (max 150) per selected
  analysis, labelled with the analysis name (O-07).
- When a perspective is chosen: the exported marks. Each selected analysis
  with a manifest row for that perspective, requested from this or any other
  submission, is rendered as "Exported {requested_at} by
  {requested_by_email} · {status}" linking to that export's §6 page under
  its own submission (`/submissions/{requested_from_submission_id}/exports/{export_id}`,
  spec P-16), and its checkbox is unticked and disabled by the fragment's
  out-of-band swap (`hx-swap-oob` on the row).

## 4. Submit

```
POST /submissions/{submission_id}/exports
Form fields: analysis_ids[] (uuid, ≥1), perspective (code), client_id (int),
             treaty_incept (date), crm_id (text ≤30, optional),
             data_vintage (date, optional), data_name[<uuid>] (text ≤150, optional)
```

Validation, in order; the first failure re-renders §2 with the message and
the analyst's values, HTTP 422:

1. Every `analysis_ids` entry resolves to an exportable analysis of this
   submission (T-24); the reason names the analysis otherwise.
2. `perspective` is in `EXPORT_PERSPECTIVE_CODES` and in every selected
   analysis's perspectives.
3. `client_id` exists in `dbo.Client`.
4. `treaty_incept` parses; `data_vintage` parses when present.
5. **Duplicate check**: no manifest row exists for any (`irp_app_analysis_id`,
   `perspective`). Failure names each blocked analysis and its existing
   export's `requested_at` and `requested_by_email` (FR-004).

On success:

1. `export_id = uuid4()`.
2. One `LOSS` transaction inserts every manifest row (data-model.md §4.1
   values; `requested_from_submission_id = submission_id`;
   `stage_status = pending`, `load_status = pending`). An
   `IntegrityError` on the unique index rolls the transaction back; the
   route re-runs the duplicate check to name the analysis and answers as in
   validation step 5 (concurrent submit, FR-004).
3. `rwb_job_service.enqueue_rwb_job` for `submit_results_export`
   ([jobs.md](jobs.md) §1), then `dispatch.dispatch`. If the enqueue fails
   after the manifest commit, the route answers 500 with the error; the
   manifest rows stay `pending` and the detail page shows them with no job.
4. `303 See Other` to §6 (`HX-Redirect` when the request is HTMX).

An edited `treaty_incept` or `crm_id` is recorded on the manifest only; the
route never updates `submission.inception_date` or `submission_crm_id`
(spec P-15).

## 5. Exports section (submission page)

```
GET /submissions/{submission_id}/exports
```

Renders `partials/exports_section.html`, loaded into the submission detail
page below the analyses section (`hx-get` on load, and `hx-trigger="every
10s"` while any row has an analysis not yet loaded or failed). One row per
export whose `requested_from_submission_id` is this submission (spec P-16),
newest first (data-model.md §7 `ExportSummary`): perspective,
requester, request time, client, analysis count, loaded / failed counts. Each
row links to §6. Empty state: "No exports yet".

## 6. Export detail page

```
GET /submissions/{submission_id}/exports/{export_id}
```

Nav node `submissions.export_detail` (hidden; crumb "Export {perspective} ·
{requested_at}" under the submission). Renders
`pages/submission_export_detail.html`:

- Header: perspective, client name, treaty inception, CRM ID, data vintage,
  requester, request time, `export_id`.
- One row per analysis (data-model.md §7 `ExportAnalysisDetail`): analysis
  name, origin, derived status, last change (`updated_at`), archive path
  (`EXPORT_ARCHIVE_DIR` joined with `zip_file`, when set), `data_id`, rows
  staged, stochastic rows, historical rows, exposure raised, standard
  deviation zeroed, error message (when failed), **Retry** (when failed).
- The analysis table is a fragment (`GET …/exports/{export_id}/analyses`)
  that polls every 5 seconds while any row is not loaded or failed.

An `export_id` with no manifest rows whose `requested_from_submission_id` is
this submission → 404 page.

## 7. Retry

```
POST /submissions/{submission_id}/exports/{export_id}/analyses/{irp_analysis_id}/retry
```

Preconditions: the manifest row exists for (`export_id`, `irp_analysis_id`)
with `requested_from_submission_id` equal to the path's submission (else
404) and its derived status is failed; otherwise 409 with the reason (a `loaded`
row: "already loaded as data ID {data_id}").

Decision (T-28), evaluated top-down, exactly one branch runs:

| Manifest and job state | Action |
|---|---|
| `stage_status = staged` | `ensure_pending_rwb_job` for `load_results_export` ([jobs.md](jobs.md) §1) |
| `zip_file` set and the file exists under `EXPORT_ARCHIVE_DIR`; **or** the `export` `irp_job` is `FINISHED` with `completed_at` within the last 7 days | `ensure_pending_rwb_job` for `stage_results_export` |
| Otherwise | `UPDATE` the manifest row: `irp_export_job_id = NULL`, `stage_status = 'pending'`, `error_message = NULL`; then `ensure_pending_rwb_job` for `submit_results_export` (the export's existing job) |

Then `dispatch.dispatch` for the re-armed job and re-render the analysis row
(HTMX) or redirect to §6.
