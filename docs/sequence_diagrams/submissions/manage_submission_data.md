# Execution — Manage Submission Data

An analyst can import a new EDM or RDM, relate an existing EDM or RDM, or remove
one association. Only an active submission accepts these changes.

Code: `app.routers.submissions` and `app.services.submission_service`, with new
imports delegated to `edm_service.import_edm` or `rdm_service.import_rdm`.

## Add an existing EDM or RDM

Candidate reads select every live resource that has no association to the named
submission. The POST repeats the predicate so a stale selection cannot create an
invalid association. Adding an existing resource makes no Risk Modeler call and
does not enqueue a job.

```mermaid
sequenceDiagram
    actor User
    participant App as App (route)
    participant DB as WORKBENCH DB

    rect rgb(238,244,255)
        User->>App: GET /submissions/{id}/{kind}/candidates?q=...
        App->>DB: SELECT live resources not related to submission
        App-->>User: candidate rows
        User->>App: POST /submissions/{id}/{kind}/attach
        App->>DB: SELECT submission status
        alt submission is ACTIVE
            App->>DB: INSERT association rows with repeated eligibility predicate
            App-->>User: refreshed table
        else submission is closed
            App-->>User: 409
        end
    end
```

## Import a new EDM or RDM

The resource row and association row are inserted in one transaction. The upload
job is then enqueued with `requested_from_submission_id` as provenance. EDM and RDM
imports continue through their entity-specific worker and poller processing.

```mermaid
sequenceDiagram
    actor User
    participant App as App (route)
    participant DB as WORKBENCH DB
    participant W as Worker

    rect rgb(238,244,255)
        User->>App: POST /submissions/{id}/{kind}/import
        App->>DB: SELECT submission status
        App->>DB: INSERT irp_edm or irp_rdm (pending_import)
        App->>DB: INSERT submission_edm or submission_rdm
        Note over App,DB: entity and association use one transaction
        App->>DB: INSERT rwb_job (upload_edm or upload_rdm)
        App-->>W: dispatch upload job
        App-->>User: refreshed table or detail redirect
    end
```

## Remove an association

Removal deletes only `submission_edm` or `submission_rdm`. The resource, its other
submission associations, its stored detail, and its Risk Modeler resource remain.

```mermaid
sequenceDiagram
    actor User
    participant App as App (route)
    participant DB as WORKBENCH DB

    rect rgb(238,244,255)
        User->>App: POST /submissions/{id}/{kind}/{entity_id}/detach
        App->>DB: SELECT submission status
        alt submission is ACTIVE
            App->>DB: DELETE one association
            App-->>User: refreshed table
        else submission is closed
            App-->>User: 409
        end
    end
```
