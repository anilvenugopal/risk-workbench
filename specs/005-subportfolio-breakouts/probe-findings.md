# Sub-portfolio breakout: findings and evidence

The source of truth for how Risk Modeler behaves when a portfolio is broken out,
and for what feature 005 must therefore do. It holds four things: the model of
how Risk Modeler composes a sub-portfolio, what the `irp-integration` library
provides, the findings from the live probe run, and the design-record quotes that
explain why the overlap this produces is accepted.

Replaces `irp-integration/BREAKOUT_FINDINGS.md`, deleted 2026-08-03 — that file
was never in git, so what is here is the only copy of its design-record material.

| | |
|---|---|
| Probe run | 2026-08-03 |
| Tenant | `prodmgmt` at `https://api-euw1.rms-ppe.com` |
| Library at probe time | `irp-integration` branch `feature/filtered-subportfolio-creation` at `6c5f5d0` |
| Library now | same branch at `a04e3d7` — [PR #21](https://github.com/premiumiq/irp-integration/pull/21), in review, not yet published to TestPyPI |
| Probe scripts | `../../../irp-integration/probes/` — `a*` read-only, `b*` write portfolios, `c*` build and break out the multi-LOB fixture. Gitignored, so this path is the local working tree only |
| Raw output | `../../../irp-integration/probes/results/*.json` |
| Run log | `../../../irp-integration/probes/RESULTS.md` |

Evidence is marked **[spec]** (Risk Data OpenAPI `2026.07.c`), **[sql]**
(`knowledge/sql scripts/`), **[minutes]** (CIC design sessions), **[cheryl]**
(Cheryl TeHennepe's direct testing against Risk Modeler), or **[probe]** (the
2026-08-03 live run, every API count cross-checked against Data Bridge).
Unmarked statements in Part 3 are all **[probe]**.

---

# Part 1 — How Risk Modeler composes a sub-portfolio

## The three calls

Risk Modeler has **no create-by-filter endpoint** — the create body has no filter
fields, and no such Platform operation is documented. A sub-portfolio is composed
from three calls, mirroring what the Risk Modeler UI itself does:

1. **Select** the source portfolio's account ids matching the breakout value
2. **Create** the empty portfolio
3. **Add** those accounts to it by id

## The EDM data model

The account (`ACCGRPID`) is the hub. Policies and locations are **siblings**
under it, not parent and child:

```
portacct (portfolio ↔ account)
   └── ACCGRPID ──┬── policy.ACCGRPID   → lobdet.LOBDETID   → LOBNAME    (LOB)
                  └── Property.ACCGRPID → Address.ADDRESSID → Admin1*    (state)
```

**[sql]** `portfolio_lines_of_business.sql` and `portfolio_states.sql` join
exactly this way. **[spec]** The REST schemas agree: `Policy.accountId` and
`Location.property.accountId` are the only join keys either object exposes.

**There is no policy↔location foreign key.** **[spec]** `Policy.accountId` is
documented as *"ID of account. Policy terms cover locations attached to the
account."* A policy covers every location on its account by default.

The exception is policy *conditions*, which narrow coverage without changing the
association. **[spec]** `PolicyCondition.conditionType` is either
`POLICY_RESTRICTION` ("restricts all policy coverage to specific locations") or
`SUBLIMIT` ("limits coverage to a subset of locations"). The subset is selected by
`policyConditionCriterias` — `{field, operator, value}` triples over location
properties, e.g. `POSTALCODE = 94514` — and/or by explicit `LocationCondition`
rows `{locationId, conditionId, isIncluded}`.

**Portfolios hold whole accounts.** `portacct` is an account join table, and both
write endpoints take account ids. There is no representation for "part of an
account in a portfolio." Everything in Part 3 about overlap follows from this one
fact.

## What is filterable, and where

| Operation | Filterable by | LOB? | State? |
|---|---|:--:|:--:|
| `getPortfolioAccounts` | account identity only | ✗ | ✗ |
| `getAccounts` (EDM-wide) | + counts, dates, tags, userText | ✗ | ✗ |
| `searchPolicies` | `accountId` (`IN`), peril, status, currency, dates, financial terms | ✗ | ✗ |
| `searchLocations` | `accountId` (`IN`), **`admin1Code`**, **`admin1Name`**, + ~65 more | ✗ | ✓ |

**LOB is not filterable on any Risk Data operation.** It is readable on the policy
record (`Policy.lob` → `{lobId, lobName, uri}`), so the LOB selection is
server-side scope plus client-side grouping. One pass yields every LOB at once:

```python
accounts = pm.search_accounts_by_portfolio_paginated(exposure_id, portfolio_id)
ids = ','.join(str(a['accountId']) for a in accounts)
policies = pm.search_policies_paginated(exposure_id, filter=f'accountId IN ({ids})')

by_lob = defaultdict(set)
for policy in policies:
    by_lob[policy['lob']['lobName']].add(policy['accountId'])
```

State resolves server-side, because `admin1Code` is filterable alongside
`accountId`:

```python
filter = f'accountId IN ({ids}) AND admin1Code = "TX"'
locations = pm.search_locations_paginated(exposure_id, filter=filter)
account_ids = {loc['location']['property']['accountId'] for loc in locations}
```

Both reads have to be chunked (W-6), and both nest the account id in a place
that is easy to get wrong (W-15).

---

# Part 2 — What the library provides

Verified against `portfolio.py` and `utils.py` at `a04e3d7`, not against prose.
This is what feature 005 gets to assume.

| Method | Behaviour feature 005 depends on |
|---|---|
| `search_accounts_by_portfolio_paginated(exposure_id, portfolio_id, filter, sort)` | reads the source portfolio's account ids; 284 of 284 verified against Data Bridge |
| `search_policies_paginated(exposure_id, filter, sort)` | the LOB selection read |
| `search_locations_paginated(exposure_id, filter, sort)` | the state selection read |
| `create_portfolio(edm_name, portfolio_name, portfolio_number, description)` | synchronous 201; raises `IRPValidationError` on a duplicate name, a name over 40 characters, or a number over 20 |
| `manage_portfolio_accounts(exposure_id, portfolio_id, *, accounts_to_add, accounts_to_remove)` | the populate path; synchronous 200; returns `{"addAccounts": {"completed": n, "total": m}, ...}`; idempotent |
| `paginate_search` (behind every `*_paginated`) | assumes a record offset; raises `IRPAPIError` rather than returning a list it cannot show is complete |
| `get_geohaz_job` / `poll_geohaz_job_to_completion` | GeoHaz status; a GeoHaz job id is **not** an import job id (W-12) |

Both writes are synchronous: 200 is the only success status, and any other 2xx
raises `IRPAPIError` carrying the status and `Location` header rather than being
normalized or polled. Confirmed live — no 202 and no workflow URL appeared
anywhere, so neither call needs the poller.

Three things the library deliberately does not do, each of which lands on this
repo:

- **It does not chunk.** `filter` travels in the URL and the caller owns the
  splitting (W-6).
- **It does not shorten names.** Both name fields raise instead of truncating
  (W-2, W-13).
- **`add_filtered_accounts` is not the add method.** It exists, returns `{}`, and
  cannot report what it did. `manage_portfolio_accounts` replaces it here (W-1).

`allow_deep_filters` is gone from the public signature — `allowDeepFilters=false`
is hardcoded — so the deep-filter selection path this spec once considered is not
reachable and needs no decision (W-7).

Library tests cover pagination termination and its two raises, both portfolio
writes' request bodies and 200-only status handling, the `bool`-is-not-an-`int`
rejection in the account-id validators, and the 40/20-character name boundaries.
The suite is offline and runs as a blocking CI job. Remaining library coverage is
tracked in [issue #20](https://github.com/premiumiq/irp-integration/issues/20);
migrating the four older paginators in `edm.py`, `analysis.py`, `rdm.py` and
`treaty.py` onto the shared helper is
[PR #22](https://github.com/premiumiq/irp-integration/pull/22).

## Library-side findings and what was done about each

Recorded so the reasoning behind the contract above stays findable. None of these
need action in this repo.

| | Finding | Resolution in PR #21 |
|---|---|---|
| L-1 | `offset` is a record offset on this tenant; the runtime probe cost an extra request per read and never once reported page semantics | probe removed, record offset assumed; a repeated page and the `max_pages` ceiling now raise; five hand-rolled paginators migrated onto the helper |
| L-2 | `allowDeepFilters=true` returns zero rows with HTTP 200 on `usfl_onFS` at all nine scope sizes where the truth is 272 | `allow_deep_filters` removed from the public signature |
| L-3 | `lobId` on `searchPolicies` returns HTTP 500, not a clean 400 | documented as unusable so a caller does not read the 500 as transient and retry |
| L-4 | `manage_existing_accounts=True` returns 200 and adds nothing; `markedAccounts` really is ignored | documented as a mode switch, not an upsert option |
| L-5 | PATCH reports `completed`/`total`; PUT returns `{}` | `manage_portfolio_accounts` documented as the populate path |
| L-6 | The filter dies at about 4,870 **characters** (HTTP 431), not at a fixed id count | ceiling and the characters-not-ids rule recorded in the docstring; chunking stays the caller's job |
| L-7 | The client-side duplicate-name refusal used `IRPAPIError`, indistinguishable from a real API failure | now raises `IRPValidationError` |
| L-8 | `portfolioName` caps at 40 server-side, unchecked, while `portfolio_number` was silently truncated to 20 | both validated client-side, neither truncated |
| L-9 | A GeoHaz job id polled as an import job returns `404 Invalid job id` while the job is running | the matching poller named in the `submit_geohaz_job` docstring |
| L-10 | Both selection reads nest the account id; a wrong key returns a plausible empty result | both shapes shown in the method docstrings |

---

# Part 3 — Probe findings

## W-1 The select → create → add sequence works end to end

Broke `usfl_edm_small` portfolio 1 `usfl_commercial` (1,701 accounts, 48 states)
out by state, twice:

| | TX | CA |
|---|---|---|
| Data Bridge says | 220 accounts | 209 accounts |
| selection read returned | 220 | 209 |
| chunked requests | 5 | 5 |
| `manage_portfolio_accounts` | `completed 220, total 220` | `completed 209, total 209` |
| read-back set equals selection | yes | yes |

The add step must be `manage_portfolio_accounts` (PATCH), not
`add_filtered_accounts` (PUT). PUT returns `{}`, so the worker cannot tell an
empty populate from a full one without a read-back; PATCH returns
`completed`/`total`. And `manage_existing_accounts=True` on the PUT path returns
HTTP 200 while adding nothing at all — `portacct` rows 0 on probe portfolio 7 —
so it is a mode switch, not the heal-a-partial-write option it reads as.

Evidence: `b1_create_and_populate.py`, `b2_idempotency_and_edges.py`, probe
portfolios 2, 3 and 7.

**Do:** `plan.md` line 19 names `add_filtered_accounts` as the add step. Change
it to `manage_portfolio_accounts` (T-01), and drop `add_filtered_accounts` from
the T-02 prerequisite description on lines 12, 35 and 42.

## W-2 Portfolio names cap at 40 characters, which breaks the naming rule

The `{source portfolio name} - {breakout value}` rule plus a ` (2)` collision
suffix was designed against a 200-character cap (`research.md` R4, `spec.md`
FR-010). The real cap is 40, boundary confirmed exactly: 40 characters creates,
41 rejects. `create_portfolio` now raises on an over-long name rather than
truncating, so this fails at write time instead of producing a shortened name
nobody notices.

The cap bites hardest on LOB, where values are words rather than codes.
`TY2607 Cedant Book - General Liability` is 38 characters with a source name of
only 18, and real broker-supplied portfolio names are longer than that. State
values are 2–3 characters once they are codes (W-8), so the state breakout has
room; the LOB breakout does not.

This needs a product decision, not an edit. Three options:

1. Truncate the source portfolio name, keep the breakout value whole. Names stay
   readable at the breakout end, which is what an analyst scans for, but two
   source portfolios with a shared prefix produce colliding names.
2. Use a short generated prefix and carry the full name in `description`, which
   has no observed cap. Names stay unique; the Risk Modeler portfolio list
   becomes less readable.
3. Keep the composed name and reject breakouts whose longest generated name
   exceeds 40 characters, reported in the confirmation step before any write.

Evidence: `b2_idempotency_and_edges.py`, probe portfolios 8 and 9.

**Do:** decide as a new `P-nn`, then amend FR-010 and R4. Option 3 alone is not
enough — it would refuse an ordinary LOB breakout of a normally-named portfolio
outright, leaving the analyst with no way to proceed.

## W-3 A partial match brings the whole account, verified independently

Account 751 in `usfl_edm_small` holds 84 locations across 31 states. After the TX
breakout:

- account 751 is a member of the TX portfolio
- it brought **all 84** of its locations
- **10** of those are in TX; 74 are not

Same result for CA (9 of 84 in CA). Verified in Data Bridge against the created
portfolio, independently of Cheryl's Risk Modeler test (Part 4).

Evidence: `b1_create_and_populate.py`.

## W-4 Overlap depends on the source portfolio, not on the breakout dimension

The same state breakout produces wildly different overlap on two portfolios in
this sandbox:

| source portfolio | accounts | states | multi-state accounts | Σ sub-portfolio TIV vs source |
|---|---|---|---|---|
| `usfl_edm_small` portfolio 1 | 1,701 | 48 | many (2,164 account-state pairs) | **6.6×** (+555%) |
| `usfl_onFS` portfolio 4 | 284 | 4 | 0 | 1.0× (+0%) |
| `night_edm` portfolio 3 | 248,732 | 55 | 0 | 1.0× (+0%) |

Full numbers for the first row: source TIV 30,437,380,495, Σ sub-portfolio TIV
199,518,340,497 across all 48 states. TX and CA alone summed to 23,520,046,779,
or 77.3% of source TIV, while covering 2 of the 48 states.

Evidence: `a3_lob_and_leakage.py`, `b1_create_and_populate.py`,
`b2_idempotency_and_edges.py`, `a5_multi_lob.py`.

**Do:** the confirmation step must compute overlap and TIV inflation **for the
portfolio being broken out**. A fixed warning is wrong in both directions — it
cries wolf on a single-state book and understates a 6.6× one.

## W-5 The LOB breakout is a clean partition on the only multi-LOB book available

`usfl_other` in `night_edm` (portfolio 3) does hold three LOBs, read in full
rather than sampled:

| LOB | accounts | sub-portfolio TIV |
|---|---|---|
| FLD Other | 130,104 | 19,456,998,032 |
| FLD Other Clay | 118,344 | 16,349,456,456 |
| FLD Other CB | 284 | 26,897,455 |
| | **248,732** | **35,833,351,943** |

- **0** of 248,732 accounts write more than one LOB
- **0** accounts carry no LOB, so a LOB breakout drops nothing
- per-LOB counts sum exactly to the account total; TIV inflation **0.00%**
- the LOB grouping read through `searchPolicies` and grouped client-side matched
  Data Bridge exactly on a 4,000-account cross-check

Two caveats, both important:

`night_edm` portfolio 3 and `mixed_edm` portfolio 3 are **the same book copied
into two EDMs** — identical account count, identical id range, identical id
checksum, identical TIV. So the earlier `mixed_edm` sample and this full read are
one observation, not two.

The data is flood test data (`FLD Other`, `FLD Other Clay`, `FLD Other CB`), not
a commercial book. A real book can write more than one LOB per account, and
nothing in this sandbox can prove otherwise — no account in any of the six EDMs
writes two LOBs.

Evidence: `a5_multi_lob.py`, `a5b_same_book.py`, `a3_lob_and_leakage.py`,
`a4_multistate_and_url.py`.

**Do:** keep the open question about LOB overlap open, but reframe it. It is no
longer "can we read LOB per account" — that works exactly. It is "does a real
book put two LOBs on one account", and it needs a commercial book to answer. W-11
tests the overlap behaviour on a purpose-built book instead, so the reframed
question blocks no implementation work.

## W-6 There is no portfolio predicate, so `accountId IN (...)` chunking is required

`portfolioId`, `portfolioName`, `portInfoId`, `portfolio`, and `portfolioNumber`
are all rejected on `getAccounts`, with and without deep filters. The only way to
scope a read to a source portfolio is to list its account ids.

That runs into a **character** ceiling: HTTP 431, bisected on `mixed_edm`, at an
`accountId IN (...)` filter of 4,872 characters — 1,193 ids of 1–4 digits works,
one id more fails. The limit is characters, not ids, so a portfolio with 7-digit
account ids fits roughly half as many per request. 431 is a header-size limit, so
the bearer token shares the budget and an API-key tenant may allow more; do not
treat 4,872 as a constant. The library does not chunk — it records the ceiling in
the `search_policies` docstring and leaves the splitting to the caller.

This keeps the deferred one-call populate (`T-09`, `queryFilter`) closed rather
than pending: there is no filter that names a source portfolio, so the one-call
form cannot express "the TX accounts of portfolio 1".

Evidence: `a2c_deep_filters.py`, `a4_multistate_and_url.py`,
`a3_lob_and_leakage.py`.

**Do:** the breakout worker reads the source portfolio's account ids once, then
chunks every selection read that scopes by those ids. 400 ids per request is the
tested size. Size the chunk by composed filter length rather than by id count,
and record it as a named constant, not a literal.

## W-7 The state selection read must use `searchLocations` with `admin1Code`

`admin1Code` and `admin1Name` on `searchLocations` are honoured exactly and
matched Data Bridge at every scope size tested. `admin1Name` matching is
case-insensitive. This is the read to build the state breakout on.

The alternative this spec once considered — `getAccounts` with
`allowDeepFilters=true` — is gone. It returned **zero rows at every scope size
tested** on `usfl_onFS` (1 through 272 accounts) where Data Bridge and
`searchLocations` both say 272: HTTP 200, empty list, no warning. Scope size,
filter length, and state vocabulary were all ruled out as causes. The library has
since removed the parameter, so the path cannot be taken by accident.

Evidence: `a2b_filter_verified.py`, `a2c_deep_filters.py`,
`a2d_state_vocabulary.py`, `a2e_deep_filter_reliability.py`,
`b1_create_and_populate.py`.

## W-8 Breakout values must be `admin1Code`, and the Data Bridge query has to change

`Admin1Name` can be empty where `Admin1Code` is populated — `night_edm` account
769966 has `admin1Code = NY` and `admin1Name = ''`.
`sql/databridge/portfolio_states.sql` line 9 currently reads:

```sql
COALESCE(NULLIF(a.Admin1Name, ''), a.Admin1Code) AS State
```

That produces a mixed vocabulary — full names for most states, bare codes for the
ones missing a name — and the mixed values cannot be fed to a single filter
field. On `usfl_onFS` the codes are numeric (`200` for Puerto Rico, `010` for
St Croix), so the two vocabularies are not even the same shape.

Evidence: `a2d_state_vocabulary.py`, `a2b_filter_verified.py`.

**Do:** change `portfolio_states.sql` to return `Admin1Code` as the breakout
value and `Admin1Name` as a separate display label, nullable, and change the
`WHERE` on line 17 to test `Admin1Code`. This settles R6's open question: the
selection wants the code. W-12 and W-16 give the stronger reasons.

## W-9 Re-running a breakout is safe, and `completed < total` is not an error

`manage_portfolio_accounts` is idempotent:

| | response | `portacct` rows after |
|---|---|---|
| 70 new ids | `addAccounts: completed 70, total 70` | 70 |
| same call repeated | `addAccounts: completed 0, total 70` | 70 |
| 35 ids, then all 70 | `addAccounts: completed 35, total 70` | 70 |
| removing 2 | `removeAccounts: completed 2, total 2` | 68 |

`completed` counts ids **newly added**, not ids that ended up as members.

Evidence: `b1_create_and_populate.py`, `b2_idempotency_and_edges.py`, probe
portfolios 4, 5 and 6.

**Do:** the worker must not treat `completed < total` as a failure. A retry after
a partial write is a healthy re-run and reports `completed 0`. Verify by reading
the portfolio back and comparing against the persisted plan, which is what
AGENTS.md rule 8 requires anyway — the approved account-id list is the
thing to compare against, not the response counts.

This also retires R7's conditional: re-adding already-member accounts is safe, so
the adopt-then-heal path runs unconditionally and the "record the outcome as
`adopted (verify contents)` instead" fallback is not needed.

## W-10 Adopt-by-name needs a pre-check, not exception parsing

`create_portfolio` refuses a duplicate name client-side before any POST, and now
raises `IRPValidationError` rather than `IRPAPIError`, so the two cases are
distinguishable by class. An adopt-an-existing-portfolio-by-name path should still
call `search_portfolios` first: `IRPValidationError` also covers an over-long name
(W-2) and an over-long number (W-13), so catching it is not the same as "the name
is taken".

Evidence: `b2_idempotency_and_edges.py`.

## W-11 LOB overlap tested on a purpose-built book: the breakout handles it

Since no sandbox account writes two LOBs, one was built. `c1`–`c3` generated an
MRI book, imported it into `usfl_edm_small` as portfolio 13
`ZZ_FIXTURE_multilob`, and broke it out by LOB. 40 accounts, 49 policies, 45
locations, deliberately messy:

| accounts | shape |
|---|---|
| ZZFX-0001…0005 | 2 policies in 2 LOBs |
| ZZFX-0006 | 3 policies in 3 LOBs |
| ZZFX-0007…0009 | 1 LOB, 2 locations in 2 states |
| ZZFX-0010…0011 | 2 LOBs **and** 2 locations in 2 states |
| ZZFX-0012…0040 | one LOB, one location |

Import reported `Imported 40 Accounts and 45 Locations` with no errors. All 22
checks passed against the expectations file written *before* the import:

- per-LOB account sets, read through `searchPolicies` and grouped client-side,
  matched the plan exactly with 8 accounts belonging to more than one group
- 49 LOB memberships for 40 accounts — 9 duplicates, as planned
- each of the three LOB sub-portfolios received exactly its planned accounts
  (`completed 25/25`, `18/18`, `6/6`) and its planned TIV to the cent
- LOB inflation **1.2400×**, state inflation **1.2313×** on the same book, both
  matching a hand calculation

The result that matters: **ZZFX-0006 landed in all three LOB sub-portfolios, and
carried all three of its policies into each one.** So the LOB dimension leaks
exactly the way state does — a sub-portfolio for `FLD Comm` contains that
account's `FLD Other` and `FLD Other Clay` policies too. W-3 is confirmed to be
account-level and dimension-independent.

Two side results. MRI created `lobdet` rows for the two LOB names
`usfl_edm_small` had never seen (ids 1643 and 1661), so a breakout does not
require the LOB to pre-exist. And an account with no LOB is **unrepresentable**
through MRI, because the accounts file *is* the policy file — every imported
account carries at least one policy and therefore one LOB. That retires the
silent-drop risk for MRI-sourced books, though not for books loaded another way.

Evidence: `c1_build_fixture.py`, `c2_import_fixture.py`, `c3_verify_fixture.py`.

**Do:** nothing changes in the design — it already handles overlap. This closes
the testing gap, so the overlap and inflation arithmetic can be built against a
book that actually has overlap rather than against one where every count is
trivially equal.

## W-12 `admin1Name` returns zero rows until GeoHaz runs

The fixture arrived with `Admin1Code` populated from the MRI `STATECODE` column
and `Admin1Name` **empty on all 45 locations**, `GeoResolutionCode` 0. Both
fields are filterable, and the difference is silent:

| filter | before GeoHaz | after GeoHaz |
|---|---|---|
| `admin1Code = "AR"` | 6 locations | 6 locations |
| `admin1Name = "ARKANSAS"` | **0 rows** | 6 locations |
| `admin1Name = "TENNESSEE"` | **0 rows** | 6 locations |

Running GeoHaz on portfolio 13 took `Admin1Name` from 45 empty to 0 empty and
`GeoResolutionCode` from 0 to 1–5. Neither MRI source file populates `STATE` —
`usfl_commercial`'s file leaves it blank too — so `Admin1Name` is a geocoding
output, not an import field. `usfl_commercial` has it only because it was
geocoded.

A state breakout that filters on `admin1Name` therefore produces empty
sub-portfolios, reported as success, for any portfolio geocoded later than the
breakout. This is reachable through ordinary sequencing, not a broken EDM.

Evidence: `c4_admin1name_gap.py`, `c5_geohaz_fixture.py`.

**Do:** filter on `admin1Code` only, and treat `Admin1Name` as a display label
that may be absent. Feature 005 then needs nothing from GeoHaz.

One note for whenever the workbench does await a GeoHaz job, which is outside
this feature: a GeoHaz job id is served by `/platform/geohaz/v1/jobs`, and polling
it as an import job returns `404 Invalid job id` while the job is in fact running.
The single-status check the poller needs is `get_geohaz_job`.

## W-13 A composed name over 20 characters must pass `portfolio_number` explicitly

`portfolio_number` defaults to `portfolio_name`, and it is capped at 20
characters against the name's 40. `create_portfolio` now raises
`IRPValidationError` when the name is over 20 and no number was supplied, rather
than shortening the derived value.

R4 and FR-010 both say `portfolio_number` is left empty because "the wheel
defaults it to the name, truncated to 20 chars". That is no longer true, and
every composed name of interest is over 20 characters —
`usfl_commercial - TX` is exactly 20, so the very next character breaks it.

Evidence: `create_portfolio` at `a04e3d7`; the truncation behaviour probed at
`6c5f5d0` on portfolios 10–12 was removed in the same PR.

**Do:** the plan builder must generate a `portfolio_number` of at most 20
characters per sub-portfolio, deterministically, and persist it in the confirmed
plan alongside the name. It is a second identifier the naming decision in W-2 has
to cover, not a free field.

## W-14 An incomplete paginated read now raises instead of returning a short list

`paginate_search` raises `IRPAPIError` when it cannot show it read every page — a
repeated page, or the 1,000-page ceiling with pages still coming back full.
Before, it logged a warning and returned what it had.

This matters for the worker: an under-selection is the failure that produces a
sub-portfolio missing accounts and reports success. The library now converts it
into an exception, so the worker does not need its own completeness check on the
selection read — but it does need to catch `IRPAPIError` around the read and fail
that sub-portfolio's outcome rather than continuing with a partial id list.

The read-back comparison against the persisted plan (W-9) stays regardless, since
it is the check AGENTS.md rule 8 asks for.

Evidence: `utils.paginate_search` at `a04e3d7`; the probe run recorded a record
offset on every call across three operations and three exposures, with
`search_accounts_by_portfolio_paginated` reading 284 of 284.

## W-15 Response shapes for the two selection reads

The account id is nested differently in each, and reading the wrong key returns a
plausible-looking empty result rather than an error:

```python
policy["accountId"]                                    # searchPolicies
policy["lob"]["lobName"]                               # searchPolicies
row["location"]["property"]["accountId"]               # searchLocations
row["location"]["address"]["admin1Code"]               # searchLocations
```

Filtering by `lobId` is not an alternative to the client-side grouping: it
returns HTTP 500, not a clean 400.

**Do:** the worker's parsing of both reads is worth a unit test with a recorded
response body, since every wrong-key mistake in the probe run was silent.

## W-16 The generated name must be built from the state code, not the display label

Derived from W-8 and W-12 together, and it constrains the naming decision in W-2.

`Admin1Name` appears when GeoHaz runs, so the same portfolio's state values read
as `TX` before geocoding and `TEXAS` after. If the generated name is built from
the display label, then a breakout run before geocoding produces
`{source} - TX` and a re-run after geocoding computes `{source} - TEXAS`.

That breaks adopt-by-name (T-07/R7). The re-run's `create_portfolio` would not
collide with the existing name, so it would **succeed**, leaving two Risk Modeler
portfolios for the same source, dimension and value. The lineage-key skip
(FR-011) prevents this only if `breakout_value` stores the code, since that is
the only one of the two that does not change under the analyst's feet.

**Do:** store `Admin1Code` in `breakout_value` and compose the generated name
from the code. Whether the analyst *sees* the code or the name in the modal
preview and the lineage badge is a separate product decision — but the identifier
Risk Modeler and `irp_portfolio` hold must be the code either way.

## W-17 `portfolioNumber` is filterable on `searchPortfolios`, so adoption can resolve on it

Probed directly against `usfl_edm_small` exposure 5369261, using fixture
portfolio 13 (`ZZ_FIXTURE_multilob`, number `ZZFX`):

| filter field | result |
|---|---|
| `portfolioName = "ZZ_FIXTURE_multilob"` | 1 portfolio — the control, known to work |
| `portfolioNumber = "ZZFX"` | 1 portfolio |
| `portfolionumber = "ZZFX"` | 1 portfolio — field names are case-insensitive |
| `number = "ZZFX"` | error naming the Portfolio Search syntax help |

This is what makes the naming decision workable. `portfolioName` is subject to
truncation and collision suffixing, both of which depend on what else exists in
the EDM at the moment the name is computed, so a name is not a stable handle
across runs. `portfolioNumber` can be composed from the source portfolio's Risk
Modeler id, the dimension, and the breakout value alone — inputs that do not
change between a preview and a later re-run — and it is searchable, so
adopt-an-existing-portfolio resolves on the number instead.

Note that Risk Modeler portfolio ids repeat across EDMs (portfolio 3 exists in
`night_edm` and `mixed_edm` both), so a number is unique only within its
exposure. The adoption search is scoped to one exposure, which is enough.

Evidence: `d1_workbench_decisions.py`.

## W-18 `Admin1Code` is populated on every location; `Admin1Name` is the one that goes missing

`Admin1Name` and `Admin1Code` are **separate exposure attributes**, not two
renderings of one value, so neither can be derived from the other and the app
must never synthesize a name from a code.

W-8 and W-12 established that `Admin1Name` can be empty where `Admin1Code` is
populated. The reverse direction had not been tested, and it matters: a location
with a name but no code cannot be selected by code, so its account would land in
no state sub-portfolio at all — silently, breaking SC-002's coverage claim. It
does not occur here.

| EDM | addresses | code present, name present | code present, name empty | **name present, code empty** |
|---|---|---|---|---|
| `usfl_edm_small` | 11,532 | 11,532 | 0 | **0** |
| `night_edm` | 780,273 | 780,271 | 2 | **0** |
| `usfl_onFS` | 260,915 | 260,915 | 0 | **0** |

`Admin1Code` is populated on 100% of the 1,052,720 addresses across the three
EDMs, and on 100% of the locations reachable from a portfolio. Selecting on the
code therefore loses no exposure in this sandbox. The two `night_edm` rows with a
code and no name are the ones behind W-8.

This is sandbox evidence, not a guarantee about a production EDM. The claim worth
carrying forward is the asymmetry, which has a cause: the code arrives with the
MRI import while the name is written by geocoding (W-12), so the code is present
from the moment a portfolio exists and the name is not.

Evidence: `d1_workbench_decisions.py`.

## W-19 Per-value account counts cost 1.4 seconds worker-side, and fix a value the current query invents

Timed against `night_edm`, the largest book available — 248,732 accounts and
780,273 addresses — running each query twice:

| query | rows | cold | warm |
|---|---|---|---|
| states, current `SELECT DISTINCT` | 196 | 1.16s | 1.23s |
| states, `GROUP BY` + `COUNT(DISTINCT ACCGRPID)` | **195** | 1.85s | 1.86s |
| LOB, current `SELECT DISTINCT` | 11 | 0.76s | 0.93s |
| LOB, `GROUP BY` + `COUNT(DISTINCT ACCGRPID)` | 11 | 1.06s | 1.22s |
| account total per portfolio, new query | 6 | 0.53s | 0.52s |

Net **+1.44s** on `backfill_edm_detail`, which is a Sync-time worker job. The
request path is untouched: the breakout preview reads the stored summary exactly
as it does today, with more keys in the JSON.

The row-count difference is the more interesting result. The current states query
returns **196** values for `night_edm` where the counted version returns **195**,
and the extra one is an artifact of the `COALESCE(Admin1Name, Admin1Code)`
vocabulary mixing described in W-8: the two `night_edm` addresses that carry `NY`
with no name produce a `NY` value alongside the `NEW YORK` that every other New
York address produces. A state breakout of that portfolio today would offer the
analyst 196 values including a duplicate New York, and the `NY` one would create
a sub-portfolio nobody wants. Grouping by code collapses them correctly.

Evidence: `d1_workbench_decisions.py`.

## W-20 The REST selection read cannot complete on a 248,000-account portfolio

*(Not from the 2026-08-03 probe run — observed 2026-08-05 at the US1 checkpoint,
first `run_breakout_lob` against `night_edm`'s largest portfolio.)*

The worker failed at step 3 with nothing created:

> Account search for portfolio ID 3 was still returning full pages of 100 at
> the 1000-page ceiling, so the 100000 records read cannot be shown to be the
> complete result.

The wheel behaved as designed (W-14: an unprovably-complete read raises rather
than returning a short list). The failure is the selection strategy: the
portfolio holds 248,732 accounts, so the source account-id read alone needs
2,480 pages at 100 per page, and even without the ceiling the LOB policy scan
feeds those ids back as `accountId IN (…)` URL filters chunked at 3,000
characters — roughly 370 ids per chunk, ~670 chunks, each itself paginated.
Thousands of Risk Modeler round trips for one breakout. W-1 validated the
sequence end to end on books of a few hundred accounts; nothing between that
and `night_edm` was ever run.

The same book's DataBridge aggregates run in ~1–2 seconds (W-19's timings are
against this exact EDM), and `portacct.ACCGRPID` is the id RM's account
operations accept as `accountId` (confirmed by Ben, 2026-08-05). Consequence:
the selection read and the composition read-back moved to parameterized
DataBridge SQL — R1 as revised 2026-08-05. Create and add stay on REST (writes;
DataBridge is read-only).

---

# Part 4 — Design record: why sub-portfolios, and why the overlap is accepted

The probe findings above establish that generated sub-portfolios overlap. This
part is the record of why that was accepted rather than treated as a defect. It
is the only copy of this material.

## Risk Modeler's confirmed behaviour, from Cheryl

**[cheryl]** Tested directly, email follow-up to Ben:

> I just wanted to confirm that the Risk Modeler portfolio creation logic does
> indeed keep all locations within a policy as long as at least 1 meet the filter
> criteria.
>
> For example, if I have a single policy called ABC Grocers that covers the
> following locations: Loc 1 = FL, Loc 2 = FL, Loc 3 = SC, Loc 4 = GA. If I
> create a new portfolio using the filter criteria StateCode = FL, that new
> portfolio will include the policy for ABC Grocers and all 4 of the locations
> above (not just the 2 Florida locations).

This is consistent with RiskLink. The keep-only-matching-locations checkbox
recalled during the 7-16 session was **Touchstone's**, not RiskLink's.

**[probe]** Stated precisely, the rule is **account**-level, not policy-level: at
least one matching location admits the whole account — all of its locations *and*
all of its policies. The two framings coincide for a single-policy account like
ABC Grocers, but `portacct` has no row that could admit one policy of a
two-policy account without the other. Confirmed twice, independently of Cheryl's
test: see W-3 and W-11.

## The consequence: an overlapping cover, not a partition

Because a partial match admits the whole account:

- an account with policies in two LOBs lands in **both** LOB sub-portfolios, each
  time carrying the other LOB's policy with it;
- an account with locations in three states lands in **all three** state
  sub-portfolios, each time carrying the non-matching locations;
- `Σ sub-portfolio TIV > source portfolio TIV` whenever either holds.

**[probe]** All three confirmed, and the inflation is large where it bites — 6.6×
on `usfl_edm_small` portfolio 1, 1.0× on books with no multi-state accounts
(W-4).

**[minutes]** This collides with a stated user preference — Cheryl prefers
breakouts that *"sum to 100%"* rather than running a whole portfolio and
subtracting a subset, *"that's messy"* (7-16 §II.4).

**[minutes]** It does **not** reintroduce the financial-structure problem, which
was the original worry (7-16 §III): a $5K Florida building never triggering a
$20K policy deductible on its own. Keeping the account intact keeps the policy
structure intact. The cost is scope, not correctness of the financial model.

**[minutes]** LOB was assessed as the clean case — *"one policy = one line of
business, so all locations travel with it cleanly. The issue is
geography-specific"* (7-16 §III). That is true of policies. It carries to accounts
only if accounts are single-LOB, and W-11 shows the breakout leaks the same way on
both dimensions when they are not.

## Why sub-portfolios at all, rather than output granularity

Worth recording, because it comes up every time someone re-encounters the
overlap.

**[minutes]** The driver is **treaty structure**, not report granularity: *"treaty
structures (e.g., a different retention for one state, or an excluded line of
business) that the broker's loss output doesn't break out. The analyst must
separate that state (or line) from everything else … to correctly model how losses
flow through the treaty"* (7-7 §4). The Northeast example: a treaty covers only
the Northeast but the cedent sends the whole book, so two portfolios must be run —
Northeast and everything-not-Northeast — *"to look at the treaty's risk
correctly"* (7-16 §II.5).

An output profile splits results **after** the financial model has run, under one
set of treaty terms. A segment with its own retention or exclusion needs its own
analysis. Gross losses by LOB and net losses under LOB-specific treaty terms are
different numbers.

**[minutes]** The portfolio-versus-output line was already drawn deliberately:
*"Granularity cap: state/country. Anything more granular (CRESTA, ZIP) is saved as
output, not as a portfolio — it's just too much to manage"* (7-16 §II.6). And the
team is moving **toward** portfolios, not away: analysts currently avoid
sub-portfolios and post-process losses with SQL, but *"that back-end SQL
manipulation won't be as easy in the new tool, so fast portfolio creation becomes
the preferred path"* (7-16 §II.7).

**[minutes]** Output-side handling remains the documented fallback for the
geography case specifically, where Cheryl *"leans this way when the exposure-side
split is too messy"* (7-16 §III).

**[cheryl]** Direction for the build: proceed, with the caveat made visible —
*"it is important to build this out with the understanding that the analysts
realize the issues associated with multi-location policies and geographic
splits."*

---

# Part 5 — What this changes in the spec

| File | Change | Finding |
|---|---|---|
| `plan.md` line 19 | add step becomes `manage_portfolio_accounts`; it is sync, so no poller | W-1 |
| `plan.md` lines 12, 35, 42 | T-02's library prerequisite is delivered — drop `add_filtered_accounts` and the `FILTERED_ACCOUNTS` constant, name the methods that shipped | W-1, W-7 |
| `plan.md` line 48 | T-08 spike is closed: U1, U2, U4, U5 and U6 are all answered | W-1, W-6, W-7, W-8, W-14 |
| `plan.md` T-09 | closed permanently, not deferred — no filter names a source portfolio | W-6 |
| `plan.md` worker section | selection reads chunk by filter length; catch `IRPAPIError` per sub-portfolio | W-6, W-14 |
| `spec.md` FR-010, P-10 | 200-character cap becomes 40; naming rule needs a new `P-nn`; `portfolio_number` becomes part of the plan | W-2, W-13, W-16 |
| `spec.md` user story 3, acceptance 2 | the example name uses a state name (`- Florida`); the stored value is a code | W-8, W-16 |
| `research.md` R1 | rewrite: the method table, the `add_filtered_accounts` decision, and the whole U1–U6 unknowns list are superseded | W-1, W-6, W-7, W-14 |
| `research.md` R4 | 200-character cap superseded by 40; `portfolio_number` is no longer a free field | W-2, W-13 |
| `research.md` R6 | resolved: the selection filter wants `admin1Code`; `Admin1Name` is a nullable label and a geocoding output | W-8, W-12, W-16 |
| `research.md` R7 | the "if U2 shows re-adding is unsafe" conditional is retired — re-adding is safe | W-9 |
| `spec.md` FR-007 | the disclosure gains the computed overlap for the portfolio being broken out | W-4, W-19 |
| `sql/databridge/portfolio_states.sql` lines 9, 17 | return `Admin1Code` as the value plus `Admin1Name` as a nullable label, and a per-value account count; test the code in the `WHERE` | W-8, W-12, W-19 |
| `sql/databridge/portfolio_lines_of_business.sql` | add a per-value account count | W-19 |
| `app/services/irp_gateway.py` summary builder (lines 338–353) | write state codes under a new key, carry the nullable label, carry per-value account counts and the portfolio account total | W-18, W-19 |
| `contracts/irp-library.md` | rewrite against the shipped methods — it still specifies `add_filtered_accounts`, `allow_deep_filters` as a maybe, and pagination as an open spike item | W-1, W-6, W-7, W-14 |
| `data-model.md` | `breakout_value` stores the code; the confirmed plan persists a `portfolio_number` per sub-portfolio | W-8, W-13, W-16 |

## Decisions taken 2026-08-03

To be written into `spec.md`'s decision table as `P-11`, `P-12`, `P-13` during
the document pass. Recorded here with the evidence that settled each.

**P-11 — naming.** The generated name truncates the **source portfolio name** to
fit and keeps the breakout value whole; the full source name is carried in the
Risk Modeler `description`. `portfolio_number` is a separately composed
identifier of at most 20 characters, built from the source portfolio's Risk
Modeler id, the dimension, and the breakout value — inputs that do not change
between a preview and a re-run — and **adoption resolves on the number, not the
name** (W-17). Closes W-2, W-13.

**P-12 — state values are codes.** `Admin1Code` is the filter value, the stored
`breakout_value`, the token in the generated name and number, and what the
analyst sees. `Admin1Name` is a separate exposure attribute, carried alongside as
a nullable display label and never synthesized from the code. Closes W-8, W-12,
W-16, W-18.

Consequence that needs handling: `portfolio_states.sql` writes
`COALESCE(Admin1Name, Admin1Code)` today, so **every summary already backfilled
holds a mixed vocabulary of names and codes**. Those values cannot be
reinterpreted as codes. The codes must be written under a new summary key so a
portfolio whose summary predates the change is detectably stale — the gate then
refuses the state breakout and points at Sync, which is P-04's existing behaviour
— rather than filtering a name as though it were a code and selecting nothing.

**P-13 — the preview quantifies the overlap.** Both summary queries gain a
per-value account count, and a portfolio account total is added, so the preview
states the real overlap for the portfolio being broken out rather than a fixed
warning. Measured at +1.44s on the `backfill_edm_detail` worker job for the
largest book in the sandbox, with no change to the request path (W-19). Closes
W-4.

Nothing is open. Everything in the table above is now an edit.

---

# Part 6 — Probe portfolios left in place

All in `usfl_edm_small`, exposure 5369261, not cleaned up so they can be inspected
in Risk Modeler.

| id | name | what it shows |
|---|---|---|
| 2 | `ZZ_PROBE_08031402_usfl_commercial - TX` | 220 accounts; account 751 with all 84 locations |
| 3 | `ZZ_PROBE_08031402_usfl_commercial - CA` | 209 accounts; the same account, the same 84 locations |
| 4 | `ZZ_PROBE_08031404_reidem` | 70 accounts after two identical adds |
| 5 | `ZZ_PROBE_08031404_heal` | 70 accounts after a 35-then-70 re-add |
| 6 | `ZZ_PROBE_08031404_put` | 68 accounts — PUT populate, then 2 removed |
| 7 | `ZZ_PROBE_08031404_mea` | empty — `manage_existing_accounts=True` added nothing |
| 8, 9 | `ZP140443…` | 39- and 40-character names, the length boundary |
| 10–12 | `ZPN140443_*` | `portfolioNumber` truncation at 20, since removed from the library |
| 13 | `ZZ_FIXTURE_multilob` | the multi-LOB fixture book: 40 accounts (`ZZFX-0001`…`0040`), 49 policies, 45 locations, geocoded |
| 14 | `ZZFX_LOB_FLD Comm` | 25 accounts — LOB breakout of portfolio 13 |
| 15 | `ZZFX_LOB_FLD Other` | 18 accounts, 8 of them shared with 14 and 16 |
| 16 | `ZZFX_LOB_FLD Other Clay` | 6 accounts; `ZZFX-0006` is in all three |

The `a5` probes wrote nothing. Portfolio 13 added 40 accounts and two `lobdet`
rows to `usfl_edm_small`; every fixture account number starts `ZZFX-`, so nothing
that existed before was modified. GeoHaz ran on portfolio 13 only.

Fixture files are at `../../../irp-integration/probes/fixtures/`, and
`c1_build_fixture.py` regenerates them deterministically.

---

# Sources

- Risk Data OpenAPI 3.0.2, `version 2026.07.c` — embedded inline in every
  preserved ReadMe page under `knowledge/sources/moody-docs/raw/`; extract from
  the `"schema":{"openapi":"3.0.2"` key. The knowledge base's `.gitignore` ignores
  everything, so plain `rg` misses these files — use `rg -uu`.
- `knowledge/sql scripts/portfolio_lines_of_business.sql`, `portfolio_states.sql`
- CIC design minutes `IRP_Workbench_Design_Minutes_7-16-26.md` (§II, §III, §X) and
  `IRP_Workbench_Design_Minutes_7-7-2026.md` (§4)
- Cheryl TeHennepe, email follow-up to Ben confirming portfolio creation behaviour
- Live probe run 2026-08-03 against tenant `prodmgmt` at
  `https://api-euw1.rms-ppe.com`, `irp-integration` branch
  `feature/filtered-subportfolio-creation` at `6c5f5d0`, every API count
  cross-checked against Data Bridge. Scripts and run log in
  `../../../irp-integration/probes/`.
