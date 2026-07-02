<!--
  Sync Impact Report
  ==================
  Version change: 1.1.0 → 2.0.0  (MAJOR — backward-incompatible removal/redefinition of
  workflow-engine principles; the app is a "workbench, not a workflow engine")

  Context: The workflow/stage/task/typed-port/handle-registry/manifest-projection ENGINE
  was removed and replaced by a lean job / batch / rwb_job model. Authoritative sources:
  docs/DATA_MODEL.md (canonical schema), docs/metamodel-design.md (rationale — §7 is the
  case against the engine), docs/execution-design.md, docs/CR_01__RWB_JOBS.md (rwb_job).
  Article numbering is preserved (13 articles) so downstream references stay valid; two
  now-void engine articles were repurposed into live workbench principles.

  Modified / repurposed principles:
    - Article 1 "Manifest-Driven Extensibility" — REVISED. Dropped the workflow-definition
      manifest and the type/port registry (removed). Extensibility now = the navigation
      manifest + code dispatch tables (poller on-terminal handlers keyed by job_type, the
      worker registry, prerequisite gates). Nav manifest retained.
    - Article 2 "Manifest Is Canonical; DB Is a Generated Projection"
      → "Topology Lives in Code, Not Data" — REPURPOSED. The projected tables
      (workflow_definition/definition_stage/task_template/port_template) and the
      content-hash projection guard are removed. New principle: what-follows-what is a
      fixed product decision expressed in code (prerequisite gates + poller dispatch),
      never authored DAG topology stored as data.
    - Article 3 carve-out column list — UPDATED. irp_job.job_type / irp_job.mirrored_status
      / task_instance.task_type / result_work_item.work_type → job.job_type, job.status,
      rwb_job.work_type, rwb_job.origin (edm.status, rdm.status retained). Added: rwb_job.
      status_code stays a KIND TABLE (internal, stable values — NOT an external mirror).
    - Article 4 "Status Is Event-Sourced with a Cached Current" — REVISED. The only
      event-sourced status is job.status (append job_status_event + stamp cached job.status
      in one transaction). Removed the submissions/workflows/stages/tasks scope and the
      dual composition/execution streams. batch.status is a RUNNING/COMPLETE latch; entity
      statuses (edm/rdm/analysis) are simple in-place flags.
    - Article 5 "Generic Stage Review (No HITL Stage Type)"
      → "Analyst-Gated Progression; No Stored Stage Pointer" — REPURPOSED. No stages exist.
      Mechanical follow-up auto-fires (poller Mechanism A); judgment steps wait for an
      analyst click (Mechanism B). "What's next" is a pure function of entity state.
    - Article 11 — result_work_item → rwb_job (worker-consumed rows).
    - Article 12 — removed "manifest→projection consistency check"; the job/batch state
      machine, prerequisite gates, and the on-terminal dispatch are what MUST be tested.

  Added sections: None
  Removed sections: None (Articles 2 & 5 repurposed, not deleted; numbering preserved)

  Templates updated:
    - .specify/templates/plan-template.md — Constitution Check: Article 1/2/4/5 titles ✅

  Deferred: None
-->

# Risk Analysis Workbench Constitution

## Core Principles

### Article 1 — Manifest-Driven & Code-Dispatch Extensibility

Everything that changes when requirements change MUST live in one traceable
place — the navigation manifest, or a small code dispatch table — not scattered
config, and never as authored DAG topology stored in the database.

- "Add a page" = one nav node + one handler + one template.
- "Add a chained follow-up" = one `job_type` string + one row in the poller's
  on-terminal dispatch table (release a dependent / write a head `rwb_job`),
  optionally a `prereq_job_id` edge. No schema migration.
- "Add a background action" = one entry in the worker registry.
- "Add a gate" = one clause in the pure entity-state prerequisite function.

There is no workflow-definition manifest and no type/port registry (removed with
the engine — see Article 2). Engine-style complexity that cannot be traced to a
single nav-manifest edit or a single code-dispatch entry MUST be justified
against this article; when in doubt, choose the boring, one-place-to-change option.

### Article 2 — Topology Lives in Code, Not Data

What-follows-what is a **fixed product decision expressed in code**, never
authored, versioned, or projected as data. This is a workbench (the analyst
clicks known actions), not a workflow-authoring engine.

- There are NO `workflow_definition` / `definition_stage` / `task_template` /
  `port_template` tables, no manifest→projection, no content-hash startup guard,
  and no version-pinning of in-flight definitions. All were removed.
- Sequencing is re-derived from entity + job state on demand: the poller's
  on-terminal dispatch (keyed by `job_type`) and the prerequisite-gate function
  (Article 5). The only stored dependency is a single `prereq_job_id` instance
  edge; the dependency *rule* lives in code.
- Step inputs resolve **live from Risk Modeler by name** at call time
  (`search_edms`, `search_portfolios`, `search_analyses`) — coupling is
  name-based, not id-based, so there is no pinned upstream artifact to project or
  invalidate. Reintroducing a definition-as-data layer MUST be justified against
  this article (rationale: `docs/metamodel-design.md §7`).

### Article 3 — Categoricals Are Kind Tables, Never Enums — Except External-Status Mirrors

Every internal categorical value MUST be a row in a `*_kind` table
(`code` PK, `label`, `sort_order`, optional `icon`/`color`) and referenced by
FK. The database is the source of truth for values, labels, and ordering. No
status/category enum literals may be baked into internal code paths.

**Carve-out — external-status mirrors and job-type discriminators:** Columns
that directly mirror an external system's status vocabulary, or discriminate
job types defined by an external system, MAY be plain `VARCHAR` columns
(not kind tables). A kind table for these would require a seed migration every
time the external system adds a new status or type, causing crashes on
unrecognized values before a migration can be deployed.

The following columns are explicitly governed by this carve-out:

| Column | Reason |
|---|---|
| `job.status` | Mirrors IRP's JobStatus vocabulary verbatim (plus app-local states RM never sends) |
| `job.job_type` | Discriminates the IRP endpoint family; defined by irp-integration |
| `rwb_job.work_type` | Worker dispatch key; grows with app + IRP capabilities |
| `rwb_job.origin` | Provenance discriminator (`irp_completion`/`analyst_request`/`chained`) |
| `edm.status` | Mirrors IRP EDM lifecycle; may gain values with IRP releases |
| `rdm.status` | Same rationale as `edm.status` |

**Not carved out — `rwb_job.status_code` IS a kind table** (`rwb_job_status_kind`:
`pending`/`running`/`succeeded`/`failed`). Its values are **ours** — internal and
stable — not an external mirror, so the carve-out does not apply and Article 3's
default (kind table) governs it (CR-001).

All other categoricals remain kind tables. The carve-out is narrow and
intentional: when in doubt, use a kind table.

### Article 4 — Status Is Event-Sourced with a Cached Current (job.status)

`job.status` MUST NOT be `UPDATE`-d in place. Each transition MUST:

1. Insert a `job_status_event` row.
2. In the same transaction, stamp the cached `job.status` column (O(1) reads;
   never recompute-on-read in the hot path).

This is the one lifecycle that earns an event stream. There are no workflow /
stage / task status streams (those constructs are gone). Other lifecycles are
simpler by design and MUST NOT carry an event stream:

- `batch.status` is a `RUNNING → COMPLETE` **latch** (settle + notify exactly
  once via a guarded `settled_at`); its breakdown is derived (`GROUP BY batch_id`).
- Entity statuses (`edm`, `rdm`, `analysis`) are simple in-place lifecycle flags.
- `rwb_job.status_code` transitions in place (`pending → running → …`).

`ERROR` (submission-side, no `irp_id`) is distinct from `FAILED` (RM ran it).
Event-sourced writes require two DML statements and MUST use `get_connection()`
as a context manager with an explicit transaction. `execute_command()` (single
statement only) MUST NOT be used for the event-sourced `job.status` update.

### Article 5 — Analyst-Gated Progression; No Stored Stage Pointer

"What's next" MUST be re-derived from entity + job state — never a stored
`current_stage`/`current_step` pointer. Progression happens two ways only:

- **Mechanism A — mechanical follow-up (auto-fires, no human).** On a job's
  terminal status the poller runs a fixed on-terminal handler keyed by
  `job_type`: backfill ids, then release a waiting dependent (`prereq_job_id`
  now `FINISHED`), `BLOCKED` it (prereq `FAILED`), or write a head `rwb_job`; and
  settle the batch once all its members are terminal.
- **Mechanism B — analyst-gated (judgment).** Steps needing judgment (which
  portfolios, which analyses, which settings) MUST NOT auto-run. The UI lights or
  greys each action from a **pure function of entity state** (the prerequisite
  gate); the analyst clicks when ready.

Mechanical follow-up auto-fires; anything requiring judgment waits for a click.
There is no stage-review parking, no `auto_complete` toggle, and no cancel-halts-
the-workflow gate — those belonged to the removed engine. Synchronous single ops
(subportfolio create, treaty CRUD) create no job and never appear in the monitor.

### Article 6 — Customer Isolation on the Parameterized Path Only

Row scoping MUST be enforced in the app via `apply_scope()` against a
denormalized, immutable `customer_id` on every major entity.

- Scoped tables MUST be reachable only through a repository layer that makes
  scope mandatory.
- `apply_scope()` MUST only be called against the `WORKBENCH` connection.
  Calling it against `EXPOSURE` or `LOSS` is a bug — those schemas have no
  `customer_id` column. `scoped_execute()` MUST assert the connection name
  and raise immediately on any other value.
- An admin/superuser bypass MUST be explicit and audited.
- Scope predicates MUST use bound parameters — never string interpolation.

### Article 7 — One Data-Access Package, Two Execution Paths Split by Safety (`/db`)

All SQL MUST go through the `/db` package (SQLAlchemy Core as pool/engine
only — no ORM). The package exposes exactly two paths:

**(a) Safe bound-parameter path** (`db.execute`, `db.scope`) — returns
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

### Article 10 — The SQL Table Is the Queue; Single Worker by Default

Execution MUST use a SQL-backed queue with a single worker and plain dequeue
(IRP already queues/executes). Documented upgrade paths exist for:

- A concurrency-safe claim query.
- Idempotent IRP submission.

These are documented upgrades, not default complexity. The reclaim-stuck sweep
MUST be retained regardless of worker concurrency level.

### Article 11 — IRP Polling and Result Work Behind an Interface; Submission on Request Path Permitted

**IRP polling and post-completion result work** MUST NOT run in the web layer:

- The **poller** (`app/poller/run.py`) is a standalone process — never
  imported or called from a route handler.
- **Dramatiq result workers** consume `rwb_job` rows and perform post-completion
  actions (retrieve results, push to repositories, notify). They run in a
  separate worker process, never in the web process. Stale-`running` `rwb_job`
  rows are recovered by a single-instance reconciler (heartbeat-based, CR-001),
  not by inline web-layer logic.

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

### Article 12 — Test-First, with Three Connected Strategies

Behavior MUST be covered by tests across three tiers:

1. **Unit** — fast, no external deps. Pure functions plus the `/db` safe path
   exercised via an injected SQLite engine.
2. **SQL-Server-connected** — a `sqlserver`-marked suite against a SQL Server
   Express container, covering the real driver, migrations, RLS, and
   event-sourcing transactions.
3. **IRP-connected** — a fake IRP implementing the interface for default CI,
   plus an opt-in `irp`-marked suite against a sandbox IRP.

The following MUST have tests: validators, `apply_scope`, the `job` / `batch` /
`rwb_job` state machines, the poller's on-terminal dispatch (release / `BLOCKED` /
head-`rwb_job`), the prerequisite-gate function, and `rwb_job` idempotency
(`request_key` claim) + the reconciler decision.

### Article 13 — Authentication & Secrets

- Identity: Entra ID OIDC (v2). A gated, env-flagged (`AUTH_MODE=password`),
  server-enforced, audited password login is permitted as v1 MVP fallback.
- Sessions are signed-cookie identity only; roles and customer scope are read
  from the DB on each request.
- CSRF MUST be applied on all state-changing requests.
- Idle timeout MUST be handled for HTMX via `HX-Redirect`.
- No secrets in code or VCS.

## Source-of-Truth Documents

The following documents are the authoritative references for this project. All
specs, plans, and implementations MUST be consistent with them:

- **DATA_MODEL.md** — canonical entity and relationship definitions (the lean
  job / batch / rwb_job model).
- **PRD.md** — product requirements and feature scope.
- **metamodel-design.md / execution-design.md / mvp-scope.md** — the rationale for
  the lean model (why each table exists; the case against the engine) and the MVP
  scope boundary. **CR_01__RWB_JOBS.md** — the `rwb_job` decoupling + resilience design.
- **`mock/`** — runnable clickable mock; the UX reference implementation.
- **`/db` package** — the implemented data-access layer; Article 7 governs its
  structure.

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

**Version**: 2.0.0 | **Ratified**: 2026-06-28 | **Last Amended**: 2026-07-01
