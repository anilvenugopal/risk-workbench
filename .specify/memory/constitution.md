<!--
  Sync Impact Report
  ==================
  Version change: template (unversioned) → 1.0.0 (initial ratification)

  Added sections:
    - Core Principles: Articles 1–13 (all new)
    - Source-of-Truth Documents
    - Compliance Gates

  Removed:
    - All placeholder bracket tokens ([PROJECT_NAME], [PRINCIPLE_n_*], etc.)

  Templates updated:
    - .specify/templates/plan-template.md — Constitution Check gates aligned ✅

  Deferred:
    - None (all fields resolved from user input + today's date)
-->

# Risk Analysis Workbench Constitution

## Core Principles

### Article 1 — Manifest-Driven Extensibility

Everything that changes when requirements change MUST live in versioned code
manifests, not scattered config: the navigation manifest, the workflow-definition
manifest, and the type/port registry.

- "Add a page" = one nav node + one handler + one template.
- "Add a chaining type" = registry rows + declared ports.
- "Add a constraint" = one registered validator.

Engine code stays fixed. Manifests carry a version; workflow instances pin the
version they ran under. Any complexity that cannot be traced to a single
manifest edit MUST be justified against this article.

### Article 2 — Manifest Is Canonical; DB Definition Is a Generated Projection

Where a manifest is projected into tables for FK/reporting
(`workflow_definition`, `definition_stage`, `task_template`, `port_template`),
the manifest is the source of truth and the projection is generated — never
hand-edited.

- A fail-fast startup consistency check (content-hash of the manifest vs. the
  hash the projection was built from) MUST refuse to start on mismatch.
- Projection is append-only and version-retained: new manifest versions insert
  new rows; old versions are retained while any instance pins them.

### Article 3 — Categoricals Are Kind Tables, Never Enums

Every categorical value MUST be a row in a `*_kind` table
(`code` PK, `label`, `sort_order`, optional `icon`/`color`) and referenced by
FK. The database is the source of truth for values, labels, and ordering. No
status/category enum literals may be baked into code paths.

### Article 4 — Status Is Event-Sourced with a Cached Current

Lifecycle/status on submissions, workflows, stages, and tasks MUST NOT be
`UPDATE`-d in place. Each transition MUST:

1. Insert a `*_event` row.
2. In the same transaction, stamp a cached `current_*_status` column on the
   parent (O(1) reads; never recompute-on-read in the hot path).

Stages and tasks keep two independent streams — composition and execution. The
audit trail (including accept-with-errors and cancel decisions) derives from
events. `ERROR` is a dynamic rollup (any task failed) overlaying any status —
never a stored status, never a gate.

### Article 5 — Generic Stage Review (No HITL Stage Type)

Every stage has an execution status lifecycle:
`not_started → blocked → running → review → complete | canceled`

A per-instance `auto_complete` toggle (compose-time, default `false`) governs
parking: when a stage's work finishes, `auto_complete=false` parks it in
`review`; otherwise it completes automatically.

- Review means a human reads task output/errors (or, for `blocked`, the
  validation result) and chooses **Complete** (advances the workflow) or
  **Cancel** (halts the workflow → `canceled`).
- Complete-with-errors stays `complete` and MUST be audited.
- No retry/rerun.
- `ERROR` overlays any status dynamically; it is never stored and never a gate.
- The Review queue counts active gates only (`review` + `blocked`).
- A stage whose execution status is `not_started` is editable (task add/remove/
  edit), per-stage, even while other stages run concurrently.

### Article 6 — Customer Isolation on the Parameterized Path Only

Row scoping MUST be enforced in the app via `apply_scope()` against a
denormalized, immutable `customer_id` on every major entity.

- Scoped tables MUST be reachable only through a repository layer that makes
  scope mandatory.
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

### Article 11 — IRP and External Data Sources Sit Behind an Interface

The web layer MUST NOT call IRP or external SQL Server sources directly. Only
the worker does, behind an interface abstraction with a defined degraded mode:
bounded backoff, "unavailable" surfacing, and no immediate task failure on
outage.

### Article 12 — Test-First, with Three Connected Strategies

Behavior MUST be covered by tests across three tiers:

1. **Unit** — fast, no external deps. Pure functions plus the `/db` safe path
   exercised via an injected SQLite engine.
2. **SQL-Server-connected** — a `sqlserver`-marked suite against a SQL Server
   Express container, covering the real driver, migrations, RLS, and
   event-sourcing transactions.
3. **IRP-connected** — a fake IRP implementing the interface for default CI,
   plus an opt-in `irp`-marked suite against a sandbox IRP.

The following MUST have tests: validators, `apply_scope`, the run/queue state
machine, and the manifest→projection consistency check.

### Article 13 — Authentication & Secrets

- Identity: Entra ID OIDC.
- A gated, env-flagged, server-enforced, audited backdoor login for local/dev
  is permitted.
- Sessions are signed-cookie identity only; roles and customer scope are read
  from the DB each request.
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

**Version**: 1.0.0 | **Ratified**: 2026-06-28 | **Last Amended**: 2026-06-28
