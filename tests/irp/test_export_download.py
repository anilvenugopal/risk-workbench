"""Sandbox round-trip for the loss results export (spec 014 T044, plan T-23).

One real export job for a finished sandbox analysis: submit through the
gateway, single-status poll with bounded sleeps, download, and check the archive
against the layout the stage worker expects (research R6) by running the
worker's own archive checks over it. Set ``IRP_TEST_EXPORT_ANALYSIS_ID`` to the
Risk Modeler analysis ID (``irp_analysis.irp_id``) of a finished DLM analysis
whose ``AnlsId`` is ``IRP_TEST_EXPORT_APP_ANALYSIS_ID``; ``IRP_TEST_EXPORT_PERSPECTIVE``
defaults to ``GR``.

Run: ``make shell`` then ``uv run pytest tests/irp --run-irp -k export``.
"""

from __future__ import annotations

import os
import time
import zipfile

import pyarrow.parquet as pq
import pytest

from app.services import irp_gateway
from app.workers import export_jobs

_ANALYSIS_ID = os.environ.get("IRP_TEST_EXPORT_ANALYSIS_ID", "")
_APP_ANALYSIS_ID = os.environ.get("IRP_TEST_EXPORT_APP_ANALYSIS_ID", "")
_PERSPECTIVE = os.environ.get("IRP_TEST_EXPORT_PERSPECTIVE", "GR")

pytestmark = [pytest.mark.irp]

_POLL_TIMEOUT_SECS = 900
_POLL_INTERVAL_SECS = 15


@pytest.mark.skipif(
    not (_ANALYSIS_ID and _APP_ANALYSIS_ID),
    reason="set IRP_TEST_EXPORT_ANALYSIS_ID / IRP_TEST_EXPORT_APP_ANALYSIS_ID to a "
           "finished sandbox DLM analysis to run the export round-trip")
def test_export_submit_poll_download_matches_the_archive_layout(tmp_path):
    gateway = irp_gateway._RealGateway()

    job_id, request_body = gateway.submit_analysis_export_job(
        analysis_id=int(_ANALYSIS_ID),
        loss_details=[{"metricType": "LOSS_TABLES", "outputLevels": ["Portfolio"],
                       "perspectiveCodes": [_PERSPECTIVE]}])
    assert job_id > 0
    assert request_body["settings"]["lossDetails"][0]["perspectiveCodes"] == [_PERSPECTIVE]

    deadline = time.monotonic() + _POLL_TIMEOUT_SECS
    status = gateway.get_export_job(str(job_id))
    while status.status not in ("FINISHED", "FAILED", "CANCELLED"):
        assert time.monotonic() < deadline, (
            f"export job {job_id} did not reach a terminal status in "
            f"{_POLL_TIMEOUT_SECS}s (last: {status.status})")
        time.sleep(_POLL_INTERVAL_SECS)
        status = gateway.get_export_job(str(job_id))  # single-status check only
    assert status.status == "FINISHED", f"export job {job_id} ended {status.status}: {status.result}"

    archive_dir = tmp_path / "archive"
    archive = gateway.download_export_results(job_id=job_id, output_dir=str(archive_dir))
    assert zipfile.is_zipfile(archive), f"{archive} is not a zip archive"

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(work_dir)

    # The stage worker's own checks (contracts/jobs.md §4 steps 4–7) over the
    # real archive: one top folder, one ELT folder, metadata.csv, one
    # Portfolio/{perspective} folder of Parquet files with the ELT columns.
    table_dir = export_jobs._loss_table_dir(work_dir)
    assert table_dir.name == "ELT"
    metadata = export_jobs._read_metadata(table_dir)
    assert metadata["AnlsId"] == _APP_ANALYSIS_ID
    assert {"AnalysisCurrency", "Engine Type", "ModelVersion"} <= set(metadata)
    assert metadata["Engine Type"] in ("DLM", "GROUP")
    files = export_jobs._perspective_files(table_dir, _PERSPECTIVE)
    assert files, f"no Parquet files under Portfolio/{_PERSPECTIVE}"
    for path in files:
        schema = pq.ParquetFile(path).schema.names
        assert set(export_jobs.ELT_COLUMN_MAP) <= set(schema), (
            f"{path.name} lacks {sorted(set(export_jobs.ELT_COLUMN_MAP) - set(schema))}")
