# Research: Analysis Execution (spec 010)

Evidence gathered 2026-08-20 from this worktree, the active `irp-integration` wheel
(TestPyPI `0.6.0rc2` — `uv.lock`; the `irp-local` source is commented out in
`pyproject.toml`), `docs/PRD.md`, `docs/DATA_MODEL.md`, and the unmerged branches
`origin/005-subportfolio-breakouts` and `origin/007-geohaz-execution`.

## Wheel findings (0.6.0rc2) that shaped the design

- `submit_portfolio_analysis_job(edm_name, portfolio_name, job_name, analysis_profile_name,
  output_profile_name, event_rate_scheme_name, treaty_names, tag_names, currency=None,
  skip_duplicate_check=False, franchise_deductible=False, min_loss_threshold=1.0,
  treat_construction_occupancy_as_unknown=True, num_max_loss_event=1) -> (job_id, request_body)`.
  `request_body["resourceUri"]` is the portfolio URI needed later by every result getter.
- `submit_portfolio_analysis_jobs(list)` reads **no `currency` key** and **discards the
  request bodies** (returns job IDs only). Using it would silently default every analysis
  to USD/latest-vintage and lose `resourceUri`.
- `currency=None` falls back to `get_analysis_currency()` — hardcoded USD, latest RMS
  vintage, silent defaults. FR-006 forbids any defaulting, so the currency block must be
  passed explicitly on every call.
- The client resolves every name internally (EDM → exposureId, portfolio → uri, treaties
  via `treatyName IN (...)` with an exact-count check, profiles/scheme/tags by name).
  DLM templates require `event_rate_scheme_name`; HD omits `eventRateSchemeId`.
- `get_analysis_job(job_id)` returns the raw job JSON; terminal statuses `FINISHED` /
  `FAILED` / `CANCELLED`; only `FINISHED` is success.
- Result getters: `get_stats` / `get_elt` / `get_ep` / `get_plt(analysis_id,
  perspective_code, exposure_resource_id)`, perspective codes `GR`/`GU`/`RL`, return
  `List[Dict]` (not DataFrames). `get_plt` is HD-only. There is no `get_analysis_results`.
- The completion backfill needs no name search: the FINISHED job body
  (`GET /platform/model/v1/jobs/{jobId}`) carries the created analysis at
  `tasks[].output.log.analysisId` (observed in both tasks, Ben's sandbox
  2026-08-25), and the analysis-details response
  (`GET /platform/riskdata/v1/analyses/{id}`) — stored verbatim as
  `settings_metadata` — carries `appAnalysisId`, the id the RM web UI route
  takes (design 19 O19-3).
- The client retries 429/5xx itself (5 attempts, backoff) — no second retry layer.
- Error caveat: the analysis methods re-wrap most exceptions as `IRPAPIError` without
  `from e`; catch `IRPIntegrationError` and keep the message as the failure reason.
- No treaty edit/update methods and no RM deep-link helpers exist — pass-through URLs are
  built workbench-side (`edm_service._rm_datasource_url` already does this).

## Codebase findings that shaped the design

- **No SSE exists.** PRD §14.7 mentions SSE, but the shipped live-update mechanism is an
  HTMX self-poll: `partials/edm_detail_body.html` re-fetches its own `body` route every 3s
  while a server-computed `live` flag is true, with a 204 escape hatch to avoid collapsing
  open rows. `sse-starlette` is a declared but unused dependency. FR-014's "same live
  treatment as import jobs" therefore means the self-poll, not SSE.
- The EDM detail page is one body partial shared by the standalone (`/edms/{id}`) and
  submission-contextual routes; broker analyses render via
  `partials/broker_analysis_row.html` (the "Iteration-3 view" US4 extends).
- Portfolio rows have no selection today; the multi-select pattern to copy is
  `partials/edm_sync_table.html` + `syncPicks()` in `app/static/js/app.js`. The modal
  pattern to copy is `partials/submission_entity_add_modal.html` (HTMX-injected fragment,
  Alpine shell, 409/422 re-render, close via `replaceChildren()`).
- Routes never call IRP directly: route → service (validate + persist + enqueue +
  dispatch) → worker performs the RM call and writes `irp_job` via
  `irp_job_service.record_submitted_irp_job` / `record_submission_failure`.
- The poller dispatches on three dicts in `app/poller/run.py` (`_GETTERS`,
  `_TERMINAL_RESOLVERS`, `_TERMINAL_HANDLERS`); RM lookups run outside the transaction,
  entity updates + head `rwb_job` enqueues inside it. `_dispatch_pending()` sweeps
  pending rows; `reconcile_stale_rwb_jobs` re-pends stale `running` rows (FR-015).
- `_submission_retry()` is a scaffold: no-op unless `IRP_SUBMISSION_MAX_RETRIES` is set
  (`app/config.py` defaults it to `None`). `record_submission_failure` inserts a **new**
  `irp_job` row per failure, so any retry must select per entity, not per row.
- The approved-plan-worker pattern exists on `origin/005-subportfolio-breakouts`:
  plan composed once at confirm, persisted in `rwb_job.input_data`, worker "reads NOTHING
  else", per-item try/except isolation, partial success = `succeeded` with an outcomes
  dict. That branch also raises the Dramatiq actor `time_limit` for fan-out loops.
  `origin/007-geohaz-execution` adds `irp_job.irp_portfolio_id` and
  `irp_job.request_params` — the same columns this feature needs.
- `irp_analysis` is broker-shaped today: `rdm_id` and `source_rdm_name` NOT NULL,
  `irp_id` NOT NULL, no portfolio/template linkage. DATA_MODEL §6 already specifies the
  own-analysis shape (both FKs nullable + CHECK); the migration just hasn't caught up.
- `rwb_job_type_kind` already seeds `retrieve_analysis_results` (no worker behind it);
  `irp_job_type_kind` already seeds `analysis`.
- `tests/sqlserver/test_job_tables_migration.py:53` asserts `irp_portfolio_id` is absent
  from `irp_job` — written to flip when the column lands; `tests/iteration1_mirror.py`
  (SQLite DDL + `EXACT_MATCH_TABLES`) must move with every schema change.
- The job-monitor pages `/workflows/irp-jobs` and `/workflows/rwb-jobs` are stubs; the
  status-bar activity zone is an empty placeholder. Spec assumption "job-monitor views
  exist from earlier iterations" is wrong as of this worktree.
- `pandas` arrives via `irp-integration[databridge]`; `pyarrow` is not a dependency yet
  (needed for Parquet in the loss phase).

## Decisions

| ID | Decision | Status |
|---|---|---|
| T-01 | One `execute_analysis_batch` rwb_job per execution; the approved plan (portfolios, template value snapshots with each item's confirmed currency block, treaty names) persisted in `input_data`; worker loops the submit call with per-item isolation and a resumable skip check | Approved |
| T-02 | Loop `submit_portfolio_analysis_job` app-side; never `submit_portfolio_analysis_jobs` | Approved |
| T-03 | Currency block always passed explicitly, taken from the modal's confirmed selection (per suite — spec P-15, note 17 D4/D5); `asOfDate` derived from the chosen vintage's `irp_currency_scheme_vintage.effective_date` at plan-persist time | Approved (amended 2026-08-20) |
| T-04 | Name = `"CRE_{portfolio.name}_{template.name}"` (design 18, 2026-08-24); `irp_analysis.name` holds the ≤64-char name sent to RM, new `full_name` column holds the untruncated name | Approved |
| T-05 | Rerun collision check is local (`irp_analysis` names for the EDM), suffix `_2`, `_3`… fitted inside the 64-char cap (RM rejects parentheses in analysis names); RM call uses `skip_duplicate_check=True` | Approved |
| T-06 | Schema edits in the single revision `0001_initial.py` (dev drop-create-seed): reshape `irp_analysis`, extend `irp_job`, add `analysis_result_meta` + `analysis_perspective_kind` in the loss phase | Approved |
| T-07 | Analysis job status shown in the UI comes from the latest `irp_job` row per analysis (join via new `irp_job.irp_analysis_id`); `irp_analysis.status_code` keeps the existing four coarse codes | Approved |
| T-08 | Run-failure reason: poller terminal handler extracts the message from the completion body into `irp_analysis.failure_reason`; submit-failure reason written by the worker the same way | Approved |
| T-09 | Implement the poller `_submission_retry` batch: per-analysis latest `SUBMISSION FAILED` row, exponential backoff, retry updates that row in place; `IRP_SUBMISSION_MAX_RETRIES` default changes `None` → 3 (PRD §14.3) | Approved |
| T-10 | On `FINISHED` the poller extracts `tasks[].output.log.analysisId` from the completion body into the backfill's `input_data`; `backfill_analysis_detail` fetches the analysis details by that id, writes `irp_id` + `irp_app_analysis_id` + `settings_metadata`, and (loss phase) chains `retrieve_analysis_results`. Name search dropped (2026-08-26) — the 64-char truncation makes names collide | Approved |
| T-11 | Live updates reuse the existing 3s body self-poll; the `live` flag adds "any executed analysis non-terminal". No SSE in this feature | Approved |
| T-12 | Fill the `/workflows/irp-jobs` stub with a minimal `irp_job` listing (name/type/status/submitted-by/when, 3s poll) so analysis jobs are visible per FR-014; delivered as the iteration's final phase | Approved 2026-08-20 |
| T-13 | Loss storage per DATA_MODEL §9: Parquet row-level files + one `analysis_result_meta` row per (analysis, perspective); summary columns `aal`, `std_dev`, `max_event_loss`, `elt_record_count`, `has_plt`; return-period/OEP/AEP numbers read from the EP Parquet at view time; add `pyarrow` | Approved |
| T-14 | Parquet path is `{OUTPUTS_BASE_DIR}/analyses/{analysis_id}/{perspective_code}/{result_type}.parquet` — keyed by analysis id, not submission id | Approved |
| T-15 | Retrieval attempts all three perspectives; a perspective RM returns nothing for gets no meta row and no error | Approved |
| T-16 | Treaty pass-through: RM datasource link (existing pattern) + an Alpine sliver that fires the existing detail-sync POST when the window regains focus after the link was clicked | Approved |
| T-17 | `execute_analysis_batch` actor gets `time_limit=60*60*1000` (Dramatiq's 10-min default is too short for 150 sequential submits) | Approved |
| T-18 | No extra rate limiter: the sequential per-item loop plus the client's built-in retry is the throttle (PRD §15.6) | Approved |
| T-19 | Currency defaults are three pinned settings in `app/config.py` — `default_analysis_currency_code` (default `USD`), `default_analysis_currency_scheme` (default `RMS`), `default_analysis_currency_vintage` (default empty) — read from `.env`, changed by ops only (spec P-16, note 17 D6/D7). The modal pre-fills from them; a value that is empty or absent from the synced cache pre-selects nothing. No admin UI, no table | Approved 2026-08-20 |
| T-20 | Submission tag: plan composition appends the submission's name to each item's `tag_names` when the execution has a submission context (spec P-17); RM resolves/creates the tag at submit (existing wheel behavior for `tag_names`). Workbench association is the existing `irp_job.requested_from_submission_id` + plan `submission_id` — no schema change | Approved 2026-08-20 |

## Rationale and rejected alternatives

**T-01 — one job per execution, not one per analysis.**
`UNIQUE(requestor_type, requestor_id, rwb_job_type)` means a fan-out needs distinct
requestor ids per row; 150 `rwb_job` rows buy nothing over one resumable loop (single
worker, Article 10) and multiply reconciler surface. The 005 breakout worker is the
proven shape. `requestor_id` is a fresh execution UUID minted at persist time, so
repeated runs against the same EDM never collide with the idempotency key. Rejected:
request-path submit loop (Article 11 permits it, but 150 × ~6 RM calls each blocks the
response for minutes and dies with the browser — P-11 exists precisely because of this);
one rwb_job per analysis (above).

**Resumability inside T-01.** Per work unit: (1) transaction A inserts the `irp_analysis`
row (claiming the computed name, satisfying FR-008); (2) the RM submit runs outside any
transaction; (3) transaction B writes the `irp_job` row. On reclaim the worker skips
work units whose `irp_analysis` row already has an `irp_job`; a row without one is
re-submitted using its already-recorded name. The work-unit key is `(execution_id,
portfolio_id, execution_item_no)` — with dedup dropped (P-02 as amended), the same
portfolio × template can legitimately appear twice in one run, so the plan-item ordinal,
stored on `irp_analysis.execution_item_no`, is what makes the resume check exact. A death between (2) and (3) can duplicate one RM
submission — accepted: idempotent IRP submission is a documented upgrade, not default
complexity (Article 10), and the window is one item wide.

**T-02/T-03** — forced by the wheel: the batch helper loses `resourceUri` (unrecoverable
later; §15.3 needs it for every result getter) and cannot carry currency. Explicit
currency is FR-006; the library's silent USD fallback is exactly the submit-time
defaulting FR-006 forbids. The block's values come from the modal's per-suite selection
(spec P-15 — design note 17 moved currency off templates entirely), captured into the
plan at compose time; pre-filling a visible picker from the T-19 env defaults is not
submit-time defaulting — the analyst confirms it.

**T-04 — two name columns.** The submitted name is not derivable from the full name once
a suffix exists (the suffix survives clipping, the middle doesn't), and `name` records
the exact string RM shows for the analysis. Completion no longer depends on the name:
the backfill resolves by the job-payload `analysisId` (T-10 as amended 2026-08-26), so
name uniqueness is a display concern, not a correctness requirement. Broker rows keep
`full_name` NULL. Rejected: storing only the full name and re-deriving (ambiguous), a
separator other than a space (§2.6 says "portfolio name + template name"; inventing
punctuation adds nothing).

**T-05 — local collision check.** Pre-cutover the workbench is the only thing writing
analyses into its EDMs (no-backwards-compatibility rule), so local names are the truth;
`skip_duplicate_check=True` avoids one RM search per item × 150. A name RM still rejects
lands as a submission failure with RM's reason — the edge cases table already treats
pre-blocking as a non-goal. A filtered unique index on `(edm_id, name)` for live executed
rows backs the check.

**T-07 — no new analysis statuses.** The user-executed section needs failed-to-submit vs
run-failed vs live progress; all of that is on the joined `irp_job` row (`status`,
`submission_attempt_count`). Widening `irp_analysis_status_kind` would duplicate the job
vocabulary into a second table and drift (Article 3 keeps the RM mirror on `irp_job.status`
only). `status_code` transitions: `pending` at insert → `running` once submitted →
`ready` on backfill / `error` on `FAILED`/`CANCELLED`/terminal `SUBMISSION FAILED`.

**T-09 — retry updates in place.** `record_submission_failure`'s insert-per-failure
design makes attempt counting per-row meaningless (its own docstring flags this). The
batch selects the newest `SUBMISSION FAILED` row per `irp_analysis_id`, resubmits from
`irp_job.request_params` (the kwargs snapshot written at first attempt — never recomposed
from live template rows, per the approved-plans rule), and on failure increments
`submission_attempt_count` on that row; on success sets `irp_id` + `QUEUED`. Backoff:
eligible when `now > completed_at + IRP_SUBMISSION_RETRY_BASE_SECS * 2^attempts`.

**T-11 — self-poll, not SSE.** FR-014 defines "live" by reference to import jobs, which
poll. Introducing SSE here would be new infrastructure (nginx `proxy_buffering off`,
subscription fan-out) for no requirement the poll doesn't meet at a 3s cadence. PRD §14.7
remains the eventual home for SSE when the status bar is built.

**T-12 — minimal job listing.** FR-014 (as then worded) said analysis jobs "MUST appear in the
existing job views", but those views are stubs — the spec's assumption was wrong. The
smallest honest change is a plain read-only table on the existing `/workflows/irp-jobs`
route. The alternative — treating the FR as vacuously satisfied — hides 150-job runs from
the one page named for them. Approved 2026-08-20 and scheduled as the iteration's final
delivery phase (after loss retrieval); nothing else in the design depends on it.

**T-13/T-14 — result storage.** DATA_MODEL §9 owns the hybrid (SQL summary + Parquet
rows). Deviation: §9's path template starts at `{submission_outputs_dir}`, but executions
launched from the standalone EDM page have no submission; one path rule keyed by analysis
id avoids a fork, and the meta row stores the paths anyway. DATA_MODEL §9 gets updated to
match at implementation. Return-period/OEP/AEP numbers live in the EP Parquet and are read
on drill-down — the meta row is a list-view index, not the report.

**T-15** — an analysis run without treaties has no meaningful RL perspective; RM returns
empty rather than erroring. Absence of a perspective is data, not a failure (graceful-empty
doctrine).

**Retrieval/backfill failure handling (P-14 amended 2026-08-20).** The originally
clarified design — automatic backoff retry up to a configured maximum plus a
retrieval-failed display — is deferred to a later iteration. `retrieve_analysis_results`
and `backfill_analysis_detail` follow the standard rwb_job actor pattern every existing
worker uses: `max_retries=0`, a failure lands the `rwb_job` in `failed` with
`error_detail`, interruption is recovered by the heartbeat + reconciler, and resume goes
through each worker's skip check. The detail view shows results-pending until numbers
arrive. No retrieval-retry config settings and no retrieval-failure column are added.

**T-16** — no RM API exists for treaty edit and no tracked job is wanted (P-08). The
refocus-triggered sync reuses `edm_service.sync_detail` unchanged; without it the analyst
must click Sync manually, which fails FR-018's "on return … re-read".

## Open items

None. O5-2 (exact return-period point set) is owned by the PRD and does not block —
T-13 stores full EP curves, so the displayed point set is a view concern.
