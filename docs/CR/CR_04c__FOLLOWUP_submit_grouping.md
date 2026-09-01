# Follow-up for `submit_grouping` — add `link_type`/`link_id`/`context_type`/`context_id`

**Applies to:** branch `012-grouping-execution` (PR #84), specifically
`app/services/grouping_service.py` and `app/poller/run.py`.

**Do not start this until `CR_04c__RWB_JOB_LINK_AND_CONTEXT.md`'s Phase 1 has
merged to `main`** and this branch has been rebased/merged onto that `main`.
Phase 1 adds four new columns to `rwb_job`
(`link_type`, `link_id`, `context_type`, `context_id`) and makes them
**required, keyword-only parameters** on `enqueue_rwb_job` and
`ensure_pending_rwb_job` (`app/services/rwb_job_service.py`) — no default
values. Once this branch picks up that change, both of `grouping_service.py`'s
calls into those two functions will fail to import/run until this follow-up
is applied. This document is self-contained — it does not assume you have
read the full CR, only that Phase 1 has landed.

## What the four new fields mean

- `link_type` / `link_id` — which EDM or RDM this job concerns, for search
  ("find every job for this EDM"). Always required. `link_type` is one of
  `"EDM"`, `"RDM"`, `"NOT_APPLICABLE"` (a fixed, seeded set — passing anything
  else fails a database foreign key check). `link_id` is the EDM or RDM row's
  id, or `NULL` when `link_type="NOT_APPLICABLE"`.
- `context_type` / `context_id` — what specific object the operation acts on
  (this is what `requestor_id` has always actually held — `context_id` is
  simply the existing `requestor_id` value under an honest name).
  `context_type` is a short fixed string like `"edm"`, `"portfolio"`,
  `"irp_job"` (also a seeded set). `context_id` = whatever you're already
  passing as `requestor_id` — no new value to compute.

`requestor_type`/`requestor_id` themselves are unchanged and still required —
this follow-up only adds four new arguments alongside them, it does not
remove or rename anything you already pass.

## Change 1 — `app/services/grouping_service.py`, the `enqueue_rwb_job` call

Current call (as of this branch, `grouping_service.py:603-605`):

```python
job_id = rwb_job_service.enqueue_rwb_job(
    requestor_type="analyst_request", requestor_id=grouping_request_id,
    rwb_job_type="submit_grouping", input_data=plan, actor_id=actor_id)
```

`grouping_request_id` here is a freshly minted UUID (the batch/request
identifier), not an EDM id — same pattern as `execute_analysis_batch`'s
`execution_id` elsewhere in this codebase. `context_id` should be that same
`grouping_request_id`, and `context_type` should be a new short name for
this kind of object — recommend `"grouping_request"` (parallel to
`execution` used for `execute_analysis_batch`).

`link_id` needs an EDM id. Look at what's already in `plan` right before this
call — the plan-composition step already resolves member analyses and (per
the CR's research) can reach each member's EDM. If `plan` already carries a
single `edm_id` key (check the dict you build a few lines above this call,
the same one passed as `input_data=plan`) — use that directly, no new lookup
needed:

```python
job_id = rwb_job_service.enqueue_rwb_job(
    requestor_type="analyst_request", requestor_id=grouping_request_id,
    rwb_job_type="submit_grouping", input_data=plan, actor_id=actor_id,
    link_type="EDM", link_id=plan["edm_id"],
    context_type="grouping_request", context_id=grouping_request_id)
```

If `plan` does **not** already carry a single `edm_id` (e.g. a group's
members can legitimately span more than one EDM — check
`list_eligible_members`/the member-composition logic above this call before
assuming one EDM), stop and treat this as an open question rather than
guessing: either derive a representative EDM id if the product intent is
"one EDM per group" (confirm with whoever owns this feature), or use
`link_type="NOT_APPLICABLE"`, `link_id=None` if grouping can genuinely span
EDMs and no single EDM is the "correct" link. Do not silently pick one member
analysis's EDM if the members can differ — that would misrepresent what the
job concerns for a future "jobs for this EDM" search.

## Change 2 — `app/poller/run.py`, `_handle_grouping_terminal`

Current call (as of this branch, `app/poller/run.py:148-153`):

```python
rwb_job_service.enqueue_rwb_job(
    requestor_type="irp_job", requestor_id=job["id"],
    rwb_job_type="finalize_analysis",
    input_data={"analysis_id": str(job["irp_analysis_id"])},
    conn=conn,
)
```

This is the same shape as the four existing poller-chained sites already
fixed in Phase 1 (`app/poller/run.py:76`, `:94`, `:162`, `:192` — see the main
CR document, §4, sites #4-#7, and §9 T3 for exactly how those were done).
Follow the same pattern: `context_type="irp_job"`, `context_id=job["id"]`.
For `link_type`/`link_id`: check whether `job` (the dict read from
`irp_job`/`list_non_terminal`, same source as the other poller handlers)
carries `irp_edm_id` for a `grouping`-type job the way it does for
`import_edm`/`geohaz`/`analysis` jobs. If yes:

```python
rwb_job_service.enqueue_rwb_job(
    requestor_type="irp_job", requestor_id=job["id"],
    rwb_job_type="finalize_analysis",
    input_data={"analysis_id": str(job["irp_analysis_id"])},
    conn=conn,
    link_type="EDM", link_id=job["irp_edm_id"],
    context_type="irp_job", context_id=job["id"])
```

If `job["irp_edm_id"]` is null for grouping jobs specifically, fall back to
`link_type="NOT_APPLICABLE"`, `link_id=None` for this call and flag it —
don't invent a value.

## Change 3 — `grouping_request_is_live()` read query

Current query (`grouping_service.py`, around line 619-624) filters on
`requestor_type`/`requestor_id` directly:

```python
"SELECT 1 FROM rwb_job WHERE requestor_type = 'analyst_request' "
"AND requestor_id = :id AND rwb_job_type = 'submit_grouping' "
"AND status_code IN ('pending', 'running')"
```

**No change needed here for this follow-up.** `requestor_type`/`requestor_id`
are untouched by Phase 1 — this query keeps working exactly as-is. It will be
revisited in a later, separate phase (Phase 2 of the main CR) when read
queries generally move to `context_type`/`context_id` — not part of this
follow-up.

## Verification

1. `uv run pytest tests/unit` on this branch, rebased onto post-Phase-1
   `main` — confirms the two calls above satisfy the new required keyword
   arguments and nothing else on this branch calls `enqueue_rwb_job`/
   `ensure_pending_rwb_job` unmodified.
2. If `linux-box` is up (developer's call, not yours to start): submit a real
   grouping request end-to-end and confirm the resulting `rwb_job` row has
   `link_type='EDM'` with a real EDM id (or `NOT_APPLICABLE` if that's the
   deliberate outcome from Change 1/2 above), and `context_type=
   'grouping_request'` / `'irp_job'` matching the two call sites.
3. Confirm `tests/iteration1_mirror.py`'s SQLite mirror already has the new
   `rwb_job` columns and the two new kind tables from Phase 1 — if this
   branch's own tests fail with "no such column," Phase 1's mirror update
   (main CR, §9 T1) hasn't been picked up yet; rebase first rather than
   patching around it here.

## What this follow-up does not do

- Does not add `submit_grouping` to any backfill SQL — Phase 1's backfill
  only covers rows that exist before Phase 1 merges. Since `submit_grouping`
  doesn't exist on `main` yet, there are no pre-existing `submit_grouping`
  rows to backfill anywhere.
- Does not touch the dedup constraint. `submit_grouping` keeps relying on
  `UNIQUE(requestor_type, requestor_id, rwb_job_type)` exactly like every
  other job type until the main CR's Phase 2 (not yet scoped) rewires
  everything at once.
