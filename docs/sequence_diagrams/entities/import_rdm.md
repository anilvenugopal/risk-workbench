# Execution — Import an RDM

An analyst imports one RDM against its own exposure set. The RDM is not applied to
an EDM. A submission-context import inserts `submission_rdm` with the RDM row; a
library import has no submission association.

Code: `rdm_service.import_rdm` → `entity_jobs._upload_rdm_body` →
`poller.run._handle_import_rdm_terminal` →
`entity_jobs._backfill_rdm_analyses_body`.

## Rows written

| Order | Table | Change | Process |
|---|---|---|---|
| 1 | `irp_rdm` | insert with `pending_import` | request handler |
| 2 | `submission_rdm` | insert when requested from a submission | request handler, same transaction as row 1 |
| 3 | `rwb_job` | enqueue one `upload_rdm` job | request handler |
| 4 | `irp_job` | insert one `import_rdm` operation with `irp_edm_id` null | worker |
| 5 | `irp_rdm` | update to `importing` | worker |
| 6 | `irp_job` | update mirrored status | poller |
| 7 | `rwb_job` | enqueue one `backfill_rdm_analyses` job after `FINISHED` | poller |
| 8 | `irp_analysis` | replace RDM-wide analysis rows; `edm_id` remains null | worker |
| 9 | `irp_rdm` | update to `ready` and stamp `as_of` | worker |

```mermaid
sequenceDiagram
    actor User
    participant App as App (route)
    participant DB as WORKBENCH DB
    participant W as Worker
    participant RM as Risk Modeler
    participant P as Poller

    rect rgb(238,244,255)
        User->>App: POST RDM import (name, file)
        App->>RM: cached name collision search
        App->>DB: INSERT irp_rdm (pending_import)
        opt submission context
            App->>DB: INSERT submission_rdm in the same transaction
        end
        App->>DB: INSERT rwb_job (upload_rdm)
        App-->>W: dispatch upload_rdm
        App-->>User: RDM table or detail
    end

    rect rgb(238,255,244)
        W->>DB: claim upload_rdm
        W->>RM: submit_rdm_import_job(rdm_name, file, exposure_set_name=rdm_name)
        RM-->>W: job ID and resource URI
        W->>DB: INSERT irp_job (import_rdm, irp_edm_id=NULL)
        W->>DB: UPDATE irp_rdm to importing
        W->>DB: complete upload_rdm
    end

    rect rgb(245,238,255)
        loop one status check per pass
            P->>RM: get_rdm_import_job(job ID)
            P->>DB: UPDATE irp_job status
        end
        alt FINISHED
            P->>DB: INSERT rwb_job (backfill_rdm_analyses)
            P-->>W: dispatch analysis refresh
        else FAILED or CANCELLED
            P->>DB: UPDATE irp_rdm to error
        end
    end

    rect rgb(238,255,244)
        W->>RM: search analyses by source RDM name
        W->>RM: get metadata for each analysis
        W->>DB: replace RDM analysis rows and set RDM ready
    end
```

The unique analysis identity is (`rdm_id`, `irp_id`). Metadata failure for one
analysis leaves its optional metadata blank without discarding the other analyses.
