"""Unit tests for the ``load_results_export`` worker (spec 014 T034, T041) with
``execute_procedure`` faked: the procedure itself is covered in the SQL Server
tier (tests/sqlserver/test_loss_export_procedure.py)."""

from __future__ import annotations

import json

import pytest

from app.services import rwb_job_service
from app.workers import export_jobs
from db import execute_command, execute_one
from tests.unit.export_rows import manifest_row, seed_manifest


@pytest.fixture()
def load_job(iteration2_db, loss_db):
    def make(**manifest):
        row = seed_manifest(**manifest)
        job_id = rwb_job_service.enqueue_rwb_job(
            requestor_type="rwb_job", requestor_id="stage-job", rwb_job_type="load_results_export",
            link_type="not_applicable", link_id=None, context_type="irp_analysis",
            context_id=row["irp_analysis_id"],
            input_data={"export_id": row["export_id"], "irp_analysis_id": row["irp_analysis_id"],
                        "manifest_id": row["manifest_id"]})
        return row, job_id
    return make


def _job(job_id):
    return execute_one("SELECT status_code, output_data, error_detail FROM rwb_job WHERE id = :id",
                       {"id": job_id}, connection="WORKBENCH")


def test_loaded_row_is_skipped_without_calling_the_procedure(load_job, monkeypatch):
    row, job_id = load_job(stage_status="staged", load_status="loaded", data_id=9)
    calls = []
    monkeypatch.setattr(export_jobs, "execute_procedure", lambda *a, **k: calls.append(a))

    export_jobs.run_pending(worker_id="w1")

    assert calls == []
    assert json.loads(_job(job_id)["output_data"]) == {"skipped": "loaded"}
    assert manifest_row(row["manifest_id"])["data_id"] == 9


def test_unstaged_row_fails_without_touching_the_manifest(load_job, monkeypatch):
    row, job_id = load_job(stage_status="pending")
    monkeypatch.setattr(export_jobs, "execute_procedure",
                        lambda *a, **k: pytest.fail("procedure must not run"))

    export_jobs.run_pending(worker_id="w1")

    job = _job(job_id)
    assert job["status_code"] == "failed" and job["error_detail"] == "analysis is not staged"
    after = manifest_row(row["manifest_id"])
    assert after["load_status"] == "pending" and after["error_message"] is None


def test_a_raised_call_stamps_failed_with_the_sql_server_message(load_job, monkeypatch):
    row, job_id = load_job(stage_status="staged")

    class _Orig(Exception):
        pass

    def boom(name, params, connection):
        assert (name, params, connection) == (
            "stage.usp_load_elt_result", {"manifest_id": row["manifest_id"]}, "LOSS")
        exc = RuntimeError("wrapped")
        exc.orig = _Orig(
            "('42000', '[42000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
            "Lookup_RMS_HistoricalRDS has no rows for model version 25.0 (50002) "
            "(SQLExecDirectW)')")
        raise exc
    monkeypatch.setattr(export_jobs, "execute_procedure", boom)

    export_jobs.run_pending(worker_id="w1")

    after = manifest_row(row["manifest_id"])
    assert after["load_status"] == "failed"
    assert after["error_message"] == "Lookup_RMS_HistoricalRDS has no rows for model version 25.0"
    job = _job(job_id)
    assert job["status_code"] == "failed"
    assert job["error_detail"] == "Lookup_RMS_HistoricalRDS has no rows for model version 25.0"


def test_success_returns_the_data_id_the_procedure_wrote(load_job, monkeypatch):
    row, job_id = load_job(stage_status="staged")

    def procedure(name, params, connection):
        execute_command(
            "UPDATE stage.rwb_loss_result_manifest SET load_status = 'loaded', data_id = 4127 "
            "WHERE manifest_id = :m", {"m": params["manifest_id"]}, connection="LOSS")
    monkeypatch.setattr(export_jobs, "execute_procedure", procedure)

    export_jobs.run_pending(worker_id="w1")

    assert json.loads(_job(job_id)["output_data"]) == {"data_id": 4127}
