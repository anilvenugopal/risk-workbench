"""Dramatiq worker for one portfolio hazard-lookup submission."""

from __future__ import annotations

import logging
from typing import Any

import dramatiq

from app.services import irp_gateway, irp_job_service
from app.workers import broker, runtime

logger = logging.getLogger(__name__)
_ = broker.redis_broker


def _run_geohaz_body(rwb_job_id: Any) -> runtime.JobResult:
    context = runtime.load_input(rwb_job_id)
    params = context["params"]
    payload = {
        "edm_name": context["edm_name"],
        "portfolio_name": context["portfolio_name"],
        "version": params["data_version"],
        "perils": params["perils"],
        "skip_prev_hazard": params["skip_prev_hazard"],
        "override_user_def": params["override_user_def"],
    }
    try:
        result = irp_gateway.submit_geohaz(**payload)
    except Exception as exc:  # noqa: BLE001 - the failed submit is persisted
        logger.warning(
            "geohaz submit failed for portfolio %s: %s",
            context["irp_portfolio_id"], exc,
        )
        irp_job_id = irp_job_service.record_submission_failure(
            irp_job_type="geohaz",
            irp_edm_id=context["irp_edm_id"],
            irp_portfolio_id=context["irp_portfolio_id"],
            payload=payload,
            request_params=params,
            actor_id=context["requested_by_user_id"],
        )
        return runtime.JobResult.fail(
            f"run_geohaz submit failed: {exc}",
            irp_job_id=irp_job_id,
            submit_failed=str(exc),
        )

    irp_job_id = irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz",
        irp_edm_id=context["irp_edm_id"],
        irp_portfolio_id=context["irp_portfolio_id"],
        irp_id=result.irp_id,
        resource_uri=result.resource_uri,
        payload=result.payload,
        response=result.response,
        request_params=params,
        actor_id=context["requested_by_user_id"],
        status="SUBMITTED",
    )
    logger.info(
        "geohaz submitted for portfolio=%s (irp_id=%s)",
        context["irp_portfolio_id"], result.irp_id,
    )
    return runtime.JobResult.ok(irp_job_id=irp_job_id, irp_id=result.irp_id)


@dramatiq.actor(max_retries=0)
def run_geohaz(rwb_job_id: str) -> None:
    runtime.run_job(
        rwb_job_id=rwb_job_id,
        worker_id=runtime.worker_id(__name__),
        body=lambda: _run_geohaz_body(rwb_job_id),
    )


__all__ = ["run_geohaz"]
