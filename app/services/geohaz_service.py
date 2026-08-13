"""Validate and enqueue analyst-requested hazard lookups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.services import rwb_job_service
from app.services._common import _uid
from app.services.errors import GeohazLaunchConflict, InvalidGeohazLaunch
from app.workers import dispatch
from db import execute, execute_one

_PERILS = frozenset({"earthquake", "windstorm"})
_MISSING_LOCATIONS = frozenset({"overwrite", "skip"})
_TERMINAL_IRP_STATUSES = ("FINISHED", "FAILED", "CANCELLED", "SUBMISSION FAILED")


@dataclass(frozen=True)
class LaunchResult:
    rwb_job_ids: list[str]
    portfolio_ids: list[str]
    request_params: dict[str, Any]


def eligible(portfolio_id: Any) -> bool:
    """Return whether the portfolio can start another GeoHaz lookup."""
    pid = str(portfolio_id)
    row = execute_one(
        "SELECT CASE WHEN EXISTS ("
        "  SELECT 1 FROM irp_portfolio "
        "  WHERE id = :id AND deleted_at IS NULL"
        ") AND NOT EXISTS ("
        "  SELECT 1 FROM irp_job WHERE irp_portfolio_id = :id "
        "  AND irp_job_type = 'geohaz' "
        "  AND status NOT IN (:finished, :failed, :cancelled, :submit_failed)"
        ") AND NOT EXISTS ("
        "  SELECT 1 FROM rwb_job WHERE requestor_type = 'analyst_request' "
        "  AND requestor_id = :id AND rwb_job_type = 'run_geohaz' "
        "  AND status_code IN ('pending', 'running')"
        ") THEN 1 ELSE 0 END AS is_eligible",
        {
            "id": pid,
            "finished": _TERMINAL_IRP_STATUSES[0],
            "failed": _TERMINAL_IRP_STATUSES[1],
            "cancelled": _TERMINAL_IRP_STATUSES[2],
            "submit_failed": _TERMINAL_IRP_STATUSES[3],
        },
        connection="WORKBENCH",
    )
    return bool(row and row["is_eligible"])


def launch(
    *,
    edm_id: Any,
    portfolio_ids: list[Any],
    data_version: str,
    perils: list[str],
    missing_locations: str,
    actor_id: Any,
) -> LaunchResult:
    """Validate one launch and enqueue one worker job per selected portfolio."""
    eid = str(edm_id)
    edm = execute_one(
        "SELECT id, name FROM irp_edm WHERE id = :id AND deleted_at IS NULL",
        {"id": eid}, connection="WORKBENCH")
    if edm is None:
        raise InvalidGeohazLaunch("The EDM no longer exists.")

    all_portfolios = execute(
        "SELECT id, name FROM irp_portfolio "
        "WHERE edm_id = :edm AND deleted_at IS NULL ORDER BY name",
        {"edm": eid}, connection="WORKBENCH")
    if not all_portfolios:
        raise InvalidGeohazLaunch(
            "Hazard lookup requires at least one portfolio in the EDM.")

    selected_ids = list(dict.fromkeys(_uid(pid) for pid in portfolio_ids if pid))
    if not selected_ids:
        raise InvalidGeohazLaunch("Select at least one portfolio.")

    by_id = {_uid(row["id"]): row for row in all_portfolios}
    if any(pid not in by_id for pid in selected_ids):
        raise InvalidGeohazLaunch(
            "Every selected portfolio must belong to this EDM.")

    ineligible = [by_id[pid]["name"] for pid in selected_ids if not eligible(pid)]
    if ineligible:
        names = ", ".join(ineligible)
        raise GeohazLaunchConflict(
            f"Hazard lookup is already in progress for: {names}.")

    selected_perils = list(dict.fromkeys(perils))
    if not selected_perils:
        raise InvalidGeohazLaunch("Select at least one peril.")
    if any(peril not in _PERILS for peril in selected_perils):
        raise InvalidGeohazLaunch("Select only earthquake or windstorm.")
    if data_version not in settings.geohaz_data_versions:
        raise InvalidGeohazLaunch("Select a configured hazard data version.")
    if missing_locations not in _MISSING_LOCATIONS:
        raise InvalidGeohazLaunch(
            "Missing locations must be overwritten or skipped.")

    request_params = {
        "data_version": data_version,
        "model_family": "DLM",
        "perils": selected_perils,
        "missing_locations": missing_locations,
    }
    actor = str(actor_id)
    job_ids: list[str] = []
    for pid in selected_ids:
        job_id = rwb_job_service.ensure_pending_rwb_job(
            requestor_type="analyst_request",
            requestor_id=pid,
            rwb_job_type="run_geohaz",
            input_data={
                "irp_portfolio_id": pid,
                "irp_edm_id": eid,
                "edm_name": edm["name"],
                "portfolio_name": by_id[pid]["name"],
                "requested_by_user_id": actor,
                "params": request_params,
            },
            actor_id=actor,
        )
        if job_id is not None:
            job_ids.append(job_id)
        dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="run_geohaz")
    return LaunchResult(
        rwb_job_ids=job_ids,
        portfolio_ids=selected_ids,
        request_params=request_params,
    )


__all__ = ["LaunchResult", "eligible", "launch"]
