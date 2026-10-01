# Execution Flow — Register a Submission (002 US1 / US5)

The analyst registers an incoming deal: name, cedant, treaty type, inception date, treaty
year, the deal's directory path, an optional renewal link to last year's submission. The
creator becomes its owner.

**Purely workbench.** Risk Modeler has no concept of a submission — it never learns the deal
name or the cedant — so this flow touches no external system at all. It writes two rows in one
transaction (three when the analyst typed a new cedant) and returns.

Code: `submissions.create` → `submission_service.create_submission`. The Cedant field filters
the active cedants already rendered into the form (`selectSearch` in `app.js`); a typed new name
is resolved by `cedant_service.new_cedant` and written by `cedant_service.save_new_cedant`.

**Classification:** entirely **sync**. No RM call, no `rwb_job`, no worker, no poller.

## Records written (in order)

| # | Table | Row / change | Written by | Process |
|---|---|---|---|---|
| 0 | `cedant` | INSERT, or UPDATE `is_active=1` — only when the analyst picked "Add cedant" or "Reactivate" (issue #129 P-06, P-07) | `save_new_cedant` | 🟦 request |
| 1 | `submission` | INSERT — `status_code='ACTIVE'`, `assigned_analyst_id = the creator` | `create_submission` | 🟦 request |
| 2 | `submission_status_event` | INSERT — the initial `ACTIVE` event, `reason=NULL` | `create_submission` | 🟦 request |

**All commit in one transaction** (R2), so a refused or abandoned form writes no cedant. That is Article 4 in miniature: `submission.status_code`
is a *cached* value and `submission_status_event` is the truth, so a submission may never exist
without its opening event.

## Sequence

```mermaid
sequenceDiagram
    actor User
    participant App as App (route)
    participant DB as WORKBENCH DB

    rect rgb(238,244,255)
        Note over User,DB: REQUEST PATH — the whole flow. No RM, no worker, no poller
        User->>App: GET /submissions/new
        App-->>User: the form
        Note over User,App: the Cedant field filters the rendered active cedants in the browser;<br/>a name matching none is staged as cedant_id="new" + new_cedant_name

        User->>App: POST /submissions (CSRF)
        Note over App,DB: ONE transaction
        opt cedant_id = "new"
            App->>DB: INSERT cedant, or UPDATE cedant SET is_active=1
        end
        App->>DB: INSERT submission (status_code='ACTIVE', owner = creator)
        App->>DB: INSERT submission_status_event ('ACTIVE')
        App-->>User: 303 → /submissions/{id}
    end
```

---

**Boundaries worth noting**

- **A submission exists only in our world.** Risk Modeler has no such concept: it will only
  ever see the *names of the EDMs and RDMs* the analyst later attaches. Something had to own
  "these imports belong to one deal", and this is it — which is why the submission is the
  association rows rather than anything in Risk Modeler.
- **The cedant list is shared.** Any analyst adds a cedant from this form or from
  `/cedants` (issue #129). The form shows partial matches above the "Add cedant" row, which
  is the only guard against a second spelling of an existing cedant.
- **Duplicate deals are legal.** The identity check is advisory by design. Anything that later
  wants "the submission for this deal" must not assume there is exactly one.
- **The creator is the owner, and the owner is not a permission.** `assigned_analyst_id` drives
  the "My submissions" filter and nothing else — every authenticated analyst can act on every
  submission (Article 6). See [find submissions](find_submissions.md).
- **Nothing is enqueued.** Registering a deal starts no background work; the first job appears
  only when an EDM or RDM import begins.
