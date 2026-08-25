# Data Access: GeoHaz Execution

All through `db.execute` (safe bound-parameter path, Article 7); the trusted
script path is not used. New service module `app/services/geohaz_service.py`;
the read models feed `edm_service.get_edm_detail` (table render) and the two
fragments.

## Writes (request path — WORKBENCH only, no Risk Modeler call)

```python
def launch(*, edm_id, portfolio_ids: list, actor_id) -> list[str]
```
- Validates: gate (FR-004), portfolio membership + P-06 eligibility. Rejects
  the launch whole on any failure — nothing partially enqueued.
- Builds the single `request_params` document from the fixed DLM parameter
  set (FR-002/FR-003, data-model §3) — the configured `settings.hazard_data_version`
  (research R6), DLM, earthquake + windstorm, `skip_prev_hazard=False`,
  `override_user_def=True`.
- Per portfolio: `rwb_job_service.ensure_pending_rwb_job(requestor_type='analyst_request',
  requestor_id=portfolio_id, rwb_job_type='run_geohaz', input_data=…)` then
  `dispatch.dispatch(...)`. The unique head makes the enqueue idempotent per
  portfolio (race backstop behind the form's P-06 exclusion).
- Returns the launched portfolio ids; the launch route reports their count.

## Reads

```python
def read(*, edm_id=None, portfolio_id=None) -> dict[portfolio_id, PortfolioGeohaz]
    # PortfolioGeohaz bundles:
    #   state:  CellState — data-model §4 (SUBMITTING / job status / hazardVersion);
    #           carries `live: bool` so the template emits hx-trigger only while
    #           a lookup is non-terminal
    #   latest: LatestLookup | None — the newest geohaz irp_job row, with parsed
    #           request_params, completion_summary, status, submitted_at, completed_at
```

One query, three CTEs (non-terminal geohaz `irp_job` per portfolio,
pending/running `run_geohaz` heads, and the `ROW_NUMBER()`-ranked newest geohaz
`irp_job` per portfolio), anchored on `irp_portfolio`. `edm_id` scopes the whole
table render; `portfolio_id` scopes the per-cell poll to one indexed row
(`ix_irp_job_irp_portfolio_id`). Both callers need the state and the latest
lookup together, so splitting them would double the query count on both the
render path and every 3-second poll.

## Config

`HAZARD_DATA_VERSION` (`app/config.py` `Settings.hazard_data_version`, default
`"25.0"`) — the single hazard data version sent on every launch.
