"""Validate, enqueue, and read analyst-requested hazard lookups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.services import irp_job_service, rwb_job_service
from app.services._common import _parse_json_dict, _uid
from app.services.errors import GeohazLaunchConflict, InvalidGeohazLaunch
from app.workers import dispatch
from db import execute, execute_one


@dataclass(frozen=True)
class CellState:
    portfolio_id: str
    portfolio_name: str
    label: str
    live: bool


@dataclass(frozen=True)
class LatestLookup:
    request_params: dict[str, Any]
    completion_summary: str | None
    status: str
    submitted_at: Any
    completed_at: Any

    @property
    def failed(self) -> bool:
        return self.status != "FINISHED" and self.status in irp_job_service.TERMINAL


@dataclass(frozen=True)
class PortfolioGeohaz:
    state: CellState
    latest: LatestLookup | None


def read(*, edm_id: Any | None = None,
         portfolio_id: Any | None = None) -> dict[str, PortfolioGeohaz]:
    """Return the derived lookup state and most recent workbench submission for
    every live portfolio in the scope, keyed by portfolio id. Scope is one EDM,
    one portfolio, or one portfolio within one EDM."""
    if portfolio_id is not None:
        where = "p.id = :portfolio_id"
        params: dict[str, str] = {"portfolio_id": str(portfolio_id)}
        if edm_id is not None:
            where += " AND p.edm_id = :edm_id"
            params["edm_id"] = str(edm_id)
    else:
        where = "p.edm_id = :edm_id"
        params = {"edm_id": str(edm_id)}
    terminal = {f"t{i}": s for i, s in enumerate(sorted(irp_job_service.TERMINAL))}
    terminal_in = ", ".join(f":{k}" for k in terminal)
    params |= terminal
    rows = execute(
        f"""
        WITH job_state AS (
            SELECT irp_portfolio_id,
                   MAX(CASE WHEN status NOT IN ({terminal_in})
                       THEN 1 ELSE 0 END) AS has_live_job,
                   MAX(CASE WHEN status NOT IN ({terminal_in})
                       THEN status ELSE NULL END) AS live_status
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
        ), ranked AS (
            SELECT irp_portfolio_id, request_params, completion_summary, status,
                   submitted_at, completed_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY irp_portfolio_id
                       ORDER BY inserted_at DESC, id DESC
                   ) AS recency
            FROM irp_job
            WHERE irp_job_type = 'geohaz'
        )
        SELECT p.id, p.name, p.exposure_detail,
               COALESCE(j.has_live_job, 0) AS has_live_job,
               j.live_status,
               COALESCE(h.has_live_head, 0) AS has_live_head,
               r.request_params, r.completion_summary, r.status AS latest_status,
               r.submitted_at, r.completed_at
        FROM irp_portfolio p
        LEFT JOIN job_state j ON j.irp_portfolio_id = p.id
        LEFT JOIN head_state h ON h.irp_portfolio_id = p.id
        LEFT JOIN ranked r ON r.irp_portfolio_id = p.id AND r.recency = 1
        WHERE p.deleted_at IS NULL AND {where}
        """,
        params,
        connection="WORKBENCH",
    )
    out: dict[str, PortfolioGeohaz] = {}
    for row in rows:
        pid = _uid(row["id"])
        detail = _parse_json_dict(row["exposure_detail"], "exposure_detail") or {}
        metrics = detail.get("metrics") or {}
        hazard_version = metrics.get("hazardVersion")
        label = hazard_version if isinstance(hazard_version, str) else ""
        if row["has_live_head"] and not row["has_live_job"]:
            state = CellState(pid, row["name"], "SUBMITTING", True)
        elif row["has_live_job"]:
            state = CellState(
                pid, row["name"], row["live_status"] or "SUBMITTED", True)
        else:
            state = CellState(pid, row["name"], label, False)
        latest = None
        if row["latest_status"] is not None:
            latest = LatestLookup(
                request_params=(
                    _parse_json_dict(row["request_params"], "request_params") or {}),
                completion_summary=row["completion_summary"],
                status=row["latest_status"],
                submitted_at=row["submitted_at"],
                completed_at=row["completed_at"],
            )
        out[pid] = PortfolioGeohaz(state=state, latest=latest)
    return out


def launch(*, edm_id: Any, portfolio_ids: list[Any], actor_id: Any) -> list[str]:
    """Validate one launch, enqueue one worker job per selected portfolio, and
    return the launched portfolio ids."""
    eid = str(edm_id)
    edm = execute_one(
        "SELECT id, name FROM irp_edm WHERE id = :id AND deleted_at IS NULL",
        {"id": eid}, connection="WORKBENCH")
    if edm is None:
        raise InvalidGeohazLaunch("The EDM no longer exists.")

    states = {pid: pg.state for pid, pg in read(edm_id=eid).items()}
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
        dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="run_geohaz")
    return selected_ids


__all__ = ["CellState", "LatestLookup", "PortfolioGeohaz", "launch", "read"]
