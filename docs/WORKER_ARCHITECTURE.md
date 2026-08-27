# Worker Architecture

How background jobs work in this app: the queue, the worker process model,
how to add a new job type, and how to test one by hand.

## The three pieces

**`rwb_job` (SQL table)** is the queue of record. A row is the whole truth
about one job — its type, its input, its status, its output or error. Redis
(via Dramatiq) is only a wake-up signal telling an idle worker "go look now."
If that signal is lost, the poller's reconciler notices and re-dispatches —
so losing Redis loses latency, never a job.

**Dramatiq** is the delivery mechanism. Each `rwb_job_type` has its own
Dramatiq **queue**, and each queue has its own **worker process** (`dramatiq
app.workers.entrypoint -Q <queue>`). This is why one long `backfill_rdm_analyses`
job can't delay an `upload_edm` job: they're in different processes,
competing for nothing.

**A worker actor** claims a row, runs the real work, and writes the result
back — always through the same three-step lifecycle, described next.

## The lifecycle every job follows

```
enqueue_rwb_job()  →  status='pending'
        │
        ▼  (Dramatiq wakes a worker for that job's queue)
claim_rwb_job()    →  status='running'   (atomic: UPDATE ... WHERE status='pending')
        │
        ▼
   body(rwb_job_id) runs, under a heartbeat thread
        │
        ▼
complete_rwb_job() →  status='succeeded' or 'failed'
```

- **Claim** is one `UPDATE rwb_job SET status='running' WHERE id=:id AND
  status='pending'`. Whoever's `UPDATE` actually changes a row wins; a second
  claimant sees rowcount 0 and backs off. This is what makes it safe for
  any number of workers, across any number of queues, to claim from the
  same table at once. The claiming process stamps `claimed_by` with
  `runtime.worker_id()` — `hostname:pid`, identifying the OS process, not
  the job or the Python module the actor happens to live in (neither is the
  worker's business; `rwb_job_type` already names the job precisely). This
  is what lets a support engineer look at a stuck `running` row and know
  exactly which process to go kill (see "A job is stuck," below) instead of
  only knowing which host.
- **Heartbeat**: while the body runs, a background thread stamps
  `rwb_job_heartbeat.heartbeat_at` every `RWB_HEARTBEAT_INTERVAL_SECS`. If a
  worker process dies mid-job, its heartbeat goes stale; the poller's
  reconciler resets that row to `pending` after
  `RWB_HEARTBEAT_STALE_SECS` so another worker can pick it up.
  **The heartbeat only proves the worker process is alive — not that the
  body is making progress.** A job that's genuinely wedged inside one
  blocking call (not crashed, just stuck) keeps heartbeating and is never
  reclaimed. The only fix for that case is killing the worker process by
  hand; see "A job is stuck" below.
- **Complete** writes the terminal status and, on failure, `error_detail`.

## Adding a new job type

Four steps, always in this order:

1. **Write the body.** A function `_my_thing_body(rwb_job_id) -> JobResult`
   that reads its own input via `rwb_job_service.load_input_data(rwb_job_id)`
   and returns `JobResult.ok(**output)` or `JobResult.fail(error_detail,
   **output)`. Put it in an existing `app/workers/*_jobs.py` module if it's
   related to what's already there, or a new one otherwise — a new
   `foo_jobs.py` file is picked up automatically (see "Discovery" below).

2. **Wrap it as an actor:**
   ```python
   @rwb_actor(max_retries=0)
   def my_thing(rwb_job_id: str) -> None:
       runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                       body=lambda: _my_thing_body(rwb_job_id))
   ```
   `@rwb_actor` (from `app.workers.queues`) is `@dramatiq.actor` with
   `queue_name` pinned to the function's own name. **Never use raw
   `@dramatiq.actor`** — that would land the actor on Dramatiq's shared
   `"default"` queue, silently defeating per-type isolation. A unit test
   (`tests/unit/test_rwb_job_queue.py::test_every_actor_queue_name_matches_actor_name`)
   fails if this ever happens.

3. **Seed the type into `rwb_job_type_kind`.** `rwb_job.rwb_job_type` is a
   kind-table foreign key (Article 3 of the constitution — internal
   categoricals are never bare strings), so the value must exist in
   `rwb_job_type_kind` before any row can reference it. It's seeded in
   **two places, kept in sync by hand**:
   - `alembic/versions/0001_initial.py`'s `upgrade()` — a plain `INSERT`,
     runs once when the schema is first created (`make db-rebuild` /
     `wsl-db-rebuild`).
   - `infra/scripts/seed_db.py` — an idempotent `MERGE`, safe to re-run any
     time (`make db-bootstrap` / `wsl-db-seed`). This is what backfills a
     newly-added type into a database that already exists, without a full
     rebuild.

   Add the same `(code, label, sort_order)` row to both. There's no single
   source of truth for this list — matching an existing row's shape in both
   files is the whole job.

4. **Call `enqueue_rwb_job`** (or `ensure_pending_rwb_job` for a
   request-path retry) from wherever the job should be triggered, with
   `rwb_job_type` set to the new actor's name.

Nothing else needs to change. `app/workers/loader.py`'s `discover_jobs()`
walks every `app/workers/*_jobs.py` module and imports it, which is what
registers the actor with Dramatiq — no manifest, no registry, no list to
update by hand for discovery itself (only the kind-table seed in step 3 is
a real, unavoidable second place).

## Discovery: how a new `*_jobs.py` file gets picked up

`app/workers/queues.py`'s `queue_names()` calls `loader.discover_jobs()`,
which does `pkgutil.iter_modules` over `app/workers/` and imports every
module whose name ends in `_jobs`. Importing a module runs its
`@rwb_actor`-decorated functions, which registers them with Dramatiq. This
is why `python -m app.workers.queues` always reflects the current code —
it's not a maintained list, it's a live read of what got imported.

Every dev/RHEL9 start/stop/log script gets its queue list the same way —
none of them hardcode a job type's name.

## Running the workers

One OS process per queue, always:

```bash
dramatiq app.workers.entrypoint -Q upload_edm --processes 1 --threads 2
```

- **Dev, Docker** (`infra/scripts/start-all.sh`): loops over
  `python -m app.workers.queues`, backgrounds one process per queue with
  `--pid-file .dev-pids/worker-<queue>.pid`, logs to
  `.dev-logs/worker-<queue>.log`.
- **Dev, native WSL2**: `make wsl-worker QUEUE=<name>` — one foreground
  terminal per queue you want running (no PID file; the terminal is the
  log). `make wsl-worker-list` shows the available names.
- **RHEL9** (`infra/scripts/rhel9/rhel9-start.sh`): same loop, `nohup`, PID
  files under `/var/lib/risk-workbench/`.

Check what's actually running, on either dev path, without trusting the
start script's own printed output: `bash infra/scripts/wsl-worker-health.sh`
(or the RHEL9 equivalent, `rhel9-worker-health.sh`) lists every queue with
its PID-file status and an independent process-scan side by side.

## Testing by hand with the dummy jobs

`app/workers/dummy_jobs.py` defines two job types that exist only for
manual testing — never called from product code:

- **`dummy_wait`** sleeps for `input_data["seconds"]` (default 30), then
  succeeds. Long enough to cancel while it's still `pending`, or to kill its
  worker process while it's `running` and watch the reconciler reclaim it.
- **`dummy_fail`** always fails immediately, with `input_data["message"]`
  becoming `error_detail`. For testing what a failure looks like on the
  monitoring page, and what a resubmit does to it.

Both take a `label`, which shows up in every log line together with the
worker process's PID — so when you have several running at once you can
tell them apart without cross-referencing the `rwb_job_id` UUID.

Submit one:

```bash
python -m app.workers.dummy_submit wait --label test-1 --seconds 60
python -m app.workers.dummy_submit fail --label test-2 --message "boom"
```

Each call prints the new `rwb_job_id` and enqueues a genuinely new row —
every submission gets its own random `requestor_id`, so submitting the same
type twice never dedupes against the first (the dedup key is
`(requestor_type, requestor_id, rwb_job_type)`).

### Manual test walkthrough

**Queue isolation.** Start workers for `dummy_wait` and `upload_edm`
separately. Submit a `dummy_wait --seconds 60`, then immediately trigger a
real `upload_edm` job. The EDM upload completes without waiting — they're
different queues, different processes.

**Cancel while pending.** Submit `dummy_wait --seconds 60` but don't start
its worker yet. The row sits `pending`. Cancel it from the monitoring page
(or `rwb_job_service.cancel_rwb_job`) — it moves to `cancelled` and is never
picked up, even after you start the worker.

**Kill and restart while running.** Start the `dummy_wait` worker, submit
`dummy_wait --seconds 120`, let it get claimed (check the log for "sleeping
120s"). Kill the worker process (`kill <pid>`, or stop just that queue via
`stop-all.sh`/`rhel9-stop.sh`). The row stays `running` with a stale
heartbeat. Start the worker again — it does **not** resume the old job (that
process is gone); the poller's reconciler resets the row to `pending` once
`RWB_HEARTBEAT_STALE_SECS` has passed, and the new worker process picks it
up as a fresh attempt.

**Drain check.** Submit a few `dummy_wait` jobs with long sleeps, then run
`rhel9-drain-check.sh` (or the equivalent query — see that script). It
reports them as outstanding and times out, exactly like a real deploy
would refuse to proceed while jobs are in flight.

**Failure and resubmit.** Submit `dummy_fail --label see-this`. Confirm
`error_detail` on the monitoring page shows `[see-this] pid=<n>: ...`.
Resubmit it — the same row resets to `pending` (same `id`, `attempt_count`
incremented), and the old `error_detail` is gone. This is the real
`ensure_pending_rwb_job` behavior, not something special-cased for the
dummy jobs.

## A job is stuck — what are the actual options?

Two different problems get called "stuck," and they have different fixes:

- **The worker process died** (crashed, OOM-killed, container restarted).
  The heartbeat goes stale, the reconciler resets the row to `pending`, a
  live worker picks it up. This already works, automatically, no action
  needed.
- **The worker process is alive but the body is wedged** — blocked inside
  one call that never returns. The heartbeat thread doesn't know or care
  what the body is doing, so it keeps ticking. The reconciler will never
  touch this row. **The only fix is killing that worker process by hand**
  (`kill <pid>`, or restart just that one queue). Because each `rwb_job_type`
  has its own process, doing this only affects that one job type — every
  other queue keeps running.

There is no cooperative cancellation for a `running` job in this app. It was
considered and deliberately not built (see `docs/CR/CR_04__PER_QUEUE_WORKERS.md`
§6) — the per-queue process split is what makes "kill the process" an
acceptable answer instead of a dangerous one.

## Related documents

- `docs/CR/CR_04__PER_QUEUE_WORKERS.md` — why per-queue isolation was built,
  and what was deliberately left out.
- `docs/CR/CR_04a__JOB_MONITORING_UI.md` — the monitoring page's cancel/resubmit
  design.
- `.specify/memory/constitution.md`, Article 10 — the binding rule this
  architecture implements.
