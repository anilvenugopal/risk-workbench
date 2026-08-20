# Worker and Poller Contract: Package Retirement

## EDM import

1. A standalone or submission-context import writes one `irp_edm` row.
2. A submission-context import writes `submission_edm` in the same transaction as
   the EDM row.
3. One `upload_edm` head targets the EDM. Its input may carry
   `requested_from_submission_id` as provenance.
4. The worker submits one Risk Modeler EDM import and records one `irp_job` with
   `irp_edm_id`.
5. On FINISHED, the poller updates the EDM and enqueues `backfill_edm_detail`.
6. EDM completion never enqueues RDM upload work.

## RDM import

1. A standalone or submission-context import writes one `irp_rdm` row.
2. A submission-context import writes `submission_rdm` in the same transaction as
   the RDM row.
3. One `upload_rdm` head targets the RDM. No EDM ID is accepted or derived.
4. The worker calls `submit_rdm_import_job` with `rdm_name`, `rdm_file_path`, and
   `exposure_set_name=rdm_name`.
5. The worker records one `irp_job(import_rdm)` with `irp_rdm_id` set and
   `irp_edm_id` null.
6. On FINISHED, the poller enqueues one `backfill_rdm_analyses` head.

## Broker analysis capture

- Search Risk Modeler by `sourceRdmName` only.
- Capture each result once under UNIQUE (`rdm_id`, `irp_id`).
- Write `irp_analysis.edm_id` null and no package column.
- Fetch metadata one analysis at a time; one metadata failure leaves that analysis's
  fields blank without failing the enumeration.
- A successful enumeration prunes missing analyses by `rdm_id`.
- Manual RDM sync repeats the same RDM-wide capture.

## Contextual EDM sync

- The EDM backfill targets only the selected EDM.
- RDM analysis backfills are selected from `submission_rdm` for the URL submission.
- The same RDM is enqueued at most once even if it is related to other submissions.
- The direct library EDM sync does not infer a submission and does not sync unrelated
  RDMs.

## Delete behavior

Association detach is request-path SQL only and never enqueues a worker. The existing
package delete fan-in and its delete workers are removed. Physical EDM/RDM deletion
from Risk Modeler is outside this feature.

## PR #57 port boundary

Port the gateway signature, standalone upload body, poller completion behavior,
RDM-wide capture, fake gateway, and their focused tests. Replace every `package_id`
read and every Package attach/delete action with [the Package retirement contracts](../spec.md).
