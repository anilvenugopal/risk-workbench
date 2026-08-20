# Implemented Execution

Each document describes a user action implemented by the workbench. The diagrams
name the database writes, the process that performs each write, and the asynchronous
handoffs among the request handler, worker, and poller.

Designs for features that are not implemented live under [`planned/`](planned/README.md).

## Execution tables

| Table | Purpose | Written by |
|---|---|---|
| `rwb_job` | SQL-backed queue for application work | request handler or poller enqueues; worker claims and completes |
| `irp_job` | tracked Risk Modeler operation | worker inserts; poller updates status |
| `irp_edm`, `irp_rdm` | global EDM and RDM resources | request handler inserts; worker and poller update import state |
| `submission_edm`, `submission_rdm` | many-to-many submission associations | request handler inserts or deletes |
| `irp_portfolio`, `irp_treaty`, `irp_analysis` | stored Risk Modeler detail | workers replace stored data |
| `submission`, `submission_status_event` | deal and event-sourced status history | request handler |

An EDM and an RDM may each relate to several submissions. There is no direct
EDM-to-RDM relationship. `irp_job.requested_from_submission_id` stores request
provenance and does not select an execution target.

## Process boundary

- The request handler validates input, writes application rows, and enqueues work.
- The worker submits Risk Modeler operations and performs result reads.
- The poller performs one status check per tracked operation and enqueues the next
  mechanical job after a terminal result.
- `poll_*_to_completion` is not used.
- Data Bridge reads occur only in a worker through `irp-integration`.

## Implemented actions

### Submissions

| Action | Document | Writes | Asynchronous work |
|---|---|---|---|
| Register | [Register a submission](submissions/create_submission.md) | `submission`, `submission_status_event` | none |
| Find | [Find submissions](submissions/find_submissions.md) | none | none |
| View | [View a submission](submissions/view_submission.md) | none | none |
| Edit status and metadata | [Manage a submission](submissions/manage_submission.md) | submission tables | none |
| Add or remove data | [Manage submission data](submissions/manage_submission_data.md) | association rows; new imports also create an entity and `rwb_job` | import jobs only |

### EDMs and RDMs

| Action | Document | Request writes | Completion |
|---|---|---|---|
| Browse libraries | [Browse libraries](entities/browse_libraries.md) | none | none |
| Import EDM | [Import an EDM](entities/import_edm.md) | `irp_edm`, optional `submission_edm`, `rwb_job(upload_edm)` | poller enqueues `backfill_edm_detail` |
| Import RDM | [Import an RDM](entities/import_rdm.md) | `irp_rdm`, optional `submission_rdm`, `rwb_job(upload_rdm)` | poller enqueues `backfill_rdm_analyses` |
| View EDM | [View EDM detail](entities/view_edm_detail.md) | none | contextual RDM analyses load from stored rows on demand |
| View RDM | [View RDM detail](entities/view_rdm_detail.md) | none | none |
| Retry import | [Recover an import](entities/recover_import.md) | entity status and revived upload job | repeats the entity import |

### Detail refresh

| Action | Document | Worker reads | Stored rows |
|---|---|---|---|
| Refresh EDM detail | [Backfill EDM detail](backfill/backfill_edm_detail.md) | portfolios, metrics, treaties, Data Bridge summary | `irp_portfolio`, `irp_treaty` |
| Refresh RDM analyses | [Backfill RDM analyses](backfill/backfill_rdm_analyses.md) | analyses by RDM name and per-analysis metadata | `irp_analysis` with `edm_id` null |
| Request refresh | [Manual sync](backfill/manual_sync.md) | same worker reads | same stored rows |

## Mermaid conventions

- Blue blocks are request-handler work.
- Green blocks are worker work.
- Purple blocks are poller work.
- `INSERT`, `UPDATE`, and `DELETE` arrows name application database writes.
- A poller loop performs one non-blocking Risk Modeler status request per pass.
