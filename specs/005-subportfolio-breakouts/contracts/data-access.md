# Contract: services & data access — breakout gate, slice plan, lineage (R3–R7)

**Modules**: `app/services/breakout_service.py` (NEW), `app/services/portfolio_service.py` (EDIT), `app/services/irp_gateway.py` (EDIT + fake mirror)

All SQL through `db.execute*` (Article 7). No RM/DataBridge call anywhere in this module's request-path functions (Article 11) — with one deliberate exception: `request_breakout`'s summary-freshness read (`search_portfolios` → `stampDate`), the Article 2 submit-time name-resolution pattern (clarified 2026-07-30).

---

## 1. `breakout_service` — the one testable home for the op (FR-002/003)

### Gate (Article 12 must-test)

```python
@dataclass(frozen=True)
class DimensionEligibility:
    dimension: str            # 'lob' | 'state' (breakout_dimension_kind.code)
    eligible: bool
    values: list[str]         # distinct values from the stored summary ([] when ineligible)
    reason: str | None        # analyst-facing: "exposure summary not available — run Sync",
                              # "only one line of business present", …

@dataclass(frozen=True)
class BreakoutGate:
    portfolio_eligible: bool  # EDM ready ∧ not deleted ∧ portfolio live
    reason: str | None
    dimensions: list[DimensionEligibility]
    in_flight: str | None     # dimension code of a live run_breakout_* job, if any

def evaluate_gate(edm_id: UUID, portfolio_id: UUID) -> BreakoutGate
```

Rule (R5): *EDM exists ∧ not deleted ∧ `status == 'ready'` ∧ portfolio exists ∧ not deleted*; per dimension: *summary present ∧ ≥ 2 distinct values*. Reads `irp_edm`, `irp_portfolio` (incl. defensive `exposure_detail.summary` parse — `lines_of_business` / `states`), and live `rwb_job` rows (`run_breakout_*` for this portfolio → `in_flight`, so the UI can show "breakout running" instead of the action). Pure DB reads; unit-tested as a truth table.

### Slice plan (pure function — shared by preview and worker, R4/R5)

```python
@dataclass(frozen=True)
class SlicePlan:
    value: str                # display value (stored as breakout_value)
    name: str                 # deterministic, collision-suffixed, ≤ 200 chars
    exists: bool              # a live lineage row already matches (idempotent re-run)
    # R6: gains a filter_value field iff the spike shows the selection token differs
    # from the stored display value (state name vs code)

def build_slice_plan(source_name: str, values: Sequence[str],
                     existing_names: Collection[str],
                     existing_slice_values: Collection[str]) -> list[SlicePlan]
```

- `name = f"{source_name} - {value}"` trimmed to ≤ 200; collisions against `existing_names` **and** earlier planned names get ` (2)`, ` (3)` … (lowest free suffix). Deterministic: same inputs → same plan.
- Sorted by value (stable preview ordering).
- No I/O — callers supply the current portfolio names and existing slice values.

### Enqueue (confirm POST path)

```python
def request_breakout(edm_id: UUID, portfolio_id: UUID, dimension: str, actor_id: UUID) -> UUID | None
```

Re-evaluates the gate (raise/refuse on failure — router maps to 409 + re-rendered fragment); then verifies **summary freshness** (FR-002a): `irp_gateway.fetch_portfolio_stamp(...)` (a `search_portfolios` read — the one RM call permitted on this path, Article 2 submit-time name resolution) must equal the `stampDate` captured with the stored summary at backfill; mismatch, missing stamp, or RM unreachable → refusal with reason ("portfolio data changed in Risk Modeler since the last sync — Sync the EDM, then retry" / "couldn't verify freshness") and **no `rwb_job` row is created**; then `ensure_pending_rwb_job(requestor_type='irp_portfolio', requestor_id=portfolio_id, rwb_job_type=f'run_breakout_{dimension}', input_data={edm_id, portfolio_id, dimension, actor_id})` — idempotent per (portfolio, dimension); revives a terminal row on analyst re-request; returns `None` when a live job already exists (UI: "already running"). Business-event log: `"breakout %s requested for portfolio %s by analyst %s (n_slices=%d)"`.

### Worker-side plan recompute + outcome assembly

```python
def compute_plan_for_run(portfolio_id: UUID, dimension: str) -> tuple[PortfolioCtx, list[SlicePlan]]
def summarize_outcomes(slices: list[SliceOutcome]) -> dict   # → rwb_job.output_data (data-model §4)
```

`compute_plan_for_run` re-reads the stored summary + current names inside the worker — identical pure function, fresh inputs (R5 decision: `input_data` carries no value list).

## 2. `portfolio_service` — lineage writes & reads (R3)

```python
def insert_slice(edm_id, *, name, irp_id, source_portfolio_id,
                 dimension_code, value, actor_id) -> UUID
def adopt_slice(edm_id, *, name, irp_id, source_portfolio_id,
                dimension_code, value, actor_id) -> UUID    # same write, 'adopted' logging
def find_slice(source_portfolio_id, dimension_code, value) -> Row | None   # live rows only
```

- Single write path enforces the integrity rule: lineage columns set together; source in same EDM; `inserted_by = actor_id` (first population of that column on this table).
- Insert relies on the filtered unique index for slice uniqueness and `uq_irp_portfolio_edm_irp` for id uniqueness; a race duplicate surfaces as a constraint violation → caught and treated as `skipped_existing`.
- `list_portfolios(edm_id)` (EDIT): LEFT JOIN self on `source_portfolio_id` → adds `source_name`, `breakout_dimension_code`, dimension `label` (join `breakout_dimension_kind`), `breakout_value` to each row for the badge; ordering unchanged (name) — grouping/indent is a display concern.

## 3. `irp_gateway` — the one RM write seam (fake mirrors it)

```python
@dataclass(frozen=True)
class SubPortfolioResult:
    portfolio_irp_id: str        # RM portfolioId (from create step)
    account_count: int           # accounts selected & added (per-slice outcome detail)
    # (no populate-job fields: the filtered-accounts PUT is doc-verified 200-sync —
    #  the library raises on an unexpected 202, see irp-library.md)

def create_sub_portfolio(*, edm_name: str, exposure_irp_id: str, source_portfolio_irp_id: str,
                         name: str, dimension: str, filter_value: str) -> SubPortfolioResult
def populate_sub_portfolio(*, exposure_irp_id: str, portfolio_irp_id: str,
                           source_portfolio_irp_id: str, dimension: str,
                           filter_value: str) -> SubPortfolioResult   # adopt-then-populate re-entry (R7)
def find_portfolio_by_name(exposure_irp_id: str, name: str) -> PortfolioHit | None   # adopt-by-name (R7)
def fetch_portfolio_stamp(exposure_irp_id: str, portfolio_irp_id: str) -> str | None  # FR-002a freshness read
```

- `create_sub_portfolio` composes the R1 three-call sequence behind one seam: **select** the source portfolio's matching account IDs (`search_accounts_by_portfolio_paginated` with the dimension filter — never the unpaged read) → **create** (`create_portfolio`, existing) → **add** (`add_filtered_accounts(marked_accounts=…)`, new — [irp-library.md](irp-library.md)). The selection-endpoint choice (portfolio-accounts filter vs EDM-level deep-filter search) and any queryFilter optimization (spike outcomes U1/U3) live **here**, behind the dataclass; filter strings built with `json.dumps` quoting (module convention).
- **Zero accounts selected** (stored summary drifted from RM) → a distinct gateway error **before anything is written to RM**; the worker records the slice as failed with that reason — no empty portfolio is created.
- `populate_sub_portfolio` re-runs select+add against an **adopted** portfolio (R7 adopt-then-populate healing); safe only per U2's already-member semantics.
- Errors surface as gateway exceptions per existing convention; a duplicate-name failure from the create step is a **distinct** error type so the worker can branch to adopt-by-name.
- **Confirm the wheel signatures against the active wheel (`make irp-status`) before implementing** (pre-release discipline).
- `fetch_portfolio_stamp` wraps the **existing** `search_portfolios` (no library change; `stampDate` passes through in the response — validated 2026-07-30 as an updated-at equivalent). Deliberately not named `get_*` (the architecture guard greps for web-layer `get_*` IRP calls; this one is request-path-legal). The spec-004 `backfill_edm_detail` worker gains the matching capture: it stores the portfolio's `stampDate` in `exposure_detail` alongside the summary, read **before** the DataBridge read so the stamp is conservative.
- `tests/unit/fakes/fake_irp.py` mirrors: `create_sub_portfolio` (duplicate-name behavior, seedable failures and zero-selection per slice, account counts), `populate_sub_portfolio`, `find_portfolio_by_name`, `fetch_portfolio_stamp` (seedable stamp per portfolio).

## 4. Unit-test surface (Article 12)

- `test_breakout_gate.py` — gate truth table: EDM status × deleted × summary present/absent/malformed × 0/1/2+ values × in-flight job. Plus the confirm-path freshness check: stamp match → enqueue; mismatch / missing stored stamp / gateway error → refusal, **no job row written** (fake stamp seeded per case).
- `test_breakout_plan.py` — naming determinism, collision suffixing (existing + intra-plan), 200-char trim, `exists` marking, ordering, blank-value lists absent by construction (summary SQL scrubs them — the *disclosure* is UI copy, not plan logic).
- `test_snapshot_upsert.py` / `test_edm_detail_rollup.py` (EDIT) — lineage-aware list read model; slices with NULL `exposure_detail` render the pending state.
- `insert_slice`/`adopt_slice` integrity rules + constraint-race handling.
