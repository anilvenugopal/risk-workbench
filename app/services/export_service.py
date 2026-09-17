"""Loss results export (spec 014): the export form's read models, the manifest
insert, the exports table's row model, status derivation, and the Retry and
Close decisions.

The export record is ``stage.rwb_loss_result_manifest`` in the loss repository
(``LOSS``), one row per analysis per perspective — or, at the treaty-level
perspective TY (spec 016), one row per treaty the analyst ticked on the export
form. The Workbench keeps only the ``rwb_job`` / ``irp_job`` rows that
process it. Request-path reads of the
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
from app.services import (
    analysis_service,
    rwb_job_service,
    submission_service,
    treaty_service,
)
from app.services._common import _parse_json_dict, _rm_ui_root, _uid, _utcnow
from app.workers import dispatch
from db import (
    execute,
    execute_command,
    execute_one,
    get_connection,
    get_connection_config,
    read_uncommitted_hint,
)

logger = logging.getLogger(__name__)

# Displayed analysis statuses (data-model.md §7), derived in derive_status.
QUEUED = "queued"
IN_PROGRESS = "in progress"
LOADED = "loaded"
FAILED = "failed"
CLOSED = "closed"
TERMINAL_STATUSES = frozenset({LOADED, FAILED, CLOSED})

DATA_NAME_MAX_LEN = 150
CRM_ID_MAX_LEN = 30
RETRY_EXPORT_JOB_MAX_AGE = timedelta(days=7)
# The treaty-level export (spec 016). Offered when every selected analysis was
# run with treaties; the analyst ticks the treaties to write and names each one
# on the form, and every ticked treaty gets its own manifest row at submit.
TY = "TY"


class ExportError(Exception):
    pass


class ExportValidationError(ExportError):
    pass


class ExportNotFound(ExportError):
    pass


class ExportActionRefused(ExportError):
    pass


# ── read models ──────────────────────────────────────────────────────────────


@dataclass
class ExportedMark:
    """The newest existing manifest row for (irp_app_analysis_id, perspective),
    from any submission — the warning and the link the form shows (FR-004,
    P-16). ``earlier_count`` counts every such row, this one included."""
    irp_app_analysis_id: int
    perspective_code: str
    export_id: str
    requested_from_submission_id: str
    requested_at: Any
    requested_by_email: str
    status: str
    earlier_count: int = 1

    @property
    def exports_url(self) -> str:
        return f"/submissions/{self.requested_from_submission_id}#submission-exports"


@dataclass
class TreatyChoice:
    """One treaty of an analysis as the export form's cart lists it (FR-002,
    P-12): the identity the analyst ticks, and the four terms that tell two
    layers of one program apart, formatted for display."""
    number: str
    name: str
    type_label: str
    risk_limit: str
    attachment_point: str
    occurrence_limit: str


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
    aal: dict[str, float | None] = field(default_factory=dict)
    # The treaties Risk Modeler applied when the analysis ran, as the results
    # retrieval recorded them: what the cart offers to tick at TY (FR-002).
    treaties: list[dict] = field(default_factory=list)
    disabled_reason: str | None = None
    exported: ExportedMark | None = None

    @property
    def exportable(self) -> bool:
        return self.disabled_reason is None

    def aal_display(self, perspective_code: str) -> str:
        return analysis_service.fmt_loss(self.aal.get(perspective_code))

    @property
    def treaty_choices(self) -> list[TreatyChoice]:
        return [TreatyChoice(
            number=t.get("treaty_number") or "",
            name=t.get("treaty_name") or "",
            type_label=str(treaty_service.display_value(t.get("treaty_type"),
                                                        key="treatyType") or ""),
            risk_limit=analysis_service.fmt_loss(t.get("risk_limit")),
            attachment_point=analysis_service.fmt_loss(t.get("attachment_point")),
            occurrence_limit=analysis_service.fmt_loss(t.get("occurrence_limit")))
            for t in self.treaties]


@dataclass
class Client:
    id: int
    name: str


@dataclass
class ExportAnalysisDetail:
    """One row of the exports table (data-model.md §7): the manifest row's
    values — an analysis at a portfolio-level perspective, or one ticked treaty
    of an analysis at TY (spec 016 P-09) — plus its export's ordinal within the
    submission (1 = newest)."""
    manifest_id: int
    export_id: str
    export_ordinal: int
    irp_analysis_id: str
    analysis_name: str | None
    origin: str
    treaty_number: str | None
    treaty_name: str | None
    treaty_ids: list[str]
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
    aal: float | None
    staged_row_count: int | None
    stochastic_row_count: int | None
    historical_row_count: int | None
    exp_value_raised_count: int | None
    std_dev_zeroed_count: int | None
    error_message: str | None
    closed_at: Any
    closed_by: str | None
    perspective_code: str
    client_name: str | None
    crm_id: str | None
    treaty_incept: Any
    data_vintage: Any
    requested_by_email: str
    requested_at: Any

    @property
    def aal_display(self) -> str:
        return analysis_service.fmt_loss(self.aal)

    @property
    def treaty_label(self) -> str:
        """``PR1``, or ``PR1 · Layer one`` when the name differs from the
        number; empty for a portfolio row."""
        if not self.treaty_number and not self.treaty_name:
            return ""
        if self.treaty_name and self.treaty_name != self.treaty_number:
            return f"{self.treaty_number or ''} · {self.treaty_name}".strip(" ·")
        return self.treaty_number or ""

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def can_retry(self) -> bool:
        return self.status == FAILED


# ── status derivation ────────────────────────────────────────────────────────


def derive_status(manifest: dict) -> str:
    """The displayed status of one analysis (data-model.md §7). The manifest
    row decides it alone: a closed analysis reads closed whatever else the row
    carries (P-22), and a Risk Modeler export that ended FAILED reads as in
    progress until the stage worker stamps ``stage_status = failed``."""
    if manifest["closed_at"] is not None:
        return CLOSED
    if manifest["load_status"] == "loaded":
        return LOADED
    if manifest["stage_status"] == "failed" or manifest["load_status"] == "failed":
        return FAILED
    if manifest["irp_export_job_id"] is None:
        return QUEUED
    return IN_PROGRESS


def _export_job(irp_id: Any) -> dict | None:
    """The ``export`` irp_job the submit worker recorded for a manifest row."""
    if irp_id is None:
        return None
    row = execute_one(
        "SELECT id, irp_id, status, completed_at FROM irp_job "
        "WHERE irp_job_type = 'export' AND irp_id = :j",
        {"j": str(irp_id)}, connection="WORKBENCH")
    return dict(row) if row else None


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
        produced = {code: data for code, data
                    in (loss_results.get("perspectives") or {}).items() if data}
        perspectives = list(produced)
        # Run with treaties (spec 016 FR-001): the treaties Risk Modeler reported
        # at results retrieval, one entry per (number, name) — a group repeats a
        # treaty once per member. The list gates the offer and is what the cart
        # lists to tick at TY.
        treaties = list({(t.get("treaty_number"), t.get("treaty_name")): t
                         for t in loss_results.get("treaties") or []}.values())
        if treaties:
            perspectives.append(TY)
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
            aal={code: data.get("aal") for code, data in produced.items()},
            treaties=treaties, disabled_reason=reason))
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
    """The newest existing manifest row for each of the analyses at this
    perspective, from any submission, keyed by ``irp_app_analysis_id``, with
    ``earlier_count`` counting that analysis's distinct exports (FR-004): a TY
    export's treaty rows count once."""
    ids = sorted({int(v) for v in irp_app_analysis_ids if v is not None})
    if not ids:
        return {}
    params: dict[str, Any] = {f"a{i}": v for i, v in enumerate(ids)}
    params["p"] = perspective_code
    rows = execute(
        "SELECT export_id, requested_from_submission_id, requested_at, "
        "requested_by_email, irp_app_analysis_id, perspective_code, irp_export_job_id, "
        "stage_status, load_status, closed_at "
        f"FROM stage.rwb_loss_result_manifest {read_uncommitted_hint('LOSS')} "
        f"WHERE perspective_code = :p AND irp_app_analysis_id IN "
        f"({', '.join(':' + k for k in params if k != 'p')}) "
        "ORDER BY irp_app_analysis_id, requested_at DESC, manifest_id DESC",
        params, connection="LOSS")
    marks: dict[int, ExportedMark] = {}
    seen: dict[int, set[str]] = {}
    for r in rows:
        app_id = int(r["irp_app_analysis_id"])
        export_id = _uid(r["export_id"])
        mark = marks.get(app_id)
        if mark is None:
            marks[app_id] = ExportedMark(
                irp_app_analysis_id=app_id,
                perspective_code=r["perspective_code"], export_id=export_id,
                requested_from_submission_id=_uid(r["requested_from_submission_id"]),
                requested_at=r["requested_at"],
                requested_by_email=r["requested_by_email"],
                status=derive_status(r))
        elif export_id not in seen[app_id]:
            mark.earlier_count += 1
        seen.setdefault(app_id, set()).add(export_id)
    return marks


def mark_exported(selected: list[ExportableAnalysis], perspective_code: str) -> None:
    """Attach the existing export, if any, to each selected analysis."""
    marks = find_exported([a.irp_app_analysis_id for a in selected], perspective_code)
    for a in selected:
        a.exported = marks.get(a.irp_app_analysis_id) if a.irp_app_analysis_id else None


def list_clients() -> list[Client]:
    return [Client(id=int(r["ClientID"]), name=r["ClientName"]) for r in execute(
        "SELECT ClientID, ClientName FROM dbo.Client "
        "ORDER BY ClientName, ClientID", {}, connection="LOSS")]


def model_version_choices() -> list[str]:
    """The distinct ``ModelVersion`` values of ``dbo.Lookup_RMS_HistoricalRDS``
    (``25.0``), newest numeric first, then any non-numeric form alphabetically.
    The first entry is the form's default (P-25)."""
    values = {str(r["ModelVersion"]).strip() for r in execute(
        "SELECT DISTINCT ModelVersion FROM dbo.Lookup_RMS_HistoricalRDS "
        "WHERE ModelVersion IS NOT NULL", {}, connection="LOSS")}
    numeric, other = [], []
    for value in values:
        try:
            numeric.append((float(value), value))
        except ValueError:
            other.append(value)
    return [v for _, v in sorted(numeric, reverse=True)] + sorted(other)


# ── submit ───────────────────────────────────────────────────────────────────

_MANIFEST_INSERT = """
    INSERT INTO stage.rwb_loss_result_manifest (
        export_id, requested_by_email, requested_at, requested_from_submission_id,
        irp_analysis_id, irp_analysis_irp_id, irp_app_analysis_id, analysis_name,
        analysis_description, perspective_code, client_id, treaty_incept, treaty_year,
        crm_id, data_name, treaty_number, treaty_name, data_vintage, data_currency,
        data_model_vendor, data_model_version, server, [database], peril_code, region_code,
        stage_status, load_status, inserted_at, updated_at)
    VALUES (
        :export_id, :requested_by_email, :now, :submission_id,
        :irp_analysis_id, :irp_analysis_irp_id, :irp_app_analysis_id, :analysis_name,
        :analysis_description, :perspective_code, :client_id, :treaty_incept, :treaty_year,
        :crm_id, :data_name, :treaty_number, :treaty_name, :data_vintage, :data_currency,
        'RMS', :data_model_version, :server, :database, :peril_code, :region_code,
        'pending', 'pending', :now, :now)
"""


def create_export(*, submission_id: Any, user_email: str, analysis_ids: list[str],
                  perspective_code: str, client_id: int | None, treaty_incept: Any,
                  crm_id: str | None, data_vintage: Any, model_version: str | None,
                  data_names: dict[str, str] | None = None,
                  treaty_picks: dict[str, dict[str, str]] | None = None) -> str:
    """Validate in the contracts/routes.md §4 order, insert one manifest row per
    analysis — at TY one per treaty the analyst ticked — in one LOSS
    transaction, enqueue ``submit_results_export``, and return the new
    ``export_id``. Never writes to the submission (P-15).

    ``treaty_picks`` maps an analysis id to the data name typed for each ticked
    treaty number, empty string when the analyst left it blank; a treaty number
    is ticked exactly when it is a key. ``data_names`` is the per-analysis data
    name of the other perspectives and is ignored at TY (P-06)."""
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
                f"{analysis.name} was not run with treaties." if perspective_code == TY
                else f"{analysis.name} has no {perspective_code} results.")
    picks = {_uid(k): v for k, v in (treaty_picks or {}).items()}
    if perspective_code == TY:
        for analysis in selected:
            chosen = picks.get(analysis.id) or {}
            if not chosen:
                raise ExportValidationError(f"Tick at least one treaty for {analysis.name}.")
            unknown = next((n for n in chosen
                            if n not in {t.get("treaty_number") for t in analysis.treaties}),
                           None)
            if unknown is not None:
                raise ExportValidationError(
                    f"Treaty {unknown} is not one of {analysis.name}'s treaties.")
    if client_id is None or execute_one(
            "SELECT 1 AS x FROM dbo.Client WHERE ClientID = :c",
            {"c": client_id}, connection="LOSS") is None:
        raise ExportValidationError("Choose a client.")
    if treaty_incept is None:
        raise ExportValidationError("Treaty inception is required.")
    if data_vintage is None:
        raise ExportValidationError("Data vintage is required.")
    if model_version not in model_version_choices():
        raise ExportValidationError("Choose a model version.")
    crm_id = (crm_id or "").strip() or None
    if crm_id and len(crm_id) > CRM_ID_MAX_LEN:
        raise ExportValidationError(f"CRM ID is longer than {CRM_ID_MAX_LEN} characters.")
    names = {_uid(k): (v or "").strip() for k, v in (data_names or {}).items()}
    # One manifest row per entry: (analysis, treaty number, treaty name, data
    # name). A portfolio-level perspective writes one row per analysis with no
    # treaty; TY writes one per ticked treaty, in the order the analysis records
    # its treaties, each named as the analyst typed it or, when blank, after the
    # analysis and the treaty number (P-06).
    planned: list[tuple[ExportableAnalysis, str | None, str | None, str | None]] = []
    for analysis in selected:
        if perspective_code != TY:
            if len(names.get(analysis.id, "")) > DATA_NAME_MAX_LEN:
                raise ExportValidationError(
                    f"Data name for {analysis.name} is longer than {DATA_NAME_MAX_LEN} "
                    "characters.")
            planned.append((analysis, None, None, names.get(analysis.id) or None))
            continue
        chosen = picks[analysis.id]
        for treaty in analysis.treaties:
            number = treaty.get("treaty_number")
            if number not in chosen:
                continue
            typed = (chosen[number] or "").strip()
            if len(typed) > DATA_NAME_MAX_LEN:
                raise ExportValidationError(
                    f"Data name for {analysis.name} treaty {number} is longer than "
                    f"{DATA_NAME_MAX_LEN} characters.")
            planned.append((analysis, number, treaty.get("treaty_name"),
                            typed or f"{analysis.analysis_name} {number}"[:DATA_NAME_MAX_LEN]))
    export_id = str(uuid.uuid4())
    now = _utcnow()
    # Where the results came from and where they were requested: the Risk Modeler
    # web UI origin and the Workbench database, both written through to dbo.Data
    # so a loaded row names its source without the Workbench (9/11 D11, D12).
    server = _rm_ui_root()
    database = get_connection_config("WORKBENCH")["database"] or None
    with get_connection("LOSS") as conn, conn.begin():
        for analysis, treaty_number, treaty_name, data_name in planned:
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
                "crm_id": crm_id, "data_name": data_name,
                "treaty_number": treaty_number, "treaty_name": treaty_name,
                "data_vintage": data_vintage, "data_currency": analysis.currency,
                "data_model_version": model_version,
                "server": server, "database": database,
                "peril_code": analysis.peril_code, "region_code": analysis.region_code,
            })
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


# ── exports table ────────────────────────────────────────────────────────────


def list_export_rows(submission_id: Any) -> list[ExportAnalysisDetail]:
    """One manifest row per analysis — or per treaty of an analysis at TY
    (spec 016 P-09) — of every export requested from this submission (P-16),
    newest export first; ``export_ordinal`` counts the exports from 1 over the
    unfiltered list."""
    rows = [dict(r) for r in execute(
        "SELECT m.*, c.ClientName AS client_name "
        f"FROM stage.rwb_loss_result_manifest m {read_uncommitted_hint('LOSS')} "
        "LEFT JOIN dbo.Client c ON c.ClientID = m.client_id "
        "WHERE m.requested_from_submission_id = :s "
        "ORDER BY m.requested_at DESC, m.export_id, "
        "m.analysis_description, m.analysis_name, m.treaty_number, m.treaty_name, "
        "m.manifest_id",
        {"s": _uid(submission_id)}, connection="LOSS")]
    analyses = _analysis_rows([_uid(r["irp_analysis_id"]) for r in rows])
    result: list[ExportAnalysisDetail] = []
    ordinal, current_export = 0, None
    for row in rows:
        export_id = _uid(row["export_id"])
        if export_id != current_export:
            ordinal, current_export = ordinal + 1, export_id
        analysis = analyses.get(_uid(row["irp_analysis_id"])) or {}
        perspectives = (_parse_json_dict(analysis.get("loss_results"), "loss_results")
                        or {}).get("perspectives") or {}
        result.append(ExportAnalysisDetail(
            manifest_id=row["manifest_id"], export_id=export_id, export_ordinal=ordinal,
            irp_analysis_id=_uid(row["irp_analysis_id"]),
            analysis_name=row["analysis_description"] or row["analysis_name"],
            origin=("broker" if analysis.get("rdm_id") else
                    "group" if analysis.get("is_group") else "own"),
            treaty_number=row.get("treaty_number"), treaty_name=row.get("treaty_name"),
            treaty_ids=[v for v in (row.get("treaty_ids") or "").split(",") if v],
            status=derive_status(row), updated_at=row["updated_at"],
            irp_export_job_id=row["irp_export_job_id"], zip_file=row["zip_file"],
            data_name=row["data_name"], irp_app_analysis_id=row["irp_app_analysis_id"],
            data_currency=row["data_currency"], data_model_version=row["data_model_version"],
            engine_type=row["engine_type"], peril_code=row["peril_code"],
            region_code=row["region_code"],
            data_id=row["data_id"],
            # A treaty row's AAL is its own combined rows' sum of rate × loss,
            # written by the stage worker (P-10); a portfolio row reads the
            # analysis's stored AAL at that perspective.
            aal=(row.get("aal") if row["perspective_code"] == TY
                 else (perspectives.get(row["perspective_code"]) or {}).get("aal")),
            staged_row_count=row["staged_row_count"],
            stochastic_row_count=row["stochastic_row_count"],
            historical_row_count=row["historical_row_count"],
            exp_value_raised_count=row["exp_value_raised_count"],
            std_dev_zeroed_count=row["std_dev_zeroed_count"],
            error_message=row["error_message"],
            closed_at=row["closed_at"], closed_by=row["closed_by"],
            perspective_code=row["perspective_code"], client_name=row["client_name"],
            crm_id=row["crm_id"], treaty_incept=row["treaty_incept"],
            data_vintage=row["data_vintage"],
            requested_by_email=row["requested_by_email"], requested_at=row["requested_at"]))
    return result


def _analysis_rows(irp_analysis_ids: list[str]) -> dict[str, dict]:
    """The ``irp_analysis`` row behind each manifest row: the origin label and
    the AAL the table shows (P-20) are read from it at render time, never
    copied onto the manifest."""
    if not irp_analysis_ids:
        return {}
    params = {f"i{n}": v for n, v in enumerate(irp_analysis_ids)}
    return {_uid(r["id"]): dict(r) for r in execute(
        "SELECT id, is_group, rdm_id, loss_results FROM irp_analysis "
        f"WHERE id IN ({', '.join(':' + k for k in params)})",
        params, connection="WORKBENCH")}


# ── retry and close ──────────────────────────────────────────────────────────

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


def _failed_manifest(submission_id: Any, export_id: Any, manifest_id: Any) -> dict:
    """The manifest row Retry and Close both act on — one analysis, or one
    treaty of an analysis at TY. Raises ``ExportNotFound`` when the row is not
    this submission's and ``ExportActionRefused`` when the row is not failed —
    a Risk Modeler failure the stage worker has not stamped yet is not failed
    yet, and a closed row is no longer failed."""
    try:
        manifest_key = int(manifest_id)
    except (TypeError, ValueError):
        raise ExportNotFound("That analysis is not part of this export.") from None
    manifest = execute_one(
        f"SELECT * FROM stage.rwb_loss_result_manifest {read_uncommitted_hint('LOSS')} "
        "WHERE manifest_id = :m AND export_id = :e AND requested_from_submission_id = :s",
        {"m": manifest_key, "e": _uid(export_id), "s": _uid(submission_id)},
        connection="LOSS")
    if manifest is None:
        raise ExportNotFound("That analysis is not part of this export.")
    status = derive_status(manifest)
    if status == LOADED:
        raise ExportActionRefused(f"already loaded as data ID {manifest['data_id']}")
    if status != FAILED:
        raise ExportActionRefused(f"the analysis is {status}, not failed")
    return manifest


def apply_close(submission_id: Any, export_id: Any, manifest_id: Any,
                user_email: str) -> None:
    """Close a failed row: the analyst has dealt with the failure outside
    the Workbench and wants it off the list (P-22). Nothing is re-run — a fix
    is a new export — and a closed row offers no Retry."""
    manifest = _failed_manifest(submission_id, export_id, manifest_id)
    now = _utcnow()
    execute_command(
        "UPDATE stage.rwb_loss_result_manifest SET closed_at = :now, closed_by = :u, "
        "updated_at = :now WHERE manifest_id = :m",
        {"now": now, "u": user_email, "m": manifest["manifest_id"]}, connection="LOSS")


def apply_retry(submission_id: Any, export_id: Any, manifest_id: Any) -> RetryBranch:
    """Re-arm exactly one job for a failed row and dispatch it. The job is the
    analysis's one stage or load job; it acts on every eligible row of the
    analysis, so a loaded or closed sibling treaty row is never re-run
    (spec 016 T-05)."""
    manifest = _failed_manifest(submission_id, export_id, manifest_id)
    job = _export_job(manifest["irp_export_job_id"])
    branch = retry_decision(manifest, job, settings.export_archive_dir, _utcnow())
    analysis_id = _uid(manifest["irp_analysis_id"])
    export_key = _uid(manifest["export_id"])
    analysis = execute_one("SELECT edm_id, rdm_id FROM irp_analysis WHERE id = :a",
                           {"a": analysis_id}, connection="WORKBENCH") or {}
    link_type, link_id = rwb_job_service.analysis_link(analysis.get("edm_id"),
                                                       analysis.get("rdm_id"))

    # Each branch first puts the row back into the state its job runs from, so
    # the exports table reads it as in progress (and polls) until the job stamps it.
    if branch == "load":
        stage_job = execute_one(
            "SELECT id FROM rwb_job WHERE rwb_job_type = 'stage_results_export' "
            "AND requestor_type = 'irp_job' AND requestor_id = :r",
            {"r": str((job or {}).get("id"))}, connection="WORKBENCH")
        if stage_job is None:
            raise ExportActionRefused("no stage job is recorded for this analysis")
        execute_command(
            "UPDATE stage.rwb_loss_result_manifest SET load_status = 'pending', "
            "error_message = NULL, updated_at = :now WHERE manifest_id = :m",
            {"now": _utcnow(), "m": manifest["manifest_id"]}, connection="LOSS")
        job_type = "load_results_export"
        rwb_job_id = rwb_job_service.ensure_pending_rwb_job(
            requestor_type="rwb_job", requestor_id=stage_job["id"], rwb_job_type=job_type,
            link_type=link_type, link_id=link_id,
            context_type="irp_analysis", context_id=analysis_id,
            input_data={"export_id": export_key, "irp_analysis_id": analysis_id})
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
    "QUEUED", "IN_PROGRESS", "LOADED", "FAILED", "CLOSED",
    "TERMINAL_STATUSES", "DATA_NAME_MAX_LEN", "TY",
    "ExportError", "ExportValidationError", "ExportNotFound", "ExportActionRefused",
    "ExportedMark", "ExportableAnalysis", "TreatyChoice", "Client",
    "ExportAnalysisDetail",
    "derive_status", "list_exportable_analyses", "perspective_choices", "find_exported",
    "mark_exported", "list_clients", "model_version_choices", "create_export",
    "list_export_rows", "retry_decision", "apply_retry", "apply_close",
]
