# Quickstart: Grouping Execution (spec 012)

How to verify the feature. Contracts: [contracts/](contracts/); schema:
[data-model.md](data-model.md).

## Prerequisites

- Branches `010-analysis-execution` and `011-analysis-results` merged to
  `main` and this branch rebased onto the result (plan T-01).
- Docker stack up (`make dev-up`) or WSL2 native — **starting the stack is the
  developer's call**; agents report and stop if it is down.
- DB lifecycle: **Rebuild** — `make db-rebuild` (destructive) after the
  migration edit (new column, table, seed row).
- `DEFAULT_ANALYSIS_CURRENCY_CODE` / `_SCHEME` / `_VINTAGE` set in `.env`
  (they already drive the analysis-execute picker).
- A submission with at least two finished analyses (run a suite via the
  existing flow and wait for `ready`).
- A worker process for the new queue: `submit_grouping` (CR-04 per-queue
  commands), plus the existing `finalize_analysis` /
  `retrieve_analysis_results` queues and the poller.

## 1. Unit tier (no containers)

```bash
uv run pytest tests/unit
```

Covers: compose gate, inspection view and screen rows (`test_grouping_view.py`),
group naming and collision suffix, plan composition, compose routes (dialog,
inspect fragment states and oob parts, submit 422 retarget),
worker submit paths against `FakeIRP` (success with the exact request body /
duplicate-name pre-check retry / generic and structured submission failures),
poller grouping routing, group read models.

## 2. SQL Server tier

```bash
make test-sql        # or make wsl-test-sql
```

Schema drift guard passes with `irp_analysis.submission_id`, the three-leg
origin CHECK, `uq_irp_analysis_live_submission_name`,
`irp_analysis_group_member`, and the `submit_grouping` kind row mirrored.

## 3. Manual walkthrough (spec §User Stories / SC-001…SC-005)

1. Open a submission whose Results grid shows ≥2 finished analyses. Tick two;
   the **Group** button enables. Tick a running or failed row instead — it is
   absent from the compose pick-list (US-1 acceptance 2).
2. Click Group. Screen 1 shows the whole pick-list with your rows pre-checked,
   "N of M selected", and the name prefilled `CRE_<submission>_Group`; typing
   in the search box filters the list by name without a request (US-1
   acceptance 1). Untick a row so one member is left: **Next** disables.
3. Click **Next**. The wait state shows while Risk Modeler is read, then
   screen 2: the facts strip (group output ELT or PLT, member count, scheme
   mismatch count), one table row per peril · region · model version with the
   members by name and the scheme each row uses, and the treaty section.
   **Next** is enabled. Click it: screen 3 shows the name, output, and members
   on the left, and currency/scheme/vintage prefilled from the env defaults
   and Propagate detailed output ON on the right; an ELT group shows no
   simulation count. Click **Back** twice: screen 1 still has your members
   ticked. Go forward again and click **Group**. The `submit_grouping` job
   appears immediately in the RWB jobs monitor; the group row appears in the
   grid when the worker claims the job and shows `pending → running`. On
   completion the row turns `ready`, Engine column reads **Group** (US-1
   acceptance 3–4, US-3 acceptance 2). In `irp_job`,
   `last_submission_payload` is the exact request body.
4. Pick two finished DLM analyses run under different event-rate schemes and
   click Next: the conflicting row's scheme cell is a dropdown listing only
   the members' schemes with their member counts, none preselected, and Next
   stays disabled until you choose. Choose, continue, and Group; "Schemes
   chosen" on screen 3 names your pick; the group finishes (US-2 acceptance
   1, SC-002). Pick a DLM + an HD and click Next: output reads PLT and screen
   3 shows the simulation count prefilled with the largest member PLT length
   (US-2 acceptance 2).
5. Blocked, error, and changed states: inspect a member set the platform
   cannot group (for example two HD analyses run against different
   simulation sets in one partition) — screen 2 opens with "These members
   cannot be grouped", the problem and its members, the row's scheme cell
   reads "Different simulation sets", and Next stays disabled; nothing
   reaches Risk Modeler (US-2 acceptance 3). Inspect with Risk Modeler
   unreachable: screen 2 shows the read error with **Retry**. Inspect, then
   change a member's facts in Risk Modeler before submitting — the job records
   `SUBMISSION FAILED` with a `failure_reason` telling you to inspect again,
   and no grouping job appears in Risk Modeler (US-2 acceptance 4, SC-005).
   Force a compose 422 (inspect in a second tab, then submit here with a
   stale fingerprint): the errors appear at the top of screen 3 and the
   dialog keeps its state.
6. Tick the finished group plus an analysis → **View**: both open on
   `/results/analyses`; the ◀/▶ controls move the group column to either end
   (US-3 acceptance 1, 3).
7. Compose again: the finished group is listed as a pick-list member (US-1
   acceptance 5, nested grouping).
8. In Risk Modeler, filter analyses by the submission-name tag — every
   Workbench-submitted analysis of the submission appears (US-4 acceptance 1;
   the group itself carries no tag — spec O-07).

## 4. IRP sandbox tier — T-11 and SC-002 verification

```bash
make shell
uv run pytest tests/irp --run-irp -k grouping
```

Cases in `tests/irp/test_grouping.py`, each skipped until its member set is
named in the environment as comma-separated **Platform analysis ids** of
FINISHED sandbox analyses:

| Case | Variable |
|---|---|
| Pure-ELT round-trip (T-11) and stale-fingerprint rejection | `IRP_TEST_GROUP_ELT_IDS` (≥2 analyses sharing one event-rate scheme) |
| Conflicting event-rate schemes (SC-002) | `IRP_TEST_GROUP_CONFLICTING_ELT_IDS` (≥2 ELT analyses whose schemes differ in one partition) |

The first inspects (no blocking problems, `output_loss_table == "ELT"`, no
selection required), submits with `num_of_simulations=1`, asserts the request
body (`resourceUris` are the ids, `settings.numOfSimulations == 1`,
`settings.simulateToPLT is False`), polls `get_grouping_job` to `FINISHED`,
then asserts `get_analysis_stats` / `get_analysis_ep` return data for the
group's `analysisId` — until it passes, T-11 is an assumption, not a validated
claim. The same ids submitted with a fabricated fingerprint must raise
`IRPGroupingValidationError` carrying `inspection_changed` and create no job.
The conflicting case inspects (exactly one partition requires a selection,
≥2 options), submits once per offered scheme under separate names, asserts
each body's `regionPerilSimulationSet[*].eventRateSchemeId` equals the chosen
scheme with `simulationSetId == 0` and `simulationPeriods == 0`, and polls
both to `FINISHED`. Propagate detailed output verification stops at the
`propagateDetailedLosses` flag, pending O-02.
