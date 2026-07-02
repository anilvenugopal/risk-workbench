# Change Request — Decouple result work items into `rwb_job`, and settle queue resilience

**ID:** CR-001
**Status:** Ready to apply
**Applies to:** `docs/PRD.md`, `docs/DATA_MODEL.md`, `docs/SCAFFOLDING.md`, Alembic migration(s), application models/services, Dramatiq worker actors, the poller, tests, and infra config.
**Owner decision:** all decisions in §3 are **locked** by prior review. This document is the source of truth for the change; apply it wherever the affected concepts appear, not only in the sections named.

> **How to use this CR (for the implementing agent):** treat §3 as non-negotiable decisions, §4 as the edit list per area, §5 as the design detail to embed in the spec, §6 as guardrails (things that must **not** be introduced), §7 as risks to record verbatim, §9 as a grep checklist to find every affected place. When in doubt, search the repo for the old names in §9 rather than assuming the list of files is complete.

---

## 1. Summary

Two coupled changes:

1. **Decouple result work items from IRP.** Replace the `result_work_item` table (currently a hard child of `irp_job`) with a general **`rwb_job`** construct. An IRP job completing is *one* source of an `rwb_job`, not the only one, and no longer a mandatory parent. Idempotent creation moves from `UNIQUE(irp_job_id, work_type)` to a source-agnostic **`request_key`**.

2. **Settle queue resilience with the lowest-complexity design that has no duration-based timeouts.** Make Redis **durable (AOF)** so the broker stops being a data-loss point; rely on **Dramatiq** for the failure modes it already handles (worker death, task-failure retries, graceful shutdown); add a **per-job heartbeat + a single-instance reconciler** to recover jobs that stop making progress; and keep **idempotent workers + an atomic `request_key` claim** as the backstop so any rare double-delivery is harmless.

---

## 2. Why we are changing this

**Coupling.** `result_work_item.irp_job_id` is required and the dedup key is `UNIQUE(irp_job_id, work_type)`. But real result/processing work has non-IRP sources — e.g. an analyst explicitly requesting an exposure-summary push, or one job chaining the next (`retrieve_analysis_results → push_results_to_loss_repo`). Forcing every such row to hang off an `irp_job` is wrong, and making `irp_job_id` nullable *breaks the current dedup*: SQL Server permits only a single NULL in a `UNIQUE` index, so multiple non-IRP rows would collide. The idempotency key must become source-agnostic.

**Resilience.** The open question was "no lost work item, no duplicate work item" when Dramatiq's broker (Redis) can crash. Investigation of Dramatiq's actual behavior (its Redis broker acks only after successful processing and redelivers in-flight messages when a worker dies; its heartbeat is per **worker process**, so a long job on a live worker is not falsely redelivered; the Retries middleware handles task failures) showed that **the only gap Dramatiq cannot cover is Redis itself losing data.** Everything else it already does. So the resilience work shrinks to: make Redis durable, and add the minimum needed to detect a job that has genuinely stopped progressing — **without** any timeout tied to job duration (durations here depend on data size, so any fixed window is simultaneously too short and too long).

---

## 3. Decisions (locked, with rationale)

1. **`result_work_item` → `rwb_job`.** A general queued-work construct. Behaves like `irp_job` in that a sweeper/worker picks up queued rows.
2. **`irp_job_id` becomes a nullable FK on `rwb_job`** (lineage/reporting only). IRP completion still creates an `rwb_job`, but it is a soft relationship.
3. **Idempotency key = `request_key` (VARCHAR, UNIQUE, NOT NULL)**, replacing `UNIQUE(irp_job_id, work_type)`. Producers compute it from lineage (scheme in §5.4). *(Name is `request_key`, not `dedup_key`.)*
4. **`origin` column** on `rwb_job` records how the row was created (`irp_completion` | `analyst_request` | `chained`). For observability/debugging; the reconciler does not depend on it.
5. **Durable Redis via AOF** (`appendonly yes`, `appendfsync everysec`, persisted volume, default auto-rewrite). This is the mitigation for broker data loss and closes the pending-lost case (below).
6. **Rely on Dramatiq** for worker-death redelivery, task-failure retries (Retries middleware), and graceful-shutdown requeue. Do not rebuild these.
7. **Per-job heartbeat** in a **child table `rwb_job_heartbeat`** (worker stamps `heartbeat_at` + `worker_id` while processing a job). Worker-agnostic (any worker processing the job stamps it). This is the *progress* signal, not a lease.
8. **Single-instance reconciler** re-enqueues **`running`** `rwb_job` rows whose heartbeat is stale (threshold = a small multiple of the heartbeat interval — a **constant**, never job duration). It does **not** scan `pending` rows (AOF covers pending-lost), which is what keeps duration windows out of the design entirely.
9. **Idempotent workers + atomic `request_key` claim** (`pending → running`) are the correctness backstop; any rare double-delivery is harmless.
10. **No new statuses.** `rwb_job_status_kind` stays `pending / running / succeeded / failed`. "Abandoned" = `running` with a stale heartbeat; recovery resets it to `pending`.

---

## 4. What changes, by area

### 4.1 Data model (`docs/DATA_MODEL.md`)

- **Rename/replace** `result_work_item` → **`rwb_job`** in the ER diagram, the per-table manifest, and all prose.
- **`rwb_job` columns** (indicative): `id` PK; `request_key` VARCHAR UNIQUE NOT NULL; `origin` VARCHAR; `irp_job_id` FK **NULL**; `work_type` VARCHAR; `status_code` FK → `rwb_job_status_kind`; `customer_id` (denormalized, for `apply_scope`); `payload`; `error_detail`; `attempt_count` INT; `claimed_by` VARCHAR NULL (worker id, observability); `created_at`; `updated_at`; `completed_at` NULL.
- **Drop** the `UNIQUE(irp_job_id, work_type)` constraint; **add** `UNIQUE(request_key)`.
- **New child table `rwb_job_heartbeat`**: `rwb_job_id` FK (unique — one current row per job, upserted); `worker_id` VARCHAR; `heartbeat_at` DATETIME2. (One-row-per-job upsert keeps heartbeat churn off the main `rwb_job` row and out of any event stream.)
- **Kind table rename**: `result_work_item_status_kind` → `rwb_job_status_kind` (values unchanged: `pending/running/succeeded/failed`). Update the kind-seed checklist.
- **Update** the manifest/notes to state: idempotent creation is via `request_key`; `irp_job_id` is lineage-only and nullable; the heartbeat child table is the progress signal; the reconciler recovers stale-`running` rows.
- Keep `rwb_job` inside `apply_scope` coverage (it carries `customer_id`).

### 4.2 PRD (`docs/PRD.md`)

- **Glossary**: "Result work item" → **"RWB job"**; add "Reconciler", "Job heartbeat", "`request_key`".
- **Background-work / queue section** (currently the poller + Dramatiq + result-work-item narrative): rewrite to the model in §5 — poller writes an `rwb_job` (with `origin=irp_completion`, `irp_job_id` set, computed `request_key`); Dramatiq owns worker-death/retry/shutdown; the reconciler owns stale-`running` recovery; AOF makes the broker durable.
- **Adversarial-review items** that reference the old recovery story (poller re-triggers work items; staleness sweep resets `running→pending`; "Redis stateless, losing it loses in-flight work items"): update to the AOF + reconciler + idempotency model. Remove any implication that the *poller* recovers non-IRP work (it can't; the reconciler does, source-agnostically).
- **Locked-decisions list**: add CR-001's decisions (§3).
- Add/adjust an **IRP & queue resilience** subsection capturing §5 in full so it lives in the spec, not only here.

### 4.3 Scaffolding & infrastructure (`docs/SCAFFOLDING.md` + infra)

- **Redis persistence (AOF).** This is new and must appear in dev, partner-Docker, and prod:
  - **Dev (WSL `redis-server`)**: start Redis with `--appendonly yes --appendfsync everysec` (or a checked-in `redis.conf`), with the data dir on local disk (SSD), not a network share. Update `make wsl-start` accordingly.
  - **Partner / any Redis container** (`infra/docker-compose.yml`): `command: redis-server --appendonly yes --appendfsync everysec` and a **named volume mounted at `/data`** so the AOF survives restarts.
  - **Prod (systemd `redis-server`)**: `redis.conf` with `appendonly yes`, `appendfsync everysec`, `dir` on a persisted SSD volume; leave `auto-aof-rewrite-percentage`/`-min-size` at defaults (self-compacting; the file tracks live queue size, not history — no TTL needed or possible).
  - **Verification step** (add to setup): `redis-cli CONFIG GET appendonly` → `yes`; `redis-cli INFO persistence` shows `aof_enabled:1`.
  - **Update Environment Topology** to note Redis is durable (AOF) in all environments and why (broker-loss mitigation).
- **Reconciler process.** Add the reconcile sweep. **Recommendation: fold it into the existing single-instance `poller` process** (it is already periodic and single-instance), so the process count stays at five and the "same five processes dev = prod" property holds. If separation is preferred, add a dedicated `reconciler` process/systemd unit and `make wsl-reconciler` — but it **must be single-instance**. Whichever placement: document it in the topology and the make/systemd tables.
- **Config/env** (`infra/.env.example`): `RWB_HEARTBEAT_INTERVAL_SECS` (e.g. 15), `RWB_HEARTBEAT_STALE_SECS` (e.g. 45 = 3× interval), `RWB_RECONCILE_INTERVAL_SECS` (e.g. 30). These are constants, not per-job durations.
- **Naming** across scaffolding docs/commands: any `result_work_item` reference → `rwb_job`.

### 4.4 Application code & migrations

- **Alembic**: since dev amends `0001_initial.py` in place until cutover, fold the schema change into it (rename table, add `request_key`/`origin`/nullable `irp_job_id`, `UNIQUE(request_key)`, new `rwb_job_heartbeat` table, kind rename/seed). Prefer `make wsl-db-rebuild` in dev per the existing lifecycle.
- **Models/services**: rename `result_work_item` model/repo → `rwb_job`; add `rwb_job_heartbeat`; route all access through the scoped repository (`apply_scope`, bound params — `rwb_job` carries `customer_id`).
- **Poller**: on IRP terminal status, create the `rwb_job` via idempotent insert on `request_key` (`INSERT ... WHERE NOT EXISTS`), `origin=irp_completion`.
- **Dramatiq worker actors** (result/processing): 
  - **Claim** atomically: `UPDATE rwb_job SET status='running', claimed_by=:wid WHERE id=:id AND status='pending'`; rowcount 0 ⇒ already claimed ⇒ ack and drop.
  - **Emit the job heartbeat from a separate daemon thread** so it is not blocked by long, non-chunkable work — write an initial `rwb_job_heartbeat` on claim, then stamp every `RWB_HEARTBEAT_INTERVAL_SECS` until the job ends. **Implement exactly per §5.3a** (stoppable `Event.wait` timer, context-manager start/stop, tiny lease-free write). This is the design that must not regress into "stamp from the work thread," which stalls during a download.
  - **Idempotent side effects**: downloads write to a temp path + atomic rename; "final artifact already present" ⇒ treat as done. Chained/tail `rwb_job`s are created by idempotent insert on their own `request_key`.
- **Reconciler**: single-instance loop every `RWB_RECONCILE_INTERVAL_SECS`; for each `rwb_job` with `status='running'` whose latest `rwb_job_heartbeat.heartbeat_at < now() - RWB_HEARTBEAT_STALE_SECS`: atomically reset `running→pending` (`WHERE status='running'`) and **re-enqueue** the Dramatiq message. It does **not** scan `pending`.

### 4.5 Tests

- **Unit (`tests/unit/`, sqlite_conn):** `request_key` idempotency (double insert = one row); atomic claim (second claim yields rowcount 0); reconciler decision (stale heartbeat ⇒ re-enqueue; fresh heartbeat ⇒ skip; `pending` never re-enqueued by reconciler); key-derivation per `origin`.
- **SQL tier (`make wsl-test-sql`):** end-to-end round-trip — create `rwb_job`, claim, heartbeat, simulate stale heartbeat, reconciler resets + re-enqueues; idempotent re-run leaves one result.
- Update fixtures/factories renamed `result_work_item` → `rwb_job`.

---

## 5. Resilience design (embed this in the PRD)

### 5.1 Failure modes × handling

| Failure | Handled by | Custom code? |
|---|---|---|
| Worker dies mid-job, Redis alive | Dramatiq redelivery (ack-after-success; per-process heartbeat) | No |
| Task raises / fails | Dramatiq Retries middleware (backoff, max_retries, dead-letter) | No |
| Graceful shutdown/redeploy | Dramatiq requeues in-flight messages | No |
| **Redis loses data (crash)** | **AOF durability** (≤ ~1s loss) + Dramatiq redelivery on restart | Config only |
| **Job stops progressing (wedged worker; or running-job message lost)** | **Job heartbeat + reconciler** (stale `running` ⇒ re-enqueue) | Yes (minimal) |
| Any rare double-delivery | **Idempotent worker + atomic `request_key` claim** | Yes (backstop) |

### 5.2 Redis AOF durability
`appendonly yes`, `appendfsync everysec` (≤ ~1s worst-case loss), persisted SSD volume, default auto-rewrite (self-compacting — file tracks live queue size, not history; no TTL). Rationale: acknowledged enqueues survive a broker crash, so **pending-lost stops being a case we must detect** — which is what lets us avoid a pending-side timeout (the duration window we rejected). Operators inspect **`rwb_job` in SQL** for outstanding work; `redis-cli`/RedisInsight for live broker metrics. **Do not parse AOF/RDB files** for application state.

### 5.3 Job heartbeat + reconciler
The heartbeat proves a job is *being worked*, independent of which worker and independent of duration. On claim the worker writes an initial `rwb_job_heartbeat`, then stamps every `RWB_HEARTBEAT_INTERVAL_SECS`. The reconciler treats `running` + `heartbeat_at` older than `RWB_HEARTBEAT_STALE_SECS` (a constant multiple of the interval, **never** a function of job/data size) as abandoned and re-enqueues via an atomic `running→pending` reset. Single-instance. Because the stale threshold is well beyond Dramatiq's own fast worker-death redelivery, the reconciler only fires when Dramatiq couldn't (message genuinely lost, or worker wedged-but-alive) — so the two paths don't overlap in practice, and idempotency covers the rare case they do.

### 5.3a Heartbeat writer that survives long blocking calls (implement exactly this)

**The problem this solves.** Result work includes long, **non-chunkable, blocking** calls (e.g. downloading a large file in one `urlretrieve`/streamed read). If the *same thread* that runs the work is also responsible for stamping the heartbeat, then while it is blocked in the download it **cannot** stamp — `rwb_job_heartbeat.heartbeat_at` goes stale even though the job is perfectly healthy, and the reconciler would wrongly reclaim it and cause a duplicate download. The heartbeat must therefore be emitted by **a separate thread that keeps running while the work thread is blocked.**

**The mechanism.** When a worker claims a job, it starts a small **daemon heartbeat thread** whose *only* responsibility is to write `(rwb_job_id, worker_id, heartbeat_at=now)` every `RWB_HEARTBEAT_INTERVAL_SECS`. The work thread then does the blocking call, untouched and requiring no cooperation. Because they are different threads, the OS scheduler keeps running the heartbeat thread on its timer regardless of the work thread being blocked in I/O. When the work finishes (success **or** exception), the heartbeat thread is stopped. If the **whole process** dies, the daemon thread dies with it — which is correct: a dead process *should* stop heartbeating so the reconciler can recover the job.

**Four details that must be preserved (each fixes a specific bug):**

1. **Stoppable timer via `Event.wait`, not `time.sleep`.** The loop is `while not stop_event.wait(interval): <stamp>`. `Event.wait(interval)` sleeps for the interval **but returns immediately the moment `stop()` is called**, so teardown is instant instead of waiting up to a full interval. `time.sleep` would delay shutdown and can pin the thread past job completion.
2. **`interval` ≪ `stale_threshold`.** Stamp every ~15s against a ~45s stale threshold (≥ 3–4× the interval), so a single missed write — a transient DB blip — does **not** trip a false reclaim. Never set them close.
3. **Guaranteed start-before / stop-after via a context manager.** Wrap the work in `with heartbeating(job_id, worker_id): <do work>` so the thread is started before the work begins and stopped in `finally` no matter how the work exits (return, raise, or cancel). The heartbeat must never outlive the job.
4. **The heartbeat write itself is tiny and independent.** It is a single upsert of the child row — **no lease, no ownership check, no read-modify-write of `rwb_job`.** A transient failure to write is swallowed and retried next tick (logged), because a DB blip must not kill the heartbeat thread; if it persists past the stale threshold the job is legitimately reclaimed, which is the correct outcome.

**Reference shape (illustrative — adapt to the project's db layer and logging):**

```python
import threading
from contextlib import contextmanager

class _Heartbeat:
    def __init__(self, rwb_job_id, worker_id, interval_secs):
        self.rwb_job_id = rwb_job_id
        self.worker_id = worker_id
        self.interval = interval_secs
        self._stop = threading.Event()
        self._thread = None

    def _run(self):
        # Fires immediately once, then every `interval`, until stop() is called.
        while True:
            try:
                upsert_rwb_job_heartbeat(self.rwb_job_id, self.worker_id)  # single tiny write
            except Exception:
                logger.exception("heartbeat write failed for %s", self.rwb_job_id)
                # swallow: retry next tick; persistent failure -> legitimate reclaim
            if self._stop.wait(self.interval):  # returns True immediately on stop()
                return

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

@contextmanager
def heartbeating(rwb_job_id, worker_id, interval_secs):
    hb = _Heartbeat(rwb_job_id, worker_id, interval_secs)
    hb.start()
    try:
        yield
    finally:
        hb.stop()   # ALWAYS stops, even if the work raises

# usage inside the Dramatiq actor, after the atomic pending->running claim:
#   with heartbeating(job_id, worker_id, RWB_HEARTBEAT_INTERVAL_SECS):
#       download_large_file(...)   # long blocking call — no cooperation needed
#   finalize_idempotently(...)     # temp file + atomic rename
```

**Scope note (do not over-build):** this is a *progress* heartbeat, not a lease. There is no `owner_token`, no lease renewal, no ownership gating on the write, and no worker-liveness table (see §6). If a job happens to be short and completes before the first interval elapses, that is fine — the initial stamp on claim plus completion covers it. `asyncio` is **not** appropriate here because the work is a blocking call; a thread is the correct and simplest mechanism.

### 5.4 `request_key` scheme + atomic claim
Producers compute a source-agnostic key so the *same logical work* always yields the *same* key and *different* work never collides:
- `origin=irp_completion`: `irp:{irp_job_id}:{work_type}`
- `origin=analyst_request`: `analyst:{entity_type}:{entity_id}:{work_type}`
- `origin=chained`: `chain:{parent_rwb_job_id}:{work_type}`

Creation: `INSERT ... WHERE NOT EXISTS (request_key)`. Consumption claim: `UPDATE rwb_job SET status='running', claimed_by=:wid WHERE id=:id AND status='pending'` — rowcount is the arbiter. This single transition is the dedup; no owner token needed.

---

## 6. Explicitly OUT of scope — do NOT introduce

- **No `owner_token` / per-job lease / lease renewal.** The heartbeat is a plain progress timestamp emitted by the daemon thread in §5.3a, not a lease; ownership is not gated on it. Preserve the §5.3a threading design (separate heartbeat thread) — do **not** simplify it into stamping from the work thread, which stalls during blocking calls.
- **No worker-liveness table.** Liveness is tracked *per job* (heartbeat), not per worker.
- **No duration-based timeouts / grace windows** anywhere. The only time constant is the heartbeat-staleness threshold, expressed as a multiple of the heartbeat interval, unrelated to job/data size. The reconciler must **not** scan `pending` on a timer.
- **No new statuses** on `rwb_job` or `irp_job`.
- **No changes to `task_instance`** (that is a workflow/domain object under `stage_instance`, not a queue) or to `irp_job`'s existing submission-retry mechanism, beyond the `irp_job_id`-on-`rwb_job` becoming nullable.
- **No reading of Redis AOF/RDB files** for application state; SQL is the inspection surface.

---

## 7. Residual risks (record these; they need explicit acceptance)

1. **Sub-second AOF window (pending, never-claimed).** With `appendfsync everysec`, a hard Redis crash can lose ≤ ~1s of acknowledged writes. A job enqueued in that window that was *never claimed* has no heartbeat, and the reconciler (by design) does not scan `pending` — so that specific work item is silently lost. **Likelihood:** very low (needs a hard Redis crash within the sub-second window for a not-yet-picked-up job). **Full mitigation exists but is rejected for cost:** `appendfsync always` (fsync per write) eliminates it with a throughput penalty. **Recommended stance:** accept; the `rwb_job` row still exists in SQL, so it is *auditable after the fact* even though not auto-recovered.
2. **Wedged-but-heartbeating job.** The heartbeat proves the heartbeat *thread* is alive, not that the underlying work is progressing. If the work hangs while the heartbeat thread keeps stamping, the reconciler won't reclaim it. **Likelihood:** low. **Mitigation:** where cheap, tie the heartbeat to real progress (e.g. only stamp when bytes advance); otherwise this needs human/operational intervention. Accept as a known limit of any heartbeat.
3. **Wasteful (but safe) re-execution on rare overlap.** If a heartbeat lapses transiently (e.g. a DB blip) and the reconciler re-enqueues a job that was actually still running, the work runs twice. Idempotent finalize makes the *result* correct, but the work is duplicated (wasted effort; any non-idempotent external side-effect in a step could double). **Mitigation:** generous stale threshold + strict worker idempotency. Accept.
4. **Reconciler must be single-instance.** Two instances could double-re-enqueue (still safe via the atomic claim, but wasteful). **Mitigation:** operational — run exactly one (folded into the single poller, or one dedicated unit). Accept as ops discipline.
5. **`request_key` discipline.** A wrong key scheme (same key for different work, or different keys across retries of the same work) causes dropped or duplicated work. **Mitigation:** the documented per-`origin` scheme (§5.4) + unit tests. Accept as design discipline, test-enforced.

If job-heartbeat + reconciler were ever deferred, risks (2)–(4) disappear but the **wedged-but-alive worker** case (Dramatiq cannot see it) becomes an unrecovered residual — noting this so the heartbeat's specific value is clear.

---

## 8. Acceptance criteria

- `result_work_item` no longer exists; `rwb_job` + `rwb_job_heartbeat` + `rwb_job_status_kind` exist; `irp_job_id` is nullable; `UNIQUE(request_key)` enforced.
- Poller creates `rwb_job` idempotently on IRP completion; the same completion processed twice yields one row.
- A non-IRP `rwb_job` (analyst-request or chained) can be created, claimed, processed, and recovered — with **no** `irp_job` present.
- Redis runs with AOF enabled in dev, partner-Docker, and prod (verified via `redis-cli INFO persistence`).
- Reconciler recovers a `running` job with a stale heartbeat and never touches a `pending` job; it is single-instance.
- Double-delivery of the same `request_key` results in one effective execution.
- Unit + SQL-tier tests in §4.5 pass; default CI (unit) stays green offline.
- No `owner_token`, per-job lease, worker-liveness table, duration window, or new status was introduced.

---

## 9. Affected-files checklist (grep targets)

Search the whole repo — docs **and** code — for and update every hit:

- `result_work_item`, `result_work_item_status_kind`
- `UNIQUE(irp_job_id, work_type)` / the composite dedup
- `irp_job_id` usages that assume NOT NULL on the work-item table
- "Result work item" / "result work items" (prose, glossary, comments)
- poller code that writes the work-item row
- Dramatiq actor definitions for result/processing work
- the staleness-sweep / "reset running to pending" logic (replace with reconciler)
- Redis start commands / `redis.conf` / `docker-compose` redis service / systemd unit (AOF)
- Alembic `0001_initial.py`
- test fixtures/factories referencing the old table
- `SCAFFOLDING.md` process list, make targets, env example, topology
- `PRD.md` glossary, queue/worker section, adversarial items, locked decisions
- `DATA_MODEL.md` ER, manifest, kind-seed checklist

> Reminder: the file list above is a starting set from a grep of the known structure — if the agent finds the old names anywhere not listed, update those too.