# Granular Flow — GeoHaz (Geocode / Hazard)

Re-runs geocoding and/or hazard retrieval on a portfolio (e.g. for new model
versions). Mutates the portfolio's geocode/hazard data in place — it does not
produce a new entity.

`irp-integration`: `portfolio.submit_geohaz_job(...)` → (async)
`portfolio.get_geohaz_job(job_id)`.

**Classification:** async **Job**. Not heavy (no bulk byte movement; the work
happens server-side in Risk Modeler).

Pre-requisites:
- The target EDM exists and is resolvable by name (`exposureId` known).
- The target portfolio exists within that EDM **and has accounts with locations**
  (GeoHaz on an empty portfolio is rejected).

**Definition:**

1. User initiates "Run GeoHaz" for a portfolio, choosing geocode version and
   which hazards (EQ / WS).
2. App calls `portfolio.submit_geohaz_job(portfolio_name, edm_name, version, hazard_eq, hazard_ws)`,
   which synchronously performs pre-submit validation:
   1. RM: `search_edms(exposureName="<edm_name>")` → `exposureId`.
   2. RM: `search_portfolios(exposureId, portfolioName="<name>")` → `portfolio_uri` + `portfolioId`.
   3. RM: `search_accounts_by_portfolio(exposureId, portfolioId)` → **validates the
      portfolio has ≥1 account with ≥1 location**; errors otherwise.
   4. Builds the layer set (a `geocode` layer always; `earthquake` / `windstorm`
      hazard layers appended per the flags).
   5. RM: `POST` GeoHaz → returns the **`job_id`**.
   - Returns `(job_id, request_body)`.
3. **Monitor (async)** — poll `portfolio.get_geohaz_job(job_id)` until terminal
   (`FINISHED` / `FAILED` / `CANCELLED`), tracking `progress`.
4. On `FINISHED`, the portfolio's geocode/hazard data has been updated in place.

**Sequence Flow:**
```mermaid
sequenceDiagram
    actor User
    participant App
    participant RM as Risk Modeler API

    Note over User: Pre-req: EDM + portfolio exist,<br/>portfolio has locations

    User->>App: Run GeoHaz (portfolio, version, EQ/WS)

    rect rgb(238, 244, 255)
        Note over App,RM: submit_geohaz_job (synchronous submit + validation)
        App->>RM: search_edms → exposureId
        RM-->>App: exposureId
        App->>RM: search_portfolios → portfolio_uri, portfolioId
        RM-->>App: portfolio detail
        App->>RM: search_accounts_by_portfolio
        RM-->>App: accounts + location counts
        alt No accounts / no locations
            App-->>User: Error: nothing to GeoHaz
        end
        App->>RM: POST geohaz (geocode [+ hazard] layers)
        RM-->>App: job_id
    end

    rect rgb(245, 238, 255)
        Note over App,RM: Monitor — ASYNC (runs inside Risk Modeler)
        loop until terminal (FINISHED / FAILED / CANCELLED)
            App->>RM: get_geohaz_job (job_id)
            RM-->>App: status + progress
        end
    end

    alt FINISHED
        Note over App: Portfolio geocode/hazard updated in place
    else FAILED / CANCELLED
        Note over App: Portfolio unchanged
    end
```

---

**Boundaries worth noting** (candidates for metamodel bounding boxes — observations, not decisions):

- **Async job that produces no entity.** Unlike EDM/RDM/analysis, GeoHaz creates
  nothing new — it mutates the portfolio. The only thing to track is the job
  itself and the fact that the portfolio's hazard state changed.
- **Meaningful pre-submit validation is synchronous.** The "does this portfolio
  have locations" check happens *before* a job exists, on the request path — a
  user-facing failure distinct from an async job failure (same split as EDM
  upload's dup-check).
- **Sync submit, no heavy upload.** The submit is a quick POST (no S3 upload), so
  GeoHaz is a candidate for the *light* tier at submit time even though it spawns
  an async job — different from EDM/RDM whose submit is heavy.
- **Per-portfolio granularity.** One job per portfolio; re-geocoding an EDM with
  many portfolios is many jobs.
