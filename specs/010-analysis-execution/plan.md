# Implementation Plan: Analysis Execution — Suite & Single-Template Runs (Iteration 7)

**Branch**: `010-analysis-execution` | **Date**: 2026-08-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/010-analysis-execution/spec.md`

## Review summary

**What changes in the system.** The portfolio table on the EDM detail page gains
multi-select and Execute Suite / Execute Template buttons. Submit persists the approved
run as JSON on a new `execute_analysis_batch` rwb_job and returns immediately; a Dramatiq
worker loops `submit_portfolio_analysis_job` once per plan item (portfolio × selected
template of each chosen suite — no dedup across suites), writing an `irp_analysis` row
and an `analysis`-type `irp_job` per item. The poller gains the
`analysis` getter/handler, a completion backfill worker, and the (until now scaffolded)
submission-retry batch. A user-executed analyses section joins the EDM detail body and
updates through the page's existing 3s self-poll. The loss phase adds the
`retrieve_analysis_results` worker, Parquet files plus an `analysis_result_meta` summary
row per (analysis, perspective), and a loss-numbers fragment on the analysis rows.

**Design summary** (details: [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/)):

- Background submit (P-11): plan composed once at POST, persisted in `rwb_job.input_data`,
  executed by a worker that reads nothing else — the 005-breakout approved-plan pattern.
  One rwb_job per execution (`requestor_id` = fresh execution UUID), resumable per item
  after a reclaim (T-01).
- Loop the single submit call — the wheel's batch helper discards `request_body`
  (losing `resourceUri`, needed by every result getter) and cannot carry currency (T-02).
- Currency block always explicit, taken from the modal's confirmed per-suite selection
  (P-15) with `asOfDate` from the chosen vintage's `effective_date`; the wheel's silent
  USD default is exactly what FR-006 forbids (T-03). Pickers pre-fill from three pinned
  env-var defaults (`DEFAULT_ANALYSIS_CURRENCY_CODE` / `_SCHEME` / `_VINTAGE`, T-19);
  an unset or cache-absent default pre-selects nothing (FR-020).
- Submission tag (FR-021, T-20): plan composition appends the submission's name to every
  item's `tag_names` when the execution has a submission context; RM resolves/creates the
  tag at submit. Workbench-side the association is the existing
  `requested_from_submission_id` + plan `submission_id` — no new column.
- Naming: `CRE_{portfolio}_{template}`; `irp_analysis.name` = the ≤64-char name sent to
  RM, new `full_name` = the untruncated name; local collision check + `_2`, `_3`…
  suffix, `skip_duplicate_check=True` (T-04/T-05).
- `irp_analysis` reshaped for own analyses: `rdm_id`/`source_rdm_name`/`irp_id` become
  nullable, origin CHECK added, new `full_name`, `irp_portfolio_id`,
  `analysis_template_id`, `execution_id`, `failure_reason` (T-06).
- `irp_job` gains `irp_portfolio_id` (reconciling DATA_MODEL §8), `irp_analysis_id`
  (status join + retry key), `request_params` (retry resubmits from it verbatim) (T-07/T-09).
- Poller: `_GETTERS["analysis"] = get_analysis_job`; terminal handler stores RM's failure
  reason on the analysis, enqueues `finalize_analysis` on FINISHED (T-08/T-10).
- `_submission_retry` implemented: per-analysis newest `SUBMISSION FAILED` row, exponential
  backoff, update-in-place; `IRP_SUBMISSION_MAX_RETRIES` default becomes 3 (T-09).
- Live updates are the existing HTMX 3s body self-poll — no SSE exists in the app; FR-014's
  "same treatment as import jobs" is the poll (T-11).
- `/workflows/irp-jobs` stub gets a minimal read-only `irp_job` listing so analysis jobs
  are visible (FR-014) — approved, delivered as the iteration's final phase (T-12).
- Loss phase per DATA_MODEL §9: Parquet row data + `analysis_result_meta` summary per
  (analysis, perspective); path keyed by analysis id; empty perspectives are data, not
  errors; PLT for HD only; `pyarrow` added (T-13/T-14/T-15).
- Treaty pass-through: existing RM datasource link + focus-triggered re-sync on return;
  no job (T-16, P-08).
- `execute_analysis_batch` actor `time_limit` raised to 1h; no extra rate limiter — the
  sequential loop is the throttle (T-17/T-18).
- Delivery follows P-09: phase 1 suite execution + tracking (US1+US2), phase 2 single
  templates (US3), phase 3 loss retrieval (US4), phase 4 the job-monitor listing (T-12).
  UI-first previews for the modal, the user-executed section, and the loss fragment
  before building.
- Retrieval/backfill failure handling is the standard rwb_job actor pattern
  (`max_retries=0`, failure → `failed` + `error_detail`, reconciler recovers
  interruption); the P-14 backoff retry and retrieval-failed display are deferred.

**Risk.** The wheel moves: signatures in
[contracts/irp-gateway.md](contracts/irp-gateway.md) must be re-confirmed against the
active wheel at implementation; the IRP-sandbox tier is the proof.

**Decisions**: T-01…T-20 in [research.md](research.md) — all Approved. No open items.

## Technical Context

**Language/Version**: Python ≥3.12

**Primary Dependencies**: FastAPI + Jinja2 + HTMX 2 + Alpine.js (server-rendered, no SPA);
Dramatiq[redis]; `irp-integration==0.6.0` (TestPyPI, source-switchable); SQLAlchemy
Core via the `/db` package (pyodbc, ODBC 18); `pyarrow` (new, loss phase)

**Storage**: SQL Server — `rwb_workbench` (Alembic single revision, drop-create-seed);
Parquet files under `OUTPUTS_BASE_DIR` (loss phase). `rwb_exposure`/`rwb_loss` untouched.
DATABRIDGE untouched (results are REST-only, PRD §15.3).

**Testing**: `uv run pytest tests/unit` (SQLite via `register_engine`, FakeIRP);
`make test-sql`; `make shell` + `uv run pytest tests/irp --run-irp`

**Target Platform**: Linux server (nginx + uvicorn + poller + Dramatiq workers in
`linux-box`; SQL Server 2022 separate)

**Project Type**: Single FastAPI web app + standalone poller process + Dramatiq worker
process

**Performance Goals**: a 150-analysis run submits completely unattended (SC-005); each
submit is ~6 RM calls, so the batch runs minutes in the worker — hence P-11 background
submit and the 1h actor time limit; EDM page stays on the 3s poll with a 204 escape hatch

**Constraints**: RM analysis-name cap 64 chars (truncate right, P-05); no second retry
layer around the wheel's built-in 429/5xx retry; `poll_*_to_completion` forbidden;
worker-only IRP polling/results (Article 11)

**Scale/Scope**: runs up to ~150 analyses × 3 perspectives; one EDM page section, one
modal, one fragment, three workers, one poller job type

## Constitution Check

*Gate: pass before Phase 0; re-checked after Phase 1 design.* **No violations.**
Articles that shaped the design:

- **Article 2 (sequencing derived, not stored)** — no run/batch table: a run is its
  execution UUID on the jobs it created; submit resolves names, but completion is
  ID-based (the backfill uses the `analysisId` the poller extracts from the FINISHED
  job body — names are display-only). The plan JSON is an approved input snapshot, not
  a stored DAG.
- **Article 3 (kind tables; external-status carve-out)** — new categoricals are kind
  tables (`execute_analysis_batch`/`finalize_analysis` rows in
  `rwb_job_type_kind`; new `analysis_perspective_kind`); RM's job vocabulary stays only
  on the carved-out `irp_job.status`; `irp_analysis_status_kind` deliberately not widened
  (T-07).
- **Article 5 (judgment waits for a click)** — execution is click-gated in the modal;
  backfill and loss retrieval auto-fire as mechanical follow-ups of that one intent.
- **Article 10/11 (SQL queue; worker-side IRP)** — heartbeat + reconciler carry FR-015
  unchanged; submission runs in the worker (permitted — the request path only persists
  the plan); poller uses single-status `get_analysis_job`; retry is the single-threaded
  poller batch, not an actor; result getters are worker-only.
- **Article 8 (server-rendered)** — modal, section, and loss fragment are Jinja partials
  over HTMX; Alpine only for checkbox counts, modal shell, and the focus-triggered treaty
  re-sync.
- **Articles 6, 7, 9, 12, 13** — no row scoping anywhere (US2-6); all SQL via
  `db.execute`; tokens only; tests across the three tiers including the validators, gate,
  and claim/heartbeat/reconciler machinery; CSRF on the execute POST.
- **AGENTS.md rule 8 / FR-012 (approved plans are immutable)** — the worker and the retry
  batch never re-read templates, suites, treaties, or names at execution time; first
  submit uses the plan snapshot, retries use `irp_job.request_params`.

## Project Structure

### Documentation (this feature)

```text
specs/010-analysis-execution/
├── plan.md              # This file
├── research.md          # T-01…T-20 with evidence and rejected alternatives
├── data-model.md        # irp_analysis reshape, irp_job columns, analysis_result_meta, seeds
├── quickstart.md        # Per-phase verification
├── contracts/
│   ├── routes.md        # Modal, execute POST, section, loss fragment, job listing
│   ├── worker-poller.md # Plan JSON, batch worker, poller handler, backfill, retrieval, retry
│   └── irp-gateway.md   # New gateway functions + FakeIRP additions
└── tasks.md             # /speckit-tasks output (not created by /speckit-plan)
```

### Source Code (changed directories only)

```text
app/
├── routers/edms.py                  # execute modal GET/POST (both variants), section context
├── routers/shell.py                 # /workflows/irp-jobs listing (T-12)
├── routers/analyses.py              # loss fragment (loss phase, new)
├── services/analysis_execution_service.py  # gate, name rule, plan compose/persist (new)
├── services/analysis_service.py     # user-executed read model (+ loss read, loss phase)
├── services/irp_job_service.py      # linkage columns, retry helpers
├── services/irp_gateway.py          # submit/get/backfill/result functions
├── workers/analysis_jobs.py         # execute_analysis_batch, finalize_analysis,
│                                    # retrieve_analysis_results (new)
├── workers/runtime.py               # TimeLimitExceeded handling (007-branch fix)
├── poller/run.py                    # analysis getter/handler, _submission_retry
├── config.py                        # retry defaults, retry base secs, currency defaults
├── templates/partials/              # execute_analysis_modal, executed_analysis_row,
│                                    # analysis_losses, edm_detail_body edits,
│                                    # portfolio_row checkbox, irp_jobs_table
├── templates/pages/workflows_irp_jobs.html
└── static/js/app.js                 # portfolio picks / modal slivers

alembic/versions/0001_initial.py     # schema edits (data-model.md)
infra/scripts/seed_db.py             # kind seeds
docs/ui_previews/                    # execute_modal, executed section, loss fragment previews
docs/DATA_MODEL.md                   # §6/§8/§9 reconciliation
tests/unit/  tests/sqlserver/  tests/irp/
tests/iteration1_mirror.py           # SQLite DDL + seeds + drift-guard list
```

**Structure Decision**: existing single-app layout; no new top-level directories. New
files: one service, one worker module, one router, three partials, one page body.

## Complexity Tracking

No constitution violations to justify.
