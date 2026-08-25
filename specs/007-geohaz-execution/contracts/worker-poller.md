# Worker & Poller: GeoHaz Execution

## Gateway (`app/services/irp_gateway.py`)

Two new methods on the `IRPGateway` Protocol, `_RealGateway`, the module free
functions, `__all__`, and the unit-tier `FakeIRP`
(`tests/unit/fakes/fake_irp.py` implements the whole protocol):

```python
def submit_geohaz(*, edm_name: str, portfolio_name: str, version: str,
                  perils: list[str], skip_prev_hazard: bool,
                  override_user_def: bool) -> SubmitResult
    # wraps client.portfolio.submit_geohaz_job(portfolio_name, edm_name, layers)
    # building one hazard layer per peril (R4/R5) — hazard-only, no geocode
    # layer ever sent (FR-005):
    #   {"type": "hazard", "name": peril,          # "earthquake" / "windstorm"
    #    "engineType": "RL", "version": version,
    #    "layerOptions": {"overrideUserDef": override_user_def,
    #                     "skipPrevHazard": skip_prev_hazard}}
    # returns SubmitResult(irp_id=str(job_id), resource_uri=body["resourceUri"], payload=body)
    #   — resource_uri comes from the REQUEST body (the completion response omits it)

def get_geohaz_job(irp_id: str) -> JobStatus
    # JobStatus(status=str(data["status"]), result=data) — single-status check only
```

`poll_geohaz_job_to_completion` is never referenced (the existing source-scan
guards in `tests/unit/test_architecture_guards.py` cover the new files).

## Worker: `run_geohaz` (`app/workers/geohaz_jobs.py`, NEW)

Auto-discovered by `app/workers/loader.py` (actor name == `rwb_job_type`);
module-level `_BODIES = {"run_geohaz": _run_geohaz_body}` for the unit-tier
synchronous drain.

`_run_geohaz_body(rwb_job_id) -> runtime.JobResult`:

1. Read `input_data` (data-model §2): portfolio/EDM ids + names, the analyst,
   the `request_params` document.
2. Map params to the gateway call (research R5).
3. On success:
   `irp_job_service.record_submitted_irp_job(irp_job_type='geohaz', irp_edm_id=…, irp_portfolio_id=…, irp_id=…, resource_uri=…, payload=res.payload, request_params=params, actor_id=requested_by_user_id)`
   → app-local status `SUBMITTED`; the first poll replaces it with Risk Modeler's status.
4. On exception (includes the wheel's own pre-validation failures — ambiguous
   name, zero accounts, zero locations):
   `irp_job_service.record_submission_failure(irp_job_type='geohaz', irp_edm_id=…, irp_portfolio_id=…, payload=…, request_params=params, actor_id=…)`
   → terminal `SUBMISSION FAILED`, then `JobResult.fail(...)`. The failure is
   visible in the column/history (FR-014) and the portfolio is immediately
   relaunchable (T-07). One portfolio's failure never touches its siblings —
   they are separate rwb_jobs (FR-006).

The worker changes no portfolio, EDM, or submission state (FR-014). No
`package_id` — a geohaz job hangs off the portfolio, not a package.

## `irp_job_service` changes

`record_submitted_irp_job` and `record_submission_failure` gain optional
`irp_portfolio_id` and `request_params` arguments threaded into
`_insert_irp_job`. `update_tracking` accepts the extracted completion summary;
`list_non_terminal` and `TERMINAL` are unchanged.

## Poller (`app/poller/run.py`)

One line:

```python
_GETTERS = {..., "geohaz": irp_gateway.get_geohaz_job}
```

- The existing `_track_irp_jobs` loop handles the rest: single-status check,
  and `update_tracking`, which stores the summary in `completion_summary` and the
  full response in `last_completion_result`. `_geohaz_completion_summary` extracts
  `tasks[].output.summary` and runs only on a terminal status — `update_tracking`
  writes both columns only when terminal, so parsing a non-terminal body is work
  SQL discards.
- On `FINISHED`, `_resolve_geohaz_metadata` calls Get Portfolio Metadata outside
  the database transaction. `_handle_geohaz_terminal` replaces
  `irp_portfolio.exposure_detail.metrics` inside the tracking transaction and
  retains `exposure_detail.summary`. A metadata read failure is logged and does
  not prevent the job status update.

## `_submission_retry`

Stays the existing no-op stub. Geohaz `SUBMISSION FAILED` rows are standard
rows, now deduplicable per entity via `irp_portfolio_id`, and join the batch
when it is implemented (research R9).
