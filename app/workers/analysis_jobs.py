"""Dramatiq actors for analysis execution (spec 010, contracts/worker-poller.md).

``execute_analysis_batch`` submits one Risk Modeler analysis per (portfolio,
plan item) — the plan is the approved snapshot from
``analysis_execution_service.request_execution``; this module reads nothing else
(AGENTS.md rule 8). ``backfill_analysis_detail`` fills in a FINISHED analysis' Risk
Modeler detail, resolved by the ``analysisId`` the poller extracted from the
completion body, and chains ``retrieve_analysis_results`` (spec 011), which stores
the bounded loss-results extract on ``irp_analysis.loss_results``.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import text

from app.services import irp_gateway, irp_job_service, rwb_job_service
from app.services._common import STORED_RETURN_PERIODS, _utcnow
from app.workers import broker, dispatch, runtime
from app.workers.queues import rwb_actor
from db import execute, execute_command, execute_one, get_connection, is_unique_violation

logger = logging.getLogger(__name__)

_ = broker.redis_broker

# Risk Modeler truncates a submitted analysis name at 64 characters.
NAME_MAX_LEN = 64


def build_full_name(portfolio_name: str, template_name: str) -> str:
    return f"CRE_{portfolio_name}_{template_name}"


def name_attempt(full_name: str, attempt: int) -> tuple[str, str]:
    """The (full_name, submitted_name) pair for collision attempt ``attempt``
    (0 = no suffix; attempt n ≥ 1 gets ``_{n+1}`` — the unsuffixed original is
    implicitly #1). ``submitted_name`` is right-truncated to ``NAME_MAX_LEN``,
    the suffix re-clipping the base so it always fits."""
    if attempt == 0:
        return full_name, full_name[:NAME_MAX_LEN]
    suffix = f"_{attempt + 1}"
    return (full_name + suffix,
            full_name[:NAME_MAX_LEN - len(suffix)] + suffix)


def _claim_analysis(*, edm_id: str, portfolio: dict, item: dict,
                    execution_id: str, actor_id: str | None) -> dict:
    """Resume-or-claim the ``irp_analysis`` row for one work unit
    ``(execution_id, portfolio, item_no)``. A row already claimed (crash between
    the claim and the submit record) is reused with its recorded name; otherwise
    claim a fresh, collision-free name against LIVE names of this EDM."""
    existing = execute_one(
        "SELECT id, name, full_name FROM irp_analysis "
        "WHERE execution_id = :e AND irp_portfolio_id = :p AND execution_item_no = :n",
        {"e": execution_id, "p": portfolio["id"], "n": item["item_no"]},
        connection="WORKBENCH")
    if existing is not None:
        return existing

    full = build_full_name(portfolio["name"], item["template_name"])
    attempt = 0
    while True:
        full_name, name = name_attempt(full, attempt)
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
                        name, full_name, status_code, submitted_settings,
                        inserted_at, updated_at, inserted_by, updated_by)
                    VALUES (:id, :edm, :portfolio, :template, :execution, :item_no,
                        :name, :full, 'pending', :submitted, :now, :now, :by, :by)
                    """
                ), {"id": analysis_id, "edm": edm_id, "portfolio": portfolio["id"],
                    "template": item["template_id"], "execution": execution_id,
                    "item_no": item["item_no"], "name": name, "full": full_name,
                    # The plan item verbatim: the values this run is submitted
                    # with, never re-read from analysis_template later — a template
                    # edit must not change what a finished run reports.
                    "submitted": json.dumps(item),
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

    irp_job_service.record_submitted_irp_job(
        irp_job_type="analysis", requested_from_submission_id=submission_id,
        irp_edm_id=edm_id, irp_portfolio_id=portfolio["id"],
        irp_analysis_id=claimed["id"], irp_id=irp_id,
        resource_uri=request_body.get("resourceUri"), payload=submit_kwargs,
        response=request_body, request_params=submit_kwargs, actor_id=actor_id)
    return "submitted"


def _execute_analysis_batch_body(rwb_job_id: Any) -> runtime.JobResult:
    plan = rwb_job_service.load_input_data(rwb_job_id)
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


@rwb_actor(max_retries=0, time_limit=60 * 60 * 1000)
def execute_analysis_batch(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _execute_analysis_batch_body(rwb_job_id))


def _fail_analysis(analysis_id: str, reason: str) -> runtime.JobResult:
    """End the analysis at ``error`` alongside the failed ``rwb_job``. Its
    ``irp_job`` already reads FINISHED, so leaving ``pending`` would keep the EDM
    page's 3s poll running for a row that is never coming back."""
    execute_command(
        "UPDATE irp_analysis SET status_code = 'error', failure_reason = :r, "
        "updated_at = :now WHERE id = :id",
        {"r": reason, "now": _utcnow(), "id": analysis_id}, connection="WORKBENCH")
    return runtime.JobResult.fail(reason)


def _backfill_analysis_detail_body(rwb_job_id: Any) -> runtime.JobResult:
    """Fill in a FINISHED own-executed analysis' ``irp_id`` (RM's ``analysisId``,
    extracted by the poller from the completion body), ``irp_app_analysis_id`` (the
    web-UI id for deep links) and ``settings_metadata``."""
    ctx = rwb_job_service.load_input_data(rwb_job_id)
    analysis_id = ctx.get("analysis_id")
    row = execute_one(
        "SELECT 1 AS present FROM irp_analysis WHERE id = :id",
        {"id": analysis_id}, connection="WORKBENCH") if analysis_id else None
    if row is None:
        return runtime.JobResult.ok(skipped="analysis missing")

    rm_id = ctx.get("rm_analysis_id")
    if not rm_id:
        return _fail_analysis(analysis_id, "completion payload had no analysisId")

    # Written before the metadata fetch so a fetch failure still leaves the RM
    # pointer behind — delete_executed_analyses needs it to reach the analysis
    # Risk Modeler did create.
    execute_command(
        "UPDATE irp_analysis SET irp_id = :irp, updated_at = :now WHERE id = :id",
        {"irp": str(rm_id), "now": _utcnow(), "id": analysis_id},
        connection="WORKBENCH")

    try:
        meta = irp_gateway.get_analysis_metadata(analysis_id=int(rm_id))
    except Exception as exc:  # noqa: BLE001 — resolution failed, recoverable rwb_job failure
        logger.warning("backfill_analysis_detail: metadata fetch failed for %s "
                       "(analysisId=%s): %s", analysis_id, rm_id, exc)
        return _fail_analysis(analysis_id, f"analysis resolve failed: {exc}")

    irp_app_analysis_id = (meta.payload or {}).get("appAnalysisId")
    execute_command(
        "UPDATE irp_analysis SET irp_app_analysis_id = :app, "
        "settings_metadata = :sm, status_code = 'ready', updated_at = :now "
        "WHERE id = :id",
        {"app": (str(irp_app_analysis_id) if irp_app_analysis_id is not None else None),
         "sm": (json.dumps(meta.payload) if meta.payload else None),
         "now": _utcnow(), "id": analysis_id},
        connection="WORKBENCH")
    # Chain the results retrieval: the queue's UNIQUE key dedups, so a re-fired
    # backfill is a no-op insert.
    retrieval_id = rwb_job_service.enqueue_rwb_job(
        requestor_type="irp_analysis", requestor_id=analysis_id,
        rwb_job_type="retrieve_analysis_results",
        input_data={"analysis_id": analysis_id})
    dispatch.dispatch(rwb_job_id=retrieval_id,
                      rwb_job_type="retrieve_analysis_results")
    logger.info("backfill_analysis_detail resolved analysis=%s -> irp_id=%s",
               analysis_id, rm_id)
    return runtime.JobResult.ok(irp_id=str(rm_id))


@rwb_actor(max_retries=0)
def backfill_analysis_detail(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _backfill_analysis_detail_body(rwb_job_id))


def _curve_points(element: dict | None) -> dict | None:
    """The 11 stored points from one EP-curve element, by exact return-period
    match in ``value.returnPeriods``/``value.positionValues`` (every stored
    target is present in RM's 10,004-point curve). A missing
    point raises, failing the job rather than storing a partial curve."""
    if element is None:
        return None
    value = element.get("value") or {}
    by_period = dict(zip(value.get("returnPeriods") or [],
                         value.get("positionValues") or []))
    return {str(rp): by_period[float(rp)] for rp in STORED_RETURN_PERIODS}


def build_loss_results_extract(*, perspective_codes: list[str],
                               results: dict[str, tuple[list[dict], list[dict]]],
                               settings: dict | None,
                               retrieved_at: str) -> dict:
    """The contracts/loss-results.md document from RM's verbatim row lists.

    ``results`` maps each perspective code to its ``(stats_rows, ep_elements)``.
    Every code in ``perspective_codes`` (the caller's ``analysis_perspective_kind``
    read — this builder holds no code list of its own) is a key; both lists empty
    → explicitly ``null``. ``aal``/``std_dev`` come from the stats row whose
    ``epType`` is ``OEP`` (none → both ``null``); TCE-OEP/TCE-AEP elements are
    discarded. ``settings`` is the analysis metadata payload — engine fields
    absent there are stored as ``null``, never omitted."""
    payload = settings or {}
    perspectives: dict[str, dict | None] = {}
    for code in perspective_codes:
        stats_rows, ep_rows = results.get(code) or ([], [])
        if not stats_rows and not ep_rows:
            perspectives[code] = None
            continue
        stats = next((r for r in stats_rows if r.get("epType") == "OEP"), None)
        by_type = {e.get("epType"): e for e in ep_rows}
        perspectives[code] = {
            "aal": stats.get("purePremium") if stats else None,
            "std_dev": stats.get("totalStdDev") if stats else None,
            "oep": _curve_points(by_type.get("OEP")),
            "aep": _curve_points(by_type.get("AEP")),
        }
    return {
        "engine_type": payload.get("engineType"),
        "engine_version": payload.get("engineVersion"),
        "retrieved_at": retrieved_at,
        "perspectives": perspectives,
    }


def _retrieve_analysis_results_body(rwb_job_id: Any) -> runtime.JobResult:
    """Fetch and store the bounded results extract for one analysis. Idempotent:
    stored results skip; any perspective-call failure fails the job with
    ``loss_results`` untouched — a partially-fetched analysis is never
    persisted."""
    ctx = rwb_job_service.load_input_data(rwb_job_id)
    analysis_id = ctx.get("analysis_id")
    row = execute_one(
        "SELECT a.id, a.irp_id, a.rdm_id, a.loss_results, a.settings_metadata, "
        "a.exposure_resource_id, p.irp_id AS portfolio_irp_id "
        "FROM irp_analysis a "
        "LEFT JOIN irp_portfolio p ON p.id = a.irp_portfolio_id "
        "WHERE a.id = :id",
        {"id": analysis_id}, connection="WORKBENCH") if analysis_id else None
    if row is None:
        return runtime.JobResult.ok(skipped="analysis missing")
    if row["loss_results"] is not None:
        return runtime.JobResult.ok(skipped="results already stored")
    if row["irp_id"] is None:
        return runtime.JobResult.fail("analysis has no RM id")

    settings = (json.loads(row["settings_metadata"])
                if row["settings_metadata"] else None)
    # Own rows point at the RM portfolio the analysis ran against; broker rows
    # (rdm_id set) at RM's own reported pointer captured at RDM backfill.
    # One metadata re-read when the pointer is NULL (also filling the engine
    # fields when settings_metadata is NULL too).
    pointer = (row["exposure_resource_id"] if row["rdm_id"] is not None
               else row["portfolio_irp_id"])
    if pointer is None:
        try:
            meta = irp_gateway.get_analysis_metadata(analysis_id=int(row["irp_id"]))
        except Exception as exc:  # noqa: BLE001 — recoverable rwb_job failure
            return runtime.JobResult.fail(f"analysis metadata re-read failed: {exc}")
        pointer = meta.exposure_resource_id
        if settings is None and meta.payload:
            settings = meta.payload
    if pointer is None:
        return runtime.JobResult.fail("no exposure pointer")

    codes = [r["code"] for r in execute(
        "SELECT code FROM analysis_perspective_kind ORDER BY sort_order",
        {}, connection="WORKBENCH")]
    results: dict[str, tuple[list[dict], list[dict]]] = {}
    stats_counts: dict[str, int] = {}
    for code in codes:
        try:
            stats_rows = irp_gateway.get_analysis_stats(
                analysis_id=int(row["irp_id"]), perspective_code=code,
                exposure_resource_id=int(pointer))
            ep_rows = irp_gateway.get_analysis_ep(
                analysis_id=int(row["irp_id"]), perspective_code=code,
                exposure_resource_id=int(pointer))
        except Exception as exc:  # noqa: BLE001 — no partial write
            return runtime.JobResult.fail(f"results read failed for {code}: {exc}")
        results[code] = (stats_rows, ep_rows)
        stats_counts[code] = len(stats_rows)

    doc = build_loss_results_extract(
        perspective_codes=codes, results=results, settings=settings,
        retrieved_at=_utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
    execute_command(
        "UPDATE irp_analysis SET loss_results = :doc, updated_at = :now "
        "WHERE id = :id",
        {"doc": json.dumps(doc), "now": _utcnow(), "id": row["id"]},
        connection="WORKBENCH")
    produced = sum(1 for v in doc["perspectives"].values() if v is not None)
    # stats_rows lands in rwb_job.output_data so a response carrying more than
    # one stats row is a queryable fact, not a guess.
    return runtime.JobResult.ok(perspectives_with_data=produced,
                                stats_rows=stats_counts)


@rwb_actor(max_retries=0)
def retrieve_analysis_results(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _retrieve_analysis_results_body(rwb_job_id))


# ── synchronous drain (unit tier) ────────────────────────────────────────────────

_BODIES: runtime.JobBodies = {
    "execute_analysis_batch": _execute_analysis_batch_body,
    "backfill_analysis_detail": _backfill_analysis_detail_body,
    "retrieve_analysis_results": _retrieve_analysis_results_body,
}


def run_one(*, rwb_job_id: Any, rwb_job_type: str, worker_id: str = "worker") -> bool:
    return runtime.run_one(_BODIES, rwb_job_id=rwb_job_id,
                           rwb_job_type=rwb_job_type, worker_id=worker_id)


def run_pending(*, worker_id: str = "worker") -> int:
    return runtime.run_pending(_BODIES, worker_id=worker_id)


__all__ = [
    "execute_analysis_batch", "backfill_analysis_detail",
    "retrieve_analysis_results", "build_loss_results_extract",
    "run_one", "run_pending",
]
