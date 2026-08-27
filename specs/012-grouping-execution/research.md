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
  `execute_analysis_batch` and `backfill_analysis_detail` (present only in
  `infra/scripts/seed_db.py` and `tests/iteration1_mirror.py`); 010 seeds them
  in the migration.

**Alternatives considered**: Rebasing 012 onto 011 directly — rejected: it
would inherit 011's missing CR-04 framework and force this feature to redo the
merge later.

## T-02 — The grouping submit runs in a Dramatiq worker, not the request path

**Decision**: `POST /submissions/{sid}/analyses/group` validates, persists the
approved plan as a `rwb_job` (`rwb_job_type = submit_grouping`), and returns.
A new `submit_grouping` actor in `app/workers/grouping_jobs.py` performs the
IRP submission. Per Article 10 / CR-04 the actor gets its own queue named
`submit_grouping`.

**Rationale**: Article 11 permits synchronous IRP submission on the request
path because submit calls are a sub-second HTTP round trip. The grouping
submit is not: before POSTing, `submit_analysis_grouping_job` resolves every
member name via `search_analyses`, and its automatic
`build_region_peril_simulation_set` fan-out calls `search_analyses`,
`get_analysis_by_id`, and `get_regions` **per member**, plus SimulationSet /
PETMetadata / SoftwareModelVersionMap reference-table lookups
(`irp_integration/analysis.py:421-702`; write-up
`grouping-and-event-rate-schemes.md`, IRP workspace root). The repo's own
sequence diagram classifies grouping "async Job, read-fan-out heavy at submit"
(`docs/sequence_diagrams/planned/granular/grouping.md`). A ten-member group is
30+ serial HTTP calls — not a request the analyst waits on.

**Alternatives considered**: Request-path submit (the analysis-execution
precedent is also worker-side via `execute_analysis_batch`, so there is no
request-path precedent to match); pre-building the simulation set on the
request path and passing it to the worker — same fan-out, wrong tier.

## T-03 — Groupability validation: local compose gate; everything platform-side delegated to the wheel's single submit call

**Decision**: The compose gate (request path) checks only what the Workbench
already knows: two or more members selected, every member exists, is not
deleted, belongs to the submission, and is finished (`status_code = 'ready'`).
The worker then makes one call — `submit_analysis_grouping_job` with
`skip_missing=False` — and reimplements nothing from irp-integration: the
wheel resolves member names to URIs, auto-builds `regionPerilSimulationSet`
from the resolved ids, and POSTs. Every submit exception except the
duplicate-name case (T-09) is recorded exactly as the analysis worker records
a submit failure: `SUBMISSION FAILED` `irp_job` + group row
`status_code = 'error'` + `failure_reason` = the exception text, visible in
the grid and job monitoring (FR-011). Spec amended (O-09): FR-009's "before
anything is submitted" narrows to member/name failures.

**Evidence** (wheel 0.6.2 source, read 2026-08-27): every failure mode of
`submit_analysis_grouping_job` raises the same `IRPAPIError` — tenant-wide
duplicate group name (message prefix `Analysis Group with this name already
exists`), missing or ambiguous members with `skip_missing=False`, fan-out
transport errors, and the POST wrapper (`Failed to submit analysis group
job`). Member/name failures raise **before** the POST.
`build_region_peril_simulation_set` never raises past input validation:
conflicting schemes per peril/region just trigger building the set, failed
reference lookups fall back (scheme id 0, engine-string parsing,
analysis-level codes), and an analysis with no regions is skipped — so there
is no pre-submit "unresolvable schemes" failure mode; an unresolvable set is
rejected by the platform (a rejected POST creates nothing; a created grouping
job that fails is stamped `error` by the poller).

**Alternatives considered**: A separate worker-side
`build_region_peril_simulation_set` call as a pre-submit validation step —
rejected: the function has no failure mode to surface (it falls back), the
wheel rebuilds the set inside the submit call anyway (double fan-out), and
distinguishing "resolution" from "submit" errors would mean matching wheel
message text beyond the one duplicate-name prefix the `_n` retry needs. Full
resolution on the request path — rejected per T-02. Local plausibility checks
from stored `settings_metadata` (engineType/perilCode) — rejected: the
library's resolution consults RM region rows and reference tables the
Workbench does not cache; a local pre-check could only duplicate a subset and
would still miss real failures.

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

## T-10 — `skip_missing=False`; a member that fails resolution fails the job

**Decision**: The gateway wrapper calls `submit_analysis_grouping_job` with
`skip_missing=False`, so a member name that resolves to zero analyses raises
instead of being dropped.

**Rationale**: The wheel's default `skip_missing=True` silently narrows the
member set — the exact hazard `docs/sequence_diagrams/planned/granular/grouping.md`
flags, and a violation of the approved-plan rule (AGENTS.md architecture rule
8: the worker executes the plan the user approved, never a silent recompute).
Member resolution is name-based per Article 2: own analyses pass
`analysis_edm_map` (name → EDM name) so the wheel filters
`analysisName + exposureName`; group members pass through `group_names`
(name-only lookup); broker-analysis members resolve name-only — if a broker
name is ambiguous in the tenant the submit fails loudly and the analyst
renames or excludes it. The same applies to a nested-group member whose name
is duplicated tenant-wide (`Duplicate groups exist with name`): possible only
for names created outside the Workbench, since the wheel's tenant-wide
duplicate pre-check and the worker's `_n` retry keep every Workbench group
name unique at submit. No compose-gate name check is added for these — the
wheel already names the cause and the failure is recorded
`SUBMISSION FAILED` with `failure_reason` (T-03).

## T-11 — Group completion reuses the analysis chain, with name-only resolution

**Decision**: The poller gains `_GETTERS["grouping"] =
irp_gateway.get_grouping_job` (wrapping the wheel's single-status
`get_analysis_grouping_job`) and `_handle_grouping_terminal`: `FINISHED` →
enqueue `backfill_analysis_detail` for the group's `irp_analysis` row;
`FAILED`/`CANCELLED` → `status_code='error'` + `failure_reason`.
`backfill_analysis_detail` branches for group rows (no EDM): it resolves the
platform `analysisId` by name-only `search_analyses` filter instead of
`get_analysis_by_name(name, edm_name)`, then proceeds unchanged —
`get_analysis_metadata`, stamp `irp_id`/`settings_metadata`/`status_code='ready'`,
chain `retrieve_analysis_results`. Status **Assumed**: that
`get_analysis_stats`/`get_analysis_ep` serve group `analysisId`s the same as
individual analyses is expected (PRD §16.4: results retrieved the same way;
a group's `additionalProperties` carries `eventRateSchemes`, so RM treats
groups as analyses) but is verified in the sandbox (quickstart step).

**Rationale**: FR-013 requires a group's results viewed and retrieved exactly
as an analysis's; reusing `retrieve_analysis_results` and the stored
`loss_results` extract is the smallest change that satisfies it. The poller's
`poll_analysis_grouping_job_to_completion` variants are forbidden (Article 11);
only the single-status getter is wrapped. Grouping submissions get no
automatic retry: `_submission_retry` stays analysis-only — FR-011 requires
visible failure, not auto-retry, and the analyst recomposes from the dialog.

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

- **Simulation windows and `numOfSimulations`** are left at the wheel's
  defaults (`reporting_window_start="01/01/2021"`, windows likewise,
  `num_simulations=50000`); `simulateToPLT` is managed by the wheel (forced
  `True` when the built `regionPerilSimulationSet` is non-empty). None are
  analyst-facing in the compose dialog. The wheel's defaults are the ones its
  author validated against the sandbox.
- **`description` is sent empty** — the platform caps it at 50 characters,
  too short to carry anything structural.
- **`propagate_detailed_losses`** maps FR-005's "Propagate detailed output"
  directly; the Workbench default is ON (the wheel's parameter default is
  False, so the worker always passes the plan's explicit value). What detail
  is retained stays open product-side (O-02) — pass-through only.
- **irp-integration 0.6.2 suffices** — `submit_analysis_grouping_job`,
  `get_analysis_grouping_job`, and `build_region_peril_simulation_set` are all
  in the installed wheel (byte-identical to the local checkout at 992e125).
  No wheel change is needed for this feature.

## Clarifications

### Session 2026-08-27

- Q: The platform grouping job schema has no tag field and no post-creation tagging endpoint exists (T-07) — how does the spec resolve FR-017's "including groups" and User Story 4 acceptance 2? → A: Amend the spec (O-07): groups are submitted without the tag; FR-017 and SC-003 scope to individual analyses; User Story 4 acceptance 2 removed. Revisit if Moody's adds a tagging endpoint.
- Q: O-05 was Assumed — what is the submission tag's exact value? → A: The bare submission name (011's current behavior kept for now; the structured `submission:<name>` prefix from note 18 D12 may be revisited). O-05 → Approved; T-06 rewritten.
- Q: If the sandbox check finds the platform rejects single-member grouping jobs (the T-08 emulation for Create independent groups ON), what ships? → A: The setting is dropped from the compose dialog entirely (O-08); FR-006 amended with the contingency. *(Superseded the same day by the next entry.)*
- Q: The worker contract had a separate pre-submit "Resolve" step calling `build_region_peril_simulation_set`, but the gateway exposes one submit call — which is it? → A: One call; the wheel resolves members and builds the simulation set internally, and the Workbench reimplements nothing from irp-integration. T-03 rewritten with the wheel-source evidence.
- Q: Wheel 0.6.2 raises the same `IRPAPIError` for every failure and its scheme resolution never fails pre-submit — how does the worker classify errors, and does FR-009's "before anything is submitted" hold? → A: Uniform handling — the duplicate-name message prefix retries with `_n`; every other exception records `SUBMISSION FAILED` + `failure_reason` like the analysis worker. Spec amended (O-09): the pre-submit guarantee narrows to member/name failures; an unresolvable scheme set surfaces as a failed job with the named cause. FR-009, US2 acceptance 3, and SC-005 rewritten.
- Q: Does the Workbench carry Risk Modeler's Create independent groups checkbox at all? → A: No — dropped entirely, no checkbox and no emulation (O-08 and FR-006 rewritten): CIC never enables it and the results views already show a group beside its member analyses. The per-member emulation stays recorded in T-08 as the rejected alternative.
