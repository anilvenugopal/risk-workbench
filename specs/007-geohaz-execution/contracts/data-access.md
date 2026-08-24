# Data Access: GeoHaz Execution

All through `db.execute` (safe bound-parameter path, Article 7); the trusted
script path is not used. New service module `app/services/geohaz_service.py`;
the read models feed `edm_service.get_edm_detail` (table render) and the two
fragments.

## Writes (request path — WORKBENCH only, no Risk Modeler call)

```python
def launch(*, edm_id, portfolio_ids: list, actor_id) -> LaunchResult
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

## Reads

```python
def lookup_states(edm_id) -> dict[portfolio_id, CellState]
    # one grouped query over geohaz irp_job rows + pending/claimed run_geohaz
    # heads for the whole table render (data-model §4: SUBMITTING/status/hazardVersion)

def cell_state(portfolio_id) -> CellState
    # single-portfolio variant for the poll fragment; CellState carries
    # `live: bool` so the template emits hx-trigger only while non-terminal

def latest_lookup(portfolio_id) -> LatestLookup | None
    # newest irp_job row with parsed request_params, completion_summary, and status

def latest_lookups(edm_id) -> dict[portfolio_id, LatestLookup]
    # newest irp_job row per portfolio in one EDM (batch form for the table render)

def completion_summary(result: dict | None) -> str | None
    # returns tasks[].output.summary for terminal poller storage
```

Batch shape note: `lookup_states` runs once per table render (one query, not
per-row); the per-cell poll route calls `cell_state` (indexed single-portfolio
read on `ix_irp_job_irp_portfolio_id`).

## Config

`HAZARD_DATA_VERSION` (`app/config.py` `Settings.hazard_data_version`, default
`"25.0"`) — the single hazard data version sent on every launch.
