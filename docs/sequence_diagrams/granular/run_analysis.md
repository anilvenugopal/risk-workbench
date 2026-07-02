# Granular Flow — Run Analysis (single + batch)

Submits a portfolio analysis (DLM or HD) and tracks it to completion, producing an
Analysis (`analysisId`). Submitting several is **N independent analysis jobs**, not
one job — the app loops the single submit per analysis.

`irp-integration`: `analysis.submit_portfolio_analysis_job` → (async)
`analysis.get_analysis_job(job_id)`.

**Classification:** async **Job** (N jobs for a batch). Not heavy (the analysis
compute runs server-side; the submit moves no bulk bytes).

Pre-requisites:
- The target EDM + portfolio exist (`exposureId`, portfolio `uri` resolvable).
- Any named treaties exist on the EDM.
- The named reference data resolves: model profile (determines **DLM vs HD**),
  output profile, event rate scheme (**required for DLM**, optional for HD), tags,
  currency.

**Definition (single):**

1. User submits an analysis: EDM, portfolio, job name, analysis (model) profile,
   output profile, event rate scheme, treaty names, tags.
2. App calls `analysis.submit_portfolio_analysis_job(...)`, which synchronously
   performs:
   1. RM: duplicate-name check — `search_analyses(analysisName + exposureName)`;
      errors if the analysis name already exists for that EDM.
   2. RM: `search_edms` → `exposureId`; `search_portfolios` → `portfolio_uri`.
   3. RM: `search_treaties` (by names) → `treatyIds` (count must match).
   4. RM (reference data): resolve model profile → `modelProfileId` **and job type
      (DLM/HD)**; output profile → `outputProfileId`; event rate scheme →
      `eventRateSchemeId` (DLM requires it); tag ids; currency.
   5. RM: `POST` create analysis job → returns the **`job_id`**.
   - Returns `(job_id, request_body)`.
3. **Monitor (async)** — poll `analysis.get_analysis_job(job_id)` until terminal
   (`FINISHED` / `FAILED` / `CANCELLED`), tracking `progress`.
4. On `FINISHED`, the Analysis exists (`analysisId`), resolvable via
   `search_analyses`.

**Definition (multiple):**

1. User submits a list of analyses (e.g. from saved profiles + a naming convention).
2. App **loops `submit_portfolio_analysis_job(...)` per analysis**, capturing each
   `job_id` as it returns — one independent job per analysis. Each submit does its
   own per-analysis duplicate-name check; a submit that fails is recorded and the
   loop continues. (Any "reject all before submitting any" name pre-check is an
   app-side pass.)
3. **Monitor (async)** — each job is polled independently to its own terminal state
   (`get_analysis_job` per job); they finish at different times.

**Sequence Flow:**
```mermaid
sequenceDiagram
    actor User
    participant App
    participant RM as Risk Modeler API

    Note over User: Pre-req: EDM + portfolio exist, treaties + profiles resolvable

    alt Single analysis
        User->>App: Submit analysis (portfolio, profiles, treaties, name)
    else Multiple
        User->>App: Submit N analyses (profiles + naming)
    end

    loop each analysis (1 for single, N for multiple — app loops the single submit)
        rect rgb(238, 244, 255)
            Note over App,RM: submit_portfolio_analysis_job (synchronous submit)
            App->>RM: search_analyses (dup name check)
            RM-->>App: Existing analyses
            App->>RM: search_edms + search_portfolios
            RM-->>App: exposureId + portfolio_uri
            App->>RM: search_treaties (by names)
            RM-->>App: treatyIds
            App->>RM: reference data (model / output profile,<br/>event rate scheme, tags, currency)
            RM-->>App: ids + job type (DLM/HD)
            App->>RM: POST create analysis job
            RM-->>App: job_id
        end
    end

    rect rgb(245, 238, 255)
        Note over App,RM: Monitor — ASYNC, each job independently
        loop per job_id until terminal
            App->>RM: get_analysis_job (job_id)
            RM-->>App: status + progress
        end
    end

    alt FINISHED
        App->>RM: search_analyses → analysisId
        RM-->>App: Analysis (analysisId)
        Note over App: Analysis ready
    else FAILED / CANCELLED
        Note over App: No analysis produced
    end
```

---

**Boundaries worth noting** (candidates for metamodel bounding boxes — observations, not decisions):

- **A "batch" is a submission-time convenience, not a runtime unit.** Looping the
  single submit yields N independent jobs that run and finish separately. The only
  thing genuinely batch-scoped is the user's intent to submit them together (plus any
  optional app-side name pre-check). This is the sharpest test of whether a *Batch*
  bounding box earns its place, or whether it's just "N jobs created by one click."
- **Heavy reference-data resolution on the submit path.** A single submit fans out
  into many synchronous RM reads (profiles, event rate scheme, treaties, tags,
  currency) before the job is created. Any of them can fail the submit
  synchronously — a user-facing failure distinct from an async run failure.
- **DLM vs HD is discovered, not declared.** The job type comes from the model
  profile's `softwareVersionCode` at submit time; it affects downstream results
  (HD → PLT available). Whatever represents an analysis may want to record this.
- **`analysisId` resolves only after FINISHED**, like EDM's `exposureId` — the
  analysis entity exists before it has its Risk Modeler id.
