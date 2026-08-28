# Research — Analysis Results Sync & Viewing (spec 011)

## Clarifications

### Session 2026-08-26

- Q: O-09 — keep ~10 as the soft N-up guideline on the dedicated results page, and how does the view degrade past it? → A: Keep ~10 as the soft guideline; past it the table scrolls horizontally (no pagination, no selection block).
- Q: O-10 — after the dedicated view opens in a new browser tab, do the selections in the originating tab reset? → A: Yes, the selection resets once the dedicated page opens. This resolves design note 20's O20-10(b) and the `19` O19-7 sub-question it answers: session 20 recorded the reset as a defect, and the answer here makes it the intended behaviour, built in `app.js`.

## R3 — Retrieval model decided: REST endpoints, bounded extract stored per analysis (closes O-01 for viewing)

Decided 2026-08-25, after design session 19 removed ELTs from viewing scope (D5) and the live response captures below settled the volume question. This revises session 19's D6 ("don't store loss results at all") into: **detail data is a small stored extract; row-level data is not stored for viewing.** The revision is PremiumIQ's to make — Ben stated on the call that storage was not a CIC question — and is reported back in the 8/26 session.

### Evidence — live response captures (`IRP/example_requests_responses/`, inspected 2026-08-25)

- **`ep_curve_response`** (~1.2MB): one call for one perspective (GU) returns **four** elements — AEP, OEP, TCE-AEP, TCE-OEP — each with `value.returnPeriods` / `value.positionValues` arrays of **10,004 points** (return periods 1 through 50,000). Every target return period (5–10,000) is present exactly; extraction is array lookup, no interpolation.
- **`ep_stats-aal_response`**: small; `purePremium` is AAL, `totalStdDev` is standard deviation (`cv` also present). Carries `perspectiveCode` and `epType`.

So retrieval is **2 calls per analysis per perspective** (stats + EP curve) × 5 perspectives (GR, RL, WX, QS, GU) = **10 calls per analysis**, worker-side. The worker parses the 1.2MB curve responses and stores only the extract: AAL, standard deviation, and OEP/AEP losses at 11 return periods per perspective — a few KB per analysis.

### Rejected alternatives

- **Live-fetch on the view path, store nothing** (session 19 D6 as spoken). Fails at list scale: the AAL grid column (D10) × 4–100+ analyses per submission is a per-page API fan-out, and with no cache there is no degraded state when Risk Modeler is slow or down. Also puts IRP reads on the request path.
- **Store the full EP curve per perspective.** ~1.2MB × 5 perspectives per analysis re-creates the massive-data problem D5 eliminated, for points nobody displays.
- **Parquet + `analysis_result_meta` for viewing** (DATA_MODEL §9 as originally drawn). Built for row-level retention; viewing no longer needs row-level data. §9 narrows to export, whose requirements arrive 8/26 (design note 19 O19-12) — the eager-vs-lazy ELT materialization question belongs to that session.

### Consequences

- Results attach to the `irp_analysis` row itself. Broker dedup is automatic — broker analyses are single rows keyed (`rdm_id`, `irp_id`), shared by every EDM copy — which also dissolves O-05's analysis-name-as-key question for viewing.
- TCE-OEP/TCE-AEP arrive free in the same response but are **not stored** (decided 2026-08-25): EP types are OEP and AEP only.
- The R1 export-flow spike (below) is no longer a blocker for this spec; it moves to the export iteration.

## R1 — Retrieval mechanism: REST result endpoints vs. export-job Parquet download (O-01)

> **Superseded for viewing by R3 (2026-08-25).** Kept for the export iteration: the export-job path and its broken-download evidence remain the open question there.

Two candidate mechanisms exist for pulling loss results out of Risk Modeler. Neither is fully validated for this feature's shape; the evidence below was collected 2026-08-25 from prior test artifacts.

### REST result endpoints — proven working, volume unmeasured

`get_stats` / `get_ep` / `get_elt` / `get_plt` (per analysis, per perspective) were exercised end to end by the notebook-framework validation work:

- `irp-notebook-framework/workspace/helpers/analysis_results_validator.py` and `workspace/workflows/_Tools/IRP Integration/Validate_Analysis_Results.ipynb` ran all four endpoints across batches of analyses (production-vs-test comparison from CSV lists), all perspectives, including PLT for HD. The endpoints and their response shapes are trusted.
- What is unmeasured: per-analysis call volume and latency for full row-level data. The knowledge repo flags exactly this — `gap-result-volume`: "Direct result endpoints may be too large for UI retrieval; export/Parquet boundaries need measured thresholds" (also open-questions.md #13). A large ELT/PLT may take many paginated calls per analysis × perspective.

### Export job + Parquet download — designed, never once produced a real file

`submit_analysis_export_job` (irp-integration, PR #9) creates a `DOWNLOAD_RESULTS` export job that packages results as Parquet inside a zip behind a CloudFront `downloadUrl`.

- **One analysis per export job** — Moody's API constraint (irp-integration commit `1c15eb1`).
- The only live test, `IRP/notebooks/export_flows.ipynb` (2026-02-16, PPE): the export job (23567607) reached FINISHED in ~50s with "Export is successful", `download_export_results` reported success — but the saved file, `notebooks/exports/23534840_USFL_Commercial_LT_MANUAL_2_Losses.zip` (3,425 bytes), is the HTML index page of the Moody's "Admin Center" SPA, not a zip. Inspected 2026-08-25: `<!doctype html>… <title>Admin Center</title>`.
- Probable cause: the CloudFront signed URL was rejected or expired and the distribution fell back to the SPA index with HTTP 200, so `raise_for_status()` passed; `download_export_results` (irp-integration `export_job.py`) writes the response with no content validation. The `%2F`-encoded path segments in the URL are a second suspect.
- The knowledge repo's `reports/analysis-export.md` records the workflow at state `designed_client_and_framework_observed` — explicitly not live-verified. No written test notes exist; the notebook and its broken artifact are the only evidence.

### Spike (plan phase)

1. Re-run the export flow: fetch the `downloadUrl` immediately after FINISHED, with and without the Authorization header, and validate the response by magic bytes before writing. Determines whether the failure is a client bug (expired URL / wrong headers) or platform behavior.
2. Measure REST-endpoint volume for a representative analysis (large ELT, HD PLT): calls, bytes, wall time per analysis × perspective.
3. Choose per measured thresholds. A hybrid is acceptable (e.g. REST for stats/EP summaries, export for row-level ELT/PLT) if the numbers say so.

Regardless of the choice: file an irp-integration issue — `download_export_results` saving an HTML page as a `.zip` and logging success is a bug.

## R2 — Broker exposure pointer (O-02)

> **Resolved by design 2026-08-25 (plan phase, T-03).** See the decision below; the plan-phase inspection replaced the spike.

The REST result endpoints take an `exposure_resource_id`. Own analyses store it at submission (`irp_job.resource_uri`, from `submit_portfolio_analysis_job`'s request body). Broker analyses were never submitted by the workbench; their only pointer is the `exposureResourceId` that `search_analyses` returns, captured at RDM backfill (spec 004). Whether the result endpoints accept that pointer for RDM-imported analyses is unconfirmed.

Assessment (2026-08-25): low risk — solvable with business logic if the captured pointer is rejected (e.g. re-resolving via `search_analyses` at retrieval time). Note the §2.2 trust rule is unaffected: the pointer feeds retrieval, never portfolio attribution.

### Decision (T-03, 2026-08-25)

- **Own rows**: pass `irp_portfolio.irp_id` — the RM portfolioId the analysis was submitted against. The wheel's own docstring defines the parameter as "portfolio ID from analysis", and the live stats capture echoes `exposureResourceId: 8` with `exposureResourceType: PORTFOLIO` — the portfolio id. The `irp_analysis.exposure_resource_id` column is NOT used for own rows: spec 010's backfill stores the submit-time `resource_uri` (a URI string) there, not a numeric id.
- **Broker rows**: pass the stored `irp_analysis.exposure_resource_id` — numeric, promoted at RDM backfill from RM's own `search_analyses`/`get_analysis_by_id` response for that analysis (spec 004 R9, PORTFOLIO type only). When it is NULL (metadata read failed at backfill, or non-PORTFOLIO exposure), the retrieval worker does one `get_analysis_metadata(analysis_id=irp_id)` re-read through the gateway (Article 11) and uses its pointer; still NULL after that → retrieval failure with reason.

Why this closes O-02: the pointer handed to the result endpoints is the one RM itself reports for the analysis, not a workbench guess — the "will RM accept it" question reduces to "does RM accept its own identifier", verified in the IRP-sandbox tier rather than a separate spike.

Rejected: parsing the numeric tail out of the own-row `resource_uri` (fragile, and the portfolio FK already holds the id); re-resolving via `search_analyses` on every retrieval (an extra RM round-trip per analysis to re-learn a stored fact).

## R4 — Retrieval trigger, dedup, and failure handling (T-01)

Decided 2026-08-25 (plan phase), from the shipped spec-010 worker/queue machinery.

**Decision.** Retrieval is chained worker-side; the poller is untouched:

- **Own**: `_finalize_analysis_body` already resolves `irp_id`/`settings_metadata` after FINISHED; on success it enqueues `retrieve_analysis_results`. The extract needs `irp_id` (resolved here) and the metadata payload (engine/currency fields), so chaining after backfill — not directly off the poller's FINISHED handler — is the only ordering that has its inputs ready.
- **Broker**: `_backfill_rdm_analyses_body` enqueues one retrieval per captured live `(rdm_id, irp_id)` row whose `loss_results IS NULL` — covering first import, manual RDM sync, and re-import of another EDM copy (US2-3: rows already carrying results enqueue nothing).
- **Dedup**: jobs are keyed `(requestor_type='irp_analysis', requestor_id=<analysis uuid>, rwb_job_type='retrieve_analysis_results')` — a new `irp_analysis` row in `rwb_job_requestor_type_kind`. `enqueue_rwb_job`'s UNIQUE key makes any re-fired trigger a no-op (FR-006), and the worker's own `loss_results IS NOT NULL → skip` guard covers the reclaim/re-run path. Keying on the analysis (not the parent rwb_job id) also gives views a one-join lookup of the retrieval job's `status_code`/`error_detail` for SC-005.
- **Failure**: standard actor pattern (`max_retries=0`; failure → `failed` + `error_detail`; heartbeat + reconciler recover interruption). `enqueue_rwb_job` never resurrects a terminal row, so a failed retrieval stays failed and visible — exactly O-06/spec-010 P-14: the analysis remains FINISHED, views show results-pending plus the reason. No automatic backoff retry is added (unchanged from 010's deferral).

Rejected: enqueueing retrieval from the poller's `_handle_analysis_terminal` (runs before `irp_id` exists — backfill resolves it); keying dedup on the parent backfill job id (a re-run backfill gets a new id and would double-enqueue, leaving FR-006 to the worker guard alone).

## R5 — The wheel rejects WX and QS (T-02)

Found 2026-08-25 inspecting the active wheel (irp-integration 0.6.0rc2, the installed source):

```python
PERSPECTIVE_CODES = ['GR', 'GU', 'RL']   # irp_integration.constants
```

`AnalysisManager.get_stats` / `get_ep` / `get_elt` / `get_plt` all call `_validate_perspective_code`, which raises `IRPValidationError` for any other code **client-side, before any HTTP request**. Spec O-07's approved set is GR, RL, WX, QS, GU — so WX and QS cannot be fetched with the current wheel, and Article 11 forbids going around it (no raw RM calls from app code).

**Decision:** widen `PERSPECTIVE_CODES` in irp-integration to the full Risk Modeler perspective-code list rather than bypass the wheel — filed as [irp-integration#28](https://github.com/premiumiq/irp-integration/issues/28) (2026-08-26).

**Closed 2026-08-26:** irp-integration 0.6.2 (the TestPyPI pin in `pyproject.toml`, installed and checked) ships all 64 RM perspective codes, WX and QS among them. The workbench takes no pin change beyond the usual source switch. The IRP-sandbox tier remains the proof that RM itself serves WX/QS.

Open risk: if RM rejects WX/QS server-side (they are CIC's requested codes, so unlikely but unproven), those perspectives come back as failures, not empties — the sandbox run settles it before US1 ships.

## R6 — Currency and engine-version sources (T-05, FR-021)

From `docs/IRP_INTEGRATION_FOLLOWUPS.md` (documented `search_analyses` / `get_analysis_by_id` response fields, confidence 0.99): the analysis metadata payload carries `currencyCode`, `currencyName`, `engineType`, `engineVersion`, `engineSubTypeCode`.

- **Currency (FR-010)**: both origins already store this payload verbatim in `irp_analysis.settings_metadata` — own rows at `finalize_analysis`, broker rows at `backfill_rdm_analyses` (spec 004). The merged table's Currency column is read-model extraction of `currencyCode`; no new column, no new capture. A NULL `settings_metadata` (failed metadata read) renders as `—`, the existing graceful-blank rule.
- **Engine/model version (FR-021)**: the retrieval worker snapshots `engineType` + `engineVersion` out of the same payload into the `loss_results` extract, so the stored result records what produced it even if the analysis row's snapshot is later refreshed. When `settings_metadata` is NULL at retrieval time, the T-03 `get_analysis_metadata` re-read supplies the same fields.

## R7 — Dedicated page route, breadcrumbs, and controls (T-07, T-08)

Decided 2026-08-25 (plan phase), against the shipped nav/shell machinery.

- **Route**: one page, `GET /results/analyses?ids=<uuid,uuid,…>[&submission=<id>][&edm=<id>][&perspective=GR]`, a hidden child node of the `results` rail root (`results.analyses` — the pattern `submissions.detail` already uses). One nav node + one handler + one template (Article 1). The View button is a `target="_blank"` GET form, so the page has a real, shareable URL (Article 8) and lands in a new browser tab (FR-014); after submit the originating table clears its checkboxes (spec O-10, Approved 2026-08-26).
- **Breadcrumbs**: the manifest chain renders structure ("Results"); entity crumbs (submission name, and EDM name when `edm=` is present) are appended via a new optional `extra_crumbs` list in the page context, rendered by `shell.html` after the manifest chain. Structure still derives solely from the manifest — `extra_crumbs` is display context, the same information today's detail pages put in their header band. `{% block title %}` carries the submission/EDM name (FR-014's tab title; the pattern every detail page already uses).
- **Ordering (FR-016)**: the `ids` query-param order is the column order. Reorder controls rewrite the param and re-request — no stored ordering state. They re-render `#results-view` rather than navigate, because the units selector sits outside that region and a full navigation reset it to millions on every move.
- **Perspective (FR-012)**: a query param on the dedicated page. Switching is an HTMX fragment re-render — screen-wide by construction. (Superseded for the merged table by R8: the toggle moved into the expanded row, and the section URL carries no `perspective`.)
- **Units / copy (FR-017/FR-018)**: display-only client slivers. Cells carry the raw stored value in a data attribute; Alpine formats for the ones/thousands/millions selector (millions default) and the copy button serializes the rendered table with headers as TSV to the clipboard. The server never reformats or recomputes stored numbers.
- **Soft cap (O-09/FR-015)**: no selection block; the table sits in the existing `overflow-x` shell and scrolls horizontally past ~10 columns.
- **T-08 (Assumed)**: a perspective the analysis did not produce returns an empty list from `get_stats`/`get_ep` (the endpoints return row arrays; the GU capture returns rows for a produced perspective). The worker treats an empty list as "fetched, nothing there" (explicit null in the extract, FR-004) and any non-2xx as a retrieval failure. If the sandbox shows RM instead errors on unproduced perspectives, the specific error class moves to the explicitly-empty branch — a one-branch change quarantined in the worker.

## R8 — Merged table preview: what the expanded row shows and where it comes from (O-11, O-12, T-09, T-10)

Preview `docs/ui_previews/merged_analyses_table.html` approved 2026-08-26 (EDM page, submission page, the AAL cell's four results states, the section summary line, three empty states, and the two-column expanded row). It settled three things the plan had drawn differently.

- **The perspective toggle moved off the table and into the expanded row (O-12).** The AAL column reads Gross in millions and nothing more. A section-wide select made the table's one summary number ambiguous at a glance and duplicated the control the dedicated page needs anyway; the row-level toggle sits next to the numbers it changes. The units selector went the same way — it belongs where analyses sit side by side, not on a single-number column.
- **The expanded row is two named groups plus the condensed table (O-11).** Rejected: one undifferentiated settings grid, which is what the analysis-detail expansion does today. Region, line of business, term and PLA were dropped from the group list — Region has its own column, and the other three were not asked for.
- **Blank vs. absent.** A field the origin cannot supply stays listed and reads *not returned* (FR-022). Hiding it makes two analyses of different origins look like they were configured differently rather than reported differently.

Where the fields come from:

| Field group | Own analyses | Broker analyses |
|---|---|---|
| Metadata (engine version, analysis type, peril, subperil, framework, event rate scheme) | `settings_metadata` — the RM analysis payload | same payload, same fields |
| Analysis template | `analysis_template_id` join | not returned |
| Analysis settings (currency code/scheme/vintage, min loss threshold, franchise deductible, unrecognized construction/occupancy) | `submitted_settings` (T-09) | not returned — RM returns none of them; only `currencyCode` |
| Submitted | `inserted_at` (submit request time) | `createDate` from the payload |
| Risk Modeler link | `rm_url` | new `BrokerAnalysis.rm_url`, built the same way |

`AnalysisSettings` needs a `framework` field: `_to_display` currently folds `analysisFramework` into `analysis_mode` alongside `analysisMode`/`mode`, so ELT and the mode compete for one slot.

**T-09 — where the Analysis settings values are read from.** Chosen: a per-analysis snapshot, `irp_analysis.submitted_settings`, written by `_claim_analysis` from the plan item it is about to submit.

- Rejected: **read `analysis_template` through `analysis_template_id`.** Templates are editable. A template edited after a run would silently change what a finished analysis reports it ran with — the failure AGENTS.md architecture rule 8 (approved plans are immutable) exists to prevent. It also cannot supply currency scheme and vintage, which are chosen per suite at submit time (spec 009 P-11) and live on no template.
- Rejected: **read the `execute_analysis_batch` job's `input_data`.** The values are there, keyed by the analysis row's `execution_id` + `execution_item_no`, so it is correct — but a work order is not a display source, and every expanded row would cost a two-hop JSON index lookup into a queue table.

**T-10 — Submitted renders client-side.** The server writes UTC into `<time datetime="…Z">`; a JS sliver formats it with `toLocaleString` to date, time to the second, and AM/PM in the reader's zone (FR-024), and sets the cell's `title` to the full value. The server has no way to know the reader's timezone, and nothing downstream reads the formatted string. The column is 180px so a two-digit hour does not clip.
