"""Unit tests for ``analysis_service.list_executed_analyses`` (spec 010 US2, T029).

Covers the read model (own-executed rows only, portfolio join, no RDM
grouping, settings blank-on-missing) and the status label/chip/``is_live``
derivation from the latest tracked ``irp_job`` per analysis (T-07).
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

from app.config import settings as app_settings
from app.services import analysis_service
from app.services._common import _utcnow
from db import execute_command

SETTINGS_FULL = {
    "analysisType": "Exceedance Probability", "engineType": "DLM",
    "engineVersion": "23.0", "peril": "Windstorm", "region": "North America",
    "currencyCode": "USD",
}


def _mk(table: str, **cols) -> str:
    row_id = cols.pop("id", str(uuid.uuid4()))
    now = _utcnow()
    keys = ["id", *cols.keys(), "inserted_at", "updated_at"]
    execute_command(
        f"INSERT INTO {table} ({', '.join(keys)}) "
        f"VALUES ({', '.join(':' + k for k in keys)})",
        {"id": row_id, **cols, "inserted_at": now, "updated_at": now},
        connection="WORKBENCH")
    return row_id


def _edm(name: str = "E") -> str:
    return _mk("irp_edm", name=name, status="ready")


def _portfolio(edm_id: str, name: str = "Portfolio A") -> str:
    return _mk("irp_portfolio", edm_id=edm_id, name=name)


def _executed(*, edm_id: str, portfolio_id: str | None = None, name="Portfolio A Template A",
              full_name=None, status_code="pending", failure_reason=None,
              settings=None) -> str:
    return _mk(
        "irp_analysis", edm_id=edm_id, irp_portfolio_id=portfolio_id, name=name,
        full_name=(full_name or name), status_code=status_code,
        failure_reason=failure_reason,
        settings_metadata=(json.dumps(settings) if settings else None),
        execution_id=str(uuid.uuid4()), execution_item_no=0)


def _broker(*, rdm_id: str, edm_id: str, irp_id: str) -> str:
    return _mk("irp_analysis", rdm_id=rdm_id, edm_id=edm_id, irp_id=irp_id,
              name="Broker analysis", status_code="ready")


def _job(*, analysis_id: str, status: str, attempts: int = 0, inserted_at=None) -> str:
    row_id = str(uuid.uuid4())
    now = inserted_at or _utcnow()
    execute_command(
        "INSERT INTO irp_job (id, irp_analysis_id, irp_job_type, status, "
        "submission_attempt_count, inserted_at, updated_at) VALUES "
        "(:id, :aid, 'analysis', :status, :attempts, :now, :now)",
        {"id": row_id, "aid": analysis_id, "status": status, "attempts": attempts,
         "now": now}, connection="WORKBENCH")
    return row_id


# ── read model ────────────────────────────────────────────────────────────────

def test_only_own_executed_rows_of_this_edm(iteration2_db):
    edm, other_edm, rdm = _edm("E1"), _edm("E2"), _mk("irp_rdm", name="R", status="ready")
    mine = _executed(edm_id=edm)
    _executed(edm_id=other_edm)          # a different EDM's own-executed row
    _broker(rdm_id=rdm, edm_id=edm, irp_id="9")  # a broker row on THIS edm

    rows = analysis_service.list_executed_analyses(edm_id=edm)
    assert [a.id for a in rows] == [mine.lower()]


def test_deleted_rows_excluded(iteration2_db):
    edm = _edm()
    deleted = _executed(edm_id=edm)
    execute_command("UPDATE irp_analysis SET deleted_at = :n WHERE id = :i",
                    {"n": _utcnow(), "i": deleted}, connection="WORKBENCH")

    assert analysis_service.list_executed_analyses(edm_id=edm) == []


def test_portfolio_name_joined(iteration2_db):
    edm = _edm()
    portfolio = _portfolio(edm, name="US Southeast Wind")
    _executed(edm_id=edm, portfolio_id=portfolio)

    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    assert row.portfolio_name == "US Southeast Wind"


def test_settings_parsed_or_blank_never_error(iteration2_db):
    edm = _edm()
    _executed(edm_id=edm, name="A", full_name="A", settings=SETTINGS_FULL)
    _executed(edm_id=edm, name="B", full_name="B", settings=None)  # not yet backfilled

    rows = {a.full_name: a for a in analysis_service.list_executed_analyses(edm_id=edm)}
    assert rows["A"].display.analysis_type == "Exceedance Probability"
    assert rows["A"].display.engine_type == "DLM"
    assert rows["B"].settings is None
    assert rows["B"].display.analysis_type is None


# ── status derivation (T-07) ─────────────────────────────────────────────────

def test_no_job_yet_reads_submitting(iteration2_db):
    edm = _edm()
    _executed(edm_id=edm, status_code="pending")

    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    assert row.job_status is None
    assert row.status_label == "Submitting…"
    assert row.status_chip == "importing"
    assert row.is_live is True


def test_queued_and_running_are_live_importing(iteration2_db):
    edm = _edm()
    queued = _executed(edm_id=edm, status_code="running")
    _job(analysis_id=queued, status="QUEUED")
    running = _executed(edm_id=edm, status_code="running")
    _job(analysis_id=running, status="RUNNING")

    rows = {a.id: a for a in analysis_service.list_executed_analyses(edm_id=edm)}
    assert rows[queued.lower()].status_label == "Queued"
    assert rows[running.lower()].status_label == "Running"
    for row in rows.values():
        assert row.status_chip == "importing"
        assert row.is_live is True


def test_finished_reads_ready_and_not_live(iteration2_db):
    edm = _edm()
    analysis = _executed(edm_id=edm, status_code="ready")
    _job(analysis_id=analysis, status="FINISHED")

    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    assert row.status_label == "Finished"
    assert row.status_chip == "ready"
    assert row.is_live is False


def test_failed_and_cancelled_read_error_with_reason(iteration2_db):
    edm = _edm()
    failed = _executed(edm_id=edm, status_code="error",
                       failure_reason="No locations match the criteria")
    _job(analysis_id=failed, status="FAILED")
    cancelled = _executed(edm_id=edm, status_code="error",
                          failure_reason="Cancelled in Risk Modeler")
    _job(analysis_id=cancelled, status="CANCELLED")

    rows = {a.id: a for a in analysis_service.list_executed_analyses(edm_id=edm)}
    assert rows[failed.lower()].status_label == "Failed"
    assert rows[failed.lower()].status_chip == "error"
    assert rows[failed.lower()].failure_reason == "No locations match the criteria"
    assert rows[cancelled.lower()].status_label == "Cancelled"
    assert rows[cancelled.lower()].is_live is False


def test_submission_failed_shows_attempt_count_and_stays_live_while_retrying(iteration2_db):
    edm = _edm()
    analysis = _executed(edm_id=edm, status_code="pending",
                         failure_reason="Risk Modeler unreachable")
    _job(analysis_id=analysis, status="SUBMISSION FAILED", attempts=2)

    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    assert row.status_label == (
        f"Failed to submit · attempt 2/{app_settings.irp_submission_max_retries}")
    assert row.status_chip == "submission-failed"
    assert row.is_live is True  # status_code stays pending while retries remain


def test_submission_failed_exhausted_flips_error_but_label_unchanged(iteration2_db):
    edm = _edm()
    max_retries = app_settings.irp_submission_max_retries
    analysis = _executed(edm_id=edm, status_code="error",
                         failure_reason="Risk Modeler unreachable")
    _job(analysis_id=analysis, status="SUBMISSION FAILED", attempts=max_retries)

    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    assert row.status_label == f"Failed to submit · attempt {max_retries}/{max_retries}"
    assert row.is_live is False


def test_latest_job_wins_when_more_than_one_row(iteration2_db):
    edm = _edm()
    analysis = _executed(edm_id=edm, status_code="running")
    older = _utcnow() - timedelta(minutes=5)
    _job(analysis_id=analysis, status="QUEUED", inserted_at=older)
    _job(analysis_id=analysis, status="RUNNING", inserted_at=_utcnow())

    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    assert row.status_label == "Running"
