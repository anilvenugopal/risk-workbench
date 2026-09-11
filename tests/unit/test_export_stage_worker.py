"""Unit tests for the ``stage_results_export`` worker (spec 014 T033, T041)
against a fixture archive in Risk Modeler's layout and the SQLite loss mirror.
``db.elt.upload_parquet`` is replaced by the SQLite-capable stand-in from
``tests.unit.export_archive``; everything else is the real worker."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.config import settings
from app.poller import run as poller
from app.services import export_service as svc
from app.workers import export_jobs
from db import elt, execute, execute_command, execute_one
from tests.unit.export_archive import ELT_COLUMNS, build_archive, sqlite_upload_parquet
from tests.unit.export_rows import (
    manifest_for,
    seed_analysis,
    seed_client,
    seed_edm_for,
    seed_submission,
)


@pytest.fixture()
def staging(iteration2_db, loss_db, fake_irp, tmp_path, monkeypatch):
    """An export whose Risk Modeler job has FINISHED and whose stage job is
    pending; the archive root and the staging root point at tmp_path."""
    monkeypatch.setattr(elt, "upload_parquet", sqlite_upload_parquet)
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(settings, "export_archive_dir", str(root))
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    monkeypatch.setattr(settings, "export_staging_dir", str(staging_root))
    fake_irp.export_archive_path = build_archive(tmp_path / "fixture")

    submission_id = seed_submission(iteration2_db.user_a)
    edm_id = seed_edm_for(submission_id)
    a = seed_analysis(edm_id=edm_id, irp_id="41958", irp_app_analysis_id="41958")
    seed_client()
    export_id = svc.create_export(
        submission_id=submission_id, user_email="analyst.a@example.com", analysis_ids=[a],
        perspective_code="GR", client_id=1, treaty_incept=date(2026, 4, 1), crm_id=None,
        data_vintage=None)
    export_jobs.run_pending(worker_id="w1")
    irp_id = execute_one("SELECT irp_id FROM irp_job", {}, connection="WORKBENCH")["irp_id"]
    fake_irp.finish(irp_id)
    poller.poll_once()
    return {"export_id": export_id, "analysis_id": a, "irp_id": irp_id, "root": root,
            "tmp": tmp_path, "submission_id": submission_id}


def _manifest(s):
    return manifest_for(s["export_id"], s["analysis_id"])


def _stage_job():
    return execute_one("SELECT * FROM rwb_job WHERE rwb_job_type = 'stage_results_export'",
                       {}, connection="WORKBENCH")


def _load_jobs():
    return execute("SELECT * FROM rwb_job WHERE rwb_job_type = 'load_results_export'", {},
                   connection="WORKBENCH")


def _elt_rows(manifest_id):
    return execute("SELECT * FROM stage.rwb_loss_result_elt_data WHERE manifest_id = :m "
                   "ORDER BY event_id", {"m": manifest_id}, connection="LOSS")


def _run_stage():
    return export_jobs.run_pending(worker_id="w1")


def _fail(s, text):
    """Run the stage job and assert the analysis failed with ``text``."""
    _run_stage()
    m = _manifest(s)
    assert m["stage_status"] == "failed", m["error_message"]
    assert text in m["error_message"], m["error_message"]
    assert _stage_job()["status_code"] == "failed"
    assert _load_jobs() == []
    return m


def test_happy_path_stages_files_and_rows_and_enqueues_the_load(staging, fake_irp):
    assert _run_stage() == 1

    m = _manifest(staging)
    assert m["stage_status"] == "staged" and m["staged_at"] is not None
    assert m["staged_row_count"] == 3 and m["error_message"] is None
    assert (m["loss_table_type"], m["engine_type"], m["data_model_version"]) == (
        "ELT", "DLM", "25.0")
    assert m["zip_file"] == f"{staging['export_id']}/{staging['analysis_id']}/" \
                            "25437617_CRE_Port_Template_Losses.zip"
    assert (staging["root"] / m["zip_file"]).is_file()
    assert fake_irp.export_downloads == [{
        "job_id": int(staging["irp_id"]),
        "output_dir": str(staging["root"] / staging["export_id"] / staging["analysis_id"])}]

    files = execute("SELECT * FROM stage.rwb_loss_result_file WHERE manifest_id = :m",
                    {"m": m["manifest_id"]}, connection="LOSS")
    assert len(files) == 1
    assert files[0]["result_file"].endswith("ELT/Portfolio/GR/25437617_CRE_Port_Template_ELT_Portfolio_GR_0.parquet")
    assert (files[0]["output_level"], files[0]["perspective_code"], files[0]["chunk_index"],
            files[0]["row_count"]) == ("Portfolio", "GR", 0, 3)
    rows = _elt_rows(m["manifest_id"])
    assert [r["event_id"] for r in rows] == [1001, 1002, 3001]
    assert rows[0]["loss"] == 100.0 and rows[0]["std_dev_i"] == -1.0
    assert rows[0]["exp_value"] == 50.0 and rows[0]["result_file_id"] == files[0]["result_file_id"]
    assert rows[0]["event_type"] is None  # the procedure classifies, not the worker

    load = _load_jobs()
    assert len(load) == 1
    assert load[0]["requestor_type"] == "rwb_job" and load[0]["requestor_id"] == _stage_job()["id"]
    assert load[0]["context_type"] == "irp_analysis"
    assert json.loads(load[0]["input_data"]) == {
        "export_id": staging["export_id"], "irp_analysis_id": staging["analysis_id"],
        "manifest_id": m["manifest_id"]}
    assert not any(Path(settings.export_staging_dir).rglob("*.parquet"))


def test_several_chunks_stage_in_order(staging, fake_irp):
    fake_irp.export_archive_path = build_archive(staging["tmp"] / "chunked", chunks=2)
    _run_stage()
    m = _manifest(staging)
    files = execute("SELECT chunk_index, row_count FROM stage.rwb_loss_result_file "
                    "WHERE manifest_id = :m ORDER BY chunk_index", {"m": m["manifest_id"]},
                    connection="LOSS")
    assert [(f["chunk_index"], f["row_count"]) for f in files] == [(0, 2), (1, 1)]
    assert m["staged_row_count"] == 3


def test_group_engine_type_and_build_number_version_are_recorded(staging, fake_irp):
    fake_irp.export_archive_path = build_archive(
        staging["tmp"] / "group", engine_type="GROUP", model_version="25.0.2450.0")
    _run_stage()
    m = _manifest(staging)
    assert (m["engine_type"], m["data_model_version"]) == ("GROUP", "25.0")


def test_export_job_not_finished_fails_with_the_jobs_reason(staging):
    execute_command(
        "UPDATE irp_job SET status = 'FAILED', last_completion_result = :r",
        {"r": json.dumps({"tasks": [{"output": {"errors": [{"message": "no results"}]}}]})},
        connection="WORKBENCH")
    _fail(staging, "no results")


def test_export_job_failed_without_detail_names_job_and_status(staging):
    execute_command("UPDATE irp_job SET status = 'CANCELLED'", {}, connection="WORKBENCH")
    _fail(staging, f"Risk Modeler export job {staging['irp_id']} ended CANCELLED")


def test_missing_archive_root_fails_before_any_download(staging, fake_irp, monkeypatch):
    monkeypatch.setattr(settings, "export_archive_dir", str(staging["tmp"] / "missing"))
    _fail(staging, "is not available")


def test_missing_staging_root_fails_before_any_download(staging, fake_irp, monkeypatch):
    monkeypatch.setattr(settings, "export_staging_dir", str(staging["tmp"] / "missing"))
    _fail(staging, "is not available")
    assert fake_irp.export_downloads == []


def test_existing_archive_is_reused_without_a_download(staging, fake_irp):
    target = staging["root"] / staging["export_id"] / staging["analysis_id"]
    target.mkdir(parents=True)
    zip_path = build_archive(target)
    execute_command("UPDATE stage.rwb_loss_result_manifest SET zip_file = :z",
                    {"z": zip_path.relative_to(staging["root"]).as_posix()}, connection="LOSS")

    _run_stage()

    assert _manifest(staging)["stage_status"] == "staged"
    assert fake_irp.export_downloads == []


def test_download_failure_fails_the_analysis(staging, fake_irp):
    fake_irp.raise_on_export_download = True
    _fail(staging, "download failed")


@pytest.mark.parametrize("knobs, text", [
    ({"anls_id": 99999}, "archive AnlsId '99999' does not match analysis 41958"),
    ({"currency": "EUR"}, "archive currency 'EUR' does not match analysis currency 'USD'"),
    ({"metadata": False}, "metadata.csv is missing"),
    ({"loss_table": "PLT"}, "loss table type PLT not supported"),
    ({"loss_table": "XLT"}, "unknown loss table type XLT"),
    ({"perspectives": ("GR", "RL")}, "perspective folders RL besides GR"),
    ({"perspectives": ("RL",)}, "archive has no Portfolio/GR folder"),
    ({"rows": []}, "Risk Modeler returned no GR loss rows for this analysis"),
    ({"columns": [c for c in ELT_COLUMNS if c != "StdDevI"]}, "missing columns StdDevI"),
])
def test_archive_checks_fail_the_analysis(staging, fake_irp, knobs, text):
    fake_irp.export_archive_path = build_archive(staging["tmp"] / "bad", **knobs)
    m = _fail(staging, text)
    assert m["stage_status"] == "failed"
    assert execute("SELECT 1 FROM stage.rwb_loss_result_elt_data", {}, connection="LOSS") == []
    if knobs.get("rows") == [] or "columns" in knobs:
        # the file row exists but no analysis was marked staged
        assert m["staged_row_count"] is None


def test_rerun_on_a_staged_row_only_enqueues_the_load(staging, fake_irp):
    _run_stage()
    execute_command("DELETE FROM rwb_job WHERE rwb_job_type = 'load_results_export'", {},
                    connection="WORKBENCH")
    from app.services import rwb_job_service
    rwb_job_service.ensure_pending_rwb_job(
        requestor_type="irp_job", requestor_id=_stage_job()["requestor_id"],
        rwb_job_type="stage_results_export", link_type="edm", link_id=None,
        context_type="irp_analysis", context_id=staging["analysis_id"],
        input_data=json.loads(_stage_job()["input_data"]))
    downloads = len(fake_irp.export_downloads)

    _run_stage()

    assert len(fake_irp.export_downloads) == downloads
    assert len(_load_jobs()) == 1
    assert len(_elt_rows(_manifest(staging)["manifest_id"])) == 3


def test_rerun_after_a_partial_stage_replaces_the_partial_rows(staging, fake_irp):
    # A crash mid-stage left file and loss rows behind and the archive on the share.
    m = _manifest(staging)
    execute_command(
        "INSERT INTO stage.rwb_loss_result_file (manifest_id, result_file, chunk_index) "
        "VALUES (:m, 'partial.parquet', 0)", {"m": m["manifest_id"]}, connection="LOSS")
    file_id = execute_one("SELECT result_file_id FROM stage.rwb_loss_result_file", {},
                          connection="LOSS")["result_file_id"]
    execute_command(
        "INSERT INTO stage.rwb_loss_result_elt_data (manifest_id, result_file_id, event_id, "
        "loss) VALUES (:m, :f, 77, 1.0)", {"m": m["manifest_id"], "f": file_id},
        connection="LOSS")
    target = staging["root"] / staging["export_id"] / staging["analysis_id"]
    target.mkdir(parents=True)
    zip_path = build_archive(target)
    execute_command("UPDATE stage.rwb_loss_result_manifest SET zip_file = :z",
                    {"z": zip_path.relative_to(staging["root"]).as_posix()}, connection="LOSS")

    _run_stage()

    rows = _elt_rows(m["manifest_id"])
    assert [r["event_id"] for r in rows] == [1001, 1002, 3001]
    files = execute("SELECT result_file FROM stage.rwb_loss_result_file", {}, connection="LOSS")
    assert len(files) == 1 and "partial" not in files[0]["result_file"]
    assert fake_irp.export_downloads == []
    assert _manifest(staging)["stage_status"] == "staged"


def test_loaded_row_is_skipped(staging):
    execute_command("UPDATE stage.rwb_loss_result_manifest SET load_status = 'loaded', "
                    "stage_status = 'staged'", {}, connection="LOSS")
    _run_stage()
    job = _stage_job()
    assert job["status_code"] == "succeeded"
    assert json.loads(job["output_data"]) == {"skipped": "loaded"}
    assert _load_jobs() == []


def test_corrupt_archive_on_the_share_is_forgotten_so_retry_downloads_again(staging, fake_irp):
    target = staging["root"] / staging["export_id"] / staging["analysis_id"]
    target.mkdir(parents=True)
    bad = target / "corrupt.zip"
    bad.write_bytes(b"not a zip")
    execute_command("UPDATE stage.rwb_loss_result_manifest SET zip_file = :z",
                    {"z": bad.relative_to(staging["root"]).as_posix()}, connection="LOSS")

    m = _fail(staging, "corrupt.zip is not a valid zip file")

    assert m["zip_file"] is None
    assert fake_irp.export_downloads == []
    assert svc.retry_decision(m, {"status": "FINISHED", "completed_at": m["updated_at"]},
                              str(staging["root"]), svc._utcnow()) == "stage"


def test_load_enqueue_failure_stamps_load_failed_and_keeps_the_staged_rows(
        staging, fake_irp, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("WORKBENCH is down")
    from app.services import rwb_job_service
    monkeypatch.setattr(rwb_job_service, "ensure_pending_rwb_job", boom)

    _run_stage()

    m = _manifest(staging)
    assert m["stage_status"] == "staged" and m["load_status"] == "failed"
    assert m["error_message"] == "could not queue the load: WORKBENCH is down"
    assert len(_elt_rows(m["manifest_id"])) == 3
    assert _stage_job()["status_code"] == "failed"
    assert svc.derive_status(m, None) == svc.FAILED
    assert svc.retry_decision(m, None, str(staging["root"]), svc._utcnow()) == "load"


def test_a_failure_before_the_stage_steps_still_stamps_the_row(staging, monkeypatch):
    def boom(manifest_id, work_dir):
        raise RuntimeError("LOSS is down")
    monkeypatch.setattr(export_jobs, "_discard_partial_stage", boom)

    _fail(staging, "LOSS is down")


def test_the_worker_time_limit_stamps_the_row_and_re_raises(staging, monkeypatch):
    from dramatiq.middleware import TimeLimitExceeded

    def slow(manifest, irp_job_id):
        raise TimeLimitExceeded()
    monkeypatch.setattr(export_jobs, "_stage", slow)

    with pytest.raises(TimeLimitExceeded):
        export_jobs._stage_results_export_body(_stage_job()["id"])

    m = _manifest(staging)
    assert m["stage_status"] == "failed"
    assert m["error_message"] == "the run exceeded the worker time limit"
