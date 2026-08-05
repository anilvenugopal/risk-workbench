# Tasks: One-Click Portfolio Breakouts by LOB & Geography (Iteration 4)

**Input**: Design documents in `specs/005-subportfolio-breakouts/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: included. Constitution Article 12 requires the three tiers and names the prerequisite gate as a must-test; [plan.md](plan.md) §Testing and every file in [contracts/](contracts/) name the tests each change owes.

## Format: `[ID] [P?] [Story] [Ref] Description`

- **[P]**: different file, no dependency on an incomplete task — can run in parallel
- **[Story]**: `US1` / `US2` / `US3`; Setup, Foundational, and Polish tasks carry none
- **[Ref]**: the `FR-nnn` or `SC-nnn` from spec.md, or the `T-nn`/`P-nn` from the decision tables, that the task closes
- `- Proof:` lines name the test or observation that closes a task where it is not obvious

---

## Phase 1: Setup

**Purpose**: pin the pre-release library and decide the DB lifecycle before any code is written.

- [X] T001 Point the repo at the published `irp-integration` build with `make irp-testpypi` (user direction 2026-08-04: PR #21 landed as **0.3.0** on TestPyPI, superseding the `make irp-local` instruction) and record the resolved version — resolved: `irp-integration==0.3.0` from TestPyPI
- [X] T002 [T-02] Re-confirm the six consumed signatures against the **active** wheel — `search_accounts_by_portfolio_paginated`, `search_policies_paginated`, `search_locations_paginated`, `create_portfolio`, `manage_portfolio_accounts`, `search_portfolios` — against [contracts/irp-library.md](contracts/irp-library.md); note any drift in [research.md](research.md) before writing gateway code
  - Proof: the signature list in contracts/irp-library.md matches `python -c "import inspect, irp_integration..."` output for each method — verified against 0.3.0, no drift
- [X] T003 Record the DB lifecycle choice for this schema-affecting iteration — **Rebuild** chosen per [data-model.md](data-model.md) §8; `make db-rebuild` runs at T010, after the DDL and seeds land

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: schema, lineage writes, the summary keys the gate reads, and the dimension-agnostic half of `breakout_service`. Both breakout dimensions depend on all of it.

**⚠️ No user story work can begin until this phase is complete.**

### Schema (`rwb_workbench` only — data-model.md §1–3, §8)

- [X] T004 [P] [T-04] Create `breakout_dimension_kind` (`code` PK NVARCHAR(32), `label` NVARCHAR(128) NOT NULL, `sort_order` INT NOT NULL) in `alembic/versions/0001_initial.py`, **before** `irp_portfolio` for FK ordering and dropped after it in `downgrade()`; seed `lob` / `state`
- [X] T005 [T-04] [FR-009] Add `source_portfolio_id` (UNIQUEIDENTIFIER NULL, self-FK → `irp_portfolio.id`, `ondelete=NO ACTION` — SQL Server rejects a cascading self-reference), `breakout_dimension_code` (NVARCHAR(32) NULL, FK → `breakout_dimension_kind.code`), and `breakout_value` (NVARCHAR(256) NULL) to the `irp_portfolio` create statement in `alembic/versions/0001_initial.py`
- [X] T006 [FR-011] [T-04] Add the idempotency index to `alembic/versions/0001_initial.py`: `CREATE UNIQUE NONCLUSTERED INDEX uq_irp_portfolio_breakout ON irp_portfolio (source_portfolio_id, breakout_dimension_code, breakout_value) WHERE source_portfolio_id IS NOT NULL AND deleted_at IS NULL`
- [X] T007 [P] [T-03] Seed `run_breakout_lob` and `run_breakout_state` into `rwb_job_type_kind` in `alembic/versions/0001_initial.py`. `rwb_job_requestor_type_kind` needs **no** new row — the breakout enqueues under the already-seeded `analyst_request` code (T023)
- [X] T008 [T-04] Add idempotent MERGE blocks for `breakout_dimension_kind` (2 rows) and the two `rwb_job_type_kind` rows to `infra/scripts/seed_db.py`
- [X] T009 [P] [T-04] Extend `tests/sqlserver/test_detail_tables_migration.py`: the three lineage columns, the self-FK, `breakout_dimension_kind`, and the filtered unique index are built; a second live generated portfolio on the same (source, dimension, value) is rejected; a soft-deleted one does not block re-creation
  - Proof: `pytest tests/sqlserver --run-sqlserver` green
- [X] T010 Run `make db-rebuild` and confirm the seeds landed (`breakout_dimension_kind` has 2 rows, `rwb_job_type_kind` has the 2 new codes)

### Lineage writes (`portfolio_service` — contracts/data-access.md §2)

- [X] T011 [FR-009] [T-04] Add `insert_generated`, `adopt_generated`, and `find_generated` to `app/services/portfolio_service.py`; one write path enforces the integrity rule — the three lineage columns are set together, the source portfolio is in the same EDM, `inserted_by = actor_id` — and a constraint violation from `uq_irp_portfolio_breakout` is caught and reported as `skipped_existing`, not raised
- [X] T012 [P] [FR-009] Unit-test the integrity rule and the race-duplicate path in `tests/unit/test_portfolio_lineage.py`

### Summary extension — dimension-agnostic keys (data-model.md §5, research R11)

- [X] T013 [P] [FR-007] [P-13] New `sql/databridge/portfolio_account_total.sql` returning each portfolio's account total — the overlap denominator; read-only, worker-side
- [X] T014 [FR-002a] Capture the source portfolio's Risk Modeler `stampDate` into `exposure_detail` alongside the summary in `_backfill_edm_detail_body` (`app/workers/package_jobs.py`), read **before** the DataBridge read so the stored stamp is conservative
- [X] T015 [FR-005] [P-13] Extend the summary builder in `app/services/irp_gateway.py` to write `account_total` and the `breakout_values` container keyed by `breakout_dimension_kind.code`; readers parse defensively, as the spec-004 readers do
- [X] T016 [P] [FR-005] [FR-002a] Extend `tests/unit/test_backfill_edm_detail.py`: the stored snapshot carries `stampDate`, `account_total`, and a `breakout_values` dict; a snapshot written by the pre-iteration builder still parses

### `breakout_service` — gate, plan, overlap, confirm (contracts/data-access.md §1)

- [X] T017 [FR-002] [FR-003] [P-16] Create `app/services/breakout_service.py` with `BreakoutValue`, `DimensionEligibility`, `BreakoutGate`, and `evaluate_gate(edm_id, portfolio_id)`: EDM exists ∧ not deleted ∧ `status == 'ready'` ∧ portfolio live ∧ no `backfill_edm_detail` pending or running for the EDM; per dimension, the summary carries `breakout_values[dimension]` with ≥ 2 distinct values. A summary with no `breakout_values` key reads as **absent** and points at Sync — there is no fallback to `states`. Pure DB reads through `db.execute`
- [X] T018 [P] [FR-003] Gate truth table in `tests/unit/test_breakout_gate.py`: EDM status × deleted × `breakout_values` present/absent/malformed × 0/1/2+ values × in-flight `run_breakout_*` × in-flight `backfill_edm_detail`, including a pre-iteration summary (has `states`, no `breakout_values`) reading as absent
  - Proof: Article 12's named must-test
- [X] T019 [FR-010] [P-11] [T-05] Add `SubPortfolioPlan` and `build_breakout_plan` to `app/services/breakout_service.py` — pure, no I/O: name = `{source} - {value}` inside 40 characters with the **value kept whole** and the source name truncated, room reserved for a collision suffix, lowest free ` (2)`/` (3)` against existing **and** earlier planned names; number = `P{source RM id}-{S|L}-{token}` inside 20 characters with a hash tail on a long token; `exists` marks a live lineage row; sorted by value
- [X] T020 [FR-007] [P-13] Add `Overlap` and `compute_overlap(values, account_total)` to `app/services/breakout_service.py`: `summed` = Σ accounts over the dimension's values, `repeats` = `summed − account_total` floored at 0 (`None` when the total is absent), `partition` = `repeats == 0`
- [X] T021 [P] [FR-010] [FR-007] `tests/unit/test_breakout_plan.py`: naming determinism, the 40-character budget (source truncated, value whole, suffix room), the 20-character number budget with hash tail, collision suffixing against existing and intra-plan names, `exists` marking, ordering, and the three overlap cases (clean partition, heavy overlap, absent `account_total`)
- [X] T022 [FR-002a] Add `fetch_portfolio_stamp(exposure_irp_id, portfolio_irp_id)` to `app/services/irp_gateway.py`, wrapping the existing `search_portfolios` read; deliberately not named `get_*` — this is the Article 2 submit-time pattern and the architecture guard greps for web-layer `get_*` IRP calls. Mirror it in `tests/unit/fakes/fake_irp.py` with a seedable stamp per portfolio
- [X] T023 [FR-002a] [FR-002b] [FR-006a] [FR-006b] [P-14] [T-06] Add `request_breakout(edm_id, portfolio_id, dimension, summary_as_of, actor_id)` to `app/services/breakout_service.py` — five ordered steps, **no `rwb_job` row until all five pass**: gate re-check → the stored summary's `as_of` equals the one the preview carried (FR-002b) → `fetch_portfolio_stamp` equals the stamp captured at backfill (FR-002a) → `build_breakout_plan` runs once and its output goes into `input_data["plan"]`, each entry carrying value, label, name, number, **and the previewed account count** → `ensure_pending_rwb_job(requestor_type='analyst_request', requestor_id=portfolio_id, rwb_job_type=f'run_breakout_{dimension}')`, returning `None` when a live job already exists. `analyst_request` is the already-seeded `rwb_job_requestor_type_kind` code the other analyst-triggered enqueues use (`edm_service.sync_detail`); the uniqueness key still gives each dimension its own live-job slot per portfolio because `requestor_id` is the source portfolio and the two dimensions are two job types
- [X] T024 [T-10] [FR-012] Add `load_approved_plan(input_data)` and `summarize_outcomes(outcomes)` to `app/services/breakout_service.py`. `load_approved_plan` parses `input_data["plan"]` and reads **nothing else** — not the summary, not current portfolio names, and it never re-suffixes; an empty or unparseable plan fails the job. `summarize_outcomes` produces the `output_data` shape of [data-model.md](data-model.md) §4
- [X] T025 [P] [FR-002a] [FR-002b] [FR-006a] [FR-006b] Extend `tests/unit/test_breakout_gate.py` with the confirm path: stamp match + `as_of` match → plan persisted in `input_data` and one job enqueued; stamp mismatch, missing stored stamp, and gateway error → refusal; `as_of` mismatch → refusal **even when the stamp still matches** (the case FR-002a cannot see); every refusal writes no job row. The persisted plan matches the previewed one on value, label, account count, number, and the set of entries, while a portfolio created in the EDM between preview and confirm is allowed to move a collision suffix (FR-006b/P-14). Plus `load_approved_plan` on a plan whose names no longer match a recompute, and on an unparseable plan

**Checkpoint**: schema, lineage writes, summary keys, and the dimension-agnostic service are in place — user story work can begin.

---

## Phase 3: User Story 1 — One-click breakout by line of business (Priority: P1) 🎯 MVP

**Goal**: from the EDM detail page's portfolio table, an analyst previews and confirms one sub-portfolio per distinct LOB in the source portfolio; the sub-portfolios are created in Risk Modeler, persisted with lineage, and their exposure figures fill in automatically.

**Independent Test**: on an imported `ready` EDM whose source portfolio has a backfilled summary with ≥ 2 LOBs — open **Break out** → *By line of business*, read the preview (values, names, account counts, quantified overlap), confirm, and watch N portfolios appear in the list and then acquire figures with no Sync click. Then re-run and confirm nothing duplicates.

### UI preview for User Story 1 🎨

- [X] T026 [US1] [FR-006] [FR-007] Rendered HTML preview of the breakout modal in `docs/ui_previews/breakout_modal.html`, built from `docs/ui_previews/_scaffold.html` — LOB happy path (value, label column, generated name, account count, sub-portfolio count), the quantified overlap statement in all three forms, the blank-value disclosure, missing-summary disabled state with the Sync pointer, single-value disabled state, breakout-in-flight state, sync-running disabled state (P-16), and the completion banner including partial failure. Approved (informal 👍) before the Jinja2 template is built — approved 2026-08-04 after two review rounds (source account total added to the header; sync-from-modal closes the dialog; FR-006c copy softened per P-15 amendment)

### Implementation for User Story 1

- [X] T027 [US1] [FR-005] Add an account count to `sql/databridge/portfolio_lines_of_business.sql` and fill `breakout_values["lob"]` (`value` = LOB name, `label` = null, `accounts`) in the `app/services/irp_gateway.py` summary builder
- [X] T028 [US1] [T-01] Add `BreakoutSelection` and `select_breakout_accounts` to `app/services/irp_gateway.py` with the **LOB** branch: read the source portfolio's account ids once, then one `search_policies_paginated` pass scoped by `accountId IN (…)`, grouped client-side on `policy["lob"]["lobName"]`. Chunk by **composed filter length** against a named constant well below the measured 4,872-character HTTP 431 ceiling — the bearer token shares that budget. Never filter by `lobId` (HTTP 500). A per-value `IRPAPIError` from `paginate_search` lands in `errors_by_value`; a failure of the source account-id read raises
- [X] T029 [US1] [T-01] [T-07] [FR-008] [FR-010] Add `SubPortfolioResult`, `create_sub_portfolio` (create → add → read back and compare against the ids sent), `populate_sub_portfolio` (adopt-then-populate heal), and `find_portfolio_by_number` (returns **every** hit) to `app/services/irp_gateway.py`. `create_sub_portfolio` passes the caller's `description` through to `create_portfolio` untouched — Risk Modeler's description is where the untruncated lineage lives (FR-010), so the gateway never shortens it. `portfolio_number` is always passed explicitly — omitting it makes RM default the number to the name and overrun the 20-character cap. A duplicate-name failure surfaces as a **distinct** error type, because `IRPValidationError` also covers an over-long name and an over-long number
- [X] T030 [US1] [T-01] [T-07] Mirror `select_breakout_accounts`, `create_sub_portfolio`, `populate_sub_portfolio`, and `find_portfolio_by_number` in `tests/unit/fakes/fake_irp.py`: seedable per-value id lists, empty selections, per-value read errors, the duplicate-name raise, seedable create failures, read-back counts, and 0/1/many hits on the number
- [X] T031 [P] [US1] [T-01] Response-shape parsing tests against recorded bodies in `tests/unit/test_irp_gateway.py` — the account id is nested differently in each read and a wrong key returns a plausible empty result rather than an error
- [X] T032 [US1] [FR-008] [FR-010] [FR-011] [T-10] Create `app/workers/portfolio_jobs.py` with `_run_breakout_body` and the `run_breakout_lob` actor (actor name == `rwb_job_type`, loader convention), running under `runtime.run_job`: load EDM + source portfolio → `load_approved_plan` → `select_breakout_accounts` **once, before the loop** → per entry with try/except: `find_generated` live → `skipped_existing`; read error or empty id list → `failed` with the reason and **no create call**; otherwise `create_sub_portfolio` with the description composed here — the source portfolio name, the dimension label, and the value **in full and untruncated**, which is what carries the lineage the 40-character name loses (FR-010) — then `insert_generated` immediately after the RM call (RM call first, row second). A duplicate-name error branches to `find_portfolio_by_number` — exactly one hit adopts and re-runs the add, zero or more than one fails that entry. `completed < total` from the add is **not** a failure
- [X] T033 [US1] [FR-013] On completion including partial success, `_run_breakout_body` idempotently enqueues `backfill_edm_detail` for the EDM and records `backfill_enqueued`; the job succeeds when ≥ 1 entry created/adopted/skipped and fails only when zero succeeded
- [X] T034 [P] [US1] [FR-008] [FR-010] [FR-011] [FR-012] [FR-013] [T-10] `tests/unit/test_run_breakout_worker.py`: happy path (N rows with lineage and `inserted_by`, `backfill_edm_detail` enqueued once); **executes the persisted plan** (a stored plan whose names differ from a recompute runs verbatim; no summary read happens in the worker); the description reaching `create_sub_portfolio` carries the source name, dimension, and value untruncated even where the 40-character name truncated the source (FR-010); per-entry isolation; zero accounts → `failed` with no create call; per-value `IRPAPIError` fails one entry; `completed 0` on re-run reads as success; idempotent re-run creates only missing rows with identical names; adopt-by-number with 1 / 0 / many hits; the source portfolio deleted in Risk Modeler between confirm and run → every entry fails with the RM error recorded and no lineage row written (FR-012); zero-success → fail; empty or unparseable plan → fail with nothing created
- [X] T035 [US1] [FR-001] [FR-006] Create `app/routers/portfolios.py` with `GET /edms/{edm_id}/portfolios/{portfolio_id}/breakout` returning the modal fragment from `evaluate_gate` + `build_breakout_plan` + `compute_overlap`; nav key `irp.edm_library` (no new nav node); 404 fragment when the EDM or portfolio is missing or deleted. Register it in `app/main.py` alongside the other routers
- [X] T036 [US1] [FR-006] [FR-007] [FR-006c] Build `app/templates/partials/breakout_modal.html` from the approved preview: dimension chooser with per-dimension disabled-with-reason, the preview list (value, label, generated name, account count) with **no truncation regardless of count**, the sub-portfolio count, the quantified overlap statement plus the fixed note that exposure inflation can exceed account inflation, the blank-value disclosure, the FR-006c statement above 25 sub-portfolios held as one named constant, the `summary_as_of` hidden field, and the confirm button
- [X] T037 [US1] [FR-002a] [FR-002b] [FR-006a] [FR-016] Add `POST /edms/{edm_id}/portfolios/{portfolio_id}/breakout` to `app/routers/portfolios.py`: `validate_csrf_token` → `request_breakout` → the EDM body partial with `HX-Trigger` toast *"Breakout started — N sub-portfolios"*. Refusals return **409** plus the re-rendered fragment — gate refusal, summary-rewritten (*"This EDM was synced while you were reviewing…"*), stale stamp (*"Portfolio data has changed in Risk Modeler since the last sync — Sync the EDM, then retry."*), and already-running. HTMX and no-JS PRG both supported
- [X] T038 [US1] [FR-001] Add the **Break out** control to `app/templates/partials/portfolio_row.html` near the row's expand affordance, shown when `edm.status == 'ready'`, `hx-get`ting the modal route; retire the section-header note "split / breakout arrive Iteration 4"
- [X] T039 [US1] [FR-012] In `app/templates/partials/edm_detail_body.html`, keep the existing 3-second self-poll running while any `run_breakout_*` job for the EDM's portfolios is `pending|running`, show the in-flight indicator, and on the first poll after terminal render the completion banner from `output_data`. Failed sub-portfolios render a per-row error line **derived from the latest terminal breakout job for that portfolio + dimension** — it survives refresh and navigation, carries no dismissal state, and is superseded only by the next terminal run; zero-match reasons point at Sync
- [X] T040 [US1] [FR-016] Add modal preview-list, in-flight indicator, and banner styles to `app/static/css/details.css` using existing tokens and ITCSS layers — no hardcoded hex
- [X] T041 [P] [US1] [FR-001] [FR-006] [FR-002a] [FR-002b] `tests/unit/test_breakout_routes.py`: modal states (eligible / disabled-with-reason / breakout in-flight / sync in-flight), the preview list rendering value, label, name, and account count untruncated, the overlap statement in all three forms, the FR-006c statement present above the threshold and absent below it, CSRF enforcement, 409 gate refusal, 409 stale-stamp refusal with no job row, 409 summary-rewritten refusal with a matching stamp and no job row, the plan written into `input_data` at enqueue, double POST → one job, body-partial response shape, and 404 fragments
- [X] T042 [US1] [FR-016] Extend the architecture guards over `app/workers/portfolio_jobs.py` and the new `irp_gateway` functions in `tests/unit/test_architecture_guards.py` and `tests/irp/test_article11_guard.py`: `poll_*_to_completion` absent, no web-layer `get_*` IRP call, no DataBridge or Risk Modeler read on the request path other than `fetch_portfolio_stamp`
- [X] T042a [US1] [T-01] [FR-008] Rework the selection read and the composition read-back onto DataBridge SQL (R1 revised 2026-08-05 after the checkpoint run failed on a 248,000-account portfolio, W-20): new `sql/databridge/breakout_lob_accounts.sql` and `portfolio_member_count.sql`; `select_breakout_accounts` runs the parameterized script against the EDM's cached `databaseName` and takes `edm_name`; `populate_sub_portfolio` counts members via DataBridge; the REST selection, its filter chunking, and `MAX_COMPOSED_FILTER_CHARS` are deleted. Worker progress log lines at plan load and selection start/finish; `run_breakout_*` actors set a 60-minute dramatiq `time_limit` and `runtime.run_job` marks a `TimeLimitExceeded` kill `failed` so the reconciler cannot re-dispatch it into the same kill. Gateway, worker, and runtime tests updated

**Checkpoint**: the LOB breakout works end to end. **STOP** — the approver clicks the running feature before User Story 2 begins. First checkpoint run failed on scale (W-20) → T042a; re-verification pending.

---

## Phase 4: User Story 2 — One-click breakout by geography (state or state-equivalent) (Priority: P2)

**Goal**: the same action offers *By geography (state)*, enumerating the first-level administrative divisions present in the source portfolio — US states and non-US equivalents alike — and creating one sub-portfolio per value.

**Independent Test**: on a source portfolio with ≥ 2 states, choose *By geography (state)*, confirm, and check that a known multi-state commercial account appears in full in each of its state sub-portfolios in Risk Modeler — after the preview said it would. Repeat on a global portfolio with 40+ divisions: nothing truncated, nothing refused, the queue-occupancy statement shown.

### UI preview for User Story 2 🎨

- [ ] T043 [US2] [FR-006c] [FR-007] Add two states to `docs/ui_previews/breakout_modal.html`: the geography preview with the measured overlap statement and the explicit multi-state-account consequence, and a 40+ value fan-out rendered untruncated with the FR-006c queue-occupancy statement. Approved before the template edit

### Implementation for User Story 2

- [ ] T044 [US2] [FR-005] [P-12] Rewrite `sql/databridge/portfolio_states.sql` to return `Admin1Code`, `MAX(Admin1Name)`, and an account count, grouped and filtered on the **code** — the `COALESCE(Admin1Name, Admin1Code)` goes. Fill `breakout_values["state"]` (`value` = `Admin1Code`, `label` = `Admin1Name` or null, `accounts`) and switch the summary's `states` list to codes, in `app/services/irp_gateway.py`
- [ ] T045 [US2] [T-01] [P-12] Add the **state** selection script `sql/databridge/breakout_state_accounts.sql` — `(Value, AccountId)` pairs with `Value` = `Admin1Code`, mirroring the rewritten `portfolio_states.sql` joins so the vocabulary matches the summary (R1 as revised 2026-08-05) — and register it under `state` in `_SELECTION_SCRIPTS` in `app/services/irp_gateway.py`. `Admin1Name` is never a filter input (P-12)
- [ ] T046 [US2] [FR-004] Add the `run_breakout_state` actor to `app/workers/portfolio_jobs.py`, sharing `_run_breakout_body`
- [ ] T047 [US2] [FR-007] [FR-006c] Add the geography-specific disclosure to `app/templates/partials/breakout_modal.html` — the multi-state-account consequence stated explicitly — and confirm the untruncated list and the FR-006c statement render for a 40+ value fan-out
- [ ] T048 [US2] [P-12] Render state **codes** in the `states` column of `app/templates/partials/portfolio_row.html` and in `app/templates/partials/edm_aggregate_strip.html`; pre-change snapshots keep showing names until the next Sync and both must render
- [ ] T049 [P] [US2] [FR-004] [FR-005] [P-12] Extend `tests/unit/test_irp_gateway.py`, `tests/unit/test_run_breakout_worker.py`, and `tests/unit/test_breakout_routes.py` for the state dimension: the state script's row mapping per requested value, a non-US division enumerating with no separate mode, an un-geocoded EDM producing values with null labels, and the geography disclosure copy

**Checkpoint**: both breakout dimensions work. **STOP** — the approver clicks the running feature before User Story 3 begins.

---

## Phase 5: User Story 3 — Generated sub-portfolios are identifiable: lineage, naming, and audit (Priority: P3)

**Goal**: the portfolio list shows which portfolios were generated, from which source, by which dimension and value; the run is recoverable from the audit trail with no new table.

**Independent Test**: open an EDM after two breakouts — every generated row shows `↳ from {source} · {dimension label}: {value}` and broker-arrived rows are unchanged. Break out a generated portfolio after its summary backfills and confirm the chained row badges its immediate source only. Read the actor, timestamp, source portfolio, dimension, and per-sub-portfolio outcomes out of the job row and the business-event log.

### Implementation for User Story 3

- [ ] T050 [US3] [FR-014] Extend `list_portfolios(edm_id)` in `app/services/portfolio_service.py` with a LEFT JOIN on `source_portfolio_id` and a join to `breakout_dimension_kind`, adding `source_name`, `breakout_dimension_code`, the dimension `label`, and `breakout_value` to each row; ordering stays by name — grouping and indent are display concerns
- [ ] T051 [US3] [FR-014] Render the lineage badge `↳ from {source_name} · {dimension label}: {value}` on generated rows in `app/templates/partials/portfolio_row.html`, showing the **immediate source only** for chained lineage; broker-arrived rows unchanged. Add the badge style to `app/static/css/details.css` via tokens
- [ ] T052 [US3] [FR-015] [P-08] Add the business-event log lines: `"breakout %s requested for portfolio %s by analyst %s (n_sub_portfolios=%d)"` in `breakout_service.request_breakout`, and per-sub-portfolio created/adopted/failed plus the completion summary in `app/workers/portfolio_jobs.py`, carrying the actor id from `input_data`
- [ ] T053 [US3] [FR-015] [P-08] Confirm every audited field is recoverable with no audit-log table: actor from `input_data.actor_id` and the log line, timestamp from the job row, source portfolio from `requestor_id`, dimension from `rwb_job_type`, outcomes from `output_data.sub_portfolios`, and the confirming analyst on each generated row's `inserted_by`
  - Proof: a single worker test reads all six back after a partial-failure run
- [ ] T054 [P] [US3] [FR-014] [FR-015] Extend `tests/unit/test_snapshot_upsert.py` and `tests/unit/test_edm_detail_rollup.py` for the lineage-aware list read model (generated rows with NULL `exposure_detail` render the pending state; chained lineage shows the immediate source), and add the breakout log assertions to `tests/unit/test_business_event_logs.py`

**Checkpoint**: all three user stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T055 [P] [P-09] PRD documentation pass in `docs/PRD.md`: §23 O6-1/O6-2 register and the §10A.5 blocked-note record the 2026-07-29 direction (geography breakout ships; whole-account bucketing accepted and disclosed; no toggle awaited), and the §21 Iteration-4 entry narrows to the two one-click breakouts with the filtered builder and complement split as fast-follows
- [ ] T056 [P] [P-09] `docs/DATA_MODEL.md` propagation per [data-model.md](data-model.md) §7: the three lineage columns on §5 `irp_portfolio` with the immediate-source note and the filtered unique index, the `irp_portfolio ||--o{ irp_portfolio` relationship, the `breakout_dimension_kind` table-index row, the `inserted_by` first-use note, and the `exposure_detail` note that the summary gains `breakout_values` and `account_total` and that `states` holds codes
- [ ] T057 [P] [P-09] Update `docs/IRP_INTEGRATION_FOLLOWUPS.md` with the shipped library methods, the Platform-endpoints-only direction, and the three dead ends worth not re-researching: `allowDeepFilters`, the `filtered-accounts` PUT, and `lobId` filtering
- [ ] T058 [P] [P-09] Add the pointer note to `docs/FUNCTIONAL_REQUIREMENTS.md` §3 — a pointer, not a rewrite
- [ ] T059 [P] Correct `specs/005-subportfolio-breakouts/quickstart.md`: re-map every Exit-criteria line onto the criteria spec.md actually defines — SC-006/007/008 do not exist, and three of the surviving citations point at the wrong criterion (line 40 tags SC-001's zero-free-text claim as SC-003; line 42 tags SC-003's 30-second bound as SC-005; the idempotent re-run at lines 31/42 is SC-004) — then fix the stale `add_filtered_accounts` prerequisite (the add step is `manage_portfolio_accounts` — [contracts/irp-library.md](contracts/irp-library.md) §"What this feature does not use") and the "slice" wording throughout → "sub-portfolio". Step 6's "chained lineage renders sanely" becomes the check FR-014 now states: the chained row badges its immediate source only
- [ ] T060 [T-02] Re-pin `irp-integration` to the published build once PR #21 lands on TestPyPI: `make irp-testpypi`, confirm with `make irp-status`, and run `make test` against it
- [ ] T061 [T-01] [T-07] [SC-003] New opt-in `tests/irp/test_breakout.py`: the real select → create → add round-trip through the gateway against the sandbox, re-verifying selection tokens, chunking, idempotent re-add returning `completed 0`, the 40/20-character name and number limits, and whole-account bucketing. Add one large-fan-out case — a state breakout over 40+ first-level administrative divisions — asserting every entry lands with an outcome and none is refused for the size (SC-003)
  - Proof: `pytest tests/irp --run-irp` green
- [ ] T062 [FR-017] Confirm nothing outside this iteration's scope was added: no portfolio edit, delete, or merge; no rollback path; everything else on the EDM page stays read-only as shipped in spec 004
- [ ] T063 [SC-003] Run the [quickstart.md](quickstart.md) manual walkthrough end to end, including the gate states, the idempotent re-run, and the partial-failure banner. Record the wall-clock from confirm to the sub-portfolios appearing in the list for a ≤ 15-value breakout and check it against SC-003's 30-second bound

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (T001–T003)**: no dependencies
- **Foundational (T004–T025)**: needs Setup; **blocks all three user stories**
- **US1 (T026–T042)**: needs Foundational
- **US2 (T043–T049)**: needs Foundational; reuses `create_sub_portfolio`, `_run_breakout_body`, the routes, and the modal built in US1, so it runs after US1 in practice
- **US3 (T050–T054)**: needs Foundational; the badge is most useful after US1 has generated rows to badge
- **Polish (T055–T063)**: T055–T059 can run any time after the design settles; T060–T063 need all stories complete

### Within Foundational

- T004 → T005 → T006 (FK ordering, then the index on the columns)
- T004–T008 → T010 (`make db-rebuild` after all DDL and seeds)
- T013 → T015 (the script before the builder that reads it)
- T017 → T023 (the gate before the confirm that re-checks it)
- T019, T020, T022 → T023 (the plan builder, the overlap, and the stamp read before `request_breakout` composes and gates)

### Within US1

- T026 approved → T036 (preview before the template)
- T027 → T028 (LOB values in the summary before the selection that fans out over them)
- T028, T029, T030 → T032 (gateway and fake before the worker)
- T032 → T033 → T034
- T035 → T036 → T037 (route, template, then confirm)
- T037 → T039 (the POST's response partial is what the in-flight indicator rides)

### Within US2

- T043 approved → T047
- T044 → T045 → T046
- T045 → T049

### Parallel opportunities

- Setup: T001 and T003 are independent of T002
- Foundational: **T004 ∥ T007** (different blocks of the migration), then **T009 ∥ T012 ∥ T013 ∥ T016 ∥ T018 ∥ T021** once their subjects exist
- US1: **T031 ∥ T034 ∥ T041 ∥ T042** — four different test files, each against code already written
- US2: T049 is one parallel test task across three files
- US3: T054 runs parallel to T052/T053
- Polish: **T055 ∥ T056 ∥ T057 ∥ T058 ∥ T059** — five independent documents

---

## Parallel Example: Foundational tests

```bash
# Once T011, T015, T017, T019, T020 exist, launch the five test tasks together:
Task: "T009 SQL Server migration test in tests/sqlserver/test_detail_tables_migration.py"
Task: "T012 Lineage integrity tests in tests/unit/test_portfolio_lineage.py"
Task: "T016 Summary-extension tests in tests/unit/test_backfill_edm_detail.py"
Task: "T018 Gate truth table in tests/unit/test_breakout_gate.py"
Task: "T021 Plan builder and overlap tests in tests/unit/test_breakout_plan.py"
```

## Parallel Example: User Story 1 tests

```bash
Task: "T031 Response-shape parsing in tests/unit/test_irp_gateway.py"
Task: "T034 Worker loop in tests/unit/test_run_breakout_worker.py"
Task: "T041 Routes in tests/unit/test_breakout_routes.py"
Task: "T042 Architecture guards in tests/unit/test_architecture_guards.py"
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 Setup — pin the library, decide the DB lifecycle
2. Phase 2 Foundational — schema, lineage writes, summary keys, gate/plan/confirm
3. Phase 3 User Story 1 — the LOB breakout end to end
4. **STOP and validate**: run the quickstart's LOB walkthrough and the gate states against the running app

### Incremental delivery

1. Setup + Foundational → the schema and service are in place, nothing user-visible
2. US1 → LOB breakout shipped and clicked (MVP)
3. US2 → geography breakout shipped and clicked
4. US3 → lineage display and audit shipped and clicked
5. Polish → the PRD/DATA_MODEL pass, the TestPyPI re-pin, the sandbox test, the full quickstart

### Notes

- Implement **one user story per pass** and stop at its checkpoint for the approver to click the running feature (docs/UI_WORKFLOW.md rule 2)
- The worker executes the plan persisted at confirm — no task may reintroduce recomputation (AGENTS.md rule 8, approved plans are immutable / T-10)
- Created sub-portfolios are never deleted by the app; recovery is always idempotent re-run (P-07)
