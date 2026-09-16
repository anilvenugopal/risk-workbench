"""Dramatiq actors for the loss results export (spec 014, contracts/jobs.md;
spec 016 for the treaty-level perspective TY).

``submit_results_export`` asks Risk Modeler for one loss-table export per
manifest row of an export. The poller enqueues ``stage_results_export`` when
that export job ends; the stage worker downloads the archive to the permanent
archive root, checks it against the manifest, and streams its Parquet files
into ``stage.rwb_loss_result_elt_data``. ``load_results_export`` runs
``stage.usp_load_elt_result``, which classifies, corrects, and loads the rows
into CIC's three tables in one transaction.

The stage and load workers act on every eligible manifest row of one analysis
in one export. A portfolio-level perspective has one such row. At TY the stage
worker splits the treaty-level table into one manifest row per treaty (the
analysis's own row becomes the first treaty's, siblings are copied from it),
combining each treaty's rows per event before upload; the load worker then
loads each treaty row through the same procedure. Every actor resumes from the
rows' ``stage_status`` / ``load_status``; nothing here recomputes what the
analyst approved on the form.
"""

from __future__ import annotations

import csv
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from dramatiq.middleware import TimeLimitExceeded

from app.config import settings
from app.services import irp_gateway, irp_job_service, rwb_job_service
from app.services._common import _uid, _utcnow
from app.services.export_service import DATA_NAME_MAX_LEN, TY
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
# The treaty-level table's columns (spec 016 research R1, T-10).
TY_COLUMNS = ("TreatyId", "TreatyNum", "TreatyName", "EventId", "Rate", "Loss",
              "StdDevI", "StdDevC", "ExpValue")
# The financial perspective a treaty-level request names. Risk Modeler returns
# the one TY table whatever code is sent (spec 016 P-08, research R1); GR is
# the code every analysis able to produce TY has, so the request never fails
# on it. Deliberate, not an input.
TY_REQUEST_PERSPECTIVE_CODE = "GR"
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


def _analysis_rows(export_id: str, irp_analysis_id: str) -> list[dict]:
    """Every manifest row of one analysis in one export: one row, or one per
    treaty once a TY table has been read."""
    return [dict(r) for r in execute(
        "SELECT * FROM stage.rwb_loss_result_manifest "
        "WHERE export_id = :e AND irp_analysis_id = :a ORDER BY manifest_id",
        {"e": export_id, "a": irp_analysis_id}, connection="LOSS")]


def _stage_eligible(row: dict) -> bool:
    return (row["stage_status"] != "staged" and row["load_status"] != "loaded"
            and row["closed_at"] is None)


def _load_eligible(row: dict) -> bool:
    """The procedure's own claim rule (contracts/load-procedure.md), plus never
    a row the analyst closed."""
    return (row["stage_status"] == "staged" and row["load_status"] in ("pending", "failed")
            and row["closed_at"] is None)


def _row_label(row: dict) -> str | None:
    return row.get("treaty_number") or row.get("treaty_name")


# ── submit_results_export ────────────────────────────────────────────────────


def _loss_details(perspective_code: str) -> list[dict]:
    if perspective_code == TY:
        return [{"metricType": "LOSS_TABLES", "outputLevels": ["Treaty"],
                 "perspectiveCodes": [TY_REQUEST_PERSPECTIVE_CODE]}]
    return [{"metricType": "LOSS_TABLES", "outputLevels": ["Portfolio"],
             "perspectiveCodes": [perspective_code]}]


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
                loss_details=_loss_details(row["perspective_code"]))
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


def _discard_partial_stage(manifest_id: int) -> None:
    execute_command("DELETE FROM stage.rwb_loss_result_elt_data WHERE manifest_id = :m",
                    {"m": manifest_id}, connection="LOSS")
    execute_command("DELETE FROM stage.rwb_loss_result_file WHERE manifest_id = :m",
                    {"m": manifest_id}, connection="LOSS")


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
    Risk Modeler writes the decimal form (``25.0``) or a build number
    (``23.0.2250.1``); CIC's ``Lookup_RMS_HistoricalRDS`` holds the whole number
    (``25``), and the load procedure joins the two by string equality."""
    parts = value.split(".")
    if len(parts) > 1 and all(part.isdigit() for part in parts):
        return parts[0]
    return value or None


def _perspective_files(table_dir: Path, output_level: str, perspective_code: str) -> list[Path]:
    level_dir = table_dir / output_level
    folders = {p.name for p in level_dir.iterdir() if p.is_dir()} if level_dir.is_dir() else set()
    if perspective_code not in folders:
        raise StageFailure(f"archive has no {output_level}/{perspective_code} folder")
    extra = sorted(folders - {perspective_code})
    if extra:
        raise StageFailure(
            f"archive holds perspective folders {', '.join(extra)} besides {perspective_code}")
    return sorted((level_dir / perspective_code).glob("*.parquet"), key=_chunk_index)


def _chunk_index(path: Path) -> int:
    match = _CHUNK_SUFFIX.search(path.name)
    return int(match.group(1)) if match else 0


def _stage_file(manifest_id: int, work_dir: Path, path: Path, perspective_code: str,
                output_level: str = "Portfolio") -> int:
    # upload_parquet checks the same thing, but its message names the mapping,
    # not the file the analyst has to look at.
    missing = [c for c in ELT_COLUMN_MAP if c not in pq.ParquetFile(path).schema.names]
    if missing:
        raise StageFailure(f"{path.name} is missing columns {', '.join(missing)}")
    result_file = path.relative_to(work_dir).as_posix()
    execute_command(
        "INSERT INTO stage.rwb_loss_result_file (manifest_id, result_file, output_level, "
        "perspective_code, chunk_index, staged_at) "
        "VALUES (:m, :f, :level, :p, :c, :now)",
        {"m": manifest_id, "f": result_file, "level": output_level, "p": perspective_code,
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


# ── the treaty-level table (spec 016) ────────────────────────────────────────


@dataclass
class TreatyLosses:
    """One treaty's combined loss rows out of a treaty-level table."""
    number: str
    name: str
    ids: list[str]
    rows: pd.DataFrame
    aal: float


def _read_treaty_table(files: list[Path]) -> pd.DataFrame:
    frames = []
    for path in files:
        names = pq.ParquetFile(path).schema.names
        missing = [c for c in TY_COLUMNS if c not in names]
        if missing:
            raise StageFailure(f"{path.name} is missing columns {', '.join(missing)}")
        frames.append(pq.read_table(path, columns=list(TY_COLUMNS)).to_pandas())
    if not frames:
        return pd.DataFrame(columns=list(TY_COLUMNS))
    return pd.concat(frames, ignore_index=True)


def _text_value(value: Any) -> str:
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)


def _combine_treaty_rows(table: pd.DataFrame) -> list[TreatyLosses]:
    """Spec P-11: within one analysis, one treaty's rows (whatever their treaty
    IDs) become one row per event — loss summed, independent standard deviation
    summed, correlated standard deviation the root of the sum of squares, the
    event's rate kept. The exposure value is the largest of the combined rows
    until spec O-04 is closed. Treaties come back in (number, name) order."""
    keys = ["TreatyNum", "TreatyName"]
    table = table.assign(_sq=table["StdDevC"] ** 2)
    combined = (table.groupby([*keys, "EventId"], sort=True, dropna=False)
                .agg(Rate=("Rate", "first"), Loss=("Loss", "sum"), StdDevI=("StdDevI", "sum"),
                     _sq=("_sq", "sum"), ExpValue=("ExpValue", "max"))
                .reset_index())
    combined["StdDevC"] = combined["_sq"] ** 0.5
    out: list[TreatyLosses] = []
    for (number, name), frame in combined.groupby(keys, sort=True, dropna=False):
        source = table[(table["TreatyNum"] == number) & (table["TreatyName"] == name)]
        ids = sorted({int(v) for v in source["TreatyId"].tolist()})
        out.append(TreatyLosses(
            number=_text_value(number), name=_text_value(name), ids=[str(i) for i in ids],
            rows=frame[["EventId", "Rate", "Loss", "StdDevI", "StdDevC", "ExpValue"]],
            aal=float((frame["Rate"] * frame["Loss"]).sum())))
    return out


def _treaty_data_name(base: str | None, number: str, name: str) -> str:
    """P-06 / T-07: the analyst's data name (the analysis name when blank), the
    treaty number, and the treaty name when it differs from the number."""
    parts = [base or "", number]
    if name and name != number:
        parts.append(name)
    return " ".join(p for p in parts if p)[:DATA_NAME_MAX_LEN]


def _write_treaty_file(source: Path, index: int, treaty: TreatyLosses) -> Path:
    """The derived per-treaty Parquet file beside the archive's TY files, in the
    nine ELT columns ``upload_parquet`` maps. ``PortInfoName`` and
    ``PortInfoNum`` carry the treaty so a staged row can be read alone."""
    n = len(treaty.rows)
    stem = re.sub(r"_\d+$", "", source.stem)
    path = source.with_name(f"{stem}__{index}.parquet")
    table = pa.table({
        "PortInfoId": pa.nulls(n, pa.int32()),
        "PortInfoName": pa.array([treaty.name] * n, pa.string()),
        "PortInfoNum": pa.array([treaty.number] * n, pa.string()),
        "EventId": pa.array([int(v) for v in treaty.rows["EventId"].tolist()], pa.int64()),
        "Rate": pa.array(treaty.rows["Rate"].astype(float).tolist(), pa.float64()),
        "Loss": pa.array(treaty.rows["Loss"].astype(float).tolist(), pa.float64()),
        "StdDevI": pa.array(treaty.rows["StdDevI"].astype(float).tolist(), pa.float64()),
        "StdDevC": pa.array(treaty.rows["StdDevC"].astype(float).tolist(), pa.float64()),
        "ExpValue": pa.array(treaty.rows["ExpValue"].astype(float).tolist(), pa.float64()),
    })
    pq.write_table(table, path)
    return path


_TREATY_ROW_COPIED_COLUMNS = (
    "export_id, requested_by_email, requested_at, requested_from_submission_id, "
    "irp_analysis_id, irp_analysis_irp_id, irp_app_analysis_id, analysis_name, "
    "analysis_description, perspective_code, client_id, treaty_incept, treaty_year, "
    "crm_id, data_vintage, data_currency, data_model_vendor, server, [database], "
    "irp_export_job_id, loss_table_type, engine_type, data_model_version, peril_code, "
    "region_code, zip_file")


def _insert_treaty_row(source: dict, number: str, name: str, data_name: str) -> dict:
    """A sibling manifest row for one more treaty of the analysis, copying every
    header value from ``source`` (P-09)."""
    now = _utcnow()
    execute_command(
        "INSERT INTO stage.rwb_loss_result_manifest ("
        f"{_TREATY_ROW_COPIED_COLUMNS}, data_name, treaty_number, treaty_name, "
        "stage_status, load_status, inserted_at, updated_at) "
        f"SELECT {_TREATY_ROW_COPIED_COLUMNS}, :data_name, :number, :name, "
        "'pending', 'pending', :now, :now "
        "FROM stage.rwb_loss_result_manifest WHERE manifest_id = :src",
        {"data_name": data_name, "number": number, "name": name, "now": now,
         "src": source["manifest_id"]}, connection="LOSS")
    return dict(execute_one(
        "SELECT * FROM stage.rwb_loss_result_manifest WHERE export_id = :e "
        "AND irp_analysis_id = :a AND treaty_number = :n AND treaty_name = :t",
        {"e": source["export_id"], "a": source["irp_analysis_id"], "n": number, "t": name},
        connection="LOSS"))


def _stage_treaties(rows: list[dict], targets: list[dict], table_dir: Path,
                    work_dir: Path) -> list[str]:
    """Split the treaty-level table into one staged manifest row per treaty
    (spec 016 contracts/jobs.md §4 step 6). Returns the per-row failure
    messages for target rows whose treaty the table does not hold."""
    files = _perspective_files(table_dir, "Treaty", TY)
    table = _read_treaty_table(files)
    if table.empty:
        raise StageFailure("Risk Modeler returned no treaty (TY) loss rows for this analysis")
    treaties = _combine_treaty_rows(table)

    unclaimed = [r for r in targets if r["treaty_number"] is None]
    base_row = unclaimed[0] if unclaimed else targets[0]
    base_name = base_row["data_name"] or base_row["analysis_name"]
    by_treaty = {(r["treaty_number"], r["treaty_name"]): r
                 for r in rows if r["treaty_number"] is not None}
    target_ids = {r["manifest_id"] for r in targets}
    staged: set[int] = set()
    for index, treaty in enumerate(treaties, start=1):
        row = by_treaty.get((treaty.number, treaty.name))
        if row is None:
            data_name = _treaty_data_name(base_name, treaty.number, treaty.name)
            if unclaimed:
                row = unclaimed.pop(0)
                _stamp_manifest(row["manifest_id"], treaty_number=treaty.number,
                                treaty_name=treaty.name, data_name=data_name)
            else:
                row = _insert_treaty_row(base_row, treaty.number, treaty.name, data_name)
                target_ids.add(row["manifest_id"])
        elif row["manifest_id"] not in target_ids:
            continue  # already staged, loaded, or closed
        path = _write_treaty_file(files[0], index, treaty)
        count = _stage_file(row["manifest_id"], work_dir, path, TY, output_level="Treaty")
        _stamp_manifest(row["manifest_id"], stage_status="staged", staged_at=_utcnow(),
                        staged_row_count=count, treaty_ids=",".join(treaty.ids),
                        aal=treaty.aal, error_message=None)
        staged.add(row["manifest_id"])

    failures: list[str] = []
    for row in targets:
        if row["manifest_id"] in staged or row["treaty_number"] is None:
            continue
        reason = (f"treaty {row['treaty_number']} {row['treaty_name']} is not in the loss "
                  "table Risk Modeler returned")
        _stamp_manifest(row["manifest_id"], stage_status="failed", error_message=reason)
        failures.append(reason)
    return failures


def _stage(targets: list[dict], rows: list[dict], irp_job_id: str, work_dir: Path) -> list[str]:
    """Steps 1–7 of contracts/jobs.md §4 for the eligible rows of one analysis;
    raises StageFailure with the reason, returns per-row failures (TY only)."""
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
    # The archive already on the share, if any row names one (research R6).
    anchor = next((r for r in targets if r["zip_file"] and (root / r["zip_file"]).is_file()),
                  targets[0])
    archive = _archive_path(anchor, job, root)

    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(work_dir)
    except zipfile.BadZipFile as exc:
        # Forget the archive so Retry downloads it again instead of reusing it.
        for row in targets:
            _stamp_manifest(row["manifest_id"], zip_file=None)
        raise StageFailure(f"{archive.name} is not a valid zip file; it will be "
                           "downloaded again on Retry") from exc

    table_dir = _loss_table_dir(work_dir)
    metadata = _read_metadata(table_dir)
    if metadata.get("AnlsId") != str(anchor["irp_app_analysis_id"]):
        raise StageFailure(f"archive AnlsId {metadata.get('AnlsId')!r} does not match "
                           f"analysis {anchor['irp_app_analysis_id']}")
    if metadata.get("AnalysisCurrency") != anchor["data_currency"]:
        raise StageFailure(f"archive currency {metadata.get('AnalysisCurrency')!r} does not "
                           f"match analysis currency {anchor['data_currency']!r}")
    facts = dict(loss_table_type=table_dir.name,
                 engine_type=metadata.get("Engine Type") or None,
                 data_model_version=_model_version(metadata.get("ModelVersion", "")),
                 zip_file=archive.relative_to(root).as_posix())
    for row in targets:
        _stamp_manifest(row["manifest_id"], **facts)

    perspective_code = anchor["perspective_code"]
    if perspective_code == TY:
        failures = _stage_treaties(rows, targets, table_dir, work_dir)
    else:
        manifest = targets[0]
        total = 0
        for path in _perspective_files(table_dir, "Portfolio", perspective_code):
            total += _stage_file(manifest["manifest_id"], work_dir, path, perspective_code)
        if total == 0:
            raise StageFailure(
                f"Risk Modeler returned no {perspective_code} loss rows for this analysis")
        _stamp_manifest(manifest["manifest_id"], stage_status="staged", staged_at=_utcnow(),
                        staged_row_count=total, error_message=None)
        failures = []
    _remove_dir(work_dir)
    return failures


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
        input_data={"export_id": _uid(manifest["export_id"]), "irp_analysis_id": analysis_id})
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="load_results_export")
    return job_id


def _fail_unstaged(export_id: str, analysis_id: str, reason: str) -> None:
    """Every exit but success stamps the rows: Retry is offered from the
    manifest alone, so an unstamped failure would be stuck for good."""
    for row in _analysis_rows(export_id, analysis_id):
        if _stage_eligible(row):
            _stamp_manifest(row["manifest_id"], stage_status="failed", error_message=reason)


def _stage_results_export_body(rwb_job_id: Any) -> runtime.JobResult:
    context = rwb_job_service.load_input_data(rwb_job_id)
    export_id, analysis_id = _uid(context["export_id"]), _uid(context["irp_analysis_id"])
    rows = _analysis_rows(export_id, analysis_id)
    if not rows:
        return runtime.JobResult.fail(f"no manifest row for export {export_id} analysis "
                                      f"{analysis_id}")
    if all(r["load_status"] == "loaded" for r in rows):
        return runtime.JobResult.ok(skipped="loaded")
    targets = [r for r in rows if _stage_eligible(r)]
    failures: list[str] = []
    if targets:
        work_dir = _working_dir(export_id, analysis_id)
        try:
            for row in targets:
                _discard_partial_stage(row["manifest_id"])
            _remove_dir(work_dir)
            failures = _stage(targets, rows, context["irp_job_id"], work_dir)
        except TimeLimitExceeded:
            _fail_unstaged(export_id, analysis_id, "the run exceeded the worker time limit")
            raise
        except StageFailure as exc:
            _fail_unstaged(export_id, analysis_id, str(exc))
            return runtime.JobResult.fail(str(exc))
        except Exception as exc:  # noqa: BLE001 — every stage failure lands on the manifest
            logger.exception("stage failed for export %s analysis %s", export_id, analysis_id)
            _fail_unstaged(export_id, analysis_id, str(exc))
            return runtime.JobResult.fail(str(exc))
    rows = _analysis_rows(export_id, analysis_id)
    loadable = [r for r in rows if _load_eligible(r)]
    load_job_id = None
    if loadable:
        try:
            load_job_id = _enqueue_load(rows[0], rwb_job_id)
        except Exception as exc:  # noqa: BLE001 — staged rows stay; Retry re-arms the load
            logger.exception("could not queue the load for export %s analysis %s",
                             export_id, analysis_id)
            reason = f"could not queue the load: {exc}"
            for row in loadable:
                _stamp_manifest(row["manifest_id"], load_status="failed", error_message=reason)
            return runtime.JobResult.fail(reason)
    staged_row_count = sum(r["staged_row_count"] or 0 for r in rows
                           if r["stage_status"] == "staged")
    if failures:
        return runtime.JobResult.fail("; ".join(failures), staged_row_count=staged_row_count,
                                      load_job_id=load_job_id)
    return runtime.JobResult.ok(staged_row_count=staged_row_count, load_job_id=load_job_id)


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


def _load_one(manifest_id: int) -> tuple[int | None, str | None]:
    """One procedure call; returns ``(data_id, None)`` or ``(None, reason)``."""
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
        return None, reason
    row = execute_one("SELECT data_id FROM stage.rwb_loss_result_manifest WHERE manifest_id = :m",
                      {"m": manifest_id}, connection="LOSS")
    return row["data_id"], None


def _load_results_export_body(rwb_job_id: Any) -> runtime.JobResult:
    context = rwb_job_service.load_input_data(rwb_job_id)
    export_id, analysis_id = _uid(context["export_id"]), _uid(context["irp_analysis_id"])
    rows = _analysis_rows(export_id, analysis_id)
    if not rows:
        return runtime.JobResult.fail(f"no manifest row for export {export_id} analysis "
                                      f"{analysis_id}")
    if all(r["load_status"] == "loaded" for r in rows):
        return runtime.JobResult.ok(skipped="loaded")
    eligible = [r for r in rows if _load_eligible(r)]
    if not eligible:
        return runtime.JobResult.fail("analysis is not staged")
    data_ids: list[int] = []
    reasons: list[str] = []
    for row in eligible:
        data_id, reason = _load_one(row["manifest_id"])
        if reason is not None:
            label = _row_label(row)
            reasons.append(f"{label}: {reason}" if label else reason)
        else:
            data_ids.append(data_id)
    if reasons:
        return runtime.JobResult.fail("; ".join(reasons), data_ids=data_ids)
    return runtime.JobResult.ok(data_ids=data_ids)


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
    "ELT_COLUMN_MAP", "TY_COLUMNS", "TY_REQUEST_PERSPECTIVE_CODE", "StageFailure",
    "submit_results_export", "stage_results_export", "load_results_export",
    "run_one", "run_pending",
]
