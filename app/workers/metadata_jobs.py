"""Risk Modeler analysis-reference-data sync worker."""

from __future__ import annotations

import socket
import uuid
from dataclasses import asdict

import dramatiq
from sqlalchemy import text

from app.services import irp_gateway
from app.services._common import _utcnow
from app.workers import broker, runtime
from db import get_connection

_ = broker.redis_broker

_CURRENCY_NAME_MAX_LENGTH = 16


def _worker_id() -> str:
    return f"{socket.gethostname()}:{__name__}"


def _sync_table(conn, *, table: str, key: str, rows: list[dict],
                columns: tuple[str, ...], now) -> None:
    incoming = {row[key]: row for row in rows}
    existing = {
        row[0] for row in conn.execute(text(f"SELECT {key} FROM {table}"))
    }

    updates = []
    inserts = []
    for natural_key, row in incoming.items():
        params = {column: row[column] for column in columns}
        params.update({"natural_key": natural_key, "now": now})
        if natural_key in existing:
            updates.append(params)
        else:
            params["id"] = str(uuid.uuid4())
            inserts.append(params)

    if updates:
        assignments = ", ".join(
            f"{column} = :{column}" for column in columns if column != key)
        conn.execute(text(
            f"UPDATE {table} SET {assignments}, updated_at = :now "
            f"WHERE {key} = :natural_key"
        ), updates)

    if inserts:
        insert_columns = ("id", *columns, "inserted_at", "updated_at")
        values = [f":{column}" for column in insert_columns]
        conn.execute(text(
            f"INSERT INTO {table} ({', '.join(insert_columns)}) "
            f"VALUES ({', '.join(values)})"
        ), [{**params, "inserted_at": now, "updated_at": now}
            for params in inserts])

    stale = [{"natural_key": value} for value in existing - incoming.keys()]
    if stale:
        conn.execute(text(
            f"DELETE FROM {table} WHERE {key} = :natural_key"
        ), stale)


def _sync_irp_metadata_body() -> runtime.JobResult:
    try:
        model_profiles = irp_gateway.list_model_profiles()
        output_profiles = irp_gateway.list_output_profiles()
        event_rate_schemes = irp_gateway.list_event_rate_schemes()
        currencies = irp_gateway.list_currencies()
    except Exception as exc:  # noqa: BLE001 - the job stores the gateway reason
        return runtime.JobResult.fail(f"IRP metadata sync failed: {exc}")

    model_rows = [{**asdict(row), "is_accumulation": False}
                  for row in model_profiles]
    output_rows = [asdict(row) for row in output_profiles]
    scheme_rows = [asdict(row) for row in event_rate_schemes]
    currency_rows = [
        {**asdict(row), "name": row.name[:_CURRENCY_NAME_MAX_LENGTH]}
        for row in currencies
    ]
    now = _utcnow()

    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            _sync_table(
                conn, table="irp_model_profile", key="irp_id", rows=model_rows,
                columns=("irp_id", "name", "is_accumulation",
                         "software_version_code", "peril_code",
                         "model_region_code", "peril", "region",
                         "analysis_type", "rms_default"), now=now)
            _sync_table(
                conn, table="irp_output_profile", key="irp_id", rows=output_rows,
                columns=("irp_id", "name", "rms_default"), now=now)
            _sync_table(
                conn, table="irp_event_rate_scheme", key="irp_id", rows=scheme_rows,
                columns=("irp_id", "name", "peril_code", "model_region_code",
                         "model_version_code", "is_hd"), now=now)
            _sync_table(
                conn, table="irp_currency", key="code", rows=currency_rows,
                columns=("code", "name", "country_name", "symbol"), now=now)

    return runtime.JobResult.ok(
        model_profiles=len(model_rows),
        output_profiles=len(output_rows),
        event_rate_schemes=len(scheme_rows),
        currencies=len(currency_rows),
    )


@dramatiq.actor(max_retries=0)
def sync_irp_metadata(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=_sync_irp_metadata_body)


__all__ = ["sync_irp_metadata"]
