# Dev plan — CR-004 (per-queue Dramatiq workers)

Corrects two things CR-004 got wrong about the repo before listing file-by-file changes:

- **`infra/scripts/rhel9-deploy.sh` doesn't exist.** The real deploy script is `infra/scripts/rhel9/rhel9-ssh-deploy.sh`. It pushes code over rsync and installs/migrates remotely, but does not restart uvicorn/worker/poller — that's still a manual step, by its own trailing message.
- **RHEL9 has no systemd units yet, for anything.** `infra/scripts/rhel9/rhel9-start.sh` / `rhel9-stop.sh` start/stop the worker with plain `nohup` + PID files — the same shape as dev's `start-all.sh`/`stop-all.sh`, not systemd. CR-004's systemd unit (§4.6) is real future work, but it's a bigger step than "convert the existing worker start command" — see Decision A below.

This plan covers dev (`start-all.sh`/`stop-all.sh`) and RHEL9 (`rhel9-start.sh`/`rhel9-stop.sh`/`rhel9-ssh-deploy.sh`) in the same pass, since both currently start the worker the same wrong way.

## Decision A: systemd now, or nohup scripts now and systemd later?

CR-004 assumes systemd units. That's a reasonable end state, but RHEL9 doesn't have that infrastructure for *any* process yet (not uvicorn, not the poller, not the worker) — introducing it here means also deciding the service account, `WantedBy` targets, and log destinations for the worker specifically, ahead of the other three processes. Two ways to sequence this:

1. **nohup/PID-file scripts now, matching the existing `rhel9-start.sh`/`rhel9-stop.sh` shape**, extended to loop over queues the same way `start-all.sh` will. Systemd (CR-004 §4.6) becomes a later, separate step that converts all four RHEL9 processes at once, not the worker alone.
2. **systemd now, for the worker only**, ahead of uvicorn/poller/Valkey. Matches CR-004 §4.6 as written, but leaves RHEL9 with a mixed model (worker under systemd, everything else under nohup) until the rest catches up.

This plan builds option 1 (extends the existing nohup scripts) so RHEL9's process model stays consistent, and keeps §4.6's systemd unit as documented future work. State which one you want before implementation — the file-level changes below are written for option 1; switching to option 2 changes §3 and drops §5/§6.

## 1. The queue list — one definition, everyone else reads it

New module: `app/workers/queues.py`.

```python
# app/workers/queues.py
"""Queue names for per-rwb_job_type Dramatiq workers (CR-004).

rwb_actor pins queue_name to the actor's function name — no call site sets
queue_name by hand. queue_names() reads the resulting list back from the
broker after discover_jobs() has registered every *_jobs.py actor, so a new
job module is picked up with no edit here.
"""
from __future__ import annotations

import sys

import dramatiq


def rwb_actor(fn=None, **kwargs):
    def wrap(f):
        return dramatiq.actor(queue_name=f.__name__, **kwargs)(f)
    return wrap(fn) if fn else wrap


def queue_names() -> list[str]:
    from app.workers import loader  # noqa: PLC0415 — same startup-only boundary as loader.py
    loader.discover_jobs()
    return sorted(dramatiq.get_broker().actors.keys())


def _main() -> None:
    for name in queue_names():
        print(name)


if __name__ == "__main__":
    sys.exit(_main())
```

- `python -m app.workers.queues` prints one queue name per line. Every shell script below consumes this — none hardcodes `upload_edm`, `upload_rdm`, `backfill_rdm_analyses`, `backfill_edm_detail` anywhere.
- Import cost: this module pulls in `app.workers.loader`, which sets the Dramatiq broker and needs Redis reachable. Same rule as `loader.py`'s own docstring: only import this from a startup/shell context, never from the request path.

## 2. `app/workers/entity_jobs.py`

Change the import and all four decorators:

```python
from app.workers.queues import rwb_actor
```

```python
@rwb_actor(max_retries=0)
def upload_edm(rwb_job_id: str) -> None:
    ...

@rwb_actor(max_retries=0)
def upload_rdm(rwb_job_id: str) -> None:
    ...

@rwb_actor(max_retries=0)
def backfill_rdm_analyses(rwb_job_id: str) -> None:
    ...

@rwb_actor(max_retries=0)
def backfill_edm_detail(rwb_job_id: str) -> None:
    ...
```

Remove the `import dramatiq` line if nothing else in the file uses it directly (check — `_ = broker.redis_broker` at the top of the file stays, since that's the broker-registration side effect, unrelated to the decorator).

No change to `_upload_edm_body`, `_upload_rdm_body`, `_backfill_rdm_analyses_body`, `_backfill_edm_detail_body`, `run_one`, or `run_pending`.

## 3. `app/workers/loader.py`

No functional change. `_send` already does `dramatiq.get_broker().actors.get(rwb_job_type)` then `actor.send(rwb_job_id)` — Dramatiq's `Message.send()` routes to `actor.queue_name` on its own, so this keeps working once actors carry a real `queue_name` instead of the implicit `"default"`.

## 4. `infra/scripts/start-all.sh` (dev)

Replace lines 50–59 (the single `dramatiq app.workers.entrypoint` block):

```bash
# ── 3. Dramatiq workers (one process per queue) ──────────────────────────────
echo "[start] Dramatiq workers..."
PROCESSES=${RWB_WORKER_PROCESSES:-1}
THREADS=${RWB_WORKER_THREADS:-2}
while read -r queue; do
    dramatiq app.workers.entrypoint -Q "$queue" \
        --processes "$PROCESSES" \
        --threads "$THREADS" \
        --pid-file "$PID_DIR/worker-$queue.pid" \
        >> "$LOG_DIR/worker-$queue.log" 2>&1 &
    echo "       worker[$queue] PID=$! processes=$PROCESSES threads=$THREADS"
done < <(python -m app.workers.queues)
```

Uses Dramatiq's own `--pid-file` flag instead of the current `echo $! > worker.pid` — one line of shell removed per process, not added.

Update the file's header comment (lines 7–17) to say "one Dramatiq process per queue" instead of implying a single worker process.

## 5. `infra/scripts/stop-all.sh` (dev)

Replace line 28 (`stop_pid worker`):

```bash
while read -r queue; do
    stop_pid "worker-$queue"
done < <(python -m app.workers.queues)
```

`stop_pid` itself (lines 10–26) is unchanged — it already takes a name and works off `$PID_DIR/$name.pid`.

## 6. `infra/scripts/rhel9/rhel9-start.sh`

Replace the single worker block (current lines 82–88):

```bash
echo ""
echo "=== 4. Starting Dramatiq workers (one process per queue) ==="
while read -r queue; do
    nohup .venv/bin/dramatiq app.workers.entrypoint -Q "$queue" \
        --processes "${RWB_WORKER_PROCESSES:-1}" --threads "${RWB_WORKER_THREADS:-2}" \
        > "/var/lib/risk-workbench/worker-$queue.log" 2>&1 &
    echo $! > "$PID_DIR/worker-$queue.pid"
    echo "  Started $queue (PID $(cat "$PID_DIR/worker-$queue.pid")). Log: /var/lib/risk-workbench/worker-$queue.log"
done < <(.venv/bin/python -m app.workers.queues)
```

Note `.venv/bin/python`, not bare `python` — matches this script's existing convention of calling the venv binaries directly (see its uvicorn/poller lines), unlike dev's `start-all.sh` which runs inside a container with the venv already on `PATH`.

## 7. `infra/scripts/rhel9/rhel9-stop.sh`

Replace line 93 (`stop_and_verify worker ""`):

```bash
while read -r queue; do
    stop_and_verify "worker-$queue" ""
done < <(.venv/bin/python -m app.workers.queues)
```

This runs from the app directory already (script assumes `cd`'d into `$APP_DIR` isn't required here since it only reads `$PID_DIR`, but `.venv/bin/python -m app.workers.queues` does need to run from — or be pointed at — the app checkout; confirm the script's existing working-directory assumption before wiring this in, since `rhel9-start.sh` does `cd "$APP_DIR"` near the top and `rhel9-stop.sh` currently does not).

## 8. `infra/scripts/rhel9/rhel9-ssh-deploy.sh`

No queue-list change needed here — this script never starts or stops the worker; it pushes code and installs dependencies only (confirmed: its own final message says worker restart is still manual). Once `rhel9-start.sh`/`rhel9-stop.sh` are updated per §6/§7, add a drain step before this script's step 3 (install/migrate):

```bash
echo ""
echo "=== 2.5. Drain check (remote) ==="
ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" \
    "cd $DEPLOY_DIR && .venv/bin/bash infra/scripts/rhel9/rhel9-drain-check.sh"
```

This assumes an operator has already run `rhel9-stop.sh` (or at least stopped the worker processes) before invoking the deploy — the deploy script doesn't stop/start processes today and this plan doesn't change that boundary. State whether that operator step should be folded into `rhel9-ssh-deploy.sh` itself or stay a documented manual precondition (it's manual today for uvicorn/poller too).

## 9. Drain-check script (new): `infra/scripts/rhel9/rhel9-drain-check.sh`

```bash
#!/usr/bin/env bash
# rhel9-drain-check.sh — poll rwb_job until no queue has pending/running rows,
# or a timeout is hit. Run after stopping worker processes, before deploying
# new code (CR-004).

set -euo pipefail

TIMEOUT_SECS="${DRAIN_TIMEOUT_SECS:-300}"
POLL_INTERVAL_SECS="${DRAIN_POLL_INTERVAL_SECS:-5}"
elapsed=0

while true; do
    outstanding="$(.venv/bin/python -c "
from db import execute
rows = execute(
    \"SELECT rwb_job_type, status_code, COUNT(*) AS n FROM rwb_job \"
    \"WHERE status_code IN ('pending', 'running') \"
    \"GROUP BY rwb_job_type, status_code\",
    {}, connection='WORKBENCH')
for r in rows:
    print(f\"{r[\'rwb_job_type\']}\t{r[\'status_code\']}\t{r[\'n\']}\")
")"
    if [ -z "$outstanding" ]; then
        echo "[drain-check] all queues empty."
        exit 0
    fi
    if [ "$elapsed" -ge "$TIMEOUT_SECS" ]; then
        echo "ERROR: drain timed out after ${TIMEOUT_SECS}s. Outstanding:" >&2
        echo "$outstanding" >&2
        exit 1
    fi
    sleep "$POLL_INTERVAL_SECS"
    elapsed=$((elapsed + POLL_INTERVAL_SECS))
done
```

Reads only `rwb_job` via the app's own `db.execute` — no Dramatiq/Redis dependency, matching CR-004 §4.7's point that `rwb_job` is the queue of record. Needs `infra/.env` sourced first (same as every other script here) so `db.execute`'s connection settings resolve — add `set -a && source infra/.env && set +a` near the top if this is meant to run standalone rather than always from inside `rhel9-ssh-deploy.sh` (which doesn't currently source `infra/.env` itself — check whether the remote shell it opens already has it, or add the source line here explicitly).

## 10. `infra/.env.example`

Add a comment above `RWB_WORKER_PROCESSES`/`RWB_WORKER_THREADS`:

```
# Applied per queue (CR-004): each rwb_job_type gets its own dramatiq
# process using these values, not one shared pool across all job types.
```

## 11. `Makefile`

`logs-worker` (line 58–59) currently tails one fixed `worker.log`. It needs to either take a queue-name argument or become useless once the log file is renamed `worker-<queue>.log`. Options:

- `logs-worker QUEUE=upload_edm` → `tail -f /workspace/.dev-logs/worker-$(QUEUE).log`, error if `QUEUE` unset.
- Or a `logs-workers` target that tails all of them: `tail -f /workspace/.dev-logs/worker-*.log`.

Pick one — flagged here because CR-004 named this file but didn't specify the replacement shape.

## 12. Tests

New file or addition to `tests/unit/test_rwb_job_queue.py`:

```python
from app.workers import loader
from app.workers.queues import queue_names
import dramatiq


def test_every_actor_queue_name_matches_actor_name():
    loader.discover_jobs()
    for name, actor in dramatiq.get_broker().actors.items():
        assert actor.queue_name == name


def test_queue_names_returns_current_actors():
    assert queue_names() == [
        "backfill_edm_detail", "backfill_rdm_analyses", "upload_edm", "upload_rdm",
    ]
```

Check `tests/unit/test_rwb_job_queue.py`'s existing fixtures for how it resets/reuses the broker between tests (Dramatiq actors register globally on import; a second `discover_jobs()` call across test files could raise `"An actor named X is already registered"` — confirm the existing test setup already handles re-import safely, since `entity_jobs.py`'s module-level `_ = broker.redis_broker` and actor decoration only run once per process either way).

## 13. Docs

- `docs/RHEL9_DEPLOYMENT.md`: remove the "Dramatiq queue drain before a redeploy" and "systemd unit files" open items; describe whichever of Decision A's options was chosen.
- `docs/SCAFFOLDING.md`: "the same five processes run in development and production" — update to reflect N worker processes (one per queue) instead of one.
- `.specify/memory/constitution.md`: apply CR-004 §5.4's Article 10 replacement text verbatim.

## 14. Rollout note: code changes under running workers

Stop all four queue processes, deploy the new code to disk, start all four queue processes again. That's the entire mechanism — nothing subtler than that, and nothing partial happens in between:

- Every process that's already running keeps executing whatever code was loaded into it when it started, until it exits.
- Once stopped (or once it exits after finishing an in-flight job), no process is left running old code.
- Once restarted, every process loads the code on disk at that moment — new code — and every job claimed from then on runs it.

This is a hard boundary at the process level: a running Python process cannot swap the code it's executing mid-job, and Dramatiq has no mechanism to make part of a running process behave differently from the rest of it. There's no partial or mixed state to reason about — a process is either the old build (still running, will finish what it already claimed) or the new build (started fresh, only ever runs new code), never both.

Doing this one queue at a time instead of all four at once (stop A, deploy, start A, then B, then C, then D) is the same mechanism, staggered — it only matters if other queues need to keep serving jobs while one is being cycled. Since deploys happen with no live traffic (CR-004 decision 9), that has no benefit here, so this plan uses the simpler all-four-at-once version already specified in CR-004 §5.3 (stop all → drain-check → deploy → start all).
