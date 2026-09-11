"""Import analyses by Risk Modeler id (issue #101).

A group composed in Risk Modeler belongs to no EDM or RDM, so neither the
execution path nor the RDM capture ever finds it. The Results section's Import
dialog takes the ``appAnalysisId`` the Risk Modeler UI shows, one at a time;
``check_analysis`` resolves each against Risk Modeler on the request path (a
bounded Platform read the analyst is waiting on), and ``import_analyses``
inserts one ``irp_analysis`` row per entry on the submission leg of
``ck_irp_analysis_origin`` — ``submission_id`` set, ``edm_id``/``rdm_id`` NULL,
``imported_at`` stamped — and enqueues ``retrieve_analysis_results`` for it.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import text

from app.services import irp_gateway, rwb_job_service
from app.services._common import _uid, _utcnow
from app.services.analysis_service import _to_display
from app.workers import dispatch
from app.workers.analysis_jobs import free_name_attempts
from db import execute_command, execute_one, get_connection, is_unique_violation

logger = logging.getLogger(__name__)

_RM_UNAVAILABLE = "Risk Modeler did not answer. Try again in a moment."


class ImportCheckError(ValueError):
    """One entry cannot be imported; ``str(exc)`` is the sentence the dialog
    shows. ``kind`` is ``error`` or ``warning`` (an analysis already in the
    deal is a warning — nothing the analyst typed is wrong)."""

    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class ImportCandidate:
    """One checked entry, carried in the dialog's hidden ``entries`` inputs
    between the check and the import."""
    app_analysis_id: str
    analysis_id: str        # Platform analysisId
    name: str | None
    is_group: bool
    engine: str | None
    currency: str | None

    @property
    def form_value(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_form(cls, raw: str) -> ImportCandidate | None:
        try:
            data = json.loads(raw)
            return cls(app_analysis_id=str(data["app_analysis_id"]),
                       analysis_id=str(data["analysis_id"]),
                       name=data.get("name"), is_group=bool(data.get("is_group")),
                       engine=data.get("engine"), currency=data.get("currency"))
        except (ValueError, TypeError, KeyError, AttributeError):
            return None


@dataclass(frozen=True)
class ImportOutcome:
    imported: int
    failed: list[str]  # one sentence per entry that was not imported


def _parse_app_id(raw: str) -> str:
    value = (raw or "").strip()
    if not value.isdigit() or int(value) <= 0:
        raise ImportCheckError(
            "Enter the numeric id Risk Modeler shows on the analysis.")
    return str(int(value))


# One live row for the analysisId anywhere in the deal, whatever its origin:
# imported here, captured from one of the deal's RDMs, run from one of its
# EDMs, or composed as a group. A row in another deal is not a match, and
# neither is one under a soft-deleted RDM or EDM — the Results grid and the
# group picker both filter the parent, so it is not in the deal any more.
_IN_DEAL_SELECT = """
    SELECT a.name, a.full_name, a.imported_at, a.rdm_id, a.edm_id,
           r.name AS rdm_name, e.name AS edm_name
    FROM irp_analysis a
    LEFT JOIN irp_rdm r ON r.id = a.rdm_id
    LEFT JOIN irp_edm e ON e.id = a.edm_id
    LEFT JOIN submission_rdm sr ON sr.rdm_id = a.rdm_id AND sr.submission_id = :sid
    LEFT JOIN submission_edm se ON se.edm_id = a.edm_id AND se.submission_id = :sid
    WHERE a.irp_id = :irp AND a.deleted_at IS NULL
      AND (a.submission_id = :sid
           OR (sr.submission_id IS NOT NULL AND r.deleted_at IS NULL)
           OR (se.submission_id IS NOT NULL AND e.deleted_at IS NULL))
"""


def _refuse_if_in_deal(submission_id: Any, app_id: str, analysis_id: str) -> None:
    row = execute_one(_IN_DEAL_SELECT,
                      {"sid": str(submission_id), "irp": str(analysis_id)},
                      connection="WORKBENCH")
    if row is None:
        return
    shown = row["full_name"] or row["name"] or analysis_id
    if row["imported_at"] is not None:
        message = f"{app_id} was already imported into this deal — {shown}."
    elif row["rdm_id"] is not None:
        message = (f"{app_id} is already in this deal, captured from RDM "
                   f"{row['rdm_name']} — {shown}.")
    elif row["edm_id"] is not None:
        message = (f"{app_id} is already in this deal, run from EDM "
                   f"{row['edm_name']} — {shown}.")
    else:
        message = f"{app_id} is already in this deal — {shown}."
    raise ImportCheckError(message, kind="warning")


def _candidate(app_id: str, analysis_id: str,
               meta: irp_gateway.AnalysisMetadata) -> ImportCandidate:
    display = _to_display(meta.payload)
    return ImportCandidate(
        app_analysis_id=app_id, analysis_id=str(analysis_id),
        name=meta.payload.get("analysisName") or meta.payload.get("name"),
        is_group=meta.is_group, engine=display.engine_type,
        currency=display.currency)


def check_analysis(*, submission_id: Any, app_analysis_id: str,
                   existing: list[str]) -> ImportCandidate:
    """Resolve one typed id against Risk Modeler and the deal. ``existing`` is
    the app ids already on the dialog's list, compared after normalization so
    ``035774`` does not slip past a listed ``35774``. Raises
    ``ImportCheckError`` with the sentence to show; nothing is written."""
    app_id = _parse_app_id(app_analysis_id)
    if app_id in existing:
        raise ImportCheckError(f"{app_id} is already in the list.",
                               kind="warning")
    try:
        analysis_id = irp_gateway.resolve_app_analysis_id(app_analysis_id=int(app_id))
    except irp_gateway.AmbiguousAnalysisId:
        raise ImportCheckError(
            f"Risk Modeler has more than one analysis with id {app_id} — it "
            "cannot be imported by id.") from None
    except LookupError:
        raise ImportCheckError(
            f"Risk Modeler has no analysis {app_id} — use the id shown on the "
            "analysis in Risk Modeler.") from None
    except Exception as exc:  # noqa: BLE001 — a Platform failure is a retry, not a 500
        logger.warning("appAnalysisId lookup failed for %s: %s", app_id, exc)
        raise ImportCheckError(_RM_UNAVAILABLE) from exc
    try:
        meta = irp_gateway.get_analysis_metadata(analysis_id=int(analysis_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("analysis metadata read failed for %s: %s", analysis_id, exc)
        raise ImportCheckError(_RM_UNAVAILABLE) from exc
    _refuse_if_in_deal(submission_id, app_id, analysis_id)
    return _candidate(app_id, analysis_id, meta)


def _import_one(submission_id: str, app_id: str, analysis_id: str,
                actor_id: Any) -> None:
    try:
        meta = irp_gateway.get_analysis_metadata(analysis_id=int(analysis_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("analysis metadata read failed for %s: %s", analysis_id, exc)
        raise ImportCheckError(f"{app_id}: {_RM_UNAVAILABLE}") from exc
    if str(meta.payload.get("appAnalysisId")) != app_id:
        # The form was stale or edited: the Platform id no longer names the
        # analysis the analyst checked.
        raise ImportCheckError(
            f"{app_id} no longer matches the analysis that was checked — "
            "add it again.")
    _refuse_if_in_deal(submission_id, app_id, analysis_id)

    candidate = _candidate(app_id, analysis_id, meta)
    rm_name = candidate.name or f"Analysis {app_id}"
    pointer = (meta.exposure_resource_id
               if meta.exposure_resource_type == "PORTFOLIO" else None)
    row_id = str(uuid.uuid4())
    by = str(actor_id) if actor_id is not None else None
    # ``name`` takes the local ``_n`` suffix and the 64-char clip; ``full_name``
    # stays the name Risk Modeler knows the analysis by.
    for _, _suffixed, name in free_name_attempts(
            rm_name, scope_column="submission_id", scope_value=submission_id):
        now = _utcnow()
        try:
            with get_connection("WORKBENCH") as conn, conn.begin():
                conn.execute(text(
                    """
                    INSERT INTO irp_analysis (id, submission_id, irp_id,
                        irp_app_analysis_id, name, full_name, status_code,
                        settings_metadata, submitted_settings, is_group,
                        exposure_resource_id, imported_at, inserted_at,
                        updated_at, inserted_by, updated_by)
                    VALUES (:id, :sid, :irp, :app, :name, :full, 'ready',
                        :settings, :submitted, :grp, :pointer, :now, :now,
                        :now, :by, :by)
                    """
                ), {"id": row_id, "sid": submission_id, "irp": str(analysis_id),
                    "app": app_id, "name": name, "full": rm_name,
                    "settings": json.dumps(meta.payload),
                    # Risk Modeler reports only the currency code for an
                    # existing analysis, so the block carries the code alone. A
                    # payload with no currency writes null, which _submitted_view
                    # reads back as None and leaves the row unpairable.
                    "submitted": json.dumps({"currency": {"code": candidate.currency}}),
                    "grp": (1 if meta.is_group else 0), "pointer": pointer,
                    "now": now, "by": by})
        except Exception as exc:  # noqa: BLE001 — a UNIQUE race means the next suffix
            if is_unique_violation(exc):
                continue
            raise
        break
    try:
        job_id = rwb_job_service.enqueue_rwb_job(
            requestor_type="irp_analysis", requestor_id=row_id,
            rwb_job_type="retrieve_analysis_results",
            link_type="submission", link_id=submission_id,
            context_type="irp_analysis", context_id=row_id,
            input_data={"analysis_id": row_id}, actor_id=actor_id)
    except Exception:
        # A row with no retrieval job never leaves ``pending``, and the grid
        # offers Retry only on a FAILED one — so take the row back out rather
        # than leave one that polls forever. ``_refuse_if_in_deal`` ignores
        # soft-deleted rows, so the analyst can import the id again.
        execute_command(
            "UPDATE irp_analysis SET deleted_at = :now, updated_at = :now, "
            "updated_by = :by WHERE id = :id",
            {"now": _utcnow(), "by": by, "id": row_id}, connection="WORKBENCH")
        raise
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="retrieve_analysis_results")


def import_analyses(*, submission_id: Any, entries: list[ImportCandidate],
                    actor_id: Any) -> ImportOutcome:
    """Insert one row per checked entry and enqueue its results retrieval.
    Entries are independent: one refusal or failure is reported in ``failed``
    and the rest still import."""
    sid = _uid(submission_id)
    imported = 0
    failed: list[str] = []
    for entry in entries:
        try:
            _import_one(sid, entry.app_analysis_id, entry.analysis_id, actor_id)
        except ImportCheckError as exc:
            failed.append(str(exc))
            continue
        except Exception:  # noqa: BLE001 — one entry's failure is not the batch's
            logger.exception("import failed for appAnalysisId %s",
                             entry.app_analysis_id)
            failed.append(
                f"{entry.app_analysis_id} could not be imported — try again.")
            continue
        imported += 1
    return ImportOutcome(imported=imported, failed=failed)
