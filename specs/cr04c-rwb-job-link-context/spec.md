# Feature Specification: rwb_job link and context fields

**Branch**: `cr04c-rwb-job-link-context` | **Created**: 2026-08-31

## Status

**Phase:** Phase 1 complete — unit and SQL Server tiers both green.
**Blocking:** Nothing. Scoped to Phase 1 only — see Out of scope.

## Outcome

`rwb_job.requestor_id` today does two jobs at once: it is the dedup key that
stops the same operation being queued twice, and it is the only (indirect,
inconsistent) way to tell which EDM or RDM a job concerns. This CR splits
those into four new columns — `link_type`/`link_id` (the EDM or RDM a job
concerns, always stated) and `context_type`/`context_id` (what the job's own
operation acts on, derived from what the job actually does, never copied from
`requestor_id`). After Phase 1, every `rwb_job` row states its EDM/RDM link
honestly and queryably, closing the gap that today forces a per-job-type join
(`backfill_edm_detail_rows()`) to answer "which jobs concern this EDM." A
later phase (not this one) builds "jobs I own" search on top of `link_id`,
and fixes the dedup constraint itself.

Full rationale, the corrected per-call-site design table, and the exact
column/backfill/call-site instructions live in
`docs/CR/CR_04c__RWB_JOB_LINK_AND_CONTEXT.md` — that document is the source
of truth this spec and its tasks are derived from.

## In scope (Phase 1)

- Four new columns on `rwb_job`: `link_type`, `link_id`, `context_type`,
  `context_id`.
- Two new kind tables: `rwb_job_link_type_kind`, `rwb_job_context_type_kind`.
- Every one of the 18 existing `enqueue_rwb_job`/`ensure_pending_rwb_job`
  call sites updated to pass all four fields with the correct value.
- A one-time idempotent SQL patch script that brings an existing,
  already-`0001`-migrated environment to the new shape without dropping data.
- Unit tests covering the new required/nullable field behavior.

## Out of scope

- Dropping `uq_rwb_job_requestor_type` or adding a new
  `(context_type, context_id, rwb_job_type)` constraint — a later phase.
- Rewiring any read-side query (`backfill_edm_detail_rows`, the RDM/EDM/
  breakout/geohaz in-flight checks) to use `context_type`/`context_id`
  instead of `requestor_type`/`requestor_id` — a later phase.
- Any UI for searching jobs by EDM/RDM/submission — a later phase.
- The `012-grouping-execution` branch (PR #84)'s own `submit_grouping` call
  site — handled by a separate follow-on document
  (`docs/CR/CR_04c__FOLLOWUP_submit_grouping.md`) once this lands on `main`.
- Fixing the `sync_irp_metadata` hardcoded sentinel UUID
  (`_METADATA_SYNC_REQUESTOR_ID`) — untouched by this CR.

## Non-negotiable behavior

1. `link_type` is never null — every job type states it, `not_applicable`
   included, so a missing link is never silently indistinguishable from an
   unmapped one.
2. `context_id` is never copied from `requestor_id`. It is derived from what
   the job's own worker body reads or writes, independent of who or what
   enqueued the job.
3. `context_type`/`context_id` are null only for job types whose worker body
   genuinely acts on no single application row (`dummy_wait`, `dummy_fail`,
   `sync_irp_metadata`) — never as a stand-in for "not yet resolved."
4. Every `link_id`/`context_id` value is an application table's own primary
   key (`id` column) — never an external-platform mirror column
   (`irp_id`, `exposure_resource_id`, or similar).
5. The idempotent patch script produces the same end state whether run once
   or run twice against the same database.

## Open product decisions

| ID | Decision | Status | Where |
|---|---|---|---|
| O-01 | `link_type`/`context_type` kind-table codes are lowercase (`edm`, `rdm`, `not_applicable`, ...), matching this repo's existing kind-code casing convention | Approved | `docs/CR/CR_04c__RWB_JOB_LINK_AND_CONTEXT.md` §9 T1 |
| O-02 | `execute_analysis_batch`'s `context_id` is `execution_id` — a real, schema-recognized grouping value shared across many `irp_analysis` rows, not a single row's own PK, accepted as the correct grain for a fan-out job type | Approved | `docs/CR/CR_04c__RWB_JOB_LINK_AND_CONTEXT.md` §6 |
| O-03 | `context_type`/`context_id` are nullable as a pair, unlike `link_type`/`link_id` which are always non-null | Approved | `docs/CR/CR_04c__RWB_JOB_LINK_AND_CONTEXT.md` §6 |
| O-04 | Whether call site #11 (`_finalize_analysis_body`) resolves a real EDM/RDM `link_id` via a new lookup, or is a deliberate third `not_applicable` site | Open | `docs/CR/CR_04c__RWB_JOB_LINK_AND_CONTEXT.md` §9 T3 |

## Requirements

- **FR-001**: `rwb_job` MUST have `link_type` (`NOT NULL`), `link_id`
  (nullable), `context_type` (nullable), `context_id` (nullable) columns.
- **FR-002**: `link_type` MUST be one of a fixed, seeded set of codes
  (`edm`, `rdm`, `not_applicable`), enforced by a foreign key once Phase 1's
  backfill is verified.
- **FR-003**: `context_type` MUST be one of a fixed, seeded set of codes
  (`edm`, `rdm`, `irp_analysis`, `portfolio`, `breakout_group`, `execution`)
  or `NULL`, enforced by a foreign key once Phase 1's backfill is verified.
- **FR-004**: Every call to `enqueue_rwb_job`/`ensure_pending_rwb_job` MUST
  pass `link_type`/`link_id`/`context_type`/`context_id` explicitly — no
  default silently fills in a value.
- **FR-005**: `ensure_pending_rwb_job`'s revival path (terminal row →
  `pending`) MUST re-stamp `link_type`/`link_id`/`context_type`/`context_id`
  from the retrying call's values, replacing rather than merging with the
  prior row's values (matching existing `input_data` behavior).
- **FR-006**: `resubmit_rwb_job` MUST carry the existing row's
  `link_type`/`link_id`/`context_type`/`context_id` through to
  `ensure_pending_rwb_job` unchanged.
- **FR-007**: An idempotent SQL script MUST exist that adds the four columns,
  the two kind tables, backfills every existing `rwb_job` row, and adds the
  two foreign keys — safe to run against a database at any point in this
  rollout, and safe to re-run after it has already succeeded.
- **FR-008**: `0001_initial.py` MUST reflect the complete final schema
  (columns, kind tables, seeds, FKs) so a fresh/empty database gets the same
  end state as a patched existing one.

## Success Criteria

- **SC-001**: Every `rwb_job` row inserted after Phase 1 ships has a non-null
  `link_type`.
- **SC-002**: A query filtering `rwb_job` by `link_type = 'edm' AND link_id =
  :id` returns every job type known to concern that EDM, with no per-job-type
  join required.
- **SC-003**: Running the patch script twice in a row against the same
  database produces identical `rwb_job` column values both times.
- **SC-004**: `uv run pytest tests/unit` passes with the new columns present
  and every existing call site updated.
