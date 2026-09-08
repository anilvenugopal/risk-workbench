# Tasks: rwb_job link and context fields

**Input**: `spec.md` in this directory; full design in `docs/CR/CR_04c__RWB_JOB_LINK_AND_CONTEXT.md` §9 (T1-T6 below map directly to that section).

**Tests**: Included — `tests/unit/test_rwb_job_queue.py` gains coverage for the new required/nullable fields (T6).

**Organization**: One phase, no user stories (schema/plumbing change, no new user-facing behavior in Phase 1).

## Path Conventions

Existing FastAPI app. `alembic/versions/0001_initial.py`, `app/services/rwb_job_service.py`, 18 call sites across `app/services/`, `app/workers/`, `app/poller/`, `app/routers/`, plus a new `infra/scripts/patches/` script.

---

## Phase 1: Schema and call sites

- [X] T001 [FR-001] [FR-002] [FR-003] Add `link_type`/`link_id`/`context_type`/`context_id` columns, `rwb_job_link_type_kind`/`rwb_job_context_type_kind` tables, their seed rows, and both FK constraints to `alembic/versions/0001_initial.py`. Casing: lowercase kind codes throughout (`edm`, `rdm`, `not_applicable`, `irp_analysis`, `portfolio`, `breakout_group`, `execution`), matching this repo's existing kind-code convention.
  - Proof: syntax valid; SQLite mirror (`tests/iteration1_mirror.py`, `tests/conftest.py`) updated to match; full unit tier passes against it.
- [X] T002 [FR-004] Added `link_type`, `link_id`, `context_type`, `context_id` as required keyword-only parameters to `enqueue_rwb_job` and `ensure_pending_rwb_job` in `app/services/rwb_job_service.py`; threaded through `_INSERT_IF_ABSENT`/`_insert_head`; added the same four columns to `ensure_pending_rwb_job`'s revival `UPDATE` (FR-005, replace not merge); `resubmit_rwb_job` reads and passes all four through unchanged (FR-006); `get_rwb_job`/`list_rwb_jobs_for_monitoring`'s `SELECT` lists include the four columns.
- [X] T003 [FR-004] Updated all 18 enqueue call sites plus `dummy_submit.py` with the correct `link_type`/`link_id`/`context_type`/`context_id` values, per `docs/CR/CR_04c__RWB_JOB_LINK_AND_CONTEXT.md` §6. O-04 (call site #11, `_finalize_analysis_body`) resolved: it now queries `irp_analysis.edm_id`/`rdm_id` for a real link (no `edm_id`/`rdm_id` was available in its original scope), and raises loudly if a row somehow has neither, rather than silently marking it `not_applicable`.
- [X] T004 [FR-007] Wrote `infra/scripts/patches/2026_08_rwb_job_link_context_{1..6}_*.sql` — 6 separate files (split from one combined script after SQL Server rejected it — batch compile order issue), run in order: columns, kind tables, seed kinds, backfill, NOT NULL, FK constraints. Every file safe to re-run. Run against `infra-sqlserver-1` and verified — `make wsl-test-sql` passes in full.
- [X] T005 [FR-006] Added to `tests/unit/test_rwb_job_queue.py`: `test_enqueue_requires_link_type`, `test_enqueue_stores_link_and_context_fields`, `test_enqueue_allows_null_context_when_job_has_none`, `test_ensure_pending_restamps_link_and_context_on_retry`, `test_resubmit_rwb_job_carries_link_and_context_through_unchanged`. Every existing call in that file (and in every other test file across the repo calling `enqueue_rwb_job`/`ensure_pending_rwb_job`, or writing raw `INSERT INTO rwb_job` SQL — 20 files in total, found by grepping beyond just direct function calls) updated with correct or honest-filler values.
  - Proof: `uv run pytest tests/unit` — 1449 passed. `make wsl-test-sql` — full pass (developer-run).
- [X] T006 Backfill logic (T004's step 4) audited block-by-block against the actual current production code at every call site — not against this document's own earlier draft. All verified correct, including exact JSON key/casing checks (`$.analysis_id`, `$.edm_id`, `$.execution_id`). Documented as a searchable reference table in `docs/CR/CR_04c__RWB_JOB_LINK_AND_CONTEXT.md` §10. One flagged, unresolved-but-inert item: three seeded `rwb_job_type_kind` codes (`download_export_file`, `push_results_to_loss_repo`, `notify_analyst`) have no producing code and no backfill block — harmless on an empty table, and step 5 of the patch throws loudly rather than silently accepting a NULL row if this is ever wrong on a real database.

**Checkpoint**: All 18 call sites write correct values; unit tier green (1449 passed); SQL Server tier green (`make wsl-test-sql`, developer-run); patch script written, audited, and run against `infra-sqlserver-1`. Phase 1 complete. Ready for review before Phase 2 (dedup constraint, read-query rewiring) is scoped as a separate spec.
