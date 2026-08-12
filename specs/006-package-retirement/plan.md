# Implementation Plan: Package Retirement

**Branch**: `006-package-retirement` | **Date**: 2026-08-12 | **Spec**: [spec.md](spec.md)

## Review

- Remove Package from schema, routes, services, workers, templates, assets, tests,
  current requirements, and execution diagrams.
- Add `submission_edm` and `submission_rdm`; one physical EDM/RDM can serve several submissions.
- Replace package cards with always-visible EDM and RDM tables on submission detail.
- Add direct-import and add-existing actions at the submission level.
- Add contextual EDM URLs so the source submission, EDM selector, and RDM list are deterministic.
- Load one RDM's stored analyses only when its row expands.
- Port the standalone-RDM subset of PR #57; do not merge or base the work on its Package expansion.
- Remove the EDM-completion to RDM-upload chain and the EDM x RDM import grid.
- Rebuild the pre-go-live WORKBENCH schema from the single Alembic revision.
- Preserve the current shell and page components; reuse existing EDM disclosure
  markup and caret behavior exactly.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Jinja2, HTMX, SQLAlchemy Core, Alembic,
Dramatiq, Redis, `irp-integration` 0.4.0 from TestPyPI

**Storage**: SQL Server WORKBENCH database; SQLite mirror for the unit tier

**Testing**: `uv run pytest tests/unit`; `make test-sql` from `linux-box`;
opt-in `uv run pytest tests/irp --run-irp` inside `linux-box`

**Target Platform**: Production Linux services plus separate SQL Server 2022

**Project Type**: Server-rendered FastAPI application with poller and workers

**Performance Goals**: Initial contextual EDM render does not include analysis rows;
one RDM disclosure fetches only that RDM; submission lists support 15 EDMs and 15 RDMs

**Constraints**: No container start/rebuild by agents; no Risk Modeler reads from page
handlers; all SQL through `db/`; no row-level access rules; one Alembic revision before go-live

**Scale/Scope**: 10-30 analysts, fewer than 1,000 submissions per year, normally 1-4
EDMs/RDMs per submission and about 15 each in the largest discussed case

## Constitution Check

**Violations**: None in the proposed design.

**Design-shaping articles**:

- Article 1: contextual submission/EDM position is explicit in the route and nav configuration.
- Article 2: jobs target EDM/RDM rows directly; no Package sequence or pairing is stored.
- Article 5: direct import triggers mechanical upload/backfill; selection and detach wait for a click.
- Article 6: submission joins organize data and never restrict analyst visibility.
- Article 7: all association and entity SQL stays in the `db/` safe path.
- Article 8: Jinja2 + HTMX tables, disclosures, and modals; no client data store.
- Articles 10-11: SQL queue, poller, workers, and gateway keep import/backfill outside page reads.
- Article 12: unit, SQL Server, and IRP contracts cover the schema and standalone import.

Re-check after the data model replaces `package_id`.

## Project Structure

```text
alembic/versions/
  0001_initial.py                 # EDIT: remove Package; add submission associations

app/
  main.py                         # EDIT: remove package router
  nav/                            # EDIT: contextual submission EDM position if required
  poller/                         # EDIT: remove EDM-to-RDM chaining and package finalization
  routers/                        # EDIT: submissions/edms/rdms; DELETE packages.py
  services/                       # EDIT: entity/submission/job reads; DELETE package services
  workers/                        # RENAME package_jobs.py to entity_jobs.py; remove package delete fan-in
  templates/pages/               # EDIT: submission and EDM detail
  templates/partials/            # ADD entity tables/add modal/lazy analyses; DELETE package partials
  static/css/                     # EDIT submissions/details; DELETE packages.css import/file
  static/js/                      # REMOVE package modal state; add only approved small client behavior

tests/
  iteration1_mirror.py            # EDIT schema mirror
  unit/                           # REPLACE package suites with associations/context/standalone-RDM tests
  sqlserver/                      # EDIT migration, drift, FK, and index assertions
  irp/                            # EDIT standalone RDM integration contract

docs/
  PRD.md                          # EDIT current feature and roadmap facts
  DATA_MODEL.md                   # EDIT canonical relationships and jobs
  FUNCTIONAL_REQUIREMENTS.md      # EDIT Package/nav/import/detail requirements without renumbering baseline rows
  IRP_INTEGRATION_FOLLOWUPS.md    # EDIT standalone RDM release evidence
  sequence_diagrams/              # REPLACE current Package/import/detail diagrams
  ui_previews/                    # ADD submission and contextual EDM previews before templates/routes

specs/
  002-submission-package-domain/  # EDIT: supersession pointer for Package half
  003-edm-rdm-entity-management/  # EDIT: supersession pointer for pair-import/package behavior
  004-edm-rdm-details-backfill/   # EDIT: supersession pointer for package-scoped RDM display
  006-package-retirement/         # feature owners
```

**Structure Decision**: Keep the server-rendered application and existing service
boundaries. Association operations live in a submission-data service used by
submission detail, contextual EDM detail, and add/detach routes. Rename the worker
module because it continues to own EDM/RDM import and backfill after Package is gone.

## Implementation Sequence

### 1. Decide the remaining product and dependency questions

- Render submission-detail and contextual EDM-detail previews from
  `docs/ui_previews/_scaffold.html`, including empty, failed, pending, and long-list states.
- Preserve the current application shell, submission controls, portfolio table,
  treaty table, disclosure markup, and caret behavior when wiring the previews.

### 2. Update the owning source documents

- Rewrite the Package sections in `docs/PRD.md`, `docs/DATA_MODEL.md`, and
  `docs/FUNCTIONAL_REQUIREMENTS.md` against direct submission associations.
- Keep historical design notes unchanged.
- Mark superseded behavior in specs 002-004 with links to this feature; do not
  rewrite their implementation history.
- Replace execution diagrams for Package code before deleting the Package code.

### 3. Replace the schema and test mirrors

- Edit `0001_initial.py` per `data-model.md` and update downgrade order.
- Update `tests/iteration1_mirror.py`, shared fixtures, SQL drift expectations, and
  migration constraint/index tests.
- Have the developer choose and run the documented WORKBENCH rebuild. DATABRIDGE,
  EXPOSURE, and LOSS receive no DDL.

### 4. Port standalone RDM import from PR #57

- Port the exact gateway capability from the confirmed TestPyPI 0.4.0 wheel.
- Remove `applied_edm_ids`, per-pair fan-out, EDM completion chaining, and pair-based
  analysis capture.
- Change broker-analysis identity and reads to RDM-wide rows with null `edm_id`.
- Port focused fake/unit/IRP assertions, adapting every association query to the new joins.

### 5. Implement one approved user story at a time

1. Build submission associations and the approved EDM/RDM tables end-to-end; stop for click approval.
2. Build direct import, add-existing, and detach end-to-end; stop for click approval.
3. Build contextual EDM navigation, EDM selector, submission RDM list, and lazy analysis load; stop for click approval.

Each story includes route, service, template, worker changes where applicable, unit-tier
tests, SQL Server tests for schema behavior, and a running-feature click before the
next story.

### 6. Remove Package code and verify subtraction

- Delete the package router/services/templates/CSS and Package-only JavaScript.
- Remove package filters, job counts, errors, imports, tests, and diagram links.
- Rename remaining package-named worker/test helpers whose operations target EDMs/RDMs.
- Search for Package references and retain only historical design/change records and
  explicit supersession notes.
- Review the diff for unused helpers, compatibility branches, copied rationale, and
  documentation outside the owning file.

## Planning Outputs

- [Research](research.md)
- [Data model](data-model.md)
- [HTTP routes](contracts/http-routes.md)
- [Worker and poller](contracts/worker-poller.md)
- [Verification](quickstart.md)

## Risk Review

- **Accidental redesign**: replacing Package must not alter the shell, submission
  controls, portfolio table, treaty table, or established disclosure behavior.
- **Context loss**: direct `/edms/{id}` links cannot choose a submission. The library
  page must remain context-free instead of selecting one silently.
- **Stale documentation**: PRD, functional requirements, older specs, and 22 current
  diagrams encode Package behavior. Current execution docs must change with code.
- **Large deletion diff**: much of the size comes from removing Package implementation
  and tests. The final review should compare retained code with the three user stories,
  not preserve Package compatibility.

## Complexity Tracking

No constitution violation requires an exception.
