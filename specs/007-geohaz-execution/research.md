# Research: GeoHaz Execution (Iteration 5)

Evidence and rejected alternatives behind the plan's T-02…T-07 decisions. File:line
references are against the working tree on 2026-08-13 and the `irp-integration`
0.5.0 TestPyPI wheel.

## R1 — Active wheel and the geohaz surface

**Decision**: Implement against `irp-integration` 0.5.0 (TestPyPI), reaching only
`client.portfolio.submit_geohaz_job` and `client.portfolio.get_geohaz_job` through
`app/services/irp_gateway.py`.

**Evidence**:
- Active source is TestPyPI 0.5.0: `pyproject.toml:71` (`default-groups = ["dev", "irp-testpypi"]`),
  `uv.lock` (`version = "0.5.0"`, `source = { registry = "https://test.pypi.org/simple/" }`).
  The comment on `pyproject.toml:70` claims PyPI is the committed default — the committed value is
  `irp-testpypi`. The local checkout at `../irp-integration` is NOT wired (`pyproject.toml:96` commented out).
- 0.5.0 reworked the geohaz submit (upstream commit "Update geohaz to accommodate RM API options
  directly without built-in defaults"): the caller passes the full layer list; the wheel no longer
  builds layers or forces a geocode layer. Earlier research against 0.3.1 is superseded.
- Signatures (wheel `irp_integration/portfolio.py`):
  - `submit_geohaz_job(portfolio_name, edm_name, layers: List[Dict]) -> Tuple[int, Dict]` (`:1023-1118`).
    `layers` is a non-empty list of geocode and/or hazard layer dicts, any combination, order
    preserved, validated by `validate_geohaz_layers` (`validators.py:206-273`): every layer needs
    `type` (`"geocode"`/`"hazard"`), `name`, `engineType`, `version`, `layerOptions`; geocode options
    `geoLicenseType` (str), `aggregateTriggerEnabled` (bool), `skipPrevGeocoded` (bool); hazard
    options `overrideUserDef` (bool), `skipPrevHazard` (bool). Extra fields pass through.
    Resolves names itself: `search_edms` → `search_portfolios` → `search_accounts_by_portfolio`
    (`:1063-1097`), raising `IRPAPIError` on zero accounts or zero locations; then
    `POST /platform/geohaz/v1/jobs`; job id from the `Location` header. Returns `(job_id, request_body)` —
    **the request body's `resourceUri` is the value for `irp_job_resource`** (RM's completion response
    omits it, same as analysis jobs).
  - `get_geohaz_job(job_id) -> Dict` (`:1121-1141`) — returns `response.json()` verbatim; the only
    keys the wheel itself ever relies on for geohaz are `status` and `progress`.
  - Endpoints: `constants.py` (`/platform/geohaz/v1/jobs`). Terminal vocabulary:
    `FINISHED / FAILED / CANCELLED`.
- Canonical layer values from the package's own tests (`tests/test_geohaz_submission.py`): hazard
  layers named `"earthquake"` / `"windstorm"`, `engineType="RL"`; a hazard-only list submits with no
  geocode layer inserted.
- The batch helper `submit_geohaz_jobs(list)` (`:979-1020`) now takes per-portfolio layer lists but
  still fail-fasts — one bad portfolio aborts the rest and drops the already-submitted job ids,
  violating FR-006. Still rejected.
- `poll_geohaz_job_to_completion` / `poll_geohaz_job_batch_to_completion` (`:1144`+) exist and
  are forbidden (Article 11); the source-scan guards in `tests/unit/test_architecture_guards.py:55-71`
  and `tests/irp/test_article11_guard.py:32-51` already grep the worker/poller/gateway trees.

## R2 — Submission runs worker-side (plan T-02)

**Decision**: The launch POST enqueues one `run_geohaz` `rwb_job` per selected portfolio
(`requestor_type='analyst_request'`, `requestor_id=irp_portfolio.id`) and dispatches; the Dramatiq
worker performs the Risk Modeler submit and writes the `irp_job` row.

**Rationale**:
- Article 11's request-path permission is predicated on a sub-second submit. The geohaz submit makes
  three Risk Modeler reads before its POST (R1) — a five-portfolio launch would hold the HTTP request
  through ~20 round trips.
- Every existing submit already runs worker-side: `irp_gateway.submit_*` is called only from
  `app/workers/package_jobs.py` (`:97`, `:169`, `:648`); `docs/DATA_MODEL.md:498` records the standing
  choice ("no Risk Modeler call on the request path").
- One rwb_job per portfolio gives FR-006's partial-failure isolation for free, and the
  `UNIQUE(requestor_type, requestor_id, rwb_job_type)` head (`0001_initial.py:425-426`) +
  `ensure_pending_rwb_job` revive-or-noop is a mechanical backstop behind the P-06 form exclusion:
  two racing launches cannot double-enqueue the same portfolio.
- User-visible behavior matches the spec: the analyst submits once, gets confirmation in the same
  interaction, and the column shows the in-line state (queued → RM statuses) from that moment.

**Rejected**:
- *Request-path loop over `submit_geohaz_job`* — multi-second request, diverges from every existing
  submit path, and a browser timeout mid-loop leaves no retryable record for the unattempted tail.
- *The wheel's batch `submit_geohaz_jobs`* — see R1; fail-fast and lossy.

The spec's Key Entities sentence ("submitted synchronously on the request path") was amended in this
pass to defer submission mechanics to this decision.

## R3 — The P-05 record is the `irp_job` row (plan T-03)

**Decision**: No new table. Add `irp_job.irp_portfolio_id` (Uuid FK → `irp_portfolio.id`, nullable,
indexed) and `irp_job.request_params` (NVARCHAR(MAX) JSON — the analyst-level parameter set, written
on both submitted and `SUBMISSION FAILED` rows). The remaining P-05 fields already exist on the row:
`inserted_by` (launching analyst — the writers already take `actor_id`, `irp_job_service.py:27-56`),
`submitted_at`/`completed_at`, `status`, and `last_completion_result` (the poller's `update_tracking`
stores the terminal body, `irp_job_service.py:111-128`).

**Rationale**: a lookup and a geohaz `irp_job` are one-to-one, including failed submissions
(`record_submission_failure` inserts a terminal row per attempt, `irp_job_service.py:131-154`). A
dedicated table would duplicate status, timestamps, and actor, and need its own writer transaction.
`docs/DATA_MODEL.md:470` already assigns geohaz jobs an `irp_portfolio_id` — the column was deferred
at Iteration 2 because `irp_portfolio` didn't exist yet (`0001_initial.py:333-334`); it exists now.

**Rejected**: a `geohaz_lookup` table (duplication, second writer); deriving display parameters from
`last_submission_payload` (parsing the vendor wire body for UI is brittle, and failed submissions
need the same display shape).

**Migration note**: `irp_portfolio` (`0001_initial.py:501`) is created after `irp_job` (`:335`) — add
the column in the `irp_job` create and the FK via `op.create_foreign_key` after `irp_portfolio`
exists (or reorder). Mirror in `tests/iteration1_mirror.py` (`EXACT_MATCH_TABLES` drift guard).

## R4 — FR-005: hazard-only layer list, no geocode layer (plan T-04)

**Finding**: wheel 0.3.1 forced a `geocode` layer into every payload; 0.5.0 removed that — the
caller supplies the layer list and any combination submits (verified by the package's own test:
a hazard-only list posts with no geocode layer inserted).

**Decision**: the app never sends a geocode layer. Every submit is a hazard-only layer list — one
hazard layer per selected peril — so geocoding cannot run and broker geocoding is preserved
directly. The launch form offers no geocoding option (FR-005). The earlier 0.3.1 workaround
(`skipPrevGeocoded=True` on the forced layer) and its upstream follow-up are obsolete: the
follow-up landed as the 0.5.0 rework.

## R5 — Parameter mapping (plan T-05)

The app builds the layer list; the wheel validates shape and passes it through (R1).

| Form field (P-02) | Wire (wheel 0.5.0) | Notes |
|---|---|---|
| Perils: earthquake, windstorm (both default on; ≥1 required) | One hazard layer per peril: `{"type": "hazard", "name": "earthquake"/"windstorm", "engineType": "RL", "version": <data_version>, "layerOptions": {...}}` | The wheel accepts any non-empty layer list — the ≥1-peril rule is enforced app-side, form and server. Layer names match the `request_params.perils` tokens. |
| Skip locations with previous hazard lookup (default off) | `layerOptions.skipPrevHazard` on every hazard layer | Independent checkbox boolean. |
| Overwrite user-defined hazard values (default on) | `layerOptions.overrideUserDef` on every hazard layer | Independent checkbox boolean. Both layer option keys are required by `validate_geohaz_layers`. |
| Data version: latest (default) | `version` on every layer, literal `"latest"` | Per-layer on the wire; every launch sends the same value. See R6. |
| Model family: DLM (default) | `engineType` on every layer — `"RL"` (the DLM engine, the only value observed) | Rendered DLM-only with HD disabled (O7-1 open — the HD engineType value is unconfirmed); recorded in `request_params` so the P-05 record answers "what ran". Now caller-supplied, so enabling HD later is a form + mapping change, no wheel change. |

## R6 — Data-version discovery (plan T-05)

**Finding**: no wheel API enumerates geohaz data versions (`reference_data.py` covers model profiles,
event-rate schemes, currency vintages — nothing for geohaz versions). 0.5.0 has no default either —
`version` is a required field on every layer. The PRD says v25 is current (§10B.2, 2026-08). Risk
Modeler documentation confirms `version` also accepts the literal string `"latest"`, which it resolves
server-side to the current data version.

**Decision (2026-08-19, reverses the 2026-08-13 decision below)**: every launch sends the literal
`"latest"`. No config setting, no per-environment value to keep current as Moody's ships new data
versions.

**Original decision (2026-08-13, superseded)**: a config setting `GEOHAZ_DATA_VERSIONS`
(comma-separated, newest first; the first entry was the form default), surfaced in
`infra/.env.example` and `app/config.py`. Rejected then: a kind table (external Moody's vocabulary —
seed migration per RM release, the exact churn Article 3's carve-out exists to avoid); hardcoding a
version number in the template (no way to correct without a deploy); free-text input (typo →
submission failure discovered a poll cycle later). This traded a config-drift risk (a stale version
number surfacing as `SUBMISSION FAILED`/`FAILED`) for one manual step per Moody's release — the
`"latest"` literal removes that trade entirely.

## R7 — Completion summary: store the Risk Modeler string (plan T-06)

**Finding**: a captured Risk Modeler response contains the display text at
`tasks[].output.summary`. The value is one sentence describing every requested layer, its version,
the number of locations processed, and the total location count. `details.summary` only reports
`GEOHAZ is successful`.

**Decision**: when the poller receives a terminal geohaz response, it copies
`tasks[].output.summary` into `irp_job.completion_summary`. The display renders the stored string
without parsing counts or sentences. The poller continues storing the full terminal response in
`last_completion_result` for operational diagnosis.

## R8 — Column refresh: per-cell self-terminating poll (plan T-08 in prose; UI contract)

**Decision**: each portfolio's "Hazard Version" cell is a fragment
(`GET /edms/{edm_id}/portfolios/{pid}/geohaz-cell`) that emits `hx-trigger="every 3s"` only while
that portfolio has a non-terminal lookup — the established self-terminating pattern
(`edm_detail_body.html:14-20`, `library_table.html:17-21`).

**Rejected**:
- *Ride the whole-body poll* — it only runs while `edm.sync_running`/import states, and its 204
  open-`<details>` guard (`edms.py:245-250`) exists precisely because a body swap collapses expanded
  rows; geohaz status lives inside those rows.
- *One table-wide poll with `hx-swap-oob` cell updates* — fewer requests, but a new pattern with no
  precedent in the app; per-cell polling is bounded by P-06 (≤ one non-terminal lookup per portfolio,
  ≤ 25 portfolios per EDM).

## R9 — FR-006 recovery without the retry batch (plan T-07)

**Finding**: the poller's `_submission_retry` is a no-op stub (`app/poller/run.py:300-306`,
"implemented in US6" — descoped in Iteration 2 and never built); `IRP_SUBMISSION_MAX_RETRIES` is
unset in `infra/.env.example:134`.

**Decision**: recovery is relaunch. `SUBMISSION FAILED` is terminal (`irp_job_service.py:89`), so
P-06 does not exclude the portfolio and the analyst launches again immediately; the failed attempt
stays visible in the history (FR-014). Geohaz failure rows are standard `SUBMISSION FAILED` rows —
now keyed per entity via `irp_portfolio_id`, which is exactly the per-entity dedup the batch's own
docstring demands (`irp_job_service.py:140-145`) — so they join the automatic batch whenever it is
implemented. Implementing the batch is not this feature.
