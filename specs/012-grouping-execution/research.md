# Research: Grouping Execution (spec 012)

Evidence behind the decisions in [plan.md](plan.md). IDs match the plan's
High-risk technical decisions table. Codebase citations reference the
`011-analysis-results` worktree unless marked otherwise, because that branch
holds the analysis-execution and results machinery this feature extends
(see T-01).

## T-01 — Implement on top of 010 + 011, after they merge to main

**Decision**: Spec 012 implementation starts only after branches
`010-analysis-execution` and `011-analysis-results` merge to `main`. The plan
and contracts are written against the merged shape: 011's analysis submission,
merged grid, and results views, plus 010/CR-04's per-queue `rwb_actor`
framework (`app/workers/queues.py`).

**Rationale**: The `012-grouping-execution` branch is based on `main`
(f562383), which contains none of the machinery grouping extends — no
`submit_portfolio_analysis` call, no merged analyses grid, no
`/results/analyses` page, no `execute_analysis_batch` worker. That code lives
on 010 and 011, which are not ancestors of each other (011 branched off 010 at
9fce587, before 010 merged CR-04). Known divergences the merge must resolve
before 012 lands:

- 011's actors are plain `@dramatiq.actor`; 010 moved them onto `rwb_actor`
  per-queue actors. Grouping's new actor uses the CR-04 framework.
- 011's `alembic/versions/0001_initial.py` seed list is missing
  `execute_analysis_batch` and `finalize_analysis` (present only in
  `infra/scripts/seed_db.py` and `tests/iteration1_mirror.py`); 010 seeds them
  in the migration.

**Alternatives considered**: Rebasing 012 onto 011 directly — rejected: it
would inherit 011's missing CR-04 framework and force this feature to redo the
merge later.

## T-02 — Inspection runs on the request path; the submit runs in a Dramatiq worker

**Decision**: `POST /submissions/{sid}/analyses/group/inspect` runs the local
gate and calls `client.grouping.inspect()` through `irp_gateway.inspect_grouping`
on the request path, rendering the result into the dialog. `POST
/submissions/{sid}/analyses/group` validates, persists the approved plan as a
`rwb_job` (`rwb_job_type = submit_grouping`), and returns. The
`submit_grouping` actor in `app/workers/grouping_jobs.py` performs the IRP
submission. Per Article 10 / CR-04 the actor gets its own queue named
`submit_grouping`.

**Rationale**: Article 11 permits Platform calls on the request path when they
return promptly and the analyst is waiting on the result. Inspection is such a
call: `GroupingManager._inspect` (package `0.8.0rc1`, `grouping.py:424-850`)
performs reads only — `get_analysis_by_id` and `get_regions` per member, plus
model-version, event-rate-scheme, and PET metadata reference lookups — and
creates nothing. Its fan-out is bounded by the member count the analyst just
picked, and the analyst needs the result (scheme choices, output type,
suggested simulation count) before they can submit. The HTMX indicator covers
the wait. The submit stays in the worker: it re-runs the same inspection and
then POSTs, and a created grouping job needs the poller anyway.

**Alternatives considered**: Background inspection with a persisted result —
rejected for now: it adds a job type and a polling loop to the dialog for a
call measured in seconds; revisit if a measured run exceeds the proxy timeout
(handover §8.3). Request-path submit — rejected: the analysis-execution
precedent is worker-side via `execute_analysis_batch`, and the poller tracks
the job either way.

## T-03 — Groupability validation: local gate, then the package's inspection and its submit-time re-inspection

**Decision**: The compose gate (request path) checks what the Workbench knows:
two or more members selected, every member exists, is not deleted, belongs to
the submission, is finished (`status_code = 'ready'`), and carries a Platform
analysis id. Inspection then runs the package's rule engine and the dialog
renders its `blocking_problems` (message, members, partition, PET ids) — a
blocked member set never reaches the submit. At submit, the gate additionally
requires that the picked members equal the inspected ids, that a fingerprint
is present, that the simulation count is a positive integer, and that each
event-rate selection is well formed and names a distinct partition. Which
partitions require a selection is not re-derived locally — the package checks
it at submit. The worker calls `client.grouping.submit()`, which re-inspects,
compares fingerprints, validates the selections, and raises
`IRPGroupingValidationError` with a `problems` tuple on any block; the worker
maps the problems to `failure_reason` (an `inspection_changed` code becomes
"inspect again"; other problems keep their message plus partition and PET
ids) and records `SUBMISSION FAILED`. Every other exception is recorded with
its text, as before.

**Evidence** (package `0.8.0rc1` source, read 2026-09-02): `submit()`
(`grouping.py:280-331`) raises `IRPGroupingValidationError((GroupingProblem(
code="inspection_changed", …),))` when the fresh fingerprint differs, then
`IRPGroupingValidationError(inspection.blocking_problems)` when any block is
present, then validates the selections against the partitions
(`_resolve_event_rate_selections`, codes `event_rate_selection_missing` /
`_duplicate` / `_unknown_partition` / `_not_required` / `_not_offered`) before
the POST. `GroupingProblemCode` lists 24 stable codes. `warnings` is always
empty in this release. The exception subclasses `IRPValidationError` and
therefore `IRPIntegrationError`, so the gateway's existing catch-all still
records it.

**Alternatives considered**: Re-deriving the required partitions at the
submit gate from a second inspection — rejected: doubles the Platform reads
and the package performs the same check with the same fingerprint anyway.
Local plausibility checks from stored `settings_metadata` — rejected: the
package's rules consult RM region rows and reference tables the Workbench
does not cache.

## T-04 — Group rows get `irp_analysis.submission_id`; the origin CHECK gains a third leg

**Decision**: Add nullable `submission_id` (FK `submission`) to
`irp_analysis`. Relax `ck_irp_analysis_origin` to
`edm_id IS NOT NULL OR rdm_id IS NOT NULL OR submission_id IS NOT NULL`.
A group row has `is_group = 1`, `submission_id` set, `edm_id` and `rdm_id`
NULL. Group name uniqueness mirrors the own-analysis guard: filtered unique
index `uq_irp_analysis_live_submission_name (submission_id, name)
WHERE submission_id IS NOT NULL AND deleted_at IS NULL`.

**Rationale**: Members span EDMs and RDMs within the submission (FR-002), so a
group belongs to no single EDM or RDM — it belongs to the submission, which is
also why groups appear only on the submission-level pages (design note 19
D14). The current CHECK (`edm_id IS NOT NULL OR rdm_id IS NOT NULL`,
`alembic/versions/0001_initial.py:511`) forbids such a row today.

**Alternatives considered**: Hanging the group off one member's EDM —
misrepresents cross-EDM groups and breaks the EDM grid's ownership semantics.
A separate `irp_group` table — rejected by PRD §16.4 and CR-002: "a group is
an `irp_analysis` with `is_group=true`, not a separate entity".

## T-05 — Membership is a child table; `group_parent_id` stays deferred

**Decision**: New table `irp_analysis_group_member (group_analysis_id,
member_analysis_id)` — both FK `irp_analysis.id`, composite PK — written by
the worker from the approved plan when it claims the group row.

**Rationale**: One analysis can be a member of several groups (nothing in Risk
Modeler or the spec forbids regrouping the same analysis), and groups nest
(FR-018), so the single-parent `group_parent_id` column DATA_MODEL §6 sketches
cannot represent membership. 011 already deferred that column with the note
"RM does not expose group membership, nothing populates it"
(`alembic/versions/0001_initial.py:448-449`) — the Workbench is the writer
that knows membership, at submit time. Membership feeds the compose dialog's
member display and the nested-grouping pick-list; it is lineage, not a stored
process sequence (Article 2 records entity lineage the same way via
`created_by_irp_job_irp_id` and breakout `source_portfolio_id`).

**Alternatives considered**: `group_parent_id` self-FK — cannot model
many-groups-per-analysis. No local membership (re-read from RM) — RM does not
expose group membership (011 migration comment), so display would be
impossible.

## T-06 — The submission tag is the bare submission name, applied through the existing `tag_names` path

**Decision** (clarification 2026-08-27, supersedes the structured form below):
The tag value is the bare submission name — 011's current behavior, kept
unchanged. `analysis_execution_service._compose_plan` already appends the
submission name to each plan item's `tag_names`, which flows to
`submit_portfolio_analysis_job(tag_names=…)` → `settings.tagIds` (the wheel's
`get_tag_ids_from_tag_names` get-or-creates the tag). This feature changes
nothing in that mechanism.

**Rationale**: O-05 (Approved 2026-08-27) picks the bare name for now: the
mechanism already ships in 011 and CIC queries by submission name today.

**Alternatives considered**: Structured `submission:<name>` prefix — keeps
submission tags distinguishable from free-form template tags sharing the same
platform tag namespace (design note 18 D12 records Ben demoing that form to
CIC); deferred, may be revisited. Submission UUID — stable but meaningless to
an analyst querying the platform by hand, which is the whole point (note 17
§4).

## T-07 — Groups cannot carry the submission tag; ship without it and amend the spec

**Decision**: The group itself is submitted without a tag. FR-017's "including
groups" and User Story 4 acceptance 2 are not implementable against the
current platform API; the spec needs a scope amendment recording the gap.
Member analyses still carry the tag, so a submission's grouping candidates
remain findable in the platform (SC-003 holds for analyses).

**Rationale — verified 2026-08-27**: Moody's API reference for
`POST /platform/grouping/v1/jobs`
(developer.rms.com/platform/reference/creategroupingjob) enumerates the full
`AnalysisGroupSettings` schema: `analysisName`, `currency`,
`numOfSimulations`, `propagateDetailedLosses`, `simulateToPLT` (required);
`forceGroupType`, `reportingWindowStart`, `simulationWindowStart`,
`simulationWindowEnd`, `groupingSetId`, `regionPerilSimulationSet`,
`description` (optional). **No tag field.** irp-integration exposes no method
to assign a tag to an existing analysis either — tags apply only at analysis
submit via `settings.tagIds` (`reference_data.py:449-534`; the platform tag
endpoints are GET/POST `/platform/referencedata/v1/tags` only).

**Alternatives considered**: Extending irp-integration to tag the finished
group post-hoc — no platform endpoint exists to attach a tag to an analysis
after creation, so there is nothing to wrap. Revisit if Moody's adds one.

## T-08 — "Create independent groups" is dropped entirely

**Decision** (2026-08-27, superseding the emulation below): the compose dialog
carries no Create independent groups setting and the worker submits only the
combined group. The compose settings are the currency block and Propagate
detailed output (spec O-08, FR-006).

**Rationale**: CIC never enables it — "we're never going to want to turn those
on" (PRD §16.4) — and its purpose in Risk Modeler (each input also becomes a
one-analysis group so it can sit beside the combined group in results) is
already served in the Workbench: the results views take individual analysis
ids, so a group and its member analyses render side by side with no extra
groups. Carrying the checkbox would have cost the largest speculative branch
in the feature: a plan field, a per-member worker fan-out with its own
isolation semantics, a FakeIRP mode, unit tests, and a mandatory sandbox
verification gating whether the checkbox even rendered.

**Rejected alternative — the per-member emulation** (the design to revive if
the alignment reverses): the platform grouping `settings` schema (see T-07,
verified 2026-08-27) has **no independent-groups field**; "Create independent
group" exists only on the legacy `/riskmodeler` Analysis Groups endpoint
(banned in this project). ON would have been emulated by submitting, after the
combined group, one additional grouping job per member with that member as the
sole `resourceUris` entry — each with its own `irp_analysis` group row,
membership row, and `irp_job`, per-member isolation, the `rwb_job` failing
only if the combined submit failed. The single-member submit was never
sandbox-verified. Also rejected earlier: a checkbox that only errors, and a
disabled checkbox with a "not supported" note — a control that can never be
used is dropped, not displayed.

## T-09 — Group name defaults to `CRE_<submission name>_Group`, reusing `name_attempt`

**Decision**: `build_group_name(submission_name)` returns
`CRE_{submission_name}_Group`. The compose dialog prefills it; the analyst may
edit it (O-01). Collision handling reuses
`analysis_execution_service.name_attempt` (`_n` suffix, right-truncation to
`NAME_MAX_LEN = 64` with the suffix re-clipping the base), checked against
live group names in the submission and retried when the wheel raises its
duplicate-name `IRPAPIError` (the wheel pre-checks `analysisName` against
`search_analyses` at `analysis.py:769-771`). Both names stored:
`irp_analysis.name` (≤64, submitted) and `full_name` (untruncated).

**Rationale**: O-01 approves the `CRE_` conventions (underscore delimiter,
`_n` suffix) for groups; the 64-character platform limit is confirmed (memory,
2026-08-20, and the grouping settings schema pattern caps `analysisName` at 64).
The analysis naming machinery already implements all of it.

## T-10 — Members are identified by Platform analysis id

**Decision**: The plan carries each member's Platform `analysisId`
(`irp_analysis.irp_id`, cast to `int`) and the gateway passes the ids to
`client.grouping.inspect(analysis_ids=…)` and `submit(analysis_ids=…)`. A
member without an `irp_id` fails the compose gate ("<name> has no Risk
Modeler analysis id yet."). Member names stay in the plan for display only.

**Rationale**: The `0.8.0rc1` package accepts ids only — name resolution,
`analysis_edm_map`, `group_names`, and `skip_missing` were removed with
`submit_analysis_grouping_job`. Names are the wrong key anyway: analysis names
duplicate tenant-wide (design note 22 O22-16), so a name-based lookup could
pick another tenant user's analysis, and the removed `skip_missing=True`
default could silently narrow the approved member set (AGENTS.md rule 8). The
id is already stored: `finalize_analysis` writes `irp_id` for every finished
own analysis and group (`analysis_jobs.py`), and the RDM backfill writes it
for broker analyses (`entity_jobs.py`), so every `ready` member has one.

**Rejected**: name-based lookup — removed from the package and unsafe with
duplicate names in the tenant.

## T-11 — Group completion reuses the analysis chain, with name-only resolution

**Decision**: The poller gains `_GETTERS["grouping"] =
irp_gateway.get_grouping_job` (wrapping the single-status
`client.grouping.get_job(job_id=…)`, which returns the raw Platform job dict
with the same `status` vocabulary as analysis jobs) and
`_handle_grouping_terminal`: `FINISHED` → enqueue `finalize_analysis` for the
group's `irp_analysis` row; `FAILED`/`CANCELLED` → `status_code='error'` +
`failure_reason`. `finalize_analysis` branches for group rows (no EDM): it
resolves the platform `analysisId` by name-only `search_analyses` filter
instead of `get_analysis_by_name(name, edm_name)`, then proceeds unchanged —
`get_analysis_metadata`, stamp `irp_id`/`settings_metadata`/`status_code='ready'`,
chain `retrieve_analysis_results`. Status **Assumed**: that
`get_analysis_stats`/`get_analysis_ep` serve group `analysisId`s the same as
individual analyses is expected (PRD §16.4: results retrieved the same way;
a group's `additionalProperties` carries `eventRateSchemes`, so RM treats
groups as analyses) but is verified in the sandbox (quickstart step).

Name-only resolution needs the group name to be unique tenant-wide at submit.
The `0.8.0rc1` package no longer pre-checks group names (the `"Analysis Group
with this name already exists"` error is gone), so the worker performs the
check itself: `irp_gateway.count_analyses_named(name)` wraps
`search_analyses_paginated(filter='analysisName="<name>"')`, and a non-zero
count moves the group row to the next `_n` name before the submit (bounded by
`MAX_NAME_ATTEMPTS`). A duplicate created between the check and the POST still
fails loudly at finalize, as before.

**Rationale**: FR-013 requires a group's results viewed and retrieved exactly
as an analysis's; reusing `retrieve_analysis_results` and the stored
`loss_results` extract is the smallest change that satisfies it. The poller's
`poll_*_to_completion` variants are forbidden (Article 11); only the
single-status getter is wrapped. Grouping submissions get no automatic retry:
`_submission_retry` stays analysis-only — FR-011 requires visible failure, not
auto-retry, and the analyst recomposes from the dialog.

## T-12 — Group rows render on submission-level pages only; the compose pick-list is submission-scoped from either entry

**Decision**: `analysis_service` gains group rows in the submission read model
(`submission_id = :sid AND is_group = 1`), rendered in the merged grid with
**Engine column value "Group"** (note 20 D8 — the Engine column is how a group
is disclosed; the name never is). The EDM detail grid is unchanged — a group
has no EDM. The Group button appears in the merged grid's summary bar on the
submission page and the submission-contextual EDM page (both know the
submission); it opens one compose dialog whose member pick-list lists every
eligible member of the submission — finished own analyses, broker analyses,
and finished groups — with the grid's ticked rows pre-checked. `/results/analyses`
needs no ordering change: `ids` order is column order and the existing
neighbour-swap arrows already operate on whatever ids arrive (FR-016); group
ids flow through `list_results_columns`, which selects by id with no EDM
filter.

**Rationale**: O-03 (both entry points, identical grids, submission-scoped
pick-list) and note 19 D14 (groups exist only at submission level — Ben's own
constraint). Pre-checking ticked rows but showing the full pick-list resolves
the EDM-page case where eligible members live in other EDMs.

## Assumptions carried without a decision ID

- **`numOfSimulations` is a caller input** the package never chooses (handover
  R-04). The dialog prefills it from the inspection — the largest member
  `periods` for a PLT group, `1` for a pure ELT group, where the Platform
  requires a positive value with no group-PLT meaning (live observation
  `knowledge/sources/observations/grouping-pure-elt-conflicting-event-rates-2026-09-01.json`,
  IRP workspace) — and the analyst may edit it.
- **Simulation windows and `description` are omitted** — `GroupingSettings`
  leaves them `None` and the package sends no key; the platform caps
  `description` at 50 characters, too short to carry anything structural.
- **`simulateToPLT` is derived by the package** from the inspected members
  (`GroupingInspection.simulate_to_plt`); the Workbench displays it as the
  group output type and never sets it.
- **`propagate_detailed_losses`** maps FR-005's "Propagate detailed output"
  directly; the Workbench default is ON and the worker always passes the
  plan's explicit value. What detail is retained stays open product-side
  (O-02) — pass-through only.
- **irp-integration `0.8.0rc1`** (TestPyPI) is the pinned dev build; its
  `client.grouping` module is the only grouping API. Production stays on PyPI
  `0.2.0` until the package is released there.

## Clarifications

### Session 2026-08-27

- Q: The platform grouping job schema has no tag field and no post-creation tagging endpoint exists (T-07) — how does the spec resolve FR-017's "including groups" and User Story 4 acceptance 2? → A: Amend the spec (O-07): groups are submitted without the tag; FR-017 and SC-003 scope to individual analyses; User Story 4 acceptance 2 removed. Revisit if Moody's adds a tagging endpoint.
- Q: O-05 was Assumed — what is the submission tag's exact value? → A: The bare submission name (011's current behavior kept for now; the structured `submission:<name>` prefix from note 18 D12 may be revisited). O-05 → Approved; T-06 rewritten.
- Q: If the sandbox check finds the platform rejects single-member grouping jobs (the T-08 emulation for Create independent groups ON), what ships? → A: The setting is dropped from the compose dialog entirely (O-08); FR-006 amended with the contingency. *(Superseded the same day by the next entry.)*
- Q: The worker contract had a separate pre-submit "Resolve" step calling `build_region_peril_simulation_set`, but the gateway exposes one submit call — which is it? → A: One call; the wheel resolves members and builds the simulation set internally, and the Workbench reimplements nothing from irp-integration. T-03 rewritten with the wheel-source evidence.
- Q: Wheel 0.6.2 raises the same `IRPAPIError` for every failure and its scheme resolution never fails pre-submit — how does the worker classify errors, and does FR-009's "before anything is submitted" hold? → A: Uniform handling — the duplicate-name message prefix retries with `_n`; every other exception records `SUBMISSION FAILED` + `failure_reason` like the analysis worker. Spec amended (O-09): the pre-submit guarantee narrows to member/name failures; an unresolvable scheme set surfaces as a failed job with the named cause. FR-009, US2 acceptance 3, and SC-005 rewritten.
- Q: Does the Workbench carry Risk Modeler's Create independent groups checkbox at all? → A: No — dropped entirely, no checkbox and no emulation (O-08 and FR-006 rewritten): CIC never enables it and the results views already show a group beside its member analyses. The per-member emulation stays recorded in T-08 as the rejected alternative.

### Session 2026-09-02

Source: irp-integration `0.8.0rc1` (TestPyPI, installed in `.venv`), the
handover document `irp-integration-grouping-plan.md` §8 (IRP workspace root),
design note 22, and the live observation
`knowledge/sources/observations/grouping-pure-elt-conflicting-event-rates-2026-09-01.json`
(IRP workspace).

- Q: The package replaced the automatic grouping API with `client.grouping.inspect()` / `submit()` / `get_job()`; does the compose dialog get a rendered preview first? → A: No preview; the templates are built directly (the layout is the approved dialog plus an inspection fragment inside it). *(Superseded the same day by the compose-flow session below.)*
- Q: When members of a partition use different event-rate schemes, is one preselected? → A: No default. The analyst must pick from the members' schemes, and Group stays disabled until every conflicting partition has a choice (note 22 O22-1). O-06 → Approved; FR-007 rewritten.
- Q: What prefills the simulation count? → A: The largest member `periods` for a PLT group, `1` for a pure ELT group; editable in the dialog. FR-019 added.
- Q: How is the migration committed? → A: One commit per logical step on `012-grouping-execution` (spec docs, gateway, compose flow, worker, tests, dependency lock), no push.
- Q: Members were resolved by name; the package accepts ids only — what identifies a member? → A: The Platform analysis id already stored on `irp_analysis.irp_id` (note 22 O22-16: names duplicate tenant-wide). T-10 rewritten; O-09 rewritten.
- Q: The package no longer pre-checks duplicate group names — what keeps finalize's name-only resolution valid? → A: The worker pre-checks tenant-wide with `count_analyses_named` and retries with `_n`. T-11 amended.
- Q: Treaty consistency? → A: Not validated (handover R-12); the dialog discloses the limitation. Non-negotiable 6 and FR-020 added.

### Session 2026-09-02 (compose flow)

Source: the rendered preview `docs/ui_previews/group_compose_modal.html`
(artifact `https://claude.ai/code/artifact/daebcd15-64e1-4dfc-9d45-42cfa2170f3c`),
reviewed with the approver.

- Q: The single-scroll dialog (members, name, Inspect members, the inspection fragment, currency, Propagate in one body) was judged dirty and the flow bad — what replaces it? → A: Three screens in one 1080px modal and one form: Members (pick-list with name search, editable name, "N of M selected"; Next runs the inspection), Inspection (wait state, facts strip, partition table, treaty section, blocked and read-error states; Next needs every dropdown chosen), Settings (summary, currency block, Propagate, simulation count; Group submits, a 422 shows on this screen without losing state). Screen 2 is always shown, even when nothing needs a choice. Approved from the preview; supersedes the "no preview" answer above.
- Q: What does the partition table show? → A: Peril and region as the codes the inspection returns (`WS`, `NA`; no name map), model version with the engine version from the region facts (`RL23 · 11.0`), member display names only (no scheme, PET, or period text), and the scheme cell — a dropdown only where the members' schemes differ, each option naming its member count. Names wrap; every name and resolved scheme carries a `title` tooltip.
- Q: Where does the simulation count live? → A: Screen 3, and only for a PLT group; an ELT group submits a hidden `1`. FR-019 amended.
- Q: The scheme dropdowns were `required` — kept? → A: Dropped. A hidden required select blocks the browser's submit from screen 3; the Alpine gate and `request_grouping` enforce the choice.
- Q: How does the submit's 422 render? → A: A `partials/group_submit_errors.html` fragment retargeted at `#group-submit-errors` on screen 3; the dialog is no longer re-rendered, so the inspection and inputs survive.
- Q: Where do the view rules live? → A: A new `app/services/grouping_view.py` (`build_inspection_screen`) builds rows, options, and problem texts; `grouping_service.py` stays at gate + plan scope. The plan the dialog emits (`request_grouping`, 11 keys) and the worker are unchanged.
