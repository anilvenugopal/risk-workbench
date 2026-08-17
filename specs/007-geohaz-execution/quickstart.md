# Quickstart: GeoHaz Execution (Iteration 5)

How to verify the feature. Contracts: [contracts/](contracts/); schema:
[data-model.md](data-model.md).

## Prerequisites

- Docker stack up (`make dev-up`) or WSL2 native (`make sqlserver-up` +
  `make native-dev`) — **starting the stack is the developer's call**; agents
  report and stop if it is down.
- DB lifecycle: **Rebuild** — `make db-rebuild` (destructive) after the
  migration edit.
- `GEOHAZ_DATA_VERSIONS` set in `.env` (e.g. `25.0,24.0`).
- An EDM in `ready` with ≥2 synced portfolios (import one via the existing
  flow, or `make db-rebuild` seed + sync).

## 1. Unit tier (no containers)

```bash
uv run pytest tests/unit
```

Covers: peril/eligibility/gate validation, per-portfolio enqueue, worker
submit success/failure isolation, param mapping (one hazard layer per peril;
no geocode layer ever built), live-status and stored-version display,
completion-summary extraction, terminal metadata refresh, and poller routing.

## 2. SQL Server tier

```bash
make test-sql        # or make wsl-test-sql
```

Schema drift guard passes with the three new `irp_job` columns + `run_geohaz`
kind row mirrored.

## 3. Manual walkthrough (spec §Exit / SC-001…SC-006)

1. Open an EDM summary page with ≥2 synced portfolios. The portfolios table
   shows each portfolio's raw `hazardVersion` in the **"Hazard looked up?"**
   column. An absent or empty value displays empty (SC-006).
2. Select two portfolios → **Run hazard lookup**. No modal opens. Both jobs use
   the first configured data version, DLM, earthquake + windstorm, Skip locations
   with previous hazard lookup off, and Overwrite user-defined hazard values on.
3. Both portfolios' cells show **SUBMITTING** immediately, **SUBMITTED** after
   Risk Modeler accepts each job, then Risk Modeler statuses,
   refreshing without a reload (watch the network tab: per-cell 3s polls that
   stop at terminal — SC-002). No request to Risk Modeler appears on any page
   render.
4. While a job is non-terminal, its portfolio cannot be selected for another
   launch (P-06).
5. On completion: cells display the refreshed raw `hazardVersion`; expand each row — the right-hand
   column shows the most recent run's Data Version, Model Family, Hazard Layers,
   Skip locations with previous hazard lookup, Overwrite user-defined hazard
   values, and Result from `completion_summary`.
6. Failure path: stop the worker and launch —
   the cell returns to its stored `hazardVersion`, the latest details show the failed lookup, and the same
   portfolio is immediately launchable again (SC-005). EDM with zero
   portfolios → the launch action is disabled (SC-004).

## 4. Verify the poller/worker hop

Restart the worker after deploying worker code. Dramatiq does not reload an
already imported job body when the source file changes.

`docker compose logs -f` (or the native poller/worker terminals): the launch
enqueues `run_geohaz` rwb_jobs; the worker submits and writes `irp_job`
(app-local status `SUBMITTED`); the poller's first geohaz status check replaces
that value with Risk Modeler's status and tracks the job to
terminal and store `last_completion_result`.

## 5. IRP sandbox capture (required before the feature is called done — R7/T-04)

Set `IRP_TEST_GEOHAZ_EDM_NAME` and `IRP_TEST_GEOHAZ_PORTFOLIO_NAME` in
`infra/.env` to a small sandbox portfolio that may be modified. Optional:
`IRP_TEST_GEOHAZ_VERSION` overrides the configured default,
`IRP_TEST_GEOHAZ_TIMEOUT_SECS` overrides 900 seconds, and
`IRP_TEST_GEOHAZ_CAPTURE_PATH` overrides `/tmp/rwb-geohaz-terminal.json`.

```bash
make shell
uv run pytest tests/irp --run-irp -k geohaz
```

- Submits one real lookup on a small sandbox portfolio, polls via the test
  (not the app), and **saves the terminal `get_geohaz_job` body** — confirm
  `tasks[].output.summary` remains the completion-summary field.
- Confirms Risk Modeler accepts the hazard-only layer list (no geocode layer —
  plan risk 2).

## Reporting

Name tiers and counts (e.g. "unit tier, N passed; SQL Server tier not run —
`linux-box` down"). The feature is **unverified** until steps 2 and 5 have run
on a real SQL Server / sandbox respectively.
