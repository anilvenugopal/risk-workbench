# Execution — Refresh Stored Detail

The Sync action enqueues stored-detail work and returns without waiting for Risk
Modeler. The request handler does not perform polling or result reads.

## Jobs by route

| Route | Jobs |
|---|---|
| `POST /edms/{edm_id}/sync` | one `backfill_edm_detail` job |
| `POST /rdms/{rdm_id}/sync` | one `backfill_rdm_analyses` job |
| `POST /submissions/{submission_id}/edms/{edm_id}/sync` | one EDM detail job plus one analysis job for each RDM in `submission_rdm` |

The contextual route validates `submission_edm` before enqueueing. It selects RDM
IDs only from `submission_rdm` for the named submission. The direct EDM route has no
submission context and does not select RDMs.

```mermaid
sequenceDiagram
    actor User
    participant App as App (route)
    participant DB as WORKBENCH DB
    participant W as Worker

    rect rgb(238,244,255)
        User->>App: POST an EDM or RDM sync route
        App->>DB: validate entity and optional submission association
        App->>DB: insert or revive entity backfill job
        opt contextual EDM route
            App->>DB: SELECT RDM IDs from submission_rdm
            App->>DB: insert or revive one analysis job per RDM
        end
        App-->>W: dispatch pending jobs
        App-->>User: HTMX body or redirect
    end

    rect rgb(238,255,244)
        W->>DB: claim and run each backfill job
    end

    loop while a selected backfill job is pending or running
        User->>App: GET detail body
        App->>DB: read stored detail and job status
        App-->>User: 204 for populated in-progress detail, otherwise updated body
    end
```

An existing pending or running job absorbs a repeated click. A terminal job is
revived for a new analyst request. Once all selected jobs are terminal, the rendered
body omits its HTMX polling trigger.
