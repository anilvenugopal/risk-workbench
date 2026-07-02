# Metamodel Design — Risk Workbench

**Status:** 2026-07-01 design session. Derives the **required metamodel set bottom-up from
the sequence flows** (`docs/sequence_diagrams/`). This is the **rationale** behind the lean
model now canonical in `DATA_MODEL.md` — *why* each table exists. The workflow / stage /
task / port machinery it argues against lived in the prior `DATA_MODEL.md §6–§7` (now in git
history; see `DATA_MODEL.md §12` for the removal list). The app-side work queue is realized
as CR-001's `rwb_job` (`docs/CR_01__RWB_JOBS.md`). Read alongside `execution-design.md` (the
execution model this schema serves) and `mvp-scope.md`.

**Method.** For every granular and composite flow we ask one question:

> *What has to persist after the HTTP response returns?*

Only constructs that answer it earn a table. Nothing exists "for uniformity." The result
is ~13 meaningful tables (plus a reference cache and audit), each traceable to a flow —
against ~40 tables + ~10 kind tables in the design we're replacing.

---

## 1. Guiding principle (why this is small)

This is a **workbench, not a workflow engine.** The analyst clicks known actions; the
topology of what-follows-what is fixed and lives *in code*, not *as data*. Every table in
the overthrown design that existed to **describe topology as data** — workflow
definitions, stages, task templates, ports, handle-type registry, manifest projection,
version pinning — is solving a problem we do not have.

What we genuinely cannot avoid persisting:

1. **Our own concepts** RM has no notion of (submission = broker package + CRM ID).
2. **Pointers into Moody's** — the names/ids of entities we created, so we can list them,
   hand correct names back to RM, and track their jobs.
3. **In-flight state** RM won't remember for us — async jobs and their heavy tails.
4. **Audit** — who did what, when.

Everything else is read **live from Risk Modeler by name at call time.** See §6.

---

## 2. The metamodel set

```
── Business spine (ours; RM has no concept of these) ──
customer / program
submission            broker package: Name + CRM ID. WORKBENCH-ONLY concept.
app_user / role / user_customer_access

── Entity refs (pointers into Moody's — names + backfilled irp ids) ──
edm        (name, irp_exposure_id?, status)
rdm        (name, irp_id?, edm_id, status)
portfolio  (name, irp_portfolio_id?, edm_id)
analysis   (name, irp_analysis_id?, edm_id, is_group)   ← a GROUP is an analysis
treaty     (name, irp_treaty_id?, edm_id)

── The one thing that needs tracking in flight ──
job        (job_type, irp_id?, status, progress, resource_uri,
            prereq_job_id?,   -- dependency edge: this job waits on that one (e.g. RDM → EDM)
            parent_job_id?,   -- resubmit lineage: this job supersedes that one
            batch_id?)

── Grouping + heavy tail ──
batch            (jobs submitted together; the NOTIFICATION unit — settle + notify once
                  when all members terminal. Only the completion latch is stored; the
                  breakdown is derived. No batch-of-one.)
rwb_job (CR-001: app-side queued work — decoupled from IRP, request_key dedup, nullable
                  job_id lineage. Post-terminal tail: download → load-to-LOSS; plus
                  analyst-request + chained. + rwb_job_heartbeat child for the reconciler.)

── Always ──
user_action                         (audit: every action, incl. synchronous ones)
irp_* reference cache               (model/output profiles, event-rate schemes,
                                     currencies, tags, servers — pick-lists)
```

### 2.1 Each table, and the flow that forces it

| Table | Forced by | Why it must persist |
|---|---|---|
| `submission` | `create_submission` | RM has **no** submission concept. Name + CRM ID are ours; they wrap the EDM/RDM sets. |
| `edm` / `rdm` | `edm_upload`, `rdm_upload`, `create_submission` | `irp_exposure_id` / `irp_id` don't exist until the import job is `FINISHED`; the poller backfills them. We must hold the name (what we submitted) before the id exists. |
| `portfolio` | `create_subportfolio`, `create_subportfolios_by_lob`, `view_portfolio` | The analyst picks a portfolio by name when configuring analyses; we remember the ones we made. `irp_portfolio_id` is written synchronously (create returns 201). |
| `analysis` (incl. groups) | `run_analysis`, `submit_analyses`, `group_results` | `analysisId` resolves only after `FINISHED`. **A group is stored here too** (`is_group`) — `group_results.md` shows a group is read/viewed/exported identically to any analysis. Not a separate entity type. |
| `treaty` | `treaty_view_edit`, referenced in `run_analysis` | Analyses reference treaties by name; we hold the mapping to `irp_treaty_id`. |
| `job` | every async flow (import, GeoHaz, analysis, grouping, export) | The single tracked-in-flight unit. Carries the mirrored RM status, `irp_id`, `progress`, `resource_uri`, resubmit lineage. |
| `batch` | `submit_analyses` (×N), `run_geohaz` (×N), `group_results` (×N) | The **notification unit** — notify once when the whole group settles, not per member. Also scopes partial-failure retry and the grouped monitor view. Only the completion latch is stored; the breakdown is derived. **No batch-of-one.** |
| `rwb_job` | `export_to_loss_repo`, `view_results`→load | The heavy tail *after* the RM job is terminal (download + load-to-LOSS). "Done" = loaded, **not** RM-`FINISHED`. |
| `user_action` | all | Audit. Synchronous ops (subportfolio create, treaty CRUD) live here + a toast — they create no job. |
| `irp_*` cache | `submit_analyses` load-pick-lists phase | Slow-changing reference data (profiles, schemes, currencies, tags, servers) so pick-lists resolve locally, not per-submit. |

### 2.2 Consolidations the flows earned us

- **`group` folds into `analysis`** via `is_group`. `group_results.md`: a group *is* an
  analysis (`isGroup`); it feeds View Results and Export with the same shape. Grouping is
  another way to *make* an analysis, not a new thing to model.
- **No separate `task_instance` + `irp_job`.** One `job` row per IRP op. (The overthrown
  design had both.)
- **Submission wraps EDMs; the EDM is the modeling anchor one level down.** Portfolios,
  analyses, groups, treaties all belong to a single EDM.

### 2.3 Persistence tiers — a construct exists only when it pulls weight

| Tier | Exists when | Examples |
|---|---|---|
| **Entity** | an op creates a durable artifact | edm, rdm, portfolio, analysis, treaty |
| **Job** | the op must be tracked *after* the response — async (poll) or heavy-deferred | import, GeoHaz, analysis, grouping, export |
| **Batch** | multiple jobs submitted together; the notification unit (settle + notify once) + partial-failure retry | the 50–150 analysis suite; multi-portfolio GeoHaz; multi-group |
| **rwb_job** | heavy follow-up after a job is terminal | download + load-to-LOSS |
| **Audit** | always | every action |

**Synchronous single ops create no Job and no Batch.** `create_subportfolio` →
`create_portfolio` returns a `portfolioId` in-request → persist the `portfolio` entity +
write audit → done. Nothing is in flight, so nothing shows in the job monitor.

### 2.4 `rwb_job` — the app-side job (and why it's separate from `job`)

**Which table? The bright line.** A row exists only for a thing whose **outcome is tracked
and retried on its own.** That gives two kinds of job — plus a deliberate "no row" case:

- **`job` = an RM job.** Risk Modeler performs it; RM owns the outcome and we learn it by
  polling. Retry = **resubmit** (a fresh RM op).
- **`rwb_job` = an app-side job.** *We* perform it on a Dramatiq worker; we own the outcome.
  Retry = **re-run in place.** *(CR-001 renamed `result_work_item` → `rwb_job` and **decoupled
  it from IRP** — nullable `job_id` lineage + a source-agnostic `request_key` — precisely
  because this is "simply our own job" with non-IRP sources too: an analyst-requested push, a
  chained tail. The old name and the hard `irp_job` parent both hid that.)*
- **Neither → no row.** App-side work whose *only* outcome is "*an RM job got submitted*" is
  **not** its own row — it is that job's **`SUBMITTING`** phase.

The single test — *does this have an independently-tracked, independently-retryable
outcome?* — settles the two cases that look alike:

| App-side work | Independent outcome? | Where it lives |
|---|---|---|
| **Upload `.bak` to S3** *before* an EDM import | **No** — its outcome *is* "the import submitted." If it fails, the **`job`** goes `ERROR` and you resubmit the job. | `job.SUBMITTING` — **no separate row** |
| **Download + load-to-LOSS** *after* an export | **Yes** — the RM job already `FINISHED`; the load can fail and retry in place without touching RM. | its own **`rwb_job`** |

So an `rwb_job` is specifically **app-side work that runs *after* an RM job is terminal (or
after another `rwb_job`) and carries its own pass/fail.** Work that merely *gets a job
submitted* is that job's `SUBMITTING` phase, never an `rwb_job`.

**Why it isn't folded into `job`.** It's tempting — an app job is a `job` with no `irp_id`,
and we already have those (`UNSUBMITTED`, `ERROR`). But "no `irp_id`" is a *symptom*; the real
distinction is lifecycle and retry:

| | `job` (RM job) | `rwb_job` (app job) |
|---|---|---|
| Who does the work | **Risk Modeler** — we *observe* it | **us** — we *perform* it |
| Driven by | the **poller** (poll until terminal) | a **Dramatiq worker** (message → do the work) |
| Fails when | RM fails the op | *our* download/load fails **even though RM succeeded** |
| Retry | **resubmit as a new job**, original → `SUPERSEDED` (RM consumed the submission — can't re-run in place) | **re-run the same row in place** (idempotent, `attempt_count++`) |

That retry difference is **intrinsic, not incidental.** An RM job physically can't be retried
in place (the submission is spent, so retry mints a fresh op + a new `irp_id`); an app job's
target is fixed and idempotent, so it simply runs again. Merging would cram two incompatible
retry machines behind one `job_type` switch — and it fails the very test that justified
folding `task_instance` into `job`: *that* merge was right because the two were **1:1 and the
same operation** (redundant); `job` → `rwb_job` is **1:many and different operations** (one
export spawns a `download`, then a `load_to_loss`). Same-row-two-meanings is exactly what we
removed. *(The one thing that would flip this: if an app job ever grew poll-like behavior,
the two would converge.)*

Concretely, two ops produce rwb_jobs:
- **Export** `FINISHED` → `download_export_file` rwb_job → (on success) `push_results_to_loss_repo` rwb_job.
- **Analysis** `FINISHED` → `retrieve_analysis_results` rwb_job (cache results locally, §6).

It **defines the real "done"** — the derived `LOADING` state (§5) is "RM job `FINISHED` but an
`rwb_job` still `pending`/`running`." **Durability (CR-001):** the row is written *before* the
Dramatiq message, and Redis is durable (AOF), so an acknowledged enqueue survives a broker
crash — *pending-lost stops being a case we must detect*. A **single-instance reconciler**
re-enqueues only rows stuck `running` with a stale per-job heartbeat (`rwb_job_heartbeat`); it
**never scans `pending`**, so no duration-based window enters the design. Dramatiq covers
worker-death redelivery, task-failure retries, and graceful-shutdown requeue.

---

## 3. Chaining — how "what happens next" actually works

There are only **two ways** a subsequent activity gets kicked off, and neither is a stored
DAG. There is **no "current stage" pointer** and no workflow definition — "where are we"
is always *re-derived* from entity + job state. The dependency **rule** ("an RDM import
depends on its EDM import") lives in **code**; the only thing stored is the **instance
edge** — a `prereq_job_id` on the dependent job, so the poller knows which waiting job to
release. One pointer per dependent job, not a topology.

### 3.1 The two mechanisms

**Mechanism A — Poller-driven automatic follow-up (no human).**
When the poller sees a job reach a terminal status, it runs a fixed **on-terminal handler
keyed by `job_type`.** *This handler is where an automatic "next" is defined* — in code, as
a small dispatch table, not a DB row. On success it backfills ids, then does exactly one of:
**release a waiting dependent job** (its prereq is now met → enqueue it), **write a
`rwb_job`** (§2.4), or nothing. That release/write **is** the "kick off." On
failure it **`BLOCKED`s** any dependents (their prereq can't be met until it's rectified).

| Job type reaches this status | On-terminal handler does |
|---|---|
| EDM import `FINISHED` | backfill `edm.irp_exposure_id`; **release the waiting RDM import job** (its `prereq_job_id` is now `FINISHED` → enqueue it) |
| EDM import `FAILED`/`CANCELED` | **`BLOCKED`** the waiting RDM import job (reason: "EDM import failed") |
| RDM import `FINISHED` | backfill `rdm.irp_id` |
| Analysis `FINISHED` | write a `retrieve_results` app job (cache results, §6) |
| Export `FINISHED` | write a `download` app job (→ then `load_to_loss`) |
| any member reaches terminal | **if all the batch's members are now terminal, settle the batch (once) and enqueue one `notify`** carrying the derived breakdown; else nothing. A single (non-batch) job enqueues its own `notify`. |

**Mechanism B — Analyst-gated "what's next" (human decides).**
Steps that require judgment (which portfolios, which analyses, which settings) never
auto-run. Instead the UI computes which actions are **enabled** from a pure function of
entity state — the prerequisite table below. The button is lit, or greyed with a reason;
the analyst clicks when ready. There is **no** stored "stage 4 of 8."

| Op | Enabled once these exist / are `FINISHED` |
|---|---|
| EDM import | server exists; EDM name not already in RM |
| RDM import | its EDM imported (`FINISHED`) |
| Create subportfolio | EDM exists + at least one portfolio exists |
| GeoHaz | EDM + portfolio exist |
| Treaty create/edit | EDM exists |
| Analysis | EDM + portfolio (+ named treaties) exist |
| Grouping | member analyses/groups exist (group-of-groups: members `FINISHED`) |
| Export → Loss Repo | analysis/group exists (`FINISHED`) |

The distinction is the whole point: **mechanical follow-up auto-fires (Mechanism A);
anything requiring judgment waits for a click (Mechanism B).** A broker package is one
intent, so EDM→RDM auto-fires; picking analysis settings is judgment, so it waits.

### 3.2 Worked example — `create_submission` (EDM → RDM: eager creation, gated submission)

The action fully specifies **both** jobs (both names, both files), so we **create both up
front** — the monitor immediately shows "we intend to attempt both." Creating the row and
submitting it are separate steps: the RDM job exists right away but isn't *submitted* until
the EDM is `FINISHED`.

1. **User submits.** App writes, in one transaction: `submission` (Name + CRM ID); `edm`
   and `rdm` rows; **both jobs eagerly** — EDM import `job` (`UNSUBMITTED`, heavy) and RDM
   import `job` (`UNSUBMITTED`, heavy, `prereq_job_id` → the EDM job).
2. The EDM job has no unmet prereq → the heavy worker claims it → `SUBMITTING` (uploading
   the `.bak` to S3) → submit ok → `QUEUED` + `irp_id`. The RDM job's prereq isn't met yet,
   so it **stays `UNSUBMITTED`** — waiting, not alarming.
3. Poller polls the EDM job until `FINISHED` → backfills `edm.irp_exposure_id` →
   **on-terminal(EDM import) sees the RDM job whose `prereq_job_id` is now `FINISHED` and
   releases it** (enqueues it). ← *this is the entire EDM→RDM chain:* a `prereq_job_id`
   pointer + a handler that releases on the prereq's success. No stage object, no
   `current_step`.
   - **If the EDM job `FAILED` instead:** on-terminal marks the RDM job **`BLOCKED`**
     ("EDM import failed"). The analyst resubmits the EDM; on *its* `FINISHED` the RDM job
     flips `BLOCKED → UNSUBMITTED → SUBMITTING` automatically — the intent was never lost.
4. Heavy worker uploads the RDM + submits → RDM job `QUEUED`.
5. Poller polls the RDM job until `FINISHED` → backfills `rdm.irp_id`.
6. **"Submission ready"** is *derived* — both jobs `FINISHED`. App notifies. Nothing stores
   that as a state; it is a rollup over the two jobs.

### 3.3 Worked example — `export_to_loss_repo` (job → download → load: a tail chain)

1. User submits export → export `job` (light submit) → `QUEUED`.
2. Poller polls until `FINISHED` → **on-terminal(export) writes a `rwb_job`
   (`download`, `pending`)** and enqueues the worker. RM's job is done; the *op* is not.
3. `download` worker pulls the file from S3 → on success **writes the next
   `rwb_job` (`load_to_loss`)** and enqueues it.
4. `load_to_loss` worker bulk-inserts into the LOSS SQL Server → `succeeded`.
5. **"Export done"** is *derived*: export job `FINISHED` **and** all its app jobs
   `succeeded` (the `LOADING` semantic, §5). It is **not** done at RM-`FINISHED`.

The tail sequence is defined by one rule: **the poller writes the head app job; each
worker writes the next on success.** The order lives in the worker registry (code), not a
DB DAG.

### 3.4 Why no ports / handle-type registry / staleness propagation

That machinery exists to model "an upstream output went stale and invalidated a pinned
downstream input." **That failure mode cannot occur here** — every step resolves its inputs
*live from RM by name* at call time (`search_edms`, `search_portfolios`,
`search_analyses`). There is no pinned upstream artifact to go stale. `group_results.md`:
*"Coupling is name-based, not id-based."* The notebook framework's `validate_batch` already
answered "what chains into what" from **entity existence alone** — no type algebra needed.

---

## 4. Error handling & retry — a four-surface taxonomy

The design's answer to "how do you handle errors" is: **we can always tell you *which* of
four things failed, and retry exactly that unit** — not the whole batch, not the whole
chain. The single-endpoint rule (loop the single submit, capture each `job_id`) is what
makes partial-failure retry fall out for free.

| Failure surface | When | State | Retry |
|---|---|---|---|
| **Prereq failure** | An upstream job this one depends on failed, so it can't be submitted. `create_submission`: EDM import fails → RDM can't proceed. | `BLOCKED` (app-only, no `irp_id`) | Rectify the prereq; on its `FINISHED` the dependent **auto-releases** (`BLOCKED → UNSUBMITTED → SUBMITTING`) — intent never lost |
| **Submission failure** | The submit to RM fails *before* RM accepts it (dup name, unresolvable reference data, network). `run_analysis.md`: the submit fans out into many RM reads — any can fail. | `ERROR` (app-only, no `irp_id`) | Retried automatically (Dramatiq backoff for heavy; retry actor for light) up to a max, then terminal — analyst resubmits |
| **Run failure** | RM job reaches `FAILED`/`CANCELED` server-side (it got an `irp_id`, ran, failed). | `FAILED` (mirrored from RM) | Resubmit as a **new** job; original → `SUPERSEDED` via `parent_job_id` |
| **Follow-up failure** | Heavy post-terminal tail (download / load-to-LOSS) fails. | `rwb_job.status_code=failed` + `error_detail` | Re-enqueue (idempotent worker); reconciler re-enqueues stale-`running` rows (heartbeat-based, CR-001) |
| **Partial batch failure** | `submit_analyses` × 150: 3 fail, 147 succeed. | Batch `PARTIAL`: 147 `FINISHED` / 3 `ERROR`|`FAILED` | **Resubmit just the 3** — trivial because each is an independent job; batch returns toward `COMPLETED` |

What the metamodel needs to support this: `job.status` distinguishing **prereq-fail
(`BLOCKED`)** from **submission-fail (`ERROR`)** from **run-fail (`FAILED`)** — three
different causes, three different fixes; `parent_job_id` for re-run lineage;
`rwb_job` for the idempotent tail; `batch` for partial-failure visibility. All in
§2 and §5.

**Why the single-endpoint rule matters here.** The plural helpers are fail-fast with no
rollback — a partial failure orphans already-submitted jobs with no local record, making
"resubmit just the 3" unanswerable. Looping the single submit and capturing each `job_id`
is what keeps every job independently addressable. See `prefer-single-irp-endpoints`.

---

## 5. State model

**Job is the primary tracked unit** (there is no separate task_instance). Its status is one
of two origins: **mirrored** verbatim from Moody's (only valid once we hold an `irp_id`), or
**app-local** (the pre-submission and lineage states Moody's knows nothing about).

### 5.1 `job.status`

**Mirrored from Moody's** (stored verbatim; a new RM status never crashes the poller):

| Status | Meaning |
|---|---|
| `PENDING` | RM has the job; precedes `QUEUED`. |
| `QUEUED` | On RM's queue; precedes `RUNNING`. |
| `RUNNING` | RM is processing it. |
| `FINISHED` | Done. **The only success** — terminal ≠ success, always inspect it. |
| `FAILED` | RM ran it and it failed. |
| `CANCEL_REQUESTED` → `CANCELING` → `CANCELED` | The cancel lane. |

> Note the ordering: Moody's is **`PENDING` → `QUEUED`** (not the reverse). Spellings are
> Moody's own: `CANCELING`, `CANCELED` (one `L`).

**App-local** (no `irp_id`; the states RM can't tell us about):

| Status | Meaning | Has `irp_id`? |
|---|---|---|
| `UNSUBMITTED` | Created; prereqs met (or none) **or still ongoing** — ready/waiting to submit. **Normal, nothing wrong.** | no |
| `SUBMITTING` | A worker is actively uploading + submitting *right now*. Meaningful for **heavy** jobs (multi-GB S3 upload inside the submit); a blink for light jobs. | no |
| `BLOCKED` | A prerequisite **failed** — needs rectifying before this can proceed. The **only** "needs attention" pre-submission state. | no |
| `ERROR` | Submission itself failed — the op never reached Moody's (dup name, bad reference data, network). Retried automatically up to a max; terminal once exhausted. | no |
| `SUPERSEDED` | Replaced by a resubmit — a failed job that a re-run stands in for. Terminal; excluded from batch recon. | (had one) |

```
   BLOCKED ◀──prereq failed── UNSUBMITTED ──claimed──▶ SUBMITTING ──submit ok──▶ PENDING ─▶ QUEUED ─▶ RUNNING ─▶ FINISHED
      │      ──prereq fixed──▶     ▲                        │                                              ├─▶ FAILED
      │                            │ (auto-release)          └──submit fails, retries exhausted──▶ ERROR   └─ cancel lane:
      └──cancel before submit──▶ CANCELED (app-applied)                                                    CANCEL_REQUESTED
                                                                                                            ─▶ CANCELING ─▶ CANCELED

   FAILED / ERROR ──resubmit──▶ [new job];  original ──▶ SUPERSEDED
```

- **Terminal:** `FINISHED` · `FAILED` · `CANCELED` · `ERROR` · `SUPERSEDED`. (`BLOCKED` is
  *not* terminal — it's recoverable once the prereq is fixed.)
- **`LOADING` is derived, not stored:** `FINISHED` **and** a `rwb_job` still
  `pending`/`running` (§2.4). "Export done" = loaded into LOSS, not merely RM-`FINISHED`.
- **`ERROR` vs `FAILED`** is the load-bearing distinction: `ERROR` = never reached Moody's
  (no `irp_id`, submission-side); `FAILED` = Moody's ran it and it failed (has `irp_id`).
  Different cause, different retry. See §4.

### 5.2 `batch.status` — the notification unit

A batch groups jobs submitted in one action (`job.batch_id`). Its **one load-bearing job
is notification granularity**: the analyst wants *"your 150-analysis run finished — 147 ok,
3 failed,"* **not** 150 per-analysis pings. So the batch tracks exactly one fact that
genuinely needs persisting — *has this group collectively settled, and have we notified?* —
because that notification must fire **exactly once.**

```
RUNNING ──(all members terminal)──> COMPLETE     ← fires ONE notification, then latched
   ▲                                    │
   └────────(resubmit reopens)──────────┘         re-running failed members reopens it
```

- **Stored:** the completion latch (the `RUNNING → COMPLETE` transition), guarded so the
  notification is emitted once. That is the *only* batch state that needs tracking.
- **Derived on demand (never stored, never recon'd into columns):** the breakdown — counts
  by member status, clean vs partial ("147 `FINISHED` / 3 `FAILED`"), whether it was fully
  canceled. A `GROUP BY batch_id` over the members; trivial at ~150 rows.
- **`SUPERSEDED` members are excluded** from both the completion check and the breakdown —
  a re-run's superseded original neither holds the batch open nor shows in the tally.
- **Not monotonic:** resubmitting failed members reopens `COMPLETE → RUNNING`; the batch
  re-settles and re-notifies when the re-runs finish. The batch answers "is everything I
  asked for currently done?"
- **No batch-of-one:** a single async op notifies on its *own* terminal — a batch exists
  only when >1 job is submitted together, precisely because it is the notification unit.

Partial retry is unchanged: `resubmit WHERE batch_id = X AND status IN (FAILED, ERROR)`.

### 5.3 `rwb_job.status`

`pending → running → succeeded | failed` (a **kind table**, `rwb_job_status_kind` — internal,
stable values, CR-001). "Abandoned" is not a status: it is a `running` row with a stale
`rwb_job_heartbeat`, which the single-instance reconciler resets `running → pending` and
re-enqueues. The reconciler never scans `pending`.

**Resubmit lineage:** a failed job (`FAILED` or `ERROR`) is resubmitted as a **new** job
whose `parent_job_id` points at the original; the original transitions to `SUPERSEDED`.

`job_type` / `batch_type` / all statuses are **plain strings** (Python enums), never
DB-constrained.

---

## 6. Data posture — cache by **mutability**, never mirror the exposure tree

The rule is **not** "names/ids only." The axis that matters is **mutability + ownership**:
cache what is immutable or slow-changing; never treat mutable, RM-owned metadata as local
truth; never mirror the exposure tree. Three buckets:

| Bucket | Examples | Policy |
|---|---|---|
| **Immutable once created** | analysis **results** (ELT / EP / PLT / stats) | **Cache durably.** Results never change once the analysis exists, so a local copy *cannot* drift. Caching avoids re-pulling large result sets from RM on every view. |
| **Slow-changing reference data** | model profiles, output profiles, event-rate schemes, currencies, tags, servers | **Cache with a manual "Sync IRP Metadata" refresh.** Rarely (or never) changes; tolerate mild staleness; re-sync on demand. Pick-lists then resolve locally, not per-submit. |
| **Mutable, RM-owned** | entity **names**, entity statuses; portfolio / account / policy / location data | **Do not treat as authoritative local truth.** The `irp id` is the durable key. A name can be edited in RM and drift from our copy — so a stored name is a *label we assigned*, refreshable, not a source of truth. Exposure-tree detail is read **live**. |

**What changed from the earlier draft.** We *do* cache results now (this reverses the
earlier read-live-only stance): they are immutable, so it is safe caching, not a sync
risk. Storage is hybrid — SQL metadata (aal, record counts, file paths) + row-level data
in Parquet, per `DATA_MODEL.md §6`. Retrieval is the `retrieve_analysis_results` `rwb_job`
fired on analysis `FINISHED` (§2.4).

**Names are the drift risk, not results.** We hold entity names because coupling is
name-based (`group_results.md`) and irp ids arrive late (backfilled). But a name is
*mutable* in RM. So: once the `irp id` is backfilled, prefer it as the resolution key;
treat the stored name as a refreshable label; reconcile on access (or a light re-sync)
where it matters. *(How aggressively to reconcile is open — see §9.)*

**We never mirror the exposure tree.** Portfolio contents, accounts, policies, locations
are read **live**, one entity at a time, for detail views (`view_portfolio`,
`view_results`) — on-demand REST reads, no bulk scan, no local copy.

**Performance posture** (be smart about *which* calls, not *fewer features*):
- Cache buckets 1 + 2 so the hot paths (pick-lists; viewing finished results) hit local
  storage, not RM.
- **Hoist shared resolutions out of the per-item loop.** In `submit_analyses` × 150 the
  EDM / portfolio / treaties are shared — resolve once, reuse across all 150. Per-item RM
  calls reduce to the unavoidable (the POST + dup-name check).
- **List views hit zero RM calls.** "My jobs / my submissions / what I created" come
  entirely from our tables. We hit RM only for live detail on mutable data, lazily, one
  entity at a time.

---

## 7. The case against the overthrown design (`DATA_MODEL.md` §6–§7)

Same jobs, radically less machinery:

| Job to be done | Overthrown design | This design |
|---|---|---|
| "What can I do next?" | `stage_instance` + `stage_exec_status` machine, `current_step` | Prerequisite gate computed from entity state (§3) |
| Chain EDM→RDM, export→load | `task_template`/`port_template`/`handle_type_kind` registry + `task_input`/`task_output` binding | Entity-prereq gate + linear `rwb_job` chain in code |
| Data-passing between steps | Typed ports, `accepts_types`/`emits_rule`, `is_stale` propagation | Resolve live from RM by name — nothing to pass or stale |
| Evolve workflow definitions safely | Manifest projection + content-hash startup guard + version pinning | Flows *are* the definitions, in code — nothing to project |
| Track an async op | `task_instance` **and** a separate `irp_job` | One `job` row |
| Authoring vs running | 2 event streams × (workflow, stage, task) = 6 event tables | One config-and-submit action + `user_action` audit |

**The core mismatch:** that design models a *workflow engine for arbitrary authored DAGs.*
Our flows describe a *workbench where an analyst clicks known actions.* The topology is
fixed and lives in code, so every table that exists to describe topology as data is dead
weight. ~40 tables + ~10 kind tables collapse to ~13 tables, each forced by a flow.

### 7.1 What the overthrown design was actually solving for

It is a **generic workflow-authoring engine** — the schema you'd build if the requirement
were *"let users visually compose arbitrary DAGs of steps, with typed data flowing between
nodes, evolve those definitions over time, and run many instances against them."* Every
heavyweight piece is a tell for **that** problem, not ours:

- **Typed ports + `handle_type_kind` compatibility** (`accepts_types` / `emits_rule`) →
  node-graph validation ("can this output feed that input?").
- **`workflow_definition` + manifest projection + content-hash guard + version pinning** →
  definitions are *data that evolves*, and in-flight instances must be frozen at the version
  they started under.
- **`is_stale` propagation** → an upstream re-run invalidates downstream pinned inputs.
- **Dual authoring/execution event streams × 3 levels** → auditing how a definition was
  *composed*, separately from how it *ran*.

That is a low-code pipeline builder (node-based ETL / automation tool). Most likely origin:
**inherited from the prior "workflow automation tool"** — where node authoring genuinely
*was* the point — and carried here by speculative generality before this app's actual shape
(analyst clicks known actions) was pinned down. It isn't a bad engine; it's an engine for a
different product.

### 7.2 The one axis that matters: where topology lives

Everything reduces to a single decision — **is the topology of what-follows-what a
user-facing variable (→ store it as data) or a fixed product decision (→ put it in code)?**

- **Engine = topology-as-data.** Right when end-users author workflows and you can't deploy
  for each new shape.
- **This design = topology-as-code.** Right when the flows *are* the product and only
  engineers change them.

For a cat-modeling workbench, topology is a fixed product decision — so the engine is
solving a problem we don't have, and paying for it in ~30 tables.

### 7.3 What we genuinely give up (honest accounting)

| # | We give up | Does it matter here? | Cost to regain later |
|---|---|---|---|
| 1 | **Runtime-authored workflows** (add/reorder steps as data, no deploy) | Only if *non-engineers* must define new flows. No evidence the client wants this. | High — reintroduce a definition layer |
| 2 | **Multi-parent DAG dependencies** (a job waits on *all* of {A,B,C}; diamonds) | Maybe someday. `prereq_job_id` is one edge — can't express fan-in. | **Low** — add a `job_prereq(job_id, prereq_job_id)` join table; purely additive |
| 3 | **Automatic staleness / invalidation tracking** (`is_stale`) | **The one real loss.** Re-import an EDM → nothing flags "the 40 analyses built on the old exposure are now stale." | Medium — deliberate re-add if wanted (see §9) |
| 4 | **Version pinning of in-flight definitions** | No — there are no user definitions to pin; "definitions" are code, versioned in git. | N/A |

Items 1 and 4 are non-losses for this product. Item 2 is a small additive change the day we
need it. **Item 3 is the only thing to actually keep on the radar** — and note it is a
*detection* gap (we don't notice the invalidation), not an error-handling gap: resolving
live-by-name removes the "stale pinned input" bug entirely, but nothing tracks the semantic
"downstream is now built on outdated upstream."

### 7.4 Future automated workflows — ability vs. difficulty (not a one-way door)

**We do not lose the ability, and for the workflows this app will realistically want, it
gets *easier*.** Adding a chained step here is: (1) a new `job_type` string, (2) one row in
the poller's on-terminal dispatch table (code: "when X finishes, release Y / write
app job Z"), (3) optionally a `prereq_job_id` edge. No schema migration. The engine's
equivalent is a new `task_template` + `port_template` + handle wiring + projection regen +
version bump — **more** work per new chain, not less, as long as *engineers* define it.

Difficulty rises on exactly two axes: **fan-in / diamonds** (→ the additive `job_prereq`
table) and **end-users authoring shapes at runtime** (→ the engine's whole reason to exist,
deferred). Neither is a one-way door. The `job` table is a *foundation you grow*, not a wall
you tear down: one-edge → many-edge is additive; a definition layer, if ever needed, sits
*on top of* `job` (it would *generate* `job` rows) rather than replacing it. **You can go
lean now and grow toward the engine incrementally, paying only for capabilities you turn out
to need. The reverse — starting with the full engine and simplifying — is the expensive
direction.**

### 7.5 Auditability — equal or better

Execution audit is as good or better: `job_status_event` captures every status transition;
`user_action` captures *every* action including the synchronous ops (subportfolio create,
treaty CRUD) the engine tended to bury inside task events. The one thing that *moves* is
**authoring audit**: the engine's `*_comp_event` streams recorded how a definition was
composed/edited over time — but there is no user-authored definition here, so that history
lives in **git** (blame, PR review, commit log). For a fixed-topology app, git history is a
*better* record of "how did this flow change, who approved it" than `task_comp_event` rows.
Auditability doesn't take a hit; its authoring half relocates from DB to version control,
which is the more appropriate home.

### 7.6 Error handling — better

A strength of the pivot, not a casualty. The engine's error story was diffuse —
`task_status=failed` + a dynamic stage `ERROR` rollup + a workflow-level `failed` that (per
`DATA_MODEL.md`'s own note) had *no defined transition reaching it*. This design has the
explicit five-surface taxonomy (§4) — `BLOCKED` / `ERROR` / `FAILED` / app-job-failed /
batch `PARTIAL` — each with a distinct cause→fix and clean partial-batch retry, which the
single-endpoint rule makes fall out for free. The only error-adjacent thing we drop is the
typed-port guardrail (refusing to run a task whose input went stale) — which is item 3
above: prevention-by-detection, replaced by prevention-by-resolve-live.

**Bottom line.** We're trading a *workflow-authoring platform* for a *fixed-flow
workbench*, and the app is a fixed-flow workbench. The real, non-rhetorical costs are
multi-parent dependencies (cheap to add later) and automatic staleness tracking (§9). Both
auditability and error handling come out ahead. And this is the low-regret direction — it
grows toward the engine additively if we're ever wrong; the engine cannot shrink toward lean
without a rewrite.

---

## 8. Indicative schema (refine when building)

```
submission(id, program_id, customer_id, assigned_analyst_id, name, crm_id, authoring_status, ...)
edm(id, submission_id, customer_id, name, irp_exposure_id?, server_name, status, deleted_at, ...)
rdm(id, submission_id, edm_id, customer_id, name, irp_id?, status, deleted_at, ...)
portfolio(id, edm_id, customer_id, name, irp_portfolio_id?, created_by_job_id?, ...)
analysis(id, edm_id, batch_id?, customer_id, name, irp_analysis_id?, is_group,
         origin[own|broker], status, ...)
treaty(id, edm_id, customer_id, name, irp_treaty_id?, ...)
batch(id, submission_id, edm_id?, batch_type, label?,
      status,                                     -- RUNNING | COMPLETE (latch); breakdown derived
      settled_at?,                                -- set once, when all members first terminal → notify guard
      created_by, created_at, ...)
job(id, submission_id, edm_id, batch_id?,          -- batch_id null for single async ops
    job_type, irp_id?, status, progress, error_message,
    resource_uri?,                                 -- captured at submit; needed for results
    source_path?,                                  -- staged .bak / downloaded zip
    prereq_job_id?,                                -- dependency edge (RDM waits on EDM); drives release/BLOCKED
    blocked_reason?,                               -- set when status=BLOCKED (which prereq failed)
    parent_job_id?,                                -- resubmit lineage (this job supersedes that one)
    <entity ref: portfolio_id? analysis_id? rdm_id?>,   -- data-lineage convenience
    submitted_at, completed_at, last_tracked_at, ...)
rwb_job(id, request_key UNIQUE, origin, job_id?, work_type, status_code, customer_id,
        payload, error_detail, attempt_count, claimed_by?, completed_at?, ...)   -- CR-001; job_id nullable/lineage
rwb_job_heartbeat(rwb_job_id UNIQUE, worker_id, heartbeat_at)                     -- CR-001; reconciler progress signal
user_action(id, actor, action, submission_id?, <entity refs>, detail, ts)
-- analysis result cache (immutable): analysis_result_meta + Parquet, per DATA_MODEL.md §6
-- irp_* reference cache tables per DATA_MODEL.md §7 (kept as-is)
```

- Denormalized `submission_id` + `edm_id` on `job` so the monitor filters/groups without
  joins.
- `job_type` / `batch_type` / statuses are plain strings.
- Status is event-sourced where `DATA_MODEL.md` conventions require (append event row +
  stamp cached current in one transaction via `get_connection("WORKBENCH")`).

---

## 9. Open decisions

1. **Job / failure status vocabulary — settled** (2026-07-01, §5): Moody's statuses
   mirrored verbatim + app-local `UNSUBMITTED` / `SUBMITTING` / `BLOCKED` / `ERROR` /
   `SUPERSEDED`. `BLOCKED` = prereq **failed** (needs attention); `UNSUBMITTED` = prereqs
   ongoing or ready (normal). Jobs are **created eagerly** where the action fully specifies
   them; submission stays gated on the prereq.
2. **`create_subportfolios_by_lob`: sync or job?** — TBD, and **cannot be settled from the
   flows.** The integration package doesn't yet expose create-portfolio-by-filter; the
   sync-vs-job answer depends on what the real endpoint does. **Action:** implement + test
   the endpoint, then decide. Doesn't change the table set — only which path the op takes.
3. **Name reconciliation strategy** — entity names are mutable in RM and can drift from our
   copy (§6). How aggressively do we reconcile: resolve by `irp id` once backfilled and
   never trust the stored name? refresh names on access? a periodic light re-sync? Pick a
   policy when we detail the resolve-by-name paths.
4. **Result caching confirmed IN** (2026-07-01): results are immutable, so we cache them
   (hybrid SQL + Parquet, `DATA_MODEL.md §6`). *(Reverses the earlier read-live-only
   draft.)*
5. **`batch` confirmed in scope** (2026-07-01) — as the **notification unit**: the analyst
   is notified once when a whole batch settles, not per member. Minimal machine
   (`RUNNING → COMPLETE` latch, guarded for exactly-once notify); breakdown derived; reopens
   on resubmit; **no batch-of-one.** (This is the load-bearing reason batch persists — the
   advance-gate role it played in the automation tool does *not* apply here, since nothing
   auto-advances.)
6. Currency assignment point in RM; whether new analyses write back to on-DB RDMs (open in
   `mvp-scope.md §7`).
7. **Staleness / invalidation tracking — deliberately dropped, flagged** (§7.3 item 3). The
   overthrown design's `is_stale` propagation was the one genuinely useful capability we let
   go. We resolve inputs live-by-name (which removes the "stale pinned input" bug), but
   nothing tracks the *semantic* invalidation: re-importing an EDM does **not** flag the
   analyses built on the prior exposure as stale. Accepted for MVP (no mid-cycle
   re-import expected). **Revisit if** Cincinnati Re re-imports exposure after downstream
   work exists — the fix is a targeted invalidation check (compare an analysis's source
   `edm.irp_exposure_id` / import job against the current one), not a return to the engine.
8. **Multi-parent job dependencies — additive when needed** (§7.3 item 2). `job.prereq_job_id`
   is a single edge; fan-in ("wait on all of {A,B,C}") isn't expressible. Deferred until a
   flow needs it; the fix is a `job_prereq(job_id, prereq_job_id)` join table, no rewrite.

---

**Status of the amend list:** `DATA_MODEL.md` now *is* this lean model (the workflow-engine
machinery is removed; `rwb_job` merged in per CR-001). `PRD.md` is amended per
`execution-design.md §11`, and the constitution's workflow-engine articles are rewritten
(v2.0.0). This doc remains as the standing rationale.
