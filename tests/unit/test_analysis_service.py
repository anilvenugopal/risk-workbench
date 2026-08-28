"""Unit tests for ``analysis_service.list_executed_analyses`` (spec 010 US2, T029).

Covers the read model (own-executed rows only, portfolio join, no RDM
grouping, settings blank-on-missing) and the status label/chip/``is_live``
derivation from the latest tracked ``irp_job`` per analysis (T-07).
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import text

from app.config import settings as app_settings
from app.services import analysis_service
from app.services._common import _utcnow
from db import execute_command, get_connection
from tests.unit.grouping_rows import (
    link_submission_edm,
    seed_group,
    seed_submission,
)

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
              settings=None, template_id=None, irp_id=None,
              irp_app_analysis_id=None, loss_results=None, submitted=None) -> str:
    return _mk(
        "irp_analysis", edm_id=edm_id, irp_portfolio_id=portfolio_id, name=name,
        full_name=(full_name or name), status_code=status_code,
        failure_reason=failure_reason,
        settings_metadata=(json.dumps(settings) if settings else None),
        analysis_template_id=template_id, irp_id=irp_id,
        irp_app_analysis_id=irp_app_analysis_id,
        loss_results=(json.dumps(loss_results) if loss_results else None),
        submitted_settings=(json.dumps(submitted) if submitted else None),
        execution_id=str(uuid.uuid4()), execution_item_no=0)


def _template(name="Template A") -> str:
    return _mk("analysis_template", name=name, analysis_profile_name="Profile",
               output_profile_name="Output")


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


def test_template_name_joined_and_survives_template_soft_delete(iteration2_db):
    edm = _edm()
    template = _template("US HU DLM v23")
    _executed(edm_id=edm, template_id=template)

    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    assert row.template_name == "US HU DLM v23"

    execute_command("UPDATE analysis_template SET deleted_at = :n WHERE id = :i",
                    {"n": _utcnow(), "i": template}, connection="WORKBENCH")
    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    assert row.template_name == "US HU DLM v23"


def test_inserted_at_and_irp_id_populated(iteration2_db):
    edm = _edm()
    _executed(edm_id=edm, irp_id="9001")

    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    assert row.inserted_at is not None
    assert row.irp_id == "9001"


def test_rm_url_needs_irp_app_analysis_id_and_a_configured_rm_ui(iteration2_db, monkeypatch):
    monkeypatch.setattr(app_settings, "risk_modeler_base_url",
                        "https://api-euw1.rms-ppe.com/")
    monkeypatch.setattr(app_settings, "risk_modeler_tenant_name", "acme")
    edm = _edm()
    _executed(edm_id=edm, name="A", irp_id="9001", irp_app_analysis_id="41867")
    _executed(edm_id=edm, name="B")  # not yet backfilled

    rows = {a.name: a for a in analysis_service.list_executed_analyses(edm_id=edm)}
    assert rows["A"].rm_url == (
        "https://acme.rms-ppe.com/riskmodeler/datasources/analysis/41867/0")
    assert rows["B"].rm_url is None

    monkeypatch.setattr(app_settings, "risk_modeler_tenant_name", "")
    rows = {a.name: a for a in analysis_service.list_executed_analyses(edm_id=edm)}
    assert rows["A"].rm_url is None


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
    queued = _executed(edm_id=edm, status_code="pending")
    _job(analysis_id=queued, status="QUEUED")
    running = _executed(edm_id=edm, status_code="pending")
    _job(analysis_id=running, status="RUNNING")

    rows = {a.id: a for a in analysis_service.list_executed_analyses(edm_id=edm)}
    assert rows[queued.lower()].status_label == "Queued"
    assert rows[running.lower()].status_label == "Running"
    for row in rows.values():
        assert row.status_chip == "importing"
        assert row.is_live is True


def test_finished_reads_ready_and_not_live_once_results_stored(iteration2_db):
    edm = _edm()
    # spec 011: a ready row stays live until its retrieval lands (SC-001), so
    # the not-live assertion needs stored results
    analysis = _executed(edm_id=edm, status_code="ready", loss_results=_extract())
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
    analysis = _executed(edm_id=edm, status_code="pending")
    older = _utcnow() - timedelta(minutes=5)
    _job(analysis_id=analysis, status="QUEUED", inserted_at=older)
    _job(analysis_id=analysis, status="RUNNING", inserted_at=_utcnow())

    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    assert row.status_label == "Running"


def test_more_than_2100_analyses_use_each_newest_job(iteration2_db):
    edm = _edm()
    now = _utcnow()
    older = now - timedelta(minutes=5)
    analysis_rows = []
    job_rows = []
    expected_job_ids = set()
    for i in range(2101):
        analysis_id = str(uuid.uuid4())
        old_job_id = str(uuid.uuid4())
        new_job_id = str(uuid.uuid4())
        analysis_rows.append({
            "id": analysis_id, "edm": edm, "name": f"Analysis {i}",
            "execution": str(uuid.uuid4()), "now": now,
        })
        job_rows.extend([
            {"id": old_job_id, "analysis": analysis_id, "status": "QUEUED",
             "attempts": 1, "inserted": older, "updated": older},
            {"id": new_job_id, "analysis": analysis_id, "status": "RUNNING",
             "attempts": 2, "inserted": now, "updated": now},
        ])
        expected_job_ids.add(new_job_id)

    with get_connection("WORKBENCH") as conn, conn.begin():
        conn.execute(text(
            "INSERT INTO irp_analysis (id, edm_id, name, full_name, status_code, "
            "execution_id, execution_item_no, inserted_at, updated_at) "
            "VALUES (:id, :edm, :name, :name, 'pending', :execution, 0, :now, :now)"
        ), analysis_rows)
        conn.execute(text(
            "INSERT INTO irp_job (id, irp_analysis_id, irp_job_type, status, "
            "submission_attempt_count, inserted_at, updated_at) "
            "VALUES (:id, :analysis, 'analysis', :status, :attempts, "
            ":inserted, :updated)"
        ), job_rows)

    rows = analysis_service.list_executed_analyses(edm_id=edm)

    assert len(rows) == 2101
    assert {row.irp_job_id for row in rows} == expected_job_ids
    assert all(row.job_status == "RUNNING" for row in rows)
    assert all(row.submission_attempt_count == 2 for row in rows)


# ── group_key / is_deletable (P-18/P-19) ─────────────────────────────────────


def _one_of_each_state(edm: str) -> dict[str, str]:
    """One analysis per grid-relevant state, keyed by a state label."""
    rows = {
        "failed_run": _executed(edm_id=edm, name="F1", status_code="error"),
        "failed_submit": _executed(edm_id=edm, name="F2", status_code="pending"),
        "submitting": _executed(edm_id=edm, name="P1", status_code="pending"),
        "running": _executed(edm_id=edm, name="R1", status_code="pending"),
        # job FINISHED but the backfill hasn't written irp_id / status ready yet
        "finished_unbackfilled": _executed(edm_id=edm, name="R2",
                                           status_code="pending"),
        "ready": _executed(edm_id=edm, name="D1", status_code="ready",
                           irp_id="9001"),
    }
    _job(analysis_id=rows["failed_run"], status="FAILED")
    _job(analysis_id=rows["failed_submit"], status="SUBMISSION FAILED", attempts=1)
    _job(analysis_id=rows["running"], status="RUNNING")
    _job(analysis_id=rows["finished_unbackfilled"], status="FINISHED")
    _job(analysis_id=rows["ready"], status="FINISHED")
    return {k: v.lower() for k, v in rows.items()}


def test_group_key_truth_table(iteration2_db):
    edm = _edm()
    seeded = _one_of_each_state(edm)
    rows = {a.id: a for a in analysis_service.list_executed_analyses(edm_id=edm)}

    assert rows[seeded["failed_run"]].group_key == "failed"
    assert rows[seeded["failed_submit"]].group_key == "failed"
    assert rows[seeded["submitting"]].group_key == "in_progress"
    assert rows[seeded["running"]].group_key == "in_progress"
    assert rows[seeded["finished_unbackfilled"]].group_key == "in_progress"
    assert rows[seeded["ready"]].group_key == "ready"


def test_is_deletable_truth_table(iteration2_db):
    edm = _edm()
    seeded = _one_of_each_state(edm)
    rows = {a.id: a for a in analysis_service.list_executed_analyses(edm_id=edm)}

    assert rows[seeded["failed_run"]].is_deletable is True
    assert rows[seeded["failed_submit"]].is_deletable is True
    assert rows[seeded["ready"]].is_deletable is True
    assert rows[seeded["submitting"]].is_deletable is False
    assert rows[seeded["running"]].is_deletable is False
    # chip already reads ready (job FINISHED) but the backfill hasn't written
    # irp_id yet — deleting now would orphan the RM analysis.
    assert rows[seeded["finished_unbackfilled"]].is_deletable is False


def test_submission_retrying_is_in_progress_and_not_deletable(iteration2_db):
    edm = _edm()
    analysis = _executed(edm_id=edm, status_code="pending")
    job_id = _job(analysis_id=analysis, status="SUBMISSION RETRYING", attempts=1)

    [row] = analysis_service.list_executed_analyses(edm_id=edm)

    assert row.irp_job_id == job_id
    assert row.group_key == "in_progress"
    assert row.is_live is True
    assert row.is_deletable is False


# ── delete_executed_analyses (P-19) ──────────────────────────────────────────


def _deleted_at(analysis_id: str):
    from db import execute_one
    return execute_one("SELECT deleted_at FROM irp_analysis WHERE id = :i",
                       {"i": analysis_id}, connection="WORKBENCH")["deleted_at"]


def test_delete_cascades_to_rm_then_soft_deletes(iteration2_db, fake_irp):
    edm = _edm()
    analysis = _executed(edm_id=edm, status_code="ready", irp_id="9001")
    _job(analysis_id=analysis, status="FINISHED")

    outcome = analysis_service.delete_executed_analyses(
        edm_id=edm, analysis_ids=[analysis], actor_id=iteration2_db.user_a)

    assert fake_irp.deleted_analyses == ["9001"]
    assert _deleted_at(analysis) is not None
    assert outcome.deleted == 1
    assert outcome.failed == []


def test_delete_without_irp_id_is_local_only(iteration2_db, fake_irp):
    edm = _edm()
    analysis = _executed(edm_id=edm, status_code="pending")
    _job(analysis_id=analysis, status="SUBMISSION FAILED", attempts=1)

    outcome = analysis_service.delete_executed_analyses(
        edm_id=edm, analysis_ids=[analysis], actor_id=iteration2_db.user_a)

    assert fake_irp.deleted_analyses == []
    assert _deleted_at(analysis) is not None
    assert outcome.deleted == 1


def test_delete_rm_failure_keeps_the_row_and_continues(iteration2_db, fake_irp):
    edm = _edm()
    first = _executed(edm_id=edm, name="A", status_code="ready", irp_id="1")
    middle = _executed(edm_id=edm, name="B", status_code="ready", irp_id="2")
    last = _executed(edm_id=edm, name="C", status_code="ready", irp_id="3")
    for analysis in (first, middle, last):
        _job(analysis_id=analysis, status="FINISHED")
    fake_irp.raise_on_delete_analysis.add("2")

    outcome = analysis_service.delete_executed_analyses(
        edm_id=edm, analysis_ids=[first, middle, last],
        actor_id=iteration2_db.user_a)

    assert outcome.deleted == 2
    assert outcome.failed == ["B"]
    assert _deleted_at(first) is not None
    assert _deleted_at(middle) is None  # kept visible for retry
    assert _deleted_at(last) is not None


def test_delete_rejects_a_non_terminal_row(iteration2_db, fake_irp):
    edm = _edm()
    ready = _executed(edm_id=edm, name="A", status_code="ready", irp_id="1")
    _job(analysis_id=ready, status="FINISHED")
    running = _executed(edm_id=edm, name="B", status_code="pending")
    _job(analysis_id=running, status="RUNNING")

    with pytest.raises(ValueError):
        analysis_service.delete_executed_analyses(
            edm_id=edm, analysis_ids=[ready, running],
            actor_id=iteration2_db.user_a)
    # whole batch rejected up front — nothing deleted anywhere
    assert fake_irp.deleted_analyses == []
    assert _deleted_at(ready) is None


def test_delete_rejects_a_row_of_another_edm(iteration2_db, fake_irp):
    edm, other = _edm("E1"), _edm("E2")
    foreign = _executed(edm_id=other, status_code="ready")
    _job(analysis_id=foreign, status="FINISHED")

    with pytest.raises(ValueError):
        analysis_service.delete_executed_analyses(
            edm_id=edm, analysis_ids=[foreign], actor_id=iteration2_db.user_a)
    assert _deleted_at(foreign) is None


def test_delete_rejects_finished_but_unbackfilled_row(iteration2_db, fake_irp):
    edm = _edm()
    analysis = _executed(edm_id=edm, status_code="pending")
    _job(analysis_id=analysis, status="FINISHED")

    with pytest.raises(ValueError):
        analysis_service.delete_executed_analyses(
            edm_id=edm, analysis_ids=[analysis], actor_id=iteration2_db.user_a)
    assert _deleted_at(analysis) is None


def test_delete_keeps_earlier_rows_when_the_poller_claims_a_later_one(
        iteration2_db, fake_irp, monkeypatch):
    """The poller's retry claim lands mid-batch, after the rows were read.

    The claimed row must be reported separately and left alone, without
    discarding the rows the batch already deleted.
    """
    edm = _edm()
    first = _executed(edm_id=edm, name="A", status_code="ready", irp_id="1")
    raced = _executed(edm_id=edm, name="B", status_code="pending")
    raced_job = _job(analysis_id=raced, status="SUBMISSION FAILED", attempts=1)
    _job(analysis_id=first, status="FINISHED")

    snapshot = analysis_service.list_executed_analyses(edm_id=edm)
    execute_command(
        "UPDATE irp_job SET status = 'SUBMISSION RETRYING' WHERE id = :id",
        {"id": raced_job}, connection="WORKBENCH")
    monkeypatch.setattr(analysis_service, "list_executed_analyses",
                        lambda **_: snapshot)

    outcome = analysis_service.delete_executed_analyses(
        edm_id=edm, analysis_ids=[first, raced], actor_id=iteration2_db.user_a)

    assert outcome.deleted == 1
    assert outcome.failed == []
    assert outcome.retrying == ["B"]
    assert _deleted_at(first) is not None
    assert _deleted_at(raced) is None
    assert fake_irp.deleted_analyses == ["1"]


# ── spec 011: results state, extract, and submitted settings (T013) ──────────


_ELEVEN = (5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000)


def _extract(gr_aal=38270.59, gr_std=2645726.19):
    """A stored loss_results document: GR produced with all 11 points, the
    other four perspectives explicitly empty."""
    oep = {str(rp): float(rp) for rp in _ELEVEN}
    aep = {str(rp): float(rp) * 2 for rp in _ELEVEN}
    return {
        "engine_type": "DLM", "engine_version": "23.0",
        "retrieved_at": "2026-08-26T00:00:00Z",
        "perspectives": {
            "GR": {"aal": gr_aal, "std_dev": gr_std, "oep": oep, "aep": aep},
            "RL": None, "WX": None, "QS": None, "GU": None,
        },
    }


def _failed_retrieval(analysis_id: str, detail="RM returned 500 on EP curve (GR)"):
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, "
        "status_code, error_detail) VALUES (:id, 'irp_analysis', :rid, "
        "'retrieve_analysis_results', 'failed', :detail)",
        {"id": str(uuid.uuid4()), "rid": analysis_id, "detail": detail},
        connection="WORKBENCH")


def test_results_ready_carries_all_five_perspectives_in_kind_order(iteration2_db):
    edm = _edm()
    analysis = _executed(edm_id=edm, status_code="ready", irp_id="9001",
                         loss_results=_extract())
    _job(analysis_id=analysis, status="FINISHED")

    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    assert row.results_state == "ready"
    assert row.is_live is False
    assert [(p.code, p.label) for p in row.results] == [
        ("GR", "Gross"), ("RL", "Pre-Cat Net"), ("WX", "Working Excess"),
        ("QS", "Quota Share"), ("GU", "Ground Up")]
    gr = row.results[0]
    assert gr.produced is True
    assert gr.aal == 38270.59
    assert gr.std_dev == 2645726.19
    assert all(p.produced is False for p in row.results[1:])


def test_condensed_filter_keeps_six_points_largest_first(iteration2_db):
    edm = _edm()
    _executed(edm_id=edm, status_code="ready", loss_results=_extract())

    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    gr = row.results[0]
    assert [r["rp"] for r in gr.rows] == [
        "10,000", "1,000", "500", "250", "100", "50"]
    assert [r["oep"] for r in gr.rows] == [10000.0, 1000.0, 500.0, 250.0, 100.0, 50.0]
    assert gr.rows[0]["aep"] == 20000.0
    # display formatting: ≥1M reads in millions, smaller values in full
    assert gr.rows[0]["oep_display"] == "10,000"
    assert gr.std_dev_display == "2.6M"
    assert gr.aal_display == "38,271"


def test_failed_retrieval_reads_failed_with_reason_and_run_stays_finished(
        iteration2_db):
    edm = _edm()
    analysis = _executed(edm_id=edm, status_code="ready", irp_id="9001")
    _job(analysis_id=analysis, status="FINISHED")
    _failed_retrieval(analysis)

    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    assert row.results_state == "failed"
    assert row.results_error == "RM returned 500 on EP curve (GR)"
    assert row.status_chip == "ready"   # SC-005: the run still reads successful
    assert row.is_live is False         # a failed retrieval is terminal (O-06)
    assert row.results == []


def test_pending_results_keep_a_ready_row_live(iteration2_db):
    edm = _edm()
    analysis = _executed(edm_id=edm, status_code="ready", irp_id="9001")
    _job(analysis_id=analysis, status="FINISHED")

    [row] = analysis_service.list_executed_analyses(edm_id=edm)
    assert row.results_state == "pending"
    assert row.is_live is True  # the 3s poll keeps going until the numbers land


def test_currency_reads_from_settings_metadata_or_blank(iteration2_db):
    edm = _edm()
    _executed(edm_id=edm, name="A", settings={"currencyCode": "USD"})
    _executed(edm_id=edm, name="B", settings={"peril": "Windstorm"})

    rows = {a.name: a for a in analysis_service.list_executed_analyses(edm_id=edm)}
    assert rows["A"].display.currency == "USD"
    assert rows["B"].display.currency is None


def test_submitted_settings_parsed_for_display(iteration2_db):
    edm = _edm()
    _executed(edm_id=edm, name="A", submitted={
        "currency": {"code": "USD", "scheme": "RMS", "vintage": "RL25",
                    "asOfDate": "2025-05-28"},
        "min_loss_threshold": 1.0, "franchise_deductible": False,
        "treat_construction_occupancy_as_unknown": True,
    })
    _executed(edm_id=edm, name="B")  # no snapshot — every field blank

    rows = {a.name: a for a in analysis_service.list_executed_analyses(edm_id=edm)}
    assert rows["A"].submitted.construction_occupancy == "Treat as unknown"
    assert rows["B"].submitted.construction_occupancy is None


# ── spec 011 US3: submission-scoped merged read model (T030) ─────────────────


def _submission(user_id: str, name: str = "Deal A") -> str:
    from app.services import submission_service
    return submission_service.create_submission(
        name=name, cedant_name=name, treaty_type_code="cat_xol",
        inception_date="2026-01-01", treaty_year=2026,
        actor_id=user_id, confirmed=True).submission_id


def _attach_edm(submission_id: str, edm_id: str) -> None:
    execute_command(
        "INSERT INTO submission_edm (submission_id, edm_id) VALUES (:s, :e)",
        {"s": submission_id, "e": edm_id}, connection="WORKBENCH")


def test_submission_read_spans_every_edm_with_edm_name(iteration2_db):
    submission = _submission(iteration2_db.user_a)
    coastal, inland, foreign = _edm("Coastal HO"), _edm("Inland HO"), _edm("F")
    _attach_edm(submission, coastal)
    _attach_edm(submission, inland)
    older = _executed(edm_id=coastal, name="A")
    execute_command(
        "UPDATE irp_analysis SET inserted_at = :t WHERE id = :i",
        {"t": _utcnow() - timedelta(minutes=5), "i": older},
        connection="WORKBENCH")
    _executed(edm_id=inland, name="B")
    _executed(edm_id=foreign, name="C")  # EDM not in this submission

    rows = analysis_service.list_submission_executed_analyses(
        submission_id=submission)

    # newest first, with the EDM column value; the foreign EDM's row excluded
    assert [(a.name, a.edm_name) for a in rows] == [
        ("B", "Inland HO"), ("A", "Coastal HO")]


def test_submission_read_derives_origin_from_rdm_id(iteration2_db):
    submission = _submission(iteration2_db.user_a)
    edm = _edm()
    _attach_edm(submission, edm)
    rdm = _mk("irp_rdm", name="R", status="ready")
    own = _executed(edm_id=edm, name="Own")
    _broker(rdm_id=rdm, edm_id=edm, irp_id="9")  # broker handle on the same EDM

    rows = analysis_service.list_submission_executed_analyses(
        submission_id=submission)
    assert [a.id for a in rows] == [own.lower()]


def test_submission_read_carries_results_state_and_job_status(iteration2_db):
    submission = _submission(iteration2_db.user_a)
    edm = _edm()
    _attach_edm(submission, edm)
    analysis = _executed(edm_id=edm, status_code="ready", irp_id="9001",
                         loss_results=_extract())
    _job(analysis_id=analysis, status="FINISHED")

    [row] = analysis_service.list_submission_executed_analyses(
        submission_id=submission)
    assert row.status_label == "Finished"
    assert row.results_state == "ready"
    assert row.results[0].aal == 38270.59


def test_framework_no_longer_competes_with_analysis_mode(iteration2_db):
    edm = _edm()
    _executed(edm_id=edm, name="A", settings={
        "analysisFramework": "ELT", "analysisMode": "Standard"})
    _executed(edm_id=edm, name="B", settings={"analysisFramework": "ELT"})

    rows = {a.name: a for a in analysis_service.list_executed_analyses(edm_id=edm)}
    assert rows["A"].display.framework == "ELT"
    assert rows["A"].display.analysis_mode == "Standard"
    assert rows["B"].display.framework == "ELT"
    assert rows["B"].display.analysis_mode is None


# ── spec 011 US4: the dedicated page's columns (T033) ────────────────────────


def test_results_columns_follow_ids_order_and_count_missing(iteration2_db):
    edm = _edm()
    a = _executed(edm_id=edm, name="A", status_code="ready",
                  loss_results=_extract(), settings={"currencyCode": "USD"})
    b = _executed(edm_id=edm, name="B", status_code="ready")

    columns, missing = analysis_service.list_results_columns(
        analysis_ids=[b, str(uuid.uuid4()), a, "not-a-uuid"])

    assert [c.name for c in columns] == ["B", "A"]
    assert missing == 2
    assert columns[0].results_state == "pending"
    ready = columns[1]
    assert ready.currency == "USD"
    assert ready.results_state == "ready"
    gr = ready.for_code("GR")
    assert gr.produced is True
    assert [r["rp"] for r in gr.rows] == [
        "10,000", "5,000", "2,000", "1,000", "500", "250", "100", "50",
        "25", "10", "5"]
    assert ready.for_code("GU").produced is False


def test_results_columns_broker_row_and_failed_retrieval_join(iteration2_db):
    edm = _edm()
    rdm = _mk("irp_rdm", name="Acme RDM", status="ready")
    broker = _mk("irp_analysis", edm_id=edm, rdm_id=rdm, irp_id="88215",
                 name="FL HU Gross 2026", status_code="ready")
    _failed_retrieval(broker)

    columns, missing = analysis_service.list_results_columns(
        analysis_ids=[broker])

    assert missing == 0
    [col] = columns
    assert col.name == "FL HU Gross 2026"
    assert col.results_state == "failed"
    assert col.results_error == "RM returned 500 on EP curve (GR)"


# ── spec 012 US3: group rows in the merged grid and the results columns (T024) ─


def _settings_cells(analysis) -> str:
    """The Peril · Region · Engine · Currency cells the merged grid renders."""
    env = Environment(loader=FileSystemLoader("app/templates"))
    macros = env.get_template("partials/analysis_row_macros.html").module
    return str(macros.settings_cells(analysis))


def test_submission_grid_group_row_reads_group_with_no_portfolio_or_edm(
        iteration2_db):
    edm = _edm("EDM One")
    submission = seed_submission("Sub One")
    link_submission_edm(submission, edm)
    _executed(edm_id=edm, portfolio_id=_portfolio(edm), name="CRE_P1_T1",
              status_code="ready", settings=SETTINGS_FULL)
    seed_group(submission, "CRE_Sub One_Group")

    rows = {r.name: r for r in
            analysis_service.list_submission_executed_analyses(
                submission_id=submission)}

    group = rows["CRE_Sub One_Group"]
    assert group.is_group is True
    assert (group.portfolio_name, group.template_name, group.edm_name) == (
        None, None, None)
    assert ">Group</span>" in _settings_cells(group)
    assert ">DLM · 23.0</span>" in _settings_cells(rows["CRE_P1_T1"])


def test_results_columns_include_a_group_in_ids_order(iteration2_db):
    edm = _edm()
    submission = seed_submission("Sub One")
    analysis = _executed(edm_id=edm, name="A", status_code="ready",
                         loss_results=_extract(),
                         settings={"currencyCode": "USD"})
    group = _mk("irp_analysis", submission_id=submission, is_group=1,
                name="CRE_Sub One_Group", full_name="CRE_Sub One_Group",
                status_code="ready",
                settings_metadata=json.dumps({"currencyCode": "USD"}),
                loss_results=json.dumps(_extract(gr_aal=91000.0)))

    columns, missing = analysis_service.list_results_columns(
        analysis_ids=[group, analysis])

    assert missing == 0
    assert [c.name for c in columns] == ["CRE_Sub One_Group", "A"]
    group_column = columns[0]
    assert group_column.currency == "USD"
    assert group_column.results_state == "ready"
    assert group_column.for_code("GR").aal == 91000.0


# ── spec 013: ResultsColumn engine and run currency (T-03/T-04) ───────────────


def test_results_column_run_currency_own_row_reads_submitted_snapshot(iteration2_db):
    edm = _edm()
    analysis = _executed(
        edm_id=edm, name="A", status_code="ready",
        settings={"currencyCode": "EUR"},
        submitted={"currency": {"code": "USD", "scheme": "RMS", "vintage": "RL25"}})

    [col], _ = analysis_service.list_results_columns(analysis_ids=[analysis])
    assert col.run_currency == "USD"


def test_results_column_run_currency_broker_row_reads_settings_metadata(iteration2_db):
    edm = _edm()
    rdm = _mk("irp_rdm", name="R", status="ready")
    broker = _mk("irp_analysis", edm_id=edm, rdm_id=rdm, irp_id="88", name="B",
                 status_code="ready",
                 settings_metadata=json.dumps({"currencyCode": "EUR"}))

    [col], _ = analysis_service.list_results_columns(analysis_ids=[broker])
    assert col.run_currency == "EUR"


def test_results_column_run_currency_missing_reads_none(iteration2_db):
    edm = _edm()
    rdm = _mk("irp_rdm", name="R", status="ready")
    # metadata never backfills an own row's run currency (FR-005)
    own = _executed(edm_id=edm, name="A", settings={"currencyCode": "EUR"})
    broker = _mk("irp_analysis", edm_id=edm, rdm_id=rdm, irp_id="89", name="B",
                 status_code="ready")

    columns, _ = analysis_service.list_results_columns(analysis_ids=[own, broker])
    assert [c.run_currency for c in columns] == [None, None]


def test_results_column_engine_reads_the_extract_snapshot_never_metadata(iteration2_db):
    edm = _edm()
    with_extract = _executed(
        edm_id=edm, name="A", status_code="ready",
        settings={"engineType": "IGNORED", "engineVersion": "9.9"},
        loss_results=_extract())
    without_extract = _executed(
        edm_id=edm, name="B", status_code="ready",
        settings={"engineType": "IGNORED", "engineVersion": "9.9"})

    columns, _ = analysis_service.list_results_columns(
        analysis_ids=[with_extract, without_extract])
    assert columns[0].engine == "DLM · 23.0"
    assert columns[1].engine is None


# ── spec 013: the Compare modal's read model and enablement count (T-05) ─────


def _broker_handle(*, rdm_id: str, edm_id: str, irp_id: str, name: str,
                   currency: str | None = "EUR", with_results: bool = False) -> str:
    return _mk(
        "irp_analysis", rdm_id=rdm_id, edm_id=edm_id, irp_id=irp_id, name=name,
        status_code="ready",
        settings_metadata=(json.dumps({"currencyCode": currency})
                           if currency else None),
        loss_results=(json.dumps(_extract()) if with_results else None))


def _comparable_fixture(iteration2_db):
    """One submission, two EDMs, two own rows, two RDMs with one broker
    analysis each — the first with results, the second failed."""
    submission = _submission(iteration2_db.user_a)
    edm_one, edm_two = _edm("EDM One"), _edm("EDM Two")
    _attach_edm(submission, edm_one)
    _attach_edm(submission, edm_two)
    own_old = _executed(edm_id=edm_one, name="Own Old", status_code="ready",
                        loss_results=_extract(),
                        submitted={"currency": {"code": "USD"}})
    execute_command(
        "UPDATE irp_analysis SET inserted_at = :t WHERE id = :i",
        {"t": _utcnow() - timedelta(minutes=5), "i": own_old},
        connection="WORKBENCH")
    own_new = _executed(edm_id=edm_two, name="Own New", status_code="ready",
                        submitted={"currency": {"code": "USD"}})
    rdm_one = _mk("irp_rdm", name="Acme RDM", status="ready")
    rdm_two = _mk("irp_rdm", name="Beta RDM", status="ready")
    for rdm in (rdm_one, rdm_two):
        execute_command(
            "INSERT INTO submission_rdm (submission_id, rdm_id) VALUES (:s, :r)",
            {"s": submission, "r": rdm}, connection="WORKBENCH")
    broker_rep = _broker_handle(rdm_id=rdm_one, edm_id=edm_one, irp_id="9001",
                                name="Broker One", with_results=True)
    broker_failed = _broker_handle(rdm_id=rdm_two, edm_id=edm_one,
                                   irp_id="9002", name="Broker Two",
                                   currency=None)
    _failed_retrieval(broker_failed)
    return SimpleNamespace(
        submission=submission, edm_one=edm_one, edm_two=edm_two,
        own_old=own_old, own_new=own_new, broker_rep=broker_rep)


def test_comparable_analyses_submission_scope_in_table_order(iteration2_db):
    fx = _comparable_fixture(iteration2_db)

    rows = analysis_service.list_comparable_analyses(
        submission_id=fx.submission)

    # own rows newest first, then broker rows grouped by RDM
    assert [(r.name, r.rdm_name, r.run_currency, r.results_state)
            for r in rows] == [
        ("Own New", None, "USD", "pending"),
        ("Own Old", None, "USD", "ready"),
        ("Broker One", "Acme RDM", "EUR", "ready"),
        ("Broker Two", "Beta RDM", None, "failed"),
    ]
    assert rows[1].id == fx.own_old.lower()
    assert rows[2].id == fx.broker_rep.lower()


def test_comparable_analyses_contextual_scope_narrows_own_rows(iteration2_db):
    fx = _comparable_fixture(iteration2_db)

    rows = analysis_service.list_comparable_analyses(
        submission_id=fx.submission, edm_id=fx.edm_one)

    # own rows of the EDM only; the submission's broker groups stay
    assert [r.name for r in rows] == ["Own Old", "Broker One", "Broker Two"]


def test_comparable_analyses_plain_edm_scope_has_no_broker_rows(iteration2_db):
    fx = _comparable_fixture(iteration2_db)

    rows = analysis_service.list_comparable_analyses(edm_id=fx.edm_one)

    assert [r.name for r in rows] == ["Own Old"]


def test_broker_row_currency_reads_the_live_nested_currency_object(iteration2_db):
    # The live get-analysis-by-id payload (2026-08-26) has no flat
    # currencyCode: currency arrives as {currencyName, currencyCode}. Both
    # the modal row and the comparison-page column must still read USD.
    submission = _submission(iteration2_db.user_a)
    edm = _edm()
    _attach_edm(submission, edm)
    rdm = _mk("irp_rdm", name="Live RDM", status="ready")
    execute_command(
        "INSERT INTO submission_rdm (submission_id, rdm_id) VALUES (:s, :r)",
        {"s": submission, "r": rdm}, connection="WORKBENCH")
    broker = _mk(
        "irp_analysis", rdm_id=rdm, edm_id=edm, irp_id="9100",
        name="USFL_Commercial_NT", status_code="ready",
        settings_metadata=json.dumps({
            "currency": {"currencyName": "US Dollar", "currencyCode": "USD"}}),
        loss_results=json.dumps(_extract()))

    rows = analysis_service.list_comparable_analyses(submission_id=submission)
    assert [(r.name, r.run_currency) for r in rows] == [
        ("USFL_Commercial_NT", "USD")]

    columns, _ = analysis_service.list_results_columns(analysis_ids=[broker])
    assert columns[0].run_currency == "USD"


def test_comparable_analyses_gone_scope_reads_none(iteration2_db):
    fx = _comparable_fixture(iteration2_db)
    unrelated_edm = _edm("Unrelated")

    assert analysis_service.list_comparable_analyses(
        submission_id=str(uuid.uuid4())) is None
    assert analysis_service.list_comparable_analyses(
        submission_id=fx.submission, edm_id=unrelated_edm) is None
    assert analysis_service.list_comparable_analyses(
        edm_id=str(uuid.uuid4())) is None
