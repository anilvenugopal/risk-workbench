# Contract: the `irp-integration` methods this feature consumes

**Repo**: `../../IRP/irp-integration` — branch `feature/filtered-subportfolio-creation`, [PR #21](https://github.com/premiumiq/irp-integration/pull/21), in review, not yet on TestPyPI. Develop against `make irp-local`; re-pin with `make irp-testpypi` before implement completes and confirm with `make irp-status`.

*(Rewritten 2026-08-03. This file used to be a brief for library work that had not happened yet, specifying `add_filtered_accounts` as the add step, `allow_deep_filters` as a maybe, and pagination as an open spike item. The work is done and two of those three lost. Verified against `portfolio.py` and `utils.py` at `a04e3d7` — signatures re-confirmed against the active wheel before `create_sub_portfolio` is written, since the wheel is pre-release. Selection revised 2026-08-05: the paginated REST reads lost to DataBridge SQL after failing on a 248,000-account portfolio — W-20, research R1.)*

Composition is three calls (T-01): **select** the source portfolio's matching account ids → **create** the empty portfolio → **add** those accounts by id. Risk Modeler has no create-by-filter operation.

---

## 1. Selection read — DataBridge SQL

```python
databridge.execute_query_from_file(file_path: str, params: Dict = None,
                                   connection: str = None, database: str = None) -> List[pd.DataFrame]
```

One parameterized, portfolio-scoped script per dimension (`sql/databridge/breakout_lob_accounts.sql`; the state script arrives with T045), run worker-side against the EDM's physical database — the same executor and `databaseName` resolution the exposure summary uses. `{{ portfolio_id }}` is substituted with injection-safe escaping; RM's portfolioId **is** `portacct.PORTINFOID`, and the returned `ACCGRPID` is the id `manage_portfolio_accounts` accepts as `accountId` (W-20). The script reuses the summary script's joins, so the values it filters on are byte-identical to the stored summary the plan was approved from, and the account-level bucketing (W-3/W-11) holds by construction.

**Why not the REST reads.** The 2026-08-03 selection (`search_accounts_by_portfolio_paginated` + a chunked `accountId IN (…)` scan, shaped by the no-portfolio-predicate finding W-6 and the HTTP 431 filter ceiling) failed at the US1 checkpoint: the wheel's pagination refuses past 100,000 records because completeness can no longer be proven (W-14 behaving as designed), and the target portfolio holds 248,732 accounts (W-20). The same book answers the SQL form in ~1–2 seconds (W-19). Do **not** filter policies by `lobId` (HTTP 500, W-15) and do not use `admin1Name` (zero rows until GeoHaz runs, W-12) — both findings still bind any future REST reader.

**Completeness.** A single set-based query cannot return a partial page sequence — the W-14 rule (never proceed on a result that cannot be shown complete) is enforced by construction. Any DataBridge failure raises and the worker fails the job with nothing created.

## 2. Create

```python
create_portfolio(edm_name: str, portfolio_name: str,
                 portfolio_number: str = "", description: str = "") -> Tuple[int, Dict]
```

Synchronous HTTP 201; returns `(portfolio_id, request_body)`. Takes the **EDM name**, not the exposure id.

Raises `IRPValidationError`, before any POST, for four distinct cases:

| Case | Note |
|---|---|
| duplicate name in the EDM | looked up client-side first; this is the adopt-an-existing-portfolio signal (FR-011) |
| `portfolio_name` over **40** characters | boundary confirmed exactly: 40 creates, 41 rejects (W-2) |
| `portfolio_number` over **20** characters | |
| `portfolio_number` omitted while the name is over 20 characters | it defaults to the name, which then overruns the number's own cap (W-13) |

The fourth case is why this feature always passes `portfolio_number` explicitly: every composed name of interest is over 20 characters (`usfl_commercial - TX` is exactly 20, so the next character breaks it). Neither field is ever truncated by the library.

Because all four raise the same class, **do not use exception class alone to detect "the name is taken"** — resolve the existing portfolio first with `search_portfolios` (W-10).

## 3. Add accounts

```python
manage_portfolio_accounts(exposure_id: int, portfolio_id: int, *,
                          accounts_to_add: Optional[List[int]] = None,
                          accounts_to_remove: Optional[List[int]] = None) -> Dict
```

Synchronous HTTP 200 — no `202` and no workflow URL appeared on any call in the probe run, so this needs no poller and creates no `irp_job` row. Returns:

```python
{"addAccounts": {"completed": n, "total": m}, "removeAccounts": {...}}
```

**`completed` counts ids newly added, not ids that ended up as members.** Idempotent: re-adding the same ids returns `completed 0, total m` and leaves membership correct (W-9). The worker must not read `completed < total` as a failure — that is what a healthy re-run reports. Verify by reading the portfolio back and comparing against the persisted plan, which is what AGENTS.md rule 8 asks for anyway; a count that differs from the ids sent fails that sub-portfolio (FR-008). The read-back is a DataBridge count (`sql/databridge/portfolio_member_count.sql`) — the paginated REST enumeration cannot verify a portfolio past 100,000 accounts (W-20). Adds over 1,000 ids are chunked so no single PATCH carries an unbounded list.

## 4. Adoption read

```python
search_portfolios(exposure_id: int, filter: str = "", limit: int = 100, offset: int = 0) -> List[Dict]
search_portfolios_paginated(exposure_id: int, filter: str = "") -> List[Dict]
```

Filters on `portfolioName` **and `portfolioNumber`** — field names are case-insensitive; `number` is rejected (W-17). Adoption resolves on the number, because the number is stable across runs and the name is not (P-11/R4). Note that Risk Modeler portfolio ids repeat across EDMs, so a number is unique only within its exposure; the adoption search is exposure-scoped, which is enough.

This method is also the confirm-time `stampDate` read (FR-002a) — the flow's one web-layer RM call.

## What this feature does not use

- **`add_filtered_accounts`** (PUT `.../filtered-accounts`) — it exists in the library and works, but returns `{}`, so the worker cannot distinguish an empty populate from a full one without a read-back. `manage_portfolio_accounts` reports `completed`/`total` and is the add step. Also: `manage_existing_accounts=True` returns HTTP 200 and adds **nothing at all** — it is a mode switch, not the heal-a-partial-write option it reads as (W-1).
- **`allowDeepFilters`** — removed from the public signature; `allowDeepFilters=false` is hardcoded. It returned zero rows with HTTP 200 at all nine scope sizes tested where the truth was 272 (W-7). The path cannot be taken by accident.
- **`queryFilter` one-call populate** — closed permanently, not deferred (T-09): no filter names a source portfolio, so the one-call form cannot express "the TX accounts of portfolio 1".
- **GeoHaz** — nothing in this feature waits on geocoding, since selection uses `admin1Code`. If the workbench ever does await a GeoHaz job: its id is served by `/platform/geohaz/v1/jobs`, and polling it as an import job returns `404 Invalid job id` while the job is in fact running. The single-status check is `get_geohaz_job` (W-12).

## Gateway seam

Two gateway functions own the whole sequence, so no Moody's response shape, SQL, or database-name resolution leaks past the gateway into `breakout_service` or the worker. `irp_gateway.select_breakout_accounts` runs once per breakout and returns the account ids for every value from one DataBridge query. `irp_gateway.create_sub_portfolio` then runs per sub-portfolio: create, chunked add, DataBridge count read-back. Both take `edm_name` — the gateway resolves and caches the EDM's physical `databaseName` from RM's exposures search. Contract in [data-access.md](data-access.md). The CI fake mirrors both, including the duplicate-name raise and the `completed 0` re-run response.
