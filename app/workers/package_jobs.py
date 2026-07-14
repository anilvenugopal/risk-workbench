"""Dramatiq actors for the app-side work queue (Article 10 / worker-poller.md §2).

Each ``rwb_job_type`` has a **body** (the real Risk Modeler work, via ``irp_gateway``)
wrapped by ``runtime.run_job`` — atomic claim → heartbeat → complete. The unit of
work is always the *submit* (or the synchronous RDM delete), never the remote finish;
the poller bridges the async boundary and drives chaining.

Two entrypoints share the bodies:
  • the ``@dramatiq.actor`` wrappers — the real async delivery path;
  • ``run_pending`` — a synchronous drain of currently-``pending`` rows, used by the
    unit tier (drive the queue without Redis) and usable as a simple polling worker.

Every body is **idempotent**: a reconciler re-dispatch or Dramatiq redelivery must
not double-submit. Guards key off entity status (``pending_import`` gates a submit)
and the idempotent ``enqueue_rwb_job`` / existing-``irp_job`` checks.
"""

from __future__ import annotations

import json
import logging
import socket
from datetime import datetime, timezone
from typing import Any, Callable

import dramatiq
from sqlalchemy import text

from app.services import (
    edm_service,
    irp_gateway,
    irp_job_service,
    package_sync_service,
    rdm_service,
    rwb_job_service,
)
from app.workers import broker, dispatch, runtime
from db import execute, execute_one, execute_scalar, get_connection

logger = logging.getLogger(__name__)

# Importing this module registers the actors against the broker configured in
# app.workers.broker (module import side effect — no Redis connection yet).
_ = broker.redis_broker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _worker_id() -> str:
    return f"{socket.gethostname()}:{__name__}"


def _load_input(rwb_job_id: Any) -> dict:
    row = execute_one("SELECT input_data FROM rwb_job WHERE id = :id",
                      {"id": str(rwb_job_id)}, connection="WORKBENCH")
    if row is None or not row["input_data"]:
        return {}
    return json.loads(row["input_data"])


# ── upload_edm (US1) ────────────────────────────────────────────────────────────

def _upload_edm_body(rwb_job_id: Any) -> dict:
    """Submit one EDM import and record the ``irp_job`` (the unit of work is the
    submit). Idempotent: only a ``pending_import`` EDM is submitted, so a redelivery
    or reconciler re-run is a no-op."""
    ctx = _load_input(rwb_job_id)
    edm_id = ctx.get("edm_id")
    package_id = ctx.get("package_id")
    edm = edm_service.get_edm(edm_id) if edm_id else None
    if edm is None:
        return {"skipped": "edm missing"}
    if edm.status != edm_service.PENDING:
        return {"skipped": f"edm status {edm.status}"}  # already submitted/imported

    try:
        res = irp_gateway.submit_edm_import(
            name=edm.name, source_file_path=edm.source_file_path)
    except Exception as exc:  # noqa: BLE001 — submit never reached RM → SUBMISSION FAILED
        logger.warning("upload_edm submit failed for %s: %s", edm_id, exc)
        irp_job_service.record_submission_failure(
            package_id=package_id, irp_job_type="import_edm", irp_edm_id=edm_id,
            payload={"name": edm.name, "source_file_path": edm.source_file_path})
        return {"submit_failed": str(exc)}

    irp_job_id = irp_job_service.record_submitted_irp_job(
        package_id=package_id, irp_job_type="import_edm", irp_edm_id=edm_id,
        irp_id=res.irp_id, resource_uri=res.resource_uri,
        payload=res.payload, response=res.response)
    edm_service.mark_importing(edm_id=edm_id)
    return {"irp_job_id": irp_job_id, "irp_id": res.irp_id}


@dramatiq.actor(max_retries=0)
def upload_edm(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _upload_edm_body(rwb_job_id))


# ── upload_rdm (US2) ─────────────────────────────────────────────────────────────

def _apply_exists(rdm_id: Any, edm_id: Any | None) -> bool:
    """True if an ``import_rdm`` apply already exists for this (RDM, EDM) pair (a
    prior successful submit). Makes the fan-out idempotent per pair across re-runs."""
    if edm_id is None:
        n = execute_scalar(
            "SELECT COUNT(*) FROM irp_job WHERE irp_job_type='import_rdm' "
            "AND irp_rdm_id=:r AND irp_edm_id IS NULL AND status<>'SUBMISSION FAILED'",
            {"r": str(rdm_id)}, connection="WORKBENCH")
    else:
        n = execute_scalar(
            "SELECT COUNT(*) FROM irp_job WHERE irp_job_type='import_rdm' "
            "AND irp_rdm_id=:r AND irp_edm_id=:e AND status<>'SUBMISSION FAILED'",
            {"r": str(rdm_id), "e": str(edm_id)}, connection="WORKBENCH")
    return bool(n)


def _upload_rdm_body(rwb_job_id: Any) -> dict:
    """Fan out one apply per (RDM, EDM) pair — or a single review-only apply with no
    EDM when ``edm_ids`` is empty (FR-002/FR-016). One ``irp_job(import_rdm)`` per
    apply; idempotent per pair. The EDM is name-resolved (Article 2)."""
    ctx = _load_input(rwb_job_id)
    rdm_ids = ctx.get("rdm_ids", [])
    edm_ids = ctx.get("edm_ids", []) or []
    package_id = ctx.get("package_id")
    targets: list[Any] = list(edm_ids) if edm_ids else [None]  # [None] = review-only

    submitted = 0
    for rdm_id in rdm_ids:
        rdm = rdm_service.get_rdm(rdm_id)
        if rdm is None:
            continue
        for edm_id in targets:
            if _apply_exists(rdm_id, edm_id):
                continue
            edm_name = None
            if edm_id is not None:
                edm = edm_service.get_edm(edm_id)
                edm_name = edm.name if edm is not None else None
            try:
                res = irp_gateway.submit_rdm_import(
                    name=rdm.name, source_file_path=rdm.source_file_path,
                    edm_name=edm_name)
            except Exception as exc:  # noqa: BLE001 — SUBMISSION FAILED, not a crash
                logger.warning("upload_rdm submit failed (rdm=%s edm=%s): %s",
                               rdm_id, edm_id, exc)
                irp_job_service.record_submission_failure(
                    package_id=package_id, irp_job_type="import_rdm",
                    irp_edm_id=edm_id, irp_rdm_id=rdm_id,
                    payload={"name": rdm.name, "edm_name": edm_name})
                continue
            irp_job_service.record_submitted_irp_job(
                package_id=package_id, irp_job_type="import_rdm",
                irp_edm_id=edm_id, irp_rdm_id=rdm_id, irp_id=res.irp_id,
                resource_uri=res.resource_uri, payload=res.payload,
                response=res.response)
            rdm_service.mark_importing(rdm_id=rdm_id)
            submitted += 1
    return {"applies_submitted": submitted}


@dramatiq.actor(max_retries=0)
def upload_rdm(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _upload_rdm_body(rwb_job_id))


# ── delete_rdm (US4) — SYNCHRONOUS, no irp_job ───────────────────────────────────

def _delete_rdm_body(rwb_job_id: Any) -> dict:
    """Synchronously delete the RDM's analyses in Risk Modeler (no ``irp_job``, R6),
    mark it ``deleted``, then run the RDM→EDM fan-in: once **all** the package's RDMs
    are ``deleted``, enqueue the ``delete_edm`` heads (or finalize the package if it has
    no EDMs). Idempotent — a duplicate success never double-enqueues."""
    ctx = _load_input(rwb_job_id)
    rdm_id = ctx.get("rdm_id")
    package_id = ctx.get("package_id")
    rdm = rdm_service.get_rdm(rdm_id) if rdm_id else None
    if rdm is None:
        return {"skipped": "rdm missing"}
    if rdm.status != "deleted":
        irp_gateway.delete_rdm_analyses(rdm_name=rdm.name)  # synchronous

    dispatch_edm_ids: list[str] = []
    finalize = False
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            conn.execute(text(
                "UPDATE irp_rdm SET status='deleted', updated_at=:now "
                "WHERE id=:r AND status<>'deleted'"),
                {"now": _utcnow(), "r": str(rdm_id)})
            remaining = conn.execute(text(
                "SELECT COUNT(*) FROM irp_rdm WHERE package_id=:p "
                "AND deleted_at IS NULL AND status<>'deleted'"),
                {"p": str(package_id)}).scalar()
            if not remaining or int(remaining) == 0:
                edms = conn.execute(text(
                    "SELECT id FROM irp_edm WHERE package_id=:p AND deleted_at IS NULL"),
                    {"p": str(package_id)}).mappings().all()
                if edms:
                    for e in edms:
                        eid = str(e["id"])
                        jid = rwb_job_service.enqueue_rwb_job(
                            requestor_type="analyst_request", requestor_id=eid,
                            rwb_job_type="delete_edm",
                            input_data={"edm_id": eid, "package_id": str(package_id)},
                            conn=conn)
                        if jid:
                            dispatch_edm_ids.append(jid)
                else:
                    finalize = True
            if finalize:
                package_sync_service.finalize_package(package_id=package_id, conn=conn)
    for jid in dispatch_edm_ids:
        dispatch.dispatch(rwb_job_id=jid, rwb_job_type="delete_edm")
    return {"deleted_rdm": str(rdm_id)}


@dramatiq.actor(max_retries=0)
def delete_rdm(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _delete_rdm_body(rwb_job_id))


# ── delete_edm (US4) — async (pollable irp_job) ──────────────────────────────────

def _delete_edm_body(rwb_job_id: Any) -> dict:
    """Under the atomic ``delete_pending`` guard, submit the EDM delete and record a
    pollable ``irp_job(delete_edm)``. An EDM never imported to RM (no ``irp_id``) is
    marked ``deleted`` inline and the package finalized (no async op to poll)."""
    ctx = _load_input(rwb_job_id)
    edm_id = ctx.get("edm_id")
    package_id = ctx.get("package_id")
    edm = edm_service.get_edm(edm_id) if edm_id else None
    if edm is None:
        return {"skipped": "edm missing"}
    if not edm_service.claim_for_delete(edm_id=edm_id):
        return {"skipped": "already deleting/deleted"}

    if edm.irp_id is None:
        with get_connection("WORKBENCH") as conn:
            with conn.begin():
                edm_service.set_deleted(conn, edm_id=edm_id)
                package_sync_service.finalize_package(package_id=package_id, conn=conn)
        return {"deleted_edm": str(edm_id), "no_rm": True}

    try:
        res = irp_gateway.submit_delete_edm(edm_irp_id=int(edm.irp_id))
    except Exception as exc:  # noqa: BLE001 — SUBMISSION FAILED, not a crash
        logger.warning("delete_edm submit failed for %s: %s", edm_id, exc)
        irp_job_service.record_submission_failure(
            package_id=package_id, irp_job_type="delete_edm", irp_edm_id=edm_id,
            payload={"edm_irp_id": edm.irp_id})
        return {"submit_failed": str(exc)}

    irp_job_id = irp_job_service.record_submitted_irp_job(
        package_id=package_id, irp_job_type="delete_edm", irp_edm_id=edm_id,
        irp_id=res.irp_id, resource_uri=res.resource_uri, payload=res.payload,
        response=res.response)
    return {"irp_job_id": irp_job_id, "irp_id": res.irp_id}


@dramatiq.actor(max_retries=0)
def delete_edm(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _delete_edm_body(rwb_job_id))


# ── synchronous drain (unit tier + simple worker) ────────────────────────────────

_BODIES: dict[str, Callable[[Any], dict | None]] = {
    "upload_edm": _upload_edm_body,
    "upload_rdm": _upload_rdm_body,
    "delete_rdm": _delete_rdm_body,
    "delete_edm": _delete_edm_body,
}


def run_one(*, rwb_job_id: Any, rwb_job_type: str, worker_id: str = "worker") -> bool:
    """Claim + run a single ``rwb_job`` through its body. Returns ``run_job``'s result
    (``False`` if the row was already claimed / the type has no body yet)."""
    body = _BODIES.get(rwb_job_type)
    if body is None:
        logger.debug("no body for rwb_job_type %s — skipping", rwb_job_type)
        return False
    return runtime.run_job(rwb_job_id=rwb_job_id, worker_id=worker_id,
                           body=lambda: body(rwb_job_id))


def run_pending(*, worker_id: str = "worker") -> int:
    """Claim + run every currently-``pending`` ``rwb_job`` once. Snapshot-based (rows a
    body enqueues are picked up on the next call), so tests advance the queue by
    calling this after each poller pass. Returns the number of rows run."""
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
    "upload_edm", "upload_rdm", "delete_rdm", "delete_edm",
    "run_one", "run_pending",
]
