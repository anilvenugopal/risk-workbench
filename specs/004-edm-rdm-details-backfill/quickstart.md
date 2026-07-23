# Quickstart — Validate EDM/RDM Details & Backfill (Iteration 3)

A runnable validation guide proving the iteration works end-to-end. It references
[data-model.md](data-model.md), [contracts/](contracts/), and [research.md](research.md)
rather than duplicating them. Implementation detail (SQL bodies, template markup, full
test suites) belongs in `tasks.md` and the implementation phase — not here.

## Prerequisites

- Iterations 0–2 in place (auth, shell, nav, `db/` package, the async spine: poller +
  `rwb_job` queue + Dramatiq worker + `irp_gateway`, EDM/RDM import, package cards, the
  EDM/RDM libraries + minimal detail pages this iteration redesigns).
- Dev DB reachable (`make sqlserver-up` for WSL2 native, or `make dev-up` for the full stack).
- **Redis reachable** and the **background components running** — poller + Dramatiq worker
  (`make dev-up` starts them; natively, run the poller and worker alongside `make native-dev`).
- `.env` has the Iteration-2 vars (`SHARED_DRIVE_ROOT`, `POLL_INTERVAL_SECS`, the
  `RWB_HEARTBEAT_*` / `IRP_SUBMISSION_*` vars, the `IRPClient()` env vars). **No new env var
  this iteration.**
- **New dependency**: `openpyxl` (treaty `.xlsx` export). After pulling this branch, run
  `uv sync` so it is installed.
- **Confirm the active `irp-integration` source** (`make irp-status`) and re-confirm the new
  detail-read method signatures against that wheel before implementing (R1) — the wheel is
  pre-release; confirmations/gaps go in `docs/IRP_INTEGRATION_FOLLOWUPS.md`.
- **CI needs no real Risk Modeler**: the unit tier runs against the **fake IRP** (extended
  with portfolio/treaty/analysis-metadata payloads). The `--run-irp` tier is opt-in.

## 1. Rebuild the schema (drop-create-seed) — §21.0 prompt

Schema-affecting (adds `irp_portfolio` + `irp_treaty` and three `irp_analysis` columns), so
run the **Rebuild / Refresh / Skip** prompt for **WORKBENCH** first; recommended **Rebuild**:

```bash
make db-rebuild        # drop + recreate 3 app DBs, run 0001_initial, seed
```

**Expected:** no error; the new tables exist — `irp_portfolio`, `irp_treaty` — with their
`exposure_detail` / `attributes` JSON columns and `UNIQUE(edm_id, irp_id)`; `irp_analysis`
now has `settings_metadata`, `is_group`, `exposure_resource_id` (`group_parent_id` deferred);
`rwb_job_type_kind` includes the new `backfill_edm_detail` row. `EXPOSURE`/`LOSS` untouched;
DATABRIDGE never touched.

## 2. Unit tests (SQLite + fake IRP — no external deps)

```bash
pytest tests/unit
```

**Expected — new/changed coverage passes** (maps to the contracts' test obligations):
- `test_backfill_edm_detail.py` — the worker fetches (fake) → idempotently upserts
  `irp_portfolio`/`irp_treaty` + JSON snapshot + `as_of`; a re-run **overwrites in place**
  (no duplicate rows); a fetch failure leaves the EDM `ready` and the `rwb_job` recoverable;
  one portfolio's failed read doesn't abort the rest.
- `test_edm_detail_rollup.py` — `get_edm_detail` derives the aggregate (sum counts / union
  perils / combine geography+currency) from per-portfolio snapshots; graceful empty when no
  snapshot.
- `test_broker_analyses.py` / `test_edm_analyses.py` — `list_broker_analyses` / `list_edm_analyses`
  group by `rdm_id` (an analysis across M EDMs shown once); `settings_metadata` parsed; missing
  fields blank not error; `is_group` surfaced; and the **portfolio linkage** resolves at read time
  (match on `edm_id`+`exposure_resource_id`), `is_group` → "Group", unmatched → "not linked",
  order-independent (R9/FR-036).
- `test_treaty_export.py` — `build_treaty_workbook` produces a valid `.xlsx` over the treaty
  set (union of columns) from stored detail, no gateway call.
- `test_poller.py` (extended) — `import_edm` FINISHED enqueues **both** `upload_rdm` and
  `backfill_edm_detail` (idempotent); a standalone/EDM-only import still enqueues the detail
  head; a `FAILED` terminal enqueues neither backfill.

## 3. SQL-Server integration tests

```bash
pytest tests/sqlserver --run-sqlserver
```

**Expected:** `test_detail_tables_migration.py` — the extended migration builds
`irp_portfolio`/`irp_treaty` + the new `irp_analysis` columns with all FKs and the
`UNIQUE(edm_id, irp_id)` keys; the `backfill_edm_detail` seed is present; the idempotent
detail upsert overwrites `exposure_detail`/`attributes`/`as_of` in place and inserts no
duplicate portfolio/treaty row on re-run.

## 4. (Optional) IRP sandbox tests

```bash
pytest tests/irp --run-irp
```

**Expected:** the real `list_portfolios` / `get_portfolio_exposure` / `search_treaties` /
analysis-metadata read round-trips; and an assertion that `poll_*_to_completion` (and the
poll-inside convenience methods) appear nowhere in the new worker/gateway code (Article 11).

## 5. Manual walkthrough (the analyst's day-to-day)

Log in (dev fixture `admin@example.com`), with the poller + worker running, then:

1. **Import an EDM and let it finish (US1 backfill).** Import a multi-portfolio `.bak`
   (Iteration-2 flow). *Expect:* within ~1 minute of the import reaching `ready`, its detail
   is populated **with no analyst action** (SC-001) — the poller enqueued `backfill_edm_detail`
   on FINISHED and the worker fetched + stored it.
2. **Open the redesigned EDM detail page (US1 — the headline).** *Expect:* a **minimal header**
   (name, status + last-synced, source file, identifiers, portfolio count — **no cedant/LOB**);
   an **inline per-portfolio table** as the primary content — every portfolio with its
   location/account/policy counts, perils/sub-perils, geography, currency, record volume — a
   **textual snapshot, no map**; **no** create/split/filter control (SC-002/SC-003). A
   ~1M-record portfolio renders as fast as a small one (SC-007).
3. **Read the aggregate strip (US4).** *Expect:* a compact rollup above the table (totals,
   portfolio count, union of perils, combined geography, currency set, total record volume) —
   for a single-portfolio EDM it near-mirrors the one row (both still shown).
4. **Review treaties + export (US2).** *Expect:* treaties at the EDM level, collapsed; expand
   one to its full attributes; a wide set scrolls horizontally; "Export to Excel" downloads a
   `.xlsx` of the full treaty set in one action (SC-004) — no create/edit control.
5. **Review broker analyses + portfolio linkage (US3).** Open an imported RDM. *Expect:* its
   broker analyses **grouped by RDM** (one applied across M EDMs shown once), with **EDM** and
   **Portfolio** columns — each analysis shows the **portfolio it ran against** (resolved), or
   **"Group"** (a single row, no member breakdown) / **"— not linked"** where it doesn't resolve;
   each with its settings/metadata; **no loss numbers**, no own-vs-broker comparison (SC-005/SC-009).
   Then open the **EDM** page: *Expect:* each portfolio expands to its **linked analyses inline**,
   and a **standalone RDM-grouped Broker-analyses section** lists the full set with the resolved
   portfolio per row. The package card's analysis counts now render **populated** (FR-050).
6. **Submission-page orientation line (US4).** Open the submission. *Expect:* each imported
   EDM's package row shows a **per-EDM aggregate line** (SC-008), extending the spec-003 cards.
7. **Graceful states (forward-only).** Open an EDM imported **before** this capability (or one
   whose backfill is pending/failed). *Expect:* the per-portfolio/treaty/aggregate sections show
   **"detail not available — re-import to populate" / pending** — never an error — and the
   header still displays; the EDM keeps its `ready` status (SC-006).

## Done when

- `make db-rebuild` clean; `pytest tests/unit` and `pytest tests/sqlserver --run-sqlserver` green.
- The manual walkthrough matches the expected outcomes above (SC-001…SC-009).
- **No Risk Modeler call occurs from a web request handler** (detail views + Excel export read
  stored detail), and **no `poll_*_to_completion`** exists in the new worker/gateway (Article 11).
- **No `customer`/scope construct** appears on `irp_portfolio`/`irp_treaty`/`irp_analysis` or any
  detail view (Article 6).
- Backfill is **forward-only** — no bulk sweep, no manual refresh action (FR-003).
