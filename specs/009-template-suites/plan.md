# Implementation Plan: Analysis Templates & Template Suites — Definition & Administration

**Branch**: `009-template-suites` | **Date**: 2026-08-18 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/009-template-suites/spec.md`

## Review summary

**What changes in the system** (details in [data-model.md](data-model.md),
[contracts/routes.md](contracts/routes.md),
[contracts/transfer-workbook.md](contracts/transfer-workbook.md), evidence in
[research.md](research.md) — decisions R1–R14):

- 10 new WORKBENCH tables in `alembic/versions/0001_initial.py`: 6 reference-cache tables
  (`irp_model_profile`, `irp_output_profile`, `irp_event_rate_scheme`, `irp_currency`,
  `irp_currency_scheme`, `irp_currency_scheme_vintage`) and 4
  template tables (`analysis_template`, `analysis_template_tag`, `template_suite`,
  `template_suite_item`), plus a `sync_irp_metadata` row in `rwb_job_type_kind`. **Currency
  additions (T-07, 2026-08-18)**: the built `irp_currency` table stays; `irp_currency_scheme` and
  `irp_currency_scheme_vintage` are added alongside it — their columns were pinned when the
  irp-integration scheme/vintage reads shipped in `0.6.0rc2` (released & pinned 2026-08-19,
  task T045).
- `app/services/irp_gateway.py` gains reference-data list methods (dataclasses + Protocol +
  real impl + `FakeIRP`) — the first use of the wheel's `reference_data` manager. Four are built
  (model profiles, output profiles, event-rate schemes, currencies); `list_currency_schemes` and
  `list_currency_scheme_vintages` build on the irp-integration reads released in `0.6.0rc2`
  (T-07 — pinned 2026-08-19, task T045). A
  seventh, `list_accumulation_profiles`, is **deferred**: the accumulation read it needs in
  irp-integration is tabled (T-02, 2026-08-18); the schema keeps `is_accumulation` (default 0) so
  resuming is additive. Separately, irp-integration gains a pure classification/pairing validation utility
  (T-06) — DLM/HD classification and the DLM-requires-scheme + scheme-peril/region-pairing rules,
  extracted from the wheel's submit path (`analysis.py:246-296`) so template save and
  analysis submit enforce one implementation, never re-implemented app-side. The T-06 utility
  **landed and was validated 2026-08-18**: `irp_integration.analysis_validation` ships
  `classify_model_profile` and `validate_analysis_settings` in the `0.6.0rc1` pre-release, the
  wheel's submit path is refactored onto it, and this repo consumes it via the pinned TestPyPI
  build (`make irp-testpypi`, `irp-integration[databridge]==0.6.0rc1`).
- New worker `app/workers/metadata_jobs.py`: `sync_irp_metadata` actor refreshes all the cache
  tables in one transaction (snapshot upsert + hard delete of rows the fetch no longer returned —
  cache rows have no soft delete); enqueued from the metadata page via `ensure_pending_rwb_job` with
  a fixed sentinel requestor — a sync requested while one is pending or running is refused with
  a "sync already in progress" message, never interleaved (T-01, FR-002).
- New router `app/routers/templates.py` + templates under `app/templates/pages/`: the five-tab
  metadata page (first use of the existing `.tabs` CSS component; filter fragments follow the
  `edm_library` HTMX pattern) and the suite-administration pages (server-rendered forms,
  `form-banner` errors). The `/templates` stub handler in `app/routers/shell.py` is removed; the
  existing `templates` nav root gains `templates.suites` and `templates.metadata` children.
- New service `app/services/template_service.py`: template/suite CRUD + validation (DLM-requires-
  scheme and scheme-peril/region pairing via the T-06 irp-integration utility — applied at save,
  skipped when a side is absent from the cache — live-name uniqueness, delete
  guard naming referencing suites, read-time unresolved flags) and the scheme filter/pre-fill
  query (T-03).
- **Out of MVP scope (spec P-02)**: Excel export/import and starter-suite seeding — nothing is
  seeded and no transfer routes/services ship; initial setup is manual via the admin pages. The
  Excel flows are a nice-to-have enhancement; the worked design is retained in
  [contracts/transfer-workbook.md](contracts/transfer-workbook.md).
- Role gating per P-01: mutating routes use the `_require_admin` helper pattern; viewing and sync
  stay open to every analyst; mutation controls hidden from non-admins in templates.
- Tests: `ITERATION4_SCHEMA` block in `tests/iteration1_mirror.py` + drift-guard entries; unit
  tests for worker, routes, validation, admin gating; sqlserver migration
  assertions; an `--run-irp` test pinning the R1 response shapes.

**Risks**: (1) ~~the T-06 utility is built in a separate irp-integration effort and gates the
three tasks that call it~~ — **resolved 2026-08-18**: the utility shipped in `0.6.0rc1` and is
validated against this plan's expectations, so the marker fragment and save validation
are unblocked; the tabled T-02 accumulation read means no Accumulation rows sync this pass
(FR-004's three-way marker has data for two of three classes until it resumes — revisit the spec
if it slips past the iteration), and the accumulation columns in data-model.md stay provisional
until its spike runs; (2) ~~the currency-scheme and scheme-vintage reads (T-07) exist in
the irp-integration working copy but not in a released build~~ — **resolved 2026-08-19**:
`irp-integration==0.6.0rc2` ships `search_currency_schemes`, `search_currency_scheme_vintages`,
and `get_latest_currency_scheme_vintage` (released & pinned via `make irp-testpypi`, task T045);
the `irp_currency_scheme`/`irp_currency_scheme_vintage` columns and
`CurrencySchemeEntry`/`CurrencySchemeVintageEntry` fields are pinned, and nothing external gates
the scheme/vintage pick lists or any remaining task.

**Decisions**:

| ID | Decision | Status |
|---|---|---|
| T-01 | Metadata sync is a `sync_irp_metadata` rwb_job worker, not a request-path read; a sync requested while one is pending or running is refused with a "sync already in progress" message, and the pending-dedup index makes the refusal race-safe (R5). | Approved via research |
| T-02 | Marker is three-way: irp-integration gains an accumulation-profile read (new endpoint, built in the sibling checkout); accumulation rows land in `irp_model_profile` with `is_accumulation=1`; non-accumulation rows classify DLM/HD by the wheel's rule via the T-06 utility, raw version shown (R3, R2). Endpoint shape pinned by a sandbox spike first. | **Deferred 2026-08-18** — accumulation tabled; schema keeps `is_accumulation` (default 0), sync ships six sets until the read lands (tasks: *Deferred: accumulation*) |
| T-03 | Event-rate pre-fill: filter schemes to the profile's `(peril_code, model_region_code)`; pre-fill only on exactly one active match; `isDefault` rejected as ambiguous (R4). | Approved via research |
| T-04 | Excel export/import (transfer workbook, R6) — **out of MVP scope** (spec P-02); worked design retained in `contracts/transfer-workbook.md` as a nice-to-have enhancement. | Deferred 2026-08-19 |
| T-05 | Starter-suite seeding (R10) — **out of MVP scope** (spec P-02); nothing is seeded, setup is manual. | Deferred 2026-08-19 |
| T-06 | DLM/HD classification and the DLM-requires-scheme + scheme-peril/region-pairing validation ship as a pure (no-I/O) utility in irp-integration, extracted from the submit path; the workbench calls it at template save, and the wheel's submit refactors onto it — one implementation, nothing replicated app-side. Supersedes R2's "derive with the wheel's exact rule" replication. | **Landed & validated 2026-08-18** — `irp_integration.analysis_validation` (`classify_model_profile`, `validate_analysis_settings`) in `0.6.0rc1`; submit path refactored onto it; consumed via pinned TestPyPI build, `make irp-testpypi` (tasks T003) |
| T-07 | The workbench caches and stores **all three currency objects** (spec P-07 as amended 2026-08-18): analysis submission's currency block is `{code, scheme, vintage, asOfDate}`, so `irp_currency` is **kept** (with its P-06 truncation) and two cache tables are **added** — `irp_currency_scheme` and `irp_currency_scheme_vintage` — with matching gateway reads `list_currency_schemes` and `list_currency_scheme_vintages` alongside the existing `list_currencies`. The metadata screen gains a fifth tab, Currency Schemes (schemes with their vintages); the existing Currencies tab **stays** (reversed 2026-08-19, user-corrected — D3's "not tabbed" call is dropped: see research.md R13). `analysis_template` keeps `currency_code` and gains `currency_scheme_code` + `currency_vintage`; `asOfDate` derives from the cached vintage's effective date at submit time. The scheme/vintage reads (`search_currency_schemes`, `search_currency_scheme_vintages`) shipped in `irp-integration==0.6.0rc2` — columns/fields pinned by the release plus a 2026-08-19 sandbox probe (research R13): vintage items carry **no id** and no unique natural key, so `irp_currency_scheme_vintage` is a raw snapshot (no `irp_id`, no unique index, delete-all + insert per sync) with `vintage` NVARCHAR(400); the scheme cache drops `is_default` (nothing consumes it after the P-10 reversal). This resolves O15-2: the template stores the member currency *and* the scheme (and the vintage). | Approved 2026-08-18, amended same day — external release landed 2026-08-19 (task T045); shapes probe-pinned 2026-08-19; tab reversed same day |
| T-08 | Suites are unordered (spec P-08): `template_suite_item` drops `position` and `portfolio_name_override`; display order is normalized by name. `analysis_template.treaty_name_pattern` is dropped with it (spec P-09). | Approved 2026-08-18 |
| T-09 | Currency scheme + vintage are **required NOT NULL** on `analysis_template` (spec P-10 as reversed 2026-08-19 — T-07's NOT NULL columns stand; no pair CHECK exists or is needed). No "Default" state and no default logic at submit time: Iteration 7 submits the stored values as-is (`asOfDate` derived from the stored vintage's effective date — the submission API never defaults either; a full currency block is always sent). Builder: currency, scheme, and vintage all required; the vintage is pre-selected to the chosen scheme's latest by effective date (changeable, via an HTMX vintage-options fragment); a scheme with zero vintages blocks save naming the scheme. All pick lists substring-filter the local cache — no live Risk Modeler queries from dropdowns. Currency-in-scheme membership deliberately unvalidated (deferred; trusted admin). | Approved 2026-08-19 — reversed same day from an optional pair |
**Constitution check** (v3.1.0) — no violations; the articles that shaped the design:

- **Article 11**: `reference_data.get_*` calls are reads, not job polling, but they run
  worker-side anyway (T-01) — the web layer never calls the wheel's network methods; sync enqueue
  + dispatch is the only request-path action. (The EDM-sync precedent's inline-read latitude was
  considered and not needed — R5.) The T-06 validation utility is pure (no I/O), so importing it
  from `template_service` on the request path touches no IRP interface Article 11 governs.
- **Article 2**: templates store profile/scheme/currency/vintage **names/codes**, resolved live by Risk Modeler
  at submit time (Iteration 7); the cache exists for pick lists and validation, never as a typed
  handle registry.
- **Article 3**: the two new boolean settings are API parameters, not categoricals — no kind
  tables; the DLM/HD half of the marker is derived, never stored, and `is_accumulation` records
  which endpoint returned the row (a source fact, not an app-defined category). The one new
  categorical (`sync_irp_metadata` job type) is a `rwb_job_type_kind` row.
- **Articles 1/8**: two nav-manifest children + handlers + templates; five tabs are one real URL
  with `?tab=` + HTMX fragments and `hx-push-url`; no Alpine beyond existing slivers.
- **Article 6/13**: roles gate *functions* (admin mutations, P-01), never rows; CSRF on every
  POST.
- **Article 10**: the sync job rides the existing rwb_job queue/claim/heartbeat machinery
  unchanged.

**How to verify**: [quickstart.md](quickstart.md) — per-story walkthroughs plus the three test
tiers. UI note (docs/UI_WORKFLOW.md): the metadata tab page and the suite/template builder have
real new layout → rendered HTML previews from `docs/ui_previews/_scaffold.html` before building;
one user story implemented end-to-end at a time.

## Technical Context

**Language/Version**: Python 3.13 (uv-managed)
**Primary Dependencies**: FastAPI + Jinja2 + HTMX (Article 8), SQLAlchemy Core via `/db`,
Dramatiq + Redis (existing worker runtime), irp-integration (source-switchable; the
T-06 validation utility shipped in the `0.6.0rc1` pre-release and the T-07 currency-scheme +
scheme-vintage reads in `0.6.0rc2` (2026-08-19) — accumulation read still tabled; development
runs on the pinned TestPyPI build, `make irp-testpypi`)
**Storage**: SQL Server — WORKBENCH database only (Article 6); SQLite injected via
`register_engine` in the unit tier
**Testing**: pytest, three tiers (`tests/unit`, `tests/sqlserver`, `tests/irp`)
**Target Platform**: Linux server (Docker `linux-box` mirror)
**Project Type**: server-rendered web app
**Scale/Scope**: cache ≈ 4.8k rows/sync (R1); tens of templates, single-digit suites; 2 new pages
+ builder/detail forms

No NEEDS CLARIFICATION remain: the spec's two plan-deferred questions (accumulation
classification, event-rate pre-fill) are closed by the 2026-08-18 sandbox probe plus review —
accumulation comes from a new irp-integration read (R3/T-02), pre-fill from the peril/region
filter (R4/T-03). The spec's 2026-08-18 clarification session fixed the sync-refusal message —
reflected in T-01 and the contracts.

## Project Structure

### Documentation (this feature)

```text
specs/009-template-suites/
├── plan.md              # this file
├── research.md          # probe evidence + decisions R1–R14
├── data-model.md        # 10 tables, validation rules, seeds, test mirror
├── quickstart.md        # per-story verification + test tiers
└── contracts/
    ├── routes.md            # nav nodes, pages, fragments, gateway + worker contracts
    └── transfer-workbook.md # .xlsx export/import design — DEFERRED enhancement (P-02), not built in MVP
```

### Source code (changed directories only)

```text
alembic/versions/0001_initial.py      # +10 tables (2 with T-07), +1 kind row, filtered unique indexes
app/nav/manifest.py                   # +templates.suites, +templates.metadata children
app/routers/templates.py              # NEW — all routes (contracts/routes.md)
app/routers/shell.py                  # -templates_page stub
app/services/irp_gateway.py           # +reference-data dataclasses/methods (profiles, schemes,
                                      #   currencies now; currency schemes + vintages with T-07)
app/services/template_service.py      # NEW — CRUD, validation, scheme pre-fill
app/workers/metadata_jobs.py          # NEW — sync_irp_metadata actor
app/templates/pages/                  # templates.html (rework), templates_metadata.html, forms
app/templates/partials/               # metadata table fragment, scheme options, suite item rows
infra/scripts/seed_db.py              # +sync_irp_metadata kind row
../../IRP/irp-integration             # sibling repo: pure classification/pairing validation
                                      #   utility (T-06) shipped in 0.6.0rc1; currency-scheme +
                                      #   vintage reads (T-07) shipped in 0.6.0rc2 (2026-08-19);
                                      #   accumulation read (T-02) tabled
docs/ui_previews/                     # metadata + builder previews (UI-first)
docs/DATA_MODEL.md                    # reconcile §7 deltas (tag_name, occupancy column, created_by,
                                      #   dropped auto_name_pattern/region_label/peril_code/
                                      #   treaty_name_pattern, currency scheme+vintage columns,
                                      #   unordered suite items) + §10 columns incl.
                                      #   irp_currency_scheme + irp_currency_scheme_vintage
tests/iteration1_mirror.py            # +ITERATION4_SCHEMA, drift lists
tests/unit/                           # worker, routes, validation, gating tests
tests/sqlserver/                      # migration assertions for the new tables/indexes
tests/irp/                            # reference-data shape test (R1)
```

**Structure Decision**: everything follows the existing single-app layout; no new top-level
directories. One router, one service, one worker module — the "add a page = nav node + handler +
template" rule (Article 1) applied twice.

## Complexity Tracking

No constitution violations to justify.
