"""Unit tests for the poller import-tracking path (US1, T019).

The poller (``app/poller/run.py``) bridges the async boundary with single-status
checks only (Article 11). On an import job's terminal status it mirrors the status in
place, backfills ``irp_id`` + ``completed_at``, and flips the ``irp_edm`` to
``ready``/``error``. ``SUBMISSION FAILED`` (never reached Risk Modeler) is distinct
from an RM-side ``FAILED`` and is never tracked. Driven by the fake IRP.
"""

from __future__ import annotations

import json
import logging
import re

from app.poller import run as poller
from app.services import edm_service, irp_job_service
from app.workers import entity_jobs
from db import execute, execute_command, execute_one
from tests.unit.conftest import edm_with_portfolios as _edm_with_portfolios


def test_geohaz_uses_single_status_getter_and_metadata_refresh():
    assert poller._GETTERS["geohaz"] is poller.irp_gateway.get_geohaz_job
    assert poller._TERMINAL_HANDLERS["geohaz"] is poller._handle_geohaz_terminal
    assert poller._TERMINAL_RESOLVERS["geohaz"] is poller._resolve_geohaz_metadata


def test_geohaz_terminal_stores_summary_and_refreshes_metadata(
    iteration2_db, fake_irp,
):
    edm_id, [portfolio_id] = _edm_with_portfolios(1)
    execute_command(
        "UPDATE irp_edm SET irp_id = 90001 WHERE id = :id",
        {"id": edm_id}, connection="WORKBENCH")
    execute_command(
        "UPDATE irp_portfolio SET exposure_detail = :detail WHERE id = :id",
        {"id": portfolio_id,
         "detail": json.dumps({"metrics": {"hazardVersion": "23.0"},
                               "summary": {"countries": ["US"]},
                               "stamp_date": "2026-08-01T00:00:00Z"})},
        connection="WORKBENCH")
    fake_irp.add_portfolio(
        edm_exposure_id="90001", irp_id="101", name="Portfolio 1",
        exposure={"hazardVersion": "23.0,25.0", "totalLocations": 142})
    job_id = irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_id="25234199",
        irp_edm_id=edm_id, irp_portfolio_id=portfolio_id)
    result = {
        "status": "FINISHED",
        "details": {"summary": "GEOHAZ is successful"},
        "tasks": [{"name": "HAZARD", "output": {
            "summary": "For the Layer : EARTHQUAKE processed 142 Locations out of 142."
        }}],
    }
    fake_irp.finish("25234199", result)

    poller.poll_once()

    job = execute_one(
        "SELECT completion_summary, last_completion_result FROM irp_job WHERE id=:id",
        {"id": job_id}, connection="WORKBENCH")
    assert job["completion_summary"] == result["tasks"][0]["output"]["summary"]
    assert json.loads(job["last_completion_result"]) == result
    portfolio = execute_one(
        "SELECT exposure_detail FROM irp_portfolio WHERE id = :id",
        {"id": portfolio_id}, connection="WORKBENCH")
    detail = json.loads(portfolio["exposure_detail"])
    assert detail["metrics"]["hazardVersion"] == "23.0,25.0"
    assert detail["summary"] == {"countries": ["US"]}
    assert detail["stamp_date"] == "2026-08-01T00:00:00Z"
    # The lookup moved RM's stampDate, so the stored stamp must be re-synced or
    # every later breakout on this portfolio is refused as stale (005 FR-002a).
    backfills = _rwb_jobs_of("backfill_edm_detail")
    assert len(backfills) == 1
    assert edm_id in backfills[0]["input_data"]


def test_failed_geohaz_still_enqueues_the_detail_backfill(
    iteration2_db, fake_irp,
):
    """A failed lookup can still have written part of its hazard data, moving
    the portfolio's stampDate — the re-sync is chained on any terminal."""
    edm_id, [portfolio_id] = _edm_with_portfolios(1)
    execute_command(
        "UPDATE irp_edm SET irp_id = 90001 WHERE id = :id",
        {"id": edm_id}, connection="WORKBENCH")
    irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_id="25234200",
        irp_edm_id=edm_id, irp_portfolio_id=portfolio_id)
    fake_irp.fail("25234200")

    poller.poll_once()

    backfills = _rwb_jobs_of("backfill_edm_detail")
    assert len(backfills) == 1
    assert edm_id in backfills[0]["input_data"]


def _import_and_submit(drive, actor, name="EDM", fname="edm1.bak") -> tuple[str, str]:
    """Import an EDM then run its upload_edm worker body → returns (edm_id, irp_id)."""
    res = edm_service.import_edm(name=name, source_file_path=str(drive / fname),
                                 actor_id=actor)
    entity_jobs.run_pending(worker_id="w1")  # submits → irp_job(import_edm, QUEUED)
    row = execute_one(
        "SELECT irp_id FROM irp_job WHERE irp_edm_id=:e AND irp_job_type='import_edm'",
        {"e": res.entity_id}, connection="WORKBENCH")
    return res.entity_id, str(row["irp_id"])


def test_finished_backfills_exposure_id_and_readies_edm(iteration2_db, fake_irp, drive):
    edm_id, irp_id = _import_and_submit(drive, iteration2_db.user_a, name="EDM")
    assert edm_service.get_edm(edm_id).status == edm_service.IMPORTING
    fake_irp.finish(irp_id)
    poller.poll_once()
    edm = edm_service.get_edm(edm_id)
    assert edm.status == edm_service.READY
    # irp_id is the RM *exposureId* (the durable entity id, resolved by name), NOT the
    # import job id — the two live in different id spaces (see irp_gateway caveat).
    exposure_id = fake_irp.edm_exposure_id("EDM")
    assert exposure_id is not None and exposure_id != irp_id
    assert edm.irp_id == int(exposure_id)
    # the import job's id is recorded separately, as created_by_irp_job_irp_id.
    row = execute_one("SELECT created_by_irp_job_irp_id FROM irp_edm WHERE id=:i",
                      {"i": edm_id}, connection="WORKBENCH")
    assert row["created_by_irp_job_irp_id"] == irp_id
    job = execute_one("SELECT status, completed_at, last_tracked_at FROM irp_job "
                      "WHERE irp_id=:i", {"i": irp_id}, connection="WORKBENCH")
    assert job["status"] == "FINISHED"
    assert job["completed_at"] is not None
    assert job["last_tracked_at"] is not None


def test_failed_terminal_flips_edm_to_error(iteration2_db, fake_irp, drive):
    edm_id, irp_id = _import_and_submit(drive, iteration2_db.user_a)
    fake_irp.fail(irp_id)
    poller.poll_once()
    edm = edm_service.get_edm(edm_id)
    assert edm.status == edm_service.ERROR
    assert edm.irp_id is None  # no id backfilled on failure


def test_submission_failed_is_not_tracked_and_distinct_from_failed(
        iteration2_db, fake_irp, drive):
    # Force the submit to fail → the worker writes SUBMISSION FAILED (no irp_id).
    res = edm_service.import_edm(name="EDM", source_file_path=str(drive / "edm1.bak"),
                                 actor_id=iteration2_db.user_a)
    fake_irp.raise_on_submit = True
    entity_jobs.run_pending(worker_id="w1")
    job = execute_one("SELECT status, irp_id FROM irp_job WHERE irp_edm_id=:e",
                      {"e": res.entity_id}, connection="WORKBENCH")
    assert job["status"] == "SUBMISSION FAILED"
    assert job["irp_id"] is None
    # The poller must not touch it (no irp_id, terminal) — last_tracked_at stays null.
    poller.poll_once()
    after = execute_one("SELECT status, last_tracked_at FROM irp_job WHERE irp_edm_id=:e",
                        {"e": res.entity_id}, connection="WORKBENCH")
    assert after["status"] == "SUBMISSION FAILED"
    assert after["last_tracked_at"] is None


# ── spec 004 (US1, T013): import_edm FINISHED also enqueues backfill_edm_detail ──

def _rwb_jobs_of(rwb_job_type: str) -> list[dict]:
    return execute(
        "SELECT id, requestor_type, requestor_id, rwb_job_type, input_data "
        "FROM rwb_job WHERE rwb_job_type = :t",
        {"t": rwb_job_type}, connection="WORKBENCH")


def test_standalone_edm_import_still_enqueues_backfill_edm_detail(
        iteration2_db, fake_irp, drive):
    """A standalone import gets its detail backfilled after completion."""
    edm_id, irp_id = _import_and_submit(drive, iteration2_db.user_a)
    fake_irp.finish(irp_id)
    poller.poll_once()
    backfills = _rwb_jobs_of("backfill_edm_detail")
    assert len(backfills) == 1
    assert edm_id in backfills[0]["input_data"]


def test_failed_terminal_enqueues_neither_backfill(iteration2_db, fake_irp, drive):
    """A FAILED/CANCELLED terminal flips the EDM to error and enqueues no
    follow-up work — there is no detail to fetch."""
    edm_id, irp_id = _import_and_submit(drive, iteration2_db.user_a)
    fake_irp.fail(irp_id)
    poller.poll_once()
    assert edm_service.get_edm(edm_id).status == edm_service.ERROR
    assert _rwb_jobs_of("backfill_edm_detail") == []


# ── poller business-level logging (#28 follow-up, PR #31) ────────────────────────

def test_transition_logged_once_and_terminal_line_carries_duration(
        iteration2_db, fake_irp, drive, caplog):
    """Business-level poller logs (#28 follow-up): an observed status change logs one
    INFO line, an unchanged status logs nothing at INFO, and the terminal line carries
    the elapsed time since submit."""
    _, irp_id = _import_and_submit(drive, iteration2_db.user_a)
    with caplog.at_level(logging.INFO, logger="app.poller.run"):
        fake_irp.run(irp_id)
        poller.poll_once()
        assert sum("irp_job status: QUEUED -> RUNNING" in r.getMessage()
                   for r in caplog.records) == 1
        caplog.clear()
        poller.poll_once()  # still RUNNING — an observation, not a transition
        assert not any("irp_job status:" in r.getMessage() for r in caplog.records)
        fake_irp.finish(irp_id)
        poller.poll_once()
    terminal = [r.getMessage() for r in caplog.records
                if "irp_job terminal:" in r.getMessage()]
    assert len(terminal) == 1
    assert "RUNNING -> FINISHED" in terminal[0]
    assert re.search(r"\(after \d+[hms]", terminal[0])


def test_every_status_check_logged_at_debug(iteration2_db, fake_irp, drive, caplog):
    _, irp_id = _import_and_submit(drive, iteration2_db.user_a)
    with caplog.at_level(logging.DEBUG, logger="app.poller.run"):
        poller.poll_once()
    checks = [r for r in caplog.records
              if r.levelno == logging.DEBUG
              and "irp_job status check" in r.getMessage()]
    assert len(checks) == 1
    assert any("tracking 1 in-flight irp_job(s)" in r.getMessage()
               for r in caplog.records)
