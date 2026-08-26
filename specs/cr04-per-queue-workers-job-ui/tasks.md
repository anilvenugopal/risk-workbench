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

- [X] T001 [FR-001] [FR-003] [T-01] [T-02] Create `app/workers/queues.py` with `rwb_actor(fn=None, **kwargs)` (a decorator wrapping `dramatiq.actor`, setting `queue_name=f.__name__`) and `queue_names()` (calls `app.workers.loader.discover_jobs()`, returns `sorted(dramatiq.get_broker().actors.keys())`), plus a `__main__` block printing one name per line
  - Proof: `python -m app.workers.queues` prints one queue name per line, sorted — confirmed live, currently 11 lines (see T002)
- [X] T002 [FR-001] [T-01] Replace every `@dramatiq.actor(...)` with `@rwb_actor(...)` (preserving any other kwargs, e.g. `time_limit`) across all `app/workers/*_jobs.py` modules — not just `entity_jobs.py`'s four (`upload_edm`, `upload_rdm`, `backfill_rdm_analyses`, `backfill_edm_detail`), but every actor discovered when this task was implemented: `portfolio_jobs.py`'s five breakout actors (`run_breakout_country/custom/lob/peril/state`), `geohaz_jobs.py`'s `run_geohaz`, and `metadata_jobs.py`'s `sync_irp_metadata` — 11 actors total. Re-run `python -m app.workers.queues` before finishing to confirm no `*_jobs.py` module was missed. Drop each file's bare `import dramatiq` only where nothing else in that file uses `dramatiq.*` directly.
  - Proof: T003's tests pass; confirmed live that all 11 actors have `queue_name == actor_name`
- [X] T003 [P] [T-01] [T-02] Add `test_every_actor_queue_name_matches_actor_name` and `test_queue_names_returns_current_actors` to `tests/unit/test_rwb_job_queue.py` — the first iterates every actor the broker actually has (whatever the count) and fails if any has `queue_name != actor_name`, catching a future raw `@dramatiq.actor` in any `*_jobs.py` module; the second asserts `queue_names()` against an exact, explicit list of the current 11 names (`_EXPECTED_QUEUE_NAMES`), not membership-only — a real job type silently disappearing from the list must fail this test, not just an unexpected new one appearing. Adding a job type later means updating that list, which is the deliberate cost of catching a silent drop immediately.
  - Proof: `uv run pytest tests/unit/test_rwb_job_queue.py -v --no-cov` — 19 passed (17 pre-existing + these 2), no live Redis needed (`RedisBroker(url=...)` only constructs a lazy `redis.Redis` client; `discover_jobs()`'s repeated imports are no-ops). Full unit tier (`uv run pytest tests/unit --no-cov`) — 1214 passed, no regressions from converting all 11 actors.

**Checkpoint**: `app/workers/queues.py` exists and is proven correct. Both user stories can now proceed.

---

## Phase 3: User Story 1 — Isolate worker capacity by job type (Priority: P1) 🎯 MVP

**Goal**: Each `rwb_job_type` runs in its own Dramatiq queue and worker process; a drain check confirms all queues are empty before a deploy.

**Independent Test**: Start the four per-queue worker processes (`bash infra/scripts/start-all.sh`), confirm four separate PID files exist, kill one, confirm the other three keep processing jobs of their own type (quickstart.md steps 1–4).

No UI in this story — skip the UI preview step (Article 8 doesn't apply; nothing renders).

### Implementation for User Story 1

- [X] T004 [US1] [FR-001] [FR-002] [FR-003] [FR-004] [T-03] Replace the single `dramatiq app.workers.entrypoint --processes ... --threads ...` block in `infra/scripts/start-all.sh` (the `# ── 3. Dramatiq workers` section) with a loop over `python -m app.workers.queues`, one `dramatiq app.workers.entrypoint -Q "$queue" --processes "$PROCESSES" --threads "$THREADS" --pid-file "$PID_DIR/worker-$queue.pid"` per line, logging to `$LOG_DIR/worker-$queue.log`; update the file's header comment block describing the process layout to describe one process per queue instead of one worker process
  - Proof: ran the extracted loop natively (Docker/`linux-box` not up during implementation) against a real `.dev-pids`/`.dev-logs` — produced 11 PID files, one per queue (11 now, not 4 — see T002). Sent test messages to `upload_edm` and `run_geohaz` via `dispatch.dispatch()`; only those two queues' logs grew, the other 9 were untouched — confirmed `-Q` genuinely isolates message routing, not just discovery. `bash infra/scripts/wsl-worker-health.sh` (new, T010b) showed all 11 alive by both PID-file and independent process-scan.
- [X] T005 [US1] [FR-004] [T-03] Replace `stop_pid worker` in `infra/scripts/stop-all.sh` with a loop over `python -m app.workers.queues` calling `stop_pid "worker-$queue"` for each
  - Proof: ran the extracted loop against the same 11 running processes from T004's test — all 11 stopped by name, no leftover PID files, `wsl-worker-health.sh` confirmed all absent afterward (exit 1, matching its own "no live workers" contract).
- [X] T006 [P] [US1] [FR-004] [T-11] Replace the single worker block in `infra/scripts/rhel9/rhel9-start.sh` (the `=== 4. Starting Dramatiq worker ===` section) with the same per-queue loop, using `.venv/bin/dramatiq` and `.venv/bin/python -m app.workers.queues` (RHEL9 scripts call venv binaries directly, unlike dev's `start-all.sh` which runs inside a container with the venv already on `PATH`), logging to `/var/lib/risk-workbench/worker-$queue.log`
  - Proof: `bash -n` syntax-checked clean. No RHEL9 host available to run live — `rhel9-start.sh` already `cd`s to `$APP_DIR` before this block (confirmed by reading the file), so `.venv/bin/python -m app.workers.queues` resolves correctly with no further change needed there.
- [X] T007 [P] [US1] [FR-004] [FR-007] [T-11] Replace `stop_and_verify worker ""` in `infra/scripts/rhel9/rhel9-stop.sh` with a loop over `.venv/bin/python -m app.workers.queues` calling `stop_and_verify "worker-$queue" ""` for each. Confirmed by reading the file — unlike `rhel9-start.sh`, this script had no `APP_DIR`/`cd` at all (it previously only read PID files by absolute path); added `APP_DIR="${APP_DIR:?...}"` and `cd "$APP_DIR"` matching `rhel9-start.sh`'s existing pattern. This is a breaking change for any existing caller that ran `rhel9-stop.sh` without `APP_DIR` — fixed the one real call site found (`docs/RHEL9/RHEL9_QUICKSTART.md`'s stop command, which previously omitted it).
  - Proof: `bash -n` syntax-checked clean. No RHEL9 host available to run live.
- [X] T007a [P] [US1] New `infra/scripts/rhel9/rhel9-worker-health.sh` (+ doc reference in `RHEL9_QUICKSTART.md`) — the RHEL9 equivalent of T010b's `wsl-worker-health.sh`, adapted to RHEL9's real conventions: `.venv/bin/python` not `uv run`, requires `APP_DIR`, matches `rhel9-stop.sh`'s `stop_and_verify` PID_DIR default (`/var/lib/risk-workbench/pids`). Not in the original task list — added for parity with the WSL2 health-check tool, at the same request.
  - Proof: `bash -n` syntax-checked clean; executable bit set. No RHEL9 host available to run live — flagged as unverified-live, unlike T010b's WSL2 counterpart which was run end to end.
- [X] T008 [US1] [FR-005] [T-05] Create `infra/scripts/rhel9/rhel9-drain-check.sh`: poll `SELECT rwb_job_type, status_code, COUNT(*) AS n FROM rwb_job WHERE status_code IN ('pending','running') GROUP BY rwb_job_type, status_code` via the app's own `db.execute` (not raw `sqlcmd`), on a `DRAIN_POLL_INTERVAL_SECS` interval up to `DRAIN_TIMEOUT_SECS`, exiting 0 with "all queues empty" when no rows come back, or exiting 1 listing the outstanding `rwb_job_type`/`status_code`/count rows on timeout; sources `infra/.env` so `db.execute`'s connection settings resolve when run standalone; requires `APP_DIR` like the other RHEL9 scripts
  - Proof: ran the drain-check logic directly against the real `WORKBENCH` database (SQL Server up, reachable natively) — empty case: `execute(...)` returned `[]`, prints "all queues empty", exit 0. Outstanding case: enqueued a real `pending` `upload_edm` row via `enqueue_rwb_job`, ran the poll/timeout loop with a 3s timeout — exited 1, printed `upload_edm  pending  1` exactly. Cleaned up the test row afterward. `bash -n` syntax-checked clean on the actual RHEL9 script file (which additionally wraps this in `.venv/bin/python`/`APP_DIR` — not re-run in that exact form since no RHEL9 host is available, but the SQL/Python logic is identical and was verified live).
- [X] T009 [US1] [FR-006] [T-12] Add a drain-check step (`=== 2.5. Drain check (remote) ===`) to `infra/scripts/rhel9/rhel9-ssh-deploy.sh` between prerequisite-check and dependency-install/migration, calling `rhel9-drain-check.sh` over SSH with `APP_DIR` set; updated the script's trailing message to describe the stop (`rhel9-stop.sh`) → drain-check (now automatic, step 2.5) → deploy → start (`rhel9-start.sh`) sequence explicitly, since this script still does not stop/start those processes itself
  - Proof: `bash -n` syntax-checked clean. No RHEL9/SSH target available to run live — the added block matches this script's own existing `ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" "..."` pattern exactly (same shape as steps 2 and 4).
- [ ] T010 [P] [US1] Add a comment above `RWB_WORKER_PROCESSES`/`RWB_WORKER_THREADS` in `infra/.env.example` noting they now apply per queue (one `dramatiq` invocation per queue, each using these same values), not to one shared pool
  - Proof: comment present in the diff; no functional test
- [X] T010a [P] [US1] [FR-004] A third, previously-missed place starts the worker with no queue split: the `wsl-worker` Makefile target (native WSL2 dev, one process per foreground terminal — distinct from `start-all.sh`'s background+PID-file model). Changed to require `QUEUE=<name>` and pass `-Q "$(QUEUE)"` through to `dramatiq app.workers.entrypoint`, matching `wsl-app`/`wsl-poller`'s existing one-target-one-foreground-terminal pattern. Added `wsl-worker-list` (prints available queue names) alongside it.
  - Proof: `make wsl-worker` (no `QUEUE`) fails with a usage message pointing at `wsl-worker-list`; `make wsl-worker QUEUE=upload_edm` starts a single foreground process consuming only that queue.
- [X] T010b [P] [US1] New `infra/scripts/wsl-worker-health.sh` (+ `make wsl-worker-health`) for repeatable before/in-between/after inspection: reports every queue's live/dead state by two independent methods — PID-file check (`PID_DIR`, default `.dev-pids`) and a direct process scan (`pgrep` matching `dramatiq app.workers.entrypoint -Q <queue>`) — so PID-file mode (Docker/RHEL9) and native WSL2's foreground-terminal mode (no PID file at all) are both covered by one tool. Not in the original task list — added during T004/T005 verification, since testing the start/stop loops repeatably needed this rather than one-off `ps`/`ls` commands each time.
  - Proof: ran before (all absent, exit 1) → started 11 workers (all alive by both methods, exit 0) → stopped all 11 → after (all absent again, exit 1, zero leftover PID files). Full before/in-between/after cycle observed directly, not assumed.
- [X] T011 [P] [US1] [T-13] Replace the `logs-worker` target in `Makefile` with `logs-worker QUEUE=<name>`, failing with a usage message if `QUEUE` is unset, tailing `/workspace/.dev-logs/worker-$(QUEUE).log`. Also added the RHEL9 equivalent as real scripts (not doc-only commands — corrected mid-task after an initial doc-only pass was flagged as insufficient): `infra/scripts/rhel9/rhel9-logs-worker.sh` (no-arg usage lists queue names via `python -m app.workers.queues`; missing-log-file case reports a clear error instead of a raw `tail` failure) and `rhel9-logs-poller.sh`, both requiring `APP_DIR` like every other `rhel9-*.sh` script; `RHEL9_QUICKSTART.md` points at these scripts, not inline `tail` commands.
  - Proof, Docker target: (1) `make logs-worker` with no `QUEUE` — usage message, exit 2. (2) `make logs-worker QUEUE=upload_edm` passes the guard and prints the exact command it builds (`docker compose ... exec linux-box tail -f /workspace/.dev-logs/worker-upload_edm.log`), confirming `$(QUEUE)` substitutes correctly; only Docker's own `exec` layer is unrun (no `linux-box` container here). (3) Started a real `dramatiq -Q upload_edm` worker natively and ran `tail -f` against `.dev-logs/worker-upload_edm.log` — real log lines came through, proving the path/filename half of the target is correct.
  - Proof, RHEL9 scripts: `bash -n` clean on both, executable bit set. `rhel9-logs-worker.sh` with no queue arg — confirmed live (env sourced) it prints the usage message and lists all 11 real queue names via `python -m app.workers.queues`, exit 1. Both scripts' missing-log-file guard confirmed live against real (nonexistent, since no worker is running here) paths — each reports its own clear error, not a raw `tail: cannot open` message. The `exec tail -f "$logfile"` success line itself is the same single call already proven against a real Dramatiq log earlier in this session (T004/T005's native test) — not re-proven through an artificial path substitution, which would have tested nothing new.
- [X] T012 [US1] [T-06] Amend Article 10 in `.specify/memory/constitution.md` — replaced the "Single Worker by Default" title and body with CR-004 §5.4's text (retitled "Concurrency Is Per-Queue, Not Per-Row"). Corrected the version-bump class while implementing: the constitution's own precedent (checked directly — CR-002 and CR-003 are both scored MAJOR specifically for "article redefinitions," while the two MINOR entries are explicitly "no article redefined") means this is MAJOR, not the MINOR the CR doc assumed — an article's title and core rule changing is the same class of change as CR-002/CR-003, not a carve-out added alongside an unchanged rule. Bumped `3.2.0 → 4.0.0`, added a Sync Impact Report entry matching the existing entries' style, updated the footer's Version/Last Amended line.
  - Proof: Article 10's body matches CR-004 §5.4 verbatim (line 300 on). Checked for other files hardcoding the constitution's version — `AGENTS.md`/`docs/DATA_MODEL.md` cite "Art. 11 v3.2.0" specifically to pin *when that clause was added* (matching the constitution's own "added v3.1.0"/"added v3.2.0" citation style for Article 11's history), not the document's current version — correctly left unchanged, not a miss. `specs/005-.../plan.md` and `research.md` are a completed feature's historical record and correctly untouched.
- [X] T013 [P] [US1] Update `docs/RHEL9/RHEL9_DEPLOYMENT.md`'s Open items (moved from `docs/RHEL9_DEPLOYMENT.md` since this task was originally written — that path no longer exists): mark worker isolation done (describe the per-queue scripts + `rhel9-worker-health.sh` from T006/T007/T010b), keep the drain-check item open until T008/T009 land (only the drain mechanism itself is still missing, not the per-queue stop), keep the systemd item open with the CR-004 rationale for deferring it
  - Proof: the doc distinguishes what T006/T007 actually built from what T008/T009 still need to build — no item is marked resolved before its task is actually done
- [ ] T014 [P] [US1] Update the "same five processes run in development and production" line in `docs/SCAFFOLDING.md`'s Environment Topology section to describe N worker processes (one per queue) instead of one
  - Proof: the line no longer asserts a single worker process

**Checkpoint**: User Story 1 is fully functional — isolated queues, per-queue start/stop in both environments, a working drain check, and the constitution amended. **STOP** — validate quickstart.md steps 1–4 before starting User Story 2.

---

## Phase 4: User Story 2 — Monitor and manage jobs from one page (Priority: P2)

**Goal**: A read-only page lists every `rwb_job` by type and status, including queued jobs; a `pending` job can be cancelled, a `failed` job can be resubmitted.

**Independent Test**: With jobs in various states, open the monitoring page, cancel a queued job, resubmit a failed job, confirm a running job offers neither action (quickstart.md step 5).

### Tests for User Story 2

> Write these first; confirm they fail before T017/T018 are implemented.

- [X] T015 [P] [US2] [FR-010] [FR-012] [T-08] Added `test_cancel_pending_row_succeeds`, `test_cancel_non_pending_row_is_noop` (looped over `running`/`succeeded`/`failed`/`cancelled`, not `pytest.mark.parametrize` — each iteration needs its own freshly-enqueued row, which a plain loop expresses more directly), and `test_claim_racing_cancel_resolves_to_one_winner` to `tests/unit/test_rwb_job_queue.py`; added `cancel_rwb_job` to that file's top-level import from `rwb_job_service`, which does not exist yet
  - Proof: `uv run pytest tests/unit --no-cov` — the added import makes the **whole module** fail to collect (`ImportError: cannot import name 'cancel_rwb_job'`), not just these three tests; confirmed live. This blocks every other test in `test_rwb_job_queue.py` until T018 lands, a stronger and more immediate failure than the task's original wording ("all three fail") implied, but consistent with its intent. Verified the three tests' actual SQL logic separately, directly against the real SQL Server WORKBENCH database (not just the SQLite unit fixture), by running `cancel_rwb_job`'s intended `UPDATE ... WHERE status_code='pending'` inline — all three scenarios (cancel-while-pending, claim-vs-cancel race both directions, cancel-a-terminal-row no-op) passed for real, including hitting and working around the real `rwb_job_status_kind` FK (cancelled a manually-seeded test row, then removed it — T019 does this seeding for real).
- [X] T016 [P] [US2] [FR-013] [FR-014] [T-09] Added `test_resubmit_via_ensure_pending_resets_same_row` to `tests/unit/test_rwb_job_queue.py` — a regression check confirming `ensure_pending_rwb_job` on a `failed` row still produces the documented same-row, `attempt_count`-incremented, `error_detail`-cleared reset
  - Proof: ran the test's logic directly against the real SQL Server WORKBENCH database (not just SQLite) — passed, but only after fixing a real assertion bug found in the process: comparing the returned id to the original with plain `==` failed, because SQL Server's `uniqueidentifier` round-trips with different letter casing than the lowercase string Python's `uuid.uuid4()` generated (same row, same value, different case). Fixed to compare `.lower()`. The pre-existing `test_ensure_pending_restamps_on_retry` (same file) has this identical latent risk on a plain `==` and has never been run against SQL Server for that specific line — flagged here, not silently fixed, since it's outside this task's scope.

### UI Preview for User Story 2

> Real new layout (a full page, not a copy tweak) — needs a preview and approval before the template is built (Article 8; docs/UI_WORKFLOW.md).

- [ ] T017 [US2] Build a rendered HTML preview of the monitoring page at `docs/ui_previews/job_monitoring.html` (from `docs/ui_previews/_scaffold.html`, reusing existing design tokens per Article 9), covering: a job list grouped by type/status per `contracts/job-monitoring-routes.md`'s `GET /jobs` table, a `pending` row shown as "queued" with no elapsed time, a `failed` row with its `error_detail` and a Resubmit action, a `running` row with elapsed time and the "stopping this requires an operator" note, and the empty state (no jobs at all)
  - Proof: approved by the feature owner before T019 is started

### Implementation for User Story 2

- [X] T018 [US2] [FR-010] [FR-011] [FR-012] [T-08] Added `cancel_rwb_job(*, rwb_job_id) -> bool` to `app/services/rwb_job_service.py`: `UPDATE rwb_job SET status_code = 'cancelled', updated_at = :now WHERE id = :id AND status_code = 'pending'`, returning whether the update matched a row — same rowcount contract as `claim_rwb_job`
  - Proof: T015's tests pass (23/23 in `test_rwb_job_queue.py`); full unit tier green (1218 passed)
- [X] T019 [US2] Added `cancelled` to `rwb_job_status_kind`'s seed block in both `alembic/versions/0001_initial.py` (sort order 50, after `failed`'s 40) and `infra/scripts/seed_db.py`'s matching `MERGE` block (the second place this repo's seed values live — confirmed both need updating, not just the migration)
  - Proof: `make wsl-db-seed` run for real — confirmed `cancelled` present in the real `rwb_job_status_kind` table on the dev database. Updated one pre-existing SQL-Server-tier test (`tests/sqlserver/test_job_tables_migration.py::test_rwb_job_requestor_and_status_seeds`) that asserted the exact prior 4-value set; `make wsl-test-sql` — 234 passed.
- [~] T020 [US2] [FR-008] [FR-009] **Backend only** (route/template deferred to the UI pass, per explicit scope decision): added `list_rwb_jobs_for_monitoring()` to `app/services/rwb_job_service.py` — every `rwb_job` row, `ORDER BY rwb_job_type, status_code, updated_at DESC`, matching `contracts/job-monitoring-routes.md`'s grouping/ordering exactly. Elapsed-time display is deliberately NOT computed here — it changes on every render, so it belongs in the route/template layer, not baked into a point-in-time query. The `GET /workflows/rwb-jobs` route replacing the stub handler is still open, folded into T023's UI pass.
  - Proof: two new unit tests (`test_list_rwb_jobs_for_monitoring_returns_all_types_and_fields`, `...orders_by_type_then_status_then_recency`) pass on SQLite; both re-verified directly against the real SQL Server WORKBENCH database, including the specific `'cancelled' < 'pending'` ordering claim (confirmed SQL Server sorts the same way).
- [ ] T021 [US2] [FR-010] [FR-011] [FR-012] Add `POST /workflows/rwb-jobs/{id}/cancel` to `app/routers/shell.py`, calling T018's `cancel_rwb_job` and re-rendering the row partial with the row's actual resulting status (not an error) regardless of which side of the race won, per `contracts/job-monitoring-routes.md`
  - Proof: manual test — cancelling a `pending` row shows it as `cancelled`; attempting to cancel a row already claimed by a worker (simulate by calling `claim_rwb_job` first in a test) shows it as `running`, not an error
- [~] T022 [US2] [FR-013] [FR-014] **Backend only** (route/template deferred to the UI pass, per explicit scope decision): added `resubmit_rwb_job(*, rwb_job_id) -> str | None` to `app/services/rwb_job_service.py` — the real new logic the UI needs: given only a row's `id` (the UI never already knows its `requestor_type`/`requestor_id`/`rwb_job_type`/`input_data` the way a code caller of `ensure_pending_rwb_job` normally would), looks those up and calls `ensure_pending_rwb_job` unchanged. Returns `None` for an unknown id or a non-terminal row, matching `ensure_pending_rwb_job`'s own existing skip behavior. The `POST /workflows/rwb-jobs/{id}/resubmit` route is still open, folded into T023's UI pass.
  - Proof: T016's regression test still passes unchanged (proves `ensure_pending_rwb_job` itself is untouched). Three new unit tests for `resubmit_rwb_job` specifically (by-id lookup + resubmit, unknown id, non-terminal row) pass on SQLite and re-verified directly against real SQL Server — including confirming the row's own stored `input_data` carries forward correctly through the by-id lookup.
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
4. This alone resolves the starvation problem and the two `docs/RHEL9/RHEL9_DEPLOYMENT.md` open items — it is independently deployable without any UI change.

### Incremental Delivery

1. Foundational (Phase 2) → both stories unblocked.
2. User Story 1 → validate independently → this is the MVP; the monitoring UI is a separate, later increment.
3. User Story 2 → validate independently → full quickstart.md now passes.

---

## Notes

- No task touches `claim_rwb_job`, `complete_rwb_job`, `reconcile_stale_rwb_jobs`, `run_one`, or `run_pending` — plan.md's Complexity Tracking table is empty because nothing here needs justifying against the constitution beyond the Article 10 amendment itself (T012), which is the feature's own stated purpose.
- Every script task (T004–T009) reads the queue list from T001's `queue_names()`/CLI entry point — no task hardcodes `upload_edm`/`upload_rdm`/`backfill_rdm_analyses`/`backfill_edm_detail` into a shell script, matching spec.md's non-negotiable behavior #5.
