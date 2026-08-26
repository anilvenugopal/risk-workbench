# Tasks: Analysis Results Sync & Viewing (Iteration 8)

**Input**: Design documents from `/specs/011-analysis-results/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R7), data-model.md, contracts/, quickstart.md

**Tests**: Included — plan.md names what each tier covers. Unit tests run after every
change (`uv run pytest tests/unit`); the SQL Server and IRP tiers need the
developer-started stack — report tiers by name and count, and say plainly when a tier
did not run.

**Organization**: Tasks are grouped by user story. US1 (own-analysis retrieval and
inline viewing) is the MVP; US2 adds the broker trigger; US3 the merged table with
currency/AAL and copy; US4 the dedicated results page. Each story stops at a
checkpoint for the approver to click the running feature before the next begins.

**Building the UI**: one preview is approved —
`docs/ui_previews/merged_analyses_table.html` (merged table, two-column expanded row,
results states, section summary line, empty states), approved 2026-08-26.
`docs/ui_previews/results_ep_table.html` is **superseded** — the 8/25–8/26 decisions
dropped the Display and EP-type selectors, changed the units selector, widened
perspectives to five, and removed the ELT metrics it shows; its banner lists all five.
Do not build from it. The dedicated results page (US4) has no approved preview yet: cut
one from `_scaffold.html` and get it approved at the start of Phase 6, per
[docs/UI_WORKFLOW.md](../../docs/UI_WORKFLOW.md) rule 1. **An approved preview is
guidance, not markup to paste.** Build against the components and CSS that already exist — `.dtable` in
`app/static/css/details.css`, the status chips, `btn-sm`, the section summary line — and
extend those when a preview needs something they do not have, rather than adding
preview-only classes. **Scope is the analyses tables and their controls**: the merged
table, its section summary line, the expanded row, and the dedicated results page. The
EDM detail header, the portfolio and geohazard sections, the submission header and every
other section stay as they are.

## Format: `[ID] [P?] [Story] [Ref] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1–US4 from spec.md; setup/foundational/polish tasks carry no story label
- **[Ref]**: the `FR-nnn` / `T-nn` / `O-nn` the task closes

---

## Phase 1: Setup

**Purpose**: Pin the external prerequisite and re-confirm the moving wheel.

- [ ] T001 Confirm `get_stats` / `get_ep` signatures and row shapes against the active wheel (`make irp-status`; irp-integration 0.6.2 `AnalysisManager`) before writing the gateway — the wheel is pre-release and moves (contracts/irp-gateway.md)
- [X] T002 [T-02] irp-integration 0.6.2 (TestPyPI pin, installed 2026-08-26) carries the full RM `PERSPECTIVE_CODES` vocabulary — WX and QS pass client-side validation, so no external dependency remains

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, seeds, mirror, and the gateway functions every story calls. No user story work can begin until this phase is complete.

- [ ] T003 [T-04] [T-06] [T-01] [T-09] In `alembic/versions/0001_initial.py` per data-model.md: add `irp_analysis.loss_results` and `irp_analysis.submitted_settings` (both NVARCHAR(MAX), NULL); create `analysis_perspective_kind` (code PK, label, sort_order, inserted_at) with seeds GR/RL/WX/QS/GU at sort_order 10–50 (Gross first = default); seed `rwb_job_requestor_type_kind` row `irp_analysis`
- [ ] T004 [P] [T-06] [T-01] Same seeds in `infra/scripts/seed_db.py` (`retrieve_analysis_results` already exists in `rwb_job_type_kind` — no job-type change)
- [ ] T005 Mirror T003 in `tests/iteration1_mirror.py`: SQLite DDL for `loss_results`, `submitted_settings` and `analysis_perspective_kind`, both seed sets (depends on T003–T004)
- [ ] T006 [P] [T-04] [T-09] SQL Server tier assertions in `tests/sqlserver/test_analysis_results_migration.py` (new): `loss_results` and `submitted_settings` columns present and NVARCHAR(MAX), the 5 perspective seeds in order, the `irp_analysis` requestor kind row, and a JSON extract write/read round-trip on `loss_results`
  - Proof: `make test-sql` (developer-run; unverified until the developer runs it)
- [ ] T007 [P] [FR-003] Add `get_analysis_stats` / `get_analysis_ep` to `app/services/irp_gateway.py` per contracts/irp-gateway.md — protocol + `_RealGateway` + module wrappers, keyword-only `analysis_id` / `perspective_code` / `exposure_resource_id`, RM row lists returned verbatim; worker-only (Article 11), never bypassing the wheel's perspective validation
- [ ] T008 [P] [FR-003] [FR-004] FakeIRP counterparts in `tests/unit/fakes/fake_irp.py`: accept all five perspective codes from day one; per-(analysis, perspective) fixtures shaped like the R3 captures including the TCE-OEP/TCE-AEP elements; default rows for GR/GU and empty lists otherwise; record calls for idempotency assertions
- [ ] T009 [FR-003] Unit tests for the two gateway wrappers in `tests/unit/test_irp_gateway.py`, and extend the guard in `tests/unit/test_architecture_guards.py` so no route module calls them (worker-only)

**Checkpoint**: Unit tier green on the new schema mirror and gateway — user story work can begin.

---

## Phase 3: User Story 1 — Read a finished analysis's loss numbers (Priority: P1) 🎯 MVP

**Goal**: When an own executed analysis reaches FINISHED, a worker retrieves the bounded
extract with zero analyst actions; the expanded analysis row shows how the run was
configured on the left and OEP+AEP at the condensed return periods on the right, with
the perspective toggle in the row; pending/failed retrievals show results-pending (with
reason) while the analysis stays FINISHED.

**Independent Test**: quickstart.md §US1 — execute a template, wait for FINISHED,
watch the inline results appear with no action; kill/fail a retrieval and confirm the
row stays FINISHED with results-pending + reason; re-fire the trigger and count exactly
one retrieval job.

UI reference: the expanded row in tile 1 of `docs/ui_previews/merged_analyses_table.html` — approved, and guidance (see **Building the UI** above).

### Implementation for User Story 1

- [ ] T010 [US1] [FR-003] [FR-004] [FR-005] [FR-021] [T-04] [O-03] Extract builder in `app/workers/analysis_jobs.py`: named constants for the 11 stored return periods and the 6-point condensed subset (data-model.md §4); pure function building the contracts/loss-results.md document from the verbatim stats/EP row lists — `aal` = `purePremium` and `std_dev` = `totalStdDev` from the stats row whose `epType` is `OEP` (no such row → both `null`, the same explicitly-empty perspective as an empty response; extra stats rows are unobserved and get no handling), epType filter keeps OEP/AEP and discards TCE-OEP/TCE-AEP (O-04), exact-match lookup of the 11 points in `value.returnPeriods`/`value.positionValues`, `engine_type`/`engine_version` snapshotted from the analysis metadata with absent fields stored as `null`. The perspective codes are passed in by the caller from its `analysis_perspective_kind` read (Article 3 — the builder holds no code list of its own) and every one of them is a key in the document, `null` when the analysis did not produce it
- [ ] T011 [US1] [FR-006] [FR-007] [T-01] [T-03] [T-08] `retrieve_analysis_results` actor in `app/workers/analysis_jobs.py` per contracts/worker-poller.md §2 (`max_retries=0`, standard `runtime.run_job` wrapper, `_BODIES` + `app/workers/dispatch.py` registration): skip when the analysis is missing or `loss_results IS NOT NULL`; fail when `irp_id IS NULL`; resolve `exposure_resource_id` — own rows via `irp_portfolio.irp_id`, one metadata re-read when NULL (also refilling engine fields when `settings_metadata` is NULL), still NULL → fail; per perspective in `analysis_perspective_kind` sort order call `get_analysis_stats` + `get_analysis_ep`, empty lists → explicitly-empty perspective, any exception → job `failed` with `error_detail` and `loss_results` untouched; write the whole extract in one UPDATE; `ok(perspectives_with_data=n, stats_rows={code: len(rows)})` — the row count lands in `rwb_job.output_data` (`JobResult.ok(**output)`, `app/workers/runtime.py:58`) so a stats response carrying more than one row becomes a queryable fact instead of a guess made now (contracts/loss-results.md)
- [ ] T012 [US1] [FR-001] [T-01] Chain enqueue in `_backfill_analysis_detail_body` (`app/workers/analysis_jobs.py`), after the successful UPDATE that stamps `irp_id`/`settings_metadata`/`ready`: `enqueue_rwb_job(requestor_type="irp_analysis", requestor_id=analysis_id, rwb_job_type="retrieve_analysis_results", input_data={"analysis_id": analysis_id})` — the queue UNIQUE key is the FR-006 dedup
- [ ] T012a [US1] [FR-022] [T-09] Submitted-settings snapshot: `_claim_analysis` (`app/workers/analysis_jobs.py`) writes the plan `item` verbatim into the new `irp_analysis.submitted_settings` in the INSERT that claims the row — the values the run is submitted with, never re-read from `analysis_template` afterwards (AGENTS.md architecture rule 8 — approved plans are immutable; data-model.md §1b)
- [ ] T013 [US1] [FR-008] [FR-010] [FR-022] [T-05] [T-09] Read models in `app/services/analysis_service.py` per data-model.md §5: results state (`loss_results IS NOT NULL` → ready; else `failed` retrieval `rwb_job` row → failed + `error_detail`; else pending), per-perspective AAL and standard deviation from the extract, currency parsed out of `settings_metadata` in Python via the existing `_parse_settings` (NULL or absent key → `—`; no JSON extraction in SQL — SQL Server has no `->>` and nothing in `app/` reads JSON in a query), condensed extract filtered to the §4 subset, the perspective list (codes/labels/order) from `analysis_perspective_kind`, the parsed `submitted_settings`, and a `framework` field on `AnalysisSettings` — `_to_display` folds `analysisFramework` into `analysis_mode` today, so ELT and the mode compete for one slot
- [ ] T014 [US1] [FR-011] [FR-012] [FR-008] [FR-022] [FR-023] Two-column expanded row: new partial `app/templates/partials/analysis_results_inline.html` rendered in the `executed_analysis_row.html` expansion — left column the source line (portfolio, analysis id) then the **Metadata** group (engine version, analysis type, peril, subperil, framework, event rate scheme, analysis template) and the **Analysis settings** group (currency code/scheme/vintage, min loss threshold, franchise deductible, unrecognized construction and occupancy), a field the origin does not supply listed and read as not returned; right column OEP and AEP at 50/100/250/500/1000/10000 with the perspective toggle in the row (kind table, Gross default), then **AAL** and **Std dev** as the last two rows of the same table (one value each, spanning both EP columns — `merged_analyses_table.html:351-352`), an unproduced perspective displayed as absent, plus the results-pending and failed-with-reason states. Values wrap and carry `title` tooltips; the columns stack when the row is too narrow. No perspective or units control on the table itself (O-12). The new partial replaces `_analysis_metadata_fields.html` in this expansion; that partial's remaining callers keep it until they move too. **Four fields it renders today leave the expanded row**: Construction, Line of business, Term, and Loss amplification (PLA) — not in O-11, not in the approved preview, and dropped for now to be re-added as the team asks for them. Engine type and Region are not dropped: they read from the merged table's Engine and Region columns instead

### Tests for User Story 1

- [ ] T015 [P] [US1] [FR-004] [FR-006] [FR-007] Unit tests in `tests/unit/test_analysis_jobs_worker.py`: builder against the captured fixture shapes (TCE drop, 11-point exact lookup, empty perspective → explicit `null`, engine snapshot, absent stats fields → `null`, `aal`/`std_dev` taken from the `OEP` stats row and `null` when no stats row is `OEP`, and a sixth perspective code passed in producing a sixth key — the builder holds no code list); worker skip when `loss_results` set (zero FakeIRP calls), fail on missing `irp_id`/pointer, failure leaves `loss_results` NULL and the analysis `ready`; chain enqueue fires once on backfill success and a re-fired trigger is a no-op insert; `_claim_analysis` writes `submitted_settings` from the plan item and a resumed claim leaves the stored value alone (T-09)
- [ ] T016 [P] [US1] [FR-008] [T-05] [T-09] Unit tests in `tests/unit/test_analysis_service.py`: results-state derivation including the failed-reason join, per-perspective AAL and standard deviation, currency extraction and `—` fallback, condensed filter, `submitted_settings` parsing (absent → all four settings blank), and `framework` no longer competing with `analysis_mode`
- [ ] T017 [P] [US1] [FR-011] [FR-022] [FR-023] Route tests in `tests/unit/test_edm_analyses.py`: expanded row renders both groups and the inline results block including its AAL and Std dev rows, a not-returned field is listed rather than hidden, results-pending row, the perspective toggle defaults to GR, and a long value's cell carries the full text in `title` (the wrapping itself is the quickstart §US1 step 4 eye check)
- [ ] T018 [US1] [FR-006] SQL Server tier in `tests/sqlserver/test_analysis_results_migration.py`: retrieval enqueue dedup under the real `UNIQUE(requestor_type, requestor_id, rwb_job_type)` key
  - Proof: `make test-sql` (developer-run)
- [ ] T019 [US1] [T-02] [T-08] IRP sandbox test in `tests/irp/`: one own FINISHED analysis — stats + EP for all five perspectives (the run is what proves RM itself serves WX/QS) and the T-08 empty-perspective shape (empty list, not an error)
  - Proof: `make shell` → `uv run pytest tests/irp --run-irp` (developer-run)

**Checkpoint**: Own-analysis results arrive and read end-to-end. **STOP** — approver clicks the running feature before US2.

---

## Phase 4: User Story 2 — Broker loss numbers appear on RDM import (Priority: P2)

**Goal**: RDM import completion triggers one retrieval per broker source analysis with
no analyst action; the extract is stored once per (`rdm_id`, `irp_id`) row and shared by
every EDM copy; broker rows show the same inline results; no portfolio attribution.

**Independent Test**: quickstart.md §US2 — import an RDM, watch broker rows fill with
zero clicks; import a second EDM copy of the same RDM and confirm no new retrieval jobs
and identical numbers on both copies.

### Implementation for User Story 2

- [ ] T020 [US2] [FR-002] [FR-006] [T-01] Broker chain enqueue in `_backfill_rdm_analyses_body` (`app/workers/entity_jobs.py`), after the capture transaction commits: for every live (`rdm_id`, `irp_id`) row of this RDM with `loss_results IS NULL`, the same `enqueue_rwb_job` keyed on that row's `irp_analysis.id`; rows already carrying results enqueue nothing
- [ ] T021 [US2] [T-03] Broker pointer branch in the `retrieve_analysis_results` body (`app/workers/analysis_jobs.py`): broker rows (`rdm_id IS NOT NULL`) use stored `irp_analysis.exposure_resource_id`, with the T011 metadata re-read when NULL — closes spec O-02
- [ ] T022 [US2] [FR-020] [FR-011] [FR-022] Broker row display: results state and the two-column expanded row (reusing T014) in `app/templates/partials/broker_analysis_row.html` / `contextual_rdm_analyses.html`; the analysis template and all four analysis settings read as not returned, since Risk Modeler returns none of them; no broker row names a portfolio
- [ ] T022a [US2] [FR-024] [FR-025] Two fields on `BrokerAnalysis` (`app/services/analysis_service.py`): `rm_url`, built the same way `ExecutedAnalysis.rm_url` is, and `created_at` from the payload's documented `createDate` — so broker rows fill the Risk Modeler and Submitted columns instead of showing `—`

### Tests for User Story 2

- [ ] T023 [P] [US2] [FR-002] [FR-006] Unit tests in `tests/unit/test_rdm_sync.py`: RDM backfill enqueues one retrieval per captured live analysis, skips rows with stored results, and a re-import of another EDM copy enqueues nothing and triggers zero FakeIRP calls; broker pointer resolution (stored value, re-read fallback, still-NULL fail) in `tests/unit/test_analysis_jobs_worker.py`
- [ ] T024 [P] [US2] [FR-020] [FR-024] [FR-025] Render tests in `tests/unit/test_broker_analyses.py`: broker rows show results state and the expanded row with the not-returned fields listed, carry a Risk Modeler link and a Submitted value from `createDate`, and name no portfolio anywhere
- [ ] T025 [US2] [T-03] IRP sandbox test in `tests/irp/`: broker pointer (`exposure_resource_id`) against an RDM-imported analysis returns stats/EP rows
  - Proof: `uv run pytest tests/irp --run-irp` inside `make shell` (developer-run)

**Checkpoint**: Broker results arrive once per RDM and read on every copy. **STOP** — approver clicks before US3.

---

## Phase 5: User Story 3 — One merged analyses table with inline results (Priority: P2)

**Goal**: One table on the EDM detail and a new submission Results section — own rows
plus expandable RDM group rows, one column set for both origins, and client-side
copy-with-headers and Submitted formatting.

**Independent Test**: quickstart.md §US3 — one merged section on the EDM detail, the
same shape submission-wide, the status filter and selection survive the 3s poll, table
pastes into Excel with headers, Submitted reads in the local timezone.

UI reference: `docs/ui_previews/merged_analyses_table.html` tiles 1, 2, 3, 4 and 5 (the two pages, the AAL results states, the section summary line, the empty states) — approved, and guidance (see **Building the UI** above).

### Implementation for User Story 3

- [ ] T026 [US3] [FR-009] Submission-scoped merged read model in `app/services/analysis_service.py`: own analyses across every EDM of the submission (with EDM name) plus broker groups for every related RDM, origin derived from `rdm_id IS NULL`
- [ ] T027 [US3] [FR-009] [FR-010] Merged analyses section on the EDM detail: new partial `app/templates/partials/analyses_merged_section.html` replacing the separate "Analyses" and "Broker analyses" sections — own rows (existing checkbox/status/failure/delete/settings behavior) plus one expandable group row per submission RDM lazy-loading as today. Columns: Analysis · Type · Peril · Region · Engine · Currency · AAL · Status · Submitted · Risk Modeler, AAL Gross in millions (`—` when the perspective is empty, `retrieving…` / `retrieval failed` for the pending and failed states), no return-period column, AAL-only display mode dropped, no perspective or units control on the table (O-12). Section summary line gains **Copy table** and **View** beside the status filter and Delete; Delete disables whenever a broker row is ticked. Delete, status filter, and the 3s self-poll carry over unchanged (`app/routers/edms.py`)
- [ ] T028 [US3] [FR-009] [FR-013] Submission Results section: new fragment endpoint `GET /submissions/{submission_id}/analyses` in `app/routers/submissions.py` rendering the same merged partial submission-wide with an EDM column after Analysis (the `show_edm` flag the broker row already takes; broker rows read `—`); included in `app/templates/pages/submission_detail.html`, self-polling every 3s only while any listed analysis or retrieval is live
- [ ] T029 [US3] [FR-018] [FR-024] [T-10] Copy and Submitted slivers in `app/static/js/app.js`: copy-to-clipboard as TSV with headers over `data-value` attributes (no server round trip, no recomputation of stored numbers), and the Submitted column formatted from `<time datetime="…Z">` with `toLocaleString` — date, time to the second, AM/PM in the reader's own zone, full value as the cell's `title`

### Tests for User Story 3

- [ ] T030 [P] [US3] [FR-009] Unit tests in `tests/unit/test_analysis_service.py`: submission-scoped merged read model (own rows across EDMs, broker groups, origin derivation)
- [ ] T031 [P] [US3] [FR-009] [FR-010] [FR-018] [FR-024] Route tests in `tests/unit/test_edm_analyses.py` and `tests/unit/test_submission_routes.py`: one merged section with both origins and group rows, the full column set including the submission page's EDM column, the four AAL states, the status param persisting across the poll URL, submission Results section renders, `data-value` attributes present for the copy sliver, and Submitted emitted as `<time datetime>` in UTC

**Checkpoint**: The merged table reads end-to-end on both pages. **STOP** — approver clicks before US4.

---

## Phase 6: User Story 4 — View several analyses on the dedicated results page (Priority: P3)

**Goal**: Multi-select → View opens `/results/analyses` in a new tab: one column per
analysis in selection order, all 11 return periods, both EP types, screen-wide
perspective, entity breadcrumbs and tab title, reorder controls, horizontal scroll past
~10 columns.

**Independent Test**: quickstart.md §US4 — select 3–5 analyses on either page, View
opens the new tab with columns in selection order; reorder moves columns; perspective
follows screen-wide; >10 selections scroll horizontally; originating-tab selection resets.

### Implementation for User Story 4

- [ ] T031a [US4] [O-08] Cut the dedicated-page preview from `docs/ui_previews/_scaffold.html` and get it approved before T034: N analyses side by side, all 11 return periods × both EP types, AAL and Std dev rows, the five-perspective screen-wide dropdown, the ones/thousands/millions selector, copy control, reorder controls, and the states that matter — a pending column, a failed column, past-10-columns horizontal scroll. `results_ep_table.html` is superseded and is not the reference (see **Building the UI** above)
- [ ] T032 [US4] [T-07] [FR-014] Shell and nav plumbing: optional `extra_crumbs` (`[{label, route}]`) rendered after `nav.breadcrumb` in `app/templates/base/shell.html` (pages that do not pass it are unaffected); hidden child node `results.analyses` under the `results` rail root in `app/nav/manifest.py` (pattern: `submissions.detail`)
- [ ] T033 [US4] [FR-012] [FR-013] [FR-014] [FR-015] [FR-016] [T-07] Handler `GET /results/analyses?ids=…[&submission=…][&edm=…][&perspective=GR]` in `app/routers/shell.py` per contracts/routes.md §3: `ids` order = column order; `perspective` re-renders over HTMX screen-wide; `{% block title %}` = the submission or EDM name; `extra_crumbs` — `edm=` present → submission crumb then EDM crumb, else submission crumb only, both linking back; unknown/deleted ids render an absent-analysis notice, never a 500; pending/failed analyses render as pending columns, never dropped
- [ ] T034 [US4] [FR-015] [FR-017] [FR-019] [FR-020] [O-09] Page template `app/templates/pages/results_analyses.html` following the Phase 6 preview (cut and approved first — see **Building the UI** above; the EP block of `merged_analyses_table.html` carries the table shape it starts from): all 11 return periods × both EP types, then **AAL** and **Std dev** as the last two rows, one value per analysis column; one column per analysis with name/currency/results-state header, `overflow-x` shell (no pagination, no count block), numbers and tables only, broker columns never naming a portfolio. Toolbar carries the T029 copy control plus the ones/thousands/millions selector (millions default, never auto-switching) — the one place a units control exists (O-12); AAL and Std dev follow the selector like every other number
- [ ] T035 [US4] [FR-013] [FR-014] [FR-016] [O-10] Selection and ordering controls: **View** button on both merged-section toolbars — a `target="_blank"` GET form posting the checked ids in check order, checkboxes reset after submit; reorder controls on the dedicated page rewriting the `ids` param and re-requesting (`app/templates/partials/analyses_merged_section.html`, `app/static/js/app.js`)

### Tests for User Story 4

- [ ] T036 [P] [US4] [T-07] [FR-012] [FR-014] [FR-016] Unit tests in `tests/unit/test_shell_routes.py` and `tests/unit/test_nav_manifest.py`: hidden nav node, column order follows `ids`, the `perspective` param re-rendering every column (screen-wide, not per column), both breadcrumb variants, tab title carries the entity name, absent-id notice, pending column renders

**Checkpoint**: All four stories functional. **STOP** — approver clicks the dedicated page.

---

## Phase 7: Polish

- [ ] T037 Subtraction review of the full diff per AGENTS.md: remove comments/tests restating the implementation, inline single-use helpers, delete speculative branches; compare diff size with requirement size
- [ ] T038 Run the quickstart.md walkthrough end-to-end (developer-run stack) and report tiers by name and count — unit tier count, SQL Server tier (`make test-sql`), IRP sandbox tier (`--run-irp`)

---

## Dependencies & Execution Order

- **Setup (Phase 1)** → **Foundational (Phase 2)** → user stories. T003 → T005; T003/T004 before T006.
- **US1 (Phase 3)** depends only on Foundational. T010 → T011 → (T012, T012a same file); T012a → T013 → T014; tests after their subjects.
- **US2 (Phase 4)** depends on US1's worker (T011) and expanded-row partial (T014). T020, T021 and T022a are independent of each other; T021 edits the same file as T011 (sequential with US1, not with T020).
- **US3 (Phase 5)** depends on US1's read models (T013); T026 → T027/T028; T029 is independent of T026.
- **US4 (Phase 6)** depends on US3's merged section (T027) for the View button and on T013 for read models. T031a (approved preview) → T034; T032 → T033 → T034; T035 last.
- **Polish (Phase 7)** after all desired stories.

### Parallel opportunities

- Phase 2: T004, T006, T007, T008 in parallel after T003; T009 after T007/T008.
- US1 tests T015–T017 in parallel (different files).
- US2: T020 ∥ T022 ∥ T022a; T023 ∥ T024.
- US3: T029 ∥ T026; T030 ∥ T031.
- Different stories are sequential by policy (one story per pass, approver checkpoint between).

## Implementation Strategy

MVP = Phase 1 + Phase 2 + US1: retrieval works end-to-end for own analyses and the
numbers read inline — demonstrable before any broker or merged-table work. Then US2
(broker trigger reuses the same worker), US3 (merged table + units/copy), US4
(dedicated page). Nothing external is outstanding: the wheel accepts all five
perspectives (T002), and FakeIRP does too from T008 on.
