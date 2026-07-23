# Phase 1 — Data Model: EDM/RDM Details & Backfill (Iteration 3)

Derived from **DATA_MODEL §5 (EDM/RDM/portfolio/treaty), §6 (analysis)** and the storage-shape decision **research R2** (a JSON snapshot cache). This is the concrete set of schema changes the single `alembic/versions/0001_initial.py` revision **adds** for Iteration 3, *on top of* the Iterations 0–2 tables already there. No table is dropped; `irp_edm`/`irp_rdm` are unchanged.

**What is new:** two entity tables — **`irp_portfolio`** and **`irp_treaty`** — created for the first time (deferred by spec 003 / research R13); three columns on the existing **`irp_analysis`** table; and one **`rwb_job_type_kind`** seed row (`backfill_edm_detail`). Each detail-bearing entity gains a **JSON snapshot column** (`NVARCHAR(MAX)`, nullable) holding the Risk Modeler detail verbatim (R2).

**Conventions** (DATA_MODEL §2): singular `snake_case`; `id` is a UUID surrogate PK generated app-side (`uuid4()`, bound param; `NEWID()` default retained as fallback); `*_id` FK → entity; entity tables carry `inserted_at`/`updated_at`/`inserted_by`/`updated_by`; kind tables carry `inserted_at` only. Types shown are SQL Server (`DATETIME2`, `NVARCHAR`, `Uuid`, `INT`, `BIT`); the SQLite unit tier maps these via SQLAlchemy. **Single revision** (drop-create-seed) — `irp_analysis` gains columns by editing its existing `create_table`, **not** an `ALTER`.

**Out of scope this iteration** (unchanged from R13, plus this iteration's scope calls): the **normalized/filterable** exposure model (typed peril/geography/split tables) — Iteration 4; `analysis_result_meta`, Parquet result storage, `result_export`, the IRP reference cache (§10), Phase A validation (§11) — Iteration 6+; `analysis_template` / `template_suite` (§7). The detail this iteration stores is a **read-only snapshot**, not a queryable model.

---

## 1. Kind-table change (with seed — DATA_MODEL §13)

### `rwb_job_type_kind` — one new row
Add **`backfill_edm_detail`** ("Backfill EDM Detail") to the existing `rwb_job_type_kind` seed (the closed, app-defined worker-type set — Article 3 kind table). No new kind table is created this iteration.

> Existing rows (unchanged): `upload_edm`, `upload_rdm`, `backfill_rdm_analyses`, `retrieve_analysis_results`, `download_export_file`, `push_results_to_loss_repo`, `notify_analyst`, `delete_rdm`, `delete_edm`.
> **Exercised this iteration:** the new `backfill_edm_detail`, and the existing `backfill_rdm_analyses` (extended — R3). No new `irp_job_type` (backfill creates no `irp_job`; it is app-side `rwb_job` work off the existing import jobs).

> **No kind table for the detail vocabularies (R2 / Article 3).** Perils, sub-perils, geography, currency, and analysis-setting values live inside the JSON snapshots as **external Risk Modeler vocabularies** — no internal code path dispatches on them, so they are correctly *not* kind tables (minting one would force a seed migration on every RM vocabulary change — the crash-risk the Article 3 carve-out guards against).

---

## 2. `irp_portfolio` — a portfolio within an EDM (NEW) — DATA_MODEL §5

The **primary unit** of the EDM detail page. Created for the first time this iteration. Identity/lineage columns per DATA_MODEL §5; the per-portfolio exposure figures live in the `exposure_detail` JSON snapshot (R2).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | Uuid | PK | app-generated |
| `edm_id` | Uuid | not null | FK → `irp_edm.id` — the owning EDM |
| `name` | NVARCHAR(256) | not null | portfolio name in Risk Modeler |
| `irp_id` | NVARCHAR(64) | null | RM portfolio id **as string** (backfilled with the detail) |
| `exposure_detail` | NVARCHAR(MAX) | null | **JSON snapshot** — the per-portfolio figures (§5 shape below); null ⇒ not yet backfilled (graceful empty) |
| `as_of` | DATETIME2 | null | last-confirmed-against-Risk-Modeler trust signal (FR-052), stamped on backfill |
| `deleted_at` | DATETIME2 | null | soft delete (unused this iteration; present for §5 fidelity) |
| `inserted_at`/`updated_at` | DATETIME2 | not null | defaults `GETUTCDATE()` |
| `inserted_by`/`updated_by` | Uuid | null | FK → `app_user.id` (nullable — worker-written) |

**`exposure_detail` JSON shape** (illustrative; the exact keys mirror the Risk Modeler payload confirmed in R1 — stored verbatim, read defensively):
```json
{
  "location_count": 12043, "account_count": 318, "policy_count": 402,
  "record_volume": 12043,
  "perils": ["EQ", "WS", "FL"], "sub_perils": ["storm_surge", "sprinkler_leakage"],
  "geography": {"regions": ["North America"], "states": ["FL", "TX", "LA"]},
  "currencies": ["USD"],
  "tiv": {"amount": 4.2e9, "currency": "USD"}   // MAY be present (FR-013); absent ⇒ not shown
}
```
- **Read-only display** (FR-012/FR-014): no create/edit/split/filter this iteration.
- **UNIQUE (`edm_id`, `irp_id`)** — the idempotent-upsert key; a re-backfill overwrites `exposure_detail`/`as_of` in place, never inserting a duplicate. Where `irp_id` is unavailable at first write, fall back to **UNIQUE (`edm_id`, `name`)** (RM portfolio names are unique within an EDM). Index `(edm_id)`.

---

## 3. `irp_treaty` — reinsurance on an EDM (NEW) — DATA_MODEL §5

Created for the first time this iteration. A **read/cache record**: identity per §5; the full attribute set lives in the `attributes` JSON snapshot for the EDM-level treaty view + Excel export (US2).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | Uuid | PK | app-generated |
| `edm_id` | Uuid | not null | FK → `irp_edm.id` — the EDM this treaty belongs to |
| `name` | NVARCHAR(256) | not null | treaty name in Risk Modeler (analyses reference treaties **by name**, §5) |
| `irp_id` | NVARCHAR(64) | null | RM `treatyId` **as string** (backfilled with the detail) |
| `attributes` | NVARCHAR(MAX) | null | **JSON snapshot** — the full attribute set (every field), key/value; null ⇒ not yet backfilled |
| `as_of` | DATETIME2 | null | trust signal, stamped on backfill (FR-052) |
| `deleted_at` | DATETIME2 | null | soft delete (unused this iteration; §5 fidelity) |
| `inserted_at`/`updated_at` | DATETIME2 | not null | defaults `GETUTCDATE()` |
| `inserted_by`/`updated_by` | Uuid | null | FK → `app_user.id` (nullable — worker-written) |

- **Read-only** (FR-025): no create/edit this iteration (treaty create/edit — the §5 `create_treaty` + `create_treaty_lob` 1+N pattern — is a later Risk Modeler pass-through). The `treaty_type_kind` seed already exists (spec 003) but is **not** referenced here — the snapshot carries RM's own type/attribute values; classifying a treaty into `treaty_type_kind` is a later concern.
- **`attributes`** is the whole attribute model for the compact/expanded view (FR-021/FR-022/FR-023) and the union-of-keys Excel export (FR-024, R5).
- **UNIQUE (`edm_id`, `irp_id`)** idempotent-upsert key (fallback **UNIQUE (`edm_id`, `name`)** — treaty names are unique within an EDM and analyses key on name). Index `(edm_id)`.

---

## 4. `irp_analysis` — new detail columns (EDIT) — DATA_MODEL §6

`irp_analysis` already exists (spec 003 / D2) with identity/lineage + `source_rdm_name` + `status_code`. This iteration **adds three columns** (by editing the existing `create_table` — single revision, no `ALTER`) so the broker-analysis view can show settings/metadata and group rows (US3):

| Column (new) | Type | Null | Notes |
|---|---|---|---|
| `settings_metadata` | NVARCHAR(MAX) | null | **JSON snapshot** — the analysis settings/metadata (§6 shape below); null ⇒ not yet backfilled (graceful blank) |
| `is_group` | BIT | not null | default `0`; `1` ⇒ this row **is** a group (FR-035; DATA_MODEL §6) |
| `group_parent_id` | Uuid | null | FK → `irp_analysis.id` (self-ref) — the group this belongs to (§6); populated only if RM exposes membership |

**Existing columns (unchanged):** `id`, `rdm_id` (FK `irp_rdm`), `edm_id` (FK `irp_edm`, nullable — always set this iteration, D3), `package_id`, `irp_id` (Moody's `analysisId`), `name`, `source_rdm_name`, `status_code` (FK `irp_analysis_status_kind`), `created_by_irp_job_irp_id`, `deleted_at`, audit; `UNIQUE(rdm_id, edm_id, irp_id)`.

**`settings_metadata` JSON shape** (illustrative; keys mirror the RM payload confirmed in R1 — FR-031 / FUNCTIONAL_REQUIREMENTS §7):
```json
{
  "engine": {"name": "RL", "model_version": "23.0"}, "engine_type": "DLM", "engine_version": "...",
  "analysis_type": "DLM", "analysis_mode": "...",
  "peril": {"primary": "EQ", "secondary": ["fire_following"]},
  "region": "North America", "currency": "USD", "construction": "...",
  "line_of_business": "Commercial", "group_type": null,
  "term": "long_term", "event_rate_scheme": "...", "loss_amplification": {"pla": false}
}
```
- **Broker vs own is derived from `rdm_id`** (set ⇒ broker) — no stored `origin` column (§6). This iteration surfaces **broker** analyses (`rdm_id` set) grouped by `rdm_id` (R8).
- **Loss result data stays out** (FR-033): no `analysis_result_meta`, no Parquet, no `retrieve_analysis_results` worker.

---

## 5. `irp_edm` — no schema change

The redesigned EDM detail page's **light header** (name, status, `as_of`, source file, identifiers, portfolio count — FR-011) reads entirely from the **existing** `irp_edm` columns (spec 003 §6) + a `COUNT` of its `irp_portfolio` rows. The **EDM-aggregate** shown for orientation is **derived** from the per-portfolio `exposure_detail` snapshots at read time (research R4) — **not** a stored column. No `ALTER` on `irp_edm`.

---

## 6. Relationships (Iteration-3 additions)

```text
irp_edm        1──∞ irp_portfolio     (edm_id — NEW; contains portfolios)
irp_edm        1──∞ irp_treaty        (edm_id — NEW; holds treaties)
irp_analysis   1──0..1 irp_analysis   (group_parent_id — NEW self-ref; group membership)
rwb_job_type_kind 1──∞ rwb_job        (rwb_job_type — existing; new 'backfill_edm_detail' value)
```
- **No FK from `irp_portfolio`/`irp_treaty` to `rwb_job`/`irp_job`.** They are backfilled by the `backfill_edm_detail` worker keyed off the finished `import_edm` job; lineage is the worker's `requestor_id`, not a stored FK.
- **No `customer`/scope column anywhere** (Article 6). A portfolio/treaty reaches a submission only transitively: `irp_portfolio.edm_id → irp_edm.package_id → submission_package → submission`.
- **`irp_job.irp_portfolio_id` stays deferred.** DATA_MODEL §8 defines this FK and `irp_portfolio` now exists, but no portfolio-scoped `irp_job` is created this iteration (portfolio-scoped analysis jobs are Iteration 6). The column continues to be omitted (spec 003 §2 note carried forward); it is added when a job actually populates it. Recorded here so the deviation stays tracked, not silent.

---

## 7. Migration & seed impact (single revision — drop-create-seed)

**`alembic/versions/0001_initial.py`** — extend the one existing revision (after the Iteration-2 tables, in FK order):
- **Add entity creates:** `irp_portfolio` (FK → `irp_edm`; `exposure_detail` JSON col; `UNIQUE(edm_id, irp_id)` + index `(edm_id)`), `irp_treaty` (FK → `irp_edm`; `attributes` JSON col; `UNIQUE(edm_id, irp_id)` + index `(edm_id)`).
- **Edit the `irp_analysis` create:** add `settings_metadata NVARCHAR(MAX) null`, `is_group BIT not null default 0`, `group_parent_id Uuid null` (self-ref FK → `irp_analysis.id`).
- **Extend the `rwb_job_type_kind` seed:** add `('backfill_edm_detail', 'Backfill EDM Detail', 27)` (between `backfill_rdm_analyses`=25 and `retrieve_analysis_results`=30).
- **Downgrade:** drop `irp_treaty`, `irp_portfolio` (and their indexes) in reverse FK order, ahead of the Iteration-2 drops; the `irp_analysis` column additions are inherent to its create (no separate drop). No change to the existing Iteration-2 downgrade steps.
- **No `ALTER`** anywhere — every change is inside a `create_table` or a seed `INSERT` (drop-create-seed).

**`infra/scripts/seed_db.py`** — add an idempotent `MERGE` for the new `rwb_job_type_kind` row `backfill_edm_detail` (same pattern as the existing kind MERGEs), so a re-seed without a full rebuild stays correct.

**Dev DB strategy (§21.0 prompt):** **Rebuild** (`make db-rebuild` — drop, recreate, migrate, seed) — this is schema-affecting (two new tables + three columns). Single revision until production cutover. Run the Rebuild/Refresh/Skip prompt for **WORKBENCH** before this work; `EXPOSURE`/`LOSS` are untouched; DATABRIDGE is never touched.

---

## 8. Test obligations (Article 12 — cross-referenced in contracts/)

Unit tier (SQLite via `register_engine` + **fake IRP** returning portfolio/treaty/analysis-metadata payloads):
- **`backfill_edm_detail` worker:** fetches portfolios + per-portfolio exposure + treaties (fake), **idempotently upserts** `irp_portfolio`/`irp_treaty` with the JSON snapshot + `as_of`; a duplicate/re-run **overwrites in place** (no duplicate rows, on the `UNIQUE(edm_id, irp_id)` key); a fetch failure leaves the EDM `ready` and the `rwb_job` recoverable (FR-004/FR-005).
- **Extended `backfill_rdm_analyses`:** also writes `settings_metadata` on each captured `irp_analysis`; idempotent with the existing pair capture.
- **Aggregate rollup (`edm_service.get_edm_detail`):** sums counts / unions perils / combines geography+currency from the per-portfolio snapshots; returns a graceful empty marker when no snapshot exists (FR-042/FR-043).
- **Broker-analysis grouping:** `list_broker_analyses` groups by `rdm_id` (M-EDM analysis shown once); `is_group` surfaced; missing metadata renders blank not error (FR-030/FR-031/FR-035).
- **Poller enqueue extension:** `import_edm` FINISHED enqueues **both** `upload_rdm` and `backfill_edm_detail` (idempotent); a standalone/EDM-only import still enqueues `backfill_edm_detail`.

SQL-Server tier (`--run-sqlserver`):
- The extended migration builds `irp_portfolio`/`irp_treaty` + the new `irp_analysis` columns with all FKs + the `UNIQUE(edm_id, irp_id)` keys; the `backfill_edm_detail` row is seeded.
- The idempotent detail upsert: a second backfill of the same EDM updates `exposure_detail`/`attributes`/`as_of` in place and inserts no duplicate portfolio/treaty row.

IRP tier (`--run-irp`, opt-in): the real portfolio-enumeration / per-portfolio exposure / treaty-attribute / analysis-metadata read round-trips; an assertion that `poll_*_to_completion` (and the poll-inside convenience methods) appear nowhere in the new worker/gateway code.
