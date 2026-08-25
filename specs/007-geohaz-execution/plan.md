# Implementation Plan: GeoHaz Execution (Iteration 5)

**Branch**: `007-geohaz-execution` | **Date**: 2026-08-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/007-geohaz-execution/spec.md`

---

## Review

### Design summary

- The launch is a one-click CSRF-protected form post from the EDM summary page after the analyst checks portfolios. No modal opens. The route supplies the fixed P-02 parameters.
- The launch POST validates the gate (EDM + ≥1 portfolio) and P-06 eligibility, then enqueues **one `run_geohaz` `rwb_job` per selected portfolio** (`requestor_type='analyst_request'`, `requestor_id=irp_portfolio.id`) and dispatches. The response confirms immediately; each portfolio's column shows the in-line state from that moment (T-02, research R2).
- The Dramatiq worker (`app/workers/geohaz_jobs.py`) calls `irp_gateway.submit_geohaz(...)` — a new gateway method wrapping the wheel's `submit_geohaz_job` — then `irp_job_service.record_submitted_irp_job(...)` with the new `irp_portfolio_id` and `request_params` arguments. A submit exception writes a terminal `SUBMISSION FAILED` `irp_job` via `record_submission_failure`, isolated per portfolio (FR-006).
- The wheel resolves EDM and portfolio **by name** at submit time (`search_edms` → `search_portfolios`) — exactly Article 2's name-based coupling; the worker passes the stored `irp_edm.name` and `irp_portfolio.name`.
- FR-005 (never re-run geocoding): wheel 0.5.0 takes the layer list from the caller and inserts nothing — the app submits a **hazard-only layer list**, so no geocode layer ever reaches Risk Modeler (research R4; the 0.3.1 `skipPrevGeocoded` workaround is obsolete).
- Parameter mapping (research R5): the app always builds earthquake and windstorm hazard layers with `engineType="RL"`, the configured data version, `overrideUserDef=true`, and `skipPrevHazard=false`. DLM is recorded in the parameter set.
- Data version comes from a single config setting `HAZARD_DATA_VERSION` (default `25.0`) because the wheel has no version-discovery API and Risk Modeler does not accept a `"latest"` literal (research R6).
- The P-05 record lives **on the `irp_job` row** (T-03, research R3): `irp_job.irp_portfolio_id` (Uuid FK), `irp_job.request_params` (NVARCHAR(MAX) JSON), and `irp_job.completion_summary` (NVARCHAR(MAX)) join the existing analyst, timestamps, status, and terminal response columns.
- On terminal completion, the poller copies the captured `tasks[].output.summary` string into `completion_summary`; the most recent lookup details display the string as Result without parsing it (research R7). Missing summary text renders as unavailable.
- The poller gains one `_GETTERS` entry — `"geohaz": irp_gateway.get_geohaz_job` (single-status check). On successful completion, a terminal resolver reads Get Portfolio Metadata outside the database transaction and the terminal handler replaces the `metrics` portion of `irp_portfolio.exposure_detail` while retaining its DataBridge summary.
- The portfolios table gains **"Hazard Version"** as its final column (update `--cols`/`min-width` in `edm_portfolios_section.html`) with the in-line status for a non-terminal job and the stored raw `hazardVersion` otherwise; the expanded `<details>` row shows the most recent lookup in a column to the right of the exposure value lists.
- The column refreshes with a **per-cell** self-terminating poll: `GET .../geohaz-cell` emits `hx-trigger="every 3s"` only while a lookup is non-terminal (research R8) — the whole-body poll is wrong for this (its 204 open-rows guard, and it only runs during sync/import).
- Repeat launches: `SUBMISSION FAILED`, `FAILED`, `CANCELLED`, `FINISHED` are terminal, so the portfolio is launchable again (FR-007/FR-014); the `UNIQUE(requestor_type, requestor_id, rwb_job_type)` head + `ensure_pending_rwb_job` revive-or-noop is the mechanical double-submit backstop behind the P-06 form exclusion.
- Schema work is an edit to the single `alembic/versions/0001_initial.py` revision plus the `infra/scripts/seed_db.py` MERGE and the `tests/iteration1_mirror.py` mirror/seeds (one new `rwb_job_type_kind` row `run_geohaz`; the `geohaz` `irp_job_type_kind` row already exists).
- UI workflow: the one-click submit button is a derivative change to the existing selection form, so no rendered preview is needed.

### Decisions

| ID | Decision | Status | Source |
|---|---|---|---|
| T-02 | Submission is worker-side: one `run_geohaz` `rwb_job` per selected portfolio; the launch POST enqueues and confirms, the Dramatiq worker performs the Risk Modeler submit | Approved (plan) | R2 — the wheel's submit makes 3 RM reads before the POST (not sub-second), and every existing submit is worker-side; supersedes the spec's Key Entities "synchronously on the request path" wording (spec.md amended in this pass; user-visible behavior unchanged) |
| T-03 | The P-05 record is the geohaz `irp_job` row: `irp_portfolio_id`, `request_params`, and `completion_summary`; analyst = `inserted_by` | Approved (plan) | R3 — a dedicated lookup table would duplicate status, timestamps, and actor the job row already owns |
| T-04 | FR-005 is satisfied directly: the app submits a hazard-only layer list — no geocode layer is ever sent | Approved (plan) | R4 — wheel 0.5.0 takes the caller's layer list verbatim; the 0.3.1 forced-geocode workaround and its upstream follow-up are obsolete |
| T-05 | Data version is config-owned (`HAZARD_DATA_VERSION`, single value, default `25.0`); model family renders DLM-only (HD disabled pending O7-1) — `engineType` is caller-supplied but only `"RL"` is confirmed | Approved (plan) | R5/R6 — no discovery API, and Risk Modeler rejects the `"latest"` literal; the HD engineType value is unconfirmed |
| T-06 | Store the terminal body verbatim and copy `tasks[].output.summary` into `irp_job.completion_summary`; display the string without parsing it | Approved (plan) | R7 — captured Risk Modeler response supplied 2026-08-13 |
| T-07 | FR-006 recovery is relaunch: `SUBMISSION FAILED` is terminal so the portfolio is immediately launchable again; the auto-retry batch stays the existing no-op stub, and geohaz failure rows join it when it is implemented (per-entity dedup now possible via `irp_portfolio_id`) | Approved (plan) | R9 — the `_submission_retry` batch was never implemented (poller stub); building it is not this feature |

### Constitution check

No violations. No Complexity Tracking entries. Articles that shaped the design:

- **Article 11** — the deciding article. Request-path submission is *permitted* but declined (T-02): the geohaz submit is not the sub-second call the permission is predicated on. Submission, and any future summary fetch, run worker-side; the poller stays single-status (`get_geohaz_job`); `poll_*_to_completion` stays forbidden (the existing architecture-guard tests cover the new worker file).
- **Article 2** — coupling is name-based and live: the wheel resolves EDM/portfolio names at submit time; no stored handle, no stored sequence. The in-line state is derived in the query layer; the terminal value comes from the stored portfolio metadata snapshot.
- **Article 5** — hazard lookup is judgment: launched only by an explicit analyst click, never auto-fired; nothing auto-fires on its completion.
- **Article 3** — no new categorical columns: `geohaz` already exists in `irp_job_type_kind`; the one new kind row is `rwb_job_type_kind('run_geohaz')`; `request_params` is a JSON snapshot record (same rationale as `irp_portfolio.exposure_detail`), not a dispatch value.
- **Article 4** — no new status columns; `irp_job.status` stays an in-place update.
- **Articles 8/9** — Jinja2 + HTMX fragments, native `<details>`, Alpine for portfolio selection, and the existing `.dtable` tokens.
- **Article 12** — unit tier covers the peril validator, gate/eligibility, param mapping, column-state derivation, count parser, worker success/failure, and poller routing; SQL Server tier covers the migration via the schema-drift mirror; the opt-in IRP tier captures a real completion body.
- **Article 13** — the launch POST is CSRF-validated; no new secrets (`HAZARD_DATA_VERSION` is configuration, not a credential).

### DB lifecycle (WORKBENCH)

**Rebuild** — three new `irp_job` columns + one kind row edited into `0001_initial.py`; `EXPOSURE`/`LOSS` untouched; DATABRIDGE never in schema scope.

### Risks

1. **The terminal response field may change in a later wheel version** (T-06). The page renders "Summary unavailable" when `tasks[].output.summary` is absent; the opt-in sandbox test checks the active wheel.
2. **Whether Risk Modeler accepts a hazard-only job on a never-geocoded portfolio is unobserved** (T-04): the wheel submits any layer combination, but RM may fail a hazard lookup that has no geocode data to read. If so it surfaces as a per-portfolio `FAILED` — visible and relaunchable, and consistent with FR-005 (the app must not run geocoding for the analyst). T029 (`uv run pytest tests/irp --run-irp` inside `make shell`) is the pre-merge gate that closes this risk; until it runs against the active wheel, the hazard-only submit is unobserved.
3. The wheel hard-fails a submit when the portfolio has zero accounts or zero locations (its own pre-validation) — that surfaces as a per-portfolio `SUBMISSION FAILED`, which is the intended FR-006/FR-014 path, not a special case.

---

## Technical Context

**Language/Version**: Python 3.12 (inherited; `requires-python = ">=3.12"`).

**Primary Dependencies** (existing, reused — no new dependency):
- `fastapi` + `jinja2` + HTMX (Alpine.js for portfolio selection) — server-rendered (Article 8).
- `dramatiq[redis]` + `redis` — the worker tier that performs the Risk Modeler submit; `rwb_job` stays the queue of record (Article 10).
- `sqlalchemy>=2.0` (Core) + `pyodbc`, `alembic` — WORKBENCH schema via `db/`; single `0001_initial.py` revision.
- **`irp-integration[databridge]` — active source TestPyPI `0.5.0`** (`pyproject.toml` `default-groups = ["dev", "irp-testpypi"]`; `uv.lock`). Geohaz surface confirmed against 0.5.0: `client.portfolio.submit_geohaz_job(portfolio_name, edm_name, layers) -> (job_id, request_body)` — the caller builds the full layer list, any geocode/hazard combination — and `client.portfolio.get_geohaz_job(job_id) -> dict` (research R1). Reached only through `app/services/irp_gateway.py`.

**Storage**: SQL Server 2022, **WORKBENCH connection only**. Delta: `irp_job.irp_portfolio_id` (Uuid FK → `irp_portfolio.id`, nullable, indexed), `irp_job.request_params` (NVARCHAR(MAX) JSON, nullable), `irp_job.completion_summary` (NVARCHAR(MAX), nullable), and one `rwb_job_type_kind` seed row (`run_geohaz`). No EXPOSURE/LOSS access; DATABRIDGE untouched.

**Testing** (Article 12, three tiers):
- `uv run pytest tests/unit` — SQLite via `register_engine` + `FakeIRP` extended with the gateway methods `submit_geohaz`/`get_geohaz_job`: peril/eligibility/gate validators, per-portfolio enqueue, worker submit success + failure isolation, param mapping (one hazard layer per peril; no geocode layer ever built), live-status and stored-version display, terminal metadata refresh, count parser (counts / zero / missing → unavailable), poller `_GETTERS` routing, cell-fragment trigger emission.
- `make test-sql` — migration drift: the three new columns + kind row mirrored in `tests/iteration1_mirror.py` (`EXACT_MATCH_TABLES` enforces column-for-column).
- `make shell` + `uv run pytest tests/irp --run-irp` — opt-in sandbox: one real geohaz round trip; **captures the terminal `get_geohaz_job` body** (finalizes the R7 parser) and confirms Risk Modeler accepts the hazard-only layer list (plan risk 2); the existing `poll_*_to_completion` guard scans cover the new files.

**Target Platform**: Linux server (WSL2/Docker dev: uvicorn + poller + Dramatiq worker + Redis + SQL Server).

**Project Type**: Server-rendered web app with two out-of-process background components (poller, Dramatiq worker). Extends the existing `app/` tree.

**Performance Goals**: the EDM summary page renders the status and most recent lookup from stored rows only — no Risk Modeler call on any request path (FR-013/FR-020); per-cell polls are single-row reads on the indexed `irp_portfolio_id`. Submission latency is worker-side and invisible to the request (< 1 poll interval + ~4 RM round trips per portfolio).

**Constraints**: Article 11 discipline as above; `db.execute` safe path only (no trusted scripts); CSRF on the launch POST; P-06 enforced server-side at launch time with the `rwb_job` unique head as the race backstop; `hazardVersion` is display-only and never gates an action (FR-013).

**Scale/Scope**: 1–25 portfolios per EDM; a launch enqueues ≤ 25 rwb_jobs; concurrent non-terminal geohaz jobs per EDM ≤ portfolio count, so per-cell polling stays a handful of 3s single-row GETs.

---

## Project Structure

### Documentation (this feature)

```text
specs/007-geohaz-execution/
├── plan.md              ← this file
├── research.md          ← Phase 0 (R1–R9)
├── data-model.md        ← Phase 1 (irp_job delta; request_params shape; state derivation)
├── quickstart.md        ← Phase 1 (rebuild + tests + walkthrough + sandbox capture)
├── contracts/           ← Phase 1
│   ├── http-routes.md    ← launch POST, cell GET
│   ├── worker-poller.md  ← run_geohaz worker, poller getter, failure paths
│   └── data-access.md    ← geohaz_service read/write contract
├── checklists/
│   └── requirements.md   ← spec quality checklist (from /speckit-specify)
└── tasks.md             ← Phase 2 (/speckit-tasks — not created here)
```

### Source code (changed directories only)

```text
alembic/versions/0001_initial.py     # EDIT: irp_job.irp_portfolio_id (+FK after irp_portfolio create, +index),
                                     #       irp_job.request_params; rwb_job_type_kind ('run_geohaz','Run GeoHaz',28)
infra/scripts/seed_db.py             # EDIT: run_geohaz MERGE row
infra/.env.example                   # EDIT: HAZARD_DATA_VERSION
tests/iteration1_mirror.py           # EDIT: mirror columns + seed constants (drift guard)

app/config.py                        # EDIT: hazard_data_version setting
app/services/irp_gateway.py          # EDIT: submit_geohaz + get_geohaz_job (Protocol, _RealGateway,
                                     #       free functions, __all__)
app/services/irp_job_service.py      # EDIT: irp_portfolio_id + request_params on the writers;
                                     #       latest-lookup/state queries may live here or in geohaz_service
app/services/geohaz_service.py       # NEW: gate + P-06 eligibility, launch (validate, enqueue per portfolio),
                                     #      column-state + latest-lookup read models, summary parser
app/services/edm_service.py          # EDIT: get_edm_detail attaches per-portfolio geohaz cell state + latest
                                     #       lookup (both detail routes render from this one read model)
app/workers/geohaz_jobs.py           # NEW: run_geohaz body + actor + _BODIES (auto-discovered by loader)
app/poller/run.py                    # EDIT: _GETTERS["geohaz"]
app/routers/edms.py                  # EDIT: POST /edms/{edm_id}/geohaz               (one-click launch)
                                     #       GET  /edms/{edm_id}/portfolios/{pid}/geohaz-cell
app/templates/partials/
├── edm_portfolios_section.html      # EDIT: column header + --cols/min-width; selection form + launch button
├── portfolio_row.html               # EDIT: checkbox cell, geohaz cell include, latest lookup details column
├── geohaz_cell.html                 # NEW: the self-terminating status cell fragment
├── geohaz_checkbox.html             # NEW: the per-portfolio selection checkbox (oob-swapped by the cell fragment)
└── geohaz_details.html              # NEW: the most recent lookup's details column (oob-swapped by the cell fragment)
app/static/js/app.js                 # EDIT: portfolio-selection component
app/static/css/                     # EDIT: status and latest-details styling via tokens if needed

tests/unit/…                         # NEW: test_geohaz_service, test_run_geohaz_worker, test_geohaz_parser,
                                     #      poller routing + fake extensions
tests/irp/…                          # NEW: opt-in sandbox round trip + completion-body capture
```

**Structure decision**: extends the existing single-project `app/` tree; no new process, no new queue, no new nav node (`/edms/{id}/geohaz*` resolves to the existing `irp.edm_library` nav key).

## Complexity Tracking

No constitution violations — table not needed.
