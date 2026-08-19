# Quickstart: Verifying Analysis Templates & Template Suites (009)

## Prerequisites

- Dev stack up (`make dev-up` or WSL2 native — developer's call, not an agent's).
- Rebuilt database: `make db-rebuild` (destructive) — migrates the 10 new tables (8 until T-07's
  scheme/vintage tables land) and seeds the
  four starter suites.
- IRP sandbox credentials in the environment (for the sync; everything else works without them).
- irp-integration `>= 0.6.0rc1` — the pinned TestPyPI pre-release (`make irp-testpypi`;
  `make irp-status` shows the active source) carrying the T-06 validation utility. The
  accumulation-profile read is tabled (T-02), so no Accumulation rows appear until it resumes;
  the currency-scheme and scheme-vintage reads (T-07) exist in the sibling working copy but await
  a release — until it ships the fourth tab still shows the individual-currency list (which stays
  cached regardless — submission needs currency, scheme, and vintage), not currency schemes.
- Sign in as the seeded admin (`admin@example.com`) for mutating steps; any analyst for viewing.

## US1 — Metadata sync & four-tab screen

1. Open **Templates → Analysis Metadata** (`/templates/metadata`). Before any sync: four tabs
   (Model Profiles, Output Profiles, Event Rate Schemes, Currency Schemes with their vintages —
   P-07), each with an
   empty state and no last-synced time.
2. Click **Sync IRP Metadata**. The page reports the queued job; when the worker finishes,
   refresh: each tab lists its set read-only, last-synced time shown. Model profiles carry a
   DLM/HD/Accumulation marker and the raw software version. Expect ~3,500 model profiles in the
   sandbox — type
   `UDCT` (or any UD prefix) in the filter and watch the list narrow without a page reload.
3. Click sync twice quickly: one job runs; the second click is refused with a "sync already in
   progress" message (never two interleaved syncs).
4. Failure path: with broken IRP credentials, sync → job fails with a reason on the page;
   previously synced rows and last-synced time unchanged.
5. No create/edit control exists on any tab.

## US2 — Templates, suites, starter set

1. `/templates` as a non-admin analyst: suites and templates visible, no create/edit/delete/import
   controls; direct POSTs redirect to `/`.
2. As admin, create a template: pick a DLM profile (e.g. an `RL25` one) — the Event Rate Scheme
   list narrows to the profile's peril/region and pre-selects when only one matches. Clear it and
   save → rejected naming the DLM rule. Pick an HD profile → saves without a scheme. Pick a
   currency (required); leave the currency scheme blank → the saved template shows "Default" for
   scheme and vintage (P-10). Then edit it and pick a scheme → the vintage list loads with the
   scheme's latest by effective date pre-selected; a vintage-less scheme blocks the save naming
   the scheme (P-07/P-10).
3. Analysis settings show defaults (1.00 / 1 / off / "Treat as unknown") and accept edits.
4. Duplicate template name → rejected with a message.
5. Compose a suite from a DLM + an HD template (an unordered group — no ordering controls, no
   per-item settings); try adding the same template twice (blocked). Save — no mixing error.
6. Try deleting a template the suite references → blocked, suite named.
7. After `make db-rebuild`: US, Canada, US+Canada, Global starter suites present with ~10
   templates each, editable and deletable (SC-005) — seeded by importing
   `infra/scripts/starter_suites.xlsx` through the import service; edit a suite and re-run
   `seed_db.py` (not the rebuild) to confirm the seed skips and the edit survives.
8. Unresolved flag: create a template, then (in Risk Modeler or by editing the sandbox) remove its
   profile and re-sync — the template shows the unresolved flag; the value is unchanged.

## US3 — Export / import round-trip

1. `/templates` → **Export** (all) → file opens in Excel with `Templates` + `Suites` sheets per
   `contracts/transfer-workbook.md`; a template in no suite appears on the `Templates` sheet too.
2. `make db-rebuild`, sign in, **Import** the file → all suites and templates recreated; export
   again and diff the two workbooks — identical (SC-004).
3. Import the same file a second time → matched names update in place, no duplicates.
4. Break one cell (blank a `Model Profile`, or clear a DLM row's scheme) → import applies nothing
   and lists every error with sheet + row.

## Test tiers

| Tier | Command | Covers |
|---|---|---|
| Unit | `uv run pytest tests/unit` | sync worker (fake IRP), metadata routes/fragments, template validation (DLM rule, duplicates, delete guard), suite composition, workbook build/parse round-trip, admin gating |
| SQL Server | `make test-sql` (or `make wsl-test-sql`) | migration of the new tables, filtered unique indexes, schema-drift guard |
| IRP sandbox | `make shell`, then `uv run pytest tests/irp --run-irp` | reference-data reads return the shapes R1 documents |
