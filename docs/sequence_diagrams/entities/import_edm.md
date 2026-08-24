# Execution — Import an EDM

An analyst imports one exposure file as an EDM. A submission-context import inserts
`submission_edm` in the same transaction as the EDM row. A library import creates no
submission association.

Code: `edm_service.import_edm` → `entity_jobs._upload_edm_body` →
`poller.run._handle_import_edm_terminal` → `backfill_edm_detail`.

## Rows written

| Order | Table | Change | Process |
|---|---|---|---|
| 1 | `irp_edm` | insert with `pending_import` | request handler |
| 2 | `submission_edm` | insert for a submission-context request | request handler, same transaction as row 1 |
| 3 | `rwb_job` | enqueue `upload_edm` | request handler |
| 4 | `irp_job` | insert tracked `import_edm` operation | worker |
| 5 | `irp_job_resource` | store the submit response resource URI | worker |
| 6 | `irp_edm` | update to `importing` | worker |
| 7 | `irp_job` | update mirrored Risk Modeler status | poller |
| 8 | `irp_edm` | update to `ready` or `error` | poller |
| 9 | `rwb_job` | enqueue `backfill_edm_detail` after `FINISHED` | poller |

```mermaid
sequenceDiagram
    actor User
    participant App as App (route)
    participant DB as WORKBENCH DB
    participant W as Worker
    participant RM as Risk Modeler
    participant P as Poller

    rect rgb(238,244,255)
        User->>App: POST EDM import (name, file)
        App->>RM: cached name collision search
        App->>DB: INSERT irp_edm (pending_import)
        opt submission context
            App->>DB: INSERT submission_edm in the same transaction
        end
        App->>DB: INSERT rwb_job (upload_edm)
        App-->>W: dispatch upload_edm
        App-->>User: EDM table or detail
    end

    rect rgb(238,255,244)
        W->>DB: claim upload_edm
        W->>RM: submit EDM import
        RM-->>W: job ID and resource URI
        W->>DB: INSERT irp_job and irp_job_resource
        W->>DB: UPDATE irp_edm to importing
        W->>DB: complete upload_edm
    end

    rect rgb(245,238,255)
        loop one status check per pass
            P->>RM: get EDM import status
            P->>DB: UPDATE irp_job status
        end
        alt FINISHED
            P->>DB: UPDATE irp_edm to ready
            P->>DB: INSERT rwb_job (backfill_edm_detail)
            P-->>W: dispatch detail refresh
        else FAILED or CANCELLED
            P->>DB: UPDATE irp_edm to error
        end
    end
```

The poller resolves the Risk Modeler exposure ID by name after completion. A delayed
name-search result can leave a ready EDM without `irp_id`; the detail worker retries
the lookup. EDM completion does not enqueue RDM work.
