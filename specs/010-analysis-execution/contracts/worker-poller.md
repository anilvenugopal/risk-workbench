# Worker & Poller Contracts: Analysis Execution (spec 010)

Extends `specs/003-edm-rdm-entity-management/contracts/worker-poller.md` (claim /
heartbeat / reconciler / head-row rules unchanged).

## 1. Plan JSON (`rwb_job.input_data` of `execute_analysis_batch`)

Composed once at POST time, immutable thereafter (FR-012; AGENTS.md "approved plans are
immutable"). The worker reads nothing else — no template, suite, or treaty re-reads at
execution time.

```json
{
  "execution_id": "uuid",
  "edm_id": "uuid",
  "edm_name": "CIC_2026_US",
  "submission_id": "uuid | null",
  "actor_id": "uuid",
  "treaty_names": ["Treaty A", "Treaty B"],
  "portfolios": [{"id": "uuid", "name": "US Southeast Wind"}],
  "items": [
    {
      "item_no": 0,
      "suite_id": "uuid | null",
      "suite_name": "US 2026 Q1 | null",
      "template_id": "uuid",
      "template_name": "US HU DLM v23",
      "analysis_profile_name": "...",
      "output_profile_name": "...",
      "event_rate_scheme_name": "... | null",
      "currency": {"code": "USD", "scheme": "RMS", "vintage": "RL25", "asOfDate": "2025-05-28"},
      "min_loss_threshold": 1.0,
      "num_max_loss_event": 1,
      "franchise_deductible": false,
      "treat_construction_occupancy_as_unknown": true,
      "tag_names": ["..."]
    }
  ]
}
```

One item per selected template of each chosen suite — no dedup across suites (spec
P-02 as amended): a template in two chosen suites appears twice, each item carrying its
suite's confirmed `currency` block (P-15/T-03; `asOfDate` resolved from the chosen
vintage's `effective_date` at compose time). Template runs produce items with
`suite_id`/`suite_name` null and the execution's single currency block. `tag_names`
includes the submission's name when the execution has a submission context (FR-021,
T-20).

## 2. `execute_analysis_batch` worker (`app/workers/analysis_jobs.py`)

Actor named exactly `execute_analysis_batch` (name-based dispatch), registered in the
module's `_BODIES`, `max_retries=0`, `time_limit=60*60*1000` (T-17). Body iterates
`portfolios × items` in plan order; **per-item isolation** — one item's failure never
stops the loop (FR-010/FR-011; 005 breakout pattern). The work-unit key is
`(execution_id, portfolio, item_no)` — `(portfolio, template)` alone is ambiguous when
two suites share a template.

Per work unit (portfolio *p*, item *i*):

1. **Resume check** (FR-015): an `irp_analysis` row for `(execution_id, p.id,
   i.item_no)` (`execution_item_no` column) with an `irp_job` row → skip (already done).
   A row **without** an `irp_job` → go to step 3 reusing its recorded `name`.
2. **Name** (FR-007, T-04/T-05): `full = f"CRE_{p.name}_{i.template_name}"`; `rm = full[:64]`;
   while a live `irp_analysis` with `(edm_id, name=rm)` exists, next suffix `_{n}` (first
   collision → `_2`), re-clipping the base so `rm` stays ≤64; the same suffix is
   appended to `full`.
   Transaction A: insert `irp_analysis` (`edm_id`, `irp_portfolio_id`,
   `analysis_template_id`, `execution_id`, `execution_item_no=i.item_no`, `name=rm`,
   `full_name=full`, `status_code='pending'`, `inserted_by=actor_id`) — the row is
   visible immediately (FR-008) and claims the name. `uq_irp_analysis_execution_item`
   makes step 1's read of the resume key single-row by construction.
3. **Submit** (outside any transaction): `irp_gateway.submit_portfolio_analysis(...)`
   with exactly the plan's snapshot values and explicit `currency` (FR-006, T-02/T-03),
   `treaty_names` from the plan (FR-004/US1-5), `skip_duplicate_check=True`.
4. **Record**, transaction B:
   - Success: `record_submitted_irp_job` — `irp_job_type='analysis'`, `status='QUEUED'`,
     `irp_id=job_id`, `irp_edm_id`, `irp_portfolio_id`, `irp_analysis_id`,
     `requested_from_submission_id`, `request_params` = the submit kwargs JSON,
     `resource_uri = request_body["resourceUri"]` (→ `irp_job_resource`).
     `irp_analysis.status_code` stays `pending`: `irp_job.status` carries the
     progress and the poller keeps it current.
   - `IRPIntegrationError`: `record_submission_failure` (same linkage columns,
     `status='SUBMISSION FAILED'`, `submission_attempt_count=1`, `request_params` set) and
     write the message to `irp_analysis.failure_reason` (status stays `pending` while
     retries remain — FR-010).

Outcome: `output_data = {"submitted": n, "submission_failed": m}`; `JobResult.fail` only
when every item failed to submit. A death between steps 3 and 4 may resubmit one item on
reclaim — accepted (research T-01).

## 3. Poller — `analysis` job type (`app/poller/run.py`)

- `_GETTERS["analysis"] = irp_gateway.get_analysis_job` (single-status check only;
  `poll_*_to_completion` stays forbidden).
- `_TERMINAL_HANDLERS["analysis"]` (inside the tracking transaction):
  - `FINISHED` → enqueue head `rwb_job` `backfill_analysis_detail`
    (`requestor_type='irp_job'`, `requestor_id=job.id`,
    `input_data={"analysis_id": ..., "rm_analysis_id": ...}` — `rm_analysis_id` is
    RM's `analysisId`, extracted from the completion body's
    `tasks[].output.log.analysisId`, `null` when absent). Only success path (US4-4:
    no retrieval for failures).
  - `FAILED` / `CANCELLED` → `irp_analysis.status_code='error'`,
    `failure_reason` = the message extracted from the completion body (fallback: the raw
    summary) (FR-011; CANCELLED is a failure — edge case list).
- No resolver needed (all RM reads happen in the backfill worker).

## 4. `backfill_analysis_detail` worker (FR-009)

Body: fetch the analysis details by the `rm_analysis_id` the poller extracted from the
completion body — `get_analysis_metadata(analysis_id=int(rm_analysis_id))`; a missing
`rm_analysis_id` fails the `rwb_job` — then update `irp_analysis`: `irp_id`
(= `rm_analysis_id`), `irp_app_analysis_id` (the payload's `appAnalysisId`, NULL when
absent), `settings_metadata` (the `backfill_rdm_analyses` shape),
`status_code='ready'`. `exposure_resource_id` is not written — it holds RM's numeric
`exposureResourceId` for broker rows (R9/FR-036), while the portfolio `resourceUri`
this analysis was submitted against stays in `irp_job_resource`.
On success, loss phase only: chain `retrieve_analysis_results` via
`ensure_pending_rwb_job(requestor_type='rwb_job', requestor_id=own id)` + dispatch.
Actor follows the standard pattern (`max_retries=0`). Resolution failure → `rwb_job`
`failed` with `error_detail`, **and** `irp_analysis.status_code='error'` with the same
reason: the `irp_job` already reads FINISHED, so leaving the analysis `pending` would
keep the EDM page's 3s poll running for a row that is never coming back. The reconciler
recovers an *interrupted* backfill; a genuinely failed one stays `failed` until
re-dispatched — automatic retry is deferred (P-14 amendment, research.md).

## 5. `retrieve_analysis_results` worker (loss phase, FR-016)

Input: `analysis_id`. Reads `irp_analysis.irp_id` + the portfolio `resource_uri` from
`irp_job_resource` (`resource_type='portfolio'`); engine type
from `settings_metadata` (HD ⇒ PLT). For each perspective `GR`, `GU`, `RL`:

1. Skip if an `analysis_result_meta` row exists for `(analysis_id, perspective)` —
   results are immutable; this is the resume/idempotency key.
2. `get_stats`, `get_elt`, `get_ep`, and `get_plt` when HD.
3. All empty → no row, no error (T-15). Otherwise write Parquet files
   (`{OUTPUTS_BASE_DIR}/analyses/{analysis_id}/{code}/{type}.parquet`, T-14) then insert
   the meta row (summary columns + paths) in one transaction.

Own analyses only — never fired for rows with `rdm_id` set (P-12).

Actor follows the standard pattern (`max_retries=0`): a failure lands the `rwb_job` in
`failed` with `error_detail`, and the detail view keeps showing results-pending.
Interruption recovers via the reconciler plus the step-1 skip; automatic backoff retry
and a retrieval-failed display are deferred (P-14 amendment, research.md).

## 6. `_submission_retry` batch (poller step, FR-010, T-09)

Implements the existing scaffold. Single-threaded, inside `poll_once()`:

1. Select the newest `SUBMISSION FAILED` `irp_job` per `irp_analysis_id`
   (`irp_job_type='analysis'`, `irp_analysis_id IS NOT NULL`) where
   `submission_attempt_count < IRP_SUBMISSION_MAX_RETRIES` and
   `now > completed_at + IRP_SUBMISSION_RETRY_BASE_SECS * 2^submission_attempt_count`.
2. Resubmit from `irp_job.request_params` verbatim (same name, same values — never
   recomposed from live rows).
3. Success → update that row in place: `irp_id`, `status='QUEUED'`,
   `submission_attempt_count += 1`, `completed_at = NULL` (it is the backoff clock, and
   the job is back in flight); clear `irp_analysis.failure_reason`. `status_code` is
   already `pending`.
4. Failure → `submission_attempt_count += 1`, refresh `last_submission_response` and
   `irp_analysis.failure_reason`. At the maximum the row stays `SUBMISSION FAILED` and
   `irp_analysis.status_code` flips to `error` — visible, never dropped (SC-004).

Pre-010 entity imports keep their insert-per-failure behavior; this batch touches only
`analysis` rows.

## 7. Recovery summary (FR-015)

Unchanged machinery, no new code: heartbeat thread + `reconcile_stale_rwb_jobs` re-pends
a dead worker's job; `_dispatch_pending` redelivers; `execute_analysis_batch` resumes via
§2 step 1; `retrieve_analysis_results` resumes via §5 step 1; a poller crash loses
nothing (next pass re-reads non-terminal jobs).
