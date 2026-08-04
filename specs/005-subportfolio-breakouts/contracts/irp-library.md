# Contract: the `irp-integration` methods this feature consumes

**Repo**: `../../IRP/irp-integration` — branch `feature/filtered-subportfolio-creation`, [PR #21](https://github.com/premiumiq/irp-integration/pull/21), in review, not yet on TestPyPI. Develop against `make irp-local`; re-pin with `make irp-testpypi` before implement completes and confirm with `make irp-status`.

*(Rewritten 2026-08-03. This file used to be a brief for library work that had not happened yet, specifying `add_filtered_accounts` as the add step, `allow_deep_filters` as a maybe, and pagination as an open spike item. The work is done and two of those three lost. What follows is the contract as shipped, verified against `portfolio.py` and `utils.py` at `a04e3d7` — signatures re-confirmed against the active wheel before `create_sub_portfolio` is written, since the wheel is pre-release.)*

Composition is three calls (T-01): **select** the source portfolio's matching account ids → **create** the empty portfolio → **add** those accounts by id. Risk Modeler has no create-by-filter operation.

---

## 1. Selection reads

```python
search_accounts_by_portfolio_paginated(exposure_id: int, portfolio_id: int,
                                       filter: str = "", sort: str = "") -> List[Dict]
search_policies_paginated(exposure_id: int, filter: str = "", sort: str = "") -> List[Dict]
search_locations_paginated(exposure_id: int, filter: str = "", sort: str = "") -> List[Dict]
```

**Scope always comes from an account-id list.** No portfolio predicate exists — `portfolioId`, `portfolioName`, `portInfoId`, `portfolio`, and `portfolioNumber` are all rejected on the EDM-wide account search, with and without deep filters (W-6). So the worker reads the source portfolio's account ids once, then scopes every subsequent read with `accountId IN (…)`.

**LOB — one pass, grouped client-side.** LOB is not filterable on any Risk Data operation, but every policy carries it:

```python
accounts = pm.search_accounts_by_portfolio_paginated(exposure_id, portfolio_id)
ids = [a["accountId"] for a in accounts]
policies = pm.search_policies_paginated(exposure_id, filter=f'accountId IN ({",".join(map(str, chunk))})')
by_lob[policy["lob"]["lobName"]].add(policy["accountId"])
```

Do **not** filter by `lobId` — it returns HTTP 500, not a clean 400 (W-15).

**State — filtered server-side.**

```python
locations = pm.search_locations_paginated(
    exposure_id, filter=f'accountId IN ({ids}) AND admin1Code = "TX"')
account_id = row["location"]["property"]["accountId"]
```

`admin1Code` is the filter field. `admin1Name` is also filterable and honoured case-insensitively, but it returns **zero rows with HTTP 200** until the EDM is geocoded (W-12), so it is not used — see P-12.

**Two behaviours the caller owns:**

- **Chunking.** The filter travels in the URL and dies at HTTP 431 around 4,872 characters — a *character* ceiling, not an id count, so a book with 7-digit account ids fits roughly half as many per request. 431 is a header-size limit, so the bearer token shares the budget and the number is not a constant across tenants. The library records the ceiling in the docstring and does not chunk. Size chunks by composed filter length against a named constant well below the measured ceiling.
- **Response-shape parsing.** The account id is nested differently in each read, and reading the wrong key returns a plausible empty result rather than an error (W-15). Worth a unit test against a recorded body — every wrong-key mistake in the probe run was silent.

**Completeness is enforced, not warned about.** `paginate_search` raises `IRPAPIError` when it cannot show it read every page — a repeated page fingerprint, or the page ceiling with pages still coming back full (W-14). Catch it per sub-portfolio and fail that entry; never proceed on a possibly-short id list, because under-selection produces a sub-portfolio missing accounts and reports success.

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

**`completed` counts ids newly added, not ids that ended up as members.** Idempotent: re-adding the same ids returns `completed 0, total m` and leaves membership correct (W-9). The worker must not read `completed < total` as a failure — that is what a healthy re-run reports. Verify by reading the portfolio back and comparing against the persisted plan, which is what Article 8 asks for anyway.

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

Two gateway functions own the whole sequence, so no Moody's response shape, filter grammar, or chunk arithmetic leaks past the gateway into `breakout_service` or the worker. `irp_gateway.select_breakout_accounts` runs once per breakout and returns the account ids per value — one grouped policy pass for LOB, one location read per value for state — because the LOB read resolves every value at once and must not be repeated per sub-portfolio. `irp_gateway.create_sub_portfolio` then runs per sub-portfolio: create, add, read-back verification. Contract in [data-access.md](data-access.md). The CI fake mirrors both, including the duplicate-name raise and the `completed 0` re-run response.
