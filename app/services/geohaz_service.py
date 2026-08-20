"""Validate, enqueue, and read analyst-requested hazard lookups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.services import rwb_job_service
from app.services._common import _parse_json_dict, _uid
from app.services.errors import GeohazLaunchConflict, InvalidGeohazLaunch
from app.workers import dispatch
from db import execute, execute_one


@dataclass(frozen=True)
class LaunchResult:
    rwb_job_ids: list[str]
    portfolio_ids: list[str]
    request_params: dict[str, Any]


@dataclass(frozen=True)
class CellState:
    portfolio_id: str
    portfolio_name: str
    label: str
    kind: str
    live: bool


@dataclass(frozen=True)
class LatestLookup:
    id: str
    request_params: dict[str, Any]
    completion_summary: str | None
    status: str

    @property
    def failed(self) -> bool:
        return self.status in ("FAILED", "CANCELLED", "SUBMISSION FAILED")


def _scope_clause(*, portfolio_col: str, edm_col: str,
                   edm_id: Any | None, portfolio_id: Any | None,
                   ) -> tuple[str, dict[str, str]]:
    if portfolio_id is not None:
        where = f"{portfolio_col} = :portfolio_id"
        params = {"portfolio_id": str(portfolio_id)}
        if edm_id is not None:
            where += f" AND {edm_col} = :edm_id"
            params["edm_id"] = str(edm_id)
        return where, params
    return f"{edm_col} = :edm_id", {"edm_id": str(edm_id)}


def _read_states(*, edm_id: Any | None = None,
                 portfolio_id: Any | None = None) -> dict[str, CellState]:
    where, params = _scope_clause(
        portfolio_col="p.id", edm_col="p.edm_id",
        edm_id=edm_id, portfolio_id=portfolio_id)
    rows = execute(
        f"""
        WITH job_state AS (
            SELECT irp_portfolio_id,
                   MAX(CASE WHEN status NOT IN (
                       'FINISHED', 'FAILED', 'CANCELLED', 'SUBMISSION FAILED'
                   ) THEN 1 ELSE 0 END) AS has_live_job,
                   MAX(CASE WHEN status NOT IN (
                       'FINISHED', 'FAILED', 'CANCELLED', 'SUBMISSION FAILED'
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
        SELECT p.id, p.name, p.exposure_detail,
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
        detail = _parse_json_dict(row["exposure_detail"], "exposure_detail") or {}
        metrics = detail.get("metrics") or {}
        hazard_version = metrics.get("hazardVersion")
        label = hazard_version if isinstance(hazard_version, str) else ""
        if row["has_live_head"] and not row["has_live_job"]:
            state = CellState(pid, row["name"], "SUBMITTING", "live", True)
        elif row["has_live_job"]:
            state = CellState(
                pid, row["name"], row["live_status"] or "SUBMITTED", "live", True)
        else:
            state = CellState(pid, row["name"], label, "version", False)
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


def _latest_lookups(*, edm_id: Any | None = None,
                    portfolio_id: Any | None = None) -> dict[str, LatestLookup]:
    where, params = _scope_clause(
        portfolio_col="j.irp_portfolio_id", edm_col="j.irp_edm_id",
        edm_id=edm_id, portfolio_id=portfolio_id)
    rows = execute(
        f"""
        WITH ranked AS (
            SELECT j.id, j.irp_portfolio_id, j.request_params,
                   j.completion_summary, j.status,
                   ROW_NUMBER() OVER (
                       PARTITION BY j.irp_portfolio_id
                       ORDER BY j.inserted_at DESC, j.id DESC
                   ) AS recency
            FROM irp_job j
            WHERE j.irp_job_type = 'geohaz' AND {where}
        )
        SELECT r.id, r.irp_portfolio_id, r.request_params,
               r.completion_summary, r.status
        FROM ranked r
        WHERE r.recency = 1
        """,
        params,
        connection="WORKBENCH",
    )
    return {
        _uid(row["irp_portfolio_id"]): LatestLookup(
            id=_uid(row["id"]),
            request_params=(
                _parse_json_dict(row["request_params"], "request_params") or {}),
            completion_summary=row["completion_summary"],
            status=row["status"],
        )
        for row in rows
    }


def latest_lookup(portfolio_id: Any) -> LatestLookup | None:
    """Return the portfolio's most recent workbench GeoHaz submission."""
    return _latest_lookups(portfolio_id=portfolio_id).get(_uid(portfolio_id))


def latest_lookups(edm_id: Any) -> dict[str, LatestLookup]:
    """Return the most recent workbench GeoHaz submission for every portfolio
    in one EDM."""
    return _latest_lookups(edm_id=edm_id)


def launch(*, edm_id: Any, portfolio_ids: list[Any], actor_id: Any) -> LaunchResult:
    """Validate one launch and enqueue one worker job per selected portfolio."""
    eid = str(edm_id)
    edm = execute_one(
        "SELECT id, name FROM irp_edm WHERE id = :id AND deleted_at IS NULL",
        {"id": eid}, connection="WORKBENCH")
    if edm is None:
        raise InvalidGeohazLaunch("The EDM no longer exists.")

    states = _read_states(edm_id=eid)
    if not states:
        raise InvalidGeohazLaunch(
            "Hazard lookup requires at least one portfolio in the EDM.")

    selected_ids = list(dict.fromkeys(_uid(pid) for pid in portfolio_ids if pid))
    if not selected_ids:
        raise InvalidGeohazLaunch("Select at least one portfolio.")

    if any(pid not in states for pid in selected_ids):
        raise InvalidGeohazLaunch(
            "Every selected portfolio must belong to this EDM.")

    live = [states[pid].portfolio_name for pid in selected_ids if states[pid].live]
    if live:
        names = ", ".join(live)
        raise GeohazLaunchConflict(
            f"Hazard lookup is already in progress for: {names}.")

    request_params = {
        "data_version": settings.hazard_data_version,
        "model_family": "DLM",
        "perils": ["earthquake", "windstorm"],
        "skip_prev_hazard": False,
        "override_user_def": True,
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
                "portfolio_name": states[pid].portfolio_name,
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
    "CellState", "LaunchResult", "LatestLookup", "cell_state",
    "completion_summary", "latest_lookup", "latest_lookups", "launch",
    "lookup_states",
]
