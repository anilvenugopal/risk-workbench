# Implementation Plan: Grouping

**Branch**: `012-grouping-execution` | **Date**: 2026-08-27 | **Spec**: [spec.md](spec.md)

<!-- Technical only. User stories and scope → spec.md. Schema → data-model.md.
     Payloads → contracts/. Endpoint investigation → research.md. Everything
     above the `---` is what a reviewer reads to decide: ten minutes to read. -->

## Plan status

**Ready for tasks:** Yes
**Blocked by:** Nothing in this spec. Implementation sequencing: branches
`010-analysis-execution` and `011-analysis-results` must merge to `main`
first — they hold the analysis submission, merged grid, results views, and
per-queue worker framework this feature extends (T-01).

## Design summary

- The merged analyses grid (submission page and submission-contextual EDM
  page) gains a **Group** button in its summary bar, enabled at ≥2 ticked
  rows. It opens the compose dialog: a submission-scoped member pick-list
  (finished own analyses, broker analyses, finished groups — ticked rows
  pre-checked), the prefilled editable group name
  (`CRE_<submission name>_Group`, T-09), the reused currency/scheme/vintage
  `currency_block` macro with env-var defaults, and Propagate detailed output
  ON — the dialog's only settings (O-08 drops Risk Modeler's Create
  independent groups checkbox).
- `POST /submissions/{sid}/analyses/group` runs the compose gate in a new
  `app/services/grouping_service.py` (members exist, finished, belong to the
  submission, ≥2 — `ExecutionGateError` pattern, 422 re-render), then persists
  the approved plan verbatim as one `rwb_job`
  (`rwb_job_type = submit_grouping`, requestor `analyst_request`) and
  dispatches. The plan carries a minted `group_analysis_id` so the worker's
  claim is idempotent.
- The new `submit_grouping` actor (`app/workers/grouping_jobs.py`, own CR-04
  queue) claims the group `irp_analysis` row (`is_group=1`,
  `submission_id` set, `edm_id`/`rdm_id` NULL — T-04) plus its
  `irp_analysis_group_member` rows (T-05), then makes one gateway call:
  `submit_analysis_grouping` with `skip_missing=False` (T-10), explicit
  currency, and the plan's propagate flag. The wheel resolves member names
  and auto-builds the region/peril simulation set internally — the Workbench
  never calls `build_region_peril_simulation_set` (T-03). The duplicate-name
  `IRPAPIError` (matched by its message prefix) retries with the `_n` suffix;
  every other exception records `SUBMISSION FAILED` + `failure_reason`, and
  success records the `irp_job` (`irp_job_type = grouping`, already seeded) —
  both exactly as the analysis worker does (T-03, spec O-09).
- The poller gains a `grouping` getter (`get_grouping_job`, single-status) and
  terminal handler: FINISHED → `backfill_analysis_detail`, which resolves a
  group's platform id by name-only search (groups have no EDM) and then runs
  unchanged — metadata, `irp_id`, `status_code='ready'`, chain
  `retrieve_analysis_results` (T-11). FAILED → `status_code='error'` +
  `failure_reason`.
- Group rows join the submission-page read models: listed in the merged grid
  (Engine column shows **"Group"** — the disclosure, per note 20 D8) and flow
  into `/results/analyses` by id like any analysis; the existing
  neighbour-swap ordering already covers them (FR-016, T-12). The EDM grid is
  unchanged.
- The submission tag stays the bare submission name on the existing
  `tag_names` path at analysis submit (T-06 / O-05) — no code change. The
  group itself cannot carry a tag — the platform grouping settings has no tag
  field (verified; T-07) — spec amended (O-07).

## Material changes

| Area | Change |
|---|---|
| Database | `irp_analysis.submission_id` (FK, nullable) + origin CHECK third leg + filtered unique `(submission_id, name)`; new `irp_analysis_group_member` table; `submit_grouping` seeded in `rwb_job_type_kind` (migration, `seed_db.py`, `iteration1_mirror.py`) |
| Worker | New `app/workers/grouping_jobs.py` (`submit_grouping` actor, own queue); `backfill_analysis_detail` gains the group branch (name-only resolution); poller `_GETTERS`/`_TERMINAL_HANDLERS` gain `grouping` |
| UI | Group button + compose dialog (`group_compose_modal.html`, reuses `currency_block`); group rows in the submission merged grid and results page; Engine column renders "Group" |
| Library | `irp_gateway` gains `submit_analysis_grouping` / `get_grouping_job` (+ `FakeIRP`); no irp-integration wheel change — 0.6.2 already ships the grouping API |

## High-risk technical decisions

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | Implement after 010 + 011 merge to main; contracts written against the merged shape (011 code + CR-04 per-queue actors) | Approved | [research.md](research.md) T-01 |
| T-02 | Grouping submit runs in the `submit_grouping` worker — the wheel's per-member read fan-out disqualifies the request path | Approved | research T-02 |
| T-03 | Validation split: local compose gate on the request path; everything platform-side delegated to the wheel's single submit call — member/name failures raise before the POST, scheme resolution never fails pre-submit in 0.6.2, and the worker records every submit exception uniformly as `SUBMISSION FAILED` with the cause | Approved | research T-03 (wheel source verified 2026-08-27), spec O-09 |
| T-04 | Group rows: `irp_analysis.submission_id`, origin CHECK relaxed, filtered unique group name per submission | Approved | research T-04, [data-model.md](data-model.md) |
| T-05 | Membership = `irp_analysis_group_member` child table; `group_parent_id` stays deferred (cannot model many-groups-per-analysis) | Approved | research T-05 |
| T-06 | Submission tag value is the bare submission name, via the existing `tag_names` submit path — no change to 011's behavior | Approved | research T-06, spec O-05 |
| T-07 | Groups ship untagged — the platform grouping settings schema has no tag field and no post-hoc tag endpoint exists; spec amended (O-07) | Approved | research T-07 (API verified 2026-08-27) |
| T-08 | Create independent groups is dropped entirely — no checkbox, no emulation; the per-member design is recorded in research.md if the alignment reverses | Approved | research T-08, spec O-08 (decided 2026-08-27) |
| T-09 | Group name default `CRE_<submission name>_Group`; `_n` collision suffix and 64-char truncation via the existing `name_attempt` | Approved | research T-09 |
| T-10 | `skip_missing=False` — a member that fails name resolution fails the job; own members resolve name+EDM, groups name-only, broker name-only | Approved | research T-10 |
| T-11 | Group completion reuses `backfill_analysis_detail` → `retrieve_analysis_results`; stats/EP endpoints assumed to serve group ids | Assumed | research T-11, quickstart step 4 |
| T-12 | Groups render on submission-level pages only; compose pick-list submission-scoped from either entry page | Approved | research T-12 |

---

## Technical Context

**New dependencies**: None. irp-integration 0.6.2 (the pin 010/011 carry)
already ships `submit_analysis_grouping_job` and `get_analysis_grouping_job`.
**Databases touched**: `rwb_workbench` only (new column, new table, one kind
row). No EXPOSURE/LOSS/DATABRIDGE work.

## Constitution Check

Reviewed against all 13 articles in `.specify/memory/constitution.md`
(v4.0.0): no violations.

Material interactions — where an article actively shapes this design:

- **Article 2 (Sequencing Is Derived, Not Stored)**: the compose gate is
  computed (member existence + `status_code='ready'`), and member coupling is
  name-based at submit time (`analysis_names` + `analysis_edm_map` +
  `group_names`). `irp_analysis_group_member` records lineage the same way
  `created_by_irp_job_irp_id` and breakout lineage do — membership facts RM
  never exposes back, not a stored process sequence.
- **Article 5 (Judgment Waits for a Click)**: the article names "composing a
  grouping" as click-gated; nothing here auto-fires a grouping. The
  post-completion chain (backfill → retrieve results) is the mechanical
  follow-up that does auto-fire.
- **Article 10 (Concurrency Is Per-Queue)**: `submit_grouping` is a new
  `rwb_job_type` with its own queue and single worker process via the CR-04
  `rwb_actor` framework.
- **Article 11 (Polling Behind an Interface)**: the grouping submit is
  deliberately **not** taken on the request path despite the submission
  permission — the wheel's pre-submit fan-out (per-member `search_analyses` +
  `get_analysis_by_id` + `get_regions`) makes it result-work-shaped, so it
  runs in the worker. The poller uses the single-status
  `get_analysis_grouping_job`; the `poll_*_to_completion` variants are never
  called.
- **AGENTS.md architecture rule 8 (approved plans are immutable)**: the
  compose plan (members, name, currency, flags) is persisted in
  `rwb_job.input_data` and executed verbatim; `skip_missing=False` prevents
  the wheel from silently narrowing the approved member set.

## Project Structure

```text
alembic/versions/0001_initial.py       # submission_id + CHECK + uq index; irp_analysis_group_member; submit_grouping seed
infra/scripts/seed_db.py               # submit_grouping seed mirror
tests/iteration1_mirror.py             # schema + seed mirror for the unit tier
app/services/grouping_service.py       # new: eligibility, gate, naming, plan compose, enqueue
app/services/irp_gateway.py            # submit_analysis_grouping / get_grouping_job / name-only analysis search
app/services/analysis_service.py       # group rows in submission read models; Engine "Group"
app/workers/grouping_jobs.py           # new: submit_grouping actor
app/workers/analysis_jobs.py           # backfill_analysis_detail group branch
app/poller/run.py                      # grouping getter + terminal handler
app/routers/submissions.py             # GET/POST /submissions/{sid}/analyses/group
app/templates/partials/analyses_merged_section.html   # Group button
app/templates/partials/group_compose_modal.html       # new dialog
tests/unit/  tests/sqlserver/  tests/irp/
```

## Complexity Tracking

No constitution violations to justify.

## Testing

- **Unit**: compose gate (unfinished member, foreign member, <2 members,
  nested-group eligibility), group naming and `_n` collision retry, plan
  composition, `submit_grouping` worker against `FakeIRP` (success;
  duplicate-name retry; uniform `SUBMISSION FAILED` recording with the cause
  in `failure_reason`), poller `grouping` routing and
  terminal handling, group rows in submission read models and results
  columns.
- **SQL Server integration**: schema drift guard for `submission_id`, the
  relaxed CHECK, `uq_irp_analysis_live_submission_name`,
  `irp_analysis_group_member`, and the `submit_grouping` kind row.
- **IRP sandbox** (opt-in, `--run-irp`): submit a real grouping of two
  finished sandbox analyses; poll `get_grouping_job` to terminal; verify
  stats/EP retrieval against the group id (T-11). See
  [quickstart.md](quickstart.md).
