"""Dramatiq actors for analysis execution (spec 010, contracts/worker-poller.md).

``execute_analysis_batch`` submits one Risk Modeler analysis per (portfolio,
plan item) — the plan is the approved snapshot from
``analysis_execution_service.request_execution``; this module reads nothing else
(AGENTS.md rule 8). ``backfill_analysis_detail`` resolves a FINISHED analysis by
its exact submitted name and fills in its Risk Modeler detail.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable

import dramatiq
from sqlalchemy import text

from app.services import analysis_execution_service, irp_gateway, irp_job_service
from app.services._common import _utcnow
from app.workers import broker, runtime
from db import execute, execute_command, execute_one, get_connection, is_unique_violation

logger = logging.getLogger(__name__)

_ = broker.redis_broker


def _load_input(rwb_job_id: Any) -> dict:
    row = execute_one("SELECT input_data FROM rwb_job WHERE id = :id",
                      {"id": str(rwb_job_id)}, connection="WORKBENCH")
    if row is None or not row["input_data"]:
        return {}
    return json.loads(row["input_data"])


# ── execute_analysis_batch (US1, T-01/T-04/T-05) ────────────────────────────────

def _claim_analysis(*, edm_id: str, portfolio: dict, item: dict,
                    execution_id: str, actor_id: str | None) -> dict:
    """Resume-or-claim the ``irp_analysis`` row for one work unit
    ``(execution_id, portfolio, item_no)``. A row already claimed (crash between
    the claim and the submit record) is reused with its recorded name; otherwise
    claim a fresh, collision-free name (T-04/T-05) against LIVE names of this EDM."""
    existing = execute_one(
        "SELECT id, name, full_name FROM irp_analysis "
        "WHERE execution_id = :e AND irp_portfolio_id = :p AND execution_item_no = :n",
        {"e": execution_id, "p": portfolio["id"], "n": item["item_no"]},
        connection="WORKBENCH")
    if existing is not None:
        return existing

    full = analysis_execution_service.build_full_name(
        portfolio["name"], item["template_name"])
    attempt = 0
    while True:
        full_name, name = analysis_execution_service.name_attempt(full, attempt)
        taken = execute_one(
            "SELECT 1 FROM irp_analysis "
            "WHERE edm_id = :e AND name = :n AND deleted_at IS NULL",
            {"e": edm_id, "n": name}, connection="WORKBENCH")
        if taken is not None:
            attempt += 1
            continue
        analysis_id = str(uuid.uuid4())
        now = _utcnow()
        try:
            with get_connection("WORKBENCH") as conn, conn.begin():
                conn.execute(text(
                    """
                    INSERT INTO irp_analysis (id, edm_id, irp_portfolio_id,
                        analysis_template_id, execution_id, execution_item_no,
                        name, full_name, status_code, inserted_at, updated_at,
                        inserted_by, updated_by)
                    VALUES (:id, :edm, :portfolio, :template, :execution, :item_no,
                        :name, :full, 'pending', :now, :now, :by, :by)
                    """
                ), {"id": analysis_id, "edm": edm_id, "portfolio": portfolio["id"],
                    "template": item["template_id"], "execution": execution_id,
                    "item_no": item["item_no"], "name": name, "full": full_name,
                    "now": now, "by": actor_id})
        except Exception as exc:  # noqa: BLE001 — a UNIQUE race means try the next suffix
            if is_unique_violation(exc):
                attempt += 1
                continue
            raise
        return {"id": analysis_id, "name": name, "full_name": full_name}


def _submit_one(*, edm_id: str, edm_name: str, execution_id: str, portfolio: dict,
                item: dict, treaty_names: list[str], submission_id: str | None,
                actor_id: str | None) -> str:
    """Per-work-unit submit (contracts/worker-poller.md §2). Returns
    ``"submitted"`` / ``"submission_failed"`` / ``"skipped"`` (already done)."""
    job_count = execute_one(
        "SELECT COUNT(*) AS n FROM irp_job WHERE irp_analysis_id = ("
        "  SELECT id FROM irp_analysis WHERE execution_id = :e "
        "  AND irp_portfolio_id = :p AND execution_item_no = :i)",
        {"e": execution_id, "p": portfolio["id"], "i": item["item_no"]},
        connection="WORKBENCH")
    if job_count and job_count["n"]:
        return "skipped"

    claimed = _claim_analysis(edm_id=edm_id, portfolio=portfolio, item=item,
                              execution_id=execution_id, actor_id=actor_id)
    submit_kwargs = {
        "edm_name": edm_name, "portfolio_name": portfolio["name"],
        "job_name": claimed["name"],
        "analysis_profile_name": item["analysis_profile_name"],
        "output_profile_name": item["output_profile_name"],
        "event_rate_scheme_name": item["event_rate_scheme_name"],
        "treaty_names": treaty_names, "tag_names": item["tag_names"],
        "currency": item["currency"],
        "min_loss_threshold": item["min_loss_threshold"],
        "num_max_loss_event": item["num_max_loss_event"],
        "franchise_deductible": item["franchise_deductible"],
        "treat_construction_occupancy_as_unknown": (
            item["treat_construction_occupancy_as_unknown"]),
    }
    try:
        irp_id, request_body = irp_gateway.submit_portfolio_analysis(**submit_kwargs)
    except Exception as exc:  # noqa: BLE001 — SUBMISSION FAILED, retried by the poller batch
        logger.warning("analysis submit failed for %s: %s", claimed["name"], exc)
        irp_job_service.record_submission_failure(
            irp_job_type="analysis", requested_from_submission_id=submission_id,
            irp_edm_id=edm_id, irp_portfolio_id=portfolio["id"],
            irp_analysis_id=claimed["id"], payload=submit_kwargs,
            request_params=submit_kwargs, actor_id=actor_id)
        execute_command(
            "UPDATE irp_analysis SET failure_reason = :r, updated_at = :now "
            "WHERE id = :id",
            {"r": str(exc), "now": _utcnow(), "id": claimed["id"]},
            connection="WORKBENCH")
        return "submission_failed"

    with get_connection("WORKBENCH") as conn, conn.begin():
        irp_job_service.record_submitted_irp_job(
            irp_job_type="analysis", requested_from_submission_id=submission_id,
            irp_edm_id=edm_id, irp_portfolio_id=portfolio["id"],
            irp_analysis_id=claimed["id"], irp_id=irp_id,
            resource_uri=request_body.get("resourceUri"), payload=submit_kwargs,
            response=request_body, request_params=submit_kwargs,
            actor_id=actor_id, conn=conn)
        conn.execute(text(
            "UPDATE irp_analysis SET status_code = 'running', updated_at = :now "
            "WHERE id = :id"
        ), {"now": _utcnow(), "id": claimed["id"]})
    return "submitted"


def _execute_analysis_batch_body(rwb_job_id: Any) -> runtime.JobResult:
    plan = _load_input(rwb_job_id)
    portfolios = plan.get("portfolios") or []
    items = plan.get("items") or []
    counts = {"submitted": 0, "submission_failed": 0, "skipped": 0}
    for portfolio in portfolios:
        for item in items:
            # Per-item isolation (FR-010/FR-011): one item's failure never stops
            # the loop — outcomes are counted, never raised.
            outcome = _submit_one(
                edm_id=plan["edm_id"], edm_name=plan["edm_name"],
                execution_id=plan["execution_id"], portfolio=portfolio, item=item,
                treaty_names=plan.get("treaty_names") or [],
                submission_id=plan.get("submission_id"),
                actor_id=plan.get("actor_id"))
            counts[outcome] += 1

    total = len(portfolios) * len(items)
    output = {"submitted": counts["submitted"],
             "submission_failed": counts["submission_failed"]}
    if total and counts["submitted"] == 0 and counts["skipped"] == 0:
        return runtime.JobResult.fail("every item failed to submit", **output)
    return runtime.JobResult.ok(**output)


@dramatiq.actor(max_retries=0, time_limit=60 * 60 * 1000)
def execute_analysis_batch(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(__name__),
                    body=lambda: _execute_analysis_batch_body(rwb_job_id))


# ── backfill_analysis_detail (US1, FR-009) ──────────────────────────────────────

def _backfill_analysis_detail_body(rwb_job_id: Any) -> runtime.JobResult:
    """Resolve a FINISHED own-executed analysis by its exact submitted name
    (Article 2 — name-based coupling) and fill in ``irp_id`` + ``settings_metadata``.
    ``exposure_resource_id`` is copied from the ``resourceUri`` already captured at
    submit (``irp_job_resource``) — never re-resolved (T-02)."""
    ctx = _load_input(rwb_job_id)
    analysis_id = ctx.get("analysis_id")
    row = execute_one(
        "SELECT a.name, e.name AS edm_name FROM irp_analysis a "
        "JOIN irp_edm e ON e.id = a.edm_id WHERE a.id = :id",
        {"id": analysis_id}, connection="WORKBENCH") if analysis_id else None
    if row is None:
        return runtime.JobResult.ok(skipped="analysis missing")

    try:
        hit = irp_gateway.get_analysis_by_name(row["name"], row["edm_name"])
        meta = irp_gateway.get_analysis_metadata(analysis_id=int(hit.analysis_id))
    except Exception as exc:  # noqa: BLE001 — resolution failed, recoverable rwb_job failure
        logger.warning("backfill_analysis_detail: resolve failed for %s (%s): %s",
                       analysis_id, row["name"], exc)
        return runtime.JobResult.fail(f"analysis resolve failed: {exc}")

    resource = execute_one(
        "SELECT r.resource_uri FROM irp_job j "
        "JOIN irp_job_resource r ON r.irp_job_id = j.id "
        "WHERE j.irp_analysis_id = :id AND r.resource_type = 'portfolio' "
        "ORDER BY j.inserted_at",
        {"id": analysis_id}, connection="WORKBENCH")

    execute_command(
        "UPDATE irp_analysis SET irp_id = :irp, settings_metadata = :sm, "
        "exposure_resource_id = :x, status_code = 'ready', updated_at = :now "
        "WHERE id = :id",
        {"irp": hit.analysis_id,
         "sm": (json.dumps(meta.payload) if meta.payload else None),
         "x": (resource["resource_uri"] if resource else None),
         "now": _utcnow(), "id": analysis_id},
        connection="WORKBENCH")
    logger.info("backfill_analysis_detail resolved analysis=%s -> irp_id=%s",
               analysis_id, hit.analysis_id)
    return runtime.JobResult.ok(irp_id=hit.analysis_id)


@dramatiq.actor(max_retries=0)
def backfill_analysis_detail(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(__name__),
                    body=lambda: _backfill_analysis_detail_body(rwb_job_id))


# ── synchronous drain (unit tier) ────────────────────────────────────────────────

_BODIES: dict[str, Callable[[Any], runtime.JobResult | dict | None]] = {
    "execute_analysis_batch": _execute_analysis_batch_body,
    "backfill_analysis_detail": _backfill_analysis_detail_body,
}


def run_one(*, rwb_job_id: Any, rwb_job_type: str, worker_id: str = "worker") -> bool:
    body = _BODIES.get(rwb_job_type)
    if body is None:
        logger.debug("no body for rwb_job_type %s — skipping", rwb_job_type)
        return False
    return runtime.run_job(rwb_job_id=rwb_job_id, worker_id=worker_id,
                           body=lambda: body(rwb_job_id))


def run_pending(*, worker_id: str = "worker") -> int:
    """Snapshot-based drain of every currently-``pending`` row this module has a
    body for (unit tier / simple polling worker — same shape as
    ``entity_jobs.run_pending``)."""
    rows = execute(
        "SELECT id, rwb_job_type FROM rwb_job WHERE status_code = 'pending' "
        "ORDER BY inserted_at, id",
        {}, connection="WORKBENCH",
    )
    count = 0
    for row in rows:
        if run_one(rwb_job_id=row["id"], rwb_job_type=row["rwb_job_type"],
                   worker_id=worker_id):
            count += 1
    return count


__all__ = [
    "execute_analysis_batch", "backfill_analysis_detail", "run_one", "run_pending",
]
