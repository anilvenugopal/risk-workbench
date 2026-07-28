# Contract — HTTP Routes (Iteration 3)

Server-rendered FastAPI + Jinja2 + HTMX (Article 8). All routes require an authenticated session. **No route applies row-level access** — every authenticated analyst may load every EDM/RDM's detail (Article 6). **No route calls Risk Modeler** (Article 11) — detail views and the Excel export read only **stored** detail; the backfill is worker-driven (see [worker-poller.md](worker-poller.md)). This iteration adds **no new state-changing route** (backfill auto-fires from the poller/worker), so the only new CSRF surface is none; the existing import/retry/replace routes are unchanged.

Response convention: full-page GETs return the shell-embedded page (`hx-boost` handles nav); HTMX GETs return the affected **partial** (a treaty expand, a portfolio row). The `.xlsx` export is a normal authenticated GET returning a file download.

---

## EDM detail — redesigned (US1/US2/US4)

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/edms/{id}` | **Redesigned** EDM detail — minimal header + aggregate strip + inline per-portfolio table (with linked analyses) + treaties + broker analyses | EDIT of the spec-003 route (`app/routers/edms.py` `_detail`): now passes `edm_service.get_edm_detail(id)` (header + portfolios **with their linked analyses** + aggregate + treaties + the standalone RDM-grouped analyses list) to the template, replacing the minimal `{edm}` payload. Reachable from the EDM library and the submission package cards. Nav node `irp.edm_library` unchanged (no manifest edit — R6). |
| GET | `/edms/{id}/treaties.xlsx` | **NEW** — download the full treaty set as `.xlsx` (FR-024) | Authenticated GET; streams `treaty_service.build_treaty_workbook(id)` with `Content-Disposition: attachment; filename="<edm>-treaties.xlsx"` and the `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` media type. **Reads stored detail — no Risk Modeler call.** No CSRF (a read/GET, no state change). |

**Header (FR-011):** name, status + `as_of` (the last-synced trust signal, FR-052), source file, identifiers (Risk Modeler id / creating-job id), portfolio count. **MUST NOT show cedant or line of business** (cedant is a submission attribute; LOB is per-portfolio / an Iteration-4 breakout dimension).

**Per-portfolio table (FR-012/FR-013/FR-014 — the primary content):** an **inline read-only** row per portfolio showing location/account/policy counts, perils, lines of business, geography, currency, TIV, record volume, and a descriptive **Analyses** count (FR-037). **No Records column** (records == locations). Expanding a portfolio row reveals the **analyses linked to that portfolio** (inline mini-table; the pinned + rail-connected expanded body of `ui.md §1.1`); a portfolio with no linked analyses reads "None". **No** create/edit/split/filter control (Iteration 4). A textual snapshot — **no map**, no Power BI rebuild (FR-016). All N portfolios listed, no silent truncation (SC-003), including a 25-portfolio EDM.

**Aggregate strip (FR-040/FR-042/US4):** a compact rollup above the per-portfolio table — total counts, portfolio count, union of perils and lines of business, combined geography, currency set, total record volume — **derived** from the stored per-portfolio detail (`portfolio_service.aggregate_exposure`), never a separate fetch.

**Treaties section (US2):** treaties shown at the EDM level, most collapsed; expand any one to its full attribute detail (`treaty_row.html`); a wide attribute set scrolls **horizontally** in the compact view without breaking layout (Alpine/CSS sliver); an "Export to Excel" link hits `/edms/{id}/treaties.xlsx`. Read-only — no create/edit control (FR-025).

**Broker analyses section (US3/FR-037):** a standalone list of the EDM's broker analyses **grouped by source RDM**, each row carrying its **resolved portfolio** (name link / "Group" / "— not linked", `ui.md §4`), settings columns, and a per-row expand to the full settings grid + the rate/event-rate sub-drill. Same analyses as the per-portfolio inline panels, but the compare-everything view with full metadata. Read-only; no loss numbers (FR-033). Supplied by `analysis_service` as part of the `get_edm_detail` payload (portfolio resolution is read-time, R9).

**Graceful states (FR-017/FR-043/SC-006):** when the EDM has no backfilled detail (imported before this capability, or fetch pending/failed), the per-portfolio section, aggregate strip, and treaties section render a **"detail not available — re-import to populate" / pending** state; the header still displays. A zero-portfolio EDM shows a clear "no portfolios" state (FR-015). Never a broken/errored page.

---

## RDM detail — broker analyses (US3)

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/rdms/{id}` | **Extended** RDM detail — broker analyses grouped by `rdm_id` + per-analysis settings/metadata | EDIT of the spec-003 route (`app/routers/rdms.py`): renders `analysis_service.list_broker_analyses(id)`. Nav node `irp.rdm_library` unchanged. |

**Broker-analysis view (FR-030/FR-031/FR-035/FR-036):** the RDM's analyses **grouped by `rdm_id`** so one applied across M EDMs shows **once** ("1 broker analysis across 4 EDMs"), not M times (R8). Because an RDM's analyses can span EDMs, the view carries **EDM** and **Portfolio** columns; the Portfolio is the **resolved** owning portfolio (name link / "Group" / "— not linked", read-time resolution, R9). Each analysis (`broker_analysis_row.html`) shows its settings/metadata (engine/model version, engine type + version, analysis type/mode, peril primary+secondary, region, currency, construction, LOB, group type, long-term vs near-term, event-rate scheme / rate vintage, loss amplification) — with rate/event-rate detail one drill-down deeper. A group (`is_group = true`) is a single row (no member breakdown). **No loss numbers, no own-vs-broker comparison** (FR-033/FR-034). Missing/partial metadata → a blank/unavailable state, never an error.

---

## Submission detail — package cards (extended)

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/submissions/{id}` | Detail — package cards now carry a **per-EDM aggregate orientation line** + populated analysis counts | EDIT of the spec-003 route: `package_sync_service.get_package_cards` supplies the per-EDM aggregate line (FR-041) and the now-**populated** analysis counts (FR-050); `package_card.html` renders them. Graceful pending line when an EDM has no snapshot (FR-043). |

**Per-EDM aggregate line (FR-041):** in each EDM's package row — total counts, portfolio count, perils, record volume — from the same rollup as the EDM-page strip (a quick read before drilling in). Extends the spec-003 cards; does not replace the existing chips/counts.

---

## Cross-cutting

- **No new state-changing route / no new CSRF surface:** backfill is auto-fired by the poller/worker (Article 5); viewing detail and exporting Excel are authenticated **reads**. The existing import/retry/replace-file routes (spec 003) keep their CSRF unchanged.
- **Nav manifest (Article 1):** unchanged — `irp.edm_library` / `irp.rdm_library` and the `/edms/{id}` / `/rdms/{id}` detail routes already exist; this iteration edits page *content* + handler payload, adding no nodes (R6). Breadcrumb/active-state derive from position.
- **No IRP polling/result calls on any route (Article 11):** detail views and the `.xlsx` export read stored detail only; the poller and worker own every Risk Modeler read (see [worker-poller.md](worker-poller.md)).
- **Roles gate functions, not rows (Article 6):** the detail + export routes are available to any authenticated analyst; no row scoping.
- **HTMX idle-timeout:** inherits the Iteration-0 `HX-Redirect` on session expiry.
