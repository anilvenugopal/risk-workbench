# Contract — Routes (TY export)

Deltas over [014 contracts/routes.md](../../014-results-export/contracts/routes.md).
Everything not named here is unchanged.

## 1. Export form page and selection fragment (014 §2, §3)

- The perspective `<select>` offers `TY` when it is in
  `EXPORT_PERSPECTIVE_CODES` and every selected analysis has a non-empty
  `loss_results.treaties` (FR-001). The analysis row's Perspectives column
  lists `TY` beside the codes it has results for.
- When `TY` is chosen the fragment renders, under the select:
  "TY writes one loss set per ticked treaty, per analysis." (FR-002). No cart
  row shows an AAL line at TY (FR-016).
- At `TY` each cart row lists the analysis's `loss_results.treaties`, deduped
  by (number, name) and in recorded order, in place of the per-analysis Data
  name field:

  | Field | Name | Value |
  |---|---|---|
  | tick | `treaty[{analysis_id}]` (multi-value) | the treaty number; present only when ticked |
  | data name | `treaty_data_name[{analysis_id}][{treaty_number}]` | what the analyst typed; `placeholder` is `{analysis_name} {treaty_number}`, `maxlength` 150 |

  Each row shows the number, the name, the type label
  (`treaty_service.display_value(code, key="treatyType")`), and
  `risk {risk_limit} · att {attachment_point} · occ {occurrence_limit}`
  through `analysis_service.fmt_loss` (P-12). The head reads
  "Treaties · {n} of {m} ticked" with `all` and `none` links that tick or clear
  that analysis's boxes. The data name field is shown only for a ticked treaty,
  by CSS, and Export is disabled while any cart row has no tick.
- Both `hx-include` lists (the analyses table and the perspective select) add
  `[name^='treaty['], [name^='treaty_data_name[']`, so ticks and typed names
  survive every fragment re-render and come back re-checked and re-filled.
- Submit validation messages at TY, in this order:
  "{name} was not run with treaties." ·
  "Tick at least one treaty for {name}." ·
  "Treaty {number} is not one of {name}'s treaties." ·
  "Data name for {name} treaty {number} is longer than 150 characters."

## 2. Exports section (014 §5)

- A row is one manifest row: an analysis at a portfolio-level perspective, or
  one ticked treaty of an analysis at TY. An analysis at TY therefore occupies
  several rows of its export, ordered by treaty number then treaty name, from
  the moment the export is requested.
- A **Treaty** column follows the analysis name and holds `{treaty_number}`
  (`· {treaty_name}` appended when it differs), `—` for a portfolio row; its
  `title` holds the treaty IDs, which the stage worker fills in.
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
