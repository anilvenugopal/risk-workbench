# Execution & Construct Design — Risk Workbench

**Status:** The **2026-06-30** design session — the origin of the lean execution model. Its
*structural* decisions stand and are authoritative for the execution model (light/heavy work
split, SQL-truth + Redis-dispatch, single-shot poll, entity-prerequisite gates in place of a
workflow engine). **Where its *vocabulary* differs from the later `metamodel-design.md`
(2026-07-01) and the canonical `DATA_MODEL.md`, those are authoritative and this doc has been
reconciled to them** — specifically: the app-side queue is CR-001's **`rwb_job`** (not
`rwb_job`); job app-local states are `UNSUBMITTED`/`SUBMITTING`/`BLOCKED`/**`ERROR`**
(not `SUBMISSION_FAILED`) / `SUPERSEDED`, and IRP spellings are one-`L` (`CANCELED`); the
batch is a `RUNNING`/`COMPLETE` latch with a derived breakdown (not `INITIATED`→`ACTIVE`→…);
and resilience is AOF + heartbeat + reconciler (CR-001), not a duration sweep. See
`metamodel-design.md §5` for the full state model.

**How we got here:** the PRD imported a heavy workflow/stage/task/handle/port DAG + Dramatiq framing that (a) exceeds the team's MVP (`mvp-scope.md §6`) and (b) diverged from the prototype (`irp-workbench/`). We re-derived the model bottom-up from the real `irp-integration` surface, the working prototype, and the prior **notebook framework** (`irp-notebook-framework/`, the origin of the stages/steps/batches/jobs mental model). Net result is leaner than both the PRD and the notebook framework.

**Source materials:**
- `irp-workbench/` — working prototype (durable-intent + cron poller; `models/job.py`, `poller/run.py`).
- `irp-notebook-framework/` — prior config-driven **automation** framework (`Cycle→Stage→Step→StepRun→Batch→JobConfiguration→Job`, `step_chain.py`, `validate_batch`).
- `irp-integration` v0.2.1.dev23 — the actual client surface.
- `CReWorkflow_Expanded_20260617.xlsx` — MVP scope (→ `mvp-scope.md`).

---

## 1. Guiding principle

This is a **workbench, not a workflow engine** — the analyst drives; the tool makes each step fast. Constructs earn their place by **what needs tracking after the HTTP response returns**, not by uniformity. We do **not** pre-plan a run from config and auto-advance it (that was the notebook framework's automation model, which does not carry over).

---

## 2. Domain construct model

```
Submission            broker package; assigned analyst; wraps the EDM/RDM sets
  ├──< Edm / Rdm      multiple (EDM + RDM) sets per submission; RDM paired to its EDM
  │       └── work is anchored to an EDM (portfolios/analyses/groups belong to one EDM)
  ├──< Batch          a set of jobs submitted together (grouping); status recon'd from jobs
  │       └──< Job    one IRP operation (async-polled or heavy-deferred); resubmit lineage
  ├──< {Portfolio, Analysis, Group, Treaty}   entity artifacts produced by ops
  └──< UserAction     audit: who did what, when (every action, incl. synchronous ones)
```

- **Submission is the wrapper around EDMs** (corrects an earlier "EDM-as-top-anchor" error). A submission can hold multiple EDMs, each typically paired with an RDM.
- **EDM is the *modeling* anchor** one level down: portfolios, analyses, groups, treaties all belong to a single EDM.

### 2.1 Persistence tiers — each construct exists only when it pulls weight

| Tier | Exists when | Examples | Notes |
|---|---|---|---|
| **Entity** | any op creates a durable artifact | EDM, RDM, Portfolio, Analysis, Group, Treaty | the lasting record; source for the UI |
| **Job** | the op must be tracked *after* the response — async IRP (poll) **or** heavy deferred (Dramatiq) | EDM/RDM import, GeoHaz, Analysis, Grouping, Export | carries `irp_id`, status, progress, resubmit lineage |
| **Batch** | multiple jobs submitted together; want aggregate status + partial-failure | the 50–150 analysis suite; multi-portfolio GeoHaz | status recon'd from member jobs; **no batch-of-one** |
| **UserAction / audit** | always | every action | sync ops live here + a flash toast, not in the job monitor |

**Synchronous single ops create no Job and no Batch.** Example: create subportfolio → `create_portfolio` returns a `portfolioId` in-request → persist the `Portfolio` entity + write audit → done. It never shows in the job monitor because there is nothing in flight to monitor. (Grouped *sync* ops — e.g. "create 5 subportfolios at once" with partial-failure reporting — may opt into a Batch; decide per-flow.)

---

## 3. Constructs kept, dropped, replaced (vs the notebook framework)

| Notebook framework | New app | Disposition |
|---|---|---|
| `Cycle` (1 active, config-driven, quarterly) | **Submission** (many concurrent, broker package) | keep as top container, new semantics |
| `Configuration` (Excel → all jobs) | **Template suite** (the analysis batch) + interactive | partial — suites are the config-driven batch; the rest is interactive |
| `Stage` / `Step` / `StepRun` | — | **drop** (notebook-orchestration structure; no notebooks here) |
| `step_chain` auto-advance | analyst-gated "enable next" | **replace** with entity-prerequisite gates (§4) |
| **`Batch`** | **Batch** | **keep** — the grouping construct |
| `JobConfiguration` (params, resubmit lineage) | resubmit lineage via `parent_job_id` (+ optional config) | **keep the lineage**; params may fold into Job for MVP |
| **`Job`** | **Job** | **keep 1:1 with an IRP op** |

**Also dropped from the PRD:** the handle/port **type registry**, derived-type propagation, two-phase validation pass, the workflow-definition **manifest + projection**, and the generic `JOB_FLOWS`/`current_step`/`context` step-flow engine. None are needed for a single, analyst-driven, mostly-linear pipeline.

---

## 4. Chaining model — entity-prerequisite gates (not a type registry, not auto-advance)

The notebook framework's `validate_batch` already solved "what can chain into what" **without** handles/ports: it checks **entity-existence prerequisites** per batch type. We adopt that, as an **analyst-gated enabler** (button lit / greyed-with-reason), never an auto-runner.

| Op / batch type | Prerequisites (must exist) |
|---|---|
| EDM import | server exists; EDM name not already in IRP |
| RDM import | its EDM exists (imported) |
| Create subportfolio | EDM exists |
| GeoHaz | EDM + portfolio exist |
| Treaty create/edit | EDM exists |
| Analysis | EDM + portfolio (+ treaties if applicable) exist |
| Grouping | analyses (or groups) exist |
| Export → Loss Repo | analysis (or group) exists |

"What's next" is therefore a **function of entity state**, computed on demand — no stored stage pointer. Some chains are *offered automatically* as mechanical follow-up (e.g. RDM import auto-chains off "its EDM is ready" — a broker package is one intent), but nothing that involves judgment auto-runs.

---

## 5. Work-tier model — light vs heavy

**The axis that matters is whether an op moves bulk bytes / does bulk DB work**, not submit-vs-follow-up. (PRD §14.3's "submission is always fast" is false: `submit_edm_import_job` does a multi-GB S3 upload *inside* the call.)

- **Light ops → synchronous on the request path.** Cheap POSTs that return a job id fast: EDM create/upgrade/delete, GeoHaz, analysis submit (even batch — just many POSTs), grouping, export kickoff; plus all sync ops (create_portfolio, treaty CRUD, results retrieval).
- **Heavy ops → Dramatiq actor on a bounded queue.** EDM/RDM import (S3 upload); export download + bulk load; result retrieval + push-to-repo.

**SQL Server is the durable truth; Redis/Dramatiq is dispatch + concurrency control.**
- A durable SQL row is written first (Job `UNSUBMITTED`, or a `rwb_job` `pending`); the Dramatiq message only dispatches.
- Losing Redis loses no work — Redis is **durable (AOF)** and a **single-instance reconciler** re-enqueues stale-`running` `rwb_job` rows via their per-job heartbeat (CR-001; never scans `pending`). *(This supersedes the "staleness sweep" phrasing in the 06-30 draft.)*
- **Two named queues**: `heavy` (low concurrency `M`≈3–4, respects VM↔S3 egress + bulk-insert load) and `light` (notifications etc., higher concurrency).
- The bounded `heavy` queue **replaces** the `job-flow-engine.md` bespoke `ThreadPool(M)` + SQL claim/lease plan. Dramatiq's built-in backoff handles **heavy-submit** retry; the **light-path submission (`ERROR`) retry keeps a dedicated `submission_retry` actor** (`attempt_count` + `retry_locked_until` on `job`), per `DATA_MODEL.md §4` and CR-001 §6 (which preserves the job's submission-retry mechanism).

---

## 6. State model

Three status machines. The **Job** is the primary tracked unit (there is no separate `task_instance`).

**Job.status** — one IRP operation. *(This 06-30 sketch is refined in `metamodel-design.md §5`
/ `DATA_MODEL.md §5` — the authoritative machine: app-local `UNSUBMITTED` / `SUBMITTING` /
`BLOCKED` / `ERROR` / `SUPERSEDED` plus IRP-mirrored `PENDING → QUEUED → RUNNING → FINISHED`,
one-`L` `CANCELED`.)*
```
UNSUBMITTED ──> (SUBMITTING) ──> PENDING ──> QUEUED ──> RUNNING ──> FINISHED
   (app-only,        (mirrored from IRP by the poller)               ├─> FAILED
    durable intent)                                                  └─> CANCELED
   └─> ERROR      (app-only; submission never reached IRP — retries exhausted)
   └─> BLOCKED    (app-only; a prerequisite job failed)   ─▶ SUPERSEDED (replaced by a resubmit)
```
- `UNSUBMITTED` = durable intent, no `irp_id` yet. Light jobs leave it within the same request; **heavy jobs sit here (then `SUBMITTING`) until the Dramatiq actor uploads + submits.**
- Terminal = IRP terminal (`FINISHED` is the only success; always inspect it — terminal ≠ success). `ERROR` (submission-side, no `irp_id`) is distinct from `FAILED` (RM ran it and it failed).
- **`LOADING` is derived, not stored**: `FINISHED` **and** a `rwb_job` still `pending`/`running`. A Job is "fully complete" only when IRP is `FINISHED` *and* all its rwb_jobs succeed (so "export done" = ELT loaded, not merely parquet-ready).

**Batch.status** — the **notification unit**: a `RUNNING`/`COMPLETE` **latch** (settle + notify exactly once when all members terminal), with the breakdown (147 ok / 3 failed) **derived** on demand via `GROUP BY batch_id`, never stored. *(Supersedes the 06-30 `INITIATED`→`ACTIVE`→`COMPLETED`/`FAILED`/`ERROR` recon columns; see `DATA_MODEL.md §5.2`.)*
```
RUNNING ──(all members terminal)──> COMPLETE   (fires ONE notification, then latched;
   ▲                                    │        resubmitting failed members reopens it)
   └────────(resubmit reopens)──────────┘
```

**rwb_job.status_code** — app-side queued-work unit: `pending → running → succeeded | failed` (a kind table, `rwb_job_status_kind`; CR-001). Stale-`running` recovery is the reconciler (heartbeat-based), not a duration sweep.

**Resubmit lineage:** a failed Job (`FAILED`/`ERROR`) is resubmitted as a **new** Job that references the original via `parent_job_id`; the original transitions to `SUPERSEDED` (excluded from batch recon). (E.g. "run 150 analyses, re-run the 3 that failed.")

---

## 7. The poller (`app/poller/run.py`) — status mirror + dispatcher, never heavy

Standalone loop process (not Dramatiq — batch by design). One pass per interval:

1. **Query** non-terminal jobs from `WORKBENCH` (`status NOT IN terminal`; terminal now includes `ERROR` and `SUPERSEDED`), grouped by `job_type`.
2. **Poll each** with the **single-shot `get_*_job`** call for its type — **never** the blocking `poll_*_to_completion` variants (those would block the whole pass):

   | job_type | poll call |
   |---|---|
   | EDM/RDM import | `import_job.get_import_job(id)` |
   | GeoHaz | `portfolio.get_geohaz_job(id)` |
   | Analysis | `analysis.get_analysis_job(id)` |
   | Grouping | `analysis.get_analysis_grouping_job(id)` |
   | Export | `export_job.get_export_job(id)` |

   *(Reconcile against PRD §14.4, which routes imports to `risk_data_job` and lists blocking `poll_*_to_completion` methods — both wrong; the prototype confirms `import_job.get_import_job`.)*
3. **Update** `job.status`/`progress`; on terminal, resolve backfills (e.g. `exposureId`).
4. **On terminal + follow-up needed:** write `rwb_job` row(s) (durable) **then** enqueue the Dramatiq actor.
5. **Settle** affected Batches: if all a batch's members are terminal, flip `RUNNING → COMPLETE` (guarded) and enqueue one `notify_analyst`; the breakdown is derived, not stored.
6. **Reconcile** (CR-001, folded into this single-instance process): re-enqueue `rwb_job` rows stuck `running` whose per-job `rwb_job_heartbeat` is stale (a constant multiple of the heartbeat interval — **never** a job-duration window). It does **not** scan `pending` (durable AOF covers pending-lost).

The poller **never** does heavy work inline. (Fixes the prototype wart where `_submit_rdm_job` runs a multi-GB S3 upload *inside* the poll pass — that becomes a `heavy`-queue actor.)

---

## 8. Dramatiq workers (`app/workers/`) — Redis broker

Two queues. Every actor is **idempotent** (safe to re-run on retry/re-enqueue) and follows: set `running` → do work → `succeeded` + `completed_at`, or `failed` + `error_detail`.

**`heavy` queue** (bounded `M`≈3–4):
- **Heavy submission** — `edm_import`, `rdm_import`: upload `.bak` to S3 + submit; on success set Job `QUEUED` + `irp_id`; Dramatiq backoff on failure, then `ERROR`.
- **Heavy follow-up** (consume a `rwb_job`) — `retrieve_analysis_results`, `download_export_file` + `push_results_to_loss_repo`, `push_rdm_to_loss_repo`. Each stamps a per-job heartbeat from a daemon thread (CR-001 §5.3a) so long blocking downloads don't look abandoned.

**`light` queue** (higher concurrency):
- `notify_analyst` (Teams / email / in-app on job or batch completion/failure).

Follow-up dispatch shape (from §5): durable `rwb_job` row (idempotent on `request_key`) → Dramatiq message → actor **claims atomically** (`UPDATE … WHERE status_code='pending'`). Durable-AOF Redis + the heartbeat reconciler mean Redis loss ≠ work loss.

*(MVP note: no Exposure Repository worker — Phase A profiling/exposure-repo is out of MVP per `mvp-scope.md §6`.)*

---

## 9. Indicative data model (refine when building)

```
submission(id, name, customer_id, program_id, assigned_analyst_id, status, created_at)
edm(id, submission_id, name, irp_exposure_id, status, source_path|artifact_id, server_name, deleted_at)
rdm(id, submission_id, edm_id, name, irp_id, status, source_path, deleted_at)
batch(id, submission_id, edm_id, batch_type, status, created_by, created_at, submitted_at, completed_at)
job(id, submission_id, edm_id, batch_id?,            -- batch_id null for single async ops
    job_type, irp_id?, status, progress, error_message,
    source_path?,                                     -- staged .bak / downloaded zip
    parent_job_id?,                                   -- resubmit lineage
    submitted_at, completed_at, last_tracked_at,
    <entity ref: portfolio_id? analysis_id? rdm_id? group_id?>)  -- data-lineage convenience
rwb_job(id, request_key UNIQUE, origin, job_id?, work_type, status_code, customer_id, payload, error_detail, attempt_count, claimed_by?, created_at, completed_at)  -- CR-001; job_id nullable/lineage
rwb_job_heartbeat(rwb_job_id UNIQUE, worker_id, heartbeat_at)  -- CR-001; reconciler progress signal
portfolio(id, edm_id, irp_portfolio_id, name, created_by_job_id?, created_at)
analysis(id, edm_id, batch_id?, irp_analysis_id, name, origin[own|broker], rdm_id?, status, created_at)
-- NOTE: in the final model a **group is an analysis** (`analysis.is_group`), not a separate table (see `DATA_MODEL.md §3`).
treaty(id, edm_id, irp_treaty_id, ...)
user_action(id, actor, action, submission_id?, entity refs, detail, ts)   -- audit
```
- `job_type` / `batch_type` / `job`+`batch` statuses are **plain strings** (Python enums), never DB-constrained — a new IRP status never crashes the poller. **Exception:** `rwb_job.status_code` is a kind table (`rwb_job_status_kind`; internal, stable — CR-001).
- Job carries denormalized `submission_id` + `edm_id` so the job monitor filters/groups without joins.

---

## 10. What this satisfies (the stress tests)

- **Sequencing / "what's next"** → derived from entity state + prerequisite gates (§4). No stage object.
- **Job monitor / "my jobs"** → `Job` filtered by `submission.assigned_analyst`, grouped by submission → EDM → (batch). Sync ops excluded by design.
- **Batch-of-150** → `Batch` groups the 150 async analysis Jobs; recon'd aggregate status; the export/grouping candidate pool.
- **Resubmit failed jobs** → `parent_job_id` lineage on Job.

---

## 11. PRD sections to amend (later)

- **§2.3 / §14** — replace the work-tier + poller + Dramatiq framing with §5–§8 here (light/heavy split, SQL-truth+Redis-dispatch, single-shot poll, derived LOADING, no separate submission_retry actor).
- **§12–§13** — replace the workflow/stage/task/handle/port/registry/manifest model with §2–§4 here (Submission→EDM/RDM→Batch→Job + prerequisite gates).
- **§10, §16.5, §17.3** — mark Phase A validation/profiling, Exposure Repository, and visual broker comparison **out of MVP** (`mvp-scope.md §6`).
- **Add** treaty view/edit as an MVP feature (missing from the PRD; `mvp-scope.md §6`).
- **§14.4** — correct import poll routing to `import_job.get_import_job`; drop blocking `poll_*_to_completion` from the poller.

---

## 12. Open / to confirm via the sequence flows

- Grouped **sync** ops (multi-subportfolio) — Batch or independent creates? Decide per-flow.
- Whether a first-class **Run** object is ever needed (vs Submission+EDM anchoring + Batch) — only if re-runs/multiple EDMs make the job monitor want it; revisit after flows.
- `JobConfiguration` as its own table vs params folded into Job (lineage via `parent_job_id` is decided either way).
- Currency assignment point in RM; whether new analyses write back to on-DB RDMs (open questions in `mvp-scope.md §7`).

---

**Next step:** draw granular sequence flows in `docs/sequence_diagrams/` (EDM upload + RDM upload first), using the `edm_flows.md` structure (numbered Definition + mermaid `sequenceDiagram`), amended to these decisions. Then composite user-action flows. Then amend `PRD.md` per §11.
