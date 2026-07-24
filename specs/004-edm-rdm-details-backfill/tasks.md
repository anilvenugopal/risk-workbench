# Tasks: EDM/RDM Details & Backfill (Iteration 3)

**Input**: Design documents from `specs/004-edm-rdm-details-backfill/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [ui.md](ui.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: INCLUDED and test-first. The constitution **Article 12 (Test-First, Three Connected Strategies)** is a compliance gate, and the spec, `data-model.md §8`, and every `contracts/*` file carry explicit **Test Obligations**. Test tasks are therefore mandatory — write each test task and confirm it **FAILS** before the implementation tasks in the same story.

**Organization**: Tasks are grouped by user story so each can be implemented, tested, and demoed as an independent increment. Per `docs/UI_WORKFLOW.md`, implement **one vertical slice at a time** and **STOP** at each checkpoint for the approver to click the running slice before starting the next.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 / US4 (Setup, Foundational, Polish carry no story label)
- All file paths are repository-relative.

## Path Conventions

Single server-rendered web app extending the existing `app/` tree (FastAPI + Jinja2 + HTMX; poller + Dramatiq worker out-of-process). SQL via the `db/` package; single Alembic revision `alembic/versions/0001_initial.py` (drop-create-seed). See `plan.md` → Project Structure.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: One-time project prerequisites before schema/code work.

- [X] T001 [P] Add `openpyxl` to `[project.dependencies]` in `pyproject.toml` and run `uv sync` (the one new dependency — treaty `.xlsx` export, R5/FR-024)
- [X] T002 [P] Confirm the active `irp-integration` wheel (`make irp-status`) and verify the Risk Modeler **read**-method signatures — portfolio enumeration, per-portfolio exposure, treaty attributes, analysis metadata — against it; record confirmations and any gaps in `docs/IRP_INTEGRATION_FOLLOWUPS.md` (research R1; the wheel is pre-release)
- [X] T003 Confirm the §21.0 DB-lifecycle choice for **WORKBENCH** = **Rebuild** (drop-create-seed); note `EXPOSURE`/`LOSS` are untouched and DATABRIDGE is never touched (`plan.md` §21.0 note)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema + gateway plumbing that **every** user story reads from. No story work can begin until this phase is complete and the DB is rebuilt.

**⚠️ CRITICAL**: All four user stories read **stored** detail produced through this layer.

- [X] T004 Add the `irp_portfolio` and `irp_treaty` `create_table` statements to `alembic/versions/0001_initial.py` (both FK → `irp_edm.id`; `exposure_detail` / `attributes` `NVARCHAR(MAX)` null JSON snapshot cols; `as_of DATETIME2` null; `deleted_at`; audit cols; **UNIQUE(edm_id, irp_id)** + index `(edm_id)` on each) — in FK order after the Iteration-2 tables (data-model §2/§3)
- [X] T005 Edit the existing `irp_analysis` `create_table` in `alembic/versions/0001_initial.py` to add `settings_metadata NVARCHAR(MAX) null`, `is_group BIT not null default 0`, `exposure_resource_id NVARCHAR(64) null` (the RM portfolio pointer, R9/FR-036) — no `ALTER`; **`group_parent_id` is deferred** (nothing populates it — data-model §4/§6) (data-model §4)
- [X] T006 Add the `('backfill_edm_detail', 'Backfill EDM Detail', 27)` row to the `rwb_job_type_kind` seed in `alembic/versions/0001_initial.py`, and add the reverse-FK-order downgrade drops for `irp_treaty` then `irp_portfolio` (and their indexes) ahead of the Iteration-2 drops (data-model §1/§7)
- [X] T007 [P] Add an idempotent `MERGE` for the `backfill_edm_detail` `rwb_job_type_kind` row to `infra/scripts/seed_db.py` (same pattern as the existing kind MERGEs — data-model §7)
- [X] T008 [P] Add gateway value objects (`PortfolioHit`, `ExposureDetail`, `TreatyDetail`, `AnalysisMetadata`) and read methods (`list_portfolios`, `get_portfolio_exposure`, `search_treaties`, `get_analysis_metadata`) to the `IRPGateway` protocol + free functions in `app/services/irp_gateway.py`, **and extend `AnalysisHit` to carry `exposure_resource_id` + `exposure_resource_type`** (it currently **drops** RM's `exposureResourceId` — R9/FR-036) — single-status/read only; **never** `poll_*_to_completion` (Article 11; contracts/worker-poller.md)
- [X] T009 [P] Mirror the new read methods with canned portfolio / treaty / analysis-metadata payloads in `tests/unit/fakes/fake_irp.py` — including analyses with `exposureResourceType == "PORTFOLIO"` (linkable), a group, and a non-portfolio/unresolvable exposure (so the backfill worker, rollup, linkage resolution, and graceful-empty paths are unit-testable without IRP)
- [X] T010 [P] Write `tests/sqlserver/test_detail_tables_migration.py` — asserts the extended migration builds `irp_portfolio`/`irp_treaty` + the new `irp_analysis` columns with all FKs and the `UNIQUE(edm_id, irp_id)` keys, and that the `backfill_edm_detail` seed row is present (write to **FAIL** first)
- [X] T011 Run `make db-rebuild` (WORKBENCH drop-create-seed via the single revision) and confirm the new tables/columns/seed exist; run `pytest tests/sqlserver/test_detail_tables_migration.py --run-sqlserver` green

**Checkpoint**: Schema + gateway + fake ready and the DB is rebuilt — user story work can begin.

---

## Phase 3: User Story 1 - Understand each portfolio's exposure inside an EDM (Priority: P1) 🎯 MVP

**Goal**: On `import_edm` FINISHED, auto-backfill the EDM's per-portfolio exposure and show it as the inline, read-only **primary content** of the redesigned EDM detail page (header stays minimal — no cedant/LOB).

**Independent Test**: Import a multi-portfolio EDM, let it reach *ready*; open its detail page and confirm every portfolio is listed inline with its own figures (location/account/policy counts, perils/sub-perils, geography, currency, record volume) — a textual snapshot, no map, no create/edit/split control — populated with no analyst action.

### Tests for User Story 1 (write first — must FAIL before implementation) ⚠️

- [X] T012 [P] [US1] Write `tests/unit/test_backfill_edm_detail.py` (portfolio path) — worker fetches (fake IRP) → idempotently upserts `irp_portfolio` + JSON snapshot + `as_of`; a re-run **overwrites in place** (no duplicate rows on `UNIQUE(edm_id, irp_id)`); a gateway failure fails the `rwb_job` but leaves the EDM `ready` and recoverable (FR-005); one portfolio's failed exposure read does not abort the rest (FAIL first)
- [X] T013 [P] [US1] Extend `tests/unit/test_poller.py` — `import_edm` FINISHED enqueues **both** `upload_rdm` and `backfill_edm_detail` (idempotent on re-poll); a standalone/EDM-only import (`package_id` null / no RDMs) still enqueues `backfill_edm_detail`; a `FAILED`/`CANCELLED` terminal enqueues neither backfill (FAIL first)

### UI Preview for User Story 1 (new layout — approve before wiring) 🎨

- [x] T014 [US1] **DONE — approved.** The rendered preview of the redesigned EDM detail page is `docs/ui_previews/edm_detail.html` (rev 7, 👍): minimal header, aggregate strip (US4), inline per-portfolio table (primary content) with the per-portfolio linked-analyses expansion (US3/FR-037), treaty section (US2), the standalone RDM-grouped broker-analyses section (US3), and the empty / pending / failed / zero-portfolio states. Composition is fixed in `ui.md`. *(This one preview covers the whole EDM page shell, so US2's treaty section, US3's analyses sections, and US4's aggregate strip are derivative and skip their own preview.)* When wiring, **match the approved preview + `ui.md`.**

### Implementation for User Story 1

- [X] T015 [US1] Implement `portfolio_service.upsert_portfolio_detail(...)` in `app/services/portfolio_service.py` — idempotent upsert on `UNIQUE(edm_id, irp_id)` (fallback `(edm_id, name)`): insert or overwrite `exposure_detail` (verbatim JSON) + `as_of` in place; via `db.get_connection("WORKBENCH")` + explicit `conn.begin()` (Article 7; contracts/data-access.md)
- [X] T016 [US1] Implement `portfolio_service.list_portfolios(edm_id)` in `app/services/portfolio_service.py` — every portfolio of an EDM with parsed `exposure_detail` (None → graceful empty); read-only, no row scoping (Article 6)
- [X] T017 [US1] Add `_backfill_edm_detail_body` + `@dramatiq.actor(max_retries=0) def backfill_edm_detail(...)` + a `_BODIES` map entry in `app/workers/package_jobs.py` — reuse `runtime.run_job` claim/heartbeat; `get_edm`; skip (JobResult.ok) if missing or `irp_id` None; loop portfolios app-side (single-item `get_portfolio_exposure`) → `upsert_portfolio_detail`; `JobResult.ok/fail` leaving EDM `ready` on failure; no transaction held across a gateway round-trip (contracts/worker-poller.md §1)
- [X] T018 [US1] Extend `_handle_import_edm_terminal` in `app/poller/run.py` — on `status == FINISHED`, idempotently `enqueue_rwb_job(requestor_type="irp_job", requestor_id=job["id"], rwb_job_type="backfill_edm_detail", ...)` **before** the `if not rdm_ids: return` guard and independent of `package_id`; coexists with the existing `upload_rdm` enqueue via the distinct `rwb_job_type` under `UNIQUE(requestor_type, requestor_id, rwb_job_type)` (contracts/worker-poller.md §3)
- [X] T019 [US1] Implement `edm_service.get_edm_detail(edm_id)` in `app/services/edm_service.py` — light header (name, status, `as_of`, source file, identifiers, portfolio count; **MUST NOT** include cedant or LOB, FR-011) + `portfolios` (from `list_portfolios`); graceful empty when no snapshot; return `None` only if the EDM itself is missing (→ router 404). `get_edm` stays unchanged for the worker/recovery paths.
- [X] T020 [US1] Redesign `app/templates/pages/edm_detail.html` — minimal header + inline read-only **per-portfolio table** as primary content; graceful "detail not available — re-import to populate" / pending / failed states and a clear zero-portfolio state; **no** create/edit/split/filter control; no map (FR-010–FR-018)
- [X] T021 [P] [US1] Create `app/templates/partials/portfolio_row.html` — one portfolio's figures (location/account/policy counts, perils + sub-perils, geography, currency, record volume; TIV where present)
- [X] T022 [P] [US1] Create `app/static/css/details.css` — per-portfolio table styling via ITCSS design tokens (Article 9); wire into the page's stylesheet layers
- [X] T023 [US1] Update `_detail` in `app/routers/edms.py` to pass the full `edm_service.get_edm_detail(id)` payload to the redesigned template (replacing the minimal `{edm}` payload); 404 when `get_edm_detail` returns `None`
- [X] T024 [US1] Run `pytest tests/unit/test_backfill_edm_detail.py tests/unit/test_poller.py` green; manually verify quickstart steps 1, 2, 7 (auto-backfill on import; per-portfolio table; graceful state for a pre-capability EDM) — *tests green (unit 482, sqlserver 112); steps 1/2/7 verified by a scripted render walkthrough against the fake IRP (import → FINISHED → auto-backfill → populated table / pre-capability graceful state / 404); the approver's click-through of the running slice remains the checkpoint gate*

**Checkpoint**: US1 is fully functional and independently testable. **STOP** — the approver clicks the running slice before US2 begins.

---

## Phase 4: User Story 2 - Review treaty setup on an EDM (Priority: P2)

**Goal**: Show every treaty on an EDM at the EDM level with full attributes (expand/collapse, horizontal scroll) and a one-action Excel export — all from stored detail, read-only.

**Independent Test**: Open an EDM that has treaties; confirm they show at the EDM level with full attribute detail, expand/collapse individually, scroll horizontally when wide, and export the whole set to `.xlsx` in one action — with no create/edit control.

**Builds on US1**: extends the same `_backfill_edm_detail_body` worker and the `edm_detail.html` page / `get_edm_detail` payload (both introduced in US1).

### Tests for User Story 2 (write first — must FAIL before implementation) ⚠️

- [ ] T025 [P] [US2] Extend `tests/unit/test_backfill_edm_detail.py` (treaty path) — worker fetches treaties (fake IRP) → idempotently upserts `irp_treaty` + `attributes` snapshot + `as_of`; re-run overwrites in place, no duplicate rows (FAIL first)
- [ ] T026 [P] [US2] Write `tests/unit/test_treaty_export.py` — `build_treaty_workbook` produces a valid `.xlsx` over the treaty set (columns = union of attribute keys) from **stored** detail with **no** gateway call (FAIL first)

### Implementation for User Story 2

- [ ] T027 [US2] Implement `treaty_service.upsert_treaty_detail(...)` (idempotent on `UNIQUE(edm_id, irp_id)`, fallback `(edm_id, name)`) and `treaty_service.list_treaties(edm_id)` (parsed `attributes`, read-only, no scoping) in `app/services/treaty_service.py`
- [ ] T028 [US2] Extend `_backfill_edm_detail_body` in `app/workers/package_jobs.py` — after the portfolio loop, `search_treaties(edm_irp_id=...)` and `upsert_treaty_detail` per treaty (idempotent); update `JobResult.ok(portfolios=..., treaties=...)`
- [ ] T029 [US2] Implement `treaty_service.build_treaty_workbook(edm_id) -> bytes` in `app/services/treaty_service.py` (openpyxl; one row per treaty; columns = union of attribute keys across the set) — reads stored detail only, no Risk Modeler call (R5/FR-024)
- [ ] T030 [US2] Extend `edm_service.get_edm_detail` in `app/services/edm_service.py` to include `treaties` (from `list_treaties`) in the payload
- [ ] T031 [US2] Add `GET /edms/{id}/treaties.xlsx` in `app/routers/treaties.py` (new router; register in the app) — authenticated file download streaming `build_treaty_workbook(id)` with `Content-Disposition: attachment; filename="<edm>-treaties.xlsx"` and the xlsx media type; a read/GET (no CSRF), no Risk Modeler call (contracts/http-routes.md)
- [ ] T032 [US2] Add the treaties section to `app/templates/pages/edm_detail.html` — treaties at the EDM level, most collapsed, expand any to full attributes, horizontal scroll for wide sets, an "Export to Excel" link to `/edms/{id}/treaties.xlsx`; read-only (FR-020–FR-025)
- [ ] T033 [P] [US2] Create `app/templates/partials/treaty_row.html` — one treaty collapsed, expandable to full attributes (Alpine.js sliver) with horizontal scroll; extend `app/static/css/details.css` with token-based treaty styling
- [ ] T034 [US2] Run `pytest tests/unit/test_treaty_export.py` and the treaty path of `test_backfill_edm_detail.py` green; manually verify quickstart step 4 (expand/collapse + Excel export)

**Checkpoint**: US1 AND US2 both work independently. **STOP** for the approver to click the running slice.

---

## Phase 5: User Story 3 - Review broker (RDM) analyses and their settings (Priority: P2)

**Goal**: Surface broker analyses **grouped by `rdm_id`** (shown once across M EDMs), each with its settings/metadata **and the portfolio it ran against** (R9/FR-036) — on the RDM page (with an EDM column) and on the EDM page (standalone RDM-grouped section + per-portfolio inline panels, FR-037). No loss numbers, no comparison.

**Independent Test**: Open an imported RDM that produced broker analyses; confirm they are listed grouped by source RDM, each with settings/metadata + its resolved portfolio (or "Group" / "— not linked"), no loss numbers, no comparison. On the EDM page, confirm each portfolio's linked analyses appear inline and the full set appears in the standalone RDM-grouped section.

**Dependencies**: the **RDM-page** portion depends only on the Foundational schema/gateway (independent of US1/US2). The **EDM-page** portion (standalone section + per-portfolio inline analyses) **builds on US1** (`edm_detail.html`, `portfolio_row.html`, `get_edm_detail`, and the portfolio rows to resolve against).

### Tests for User Story 3 (write first — must FAIL before implementation) ⚠️

- [ ] T035 [P] [US3] Write `tests/unit/test_broker_analyses.py` — `list_broker_analyses` groups by `rdm_id` (an analysis across M EDMs shown once); `settings_metadata` parsed; missing fields render blank not error; `is_group` surfaced; **portfolio linkage (R9/FR-036): an analysis whose `exposure_resource_id` matches an `irp_portfolio.irp_id` in the same `edm_id` resolves to that portfolio; `is_group` → "Group"; null/unmatched/non-portfolio → "not linked"; resolution is order-independent (portfolio backfilled before AND after the analysis)** (FAIL first)
- [ ] T035b [P] [US3] Write `tests/unit/test_edm_analyses.py` — `list_edm_analyses(edm_id)` returns the EDM's analyses grouped by source `rdm_id` with resolved portfolios, and buckets linked analyses per portfolio while keeping group/unresolved rows standalone-only (FAIL first)
- [ ] T036 [P] [US3] Extend the existing `backfill_rdm_analyses` unit test — `settings_metadata` + `is_group` written per captured `irp_analysis`, and `exposure_resource_id` promoted **only when `exposureResourceType == "PORTFOLIO"`** (null otherwise, R9); idempotent with the existing pair capture on `UNIQUE(rdm_id, edm_id, irp_id)` (FAIL first)

### Implementation for User Story 3

- [ ] T037 [US3] Extend `_backfill_rdm_analyses_body` in `app/workers/package_jobs.py` — for each captured analysis, also fetch + store `settings_metadata` (richer `search_analyses` fields or `get_analysis_metadata`), set `is_group` from the payload, and **promote RM's `exposureResourceId` to `exposure_resource_id` only when `exposureResourceType == "PORTFOLIO"`** (null otherwise — R9; no portfolio lookup here, resolution is read-time); idempotent overwrite; **no** new poller enqueue and **no** new `rwb_job_type` (rides the existing `import_rdm` FINISHED chain, contracts/worker-poller.md §2)
- [ ] T038 [US3] Implement in `app/services/analysis_service.py`: `list_broker_analyses(rdm_id)` (grouped by `rdm_id`, parsed `settings_metadata` blank-on-missing, `is_group`, broker-only via `rdm_id` set) **with its resolved portfolio** via a `LEFT JOIN irp_portfolio ON edm_id + exposure_resource_id↔irp_id` (R9/FR-036 — `is_group` → "Group", no match → "not linked"); `list_edm_analyses(edm_id)` (same, scoped to an EDM, for the EDM page); and `analysis_counts(package_id=?, edm_id=?)` (FR-050)
- [ ] T039 [x] [US3] **DONE — approved.** Rendered previews exist and are 👍: `docs/ui_previews/rdm_detail.html` (rev 3 — RDM page: analyses grouped by RDM, EDM + Portfolio columns, per-analysis settings, group as a single "Group" row, not-linked + partial-metadata states) and the analyses sections of `docs/ui_previews/edm_detail.html` (rev 7). Composition fixed in `ui.md`. When wiring, **match the approved previews + `ui.md`.**
- [ ] T040 [US3] Extend `app/templates/pages/rdm_detail.html` — broker analyses grouped by `rdm_id` with **EDM + Portfolio columns** (resolved portfolio / "Group" / "— not linked", FR-036) + per-analysis settings/metadata (rate/event-rate one drill-down deeper); **no** loss numbers, no own-vs-broker comparison; graceful blank for missing metadata (FR-030–FR-036); match `ui.md §3`
- [ ] T041 [P] [US3] Create `app/templates/partials/broker_analysis_row.html` — one broker analysis + its settings/metadata + rate/event-rate sub-drill; a group rendered as a single "Group" row; **shared by the RDM page AND the EDM standalone section**; extend `app/static/css/details.css` with the token-based `.dtable` styling (frozen column, pinned + rail-connected body, `.drill` sub-drill — `ui.md §1`)
- [ ] T041a [US3] Extend `edm_service.get_edm_detail` in `app/services/edm_service.py` to include `analyses` (`analysis_service.list_edm_analyses(edm_id)`) and to attach each portfolio's **linked** analyses (bucketed by the R9 resolution; group/unresolved stay standalone-only) — builds on the US1 `get_edm_detail`
- [ ] T041b [US3] Wire the EDM-page analyses (builds on US1's `edm_detail.html` + `portfolio_row.html`): add the **standalone RDM-grouped Broker-analyses section** to `app/templates/pages/edm_detail.html` (reusing `broker_analysis_row.html`), and extend `app/templates/partials/portfolio_row.html` with the descriptive **Analyses** count + the **inline linked-analyses panel** (pinned + rail-connected per `ui.md §1.1/§2`); "None" when a portfolio has no linked analyses (FR-037)
- [ ] T042 [US3] Update the RDM detail handler in `app/routers/rdms.py` to render `analysis_service.list_broker_analyses(id)` (the EDM-page analyses ride the existing `get_edm_detail` payload — no `edms.py` change beyond US1's T023)
- [ ] T043 [US3] Run `pytest tests/unit/test_broker_analyses.py tests/unit/test_edm_analyses.py` and the extended `backfill_rdm_analyses` test green; manually verify quickstart step 5 (grouped broker analyses + settings + portfolio linkage on both the RDM and EDM pages)

**Checkpoint**: US1, US2, US3 all independently functional. **STOP** for the approver.

---

## Phase 6: User Story 4 - Quick-orientation aggregate rollup (Priority: P3)

**Goal**: A compact aggregate strip atop the EDM page and a per-EDM aggregate line on the submission package cards — both **derived** from the stored per-portfolio detail (no separate fetch). Also un-empties the package-card analysis counts (FR-050).

**Independent Test**: Open an EDM and confirm a compact aggregate strip rolls up its portfolios' figures; open a submission and confirm each EDM's package row shows a per-EDM aggregate line — both from stored detail, both showing a graceful pending state when detail is not yet backfilled.

**Depends on US1** (portfolio data + `get_edm_detail` + `edm_detail.html`); the FR-050 count portion depends on US3's `analysis_service.analysis_counts`.

### Tests for User Story 4 (write first — must FAIL before implementation) ⚠️

- [ ] T044 [P] [US4] Write `tests/unit/test_edm_detail_rollup.py` — `aggregate_exposure` derives sum counts / union perils+sub-perils / combine geography + currency set / portfolio count from per-portfolio snapshots; returns `None` when no snapshot; `get_edm_detail` surfaces it; graceful empty renders the pending state (FR-040/FR-042/FR-043) (FAIL first)

### Implementation for User Story 4

- [ ] T045 [US4] Implement `portfolio_service.aggregate_exposure(portfolios) -> EdmAggregate | None` in `app/services/portfolio_service.py` — pure function over the already-fetched snapshots (no DB, no RM); `None` when no snapshot (research R4)
- [ ] T046 [US4] Extend `edm_service.get_edm_detail` in `app/services/edm_service.py` to include the derived `aggregate` (`aggregate_exposure(portfolios)`); pending state when `None`
- [ ] T047 [P] [US4] Create `app/templates/partials/edm_aggregate_strip.html` — compact rollup strip above the per-portfolio table (total counts, portfolio count, union of perils, combined geography, currency set, total record volume); extend `app/static/css/details.css`; wire into the aggregate-strip slot in `edm_detail.html`
- [ ] T048 [US4] Extend `package_sync_service.get_package_cards` in `app/services/package_sync_service.py` — add the per-EDM aggregate orientation line (FR-041, from the same `aggregate_exposure` rollup, graceful pending when no snapshot) and the now-**populated** analysis counts (FR-050, via `analysis_service.analysis_counts`)
- [ ] T049 [US4] Extend `app/templates/partials/package_card.html` — render the per-EDM aggregate line + populated analysis counts (extends the spec-003 cards; graceful pending line when an EDM has no snapshot)
- [ ] T050 [US4] Run `pytest tests/unit/test_edm_detail_rollup.py` green; manually verify quickstart steps 3 and 6 (EDM aggregate strip; submission per-EDM line)

**Checkpoint**: All four user stories independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Cross-story verification and the compliance/quickstart gates.

- [ ] T051 [P] Confirm the shell conventions (FR-051 — breadcrumb/active-state as a function of manifest position, `hx-boost` nav, status-bar last-action) are inherited on the redesigned EDM detail and extended RDM detail pages, AND the `as_of` last-synced trust signal (FR-052) is surfaced wherever detail is shown — EDM header, per-portfolio and per-treaty rows — across the detail templates
- [ ] T052 [P] Add/confirm the Article-6 no-scope assertion (the `test_no_scope` pattern — no `customer`/scope column or filter on `irp_portfolio`/`irp_treaty`/`irp_analysis` or any detail read) and the Article-11 `--run-irp` assertion that `poll_*_to_completion` and the poll-inside convenience methods appear nowhere in the new worker/gateway code (`tests/irp/`)
- [ ] T053 Run the full `pytest tests/unit` and `pytest tests/sqlserver --run-sqlserver` green, then walk `quickstart.md` end-to-end (SC-001…SC-009), confirming forward-only automatic backfill (no bulk sweep; the per-EDM manual Sync is the only manual path, FR-003 as amended), the portfolio↔analysis linkage on both pages (SC-009), and that no Risk Modeler call occurs on any web request handler
- [ ] T054 [P] Update `docs/IRP_INTEGRATION_FOLLOWUPS.md` with any gateway method gaps/confirmations discovered during implementation (close the R1 loop)

---

## Addendum A — approved mid-iteration changes (2026-07-23, post-US1 checkpoint)

Approved at the US1 checkpoint after the sandbox exposed the real `/metrics` payload shape
(plan record: FR-003/FR-013 amendments, research R1/R7 amendments, data-model §2 rewrite,
constitution Art. 11 DataBridge clause).

- [X] T055 [ADD] Fix `portfolio_row.html` to read the real RM `/metrics` keys (`totalLocations`/`totalAccounts`/`totalPolicies`/`perilsExposed`); store snapshots namespaced as `{"metrics", "summary"}` in `_backfill_edm_detail_body`; reshape the fake `DEFAULT_EXPOSURE` + `tests/unit/test_backfill_edm_detail.py` to the real shape
- [X] T056 [ADD] Per-EDM manual Sync (FR-003 as amended): `edm_service.sync_detail` (`ensure_pending_rwb_job` keyed `analyst_request`+`edm_id` + dispatch), broadened `_latest_backfill_status` (both requestor keys, `updated_at DESC`), `EdmDetail.sync_running`, worker name-resolution for `irp_id`-less EDMs, `POST /edms/{edm_id}/sync` (CSRF), header/state-box Sync buttons, `tests/unit/test_edm_sync.py`
- [X] T057 [ADD] DataBridge exposure summary: `irp_gateway.get_edm_exposure_summary(*, edm_name)` → wheel `client.databridge.get_portfolio_exposure_summary` (contract in `docs/IRP_INTEGRATION_FOLLOWUPS.md`); worker merges per-portfolio `summary` with graceful `null` degradation (`output_data.summary = ok|unavailable`); fake knobs + tests. Workbench side ships before the wheel method exists (blocked only for live data)

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies — start immediately.
- **Foundational (Phase 2)**: depends on Setup — **blocks all user stories** (schema + gateway + fake + rebuild).
- **User Stories (Phase 3–6)**: all depend on Foundational.
  - **US1 (P1)**: no dependency on other stories.
  - **US2 (P2)**: builds on US1 (extends the `backfill_edm_detail` worker, `get_edm_detail`, and `edm_detail.html`).
  - **US3 (P2)**: the **RDM-page** portion (worker/gateway extension + `rdm_detail.html` + `analysis_service`) is independent of US1/US2 and can run in parallel once Foundational is done; the **EDM-page** portion (T041a/T041b — standalone section + per-portfolio inline analyses) **depends on US1** (`edm_detail.html`, `portfolio_row.html`, `get_edm_detail`, portfolio rows to resolve against).
  - **US4 (P3)**: depends on US1 (portfolio data + `get_edm_detail`); the FR-050 count portion (T048) depends on US3's `analysis_service.analysis_counts`.
- **Polish (Phase 7)**: depends on all targeted stories being complete.

### Within each story

- Tests are written **first** and must FAIL before implementation (Article 12).
- UI previews are **already built and approved** (`docs/ui_previews/edm_detail.html` rev 7, `rdm_detail.html` rev 3) and their composition is fixed in `ui.md`; wiring MUST match them. US2/US4/US3-EDM additions to the previewed EDM page are derivative and need no further preview (`docs/UI_WORKFLOW.md`).
- Services → worker/poller → routes → templates; story complete **and clicked** by the approver before the next priority.

### Parallel opportunities

- Setup: T001, T002 in parallel.
- Foundational: T007 (seed_db), T008 (gateway), T009 (fake), T010 (migration test) in parallel; T004→T005→T006 are sequential (same file `0001_initial.py`); T011 (rebuild) after all.
- US1: T012/T013 (tests) parallel; T021/T022 (partial + css) parallel with each other.
- Across stories: once Foundational is done, **US3's RDM-page portion can be developed in parallel with US1/US2** (different worker path + `rdm_detail.html`); US3's EDM-page portion (T041a/T041b) waits on US1.

---

## Parallel Example: User Story 1

```bash
# Write both US1 tests together (must fail first):
Task: "tests/unit/test_backfill_edm_detail.py — portfolio fetch + idempotent upsert + failure preserves ready"
Task: "tests/unit/test_poller.py — import_edm FINISHED enqueues both upload_rdm and backfill_edm_detail"

# Build the presentation partials together:
Task: "app/templates/partials/portfolio_row.html"
Task: "app/static/css/details.css (per-portfolio table tokens)"
```

---

## Implementation Strategy

### MVP first (User Story 1 only)

1. Phase 1 Setup → 2. Phase 2 Foundational (rebuild the DB) → 3. Phase 3 US1 → 4. **STOP and validate**: import a multi-portfolio EDM, confirm auto-backfill + the inline per-portfolio table + graceful states. Demo.

### Incremental delivery

US1 (MVP: per-portfolio breakdown) → US2 (treaties + Excel) → US3 (broker analyses + settings) → US4 (aggregate strip + submission line + populated counts). Each story is demoed independently before the next; the approver clicks the running slice at every checkpoint.

---

## Notes

- Backfill is a **forward extension** of the Iteration-2 completion path — no new poller, no new async spine; every Risk Modeler detail read runs in the worker behind `irp_gateway` (Article 11), never on a web request path.
- Detail is a **JSON snapshot cache** (`exposure_detail` / `attributes` / `settings_metadata`), overwritten idempotently in place; the EDM-aggregate is **derived**, never stored (research R2/R4).
- One new state-changing route this iteration (Addendum A: `POST /edms/{edm_id}/sync`, CSRF-protected); no new nav node; viewing + the `.xlsx` export are authenticated GETs.
- Commit after each task or logical group. Confirm tests FAIL before implementing. Stop at each checkpoint to validate the story independently.
