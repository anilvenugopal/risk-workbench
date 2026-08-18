# Data Model: Analysis Templates & Template Suites (009)

All tables live in the WORKBENCH database, defined in `alembic/versions/0001_initial.py`
(single-revision drop-create-seed strategy). Conventions follow the existing file: `sa.Uuid` PK
with `NEWID()` default, `DATETIME2`, `inserted_at`/`updated_at` with `GETUTCDATE()` defaults,
`inserted_by`/`updated_by` FK → `app_user.id`, `deleted_at` for soft delete, filtered unique
indexes for live-row uniqueness (pattern: `uq_irp_edm_live_irp_id`).

## Reference cache (4 tables, DATA_MODEL §10 shape + probe-driven columns)

Populated only by the `sync_irp_metadata` worker: snapshot upsert plus **hard delete** of rows the
fetch no longer returned, in one transaction. No soft delete and no per-row `as_of` — after a
successful sync every surviving row was seen by that sync, so the metadata page's last-synced time
comes from the latest succeeded `sync_irp_metadata` rwb_job. Uniqueness is a plain unique index on
the natural key (`irp_id`, or `code` for currencies). Pick lists and the metadata tabs read the
tables directly. Templates reference these rows by name (Article 2), never by FK, so the hard
delete cannot orphan anything — a missing name is the read-time unresolved flag (R9).

### `irp_model_profile`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `irp_id` | INT NOT NULL UNIQUE | RM profile `id` |
| `name` | NVARCHAR(200) NOT NULL | |
| `is_accumulation` | BIT NOT NULL DEFAULT 0 | 1 = row came from the accumulation-profile read (T-02); marker = Accumulation |
| `software_version_code` | NVARCHAR(50) NULL | when not accumulation: `"HD" in code` → HD, else DLM (R2); NULL-ability for accumulation rows is provisional until the T-02 spike |
| `peril_code` | NVARCHAR(20) NULL | filters/pre-fills event-rate schemes (R4) |
| `model_region_code` | NVARCHAR(20) NULL | same |
| `peril` | NVARCHAR(100) NULL | display |
| `region` | NVARCHAR(100) NULL | display |
| `analysis_type` | NVARCHAR(50) NULL | display/filter (e.g. "Exceedance Probability") |
| `rms_default` | BIT NOT NULL DEFAULT 0 | display/filter |
| audit | | `inserted_at/updated_at` |

### `irp_output_profile`

`id` PK, `irp_id` INT NOT NULL UNIQUE, `name` NVARCHAR(200) NOT NULL, `rms_default` BIT NOT NULL
DEFAULT 0, audit.

### `irp_event_rate_scheme`

`id` PK, `irp_id` INT NOT NULL UNIQUE (= `eventRateSchemeId`), `name` NVARCHAR(200) NOT NULL
(= `eventRateSchemeName`), `peril_code` NVARCHAR(20) NULL, `model_region_code` NVARCHAR(20) NULL,
`model_version_code` NVARCHAR(50) NULL, `is_hd` BIT NOT NULL DEFAULT 0, audit. Only active schemes
are synced (the wheel filters `isActive=True`).

### `irp_currency`

`id` PK, `code` NVARCHAR(10) NOT NULL UNIQUE (ISO-ish natural key), `name` NVARCHAR(100) NOT NULL,
`country_name` NVARCHAR(100) NULL, `symbol` NVARCHAR(10) NULL, audit. (Sandbox carries a
`currencyId` surrogate, but `code` is what templates store and what submit consumes — DATA_MODEL
§10's natural-key note stands.)

## Templates & suites (DATA_MODEL §7 with three deltas)

Deltas from DATA_MODEL §7, to be reconciled there at implementation: (a)
`analysis_template_tag.tag_name` replaces `irp_tag_id` (R7); (b) `auto_name_pattern`,
`region_label`, and `peril_code` are dropped (R11); (c) new
`treat_construction_occupancy_as_unknown` column (spec FR-005, wheel parameter); (d) no separate
`created_by` — `inserted_by` is the authorship record (one fact, one column).

### `analysis_template`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `name` | NVARCHAR(200) NOT NULL | filtered-unique on live rows (import matching key, P-05) |
| `analysis_profile_name` | NVARCHAR(200) NOT NULL | RM model-profile name (name-based coupling, Art. 2) |
| `output_profile_name` | NVARCHAR(200) NOT NULL | |
| `event_rate_scheme_name` | NVARCHAR(200) NULL | required when the cached profile is DLM (validated at save/import) |
| `currency_code` | NVARCHAR(10) NOT NULL | |
| `min_loss_threshold` | DECIMAL(18,2) NOT NULL DEFAULT 1.00 | FR-005: numeric, 2 dp |
| `num_max_loss_event` | INT NOT NULL DEFAULT 1 | |
| `franchise_deductible` | BIT NOT NULL DEFAULT 0 | |
| `treat_construction_occupancy_as_unknown` | BIT NOT NULL DEFAULT 1 | 1 = "Treat as unknown", 0 = "Skip location during analysis" (R8) |
| `treaty_name_pattern` | NVARCHAR(200) NULL | stored only; resolved in Iteration 7 |
| `deleted_at` | DATETIME2 NULL | soft delete |
| audit | | `inserted_at/updated_at`, `inserted_by/updated_by` (authorship = `inserted_by`) |

Not a boolean pair of kind tables: `franchise_deductible` and
`treat_construction_occupancy_as_unknown` are boolean API parameters mirrored onto submit calls,
not app-defined category sets (Article 3 governs categoricals; the two UI labels for the occupancy
boolean live in the template/builder, sourced from one place in the service).

### `analysis_template_tag`

`template_id` uuid FK → `analysis_template.id`, `tag_name` NVARCHAR(200) NOT NULL, PK
`(template_id, tag_name)`, `inserted_at`, `inserted_by`. Rows replaced with the template on edit.

### `template_suite`

`id` uuid PK, `name` NVARCHAR(200) NOT NULL (filtered-unique live; conveys region + output level,
P-03), `deleted_at` NULL, audit columns (authorship = `inserted_by`).

### `template_suite_item`

`id` uuid PK, `suite_id` uuid FK → `template_suite.id`, `template_id` uuid FK →
`analysis_template.id`, `position` INT NOT NULL, `portfolio_name_override` NVARCHAR(200) NULL,
`inserted_at`, `inserted_by`. Constraints: `UNIQUE(suite_id, template_id)` (a template at most once
per suite, FR-012). Items are hard-deleted/re-written as part of editing their suite (composition
rows, no independent lifecycle); positions are renumbered 1..n on save.

## Validation rules (service layer, unit-tested)

- **DLM scheme rule**: on save/import, classify the template's `analysis_profile_name` via the
  cache: `is_accumulation` → Accumulation, else `"HD" in software_version_code` → HD, else DLM.
  Only DLM requires `event_rate_scheme_name`; missing → reject naming the rule. Profile absent
  from the cache → rule is skipped and the template is *unresolved* (R9); never a save-blocker
  (FR-011/FR-019).
- **Name uniqueness**: duplicate live template/suite name → reject (DB filtered-unique is the
  guarantee; `is_unique_violation` absorbed into the form error).
- **Delete guard**: deleting a template referenced by live suites → blocked, referencing suite
  names returned (FR-010).
- **Import**: whole-file validation first (missing required field, wrong type, duplicate name
  within file, DLM without scheme, unknown column/sheet), then apply in one transaction —
  match-by-name update or create (P-04/P-05).

## Seed data

- `rwb_job_type_kind` += `('sync_irp_metadata', 'Sync IRP metadata', …)` (0001 inline seed +
  `infra/scripts/seed_db.py`, both).
- Starter suites: US, Canada, US+Canada, Global (~10 templates each, indicative settings, P-02)
  live in the seed workbook `infra/scripts/starter_suites.xlsx` (transfer-workbook format);
  `seed_db.py` imports it through the import service, skipping when any live suite exists (R10).
  No kind-table rows for templates/suites themselves (DATA_MODEL §13 unchanged).

## Test schema mirror

`tests/iteration1_mirror.py` gains the 8 new tables in a new `ITERATION4_SCHEMA` block (SQLite
DDL), registered in the drift-guard lists so `tests/sqlserver/test_schema_drift.py` compares them
against the real migration.
