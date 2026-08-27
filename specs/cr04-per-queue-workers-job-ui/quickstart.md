# Quickstart: validating per-queue workers and the job monitoring UI

Prerequisites: `linux-box` and `sqlserver` containers up (`make dev-up`), or native dev per `docs/SCAFFOLDING.md`. `make shell` for a shell inside `linux-box`.

## 1. Queue isolation actually works

```bash
make shell
python -m app.workers.queues
```

Expect exactly four lines: `backfill_edm_detail`, `backfill_rdm_analyses`, `upload_edm`, `upload_rdm` (sorted).

Start the stack (`make dev-up`, or `bash infra/scripts/start-all.sh` inside the container) and confirm four separate worker processes, not one:

```bash
ls /workspace/.dev-pids/worker-*.pid
```

Expect four PID files, one per queue name.

## 2. A long job doesn't block a short one

Manually enqueue a `backfill_rdm_analyses` job for an RDM with many analyses (via the existing UI flow that triggers it), then immediately trigger an `upload_edm` job. Confirm the `upload_edm` job completes without waiting for `backfill_rdm_analyses` to finish — check both jobs' status on the monitoring page (`GET /workflows/rwb-jobs`) or via:

```sql
SELECT rwb_job_type, status_code, submitted_at, completed_at FROM rwb_job ORDER BY submitted_at DESC;
```

## 3. Stopping one queue's worker doesn't affect the others

```bash
kill "$(cat /workspace/.dev-pids/worker-backfill_rdm_analyses.pid)"
```

Trigger an `upload_edm` job. Confirm it still completes normally — the killed queue's worker is unrelated. Restart the stack (or just that one queue) to bring `backfill_rdm_analyses` back.

## 4. Drain check

```bash
bash infra/scripts/rhel9/rhel9-drain-check.sh
```

With no jobs `pending`/`running`, expect `[drain-check] all queues empty.` and exit 0. Enqueue a job, run again, expect it to report that job outstanding and (if left running past the timeout) exit 1 with a per-type/per-status listing.

## 5. Monitoring page

Open `/workflows/rwb-jobs` (Workflows › RWB Jobs in the sidebar). Confirm:

- Every job across all four types is listed, including any currently `pending` one, shown as "queued" with no elapsed running time.
- A `pending` job has a Cancel action; clicking it (after the confirmation prompt) moves it to `cancelled` and removes the Cancel action from that row.
- A `failed` job has a Resubmit action; clicking it moves the same row back to `pending` — confirm via the row's own history that no second row was created (`SELECT COUNT(*) FROM rwb_job WHERE requestor_type = ... AND requestor_id = ... AND rwb_job_type = ...` returns 1, not 2).
- A `running` job shows no Cancel or Resubmit action, and the page states that stopping it requires an operator to act outside the app (see `contracts/job-monitoring-routes.md`).

## 6. Cancel/claim race

Hard to trigger deterministically by hand; covered by the unit test in `tests/unit/test_rwb_job_queue.py` (see plan.md Testing section) rather than a manual step here.

## Full CR context

`docs/CR/CR_04__PER_QUEUE_WORKERS.md`, `docs/CR/CR_04a__JOB_MONITORING_UI.md`, and `docs/CR/CR_04_DEV_PLAN.md` carry the full rationale and file-by-file change list behind this quickstart's checks.
