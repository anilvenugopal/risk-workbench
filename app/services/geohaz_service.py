"""Validate, enqueue, and read analyst-requested hazard lookups."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.services import rwb_job_service
from app.services._common import _parse_json_dict, _uid
from app.services.errors import GeohazLaunchConflict, InvalidGeohazLaunch
from app.workers import dispatch
from db import execute, execute_one

_PERILS = frozenset({"earthquake", "windstorm"})
_TERMINAL_IRP_STATUSES = ("FINISHED", "FAILED", "CANCELLED", "SUBMISSION FAILED")


@dataclass(frozen=True)
class LaunchResult:
    rwb_job_ids: list[str]
    portfolio_ids: list[str]
    request_params: dict[str, Any]


@dataclass(frozen=True)
class CellState:
    portfolio_id: str
    label: str
    kind: str
    live: bool


@dataclass(frozen=True)
class LatestLookup:
    id: str
    request_params: dict[str, Any]
    completion_summary: str | None


def _read_states(*, edm_id: Any | None = None,
                 portfolio_id: Any | None = None) -> dict[str, CellState]:
    if portfolio_id is not None:
        where = "p.id = :portfolio_id"
        params = {"portfolio_id": str(portfolio_id)}
        if edm_id is not None:
            where += " AND p.edm_id = :edm_id"
            params["edm_id"] = str(edm_id)
    else:
        where = "p.edm_id = :edm_id"
        params = {"edm_id": str(edm_id)}
    params.update({
        "finished": _TERMINAL_IRP_STATUSES[0],
        "failed": _TERMINAL_IRP_STATUSES[1],
        "cancelled": _TERMINAL_IRP_STATUSES[2],
        "submit_failed": _TERMINAL_IRP_STATUSES[3],
    })
    rows = execute(
        f"""
        WITH job_state AS (
            SELECT irp_portfolio_id,
                   COUNT(id) AS job_count,
                   MAX(CASE WHEN status = :finished THEN 1 ELSE 0 END)
                       AS has_finished,
                   MAX(CASE WHEN status NOT IN (
                       :finished, :failed, :cancelled, :submit_failed
                   ) THEN 1 ELSE 0 END) AS has_live_job,
                   MAX(CASE WHEN status NOT IN (
                       :finished, :failed, :cancelled, :submit_failed
                   ) THEN status ELSE NULL END) AS live_status
            FROM irp_job
            WHERE irp_job_type = 'geohaz'
            GROUP BY irp_portfolio_id
        ), head_state AS (
            SELECT requestor_id AS irp_portfolio_id,
                   MAX(CASE WHEN status_code IN ('pending', 'running')
                       THEN 1 ELSE 0 END) AS has_live_head
            FROM rwb_job
            WHERE requestor_type = 'analyst_request'
              AND rwb_job_type = 'run_geohaz'
            GROUP BY requestor_id
        )
        SELECT p.id,
               COALESCE(j.job_count, 0) AS job_count,
               COALESCE(j.has_finished, 0) AS has_finished,
               COALESCE(j.has_live_job, 0) AS has_live_job,
               j.live_status,
               COALESCE(h.has_live_head, 0) AS has_live_head
        FROM irp_portfolio p
        LEFT JOIN job_state j ON j.irp_portfolio_id = p.id
        LEFT JOIN head_state h ON h.irp_portfolio_id = p.id
        WHERE p.deleted_at IS NULL AND {where}
        """,
        params,
        connection="WORKBENCH",
    )
    states: dict[str, CellState] = {}
    for row in rows:
        pid = _uid(row["id"])
        if row["has_live_head"] and not row["has_live_job"]:
            state = CellState(pid, "Queued", "live", True)
        elif row["has_live_job"]:
            state = CellState(pid, row["live_status"] or "Queued", "live", True)
        elif row["has_finished"]:
            state = CellState(pid, "Yes", "yes", False)
        elif row["job_count"]:
            state = CellState(pid, "Failed", "failed", False)
        else:
            state = CellState(pid, "No", "no", False)
        states[pid] = state
    return states


def lookup_states(edm_id: Any) -> dict[str, CellState]:
    """Return the derived lookup state for every live portfolio in one EDM."""
    return _read_states(edm_id=edm_id)


def cell_state(portfolio_id: Any, *, edm_id: Any | None = None) -> CellState | None:
    """Return one portfolio's lookup state, or ``None`` if it is gone."""
    return _read_states(
        edm_id=edm_id, portfolio_id=portfolio_id).get(_uid(portfolio_id))


def completion_summary(result: dict[str, Any] | None) -> str | None:
    """Return the GeoHaz task's summary text."""
    if not isinstance(result, dict):
        return None
    tasks = result.get("tasks")
    if not isinstance(tasks, list):
        return None
    for task in tasks:
        if not isinstance(task, dict):
            continue
        output = task.get("output")
        summary = output.get("summary") if isinstance(output, dict) else None
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    return None


def latest_lookup(portfolio_id: Any) -> LatestLookup | None:
    """Return the portfolio's most recent workbench GeoHaz submission."""
    rows = execute(
        """
        WITH ranked AS (
            SELECT j.id, j.request_params, j.completion_summary,
                   ROW_NUMBER() OVER (
                       ORDER BY j.inserted_at DESC, j.id DESC
                   ) AS recency
            FROM irp_job j
            WHERE j.irp_portfolio_id = :portfolio_id
              AND j.irp_job_type = 'geohaz'
        )
        SELECT r.id, r.request_params, r.completion_summary
        FROM ranked r
        WHERE r.recency = 1
        """,
        {"portfolio_id": str(portfolio_id)},
        connection="WORKBENCH",
    )
    if not rows:
        return None
    row = rows[0]
    return LatestLookup(
        id=_uid(row["id"]),
        request_params=(
            _parse_json_dict(row["request_params"], "request_params") or {}),
        completion_summary=row["completion_summary"],
    )


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
    skip_prev_hazard: bool,
    override_user_def: bool,
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
    request_params = {
        "data_version": data_version,
        "model_family": "DLM",
        "perils": selected_perils,
        "skip_prev_hazard": skip_prev_hazard,
        "override_user_def": override_user_def,
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


__all__ = [
    "CellState", "LaunchResult", "LatestLookup", "cell_state", "eligible",
    "completion_summary", "latest_lookup", "launch", "lookup_states",
]
