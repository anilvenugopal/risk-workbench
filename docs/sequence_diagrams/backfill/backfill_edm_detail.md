# Execution — Backfill EDM Detail

After an EDM import finishes, the poller enqueues one `backfill_edm_detail` job.
The worker reads the EDM's portfolios, exposure metrics, treaties, and Data Bridge
summary, then replaces the stored portfolio and treaty detail.

EDM completion does not enqueue RDM imports. RDM imports and analysis refreshes are
independent operations.

## Rows written

| Table | Change |
|---|---|
| `rwb_job` | poller enqueues `backfill_edm_detail`; worker claims and completes it |
| `irp_portfolio` | upsert returned portfolios and stored exposure summaries |
| `irp_treaty` | upsert returned treaties and LOB attributes |
| `irp_edm` | fill exposure ID when found and stamp `as_of` after a usable refresh |

```mermaid
sequenceDiagram
    participant P as Poller
    participant DB as WORKBENCH DB
    participant W as Worker
    participant RM as Risk Modeler
    participant DataBridge

    rect rgb(245,238,255)
        P->>DB: UPDATE finished import_edm status
        P->>DB: INSERT rwb_job (backfill_edm_detail, edm_id)
        P-->>W: dispatch
    end

    rect rgb(238,255,244)
        W->>DB: claim job and SELECT irp_edm
        W->>RM: resolve exposure and list portfolios
        loop each portfolio
            W->>RM: get portfolio metrics
            W->>DataBridge: read exposure summary through irp-integration
        end
        W->>RM: list treaties and treaty LOBs
        W->>DB: upsert portfolio and treaty snapshots
        W->>DB: prune missing stored rows and stamp EDM as_of
        W->>DB: complete rwb_job
    end
```

Risk Modeler and Data Bridge failures do not run in a page handler. Individual
portfolio enrichment failures leave the affected optional detail empty. A refresh
that stores no usable detail fails without replacing the prior snapshot.
