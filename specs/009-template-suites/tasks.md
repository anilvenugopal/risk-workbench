# Tasks: Analysis Templates & Template Suites — Definition & Administration

**Input**: Design documents from `specs/009-template-suites/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R15), data-model.md, contracts/routes.md, quickstart.md

**Tests**: Included — the plan mandates the three-tier test strategy (Article 12): unit tests for worker/routes/validation/gating, sqlserver migration assertions, and an `--run-irp` shape test.

**Organization**: Tasks are grouped by user story. One story is implemented end-to-end per pass (docs/UI_WORKFLOW.md); each story phase ends at a checkpoint for the approver to click the running slice.

**2026-08-19 revision**: task descriptions updated to the amended spec (P-07 currency schemes/vintages, P-08 unordered suites, P-09 treaty-pattern drop, P-10 currency pair). A task whose requirements changed is unchecked, even where an earlier version was built — the note on each says what stands. The former rework tasks T045–T050 were folded back into the tasks they extended; only T045 (external release tracking, done) keeps its ID in Phase 1, and T046 was re-cut in Phase 4 as the deferred Excel/seed code removal. **Later the same day P-10 was reversed**: currency scheme + vintage are required NOT NULL (no pair CHECK, no "Default" display, no submit-time default logic); the affected task descriptions below are re-worded in place.

**2026-08-20 revision (design note 17)**: currency comes off templates entirely (P-11 — reverses P-10; history in research.md R13), duplicate-and-edit is added (P-12/FR-021), and the event-rate options must populate on profile selection (O17-9). The checked-off T021–T031 records below describe the pre-note-17 build they produced; the new tasks T047–T050 (IDs re-cut — the former T047–T050 were folded away in the 2026-08-19 revision) amend that build rather than rewriting the done records.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 / US2 (user story phases only; the former US3 — Excel export/import — is out of MVP scope, spec P-02)

---

## Phase 1: Cross-repo prerequisite (irp-integration — built externally)

**Purpose**: Track the irp-integration work this feature consumes. Both deliverables have shipped; the accumulation-profile read is **tabled** (old T001/T002 — moved to *Deferred: accumulation* below).

- [x] T003 The T-06 utility landed in `irp-integration==0.6.0rc1`; consumed via the pinned TestPyPI build (`make irp-testpypi`, pin in the `irp-testpypi` dependency group). Validated 2026-08-18 (research.md R2): `irp_integration.analysis_validation` exposes `classify_model_profile` + `validate_analysis_settings`, pure (no `IRPClient`), and the wheel's submit path is refactored onto it
- [x] T045 Currency-scheme + scheme-vintage reads (T-07, R13) **released and pinned 2026-08-19**: `irp-integration==0.6.0rc2` ships `search_currency_schemes`, `search_currency_scheme_vintages`, and `get_latest_currency_scheme_vintage` (verified in the installed wheel). This pins the `irp_currency_scheme`/`irp_currency_scheme_vintage` columns (data-model.md) and the `CurrencySchemeEntry`/`CurrencySchemeVintageEntry` fields (contracts/routes.md) — nothing external gates any remaining task

**Checkpoint**: reached — both utilities importable from the pinned `0.6.0rc2` build.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, seed row, nav wiring, and router skeleton that every user story builds on.

**⚠️ CRITICAL**: The schema tasks below must be complete before the user-story code that touches the affected tables.

- [X] T004 The 6 reference-cache tables in `alembic/versions/0001_initial.py` per data-model.md — `sa.Uuid` PK/`NEWID()`, `DATETIME2`, `GETUTCDATE()` audit defaults, plain unique index on the natural key (`irp_id`; `code` for currencies; **none** for scheme vintages — no upstream id or unique natural key, raw-snapshot rows), no `deleted_at` (cache rows hard-delete). *4 of 6 built (`irp_model_profile`, `irp_output_profile`, `irp_event_rate_scheme`, `irp_currency`); remaining: `irp_currency_scheme` (no `is_default`) and `irp_currency_scheme_vintage` (`vintage` NVARCHAR(400)) — columns pinned by the released `0.6.0rc2` reads + the 2026-08-19 sandbox probe (R13). Done 2026-08-19: both tables added.*
- [X] T005 The 4 template tables (`analysis_template`, `analysis_template_tag`, `template_suite`, `template_suite_item`) in `alembic/versions/0001_initial.py` per current data-model.md — filtered unique live-name indexes on `analysis_template.name` and `template_suite.name`, `UNIQUE(suite_id, template_id)` on suite items, composite PK `(template_id, tag_name)` on tags, R8 column defaults (1.00 / 1 / 0 / 1), NOT NULL `currency_scheme_code` + `currency_vintage` (P-10 as reversed — required, no pair CHECK), no `treaty_name_pattern` (P-09), no `position`/`portfolio_name_override` on suite items (P-08). *Built pre-trim; remaining: drop the three dropped columns, add the two NOT NULL currency columns, fix the "ordered template suites" comment. Done 2026-08-19: three columns dropped, two NOT NULL currency columns added, comment fixed. Note: this deltas the shape `template_service.py` (T021/T022) still reads/writes — those tasks and their tests are expected red until Phase 4 lands.*
- [x] T006 Seed the `('sync_irp_metadata', 'Sync IRP metadata', …)` row into `rwb_job_type_kind` in both the `alembic/versions/0001_initial.py` inline seed and `infra/scripts/seed_db.py`
- [X] T007 [P] The `ITERATION4_SCHEMA` block (SQLite DDL for all 10 tables) in `tests/iteration1_mirror.py`, registered in the drift-guard lists consumed by `tests/sqlserver/test_schema_drift.py`. *Built for the pre-trim 8; remaining: mirror the T004/T005 deltas. Done 2026-08-19: `irp_currency_scheme`/`irp_currency_scheme_vintage` added, `analysis_template`/`template_suite_item` trimmed to match, both new tables added to `EXACT_MATCH_TABLES`.*
- [X] T008 [P] SQL Server migration assertions for the 10 tables, their unique/filtered indexes, the NOT NULL currency columns, and the kind row in `tests/sqlserver/test_template_suite_schema.py`. *Built for the pre-trim 8; remaining: assert the T004/T005 deltas. Done 2026-08-19: both new tables + `irp_currency_scheme`'s unique index added to the parametrized assertions, a NOT NULL check for `currency_scheme_code`/`currency_vintage`, and an absence check for the three dropped columns.*
- [x] T009 Add `templates.suites` (label "Template Suites", route `/templates`, roles `[]`) and `templates.metadata` (label "Analysis Metadata", route `/templates/metadata`, roles `[]`) children under the existing `templates` root in `app/nav/manifest.py`
- [x] T010 Create `app/routers/templates.py` skeleton (literal sub-paths before parameterized, EDM-router precedent), register it before `shell.router` in the app factory, and remove the `/templates` stub handler from `app/routers/shell.py:49` — skeleton serves a minimal `GET /templates` placeholder so the nav route keeps resolving until US2 reworks the page

**Checkpoint**: `make db-rebuild` migrates all 10 tables with the trimmed columns and the NOT NULL currency columns; nav shows both children; unit + sqlserver schema tests green.

---

## Phase 3: User Story 1 — Sync and view analysis metadata (Priority: P1) 🎯 MVP

**Goal**: On-demand `sync_irp_metadata` worker refreshes the six cache tables from Risk Modeler; a five-tab read-only metadata page (fourth tab: currencies; fifth tab: currency schemes with their vintages) lists them filterable with DLM/HD/Accumulation markers and last-synced time.

**Independent Test**: Run the sync against the IRP sandbox; the five tabs match Risk Modeler's lists, filtering narrows ~3,500 profiles to a UD profile without a reload, no create/edit control exists, a second sync while one runs is refused with "sync already in progress", and a failed sync leaves the cache and last-synced time intact (quickstart US1).

### Implementation for User Story 1

- [x] T011 [US1] 6 frozen dataclasses (`ModelProfileEntry`, `OutputProfileEntry`, `EventRateSchemeEntry`, `CurrencyEntry`, `CurrencySchemeEntry`, `CurrencySchemeVintageEntry`) and the 6 `list_*` Protocol methods + real implementation via `client.reference_data` in `app/services/irp_gateway.py`, field lists per contracts/routes.md (`list_accumulation_profiles` deferred — see *Deferred: accumulation*). *Done 2026-08-19: `CurrencySchemeEntry`/`list_currency_schemes` (explicit `where_clause="isActive=True"`, per the wheel's `search_currency_schemes` signature) and `CurrencySchemeVintageEntry`/`list_currency_scheme_vintages` added.*
- [x] T012 [P] [US1] Mirror the 6 list methods in `tests/unit/fakes/fake_irp.py` with configurable sample data covering DLM (`RL25`), HD (`HDv3.0`), and `Open` rows, plus schemes with multiple vintages. *Done 2026-08-19: `currency_schemes` (RMS, DT) and `currency_scheme_vintages` (RMS: RL25 latest + RL23; DT: RL24 — a code RMS doesn't share) added.*
- [x] T013 [US1] The `sync_irp_metadata` Dramatiq actor in `app/workers/metadata_jobs.py` (name-based dispatch, body via `runtime.run_job`): fetch all six sets, truncate currency names to Risk Modeler's 16-character creation limit (P-06), then one WORKBENCH transaction — snapshot upsert keyed on `irp_id` (currencies: `code`; scheme vintages: **delete-all + insert** — no upstream id or unique key, duplicates stored as returned), hard delete of rows the fetch no longer returned; return `JobResult.ok(synced counts)` / `.fail(reason)`; a gateway failure aborts before any write (FR-002). *Done 2026-08-19: scheme fetch synced via the existing keyed `_sync_table` helper; vintages via a new `_replace_table` helper (delete-all + insert, no natural key).*
- [x] T014 [P] [US1] Unit tests for the worker in `tests/unit/test_metadata_sync_worker.py` (fake IRP): initial populate, re-sync removes vanished rows and updates changed names, legacy currency names truncated to 16 characters (P-06), fetch failure leaves prior cache rows intact and fails the job. *Done 2026-08-19: six-table populate/counts, scheme removal on resync, and a dedicated wholesale-replace test (seeds a duplicate vintage pair, asserts both stored, then resyncs to a disjoint set and asserts the old rows are gone). Inactive-scheme filtering is exercised at the gateway unit level (`test_irp_gateway.py`) since the fake has no active/inactive concept — filtering happens via the where_clause the real gateway sends.*
- [x] T015 [US1] UI preview `docs/ui_previews/templates_metadata.html` (from `docs/ui_previews/_scaffold.html`, reuse the existing `.tabs` CSS component): tabs with **currency schemes** (schemes + their vintages), per-tab filter input, DLM/HD/Accumulation marker + raw software version column, last-synced line, sync button, empty state — approved before wiring. *Done 2026-08-19: Currency Schemes tab added (schemes + vintage badges, incl. an empty-vintages scheme). Reversed same day (user-corrected): the Currencies tab, first dropped per P-07/D3, is restored alongside it — five tabs total. User 👍'd 2026-08-19 per docs/UI_WORKFLOW.md.*
- [x] T016 [US1] `GET /templates/metadata` page route in `app/routers/templates.py` + `app/templates/pages/templates_metadata.html`: five tabs (`?tab=model-profiles` default, `output-profiles`, `event-rate-schemes`, `currencies`, `currency-schemes`), tab links `hx-get` the fragment with `hx-push-url`, last-synced time and status/failure reason from the latest `sync_irp_metadata` rwb_job, `?sync=` banner messages; context built by a builder shared with the fragment route. *Done 2026-08-19: `currency-schemes` tab added; counts query now counts `irp_currency_scheme` too; last-synced UNION extended to both new tables. Reversed same day: `currencies` tab (briefly replaced by `currency-schemes`) restored alongside it, with its own `_metadata_rows`/counts branch.*
- [x] T017 [US1] `GET /templates/metadata/table` HTMX fragment + `app/templates/partials/metadata_table.html`: one tab's read-only table with filter input (`hx-trigger="input delay:300ms"`, edm_library pattern); model-profile tab derives the marker (is_accumulation → Accumulation, else the T-06 classification utility — never a re-implemented rule) and shows the raw version; currency-schemes tab lists each scheme with its vintages (vintage code + effective date). *Done 2026-08-19: currency-schemes tab renders scheme name/code + a badge per vintage (vintage code · effective date), "—" when a scheme has no cached vintages. Reversed same day: the currencies tab's own Code/Name/Country/Symbol table (briefly removed) is restored as an explicit branch alongside currency-schemes.*
- [x] T018 [US1] `POST /templates/metadata/sync` in `app/routers/templates.py`: CSRF-validated, open to every analyst; `ensure_pending_rwb_job` with the fixed sentinel requestor + dispatch, PRG to `?sync=queued`; when a sync job is already pending/running nothing is enqueued and PRG lands on `?sync=already-running` rendered as "sync already in progress" (FR-002)
- [x] T019 [P] [US1] Unit tests for the metadata routes in `tests/unit/test_templates_metadata_routes.py`: page renders five tabs, fragment filters, marker derivation shown, currencies tab renders/filters, currency-schemes tab renders vintages, sync enqueues once, second request refused with the message, failed-job reason displayed, no create/edit control in any tab's markup. *Done 2026-08-19: currency-schemes cases added (renders schemes + vintages, filters by name/code, empty-vintages marker); the RM deep-link test extended to cover both the `currencies` and `currency-schemes` tabs (same deep link). Reversed same day: currencies-tab render/filter tests restored.*
- [x] T020 [P] [US1] IRP-tier shape test `tests/irp/test_reference_data_shapes.py` (opt-in `--run-irp`): all six gateway reads return the documented fields against the sandbox. *Done 2026-08-19: `list_currency_schemes`/`list_currency_scheme_vintages` added to the sandbox assertions.*

**Checkpoint**: reached 2026-08-19 — the currency-scheme extension of US1, plus the same-day reversal restoring the Currencies tab, is built and unit-tested (926 unit tests green); the T015 preview is 👍'd. Only the sandbox shape assertions (T020) and the full quickstart US1 walkthrough against the live IRP sandbox remain to be run (`make shell` → `uv run pytest tests/irp --run-irp`, then quickstart.md). **STOP** — approver clicks the running slice before US2 begins.

---

## Phase 4: User Story 2 — Create and administer templates and suites (Priority: P2)

**Goal**: Admin builds analysis templates (cached pick lists, DLM-requires-scheme, R8 defaults, tags — no currency fields, P-11) and composes unordered suites (no duplicate members); duplicate-and-edit for both (P-12); everything global, mutations admin-gated, unresolved references flagged. Nothing is seeded — setup is manual (spec P-02).

**Independent Test**: As admin: create a DLM template (save blocked without a scheme), an HD template (saves without one), a template save missing the currency scheme or vintage (rejected naming the field), a mixed suite; deleting a referenced template is blocked naming the suite; as non-admin: everything visible, no mutation controls, direct POSTs rejected (quickstart US2).

### Implementation for User Story 2

- [X] T021 [US2] Classification + template CRUD/validation in `app/services/template_service.py`: classify a profile name via the cache (`is_accumulation` → Accumulation, else the T-06 classification utility); create/update/soft-delete templates with tag-row replacement; save-time validation via the T-06 utility — DLM-requires-`event_rate_scheme_name` and scheme-peril/region-must-match-profile (reject naming the rule; each check skipped when its cache row is absent — unresolved, never a save-blocker); **currency rules (P-10)** — currency, scheme, and vintage all required (missing → reject naming the field; NULL never stored), a vintage-less scheme blocks save naming the scheme, vintage-belongs-to-scheme when both resolve in the cache (skipped + unresolved otherwise, R9-style); live-name uniqueness (`is_unique_violation` absorbed into the form error); delete guard returning referencing live suite names (FR-010); read-time unresolved flags via LEFT JOIN (R9 — vintage lookups EXISTS-style, never a bare join: the raw-snapshot vintage cache can hold duplicates). *Built and green except: carries a dropped `treaty_name_pattern` field (P-09 — remove) and has no currency scheme/vintage fields or validation. Done 2026-08-19: `treaty_name_pattern` removed from `TemplateValues`/SQL; `currency_scheme_code`/`currency_vintage` added as required fields with `_validate_currency` (missing-field, zero-vintage, mismatched-pairing checks) plus EXISTS-style `currency_scheme_unresolved`/`currency_vintage_unresolved` read-time flags in `_TEMPLATE_SELECT` and `get_suite`'s item query.*
- [X] T022 [US2] Suite CRUD + item composition in `app/services/template_service.py`: create/update/soft-delete suites, items hard-rewritten on save as a plain **unordered set** (no position, no per-item settings — P-08; display sorts by template name), duplicate template per suite rejected (`UNIQUE(suite_id, template_id)`), no DLM/HD/accumulation mixing restriction. *Built and green except: items still carry `position` (renumbered on save) and `portfolio_name_override` — remove per P-08. Done 2026-08-19: both removed from `SuiteItemValues`, `save_suite`, and `get_suite`; item display now `ORDER BY t.name`.*
- [x] T023 [US2] Scheme filter/pre-fill query in `app/services/template_service.py` (T-03): live schemes matching the chosen profile's `(peril_code, model_region_code)`; pre-select only when exactly one active scheme matches; zero → empty, multiple → unselected list (built as `scheme_options`; `reference_options` supplies the builder pick lists)
- [X] T024 [P] [US2] Unit tests for the service in `tests/unit/test_template_service.py`: DLM rejection message, HD/Accumulation optional (HD *with* scheme allowed), peril/region-mismatched scheme rejected at save (and allowed when either side is absent from the cache), unresolved profile skips the rule, currency cases (missing scheme rejected, missing vintage rejected, vintage-less scheme blocks save naming the scheme, vintage-not-in-scheme rejected when both resolve), duplicate template/suite names rejected, delete guard names suites, unresolved flag appears/disappears across simulated re-syncs, scheme pre-fill exactly-one / zero / multiple cases, unordered suite items. *Built and green minus the currency-pair cases; the `renumbers_items_and_keeps_override` test asserts dropped behavior — replace it. Done 2026-08-19: `_values()`/`_sync()` updated (currency scheme/vintage seeded directly since the T011-T014 sync extension hasn't landed), the renumber test replaced with `test_suite_items_are_unordered_and_display_sorts_by_template_name`, and 5 new currency-validation tests added (missing scheme/vintage, zero-vintage scheme, mismatched pairing, unresolved-not-rejected). 916 unit tests green.*
- [X] T025 [US2] UI preview `docs/ui_previews/templates_admin.html` (from `_scaffold.html`): administration page (suite list with name/item count/author/unresolved badge + filterable template list), template builder form (pick lists — model-profile options carry their DLM/HD/Accumulation marker, FR-004; currency selection per P-10: currency, scheme, and vintage all required, the vintage defaulting to the chosen scheme's latest by effective date — analysis settings with R8 defaults, tags), suite builder (unordered item picker, empty-state marker). *Authored and committed 2026-08-19 with the optional-pair currency design; reworked 2026-08-20 for the P-10 reversal (required scheme+vintage selects, no "Default" state) and P-08/P-09 (unordered checkbox picker, no drag/position, no treaty-name-pattern field); Export/Import buttons dropped (out of MVP scope, P-02). User feedback on this rework (2026-08-20) drove two further changes carried into T026/T029: Suites and Templates split into separate tabs on `/templates` (never listed together), and the suite builder's template picker gained a filter box. Built only with classes already in app.css/components.css/submissions.css — no invented look.*
- [X] T026 [US2] `GET /templates` administration page: reworked `app/templates/pages/templates.html` + handler in `app/routers/templates.py` — suite list and filterable template list visible to all; create/edit/delete controls rendered only for `is_admin`. *Done 2026-08-20, extended past the original route contract per user feedback: Suites and Templates render as two tabs of one page (`.tabs`, mirroring the T016 metadata pattern) rather than two lists stacked together — `GET /templates?tab=suites|templates` + a new `GET /templates/table` HTMX fragment (same tab/hx-push-url pattern as `/templates/metadata/table`); contracts/routes.md updated to match.*
- [X] T027 [US2] Template builder routes in `app/routers/templates.py` + form template `app/templates/pages/analysis_template_form.html`: `GET /templates/analysis-templates/new`, `POST /templates/analysis-templates` (create), `GET /templates/analysis-templates/{id}` (detail — edit form for admins, read-only for other analysts, unresolved flags inline), `POST /templates/analysis-templates/{id}` (update), `POST /templates/analysis-templates/{id}/delete` — form-banner validation errors, R8 defaults pre-filled, pick lists from live cache with the DLM/HD/Accumulation marker on model-profile options (FR-004), P-10 currency selection (currency, scheme, and vintage all required), tags entered as names with a `<datalist>` autocomplete over names already used on templates (FR-006). *Done 2026-08-20. A select bound to a stored value the cache no longer has (FR-011 unresolved) gets a synthetic marked-selected option (`_select_options` in templates.py) so an unrelated edit-and-save can never silently swap it out.*
- [X] T028 [US2] Builder option fragments in `app/routers/templates.py`: `GET /templates/analysis-templates/scheme-options` + `app/templates/partials/scheme_options.html` (`<option>` list for `?profile=<name>` via the T023 query, pre-selected when exactly one; triggered on profile change) and `GET /templates/analysis-templates/vintage-options` + `app/templates/partials/vintage_options.html` (`<option>` list of cached vintages for `?scheme=<code>`, latest by effective date pre-selected; empty scheme param → empty list — no scheme chosen yet, the form cannot submit without one; triggered on currency-scheme change, P-10). *Done 2026-08-20; `template_service.vintage_options()` added (collapses duplicate raw-snapshot vintage rows to their latest `effective_date`). Both partials double as the main form's initial-render include (`{% with options=... %}`) and the HTMX fragment response, so the two never drift.*
- [X] T029 [US2] Suite builder routes in `app/routers/templates.py` + template `app/templates/pages/suite_form.html`: `GET /templates/suites/new`, `POST /templates/suites` (create with items), `GET /templates/suites/{id}` (detail — edit form for admins, read-only for other analysts, items sorted by template name, empty-state marker), `POST /templates/suites/{id}` (update — items rewritten), `POST /templates/suites/{id}/delete`; item rows partial `app/templates/partials/suite_item_rows.html`. *Done 2026-08-20 as a plain unordered checkbox picker (P-08 — no drag handles, no position numbers). Per user feedback (2026-08-20), the picker got a client-side filter box (`#suite-item-filter`, plain JS over `.picker-item[data-name]` — no HTMX round trip needed at suite/template scale).*
- [X] T030 [US2] Admin gating pass over `app/routers/templates.py`: `_require_admin` (pattern `app/routers/admin.py:19`) on every mutating route, CSRF on every POST, mutation controls hidden from non-admins in all templates (P-01). *Done 2026-08-20 — applied to all 8 admin-only routes (both `GET .../new` forms plus every POST); read routes stay open to every analyst and branch render-only vs. edit-form on `current_user.is_admin`.*
- [X] T031 [P] [US2] Unit tests for routes + gating in `tests/unit/test_templates_routes.py`: create/edit/delete flows, DLM rejection re-renders with the rule named, currency form errors (missing scheme, missing vintage, vintage-less scheme rejected naming the scheme), duplicate-name form error, delete-guard message, item add/remove round-trip, same-template-twice blocked, non-admin sees no controls and direct POSTs are rejected, unresolved badge renders. *Done 2026-08-20 — 32 tests, plus the tabs/search additions (separate-tabs rendering, picker search box present) and the option-fragment pre-fill/empty cases. Also fixed a pre-existing `tests/unit/test_templates_foundation_routes.py` test that asserted the old DB-free Phase-2 placeholder — it now needs `iteration2_db` since `GET /templates` reads the template/suite tables. 958 unit tests green.*
*T032–T035 (workbook parse/import, seed workbook, seed wiring, seed tests) are out of scope — Excel flows and seeding are deferred (spec P-02). Some of that code was built before the deferral; T046 removes it.*

- [X] T046 [US2] Remove the deferred Excel/seed code built before the cancellation: the workbook parse + import-apply in `app/services/template_service.py`, the starter-seed import wiring in `infra/scripts/seed_db.py` (the `sync_irp_metadata` kind row from T006 stays), `infra/scripts/starter_suites.xlsx`, and the tests `tests/unit/test_starter_seed.py` + `tests/unit/test_template_workbook_import.py`. *Done 2026-08-19: workbook parse/import + `WorkbookError`/`ImportResult` removed from `template_service.py` (along with the now-unused `openpyxl` import), `_seed_starter_suites` and its call site removed from `seed_db.py`, both test files deleted. **`infra/scripts/starter_suites.xlsx` intentionally left in place** — it's currently modified in the working tree with a `~$` Excel lock file present (someone has it open); left for the user to remove once confirmed safe, rather than discarding what may be in-progress work.*

### Design-session-17 amendments (2026-08-20 — note 17; spec P-11/P-12/FR-021)

- [X] T047 [US2] [P-11] Remove the currency columns from the schema: drop `currency_code`, `currency_scheme_code`, and `currency_vintage` from `analysis_template` in `alembic/versions/0001_initial.py`; mirror the drop in `tests/iteration1_mirror.py` (`ITERATION4_SCHEMA`); flip `tests/sqlserver/test_template_suite_schema.py`'s currency NOT-NULL assertions to absence assertions. *Done 2026-08-20: the NOT-NULL test replaced with three absence-assertion parametrize cases alongside the existing dropped-column checks.*
- [X] T048 [US2] [P-11] Currency removal sweep over code (T-10): the three fields out of `TemplateValues`, `_validate_currency`, `vintage_options()`, and the `currency_scheme_unresolved`/`currency_vintage_unresolved` flags out of `app/services/template_service.py`; the `vintage-options` route and currency form parsing out of `app/routers/templates.py`; `app/templates/partials/vintage_options.html` deleted; the currency/scheme/vintage selects out of `analysis_template_form.html`; the Currency column out of `templates_table.html`; the currency cases out of `tests/unit/test_template_service.py` and `tests/unit/test_templates_routes.py`. The sync worker, gateway reads, and all five metadata tabs stay untouched. *Done 2026-08-20 — 961 unit tests green.*
- [X] T049 [US2] [P-12/FR-021] Duplicate-and-edit (T-11): `duplicate_template(id)` + `duplicate_suite(id)` in `template_service.py` (copy row + tag/membership rows in one transaction; `<name> (copy)` with collision counter, base truncated to fit NVARCHAR(200)); `POST /templates/analysis-templates/{id}/duplicate` + `POST /templates/suites/{id}/duplicate` in `templates.py` (`_require_admin`, CSRF, redirect to the copy's detail page); Duplicate button on both detail pages (admin-only); unit tests — copy fidelity (fields, tags, membership), collision naming, truncation, non-admin rejected. *Done 2026-08-20: `duplicate_template`/`duplicate_suite` reuse `save_template`/`save_suite` under the copied row's own transaction rather than hand-rolled INSERT SQL — the copy re-validates for free and the naming/truncation helper (`_duplicate_name`) is shared between both.*
- [X] T050 [US2] [FR-007/O17-9] Event-rate scheme options must populate on model-profile selection: reproduce the blank-until-typed behavior from the 8/20 demo against current code (the T028 fragment may already cover it — the demoed build may predate it); fix the trigger if real; route test asserting the scheme options render on profile change with no filter input. *Investigated 2026-08-20: traced the profile→scheme cascade (native `change` event dispatched with `bubbles:true` from `pick()`, htmx's `hx-vals='js:...'` reading the just-set value, and the `@htmx:after-swap` Alpine listener — confirmed htmx dispatches both the camelCase and kebab-case event names, verified against the vendored `htmx.min.js` and this codebase's other working `@htmx:after-swap` usages) — no wiring defect found; the two "Fix dropdowns"/"Fix model profile dropdown" commits earlier the same day (the `@mousedown.prevent` fix) most likely already fixed the demoed symptom. No code change; added regression tests instead (`test_scheme_options_populate_on_profile_change_alone`, `test_edit_form_prefills_scheme_options_for_the_stored_profile`).*

**Checkpoint**: T021–T031 built and unit-green (958 unit tests) under the pre-note-17 design;
T047–T050 amend that build and are themselves done and unit-green (961 unit tests). Quickstart
US2's live walkthrough against the dev stack has not been run. **STOP** — approver clicks the
running slice before Polish.

---

## Phase 5: Deferred — Excel export/import (out of MVP scope)

The former US3 (move suites between environments via an `.xlsx` workbook, tasks T036–T040) is
out of MVP scope (spec P-02): moving suites is manual for now. The worked design — including the
reference-data dropdown sheet — is retained in `contracts/transfer-workbook.md` for the
nice-to-have enhancement.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T041 [P] Reconcile `docs/DATA_MODEL.md`: §7 deltas (`analysis_template_tag.tag_name` replaces `irp_tag_id`; `treat_construction_occupancy_as_unknown` added; `auto_name_pattern`/`region_label`/`peril_code`/`treaty_name_pattern` dropped (O15-6); **no currency columns** — `currency_code` removed per P-11/O17-2, currency is a submit-time parameter in Iteration 7; `template_suite_item` loses `position`/`portfolio_name_override` — suites unordered; UNIQUE names on `analysis_template`/`template_suite` (O17-5); authorship = `inserted_by`, no separate `created_by`) and §10 cache-table columns per data-model.md, including `irp_currency_scheme` and `irp_currency_scheme_vintage` alongside `irp_currency` (O17-1)
- [ ] T042 Run the full three-tier suite and fix any drift: `uv run pytest tests/unit`, `make test-sql` (or `make wsl-test-sql`), and `make shell` → `uv run pytest tests/irp --run-irp`
- [ ] T043 Run the complete `specs/009-template-suites/quickstart.md` walkthrough (US1–US2) including the sync-refusal double-click check

---

## Deferred: accumulation profiles (tabled 2026-08-18)

Accumulation ingestion is postponed; `irp_model_profile.is_accumulation` (default 0) and every
marker/validation branch on it ship anyway, so resuming is purely additive. Until it resumes,
sync ships six sets and no synced profile shows the Accumulation marker (the Accumulation branch
is exercised only by tests). If accumulation slips past this iteration entirely, revisit spec
FR-001/FR-004's three-way promise. Deferred work, in order:

- [ ] T001 [DEFERRED] Sandbox spike in `../../IRP/irp-integration`: probe the Risk Modeler accumulation-profile endpoint against the CIC sandbox, pin the URL and response shape (fields, id/name keys, whether `software_version_code` exists on accumulation rows); record findings and reconcile the provisional accumulation columns in `specs/009-template-suites/data-model.md` (`irp_model_profile` notes) and `specs/009-template-suites/contracts/routes.md` (`AccumulationProfileEntry` field list) if the shape differs
- [ ] T002 [DEFERRED] Implement the accumulation-profile read in `../../IRP/irp-integration` (new `reference_data` manager method per the T001 shape, with that repo's tests), depends on T001
- [ ] T044 [DEFERRED] Wire accumulation into this repo: `AccumulationProfileEntry` dataclass + `list_accumulation_profiles` in `app/services/irp_gateway.py`, FakeIRP mirror with accumulation sample rows, seventh fetch in the `sync_irp_metadata` worker upserting rows with `is_accumulation=1`, worker/route test cases, and the seventh read in `tests/irp/test_reference_data_shapes.py`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (external)**: complete — T003 (`0.6.0rc1`) and T045 (`0.6.0rc2`). T001/T002 remain deferred with accumulation
- **Foundational (Phase 2)**: T004 → T005 share `0001_initial.py`; T007/T008 after T005; T006/T009/T010 done. The T004/T005 schema deltas BLOCK every task that touches the affected tables (T011–T017, T019–T024, T031)
- **US1 (Phase 3)**: needs Phase 2; done 2026-08-19 — T011–T020 all built and unit-green; only the sandbox-only pieces (T020 against a live tenant, the quickstart walkthrough) remain to actually run
- **US2 (Phase 4)**: needs Phase 2; T021/T022 conformance next; T046 (deferred-code removal) anytime; T025 approval gates T026–T031; consumes US1's cache for pick lists (buildable against fake/synced data, but the story is verified after US1)
- **Polish (Phase 6)**: after all desired stories

### Within-story ordering

- US1: T011 → {T012, T013} → T014; T015 (preview approved) → T016 → T017 → T019; T020 anytime after T011
- US2: T021 → T022 → T024; T025 (preview approved) → T026 → T027 → T028 → T029 → T030 → T031; T046 anytime
- Note-17 amendments: T047 → T048 (schema before code sweep); T049 and T050 independent of both and of each other

### Parallel Opportunities

- T007 and T008 (different test files) after the T004/T005 deltas
- US1: T012 (fake) alongside T013 (worker); T014, T019, T020 in parallel once their subjects exist
- US2: T024, T031 (separate test files) in parallel with later implementation tasks; T046 in parallel with anything

---

## Implementation Strategy

### Current state (2026-08-20)

US1 (Phase 3) is fully built and unit-green, six-set sync included: the gateway's two
currency-scheme/vintage reads, the fake mirrors, the worker's scheme sync + wholesale vintage
replace, the currency-schemes metadata tab (schemes + vintage badges), and the route/worker unit
tests. US2 (Phase 4) is now also fully built and unit-green (958 unit tests pass): the service
layer (T021–T024), the reworked preview (T025), the suites/templates administration page as two
tabs (T026), the template builder with cascading scheme/vintage selects (T027–T028), the unordered
suite builder with a filterable template picker (T029), admin gating (T030), and route/gating
tests (T031). The workbook import and seed wiring that were built pre-deferral are out of scope
(spec P-02) and came out via T046.

The 2026-08-20 design session (note 17) then amended the spec: currency comes off templates
entirely (P-11 — T047/T048 undo the built currency columns, validation, and builder fields;
the cache and metadata tabs stay), duplicate-and-edit is added (P-12/FR-021 — T049), and the
event-rate options must populate on profile selection (O17-9 — T050). **T047–T050 are now done**
(961 unit tests green): the schema/mirror/sqlserver-assertion currency drop, the full code sweep
(service, router, templates, tests), `duplicate_template`/`duplicate_suite` with a Duplicate
button on both detail pages, and the O17-9 investigation (no wiring defect found; regression
tests added). The remaining work:

1. **US1 sandbox validation**: T020 against a live tenant (`make shell` → `uv run pytest tests/irp --run-irp`) + the quickstart US1 walkthrough; the T015 preview still wants the user's informal 👍
2. **US2 live click-through**: quickstart US2 against the dev stack (`make dev-up` / WSL2) — not yet run in this pass, DB-tier setup wasn't available; unit-level coverage (route rendering + service validation) is green
3. **Polish**: T041–T043 after US2

### Incremental Delivery

1. Schema deltas + conformance track → `make db-rebuild` clean — done
2. US1 currency-scheme extension → validate quickstart US1 against the sandbox → demo
3. US2 pages/routes/gating → validate quickstart US2 → demo
4. Polish → DATA_MODEL reconciliation + full three-tier run + quickstart sweep

### Notes

- One story per pass; stop at each checkpoint (docs/UI_WORKFLOW.md)
- UI previews (T015, T025) are approved informally (show → 👍) before any wiring
- Commit after each task or logical group; never add AI attribution trailers
