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
    """FR-030/FR-031/R8: the RDM's broker analyses (irp_analysis rows, rdm_id set),
    GROUPED BY rdm_id so an analysis applied across M EDMs is shown ONCE (dedup on
    rdm_id; the M pair-rows are handles). Each carries its parsed settings_metadata
    (missing/partial fields → blank, never error) and is_group (FR-035). Broker vs own
    is derived from rdm_id (§6) — this returns broker only (rdm_id set). No scoping."""

def analysis_counts(*, package_id: UUID | None = None,
                    edm_id: UUID | None = None) -> AnalysisCounts:
    """FR-050: populated analysis counts for the package card + EDM detail (spec 003
    D5 rendered these EMPTY; this un-empties them from the captured irp_analysis rows)."""
```

## `edm_service` (EDIT — add the detail read model)

```python
def get_edm_detail(edm_id: UUID) -> EdmDetail | None:
    """The redesigned EDM detail page's single read (R6). Returns:
      • header — the EDM's light context from existing irp_edm columns: name, status,
        as_of, source_file_path, identifiers (irp_id / created_by_irp_job_irp_id),
        portfolio_count. MUST NOT include cedant or line of business (FR-011).
      • portfolios — portfolio_service.list_portfolios(edm_id) (the PRIMARY content, US1).
      • aggregate — portfolio_service.aggregate_exposure(portfolios) (US4; None → pending).
      • treaties — treaty_service.list_treaties(edm_id) (US2).
    When no per-portfolio snapshot exists (pre-capability / pending / failed backfill),
    portfolios/aggregate/treaties render a graceful empty state and the header still
    displays (FR-017). None only if the EDM itself does not exist (→ router 404)."""
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
- `treaty_service.build_treaty_workbook` — a valid `.xlsx` over the treaty set (union of columns), built from stored detail with no gateway call (`test_treaty_export`).
- `edm_service.get_edm_detail` / `package_sync_service.get_package_cards` — graceful empty when unbackfilled; header always renders; per-EDM aggregate line + populated analysis counts present.
- No row scoping on any list/read (Article 6 / `test_no_scope` pattern).

SQL-Server tier: the idempotent detail upsert overwrites in place under the real driver (data-model §8).
