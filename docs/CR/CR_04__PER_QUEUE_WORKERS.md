# Change Request — Isolate Dramatiq worker queues per `rwb_job_type`

**ID:** CR-004
**Status:** Ready to apply
**Applies to:** `.specify/memory/constitution.md` (Article 10), `app/workers/`, `infra/scripts/start-all.sh`, `infra/scripts/stop-all.sh`, `infra/scripts/rhel9/rhel9-deploy.sh`, a new systemd template unit, `infra/.env.example`, `docs/RHEL9_DEPLOYMENT.md`, `docs/SCAFFOLDING.md`, tests.

## 1. Summary

Give each `rwb_job_type` its own Dramatiq queue and worker process, so one long-running job type cannot starve a short one. Four actors today: `upload_edm`, `upload_rdm`, `backfill_rdm_analyses`, `backfill_edm_detail`.

- `queue_name` is derived from the actor's function name (never hand-set) — §5.1.
- One OS process per queue, one PID/log file each, one systemd unit instance each.
- Deploy drains all queues by stopping their systemd units and polling `rwb_job` until empty.
- Article 10 is amended (§5.4): per-queue concurrency is the new default; the claim query and reconciler are unchanged.
- A job stuck for hours is recovered by killing its queue's worker process. No other mechanism is built for this.

## 2. Why

**Starvation.** All four actors share Dramatiq's implicit `"default"` queue and one process pool (`app/workers/loader.py`'s `discover_jobs()`). A long `backfill_rdm_analyses` run can occupy every thread, delaying an unrelated `upload_edm` job submitted moments later.

**No drain mechanism for deploys.** `docs/RHEL9_DEPLOYMENT.md` already lists this as unresolved: the drain steps are known (stop the worker, poll `rwb_job` for `running` rows) but not built, and no systemd unit exists for the worker. This CR builds both.

**No way to stop a stuck job.** `backfill_rdm_analyses` and `backfill_edm_detail` loop over per-item Risk Modeler calls with no point in the loop that checks for a stop request (confirmed in `app/workers/entity_jobs.py`). A job stuck for hours cannot be told to stop. Per-queue isolation limits the damage of the only real fix (kill the worker process) to one job type instead of all four.

## 3. Decisions

1. `queue_name == actor_name == rwb_job_type`, always, for every actor.
2. A decorator (`rwb_actor`, §5.1) sets `queue_name` to the function name automatically. No actor passes `queue_name=` by hand.
3. `app/workers/queues.py` is the one place that lists the queues, built by reading the broker's registered actors (§5.2). Shell and systemd get the list by running `python -m app.workers.queues`, never by copying it into a script.
4. One worker OS process per job type (four today), not grouped by type.
5. Draining a queue means `systemctl stop`ping its unit, not writing a pause flag to the database.
6. One systemd template unit (`rwb-worker@.service`), started once per queue name via `%i`. Adding a job type does not mean writing a new unit file.
7. The claim query and the reconciler (CR-001) do not change. `UPDATE rwb_job SET status_code='running' ... WHERE status_code='pending'` already works correctly with any number of workers claiming from it at once. The reconciler already looks up actors by name, so it works the same regardless of which process owns which queue.
8. A deploy stops and drains all four queues together. No tooling to deploy one queue's code while leaving the others running.
9. Deploys happen in an announced maintenance window with no one using the app, so no new `rwb_job` rows are created while draining. The drain check only has to wait for existing rows to finish, never for a growing backlog.
10. A job stuck for hours is fixed by killing its queue's worker process, nothing else. Cooperative cancellation was considered and is out of scope (§6). Killing the process only affects that one job type. Once the process is dead, its heartbeat stops, and the reconciler (CR-001) resets the row to `pending` after the heartbeat goes stale — this is exactly the case the reconciler already handles.

## 4. What changes, by area

### 4.1 `app/workers/queues.py` (new)

- `rwb_actor` (§5.1) and `queue_names()` (§5.2).
- A CLI entry point (`python -m app.workers.queues`) that prints one queue name per line.
- Depends on `app.workers.loader` (Dramatiq/Redis) — same startup-only import rule `loader.py` already documents.

### 4.2 `app/workers/entity_jobs.py`

Replace `@dramatiq.actor(...)` with `@rwb_actor(...)` on all four actors. Nothing else changes — `run_one`/`run_pending` don't touch Dramatiq queue routing.

### 4.3 `app/workers/loader.py`

No change. `_send` resolves actors by name; Dramatiq sends to `actor.queue_name` on its own. Add the test from §4.10 here or next to it.

### 4.4 `infra/scripts/start-all.sh`

Replace the single `dramatiq app.workers.entrypoint --processes ... --threads ...` call with a loop:

```bash
while read -r queue; do
    dramatiq app.workers.entrypoint -Q "$queue" \
        --processes "${RWB_WORKER_PROCESSES:-1}" \
        --threads "${RWB_WORKER_THREADS:-2}" \
        --pid-file "$PID_DIR/worker-$queue.pid" \
        >> "$LOG_DIR/worker-$queue.log" 2>&1 &
    echo "       worker[$queue] PID=$!"
done < <(python -m app.workers.queues)
```

Use Dramatiq's `--pid-file` flag instead of the current `echo $! > worker.pid`. `RWB_WORKER_PROCESSES`/`RWB_WORKER_THREADS` keep their current meaning, applied to each queue the same way.

### 4.5 `infra/scripts/stop-all.sh`

`stop_pid worker` becomes a loop over the same queue list, calling `stop_pid "worker-$queue"` for each.

### 4.6 systemd: `deploy/systemd/rwb-worker@.service` (new)

```ini
[Unit]
Description=Risk Workbench Dramatiq worker (%i queue)
After=network.target redis.service

[Service]
Type=simple
User=<service-account>            # placeholder — get the real account name from infra
WorkingDirectory=/opt/risk-workbench
EnvironmentFile=/opt/risk-workbench/infra/.env
ExecStart=/opt/risk-workbench/.venv/bin/dramatiq app.workers.entrypoint -Q %i --processes ${RWB_WORKER_PROCESSES} --threads ${RWB_WORKER_THREADS}
TimeoutStopSec=<worker-shutdown-timeout + margin>
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Verify at implementation time: confirm `EnvironmentFile` values actually expand inside `ExecStart=` the way this draft assumes, with a real `systemctl start`.

Enable all instances: `for q in $(python -m app.workers.queues); do systemctl enable --now "rwb-worker@$q"; done`.

Resolves the "systemd unit files not yet written" item in `docs/RHEL9_DEPLOYMENT.md` for the worker only — uvicorn, the poller, and Valkey stay separate open items.

### 4.7 Drain-check script (new)

`infra/scripts/rhel9/rhel9-drain-check.sh` (or a `queues.py` subcommand). Polls:

```sql
SELECT rwb_job_type, status_code, COUNT(*) AS n
FROM rwb_job
WHERE status_code IN ('pending', 'running')
GROUP BY rwb_job_type, status_code;
```

until it returns no rows, or a timeout is hit — then exits non-zero and lists what's still outstanding. Reads only `rwb_job`; no dependency on Dramatiq/Redis, since `rwb_job` is the queue of record.

### 4.8 `infra/scripts/rhel9/rhel9-deploy.sh`

Add a drain-check step before dependency install/migration; stop if it times out with work still outstanding. Replace the systemd TODO note with: stop all `rwb-worker@*` units → drain-check → deploy → start all `rwb-worker@*` units.

### 4.9 `infra/.env.example`

No new variables. Add a comment noting `RWB_WORKER_PROCESSES`/`RWB_WORKER_THREADS` now apply per queue, not to one shared pool.

### 4.10 Tests

- Unit: every actor `discover_jobs()` registers has `queue_name == actor_name`.
- Unit: `queue_names()` returns the four current names.
- No SQL-tier or IRP-tier changes — schema, claim query, and reconciler are untouched.

### 4.11 Docs

- `docs/RHEL9_DEPLOYMENT.md`: remove the two open items this CR resolves; describe the built mechanism instead.
- `docs/SCAFFOLDING.md`: the "same five processes" topology line no longer holds — the worker is now N processes, one per queue.
- `.specify/memory/constitution.md`: apply the Article 10 replacement in §5.4.

## 5. Design detail

### 5.1 `rwb_actor`

```python
# app/workers/queues.py
import dramatiq

def rwb_actor(fn=None, **kwargs):
    def wrap(f):
        return dramatiq.actor(queue_name=f.__name__, **kwargs)(f)
    return wrap(fn) if fn else wrap
```

Every actor uses `@rwb_actor(...)` instead of `@dramatiq.actor(...)`. No caller sets `queue_name`.

### 5.2 `queue_names()`

```python
# app/workers/queues.py
from app.workers import loader

def queue_names() -> list[str]:
    loader.discover_jobs()
    return sorted(dramatiq.get_broker().actors.keys())
```

Read from the broker's registered actors, not a hand-written list — a new `*_jobs.py` actor is picked up automatically.

### 5.3 Drain sequence

1. `systemctl stop rwb-worker@<name>` for every queue (SIGTERM; Dramatiq finishes messages already received, within `--worker-shutdown-timeout`).
2. Poll the drain-check query (§4.7) until it returns no rows, or timeout.
3. Deploy.
4. `systemctl start rwb-worker@<name>` for every queue.

Step 2 is needed because SIGTERM only covers messages Dramatiq already received — it says nothing about a row still `pending` and not yet dispatched, or one the reconciler is mid-recovering. `rwb_job` is the only check that reflects both.

### 5.4 Article 10 amendment

Current (`.specify/memory/constitution.md`, Article 10):

> ### Article 10 — The SQL Table Is the Queue; Single Worker by Default
>
> App-side work (`rwb_job`) MUST use a SQL-backed queue with a single worker and plain dequeue (IRP already queues/executes its own jobs; `irp_job` is *tracked* by the poller, not dequeued). Documented upgrade paths exist for:
>
> - A concurrency-safe claim query.
> - Idempotent IRP submission.
>
> These are documented upgrades, not default complexity. The stale-`running` reclaim (heartbeat + reconciler) MUST be retained regardless of worker concurrency level.

Replacement:

> ### Article 10 — The SQL Table Is the Queue; Concurrency Is Per-Queue, Not Per-Row
>
> App-side work (`rwb_job`) MUST use a SQL-backed queue with plain dequeue (IRP already queues/executes its own jobs; `irp_job` is *tracked* by the poller, not dequeued). The claim query (`UPDATE ... WHERE status_code='pending'`) already works correctly with any number of concurrent workers claiming from it (CR-004).
>
> Each `rwb_job_type` MUST run in its own Dramatiq queue, named identically to the `rwb_job_type` (CR-004). A single worker process per queue remains the default; adding more processes or threads to one queue requires an observed contention problem in that queue, not anticipated scale.
>
> The stale-`running` reclaim (heartbeat + reconciler, CR-001) MUST be retained regardless of worker concurrency level, and MUST NOT be made queue-aware.
>
> Documented upgrade path that remains open: idempotent IRP submission.

## 6. Out of scope

- A pause flag in the database. Draining uses `systemctl stop`.
- Separate Redis brokers per queue. One broker serves all queues.
- Autoscaling, dynamic worker-count changes, priority queues.
- Different `RWB_WORKER_PROCESSES`/`RWB_WORKER_THREADS` values per queue.
- Deploying one queue's code while others keep running.
- Any change to the claim query, the heartbeat, or the reconciler.
- Any change to `run_one`/`run_pending`.
- A way for `backfill_rdm_analyses`/`backfill_edm_detail` to stop mid-run on request. The only fix for a stuck job is killing the worker process (§3.10).

## 7. Residual risks

1. A future actor written with raw `@dramatiq.actor` instead of `@rwb_actor` lands silently on the `"default"` queue. Caught by the test in §4.10 only if it runs before merge.
2. A stuck job still needs a person to kill the worker process by hand. This is the intended fix (§3.10), not a gap.
3. Four processes means four PID files and four log files instead of one. `make logs-worker` and similar tooling need updating to loop over the queue list.
4. The systemd `ExecStart` env-var substitution in §4.6 is unverified — confirm with a real `systemctl start` before treating it as final.
5. The service account in the unit file is a placeholder — get the real name from infra (pre-existing open item, not created by this CR).

## 8. Acceptance criteria

- All four actors use `@rwb_actor(...)`; none sets `queue_name=` by hand.
- `app/workers/queues.py` builds the queue list from registered actors; works as an import and as `python -m app.workers.queues`.
- `start-all.sh`/`stop-all.sh` start/stop one process per queue with PID/log names from that list — no queue name hardcoded in either script.
- `rwb-worker@.service` exists and starts per queue.
- The drain-check script polls `rwb_job` across all queues, times out with a per-queue/per-status list, and gates `rhel9-deploy.sh`.
- Both open items in `docs/RHEL9_DEPLOYMENT.md` (drain mechanism, worker systemd units) are resolved.
- Article 10 is amended per §5.4.
- Unit tests pass; no SQL-tier or IRP-tier test is touched.
- Nothing in §6 was added.

## 9. Grep checklist

- `dramatiq app.workers.entrypoint`
- `RWB_WORKER_PROCESSES`, `RWB_WORKER_THREADS`
- `worker.pid`, `worker.log`
- `@dramatiq.actor` (in `app/workers/*_jobs.py`)
- "Single Worker by Default" (constitution and cross-references)
- "Dramatiq queue drain before a redeploy" / "systemd unit files" (`docs/RHEL9_DEPLOYMENT.md`)
- "same five processes" (`docs/SCAFFOLDING.md`)
- `make logs-worker`
- `infra/scripts/rhel9/rhel9-deploy.sh`
