"""Unit tests for Retry (spec 014 T039): the decision tree, the preconditions,
the reset before a new Risk Modeler request, and the idempotent re-arm."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.services import export_service as svc
from tests.unit.export_rows import (
    manifest_row,
    rwb_jobs,
    seed_analysis,
    seed_edm_for,
    seed_export_job,
    seed_manifest,
    seed_submission,
)

NOW = datetime(2026, 9, 10, 12, 0, 0)


def _m(**kw):
    base = {"stage_status": "failed", "load_status": "pending", "zip_file": None}
    base.update(kw)
    return base


def test_staged_row_retries_the_load():
    assert svc.retry_decision(_m(stage_status="staged", load_status="failed"), None,
                              "/nowhere", NOW) == "load"


def test_archive_on_the_share_retries_the_stage(tmp_path):
    (tmp_path / "e" / "a").mkdir(parents=True)
    (tmp_path / "e" / "a" / "x.zip").write_bytes(b"zip")
    assert svc.retry_decision(_m(zip_file="e/a/x.zip"), {"status": "FAILED"},
                              str(tmp_path), NOW) == "stage"


def test_archive_missing_but_finished_within_seven_days_retries_the_stage(tmp_path):
    job = {"status": "FINISHED", "completed_at": NOW - timedelta(days=6)}
    assert svc.retry_decision(_m(zip_file="e/a/gone.zip"), job, str(tmp_path), NOW) == "stage"
    job = {"status": "FINISHED", "completed_at": (NOW - timedelta(days=6)).isoformat(sep=" ")}
    assert svc.retry_decision(_m(), job, str(tmp_path), NOW) == "stage"


def test_finished_over_seven_days_ago_or_failed_submits_again(tmp_path):
    old = {"status": "FINISHED", "completed_at": NOW - timedelta(days=8)}
    assert svc.retry_decision(_m(), old, str(tmp_path), NOW) == "submit"
    assert svc.retry_decision(_m(), {"status": "FAILED", "completed_at": NOW}, str(tmp_path),
                              NOW) == "submit"
    assert svc.retry_decision(_m(), None, "", NOW) == "submit"


def test_archive_on_the_share_without_a_job_submits_again(tmp_path):
    # the stage job hangs off the export irp_job, so there is nothing to re-arm
    (tmp_path / "e" / "a").mkdir(parents=True)
    (tmp_path / "e" / "a" / "x.zip").write_bytes(b"zip")
    assert svc.retry_decision(_m(zip_file="e/a/x.zip"), None, str(tmp_path), NOW) == "submit"


# ── apply_retry ──────────────────────────────────────────────────────────────

@pytest.fixture()
def failed(iteration2_db, loss_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "export_archive_dir", str(tmp_path))
    submission_id = seed_submission(iteration2_db.user_a)
    edm_id = seed_edm_for(submission_id)
    analysis_id = seed_analysis(edm_id=edm_id)
    export_id = str(uuid.uuid4())

    def make(*, job_status="FAILED", completed_at=None, **manifest):
        irp_job_id = seed_export_job(export_id=export_id, irp_analysis_id=analysis_id,
                                     irp_id="500", status=job_status, edm_id=edm_id,
                                     completed_at=completed_at)
        row = seed_manifest(export_id=export_id, submission_id=submission_id,
                            irp_analysis_id=analysis_id, irp_export_job_id="500",
                            **manifest)
        return {"submission_id": submission_id, "export_id": export_id,
                "analysis_id": analysis_id, "edm_id": edm_id, "irp_job_id": irp_job_id,
                "manifest_id": row["manifest_id"], "tmp": tmp_path}
    return make


def test_404_when_the_export_was_not_requested_from_this_submission(failed):
    f = failed(stage_status="failed")
    with pytest.raises(svc.ExportNotFound):
        svc.apply_retry(str(uuid.uuid4()), f["export_id"], f["analysis_id"])
    with pytest.raises(svc.ExportNotFound):
        svc.apply_retry(f["submission_id"], f["export_id"], str(uuid.uuid4()))


def test_409_when_not_failed_names_the_data_id_for_a_loaded_row(failed):
    f = failed(job_status="FINISHED", stage_status="staged", load_status="loaded", data_id=4127)
    with pytest.raises(svc.ExportRetryRefused) as exc:
        svc.apply_retry(f["submission_id"], f["export_id"], f["analysis_id"])
    assert str(exc.value) == "already loaded as data ID 4127"
    assert rwb_jobs("load_results_export") == []


def test_409_for_a_row_waiting_on_risk_modeler(failed):
    f = failed(job_status="RUNNING")
    with pytest.raises(svc.ExportRetryRefused) as exc:
        svc.apply_retry(f["submission_id"], f["export_id"], f["analysis_id"])
    assert "requested from Risk Modeler" in str(exc.value)


def test_409_while_the_stage_worker_has_not_stamped_a_failed_risk_modeler_job(failed):
    # The poller has enqueued the stage job; only that job may fail the row.
    f = failed(job_status="FAILED", stage_status="pending")
    with pytest.raises(svc.ExportRetryRefused) as exc:
        svc.apply_retry(f["submission_id"], f["export_id"], f["analysis_id"])
    assert str(exc.value) == "the analysis is downloading and staging, not failed"
    assert rwb_jobs("submit_results_export") == []
    assert manifest_row(f["manifest_id"])["irp_export_job_id"] == "500"


def test_load_branch_rearms_the_load_job_keyed_by_the_stage_job(failed):
    f = failed(job_status="FINISHED", stage_status="staged", load_status="failed")
    from app.services import rwb_job_service
    stage_job = rwb_job_service.enqueue_rwb_job(
        requestor_type="irp_job", requestor_id=f["irp_job_id"],
        rwb_job_type="stage_results_export", link_type="edm", link_id=f["edm_id"],
        context_type="irp_analysis", context_id=f["analysis_id"], input_data={})
    rwb_job_service.claim_rwb_job(rwb_job_id=stage_job, worker_id="w1")
    rwb_job_service.complete_rwb_job(rwb_job_id=stage_job, status="succeeded")

    assert svc.apply_retry(f["submission_id"], f["export_id"], f["analysis_id"]) == "load"

    jobs = rwb_jobs("load_results_export")
    assert len(jobs) == 1
    assert jobs[0]["requestor_type"] == "rwb_job" and jobs[0]["requestor_id"] == stage_job
    assert jobs[0]["link_type"] == "edm" and jobs[0]["link_id"] == f["edm_id"]
    assert json.loads(jobs[0]["input_data"]) == {
        "export_id": f["export_id"], "irp_analysis_id": f["analysis_id"],
        "manifest_id": f["manifest_id"]}
    # the row is back in progress: it reads staged and a second Retry is refused
    row = manifest_row(f["manifest_id"])
    assert row["load_status"] == "pending" and row["error_message"] is None
    with pytest.raises(svc.ExportRetryRefused):
        svc.apply_retry(f["submission_id"], f["export_id"], f["analysis_id"])
    assert len(rwb_jobs("load_results_export")) == 1


def test_stage_branch_rearms_the_stage_job(failed):
    f = failed(job_status="FINISHED", completed_at="2026-09-09 08:00:00", stage_status="failed")
    from app.services import rwb_job_service
    rwb_job_service.enqueue_rwb_job(
        requestor_type="irp_job", requestor_id=f["irp_job_id"],
        rwb_job_type="stage_results_export", link_type="edm", link_id=f["edm_id"],
        context_type="irp_analysis", context_id=f["analysis_id"], input_data={})
    stage_job = rwb_jobs("stage_results_export")[0]
    rwb_job_service.claim_rwb_job(rwb_job_id=stage_job["id"], worker_id="w1")
    rwb_job_service.complete_rwb_job(rwb_job_id=stage_job["id"], status="failed",
                                     error_detail="Archive root x is not available")
    with __import__("unittest.mock", fromlist=["patch"]).patch(
            "app.services.export_service._utcnow", return_value=datetime(2026, 9, 10, 8)):
        assert svc.apply_retry(f["submission_id"], f["export_id"], f["analysis_id"]) == "stage"

    jobs = rwb_jobs("stage_results_export")
    assert len(jobs) == 1 and jobs[0]["status_code"] == "pending"
    assert jobs[0]["attempt_count"] == 1
    assert json.loads(jobs[0]["input_data"]) == {
        "export_id": f["export_id"], "irp_analysis_id": f["analysis_id"],
        "irp_job_id": f["irp_job_id"]}
    assert manifest_row(f["manifest_id"])["irp_export_job_id"] == "500"


def test_submit_branch_resets_the_row_and_rearms_the_export_submit(failed):
    f = failed(job_status="FAILED", stage_status="failed", error_message="Analysis not found")
    from app.services import rwb_job_service
    submit_job = rwb_job_service.enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=f["export_id"],
        rwb_job_type="submit_results_export", link_type="not_applicable", link_id=None,
        context_type="result_export", context_id=f["export_id"], input_data={})
    rwb_job_service.claim_rwb_job(rwb_job_id=submit_job, worker_id="w1")
    rwb_job_service.complete_rwb_job(rwb_job_id=submit_job, status="succeeded")

    assert svc.apply_retry(f["submission_id"], f["export_id"], f["analysis_id"]) == "submit"

    row = manifest_row(f["manifest_id"])
    assert row["irp_export_job_id"] is None and row["stage_status"] == "pending"
    assert row["error_message"] is None
    jobs = rwb_jobs("submit_results_export")
    assert len(jobs) == 1 and jobs[0]["id"] == submit_job
    assert jobs[0]["status_code"] == "pending"
    assert json.loads(jobs[0]["input_data"]) == {
        "export_id": f["export_id"], "submission_id": f["submission_id"]}
