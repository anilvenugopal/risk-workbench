"""Delete a submission from the Workbench (issue 206).

A hard delete of Workbench rows only (P-01): the EDMs and RDMs, the analyses
they own, everything in Risk Modeler and every export in the loss repository
stay. Refused while any job tied to the submission is unfinished (P-04, P-07).
A separate module because ``export_service`` imports ``submission_service``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.services import export_service, irp_job_service
from app.services._common import SubmissionRef, _in_clause
from db import get_connection
from db.errors import SQLServerError

logger = logging.getLogger(__name__)

# Rows of these job types belong to the EDM or RDM they imported, which the
# delete keeps; they block the delete while unfinished but are not removed.
_KEPT_JOB_TYPES = ("upload_edm", "upload_rdm")


class SubmissionDeleteBlocked(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class DeleteSummary:
    """What the delete removes, for the confirm dialog and the log line."""
    contract_count: int = 0
    edm_count: int = 0
    rdm_count: int = 0
    imported_count: int = 0
    group_count: int = 0
    linking: list[SubmissionRef] = field(default_factory=list)
    blocked: str | None = None


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _unfinished_export_count(submission_id: str) -> int:
    """Exports requested from the submission that are still queued or in
    progress (P-07). Raises ``SubmissionDeleteBlocked`` when the loss
    repository cannot be read, since a running export cannot be ruled out."""
    try:
        rows = export_service.list_export_rows(submission_id)
    except (SQLServerError, SQLAlchemyError):
        logger.warning("submission delete: loss repository unreachable", exc_info=True)
        raise SubmissionDeleteBlocked(
            "Cannot check this submission's exports: the loss repository is "
            "unreachable. Try again later.") from None
    return len({r.export_id for r in rows if not r.is_terminal})


def _analysis_ids(conn, sid: str) -> list[str]:
    """Imported analyses and analysis groups, soft-deleted ones included."""
    return [str(r[0]) for r in conn.execute(
        text("SELECT id FROM irp_analysis WHERE submission_id = :s"), {"s": sid})]


def _rwb_job_match(sid: str, analysis_ids: list[str]) -> tuple[str, dict]:
    """The ``rwb_job`` rows a resubmit would run against the submission or its
    analyses (P-05). ``execute_analysis_batch``, ``submit_grouping`` and
    ``submit_results_export`` carry the submission only in ``input_data``."""
    clauses = ["(link_type = 'submission' AND link_id = :s)", "input_data LIKE :s_like"]
    params: dict[str, Any] = {"s": sid, "s_like": f"%{sid}%"}
    if analysis_ids:
        requestor, p1 = _in_clause("requestor_id", analysis_ids, "ra")
        context, p2 = _in_clause("context_id", analysis_ids, "ca")
        clauses += [f"(requestor_type = 'irp_analysis' AND {requestor})",
                    f"(context_type = 'irp_analysis' AND {context})"]
        params |= p1 | p2
    return "(" + " OR ".join(clauses) + ")", params


def _blocked_reason(conn, sid: str, analysis_ids: list[str], exports: int) -> str | None:
    terminal, params = _in_clause("status", sorted(irp_job_service.TERMINAL), "t")
    owner = "requested_from_submission_id = :s"
    if analysis_ids:
        analysis, p = _in_clause("irp_analysis_id", analysis_ids, "ia")
        owner, params = f"({owner} OR {analysis})", params | p
    irp_jobs = conn.execute(text(
        f"SELECT COUNT(*) FROM irp_job WHERE NOT {terminal} AND {owner}"),
        {"s": sid, **params}).scalar()
    match, params = _rwb_job_match(sid, analysis_ids)
    rwb_jobs = conn.execute(text(
        f"SELECT COUNT(*) FROM rwb_job WHERE status_code IN ('pending', 'running') "
        f"AND {match}"), params).scalar()
    running = [part for count, part in (
        (irp_jobs, _plural(irp_jobs, "Risk Modeler job")),
        (rwb_jobs, _plural(rwb_jobs, "Workbench job")),
        (exports, _plural(exports, "export"))) if count]
    if not running:
        return None
    return ("Cannot delete while work on this submission is still running: "
            f"{', '.join(running)}. Try again when they finish.")


def _summary(conn, sid: str, blocked: str | None) -> DeleteSummary:
    def count(sql: str) -> int:
        return conn.execute(text(sql), {"s": sid}).scalar()

    linking = conn.execute(text(
        "SELECT id, name FROM submission WHERE links_to_submission_id = :s "
        "ORDER BY name"), {"s": sid})
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
    try:
        exports = _unfinished_export_count(sid)
    except SubmissionDeleteBlocked as exc:
        exports, blocked = 0, exc.reason
    else:
        blocked = None
    with get_connection("WORKBENCH") as conn:
        if blocked is None:
            blocked = _blocked_reason(conn, sid, _analysis_ids(conn, sid), exports)
        return _summary(conn, sid, blocked)


def delete_submission(*, submission_id: Any, actor_id: Any) -> None:
    """Delete the submission and the Workbench rows that exist only for it, in
    one transaction. Raises ``SubmissionDeleteBlocked`` with the reason while
    work is running, and ``LookupError`` when the submission is gone. A job
    created between the check and the delete makes a foreign key fail, which
    rolls everything back."""
    sid = str(submission_id)
    exports = _unfinished_export_count(sid)
    with get_connection("WORKBENCH") as conn, conn.begin():
        analysis_ids = _analysis_ids(conn, sid)
        blocked = _blocked_reason(conn, sid, analysis_ids, exports)
        if blocked:
            raise SubmissionDeleteBlocked(blocked)
        deal = conn.execute(text(
            "SELECT s.name, c.name AS cedant FROM submission s "
            "JOIN cedant c ON c.id = s.cedant_id WHERE s.id = :s"), {"s": sid}).first()
        if deal is None:
            raise LookupError(f"submission {sid} not found")
        summary = _summary(conn, sid, None)

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
        match, params = _rwb_job_match(sid, analysis_ids)
        kept, kp = _in_clause("rwb_job_type", list(_KEPT_JOB_TYPES), "k")
        jobs = f"SELECT id FROM rwb_job WHERE {match} AND NOT {kept}"
        conn.execute(text(
            f"DELETE FROM rwb_job_heartbeat WHERE rwb_job_id IN ({jobs})"), params | kp)
        job_count = conn.execute(text(
            f"DELETE FROM rwb_job WHERE {match} AND NOT {kept}"), params | kp).rowcount
        for table in ("irp_analysis", "contract", "submission_status_event",
                      "submission_edm", "submission_rdm"):
            conn.execute(text(f"DELETE FROM {table} WHERE submission_id = :s"), {"s": sid})
        conn.execute(text(
            "UPDATE submission SET links_to_submission_id = NULL "
            "WHERE links_to_submission_id = :s"), {"s": sid})
        conn.execute(text(
            "UPDATE irp_job SET requested_from_submission_id = NULL "
            "WHERE requested_from_submission_id = :s"), {"s": sid})
        conn.execute(text("DELETE FROM submission WHERE id = :s"), {"s": sid})

    logger.info(
        "submission deleted: id=%s name=%r cedant=%r actor=%s contracts=%d edms=%d rdms=%d "
        "imported_analyses=%d groups=%d linking_submissions=%d rwb_jobs=%d",
        sid, deal.name, deal.cedant, actor_id, summary.contract_count, summary.edm_count,
        summary.rdm_count, summary.imported_count, summary.group_count,
        len(summary.linking), job_count)
