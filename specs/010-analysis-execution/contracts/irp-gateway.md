# IRP Gateway Contract: Analysis Execution (spec 010)

`app/services/irp_gateway.py` is the only module importing `irp_integration`
(architecture-guard test). Signatures below are against the active wheel, TestPyPI
`irp-integration==0.6.0`; confirm at implementation (`make irp-status`). New free
functions + `IRPGateway` protocol methods + `_RealGateway` implementations + `FakeIRP`
counterparts (`tests/unit/fakes/fake_irp.py`).

## Submission phase

```python
def submit_portfolio_analysis(
    *, edm_name: str, portfolio_name: str, job_name: str,
    analysis_profile_name: str, output_profile_name: str,
    event_rate_scheme_name: str | None, treaty_names: list[str],
    tag_names: list[str], currency: dict,           # {code, scheme, vintage, asOfDate} — always explicit (T-03)
    min_loss_threshold: float, num_max_loss_event: int,
    franchise_deductible: bool, treat_construction_occupancy_as_unknown: bool,
) -> tuple[str, dict]:
    """client.analysis.submit_portfolio_analysis_job(..., skip_duplicate_check=True).
    Returns (job_id_str, request_body). request_body['resourceUri'] must be stored
    (irp_job_resource) — unrecoverable later. Raises IRPIntegrationError; callers keep
    str(exc) as the failure reason (the wheel re-wraps subclasses as IRPAPIError)."""

def get_analysis_job(irp_id: str) -> JobStatusResult:
    """client.analysis.get_analysis_job(int(irp_id)) — single-status check, poller only.
    Same result shape as get_import_job (status + raw body)."""

def delete_analysis(irp_id: str) -> None:
    """client.analysis.delete_analysis(int(irp_id)) — the Risk Modeler half of the
    analyses-delete cascade (P-19). Request path, synchronous, before the local
    soft delete; raises so a failed delete keeps the row visible for retry."""
```

Never wrapped: `submit_portfolio_analysis_jobs` (drops request bodies, ignores currency
— research T-02), `poll_*_to_completion` (Article 11).

## Backfill phase

No new gateway method: the backfill calls the existing
`get_analysis_metadata(analysis_id=...)` with the `analysisId` the poller extracted
from the FINISHED job body.

## Loss phase

```python
def get_analysis_stats(analysis_irp_id: str, perspective_code: str, exposure_resource_id: str) -> list[dict]
def get_analysis_elt(analysis_irp_id: str, perspective_code: str, exposure_resource_id: str) -> list[dict]
def get_analysis_ep(analysis_irp_id: str, perspective_code: str, exposure_resource_id: str) -> list[dict]
def get_analysis_plt(analysis_irp_id: str, perspective_code: str, exposure_resource_id: str) -> list[dict]
```

All map to `client.analysis.get_*(analysis_id, perspective_code, exposure_resource_id)`;
`perspective_code ∈ {GR, GU, RL}`; raw `list[dict]` passed through; empty list means the
perspective doesn't exist (T-15), never an error. Worker-side only (Article 11) — the web
layer reads `analysis_result_meta` and Parquet, never these.

The `exposure_resource_id` argument is the portfolio `resourceUri` captured at submit,
read from `irp_job_resource` (`resource_type='portfolio'`) — not
`irp_analysis.exposure_resource_id`, which holds RM's numeric `exposureResourceId` for
broker rows (R9/FR-036).

## FakeIRP additions

Per-name programmable outcomes so unit tests cover: successful submit (returns job id +
body with `resourceUri`), submit raising `IRPIntegrationError` (FR-010 path), job status
sequences ending FINISHED / FAILED-with-reason / CANCELLED, `get_analysis_metadata`
by-id resolution (seeded via `add_analysis`, forced failure via
`raise_on_analysis_metadata`), and per-perspective result payloads including the
empty-perspective and HD/PLT cases.
