<!--
  Sync Impact Report
  ==================

  --- CR-004 (2026-08-25) ---
  Version change: 3.2.0 → 4.0.0  (MAJOR — CR-004: Article 10 redefined in
  place; "Single Worker by Default" replaced by "Concurrency Is Per-Queue,
  Not Per-Row" — same MAJOR class as CR-002/CR-003's article redefinitions,
  since the article's title and core rule change, not just a carve-out added
  alongside the existing rule. 13-article numbering stable.)

  Applies CR-004 (docs/CR/CR_04__PER_QUEUE_WORKERS.md §5.4). Each
  `rwb_job_type` now runs in its own Dramatiq queue and worker process
  (`app/workers/queues.py`'s `rwb_actor`, deriving `queue_name` from the
  actor's own function name) — a long-running job of one type can no longer
  starve a job of a different type sharing the same worker pool. The claim
  query (`UPDATE rwb_job SET status_code='running' ... WHERE
  status_code='pending'`) and the heartbeat/reconciler (CR-001) are
  unchanged — this redefinition only changes how many worker processes may
  claim from `rwb_job` concurrently, not the claim mechanism itself.

  Removed: the "single worker" default. A single worker process per queue
  remains the default — this is per-queue, not a general declaration that
  concurrency is now unbounded — and scaling one queue's worker count
  requires an observed contention problem in that queue, not anticipated
  scale (ties to Article 1's maintainability contract).

  Templates: no plan-template Constitution Check title changes (no per-article
  title table exists there to sync).

  --- 2026-08-12 (spec 005 follow-on, note 12 §1.2) ---
  Version change: 3.1.0 → 3.2.0  (MINOR — the Article 11 DataBridge clause
  gains a request-path carve-out; no article redefined or removed; 13-article
  numbering stable)

  Added: Article 11 DataBridge clause — a bounded, single-row, parameterized
  DataBridge read is permitted on the request path when it answers a
  point-of-action validation. Motivated by the custom-breakout empty-selection
  problem: a group filtering two dimensions can name values that no single
  account carries (Japan + Earthquake in the 2026-08-11 demo), the stored
  summary carries per-value counts but no cross-tab, and the intersection is
  only knowable from DataBridge. The 3.1.0 wording pinned every DataBridge read
  worker-side, which forced a job-and-poll round trip to answer one integer the
  analyst is waiting on. The enumeration ban is unchanged: the modal still
  renders the STORED summary.

  --- 2026-07-23 (spec 004 Addendum A) ---
  Version change: 3.0.0 → 3.1.0  (MINOR — new permission clause added to
  Article 11; no article redefined or removed; 13-article numbering stable)

  Added: Article 11 "DataBridge access" clause — read-only Data Bridge SQL is
  permitted, worker-side only, exclusively via irp-integration client methods
  (never raw SQL from app code, never DDL/migrations). Motivated by spec-004
  Addendum A: RM REST exposes no TIV/currency/geography at any level (wheel
  0.2.1 + sandbox confirmed); a per-EDM DataBridge aggregate is the sanctioned
  source. Supersedes the absolute "DATABRIDGE is never touched by this app"
  wording in CLAUDE.md / docs/DATA_MODEL.md (both softened in the same pass).

  Templates: no plan-template Constitution Check title changes (Article 11
  title unchanged).

  --- CR-003 (2026-07-08) ---
  Version change: 2.0.0 → 3.0.0  (MAJOR — CR-003: drop customer/program
  hierarchy and row-level security; submission becomes the deal root)

  Applies CR-003 (docs/CR/CR_03__SUBMISSION_PACKAGE_MODEL.md §8.1). Consolidates
  the model to Submission + Package and retires customer-based RLS.

  Redefined principle (backward-incompatible → MAJOR):
    - Article 6: "Customer Isolation on the Parameterized Path Only"
      → "No Row-Level Security; All Authenticated Analysts See All Deals"
      No customer_id scoping key, no apply_scope(), no user_customer_access.
      assigned_analyst_id is a soft "my submissions" owner, not an access gate.
      Redefined in place (not removed) to preserve the stable 13-article
      numbering the Compliance Gates depend on.

  Modified principles:
    - Article 7: safe-path description drops db.scope (the customer-scoping
      helper); the bound-parameter safe path (db.execute) is otherwise unchanged.
    - Article 12: RLS struck from the SQL-Server-connected tier; apply_scope
      removed from the required-test list.
    - Article 13: sessions read "roles" from the DB each request (was "roles
      and customer scope").

  Removed sections: None (Article 6 redefined in place; 13-article numbering
    stable).

  Templates: .specify/templates/plan-template.md Constitution Check titles
    synced — Article 6 (this CR) plus Articles 1/2/5 (outstanding CR-002 debt).

  Follow-up (not in this pass): PRD realignment (CR-003 §8.2) and spec-002
    code/RLS/file-inventory removal (CR-003 §8.3).

  --- CR-002 ---
  Version change: 1.1.0 → 2.0.0  (MAJOR — CR-002: no workflow engine; article redefinitions)

  Applies CR-002 (docs/CR_02__NO_WORKFLOW_ENGINE.md). The Workflow / Stage /
  Task / typed-handle / type-port-registry / manifest-projection layer is
  removed; this app is a workbench, not a workflow engine.

  Redefined principles (backward-incompatible → MAJOR):
    - Article 1: "Manifest-Driven Extensibility"
      → "Navigation Manifest Is the One Versioned Source of Truth"
      The workflow-definition manifest and type/port registry no longer exist;
      the nav manifest is the sole versioned code manifest. Removed instance
      version-pinning (no workflow instances to pin).

    - Article 2: "Manifest Is Canonical; DB Definition Is a Generated Projection"
      → "Sequencing Is Derived, Not Stored"
      No manifest is projected into tables anymore, so the projection /
      content-hash-consistency pattern is removed. Repurposed to state the
      replacement principle: "what's next" is the prerequisite gate computed
      in code, and coupling is name-based (IRP search_* at submit time), never
      a stored stage machine or typed handle.

    - Article 5: "Generic Stage Review (No HITL Stage Type)"
      → "Mechanical Follow-up Auto-fires; Judgment Waits for a Click"
      No stages, no review-gate lifecycle. Repurposed to the surviving
      principle from §13.1: mechanical follow-up auto-fires; anything needing
      judgment waits for an explicit analyst click.

  Modified principles:
    - Article 3 carve-out table updated: irp_job.mirrored_status → irp_job.status;
      edm.status → irp_edm.status; rdm.status → irp_rdm.status. REMOVED from the
      carve-out (now kind tables): irp_job.job_type (→ irp_job_type_kind),
      result_work_item.work_type / rwb_job.work_type (→ rwb_job_type_kind),
      task_instance.task_type (table removed). Final carve-out: irp_job.status,
      irp_edm.status, irp_rdm.status.
    - Article 4: event-sourced-status list narrowed from
      "submissions, workflows, stages, tasks" to "submissions" only. irp_job /
      rwb_job status is updated in place; per-transition audit is deferred.
      Removed the two-stream (composition/execution) and stored-ERROR text.
    - Article 10 / Article 11: reworded to reference rwb_job / irp_job (not
      task_instance / result_work_item); submission retry is a single-threaded
      batch job, not a Dramatiq actor.

  Removed sections: None (Articles 1/2/5 redefined in place to keep the 13-article
    numbering stable; compliance gates still reference Articles 1–13).

  Templates to update:
    - .specify/templates/plan-template.md — Constitution Check table
      Article 1/2/5 titles need updating (done in CR-003, 2026-07-08).
-->

# Risk Analysis Workbench Constitution

## Core Principles

### Article 1 — Navigation Manifest Is the One Versioned Source of Truth

The structural configuration that changes when requirements change MUST live in
a single versioned code manifest — the **navigation manifest** (rail / sidebar /
breadcrumb / search tree) — not scattered config.

- "Add a page" = one nav node + one handler + one template. Rail, sidebar,
  breadcrumb, active-state, RBAC, and search visibility are inherited.

Engine code stays fixed. There is **no workflow-definition manifest and no
type/port registry** (removed with the workflow engine, CR-002), so there are no
manifest versions to pin. Any complexity that cannot be traced to a single
manifest edit MUST be justified against this article.

### Article 2 — Sequencing Is Derived, Not Stored

The app does **not** store process topology as data. There is no workflow
definition, no stage machine, no typed-handle graph, and **no manifest→table
projection** (the content-hash consistency check and version-retention rule are
removed with it — nothing is projected).

- **"What's next" is computed in code** — a prerequisite gate (a lookup +
  entity-existence / job-terminal-status check), not read off a stored
  `stage.exec_status`.
- **Coupling is name-based** — each operation resolves its inputs live from Risk
  Modeler by name at submit time (`search_edms` / `search_portfolios` /
  `search_analyses` / `search_treaties`). Risk Modeler re-validates names to
  internal IDs anyway, so a local typed-handle registry would only duplicate
  state IRP already owns.
- Entity rows reference each other directly; a job's produced entity records its
  creator via `created_by_irp_job_irp_id`. Any proposal to persist a stored
  sequence/DAG MUST be justified against this article.

### Article 3 — Categoricals Are Kind Tables, Never Enums — Except External-Status Mirrors

Every internal categorical value MUST be a row in a `*_kind` table
(`code` PK, `label`, `sort_order`, optional `icon`/`color`) and referenced by
FK. The database is the source of truth for values, labels, and ordering. No
status/category enum literals may be baked into internal code paths.

**Carve-out — external-status mirrors only:** Columns that directly mirror an
external system's **status** vocabulary MAY be plain `VARCHAR` columns (not kind
tables). A kind table for these would require a seed migration every time the
external system adds a new status, causing crashes on unrecognized values before
a migration can be deployed. This carve-out is for external *statuses* only —
**not** for job/work *type* discriminators, which are app-defined, closed sets
and therefore remain kind tables (`irp_job_type_kind`, `rwb_job_type_kind`).

The following columns are explicitly governed by this carve-out:

| Column | Reason |
|---|---|
| `irp_job.status` | Mirrors IRP's JobStatus vocabulary verbatim (+ app-local states) |
| `irp_edm.status` | Mirrors IRP EDM lifecycle; may gain values with IRP releases |
| `irp_rdm.status` | Same rationale as `irp_edm.status` |

`irp_job.irp_job_type` and `rwb_job.rwb_job_type` are **kind tables** — the set
of operation/worker types the app dispatches on is closed and app-defined
(it changes only when the app itself adds support for a new op), so the "always
kind table" default applies. All other categoricals remain kind tables. The
carve-out is narrow and intentional: when in doubt, use a kind table.

### Article 4 — Status Is Event-Sourced with a Cached Current — Where It Earns It

Event-sourced status applies where the audit trail matters and is not otherwise
deferred. In this model that is **`submission.status_code`**: a transition MUST
NOT be `UPDATE`-d in place — it MUST:

1. Insert a `submission_status_event` row.
2. In the same transaction, stamp the cached `submission.status_code` column
   (O(1) reads; never recompute-on-read in the hot path).

**Other status is updated in place.** `irp_job.status`, `rwb_job.status_code`,
`irp_edm.status`, `irp_rdm.status`, and `irp_analysis.status_code` are plain
updates — a per-transition audit log for them is part of the deferred general
auditing capability (CR-002), not built now. `irp_job.last_tracked_at` (not an
event log) records that a job is still being actively tracked. There is no
stored `ERROR` status; a failure is a job in `FAILED` or `SUBMISSION FAILED`.

Event-sourced writes require two DML statements and MUST use `get_connection()`
as a context manager with an explicit transaction. `execute_command()` (single
statement only) MUST NOT be used for event-sourced status updates.

### Article 5 — Mechanical Follow-up Auto-fires; Judgment Waits for a Click

There is no stage-review lifecycle (stages are removed, CR-002). The surviving
principle governs how the prerequisite gate (Article 2) hands off between ops:

- **Mechanical follow-up auto-fires.** When the next step is a direct
  consequence of one intent — e.g. a broker package's EDM import completing,
  which enables its RDM import — the follow-up op is enqueued automatically.
- **Anything requiring judgment waits for an explicit analyst click.** Picking
  analysis settings, choosing which portfolios to run, composing a grouping —
  these are never auto-fired.
- The auto vs. click-gated distinction MUST be made **explicit per op**, not
  left implicit. The analyst is always in the driver's seat for judgment steps.

### Article 6 — No Row-Level Security; All Authenticated Analysts See All Deals

There is **no row-level security**. Any authenticated analyst can read and act
on every submission and everything under it. There is no `customer_id` scoping
key, no `apply_scope()`, and no `user_customer_access` grant table.

- **`assigned_analyst_id` is a soft owner** — it drives the "my submissions"
  filter only and MUST NOT be used to restrict reads or writes.
- **Roles (`role_kind` / `user_role`) gate function, not rows.** `is_admin`
  grants admin capabilities; it is not a scope bypass, because there is no scope
  to bypass.
- **The safe bound-parameter path (Article 7) stays mandatory.** Dropping RLS
  does not relax the "bound parameters, never string interpolation" rule for any
  application query.
- App tables live only in the `WORKBENCH` connection. A `WORKBENCH`-only
  assertion, if retained in code, is now a wrong-database guard — no longer a
  customer-isolation mechanism.

### Article 7 — One Data-Access Package, Two Execution Paths Split by Safety (`/db`)

All SQL MUST go through the `/db` package (SQLAlchemy Core as pool/engine
only — no ORM). The package exposes exactly two paths:

**(a) Safe bound-parameter path** (`db.execute`) — returns
`list[dict]`. This is the default and the ONLY path for application data and
any user-derived value.

**(b) Trusted-script path** (`db.scripts`, `{{ }}` substitution, DataFrames,
multi-result-set) — for curated, team-authored scripts against external sources
only. Used worker-side only; MUST NOT be used by the web layer and MUST NOT
target the app's own tables. The script path MUST NOT be exported from the
package top level; it MUST be imported explicitly so its use is visible in
review.

### Article 8 — Server-Rendered; No SPA

The stack is FastAPI + Jinja + HTMX. Alpine.js is permitted only for small
client slivers (modal, shortcuts, focus, collapse).

- Top-level navigation MUST use `hx-boost`.
- Every page/detail MUST have a real URL.
- Breadcrumb and active-state MUST be a pure function of position in the nav
  manifest — not browser history.

### Article 9 — Styling Extends the ITCSS Design System via Tokens

The copied design system MUST be extended through named design tokens
(`--surface-rail`, `--surface-sidebar`, `--color-danger`, …) layered into the
correct ITCSS layers.

- No hardcoded hex values in components.
- No flat append-sheets outside the ITCSS layer structure.
- No overriding the system where a token would do.

### Article 10 — The SQL Table Is the Queue; Concurrency Is Per-Queue, Not Per-Row

App-side work (`rwb_job`) MUST use a SQL-backed queue with plain dequeue (IRP
already queues/executes its own jobs; `irp_job` is *tracked* by the poller, not
dequeued). The claim query (`UPDATE ... WHERE status_code='pending'`) already
works correctly with any number of concurrent workers claiming from it (CR-004).

Each `rwb_job_type` MUST run in its own Dramatiq queue, named identically to
the `rwb_job_type` (CR-004). A single worker process per queue remains the
default; adding more processes or threads to one queue requires an observed
contention problem in that queue, not anticipated scale.

The stale-`running` reclaim (heartbeat + reconciler, CR-001) MUST be retained
regardless of worker concurrency level, and MUST NOT be made queue-aware.

Documented upgrade path that remains open: idempotent IRP submission.

### Article 11 — IRP Polling and Result Work Behind an Interface; Submission on Request Path Permitted

**IRP polling and post-completion result work** MUST NOT run in the web layer:

- The **poller** (`app/poller/run.py`) is a standalone process — never
  imported or called from a route handler.
- **Dramatiq result workers** consume `rwb_job` rows and perform
  post-completion actions (retrieve results, push to repositories, notify).
  They run in a separate worker process, never in the web process. **Submission
  retry** is a single-threaded batch job (not a Dramatiq actor, CR-002).

**Synchronous IRP job submission on the request path is explicitly permitted.**
Submit calls (`submit_edm_import_job`, `submit_portfolio_analysis_job`, etc.)
return a job ID immediately (sub-second HTTP round-trip). The analyst gets
immediate confirmation or an error in the same HTTP response, and deferring
through a queue adds no benefit. A service called from a route handler MAY call
`irp_integration` submit functions directly.

**Interface contract:** The web layer MUST NOT call IRP polling methods
(`get_*`, `poll_*_to_completion`) or result-retrieval methods (`get_elt`,
`get_ep`, etc.). These are exclusively the domain of the poller and result
workers. The `poll_*_to_completion` blocking variants MUST NEVER be called
inside the poller — use single-status-check `get_*` methods only.

**DataBridge access (added v3.1.0, 2026-07-23):** Moody's Data Bridge SQL is
reachable **read-only**, **worker-side only**, and **exclusively through
`irp-integration` client methods** (e.g. `client.databridge.*` behind
`irp_gateway`). App code MUST NEVER send raw SQL to DataBridge — not through
`db.execute`, not through the `db.scripts` trusted path — and MUST NEVER run
DDL, migrations, or bootstrap against it. Moody's EDM schema knowledge lives in
the integration library, not this codebase. A DataBridge read failure is
enrichment degradation, never a page error (the graceful-empty doctrine applies).

**Request-path exception (added v3.2.0, 2026-08-12):** a **bounded, single-row,
parameterized** DataBridge read is permitted on the request path when it answers
a point-of-action validation the analyst is waiting on — still read-only, still
through `irp_gateway`, still a repo-owned SQL file, and it MUST fail open (an
unreachable DataBridge never blocks the action). Enumerations, per-EDM
aggregates, and any read whose result size grows with the book stay worker-side.

### Article 12 — Test-First, with Three Connected Strategies

Behavior MUST be covered by tests across three tiers:

1. **Unit** — fast, no external deps. Pure functions plus the `/db` safe path
   exercised via an injected SQLite engine.
2. **SQL-Server-connected** — a `sqlserver`-marked suite against a SQL Server
   Express container, covering the real driver, migrations, and
   event-sourcing transactions.
3. **IRP-connected** — a fake IRP implementing the interface for default CI,
   plus an opt-in `irp`-marked suite against a sandbox IRP.

The following MUST have tests: point-of-action validators (§13.3),
the prerequisite gate (Article 2), and the `rwb_job` claim/heartbeat/reconciler
state machine.

### Article 13 — Authentication & Secrets

- Identity: Entra ID OIDC (v2). A gated, env-flagged (`AUTH_MODE=password`),
  server-enforced, audited password login is permitted as v1 MVP fallback.
- Sessions are signed-cookie identity only; roles are read from the DB on each
  request.
- CSRF MUST be applied on all state-changing requests.
- Idle timeout MUST be handled for HTMX via `HX-Redirect`.
- No secrets in code or VCS.

## Source-of-Truth Documents

The following documents are the authoritative references for this project. All
specs, plans, and implementations MUST be consistent with them:

- **PRD.md** — product requirements and feature scope.
- **DATA_MODEL.md** — canonical entity and relationship definitions.
- **`mock/`** — runnable clickable mock; the UX reference implementation.
- **`/db` package** — the implemented data-access layer; Articles 2 and 7 govern
  its structure.

## Compliance Gates

`/speckit-analyze` MUST treat any violation of Articles 1–13 as **CRITICAL**.
No feature may proceed to `/speckit-implement` while any CRITICAL violation is
open. Any added complexity MUST be justified against the maintainability
contract (Article 1); when in doubt, choose the boring, one-place-to-change
option.

## Governance

This constitution supersedes all other practices for the Risk Analysis Workbench.

**Amendments** require:
1. An explicit logged decision (captured in the Sync Impact Report header of
   the updated constitution).
2. A semantic version bump:
   - **MAJOR**: backward-incompatible principle removals or redefinitions.
   - **MINOR**: new principle/section added or materially expanded guidance.
   - **PATCH**: clarifications, wording, or non-semantic refinements.
3. Propagation to all dependent specs, plans, and templates before any
   feature work resumes.

**Compliance review**: every feature spec MUST include a Constitution Check
section in its plan confirming compliance with all 13 articles before Phase 0
research begins.

---

**Version**: 4.0.0 | **Ratified**: 2026-06-28 | **Last Amended**: 2026-08-25
