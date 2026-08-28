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

Covers: compose gate, group naming and collision suffix, plan composition,
worker submit paths against `FakeIRP` (success / duplicate-name retry /
uniform submission-failure recording), poller grouping routing, group read
models.

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
2. Click Group. The dialog shows the pick-list with your rows pre-checked, the
   name prefilled `CRE_<submission>_Group`, currency/scheme/vintage prefilled
   from the env defaults, Propagate detailed output ON (US-1 acceptance 1).
3. Submit. The `submit_grouping` job appears immediately in the RWB jobs
   monitor; the group row appears in the grid when the worker claims the job
   (normally within seconds) and shows `pending → running`. On completion the
   row turns `ready`, Engine column reads **Group** (US-1 acceptance 3–4,
   US-3 acceptance 2).
4. Pick two finished DLM analyses run under different event-rate schemes (or a
   DLM + an HD): group them with no scheme choice anywhere in the dialog; the
   group finishes (US-2 acceptance 1–2, SC-002).
5. Force a member-resolution failure (e.g. delete a member analysis in Risk
   Modeler between compose and worker pickup): the job records
   `SUBMISSION FAILED` with the cause in `failure_reason` / job monitoring,
   and no grouping job appears in Risk Modeler. An unresolvable scheme set is
   rejected by the platform instead — the grouping job fails with the cause
   (US-2 acceptance 3, SC-005, spec O-09).
6. Tick the finished group plus an analysis → **View**: both open on
   `/results/analyses`; the ◀/▶ controls move the group column to either end
   (US-3 acceptance 1, 3).
7. Compose again: the finished group is listed as a pick-list member (US-1
   acceptance 5, nested grouping).
8. In Risk Modeler, filter analyses by the submission-name tag — every
   Workbench-submitted analysis of the submission appears (US-4 acceptance 1;
   the group itself carries no tag — spec O-07).

## 4. IRP sandbox tier — T-11 verification (group results retrieval)

```bash
make shell
uv run pytest tests/irp --run-irp -k grouping
```

Submits a real grouping of two finished sandbox analyses, polls
`get_grouping_job` to `FINISHED`, then asserts `get_analysis_stats` /
`get_analysis_ep` return data for the group's `analysisId`. Until this passes,
T-11 is an assumption, not a validated claim.
