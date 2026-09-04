# Worker / Gateway / Poller Contracts: Grouping Execution (spec 012)

## Approved plan (`rwb_job.input_data`, `rwb_job_type = submit_grouping`)

```json
{
  "grouping_request_id": "<uuid — the rwb_job requestor_id>",
  "group_analysis_id": "<uuid — minted at compose; the group irp_analysis PK>",
  "submission_id": "<uuid>",
  "submission_name": "<str>",
  "group_full_name": "CRE_Acme_Re_2026_Group",
  "actor_id": "<uuid|null>",
  "currency": {"code": "USD", "scheme": "RMS", "vintage": "RL25", "asOfDate": "2026-05-28"},
  "propagate_detailed_losses": true,
  "num_of_simulations": 50000,
  "event_rate_selections": [
    {"peril_code": "WS", "region_code": "NA", "model_version": "11.0", "event_rate_scheme_id": 738}
  ],
  "simulation_set_selections": [
    {"peril_code": "WS", "region_code": "NA", "model_version": "11.0", "simulation_set_id": 147}
  ],
  "expected_inspection_fingerprint": "v4:<sha256>",
  "members": [
    {"analysis_id": "<uuid>", "irp_id": 5630592, "name": "<submitted ≤64 name>", "display_name": "<untruncated name>", "kind": "own"},
    {"analysis_id": "<uuid>", "irp_id": 5630601, "name": "<name>",               "display_name": "<untruncated name>", "kind": "broker"},
    {"analysis_id": "<uuid>", "irp_id": 5630777, "name": "<group name>",         "display_name": "<untruncated name>", "kind": "group"}
  ]
}
```

`irp_id` is the member's Platform `analysisId` (`irp_analysis.irp_id` as an
int). `event_rate_selections` holds one entry per partition the inspection
marked `event_rate_selection_required`; `simulation_set_selections` one per
partition marked `simulation_set_selection_required` (the ELT partitions of a
PLT group); `expected_inspection_fingerprint` is
the `GroupingInspection.fingerprint` the analyst inspected. The worker
executes this verbatim (AGENTS.md rule 8) — it never re-derives the member
set, name, currency, simulation count, selections, or fingerprint.

## `submit_grouping` worker body (`app/workers/grouping_jobs.py`)

CR-04 `rwb_actor`; queue name `submit_grouping`; `max_retries=0`.

1. **Claim** — INSERT the group `irp_analysis` row by `group_analysis_id` PK
   (`is_group=1`, `submission_id`, `name`/`full_name` from
   `name_attempt(group_full_name, attempt)`, `status_code='pending'`,
   `submitted_settings` = plan). PK hit → resume. Local name collision →
   increment attempt (same loop as `_claim_analysis`). INSERT the
   `irp_analysis_group_member` rows.
2. **Name pre-check** — `irp_gateway.count_analyses_named(group.name)`; while
   the count is non-zero, move the group row to the next locally free `_n`
   name (`_rename_group`, bounded by `MAX_NAME_ATTEMPTS`). The package no
   longer pre-checks group names, and `finalize_analysis` resolves the group
   by name only, so uniqueness at submit is the worker's job.
3. **Submit** — one gateway call: `irp_gateway.submit_grouping(...)` (below)
   with the plan's Platform ids, currency, propagate flag, simulation count,
   selections, and fingerprint. The package re-inspects, compares the
   fingerprint, validates the selections, and POSTs.
   - `IRPGroupingValidationError` (`.problems`): `failure_reason` is built
     from the problems — an `inspection_changed` code yields "The member
     analyses or reference data changed after inspection. Reopen the compose
     dialog and inspect again."; otherwise each problem's message is joined
     with its partition (`peril · region · model version`) and PET ids when
     present.
   - Any other exception (pre-check read failure, transport error, rejected
     POST): `failure_reason` = the exception text.
   - Both: `irp_job_service.record_submission_failure(...)`
     (`irp_job_type='grouping'`, status `SUBMISSION FAILED`, payload and
     `request_params` = the submit kwargs plus `group_name`) + group row
     `status_code='error'`, `failure_reason`; no automatic retry (T-11).
4. **Record** — `record_submitted_irp_job`
   (`irp_job_type='grouping'`, `irp_analysis_id=group_analysis_id`,
   `requested_from_submission_id`, `irp_id` = the job id, `payload` = the
   exact request body the package POSTed, `response` = `{"job_id": <int>}`,
   `request_params` = the submit kwargs plus `group_name`). The group row
   stays `pending`; progress is `irp_job.status` (spec 010 T-07).

## Gateway (`app/services/irp_gateway.py` — Protocol, `_RealGateway`, module functions, `FakeIRP`)

The gateway re-exports the package grouping types (`GroupingInspection`,
`GroupingMember`, `GroupingRegionFact`, `GroupingPartition`,
`GroupingPartitionKey`, `EventRateSchemeOption`, `SimulationSetOption`,
`GroupingProblem`, `GroupingProblemCode`, `GroupingSimulationMapping`) and
`IRPGroupingValidationError`, so the service, worker, templates, and `FakeIRP`
never import `irp_integration`.

```python
def inspect_grouping(*, analysis_ids: list[int]) -> GroupingInspection:
```

`client.grouping.inspect(analysis_ids=analysis_ids)` — Platform reads only,
nothing created. Raises `IRPIntegrationError` subclasses on a malformed id
list or a failed read.

```python
def submit_grouping(
    *,
    analysis_ids: list[int],                  # Platform analysisIds, from the plan
    group_name: str,                          # ≤64, collision-resolved by the worker
    currency: dict,                           # {code, scheme, vintage, asOfDate}
    propagate_detailed_losses: bool,
    num_of_simulations: int,                  # > 0
    event_rate_selections: list[dict],        # {peril_code, region_code, model_version, event_rate_scheme_id}
    simulation_set_selections: list[dict],    # {peril_code, region_code, model_version, simulation_set_id}
    simulation_periods_selections: list[dict],  # {peril_code, region_code, model_version, simulation_periods}
    expected_inspection_fingerprint: str,
) -> tuple[str, dict]:                        # (irp job id, exact request body)
```

Builds `GroupingCurrency(code, scheme, vintage, as_of_date=currency["asOfDate"])`,
`GroupingSettings(analysis_name=group_name, currency, propagate_detailed_losses,
num_of_simulations)` (description and windows omitted), and one
`EventRateSelection(GroupingPartitionKey(peril_code, region_code,
model_version), event_rate_scheme_id)` per event-rate selection and one
`SimulationSetSelection(GroupingPartitionKey(...), simulation_set_id)` per
simulation-set selection, then calls
`client.grouping.submit(...)`. Returns `(str(submission.job_id),
submission.request_body)`. `IRPGroupingValidationError` and `IRPAPIError`
propagate to the worker.

```python
def get_grouping_job(irp_id: str) -> JobStatus:
```

`client.grouping.get_job(job_id=int(irp_id))` — the single-status read;
`JobStatus(status=str(data["status"]), result=data)`, same shape as
`get_analysis_job`. No `poll_*_to_completion` variant is wrapped (Article 11).

```python
def count_analyses_named(name: str) -> int:
```

`len(client.analysis.search_analyses_paginated(filter='analysisName="<name>"'))`
— the tenant-wide duplicate pre-check the package no longer performs (worker
step 2).

```python
def get_analysis_by_name_only(name: str) -> AnalysisHit:
```

Same filter; raises unless exactly one hit. Used by the group branch of
`finalize_analysis`. The worker's pre-check plus the `_n` retry guarantee the
group's name was unique at submit; a duplicate appearing between submit and
finalize is a tenant hygiene error worth failing loudly on.

## Poller (`app/poller/run.py`)

- `_GETTERS["grouping"] = irp_gateway.get_grouping_job`.
- `_TERMINAL_HANDLERS["grouping"] = _handle_grouping_terminal`:
  - `FINISHED` → `enqueue_rwb_job(requestor_type='irp_job',
    requestor_id=job.id, rwb_job_type='finalize_analysis',
    input_data={"analysis_id": <group row id>})`.
  - `FAILED` / `CANCELLED` → group row `status_code='error'`,
    `failure_reason=_analysis_failure_reason(result)`.
- `_submission_retry` stays filtered to `irp_job_type='analysis'` — grouping
  submission failures are terminal and visible; the analyst recomposes.

## `finalize_analysis` group branch (`app/workers/analysis_jobs.py`)

When the target row has `is_group=1` and no `edm_id`: resolve via
`get_analysis_by_name_only(analysis.name)` instead of
`get_analysis_by_name(name, edm_name)`. Everything after is unchanged:
`get_analysis_metadata`, stamp `irp_id` + `settings_metadata` +
`status_code='ready'`, chain `retrieve_analysis_results`
(`requestor_type='irp_analysis'`). `retrieve_analysis_results` itself is
untouched — a group's stats/EP are fetched by `analysisId` like any analysis
(T-11 assumption, sandbox-verified).
