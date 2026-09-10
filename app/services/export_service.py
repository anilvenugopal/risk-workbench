"""Loss results export (spec 014): the export form's read models, the manifest
insert, the exports section and detail read models, status derivation, and the
Retry decision.

The export record is ``stage.rwb_loss_result_manifest`` in the loss repository
(``LOSS``), one row per analysis per perspective. The Workbench keeps only the
``rwb_job`` / ``irp_job`` rows that process it. Request-path reads of the
manifest carry ``read_uncommitted_hint("LOSS")`` because read-committed snapshot
isolation is off on CIC's repository. No function here calls Risk Modeler.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import text

from app.config import settings
from app.services import analysis_service, irp_job_service, rwb_job_service, submission_service
from app.services._common import _parse_json_dict, _uid, _utcnow
from app.workers import dispatch
from db import (
    execute,
    execute_command,
    execute_one,
    get_connection,
    is_unique_violation,
    read_uncommitted_hint,
)

logger = logging.getLogger(__name__)

# Displayed analysis statuses (data-model.md §7), derived top-down in derive_status.
PENDING = "pending"
REQUESTED = "requested from Risk Modeler"
STAGING = "downloading and staging"
STAGED = "staged"
LOADING = "loading"
LOADED = "loaded"
FAILED = "failed"
TERMINAL_STATUSES = frozenset({LOADED, FAILED})

DATA_NAME_MAX_LEN = 150
CRM_ID_MAX_LEN = 30
RETRY_EXPORT_JOB_MAX_AGE = timedelta(days=7)


class ExportError(Exception):
    pass


class ExportValidationError(ExportError):
    pass


class DuplicateExportError(ExportValidationError):
    """One or more selected analyses already have an export for the perspective."""

    def __init__(self, blocked: list[tuple["ExportableAnalysis", "ExportedMark"]]) -> None:
        self.blocked = blocked
        super().__init__("; ".join(
            f"{analysis.name} was already exported for {mark.perspective_code} "
            f"on {mark.requested_at} by {mark.requested_by_email}"
            for analysis, mark in blocked))


class ExportNotFound(ExportError):
    pass


class ExportRetryRefused(ExportError):
    pass


# ── read models ──────────────────────────────────────────────────────────────


@dataclass
class ExportedMark:
    """An existing manifest row for (irp_app_analysis_id, perspective), from any
    submission — the block and the link the form shows (FR-004, P-16)."""
    irp_app_analysis_id: int
    perspective_code: str
    export_id: str
    requested_from_submission_id: str
    requested_at: Any
    requested_by_email: str
    status: str

    @property
    def detail_url(self) -> str:
        return f"/submissions/{self.requested_from_submission_id}/exports/{self.export_id}"


@dataclass
class ExportableAnalysis:
    id: str
    name: str                      # display name (full name when known)
    origin: str                    # own | group | broker
    rdm_name: str | None
    irp_id: str | None
    irp_app_analysis_id: int | None
    analysis_name: str | None      # irp_analysis.name → Data.Name
    analysis_description: str | None  # irp_analysis.full_name → Data.Description
    perspectives: list[str]
    peril_code: str | None
    region_code: str | None
    currency: str | None
    disabled_reason: str | None = None
    exported: ExportedMark | None = None

    @property
    def exportable(self) -> bool:
        return self.disabled_reason is None and self.exported is None


@dataclass
class Client:
    id: int
    name: str


@dataclass
class ExportAnalysisDetail:
    manifest_id: int
    irp_analysis_id: str
    analysis_name: str | None
    origin: str
    status: str
    updated_at: Any
    irp_export_job_id: str | None
    zip_file: str | None
    data_name: str | None
    irp_app_analysis_id: int | None
    data_currency: str | None
    data_model_version: str | None
    engine_type: str | None
    peril_code: str | None
    region_code: str | None
    data_id: int | None
    staged_row_count: int | None
    stochastic_row_count: int | None
    historical_row_count: int | None
    exp_value_raised_count: int | None
    std_dev_zeroed_count: int | None
    error_message: str | None

    @property
    def archive_path(self) -> str | None:
        if not self.zip_file:
            return None
        root = settings.export_archive_dir.rstrip("/\\")
        return f"{root}/{self.zip_file}" if root else self.zip_file

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def can_retry(self) -> bool:
        return self.status == FAILED


@dataclass
class ExportDetail:
    export_id: str
    submission_id: str
    perspective_code: str
    client_id: int
    client_name: str | None
    treaty_incept: Any
    crm_id: str | None
    data_vintage: Any
    requested_by_email: str
    requested_at: Any
    analyses: list[ExportAnalysisDetail] = field(default_factory=list)

    @property
    def in_progress(self) -> bool:
        return any(not a.is_terminal for a in self.analyses)


@dataclass
class ExportSummary:
    export_id: str
    perspective_code: str
    requested_by_email: str
    requested_at: Any
    client_name: str | None
    analyses: list[ExportAnalysisDetail] = field(default_factory=list)

    @property
    def analysis_count(self) -> int:
        return len(self.analyses)

    @property
    def loaded_count(self) -> int:
        return sum(1 for a in self.analyses if a.status == LOADED)

    @property
    def failed_count(self) -> int:
        return sum(1 for a in self.analyses if a.status == FAILED)

    @property
    def in_progress(self) -> bool:
        return any(not a.is_terminal for a in self.analyses)

    @property
    def progress(self) -> str:
        """The roll-up the exports section shows in place of a status column."""
        counts = ((self.loaded_count, "loaded"), (self.failed_count, "failed"),
                  (self.analysis_count - self.loaded_count - self.failed_count,
                   "in progress"))
        return " · ".join(f"{n} {label}" for n, label in counts if n)


# ── status derivation ────────────────────────────────────────────────────────


def derive_status(manifest: dict, export_job: dict | None) -> str:
    """The displayed status of one analysis (data-model.md §7), evaluated
    top-down from the manifest row and its ``export`` irp_job, if any."""
    if manifest["load_status"] == "loaded":
        return LOADED
    if manifest["stage_status"] == "failed" or manifest["load_status"] == "failed":
        return FAILED
    if manifest["load_status"] == "loading":
        return LOADING
    if manifest["stage_status"] == "staged":
        return STAGED
    if manifest["irp_export_job_id"] is None:
        return PENDING
    # A terminal Risk Modeler job of any outcome hands the row to the stage
    # worker, which stamps failed itself when the job did not finish.
    if (export_job or {}).get("status") in irp_job_service.TERMINAL:
        return STAGING
    return REQUESTED


def _export_jobs(manifests: list[dict]) -> dict[str, dict]:
    """The ``export`` irp_job of each manifest row, keyed by Risk Modeler job id."""
    irp_ids = sorted({str(m["irp_export_job_id"]) for m in manifests
                      if m.get("irp_export_job_id") is not None})
    if not irp_ids:
        return {}
    params = {f"j{i}": v for i, v in enumerate(irp_ids)}
    rows = execute(
        "SELECT id, irp_id, status, completed_at "
        "FROM irp_job WHERE irp_job_type = 'export' "
        f"AND irp_id IN ({', '.join(':' + k for k in params)})",
        params, connection="WORKBENCH")
    return {str(r["irp_id"]): dict(r) for r in rows}


# ── the form ─────────────────────────────────────────────────────────────────


def _app_analysis_id(raw: Any) -> tuple[int | None, str | None]:
    if raw is None or str(raw).strip() == "":
        return None, "the analysis has no Risk Modeler application analysis ID"
    try:
        return int(str(raw).strip()), None
    except ValueError:
        return None, (f"Risk Modeler application analysis ID {raw!r} is not a whole "
                      "number")


def list_exportable_analyses(submission_id: Any) -> list[ExportableAnalysis] | None:
    """The submission's analyses in the analyses section's order — own and
    group rows first, then broker rows grouped by RDM — each marked exportable
    or disabled with the reason (FR-001, FR-005). ``None`` when the submission
    does not resolve."""
    rows = analysis_service.list_comparable_analyses(submission_id=submission_id)
    if rows is None:
        return None
    if not rows:
        return []
    params = {f"i{n}": r.id for n, r in enumerate(rows)}
    detail = {_uid(d["id"]): d for d in execute(
        "SELECT id, irp_id, irp_app_analysis_id, name, full_name, is_group, "
        "settings_metadata, loss_results FROM irp_analysis "
        f"WHERE id IN ({', '.join(':' + k for k in params)})",
        params, connection="WORKBENCH")}
    out: list[ExportableAnalysis] = []
    for r in rows:
        d = detail.get(_uid(r.id), {})
        parsed = _parse_json_dict(d.get("settings_metadata"), "settings_metadata")
        display = analysis_service._to_display(parsed)
        loss_results = _parse_json_dict(d.get("loss_results"), "loss_results") or {}
        perspectives = [code for code, data in (loss_results.get("perspectives") or {}).items()
                        if data]
        # The column, else the metadata snapshot's appAnalysisId (spec 012
        # FR-023): only the own-executed finalize path writes the column, so
        # every RDM-backfilled broker row carries the id in its snapshot alone.
        app_id, reason = _app_analysis_id(
            d.get("irp_app_analysis_id") or (parsed or {}).get("appAnalysisId"))
        if r.results_state == "failed":
            reason = "results retrieval failed"
        elif r.results_state != "ready" or not perspectives:
            reason = "results not retrieved yet"
        elif display.currency is None:
            reason = "the analysis currency is not recorded"
        out.append(ExportableAnalysis(
            id=_uid(r.id), name=r.name or d.get("name") or _uid(r.id),
            origin=("broker" if r.rdm_name else "group" if d.get("is_group") else "own"),
            rdm_name=r.rdm_name,
            irp_id=(str(d["irp_id"]) if d.get("irp_id") is not None else None),
            irp_app_analysis_id=app_id,
            analysis_name=d.get("name"), analysis_description=d.get("full_name"),
            perspectives=perspectives, peril_code=display.peril,
            region_code=display.region, currency=display.currency,
            disabled_reason=reason))
    return out


def perspective_choices(selected: list[ExportableAnalysis]) -> list[str]:
    """The configured codes, in configured order, that every selected analysis
    has results for (FR-003); empty with no selection or an empty intersection."""
    if not selected:
        return []
    return [code for code in settings.export_perspective_codes
            if all(code in a.perspectives for a in selected)]


def find_exported(irp_app_analysis_ids: list[int],
                  perspective_code: str) -> dict[int, ExportedMark]:
    """Existing manifest rows for the analyses at this perspective, from any
    submission, keyed by ``irp_app_analysis_id`` (FR-004)."""
    ids = sorted({int(v) for v in irp_app_analysis_ids if v is not None})
    if not ids:
        return {}
    params: dict[str, Any] = {f"a{i}": v for i, v in enumerate(ids)}
    params["p"] = perspective_code
    rows = execute(
        "SELECT export_id, requested_from_submission_id, requested_at, "
        "requested_by_email, irp_app_analysis_id, perspective_code, irp_export_job_id, "
        "stage_status, load_status "
        f"FROM stage.rwb_loss_result_manifest {read_uncommitted_hint('LOSS')} "
        f"WHERE perspective_code = :p AND irp_app_analysis_id IN "
        f"({', '.join(':' + k for k in params if k != 'p')})",
        params, connection="LOSS")
    jobs = _export_jobs(rows)
    return {int(r["irp_app_analysis_id"]): ExportedMark(
        irp_app_analysis_id=int(r["irp_app_analysis_id"]),
        perspective_code=r["perspective_code"], export_id=_uid(r["export_id"]),
        requested_from_submission_id=_uid(r["requested_from_submission_id"]),
        requested_at=r["requested_at"], requested_by_email=r["requested_by_email"],
        status=derive_status(r, jobs.get(str(r["irp_export_job_id"]))),
    ) for r in rows}


def mark_exported(selected: list[ExportableAnalysis], perspective_code: str) -> None:
    """Attach the existing export, if any, to each selected analysis."""
    marks = find_exported([a.irp_app_analysis_id for a in selected], perspective_code)
    for a in selected:
        a.exported = marks.get(a.irp_app_analysis_id) if a.irp_app_analysis_id else None


def list_clients() -> list[Client]:
    return [Client(id=int(r["ClientID"]), name=r["ClientName"]) for r in execute(
        "SELECT ClientID, ClientName FROM dbo.Client WHERE ActiveFlag = 'Y' "
        "ORDER BY ClientName, ClientID", {}, connection="LOSS")]


# ── submit ───────────────────────────────────────────────────────────────────

_MANIFEST_INSERT = """
    INSERT INTO stage.rwb_loss_result_manifest (
        export_id, requested_by_email, requested_at, requested_from_submission_id,
        irp_analysis_id, irp_analysis_irp_id, irp_app_analysis_id, analysis_name,
        analysis_description, perspective_code, client_id, treaty_incept, treaty_year,
        crm_id, data_name, data_vintage, data_currency, data_model_vendor, server,
        peril_code, region_code, stage_status, load_status, inserted_at, updated_at)
    VALUES (
        :export_id, :requested_by_email, :now, :submission_id,
        :irp_analysis_id, :irp_analysis_irp_id, :irp_app_analysis_id, :analysis_name,
        :analysis_description, :perspective_code, :client_id, :treaty_incept, :treaty_year,
        :crm_id, :data_name, :data_vintage, :data_currency, 'RMS', :server,
        :peril_code, :region_code, 'pending', 'pending', :now, :now)
"""


def create_export(*, submission_id: Any, user_email: str, analysis_ids: list[str],
                  perspective_code: str, client_id: int | None, treaty_incept: Any,
                  crm_id: str | None, data_vintage: Any,
                  data_names: dict[str, str] | None = None) -> str:
    """Validate in the contracts/routes.md §4 order, insert one manifest row per
    analysis in one LOSS transaction, enqueue ``submit_results_export``, and
    return the new ``export_id``. Never writes to the submission (P-15)."""
    submission = submission_service.get_submission(submission_id)
    analyses = list_exportable_analyses(submission_id) if submission else None
    if analyses is None:
        raise ExportValidationError("This submission no longer exists.")
    by_id = {a.id: a for a in analyses}
    analysis_ids = list(dict.fromkeys(analysis_ids))
    if not analysis_ids:
        raise ExportValidationError("Select at least one analysis.")
    selected: list[ExportableAnalysis] = []
    for raw in analysis_ids:
        analysis = by_id.get(_uid(raw))
        if analysis is None:
            raise ExportValidationError(f"Analysis {raw} is not part of this submission.")
        if analysis.disabled_reason:
            raise ExportValidationError(
                f"{analysis.name} cannot be exported: {analysis.disabled_reason}.")
        selected.append(analysis)
    if perspective_code not in settings.export_perspective_codes:
        raise ExportValidationError("Choose a perspective.")
    for analysis in selected:
        if perspective_code not in analysis.perspectives:
            raise ExportValidationError(
                f"{analysis.name} has no {perspective_code} results.")
    if client_id is None or execute_one(
            "SELECT 1 AS x FROM dbo.Client WHERE ClientID = :c AND ActiveFlag = 'Y'",
            {"c": client_id}, connection="LOSS") is None:
        raise ExportValidationError("Choose an active client.")
    if treaty_incept is None:
        raise ExportValidationError("Treaty inception is required.")
    crm_id = (crm_id or "").strip() or None
    if crm_id and len(crm_id) > CRM_ID_MAX_LEN:
        raise ExportValidationError(f"CRM ID is longer than {CRM_ID_MAX_LEN} characters.")
    names = {_uid(k): (v or "").strip() for k, v in (data_names or {}).items()}
    for analysis in selected:
        if len(names.get(analysis.id, "")) > DATA_NAME_MAX_LEN:
            raise ExportValidationError(
                f"Data name for {analysis.name} is longer than {DATA_NAME_MAX_LEN} "
                "characters.")
    _raise_if_exported(selected, perspective_code)

    export_id = str(uuid.uuid4())
    now = _utcnow()
    try:
        with get_connection("LOSS") as conn, conn.begin():
            for analysis in selected:
                conn.execute(text(_MANIFEST_INSERT), {
                    "export_id": export_id, "requested_by_email": user_email, "now": now,
                    "submission_id": _uid(submission_id),
                    "irp_analysis_id": analysis.id, "irp_analysis_irp_id": analysis.irp_id,
                    "irp_app_analysis_id": analysis.irp_app_analysis_id,
                    "analysis_name": analysis.analysis_name,
                    "analysis_description": analysis.analysis_description,
                    "perspective_code": perspective_code, "client_id": int(client_id),
                    "treaty_incept": treaty_incept,
                    "treaty_year": submission.treaty_year,
                    "crm_id": crm_id, "data_name": names.get(analysis.id) or None,
                    "data_vintage": data_vintage, "data_currency": analysis.currency,
                    "server": settings.risk_modeler_base_url or None,
                    "peril_code": analysis.peril_code, "region_code": analysis.region_code,
                })
    except Exception as exc:  # noqa: BLE001 — a concurrent submit won the unique index
        if is_unique_violation(exc):
            _raise_if_exported(selected, perspective_code)
        raise
    try:
        job_id = rwb_job_service.enqueue_rwb_job(
            requestor_type="analyst_request", requestor_id=export_id,
            rwb_job_type="submit_results_export",
            link_type="not_applicable", link_id=None,
            context_type="result_export", context_id=export_id,
            input_data={"export_id": export_id, "submission_id": _uid(submission_id)})
    except Exception as exc:  # noqa: BLE001 — the manifest is committed; fail its rows so Retry applies
        logger.exception("submit_results_export enqueue failed for export %s", export_id)
        execute_command(
            "UPDATE stage.rwb_loss_result_manifest SET stage_status = 'failed', "
            "error_message = :e, updated_at = :now WHERE export_id = :x "
            "AND stage_status = 'pending'",
            {"e": f"could not queue the Risk Modeler request: {exc}", "now": _utcnow(),
             "x": export_id}, connection="LOSS")
        return export_id
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="submit_results_export")
    return export_id


def _raise_if_exported(selected: list[ExportableAnalysis], perspective_code: str) -> None:
    marks = find_exported([a.irp_app_analysis_id for a in selected], perspective_code)
    blocked = [(a, marks[a.irp_app_analysis_id]) for a in selected
               if a.irp_app_analysis_id in marks]
    if blocked:
        raise DuplicateExportError(blocked)


# ── exports section and detail page ──────────────────────────────────────────


def list_exports(submission_id: Any) -> list[ExportSummary]:
    """One row per export requested from this submission, newest first (P-16),
    each carrying its analyses so the section can expand to them. The analyses
    are built by the same helper the detail page uses, so the two pages can
    never disagree about a status."""
    rows = [dict(r) for r in execute(
        "SELECT m.*, c.ClientName AS client_name "
        f"FROM stage.rwb_loss_result_manifest m {read_uncommitted_hint('LOSS')} "
        "LEFT JOIN dbo.Client c ON c.ClientID = m.client_id "
        "WHERE m.requested_from_submission_id = :s "
        "ORDER BY m.requested_at DESC, m.export_id, "
        "m.analysis_description, m.analysis_name, m.manifest_id",
        {"s": _uid(submission_id)}, connection="LOSS")]
    if not rows:
        return []
    jobs = _export_jobs(rows)
    origins = _origins([_uid(r["irp_analysis_id"]) for r in rows])
    summaries: dict[str, ExportSummary] = {}
    for r in rows:
        key = _uid(r["export_id"])
        summary = summaries.get(key)
        if summary is None:
            summary = summaries[key] = ExportSummary(
                export_id=key, perspective_code=r["perspective_code"],
                requested_by_email=r["requested_by_email"], requested_at=r["requested_at"],
                client_name=r["client_name"])
        summary.analyses.append(_analysis_detail(r, jobs, origins))
    return list(summaries.values())


def _origins(irp_analysis_ids: list[str]) -> dict[str, str]:
    if not irp_analysis_ids:
        return {}
    params = {f"i{n}": v for n, v in enumerate(irp_analysis_ids)}
    return {_uid(r["id"]): ("broker" if r["rdm_id"] else "group" if r["is_group"] else "own")
            for r in execute(
                "SELECT id, is_group, rdm_id FROM irp_analysis "
                f"WHERE id IN ({', '.join(':' + k for k in params)})",
                params, connection="WORKBENCH")}


def _analysis_detail(row: dict, jobs: dict[str, dict], origins: dict[str, str]
                     ) -> ExportAnalysisDetail:
    job = jobs.get(str(row["irp_export_job_id"])) if row["irp_export_job_id"] else None
    return ExportAnalysisDetail(
        manifest_id=row["manifest_id"], irp_analysis_id=_uid(row["irp_analysis_id"]),
        analysis_name=row["analysis_description"] or row["analysis_name"],
        origin=origins.get(_uid(row["irp_analysis_id"]), "own"),
        status=derive_status(row, job), updated_at=row["updated_at"],
        irp_export_job_id=row["irp_export_job_id"], zip_file=row["zip_file"],
        data_name=row["data_name"], irp_app_analysis_id=row["irp_app_analysis_id"],
        data_currency=row["data_currency"], data_model_version=row["data_model_version"],
        engine_type=row["engine_type"], peril_code=row["peril_code"],
        region_code=row["region_code"],
        data_id=row["data_id"], staged_row_count=row["staged_row_count"],
        stochastic_row_count=row["stochastic_row_count"],
        historical_row_count=row["historical_row_count"],
        exp_value_raised_count=row["exp_value_raised_count"],
        std_dev_zeroed_count=row["std_dev_zeroed_count"],
        error_message=row["error_message"])


def get_export_detail(submission_id: Any, export_id: Any) -> ExportDetail | None:
    """The export's header and one row per analysis; ``None`` when no manifest
    row of this export was requested from this submission."""
    rows = execute(
        "SELECT m.*, c.ClientName AS client_name "
        f"FROM stage.rwb_loss_result_manifest m {read_uncommitted_hint('LOSS')} "
        "LEFT JOIN dbo.Client c ON c.ClientID = m.client_id "
        "WHERE m.export_id = :e AND m.requested_from_submission_id = :s "
        "ORDER BY m.analysis_description, m.analysis_name, m.manifest_id",
        {"e": _uid(export_id), "s": _uid(submission_id)}, connection="LOSS")
    if not rows:
        return None
    jobs = _export_jobs(rows)
    origins = _origins([_uid(r["irp_analysis_id"]) for r in rows])
    first = rows[0]
    return ExportDetail(
        export_id=_uid(first["export_id"]), submission_id=_uid(submission_id),
        perspective_code=first["perspective_code"], client_id=int(first["client_id"]),
        client_name=first["client_name"], treaty_incept=first["treaty_incept"],
        crm_id=first["crm_id"], data_vintage=first["data_vintage"],
        requested_by_email=first["requested_by_email"], requested_at=first["requested_at"],
        analyses=[_analysis_detail(r, jobs, origins) for r in rows])


# ── retry ────────────────────────────────────────────────────────────────────

RetryBranch = Literal["load", "stage", "submit"]


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def retry_decision(manifest: dict, export_job: dict | None, archive_root: str,
                   now: datetime) -> RetryBranch:
    """Which single job Retry re-arms (T-28): the load for a staged row; the
    stage when the archive is on the share or the Risk Modeler export is still
    downloadable (FINISHED within seven days); otherwise a new Risk Modeler
    export request."""
    if manifest["stage_status"] == "staged":
        return "load"
    if export_job is None:
        return "submit"
    zip_file = manifest.get("zip_file")
    if zip_file and archive_root and (Path(archive_root) / zip_file).is_file():
        return "stage"
    if export_job.get("status") == "FINISHED":
        completed = _as_datetime(export_job.get("completed_at"))
        if completed is not None and now - completed <= RETRY_EXPORT_JOB_MAX_AGE:
            return "stage"
    return "submit"


def apply_retry(submission_id: Any, export_id: Any, irp_analysis_id: Any) -> RetryBranch:
    """Re-arm exactly one job for a failed analysis and dispatch it. Raises
    ``ExportNotFound`` when the manifest row is not this submission's and
    ``ExportRetryRefused`` when the manifest row itself is not failed (a Risk
    Modeler failure the stage worker has not stamped yet is not failed yet)."""
    manifest = execute_one(
        f"SELECT * FROM stage.rwb_loss_result_manifest {read_uncommitted_hint('LOSS')} "
        "WHERE export_id = :e AND irp_analysis_id = :a AND requested_from_submission_id = :s",
        {"e": _uid(export_id), "a": _uid(irp_analysis_id), "s": _uid(submission_id)},
        connection="LOSS")
    if manifest is None:
        raise ExportNotFound("That analysis is not part of this export.")
    job = _export_jobs([manifest]).get(str(manifest["irp_export_job_id"]))
    status = derive_status(manifest, job)
    if status == LOADED:
        raise ExportRetryRefused(f"already loaded as data ID {manifest['data_id']}")
    if status != FAILED:
        raise ExportRetryRefused(f"the analysis is {status}, not failed")

    branch = retry_decision(manifest, job, settings.export_archive_dir, _utcnow())
    analysis_id = _uid(manifest["irp_analysis_id"])
    export_key = _uid(manifest["export_id"])
    analysis = execute_one("SELECT edm_id, rdm_id FROM irp_analysis WHERE id = :a",
                           {"a": analysis_id}, connection="WORKBENCH") or {}
    link_type, link_id = rwb_job_service.analysis_link(analysis.get("edm_id"),
                                                       analysis.get("rdm_id"))

    # Each branch first puts the row back into the state its job runs from, so
    # the detail page reads it as in progress (and polls) until the job stamps it.
    if branch == "load":
        stage_job = execute_one(
            "SELECT id FROM rwb_job WHERE rwb_job_type = 'stage_results_export' "
            "AND requestor_type = 'irp_job' AND requestor_id = :r",
            {"r": str((job or {}).get("id"))}, connection="WORKBENCH")
        if stage_job is None:
            raise ExportRetryRefused("no stage job is recorded for this analysis")
        execute_command(
            "UPDATE stage.rwb_loss_result_manifest SET load_status = 'pending', "
            "error_message = NULL, updated_at = :now WHERE manifest_id = :m",
            {"now": _utcnow(), "m": manifest["manifest_id"]}, connection="LOSS")
        job_type = "load_results_export"
        rwb_job_id = rwb_job_service.ensure_pending_rwb_job(
            requestor_type="rwb_job", requestor_id=stage_job["id"], rwb_job_type=job_type,
            link_type=link_type, link_id=link_id,
            context_type="irp_analysis", context_id=analysis_id,
            input_data={"export_id": export_key, "irp_analysis_id": analysis_id,
                        "manifest_id": manifest["manifest_id"]})
    elif branch == "stage":
        execute_command(
            "UPDATE stage.rwb_loss_result_manifest SET stage_status = 'pending', "
            "error_message = NULL, updated_at = :now WHERE manifest_id = :m",
            {"now": _utcnow(), "m": manifest["manifest_id"]}, connection="LOSS")
        job_type = "stage_results_export"
        rwb_job_id = rwb_job_service.ensure_pending_rwb_job(
            requestor_type="irp_job", requestor_id=job["id"], rwb_job_type=job_type,
            link_type=link_type, link_id=link_id,
            context_type="irp_analysis", context_id=analysis_id,
            input_data={"export_id": export_key, "irp_analysis_id": analysis_id,
                        "irp_job_id": _uid(job["id"])})
    else:
        execute_command(
            "UPDATE stage.rwb_loss_result_manifest SET irp_export_job_id = NULL, "
            "stage_status = 'pending', error_message = NULL, updated_at = :now "
            "WHERE manifest_id = :m",
            {"now": _utcnow(), "m": manifest["manifest_id"]}, connection="LOSS")
        job_type = "submit_results_export"
        rwb_job_id = rwb_job_service.ensure_pending_rwb_job(
            requestor_type="analyst_request", requestor_id=export_key, rwb_job_type=job_type,
            link_type="not_applicable", link_id=None,
            context_type="result_export", context_id=export_key,
            input_data={"export_id": export_key,
                        "submission_id": _uid(manifest["requested_from_submission_id"])})
    dispatch.dispatch(rwb_job_id=rwb_job_id, rwb_job_type=job_type)
    return branch


__all__ = [
    "PENDING", "REQUESTED", "STAGING", "STAGED", "LOADING", "LOADED", "FAILED",
    "TERMINAL_STATUSES", "DATA_NAME_MAX_LEN",
    "ExportError", "ExportValidationError", "DuplicateExportError", "ExportNotFound",
    "ExportRetryRefused",
    "ExportedMark", "ExportableAnalysis", "Client",
    "ExportAnalysisDetail", "ExportDetail", "ExportSummary",
    "derive_status", "list_exportable_analyses", "perspective_choices", "find_exported",
    "mark_exported", "list_clients", "create_export", "list_exports",
    "get_export_detail", "retry_decision", "apply_retry",
]
