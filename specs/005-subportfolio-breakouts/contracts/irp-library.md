# Contract: `irp-integration` enhancements — selection read + `add_filtered_accounts` (R1)

**Repo**: `../../IRP/irp-integration` (sibling; develop via `make irp-local`, publish TestPyPI `0.2.2.devN`, pin before implement completes)

The workbench composes slice creation from **three calls** (R1, validated against `../knowledge/` + the RM LLM companion's conceptual flow): **select** the source portfolio's matching account IDs → **create** the empty portfolio (existing `create_portfolio()`, sync 201, duplicate-name guard) → **add** the selected accounts by ID. RM has no one-shot create-by-filter. Two library changes deliver the missing pieces.

## 1. Selection read — upgrade `search_accounts_by_portfolio`

```python
# irp_integration/portfolio.py  (PortfolioManager)
def search_accounts_by_portfolio(
    self, exposure_id: int, portfolio_id: int,
    filter: str = "", sort: str = "", limit: int = 100, offset: int = 0,
) -> List[Dict[str, Any]]:
    """Existing method gains the DOCUMENTED query params (filter, sort + pagination).
    Backward-compatible: all new params default to today's behavior."""

def search_accounts_by_portfolio_paginated(
    self, exposure_id: int, portfolio_id: int, filter: str = "",
) -> List[Dict[str, Any]]:
    """Fully-paged variant (the search_portfolios_paginated pattern).
    The breakout's selection read — MUST never truncate."""
```

- Wraps the existing `GET /platform/riskdata/v1/exposures/{exposureId}/portfolios/{id}/accounts` (documented: `filter`, `sort`; pagination behavior is spike item **U6** — limit/offset absent from the captured parameter list).
- **Known limitation feeding the spike (U1)**: the endpoint's documented filter property list is closed (account identity/name/branch/cedant/owner/producer/underwriter) — **no LOB, no state**. If the spike proves LOB/state predicates need the EDM-level search with `allowDeepFilters`, add `search_accounts(exposure_id, filter, allow_deep_filters=False, ...)` **at that point**, not speculatively.

## 2. Populate write — `add_filtered_accounts`

```python
# irp_integration/portfolio.py  (PortfolioManager)
def add_filtered_accounts(
    self,
    exposure_id: int,
    portfolio_id: int,
    *,
    marked_accounts: Optional[List[int]] = None,   # PRIMARY mode: explicit account IDs
    query_filter: str = "",                        # optimization mode (spike-conditional)
    select_all: bool = False,
    manage_existing_accounts: bool = False,
) -> Dict[str, Any]:
    """
    Add accounts to a portfolio via Risk Modeler's filtered-accounts operation.

    Wraps PUT /platform/riskdata/v1/exposures/{exposureId}/portfolios/{id}/filtered-accounts
    with body {selectAll, markedAccounts, queryFilter, manageExistingAccounts}.

    Returns:
        Dict[str, Any]: parsed body of the 200 response (may be empty — the doc
            specifies only "Accounts added to portfolio"). The operation is
            SYNCHRONOUS: 200 is the sole documented success status
            (developer.rms.com managefilteredaccounts, fetched 2026-07-30;
            no async/job/workflow path documented).

    Raises:
        IRPValidationError: exposure_id/portfolio_id invalid; OR no explicit intent —
            marked_accounts empty AND query_filter empty AND select_all is not True
            (an empty-intent call would add ALL accounts in the EDM; require the caller
            to pass select_all=True explicitly for that)
        IRPAPIError: RM rejects the request / portfolio not found / request fails;
            ALSO raised on any unexpected non-200 success status (e.g. a 202) —
            include the status and Location header in the message and fail loudly
            rather than normalize; if RM ever goes async here, revisit this
            contract deliberately
    """
```

- New constant: `constants.FILTERED_ACCOUNTS = '/platform/riskdata/v1/exposures/{exposureId}/portfolios/{id}/filtered-accounts'`.
- **`marked_accounts` is the primary mode** (R1): source scoping exact by construction, footgun unreachable, failures visible. `query_filter` stays supported for the spike-conditional optimization; its string construction stays in the **caller** (workbench gateway) — the library transports it verbatim (values double-quoted; `IN`-list grammar per RM Response Filtering).
- **Doc-verified 200-sync** ([developer.rms.com managefilteredaccounts](https://developer.rms.com/platform/reference/managefilteredaccounts), fetched 2026-07-30): 200 "Accounts added to portfolio" is the only success status; no async/job/workflow mention anywhere on the page. Legacy `/riskmodeler/v1|v2 filteredaccounts` variants are 202+workflow-job, but **legacy riskmodeler endpoints are out of scope by project direction (Platform endpoints only)** — they are not a contingency. An unexpected 202 raises (see above); no polling inside the method, ever.
- **Doc body-field semantics** (same fetch, verbatim where quoted): `selectAll` adds all accounts selected by `queryFilter` (all accounts in the EDM if no filter) and **overrides `markedAccounts`**; `manageExistingAccounts` — "If `true`, only existing accounts can be added to portfolio. All specified `markedAccount` values are ignored." That reads as a mode switch, not an upsert flag — keep the default `False`; its real behavior is the U2 sandbox probe.
- **Guard the empty-intent footgun**: `queryFilter:"" + selectAll:true` adds *every* account in the EDM; a call with no accounts, no filter, and no explicit `select_all=True` must be a validation error so a bug in the caller cannot silently clone the whole EDM into a slice.
- The Platform TOC also lists a `PATCH` *Manage accounts by portfolio*; if the spike's UI-traffic capture shows the UI drives the PATCH (or the PUT's already-member semantics are unsafe for adopt-then-populate, R7), wrap that operation instead/additionally — same sync contract (raise on unexpected non-200).
- Docstrings document the R1 findings (endpoint provenance, doc-verified sync response, deep-filter selection lead, batching) so the next library consumer doesn't re-research them. Add an entry to `docs/IRP_INTEGRATION_FOLLOWUPS.md` (workbench repo) recording both changes shipping in-house.

## Sandbox spike checklist (closes R1's unknowns; run before the worker is implemented; codified as `tests/irp/test_filtered_accounts.py`)

1. **U1 — selection query**: probe `GET .../exposures/{id}/accounts?filter=…&allowDeepFilters=true` with candidate tokens (`lobName`, `LOB Name`, `lineOfBusiness`, `admin1Name`, `admin1Code`); read the 400 texts. **Capture RM UI network traffic** for "Accounts grid → filter by LOB/state → select → Add to Portfolio" — the authoritative answer for both the selection query and the add call.
2. **U2 — add-step already-member semantics** (the 200-sync question is closed by the reference doc, fetched 2026-07-30): `create_portfolio` + `add_filtered_accounts(marked_accounts=[…])` in the sandbox confirms the doc in practice; then re-PUT the same IDs to learn already-member behavior, and probe `manageExistingAccounts=true` (its doc description — "markedAccount values are ignored" — is ambiguous; feeds R7 adopt-then-populate).
3. **U4 — state vocabulary**: names vs codes in the selection filter (feeds R6 — whether `portfolio_states.sql` gains an additive code/name pair).
4. **U5 — bucketing spot-check**: a mixed-LOB / multi-state account must appear in every matching selection and slice (disclosure wording depends on it).
5. **U6 — pagination**: page a >100-account portfolio through the paginated variant; verify completeness.
6. RM portfolio **name length limit** spot-check (feeds R4's 200-char cap).
7. *(Optimization only)* **U3 — queryFilter source scoping**: portfolio predicate under `allowDeepFilters`; pursue only if 1–2 make the one-call populate attractive.

**Spike outcomes gate the gateway seam**: `irp_gateway.create_sub_portfolio` is implemented only after 1–2 are answered; its contract ([data-access.md](data-access.md)) is written against the `SubPortfolioResult` dataclass so the selection-endpoint and marked-accounts-vs-queryFilter decisions never leak past the gateway.
