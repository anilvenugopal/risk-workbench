# Contract: `refresh_portfolios` job and its enqueuers

## Job

`rwb_job_type = 'refresh_portfolios'`, Dramatiq queue `refresh_portfolios`
(`rwb_actor`, `max_retries=0`), body in `app/workers/entity_jobs.py`.

`input_data`:

```json
{"edm_id": "<irp_edm.id>", "portfolio_irp_ids": ["<irp_portfolio.irp_id>", "..."]}
```

`output_data` on completion:

| Key | When | Value |
|---|---|---|
| `portfolios` | always | count of portfolios whose `exposure_detail` was written |
| `covered` | always | `portfolio_irp_ids` as received (FR-011) |
| `missing` | some covered id absent from `list_portfolios` | those ids; their rows are untouched (FR-006) |
| `exposure_failures` | some `/metrics` read failed | those ids; their prior snapshots stay (FR-006, FR-011) |
| `summary` | at least one covered portfolio found | `"ok"` or `"unavailable"` (DataBridge failure writes `summary: null`, as the full sync does) |
| `skipped` | EDM missing or without `irp_id` | reason string; job succeeds |

The job fails when `portfolios == 0` and `exposure_failures` is non-empty, or
when `list_portfolios` raises. Every other outcome succeeds.

The job writes `irp_portfolio.exposure_detail` and `irp_portfolio.as_of` for
covered portfolios only. It never writes `irp_edm`, `irp_treaty`, or
`irp_portfolio.deleted_at` (FR-005).

## Enqueuers

| Enqueuer | Call | `requestor` | `link` | `context` |
|---|---|---|---|---|
| Breakout completion (`_complete_breakout`) | `ensure_pending_rwb_job`, then `dispatch` | `rwb_job`, the breakout job id | `edm`, edm id | `edm`, edm id |
| Hazard lookup terminal (`_handle_geohaz_terminal`), `FINISHED` or `FAILED` only | `enqueue_rwb_job` on the poller's connection | `irp_job`, the geohaz job id | `edm`, edm id | `portfolio`, the looked-up `irp_portfolio.id` |

The breakout enqueue is skipped when no outcome succeeded. On a resubmitted
breakout, `ensure_pending_rwb_job` replaces `input_data`, so the refresh covers
that run's outcomes.

## Readers

| Reader | Predicate |
|---|---|
| `breakout_service.evaluate_gate` (P-03) | `rwb_job_type = 'refresh_portfolios' AND context_type = 'portfolio' AND context_id = :portfolio AND status_code IN ('pending','running')` → `reason` = the portfolio-refreshing reason |
| `breakout_service._follow_up_pending` (FR-010) | `requestor_type = 'rwb_job' AND requestor_id = :breakout_job AND rwb_job_type = 'refresh_portfolios'` |
| `rwb_job_service.backfill_edm_detail_rows` | unchanged type `backfill_edm_detail`; keys reduced to the `import_edm` irp_job and `(analyst_request, edm_id)` |

## Gateway

`irp_gateway.get_edm_exposure_summary(*, edm_name, edm_irp_id, portfolio_irp_ids: Sequence[str] | None = None)`.
None means every portfolio. A list passes `CHAR(31).join(ids)` as the
`portfolio_ids` script parameter. The return shape is unchanged. The fake in
`tests/unit/fakes/fake_irp.py` takes the same keyword.
