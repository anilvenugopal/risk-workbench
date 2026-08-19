# Data Model: Analysis Templates & Template Suites (009)

All tables live in the WORKBENCH database, defined in `alembic/versions/0001_initial.py`
(single-revision drop-create-seed strategy). Conventions follow the existing file: `sa.Uuid` PK
with `NEWID()` default, `DATETIME2`, `inserted_at`/`updated_at` with `GETUTCDATE()` defaults,
`inserted_by`/`updated_by` FK → `app_user.id`, `deleted_at` for soft delete, filtered unique
indexes for live-row uniqueness (pattern: `uq_irp_edm_live_irp_id`).

## Reference cache (6 tables, DATA_MODEL §10 shape + probe-driven columns)

Populated only by the `sync_irp_metadata` worker: snapshot upsert plus **hard delete** of rows the
fetch no longer returned, in one transaction. No soft delete and no per-row `as_of` — after a
successful sync every surviving row was seen by that sync, so the metadata page's last-synced time
comes from the latest succeeded `sync_irp_metadata` rwb_job. Uniqueness is a plain unique index on
the natural key (`irp_id`; `code` for `irp_currency`). Pick lists and the metadata tabs read the
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

Built in US1 and **kept** (spec P-07 as amended 2026-08-18): analysis submission's currency block
is `{code, scheme, vintage, asOfDate}`, so the currency itself stays a stored/selected value even
though the metadata screen shows schemes, not currencies (D3). `id` PK, `code` NVARCHAR NOT NULL
UNIQUE (natural key, `uq_irp_currency_code`), `name` (16-char truncation, P-06), `country_name`,
`symbol`, audit.

### `irp_currency_scheme`

New (spec P-07 / plan T-07, 2026-08-18): CIC selects currency **schemes** first — the same
currency appears in multiple schemes with different FX rates, and only ~2–5 schemes will ever
exist. **Columns pinned 2026-08-19** by the released `0.6.0rc2` read plus a sandbox probe (45
schemes; item fields `currencySchemeId` / `currencySchemeName` / `currencySchemeCode` — codes
non-null and unique, observed ≤26 chars, names ≤29): `id` PK, `irp_id` INT NOT NULL UNIQUE
(= `currencySchemeId`), `name` NVARCHAR(200) NOT NULL, `code` NVARCHAR(50) NOT NULL (scheme
code, e.g. "RMS", "DT"), audit. Only active schemes are cached (`isActive` filter, like
event-rate schemes). No `is_default` column (dropped 2026-08-19: upstream `isDefault` is
true/false/**null** in the sandbox and nothing consumes it after the P-10 reversal — re-add only
if the metadata tab ever wants a "Default" marker).

### `irp_currency_scheme_vintage`

New (spec P-07 as amended): the vintage supplies submission's `vintage` code and the effective
date that `asOfDate` derives from. **Columns pinned 2026-08-19** by the released `0.6.0rc2` read
plus a sandbox probe (51 vintages; item fields `vintage`, `currencySchemeCode`, `effectiveDate`,
`vintageDescription` + audit). The item carries **no id field**, and `(currencySchemeCode,
vintage)` is **not unique** upstream (duplicate pairs observed in the sandbox), so this cache is
a **raw snapshot** (user-decided 2026-08-19): no `irp_id`, no unique index, and the sync replaces
it wholesale — delete-all + insert, never a keyed upsert — storing exactly what the API returned,
duplicates included. Columns: `id` PK, `vintage` NVARCHAR(400) NOT NULL (e.g. "RL25"; sandbox
values up to 371 chars observed, and the value must round-trip verbatim into submission, so no
truncation), `currency_scheme_code` NVARCHAR(50) NOT NULL (name-based link to the scheme,
Article 2 — no FK), `effective_date` DATETIME2 NOT NULL, audit. The builder defaults a template's
vintage to the chosen scheme's latest by `effective_date`.

## Templates & suites (DATA_MODEL §7 with deltas)

Deltas from DATA_MODEL §7, to be reconciled there at implementation: (a)
`analysis_template_tag.tag_name` replaces `irp_tag_id` (R7); (b) `auto_name_pattern`,
`region_label`, and `peril_code` are dropped (R11); (c) new
`treat_construction_occupancy_as_unknown` column (spec FR-005, wheel parameter); (d) no separate
`created_by` — `inserted_by` is the authorship record (one fact, one column); (e)
`treaty_name_pattern` is dropped (spec P-09 / O15-6 — treaties selected at run time in
Iteration 7); (f) `currency_scheme_code` and `currency_vintage` join `currency_code` — all three **required
NOT NULL** (spec P-10 as reversed 2026-08-19 / plan T-07/T-09): every template pins the full set
of currency values submission needs; NULL is never stored and no default is resolved at submit
time — Iteration 7 submits the stored values as-is; (g)
`template_suite_item` loses `position` and `portfolio_name_override` — suites are unordered
(spec P-08).

### `analysis_template`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `name` | NVARCHAR(200) NOT NULL | filtered-unique on live rows |
| `analysis_profile_name` | NVARCHAR(200) NOT NULL | RM model-profile name (name-based coupling, Art. 2) |
| `output_profile_name` | NVARCHAR(200) NOT NULL | |
| `event_rate_scheme_name` | NVARCHAR(200) NULL | required when the cached profile is DLM (validated at save) |
| `currency_code` | NVARCHAR(20) NOT NULL | submission `code` (e.g. "USD") — kept from the US1 build; always required |
| `currency_scheme_code` | NVARCHAR(50) NOT NULL | submission `scheme` (e.g. "RMS") — P-07/P-10; name-based coupling, Art. 2; required — never NULL, no "Default" state, no default resolution at submit time (the submission API never defaults; Iteration 7 sends the stored value) |
| `currency_vintage` | NVARCHAR(400) NOT NULL | submission `vintage` (e.g. "RL25") — required; width matches `irp_currency_scheme_vintage.vintage` (probe: values up to 371 chars exist upstream); the builder pre-selects the chosen scheme's latest by effective date; `asOfDate` derives from the vintage's effective date at submit time, never stored |
| `min_loss_threshold` | DECIMAL(18,2) NOT NULL DEFAULT 1.00 | FR-005: numeric, 2 dp |
| `num_max_loss_event` | INT NOT NULL DEFAULT 1 | |
| `franchise_deductible` | BIT NOT NULL DEFAULT 0 | |
| `treat_construction_occupancy_as_unknown` | BIT NOT NULL DEFAULT 1 | 1 = "Treat as unknown", 0 = "Skip location during analysis" (R8) |
| `deleted_at` | DATETIME2 NULL | soft delete |
| audit | | `inserted_at/updated_at`, `inserted_by/updated_by` (authorship = `inserted_by`) |

No currency-pair CHECK: P-10 as reversed makes both columns NOT NULL, which supersedes the
briefly-specced `ck_analysis_template_currency_pair` (both-or-neither) — the column constraints
are the whole rule.

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
`analysis_template.id`, `inserted_at`, `inserted_by`. Constraints: `UNIQUE(suite_id, template_id)`
(a template at most once per suite, FR-012). No `position` and no `portfolio_name_override` —
suites are unordered plain membership (spec P-08); display sorts by template name. Items are
hard-deleted/re-written as part of editing their suite (composition rows, no independent
lifecycle).

## Validation rules (service layer, unit-tested)

- **DLM scheme + pairing rules (T-06 utility)**: on save, classify the template's
  `analysis_profile_name` via the cache: `is_accumulation` → Accumulation, else the T-06
  irp-integration classification utility (DLM/HD). Only DLM requires `event_rate_scheme_name`;
  missing → reject naming the rule. When both the profile and the scheme resolve in the cache,
  the scheme's `(peril_code, model_region_code)` must match the profile's → mismatch rejected
  (same rule submit enforces). A side absent from the cache → that check is skipped and the
  template is *unresolved* (R9); never a save-blocker (FR-011).
- **Currency rules (P-10)**: `currency_code`, `currency_scheme_code`, and `currency_vintage` are
  all required — a missing value rejects the save naming the field (belt to the NOT NULL columns'
  suspenders; NULL is never stored). The builder pre-selects the chosen scheme's latest vintage
  by `effective_date`; a scheme with zero cached vintages blocks the save, naming the scheme.
  When both the scheme and vintage resolve
  in the cache, the vintage must belong to the scheme (`currency_scheme_code` match); either side
  absent from the cache → check skipped, template unresolved (R9), same posture as the DLM rules.
  Vintage lookups (validation and the R9 read-time unresolved flag) use EXISTS-style semantics,
  never a bare LEFT JOIN — the raw-snapshot vintage cache has no unique key, and a duplicate row
  must not fan out template reads. Currency-in-scheme membership is deliberately **not**
  validated (deferred — trusted admin; fails at submit in Iteration 7).
- **Name uniqueness**: duplicate live template/suite name → reject (DB filtered-unique is the
  guarantee; `is_unique_violation` absorbed into the form error).
- **Delete guard**: deleting a template referenced by live suites → blocked, referencing suite
  names returned (FR-010).

## Seed data

- `rwb_job_type_kind` += `('sync_irp_metadata', 'Sync IRP metadata', …)` (0001 inline seed +
  `infra/scripts/seed_db.py`, both).
- No suite or template content is seeded — setup is manual (spec P-02; Excel import deferred).
  No kind-table rows for templates/suites themselves (DATA_MODEL §13 unchanged).

## Test schema mirror

`tests/iteration1_mirror.py` gains the 10 new tables (6 reference-cache + 4 template/suite — the
scheme and vintage tables join when T-07's swap runs) in a new `ITERATION4_SCHEMA` block (SQLite
DDL), registered in the drift-guard lists so `tests/sqlserver/test_schema_drift.py` compares them
against the real migration.
