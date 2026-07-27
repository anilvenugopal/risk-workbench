# Contract — Data-Access / Service Layer (Iteration 3)

The developer-facing interface this iteration adds. Functions (not classes, matching the Iteration-1/2 services) across `app/services/`. Every function persists/reads through the `db/` **safe bound-parameter path** (Article 7); the backfill upserts use `db.get_connection("WORKBENCH")` + explicit `conn.begin()`. Signatures are the contract; types are illustrative Python.

**Risk Modeler is reached only through `app/services/irp_gateway.py`** (Article 11) — and only by the **worker** (backfill), never by these read services or the web layer. The read services below operate entirely on **stored** detail; the treaty Excel export builds from stored detail with **no** Risk Modeler call.

Shared typed errors — reuse `app/services/errors.py` (no new error types required this iteration; a missing entity → 404 in the router, a missing snapshot → a graceful empty state, not an error).

---

## `irp_gateway` — new read methods (worker-only)

See [worker-poller.md](worker-poller.md) for the full surface. Added to the `IRPGateway` protocol + free functions + the CI fake:

```python
def list_portfolios(*, edm_irp_id: int) -> list[PortfolioHit]: ...
def get_portfolio_exposure(*, portfolio_irp_id: int) -> ExposureDetail: ...
def search_treaties(*, edm_irp_id: int) -> list[TreatyDetail]: ...
def get_analysis_metadata(*, analysis_id: int) -> AnalysisMetadata: ...   # or enrich AnalysisHit
```
> Single-status/read only; `poll_*_to_completion` never wrapped (Article 11). Confirm signatures vs the active wheel before implementing (R1).
>
> **`AnalysisHit` must carry the exposure pointer (R9).** The existing `AnalysisHit` value object currently **drops** Risk Modeler's `exposureResourceId`; extend it (and the metadata payload) to carry `exposure_resource_id` + `exposure_resource_type` so the backfill can promote the portfolio pointer to `irp_analysis.exposure_resource_id` (only when type == `PORTFOLIO`). This is the sole gateway-shape change for the linkage — resolution to `irp_portfolio` is read-time in `analysis_service`, not in the gateway.

---

## `portfolio_service` (NEW)

```python
def upsert_portfolio_detail(*, edm_id: UUID, irp_id: str | None, name: str,
                            exposure_detail: dict, as_of: datetime,
                            conn=None) -> None:
    """Worker-side (backfill_edm_detail). Idempotent upsert on UNIQUE(edm_id, irp_id)
    (fallback (edm_id, name)): insert the irp_portfolio row or OVERWRITE exposure_detail
    (JSON) + as_of in place — never a duplicate (R2/FR-004). exposure_detail is stored
    verbatim as the JSON snapshot."""

def list_portfolios(*, edm_id: UUID) -> list[PortfolioRow]:
    """Every portfolio of an EDM (read model), each with its parsed exposure_detail
    (or None → graceful empty). No row scoping (Article 6). Read-only — no create/
    edit/split (Iteration 4)."""

def aggregate_exposure(portfolios: list[PortfolioRow]) -> EdmAggregate | None:
    """Derive the EDM-aggregate (R4): sum location/account/policy counts + record
    volume; UNION perils/sub-perils; COMBINE geography (regions/states) + currency set;
    portfolio count. Pure function over the already-fetched snapshots (no DB, no RM).
    Returns None when no portfolio has a snapshot → the caller renders the pending
    state (FR-042/FR-043)."""
```

## `treaty_service` (NEW)

```python
def upsert_treaty_detail(*, edm_id: UUID, irp_id: str | None, name: str,
                         attributes: dict, as_of: datetime, conn=None) -> None:
    """Worker-side. Idempotent upsert on UNIQUE(edm_id, irp_id) (fallback (edm_id,
    name)): overwrite the attributes JSON snapshot + as_of in place (R2/FR-004)."""

def list_treaties(*, edm_id: UUID) -> list[TreatyRow]:
    """Every treaty on an EDM, each with its parsed attributes (full set) for the
    expand/collapse view (FR-020/FR-021). Read-only (FR-025). No scoping (Article 6)."""

def build_treaty_workbook(*, edm_id: UUID) -> bytes:
    """FR-024/R5: build a standard .xlsx (openpyxl) over the EDM's treaty set — one
    row per treaty, columns = UNION of attribute keys across the set (so a wide/
    heterogeneous set exports cleanly). Reads STORED detail only — no Risk Modeler
    call. Returns the workbook bytes for the router to stream as a download."""
```

## `analysis_service` (NEW)

```python
def list_broker_analyses(*, rdm_id: UUID) -> list[BrokerAnalysisGroup]:
    """FR-030/FR-031/R8 (RDM page): the RDM's broker analyses (irp_analysis rows, rdm_id
    set), GROUPED BY rdm_id so an analysis applied across M EDMs is shown ONCE (dedup on
    rdm_id; the M pair-rows are handles). Each carries its parsed settings_metadata
    (missing/partial fields → blank, never error), is_group (FR-035), and its RESOLVED
    portfolio (FR-036/R9 — see resolve_portfolio below; None → 'not linked'). Broker vs
    own is derived from rdm_id (§6) — broker only. No scoping."""

def list_edm_analyses(*, edm_id: UUID) -> list[BrokerAnalysisGroup]:
    """FR-037 (EDM page): the EDM's broker analyses (irp_analysis rows with this edm_id),
    GROUPED BY source rdm_id, each with its resolved portfolio + is_group + parsed
    settings_metadata. Feeds the EDM page's standalone RDM-grouped section AND the
    per-portfolio inline panels (bucket by resolved portfolio; is_group / unresolved rows
    stay OUT of every portfolio bucket — standalone only). No scoping."""

def resolve_portfolio(*, edm_id: UUID, exposure_resource_id: str | None,
                      is_group: bool) -> PortfolioRef | None:
    """FR-036/R9: the read-time linkage. Returns the owning irp_portfolio (id + name) for
    an analysis by matching exposure_resource_id against irp_portfolio.irp_id within the
    SAME edm_id. Returns None (→ rendered 'Group' when is_group, else '— not linked') when
    is_group, when exposure_resource_id is null (non-portfolio exposure), or when no
    portfolio in this EDM matches. Order-independent — correct whether the portfolio or the
    analysis backfilled first; no stored FK. Usually resolved in the same query as the
    list functions (LEFT JOIN irp_portfolio ON edm_id + irp_id); shown here as the contract."""

def analysis_counts(*, edm_id: UUID) -> AnalysisCounts:
    """FR-050: populated analysis counts for one EDM (spec 003 D5 rendered these
    EMPTY; this un-empties them from the captured irp_analysis rows). The package
    card renders them per EDM member — no package-level variant exists."""
```

## `edm_service` (EDIT — add the detail read model)

```python
def get_edm_detail(edm_id: UUID) -> EdmDetail | None:
    """The redesigned EDM detail page's single read (R6). Returns:
      • header — the EDM's light context from existing irp_edm columns: name, status,
        as_of, source_file_path, identifiers (irp_id / created_by_irp_job_irp_id),
        portfolio_count. MUST NOT include cedant or line of business (FR-011).
      • portfolios — portfolio_service.list_portfolios(edm_id) (the PRIMARY content, US1);
        each portfolio carries its linked analyses (bucketed from list_edm_analyses by the
        R9 resolution) for the inline expansion — a descriptive count + the mini-list (US3/FR-037).
      • analyses — analysis_service.list_edm_analyses(edm_id): the standalone RDM-grouped
        broker-analyses list, each with its resolved portfolio (US3/FR-037).
      • aggregate — portfolio_service.aggregate_exposure(portfolios) (US4; None → pending).
      • treaties — treaty_service.list_treaties(edm_id) (US2).
    When no per-portfolio snapshot exists (pre-capability / pending / failed backfill),
    portfolios/aggregate/treaties render a graceful empty state and the header still
    displays (FR-017). A portfolio with no linked analyses shows 'None'; group/unresolved
    analyses appear only in the standalone list, never a portfolio bucket. None only if the
    EDM itself does not exist (→ router 404)."""
```

> `get_edm(edm_id)` (existing) is unchanged and still used by the backfill worker and the recovery routes; `get_edm_detail` is the new richer read for the redesigned page.

## `package_sync_service` (EDIT)

```python
def get_package_cards(submission_id: UUID) -> list[PackageCard]:
    """EXTENDED: each EDM's package row now carries a per-EDM aggregate orientation
    line (FR-041) — total counts, portfolio count, perils, record volume — from the
    same portfolio_service.aggregate_exposure rollup (a graceful pending state when the
    EDM has no snapshot, FR-043); and the analysis counts render POPULATED (FR-050),
    no longer empty (spec 003 D5)."""
```

---

## Test obligations (Article 12 / references data-model §8)

Unit tier (SQLite + **fake IRP**):
- `portfolio_service.upsert_portfolio_detail` / `treaty_service.upsert_treaty_detail` — idempotent overwrite on the UNIQUE key (no dupes); JSON snapshot round-trips (`test_backfill_edm_detail`).
- `portfolio_service.aggregate_exposure` — correct sum/union/combine over multiple snapshots; `None` when no snapshot (`test_edm_detail_rollup`).
- `analysis_service.list_broker_analyses` — grouped by `rdm_id` (M-EDM analysis shown once); `settings_metadata` parsed; missing fields blank not error; `is_group` surfaced (`test_broker_analyses`).
- `analysis_service` portfolio linkage (R9/FR-036) — an analysis whose `exposure_resource_id` matches an `irp_portfolio.irp_id` in the same `edm_id` resolves to that portfolio; `is_group` → "Group"; null/unmatched/non-portfolio → "not linked"; resolution is **order-independent** (portfolio backfilled before *or* after the analysis); `list_edm_analyses` buckets linked analyses per portfolio and keeps group/unresolved rows standalone-only (`test_broker_analyses` / `test_edm_analyses`).
- `treaty_service.build_treaty_workbook` — a valid `.xlsx` over the treaty set (union of columns), built from stored detail with no gateway call (`test_treaty_export`).
- `edm_service.get_edm_detail` / `package_sync_service.get_package_cards` — graceful empty when unbackfilled; header always renders; per-EDM aggregate line + populated analysis counts present.
- No row scoping on any list/read (Article 6 / `test_no_scope` pattern).

SQL-Server tier: the idempotent detail upsert overwrites in place under the real driver (data-model §8).
