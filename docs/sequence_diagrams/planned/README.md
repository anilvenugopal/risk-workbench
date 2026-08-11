# Planned Flows — the design-ahead archive (metamodel-free)

These are the sequence flows for parts of the MVP spine that are **not built yet**. They
were written before the metamodel existed, at two altitudes, and they are kept here
verbatim because the interaction analysis in them is still correct and still the best
starting point for the iteration that finally builds each one.

**Nothing in this folder describes running code.** For what the app actually does today,
see the metamodel-full flows in [`../README.md`](../README.md).

## Why these read differently from the current set

**They are deliberately metamodel-free.** They show only the real interactions between the
app, Risk Modeler, S3, and Data Bridge — *not* where we write `rwb_job` / `irp_job` /
entity rows. That was intentional: read the interactions objectively first, and *then*
decide where the metamodel bounding boxes belong. Each flow therefore ends with a
**"Boundaries worth noting"** block flagging the sync / async / heavy seams that were
candidates for those boxes — observations, not decisions.

Those decisions have since been made (constitution v3.0.0, Articles 10 & 11), and the
current set is written the opposite way round — metamodel-full. When one of these flows
gets built, expect to **rewrite** it in the current-set pattern rather than annotate it
in place.

## A note on the missing references

These files cite `../../mvp-scope.md` §2/§3/§4 and `../../execution-design.md`. **Neither
document exists in the repo** — a known open gap recorded in
[`../../CR/completed/CR_02__NO_WORKFLOW_ENGINE.md`](../../CR/completed/CR_02__NO_WORKFLOW_ENGINE.md)
§9.1. This folder is the surviving proxy for what those sections said:

- **§2 — the MVP spine.** The ordered path: EDM upload → RDM upload → view portfolio →
  subportfolio creation → GeoHaz → treaty view → run analysis → view results → grouping →
  export to Loss Repo.
- **§3 — granular activities** → `granular/`, one flow per atomic IRP activity.
- **§4 — user-level actions** → `composite/`, each composing several granular activities.

The first two spine steps (EDM upload, RDM upload) and the treaty *view* have since been
built; their flows now live in the current set, which is why they are absent below.

## Actors used across these flows

| Actor | Meaning |
|---|---|
| **User** | The analyst driving the app |
| **App** | The Risk Workbench (FastAPI), calling `irp-integration` in-process |
| **RM** | Risk Modeler REST API (Moody's cloud) |
| **S3** | Moody's-provided S3 / CloudFront bulk file store (upload on import, download on export) |
| **DB Bridge** | Data Bridge ODBC (Moody's cloud SQL), where used |
| **Loss Repo** | The client's LOSS SQL Server (export destination) — a real external system, not the workbench metamodel |

Note that "App" here is a single undifferentiated actor. The current set splits it into
**App (route)** / **Worker** / **Poller**, because which process does a step turned out to
be the load-bearing question. Any of these flows that shows "App" polling a job to
completion is showing something the constitution now **forbids** on the request path
(Article 11) — that is exactly the kind of thing the rewrite has to resolve.

## Granular flows (`granular/`)

Ordered along the spine. **Classification:** Sync = immediate return, no poll;
Job = tracked async job polled to completion; Heavy = moves bulk bytes / does bulk DB work.

| Flow | Classification | Produces |
|---|---|---|
| [View portfolio](granular/view_portfolio.md) | Sync read | — (read only) |
| [Create subportfolio](granular/create_subportfolio.md) | Sync | *empty* Portfolio (`portfolioId`) |
| [GeoHaz](granular/geohaz.md) | Job | — (mutates portfolio hazard in place) |
| [Treaty view/edit](granular/treaty_view_edit.md) | Sync (1+N calls, not atomic) | Treaty |
| [Run analysis](granular/run_analysis.md) | Job | Analysis (`analysisId`) |
| [View results](granular/view_results.md) | Sync read (REST only) | result rows (ELT / EP / stats / PLT) |
| [Grouping](granular/grouping.md) | Job (read-fan-out submit) | Group (an analysis, `isGroup`) |
| [Export → Loss Repo](granular/export_to_loss_repo.md) | Job + **Heavy** post-finish load | rows in LOSS SQL |

Partially superseded: **View portfolio** and **Treaty view/edit** describe RM reads the
workbench now performs — but in the `backfill_edm_detail` worker, storing a snapshot, with
the page rendering from that snapshot instead of calling RM. See
[`../backfill/backfill_edm_detail.md`](../backfill/backfill_edm_detail.md) and
[`../entities/review_treaties.md`](../entities/review_treaties.md). What remains genuinely
unbuilt in them is treaty **editing** and portfolio reads outside the backfill path.

## Composite flows (`composite/`)

User-level actions, each composing one or more granular activities. Multi-item composites
loop the **single** IRP endpoint app-side (not the plural helpers) so each `job_id` is
captured and one item's failure doesn't orphan the rest.

| Flow | Composes | Key note |
|---|---|---|
| [Submit analyses](composite/submit_analyses.md) | Run analysis × N | Manual config, **no suite**: load pick-lists → hand-pick each setting. DLM/HD discovered from model profile |
| [Run GeoHaz](composite/run_geohaz.md) | GeoHaz × N portfolios | App loops single submit, records each `job_id`; per-portfolio failure continues (avoids plural helper's orphaning) |
| [Group results](composite/group_results.md) | Grouping × N | Surface included-vs-skipped members; group-of-groups is a sequencing gate; a group *is* an analysis |
| [Export to Loss Repo](composite/export_to_loss_repo.md) | Export job + load × N analyses | "Done" = loaded into LOSS SQL, **not** RM-`FINISHED`; heavy work is on the load |
| [Create subportfolios by LOB](composite/create_subportfolios_by_lob.md) | Native create-by-filter × N LOBs | Account-bucketed → slices double-count, can't be "pure"; needs a create-by-filter enhancement |

**Create subportfolios by LOB** is the closest to being built: it is specced as
`specs/005-subportfolio-breakouts` (US1/US2/US3), which exists as a full plan with no
`tasks.md` and no implementation.

## Machinery these flows will need that doesn't exist yet

Worth knowing before picking one up — all of it is *seeded but empty*:

- four `rwb_job_type` kinds with **no worker actor**: `retrieve_analysis_results`,
  `download_export_file`, `push_results_to_loss_repo`, `notify_analyst`;
- four `irp_job_type` kinds with **no poller getter** — `geohaz`, `analysis`, `grouping`,
  `export` — so the poller logs "No getter for irp_job_type" and skips them;
- no EXPOSURE / LOSS database access anywhere in `app/` yet (only the health probe
  connects to them).
