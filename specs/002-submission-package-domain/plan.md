# Implementation Plan: Submission & Package Domain Model (Iteration 1)

**Branch**: `002-submission-package-domain` | **Date**: 2026-07-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-submission-package-domain/spec.md`

---

## Summary

Iteration 1 makes the **submission** (a deal — one cedant's treaty at one inception) the top-level unit of work in the workbench, and lays the **package** bundle structure beneath it. Analysts create, find/filter, tag (CRM IDs), and status-track deals; the package tables (bundle + M:N + `package_id` membership on `irp_edm`/`irp_rdm`) are delivered as *structure only* so Iteration 2 can build package behavior on them. Status is event-sourced (`submission_status_event` + cached `submission.status_code`); closed states are fully read-only and reopenable; there is no delete. The iteration also **retires the CR-003 dead scaffolding** (customer/program/`user_customer_access` tables, `db.scope` helper, and all RLS references) folded into the single `0001_initial.py` revision (drop-create-seed).

Technical approach: extend the existing Iteration-0 stack (FastAPI + Jinja2 + HTMX, SQLAlchemy Core via the `db/` safe path, Alembic single-revision schema, pytest/SQLite unit tier + SQL-Server integration tier). New code is a thin repository layer (`app/services/` + a `db`-backed submission/package data-access module), master-detail routes/templates behind the existing nav manifest, and seed rows for two new kind tables. No IRP calls, no workers, no EDM/RDM management this iteration.

---

## Technical Context

**Language/Version**: Python 3.12 (inherited from Iteration 0)

**Primary Dependencies** (all already present — no new deps):
- `fastapi` + `uvicorn[standard]` — web server
- `jinja2` — server-side templates; HTMX partials for master-detail
- `sqlalchemy>=2.0` (Core only, no ORM) — engine/pool via `db/` package
- `pyodbc` + ODBC Driver 18 — SQL Server connectivity
- `alembic` — WORKBENCH schema (single `0001_initial.py` revision)
- `itsdangerous`, `bcrypt`, `msal`, `python-multipart` — auth (reused, unchanged)
- HTMX + Alpine.js — partial swaps, `hx-boost`; Alpine for the duplicate-warning confirm + CRM-tag editor slivers

**Storage**: SQL Server 2022 (`rwb_workbench`) — all Iteration-1 tables live in the `WORKBENCH` connection. No EXPOSURE/LOSS/DATABRIDGE access this iteration.

**Testing**:
- `pytest` unit tier — SQLite via `db.register_engine` (repository functions, package ≥1-member invariant, optimistic-concurrency conflict, duplicate-warning matcher, read-only gate, "My Submissions" filter, absence-of-scope regression)
- `pytest --run-sqlserver` — real driver: migration idempotency, event-sourced status transaction (`submission_status_event` + cached column atomic), FK/constraint enforcement

**Target Platform**: Linux server (WSL2 native dev: uvicorn + SQL Server container)

**Project Type**: Server-rendered web application (FastAPI + Jinja2 + HTMX). Single project; extends the existing `app/` tree.

**Performance Goals** (consistent with Iteration 0; no heavy paths here):
- Submission list render (My/All, filtered): < 300ms p95 at ~thousands of deals
- Create / status-change / CRM-tag round-trips: < 300ms p95
- Cedant autocomplete lookup: < 150ms p95

**Constraints**:
- No SPA; every submission and its detail has a real URL; JS-disabled degrades to full-page (Article 8)
- All SQL via the `db/` safe bound-parameter path; event-sourced writes use `get_connection("WORKBENCH")` + explicit `conn.begin()` (Articles 4, 7)
- No row-level security anywhere; `assigned_analyst_id` is a soft filter only (Article 6)
- Optimistic concurrency on `submission` via `updated_at` version marker; rowcount-0 write → surfaced conflict, never a silent overwrite (DATA_MODEL §2)
- CSRF on every state-changing route; function-level role gating server-side (Article 13, Article 6)
- UUID primary keys generated **app-side** (`uuid.uuid4()`, bound param) so the same repository code runs on SQLite (unit) and SQL Server; DB `NEWID()` default retained as a server-side fallback

**Scale/Scope**: ~10–30 internal analysts; low-thousands of submissions over a season. ~9 new tables, ~2 new kind-table seeds, one master-detail page area, one repository module, cleanup across ~11 existing files.

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Article | Title | Status | Notes |
|---------|-------|--------|-------|
| 1 | Navigation Manifest Is the One Versioned Source of Truth | ✅ | `submissions` rail + `submissions.mine`/`submissions.all` sidebar nodes already exist (Iteration 0). New submission-detail routes are added as manifest nodes + handler + template — no scattered config. |
| 2 | Sequencing Is Derived, Not Stored | ✅ | No stored process topology. `renews_from_submission_id` is a direct entity self-reference (Article 2 permits "entity rows reference each other directly"), not a stage machine or DAG. |
| 3 | Categoricals Are Kind Tables, Never Enums — Except External-Status Mirrors | ✅ | `treaty_type_kind`, `submission_status_kind` are kind tables (FK-referenced). `irp_edm.status`/`irp_rdm.status` columns are created as plain `VARCHAR` per the Article 3 carve-out (external-status mirrors) but carry no behavior this iteration. |
| 4 | Status Is Event-Sourced with Cached Current | ✅ | `submission.status_code` is the one event-sourced status: every transition inserts `submission_status_event` **and** stamps the cached column in one transaction (`get_connection` + `conn.begin()`); `execute_command` is not used for it. |
| 5 | Mechanical Follow-up Auto-fires; Judgment Waits for a Click | N/A | No auto-fire ops this iteration (package *behavior* + IRP jobs are Iteration 2). |
| 6 | No Row-Level Security; All Authenticated Analysts See All Deals | ✅ | **Central to this iteration.** No `customer_id`, no `apply_scope`, no `user_customer_access` — this iteration *removes* the last of that scaffolding. `assigned_analyst_id` drives "My Submissions" only. Roles gate functions, never rows. |
| 7 | One Data-Access Package, Two Paths (`/db`) | ✅ | All submission/package SQL goes through the `db.execute*` safe path; transactional event-sourced writes through `db.get_connection`. No script path; no raw connection strings in handlers. |
| 8 | Server-Rendered; No SPA | ✅ | FastAPI + Jinja2 + HTMX master-detail; `hx-boost` top-level nav; real URLs for list/detail; Alpine only for the duplicate-warning confirm and inline CRM-tag editor. |
| 9 | Styling Extends ITCSS via Tokens | ✅ | Submission list/detail, status chips, and CRM tags styled via existing design tokens layered into ITCSS; no hardcoded hex, no flat append-sheets. |
| 10 | The SQL Table Is the Queue; Single Worker by Default | N/A | No `rwb_job` work this iteration (package sync is Iteration 2). |
| 11 | IRP Polling and Result Work Behind Interface; Submission on Request Path Permitted | N/A | No IRP calls at all this iteration. |
| 12 | Test-First, Three Connected Strategies | ✅ | Unit (SQLite) for repository + invariants + concurrency + filter + no-scope regression; SQL-Server tier for migration + event-sourcing transaction. IRP tier N/A. FR-024/FR-029 mandate the package-invariant and data-access tests. |
| 13 | Authentication & Secrets | ✅ | Reuses Iteration-0 auth; CSRF on all state-changing submission/CRM/status routes; role checks server-side per request; no secrets introduced. |

**Constitution Check: PASSED — no violations. No Complexity Tracking entries required.**

> Re-check after Phase 1 design: **still PASSED** (see end of plan). Design introduced no new scoping, no stored sequencing, and no in-place status update for `submission`.

---

## Project Structure

### Documentation (this feature)

```text
specs/002-submission-package-domain/
├── plan.md              ← this file (/speckit-plan output)
├── research.md          ← Phase 0 output (design decisions + cleanup surface)
├── data-model.md        ← Phase 1 output (Iteration-1 tables from DATA_MODEL §4–§5)
├── quickstart.md        ← Phase 1 output (rebuild + test + manual validation walkthrough)
├── contracts/           ← Phase 1 output
│   ├── data-access.md   ← repository (db-backed) function contract — the primary interface
│   └── http-routes.md   ← submission/CRM/status/package route contract (methods, roles, CSRF, HTMX)
├── checklists/
│   └── requirements.md  ← spec quality checklist (created by /speckit-specify; 16/16)
└── tasks.md             ← Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root — extends the existing Iteration-0 tree)

```text
app/
├── services/
│   ├── submission_service.py     # NEW: create/get/list/update/reassign, status transitions
│   │                             #      (event-sourced), CRM-tag add/edit/remove, duplicate
│   │                             #      warning, optimistic-concurrency conflict, read-only gate
│   └── package_service.py        # NEW: create package, add/remove member (irp_edm/irp_rdm),
│                                 #      ≥1-member invariant, attach-to-submission (M:N), soft delete
├── routers/
│   └── submissions.py            # NEW: master-detail routes (list My/All + filters, detail,
│                                 #      create, edit, status change, reopen, CRM-tag CRUD,
│                                 #      cedant autocomplete). CSRF + role-gated.
│   └── shell.py                  # EDIT: home() no longer queries `customer` (table dropped)
├── nav/
│   └── manifest.py               # EDIT: add submission-detail node(s) under `submissions`
├── templates/
│   └── pages/
│       ├── submissions.html      # EDIT: real master-detail list (replaces stub); My/All + filters
│       ├── submission_detail.html   # NEW: detail view — attributes, status, CRM tags, packages
│       ├── submission_form.html     # NEW: create/edit form + duplicate-warning + conflict banner
│       └── home.html             # EDIT: drop customer_count stat (table dropped)
│   └── partials/
│       ├── submission_row.html   # NEW: one list row (HTMX swap target)
│       ├── crm_tags.html         # NEW: CRM-tag set editor fragment
│       └── dup_warning.html      # NEW: non-blocking "similar deal exists" fragment
├── static/css/
│   └── submissions.css           # NEW: list/detail/status-chip/tag styling via tokens

db/
├── __init__.py                   # EDIT: remove apply_scope/scoped_execute export + docstring example
├── scope.py                      # DELETE
├── execute.py                    # EDIT: docstring — drop apply_scope/customer_id references
└── README.md                     # EDIT: remove db.scope path, RLS example, scope.py listing

alembic/versions/
└── 0001_initial.py               # EDIT: drop customer/program/user_customer_access;
                                  #       add submission domain + package + irp_edm/irp_rdm;
                                  #       seed submission_status_kind + treaty_type_kind

infra/scripts/
└── seed_db.py                    # EDIT: add submission_status_kind + treaty_type_kind seeds
                                  #       (idempotent MERGE, mirrors role_kind pattern)

tests/
├── unit/
│   ├── test_submission_service.py   # NEW: create/list/filter/reassign/status/CRM/dup/concurrency/gate
│   ├── test_package_service.py      # NEW: ≥1-member invariant (FR-024), M:N share, soft delete
│   ├── test_no_scope.py             # NEW: regression — db exposes no scope constructs (FR-032)
│   ├── test_db_package.py           # EDIT: drop apply_scope/scoped_execute import + scope tests
│   ├── test_db_config.py            # EDIT: remove the apply_scope test block
│   ├── test_scope.py                # DELETE
│   └── test_shell_routes.py         # EDIT: drop test_customer_count_in_page (or retarget)
└── sqlserver/
    └── test_submission_migration.py # NEW: migration builds; event-sourced status txn; FKs/PKs
```

**Structure Decision**: Single server-rendered web app, extending the existing `app/` package established in Iteration 0. Business logic lives in `app/services/*_service.py` (functions, not classes, matching `auth_service.py`); all persistence goes through the `db/` safe path. No new top-level packages. The cleanup edits are surgical, folded into the existing single Alembic revision (drop-create-seed), and are broader than the spec's enumerated list (see research.md §R8).

---

## Complexity Tracking

*No Constitution violations — no entries required.*

---

## Phase 0 — Research

See [research.md](research.md). All Technical Context items are inherited/known from Iteration 0; there were no `NEEDS CLARIFICATION` unknowns. Research instead records the eleven design decisions the implementation depends on (optimistic-concurrency mechanism, event-sourced status write pattern, read-only gate placement, non-blocking duplicate-warning UX, package ≥1-member invariant enforcement, cedant autocomplete, My/All filter, UUID generation, renewal self-ref prevention, inception-filter granularity, and the full cleanup surface incl. the files the spec did not enumerate).

## Phase 1 — Design & Contracts

- [data-model.md](data-model.md) — the nine Iteration-1 tables derived from DATA_MODEL §4–§5, with columns, keys, FKs, the event-sourced status mechanism, optimistic-concurrency marker, and the two kind-table seeds.
- [contracts/data-access.md](contracts/data-access.md) — the repository function contract (the primary developer-facing interface; FR-029).
- [contracts/http-routes.md](contracts/http-routes.md) — the submission/CRM/status/package route contract.
- [quickstart.md](quickstart.md) — rebuild + test + manual validation walkthrough.
- Agent context updated: the `<!-- SPECKIT START/END -->` block in `CLAUDE.md` now points at this plan.

**Post-Design Constitution Re-check: PASSED.** The data model adds no scoping key and no stored sequence; `submission.status_code` is written only via the event-sourced transaction; `irp_edm`/`irp_rdm` status columns honor the Article 3 carve-out. No violations introduced.
