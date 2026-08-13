"""Dramatiq worker for one portfolio hazard-lookup submission."""

from __future__ import annotations

import json
import logging
import socket
from collections.abc import Callable
from typing import Any

import dramatiq

from app.services import irp_gateway, irp_job_service
from app.workers import broker, runtime
from db import execute_one

logger = logging.getLogger(__name__)
_ = broker.redis_broker


def _worker_id() -> str:
    return f"{socket.gethostname()}:{__name__}"


def _load_input(rwb_job_id: Any) -> dict:
    row = execute_one(
        "SELECT input_data FROM rwb_job WHERE id = :id",
        {"id": str(rwb_job_id)}, connection="WORKBENCH")
    if row is None or not row["input_data"]:
        return {}
    return json.loads(row["input_data"])


def _run_geohaz_body(rwb_job_id: Any) -> runtime.JobResult:
    context = _load_input(rwb_job_id)
    params = context["params"]
    payload = {
        "edm_name": context["edm_name"],
        "portfolio_name": context["portfolio_name"],
        "version": params["data_version"],
        "perils": params["perils"],
        "skip_prev_hazard": params["missing_locations"] == "skip",
    }
    try:
        result = irp_gateway.submit_geohaz(**payload)
    except Exception as exc:  # noqa: BLE001 - the failed submit is persisted
        logger.warning(
            "geohaz submit failed for portfolio %s: %s",
            context["irp_portfolio_id"], exc,
        )
        irp_job_id = irp_job_service.record_submission_failure(
            package_id=None,
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
        package_id=None,
        irp_job_type="geohaz",
        irp_edm_id=context["irp_edm_id"],
        irp_portfolio_id=context["irp_portfolio_id"],
        irp_id=result.irp_id,
        resource_uri=result.resource_uri,
        payload=result.payload,
        response=result.response,
        request_params=params,
        actor_id=context["requested_by_user_id"],
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
        worker_id=_worker_id(),
        body=lambda: _run_geohaz_body(rwb_job_id),
    )


_BODIES: dict[str, Callable[[Any], runtime.JobResult]] = {
    "run_geohaz": _run_geohaz_body,
}


__all__ = ["run_geohaz"]
