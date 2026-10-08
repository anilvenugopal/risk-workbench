"""EDM-detail rows shared by the ``backfill_edm_detail`` and
``refresh_portfolios`` worker tests."""

from __future__ import annotations

from app.poller import run as poller
from app.services import edm_service
from app.workers import entity_jobs
from db import execute, execute_one

# Real RM /metrics payloads (sandbox-confirmed shape, data-model §2) — stored
# verbatim under the snapshot's "metrics" namespace.
EXPOSURE_A = {
    "totalAccounts": 1120, "totalLocations": 8240, "totalPolicies": 1180,
    "perilsExposed": "WS, EQ",
    "name": "Primary 2026", "number": "Primary 2026",
    "geocodeVersion": "23.0", "hazardVersion": "23.0",
}
EXPOSURE_B = {
    "totalAccounts": 720, "totalLocations": 3900, "totalPolicies": 760,
    "perilsExposed": "WS, FL",
    "name": "Excess 2026", "number": "Excess 2026",
    "geocodeVersion": "23.0", "hazardVersion": "23.0",
}
SUMMARY_A = {
    "portfolio_name": "Primary 2026",
    "currencies": ["USD"],
    "countries": ["US"],
    "states": ["FL", "LA", "TX"],
    "lines_of_business": ["Commercial"],
}
TREATY_CAT = {
    "treatyId": 1042, "treatyName": "Meridian Property Cat XoL",
    "treatyNumber": "TR-1042", "treatyType": "CATA",
    "attachmentBasis": "L", "attachmentLevel": "PORT",
    "attachmentPoint": 25000000.0, "occurrenceLimit": 100000000.0,
    "percentageRiShare": 20.0, "percentagePlaced": 85.0,
    "premium": 4200000.0, "currency": {"code": "USD"},
    "effectiveDate": "2026-01-01T00:00:00Z", "expirationDate": "2026-12-31T00:00:00Z",
}


def edm_ready(drive, fake, actor, name="EDM") -> str:
    """Import a standalone EDM and drive it to ``ready`` (submit → FINISHED →
    poll). The poller pass also enqueues the ``backfill_edm_detail`` head; the
    caller seeds fake portfolios (before or after — the worker fetches at run
    time) then drains the queue with ``run_pending``. Returns the edm id."""
    res = edm_service.import_edm(name=name, source_file_path=str(drive / "edm1.bak"),
                                 actor_id=actor)
    entity_jobs.run_pending(worker_id="w1")  # submit → irp_job(import_edm, QUEUED)
    row = execute_one(
        "SELECT irp_id FROM irp_job WHERE irp_edm_id=:e AND irp_job_type='import_edm'",
        {"e": res.entity_id}, connection="WORKBENCH")
    fake.finish(str(row["irp_id"]))
    poller.poll_once()  # EDM → ready + exposureId; enqueues backfill_edm_detail
    return res.entity_id


def treaty_rows(edm_id: str) -> list[dict]:
    return execute(
        "SELECT name, irp_id, attributes, as_of FROM irp_treaty "
        "WHERE edm_id=:e ORDER BY name",
        {"e": edm_id}, connection="WORKBENCH")
