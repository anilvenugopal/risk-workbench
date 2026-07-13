"""The Article-11 bridge — records an async Risk Modeler op as an ``irp_job``.

Written by the **worker** at submit time (``record_submitted_irp_job`` / on failure
``record_submission_failure``) and later updated in place by the **poller** (the
status-mirror transitions live alongside the poller, worker-poller.md §3). The web
layer never writes here.

Every function is a thin per-table statement that optionally accepts an explicit
``conn`` so the caller can span *both* tables in one transaction (a worker completes
its ``rwb_job`` **and** records the ``irp_job`` atomically — contracts/data-access.md).
With no ``conn`` it opens its own transaction. Portable across SQLite / SQL Server.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from db import get_connection


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


@contextmanager
def _txn(conn):
    """Yield a working connection: reuse the caller's (no new transaction) or open
    our own ``get_connection(...) + begin()`` when none was supplied."""
    if conn is not None:
        yield conn
    else:
        with get_connection("WORKBENCH") as owned:
            with owned.begin():
                yield owned


def _insert_irp_job(conn, *, job_id: str, package_id, irp_edm_id, irp_rdm_id,
                    irp_job_type: str, irp_id: str | None, status: str,
                    payload: dict | None, response: dict | None,
                    attempt_count: int, actor_id, now: datetime) -> None:
    conn.execute(text(
        """
        INSERT INTO irp_job (id, package_id, irp_edm_id, irp_rdm_id, irp_job_type,
            irp_id, status, last_submission_payload, last_submission_response,
            submission_attempt_count, submitted_at, inserted_at, updated_at,
            inserted_by, updated_by)
        VALUES (:id, :pkg, :edm, :rdm, :jt, :irp_id, :status, :payload, :response,
            :attempts, :now, :now, :now, :by, :by)
        """
    ), {
        "id": job_id,
        "pkg": (str(package_id) if package_id is not None else None),
        "edm": (str(irp_edm_id) if irp_edm_id is not None else None),
        "rdm": (str(irp_rdm_id) if irp_rdm_id is not None else None),
        "jt": irp_job_type,
        "irp_id": irp_id,
        "status": status,
        "payload": _json(payload),
        "response": _json(response),
        "attempts": attempt_count,
        "now": now,
        "by": (str(actor_id) if actor_id is not None else None),
    })


def record_submitted_irp_job(
    *, package_id: Any | None, irp_job_type: str,
    irp_edm_id: Any | None = None, irp_rdm_id: Any | None = None,
    irp_id: str, resource_uri: str | None = None,
    payload: dict | None = None, response: dict | None = None,
    actor_id: Any | None = None, conn=None,
) -> str:
    """Worker-side: write the ``irp_job`` (status ``QUEUED``, ``irp_id`` set) plus
    any ``irp_job_resource`` (the ``resource_uri`` captured at submit — the
    completion response omits it, R1). Returns the new ``irp_job`` id."""
    job_id = str(uuid.uuid4())
    now = _utcnow()
    with _txn(conn) as c:
        _insert_irp_job(
            c, job_id=job_id, package_id=package_id, irp_edm_id=irp_edm_id,
            irp_rdm_id=irp_rdm_id, irp_job_type=irp_job_type, irp_id=irp_id,
            status="QUEUED", payload=payload, response=response,
            attempt_count=0, actor_id=actor_id, now=now)
        if resource_uri is not None:
            c.execute(text(
                "INSERT INTO irp_job_resource (id, irp_job_id, resource_type, "
                "resource_uri, inserted_at) "
                "VALUES (:id, :jid, 'portfolio', :uri, :now)"
            ), {"id": str(uuid.uuid4()), "jid": job_id, "uri": resource_uri,
                "now": now})
    return job_id


def record_submission_failure(
    *, package_id: Any | None, irp_job_type: str,
    irp_edm_id: Any | None = None, irp_rdm_id: Any | None = None,
    payload: dict | None = None, actor_id: Any | None = None, conn=None,
) -> str:
    """Worker-side: the submit never reached Risk Modeler — write the ``irp_job``
    as terminal ``SUBMISSION FAILED`` with ``irp_id=NULL`` (distinct from an RM-side
    ``FAILED``, FR-029). The poller's ``submission_retry`` batch re-attempts it."""
    job_id = str(uuid.uuid4())
    now = _utcnow()
    with _txn(conn) as c:
        _insert_irp_job(
            c, job_id=job_id, package_id=package_id, irp_edm_id=irp_edm_id,
            irp_rdm_id=irp_rdm_id, irp_job_type=irp_job_type, irp_id=None,
            status="SUBMISSION FAILED", payload=payload, response=None,
            attempt_count=1, actor_id=actor_id, now=now)
    return job_id


__all__ = ["record_submitted_irp_job", "record_submission_failure"]
