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
- Q: Treaty consistency? → A: Not validated (handover R-12); the dialog discloses the limitation. Non-negotiable 6 and FR-020 added. *(Superseded by the 2026-09-03 treaty terms session below.)*

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

### Session 2026-09-03 (treaty terms)

Source: irp-integration `0.8.0rc4`, then `0.8.0rc5`
(`.venv/Lib/site-packages/irp_integration/grouping.py`,
`LOSS_AFFECTING_TREATY_FIELDS`, `_inspect`, `_treaty_warnings`), reviewed with
the approver.

- Q: rc4 compares the loss-affecting terms (`LOSS_AFFECTING_TREATY_FIELDS` plus `lobs` and `lossOccurrences`) of every treaty sharing a Treaty Number across the members and returns each mismatch as a `GroupingProblem` with `code="inconsistent_treaty_terms"` in `GroupingInspection.warnings`; `submit()` raises only on `blocking_problems`. Does the Workbench show them? → A: Yes. Screen 2 lists each mismatch plus the count on the facts strip; screen 3's summary gains a Treaties row. Mismatches never disable Next. Supersedes the 2026-09-02 "Not validated" answer; non-negotiable 6, FR-020, O-09 rewritten. *(The amber-notice rendering is superseded by the rc5 answers below.)*
- Q: The problem carries `analysis_ids` and `treaty_ids` as two sorted tuples with no pairing — pair them in the Workbench? → A: No. The package would have to return the pairing. *(rc5 returns it: see `GroupingProblem.treaties` below.)*
- Q: How are `differing_fields` (raw API names such as `attachmentPoint`) rendered? → A: With the treaty grid's key humanizer in `app/services/treaty_service.py` (`humanize_key`, made public). Two overrides added for initialisms the humanizer gets wrong: `maolAmount` → "MAOL Amount", `percentageRiShare` → "Percentage RI Share"; the EDM treaty grid gets the same labels.
- Q: The notices name the differing terms but never their values, so the analyst has to open Risk Modeler to see the two numbers. What should the screen show? → A: Risk Modeler's own table. `0.8.0rc5` adds `GroupingProblem.treaties`, a tuple of `GroupingTreaty(analysis_id, treaty_id, treaty_number, terms)` carrying the rows the comparison already read, sorted by `(analysis_id, treaty_id or 0)` and populated only on `inconsistent_treaty_terms`. `FINGERPRINT_VERSION` stays at 3 and every other warning field is unchanged. The Workbench renders one table per Treaty Number and drops the notices.
- Q: Get the values by calling `search_analysis_treaties_paginated` per affected member during inspect instead? → A: Rejected. The inspection already made that call for every member; repeating it doubles the Risk Modeler HTTP on the request path for facts the package holds in memory.
- Q: Read the values from the local `irp_treaty` snapshot? → A: Rejected on two counts. Terms change when a treaty is applied to an analysis — an analysis run in CAD against a treaty defined in USD in the EDM reports CAD, which is exactly what the comparison flags — so the EDM definition is the wrong number; and `irp_analysis` allows a row with no `edm_id` (broker RDM captures), whose treaties `backfill_edm_detail` never wrote.
- Q: Which columns — the treaty identity plus one column per differing term, or Risk Modeler's fixed eleven? → A: The fixed eleven (analysis name, analysis id, treaty id, Treaty Number, treaty type, effective date, expiration date, attachment point, occurrence limit, per risk limit, currency), for familiarity with the Risk Modeler screen analysts already read. The comparison covers 23 terms, so a mismatch on `priority`, `lobs`, `aggregateLimit` or `lossOccurrences` would render eleven identical columns; the table heading therefore names every differing term, and a differing term among the eleven marks its header and cells.
- Q: Reuse the `attr_val` and `money` macros by importing `partials/treaty_row.html`? → A: Not from that file. A Jinja `from` evaluates the imported template's body, which reads `t`, so the import raised `'t' is undefined`. The three macros moved to `partials/treaty_macros.html`; `treaty_row.html` and `group_inspection.html` both import from there.
- Q: Treaties changed after inspection? → A: Already covered: treaties feed the package fingerprint, so the submit fails with `inspection_changed`. No worker change.
- Q: The click-through showed the group row frozen after submit — why? → A: The worker wrote `status_code='running'`, but `ExecutedAnalysis.is_live` is the single test `status_code == 'pending'` (spec 010 T-07: progress lives on `irp_job.status`, every write leaving `pending` is terminal), so the Results section stopped polling. Widening `is_live` to `running` was rejected as a second status vocabulary; the group row now stays `pending` through submit like every analysis row. data-model.md and contracts/grouping-worker.md corrected.

### Session 2026-09-03 (event-rate scheme names)

Source: sandbox probes of group 5676625 `HU_US Workbench Group` and analysis
5627699 `CRE_EQ_HI_RES_US EQ wFFSL wDS - PERS Stochastic`, the Risk Modeler
help page `Conflicting Event Rates in Groups`, the `createGroupingJob` schema
(`knowledge/sources/moody-docs/raw/creategroupingjob-93013aaa.html`), and the
approver's side-by-side run of the same pairing in Risk Modeler.

- Q: Inspecting that group beside that analysis offered two schemes under one name, `RMS 2025 Historical Event Rates`. Why two? → A: They are scheme 738 (Historical) and 739 (Stochastic), and the shared name was a mislabel. `_inspect` ran `labels.setdefault(scheme_id, analysis_label)` for every region row, which attributed the analysis-level scheme name to any other scheme ID the rows carried. `0.8.0rc6` drops that line and names each offered ID from `reference_data.get_event_rate_schemes()`.
- Q: `get_regions` on a group returns its member analyses' rows as well as its own — 23 rows on 738 from `CRE_HU_US_DLM USFL 85pct SS v23 Historical` and 23 on 739 from `...Stochastic`, plus one group row (`modelProfileId` 0, `RL25`, scheme 738). The group's `additionalProperties.eventRateSchemes` records 738 alone. Should a group member therefore offer only 738? → A: No. Risk Modeler asks for the scheme again when that group is regrouped, so the members' schemes stay the candidates. An ELT stores losses per event and takes its rates from the scheme at EP time, so a group's ELT is not rated until the scheme is chosen; `eventRateSchemes` records what the group's own EP used, not a constraint on regrouping. Reading only that was implemented and reverted.
- Q: Risk Modeler's dropdown lists every North Atlantic Hurricane scheme, not just the two the members used. Match it? → A: No. FR-007 stands: the Workbench offers only the members' schemes, agreed with the analysts.
- Q: Does Risk Modeler prompt on the earthquake partition too, where one analysis contributes scheme 163? → A: No — the selector appears only where schemes conflict, which is what `event_rate_selection_required` already does.
- Q: `AnalysisGroupSettings` carries `eventRateSchemeId` only inside `regionPerilSimulationSet`, documented as required for HD (PLT-based) groups, and `simulateToPLT` recalculates ELT losses as PLT using that scheme. Does resolving a pure-ELT conflict force PLT output? → **Open.** `rm_grouping_request.json` (Risk Modeler's own request for the flat 738-vs-739 conflict) sends one entry per model region carrying the chosen scheme, so Risk Modeler does route the choice through that array. Whether the group's output loss table stays ELT is unverified.

### Session 2026-09-03 (simulation sets)

Source: irp-integration `0.8.0rc7` (commit `b767a50`, TestPyPI;
`irp_integration/grouping.py` `_inspect`, `_resolve_simulation_set_selections`,
`tests/test_grouping.py::test_risk_modeler_simulation_choices_are_independent_of_event_rate_scheme`)
and the Risk Modeler request the approver captured for the HD + DLM + group
pairing above.

- Q: rc6 resolved one simulation set per ELT partition through `get_simulation_set_exact(scheme, model region, model version)` and blocked with `simulation_set_mapping_ambiguous` when the reference data held several. Risk Modeler instead offers every system simulation set for the peril, region, and model version and lets the analyst pick one beside the scheme. What changed? → A: rc7 returns the candidates as `GroupingPartition.simulation_set_options` with `simulation_set_selection_required` (true whenever any option exists on an ELT partition of a PLT group), adds `"simulation_set_selections"` to `required_caller_inputs`, takes `simulation_set_selections=` on `submit()`, validates them like the event-rate selections (`simulation_set_selection_missing` / `_duplicate` / `_unknown_partition` / `_not_required` / `_not_offered`), and drops the ambiguous code. `simulation_set_mapping_missing` (no set at all) still blocks. `FINGERPRINT_VERSION` is 4, so every earlier fingerprint fails with `inspection_changed`. The Workbench renders one dropdown per required partition (FR-021) and carries the choice in the plan.
- Q: Filter the simulation sets by the chosen scheme, since each reference row names an `eventRateSchemeId`? → A: No. Risk Modeler accepted set 147 (reference scheme 739) under scheme 738 for the NA/WS partition; the package documents `SimulationSetOption.event_rate_scheme_id` as descriptive only. The two selects are independent and a scheme change does not touch the simulation-set select.
- Q: Preselect the first option, or the one whose reference scheme matches? → A: No default (Article 5, same rule as O-06 for schemes); the option order is the package's ascending id and carries no meaning. A partition with a single set still asks.
- Q: Does the compose gate check that every required partition has a simulation set? → A: Parse and dedupe only, as for the event-rate selections; the required set is only knowable by re-inspecting, which the package does at submit. Missing, unknown-partition, and unoffered selections fail the job with the package message plus the partition in `failure_reason`; the Alpine gate keeps Next disabled until every dropdown is chosen.
- Q: Show the HD partition's PET in the new column? → A: Reversed the same day; see Session 2026-09-03 (HD PET names) below.

### Session 2026-09-03 (HD PET names)

Source: probes of the sandbox reference tables
`/data-store/referenceTables/PETMetadata` (2,841 rows) and
`/data-store/referenceTables/SimulationSet` (578 rows) through
`ReferenceDataManager.get_all_pet_metadata` and `get_all_simulation_sets`,
and irp-integration `0.8.0rc8` (commit `be1a6ef`, TestPyPI).

- Q: Risk Modeler names the simulation set of an HD analysis with no ELT-to-PLT conversion, and the em dash of the previous session shows nothing. Where does the name come from? → A: `PETMetadata.petName`, keyed by the region's `petId` and the model version. `_inspect` already read that row for every PLT region fact to correct its peril and region codes from `modelRegionCode` and discarded the rest, so rc8 keeps it as `GroupingRegionFact.pet_name` at no extra request. `FINGERPRINT_VERSION` is 5.
- Q: Resolve the `pet_id` against `SimulationSet`, the table the ELT options come from? → A: No, the name would be wrong. `PETMetadata` and `SimulationSet` are separate tables with separate ID sequences: 452 IDs occur in both and none of the 452 describe the same model region and model version. `PETMetadata` 15 is JPWS 2.0 "RMS V2.0 Stochastic Event Rates - Typhoon Events Only" while `SimulationSet` 15 is APEQ 7.0 "Philippines Earthquake, Risklink 7.0". The two only meet in the grouping request, where `regionPerilSimulationSet[].simulationSetId` carries a PET ID on a PLT row and a `SimulationSet` ID on an ELT row, and in the region payload, where Risk Modeler spells the same PET ID `petId` or `simulationSetId`.
- Q: Keep the column hidden until some partition needs a choice? → A: No. Every partition of a PLT group has a simulation set, so the column appears whenever the group output is PLT — a group of HD members alone showed no column at all. An ELT group still has none.
- Q: What does a PLT row show when `PETMetadata` names no single row for the PET? → A: `PET <id>`. `get_pet_metadata_exact` raising on zero or several rows already neither warns nor blocks, and the ID is the only fact left.
- Q: One row, several PETs? → A: List them. Nothing blocks a partition whose PLT members ran on different PETs, and the grouping request carries one `regionPerilSimulationSet` entry per distinct PET.

### Session 2026-09-04 (design note 26 follow-ups)

Source: design note 26 (the 2026-09-03 CIC session — D7, D12, D13–D15, D18)
and the approver's decisions of 2026-09-04.

- Q: Risk Modeler leaves the group currency empty and blocks; the Workbench hard-defaulted to USD; CIC wants the members' currency (D7). What is "the same currency", and what does a broker member do to it? → A: The currency **code** alone. Own analyses and groups carry `submitted_settings.currency.code`; broker rows carry only the metadata snapshot's currency, so a code-plus-scheme-plus-vintage comparison could never hold for them. When every member has a code and the codes agree, that code is prefilled; when they differ or any member's code is unknown, the env default (USD) is prefilled and the hint says which case applies. The analyst may still change it — the note's "otherwise USD" is a default, not a lock, so the Finish fast path treats a currency mismatch as a stop (Pass 2). Rejected: leaving the field empty on a mismatch (Risk Modeler's behaviour, which CIC named as unwanted).
- Q: The member currency lives in two places. Reuse? → A: The FR-005 run-currency rule already in `analysis_service` (own → `submitted_settings.currency.code`, broker → the snapshot's currency) is applied to `GroupMember.currency`; `inspect_grouping` derives `common_currency`, and the inspect response re-renders the `currency_block` out of band into `#group-currency`. Rejected: setting the select client-side from a data attribute — the block's vintage cascade and typeahead are server-rendered, so re-rendering keeps one source.
- Q: Risk Modeler's group simulation periods is a dropdown (D18); the Workbench accepted any positive integer. Which options, and what is preselected? → A: The fixed list 3,125, 6,250, 12,500, 25,000, 50,000, 100,000, 200,000, 400,000, 800,000, kept as `grouping_service.SIMULATION_PERIOD_OPTIONS`; **50,000 always preselected** (approver, 2026-09-04). The largest member PLT length stays as the hint. Rejected: preselecting the largest member length (it is often not in the list); rounding up to the next option; no preselection. The gate accepts `1` (the ELT hidden value) or a listed option and does not re-derive ELT/PLT — that stays the package's submit-time check.
- Q: Which analysis id does the analyst see (D15)? → A: The Risk Modeler app analysis id (`irp_app_analysis_id`; the only id the Risk Modeler UI exposes), in the expanded analysis row and the inspection treaty table. Source order: the column, else the metadata snapshot's `appAnalysisId` (every broker row, and own rows whose finalize wrote none), else an em dash. Rejected: falling back to the Platform id — it is the API's id and never appears in Risk Modeler's UI, so showing it under the same label would mislead. Other placements (the compose member list, the review-screen member list) wait on CIC's answer held for the 9/4 call.
- Q: The treaty table's "Analysis ID" column came from the package's `GroupingTreaty.analysis_id`, a Platform id. → A: `TreatyMismatchRow` keeps `analysis_id` (Platform, the key for the distinct-analysis count) and gains `app_analysis_id` from the member, which the cell shows.
- Q: How does the analyst see what is selected on the Members screen (D13, D14)? → A: A chips panel beside the pick-list, the breakout modal's `.bo-picked` pattern: chips derived from the checked boxes on every `recompute()`, each chip's × unticks its row. The list keeps its order and its unselected rows (Cheryl: "sometimes you start with two and then you're like, oops, I missed this one"). Rejected: moving ticked rows to the top (Cheryl's literal ask, met by the panel without reordering a list the analyst is scanning).
- Q: Where does the runaway comma-separated scheme list live (D12)? → A: In the **expanded analysis row** of a group (`_event_rate_scheme` joins one scheme per member region/peril), not on the compose dialog's review screen as the note's placement suggested. Fix: a five-line CSS clamp on the `settings-grid` value cells other than the wide Members list; the cell's `title` already carries the full text for hover. Rejected: a "+ n more" toggle (needs a view-model split for one cell).
- Q: CIC wants a Finish button that skips the wizard when there is nothing to decide (D8–D10). When does Finish stop, and where does the analyst land? → A: Finish stops when the inspection blocks, any partition needs an event-rate scheme or simulation-set choice, the group output is PLT (the simulation periods would be a silent default), the members' currency codes differ or one is unknown (the USD prefill is a default, not a decision the analyst made), or the env scheme/vintage default is missing. Treaty mismatches do **not** stop it — they never block a submit and the confirmation pane reports them (approver, 2026-09-04). A stop lands on the Inspection screen with one generic notice ("Finish could not submit this group. Review the inspection and continue with Next."); the inspection already names the specific reason, so a per-reason message would repeat it. Rejected: stopping on treaty mismatches (offered, not chosen); a per-reason notice.
- Q: Finish success feedback? → A: The dialog is replaced by a confirmation pane — Inspection passed, group name (with any collision suffix, read back off the persisted plan), output, schemes, treaties, currency, members — that stays until Close, alongside the existing toast and grid refresh. The toast alone disappears before the analyst reads what was submitted. Rejected: closing the dialog on the toast.
- Q: One request or a client-side chain? → A: One route, `POST …/analyses/group/finish`, that inspects and then calls `request_grouping` with the inspection's own fingerprint and ids. A client-side inspect-then-submit chain would post the fingerprint back through the browser for no reason and split the stop logic across Alpine and the server. `finish_blockers` lives in `grouping_service` so the reasons are unit-tested without HTTP.

### Session 2026-09-04 (per-partition simulation periods)

Source: the approver's decisions of 2026-09-04 (afternoon) and irp-integration
`0.8.0rc8` `GroupingManager._build_region_peril_simulation_set`.

- Q: Risk Modeler's group dialog lets the analyst set the simulation periods of every region/peril row of a PLT group. What did the Workbench send in `regionPerilSimulationSet[].simulationPeriods`? → A: Whatever rc8 computed: a PLT row carried the member PET's period count from the region payload, a converted ELT row carried the chosen simulation set's `defaultPeriods` from the SimulationSet reference row, an ELT group's rows carried 0. `GroupingSettings` holds only the group-level `numOfSimulations` and `SimulationSetSelection` only the set id, so the analyst's value had no way through.
- Q: Where does the analyst's per-partition value go? → A: A new `SimulationPeriodsSelection(partition, simulation_periods)` on `GroupingManager.submit(simulation_periods_selections=…)` (irp-integration commit `8b92512`, the build after rc8). The override is optional in the package — a partition without one keeps rc8's value, so existing callers are unchanged — and the package validates only that the value is a positive integer and that the group is simulated to PLT (`simulation_periods_selection_not_required` otherwise, plus `_duplicate` and `_unknown_partition`). The Workbench always sends one per partition of a PLT group and restricts the value to `SIMULATION_PERIOD_OPTIONS`: Risk Modeler's dropdown list is the Workbench's rule, not the package's. Rejected: a `simulation_periods` field on `SimulationSetSelection` (it would leave HD partitions, which have no set selection, without a way to set theirs).
- Q: Which rows get the dropdown? → A: Every row of a PLT group, HD rows included, whether or not a simulation set choice is pending (approver, 2026-09-04) — the value is meaningful for a PET row too. 50,000 preselected, like the group-level dropdown. An ELT group has no column and no per-partition value.
- Q: The Settings screen dropdown and its hint? → A: Labelled "Group simulation periods", the label above the control like the other Settings blocks. The "Largest member: n" hint is dropped and `GroupingInspectionView.largest_member_periods` with it (approver, 2026-09-04): the per-partition rows now show each PET's own period count beside the choice, so the hint repeated the table. The target-length hint stays.
- Q: Finish on a PLT group? → A: It no longer stops on PLT output. When no partition needs a simulation set choice — in practice a group of HD members — Finish submits 50,000 for the group and 50,000 for every partition (`default_simulation_periods_selections`), and the confirmation pane says so (approver, 2026-09-04). A pending simulation set choice still stops it, as before.
- Q: Screen 3's "Schemes chosen" list omitted the simulation set choices. → A: The review lists every screen 2 choice: schemes, simulation sets, and simulation periods, one row each, each entry the partition and the chosen label, built client-side from the selects the same way (approver, 2026-09-04).
