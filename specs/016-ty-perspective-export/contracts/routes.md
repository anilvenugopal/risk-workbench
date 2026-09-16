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

- The count column is headed **Data sets** and holds the export's manifest
  rows (one per analysis, or one per treaty per analysis at TY).
- An expanded row's analysis line shows the treaty after the analysis name:
  `{analysis} · {treaty_number}` (`· {treaty_name}` appended when it differs).
- Close posts to §4.

## 3. Export detail page and analysis table (014 §6)

- Header badge: "{n} data set(s)".
- Column order: analysis name, **Treaty** (the label above, `—` for a
  portfolio row or a TY row whose table has not been read; `title` holds the
  treaty IDs), origin, status, and the 014 columns unchanged.
- The AAL cell of a TY row is the row's `aal`; other rows keep the
  `loss_results` value.
- Retry and Close post to §4.

## 4. Retry and Close

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
