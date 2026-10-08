# Tasks: Scoped Portfolio Refresh

**Input**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/jobs.md`, `quickstart.md` in `specs/207-scoped-portfolio-refresh/`.

**Tests**: Included. `plan.md` §Testing lists them. The unit tier (`uv run pytest tests/unit`) is the proof for every code task.

**Organization**: Phase 1 adds the `refresh_portfolios` job type and worker, which stories 1 and 2 both enqueue. Phases 2–4 are stories 1–3. No task adds a UI preview: the only visible change is a new gate reason in the existing modal note.

---

## Phase 1: Foundational — the `refresh_portfolios` job

Blocks stories 1 and 2. Story 3 does not depend on it.

- [X] T001 [T-01] Add `alembic/versions/0008_refresh_portfolios_job_type.py`, modelled on `0006_irp_job_list_indexes.py`. `upgrade()` inserts the `rwb_job_type_kind` row (`refresh_portfolios`, `Refresh portfolios`, 26) with `op.execute`; `downgrade()` deletes it. Run `uv run alembic heads` first; if a merged branch took `0007`, take the next id and point `down_revision` at the current head.
- [X] T002 [P] [T-01] Add `("refresh_portfolios", "Refresh portfolios", 26)` to `RWB_JOB_TYPE_SEED` in `tests/iteration1_mirror.py` and the same row to the `rwb_job_type_kind` MERGE in `infra/scripts/seed_db.py`, each after `backfill_rdm_analyses`.
- [X] T003 [P] [FR-004] [T-05] In the 11 scripts `get_edm_exposure_summary` runs, add `AND ({{ portfolio_ids }} IS NULL OR <alias>.PORTINFOID IN (SELECT CAST(value AS INT) FROM STRING_SPLIT({{ portfolio_ids }}, CHAR(31))))` at the `portacct` read, where `<alias>` is the alias that script gives `portacct`. The scripts are `sql/databridge/portfolio_list.sql` (filter `portinfo` instead), `portfolio_account_total.sql`, `portfolio_countries.sql`, `portfolio_states.sql`, `portfolio_lines_of_business.sql`, `portfolio_perils.sql`, `portfolio_currencies.sql` (filter the `acct` CTE), `portfolio_lob_coverage.sql`, `portfolio_state_coverage.sql`, `portfolio_country_coverage.sql` and `portfolio_peril_coverage.sql`. Copy the form from `breakout_match_count.sql`. Leave `portfolio_member_count.sql` and the breakout scripts alone.
- [X] T004 [FR-004] [T-05] Add `portfolio_irp_ids: Sequence[str] | None = None` to `get_edm_exposure_summary` in all three places in `app/services/irp_gateway.py`: the protocol (line 563), `IRPGateway` (line 1028) and the module function (line 1617). In `IRPGateway`, the `rows()` helper passes `params={"portfolio_ids": None if portfolio_irp_ids is None else "\x1f".join(str(i) for i in portfolio_irp_ids)}` to `execute_query_from_file`. Every call passes the key, because a missing key raises "Missing required parameter". In `tests/unit/fakes/fake_irp.py` (line 562), the fake takes the same keyword, records it, and returns only those ids' entries when it is given. Depends on T003.
  - Proof: a new test in `tests/unit/test_irp_gateway.py` stubs `execute_query_from_file(file_path, params=None, database=None)`, as the test at line 421 does. It asserts every script receives `portfolio_ids=None` for the full sync and `"1\x1f2"` for `["1", "2"]`.
- [X] T005 [T-03] Move the per-portfolio loop out of `_backfill_edm_detail_body` in `app/workers/entity_jobs.py` into `_store_portfolio_details(*, edm_id, edm_irp_id, portfolios, summary_map, now) -> list[str]`, which returns the `irp_id`s whose `/metrics` read failed. The loop covers the `/metrics` read, the summary match by id with the fallback by name, and `upsert_portfolio_detail`. `_backfill_edm_detail_body` keeps the prune, the treaties, `irp_edm.as_of`, the exposureId resolution by name, and its output keys.
  - Proof: `tests/unit/test_backfill_edm_detail.py` passes unchanged.
- [X] T006 [FR-002] [FR-004] [FR-005] [FR-006] [FR-011] [T-02] [T-04] [T-08] Add `_refresh_portfolios_body(rwb_job_id)` and the `@rwb_actor(max_retries=0) refresh_portfolios` actor to `app/workers/entity_jobs.py`, and register both in `_BODIES` and `__all__`. The body works through these steps in order:
  1. Load `{edm_id, portfolio_irp_ids}` with `rwb_job_service.load_input_data`.
  2. Return `JobResult.ok(skipped=...)` when `edm_service.get_edm` returns None or `edm.irp_id` is None. Never resolve the exposureId by name.
  3. Call `irp_gateway.list_portfolios` once. Fail the job if it raises.
  4. Keep the hits whose `irp_id` is in `portfolio_irp_ids`. Record the rest as `missing`.
  5. If any covered portfolio was found, call `get_edm_exposure_summary(..., portfolio_irp_ids=<found ids>)`. On any exception, log it and leave the summary `None`, as the full sync does.
  6. Call `_store_portfolio_details`.
  7. Fail when nothing was stored and at least one read failed. Otherwise succeed with the `output_data` keys in `contracts/jobs.md`: `portfolios`, `covered`, `missing`, `exposure_failures` (a list of ids), and `summary`.

  The body never prunes, never reads treaties, and never writes `irp_edm`. Change the docstring of `upsert_portfolio_detail` in `app/services/portfolio_service.py` from "Worker-side (``backfill_edm_detail``)" to "Worker-side".
- [X] T007 [FR-004] [FR-005] [FR-006] [FR-011] [T-08] Add `tests/unit/test_refresh_portfolios.py`, using the `fake_irp` and `drive` fixtures the way `tests/unit/test_backfill_edm_detail.py` does. Cases:
  - Only the covered portfolios' `exposure_detail` and `as_of` change, and an uncovered portfolio's row is byte-identical.
  - `irp_edm.as_of`, `irp_treaty` rows and an uncovered portfolio that `list_portfolios` no longer returns are untouched.
  - A covered id `list_portfolios` omits lands in `missing`, and its row stays.
  - One failed `/metrics` read lands in `exposure_failures`, and the others store.
  - All reads failing fails the job.
  - A DataBridge failure stores `exposure_detail.summary = null` for each covered portfolio, sets `output_data.summary` to `"unavailable"`, and the job succeeds.
  - The fake records the summary call with the covered ids.
  - A missing EDM or a null `irp_id` is skipped.

**Checkpoint**: `uv run pytest tests/unit` passes. Nothing enqueues `refresh_portfolios` yet.

---

## Phase 2: User Story 1 — A breakout refreshes only its generated portfolios (P1) 🎯 MVP

**Goal**: a finished breakout refreshes its generated portfolios only, and another portfolio of the same EDM stays available to break out.

**Independent test**: [quickstart.md](quickstart.md) §1.

- [X] T008 [US1] [FR-001] [P-01] [T-02] In `_complete_breakout` (`app/workers/portfolio_jobs.py:210`), enqueue and dispatch `refresh_portfolios` in place of `backfill_edm_detail`. Its `input_data` is `{"edm_id": str(edm_id), "portfolio_irp_ids": [str(o.irp_id) for o in outcomes if o.outcome != "failed" and o.irp_id is not None]}`. The requestor stays the breakout job, and the link and context stay `edm`. Keep the `backfill_enqueued` output key. Update the docstrings of `_complete_breakout`, `_run_breakout_body` and `_run_breakout_group_body`, which name `backfill_edm_detail`.
- [X] T009 [US1] [FR-007] [FR-008] [P-04] [P-05] [T-07] In `backfill_edm_detail_rows` (`app/services/rwb_job_service.py:553`), delete the `rwb_job bj`, `breakout_group bg` and `irp_portfolio p` joins and the `p.edm_id IN (...)` branch. `edm_id` becomes `COALESCE(ij.irp_edm_id, rj.requestor_id)`. Rewrite its docstring to name the two remaining keys: the `import_edm` irp_job and `(analyst_request, edm_id)`. Change "three enqueue keys" to "two" in the docstrings of `breakout_service._backfill_in_flight` (line 290) and `edm_service.latest_backfill_status` (line 458).
  - Proof: `test_sync_enqueues_analyst_head_and_dispatches` and `test_sync_skips_while_backfill_in_flight_either_key` in `tests/unit/test_edm_sync.py` pass, so the import key and the analyst Sync key still mark the EDM as syncing.
- [X] T010 [US1] [FR-010] In `_follow_up_pending` (`app/services/breakout_service.py:796`), read `rwb_job_type = 'refresh_portfolios'`. Update the docstrings of `_follow_up_pending` and `BreakoutBanner` and the comment at the top of `app/templates/partials/edm_portfolios_section.html`, which name `backfill_edm_detail`.
- [X] T011 [US1] [FR-001] [FR-008] [FR-010] Update the unit tests that expect the breakout follow-up to be `backfill_edm_detail`:
  - `tests/unit/test_run_breakout_worker.py`: in the happy path (line 74) and the idempotent-enqueue test (line 454), assert the follow-up is `refresh_portfolios` and that its `portfolio_irp_ids` equal the created, adopted and skipped-existing outcomes' ids. In `test_zero_success_fails_the_job` (line 394), assert no `refresh_portfolios` row exists.
  - `tests/unit/test_breakout_page_state.py:232`: drive the banner with a `refresh_portfolios` row.
  - `tests/unit/test_edm_sync.py:192`: replace `test_backfill_status_sees_breakout_fired_heads_quick_and_group` with a test asserting that a pending `refresh_portfolios` keyed on a breakout job leaves `sync_running` false and lets `sync_detail` enqueue.
  - `tests/unit/test_breakout_gate.py`: add a test that a pending breakout `refresh_portfolios` leaves another portfolio of the EDM eligible.
  - Fix whichever of `tests/unit/breakout_rows.py`, `test_breakout_groups.py`, `test_portfolio_lineage.py` and `test_edm_adopt.py` the unit tier shows still expect `backfill_edm_detail` from a breakout.
  - Proof: `uv run pytest tests/unit` passes.

**Checkpoint**: STOP. The approver runs [quickstart.md](quickstart.md) §1 before story 2 begins.

---

## Phase 3: User Story 2 — A hazard lookup refreshes only its portfolio (P1)

**Goal**: a finished or failed hazard lookup refreshes its portfolio, the gate refuses only that portfolio while the refresh runs, and a cancelled lookup refreshes nothing.

**Independent test**: [quickstart.md](quickstart.md) §2.

- [ ] T012 [US2] [FR-002] [FR-003] [P-02] [T-02] [T-06] In `_handle_geohaz_terminal` (`app/poller/run.py:192`):
  - Delete the `update_exposure_metrics` call, `_resolve_geohaz_metadata`, and its `"geohaz"` entry in `_TERMINAL_RESOLVERS`. `refresh_portfolios` rewrites the metrics. Delete `update_exposure_metrics` from `app/services/portfolio_service.py` and its `__all__`; the poller is its only caller.
  - Return before the enqueue when `status == "CANCELLED"`.
  - Otherwise read `irp_id` from `irp_portfolio` for `job["irp_portfolio_id"]` on the handler's `conn`. Skip the enqueue when the portfolio id or its `irp_id` is null.
  - Enqueue `refresh_portfolios` with requestor `irp_job`/`job["id"]`, link `edm`, `context_type="portfolio"`, `context_id=job["irp_portfolio_id"]`, and `input_data={"edm_id": ..., "portfolio_irp_ids": [str(irp_id)]}`.
  - Rewrite the comment above the enqueue so it names the scoped refresh and the cancelled exception.
- [ ] T013 [US2] [FR-009] [P-03] [T-06] In `app/services/breakout_service.py`:
  - Add `PORTFOLIO_REFRESHING_REASON = "this portfolio is refreshing after its hazard lookup"` next to `REFRESH_IN_FLIGHT_REASON` (line 89), and add it to `__all__`.
  - In `evaluate_gate` (line 305), after the EDM-wide check and only while `reason` is None, set `reason` to `PORTFOLIO_REFRESHING_REASON` when a `rwb_job` row has `rwb_job_type = 'refresh_portfolios'`, `context_type = 'portfolio'`, `context_id = :portfolio` and `status_code IN ('pending', 'running')`.
  - `refresh_in_flight` stays False for this case. The preview and both confirm paths (lines 957 and 1268) call `evaluate_gate`, so they refuse too.
- [ ] T014 [US2] [FR-002] [FR-003] [FR-008] [FR-009] Update the unit tests:
  - `tests/unit/test_poller.py`: replace `test_failed_geohaz_still_enqueues_the_detail_backfill` (line 79) with a test parametrized over `FINISHED` and `FAILED`. Each enqueues one `refresh_portfolios` with `context_type='portfolio'` and the portfolio's `irp_id`. Add a test that `CANCELLED` enqueues no `rwb_job`. In `test_geohaz_uses_single_status_getter_and_metadata_refresh` (line 23), drop the resolver assertion. Rename `test_geohaz_terminal_stores_summary_and_refreshes_metadata` (line 29) to `test_geohaz_terminal_stores_summary` and replace its metrics and `backfill_edm_detail` assertions with: `exposure_detail` is unchanged by the poller, and one `refresh_portfolios` is enqueued.
  - `tests/unit/test_breakout_gate.py`: a pending `refresh_portfolios` with portfolio context refuses that portfolio with `PORTFOLIO_REFRESHING_REASON` and `refresh_in_flight` False. Another portfolio of the EDM stays eligible. A terminal one does not refuse.
  - Proof: `uv run pytest tests/unit` passes.

**Checkpoint**: STOP. The approver runs [quickstart.md](quickstart.md) §2 before story 3 begins.

---

## Phase 4: User Story 3 — The Sync on an EDM page syncs only that EDM (P2)

**Goal**: the Sync on an EDM page inside a submission starts no RDM sync.

**Independent test**: [quickstart.md](quickstart.md) §3.

- [ ] T015 [US3] [FR-012] [P-07] [T-09] In `sync_contextual_detail` (`app/services/edm_service.py:666`), delete the RDM loop and its local `rdm_service` import. Keep the context check that returns False. Change the docstring to "Queue the stored EDM refresh for a valid context." In `tests/unit/test_edm_service.py` (around line 214), assert `calls == [("edm", "edm-1")]`, and drop the `rdm_service.sync_detail` monkeypatch.
  - Proof: `uv run pytest tests/unit` passes.

**Checkpoint**: STOP. The approver runs [quickstart.md](quickstart.md) §3.

---

## Phase 5: Documentation and verification

- [ ] T016 [P] [T-01] In `docs/DATA_MODEL.md`, add a `refresh_portfolios` row after `backfill_edm_detail` in the `rwb_job_type` table (line 589): "Read and store the exposure detail of named portfolios in one EDM, after a breakout or hazard lookup (spec 207)". Add the code to the `rwb_job_type_kind` list (line 812) with "added by spec 207".
- [ ] T017 [P] [FR-001] [P-04] In `specs/005-subportfolio-breakouts/spec.md`, append one line to FR-013 (line 130) and one to P-16 (line 164), each pointing to spec 207. The line on FR-013 says the follow-up refreshes the generated portfolios only. The line on P-16 says the follow-up no longer holds the gate.
- [ ] T018 [P] [T-01] In `tests/sqlserver/test_detail_tables_migration.py`, add `test_refresh_portfolios_seed_present` beside `test_backfill_edm_detail_seed_present` (line 120): label `Refresh portfolios`, sort_order 26. `test_migration_downgrade.py` already covers the downgrade. The test stays unverified until someone runs `make test-sql`.
- [ ] T019 [T-05] Someone with DataBridge access runs [quickstart.md](quickstart.md) §4 against the largest EDM available. They record `t_full`, `t_part`, and the EDM's portfolio and account counts under Risk in [research.md](research.md#r4), then move T-05 to Approved in `plan.md`.
- [ ] T020 Review the diff for subtraction per `AGENTS.md`, then run `uv run pytest tests/unit`. Report the unit tier count, and report the SQL Server tier as not run unless the developer ran `make test-sql`.

---

## Dependencies

- T001–T007 come before story 1 and story 2.
  - T004 needs T003.
  - T006 needs T004 and T005.
  - T007 needs T006.
- Story 1 (T008–T011) and story 2 (T012–T014) touch different enqueuers but share `app/services/breakout_service.py` (T010, T013). Run them in story order.
- Story 3 (T015) depends on nothing above and can ship first if needed.
- T016–T018 can run any time after T001. T019 needs T003 and T004 on a stack with DataBridge access. T020 comes last.

## Parallel opportunities

- T002 and T003 run alongside T001.
- T016, T017 and T018 touch separate files and run together.

## Implementation strategy

Phase 1, then story 1, is the MVP: it closes the case issue #207 reports for breakouts. Story 2 fixes the hazard lookup that blocked every portfolio in the EDM. Story 3 is a one-function deletion. Stop at each checkpoint for the approver to click the running feature (`docs/UI_WORKFLOW.md`).
