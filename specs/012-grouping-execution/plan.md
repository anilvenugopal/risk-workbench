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
  rows. It opens the compose dialog: one form, three screens in a 1080px
  shell. Screen 1 (Members) is the submission-scoped pick-list (finished own
  analyses, broker analyses, finished groups — ticked rows pre-checked) with
  a client-side name search and the prefilled editable group name
  (`CRE_<submission name>_Group`, T-09). Screen 2 (Inspection) is the
  inspect response. Screen 3 (Settings) is the summary, the reused
  currency/scheme/vintage `currency_block` macro with env-var defaults,
  Propagate detailed output ON (O-08 drops Risk Modeler's Create independent
  groups checkbox), and the simulation count for a PLT group.
- Compose is two requests. Screen 1's Next posts
  `POST /submissions/{sid}/analyses/group/inspect`, which runs the local gate
  (≥2 eligible members, each with a Platform analysis id) and calls
  `irp_gateway.inspect_grouping` — `client.grouping.inspect()`, reads only —
  on the request path (T-02). `app/services/grouping_view.py` turns the
  result into screen 2: a facts strip and one table row per peril / region /
  model-version partition (members by display name; the scheme cell is a
  `<select>` limited to the members' schemes with none preselected where they
  differ, the shared scheme where they agree, "Different simulation sets"
  where the partition is incompatible), the blocking problems above the
  table, and the hidden fingerprint and inspected ids when nothing blocks.
  The same response fills screen 3's summary and simulation count out of
  band. `POST /submissions/{sid}/analyses/group` re-runs the gate in
  `app/services/grouping_service.py` (members unchanged since inspection,
  fingerprint present, positive simulation count, well-formed selections —
  `ExecutionGateError` pattern, 422 into screen 3's error slot), then
  persists the approved
  plan verbatim as one `rwb_job` (`rwb_job_type = submit_grouping`,
  requestor `analyst_request`) and dispatches. The plan carries the Platform
  analysis ids, settings, selections, fingerprint, and a minted
  `group_analysis_id` so the worker's claim is idempotent.
- The `submit_grouping` actor (`app/workers/grouping_jobs.py`, own CR-04
  queue) claims the group `irp_analysis` row (`is_group=1`,
  `submission_id` set, `edm_id`/`rdm_id` NULL — T-04) plus its
  `irp_analysis_group_member` rows (T-05), pre-checks the group name
  tenant-wide with `irp_gateway.count_analyses_named` (retrying with the
  `_n` suffix — the package no longer pre-checks names), then makes one
  gateway call: `submit_grouping`, which builds the package settings and
  selections and calls `client.grouping.submit()` with the plan's
  fingerprint. The package re-inspects and raises
  `IRPGroupingValidationError` (structured problems) when facts changed or a
  block appears; the worker maps the problems to an analyst-readable
  `failure_reason`. Every failure records `SUBMISSION FAILED` + the reason;
  success records the `irp_job` with the exact request body (T-03, spec O-09).
- The poller's `grouping` getter (`get_grouping_job`, single-status via
  `client.grouping.get_job`) and terminal handler are unchanged: FINISHED →
  `finalize_analysis`, which resolves a group's platform id by name-only
  search and then runs unchanged (T-11). FAILED → `status_code='error'` +
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
| Worker | New `app/workers/grouping_jobs.py` (`submit_grouping` actor, own queue; tenant-wide name pre-check; structured failure reasons); `finalize_analysis` gains the group branch (name-only resolution); poller `_GETTERS`/`_TERMINAL_HANDLERS` gain `grouping` |
| UI | Group button + three-screen compose dialog (`group_compose_modal.html`: members, inspection, settings; reuses `currency_block`), its `group_inspection.html` screen built by `grouping_view.py`, and the `group_submit_errors.html` 422 fragment; group rows in the submission merged grid and results page; Engine column renders "Group" |
| Library | irp-integration pinned to `0.8.0rc1` (TestPyPI); `irp_gateway` grouping methods replaced by `inspect_grouping` / `submit_grouping` / `get_grouping_job` / `count_analyses_named` over `client.grouping` (+ `FakeIRP`) |

## High-risk technical decisions

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | Implement after 010 + 011 merge to main; contracts written against the merged shape (011 code + CR-04 per-queue actors) | Approved | [research.md](research.md) T-01 |
| T-02 | Inspection runs on the request path (reads only, the analyst waits on the result, fan-out bounded by member count); the submit runs in the `submit_grouping` worker | Approved | research T-02 |
| T-03 | Validation split: local compose gate, then the package's inspection (blocking problems rendered in the dialog) and its submit-time re-inspection (typed `IRPGroupingValidationError`, mapped to `failure_reason`) | Approved | research T-03, spec O-09 |
| T-04 | Group rows: `irp_analysis.submission_id`, origin CHECK relaxed, filtered unique group name per submission | Approved | research T-04, [data-model.md](data-model.md) |
| T-05 | Membership = `irp_analysis_group_member` child table; `group_parent_id` stays deferred (cannot model many-groups-per-analysis) | Approved | research T-05 |
| T-06 | Submission tag value is the bare submission name, via the existing `tag_names` submit path — no change to 011's behavior | Approved | research T-06, spec O-05 |
| T-07 | Groups ship untagged — the platform grouping settings schema has no tag field and no post-hoc tag endpoint exists; spec amended (O-07) | Approved | research T-07 (API verified 2026-08-27) |
| T-08 | Create independent groups is dropped entirely — no checkbox, no emulation; the per-member design is recorded in research.md if the alignment reverses | Approved | research T-08, spec O-08 (decided 2026-08-27) |
| T-09 | Group name default `CRE_<submission name>_Group`; `_n` collision suffix and 64-char truncation via the existing `name_attempt` | Approved | research T-09 |
| T-10 | Members are identified by Platform analysis id (`irp_analysis.irp_id`), carried in the plan from inspection to submit; names are display only | Approved | research T-10 |
| T-11 | Group completion reuses `finalize_analysis` → `retrieve_analysis_results`; stats/EP endpoints assumed to serve group ids; the worker's tenant-wide name pre-check keeps name-only resolution valid | Assumed | research T-11, quickstart step 4 |
| T-12 | Groups render on submission-level pages only; compose pick-list submission-scoped from either entry page | Approved | research T-12 |

---

## Technical Context

**New dependencies**: irp-integration `0.8.0rc1` from TestPyPI (`make
irp-testpypi`), which ships `client.grouping.inspect()` / `submit()` /
`get_job()` and removed the name-based `submit_analysis_grouping_job`,
`get_analysis_grouping_job`, and public `build_region_peril_simulation_set`.
Production (`make irp-pypi`, PyPI `0.2.0`) has no grouping API; switching
production waits for the package release on PyPI.
**Databases touched**: `rwb_workbench` only (new column, new table, one kind
row). No EXPOSURE/LOSS/DATABRIDGE work.

## Constitution Check

Reviewed against all 13 articles in `.specify/memory/constitution.md`
(v4.0.0).

One deviation, justified:

- **Article 2 (Sequencing Is Derived, Not Stored)** says member coupling at an
  external boundary is name-based. Grouping members are passed to the package
  by Platform analysis id instead: the package accepts ids only, analysis
  names duplicate tenant-wide (note 22 O22-16), and `finalize_analysis`
  already stores the Platform id on `irp_analysis.irp_id` for every finished
  analysis, so no new stored sequence is introduced. The group's own platform
  id is still resolved by name once, at finalize, after the worker's
  tenant-wide duplicate-name pre-check.

Material interactions — where an article actively shapes this design:

- **Article 5 (Judgment Waits for a Click)**: the article names "composing a
  grouping" as click-gated; nothing here auto-fires a grouping, and the
  event-rate scheme choice is the analyst's, never defaulted. The
  post-completion chain (`finalize_analysis` → `retrieve_analysis_results`)
  is the mechanical follow-up that does auto-fire.
- **Article 10 (Concurrency Is Per-Queue)**: `submit_grouping` is a new
  `rwb_job_type` with its own queue and single worker process via the CR-04
  `rwb_actor` framework.
- **Article 11 (Polling Behind an Interface)**: inspection runs on the request
  path — it performs Platform reads only and returns promptly, the analyst is
  waiting on its result, and the fan-out is bounded by the member count. The
  submit stays in the worker. The poller uses the single-status
  `client.grouping.get_job`; no `poll_*_to_completion` variant is called.
- **AGENTS.md architecture rule 8 (approved plans are immutable)**: the
  compose plan (Platform ids, name, currency, simulation count, event-rate
  selections, inspection fingerprint) is persisted in `rwb_job.input_data`
  and executed verbatim; the worker never recomputes the selections or the
  fingerprint, and the package's fingerprint check fails the job rather than
  silently accepting changed members.

## Project Structure

```text
alembic/versions/0001_initial.py       # submission_id + CHECK + uq index; irp_analysis_group_member; submit_grouping seed
infra/scripts/seed_db.py               # submit_grouping seed mirror
tests/iteration1_mirror.py             # schema + seed mirror for the unit tier
app/services/grouping_service.py       # eligibility, gate, naming, inspection view, plan compose, enqueue
app/services/grouping_view.py          # inspection screen rows, options, problem texts
app/services/irp_gateway.py            # inspect_grouping / submit_grouping / get_grouping_job / count_analyses_named / name-only analysis search
app/services/analysis_service.py       # group rows in submission read models; Engine "Group"
app/workers/grouping_jobs.py           # submit_grouping actor
app/workers/analysis_jobs.py           # finalize_analysis group branch
app/poller/run.py                      # grouping getter + terminal handler
app/routers/submissions.py             # GET/POST /submissions/{sid}/analyses/group, POST .../group/inspect
app/templates/partials/analyses_merged_section.html   # Group button
app/templates/partials/group_compose_modal.html       # dialog shell + three panes
app/templates/partials/group_inspection.html          # screen 2 + screen 3's oob summary and simulation count
app/templates/partials/group_submit_errors.html       # submit 422 errors
tests/unit/  tests/sqlserver/  tests/irp/
```

## Complexity Tracking

Article 2 deviation recorded in the Constitution Check above.

## Testing

- **Unit**: compose gate (unfinished member, foreign member, <2 members,
  nested-group eligibility, missing Platform id, members changed since
  inspection, missing fingerprint, non-positive simulation count, malformed
  or duplicate selections), inspection view (Platform ids, suggested
  simulation count, no writes), group naming and `_n` collision retry, plan
  composition, `submit_grouping` worker against `FakeIRP` (success with the
  exact request body recorded; duplicate-name pre-check retry; generic
  failure; `inspection_changed` and structured-problem failure reasons),
  poller `grouping` routing and terminal handling, group rows in submission
  read models and results columns, compose routes including the inspect
  fragment states.
- **SQL Server integration**: schema drift guard for `submission_id`, the
  relaxed CHECK, `uq_irp_analysis_live_submission_name`,
  `irp_analysis_group_member`, and the `submit_grouping` kind row.
- **IRP sandbox** (opt-in, `--run-irp`): inspect and submit a real grouping
  of finished sandbox analyses by Platform id; poll `get_grouping_job` to
  terminal; verify stats/EP retrieval against the group id (T-11); a
  conflicting-scheme pair submitted once per offered scheme; a stale
  fingerprint rejected before any POST. See [quickstart.md](quickstart.md).
