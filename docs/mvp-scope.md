# MVP Scope — Cincinnati Re Risk Workbench

**Source of truth:** `CReWorkflow_Expanded_20260617.xlsx` (sheet *Overview*, "In / Out New System" column), June 2026. Cross-referenced against `irp-integration` v0.2.1.dev23 and the working prototype (`irp-workbench/`).

This doc is the shared reference for *what we are actually building*. It is deliberately narrower than `PRD.md` — see **§5 PRD deltas** for where the PRD over-scopes relative to what the team marked "In".

---

## 1. The client workflow (verbatim scope calls)

The team's end-to-end submission workflow, with each step's disposition for the new system. "In" = build it; "Out" = stays in existing tools / not built; "Future" = later version.

| # | Step | In/Out | Notes |
|---|---|---|---|
| 1 | Uncompress files | **Out** | WinZIP; ideal-but-not-MVP to auto-decompress |
| 2 | Attach DBs to SQL w/ new name (EDMs & RDMs) | **Out** | naming conventions applied automatically |
| 3 | Upgrade/downgrade DBs for RL version | N/A | not needed with Risk Modeler |
| 4 | **Understanding the portfolio — Portfolio Information** | **In** | upload EDM MDF/BAK to Data Bridge; view portfolio info |
| 4 | — Data validation | **Out** | validation reports re data quality |
| 5 | Exposure Profiling / Data Quality Checks | **Out** | *(incl. load summary to SQL Exposure repository)* |
| 6 | **Modify Exposure — Create Subportfolios** | **In** | e.g. LOB, geographic |
| 6 | — Create portfolios for missing perils | **Out** | (verify if RM does this now) |
| 6 | — Update/change data elements | **Out** | proforma views, LOB edits |
| 7 | **Rerun hazard retrieval / geocoding (GeoHaz)** | **In** | generally preserve user geocode data; rerun for new models |
| 8 | **Reinsurance — Treaty details** | **In** | view/edit treaty info for main types |
| 8 | — Cedant ID check, treaty accuracy, location details | **Out** | |
| 8 | — Add/edit reinsurance | **In (edit only)** | create/add within UI |
| 9 | **Review broker-provided analyses (RDM)** | **In** | upload RDM to Data Bridge; use broker results when appropriate |
| 10 | **Run additional DLM analyses** | **In** | monitor # locations analyzed per day |
| 10 | — Select loss currency | **?** | open question: where in RM? |
| 10 | — Choose from standardized DLM profiles | **In** | select profile(s) |
| 10 | — Rename analyses | **In** | apply naming conventions, incl. post-analysis rename |
| 10 | — Schedule / stagger analyses | **Out** | optional if available |
| 11 | **Run accumulation analyses** | **In** | needs monitoring tool |
| 12 | **Post-analysis — View results** | **In** | broker or CRe rerun; copy/paste; analysis-level |
| 12 | **— Grouping analyses** | **In** | sometimes RiskLink, sometimes our tool (faster) |
| 12 | — Create ELTs by Zone/County/Country | **Out** | |
| 12 | — Validate losses vs broker/cedant; visual compare | **Out** | |
| 13 | **Upload ELTs to Loss Repository (SQL)** | **In** | losses + financial perspective + metadata, RiskLink → Loss Repo |
| 14 | Upload loss sets to Analyze Re | **Out** | CRe Loss Repo → Analyze Re |
| 15 | Detach/archive databases | **Future** | data cleanup |

---

## 2. The MVP spine (what we actually build)

A single analyst-driven pipeline against an EDM. Linear in practice; the analyst drives each step.

```
Upload EDM  (+ Upload RDM, paired)
      │
      ▼
[ View portfolio info ]
      │
      ▼
Create subportfolios          (LOB / geographic filters)
      │
      ▼
GeoHaz / re-geocode           (per portfolio, for new models)
      │
      ▼
[ View / edit treaties ]      (reinsurance, edit-only)
      │
      ▼
Run analyses                  (DLM profiles, naming, accumulation; single + batch)
      │
      ▼
View results                  (ELT / EP / AAL; own + broker-from-RDM)
      │
      ▼
Grouping analyses             (combine/break out; group-of-groups)
      │
      ▼
Export ELTs → Loss Repository (parquet export → load to LOSS SQL)
```

---

## 3. Granular IRP activities (the building blocks)

Each row is one atomic capability. **Async/job** = creates a tracked job row, polled to completion. **Sync** = a direct `irp-integration` call that returns immediately (no polling). **Heavy** = moves bulk bytes / does bulk DB work (→ off-request worker).

| Activity | irp-integration call(s) | Async? | Heavy? | Produces |
|---|---|---|---|---|
| **EDM upload** | `search_edms` (dup check) → `submit_edm_import_job` → poll `import_job.get_import_job` | Job | **Heavy** (S3 upload inside submit) | EDM (`exposureId`) |
| **RDM upload** | `submit_rdm_import_job(rdm, edm, path)` → poll `import_job.get_import_job` | Job | **Heavy** (S3 upload) | RDM (+ broker analyses) |
| **Create subportfolio** | `portfolio.create_portfolio(...)` | **Sync** | no | Portfolio (`portfolioId`) |
| **GeoHaz** | `portfolio.submit_geohaz_job(...)` → poll `get_geohaz_job` | Job | no | (updates portfolio hazard) |
| **Treaty view/edit** | `treaty.search_treaties`, `create_treaty`, `create_treaty_lob` | **Sync** | no | Treaty |
| **Run analysis** | `analysis.submit_portfolio_analysis_job(s)` → poll `get_analysis_job` | Job (N for batch) | no | Analysis (`analysisId`) |
| **Grouping** | `analysis.submit_analysis_grouping_job(s)` → poll `get_analysis_grouping_job` | Job | no | Group (analysis-like) |
| **View results** | `analysis.get_elt / get_ep / get_stats / get_plt` (REST only) | **Sync** read | medium | result rows |
| **Export → Loss Repo** | `analysis.submit_analysis_export_job` → poll `get_export_job` → `download_export_results` → load LOSS | Job + follow-up | **Heavy** (download + bulk insert) | loss-repo rows |

**Notes / reconciliations:**
- **Imports poll via `import_job.get_import_job`, not `risk_data_job`.** The prototype confirms this; PRD §14.4's `risk_data_job` routing for imports needs correcting.
- **Poller must use single-shot `get_*_job`**, never the blocking `poll_*_to_completion` variants (those would block the batch pass).
- **create_portfolio is synchronous** — no async job, no poll. A subportfolio is just a portfolio created with filter criteria.
- **Results retrieval is REST-only** (never Data Bridge), per perspective code (`GU`/`GR`/`RL`); `PLT` is HD-only.

---

## 4. User-level actions (composites over §3)

What the analyst clicks. Each composes one or more granular activities and is the unit of audit (`UserActions`).

| User action | Composed of | Notes |
|---|---|---|
| **Create submission** | EDM upload **+** RDM upload | broker package = both files; RDM upload chains off EDM-ready (needs `edm_name`) |
| **Upload EDM** (standalone) | EDM upload | when only an EDM arrives, or added later |
| **Upload RDM** (standalone) | RDM upload | requires an existing EDM |
| **Create subportfolio** | create_portfolio | sync; immediate |
| **Run GeoHaz** | geohaz job (per portfolio) | |
| **Submit analyses** | analysis job(s) | single or batch from saved profiles/naming |
| **Group results** | grouping job | |
| **Export to Loss Repo** | export job + load | one job per analysis; "done" = loaded, not just parquet-ready |

---

## 5. Cross-cutting (Additional Considerations sheet — Version 1)

- **Job monitor / "my jobs" dashboard** — per-analyst view; filter everything by analyst (role).
- **Progress/status messaging** — show only what's useful to the modeler.
- **Notifications** — when jobs finish.
- **Auto-refresh job monitor** — polling/live status.
- **Error handling** — first-class.
- **Project hierarchy vs tagging** — open design question (multiple portfolios/EDMs/results per project).

---

## 6. PRD deltas (where PRD.md over-scopes vs MVP)

These PRD features are **Out** or absent per the spreadsheet — flag before building:

- **Phase A: Data Validation & Profiling (PRD §10)** → **Out** (rows 4-5). Validation reports + exposure profiling are not MVP.
- **Exposure Repository load (PRD §10.4, §16.5, `push_exposure_summary`)** → **Out** (row 5). No Exposure Repository in MVP.
- **Visual broker-RDM comparison (PRD §17.3)** → **Out** (rows 12 validate/compare). Only *RDM upload + reviewing broker analyses* (row 9) is In; the side-by-side comparison UI is not MVP.
- **Treaties / reinsurance editing** → **In** (rows 8) but **largely absent from the PRD** (only `treaty_name` in analysis templates). This is an MVP gap in the PRD.
- **8-stage formal workflow engine (PRD §12)** → not called for by the workflow sheet; the MVP spine is a linear analyst-driven sequence. See open construct question below.

---

## 7. Open questions

- Loss currency: where is it assigned in Risk Modeler? (Analysis Builder only?)
- Do new analyses get written to RDMs already on Data Bridge?
- Can a profile be edited in RM once created?
- Project hierarchy vs tagging model for grouping related portfolios/EDMs/results.
