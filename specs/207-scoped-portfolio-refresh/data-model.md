# Data Model: Scoped Portfolio Refresh

No table or column changes.

## `rwb_job_type_kind` row

Alembic revision `0007` inserts, and its downgrade deletes:

| code | label | sort_order |
|---|---|---|
| `refresh_portfolios` | Refresh portfolios | 26 |

The same row goes into `RWB_JOB_TYPE_SEED` in `tests/iteration1_mirror.py` and
into the `rwb_job_type_kind` MERGE in `infra/scripts/seed_db.py`.

## `irp_portfolio.exposure_detail`

The shape is unchanged (`{metrics, summary, stamp_date}`, docs/DATA_MODEL.md).
A scoped refresh writes the same three keys from the same sources a full sync
uses, so FR-002a and FR-002b compare like with like.
