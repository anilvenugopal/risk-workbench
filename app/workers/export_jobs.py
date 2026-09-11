"""Dramatiq actors for the loss results export (spec 014, contracts/jobs.md).

``submit_results_export`` asks Risk Modeler for one loss-table export per
manifest row of an export. The poller enqueues ``stage_results_export`` when
that export job ends; the stage worker downloads the archive to the permanent
archive root, checks it against the manifest, and streams its Parquet files
into ``stage.rwb_loss_result_elt_data``. ``load_results_export`` runs
``stage.usp_load_elt_result``, which classifies, corrects, and loads the rows
into CIC's three tables in one transaction. Every actor resumes from the
manifest row's ``stage_status`` / ``load_status``; nothing here recomputes what
the analyst approved on the form.
"""

from __future__ import annotations

import csv
import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from dramatiq.middleware import TimeLimitExceeded

from app.config import settings
from app.services import irp_gateway, irp_job_service, rwb_job_service
from app.services._common import _uid, _utcnow
from app.services.irp_gateway import IRPAPIError, IRPIntegrationError
from app.workers import broker, dispatch, runtime
from app.workers.queues import rwb_actor
from db import elt, execute, execute_command, execute_one, execute_procedure

logger = logging.getLogger(__name__)

_ = broker.redis_broker

ELT_COLUMN_MAP = {
    "PortInfoId": "port_info_id", "PortInfoName": "port_info_name",
    "PortInfoNum": "port_info_num", "EventId": "event_id", "Rate": "rate",
    "Loss": "loss", "StdDevI": "std_dev_i", "StdDevC": "std_dev_c",
    "ExpValue": "exp_value",
}
_SIX_HOURS_MS = 6 * 60 * 60 * 1000
_CHUNK_SUFFIX = re.compile(r"_(\d+)\.parquet$", re.IGNORECASE)


class StageFailure(Exception):
    """A stage step failed for a reason the analyst can act on."""


def _stamp_manifest(manifest_id: int, **columns: Any) -> None:
    """``UPDATE`` the manifest row's listed columns plus ``updated_at``. Column
    names are the code constants below, never input."""
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    execute_command(
        f"UPDATE stage.rwb_loss_result_manifest SET {assignments}, updated_at = :now "
        "WHERE manifest_id = :manifest_id",
        {**columns, "now": _utcnow(), "manifest_id": manifest_id}, connection="LOSS")


def _analysis_ids(irp_analysis_id: str) -> dict:
    return execute_one("SELECT edm_id, rdm_id FROM irp_analysis WHERE id = :a",
                       {"a": irp_analysis_id}, connection="WORKBENCH") or {}


# ── submit_results_export ────────────────────────────────────────────────────


def _submit_results_export_body(rwb_job_id: Any) -> runtime.JobResult:
    context = rwb_job_service.load_input_data(rwb_job_id)
    export_id = _uid(context["export_id"])
    submission_id = context.get("submission_id")
    rows = execute(
        "SELECT manifest_id, irp_analysis_id, irp_analysis_irp_id, perspective_code "
        "FROM stage.rwb_loss_result_manifest "
        "WHERE export_id = :e AND stage_status = 'pending' AND irp_export_job_id IS NULL "
        "ORDER BY manifest_id", {"e": export_id}, connection="LOSS")
    submitted = failed = 0
    for row in rows:
        existing = irp_job_service.find_export_job(export_id, row["irp_analysis_id"])
        if existing is not None:
            # A previous run recorded the job and died before stamping the row.
            _stamp_manifest(row["manifest_id"], irp_export_job_id=existing["irp_id"])
            submitted += 1
            continue
        try:
            job_id, request_body = irp_gateway.submit_analysis_export_job(
                analysis_id=int(row["irp_analysis_irp_id"]),
                loss_details=[{"metricType": "LOSS_TABLES", "outputLevels": ["Portfolio"],
                               "perspectiveCodes": [row["perspective_code"]]}])
        except IRPAPIError as exc:
            logger.warning("export submit rejected for analysis %s: %s",
                           row["irp_analysis_id"], exc)
            _stamp_manifest(row["manifest_id"], stage_status="failed", error_message=str(exc))
            failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 — Risk Modeler unreachable: stop, resume later
            logger.exception("export submit stopped at analysis %s", row["irp_analysis_id"])
            return runtime.JobResult.fail(str(exc), submitted=submitted, failed=failed)
        analysis = _analysis_ids(_uid(row["irp_analysis_id"]))
        irp_job_service.record_submitted_irp_job(
            irp_job_type="export", requested_from_submission_id=submission_id,
            irp_edm_id=analysis.get("edm_id"), irp_rdm_id=analysis.get("rdm_id"),
            irp_analysis_id=row["irp_analysis_id"], irp_id=str(job_id),
            payload=request_body, request_params=request_body, export_id=export_id)
        _stamp_manifest(row["manifest_id"], irp_export_job_id=str(job_id))
        submitted += 1
    return runtime.JobResult.ok(submitted=submitted, failed=failed)


@rwb_actor(max_retries=0)
def submit_results_export(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _submit_results_export_body(rwb_job_id))


# ── stage_results_export ─────────────────────────────────────────────────────


def _working_dir(export_id: str, irp_analysis_id: str) -> Path:
    return Path(settings.export_staging_dir) / export_id / irp_analysis_id


def _remove_dir(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("could not remove working directory %s", path)


def _discard_partial_stage(manifest_id: int, work_dir: Path) -> None:
    execute_command("DELETE FROM stage.rwb_loss_result_elt_data WHERE manifest_id = :m",
                    {"m": manifest_id}, connection="LOSS")
    execute_command("DELETE FROM stage.rwb_loss_result_file WHERE manifest_id = :m",
                    {"m": manifest_id}, connection="LOSS")
    _remove_dir(work_dir)


def _archive_path(manifest: dict, job: dict, root: Path) -> Path:
    """The permanent archive: the one already under the root, else a fresh
    download into ``{root}/{export_id}/{irp_analysis_id}/`` (T-15)."""
    if manifest["zip_file"]:
        existing = root / manifest["zip_file"]
        if existing.is_file():
            return existing
    export_id, analysis_id = _uid(manifest["export_id"]), _uid(manifest["irp_analysis_id"])
    try:
        downloaded = Path(irp_gateway.download_export_results(
            job_id=int(job["irp_id"]), output_dir=str(root / export_id / analysis_id)))
    except IRPIntegrationError as exc:
        raise StageFailure(f"download failed: {exc}") from exc
    _stamp_manifest(manifest["manifest_id"],
                    zip_file=downloaded.relative_to(root).as_posix())
    return downloaded


def _loss_table_dir(work_dir: Path) -> Path:
    tops = [p for p in work_dir.iterdir() if p.is_dir()]
    if len(tops) != 1:
        raise StageFailure(f"archive holds {len(tops)} top-level folders, expected one")
    tables = [p for p in tops[0].iterdir() if p.is_dir()]
    if len(tables) != 1:
        raise StageFailure(f"archive holds {len(tables)} loss-table folders, expected one")
    table = tables[0]
    if table.name == "PLT":
        raise StageFailure("loss table type PLT not supported")
    if table.name != "ELT":
        raise StageFailure(f"unknown loss table type {table.name}")
    return table


def _read_metadata(table_dir: Path) -> dict:
    path = table_dir / "metadata.csv"
    if not path.is_file():
        raise StageFailure("metadata.csv is missing from the archive")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise StageFailure("metadata.csv holds no rows")
    return {k.strip(): (v or "").strip() for k, v in rows[0].items() if k}


def _model_version(value: str) -> str | None:
    """``metadata.csv`` ``ModelVersion`` as CIC's ``Data.DataModelVersion`` takes it.
    Risk Modeler writes either the decimal form (``25.0``) or a build number
    (``23.0.2250.1``), which is wider than the ``nvarchar(10)`` target."""
    parts = value.split(".")
    if len(parts) > 2 and all(part.isdigit() for part in parts):
        return ".".join(parts[:2])
    return value or None


def _perspective_files(table_dir: Path, perspective_code: str) -> list[Path]:
    portfolio_dir = table_dir / "Portfolio"
    folders = {p.name for p in portfolio_dir.iterdir() if p.is_dir()} if portfolio_dir.is_dir() else set()
    if perspective_code not in folders:
        raise StageFailure(f"archive has no Portfolio/{perspective_code} folder")
    extra = sorted(folders - {perspective_code})
    if extra:
        raise StageFailure(
            f"archive holds perspective folders {', '.join(extra)} besides {perspective_code}")
    return sorted((portfolio_dir / perspective_code).glob("*.parquet"), key=_chunk_index)


def _chunk_index(path: Path) -> int:
    match = _CHUNK_SUFFIX.search(path.name)
    return int(match.group(1)) if match else 0


def _stage_file(manifest_id: int, work_dir: Path, path: Path, perspective_code: str) -> int:
    # upload_parquet checks the same thing, but its message names the mapping,
    # not the file the analyst has to look at.
    missing = [c for c in ELT_COLUMN_MAP if c not in pq.ParquetFile(path).schema.names]
    if missing:
        raise StageFailure(f"{path.name} is missing columns {', '.join(missing)}")
    result_file = path.relative_to(work_dir).as_posix()
    execute_command(
        "INSERT INTO stage.rwb_loss_result_file (manifest_id, result_file, output_level, "
        "perspective_code, chunk_index, staged_at) "
        "VALUES (:m, :f, 'Portfolio', :p, :c, :now)",
        {"m": manifest_id, "f": result_file, "p": perspective_code,
         "c": _chunk_index(path), "now": _utcnow()}, connection="LOSS")
    file_id = execute_one(
        "SELECT result_file_id FROM stage.rwb_loss_result_file "
        "WHERE manifest_id = :m AND result_file = :f",
        {"m": manifest_id, "f": result_file}, connection="LOSS")["result_file_id"]
    rows = elt.upload_parquet(
        path, "rwb_loss_result_elt_data", schema="stage",
        extra_columns={"manifest_id": manifest_id, "result_file_id": file_id},
        column_mapping=ELT_COLUMN_MAP, drop_unmapped_columns=True, connection="LOSS")
    execute_command("UPDATE stage.rwb_loss_result_file SET row_count = :n "
                    "WHERE result_file_id = :id", {"n": rows, "id": file_id}, connection="LOSS")
    return rows


def _stage(manifest: dict, irp_job_id: str) -> None:
    """Steps 1–7 of contracts/jobs.md §4; raises StageFailure with the reason."""
    manifest_id = manifest["manifest_id"]
    job = execute_one(
        "SELECT id, irp_id, status, last_completion_result FROM irp_job WHERE id = :j",
        {"j": irp_job_id}, connection="WORKBENCH")
    if job is None:
        raise StageFailure(f"Risk Modeler export job record {irp_job_id} not found")
    if job["status"] != "FINISHED":
        raise StageFailure(
            irp_job_service.failure_message(job["last_completion_result"])
            or f"Risk Modeler export job {job['irp_id']} ended {job['status']}")

    root = Path(settings.export_archive_dir or "")
    if not settings.export_archive_dir or not root.is_dir():
        raise StageFailure(f"Archive root {settings.export_archive_dir or '(unset)'} "
                           "is not available")
    if not settings.export_staging_dir or not Path(settings.export_staging_dir).is_dir():
        raise StageFailure(f"Staging root {settings.export_staging_dir or '(unset)'} "
                           "is not available")
    archive = _archive_path(manifest, job, root)

    work_dir = _working_dir(_uid(manifest["export_id"]), _uid(manifest["irp_analysis_id"]))
    _remove_dir(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(work_dir)
    except zipfile.BadZipFile as exc:
        # Forget the archive so Retry downloads it again instead of reusing it.
        _stamp_manifest(manifest_id, zip_file=None)
        raise StageFailure(f"{archive.name} is not a valid zip file; it will be "
                           "downloaded again on Retry") from exc

    table_dir = _loss_table_dir(work_dir)
    metadata = _read_metadata(table_dir)
    if metadata.get("AnlsId") != str(manifest["irp_app_analysis_id"]):
        raise StageFailure(f"archive AnlsId {metadata.get('AnlsId')!r} does not match "
                           f"analysis {manifest['irp_app_analysis_id']}")
    if metadata.get("AnalysisCurrency") != manifest["data_currency"]:
        raise StageFailure(f"archive currency {metadata.get('AnalysisCurrency')!r} does not "
                           f"match analysis currency {manifest['data_currency']!r}")
    _stamp_manifest(manifest_id, loss_table_type=table_dir.name,
                    engine_type=metadata.get("Engine Type") or None,
                    data_model_version=_model_version(metadata.get("ModelVersion", "")))

    perspective_code = manifest["perspective_code"]
    total = 0
    for path in _perspective_files(table_dir, perspective_code):
        total += _stage_file(manifest_id, work_dir, path, perspective_code)
    if total == 0:
        raise StageFailure(
            f"Risk Modeler returned no {perspective_code} loss rows for this analysis")
    _stamp_manifest(manifest_id, stage_status="staged", staged_at=_utcnow(),
                    staged_row_count=total, error_message=None)
    _remove_dir(work_dir)


def _enqueue_load(manifest: dict, stage_rwb_job_id: Any) -> str | None:
    analysis_id = _uid(manifest["irp_analysis_id"])
    analysis = _analysis_ids(analysis_id)
    link_type, link_id = rwb_job_service.analysis_link(analysis.get("edm_id"),
                                                       analysis.get("rdm_id"))
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="rwb_job", requestor_id=stage_rwb_job_id,
        rwb_job_type="load_results_export",
        link_type=link_type, link_id=link_id,
        context_type="irp_analysis", context_id=analysis_id,
        input_data={"export_id": _uid(manifest["export_id"]), "irp_analysis_id": analysis_id,
                    "manifest_id": manifest["manifest_id"]})
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="load_results_export")
    return job_id


def _stage_results_export_body(rwb_job_id: Any) -> runtime.JobResult:
    context = rwb_job_service.load_input_data(rwb_job_id)
    export_id, analysis_id = _uid(context["export_id"]), _uid(context["irp_analysis_id"])
    manifest = execute_one(
        "SELECT * FROM stage.rwb_loss_result_manifest "
        "WHERE export_id = :e AND irp_analysis_id = :a",
        {"e": export_id, "a": analysis_id}, connection="LOSS")
    if manifest is None:
        return runtime.JobResult.fail(f"no manifest row for export {export_id} analysis "
                                      f"{analysis_id}")
    if manifest["load_status"] == "loaded":
        return runtime.JobResult.ok(skipped="loaded")
    manifest_id = manifest["manifest_id"]
    if manifest["stage_status"] != "staged":
        # Every exit but success stamps the row: Retry is offered from the
        # manifest alone, so an unstamped failure would be stuck for good.
        try:
            _discard_partial_stage(manifest_id, _working_dir(export_id, analysis_id))
            _stage(manifest, context["irp_job_id"])
        except TimeLimitExceeded:
            _stamp_manifest(manifest_id, stage_status="failed",
                            error_message="the run exceeded the worker time limit")
            raise
        except StageFailure as exc:
            _stamp_manifest(manifest_id, stage_status="failed", error_message=str(exc))
            return runtime.JobResult.fail(str(exc))
        except Exception as exc:  # noqa: BLE001 — every stage failure lands on the manifest
            logger.exception("stage failed for manifest %s", manifest_id)
            _stamp_manifest(manifest_id, stage_status="failed", error_message=str(exc))
            return runtime.JobResult.fail(str(exc))
    try:
        load_job_id = _enqueue_load(manifest, rwb_job_id)
    except Exception as exc:  # noqa: BLE001 — staged rows stay; Retry re-arms the load
        logger.exception("could not queue the load for manifest %s", manifest_id)
        reason = f"could not queue the load: {exc}"
        _stamp_manifest(manifest_id, load_status="failed", error_message=reason)
        return runtime.JobResult.fail(reason)
    row = execute_one("SELECT staged_row_count FROM stage.rwb_loss_result_manifest "
                      "WHERE manifest_id = :m", {"m": manifest_id}, connection="LOSS")
    return runtime.JobResult.ok(staged_row_count=row["staged_row_count"],
                                load_job_id=load_job_id)


@rwb_actor(max_retries=0, time_limit=_SIX_HOURS_MS)
def stage_results_export(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _stage_results_export_body(rwb_job_id))


# ── load_results_export ──────────────────────────────────────────────────────

_ODBC_MESSAGE = re.compile(r"\[SQL Server\](.*?)\s*\(\d+\)\s*\(SQL\w+\)", re.DOTALL)


def _error_text(exc: BaseException) -> str:
    """The SQL Server message from a driver error, without the ODBC wrapping."""
    text = str(getattr(exc, "orig", None) or exc)
    match = _ODBC_MESSAGE.search(text)
    return match.group(1).strip() if match else text


def _load_results_export_body(rwb_job_id: Any) -> runtime.JobResult:
    context = rwb_job_service.load_input_data(rwb_job_id)
    manifest_id = int(context["manifest_id"])
    manifest = execute_one(
        "SELECT stage_status, load_status, data_id FROM stage.rwb_loss_result_manifest "
        "WHERE manifest_id = :m", {"m": manifest_id}, connection="LOSS")
    if manifest is None:
        return runtime.JobResult.fail(f"no manifest row {manifest_id}")
    if manifest["load_status"] == "loaded":
        return runtime.JobResult.ok(skipped="loaded")
    if manifest["stage_status"] != "staged":
        return runtime.JobResult.fail("analysis is not staged")
    try:
        execute_procedure("stage.usp_load_elt_result", {"manifest_id": manifest_id},
                          connection="LOSS")
    except Exception as exc:  # noqa: BLE001 — the procedure's message is the analyst's answer
        reason = _error_text(exc)
        logger.warning("load failed for manifest %s: %s", manifest_id, reason)
        try:
            execute_command(
                "UPDATE stage.rwb_loss_result_manifest SET load_status = 'failed', "
                "error_message = :e, updated_at = :now "
                "WHERE manifest_id = :m AND load_status NOT IN ('loaded', 'loading')",
                {"e": reason, "now": _utcnow(), "m": manifest_id}, connection="LOSS")
        except Exception:  # noqa: BLE001 — the procedure's CATCH already stamped it
            logger.exception("could not stamp load failure on manifest %s", manifest_id)
        return runtime.JobResult.fail(reason)
    row = execute_one("SELECT data_id FROM stage.rwb_loss_result_manifest WHERE manifest_id = :m",
                      {"m": manifest_id}, connection="LOSS")
    return runtime.JobResult.ok(data_id=row["data_id"])


@rwb_actor(max_retries=0, time_limit=_SIX_HOURS_MS)
def load_results_export(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _load_results_export_body(rwb_job_id))


# ── synchronous drain (unit tier) ────────────────────────────────────────────

_BODIES: runtime.JobBodies = {
    "submit_results_export": _submit_results_export_body,
    "stage_results_export": _stage_results_export_body,
    "load_results_export": _load_results_export_body,
}


def run_one(*, rwb_job_id: Any, rwb_job_type: str, worker_id: str = "worker") -> bool:
    return runtime.run_one(_BODIES, rwb_job_id=rwb_job_id,
                           rwb_job_type=rwb_job_type, worker_id=worker_id)


def run_pending(*, worker_id: str = "worker") -> int:
    return runtime.run_pending(_BODIES, worker_id=worker_id)


__all__ = [
    "ELT_COLUMN_MAP", "StageFailure",
    "submit_results_export", "stage_results_export", "load_results_export",
    "run_one", "run_pending",
]
