# Implementation Plan: Scoped Portfolio Refresh

**Branch**: `207-scoped-portfolio-refresh` | **Date**: 2026-10-07 | **Spec**: [spec.md](spec.md) | **Issue**: #207 (supersedes #180)

## Plan status

**Ready for tasks:** Yes
**Blocked by:** Nothing

## Design summary

- A new `rwb_job_type` `refresh_portfolios` runs on its own Dramatiq queue. It
  takes `input_data = {edm_id, portfolio_irp_ids}` ([contracts/jobs.md](contracts/jobs.md)) (T-01, T-02).
- `_complete_breakout` (`app/workers/portfolio_jobs.py`) enqueues
  `refresh_portfolios` in place of `backfill_edm_detail`. The enqueue keeps the
  breakout job as its requestor and `context_type='edm'`. `portfolio_irp_ids`
  holds the `irp_id` of every created, adopted or skipped-existing outcome (P-01).
- `_handle_geohaz_terminal` (`app/poller/run.py`) returns without enqueuing
  when the lookup ends `CANCELLED`. On `FINISHED` or `FAILED` it reads the
  portfolio's `irp_id` and enqueues `refresh_portfolios` with
  `context_type='portfolio'` and `context_id` set to the looked-up portfolio
  (P-02, T-06).
- The `refresh_portfolios` worker (`app/workers/entity_jobs.py`) makes three
  reads. It calls `list_portfolios` once and keeps the covered portfolios with
  their `stampDate`. It runs the DataBridge summary scripts once, filtered to
  those ids. It reads `/metrics` once per covered portfolio (T-04, T-05).
- The worker then upserts each portfolio's `exposure_detail` with
  `upsert_portfolio_detail`, the same call the full sync uses, through one
  per-portfolio loop extracted from `_backfill_edm_detail_body` (T-03). It never
  prunes, never reads treaties, and never writes `irp_edm.as_of`.
- A covered id that `list_portfolios` no longer returns is skipped, and its row
  stays. A failed `/metrics` read keeps the prior snapshot. The job fails only
  when it stored nothing and at least one read failed (T-08).
- The 11 `sql/databridge/portfolio_*.sql` scripts the summary runs gain the
  clause `{{ portfolio_ids }} IS NULL OR PORTINFOID IN (STRING_SPLIT(...))` at
  their `portacct` read, which seeks `UIX_PORTACCT`.
  `get_edm_exposure_summary` passes NULL for the full sync, whose output is
  unchanged (T-05).
- `rwb_job_service.backfill_edm_detail_rows` drops its breakout joins. Only the
  import key and the analyst Sync key remain, so a scoped refresh never shows
  the EDM as syncing, never holds back Sync, and never sets the EDM-wide P-16
  refusal (P-04, P-05, T-07).
- `breakout_service.evaluate_gate` refuses a portfolio that has a pending or
  running `refresh_portfolios` with `context_type='portfolio'` and
  `context_id` equal to that portfolio. The refusal carries a "this portfolio
  is refreshing" reason (P-03, T-06).
- `_follow_up_pending` reads `refresh_portfolios`, so the "figures are filling
  in" banner tracks the scoped refresh (FR-010).
- `edm_service.sync_contextual_detail` drops its RDM loop. The Sync on an EDM
  page inside a submission calls `sync_detail` for that EDM only (P-07, T-09).

## Material changes

| Area | Change |
|---|---|
| Database | Alembic revision `0007` adds a `rwb_job_type_kind` row `refresh_portfolios`. No table or column changes. |
| Worker | `entity_jobs.py`: new actor and body `refresh_portfolios`, plus a per-portfolio store loop shared with `backfill_edm_detail`. `portfolio_jobs.py`: the follow-up job type and its input. |
| Poller | `run.py`: `_handle_geohaz_terminal` skips `CANCELLED` and enqueues `refresh_portfolios` for one portfolio. |
| Services | `irp_gateway.get_edm_exposure_summary` takes `portfolio_irp_ids`. `rwb_job_service.backfill_edm_detail_rows` narrows its predicate. `breakout_service` changes the gate and the banner. `edm_service.sync_contextual_detail` loses its RDM loop. |
| SQL | 11 `sql/databridge/portfolio_*.sql` scripts gain the `{{ portfolio_ids }}` filter. |
| UI | None. The gate's existing "not available right now: {reason}" note shows the new reason. |
| Library | None. |

## High-risk technical decisions

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | Scoped refresh is its own job type, `refresh_portfolios`, not a `portfolio_ids` input on `backfill_edm_detail` | Proposed | Every EDM-sync read keys on the job type. A shared type makes each reader parse `input_data`. [R1](research.md#r1) |
| T-02 | `input_data` carries Risk Modeler portfolio ids | Proposed | Breakout outcomes already hold them. `list_portfolios` and DataBridge both match on them. [R2](research.md#r2) |
| T-03 | One per-portfolio store loop shared by both bodies | Proposed | Prune, treaties, `irp_edm.as_of` and the exposureId-by-name resolution stay in `_backfill_edm_detail_body` only. |
| T-04 | Stamps come from one `list_portfolios` call filtered client-side | Proposed | `fetch_portfolio_stamp` enumerates the same search, so a per-portfolio stamp read costs more. [R3](research.md#r3) |
| T-05 | The summary scripts filter on `{{ portfolio_ids }}`, CHAR(31)-joined, NULL for the full sync | Proposed | The wheel inlines NULL as a literal, so the full sync's plan is unchanged. The EDM indexes every join column from `portacct` onward except `hdsteppolicy.ACCGRPID`, which the currencies script reads in full as it does today. [R4](research.md#r4) |
| T-06 | The hazard refresh keys `context_type='portfolio'`. The breakout follow-up keys `'edm'`. The gate refuses through `reason`, not `refresh_in_flight` | Proposed | `refresh_in_flight` keeps meaning "the EDM is syncing", so the modal note stays correct. [R5](research.md#r5) |
| T-07 | `backfill_edm_detail_rows` deletes its `rwb_job`, `breakout_group` and `irp_portfolio` joins | Proposed | After this change, `backfill_edm_detail` has no breakout requestor left. The `irp_job` join matches only `import_edm`, the one remaining `irp_job` requestor. [R5](research.md#r5) |
| T-08 | A scoped refresh fails only when it stored nothing and a read failed | Proposed | This is the full sync's rule. A missing portfolio is a skip, not a failure (FR-006). |
| T-09 | Story 3 is a deletion in `sync_contextual_detail`. The route and template are unchanged | Proposed | No requirement asks for the RDM loop. |

---

## Technical Context

**New dependencies**: None
**Databases touched**: `rwb_workbench` (one kind row; `irp_portfolio.exposure_detail` writes as today). DATABRIDGE read-only through `irp_gateway`, worker-side.

## Constitution Check

*GATE: before Phase 0 research, re-checked after Phase 1 design.*

Reviewed against all 13 articles in `.specify/memory/constitution.md`: no violations. The post-design re-check found none either.

Material interactions:

- **Article 2 (Sequencing Is Derived)**: the hazard-portfolio refusal is computed per request from `rwb_job` rows. Nothing stores it.
- **Article 3 (Kind Tables)**: `refresh_portfolios` is a `rwb_job_type_kind` row. A type discriminator is never a plain string.
- **Article 5 (Mechanical Follow-up Auto-fires)**: both follow-ups stay auto-fired. Only their scope changes.
- **Article 10 (SQL Table Is the Queue)**: the new type gets its own queue through `rwb_actor`. `infra/scripts/start-all.sh` lists queues from the registered actors, so it needs no edit.
- **Article 11 (IRP Behind the Gateway)**: the DataBridge filter goes through `get_edm_exposure_summary` and repo-owned SQL files, worker-side.

## Project Structure

```text
alembic/versions/0007_refresh_portfolios_job_type.py   # new kind row
app/workers/entity_jobs.py           # refresh_portfolios actor; shared store loop
app/workers/portfolio_jobs.py        # _complete_breakout follow-up
app/poller/run.py                    # _handle_geohaz_terminal
app/services/irp_gateway.py          # get_edm_exposure_summary(portfolio_irp_ids=)
app/services/rwb_job_service.py      # backfill_edm_detail_rows
app/services/breakout_service.py     # evaluate_gate, _follow_up_pending
app/services/edm_service.py          # sync_contextual_detail
sql/databridge/portfolio_*.sql       # 11 scripts
tests/iteration1_mirror.py           # RWB_JOB_TYPE_SEED row
infra/scripts/seed_db.py             # rwb_job_type_kind MERGE row
docs/DATA_MODEL.md                   # rwb_job types table
specs/005-subportfolio-breakouts/spec.md   # one-line pointers on FR-013 and P-16
specs/007-geohaz-execution/spec.md         # one-line pointer on the hazard follow-up
```

## Testing

- **Unit**:
  - The `refresh_portfolios` body with a fake gateway. It covers the target portfolios only. It writes no prune, no treaties and no `irp_edm.as_of`. It skips a missing id. It isolates a failed read. It records its output keys.
  - The poller: `CANCELLED` enqueues nothing; `FINISHED` and `FAILED` enqueue one portfolio.
  - The breakout completion: the input lists every non-failed outcome's `irp_id`.
  - The gate: it refuses the hazard portfolio only, and the EDM's sync state ignores scoped jobs.
  - The banner reads the new type.
  - `sync_contextual_detail` queues no `backfill_rdm_analyses`.
- **SQL Server integration**: revision `0007` applies and its downgrade removes the row; existing `backfill_edm_detail_rows` tests rerun against the narrowed predicate.
- **IRP sandbox**: `get_edm_exposure_summary` with `portfolio_irp_ids` returns, for those portfolios, the same entries the unfiltered call returns ([quickstart.md](quickstart.md) §4).
