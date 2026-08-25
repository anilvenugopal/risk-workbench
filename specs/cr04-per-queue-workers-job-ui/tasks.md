# Tasks: Per-queue Dramatiq workers and job monitoring UI

**Input**: Design documents from `/specs/cr04-per-queue-workers-job-ui/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/job-monitoring-routes.md, quickstart.md — all present.

**Tests**: Included. Article 12 requires unit-tier coverage of the `rwb_job` claim/heartbeat/reconciler state machine, and this feature adds a new state transition (`cancelled`) to it.

**Organization**: Tasks are grouped by user story (US1 = per-queue worker isolation, US2 = job monitoring UI), matching spec.md.

**Success Criteria**: SC-001–SC-005 are outcome statements, not implementation targets — no task cites them by ID. They are validated collectively by quickstart.md (SC-001/SC-002 by steps 2–3, SC-003 by step 4, SC-004/SC-005 by step 5), exercised end to end by T026.

## Path Conventions

Existing FastAPI + Jinja2 + HTMX app. `app/workers/`, `app/services/`, `app/routes/`, `app/templates/`, `infra/scripts/`, `alembic/versions/`, `tests/unit/` at repository root — per plan.md's Project Structure.

---

## Phase 1: Setup

No new project scaffolding needed — this feature extends an existing app. Nothing to do in this phase.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The queue-name derivation mechanism both user stories' scripts and tests depend on.

**⚠️ CRITICAL**: US1's script tasks and US2's tests both need `app/workers/queues.py` to exist first.

- [ ] T001 [FR-001] [FR-003] [T-01] [T-02] Create `app/workers/queues.py` with `rwb_actor(fn=None, **kwargs)` (a decorator wrapping `dramatiq.actor`, setting `queue_name=f.__name__`) and `queue_names()` (calls `app.workers.loader.discover_jobs()`, returns `sorted(dramatiq.get_broker().actors.keys())`), plus a `__main__` block printing one name per line
  - Proof: `python -m app.workers.queues` run from a shell with the venv active prints exactly `backfill_edm_detail`, `backfill_rdm_analyses`, `upload_edm`, `upload_rdm`, one per line, sorted
- [ ] T002 [FR-001] [T-01] Replace `@dramatiq.actor(max_retries=0)` with `@rwb_actor(max_retries=0)` on `upload_edm`, `upload_rdm`, `backfill_rdm_analyses`, `backfill_edm_detail` in `app/workers/entity_jobs.py`; update the module's `import dramatiq` line only if nothing else in the file still needs it directly (check `_ = broker.redis_broker` and any other bare `dramatiq.*` reference first)
  - Proof: T003's test passes
- [ ] T003 [P] [T-01] [T-02] Add `test_every_actor_queue_name_matches_actor_name` and `test_queue_names_returns_current_actors` to `tests/unit/test_rwb_job_queue.py` (or a new `tests/unit/test_worker_queues.py` if that file's existing fixtures don't fit) — call `loader.discover_jobs()` directly, assert `actor.queue_name == name` for every entry in `dramatiq.get_broker().actors.items()`, and assert `queue_names() == ["backfill_edm_detail", "backfill_rdm_analyses", "upload_edm", "upload_rdm"]`
  - Proof: confirmed runnable with no live Redis — `RedisBroker(url=...)` only constructs a lazy `redis.Redis` client (no socket opened at construction), and neither `discover_jobs()` nor `queue_names()` sends a Redis command; `discover_jobs()`'s repeated `importlib.import_module` calls are no-ops on an already-imported module, so calling it more than once in one test process does not re-register any actor. `uv run pytest tests/unit/test_rwb_job_queue.py -k queue` passes with `linux-box` down.

**Checkpoint**: `app/workers/queues.py` exists and is proven correct. Both user stories can now proceed.

---

## Phase 3: User Story 1 — Isolate worker capacity by job type (Priority: P1) 🎯 MVP

**Goal**: Each `rwb_job_type` runs in its own Dramatiq queue and worker process; a drain check confirms all queues are empty before a deploy.

**Independent Test**: Start the four per-queue worker processes (`bash infra/scripts/start-all.sh`), confirm four separate PID files exist, kill one, confirm the other three keep processing jobs of their own type (quickstart.md steps 1–4).

No UI in this story — skip the UI preview step (Article 8 doesn't apply; nothing renders).

### Implementation for User Story 1

- [ ] T004 [US1] [FR-001] [FR-002] [FR-003] [FR-004] [T-03] Replace the single `dramatiq app.workers.entrypoint --processes ... --threads ...` block in `infra/scripts/start-all.sh` (the `# ── 3. Dramatiq workers` section) with a loop over `python -m app.workers.queues`, one `dramatiq app.workers.entrypoint -Q "$queue" --processes "$PROCESSES" --threads "$THREADS" --pid-file "$PID_DIR/worker-$queue.pid"` per line, logging to `$LOG_DIR/worker-$queue.log`; update the file's header comment block describing the process layout to describe one process per queue instead of one worker process
  - Proof: `bash infra/scripts/start-all.sh` (inside `linux-box`) produces four files under `.dev-pids/worker-*.pid`, one per queue name from T001
- [ ] T005 [US1] [FR-004] [T-03] Replace `stop_pid worker` in `infra/scripts/stop-all.sh` with a loop over `python -m app.workers.queues` calling `stop_pid "worker-$queue"` for each
  - Proof: after T004's start, `bash infra/scripts/stop-all.sh` removes all four `worker-*.pid` files and reports each as stopped
- [ ] T006 [P] [US1] [FR-004] [T-11] Replace the single worker block in `infra/scripts/rhel9/rhel9-start.sh` (the `=== 4. Starting Dramatiq worker ===` section) with the same per-queue loop, using `.venv/bin/dramatiq` and `.venv/bin/python -m app.workers.queues` (RHEL9 scripts call venv binaries directly, unlike dev's `start-all.sh` which runs inside a container with the venv already on `PATH`), logging to `/var/lib/risk-workbench/worker-$queue.log`
  - Proof: manual RHEL9 dry run produces one PID file per queue under `$PID_DIR`
- [ ] T007 [P] [US1] [FR-004] [FR-007] [T-11] Replace `stop_and_verify worker ""` in `infra/scripts/rhel9/rhel9-stop.sh` (line 93) with a loop over `.venv/bin/python -m app.workers.queues` calling `stop_and_verify "worker-$queue" ""` for each; confirm whether this script needs an explicit `cd "$APP_DIR"` added (it doesn't currently `cd` there, unlike `rhel9-start.sh`) for the venv python invocation to resolve
  - Proof: manual RHEL9 dry run stops and verifies all four per-queue processes; each queue's stop/restart is independent of the others, demonstrating FR-007 (recovery behavior is unaffected by which or how many other job types exist)
- [ ] T008 [US1] [FR-005] [T-05] Create `infra/scripts/rhel9/rhel9-drain-check.sh`: poll `SELECT rwb_job_type, status_code, COUNT(*) AS n FROM rwb_job WHERE status_code IN ('pending','running') GROUP BY rwb_job_type, status_code` via the app's own `db.execute` (not raw `sqlcmd`), on a `DRAIN_POLL_INTERVAL_SECS` interval up to `DRAIN_TIMEOUT_SECS`, exiting 0 with "all queues empty" when no rows come back, or exiting 1 listing the outstanding `rwb_job_type`/`status_code`/count rows on timeout; source `infra/.env` at the top so `db.execute`'s connection settings resolve when run standalone
  - Proof: with no `rwb_job` rows in `pending`/`running`, `bash infra/scripts/rhel9/rhel9-drain-check.sh` prints `[drain-check] all queues empty.` and exits 0; with one such row and a short timeout, it exits 1 and names that row's type/status/count
- [ ] T009 [US1] [FR-006] [T-12] Add a drain-check step to `infra/scripts/rhel9/rhel9-ssh-deploy.sh` before its dependency-install/migration step, calling `rhel9-drain-check.sh` over SSH and aborting the deploy on a non-zero exit; update the script's trailing message (currently states worker restart is manual) to describe the stop → drain-check → deploy → start sequence
  - Proof: a dry run against a test host with an outstanding `pending` row and a short `DRAIN_TIMEOUT_SECS` aborts before step 3 runs
- [ ] T010 [P] [US1] Add a comment above `RWB_WORKER_PROCESSES`/`RWB_WORKER_THREADS` in `infra/.env.example` noting they now apply per queue (one `dramatiq` invocation per queue, each using these same values), not to one shared pool
  - Proof: comment present in the diff; no functional test
- [ ] T011 [P] [US1] [T-13] Replace the `logs-worker` target in `Makefile` (currently lines 58–59) with `logs-worker QUEUE=<name>`, failing with a usage message if `QUEUE` is unset, tailing `/workspace/.dev-logs/worker-$(QUEUE).log`
  - Proof: `make logs-worker` (no `QUEUE`) prints the usage message and exits non-zero; `make logs-worker QUEUE=upload_edm` tails that queue's log file
- [ ] T012 [US1] [T-06] Amend Article 10 in `.specify/memory/constitution.md` — replace the "Single Worker by Default" title and body with the text in `docs/CR/CR_04__PER_QUEUE_WORKERS.md` §5.4 (retitled "Concurrency Is Per-Queue, Not Per-Row"; states the claim query is already concurrency-safe under any number of workers, each `rwb_job_type` runs in its own queue named identically to the type, a single worker process per queue remains the default, and the reconciler is retained unchanged and MUST NOT be made queue-aware); bump the constitution's version per its own Governance section (this is a MINOR change — new default behavior described, no article redefined/removed, 13-article numbering stable) and add a Sync Impact Report entry
  - Proof: Article 10's body matches CR-004 §5.4 verbatim; version bump and Sync Impact Report entry present
- [ ] T013 [P] [US1] Remove the "Dramatiq queue drain before a redeploy" and "systemd unit files" open items from `docs/RHEL9_DEPLOYMENT.md`, replacing them with a description of the built per-queue nohup scripts and drain-check script from T004–T009
  - Proof: neither open item's original text remains; the doc describes the actual mechanism built
- [ ] T014 [P] [US1] Update the "same five processes run in development and production" line in `docs/SCAFFOLDING.md`'s Environment Topology section to describe N worker processes (one per queue) instead of one
  - Proof: the line no longer asserts a single worker process

**Checkpoint**: User Story 1 is fully functional — four isolated queues, per-queue start/stop in both environments, a working drain check, and the constitution amended. **STOP** — validate quickstart.md steps 1–4 before starting User Story 2.

---

## Phase 4: User Story 2 — Monitor and manage jobs from one page (Priority: P2)

**Goal**: A read-only page lists every `rwb_job` by type and status, including queued jobs; a `pending` job can be cancelled, a `failed` job can be resubmitted.

**Independent Test**: With jobs in various states, open the monitoring page, cancel a queued job, resubmit a failed job, confirm a running job offers neither action (quickstart.md step 5).

### Tests for User Story 2

> Write these first; confirm they fail before T017/T018 are implemented.

- [ ] T015 [P] [US2] [FR-010] [FR-012] [T-08] Add `test_cancel_pending_row_succeeds`, `test_cancel_non_pending_row_is_noop` (parametrized over `running`/`succeeded`/`failed`/`cancelled`), and `test_claim_racing_cancel_resolves_to_one_winner` to `tests/unit/test_rwb_job_queue.py`, exercising a `cancel_rwb_job` function that does not exist yet
  - Proof: all three fail with `ImportError`/`AttributeError` before T017, and pass after
- [ ] T016 [P] [US2] [FR-013] [FR-014] [T-09] Add `test_resubmit_via_ensure_pending_resets_same_row` to `tests/unit/test_rwb_job_queue.py` — a regression check confirming `ensure_pending_rwb_job` on a `failed` row still produces the documented same-`id`, `attempt_count`-incremented, `error_detail`-cleared reset (this function is not changed by this feature; the test protects against a future accidental change)
  - Proof: passes immediately (no implementation change needed) — confirms the assumption T-09/data-model.md's transition table depends on

### UI Preview for User Story 2

> Real new layout (a full page, not a copy tweak) — needs a preview and approval before the template is built (Article 8; docs/UI_WORKFLOW.md).

- [ ] T017 [US2] Build a rendered HTML preview of the monitoring page at `docs/ui_previews/job_monitoring.html` (from `docs/ui_previews/_scaffold.html`, reusing existing design tokens per Article 9), covering: a job list grouped by type/status per `contracts/job-monitoring-routes.md`'s `GET /jobs` table, a `pending` row shown as "queued" with no elapsed time, a `failed` row with its `error_detail` and a Resubmit action, a `running` row with elapsed time and the "stopping this requires an operator" note, and the empty state (no jobs at all)
  - Proof: approved by the feature owner before T019 is started

### Implementation for User Story 2

- [ ] T018 [US2] [FR-010] [FR-011] [FR-012] [T-08] Add `cancel_rwb_job(*, rwb_job_id) -> bool` to `app/services/rwb_job_service.py`: `UPDATE rwb_job SET status_code = 'cancelled', updated_at = :now WHERE id = :id AND status_code = 'pending'`, returning whether the update matched a row — same rowcount contract as `claim_rwb_job`
  - Proof: T015's tests pass
- [ ] T019 [US2] Add `cancelled` to `rwb_job_status_kind`'s seed block in `alembic/versions/0001_initial.py` (sort order 50, after `failed`'s 40) per `data-model.md`'s schema-change section
  - Proof: `make db-rebuild` (developer-run, not this task) seeds a `cancelled` row in `rwb_job_status_kind`; T015's tests can insert a row with `status_code = 'cancelled'` without an FK violation
- [ ] T020 [US2] [FR-008] [FR-009] Replace the stub handler for `GET /workflows/rwb-jobs` in `app/routers/shell.py` (currently renders the placeholder `pages/workflows_rwb_jobs.html`, "RWB jobs will appear here") with the real job-monitoring implementation per `contracts/job-monitoring-routes.md`: query `rwb_job` grouped by `rwb_job_type`, ordered by status then `updated_at` descending within each group, computing elapsed queued/running time per row. No nav manifest change — `workflows.rwb_jobs` already exists in `app/nav/manifest.py` under the Workflows rail root.
  - Proof: matches T017's approved preview structurally (same grouping, same fields)
- [ ] T021 [US2] [FR-010] [FR-011] [FR-012] Add `POST /workflows/rwb-jobs/{id}/cancel` to `app/routers/shell.py`, calling T018's `cancel_rwb_job` and re-rendering the row partial with the row's actual resulting status (not an error) regardless of which side of the race won, per `contracts/job-monitoring-routes.md`
  - Proof: manual test — cancelling a `pending` row shows it as `cancelled`; attempting to cancel a row already claimed by a worker (simulate by calling `claim_rwb_job` first in a test) shows it as `running`, not an error
- [ ] T022 [US2] [FR-013] [FR-014] Add `POST /workflows/rwb-jobs/{id}/resubmit` to `app/routers/shell.py`, calling the existing `ensure_pending_rwb_job` (unchanged) with the row's own `requestor_type`/`requestor_id`/`rwb_job_type`/`input_data`, re-rendering the row partial as `pending`
  - Proof: T016's regression test passes; manual test confirms one row, not two, after resubmit
- [ ] T023 [US2] Replace `app/templates/pages/workflows_rwb_jobs.html`'s placeholder body with the real job-monitoring template and row partial, wiring the two actions (`hx-post` to T021/T022's routes, `hx-target`/`hx-swap="outerHTML"` on the row, `hx-confirm` on Cancel), matching T017's approved preview. No new nav entry — this fills the existing `workflows.rwb_jobs` slot (Article 1: one nav node + one handler + one template, already in place; this task only replaces the template and handler body).
  - Proof: quickstart.md step 5 passes end to end in a browser at `/workflows/rwb-jobs`
- [ ] T024 [P] [US2] [FR-015] [FR-016] [FR-017] Confirm (by reading the finished template) that no Stop/Cancel action renders for a `running` row, no generic job-submission form exists anywhere on the page, and no drain or worker-scaling control exists — add a one-line note in the template stating that stopping a running job requires an operator to act outside the app
  - Proof: quickstart.md step 5's running-job assertion passes

**Checkpoint**: Both user stories independently functional. Full quickstart.md passes.

---

## Phase 5: Polish & Cross-Cutting Concerns

- [ ] T025 [P] Security review: CSRF present on `POST /jobs/{id}/cancel` and `POST /jobs/{id}/resubmit` (Article 13); confirm no route in this feature bypasses the safe bound-parameter path (Article 7)
- [ ] T026 Run quickstart.md end to end (all six steps) and record the result
- [ ] T027 [P] Add an ADR entry recording the Article 10 amendment (T012) and the `rwb_actor`/derived-queue-list pattern (T001) to this repo's `CONTEXT.md`/`docs/adr/` (create both lazily now if neither exists yet, per `docs/agents/domain.md`)
  - Proof: `docs/adr/` contains a new entry naming the per-queue worker decision and pointing back to `docs/CR/CR_04__PER_QUEUE_WORKERS.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: empty, nothing to do.
- **Foundational (Phase 2)**: T001–T003 must complete before any US1 script task (T004–T009 read the module T001 creates) and before any US2 test task that references `cancel_rwb_job` conceptually depends on the same test file's existing import structure — no direct code dependency, but T003 establishes the pattern T015/T016 extend in the same file.
- **User Story 1 (Phase 3)**: depends only on Phase 2. Independently completable and testable (quickstart.md steps 1–4) without any User Story 2 work.
- **User Story 2 (Phase 4)**: depends only on Phase 2 — does not depend on User Story 1's script changes (the monitoring page reads `rwb_job` directly, regardless of which process claimed a row). Can be built in parallel with US1 by a second developer, but per `docs/UI_WORKFLOW.md`'s one-story-at-a-time rule, implement and get US1 clicked/approved before starting US2 if working solo.
- **Polish (Phase 5)**: depends on both user stories being complete.

### Within User Story 2

- T015/T016 (tests) before T018 (implementation) — write first, confirm they fail.
- T017 (UI preview, approved) before T023 (template).
- T018 before T021 (route calls the service function).
- T019 (migration) before T015/T021 can pass against a real `cancelled` status — note T015's tests can insert `status_code = 'cancelled'` as a raw string against SQLite without the FK the real migration adds, so T015 can be written before T019, but the SQL Server tier depends on migration order matching.

### Parallel Opportunities

- T006/T007 (RHEL9 scripts) in parallel with T004/T005 (dev scripts) — different files, same pattern.
- T010, T011, T013, T014 (docs/env/Makefile) in parallel with each other and with T004–T009 — no shared files.
- T015/T016 (US2 tests) in parallel with each other, and in parallel with all of US1's tasks — different files, no dependency.
- T024 in parallel with T025 — both are review/confirmation tasks against the finished template, not code changes.

---

## Parallel Example: User Story 1

```bash
Task: "Replace the worker block in infra/scripts/rhel9/rhel9-start.sh with a per-queue loop"
Task: "Replace stop_and_verify worker in infra/scripts/rhel9/rhel9-stop.sh with a per-queue loop"
Task: "Add a comment to infra/.env.example about per-queue RWB_WORKER_PROCESSES/THREADS"
Task: "Change the logs-worker Makefile target to require QUEUE=<name>"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 2 (T001–T003).
2. Complete Phase 3 (T004–T014).
3. **STOP and VALIDATE**: run quickstart.md steps 1–4.
4. This alone resolves the starvation problem and the two `docs/RHEL9_DEPLOYMENT.md` open items — it is independently deployable without any UI change.

### Incremental Delivery

1. Foundational (Phase 2) → both stories unblocked.
2. User Story 1 → validate independently → this is the MVP; the monitoring UI is a separate, later increment.
3. User Story 2 → validate independently → full quickstart.md now passes.

---

## Notes

- No task touches `claim_rwb_job`, `complete_rwb_job`, `reconcile_stale_rwb_jobs`, `run_one`, or `run_pending` — plan.md's Complexity Tracking table is empty because nothing here needs justifying against the constitution beyond the Article 10 amendment itself (T012), which is the feature's own stated purpose.
- Every script task (T004–T009) reads the queue list from T001's `queue_names()`/CLI entry point — no task hardcodes `upload_edm`/`upload_rdm`/`backfill_rdm_analyses`/`backfill_edm_detail` into a shell script, matching spec.md's non-negotiable behavior #5.
