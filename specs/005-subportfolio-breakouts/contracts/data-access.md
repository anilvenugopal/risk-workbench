# Contract: services & data access — breakout gate, approved plan, lineage (R3–R7, R10)

**Modules**: `app/services/breakout_service.py` (NEW), `app/services/portfolio_service.py` (EDIT), `app/services/irp_gateway.py` (EDIT + fake mirror)

All SQL through `db.execute*` (Article 7). No RM/DataBridge call anywhere in this module's request-path functions (Article 11) — with one deliberate exception: `request_breakout`'s summary-freshness read (`search_portfolios` → `stampDate`), the Article 2 submit-time name-resolution pattern.

*(Revised 2026-08-03 after the probe run. Three things this file used to specify are now wrong and are corrected below: the worker recomputed the plan from the stored summary — AGENTS.md rule 8 forbids it, the confirmed plan is persisted and executed (T-10/R10); adoption resolved on the portfolio name — it resolves on `portfolioNumber` (T-07/P-11); names were capped at 200 characters — Risk Modeler's limit is 40, with a separate 20-character number (W-2/W-13).)*

---

## 1. `breakout_service` — the one testable home for the op (FR-002/003)

### Gate (Article 12 must-test)

```python
@dataclass(frozen=True)
class BreakoutValue:
    value: str                # the selection filter value, verbatim: Admin1Code | LOB name (P-12)
    label: str | None         # Admin1Name where the EDM has it; None for lob and un-geocoded state
    accounts: int             # source accounts carrying this value (FR-007 numerator)

@dataclass(frozen=True)
class DimensionEligibility:
    dimension: str            # 'lob' | 'state' (breakout_dimension_kind.code)
    label: str                # breakout_dimension_kind.label
    noun: str                 # analyst-facing noun, resolved ONCE — the modal reads it
    eligible: bool
    values: list[BreakoutValue]   # from the stored summary ([] when ineligible)
    reason: str | None        # analyst-facing: "exposure summary not available — run Sync",
                              # "only one line of business present", …

@dataclass(frozen=True)
class BreakoutGate:
    portfolio_eligible: bool  # EDM ready ∧ not deleted ∧ portfolio live ∧ no refresh in flight
    reason: str | None
    dimensions: list[DimensionEligibility]
    in_flight: str | None     # dimension code of a live run_breakout_* job, if any
    refresh_in_flight: bool   # a backfill_edm_detail for this EDM is pending|running (P-16)
    summary_as_of: str | None # the summary this preview renders from; echoed into the confirm (FR-002b)
    account_total: int | None                  # summary.account_total
    coverage: dict[str, DimensionCoverage]     # summary.breakout_coverage per dimension code
    # The two rows the gate read, carried so no caller re-reads them — one
    # instant's view of the same rows the eligibility decision was made from:
    rows_live: bool           # EDM row and source portfolio row both live (the modal's 404 test)
    edm_irp_id: str | None    # irp_edm.irp_id — the RM exposureId
    source_name: str | None
    source_irp_id: str | None # the source portfolio's RM portfolioId
    stored_stamp: str | None  # exposure_detail.stamp_date — the FR-002a comparison anchor

def evaluate_gate(edm_id: UUID, portfolio_id: UUID) -> BreakoutGate
```

Rule (R5): *EDM exists ∧ not deleted ∧ `status == 'ready'` ∧ portfolio exists ∧ not deleted ∧ no `backfill_edm_detail` for the EDM pending or running*; per dimension: *summary carries `breakout_values` ∧ ≥ 2 distinct values*. Reads `irp_edm`, `irp_portfolio` (defensive `exposure_detail.summary` parse — `breakout_values[dimension]`, data-model §5), and live `rwb_job` rows — `run_breakout_*` for this portfolio → `in_flight`, so the UI shows "breakout running" instead of the action; `backfill_edm_detail` for the EDM → `refresh_in_flight`, disabled-with-reason because that job rewrites the summary the preview reads (P-16, the condition `edm_service.sync_detail` already applies to itself). Pure DB reads; unit-tested as a truth table.

A summary with no `breakout_values` key reads as **absent**, not empty. Every summary written before this iteration lacks it, and its `states` list holds a mixed vocabulary of state names and codes that must never be used as filter values (P-12/R11) — so the reason points at the existing per-EDM Sync, and there is no fallback to `states`.

### Approved plan (pure function — the preview list and the persisted plan are the same list, R4/R10)

```python
@dataclass(frozen=True)
class SubPortfolioPlan:
    value: str                # selection filter value; stored as breakout_value
    label: str | None         # display only, never a filter input
    name: str                 # ≤ 40 chars: source truncated, value whole, collision-suffixed (P-11)
    number: str               # ≤ 20 chars: P{source RM id}-{S|L}-{token}, hash-tailed when long
    accounts: int             # previewed count from the summary (FR-006)
    exists: bool              # a live lineage row already matches (idempotent re-run view)

def build_breakout_plan(*, source_name: str, source_portfolio_irp_id: str, dimension: str,
                        values: Sequence[BreakoutValue],
                        existing_names: Collection[str],
                        existing_values: Collection[str]) -> list[SubPortfolioPlan]
```

- **Name** = `f"{source_name} - {value}"` inside Risk Modeler's 40-character limit: the **value is kept whole** and the source name absorbs the truncation, with room reserved for a collision suffix (R4). Collisions against `existing_names` **and** earlier planned names take the lowest free ` (2)`, ` (3)` … suffix.
- **Number** = the source portfolio's RM id, a dimension letter (`S`/`L`), and the value token, ≤ 20 characters with a hash tail when the token is too long. Composed only from inputs that do not change between preview and re-run — which is why adoption resolves on it and not on the name (P-11).
- **Description** (passed to RM, not stored here) names source portfolio, dimension, and value **in full and untruncated** — FR-010, so nothing the 40-character name drops is lost outright.
- Deterministic: same inputs → same plan. Sorted by value (stable preview ordering).
- No I/O — callers supply the current portfolio names and existing breakout values.

### Overlap statement (FR-007 / P-13, revised 2026-08-05)

```python
@dataclass(frozen=True)
class DimensionCoverage:
    covered: int                # source accounts carrying at least one value
    multi_value: int            # source accounts carrying MORE THAN ONE value

@dataclass(frozen=True)
class Overlap:
    account_total: int | None   # summary.account_total (the denominator)
    summed: int                 # Σ accounts over the dimension's values — MEMBERSHIPS, not accounts
    covered: int | None         # accounts landing in ≥ 1 sub-portfolio (SC-002)
    uncovered: int | None       # account_total − covered: accounts landing in none (FR-007b)
    repeats: int | None         # accounts landing in MORE THAN ONE sub-portfolio (FR-007a)
    partition: bool             # no repeats AND no uncovered accounts

def compute_overlap(values: Sequence[BreakoutValue], account_total: int | None,
                    coverage: DimensionCoverage | None) -> Overlap
```

Both figures are read from `summary.breakout_coverage[dimension]` (data-model §5), where the coverage scripts measured them per account. Neither is derived from `summed`: that counts memberships, so an account carrying three values contributes three, while an account carrying none contributes nothing yet still counts in `account_total` — the two errors cancel, and `summed − account_total` can read as a clean partition for a portfolio where most accounts land in no sub-portfolio at all. `summed` stays on the dataclass as what it is and is not rendered. Absent coverage yields `repeats=None` and the preview falls back to the qualitative disclosure alone, as a missing `account_total` already does (data-model §6).

### Enqueue (confirm POST path)

```python
def request_breakout(edm_id: UUID, portfolio_id: UUID, dimension: str,
                     summary_as_of: str, actor_id: UUID) -> UUID | None
```

Five steps, in order; each gates the next, and **no `rwb_job` row exists until all five pass**:

1. **Gate re-check** — `evaluate_gate` server-side (raise/refuse; the router maps to 409 + re-rendered fragment). Steps 3 and 4 read the source portfolio's stamp, RM id, and name **off the returned gate**, so the whole confirm decides from the rows that one read loaded; `modal_context` does the same. Neither re-reads them.
2. **Summary-unchanged check (FR-002b)** — the gate's current `summary_as_of` must equal the `summary_as_of` the preview carried. A detail refresh that landed since the preview rendered changes the value set and the account counts the analyst judged from, and FR-002a cannot see it (a refresh that leaves the RM portfolio untouched writes back an equal `stampDate`). Mismatch → refusal, re-rendered preview, no job row.
3. **Freshness check (FR-002a)** — `irp_gateway.fetch_portfolio_stamp(...)` (a `search_portfolios_paginated` read, the one RM call permitted on this path) must equal the `stampDate` captured with the stored summary at backfill. Mismatch, missing stored stamp, or RM unreachable → refusal with reason ("portfolio data changed in Risk Modeler since the last sync — Sync the EDM, then retry" / "couldn't verify freshness").
4. **Build and persist the approved plan** — `build_breakout_plan(...)` runs here, once, and its output goes into `input_data["plan"]` (data-model §4). Composed from the same stored summary and the same naming rule the preview used, and steps 1–2 have established that summary has not moved — so the values, labels, account counts, and numbers match the preview exactly. A collision suffix can still differ, because suffixing reads portfolio names the preview did not fix; the number, not the name, is the identity, which is what makes that harmless (P-14 / FR-006b). From here the plan is authoritative and the worker executes it (AGENTS.md rule 8 / R10). Each persisted entry carries value, label, name, number, **and the previewed account count**, so what the analyst saw stays comparable against what ran.
5. **Enqueue** — `ensure_pending_rwb_job(requestor_type='analyst_request', requestor_id=portfolio_id, rwb_job_type=f'run_breakout_{dimension}', input_data={edm_id, portfolio_id, dimension, actor_id, plan})`. `analyst_request` is the already-seeded requestor-type code the other analyst-triggered enqueues use; `requestor_id` is the source portfolio, which the column allows (no DB FK — its target varies by requestor type). Idempotent per (portfolio, dimension); revives a terminal row on analyst re-request; returns `None` when a live job already exists (UI: "already running").

Business-event log: `"breakout %s requested for portfolio %s by analyst %s (n_sub_portfolios=%d)"`.

### Worker-side plan load + outcome assembly (R10)

```python
def load_approved_plan(input_data: dict) -> list[SubPortfolioPlan]
def summarize_outcomes(outcomes: list[SubPortfolioOutcome]) -> dict   # → rwb_job.output_data (data-model §4)
```

`load_approved_plan` parses `input_data["plan"]` and reads nothing else — not the stored summary, not the current portfolio names, and it never re-suffixes. Collision suffixing depends on the portfolio names present in the EDM, which the run itself changes, so a recomputed name can differ from the one the analyst approved (T-10). An empty or unparseable plan fails the job with a recorded reason and creates nothing.

## 2. `portfolio_service` — lineage writes & reads (R3)

```python
def insert_generated(edm_id, *, name, irp_id, source_portfolio_id,
                     dimension_code, value, actor_id) -> UUID
def adopt_generated(edm_id, *, name, irp_id, source_portfolio_id,
                    dimension_code, value, actor_id) -> UUID    # same write, 'adopted' logging
def find_generated(source_portfolio_id, dimension_code, value) -> Row | None   # live rows only
```

- Single write path enforces the integrity rule: the three lineage columns are set together; the source portfolio is in the same EDM; `inserted_by = actor_id` (first population of that column on this table).
- **Lineage is stamped, never reassigned.** A row for `(edm_id, irp_id)` that carries no lineage is claimed in place (a backfill enumerated the Risk Modeler portfolio first). A row that already carries a **different** `(source, dimension, value)` raises — the write cannot move a generated portfolio out of the traceability of the value it was created for, so the worker records that sub-portfolio as failed instead. That outcome depends on the worker's per-entry guard covering the **write**, not only the Risk Modeler calls: worker-poller.md step 4 places it around the whole entry for exactly this raise.
- No `portfolio_number` is written — data-model §1 keeps it out of the schema because it is recomputable from the source RM id, dimension, and value.
- Insert relies on the filtered unique index `uq_irp_portfolio_breakout` for lineage uniqueness and `uq_irp_portfolio_edm_irp` for id uniqueness; a race duplicate surfaces as a constraint violation → caught and treated as `skipped_existing`.
- `list_portfolios(edm_id)` (EDIT): LEFT JOIN self on `source_portfolio_id` → adds `source_name`, `breakout_dimension_code`, dimension `label` (join `breakout_dimension_kind`), `breakout_value` to each row for the badge; ordering unchanged (name) — grouping/indent is a display concern.

## 3. `irp_gateway` — the one RM write seam (fake mirrors it)

Selection is **hoisted out of the per-sub-portfolio loop**: one portfolio-scoped DataBridge query per run resolves every value at once (R1, revised 2026-08-05 — the paginated REST selection could not complete on a 248,000-account portfolio, W-20). The script mirrors the summary script's joins, so the values filtered are byte-identical to the stored summary the plan was approved from; `ACCGRPID` is the id `manage_portfolio_accounts` accepts as `accountId`. The gateway resolves and caches the EDM's physical `databaseName`, which is why the seam takes `edm_name`.

```python
@dataclass(frozen=True)
class BreakoutSelection:
    accounts_by_value: dict[str, list[int]]   # value → source account ids matching it
    errors_by_value: dict[str, str]           # value → reason, for reads that failed on their own

def select_breakout_accounts(*, edm_name: str, exposure_irp_id: str,
                             source_portfolio_irp_id: str,
                             dimension: str, values: Sequence[str]) -> BreakoutSelection

@dataclass(frozen=True)
class SubPortfolioResult:
    portfolio_irp_id: str        # RM portfolioId (from the create step)
    account_count: int           # read back from RM and compared against the ids sent —
                                 # never the `completed` figure from the add call (W-9)

def create_sub_portfolio(*, edm_name: str, exposure_irp_id: str, name: str, number: str,
                         description: str, account_ids: Sequence[int]) -> SubPortfolioResult
def populate_sub_portfolio(*, exposure_irp_id: str, portfolio_irp_id: str,
                           account_ids: Sequence[int]) -> SubPortfolioResult   # adopt-then-populate (R7)
def find_portfolio_by_number(exposure_irp_id: str, number: str) -> list[PortfolioHit]  # adopt (R7/W-17)
def fetch_portfolio_stamp(exposure_irp_id: str, portfolio_irp_id: str) -> str | None   # FR-002a
```

- `select_breakout_accounts` runs one parameterized DataBridge query — no filter grammar and no URL chunking, both of which belonged to the REST selection W-20 retired on 2026-08-05. It resolves and caches the EDM's `databaseName`, substitutes `{{ portfolio_id }}`, and maps the `Value`/`AccountId` rows into per-value id lists. A wrong column name returns a plausible empty result rather than an error (W-15) — unit-tested against recorded rows.
- **The selection read is all-or-nothing**: the single DataBridge query failing raises, and the worker fails the job before anything is created (the W-14 never-proceed-on-an-unprovable-list rule, enforced by construction). `errors_by_value` stays on the seam for implementations that can fail per value — the worker fails such an entry with no create call.
- **A value with an empty id list is returned as empty**, not as an error; the worker turns it into a zero-match failure (FR-008) with no create call made, so no empty portfolio reaches Risk Modeler.
- `create_sub_portfolio` composes create + add + verify: `create_portfolio(edm_name, name, number, description)` (synchronous 201) → `manage_portfolio_accounts(accounts_to_add=chunk)` per 1,000-id chunk (synchronous 200) → a DataBridge member count compared against `account_ids` (the paginated REST read-back cannot verify past 100,000 accounts, W-20). `add_filtered_accounts` is deliberately not used — it returns `{}` (irp-library.md).
- **`portfolio_number` is always passed explicitly.** Omitting it makes RM default the number to the name, which then overruns the number's own 20-character cap (W-13).
- A duplicate-name failure from the create step surfaces as a **distinct** error type so the worker can branch to adoption. Class alone is not enough: `IRPValidationError` also covers an over-long name and an over-long number (W-10).
- `find_portfolio_by_number` returns **every** hit, not the first — FR-011 requires the worker to fail that sub-portfolio rather than adopt an arbitrary one when more than one portfolio carries the number. Filters on `portfolioNumber` via `search_portfolios_paginated`; numbers are unique only within an exposure, which the exposure-scoped search covers (W-17). Every exposure-scoped lookup in the gateway uses the paginated read, including the duplicate-name verification: reading only the first page would report a name held further down as free, and the entry would fail instead of adopting.
- `populate_sub_portfolio` re-runs the add against an **adopted** portfolio (R7 adopt-then-populate healing). Re-adding already-member accounts is safe and returns `completed 0` (W-9), so the heal runs unconditionally.
- **The comparison decides success: a member count that does not equal the ids sent raises**, in both the create path and the adopt-then-populate heal. FR-008 asks for exactly the selected accounts, so the worker fails that sub-portfolio and writes no lineage row rather than reporting a short or over-populated portfolio as created. Nothing is deleted (P-07); the re-run adopts the portfolio on its number and re-adds, which heals a **short** membership. An **over-populated** one it cannot heal — re-adding never removes a member — so that entry fails on every run until the extra accounts are removed in Risk Modeler, which is what the failure reason says.
- **A member-count read returning no rows raises rather than counting zero.** The script is a `COUNT`, so it always returns one row; no rows means the read came back empty, and treating that as an empty portfolio would blame the add for a DataBridge failure.
- `fetch_portfolio_stamp` wraps the **existing** `search_portfolios_paginated` (no library change; `stampDate` passes through in the response — confirmed as Risk Modeler's updated-at equivalent). It takes `exposure_irp_id` as a string like the other four and coerces internally, so no caller has to pick. Deliberately not named `get_*`: the architecture guard greps for web-layer `get_*` IRP calls, and this one is request-path-legal. The spec-004 `backfill_edm_detail` worker gains the matching capture — it stores the portfolio's `stampDate` in `exposure_detail` alongside the summary, read **before** the DataBridge read so the stamp is conservative.
- **Confirm the signatures against the active wheel (`make irp-status`) before implementing** (pre-release discipline).

### Extended summary builder (spec-004 edit, worker-side)

The gateway's per-portfolio summary builder gains `breakout_values` (per dimension: value, label, account count), `account_total`, and `breakout_coverage` (per dimension: `covered`, `multi_value`), from the DataBridge scripts in plan.md's Material changes table. `_COVERAGE_SCRIPTS` keys the two coverage scripts by dimension code beside `_SELECTION_SCRIPTS`, so no dimension code appears in SQL. Shape in data-model §5; measured cost of the pre-coverage set +1.44s on the backfill job (R11).

### CI fake

`tests/unit/fakes/fake_irp.py` mirrors: `select_breakout_accounts` (seedable per-value id lists, empty selections, and per-value read errors), `create_sub_portfolio` (duplicate-name raise, seedable failures, read-back counts), `populate_sub_portfolio`, `find_portfolio_by_number` (seedable 0/1/many hits), `fetch_portfolio_stamp` (seedable stamp per portfolio), and the extended summary builder (`set_exposure_summary` seeds `breakout_coverage` per dimension, or omits it for the pre-revision summary).

## 4. Unit-test surface (Article 12)

- `test_breakout_gate.py` — gate truth table: EDM status × deleted × `breakout_values` present/absent/malformed × 0/1/2+ values × in-flight breakout job × in-flight `backfill_edm_detail` (P-16), including a pre-iteration summary (has `states`, no `breakout_values`) reading as absent. Plus the confirm path: stamp match and `summary_as_of` match → plan persisted and job enqueued; stamp mismatch / missing stored stamp / gateway error → refusal; `summary_as_of` mismatch → refusal (FR-002b, and it must refuse even when the stamp still matches — the case FR-002a cannot see); every refusal writes **no job row** (fake stamp seeded per case).
- `test_breakout_plan.py` — naming determinism; the 40-character name budget (source truncated, value whole, room reserved for the suffix) and the 20-character number budget (hash tail whenever the value is not already uppercase alphanumerics that fit, so `AB`, `A-B`, `a b`, and ` AB` keep four distinct numbers); collision suffixing against existing and intra-plan names, casefolded; `exists` marking; ordering; a dimension with no registered number letter refusing to compose one; the overlap statement (clean partition, heavy overlap, absent coverage, absent `account_total`, **no repeats but uncovered accounts is not a partition**, and an account in three values counted once). Blank values never appear in the value list — the summary SQL scrubs them; how many accounts they cost is the `uncovered` count.
- `load_approved_plan` — executes what was persisted: a plan whose names no longer match what a recompute would produce still runs verbatim; empty/unparseable plan fails the job.
- `test_breakout_page_state.py` — the FR-012 read model derived from job rows: live-flight progress, per-entry error lines, the job-level fallback line when the run failed before its loop, both dimensions of one portfolio accumulating, per-portfolio keying, the banner from the newest terminal run, `filling_in` from the pending follow-up, a settled successful run showing no banner, and unparseable `output_data` degrading to no lines.
- `test_snapshot_upsert.py` / `test_edm_detail_rollup.py` (EDIT) — lineage-aware list read model; generated portfolios with NULL `exposure_detail` render the pending state.
- `insert_generated`/`adopt_generated` integrity rules + constraint-race handling, including the refusal to move a row from one `(source, dimension, value)` to another and the re-adoption of the key it already holds.
- `test_irp_gateway.py` — the read-back comparison: a short membership, an over-populated adopted portfolio, and a member-count read returning no rows all raise, so `test_run_breakout_worker.py` sees the entry fail with no lineage row. Plus the summary builder mapping both coverage scripts.
- `test_architecture_guards.py` — every seeded `breakout_dimension_kind` code carries a number letter, a noun, a selection script, and a coverage script, and every registered script file exists.
