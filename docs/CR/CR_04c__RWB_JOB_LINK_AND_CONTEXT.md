# Change Request — Add link/context fields to `rwb_job`, retire the overloaded `requestor_id`

**ID:** CR-04c
**Status:** Draft — Phase 1 scoped, Phase 2 not yet planned in detail
**Applies to:** `alembic/versions/`, `app/services/rwb_job_service.py`, every enqueue call site listed in §4, `docs/DATA_MODEL.md`

## 1. Summary

`rwb_job.requestor_id`/`requestor_type` do two unrelated jobs at once: they are the
dedup key that stops the same operation being queued twice, and they are the only
way (indirectly, and inconsistently) to find out which EDM or RDM a job concerns.
Neither job is done well, because one column cannot cleanly serve both.

This CR splits the two jobs apart:

- **`link_type` / `link_id`** — new, always-present columns naming the EDM or RDM
  a job concerns, for search ("find every job for EDM X" → later, "for submission
  Y" via the existing `submission_edm`/`submission_rdm` join tables). Required on
  every insert; `not_applicable` where no EDM/RDM applies (metadata sync, dummy
  test jobs) so a missing link is never silently indistinguishable from an
  unmapped one.
- **`context_type` / `context_id`** — new columns naming the specific thing the
  job acts on (a portfolio, a breakout group row, an IRP job, an IRP analysis,
  etc.) — this is what `requestor_id` actually holds today, given an honest name.
- The dedup constraint moves off `requestor_type` onto
  `(context_type, context_id, rwb_job_type)`, because the current constraint
  lets a chained backend job and an analyst-initiated job collide/miss each
  other purely because their `requestor_type` differs, even when both act on the
  same object (§5).
- `requestor_type`/`requestor_id` stay, meaning strictly "who/what triggered this"
  (an analyst, the poller on behalf of an IRP job, another `rwb_job` chaining
  into this one) — not folded into the new dedup key.

Phase 1 (this CR) adds the four columns, back-fills them on existing rows, and
fixes every insert point. Phase 2 (a later CR) rewires the constraint and the
read-side queries in `rwb_job_service.py` to actually use
`context_type`/`context_id` instead of `requestor_type`/`requestor_id` for
dedup and lookup — done one call site/scenario at a time, each its own commit,
each tested before the next. See §8.

## 2. Why

**`requestor_id` means "the thing this job acts on," not "who requested it."**
In all 18 current insert call sites (§4), `requestor_id` is never a user id. It
is an EDM id, an RDM id, a portfolio id, an `irp_job` row id, an `irp_analysis`
row id, a `breakout_group` row id, a job's own id (self-referential, for
chaining), a hardcoded sentinel UUID, or — for the CLI smoke-test tool — a
freshly generated UUID with nothing behind it. The actual acting user, when one
exists, already travels separately as `actor_id`/`inserted_by`, or is buried
inside `input_data`. Nothing today is inconsistent about *how* `requestor_id`
is used — every call site uses it correctly as a dedup key — but the column
name promises something the value never delivers, and anyone reading a
`rwb_job` row cannot tell what `requestor_id` points to without also reading
`requestor_type` and knowing the mapping by heart.

**There is no queryable link to EDM or RDM.** `list_rwb_jobs_for_monitoring()`
and every other read path can show `requestor_type`/`requestor_id`, but cannot
answer "show me every job for this EDM" without the four-way `COALESCE`/`JOIN`
that `backfill_edm_detail_rows()` already has to do (`rwb_job_service.py:319-339`)
— and that query only covers one `rwb_job_type`. A general "jobs I own" search
across EDM/RDM (and, later, submission) needs this to be a plain indexed
column, not a per-job-type join.

**The dedup constraint can be defeated across requestor types.** See §5 for the
concrete collision.

## 3. In-flight PRs that touch this area (checked 2026-08-31)

- **PR #53** ("Replace SQLite tests with SQL Server") — 100 commits against
  `main`, touches `rwb_job_service.py` and most call-site files, but has
  diverged far enough from current `main` that it will be abandoned rather
  than merged. Not accounted for anywhere in this CR.
- **PR #84** ("012 grouping execution", branch `012-grouping-execution`) —
  live and expected to merge. Adds a fifth `rwb_job_type`, `submit_grouping`,
  with two new insert call sites not in the table below:
  `app/services/grouping_service.py` (`enqueue_rwb_job`, `requestor_type=
  "analyst_request"`, `requestor_id=grouping_request_id`, a freshly minted id;
  `input_data["submission_id"]` is already present) and a new
  `_handle_grouping_terminal` in `app/poller/run.py` (same
  `requestor_type="irp_job"` chaining shape as sites #4–7, chains to
  `finalize_analysis`). It also adds a raw read query,
  `grouping_request_is_live()`, filtering on `requestor_type`/`requestor_id`
  directly.

  Decision: `submit_grouping` gets `link_type='edm'` — a group's members are
  drawn from a submission's analyses, which resolve to EDMs, so `link_id` is
  the member analyses' EDM id (unlike `execution_id`-style sites, this is not
  a "no EDM in scope" case once the grouping plan is composed).

  Sequencing: Phase 1 of this CR lands on `main` first, as a non-breaking
  change (additive columns, existing constraint untouched — see §8). Once
  that is in, a small follow-on PR targets PR #84's own branch
  (`012-grouping-execution`) to add `link_type`/`link_id`/`context_type`/
  `context_id` to `grouping_service.py`'s insert and to
  `_handle_grouping_terminal`, so `submit_grouping` never lands on `main`
  missing the new columns. That follow-on is out of scope for this document —
  tracked here only so Phase 1's call-site count doesn't silently go stale
  the moment #84 merges.

## 4. Current state — every insert call site (verified against source, 2026-08-31)

All inserts go through exactly two functions in `app/services/rwb_job_service.py`:
`enqueue_rwb_job` (insert-or-give-up; never touches an existing row) and
`ensure_pending_rwb_job` (insert-or-revive-a-terminal-row-or-give-up — see §7
for the full mechanics). No call site writes to `rwb_job` any other way.

| # | Call site | `rwb_job_type` | `requestor_type` | `requestor_id` is really | EDM/RDM in scope at call time | New `link_type` / `link_id` |
|---|---|---|---|---|---|---|
| 1 | `app/services/_common.py:304` (`_import_entity`) | `"upload_edm"` / `"upload_rdm"` | `"analyst_request"` | the new EDM/RDM row's own id | `entity_id` (the one being created) | `edm`/`entity_id` or `rdm`/`entity_id` |
| 2 | `app/services/_common.py:365` (`_retry_import`) | `"upload_edm"` / `"upload_rdm"` | `"analyst_request"` | the existing EDM/RDM row id | `eid` | `edm`/`eid` or `rdm`/`eid` |
| 3 | `app/services/_common.py:402` (`_replace_source_file`) | `"upload_edm"` / `"upload_rdm"` | `"analyst_request"` | the existing EDM/RDM row id | `eid` | `edm`/`eid` or `rdm`/`eid` |
| 4 | `app/poller/run.py:76` (EDM import terminal) | `"backfill_edm_detail"` | `"irp_job"` | the `irp_job` row's own id | `job["irp_edm_id"]` | `edm`/`job["irp_edm_id"]` |
| 5 | `app/poller/run.py:94` (RDM import terminal) | `"backfill_rdm_analyses"` | `"irp_job"` | the `irp_job` row's own id | `job["irp_rdm_id"]` | `rdm`/`job["irp_rdm_id"]` |
| 6 | `app/poller/run.py:162` (analysis terminal) | `"finalize_analysis"` | `"irp_job"` | the `irp_job` row's own id | `job["irp_edm_id"]` also present | `edm`/`job["irp_edm_id"]` |
| 7 | `app/poller/run.py:192` (geohaz terminal) | `"backfill_edm_detail"` | `"irp_job"` | the `irp_job` row's own id | `job["irp_edm_id"]` | `edm`/`job["irp_edm_id"]` |
| 8 | `app/workers/dummy_submit.py:28` (`_submit`) | `"dummy_wait"` / `"dummy_fail"` | `"analyst_request"` | a fresh, unbacked UUID | none | `not_applicable`/`NULL` |
| 9 | `app/services/analysis_execution_service.py:306` (`request_execution`) | `"execute_analysis_batch"` | `"analyst_request"` | a fresh `execution_id` (plan identifier, not a row id) | `edm_id` (function param, already in `plan["edm_id"]`) | `edm`/`edm_id` |
| 10 | `app/workers/entity_jobs.py:313` (inside `_backfill_rdm_analyses_body`) | `"retrieve_analysis_results"` | `"irp_analysis"` | the `irp_analysis` row id | `rdm_id` (function param) | `rdm`/`rdm_id` |
| 11 | `app/workers/analysis_jobs.py:245` (`_finalize_analysis_body`) | `"retrieve_analysis_results"` | `"irp_analysis"` | the `irp_analysis` row id | **none** — no `edm_id`/`rdm_id` local variable exists in this function today | `not_applicable`/`NULL` *(see §6 note — this is the one gap worth closing before Phase 1 ships)* |
| 12 | `app/services/breakout_service.py:979` (`request_breakout`, quick mode) | `f"run_breakout_{dimension}"` | `"analyst_request"` | the portfolio id | `edm_id` (function param) | `edm`/`edm_id` |
| 13 | `app/services/breakout_service.py:1301` (`request_group_breakout`, custom mode) | `"run_breakout_custom"` | `"breakout_group"` | the `breakout_group` row's own id | `edm_id` (function param) | `edm`/`edm_id` |
| 14 | `app/services/geohaz_service.py:170` (`launch`) | `"run_geohaz"` | `"analyst_request"` | the portfolio id | `eid` (= `edm_id`) | `edm`/`eid` |
| 15 | `app/services/edm_service.py:621` (`sync_detail`) | `"backfill_edm_detail"` | `"analyst_request"` | the EDM row id | `eid` (= the EDM id itself) | `edm`/`eid` |
| 16 | `app/services/rdm_service.py:228` (`sync_detail`) | `"backfill_rdm_analyses"` | `"analyst_request"` | the RDM row id | `rid` (= the RDM id itself) | `rdm`/`rid` |
| 17 | `app/routers/templates.py:414` (`sync_metadata`) | `"sync_irp_metadata"` | `"analyst_request"` | hardcoded sentinel `00000000-...-0009` | none | `not_applicable`/`NULL` |
| 18 | `app/workers/portfolio_jobs.py:223` (`_complete_breakout`) | `"backfill_edm_detail"` | `"rwb_job"` | the completing breakout job's own id | `edm_id` (function param) | `edm`/`edm_id` |

`resubmit_rwb_job` (`rwb_job_service.py:214-237`) is not a separate call site —
it reads an existing row's own `requestor_type`/`requestor_id`/`rwb_job_type`/
`input_data` and passes them straight through to `ensure_pending_rwb_job`, so
once that row carries `link_type`/`link_id` it must be extended to read and
carry those through too, unchanged, alongside the rest.

Sites #8 and #17 are the only two with no EDM/RDM in scope at all — a dummy
smoke-test job and a global metadata sync — and are the two `not_applicable`
cases named in the summary.

## 5. The uniqueness bug this CR is fixing

The current constraint is `UNIQUE(requestor_type, requestor_id, rwb_job_type)`.
Because `requestor_type` is part of the key, two rows that act on the *same*
object can coexist if they were requested by two different requestor types —
the constraint cannot see they're duplicates.

Concretely: an analyst clicks "Sync" on EDM `E1` (site #15,
`requestor_type="analyst_request"`, `requestor_id=E1`,
`rwb_job_type="backfill_edm_detail"`). Milliseconds later the poller finishes
an unrelated geohaz run on the same EDM and chains its own
`backfill_edm_detail` (site #7, `requestor_type="irp_job"`,
`requestor_id=<that irp_job's own id>`). These two rows have different
`requestor_type` and different `requestor_id` values even though both are
"run `backfill_edm_detail` for EDM `E1`" — the constraint does not stop the
second insert, and now two `backfill_edm_detail` jobs for the same EDM are
in flight at once.

Moving the dedup key to `(context_type, context_id, rwb_job_type)` — where
`context_id` is the object the *operation itself* is scoped to, independent of
who triggered it — closes this: both rows above would carry the same
`context_type`/`context_id` (whatever uniquely identifies "the EDM `E1`
backfill operation") and the second insert would correctly be seen as a
duplicate.

This does reintroduce the retry problem `ensure_pending_rwb_job` exists to
solve: if the analyst-requested job fails and the analyst wants to retry the
exact same backfill, that retry is legitimately a *different* requestor acting
on the *same* context — it must still succeed, not be silently absorbed as a
"duplicate" of the failed row. §8 covers this: the DB constraint only enforces
"no two `pending`/`running` rows for the same context + job type," and
`ensure_pending_rwb_job`'s existing terminal-row-revival logic (§7) is exactly
the mechanism that already handles "the old attempt is done, a new one is
requested" — it just needs to key off `context_type`/`context_id` instead of
`requestor_type`/`requestor_id` once Phase 2 rewires it.

## 6. What `context_type`/`context_id` will hold, per call site

**This section was rewritten after an initial draft got the design wrong.**
The first pass let `context_id` copy whatever value each call site happened
to pass as `requestor_id` — which silently reintroduced the exact conflation
this CR exists to remove: `requestor_id` names *who/what triggered* the job
(an analyst, the poller acting for a finished `irp_job`, a chaining `rwb_job`),
not *what the job acts on*. Framed correctly, per the request sentence from
the discussion — *"Perform `<rwb_job_type>` on `<context_type>` object
identified by `<context_id>`"* — the object is defined by what the job's own
**worker body** reads and writes when it runs, never by what enqueued it.
**`context_id` is never a copy of `requestor_id`** — every value below was
derived independently by reading the actual worker function for each
`rwb_job_type`, not the enqueue call site.

Every `context_id` below is also confirmed to be a real application table's
own primary key (`id` column) — never an external-platform mirror column such
as `irp_id`, `exposure_resource_id`, or similar plain-string RM/Moody's
pointers. Those columns exist on `irp_edm`, `irp_rdm`, `irp_analysis`,
`irp_portfolio`, and `irp_job` specifically to mirror the external system and
must never be used as an internal dedup or context key (constitution Article 3
carve-out — plain VARCHAR is for external-mirror columns only).

| Call site(s) | `rwb_job_type` | Worker body (what it actually reads/writes) | `context_type` | `context_id` |
|---|---|---|---|---|
| #1–3 (EDM/RDM upload, retry, replace) | `upload_edm` / `upload_rdm` | `_upload_edm_body`/`_upload_rdm_body` (`app/workers/entity_jobs.py:65`/`123`) — loads the `irp_edm`/`irp_rdm` row, submits the import, writes `irp_job` | `edm` / `rdm` | `irp_edm.id` / `irp_rdm.id` — already in `input_data["edm_id"/"rdm_id"]` |
| #4, #7, #15, #18 (all roads into `backfill_edm_detail`: poller EDM-import terminal, poller geohaz terminal, analyst manual sync, breakout-completion chaining) | `backfill_edm_detail` | `_backfill_edm_detail_body` (`app/workers/entity_jobs.py:337`) — upserts `irp_portfolio`/`irp_treaty` rows for the EDM, stamps `irp_edm.as_of` | `edm` | `irp_edm.id` — already in `input_data["edm_id"]` at every one of these four sites |
| #5, #16 (poller RDM-import terminal, analyst manual sync) | `backfill_rdm_analyses` | `_backfill_rdm_analyses_body` (`app/workers/entity_jobs.py:210`) — upserts `irp_analysis` rows for the RDM, updates `irp_rdm.as_of` | `rdm` | `irp_rdm.id` — already in `input_data["rdm_id"]` |
| #6 (poller analysis terminal) | `finalize_analysis` | `_finalize_analysis_body` (`app/workers/analysis_jobs.py:202`) — updates the one `irp_analysis` row's `irp_id`, `irp_app_analysis_id`, `settings_metadata`, `status_code` | `irp_analysis` | `irp_analysis.id` — already in `input_data["analysis_id"]` (**not** the triggering `irp_job`'s own id) |
| #8 (dummy jobs) | `dummy_wait` / `dummy_fail` | `_dummy_wait_body`/`_dummy_fail_body` (`app/workers/dummy_jobs.py:36`/`52`) — reads only `label`/`seconds`/`message`; touches no application row at all | `NULL` | `NULL` — no row exists to name; do not synthesize one from the throwaway `requestor_id` |
| #9 (analysis batch execution) | `execute_analysis_batch` | `_execute_analysis_batch_body` (`app/workers/analysis_jobs.py:160`) — fans out over every `(portfolio, template item)` pair in the plan, creating one `irp_analysis` row per pair, all sharing one `execution_id` value | `execution` | `execution_id` — the plan's own `execution_id` value (`plan["execution_id"]`). **Not a single table's primary key** — see the note below — but a real, schema-recognized grouping value, not a throwaway. |
| #10, #11 (both roads into `retrieve_analysis_results`: RDM-backfill chaining, analysis-finalize chaining) | `retrieve_analysis_results` | `_retrieve_analysis_results_body` (`app/workers/analysis_jobs.py:311`) — reads one `irp_analysis` row (joined to its `irp_portfolio`), writes `irp_analysis.loss_results` | `irp_analysis` | `irp_analysis.id` — already in `input_data["analysis_id"]` at both sites |
| #12 (breakout quick mode) | `run_breakout_{dimension}` | `_run_breakout_body` (`app/workers/portfolio_jobs.py:236`) — reads the source `irp_portfolio` row, creates generated child `irp_portfolio` rows | `portfolio` | `irp_portfolio.id` of the **source** portfolio — already in `input_data["portfolio_id"]` |
| #13 (custom breakout) | `run_breakout_custom` | `_run_breakout_group_body` (`app/workers/portfolio_jobs.py:305`) — reads the `breakout_group` row's plan/config, creates one generated `irp_portfolio` row stamped with `breakout_group_id` | `breakout_group` | `breakout_group.id` — already in `input_data["group"]["id"]`. This is the one site where the job's real unit of work is the group's own config row, distinct from the EDM/portfolio it eventually touches — not the same shape as #12. |
| #14 (geohaz) | `run_geohaz` | `_run_geohaz_body` (`app/workers/geohaz_jobs.py:16`) — submits the hazard lookup for one portfolio, writes an `irp_job` row | `portfolio` | `irp_portfolio.id` — already in `input_data["irp_portfolio_id"]` (confirmed this is the portfolio's own app `id`, not `irp_portfolio.irp_id`) |
| #17 (metadata sync) | `sync_irp_metadata` | `_sync_irp_metadata_body` (`app/workers/metadata_jobs.py:80`) — takes no arguments; replaces the contents of six global lookup tables (`irp_model_profile`, `irp_output_profile`, `irp_event_rate_scheme`, `irp_currency`, `irp_currency_scheme`, `irp_currency_scheme_vintage`) | `NULL` | `NULL` — a global resync scoped to no single row of any entity; forcing a value here (e.g. reusing the existing `_METADATA_SYNC_REQUESTOR_ID` sentinel) would misrepresent it as row-scoped |

**Decision — `context_type`/`context_id` are nullable, not `NOT NULL`.**
Sites #8 and #17 are real job types whose worker bodies act on no application
row at all — not an oversight to close, a fact about what those jobs are.
Forcing a non-null value onto them (a sentinel type, a fake row id) would make
those two rows' `context_id` lie about having a target. `link_type`/`link_id`
remain `NOT NULL` (with `link_type='not_applicable'` covering these same two
sites, §4) precisely because "no EDM/RDM applies" is still a meaningful,
always-answerable fact about every job type, even one with no context at all
— the two column pairs answer different questions and are allowed to differ
on nullability as a result.

**Decision — `link_type`/`link_id` and `context_type`/`context_id` are kept
fully independent, not one derived from the other**, even though they now
coincide at 15 of the 18 sites (both point at the EDM/RDM the job acts on,
once `context_id` is correctly derived from the worker body rather than
`requestor_id`). The two exceptions are #13 (`context_type='breakout_group'`,
a real distinct config row, vs. `link_type='edm'`) and #9
(`context_type='execution'` with the batch's `execution_id`, vs.
`link_type='edm'` with the batch's `edm_id` — same underlying EDM, different
identifying value). Keeping the pairs independently populated at every site
(rather than deriving one from the other) is simpler to reason about and
does not depend on a join being correct at insert time for the sites where
they happen to agree.

**Note on `execution_id` (#9) — confirmed to be a real, schema-recognized
value, not a synthetic identifier.** `irp_analysis.execution_id`
(`alembic/versions/0001_initial.py:480`, nullable `Uuid`, no FK because no
single table is its target) is stamped identically onto every `irp_analysis`
row a batch execution produces — one `execution_id` value groups many rows,
each with its own distinct `irp_analysis.id`. It already participates in a
real schema constraint (`uq_irp_analysis_execution_item`, a filtered unique
index on `(execution_id, irp_portfolio_id, execution_item_no)`,
`0001_initial.py:678-684`) and the column's own comment states it "equals the
`execute_analysis_batch` row's `requestor_id`." Using it as `context_id` for
`execute_analysis_batch` is consistent with the "real application-table
value" rule even though it identifies a *group* of rows rather than one row
— `execute_analysis_batch` is a fan-out job type by nature, so a group key is
the correct grain for its context, not a compromise.

## 7. How the two enqueue functions work today (for anyone changing them)

Both live in `app/services/rwb_job_service.py` and share one insert statement,
`_INSERT_IF_ABSENT` (lines 30-39):

```sql
INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, status_code, input_data, ...)
SELECT :id, :rt, :rid, :jt, 'pending', :input, ...
WHERE NOT EXISTS (
    SELECT 1 FROM rwb_job
    WHERE requestor_type = :rt AND requestor_id = :rid AND rwb_job_type = :jt
)
```

Read as: "insert a new pending row for this key, unless a row with this exact
key already exists in any status." If a matching row already exists — pending,
running, succeeded, failed, or cancelled — zero rows are inserted. This
`WHERE NOT EXISTS` check is not airtight against two inserts racing at the same
instant, so there's a fallback: the database's `UNIQUE` constraint rejects the
loser of that race, and the code (`_insert_head`, lines 42-61) catches that
rejection and treats it exactly the same as "a row already existed" — it does
not crash or surface an error to the caller.

**`enqueue_rwb_job`** (lines 64-88) — used by the poller and by
worker-to-worker chaining. It always attempts the insert above and returns
either the new row's id (inserted) or `None` (a row for this key already
existed, in *any* status). **It never inspects or changes an existing row.**
If a `failed` row already occupies a key, calling this function again with the
same key does nothing at all — the failed row is left exactly as it is. This
is deliberate: a mechanical re-poll or redelivery firing twice for the same
event must be a safe no-op, never a resurrection of a past failure.

**`ensure_pending_rwb_job`** (lines 91-141) — used on the request path
(analyst clicks retry/sync/resubmit). Unlike `enqueue_rwb_job`, it does look at
what's already there, in three cases:

1. **No row exists for this key** → it inserts a new one, using the exact same
   `_insert_head` call as `enqueue_rwb_job`. (This is the detail worth being
   explicit about: yes, `ensure_pending_rwb_job` inserts too, when nothing is
   there yet — it is not purely an "update an existing row" function.)
2. **A row exists with status `pending`, `running`, or `cancelled`** → do
   nothing, return `None`. Something is already in flight, or was
   deliberately stopped — leave it alone.
3. **A row exists with status `succeeded` or `failed`** (the two "terminal"
   outcomes) → `UPDATE` that *same row* back to `pending`: clear
   `output_data`/`error_detail`/`completed_at`/`submitted_at`/`claimed_by`,
   increment `attempt_count`, and **replace `input_data` entirely** with
   whatever the new call passed (old and new parameters are not merged — the
   new call's payload wins completely).

One line to remember the difference by: `enqueue_rwb_job` inserts-or-gives-up;
`ensure_pending_rwb_job` inserts-or-revives-the-one-existing-terminal-row-or-
gives-up. Neither ever produces two live rows for the same key — that is the
entire point of the `UNIQUE` constraint both rely on.

`resubmit_rwb_job` (lines 214-237) is a thin wrapper the job-monitoring UI uses
to resubmit a job by id alone: it looks up that row's own
`requestor_type`/`requestor_id`/`rwb_job_type`/`input_data` and calls
`ensure_pending_rwb_job` with them unchanged — same row, same key, no new
dedup decision of its own.

## 8. Rollout plan

### Phase 1 — this CR: add the columns, keep today's behavior working

Summary (full step-by-step task list, with exact SQL and per-site values, is
§9 — this is the short version):

1. Add `link_type`, `link_id`, `context_type`, `context_id` to `rwb_job`.
   `link_type` is `NOT NULL` from the start (`not_applicable` covers every job
   with no EDM/RDM — "never silently missed" per the user's instruction).
   `context_type`/`context_id` are nullable as a pair — two real job types
   (`dummy_wait`/`dummy_fail`, `sync_irp_metadata`) genuinely act on no
   application row, so `NULL` there states a fact rather than a gap (§6). No
   FK yet on `link_type`/`context_type` — that's step 5.
2. Do **not** touch the existing `UNIQUE(requestor_type, requestor_id, rwb_job_type)`
   constraint yet — it keeps enforcing dedup exactly as today while the new
   columns are proven out (see the open question below on when to drop it).
3. Update all 18 call sites (plus `resubmit_rwb_job`) to pass
   `link_type`/`link_id`/`context_type`/`context_id` explicitly, per §6's
   table — derived from what each job's **worker body** acts on, never copied
   from `requestor_id`.
4. Write backfill `UPDATE`s for existing rows in non-empty environments,
   reusing the same joins for `link_*` and `context_*` since they coincide at
   most sites (§6); the two sites where they diverge
   (`execute_analysis_batch`'s `execution_id`, `run_breakout_custom`'s
   `breakout_group.id`) get their context set separately.
5. Only once every insert site and every backfilled row is verified correct,
   add the `rwb_job_link_type_kind`/`rwb_job_context_type_kind` kind tables
   and their FK constraints — deliberately last, so a bad value surfaces as a
   wrong-looking query result during verification, not a migration failure
   blocking deploy.
6. Test with `app/workers/dummy_submit.py` throughout — the one call site
   with zero real entities, cheapest way to prove the nullable-context and
   `not_applicable` paths round-trip correctly.

**Open question to resolve before implementation, not yet decided:** the plan
as given says "drop the DB unique constraint on requestor ID and type" as part
of Phase 1, before Phase 2 rewires the read-side queries to use
`context_type`/`context_id`. Dropping the old constraint before the new one
(on `context_type`/`context_id`) exists would leave the table with **no**
dedup enforcement at the database level for however long Phase 1 and Phase 2
are separated in time — every one of the 18 call sites relies on that
constraint today (via the caught-violation path in `_insert_head`, §7) to make
concurrent double-submission safe. Recommend: keep the old constraint in place
through all of Phase 1, and drop it in the same commit/deploy that Phase 2
adds the new `(context_type, context_id, rwb_job_type)` constraint — never a
window with neither. This is a recommendation, not yet a decision; flag for
confirmation before Phase 1 work starts.

### Phase 2 — separate CR, one scenario at a time

Rewire `rwb_job_service.py`'s dedup insert (`_INSERT_IF_ABSENT`,
`enqueue_rwb_job`, `ensure_pending_rwb_job`) and every read-side query listed
below to key off `context_type`/`context_id` instead of
`requestor_type`/`requestor_id`, and move the `UNIQUE`/index definitions
accordingly. Each of the following is its own commit, tested before the next
one starts (per the user's instruction — no bundling):

- `rwb_job_service.py:319-339` (`backfill_edm_detail_rows`) — replace the
  three-way `COALESCE`/`LEFT JOIN` with a plain `link_id IN (...)` filter, now
  that the join work is precomputed at insert time instead of read time.
- `app/services/rdm_service.py` (`latest_import_error`, `latest_backfill_status`)
  and `app/services/edm_service.py` (equivalent EDM status lookups) — currently
  filter on `requestor_type`/`requestor_id`; move to `context_type`/`context_id`.
- `app/services/breakout_service.py` (`_live_breakout_dimension`, the breakout-
  episode error/result collection, the chained-backfill lookup) — three
  separate query shapes today, each keyed on `requestor_type`/`requestor_id`.
- `app/services/geohaz_service.py` (`head_state` CTE).
- `resubmit_rwb_job` — once it reads `context_type`/`context_id` off the
  existing row for its lookup, confirm the UI's resubmit path still finds the
  right row under the new constraint.
- `app/services/grouping_service.py` (`grouping_request_is_live`), once PR #84
  merges and its own follow-on (§3) has added `context_type`/`context_id` to
  `submit_grouping`'s insert.

Each of these is a live in-flight-job check today ("is a job for this entity
already running") — moving them one at a time, each verified against real
dummy/test jobs before the next, is what keeps this defensive rather than a
big-bang rewrite of the queue's dedup semantics.

## 9. Phase 1 implementation tasks (main branch only)

Ordered; each step is its own commit; verify with `uv run pytest tests/unit`
(+ `make test-sql` if `linux-box` is up) before the next step.

**Two artifacts:**

1. `alembic/versions/0001_initial.py` — target schema, for fresh/empty DBs
   only (`make db-rebuild`). Edited in place, same as prior deferred columns
   (`irp_analysis_id` etc.). **Does not run on an existing DB** — Alembic
   tracks applied state via one `alembic_version` row (`"0001"`); no new
   revision id here means `upgrade head` on an already-`0001` DB is a no-op.
2. `infra/scripts/patches/2026_08_rwb_job_link_context.sql` (new, plain SQL,
   not an Alembic revision) — brings an existing, non-empty DB to the same
   shape without dropping data. Contains, in order: column adds, kind-table
   create+seed, backfill (T4), FK adds (T5). Every statement guarded
   (`IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE ...)`, `IF OBJECT_ID(...)
   IS NULL`, `MERGE ... WHEN NOT MATCHED` — same idiom as
   `infra/scripts/seed_db.py`) so re-running it is a no-op.

T1–T3 = artifact 1 (write once). T4–T5 = artifact 2 (write once, run against
any non-empty environment, including yours, instead of drop-rebuild).

### T1 — Add the four columns and two kind tables to `0001_initial.py`

In the `rwb_job` table block (`alembic/versions/0001_initial.py:394-429`), add
after the `requestor_id` column (line 400) and before `rwb_job_type` (line 401):

```python
sa.Column("link_type", sa.NVARCHAR(50), nullable=False),
sa.Column("link_id", sa.Uuid, nullable=True),
sa.Column("context_type", sa.NVARCHAR(50), nullable=True),
sa.Column("context_id", sa.Uuid, nullable=True),
```

`link_type` is never null — every row states one of the seeded codes,
`not_applicable` included, since "no EDM/RDM applies" is always an answerable
fact about any job type (§4). `link_id` is nullable only for
`link_type='not_applicable'` rows. `context_type`/`context_id` are nullable
as a pair, and only for the two job types whose worker body genuinely acts on
no application row at all — `dummy_wait`/`dummy_fail` (#8) and
`sync_irp_metadata` (#17), per §6's decision. Every other job type gets a
real non-null `context_type`/`context_id`. Do not add
`sa.ForeignKeyConstraint` for `link_type`/`context_type` in this step — that
comes in T5, after every row's value is verified correct (an FK failing
mid-migration blocks the whole deploy; a bad value caught by a later
spot-check does not).

In the kind-table loop (`0001_initial.py:310-325`), add two names to the
tuple: `"rwb_job_link_type_kind"`, `"rwb_job_context_type_kind"`. They get the
same four-column shape (`code`, `label`, `sort_order`, `inserted_at`) as every
other kind table in that loop — no special-casing.

Seed both new kind tables right after the existing
`rwb_job_requestor_type_kind` seed (`0001_initial.py:982-989`):

```python
op.execute(sa.text(
    "INSERT INTO rwb_job_link_type_kind (code, label, sort_order) VALUES "
    "('EDM', 'EDM', 10), "
    "('RDM', 'RDM', 20), "
    "('not_applicable', 'Not applicable', 900)"
))
op.execute(sa.text(
    "INSERT INTO rwb_job_context_type_kind (code, label, sort_order) VALUES "
    "('edm', 'EDM', 10), "
    "('rdm', 'RDM', 20), "
    "('irp_analysis', 'IRP Analysis', 30), "
    "('portfolio', 'Portfolio', 40), "
    "('breakout_group', 'Breakout Group', 50), "
    "('execution', 'Execution', 60)"
))
```

Six codes, matching the §6 table exactly — one per distinct object a job's
worker body can act on (`edm`, `rdm`, `irp_analysis`, `portfolio`,
`breakout_group`, `execution`). There is deliberately no `irp_job`, `rwb_job`,
`singleton`, or `dummy` code here — those were artifacts of the first,
incorrect draft that derived `context_type` from what triggered a job rather
than what it acts on; §8 and §17's job types have no context at all and use
`NULL`, not a placeholder code (§6's nullability decision). Lowercase for
`context_type` (mirrors today's lowercase `requestor_type` convention),
uppercase for `link_type` (a new convention with no existing casing to match;
`not_applicable` is the sentinel name the user specified verbatim).

Because the dev DB strategy is drop-create-seed, there is no "existing rows"
problem in the developer's own environment — `make db-rebuild` gives a fresh
table with the new columns already `NOT NULL` and fully seeded. T4 (backfill
SQL) exists only for other environments (per the user's instruction:
"any environment, not just mine, which is currently empty") — write it, but
it will not run against anything locally.

Mirror the two new kind tables' `CREATE TABLE` into
`tests/iteration1_mirror.py` next to `rwb_job_requestor_type_kind` (line 96)
and add `link_type`/`link_id`/`context_type`/`context_id` columns to that
file's `rwb_job` mirror table, and add both kind tables to whatever seeding or
cleanup list already includes `rwb_job_requestor_type_kind`
(`tests/iteration1_mirror.py:330-331`) — the unit tier will not see the new
columns/tables otherwise.

Add the same two kind tables' `MERGE` blocks to `infra/scripts/seed_db.py`
next to the existing `rwb_job_requestor_type_kind` MERGE
(`infra/scripts/seed_db.py:156-169`), same idempotent `MERGE ... WHEN NOT
MATCHED THEN INSERT` shape.

**Verify**: `uv run pytest tests/unit/test_rwb_job_queue.py` — expect
failures, since every call in that file uses positional/keyword args that
don't yet include the four new required-on-insert parameters. That's expected
at this step; T2 fixes the function signatures, T3 fixes the call sites.

### T2 — Extend `enqueue_rwb_job` / `ensure_pending_rwb_job` / `_insert_head`

In `app/services/rwb_job_service.py`:

- `_INSERT_IF_ABSENT` (lines 30-39): add `link_type, link_id, context_type,
  context_id` to the column list and `:lt, :lid, :ct, :cid2` (or similarly
  named, avoiding collision with the existing `:cid` correlation-id
  parameter) to the `SELECT`.
- `_insert_head` (lines 42-61): no logic change — it stays a generic
  "run this insert, absorb a UNIQUE violation" helper; only the `params` dict
  callers build grows by four keys.
- `enqueue_rwb_job` (lines 64-88): add four required keyword-only parameters
  — `link_type: str`, `link_id: Any | None`, `context_type: str | None`,
  `context_id: Any | None`. `link_type` has no default — every call site
  states it explicitly, `not_applicable` included, per the user's instruction
  that link must never be silently missed. `context_type`/`context_id` are
  still required *keyword arguments* (no default value, so a call site must
  pass something), but the two job types with no context (#8, #17) pass
  `context_type=None, context_id=None` explicitly — the requirement is "you
  must state whether there's a context," not "there must always be one."
  Thread all four into the `params` dict alongside the existing `rt`/`rid`/`jt`.
- `ensure_pending_rwb_job` (lines 91-141): same four new required keyword-only
  parameters, same nullability rule. Thread into the initial `_insert_head`
  params dict (the "no row exists" branch, lines 120-124) exactly like
  `enqueue_rwb_job`. Also add `link_type`, `link_id`, `context_type`,
  `context_id` to the `UPDATE` statement's `SET` clause (lines 129-140) — a
  revived row's link/context should be re-stamped from the retrying call's
  values, the same way `input_data` is fully replaced rather than merged (§7
  already documents this behavior for `input_data`; apply the same
  replace-not-merge rule here for consistency, since these are Phase 1
  additions to the same UPDATE).
- `resubmit_rwb_job` (lines 214-237): its `SELECT` (lines 223-227) must also
  read `link_type, link_id, context_type, context_id` off the existing row,
  and pass them through to `ensure_pending_rwb_job` unchanged (lines 232-237)
  — this function's whole contract is "resubmit exactly what was there," so
  it must carry the new columns through without transformation.
- `get_rwb_job` (lines 179-191) and `list_rwb_jobs_for_monitoring`
  (lines 194-211): add the four new columns to their `SELECT` lists, so the
  monitoring page and any future search UI can read them. This is a pure
  additive read change — no behavior to test beyond "the columns come back."

**Verify**: `uv run pytest tests/unit/test_rwb_job_queue.py` still fails at
this step (call sites in `tests/` and in `app/` haven't been updated yet) —
expected. Do not move to T3 until T2's diff alone type-checks/imports cleanly.

### T3 — Update every call site (§4's 18 sites, in this order: dummy jobs first)

Update `app/workers/dummy_submit.py:28-33` (`_submit`) first, per the CR's own
instruction that dummy jobs are "the cheapest way to prove `link_type=
'not_applicable'` ... round-trip correctly." Add `link_type="not_applicable"`,
`link_id=None`, `context_type=None`, `context_id=None` — the dummy worker
bodies (`_dummy_wait_body`/`_dummy_fail_body`) touch no application row at
all (§6), so there is no context to state, not merely none available.

**Verify manually** before touching any other call site: run
`python -m app.workers.dummy_submit wait --label t1 --seconds 5` against a
real dev DB (only if the developer already has `linux-box`/`sqlserver` up —
do not start containers to do this) and confirm the inserted row has
`link_type='not_applicable'`, `link_id` NULL, `context_type` NULL,
`context_id` NULL. If `linux-box` isn't up, this step is deferred to whenever
it next is, and skipped in CI (the unit tier alone cannot exercise this CLI
end-to-end against a real dramatiq worker).

Then update the remaining 17 call sites plus the `resubmit_rwb_job`
pass-through, using the exact `link_type`/`link_id`/`context_type`/
`context_id` values from the §4 and §6 tables — no new value should be
invented at this step; every value is already decided. Note throughout:
**`context_id` is never the same expression as `requestor_id`** except where
the two happen to already be identical in the current code (site #13,
flagged below) — in every other site they are visibly different values:

- Sites #1–3 (EDM/RDM upload, retry, replace — `app/services/_common.py`):
  `link_type` is `"EDM"` or `"RDM"` depending on which `cfg`/branch is active
  (mirror however the existing code already distinguishes EDM vs. RDM for
  `cfg["id_col"]`); `link_id` = the same `entity_id`/`eid` already in scope;
  `context_type` = `"edm"`/`"rdm"`; `context_id` = the same `entity_id`/`eid`
  value (here, unlike most sites, `context_id` and `requestor_id` do end up
  equal — both name the EDM/RDM row itself — but derive `context_id` from
  `entity_id`/`eid` directly in the new code, not by reading back
  `requestor_id`).
- Sites #15, #16 (EDM/RDM manual sync — `edm_service.py:621`,
  `rdm_service.py:228`): same shape as #1-3 — `link_type`/`context_type` =
  `"EDM"`/`"edm"` or `"RDM"`/`"rdm"`; `link_id`/`context_id` = `eid`/`rid`.
- Sites #4, #7, #18 (all roads into `backfill_edm_detail` other than manual
  sync — poller EDM-import terminal `app/poller/run.py:76`, poller geohaz
  terminal `:192`, breakout-completion chaining
  `app/workers/portfolio_jobs.py:223`): `link_type="edm"`,
  `context_type="edm"`; both `link_id` and `context_id` = the EDM id already
  sitting in `input_data["edm_id"]` at each site — `job["irp_edm_id"]` for
  #4/#7, the local `edm_id` for #18. **Not** `job["id"]` (the triggering
  `irp_job`'s own id, sites #4/#7) and **not** `str(rwb_job_id)` (the
  triggering breakout job's own id, site #18) — those are what the earlier
  draft of this table wrongly used for `context_id`.
- Site #5 (poller RDM-import terminal, `app/poller/run.py:94`):
  `link_type="rdm"`, `context_type="rdm"`; both `link_id` and `context_id` =
  `job["irp_rdm_id"]`. Not `job["id"]`.
- Site #6 (poller analysis terminal, `app/poller/run.py:162`, enqueues
  `finalize_analysis`): `link_type="edm"`, `link_id=job["irp_edm_id"]`
  (already present on `job` for an `analysis`-type job);
  `context_type="irp_analysis"`, `context_id=job["irp_analysis_id"]` — the
  `irp_analysis` row this `finalize_analysis` job actually updates, already
  in `input_data["analysis_id"]`. Not `job["id"]` (the triggering `irp_job`'s
  own id).
- Site #9 (`app/services/analysis_execution_service.py:306`, enqueues
  `execute_analysis_batch`): `link_type="edm"`, `link_id=edm_id` (function
  parameter, already in `plan["edm_id"]`); `context_type="execution"`,
  `context_id=execution_id` (the plan's own `execution_id` — this is the one
  site where `context_id` legitimately equals what's passed as `requestor_id`
  today, because `execution_id` is already the correct grouping key per §6's
  note, not a coincidence to avoid here).
- Sites #10, #11 (both roads into `retrieve_analysis_results` —
  `app/workers/entity_jobs.py:313`, `app/workers/analysis_jobs.py:245`):
  `context_type="irp_analysis"`, `context_id=analysis_id`/`_uid(pending["id"])`
  (the `irp_analysis` row this job retrieves results for — already the exact
  value passed as `requestor_id` in the current code, since both sites'
  worker body target coincides with what they key dedup on today; no new
  lookup needed). For `link_type`/`link_id`: site #10 has `rdm_id` (function
  parameter) in scope → `link_type="rdm"`, `link_id=rdm_id`. Site #11 has
  neither `edm_id` nor `rdm_id` in local scope — resolve this before writing
  the call: check whether a cheap `SELECT edm_id, rdm_id FROM irp_analysis
  WHERE id = :id` is acceptable to add here, or whether `link_type=
  "not_applicable"`, `link_id=None` is the deliberate answer. Either way, add
  a one-line comment at the call site stating which case this is, so it reads
  as a decision, not an oversight.
- Sites #12, #14 (breakout quick-mode, geohaz —
  `app/services/breakout_service.py:979`, `geohaz_service.py:170`):
  `link_type="edm"`, `link_id=edm_id`/`eid` (already in scope);
  `context_type="portfolio"`, `context_id=portfolio_id`/`pid` — the **source**
  portfolio the job reads from, already what's passed as `requestor_id` today
  (both sites' current `requestor_id` already happens to be the portfolio id,
  so no new value needed here, only the honest field name).
- Site #13 (`app/services/breakout_service.py:1301`, enqueues
  `run_breakout_custom`): `link_type="edm"`, `link_id=edm_id` (already in
  scope); `context_type="breakout_group"`, `context_id=group_row_id` — derive
  this from `input_data["group"]["id"]` (== `group_row_id`) directly, not by
  reading back `requestor_id`, even though the two values are numerically
  identical at this one site (§6 flags this explicitly: coincidental, not a
  pattern to rely on elsewhere).
- Site #17 (`app/routers/templates.py:414`, enqueues `sync_irp_metadata`):
  `link_type="not_applicable"`, `link_id=None`, `context_type=None`,
  `context_id=None` — `_sync_irp_metadata_body` takes no arguments and acts
  on six global lookup tables, not one row (§6). The existing
  `_METADATA_SYNC_REQUESTOR_ID` sentinel stays exactly as it is for
  `requestor_id` — this site's fix is entirely about not inventing a fake
  context, not about touching the sentinel.

**Verify** after each file (not each call site — files with 2-3 sites like
`_common.py` can be one commit): `uv run pytest tests/unit -k <relevant test
module>`. After all 18 sites: `uv run pytest tests/unit` in full.

### T4 — Backfill section of the patch script

Runs after the script's guarded `ALTER TABLE ... ADD` for the 4 columns:

```sql
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('rwb_job') AND name = 'link_type')
    ALTER TABLE rwb_job ADD link_type NVARCHAR(50) NULL;
-- (same guard shape for link_id, context_type, context_id)
-- backfill runs next; NOT NULL on link_type is a separate guarded step, last
```

Each `UPDATE` block below guarded with `WHERE link_type IS NULL` (or
`context_type IS NULL AND <block's condition>`) so a re-run is a no-op.

`link_type`/`link_id` and `context_type`/`context_id` share the same joins at
15 of 18 sites (§6). Two exceptions get an extra `context_*` update after the
shared pass: `execute_analysis_batch` (`execution_id`) and
`run_breakout_custom` (`breakout_group.id`). One `UPDATE` per source shape,
each guarded so it only touches rows it can resolve — leave the rest unset
for the catch-all check. `link_type`/`link_id` and `context_type`/`context_id`
are set together in
each block, not derived from `requestor_type`/`requestor_id` directly the way
the first, incorrect draft of this backfill did):

```sql
-- irp_job-triggered EDM sites: backfill_edm_detail via import-terminal or
-- geohaz-terminal chaining (poller). Context/link both resolve to the EDM
-- irp_job carries, never to irp_job.id itself.
UPDATE rj SET link_type = 'edm', link_id = ij.irp_edm_id,
    context_type = 'edm', context_id = ij.irp_edm_id
FROM rwb_job rj JOIN irp_job ij ON rj.requestor_type = 'irp_job'
    AND rj.requestor_id = ij.id
WHERE rj.rwb_job_type = 'backfill_edm_detail' AND ij.irp_edm_id IS NOT NULL;

-- irp_job-triggered RDM site: backfill_rdm_analyses via import-terminal chaining
UPDATE rj SET link_type = 'rdm', link_id = ij.irp_rdm_id,
    context_type = 'rdm', context_id = ij.irp_rdm_id
FROM rwb_job rj JOIN irp_job ij ON rj.requestor_type = 'irp_job'
    AND rj.requestor_id = ij.id
WHERE rj.rwb_job_type = 'backfill_rdm_analyses' AND ij.irp_rdm_id IS NOT NULL;

-- irp_job-triggered finalize_analysis: context is the irp_analysis row named
-- in the job's own input_data, NOT irp_job.id. link is the EDM irp_job carries.
UPDATE rj SET link_type = 'edm', link_id = ij.irp_edm_id,
    context_type = 'irp_analysis',
    context_id = TRY_CAST(JSON_VALUE(rj.input_data, '$.analysis_id') AS UNIQUEIDENTIFIER)
FROM rwb_job rj JOIN irp_job ij ON rj.requestor_type = 'irp_job'
    AND rj.requestor_id = ij.id
WHERE rj.rwb_job_type = 'finalize_analysis';

-- irp_analysis-requested rows (retrieve_analysis_results, both chaining
-- paths): context IS the irp_analysis row (== requestor_id at this site,
-- confirmed correct per §6 — no derivation needed for context here). link
-- prefers edm_id, else rdm_id.
UPDATE rj SET
    link_type = CASE WHEN ia.edm_id IS NOT NULL THEN 'EDM' ELSE 'RDM' END,
    link_id = COALESCE(ia.edm_id, ia.rdm_id),
    context_type = 'irp_analysis', context_id = ia.id
FROM rwb_job rj JOIN irp_analysis ia ON rj.requestor_type = 'irp_analysis'
    AND rj.requestor_id = ia.id
WHERE rj.rwb_job_type = 'retrieve_analysis_results';

-- breakout_group-requested rows (run_breakout_custom): link resolves the EDM
-- via source_portfolio_id -> edm_id; context is the breakout_group row
-- itself (== requestor_id at this site, confirmed correct per §6).
UPDATE rj SET link_type = 'edm', link_id = p.edm_id,
    context_type = 'breakout_group', context_id = bg.id
FROM rwb_job rj
    JOIN breakout_group bg ON rj.requestor_type = 'breakout_group'
        AND rj.requestor_id = bg.id
    JOIN irp_portfolio p ON p.id = bg.source_portfolio_id
WHERE rj.rwb_job_type = 'run_breakout_custom';

-- portfolio-requested rows (run_geohaz, run_breakout_lob/state/country/peril):
-- link resolves the EDM via irp_portfolio.edm_id; context is the source
-- portfolio itself (== requestor_id at this site, confirmed correct per §6).
UPDATE rj SET link_type = 'edm', link_id = p.edm_id,
    context_type = 'portfolio', context_id = p.id
FROM rwb_job rj JOIN irp_portfolio p ON p.id = rj.requestor_id
WHERE rj.requestor_type = 'analyst_request'
    AND rj.rwb_job_type IN ('run_geohaz', 'run_breakout_lob', 'run_breakout_state',
                             'run_breakout_country', 'run_breakout_peril');

-- analyst_request rows keyed directly on the entity (upload_edm/upload_rdm/
-- backfill_edm_detail/backfill_rdm_analyses via manual sync): requestor_id
-- already IS the edm_id/rdm_id, so context and link both equal it directly.
UPDATE rj SET link_type = 'edm', link_id = rj.requestor_id,
    context_type = 'edm', context_id = rj.requestor_id
FROM rwb_job rj
WHERE rj.requestor_type = 'analyst_request'
    AND rj.rwb_job_type IN ('upload_edm', 'backfill_edm_detail')
    AND EXISTS (SELECT 1 FROM irp_edm e WHERE e.id = rj.requestor_id);

UPDATE rj SET link_type = 'rdm', link_id = rj.requestor_id,
    context_type = 'rdm', context_id = rj.requestor_id
FROM rwb_job rj
WHERE rj.requestor_type = 'analyst_request'
    AND rj.rwb_job_type IN ('upload_rdm', 'backfill_rdm_analyses')
    AND EXISTS (SELECT 1 FROM irp_rdm r WHERE r.id = rj.requestor_id);

-- rwb_job-requested rows (breakout-completion chaining): both link and
-- context resolve to the EDM named in the PARENT job's own input_data —
-- never to the parent job's own id (rj.requestor_id / parent.id).
UPDATE rj SET
    link_type = 'edm',
    link_id = TRY_CAST(JSON_VALUE(parent.input_data, '$.edm_id') AS UNIQUEIDENTIFIER),
    context_type = 'edm',
    context_id = TRY_CAST(JSON_VALUE(parent.input_data, '$.edm_id') AS UNIQUEIDENTIFIER)
FROM rwb_job rj JOIN rwb_job parent ON rj.requestor_type = 'rwb_job'
    AND rj.requestor_id = parent.id
WHERE rj.rwb_job_type = 'backfill_edm_detail'
    AND TRY_CAST(JSON_VALUE(parent.input_data, '$.edm_id') AS UNIQUEIDENTIFIER) IS NOT NULL;

-- execute_analysis_batch: link is the batch's edm_id; context is the batch's
-- own execution_id (a real grouping value across many irp_analysis rows,
-- confirmed correct per §6 — not the same value as link_id here).
UPDATE rj SET
    link_type = 'edm',
    link_id = TRY_CAST(JSON_VALUE(rj.input_data, '$.edm_id') AS UNIQUEIDENTIFIER),
    context_type = 'execution',
    context_id = TRY_CAST(JSON_VALUE(rj.input_data, '$.execution_id') AS UNIQUEIDENTIFIER)
FROM rwb_job rj
WHERE rj.rwb_job_type = 'execute_analysis_batch';

-- no-context rows: sync_irp_metadata acts on no single row (link is
-- NOT_APPLICABLE, context stays NULL); dummy_wait/dummy_fail the same.
UPDATE rwb_job SET link_type = 'not_applicable', link_id = NULL,
    context_type = NULL, context_id = NULL
WHERE rwb_job_type IN ('sync_irp_metadata', 'dummy_wait', 'dummy_fail');

-- catch-all: anything still unset after every block above (for a rwb_job_type
-- not covered here) is a real gap, not a row to silently paper over — leave
-- it unset and investigate.
```

This mirrors exactly the reference joins `backfill_edm_detail_rows()` already
performs at `rwb_job_service.py:319-339` (poller/breakout/group resolution
logic) — reuse that query's join shape rather than re-deriving it, since it
is the proven-correct version of these same joins. Run this against a
snapshot/staging copy first and spot-check row counts per `context_type`
before running against any real environment — per this repo's rule, an agent
does not run this against a live environment; hand it to the environment
owner with the verification query below.

**Verify**: after backfill, `SELECT context_type, link_type, COUNT(*) FROM
rwb_job GROUP BY context_type, link_type ORDER BY 1, 2` — every combination
should be one of §6's documented pairs (including the two `NULL`/`NULL` rows
for `sync_irp_metadata`/`dummy_wait`/`dummy_fail`); anything else is a bug in
the backfill SQL, not a new legitimate case. Separately confirm no row has
`context_id` equal to a value from `irp_job.id`, `rwb_job.id` (another row's
own id), or any `irp_id`-suffixed column — that would mean the old,
incorrect derivation leaked through somewhere.

### T5 — FK constraints, last

After T1–T4 verified. Both artifacts:

- `0001_initial.py`: add both FKs next to the existing `requestor_type` FK
  (line 420-421):
  `sa.ForeignKeyConstraint(["link_type"], ["rwb_job_link_type_kind.code"])`,
  `sa.ForeignKeyConstraint(["context_type"], ["rwb_job_context_type_kind.code"])`.
- Patch script: same two FKs via guarded `ALTER TABLE rwb_job ADD CONSTRAINT
  ... FOREIGN KEY ...` (`IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE
  name = '...')`), as the final section, after backfill — an FK added before
  every row has a valid value fails and blocks the rest of the script.

**Verify**: `make test-sql` (developer-run, not agent-run) — add a new test
to `tests/sqlserver/test_job_tables_migration.py`'s `TestJobTablesMigration`
class (alongside `test_rwb_job_foreign_keys_present`) asserting the two new
FKs exist, following that test's existing style.

### T6 — New/updated unit tests

Add to `tests/unit/test_rwb_job_queue.py`, following its existing bare-function
style (no test class, `iteration2_db` fixture):

- `test_enqueue_requires_link_type` — calling `enqueue_rwb_job` without
  `link_type` raises `TypeError` (keyword required, no default) — proves the
  "never silently missed" requirement at the Python layer, not just
  documentation. `context_type`/`context_id` are required *keyword arguments*
  but accept `None`, so there is no equivalent "missing raises" test for
  them — see the next test instead.
- `test_enqueue_stores_link_and_context_fields` — round-trip: enqueue with
  `link_type="edm"`, a `link_id`, `context_type="edm"`, a `context_id`; read
  back via `get_rwb_job`; assert all four match.
- `test_enqueue_allows_null_context_when_job_has_none` — round-trip: enqueue
  with `link_type="not_applicable"`, `link_id=None`, `context_type=None`,
  `context_id=None` (the `dummy_wait`/`sync_irp_metadata` shape); assert it
  succeeds and reads back with both context columns `None` — proves nullable
  context is a deliberate, tested path, not an accident of a permissive
  column definition.
- `test_ensure_pending_restamps_link_and_context_on_retry` — mirrors the
  existing `test_ensure_pending_restamps_on_retry` pattern (§7's "replace, not
  merge" behavior for `input_data`) but for the four new columns: revive a
  terminal row with different `link_id`/`context_id` values than the original
  insert, assert the revived row shows the new values, not the old ones.
- `test_resubmit_rwb_job_carries_link_and_context_through_unchanged` — mirrors
  the existing `test_resubmit_rwb_job_by_id_matches_ensure_pending_contract`.

Update every existing test in `test_rwb_job_queue.py` that calls
`enqueue_rwb_job`/`ensure_pending_rwb_job` positionally-by-keyword without the
four new required arguments — add `link_type="not_applicable"`, `link_id=None`,
`context_type=None`, `context_id=None` as filler values, since those tests
exercise dedup/claim/reconcile mechanics unrelated to link/context and
shouldn't need a real context to pass. Do **not** use a placeholder
non-`None` `context_type` (e.g. `"dummy"`) for this filler — that would
reintroduce a fake context on rows that, per these tests' own scenarios
(arbitrary dedup keys with no real backing entity), have none, exactly the
mistake §6 corrects.

**Verify**: `uv run pytest tests/unit` — full pass, this is the gate for T1-T6
being complete.

## 10. What Phase 1 explicitly leaves for later (do not attempt in this pass)

- Dropping `uq_rwb_job_requestor_type` or adding the new
  `(context_type, context_id, rwb_job_type)` constraint — §8's open question;
  stays open until Phase 2 is scoped in detail, not resolved by this task list.
- Any read-side query rewiring (§8 Phase 2's list) — `list_rwb_jobs_for_monitoring`
  and `get_rwb_job` gain the new columns in their `SELECT` (T2) but nothing
  reads or filters on them yet; no UI changes in this pass.
- The `012-grouping-execution` branch (PR #84) — handled by the separate
  follow-on document referenced in §3, not by this task list.

## 11. What this CR deliberately does not do

- Does not add a `submission_id` column to `rwb_job`. `submission_edm` and
  `submission_rdm` are many-to-many join tables (an EDM/RDM can belong to
  zero, one, or several submissions), so "jobs I own" search reaches
  submission by joining through `link_id` → `submission_edm`/`submission_rdm`
  → `submission`, not by storing a submission id directly on the job row.
- Does not remove the quick-mode breakout feature
  (`run_breakout_country`/`_lob`/`_peril`/`_state`, call sites #12) even though
  it was separately flagged as a candidate for removal — out of scope here;
  if it is removed later, its `context_type='portfolio'` row disappears with
  it, no cleanup needed in this CR.
- Does not fix the `sync_irp_metadata` hardcoded sentinel UUID
  (`app/routers/templates.py:30`, `_METADATA_SYNC_REQUESTOR_ID`) — it remains
  exactly as it is for `requestor_id`, which this CR does not touch. This
  job's fix is entirely on the new columns: `link_type='not_applicable'`,
  `context_type`/`context_id` both `NULL` (§6), never a renamed or
  reinterpreted sentinel.
