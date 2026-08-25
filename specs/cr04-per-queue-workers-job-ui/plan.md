# Implementation Plan: Per-queue Dramatiq workers and job monitoring UI

**Branch**: `cr04-per-queue-workers-job-ui` | **Date**: 2026-08-25 | **Spec**: [spec.md](spec.md)

## Plan status

**Ready for tasks:** Yes
**Blocked by:** Nothing

## Design summary

- `app/workers/queues.py` (new) defines `rwb_actor`, a decorator that pins `queue_name` to the wrapped function's own name — every `@dramatiq.actor` call site in `app/workers/entity_jobs.py` becomes `@rwb_actor`, so no call site ever sets `queue_name` by hand.
- The same module exposes `queue_names()`, which runs `app.workers.loader.discover_jobs()` and reads the resulting list back off the broker's registered actors — never a hand-written list.
- `python -m app.workers.queues` prints that list, one name per line. `infra/scripts/start-all.sh`/`stop-all.sh` (dev) and `infra/scripts/rhel9/rhel9-start.sh`/`rhel9-stop.sh` (RHEL9) loop over it to start/stop one `dramatiq app.workers.entrypoint -Q <queue>` process per job type, each with its own PID file and log file.
- A new drain-check script polls `rwb_job` (`SELECT rwb_job_type, status_code, COUNT(*) ... WHERE status_code IN ('pending','running') GROUP BY ...`) until every queue is empty or a timeout elapses. `infra/scripts/rhel9/rhel9-ssh-deploy.sh` runs it before installing new code.
- No change to `claim_rwb_job`, `complete_rwb_job`, or the reconciler (`reconcile_stale_rwb_jobs`) — per-queue routing is entirely about which process claims a row, not how the claim itself works.
- A migration adds `cancelled` to `rwb_job_status_kind`.
- `rwb_job_service.py` gains `cancel_rwb_job(*, rwb_job_id)` — one guarded `UPDATE ... WHERE status_code = 'pending'`, same shape and same race-safety as the existing claim.
- A new read-only route + template lists every `rwb_job` row grouped by type and status, including `pending` rows shown with no start time. Per-row actions: Cancel (`pending` only, calls `cancel_rwb_job`) and Resubmit (`failed` only, calls the existing `ensure_pending_rwb_job` unchanged — same row, `attempt_count` incremented, prior `error_detail`/`output_data`/`completed_at` overwritten).
- Article 10 of the constitution is amended in the same change (text fixed in CR-004 §5.4) — it currently says "single worker by default"; this feature makes per-queue concurrency the exercised default.

## Material changes

| Area | Change |
|---|---|
| Database | Add `cancelled` to `rwb_job_status_kind` (migration). No other schema change. |
| Worker | New `app/workers/queues.py` (`rwb_actor`, `queue_names()`, CLI entry point). All four actors in `entity_jobs.py` re-decorated with `@rwb_actor`. No change to job bodies, `run_one`, `run_pending`, the claim query, or the reconciler. |
| UI | New read-only monitoring route + template listing `rwb_job` by type/status, with Cancel and Resubmit row actions. |
| Library | None — no new dependency. Uses Dramatiq's existing `-Q`/`--pid-file` CLI flags. |

## High-risk technical decisions

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | `rwb_actor` derives `queue_name` from the function name at decoration time; no actor ever passes `queue_name=` explicitly | Approved | `docs/CR/CR_04__PER_QUEUE_WORKERS.md` §3.2, §5.1 |
| T-02 | `queue_names()` is derived from the broker's registered actors after `discover_jobs()` runs, not a hand-written list | Approved | `docs/CR/CR_04__PER_QUEUE_WORKERS.md` §3.3, §5.2 |
| T-03 | One worker OS process per job type, not grouped by risk profile | Approved | `docs/CR/CR_04__PER_QUEUE_WORKERS.md` §3.4 |
| T-04 | Draining means stopping the worker process (SIGTERM), not a database pause flag | Approved | `docs/CR/CR_04__PER_QUEUE_WORKERS.md` §3.5 |
| T-05 | The drain check reads only `rwb_job`, not Dramatiq/Redis state | Approved | `docs/CR/CR_04__PER_QUEUE_WORKERS.md` §5.3 |
| T-06 | The claim query and reconciler are unchanged; per-queue routing does not touch them | Approved | `docs/CR/CR_04__PER_QUEUE_WORKERS.md` §3.7 |
| T-07 | A job stuck for hours has no in-app cancellation; the only remedy is killing its queue's worker process | Approved | `docs/CR/CR_04__PER_QUEUE_WORKERS.md` §3.10, §6 |
| T-08 | Cancel is a guarded `UPDATE ... WHERE status_code = 'pending'`, same race-safety pattern as `claim_rwb_job` | Approved | `docs/CR/CR_04a__JOB_MONITORING_UI.md` §3.3, §5.1 |
| T-09 | Resubmit calls the existing `ensure_pending_rwb_job` unchanged — same row reused, no history kept | Approved | `docs/CR/CR_04a__JOB_MONITORING_UI.md` §3.5, §5.2 |
| T-10 | No generic bespoke job-submission form, no UI drain trigger, no UI worker scaling, no priority queues | Approved | `docs/CR/CR_04a__JOB_MONITORING_UI.md` §3.6–§3.9 |
| T-11 | Worker start/stop scripts: extend the existing `nohup`+PID-file shape (dev's `start-all.sh`/`stop-all.sh`, RHEL9's `rhel9-start.sh`/`rhel9-stop.sh`) to loop per queue now. Systemd units for the worker (or for all four RHEL9 processes together) are separate, later work — not built by this feature. | Approved | `docs/CR/CR_04_DEV_PLAN.md` Decision A, option 1; owner sign-off 2026-08-25 |
| T-12 | `rhel9-ssh-deploy.sh` does not itself stop or start any process today (confirmed by reading the script — its final message states worker/poller restart is still manual); the new drain-check step assumes an operator has already stopped the worker processes before running the deploy | Assumed | `docs/CR/CR_04_DEV_PLAN.md` §8 |
| T-13 | `Makefile`'s `logs-worker` target tails one fixed `worker.log`; changed to require `QUEUE=<name>` and tail `worker-<queue>.log`, rather than a multi-file tail | Approved | `research.md#R2` |

---

## Technical Context

**New dependencies**: None. Uses Dramatiq's existing `-Q`, `--pid-file`, and `--worker-shutdown-timeout` CLI flags (all present in the installed `dramatiq[redis]` version — confirmed via `dramatiq --help`).
**Databases touched**: `WORKBENCH` only — the `rwb_job_status_kind` migration and all `rwb_job`/`cancel_rwb_job` reads/writes. `EXPOSURE`, `LOSS`, and DATABRIDGE are untouched.

## Constitution Check

*GATE: before Phase 0 research, re-checked after Phase 1 design.*

Reviewed against all 13 articles in `.specify/memory/constitution.md`: no violations.

Material interactions — where an article actively shapes this design:

- **Article 3 (Categoricals Are Kind Tables)**: the new `cancelled` value is added to the existing `rwb_job_status_kind` table, not a new enum literal — consistent with `rwb_job.status_code` already being a kind-table FK.
- **Article 4 (Status Is Event-Sourced Where It Earns It)**: `rwb_job.status_code` is explicitly listed as plain in-place update, not event-sourced (only `submission.status_code` is event-sourced). Cancel and resubmit both use plain `UPDATE`s, consistent with that.
- **Article 7 (One Data-Access Package)**: `cancel_rwb_job` and the monitoring page's reads go through `db.execute`/`execute_command`, the same safe bound-parameter path every other `rwb_job_service.py` function already uses. No trusted-script path involved.
- **Article 8 (Server-Rendered; No SPA)**: the monitoring page is a FastAPI + Jinja route with HTMX for the Cancel/Resubmit actions (partial row swap on success), not a client-side app. Needs a rendered HTML preview and approval before the template is built, per the UI workflow.
- **Article 10 (The SQL Table Is the Queue) — amended by this feature.** Current text says "single worker by default." This feature is the change that exercises the documented "concurrency-safe claim query" upgrade path and makes per-queue concurrency the new default text (CR-004 §5.4 has the exact replacement wording). The claim query itself is not modified — only the article's description of the default worker topology changes, from "one worker" to "one worker per queue."
- **Article 11 (IRP Polling and Result Work Behind an Interface)**: unaffected. This feature does not touch `irp_gateway`, the poller, or any IRP submission path — it only changes how `rwb_job` rows are dispatched to worker processes.
- **Article 12 (Test-First)**: the `rwb_job` claim/heartbeat/reconciler state machine is explicitly a required-test item; this feature adds `cancel_rwb_job` as a new state transition on the same state machine and extends the unit-tier tests accordingly (see Testing below). No SQL-Server-tier or IRP-tier test is added or changed.

## Project Structure

```text
app/workers/queues.py              # new — rwb_actor, queue_names(), CLI entry point
app/workers/entity_jobs.py         # @dramatiq.actor -> @rwb_actor on all four actors
app/workers/loader.py              # no functional change; test lives alongside discover_jobs()
app/services/rwb_job_service.py    # new: cancel_rwb_job()
app/routers/shell.py               # replace the workflows_rwb_jobs stub handler with the real GET/POST cancel/POST resubmit routes
app/templates/pages/workflows_rwb_jobs.html   # replace the placeholder body ("RWB jobs will appear here") with the real page
alembic/versions/                  # new migration: add 'cancelled' to rwb_job_status_kind
infra/scripts/start-all.sh         # loop over `python -m app.workers.queues`, one dramatiq process per queue
infra/scripts/stop-all.sh          # loop over the same list
infra/scripts/rhel9/rhel9-start.sh # same per-queue loop, RHEL9 nohup style
infra/scripts/rhel9/rhel9-stop.sh  # same per-queue loop
infra/scripts/rhel9/rhel9-drain-check.sh   # new
infra/scripts/rhel9/rhel9-ssh-deploy.sh    # add drain-check step before install/migrate
infra/.env.example                 # comment: RWB_WORKER_PROCESSES/THREADS now apply per queue
Makefile                           # logs-worker target shape (pending T-13)
docs/RHEL9_DEPLOYMENT.md           # remove two resolved open items
docs/SCAFFOLDING.md                # "same five processes" line no longer holds
.specify/memory/constitution.md    # Article 10 replacement text (CR-004 §5.4)
tests/unit/test_rwb_job_queue.py   # extend: queue_name == actor_name; queue_names() contents; cancel_rwb_job
```

No change to `app/nav/manifest.py` — `workflows.rwb_jobs` already exists as a nav node under the Workflows rail root; this feature fills its existing stub route/template rather than adding a new one.

## Complexity Tracking

*No Constitution Check violation to justify — table intentionally empty.*

## Testing

- **Unit**: every actor `discover_jobs()` registers has `queue_name == actor_name`; `queue_names()` returns the four current names; `cancel_rwb_job` succeeds only against a `pending` row and is a no-op (rowcount 0) against every other status; a claim racing a cancel resolves to exactly one winner with no error; resubmitting a `failed` row via `ensure_pending_rwb_job` still produces the documented same-row reset (regression check, not new behavior).
- **SQL Server integration**: not touched by this feature — no new migration behavior beyond a straightforward kind-table seed value, already covered by the existing kind-table seed assertions.
- **IRP sandbox**: N/A — no IRP-facing code changes.
