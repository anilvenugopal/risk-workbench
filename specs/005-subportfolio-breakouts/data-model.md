# Data Model: One-Click Portfolio Breakouts (Iteration 4)

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Research**: [research.md](research.md) (R3 lineage, R4 naming, R2 job shape, R10 persisted plan, R11 summary shape)

All relational changes are **WORKBENCH** (`rwb_workbench`) only, folded into the single `alembic/versions/0001_initial.py` revision (drop-create-seed dev strategy). EXPOSURE / LOSS untouched; DATABRIDGE never in schema scope. §5 adds a JSON content contract inside an existing column — no DDL.

---

## 1. `irp_portfolio` — three new lineage columns (R3)

The entity stays the thin identity record of DATA_MODEL §5, plus the spec-004 snapshot column, plus **breakout lineage**:

| Column | Type | New? | Notes |
|---|---|---|---|
| `id` | UNIQUEIDENTIFIER PK, default `NEWID()` | | |
| `edm_id` | UNIQUEIDENTIFIER NOT NULL, FK → `irp_edm.id` | | |
| `name` | NVARCHAR(256) NOT NULL | | generated names capped at **40** by the plan builder — Risk Modeler's own limit (R4) |
| `irp_id` | NVARCHAR(64) NULL | | RM `portfolioId`; written by the worker at creation or adoption |
| `exposure_detail` | NVARCHAR(MAX) NULL | | spec-004 JSON snapshot (§5); generated portfolios start NULL until `backfill_edm_detail` runs |
| `as_of` | DATETIME2 NULL | | |
| **`source_portfolio_id`** | UNIQUEIDENTIFIER NULL, FK → `irp_portfolio.id` | ✅ | self-reference; NULL = broker-arrived. Immediate source only — chained lineage walks the chain |
| **`breakout_dimension_code`** | NVARCHAR(32) NULL, FK → `breakout_dimension_kind.code` | ✅ | NULL iff `source_portfolio_id` IS NULL |
| **`breakout_value`** | NVARCHAR(256) NULL | ✅ | the value the **selection filter** uses, verbatim: `Admin1Code` for `state`, `LOBNAME` for `lob` (P-12). External exposure vocabulary → plain column per Article 3. Not a display string: the state *name* is a separate exposure attribute that is absent until geocoding and changes under the analyst's feet (R6) |
| `deleted_at` | DATETIME2 NULL | | soft delete (prune) |
| `inserted_at` / `updated_at` | DATETIME2 NOT NULL | | |
| `inserted_by` / `updated_by` | UNIQUEIDENTIFIER NULL, FK → `app_user.id` | | **now populated for generated rows** (the confirming analyst, carried to the worker in `input_data.actor_id`) — first writer to use these columns on this table |

**Constraints & indexes**

- Existing: `uq_irp_portfolio_edm_irp UNIQUE(edm_id, irp_id)`, `ix_irp_portfolio_edm_id` — unchanged.
- **New — the idempotency key (R7):**

  ```sql
  CREATE UNIQUE NONCLUSTERED INDEX uq_irp_portfolio_breakout
      ON irp_portfolio (source_portfolio_id, breakout_dimension_code, breakout_value)
      WHERE source_portfolio_id IS NOT NULL AND deleted_at IS NULL;
  ```

  One live generated portfolio per (source, dimension, value). Re-runs and double-submits hit this as a constraint, not a convention. (SQLite unit tier: SQLAlchemy emits the same filtered/partial unique index — supported since SQLite 3.8; the fixture engine qualifies.)
- **Integrity rule (service-enforced, not a CHECK):** the three lineage columns are set together or not at all; `source_portfolio_id` must reference a portfolio in the **same EDM**. Enforced in `portfolio_service.insert_generated/adopt_generated` (one write path), asserted in unit tests.

**Non-changes (deliberate):**

- No status column (Article 4), no `created_by_irp_job_irp_id` (both RM writes are synchronous, and job lineage stays the job's `input_data` — DATA_MODEL §5 rule), no filter-spec JSON column (R3 alternative rejected).
- **No `portfolio_number` column.** The generated number is a pure function of three values already on the row — the source portfolio's RM id, `breakout_dimension_code`, and `breakout_value` (R4) — so it is recomputable exactly whenever adoption needs it. The *name* is not recomputable that way, because collision suffixing reads other portfolios' names; that is why the name is persisted, both on this row and in the approved plan (§4).

## 2. `breakout_dimension_kind` — new kind table (Article 3)

Standard kind-table shape:

| Column | Type |
|---|---|
| `code` | NVARCHAR(32) PK |
| `label` | NVARCHAR(128) NOT NULL |
| `sort_order` | INT NOT NULL |

**Seed rows** (migration + `infra/scripts/seed_db.py` idempotent MERGE):

| code | label | sort_order |
|---|---|---|
| `lob` | Line of business | 10 |
| `state` | Geography (state) | 20 |

App code dispatches on `code` — which entry of `summary.breakout_values` to read, which selection read to run. `code` is also the key inside `breakout_values` (§5), so there is no second vocabulary to keep in step. Follow-on dimensions (complement, country) add rows here, not enum literals.

## 3. `rwb_job_type_kind` — two new seed rows (R2)

| code | label |
|---|---|
| `run_breakout_lob` | Portfolio breakout by line of business |
| `run_breakout_state` | Portfolio breakout by geography (state) |

Two types — not one — because the idempotent-enqueue key is `UNIQUE(requestor_type, requestor_id, rwb_job_type)`: with `requestor_type='irp_portfolio'`, `requestor_id=<source portfolio id>`, each dimension gets its own live-job slot per portfolio (a LOB and a state breakout on the same portfolio don't collide; a re-request of the same dimension revives the terminal row via `ensure_pending_rwb_job`). Both codes dispatch to the same worker body in `app/workers/portfolio_jobs.py` (loader convention: actor name == `rwb_job_type`).

## 4. `rwb_job` payload shapes for the breakout (no schema change)

Existing columns, new content contract. `input_data` **is the approved plan** — the worker executes it rather than recomputing it (constitution Art. 8 / R10).

```jsonc
// input_data — written at enqueue (confirm POST), exactly what the analyst saw
{
  "edm_id": "<uuid>",
  "portfolio_id": "<uuid>",          // == requestor_id (the SOURCE portfolio)
  "dimension": "lob" | "state",      // redundant with rwb_job_type; kept for the shared worker body
  "actor_id": "<uuid>",              // confirming analyst → generated rows' inserted_by
  "plan": [                          // one entry per sub-portfolio, in preview order
    {"value": "TX", "label": "TEXAS", "name": "usfl_commercial - TX", "number": "P1-S-TX"},
    {"value": "CA", "label": "CALIFORNIA", "name": "usfl_commercial - CA", "number": "P1-S-CA"}
  ]
}

// output_data — written at completion (per-sub-portfolio outcomes, FR-012/FR-015)
{
  "planned": 12,
  "created": 10,
  "adopted": 1,                      // existed in RM, resolved by portfolioNumber (R7)
  "skipped_existing": 0,             // lineage row already present (idempotent re-run)
  "failed": 1,
  "sub_portfolios": [
    {"value": "TX", "name": "usfl_commercial - TX", "number": "P1-S-TX",
     "outcome": "created", "irp_id": "431", "accounts": 220},
    {"value": "MT", "name": "usfl_commercial - MT", "number": "P1-S-MT",
     "outcome": "failed",  "error": "selection returned zero accounts"}
  ],
  "backfill_enqueued": true          // FR-013 mechanical follow-up
}
```

`accounts` in an outcome is the count actually populated, read back from Risk Modeler and compared against the selection — not the `completed` figure from the add call, which counts ids *newly* added and is legitimately 0 on a re-run (W-9).

Account ids are deliberately **not** in `input_data`: they are not what the analyst approved, they can legitimately change between confirm and run, and the freshness check (FR-002a) already refuses a confirm made against drifted data.

## 5. `irp_portfolio.exposure_detail.summary` — two new keys (R11)

The spec-004 JSON snapshot, written by `backfill_edm_detail`. No DDL — this is the content contract inside the existing `NVARCHAR(MAX)` column.

```jsonc
{
  "portfolio_name": "usfl_commercial",
  "total_tiv": 30437380495.0,
  "states": ["CA", "FL", "TX"],          // CHANGED: Admin1Code (was COALESCE(Admin1Name, Admin1Code))
  "lines_of_business": ["FLD Comm"],     // unchanged — the display lists
  "currencies": ["USD"],
  "account_total": 1701,                 // NEW: the overlap denominator (FR-007)
  "breakout_values": {                   // NEW: the enumeration source (FR-005)
    "state": [{"value": "TX", "label": "TEXAS", "accounts": 220}],
    "lob":   [{"value": "FLD Comm", "label": null, "accounts": 25}]
  }
}
```

- `breakout_values` is keyed by `breakout_dimension_kind.code`, so the gate, the preview, and the worker index it by dimension with no per-dimension branch.
- `label` is `Admin1Name` where the EDM has it and `null` otherwise — a display label only, never synthesized from the code (P-12). For `lob` the value is its own label, so the key is `null`.
- **Absence of `breakout_values` is the staleness signal.** Every summary written before this iteration lacks it, and its `states` list holds a mixed vocabulary of names and codes that must not be read as filter values. The gate treats a missing `breakout_values` as a missing summary and points at Sync (FR-002). No migration or backfill of existing snapshots is needed.
- Readers parse defensively, as the existing spec-004 readers do; an additive JSON change is spec-004-compatible.
- Also captured by the same backfill, alongside the summary: the portfolio's Risk Modeler `stampDate`, the FR-002a freshness anchor. Stored in `exposure_detail`, no new column.

Source scripts, all read-only and worker-side through `irp-integration` (Article 11): `portfolio_states.sql` (returns `Admin1Code`, `MAX(Admin1Name)`, account count, grouped and filtered on the code), `portfolio_lines_of_business.sql` (+ account count), and new `portfolio_account_total.sql`. Measured cost: **+1.44s** on the backfill job for the largest sandbox book (W-19).

## 6. Read models (derived, never stored)

- **Lineage-aware portfolio list** (`portfolio_service.list_portfolios`): each row gains `source_portfolio_id`, `source_name` (joined), `breakout_dimension_code` (+ label), `breakout_value` — for the row badge "↳ from *{source}* · {label}: *{value}*". Chained lineage shows the immediate source only.
- **Breakout eligibility** (`breakout_service`): computed per request from `irp_edm.status`, portfolio existence, parsed `exposure_detail.summary`, presence of `breakout_values`, and per-dimension distinct-value counts (≥ 2). Never cached, never stored (Article 2).
- **Overlap statement** (`breakout_service`): `Σ accounts over the dimension's values` versus `account_total`. Equal → the sub-portfolios partition the source; greater → the difference is the number of repeat memberships, stated in the preview with the note that exposure inflation can exceed account inflation (FR-007). Absent `account_total` → the qualitative disclosure alone.

## 7. DATA_MODEL.md propagation (part of the R9 doc pass)

- §5 `irp_portfolio` block: add the three lineage columns + the "immediate source only" note + the filtered unique index; add `irp_portfolio ||--o{ irp_portfolio : "breakout lineage (nullable)"`.
- Table index row for `breakout_dimension_kind`.
- §5 note: "`irp_portfolio.inserted_by` populated for breakout-generated portfolios (first use)."
- §5 `exposure_detail` note: the summary gains `breakout_values` and `account_total`, and `states` holds state codes.
- Open-items list: strike "portfolio breakout lineage" if/where implied; no `irp_job_resource` change (breakouts create no `irp_job`).

## 8. Migration impact

`alembic/versions/0001_initial.py` (single-revision, drop-create):

1. `breakout_dimension_kind` created **before** `irp_portfolio` (FK ordering); dropped after it in `downgrade()`.
2. `irp_portfolio` create statement gains the three columns, the self-FK (`ondelete=NO ACTION` — SQL Server rejects cascading self-references), and the filtered unique index.
3. Seed blocks: `breakout_dimension_kind` (2 rows), `rwb_job_type_kind` (+2 rows).
4. `infra/scripts/seed_db.py`: idempotent MERGE for both seed sets.
5. SQL Server tier test (`tests/sqlserver/test_detail_tables_migration.py`): columns/FK/index built; a duplicate live generated portfolio rejected; a soft-deleted one does not block re-creation (filtered index).

> **DB lifecycle**: schema-affecting → Rebuild / Refresh / Skip decision at implement time; **Rebuild** recommended (`make db-rebuild`).
