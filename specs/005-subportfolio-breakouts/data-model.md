# Data Model: One-Click Portfolio Breakouts (Iteration 4)

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Research**: [research.md](research.md) (R3 lineage, R4 naming, R2 job shape)

All changes are **WORKBENCH** (`rwb_workbench`) only, folded into the single `alembic/versions/0001_initial.py` revision (drop-create-seed dev strategy). EXPOSURE / LOSS untouched; DATABRIDGE never in schema scope.

---

## 1. `irp_portfolio` — three new lineage columns (R3)

The entity stays the thin identity record of DATA_MODEL §5, plus the spec-004 snapshot column, plus **breakout lineage**:

| Column | Type | New? | Notes |
|---|---|---|---|
| `id` | UNIQUEIDENTIFIER PK, default `NEWID()` | | |
| `edm_id` | UNIQUEIDENTIFIER NOT NULL, FK → `irp_edm.id` | | |
| `name` | NVARCHAR(256) NOT NULL | | slice names capped at 200 by the plan builder (R4) |
| `irp_id` | NVARCHAR(64) NULL | | RM `portfolioId`; written by the worker at slice creation (or adoption) |
| `exposure_detail` | NVARCHAR(MAX) NULL | | spec-004 JSON snapshot; slices start NULL until `backfill_edm_detail` runs |
| `as_of` | DATETIME2 NULL | | |
| **`source_portfolio_id`** | UNIQUEIDENTIFIER NULL, FK → `irp_portfolio.id` | ✅ | self-reference; NULL = broker-arrived (not a slice). Immediate source only — chained lineage walks the chain |
| **`breakout_dimension_code`** | NVARCHAR(32) NULL, FK → `breakout_dimension_kind.code` | ✅ | NULL iff `source_portfolio_id` IS NULL |
| **`breakout_value`** | NVARCHAR(256) NULL | ✅ | the slice's **display value** verbatim (external exposure vocabulary — plain column per Article 3 rationale; the filter token, if it differs per R6, is not stored) |
| `deleted_at` | DATETIME2 NULL | | soft delete (prune) |
| `inserted_at` / `updated_at` | DATETIME2 NOT NULL | | |
| `inserted_by` / `updated_by` | UNIQUEIDENTIFIER NULL, FK → `app_user.id` | | **now populated for slice rows** (the confirming analyst; carried to the worker in `input_data.actor_id`) — first writer to use these columns on this table |

**Constraints & indexes**

- Existing: `uq_irp_portfolio_edm_irp UNIQUE(edm_id, irp_id)`, `ix_irp_portfolio_edm_id` — unchanged.
- **New — the slice-idempotency key (R7):**

  ```sql
  CREATE UNIQUE NONCLUSTERED INDEX uq_irp_portfolio_breakout_slice
      ON irp_portfolio (source_portfolio_id, breakout_dimension_code, breakout_value)
      WHERE source_portfolio_id IS NOT NULL AND deleted_at IS NULL;
  ```

  One live slice per (source, dimension, value). Re-runs and double-submits hit this as a constraint, not a convention. (SQLite unit tier: SQLAlchemy emits the same filtered/partial unique index — supported since SQLite 3.8; the fixture engine qualifies.)
- **Integrity rule (service-enforced, not a CHECK):** the three lineage columns are set together or not at all; `source_portfolio_id` must reference a portfolio in the **same EDM**. Enforced in `portfolio_service.insert_slice/adopt_slice` (one write path), asserted in unit tests.

**Non-changes (deliberate):** no status column (Article 4), no `created_by_irp_job_irp_id` (creation is synchronous-ish and job lineage stays the job's `input_data` — DATA_MODEL §5 rule), no filter-spec JSON column (R3 alternative rejected; dimension+value fully describes a directed slice).

## 2. `breakout_dimension_kind` — new kind table (Article 3)

Standard kind-table shape:

| Column | Type | |
|---|---|---|
| `code` | NVARCHAR(32) PK | |
| `label` | NVARCHAR(128) NOT NULL | |
| `sort_order` | INT NOT NULL | |

**Seed rows** (migration + `infra/scripts/seed_db.py` idempotent MERGE):

| code | label | sort_order |
|---|---|---|
| `lob` | Line of business | 10 |
| `state` | Geography (state) | 20 |

App code dispatches on `code` (which summary list to read, which filter attribute to build — R5/R6). Follow-on slices (complement, country) add rows here, not enum literals.

## 3. `rwb_job_type_kind` — two new seed rows (R2)

| code | label |
|---|---|
| `run_breakout_lob` | Portfolio breakout by line of business |
| `run_breakout_state` | Portfolio breakout by geography (state) |

Two types — not one — because the idempotent-enqueue key is `UNIQUE(requestor_type, requestor_id, rwb_job_type)`: with `requestor_type='irp_portfolio'`, `requestor_id=<source portfolio id>`, each dimension gets its own live-job slot per portfolio (a LOB and a state breakout on the same portfolio don't collide; a re-request of the same dimension revives the terminal row via `ensure_pending_rwb_job`). Both codes dispatch to the same worker body in `app/workers/portfolio_jobs.py` (loader convention: actor name == `rwb_job_type`).

## 4. `rwb_job` payload shapes for the breakout (no schema change)

Existing columns, new content contract:

```jsonc
// input_data — written at enqueue (confirm POST)
{
  "edm_id": "<uuid>",
  "portfolio_id": "<uuid>",        // == requestor_id (the SOURCE portfolio)
  "dimension": "lob" | "state",    // redundant with rwb_job_type; kept for the shared worker body
  "actor_id": "<uuid>"             // confirming analyst → slice rows' inserted_by
}

// output_data — written at completion (per-slice outcomes, FR-012/FR-015)
{
  "planned": 12,
  "created": 10,
  "adopted": 1,                    // existed in RM, adopted by name (R7)
  "skipped_existing": 0,           // lineage row already present (idempotent re-run)
  "failed": 1,
  "slices": [
    {"value": "Homeowners", "name": "TY2601 CAT - Homeowners", "outcome": "created", "irp_id": "431"},
    {"value": "Marine",     "name": "TY2601 CAT - Marine",     "outcome": "failed",  "error": "..."}
  ],
  "backfill_enqueued": true        // FR-013 mechanical follow-up
}
```

The worker **recomputes the plan** from the stored summary + current portfolio names (R5); the preview is the same pure function rendered earlier — `input_data` intentionally carries no value list.

## 5. Read models (derived, never stored)

- **Lineage-aware portfolio list** (`portfolio_service.list_portfolios`): each row gains `source_portfolio_id`, `source_name` (joined), `breakout_dimension_code` (+ label), `breakout_value` — for the row badge "↳ from *{source}* · {label}: *{value}*". Chained lineage shows the immediate source only.
- **Breakout eligibility** (`breakout_service`): computed per request from `irp_edm.status`, portfolio existence, parsed `exposure_detail.summary`, and per-dimension distinct-value counts (≥ 2). Never cached, never stored (Article 2).

## 6. DATA_MODEL.md propagation (part of the R9 doc pass)

- §5 `irp_portfolio` block: add the three lineage columns + the "immediate source only" note + the filtered unique index; add `irp_portfolio ||--o{ irp_portfolio : "breakout lineage (nullable)"`.
- Table index row for `breakout_dimension_kind`.
- §5 note: "`irp_portfolio.inserted_by` populated for breakout-created slices (first use)."
- Open-items list: strike "portfolio breakout lineage" if/where implied; no `irp_job_resource` change (breakouts create no `irp_job` on the primary path).

## 7. Migration impact

`alembic/versions/0001_initial.py` (single-revision, drop-create):

1. `breakout_dimension_kind` created **before** `irp_portfolio` (FK ordering); dropped after it in `downgrade()`.
2. `irp_portfolio` create statement gains the three columns, the self-FK (`ondelete=NO ACTION` — SQL Server rejects cascading self-references), and the filtered unique index.
3. Seed blocks: `breakout_dimension_kind` (2 rows), `rwb_job_type_kind` (+2 rows).
4. `infra/scripts/seed_db.py`: idempotent MERGE for both seed sets.
5. SQL Server tier test (`tests/sqlserver/test_detail_tables_migration.py`): columns/FK/index built; duplicate live slice rejected; soft-deleted slice does not block re-creation (filtered index).

> **DB lifecycle**: schema-affecting → Rebuild / Refresh / Skip decision at implement time; **Rebuild** recommended (`make db-rebuild`).
