# Research: Per-queue Dramatiq workers and job monitoring UI

## R1: Worker start/stop script shape (T-11) — RESOLVED

**Decision:** Extend the existing `nohup` + PID-file scripts (dev's `start-all.sh`/`stop-all.sh`, RHEL9's `rhel9-start.sh`/`rhel9-stop.sh`) to loop over `python -m app.workers.queues`, one `dramatiq app.workers.entrypoint -Q <queue>` process per job type. Do not introduce systemd units as part of this feature.

**Rationale:** RHEL9 has no systemd units for any of its four processes today (uvicorn, worker, poller, Valkey all run under plain `nohup`+PID files — confirmed by reading `infra/scripts/rhel9/rhel9-start.sh`/`rhel9-stop.sh`). Introducing systemd for the worker alone, ahead of the other three, would mean deciding the service account, restart policy, and `WantedBy` target for just one of four processes, leaving the RHEL9 process model split across two supervision styles until the rest catch up. Extending the existing script shape keeps that model consistent and unblocks this feature without taking on that separate decision.

**Alternatives considered:**
- *Systemd for the worker only* (`docs/CR/CR_04__PER_QUEUE_WORKERS.md` §4.6, as originally drafted) — rejected for this feature. Real future work: converting all four RHEL9 processes to systemd together, as one deliberate step, not something this feature should half-do for one process. `docs/CR/CR_04__PER_QUEUE_WORKERS.md`'s systemd unit draft (`rwb-worker@.service`) is kept on record for that future step; nothing in this feature deletes it.

**Owner sign-off:** 2026-08-25 (conversation record).

## R2: `Makefile`'s `logs-worker` target (T-13) — RESOLVED

**Decision:** Replace the single `logs-worker` target with `logs-worker QUEUE=<name>`, requiring `QUEUE` and failing with a clear message if it's unset, rather than a target that tails all four log files at once.

```makefile
logs-worker:   ## [Docker] Stream one queue's dramatiq worker log (usage: make logs-worker QUEUE=upload_edm)
	@test -n "$(QUEUE)" || (echo "Usage: make logs-worker QUEUE=<job_type>" && exit 1)
	$(BOX) tail -f /workspace/.dev-logs/worker-$(QUEUE).log
```

**Rationale:** Tailing all four logs interleaved (`tail -f worker-*.log`) is noisier for the common case — an operator chasing one specific stuck or slow job type wants that queue's log alone, not four interleaved streams. Requiring `QUEUE` explicitly also surfaces the queue name list to whoever runs the command, reinforcing that queues are now named, distinct things rather than implementation detail.

**Alternatives considered:**
- *`logs-workers` (plural) tailing `worker-*.log`* — kept as a documented option in `docs/CR/CR_04_DEV_PLAN.md` §11 but not chosen; an operator who wants all four can already do so directly with a shell glob, so it doesn't need its own Makefile target.

## R3: Dramatiq CLI flags used by this feature — confirmed present

Confirmed directly against the installed `dramatiq[redis]` package (`dramatiq --help`, `dramatiq/cli.py`) rather than assumed from documentation:

- `-Q`/`--queues`: "listen to a subset of queues (default: all queues)" — exists, used to bind one worker process to one queue name.
- `--pid-file`: "write the PID of the master process to a file" — exists, replaces the current scripts' manual `echo $! > worker.pid`.
- `--worker-shutdown-timeout`: exists, bounds how long a SIGTERM'd process waits for in-flight messages to finish before exiting.

No new dependency or version bump needed.

## R4: `rwb_job` dedup key — corrected from an earlier draft of CR-004

An earlier draft referenced a `request_key` column when describing why the claim query is concurrency-safe. Reading `app/services/rwb_job_service.py` directly shows the real dedup key is `UNIQUE(requestor_type, requestor_id, rwb_job_type)` (`alembic/versions/0001_initial.py`), and the claim query (`claim_rwb_job`) gates on `id` + `status_code = 'pending'`, independent of that dedup key. `docs/CR/CR_04__PER_QUEUE_WORKERS.md` §5.1 already carries the corrected wording; this entry records the correction for anyone tracing the decision back to its evidence.

## R5: `ensure_pending_rwb_job` — confirmed single-row reset, no new mechanism needed for resubmit

Read directly from `app/services/rwb_job_service.py`: when the existing row for `(requestor_type, requestor_id, rwb_job_type)` is terminal (`succeeded`/`failed`), `ensure_pending_rwb_job` resets that same row — same `id`, `attempt_count + 1`, `output_data`/`error_detail`/`completed_at`/`submitted_at` cleared, `input_data`/`correlation_id` replaced. No second row is ever created for that triple; the `UNIQUE` constraint prevents it. This is why CR-004a's resubmit action can call this function unchanged rather than needing new dedup logic — confirmed by reading the function, not assumed from its docstring alone.
