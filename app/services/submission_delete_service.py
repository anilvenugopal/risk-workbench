"""Delete a submission's Workbench rows, under the rules in docs/PRD.md §7.2a."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.services import irp_job_service
from app.services._common import SubmissionRef, _in_clause, _utcnow
from app.services.errors import SubmissionDeleteBlocked
from db import execute_scalar, get_connection
from db.errors import SQLServerError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeleteSummary:
    """What the delete removes, for the confirm dialog."""
    contract_count: int
    edm_count: int
    rdm_count: int
    imported_count: int
    group_count: int
    linking: list[SubmissionRef]
    failed_job_count: int
    blocked: str | None


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _analysis_ids(conn, sid: str) -> list[str]:
    """Imported analyses and analysis groups, soft-deleted ones included."""
    return [str(r[0]) for r in conn.execute(
        text("SELECT id FROM irp_analysis WHERE submission_id = :s"), {"s": sid})]


def _names_submission(job, sid: str, analysis_ids: set[str]) -> bool:
    """``execute_analysis_batch``, ``submit_grouping`` and
    ``submit_results_export`` carry the submission only in ``input_data``."""
    if job.link_type == "submission" and str(job.link_id).lower() == sid:
        return True
    for kind, id_ in ((job.requestor_type, job.requestor_id),
                      (job.context_type, job.context_id)):
        if kind == "irp_analysis" and str(id_).lower() in analysis_ids:
            return True
    data = json.loads(job.input_data) if job.input_data else {}
    return any(str(data.get(key)).lower() == sid
               for key in ("submission_id", "requested_from_submission_id"))


def _export_reason(sid: str) -> str | None:
    """Refuses while an export from the submission is not loaded or closed:
    its retry and close actions find it only through the submission."""
    try:
        exports = execute_scalar(
            "SELECT COUNT(DISTINCT export_id) FROM stage.rwb_loss_result_manifest "
            "WHERE requested_from_submission_id = :s AND closed_at IS NULL "
            "AND load_status <> 'loaded'", {"s": sid}, connection="LOSS")
    except (SQLServerError, SQLAlchemyError):
        logger.warning("submission delete: loss repository unreachable", exc_info=True)
        return ("Cannot check this submission's exports: the loss repository is "
                "unreachable. Try again later.")
    if not exports:
        return None
    return (f"{_plural(exports, 'export')} {'is' if exports == 1 else 'are'} not "
            "loaded or closed. Retry or close them in the Exports section first.")


def _rwb_jobs(conn, sid: str, analysis_ids: list[str]) -> list:
    """Pending, running and failed ``rwb_job`` rows that name the submission."""
    lowered = {a.lower() for a in analysis_ids}
    return [job for job in conn.execute(text(
        "SELECT id, status_code, link_type, link_id, requestor_type, requestor_id, "
        "context_type, context_id, input_data FROM rwb_job "
        "WHERE status_code IN ('pending', 'running', 'failed')"))
        if _names_submission(job, sid.lower(), lowered)]


def _blocked_reason(conn, sid: str, analysis_ids: list[str], rwb_jobs: list) -> str | None:
    terminal, params = _in_clause("status", sorted(irp_job_service.TERMINAL), "t")
    owner = "requested_from_submission_id = :s"
    if analysis_ids:
        analysis, p = _in_clause("irp_analysis_id", analysis_ids, "ia")
        owner, params = f"({owner} OR {analysis})", params | p
    irp_jobs = conn.execute(text(
        f"SELECT COUNT(*) FROM irp_job WHERE NOT {terminal} AND {owner}"),
        {"s": sid, **params}).scalar()
    running_rwb = sum(job.status_code != "failed" for job in rwb_jobs)
    running = [part for count, part in (
        (irp_jobs, _plural(irp_jobs, "Risk Modeler job")),
        (running_rwb, _plural(running_rwb, "Workbench job"))) if count]
    reasons = []
    if running:
        reasons.append("Cannot delete while work on this submission is still running: "
                       f"{', '.join(running)}. Try again when they finish.")
    if exports := _export_reason(sid):
        reasons.append(exports)
    return " ".join(reasons) or None


def _summary(conn, sid: str, failed_job_count: int, blocked: str | None) -> DeleteSummary:
    def count(sql: str) -> int:
        return conn.execute(text(sql), {"s": sid}).scalar()

    linking = conn.execute(text(
        "SELECT id, name FROM submission WHERE links_to_submission_id = :s "
        "ORDER BY name"), {"s": sid}).all()
    return DeleteSummary(
        contract_count=count("SELECT COUNT(*) FROM contract WHERE submission_id = :s"),
        edm_count=count("SELECT COUNT(*) FROM submission_edm WHERE submission_id = :s"),
        rdm_count=count("SELECT COUNT(*) FROM submission_rdm WHERE submission_id = :s"),
        imported_count=count(
            "SELECT COUNT(*) FROM irp_analysis WHERE submission_id = :s "
            "AND deleted_at IS NULL AND is_group = 0"),
        group_count=count(
            "SELECT COUNT(*) FROM irp_analysis WHERE submission_id = :s "
            "AND deleted_at IS NULL AND is_group = 1"),
        linking=[SubmissionRef(id=str(r.id), name=r.name) for r in linking],
        failed_job_count=failed_job_count,
        blocked=blocked)


def delete_summary(submission_id: Any) -> DeleteSummary:
    """What deleting the submission would remove, or why it cannot be deleted."""
    sid = str(submission_id)
    with get_connection("WORKBENCH") as conn:
        analysis_ids = _analysis_ids(conn, sid)
        rwb_jobs = _rwb_jobs(conn, sid, analysis_ids)
        return _summary(conn, sid, sum(job.status_code == "failed" for job in rwb_jobs),
                        _blocked_reason(conn, sid, analysis_ids, rwb_jobs))


def delete_submission(*, submission_id: Any, actor_id: Any) -> None:
    """Delete the submission and the Workbench rows that exist only for it, and
    cancel its failed ``rwb_job`` rows, in one transaction. Raises
    ``SubmissionDeleteBlocked`` while work is running, an export is unfinished
    or the loss repository is unreachable, and ``LookupError`` when the
    submission is gone. A row written during the delete that fails a foreign
    key rolls everything back and raises ``SubmissionDeleteBlocked``; other
    work that starts mid-delete is not caught."""
    sid = str(submission_id)
    try:
        with get_connection("WORKBENCH") as conn, conn.begin():
            analysis_ids = _analysis_ids(conn, sid)
            rwb_jobs = _rwb_jobs(conn, sid, analysis_ids)
            blocked = _blocked_reason(conn, sid, analysis_ids, rwb_jobs)
            if blocked:
                raise SubmissionDeleteBlocked(blocked)
            deal = conn.execute(text(
                "SELECT s.name, c.name AS cedant FROM submission s "
                "JOIN cedant c ON c.id = s.cedant_id WHERE s.id = :s"), {"s": sid}).first()
            if deal is None:
                raise LookupError(f"submission {sid} not found")
            failed = [str(job.id) for job in rwb_jobs if job.status_code == "failed"]
            if failed:
                ids, fp = _in_clause("id", failed, "f")
                conn.execute(text(
                    "UPDATE rwb_job SET status_code = 'cancelled', updated_at = :now "
                    f"WHERE status_code = 'failed' AND {ids}"), {"now": _utcnow(), **fp})

            # Every foreign key is NO ACTION, so children go first.
            if analysis_ids:
                group, gp = _in_clause("group_analysis_id", analysis_ids, "g")
                member, mp = _in_clause("member_analysis_id", analysis_ids, "m")
                conn.execute(text(
                    f"DELETE FROM irp_analysis_group_member WHERE {group} OR {member}"),
                    gp | mp)
                job_analysis, jp = _in_clause("irp_analysis_id", analysis_ids, "j")
                conn.execute(text(
                    f"UPDATE irp_job SET irp_analysis_id = NULL WHERE {job_analysis}"), jp)
            for table in ("irp_analysis", "contract", "submission_status_event",
                          "submission_edm", "submission_rdm"):
                conn.execute(text(f"DELETE FROM {table} WHERE submission_id = :s"),
                             {"s": sid})
            conn.execute(text(
                "UPDATE submission SET links_to_submission_id = NULL "
                "WHERE links_to_submission_id = :s"), {"s": sid})
            conn.execute(text(
                "UPDATE irp_job SET requested_from_submission_id = NULL "
                "WHERE requested_from_submission_id = :s"), {"s": sid})
            conn.execute(text("DELETE FROM submission WHERE id = :s"), {"s": sid})
    except IntegrityError:
        # Also what a table that references submission or irp_analysis and is
        # missing from the deletes above looks like.
        logger.warning("submission delete rolled back: id=%s", sid, exc_info=True)
        raise SubmissionDeleteBlocked(
            "Work on this submission started while it was being deleted. "
            "Nothing was deleted; try again.") from None

    logger.info("submission deleted: id=%s name=%r cedant=%r actor=%s",
                sid, deal.name, deal.cedant, actor_id)
