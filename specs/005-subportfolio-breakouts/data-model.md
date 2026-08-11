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
| `country` | Country | 25 |
| `peril` | Peril | 30 |
| `custom` | Custom group | 40 |

App code dispatches on `code` — which entry of `summary.breakout_values` to read, which selection read to run. `code` is also the key inside `breakout_values` (§5), so there is no second vocabulary to keep in step. Follow-on dimensions (complement) add rows here, not enum literals — `country` was added this way 2026-08-10, quick-mode with number letter `C` (values are the `Address` country codes, label always null — no name column in the EDM). `peril` is grouping-only (P-19: values are `loccvg.PERIL` codes stringified, label always null — W-21); `custom` is the grouping lineage code (§9), not a value dimension — the gate never enumerates it.

## 3. `rwb_job_type_kind` — two new seed rows (R2)

| code | label |
|---|---|
| `run_breakout_lob` | Portfolio breakout by line of business |
| `run_breakout_state` | Portfolio breakout by geography (state) |
| `run_breakout_country` | Portfolio breakout by country |
| `run_breakout_custom` | Portfolio breakout by custom group (§9 — requestor type `breakout_group`) |

One type per quick dimension — not one shared type — because the idempotent-enqueue key is `UNIQUE(requestor_type, requestor_id, rwb_job_type)`: with `requestor_type='analyst_request'`, `requestor_id=<source portfolio id>`, each dimension gets its own live-job slot per portfolio (a LOB and a state breakout on the same portfolio don't collide; a re-request of the same dimension revives the terminal row via `ensure_pending_rwb_job`). The quick-dimension codes dispatch to the same worker body in `app/workers/portfolio_jobs.py` (loader convention: actor name == `rwb_job_type`).

`analyst_request` is an existing `rwb_job_requestor_type_kind` code — the one `edm_service.sync_detail` and the other analyst-triggered enqueues already use — so **no new requestor-type seed row is added**. `requestor_id` holds the source portfolio id rather than an EDM id, which the column allows: it carries no DB FK precisely because its target varies by requestor type. FR-015 reads the source portfolio back off `requestor_id`, unchanged.

## 4. `rwb_job` payload shapes for the breakout (no schema change)

Existing columns, new content contract. `input_data` **is the approved plan** — the worker executes it rather than recomputing it (AGENTS.md rule 8 / R10).

```jsonc
// input_data — written at enqueue (confirm POST), exactly what the analyst saw
{
  "edm_id": "<uuid>",
  "portfolio_id": "<uuid>",          // == requestor_id (the SOURCE portfolio)
  "dimension": "lob" | "state",      // redundant with rwb_job_type; kept for the shared worker body
  "actor_id": "<uuid>",              // confirming analyst → generated rows' inserted_by
  "plan": [                          // one entry per sub-portfolio, in preview order
    // accounts is the count the preview showed; FR-006b holds it identical across the
    // confirm window, and persisting it is what makes that checkable after the fact
    {"value": "TX", "label": "TEXAS", "name": "usfl_commercial - TX", "number": "P1-S-TX",
     "accounts": 412},
    {"value": "CA", "label": "CALIFORNIA", "name": "usfl_commercial - CA", "number": "P1-S-CA",
     "accounts": 1289}
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
  "states": ["CA", "FL", "TX"],          // CHANGED: Admin1Code (was COALESCE(Admin1Name, Admin1Code))
  "lines_of_business": ["FLD Comm"],     // unchanged — the display lists
  "currencies": ["USD"],
  "account_total": 1701,                 // NEW: the overlap denominator (FR-007)
  "breakout_values": {                   // NEW: the enumeration source (FR-005)
    "state": [{"value": "TX", "label": "TEXAS", "accounts": 220}],
    "lob":   [{"value": "FLD Comm", "label": null, "accounts": 25}]
  },
  "breakout_coverage": {                 // NEW: the measured overlap (FR-007)
    "state": {"covered": 1624, "multi_value": 252},
    "lob":   {"covered": 1690, "multi_value": 311}
  }
}
```

- `breakout_values` is keyed by `breakout_dimension_kind.code`, so the gate, the preview, and the worker index it by dimension with no per-dimension branch.
- `label` is `Admin1Name` where the EDM has it and `null` otherwise — a display label only, never synthesized from the code (P-12). For `lob` the value is its own label, so the key is `null`.
- `breakout_coverage` is keyed the same way. `covered` counts the portfolio's accounts carrying **at least one** value of the dimension; `account_total − covered` is the number that carry none and therefore land in no sub-portfolio. `multi_value` counts the accounts carrying **more than one** — the accounts that appear in several sub-portfolios. Both are counted per account by `portfolio_state_coverage.sql` / `portfolio_lob_coverage.sql`, which repeat their summary script's joins and blank filter, and neither is derivable from `breakout_values[].accounts`: that sums memberships, so an account with three values adds three and an account with none adds nothing while still counting in `account_total`. A summary written before the 2026-08-05 revision has no `breakout_coverage`; the preview reads that as absent and falls back to the qualitative disclosure, exactly as it already does for a missing `account_total`. No migration or backfill of existing snapshots is needed — a Sync rewrites the summary.
- **Absence of `breakout_values` is the staleness signal.** Every summary written before this iteration lacks it, and its `states` list holds a mixed vocabulary of names and codes that must not be read as filter values. The gate treats a missing `breakout_values` as a missing summary and points at Sync (FR-002). No migration or backfill of existing snapshots is needed.
- Readers parse defensively, as the existing spec-004 readers do; an additive JSON change is spec-004-compatible.
- Also captured by the same backfill, alongside the summary: the portfolio's Risk Modeler `stampDate`, the FR-002a freshness anchor. Stored in `exposure_detail`, no new column.

Source scripts, all read-only and worker-side through `irp-integration` (Article 11): `portfolio_states.sql` (returns `Admin1Code`, `MAX(Admin1Name)`, account count, grouped and filtered on the code), `portfolio_lines_of_business.sql` (+ account count), `portfolio_account_total.sql`, and `portfolio_state_coverage.sql` / `portfolio_lob_coverage.sql`. Measured cost of the first three: **+1.44s** on the backfill job for the largest sandbox book (W-19); the two coverage scripts repeat those joins with a per-account grouping, so the added cost is measured at the T063 walkthrough.

## 6. Read models (derived, never stored)

- **Lineage-aware portfolio list** (`portfolio_service.list_portfolios`): each row gains `source_portfolio_id`, `source_name` (joined), `breakout_dimension_code` (+ label), `breakout_value` — for the row badge "↳ from *{source}* · {label}: *{value}*". Chained lineage shows the immediate source only.
- **Breakout eligibility** (`breakout_service`): computed per request from `irp_edm.status`, portfolio existence, parsed `exposure_detail.summary`, presence of `breakout_values`, and per-dimension distinct-value counts (≥ 2). Never cached, never stored (Article 2).
- **Overlap statement** (`breakout_service.compute_overlap`): read from `breakout_coverage[dimension]`, never derived. `multi_value` accounts appear in more than one sub-portfolio; `account_total − covered` accounts appear in none. Both zero → the sub-portfolios partition the source; either non-zero → the count is stated in the preview, with the note that exposure inflation can exceed account inflation (FR-007). Absent `breakout_coverage` → the qualitative disclosure alone. `Σ accounts over the dimension's values` is the membership total and is deliberately not part of the statement: see §5 for why the difference against `account_total` measures neither figure.

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

## 9. Follow-on: `breakout_group` — the custom-group entity (T-12/T-13)

One row per (source portfolio, canonical member set). The row's UUID is the group job's `rwb_job.requestor_id` — `requestor_id` is a Uuid column, so a composite string key was not an option (T-13). Supersedes R3's "no filter-spec storage": custom grouping is now a product requirement (FR-018–021), and the filter set is the approved plan.

| Column | Type | Notes |
|---|---|---|
| `id` | UNIQUEIDENTIFIER PK, default `NEWID()` | the group job's `requestor_id` |
| `source_portfolio_id` | UNIQUEIDENTIFIER NOT NULL, FK → `irp_portfolio.id` | |
| `group_key` | NVARCHAR(64) NOT NULL | `sha256(canonical filters)[:12]` — the identity (P-22) |
| `label` | NVARCHAR(256) NOT NULL | the analyst's group name; adopt-not-rename |
| `filters` | NVARCHAR(MAX) NOT NULL | canonical member-filter JSON: `{"state": ["FL","GA"], "peril": ["2"]}` — OR within, AND across (P-20) |
| `name` / `number` | NVARCHAR(256) / NVARCHAR(64) NOT NULL | the approved plan values (rule 8); name = the label exactly as typed (P-24), number = the name truncated to 20 (P-26). Rows approved before 2026-08-10 keep their composed `{source} - {label}` name and `P{rm id}-G-{key token}` number |
| `cart_id` | UNIQUEIDENTIFIER NOT NULL | the confirm that most recently carried the group — banner aggregation (FR-020) |
| audit | | `inserted_at`/`updated_at` NOT NULL; `inserted_by`/`updated_by` NULL FK → `app_user.id` |

- `UNIQUE(source_portfolio_id, group_key)` (`uq_breakout_group_source_key`): a re-confirm of the same member set reuses the row, which dedups the job through `UNIQUE(requestor_type, requestor_id, rwb_job_type)` with no `rwb_job` change.
- `irp_portfolio` += `breakout_group_id` (UNIQUEIDENTIFIER NULL, FK `fk_irp_portfolio_breakout_group`). A generated group portfolio stores `breakout_dimension_code='custom'` with the **group_key** as `breakout_value` — `uq_irp_portfolio_breakout` DDL unchanged; label/filters read via the join (one source of truth).
- New kind rows: `breakout_dimension_kind ('custom','Custom group',40)`, `rwb_job_type_kind 'run_breakout_custom'`, `rwb_job_requestor_type_kind 'breakout_group'`.

**Group job `input_data`** (per group; the worker reads `group` and nothing else):

```jsonc
{
  "edm_id": "<uuid>", "portfolio_id": "<uuid>",   // the SOURCE portfolio
  "dimension": "custom", "actor_id": "<uuid>",
  "cart_id": "<uuid>",                            // shared across the cart's jobs
  "group": {
    "id": "<breakout_group.id>", "key": "a1b2c3d4e5f6",
    "label": "Coastal HU",
    "filters": {"peril": ["2"], "state": ["FL", "GA"]},
    "name": "Coastal HU", "number": "Coastal HU",
    "accounts_upper_bound": 1445                  // P-23 preview figure
  }
}
```

`output_data` keeps the §4 shape with one `sub_portfolios` entry whose `value` is the group_key.

**Read models**: `list_portfolios` LEFT JOINs `breakout_group` for the label and parsed filters (a custom row's display label IS the group label); `page_state` resolves group jobs to their source portfolio through `breakout_group.source_portfolio_id`, renders one flight per portfolio over the live cart ("custom groups: k of n done"), and aggregates terminal jobs sharing the newest `cart_id` into one banner.
