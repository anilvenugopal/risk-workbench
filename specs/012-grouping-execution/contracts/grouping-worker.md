# Worker / Gateway / Poller Contracts: Grouping Execution (spec 012)

## Approved plan (`rwb_job.input_data`, `rwb_job_type = submit_grouping`)

```json
{
  "grouping_request_id": "<uuid — the rwb_job requestor_id>",
  "group_analysis_id": "<uuid — minted at compose; the group irp_analysis PK>",
  "submission_id": "<uuid>",
  "submission_name": "<str>",
  "group_full_name": "CRE_Acme_Re_2026_Group",
  "actor_id": "<uuid>",
  "currency": {"code": "USD", "scheme": "RMS", "vintage": "RL25", "asOfDate": "2026-05-28"},
  "propagate_detailed_losses": true,
  "create_independent_groups": false,
  "members": [
    {"analysis_id": "<uuid>", "name": "<submitted ≤64 name>", "kind": "own",    "edm_name": "<EDM name>"},
    {"analysis_id": "<uuid>", "name": "<name>",               "kind": "broker", "edm_name": null},
    {"analysis_id": "<uuid>", "name": "<group name>",         "kind": "group",  "edm_name": null}
  ]
}
```

The worker executes this verbatim (AGENTS.md rule 8) — it never re-derives the
member set, name, or currency.

## `submit_grouping` worker body (`app/workers/grouping_jobs.py`)

CR-04 `rwb_actor`; queue name `submit_grouping`; `max_retries=0`.

1. **Claim** — INSERT the group `irp_analysis` row by `group_analysis_id` PK
   (`is_group=1`, `submission_id`, `name`/`full_name` from
   `name_attempt(group_full_name, attempt)`, `status_code='pending'`,
   `submitted_settings` = plan). PK hit → resume. Local name collision →
   increment attempt (same loop as `_claim_analysis`). INSERT the
   `irp_analysis_group_member` rows.
2. **Resolve** — member `irp_id`s for `build_region_peril_simulation_set` come
   from the members' `irp_analysis.irp_id` (all finished ⇒ populated). A
   resolution error from the wheel fails the `rwb_job`
   (`error_detail` = exception text), stamps the group row
   `status_code='error'`, `failure_reason`, and **returns without submitting**
   (T-03). Nothing reaches the platform.
3. **Submit** — `irp_gateway.submit_analysis_grouping(...)` (below). On the
   wheel's duplicate-name `IRPAPIError`: increment the name attempt, update
   the group row's `name`/`full_name`, retry (bounded, as in the claim loop).
   On any other exception: `irp_job_service.record_submission_failure(...)`
   (`irp_job_type='grouping'`, status `SUBMISSION FAILED`) + group row
   `failure_reason`; no automatic retry (T-11).
4. **Record** — one transaction: `record_submitted_irp_job`
   (`irp_job_type='grouping'`, `irp_analysis_id=group_analysis_id`,
   `requested_from_submission_id`, `irp_id`, payload, response) + group row
   `status_code='running'`.
5. **Independent groups** (plan flag ON, T-08): repeat 1–4 once per member
   with a single-member list; group name
   `name_attempt(f"{member_name}_Group", …)`; each independent group gets its
   own minted row id (carried in the plan as
   `independent_group_analysis_ids[member_analysis_id]`), membership row, and
   `irp_job`. Per-member isolation: one failure does not stop the rest; the
   `rwb_job` fails only if the combined group submit failed.

## Gateway additions (`app/services/irp_gateway.py` — Protocol, `_RealGateway`, module functions, `FakeIRP`)

```python
def submit_analysis_grouping(
    *,
    group_name: str,                      # ≤64, already collision-resolved
    analysis_names: list[str],            # all member names
    analysis_edm_map: dict[str, str],     # own members only: name -> EDM name
    group_names: set[str],                # member names that are groups (name-only lookup)
    currency: dict,                       # {code, scheme, vintage, asOfDate} — always explicit
    propagate_detailed_losses: bool,
) -> tuple[str, dict]:                    # (irp job id, request body)
```

Wraps `client.analysis.submit_analysis_grouping_job(..., skip_missing=False)`
(T-10). Wheel-managed and not exposed: `simulate_to_plt`, `num_simulations`,
window dates, `region_peril_simulation_set` (auto-built), `description` (empty).
The wheel result's `job_id` is returned with `http_request_body`; a `skipped`
result cannot occur with `skip_missing=False`.

```python
def get_grouping_job(job_id: str) -> JobStatus:
```

Wraps the single-status `client.analysis.get_analysis_grouping_job(job_id)` —
same `JobStatus` shape as `get_analysis_job`. The
`poll_analysis_grouping_job_to_completion` variants are never wrapped
(Article 11).

```python
def get_analysis_by_name_only(name: str) -> AnalysisSearchHit:
```

`search_analyses(filter='analysisName = "<name>"')`; raises unless exactly one
hit. Used by the group branch of `backfill_analysis_detail` (groups have no
EDM to disambiguate with; the per-submission unique name plus the `CRE_`
prefix makes a duplicate a tenant hygiene error worth failing loudly on).

## Poller (`app/poller/run.py`)

- `_GETTERS["grouping"] = irp_gateway.get_grouping_job`.
- `_TERMINAL_HANDLERS["grouping"] = _handle_grouping_terminal`:
  - `FINISHED` → `enqueue_rwb_job(requestor_type='irp_job',
    requestor_id=job.id, rwb_job_type='backfill_analysis_detail',
    input_data={"analysis_id": <group row id>})`.
  - `FAILED` / `CANCELLED` → group row `status_code='error'`,
    `failure_reason=_analysis_failure_reason(result)`.
- `_submission_retry` stays filtered to `irp_job_type='analysis'` — grouping
  submission failures are terminal and visible; the analyst recomposes.

## `backfill_analysis_detail` group branch (`app/workers/analysis_jobs.py`)

When the target row has `is_group=1` and no `edm_id`: resolve via
`get_analysis_by_name_only(analysis.name)` instead of
`get_analysis_by_name(name, edm_name)`. Everything after is unchanged:
`get_analysis_metadata`, stamp `irp_id` + `settings_metadata` +
`status_code='ready'`, chain `retrieve_analysis_results`
(`requestor_type='irp_analysis'`). `retrieve_analysis_results` itself is
untouched — a group's stats/EP are fetched by `analysisId` like any analysis
(T-11 assumption, sandbox-verified).
