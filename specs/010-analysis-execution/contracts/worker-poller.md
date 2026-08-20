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
  "templates": [
    {
      "id": "uuid",
      "name": "US HU DLM v23",
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

## 2. `execute_analysis_batch` worker (`app/workers/analysis_jobs.py`)

Actor named exactly `execute_analysis_batch` (name-based dispatch), registered in the
module's `_BODIES`, `max_retries=0`, `time_limit=60*60*1000` (T-17). Body iterates
`portfolios × templates` in plan order; **per-item isolation** — one item's failure never
stops the loop (FR-010/FR-011; 005 breakout pattern).

Per item (portfolio *p*, template *t*):

1. **Resume check** (FR-015): an `irp_analysis` row for `(execution_id, p.id, t.id)`
   with an `irp_job` row → skip (already done). A row **without** an `irp_job` → go to
   step 3 reusing its recorded `name`.
2. **Name** (FR-007, T-04/T-05): `full = f"{p.name} {t.name}"`; `rm = full[:64]`; while a
   live `irp_analysis` with `(edm_id, name=rm)` exists, next suffix `" (n)"`, re-clipping
   the base so `rm` stays ≤64; the same suffix is appended to `full`. Transaction A:
   insert `irp_analysis` (`edm_id`, `irp_portfolio_id`, `analysis_template_id`,
   `execution_id`, `name=rm`, `full_name=full`, `status_code='pending'`,
   `inserted_by=actor_id`) — the row is visible immediately (FR-008) and claims the name.
3. **Submit** (outside any transaction): `irp_gateway.submit_portfolio_analysis(...)`
   with exactly the plan's snapshot values and explicit `currency` (FR-006, T-02/T-03),
   `treaty_names` from the plan (FR-004/US1-5), `skip_duplicate_check=True`.
4. **Record**, transaction B:
   - Success: `record_submitted_irp_job` — `irp_job_type='analysis'`, `status='QUEUED'`,
     `irp_id=job_id`, `irp_edm_id`, `irp_portfolio_id`, `irp_analysis_id`,
     `requested_from_submission_id`, `request_params` = the submit kwargs JSON,
     `resource_uri = request_body["resourceUri"]` (→ `irp_job_resource`); set
     `irp_analysis.status_code='running'`.
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
    `input_data={"analysis_id": ...}`). Only success path (US4-4: no retrieval for
    failures).
  - `FAILED` / `CANCELLED` → `irp_analysis.status_code='error'`,
    `failure_reason` = the message extracted from the completion body (fallback: the raw
    summary) (FR-011; CANCELLED is a failure — edge case list).
- No resolver needed (all RM reads happen in the backfill worker).

## 4. `backfill_analysis_detail` worker (FR-009)

Body: resolve the RM analysis by its exact submitted name —
`get_analysis_by_name(irp_analysis.name, edm_name)` (Article 2 name-based coupling) —
then update `irp_analysis`: `irp_id`, `settings_metadata` (the `backfill_rdm_analyses`
shape), `exposure_resource_id` (from `irp_job_resource`), `status_code='ready'`.
On success, loss phase only: chain `retrieve_analysis_results` via
`ensure_pending_rwb_job(requestor_type='rwb_job', requestor_id=own id)` + dispatch.
Resolution failure → `rwb_job` `failed` with `error_detail` (reconciler/Dramatiq
handling unchanged; the analysis keeps `running` + its job row shows FINISHED, which the
section renders as "completed, details pending" until a retry lands).

## 5. `retrieve_analysis_results` worker (loss phase, FR-016)

Input: `analysis_id`. Reads `irp_analysis.irp_id` + `exposure_resource_id`; engine type
from `settings_metadata` (HD ⇒ PLT). For each perspective `GR`, `GU`, `RL`:

1. Skip if an `analysis_result_meta` row exists for `(analysis_id, perspective)` —
   results are immutable; this is the resume/idempotency key.
2. `get_stats`, `get_elt`, `get_ep`, and `get_plt` when HD.
3. All empty → no row, no error (T-15). Otherwise write Parquet files
   (`{OUTPUTS_BASE_DIR}/analyses/{analysis_id}/{code}/{type}.parquet`, T-14) then insert
   the meta row (summary columns + paths) in one transaction.

Own analyses only — never fired for rows with `rdm_id` set (P-12).

## 6. `_submission_retry` batch (poller step, FR-010, T-09)

Implements the existing scaffold. Single-threaded, inside `poll_once()`:

1. Select the newest `SUBMISSION FAILED` `irp_job` per `irp_analysis_id`
   (`irp_job_type='analysis'`, `irp_analysis_id IS NOT NULL`) where
   `submission_attempt_count < IRP_SUBMISSION_MAX_RETRIES` and
   `now > completed_at + IRP_SUBMISSION_RETRY_BASE_SECS * 2^submission_attempt_count`.
2. Resubmit from `irp_job.request_params` verbatim (same name, same values — never
   recomposed from live rows).
3. Success → update that row in place: `irp_id`, `status='QUEUED'`,
   `submission_attempt_count += 1`; `irp_analysis.status_code='running'`, clear
   `failure_reason`.
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
