"""Dramatiq actor for grouping submission (spec 012, contracts/grouping-worker.md).

``submit_grouping`` executes the approved compose plan verbatim (AGENTS.md
rule 8): claim the group ``irp_analysis`` row + its membership rows, make sure
the group name is free tenant-wide, make one ``submit_grouping`` gateway call
with the plan's Platform ids, settings, event-rate and simulation-set
selections, and inspection fingerprint (the package re-inspects and raises typed
problems — T-03), and
record the ``irp_job``. Runs in the worker, never the request path (T-02).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

from app.services import irp_gateway, irp_job_service, rwb_job_service
from app.services._common import _utcnow
from app.workers import broker, runtime
from app.workers.analysis_jobs import name_attempt
from app.workers.queues import rwb_actor
from db import execute_command, execute_one, get_connection, is_unique_violation

logger = logging.getLogger(__name__)

_ = broker.redis_broker

# Bound on total name attempts (claim collisions + duplicate-name retries) —
# past this many suffixes something other than a collision is wrong.
MAX_NAME_ATTEMPTS = 25

INSPECTION_CHANGED_REASON = (
    "The member analyses or reference data changed after inspection. "
    "Reopen the compose dialog and inspect again.")


def _claim_group(plan: dict) -> dict:
    """Resume-or-claim the group ``irp_analysis`` row by the plan's minted
    ``group_analysis_id`` (idempotent on redelivery) plus its
    ``irp_analysis_group_member`` rows. A fresh claim takes the first
    collision-free name against LIVE group names of the submission (T-09)."""
    group_id = plan["group_analysis_id"]
    existing = execute_one(
        "SELECT id, name, full_name FROM irp_analysis WHERE id = :id",
        {"id": group_id}, connection="WORKBENCH")
    claimed = existing
    if claimed is None:
        attempt = 0
        while True:
            if attempt >= MAX_NAME_ATTEMPTS:
                raise RuntimeError(
                    f"no free group name after {MAX_NAME_ATTEMPTS} attempts")
            full_name, name = name_attempt(plan["group_full_name"], attempt)
            taken = execute_one(
                "SELECT 1 FROM irp_analysis WHERE submission_id = :sid "
                "AND name = :n AND deleted_at IS NULL",
                {"sid": plan["submission_id"], "n": name},
                connection="WORKBENCH")
            if taken is not None:
                attempt += 1
                continue
            now = _utcnow()
            try:
                with get_connection("WORKBENCH") as conn, conn.begin():
                    conn.execute(text(
                        """
                        INSERT INTO irp_analysis (id, submission_id, is_group,
                            name, full_name, status_code, submitted_settings,
                            inserted_at, updated_at, inserted_by, updated_by)
                        VALUES (:id, :sid, 1, :name, :full, 'pending',
                            :submitted, :now, :now, :by, :by)
                        """
                    ), {"id": group_id, "sid": plan["submission_id"],
                        "name": name, "full": full_name,
                        "submitted": json.dumps(plan),
                        "now": now, "by": plan.get("actor_id")})
            except Exception as exc:  # noqa: BLE001 — a UNIQUE race means the next suffix
                if is_unique_violation(exc):
                    attempt += 1
                    continue
                raise
            claimed = {"id": group_id, "name": name, "full_name": full_name}
            break
    now = _utcnow()
    for member in plan["members"]:
        present = execute_one(
            "SELECT 1 FROM irp_analysis_group_member "
            "WHERE group_analysis_id = :g AND member_analysis_id = :m",
            {"g": group_id, "m": member["analysis_id"]}, connection="WORKBENCH")
        if present is None:
            execute_command(
                "INSERT INTO irp_analysis_group_member "
                "(group_analysis_id, member_analysis_id, inserted_at) "
                "VALUES (:g, :m, :now)",
                {"g": group_id, "m": member["analysis_id"], "now": now},
                connection="WORKBENCH")
    return dict(claimed)


def _rename_group(plan: dict, group: dict, attempt: int) -> tuple[dict, int]:
    """The duplicate-name retry: move the group row to the next locally-free
    ``_n`` name (bounded by ``MAX_NAME_ATTEMPTS``) and return it."""
    while True:
        attempt += 1
        if attempt >= MAX_NAME_ATTEMPTS:
            raise RuntimeError(
                f"no free group name after {MAX_NAME_ATTEMPTS} attempts")
        full_name, name = name_attempt(plan["group_full_name"], attempt)
        if name == group["name"]:
            continue
        taken = execute_one(
            "SELECT 1 FROM irp_analysis WHERE submission_id = :sid "
            "AND name = :n AND deleted_at IS NULL",
            {"sid": plan["submission_id"], "n": name}, connection="WORKBENCH")
        if taken is not None:
            continue
        try:
            execute_command(
                "UPDATE irp_analysis SET name = :n, full_name = :f, "
                "updated_at = :now WHERE id = :id",
                {"n": name, "f": full_name, "now": _utcnow(),
                 "id": group["id"]}, connection="WORKBENCH")
        except Exception as exc:  # noqa: BLE001 — a UNIQUE race means the next suffix
            if is_unique_violation(exc):
                continue
            raise
        return {**group, "name": name, "full_name": full_name}, attempt


def _grouping_failure_reason(problems) -> str:
    """An analyst-readable reason from the package's structured problems."""
    if any(str(p.code) == "inspection_changed" for p in problems):
        return INSPECTION_CHANGED_REASON
    parts = []
    for p in problems:
        reason = p.message
        if p.partition is not None:
            reason += (f" (partition {p.partition.peril_code} · "
                       f"{p.partition.region_code} · {p.partition.model_version})")
        if p.pet_ids:
            reason += f" (PET IDs {', '.join(str(i) for i in p.pet_ids)})"
        parts.append(reason)
    return "; ".join(parts)


def _submit_grouping_body(rwb_job_id: Any) -> runtime.JobResult:
    plan = rwb_job_service.load_input_data(rwb_job_id)
    group_id = plan["group_analysis_id"]
    job_count = execute_one(
        "SELECT COUNT(*) AS n FROM irp_job WHERE irp_analysis_id = :a",
        {"a": group_id}, connection="WORKBENCH")
    if job_count and job_count["n"]:
        return runtime.JobResult.ok(skipped="already submitted")

    group = _claim_group(plan)
    submit_kwargs = {
        "analysis_ids": [m["irp_id"] for m in plan["members"]],
        "currency": plan["currency"],
        "propagate_detailed_losses": plan["propagate_detailed_losses"],
        "num_of_simulations": plan["num_of_simulations"],
        "event_rate_selections": plan["event_rate_selections"],
        "simulation_set_selections": plan["simulation_set_selections"],
        "simulation_periods_selections": plan["simulation_periods_selections"],
        "expected_inspection_fingerprint": plan["expected_inspection_fingerprint"],
    }
    attempt = 0
    try:
        # The package no longer pre-checks group names tenant-wide, and
        # finalize_analysis resolves the group by name only (T-11).
        while irp_gateway.count_analyses_named(group["name"]) > 0:
            group, attempt = _rename_group(plan, group, attempt)
        irp_id, request_body = irp_gateway.submit_grouping(
            group_name=group["name"], **submit_kwargs)
    except Exception as exc:  # noqa: BLE001 — every submit failure is recorded, none retried
        if isinstance(exc, irp_gateway.IRPGroupingValidationError):
            reason = _grouping_failure_reason(exc.problems)
        else:
            reason = str(exc)
        logger.warning("grouping submit failed for %s: %s", group["name"], reason)
        recorded = {**submit_kwargs, "group_name": group["name"]}
        irp_job_service.record_submission_failure(
            irp_job_type="grouping",
            requested_from_submission_id=plan["submission_id"],
            irp_analysis_id=group_id, payload=recorded,
            request_params=recorded, actor_id=plan.get("actor_id"))
        execute_command(
            "UPDATE irp_analysis SET status_code = 'error', "
            "failure_reason = :r, updated_at = :now WHERE id = :id",
            {"r": reason, "now": _utcnow(), "id": group_id},
            connection="WORKBENCH")
        return runtime.JobResult.fail(reason)

    recorded = {**submit_kwargs, "group_name": group["name"]}
    irp_job_service.record_submitted_irp_job(
        irp_job_type="grouping",
        requested_from_submission_id=plan["submission_id"],
        irp_analysis_id=group_id, irp_id=irp_id,
        payload=request_body, response={"job_id": int(irp_id)},
        request_params=recorded, actor_id=plan.get("actor_id"))
    return runtime.JobResult.ok(irp_id=irp_id, group_name=group["name"])


@rwb_actor(max_retries=0)
def submit_grouping(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _submit_grouping_body(rwb_job_id))


# ── synchronous drain (unit tier) ────────────────────────────────────────────────

_BODIES: runtime.JobBodies = {
    "submit_grouping": _submit_grouping_body,
}


def run_one(*, rwb_job_id: Any, rwb_job_type: str, worker_id: str = "worker") -> bool:
    return runtime.run_one(_BODIES, rwb_job_id=rwb_job_id,
                           rwb_job_type=rwb_job_type, worker_id=worker_id)


def run_pending(*, worker_id: str = "worker") -> int:
    return runtime.run_pending(_BODIES, worker_id=worker_id)


__all__ = ["submit_grouping", "run_one", "run_pending"]
