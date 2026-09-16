# Contract — Routes (TY export)

Deltas over [014 contracts/routes.md](../../014-results-export/contracts/routes.md).
Everything not named here is unchanged.

## 1. Export form page and selection fragment (014 §2, §3)

- The perspective `<select>` offers `TY` when it is in
  `EXPORT_PERSPECTIVE_CODES` and every selected analysis has a non-empty
  `loss_results.treaties` (FR-001). The analysis row's Perspectives column
  lists `TY` beside the codes it has results for.
- When `TY` is chosen the fragment renders, under the select:
  "TY writes one loss set per treaty, per analysis." (FR-002). No cart row
  shows an AAL line at TY (FR-016).
- Submit validation message for an analysis without treaties at TY:
  "{name} was not run with treaties."

## 2. Exports section (014 §5)

- A row is one manifest row: an analysis at a portfolio-level perspective, or
  one treaty of an analysis at TY. An analysis at TY therefore occupies several
  rows of its export, ordered by treaty number then treaty name.
- A **Treaty** column follows the analysis name and holds `{treaty_number}`
  (`· {treaty_name}` appended when it differs), `—` for a portfolio row or a TY
  row whose loss table has not been read; its `title` holds the treaty IDs.
- The AAL cell of a TY row is the row's `aal`; other rows keep the
  `loss_results` value.
- The row's DOM id is `export-analysis-{export_id}-{manifest_id}`.
- Retry and Close post to §3.

## 3. Retry and Close

```
POST /submissions/{submission_id}/exports/{export_id}/manifests/{manifest_id}/retry
POST /submissions/{submission_id}/exports/{export_id}/manifests/{manifest_id}/close
```

Replace the 014 §7 and §8 routes keyed by `irp_analysis_id`, which are
removed. Preconditions, refusal reasons (409), the 404 for a row outside this
submission's export, the render-back rules, and the `?status=` filter are
unchanged; the row is found by `manifest_id`.

Retry's decision (014 §7 table) is evaluated on the clicked row and re-arms
the analysis's one stage or load job. The re-armed job acts on every eligible
row of the analysis (jobs.md §4, §5): a loaded or closed sibling is never
re-run.
