# Tasks: rwb_job link and context fields

**Input**: `spec.md` in this directory; full design in `docs/CR/CR_04c__RWB_JOB_LINK_AND_CONTEXT.md` §9 (T1-T6 below map directly to that section).

**Tests**: Included — `tests/unit/test_rwb_job_queue.py` gains coverage for the new required/nullable fields (T6).

**Organization**: One phase, no user stories (schema/plumbing change, no new user-facing behavior in Phase 1).

## Path Conventions

Existing FastAPI app. `alembic/versions/0001_initial.py`, `app/services/rwb_job_service.py`, 18 call sites across `app/services/`, `app/workers/`, `app/poller/`, `app/routers/`, plus a new `infra/scripts/patches/` script.

---

## Phase 1: Schema and call sites

- [X] T001 [FR-001] [FR-002] [FR-003] Add `link_type`/`link_id`/`context_type`/`context_id` columns, `rwb_job_link_type_kind`/`rwb_job_context_type_kind` tables, their seed rows, and both FK constraints to `alembic/versions/0001_initial.py`. Casing: lowercase kind codes throughout (`edm`, `rdm`, `not_applicable`, `irp_analysis`, `portfolio`, `breakout_group`, `execution`), matching this repo's existing kind-code convention.
  - Proof: `python3 -c "import ast; ast.parse(open('alembic/versions/0001_initial.py').read())"` — syntax valid. Not yet run against a live database (fresh-install path only; existing environments need T005's patch script).
- [ ] T002 [FR-004] Add `link_type`, `link_id`, `context_type`, `context_id` as required keyword-only parameters to `enqueue_rwb_job` and `ensure_pending_rwb_job` in `app/services/rwb_job_service.py`; thread through `_INSERT_IF_ABSENT`/`_insert_head`; add the same four columns to `ensure_pending_rwb_job`'s revival `UPDATE` (FR-005, replace not merge); update `resubmit_rwb_job` to read and pass all four through unchanged (FR-006); add the four columns to `get_rwb_job`/`list_rwb_jobs_for_monitoring`'s `SELECT` lists.
- [ ] T003 [FR-004] Update all 18 enqueue call sites with the correct `link_type`/`link_id`/`context_type`/`context_id` values, per `docs/CR/CR_04c__RWB_JOB_LINK_AND_CONTEXT.md` §6 and §9 T3. Start with `app/workers/dummy_submit.py` (verify manually against a live dev DB if available before continuing). Resolve O-04 (call site #11, `_finalize_analysis_body`) as part of this task — either a real lookup or a documented `not_applicable`, not a silent gap.
- [ ] T004 [FR-007] Write `infra/scripts/patches/2026_08_rwb_job_link_context.sql` — guarded column/kind-table adds, backfill (per §9 T4's join logic), FK adds, in that order. Every statement safe to re-run. Do not run it against any real environment — that is the environment owner's call.
- [ ] T005 [FR-006] Add to `tests/unit/test_rwb_job_queue.py`: `test_enqueue_requires_link_type`, `test_enqueue_stores_link_and_context_fields`, `test_enqueue_allows_null_context_when_job_has_none`, `test_ensure_pending_restamps_link_and_context_on_retry`, `test_resubmit_rwb_job_carries_link_and_context_through_unchanged`. Update every existing call in that file to pass the four new fields (`not_applicable`/`None`/`None`/`None` as filler where irrelevant to the test).
  - Proof: `uv run pytest tests/unit` — full pass.

**Checkpoint**: All 18 call sites write correct values; unit tier green; patch script exists (not run). Ready for review before Phase 2 (dedup constraint, read-query rewiring) is scoped as a separate spec.
