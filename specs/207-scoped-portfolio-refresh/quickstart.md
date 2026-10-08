# Quickstart: Verify Scoped Portfolio Refresh

Prerequisites: the stack is up (`make start` or `make wsl-start` + `make wsl-app`),
`make db-migrate` has applied revision `0008`, and the workers have restarted so
the `refresh_portfolios` queue exists. Use an imported EDM with several
portfolios and a hazard-lookup-capable account.

## 1. Breakout refreshes only its portfolios (story 1)

1. Note the EDM's "synced" time and one unrelated portfolio's figures.
2. Run a breakout on one portfolio.
3. While its follow-up runs, open the Jobs monitor. One `refresh_portfolios`
   job is listed, and its input lists the generated portfolios' Risk Modeler ids.
4. While it runs, open Break out on another portfolio of the same EDM. The
   breakout is available, the banner reads "figures are filling in", and the
   EDM does not show as syncing.
5. After it finishes, the EDM's "synced" time and the unrelated portfolio's
   figures are unchanged. The generated portfolios show figures.

## 2. Hazard lookup refreshes only its portfolio (story 2)

1. Run a hazard lookup on portfolio A.
2. When it finishes, while its `refresh_portfolios` job is pending or running,
   open Break out on A. The breakout is refused with "this portfolio is
   refreshing". Break out on portfolio B of the same EDM is available.
3. After the refresh finishes, break out A without clicking Sync. The confirm
   passes the freshness check.
4. Run a hazard lookup and cancel it in Risk Modeler. No `refresh_portfolios`
   job appears for it.

## 3. EDM Sync inside a submission syncs only the EDM (story 3)

On an EDM page inside a submission that lists RDMs, click Sync. The Jobs
monitor shows one `backfill_edm_detail` job and no `backfill_rdm_analyses` job.

## 4. DataBridge filter (T-05) — someone with DataBridge access

Inside `linux-box` (`make shell`), against the largest EDM available:

```python
from app.services import irp_gateway
import time
t = time.monotonic(); full = irp_gateway.get_edm_exposure_summary(edm_name=NAME, edm_irp_id=ID); t_full = time.monotonic() - t
ids = list(full)[:3]
t = time.monotonic(); part = irp_gateway.get_edm_exposure_summary(edm_name=NAME, edm_irp_id=ID, portfolio_irp_ids=ids); t_part = time.monotonic() - t
assert part == {k: full[k] for k in ids}
print(t_full, t_part)
```

Expected: `part` equals the three entries of `full`, and `t_part` is a small
fraction of `t_full`. Record both timings and the EDM's portfolio and account
counts in [research.md](research.md#r4).

## 5. A failed custom group's line clears (spec 005 FR-012)

1. Run a custom breakout whose filters match no account. The source
   portfolio's row shows "{group label} — {name} failed: no account matches
   every filter of this breakout — nothing was created".
2. Run another custom breakout on the same portfolio that succeeds. After it
   finishes, the failure line is gone.

## Tests

`uv run pytest tests/unit` runs from any host shell. `make test-sql` covers
revision `0008`.
