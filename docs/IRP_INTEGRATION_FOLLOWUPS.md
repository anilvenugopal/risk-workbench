# irp-integration — follow-up changes for the Risk Workbench

What `irp-integration` needs (or would benefit from) to fully serve the workbench, discovered during
the spec-003 reconciliation on **2026-07-14** against the committed wheel (**PyPI `0.2.0`**). We own
the library, so these are ours to make.

**None of these is on the Iteration-2 critical path.** Iteration 2 runs on 0.2.0 as-is, behind
`app/services/irp_gateway.py`. The confirmed method surface the workbench relies on is recorded in
`specs/003-edm-rdm-entity-management/contracts/worker-poller.md` → "IRP gateway — confirmed method surface".

---

## Feature gaps (block a deferred workbench feature)

### 1. Optional-EDM RDM import (review-only / RDM-only packages)
`rdm.submit_rdm_import_job(rdm_name, edm_name, rdm_file_path)` makes `edm_name` **mandatory** — it
resolves the EDM's `resourceUri` via `search_edms` and raises if none is found. Standalone-RDM import
(analyses with no exposure) is a real Risk Modeler capability but has no code path here.
- **Change:** make `edm_name` optional and add a no-EDM import path (analyses with `edm_id` null).
- **Unblocks:** workbench FR-002 (review-only import), FR-016, SC-004 (RDM-only package) — all deferred
  by spec 003 D3.

---

## Quality / ergonomics (nice-to-have; reduce workbench workarounds)

### 2. A uniform Job / status abstraction
Across ops the shapes diverge and a poller pays for it:
- id types: `int` (import, analysis, EDM-delete) vs `str` (databridge delete).
- submit returns: `(int, dict)` (imports) vs `int` (`submit_delete_edm_job`) vs `dict` (`submit_rdm_export_job`).
- status getters: `dict` (`get_import_job`, `get_risk_data_job`) vs bare `str` (`get_databridge_job`).
- status vocabularies: `FINISHED/FAILED/CANCELLED` (workflow) vs `Enqueued/Processing/Succeeded` (databridge).
- **Change:** a single `get_job_status(job_ref) -> Status` (normalized enum) and consistent submit
  returns, so a caller can "batch non-terminal jobs and poll each" without a per-type matrix.

### 3. Return the created entity id from a finished job
After an EDM/RDM import FINISHES, nothing hands back the created `exposureId` / analysis ids — the
workbench must `search_edms(exposureName=…)` (and `search_analyses(sourceRdmName=…)`) and assume name
uniqueness. A convenience that returns the created resource id(s) from the completed job removes a
round-trip and a fragile assumption.

### 4. A non-blocking ("no-poll") client surface
Every manager ships `poll_*_to_completion`, and the convenience methods `edm.delete_edm()`,
`rdm.export_analyses_to_rdm()`, and `import_job.submit_job()` call them **internally** — they block for
minutes and are forbidden for an Article-11 caller (our poller/workers). We hand-pick `submit_*` +
single `get_*` in the gateway to avoid them.
- **Change:** expose a clearly-separated non-blocking surface (or flag the blocking methods) so the
  constitution is enforced by the library, not by our discipline.

### 5. Minor
- `rdm.submit_rdm_import_job` docstring omits `rdm_name` in its Args block (signature is
  `(rdm_name, edm_name, rdm_file_path)`).
- Submit-return body key is inconsistent: imports return `request_body`; `submit_rdm_export_job`
  returns `http_request_body`.

---

## Spec-004 detail-read surface — confirmed against the active wheel (2026-07-23)

Confirmed for Iteration 3 (EDM/RDM details & backfill) against the **active** source
(`make irp-status`: mode `irp-testpypi`, **TestPyPI `0.2.1`**, installed in `.venv`). All are
single-status/read methods; the gateway never wraps the managers' `poll_*_to_completion`
(Article 11). Endpoints from `irp_integration/constants.py`.

| Workbench need | Wheel method (confirmed) | Endpoint |
|---|---|---|
| Portfolio enumeration for an EDM | `client.portfolio.search_portfolios_paginated(exposure_id, filter='') -> List[Dict]` | `GET /platform/riskdata/v1/exposures/{exposureId}/portfolios` |
| Per-portfolio exposure figures | `client.portfolio.get_portfolio_metadata(exposure_id, portfolio_id) -> Dict` | `GET /platform/riskdata/v1/exposures/{exposureId}/portfolios/{id}/metrics` |
| Treaty attribute detail | `client.treaty.search_treaties_paginated(exposure_id, filter='') -> List[Dict]` | `GET /platform/riskdata/v1/exposures/{exposureId}/treaties` |
| Broker-analysis settings/metadata | `client.analysis.get_analysis_by_id(analysis_id) -> Dict` (and the richer `search_analyses` rows) | `GET /platform/riskdata/v1/analyses/{analysisId}` |

**Signature notes / deviations from the illustrative contract:**
- The per-portfolio exposure read needs **both** the EDM's `exposureId` and the `portfolioId`
  (`get_portfolio_metadata(exposure_id, portfolio_id)`), so the gateway signature is
  `get_portfolio_exposure(*, edm_irp_id, portfolio_irp_id)` — not the single-arg form sketched in
  `contracts/worker-poller.md`.
- `get_portfolio_by_id(exposure_id, portfolio_id)` (`GET .../portfolios/{id}`) also exists; the
  workbench uses the `/metrics` variant for exposure figures and stores the payload **verbatim**
  (JSON snapshot, read defensively — field names unconfirmed until a sandbox pass).

**To confirm at the next `--run-irp` sandbox pass:**
- ~~The exact field names of the `/portfolios/{id}/metrics` payload~~ **CONFIRMED 2026-07-23**
  (sandbox, townsend_edm/exposureId 5331056): `totalAccounts`, `totalLocations`, `totalPolicies`,
  `perilsExposed` (comma-style **string**, e.g. `"WS"`), `name`, `number`, `description`, `owner`,
  `createDate`, `geocodeVersion`, `hazardVersion`. **No TIV / geography / currency / sub-perils** —
  see §6 below.
- That `search_analyses` rows carry `exposureResourceId` + `exposureResourceType` (the R9
  portfolio-linkage pointer) and `groupType`/group marker; else fall back to per-analysis
  `get_analysis_by_id`.

---

## Spec-004 Addendum A — exposure-detail gaps + the DataBridge summary contract (2026-07-23)

Sandbox pass against wheel **0.2.1** (read-only GETs, townsend_edm / exposureId 5331056). The
workbench needed "Get EDM by ID, Get Portfolios, Get Accounts by Portfolio, Get Locations,
Get Portfolio Metadata" for per-portfolio enrichment. Status:

| Capability | Status in 0.2.1 |
|---|---|
| Get EDM by ID | **MISSING** — no `GET /exposures/{id}` wrapper; only `search_edms(filter=...)` by name |
| Get Portfolios | Present — `portfolio.search_portfolios[_paginated]` (rows carry identity/version fields only, no exposure quantities) |
| Get Accounts by Portfolio | Present **but unpaged** — see gap 6b |
| Get Locations | **MISSING** — no location read anywhere in the package |
| Get Portfolio Metadata | Present — `get_portfolio_metadata` (`/metrics`; fields confirmed above) |

### 6. Exposure-summary gaps (spec-004 Addendum A — the concrete tickets)

**(a) No RM REST endpoint at any level returns TIV, currency, or geography.** Whole-package sweep
confirmed: `/metrics` is the exposure-summary ceiling; account rows carry `totalTIV` but no
currency/geography; portfolio rows carry no quantities; sub-perils appear nowhere. Location-level
detail (the source of geography/currency) has no read path at all.

**(b) `portfolio.search_accounts_by_portfolio(exposure_id, portfolio_id)` silently truncates.**
It passes no `limit`/`offset` and RM applies its default page: a 148-account portfolio returned
exactly **100** rows in the sandbox. Any consumer summing `totalTIV` over its result gets a wrong
answer with no error. **Change:** add pagination (or a `_paginated` variant) — and even then a
248k-account portfolio (usfl_other) would take ~2,500 calls, which is why the workbench chose (c).

**(c) NEW METHOD REQUESTED — the workbench's chosen source for TIV/geography/currency/sub-perils:**

```
DataBridgeManager.get_portfolio_exposure_summary(
    edm_data_source_name: str,        # the EDM database name on Data Bridge —
                                      # the RM exposure/datasource name given at import
    connection: str = "DATABRIDGE",   # named MSSQL_{NAME}_* env connection
) -> Dict[int, Dict[str, Any]]
```

Behavior:
- Connects to `database=edm_data_source_name` on the configured server and runs **one** read-only
  aggregate query (or a small fixed set) grouped by portfolio, covering every portfolio in the EDM.
- Returns `{portfolio_id: summary}` keyed by the EDM's `portinfo.PORTINFOID`.
  **Please confirm in the sandbox that PORTINFOID equals the id returned by RM
  `GET /exposures/{id}/portfolios`** — the workbench keys its rows on the RM `portfolioId`.
  Include `portfolio_name` in each summary as a secondary join key in case the ids diverge.
- Each summary dict:
  ```
  {
    "portfolio_name":  str,                     # portinfo.PORTNAME
    "tiv_by_currency": {currency: float, ...},  # SUM of location TIV per currency
    "currencies":      [str, ...],              # distinct location currencies
    "states":          [str, ...],              # distinct location admin-1 codes
    "countries":       [str, ...],              # distinct location country codes
    "sub_perils":      [str, ...],              # distinct peril codes present in the
                                                # peril-detail tables (eqdet/hudet/... level)
  }
  ```
  Lists sorted + de-duplicated; floats for TIV; empty lists rather than nulls.
- An EDM with zero portfolios returns `{}`.
- **Raise (don't swallow)** on unconfigured connection env, connection failure, missing database,
  or SQL error — the workbench treats any exception as "summary unavailable" and renders em-dashes.
- Read-only: SELECT only; no temp tables requiring writes; no DDL.

Known constraints on the workbench side:
- **Single-server assumption:** `MSSQL_DATABRIDGE_SERVER` must be the server EDMs are imported to
  (`settings.irp_edm_import_server`, default `"databridge-1"`); `irp_edm.server_name` is not
  currently populated.
- EDM names are not guaranteed unique in RM (collision is a non-blocking warning) — same-named
  EDMs resolve to the same database.

Workbench consumption: `irp_gateway.get_edm_exposure_summary(*, edm_name)` — already implemented
against this contract with graceful absence (any raise → `summary: null` in the snapshot, cells
render "—", job still succeeds). Publishing this method to TestPyPI + `make irp-testpypi` is all
that's needed to light the columns up.

---

## Spec-004 US2/US3 read surface — confirmations + gaps (2026-07-24)

Reconciled against the **IRP knowledge base** (`IRP/knowledge/` — documented-label
assertions from the preserved Moody's reference pages, confidence 0.99) while
implementing the treaty view/export (US2) and the broker-analysis settings +
portfolio linkage (US3). Wheel source unchanged (TestPyPI `0.2.1`).

**Confirmed (documented):**

- **Treaty row schema** — `GET /platform/riskdata/v1/exposures/{exposureId}/treaties`
  (`treaty.search_treaties_paginated`): `treatyId`, `treatyName`, `treatyNumber`,
  `treatyType` (`CATA|CORP|NCAT|QUOT|STOP|SURP|WORK`), `attachmentBasis` (`L|R`),
  `attachmentLevel` (`ACCT|LOC|POL|PORT`), `attachmentPoint`, `occurrenceLimit`,
  `riskLimit`, `retentionAmount`, `percentagePlaced`, `percentageRetention`,
  `percentageRiShare`, `percentageCovered`, `premium`, `priority`,
  `numberOfReinstatements`, `reinstatementCharge`, `aggregateDeductible`,
  `aggregateLimit`, `maolAmount`, `effectiveDate`, `expirationDate`, `isValid`,
  `userId1`, `userId2`; **`currency` / `cedant` / `producer` are OBJECTS**, not
  strings. The workbench treaty summary columns map on these names (read
  defensively); the snapshot stores each row verbatim.
- **Analysis metadata fields** — documented on `search_analyses` / `get_analysis_by_id`
  (`GET /analyses[/{analysisId}]`): `analysisId`, `analysisName`, `analysisType`,
  `analysisFramework`, `engineType`, `engineVersion`, `engineSubTypeCode`,
  `currencyCode`, `currencyName`, `peril`/`perilCode`, `subperil`,
  `region`/`regionCode`, `description`, `createDate`, `sourceRdmName`,
  `exposureName`, `lossAmplificationId`, `modeId`. The workbench's curated
  settings view (`analysis_service.AnalysisSettings`) reads exactly these,
  blank-on-missing.
- **`exposureResourceType` vocabulary** includes `PORTFOLIO` / `POLICY` / `TREATY`
  (EP-metrics request-configuration table) — validating the R9 rule (promote the
  pointer only for `PORTFOLIO`) and confirming non-portfolio exposures are a
  normal *not-linked* state, not an anomaly.
- **`analysis.get_analysis_by_id(analysis_id)`** present in 0.2.1 — used by the
  extended `backfill_rdm_analyses` as a per-analysis single-item read (looped
  app-side; a single failed read leaves that row's settings blank, never aborts).

### 7. Group marker — NO documented field (confirm in sandbox)

Nothing in the documented `search_analyses` / `get_analysis_by_id` property
sets marks an analysis result as a **group** (no `isGroup`; `groupType` appears
in no documented response schema; grouping-job docs describe inputs, not the
result marker). The gateway derives `AnalysisMetadata.is_group` defensively —
any of `groupType` / `analysisFramework` / `analysisType` /
`exposureResourceType` equal to `"GROUP"` — quarantined in
`_RealGateway.get_analysis_metadata` so correcting it is a one-file edit.
**Confirm the real marker at the next `--run-irp` pass** (import an RDM that
carries a grouped analysis and inspect the payload).

### 8. `exposureResourceId` on the analysis payload — unconfirmed

`exposureResourceId`/`exposureResourceType` are documented only as **request**
parameters (EP-metrics, export-job) — no preserved reference page documents them
as **response** properties of `GET /analyses/{id}` or the `search_analyses`
rows. The R9 linkage capture reads them defensively from both (hit first,
per-analysis metadata preferred); if the sandbox shows neither carries them,
the fallback source is the per-analysis reads Moody's documents ("Get regions /
cedants / treaties by analysis result" family) or the EP-metrics configuration.
**Blocking only for the Portfolio column lighting up with live data** — every
unresolved pointer renders the normal "— not linked" state.

### 9. Term / PLA / event-rate / rate-vintage fields — undocumented (FR-031 tail)

FR-031 lists long-term-vs-near-term, loss amplification (PLA), event-rate
scheme, and rate vintage among the settings to show; none has a documented
field on the analysis-result payload (only `lossAmplificationId` — an id, not a
label). The workbench reads `term`/`timeDependency`, `lossAmplification`/`pla`,
`eventRateScheme`/`rateScheme`, `rateVintage` defensively and renders
*"not provided"* when absent. **Confirm actual spellings in the sandbox** — or
whether they require the legacy `/analysis-settings/*` reads (knowledge-base
open question #19; Platform-first policy says find a Platform source before
adopting those).

---

## To confirm against the sandbox (verification, not library changes)
Owner's `--run-irp` environment; results may turn some of the above into concrete tickets:
- `search_analyses` supports the `sourceRdmName` filter field on `/platform/riskdata/v1/analyses`
  (the enumeration D2 depends on).
- The `search_imported_rdms` name-filter field for the RDM name-collision check (FR-012).
- `edm.submit_delete_edm_job(exposure_id)` on a non-empty exposure — does it reject, or cascade? (In
  the workbench flow the RDM analyses are deleted first via fan-in, so the exposure should be empty by
  EDM-delete time — confirm it doesn't error.)
