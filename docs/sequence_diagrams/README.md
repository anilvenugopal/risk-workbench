# Sequence Flows

Sequence diagrams for the MVP spine (`../mvp-scope.md` §2), at two altitudes:

- **`granular/`** — one flow per atomic IRP activity (`mvp-scope.md` §3). What
  actually happens when the app uses `irp-integration` to perform a single
  capability (e.g. EDM upload). These are the building blocks.
- **`composite/`** — user-level actions (`mvp-scope.md` §4). What happens when
  an analyst clicks one thing that composes several granular activities
  (e.g. "Create submission" = EDM upload + RDM upload).

**These flows are deliberately metamodel-free.** They show only the real
interactions between the app, Risk Modeler, S3, and Data Bridge — *not* where
we write `UserActions` / `Job` / `Batch` / entity rows. That is intentional: we
want to read the flows objectively and *then* decide where the metamodel
bounding boxes belong (see `../execution-design.md`). Each granular flow ends
with a short **"Boundaries worth noting"** block flagging the sync / async /
heavy seams that are candidates for those boxes — observations, not decisions.

Actors used across the flows:

| Actor | Meaning |
|---|---|
| **User** | The analyst driving the app |
| **App** | The Risk Workbench (FastAPI), calling `irp-integration` in-process |
| **RM** | Risk Modeler REST API (Moody's cloud) |
| **S3** | Moody's-provided S3 / CloudFront bulk file store (upload on import, download on export) |
| **DB Bridge** | Data Bridge ODBC (Moody's cloud SQL), where used |
| **Loss Repo** | The client's LOSS SQL Server (export destination) — a real external system, not the workbench metamodel |

---

## Granular flows (`granular/`)

Ordered along the MVP spine. **Classification:** Sync = immediate return, no poll;
Job = tracked async job polled to completion; Heavy = moves bulk bytes / does bulk DB work.

| Flow | Classification | Produces |
|---|---|---|
| [EDM upload](granular/edm_upload.md) | Job, **Heavy** (S3 upload inside submit) | EDM (`exposureId`) |
| [RDM upload](granular/rdm_upload.md) | Job, **Heavy** (S3 upload inside submit) | RDM + broker analyses (0..n) |
| [View portfolio](granular/view_portfolio.md) | Sync read | — (read only) |
| [Create subportfolio](granular/create_subportfolio.md) | Sync | *empty* Portfolio (`portfolioId`) |
| [GeoHaz](granular/geohaz.md) | Job | — (mutates portfolio hazard in place) |
| [Treaty view/edit](granular/treaty_view_edit.md) | Sync (1+N calls, not atomic) | Treaty |
| [Run analysis](granular/run_analysis.md) | Job | Analysis (`analysisId`) |
| [View results](granular/view_results.md) | Sync read (REST only) | result rows (ELT / EP / stats / PLT) |
| [Grouping](granular/grouping.md) | Job (read-fan-out submit) | Group (an analysis, `isGroup`) |
| [Export → Loss Repo](granular/export_to_loss_repo.md) | Job + **Heavy** post-finish load | rows in LOSS SQL |

## Composite flows (`composite/`)

User-level actions, each composing one or more granular activities. Multi-item
composites loop the **single** IRP endpoint app-side (not the plural helpers) so each
`job_id` is captured and one item's failure doesn't orphan the rest.

| Flow | Composes | Key note |
|---|---|---|
| [Create submission](composite/create_submission.md) | EDM upload → RDM upload | Serial; RDM gated on EDM `FINISHED`. "Submission" (Name, CRM ID) is workbench-only — RM has no such concept |
| [Submit analyses](composite/submit_analyses.md) | Run analysis × N | Manual config, **no suite**: load pick-lists → hand-pick each setting. DLM/HD discovered from model profile |
| [Run GeoHaz](composite/run_geohaz.md) | GeoHaz × N portfolios | App loops single submit, records each `job_id`; per-portfolio failure continues (avoids plural helper's orphaning) |
| [Group results](composite/group_results.md) | Grouping × N | Surface included-vs-skipped members; group-of-groups is a sequencing gate; a group *is* an analysis |
| [Export to Loss Repo](composite/export_to_loss_repo.md) | Export job + load × N analyses | "Done" = loaded into LOSS SQL, **not** RM-`FINISHED`; heavy work is on the load |
| [Create subportfolios by LOB](composite/create_subportfolios_by_lob.md) | Native create-by-filter × N LOBs | Account-bucketed → slices double-count, can't be "pure"; needs a create-by-filter enhancement |
