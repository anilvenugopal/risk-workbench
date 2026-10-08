"""Delete a submission's Workbench rows, under the rules in docs/PRD.md §7.2a."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.services import irp_job_service
from app.services._common import SubmissionRef, _in_clause
from app.services.errors import SubmissionDeleteBlocked
from db import get_connection

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


def _blocked_reason(conn, sid: str, analysis_ids: list[str]) -> str | None:
    terminal, params = _in_clause("status", sorted(irp_job_service.TERMINAL), "t")
    owner = "requested_from_submission_id = :s"
    if analysis_ids:
        analysis, p = _in_clause("irp_analysis_id", analysis_ids, "ia")
        owner, params = f"({owner} OR {analysis})", params | p
    irp_jobs = conn.execute(text(
        f"SELECT COUNT(*) FROM irp_job WHERE NOT {terminal} AND {owner}"),
        {"s": sid, **params}).scalar()
    lowered = {a.lower() for a in analysis_ids}
    rwb_jobs = sum(_names_submission(job, sid.lower(), lowered) for job in conn.execute(text(
        "SELECT link_type, link_id, requestor_type, requestor_id, context_type, "
        "context_id, input_data FROM rwb_job WHERE status_code IN ('pending', 'running')")))
    running = [part for count, part in (
        (irp_jobs, _plural(irp_jobs, "Risk Modeler job")),
        (rwb_jobs, _plural(rwb_jobs, "Workbench job"))) if count]
    if not running:
        return None
    return ("Cannot delete while work on this submission is still running: "
            f"{', '.join(running)}. Try again when they finish.")


def _summary(conn, sid: str, blocked: str | None) -> DeleteSummary:
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
        blocked=blocked)


def delete_summary(submission_id: Any) -> DeleteSummary:
    """What deleting the submission would remove, or why it cannot be deleted."""
    sid = str(submission_id)
    with get_connection("WORKBENCH") as conn:
        return _summary(conn, sid, _blocked_reason(conn, sid, _analysis_ids(conn, sid)))


def delete_submission(*, submission_id: Any, actor_id: Any) -> None:
    """Delete the submission and the Workbench rows that exist only for it, in
    one transaction. Raises ``SubmissionDeleteBlocked`` while work is running,
    and ``LookupError`` when the submission is gone. An ``irp_job`` or
    ``irp_analysis`` inserted between the check and the delete fails a foreign
    key, which rolls everything back and raises ``SubmissionDeleteBlocked``."""
    sid = str(submission_id)
    try:
        with get_connection("WORKBENCH") as conn, conn.begin():
            analysis_ids = _analysis_ids(conn, sid)
            blocked = _blocked_reason(conn, sid, analysis_ids)
            if blocked:
                raise SubmissionDeleteBlocked(blocked)
            deal = conn.execute(text(
                "SELECT s.name, c.name AS cedant FROM submission s "
                "JOIN cedant c ON c.id = s.cedant_id WHERE s.id = :s"), {"s": sid}).first()
            if deal is None:
                raise LookupError(f"submission {sid} not found")

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
        raise SubmissionDeleteBlocked(
            "Work on this submission started while it was being deleted. "
            "Nothing was deleted; try again.") from None

    logger.info("submission deleted: id=%s name=%r cedant=%r actor=%s",
                sid, deal.name, deal.cedant, actor_id)
