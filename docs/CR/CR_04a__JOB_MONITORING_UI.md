# Change Request — Job monitoring, search, cancel, and resubmit UI

**ID:** CR-004a
**Status:** Ready to apply — supersedes the original version of this document
**Supersedes:** the requestor-id-based design below is replaced; the schema this
version depends on (`rwb_job.link_type`/`link_id`/`context_type`/`context_id`)
already shipped in CR-04c Phase 1.
**Depends on:** CR-04c Phase 1 (`link_type`/`link_id` columns on `rwb_job`) —
already merged. CR-004 (per-queue workers) is not a hard prerequisite, but the
"kill a stuck job" operational step this UI surfaces is safer once it lands.
**Applies to:** `app/services/rwb_job_service.py`, new `app/routers/rwb_jobs.py`
(replacing the stub in `app/routers/shell.py`), new templates, `tests/`.

## 1. Summary

A monitoring-and-search page for `rwb_job`, replacing the placeholder currently
wired to the `workflows.rwb_jobs` nav slot. Every job row is found by joining
through its `link_type`/`link_id` (the EDM or RDM it concerns, per CR-04c) out
to `submission_edm`/`submission_rdm` and `submission` — not by reading
`requestor_type`/`requestor_id`, which the original version of this CR relied
on and which CR-04c has since made the wrong join to reach a submission.

Two actions, extended from the original decision:

- Cancel a `pending`, `failed`, or **dead** job. "Dead" is not a stored
  `status_code` — it is a `running` row whose `rwb_job_heartbeat` row is
  missing or older than `settings.rwb_heartbeat_stale_secs`, the same
  condition `reconcile_stale_rwb_jobs` already reclaims to `pending` on the
  poller's next pass (§3 decision 6a). A `running` row with a live heartbeat
  is not cancellable — this is not a general "stop a running job" mechanism.
- Resubmit a `failed` job — resets the same row in place. Unchanged.

No mechanism is added to stop a **live** `running` job, submit an arbitrary
new job, or drain a queue from the UI. See §6.

## 2. Why this version replaces the original

The original CR-04a (§5.2, decision 1) said the monitoring view queries
`rwb_job` "directly," showing `requestor_type`/`requestor_id`. That was written
before CR-04c. CR-04c Phase 1 has since shipped `link_type`/`link_id` —
columns that name the EDM/RDM a job concerns, populated from each job's worker
body rather than from whoever triggered it — specifically so a general search
like "every job for this submission" does not need a bespoke join per job type
the way `backfill_edm_detail_rows()` still does. That is exactly the search
this CR now builds. Reading `requestor_id` for display, or filtering on it,
would be building the new page against the column CR-04c documented as
misleading and on its way out.

`app/services/rwb_job_service.list_rwb_jobs_for_monitoring()` already exists
(added under the original CR-04a) but is called from nowhere — no route, no
template. This CR repurposes it: same name, same purpose (the monitoring
page's one read), rewritten to join through `link_type`/`link_id` and to take
the filter parameters below instead of returning every row unconditionally.

## 3. Decisions

1. **Search reaches submission through `link_type`/`link_id`, never through a
   stored `submission_id` on `rwb_job`.** Matches CR-04c §12: `rwb_job` gets no
   `submission_id` column. A job's `link_type`/`link_id` names the EDM or RDM;
   `submission_edm`/`submission_rdm` are the existing M:N joins from there to
   `submission`. A job whose `link_type = 'not_applicable'` (metadata sync,
   dummy jobs) has no submission and is never matched by a submission filter.
2. **A job's EDM/RDM can belong to zero, one, or several submissions.** The
   list shows every submission a matched job's EDM/RDM belongs to (comma-style,
   first name plus "+N more" — the same display convention
   `partials/library_table.html` already uses for an EDM/RDM's submissions).
   Filtering by submission or owner matches a job if **any** of its EDM/RDM's
   submissions match; it does not fan the job out into one row per submission.
3. **Filters, all AND-combined, all optional:**
   - **Submission name / cedant** — free-text, matched the same
     word-and-clauses way `submission_service.list_submissions` matches `name`/
     `cedant_name` today.
   - **Submission status** (`ACTIVE`/`COMPLETED`/`CANCELLED`) — picked from
     `submission_status_kind`, same source `submission_service.status_kinds()`
     already reads.
   - **Owner** — the submission's `assigned_analyst_id`, **defaulting to the
     current user** exactly like `/submissions` (no `owner` param at all lands
     on "my submissions' jobs"; `owner=any` clears it; an explicit list of
     analyst ids narrows to just them). This is a plain predicate, not a row
     scope (Article 6) — any analyst can pass `owner=any` or another analyst's
     id and see it.
   - **Job type** (`rwb_job_type`) — multi-select from `rwb_job_type_kind`.
   - **Job status** (`status_code`) — multi-select from `rwb_job_status_kind`.
   - A job with no submission at all (`link_type = 'not_applicable'`, or an
     EDM/RDM not yet attached to any submission) is excluded by a submission-
     name/status/owner filter but still shown when none of those three are set
     — job type and job status filter independently of submission.
4. **Grouping/order unchanged from the original decision:** rows are ordered by
   `rwb_job_type`, then `status_code`, then most-recent `updated_at`, matching
   `list_rwb_jobs_for_monitoring`'s existing `ORDER BY` and the original
   contract. Filtering narrows this same ordered list; it does not introduce a
   separate sort.
5. **Queued jobs are shown even though they have no start time** (unchanged).
   A `pending` row is rendered as a distinct marker, not a zero-width bar.
6. **Cancel applies to `pending`, `failed`, and dead `running` rows.**
   `pending` → `cancelled`: same guarded `UPDATE ... WHERE status_code =
   'pending'` as `claim_rwb_job`'s atomic claim — a race against a worker
   claiming the same row resolves by rowcount, never a double-execute.
   `failed` → `cancelled`: dismisses a failure nobody intends to resubmit;
   picking Cancel forecloses Resubmit on that row, and vice versa. Dead
   `running` → `cancelled` (§3 decision 6a): the guard is `WHERE status_code =
   'running' AND (heartbeat missing OR older than
   settings.rwb_heartbeat_stale_secs)`, in the same `UPDATE` as the other two
   cases — a `running` row with a live heartbeat matches none of the three
   branches and is left alone.
6a. **A dead job's own `status_code` stays `'running'` until acted on.**
   "Dead" is computed at read time (`is_dead` in
   `list_rwb_jobs_for_monitoring`'s result, and independently re-checked by
   `cancel_rwb_job`'s own guard at write time — the read never gates the
   write), never written back to the row by the read path. This deliberately
   creates a race between the UI's Cancel and the poller's own
   `reconcile_stale_rwb_jobs` reclaim: whichever runs first wins (rowcount
   0 for the loser, same shape as every other guarded transition in this
   table) — an analyst can lose that race to the poller and see the row
   reappear as `pending` instead of `cancelled`. Accepted: this is the same
   trade every other guarded transition here already makes, and the loser is
   never left in an inconsistent state.
7. **No mechanism to stop a job with a live heartbeat** (narrowed from the
   original CR-004 §6, which covered every `running` job). A `running` job
   whose worker is still heartbeating can only be stopped by killing its
   queue's worker process — an operational step outside the app.
8. **Resubmit calls the existing `ensure_pending_rwb_job`, unchanged** (from
   the original CR-04a decision 5) — now also carrying that row's
   `link_type`/`link_id`/`context_type`/`context_id` straight through
   (`resubmit_rwb_job` already does this, per CR-04c T2/T3). No new row, no
   new dedup-key scheme.
9. **No bespoke job-submission form, no drain trigger, no worker scale-up/
   down control, no priority reordering** — all unchanged from the original
   CR-04a decisions 6–9. See §6.

## 4. What changes, by area

### 4.1 Schema

None beyond what CR-04c Phase 1 already shipped (`link_type`, `link_id`,
`context_type`, `context_id` on `rwb_job`, `cancelled` on
`rwb_job_status_kind`). This CR adds no column, no table, no migration.

### 4.2 `app/services/rwb_job_service.py`

`list_rwb_jobs_for_monitoring()` is rewritten (not renamed — it is already the
right name for what this page needs) to:

- Accept the five filters from §3.3 as keyword arguments, all optional and
  independently `None`/empty-meaning-"off", matching
  `submission_service.list_submissions`'s own convention for AND-combined
  optional filters.
- Join `rwb_job` to `irp_edm` (`link_type = 'edm'`) and `irp_rdm`
  (`link_type = 'rdm'`) for the linked entity's name, and to
  `rwb_job_type_kind` for the type label.
- Apply the submission-name/status/owner filters as an `EXISTS` predicate
  reaching `submission_edm`/`submission` or `submission_rdm`/`submission`
  through the row's own `link_type`/`link_id` — never a `JOIN` that would
  duplicate a job row once per matching submission.
- Return each row's linked entity name and enough to resolve its submissions,
  but resolve the actual submission list in a **second, batched query** over
  the distinct `(link_type, link_id)` pairs in the result set — mirrors
  `edm_service.latest_backfill_statuses`'s batch-then-join-in-Python shape,
  and avoids `STRING_AGG`/`GROUP_CONCAT` (banned in service SQL per
  `submission_service.py`'s own portability contract — not portable to the
  SQLite unit tier).

`resubmit_rwb_job`, `get_rwb_job` — unchanged; both already handle
`link_type`/`link_id`/`context_type`/`context_id` per CR-04c.

`cancel_rwb_job` — extended (§3 decision 6/6a): the guard widens from
`WHERE status_code = 'pending'` alone to also match `status_code = 'failed'`
and a dead `running` row (`LEFT JOIN rwb_job_heartbeat` in the same guarded
`UPDATE`, checking staleness against `settings.rwb_heartbeat_stale_secs`).
Still one guarded statement, still returns a plain rowcount-based bool —
callers that only ever cancelled `pending` rows see no behavior change.

### 4.3 Monitoring route + templates (new; replaces the stub)

- `app/routers/rwb_jobs.py` (new file, registered in `app/main.py`) —
  mirrors `app/routers/edms.py`'s library-page shape: a full-page route, a
  `/table` fragment route for HTMX polling/filtering, and the two action
  routes. Removes the `workflows_rwb_jobs` stub handler from
  `app/routers/shell.py` and the stub's placeholder body from
  `app/templates/pages/workflows_rwb_jobs.html`. The nav entry itself
  (`workflows.rwb_jobs` in `app/nav/manifest.py`, route `/workflows/rwb-jobs`)
  does not change.
- `GET /workflows/rwb-jobs` — full page: filter form (submission/cedant text,
  submission-status picker, owner picker defaulting to the current analyst,
  job-type picker, job-status picker — same `multi_picker` macro and
  default-to-mine convention as `pages/submissions.html`) plus the table. The
  reset link goes to the bare `/workflows/rwb-jobs` URL (no query string),
  not `?owner=any` — `pages/submissions.html`'s own "Clear filters" link uses
  `?owner=any`, which clears every filter but also switches Owner to
  everyone's; this page's reset returns Owner to the actual default (the
  current analyst) along with every other filter.
- `GET /workflows/rwb-jobs/table` — the table fragment alone, for the filter
  form's HTMX target and for live polling while any listed row is
  non-terminal (mirrors `partials/irp_jobs_table.html`'s self-polling
  wrapper).
- Each row renders: job type, EDM/RDM name (or "—" for `not_applicable`),
  submission(s) as a link to `/submissions/{id}` opening in a new tab (first
  name + "+N more", or "—" for none), status — `running` rows whose
  `is_dead` flag is set render as a distinct "Dead" chip, not "Running",
  even though `status_code` is still `running` underneath — the raw
  `submitted_at` timestamp ("—" for `pending`, null until claimed), a
  computed elapsed **duration** (not a timestamp: "queued 2m 14s" for
  `pending` off `inserted_at`, a running duration off `submitted_at` for
  `running`/dead, a fixed `completed_at` − `submitted_at` span for terminal
  rows, "—" when nothing is computable), failure detail (`failed` only), and
  the row action.
- Every sortable column (job type, EDM/RDM, submission, status, submitted at,
  elapsed) is a clickable header, same click-to-sort convention as
  `pages/submissions.html` (D15) — clicking flips direction; clicking a
  different column starts it in that column's own default direction.
  Sorting is a display order over the already-filtered rows, independent of
  the filters. Elapsed sorts by its underlying seconds, never the formatted
  string.
- `POST /workflows/rwb-jobs/{id}/cancel` — shown for `pending`, `failed`, and
  `dead` rows, `hx-confirm`'d, calls `cancel_rwb_job`. Not shown for a
  `running` row with a live heartbeat.
- `POST /workflows/rwb-jobs/{id}/resubmit` — shown only for `failed` rows,
  calls `resubmit_rwb_job` by id. A `failed` row therefore shows both Cancel
  and Resubmit; picking either forecloses the other.
- No action shown for `succeeded`/`cancelled` rows or a live `running` row;
  the page states plainly that stopping a live running job needs an operator
  outside the app.

Per AGENTS.md's UI workflow: this has real new layout (a filter form plus a
table it didn't have before), so it gets a rendered HTML preview and approval
before the route/templates are built.

### 4.4 Tests

- Unit: `list_rwb_jobs_for_monitoring` with no filters returns every row,
  ordered as today.
- Unit: submission-name filter matches a job via its EDM's `submission_edm`
  row; via its RDM's `submission_rdm` row; excludes a `not_applicable`-linked
  job; excludes an EDM/RDM attached to no submission.
- Unit: submission-status filter and owner filter, same three shapes as
  above, AND-combined with the name filter and with each other.
- Unit: owner filter defaults to a passed-in "current user" id when omitted,
  matching the call convention `list_submissions` already uses (the route
  test covers the actual default-to-current-request-user behavior).
- Unit: job-type and job-status filters narrow independently of the
  submission filters.
- Unit: a job whose EDM/RDM belongs to two submissions returns both names for
  display, from the batched second query.
- Unit: `list_rwb_jobs_for_monitoring` marks a `running` row `is_dead = 1`
  when its heartbeat is missing or older than
  `settings.rwb_heartbeat_stale_secs`, and `is_dead = 0` when the heartbeat is
  live; `pending`/terminal rows are never `is_dead`. The `"dead"` value in
  `status_codes` matches only `is_dead` rows and OR-combines with any real
  status codes also requested.
- Unit: `cancel_rwb_job` now succeeds on a `failed` row and on a dead
  `running` row (missing heartbeat, and separately a stale one), and still
  fails on `succeeded`/`cancelled` and on a `running` row with a live
  heartbeat — the claim/cancel race test asserts a live heartbeat, so the
  race it pins is pending-vs-running, not dead-vs-alive.
- Unit (existing, unchanged): `resubmit_rwb_job` race/precondition behavior
  per the original CR-04a §4.4 — not touched by this version.
- Route-level: cancel/resubmit precondition failures re-render the row's
  actual current state rather than erroring, per the existing contract.

## 5. Design detail

### 5.1 Why cancel is safe against a concurrent claim

Unchanged from the original CR-04a §5.1 — `claim_rwb_job` and `cancel_rwb_job`
both gate on `WHERE status_code = 'pending'` and both check rowcount.

### 5.2 What the monitoring view can and cannot show

Unchanged from the original CR-04a §5.2 — the view shows the current attempt's
state only; resubmit overwrites the same row, so a prior attempt's
`error_detail` is gone once resubmitted.

### 5.3 Why the submission join is read-side only

`rwb_job` gains no new column here. The submission-search capability comes
entirely from reading the columns CR-04c already added
(`link_type`/`link_id`) and joining outward at query time, the same way
`analysis_service.py` already joins `submission_edm`/`submission_rdm` to
reach analyses from a submission. This keeps the join direction consistent
with the rest of the codebase — submission is always reached *from* an
EDM/RDM, never stored redundantly on a row that already names its EDM/RDM.

## 6. Out of scope

- Stopping a `running` job with a **live** heartbeat.
- A generic form to submit a new job of any type.
- Triggering a queue drain from the UI.
- Scaling worker processes/threads from the UI.
- Priority reordering of queued jobs.
- Preserving failed-attempt history across a resubmit.
- Preserving any state for a dead job cancelled from the UI — same as
  resubmit, the row is simply moved to `cancelled`; nothing about the dead
  attempt (worker id, last heartbeat) is captured anywhere it wasn't already.
- Fanning a job row out per matching submission when its EDM/RDM belongs to
  more than one (§3 decision 2) — the row stays one row; the submission
  filter is "matches at least one."
- Rewiring `edm_service`/`rdm_service`'s own `latest_backfill_status*` or
  `breakout_service`'s in-flight checks to use `link_type`/`context_type`
  instead of `requestor_type`/`requestor_id` — that is CR-04c Phase 2, a
  separate, already-scoped piece of work this CR does not touch.

## 7. Residual risks

1. **Resubmit erases the failed attempt's `error_detail`.** Unchanged from the
   original CR-04a §7.1.
2. **A job with a live heartbeat has no cancellation path and can only be
   stopped by killing its worker process.** Narrowed from the original
   CR-04a §7.2, which covered every `running` job — a dead one is now
   cancellable directly (§3 decision 6/6a).
3. **Cancel racing the poller's reconciler on the same dead row** (§3 decision
   6a) **can lose:** an analyst clicking Cancel on a dead job can be beaten by
   `reconcile_stale_rwb_jobs` resetting the same row to `pending` first — the
   analyst then sees the row reappear as queued rather than cancelled, with
   no error. Accepted: the alternative (locking or otherwise coordinating with
   the poller) is disproportionate to a UI convenience action, and the
   analyst can simply cancel again once it's back to `pending`.
4. **A job's submission membership can change after the job ran.** An EDM
   detached from a submission after its `backfill_edm_detail` job completed
   no longer shows under that submission's filter — the join is evaluated at
   read time, not stamped onto the job. Accepted: `rwb_job` has no submission
   column to go stale, so this reflects current membership, not a caching
   bug.
5. **The owner filter's default-to-current-user matches `/submissions`'s
   convention exactly, including its complexity** (the multi-picker, the
   `owner=any` escape hatch, the hidden-input echo). Anyone changing one
   should check whether the other needs the same change — the two are not
   sharing code today, only convention.

## 8. Acceptance criteria

- `list_rwb_jobs_for_monitoring` accepts submission name/status/owner and job
  type/status filters, AND-combined, all optional, and resolves them through
  `link_type`/`link_id` — never `requestor_type`/`requestor_id`. The
  job-status filter accepts the synthetic `"dead"` value alongside real
  `rwb_job_status_kind` codes, and every row carries a computed `is_dead`
  flag.
- The monitoring page lists jobs by type and status (including `pending` jobs
  with no start time, and `running` jobs split into live vs. dead), each
  showing its EDM/RDM and a link to its submission(s) (new tab). Every column
  sorts by clicking its header.
- The page defaults to the current analyst's own submissions' jobs, with an
  owner filter to see another analyst's or everyone's.
- Working Cancel (`pending`, `failed`, or dead `running` — never a live
  `running` row) and Resubmit (`failed` only) actions.
- No code was added for: stopping a job with a live heartbeat, a generic
  job-submission form, a UI drain trigger, worker scaling, or priority
  queues.
- The monitoring UI (filter form + table) was previewed and approved before
  the route/templates were built (AGENTS.md UI workflow).

## 9. Grep checklist

- `list_rwb_jobs_for_monitoring` (the repurposed function — confirm no other
  caller assumed its old no-argument signature)
- `requestor_type`, `requestor_id` (confirm the new query never reads them)
- `link_type`, `link_id` (confirm every filter and join goes through these)
- `workflows.rwb_jobs`, `/workflows/rwb-jobs` (nav slot and route — unchanged
  path, changed handler)
- `submission_edm`, `submission_rdm` (the join tables this search reaches
  submission through)
- `multi_picker`, `owner_options`, `owner=any` (the filter convention reused
  from `pages/submissions.html`)
- `is_dead`, `rwb_job_heartbeat`, `rwb_heartbeat_stale_secs` (dead-job
  detection — confirm `cancel_rwb_job`'s guard and the monitoring query's
  `is_dead` column reference the same staleness threshold the poller's
  `reconcile_stale_rwb_jobs` already uses, per §3 decision 6/6a)
