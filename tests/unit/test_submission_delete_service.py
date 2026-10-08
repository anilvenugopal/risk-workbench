"""Deleting a submission (issue 206): which Workbench rows go, which stay, and
which unfinished work refuses the delete. The SQLite mirror carries no foreign
keys on most of these tables, so the delete order is proven only on the SQL
Server tier."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.services import submission_delete_service
from app.services.errors import SubmissionDeleteBlocked
from app.services.submission_delete_service import delete_submission, delete_summary
from db import execute_scalar
from tests.unit.export_rows import (
    NOW,
    seed_analysis,
    seed_edm_for,
    seed_irp_job,
    seed_manifest,
    seed_rdm_for,
    seed_rwb_job,
    seed_submission,
    seed_submission_analysis,
    wb,
)


def _count(sql: str, params: dict) -> int:
    return execute_scalar(sql, params, connection="WORKBENCH")


def _exists(table: str, column: str, value: str) -> bool:
    return _count(f"SELECT COUNT(*) FROM {table} WHERE {column} = :v", {"v": value}) > 0


def test_delete_removes_the_submission_and_keeps_the_edm_rdm_their_analyses_and_jobs(
        iteration2_db, loss_db):
    user = iteration2_db.user_a
    sid = seed_submission(user, name="To_delete", crm_ids=("CRM-1", "CRM-2"))
    wb("INSERT INTO submission_status_event (id, submission_id, status_code, at, "
       "inserted_by) VALUES (:id, :s, 'ACTIVE', :now, :u)",
       {"id": str(uuid.uuid4()), "s": sid, "now": NOW, "u": user})
    edm_id, rdm_id = seed_edm_for(sid), seed_rdm_for(sid)
    edm_analysis = seed_analysis(edm_id=edm_id)
    imported = seed_submission_analysis(sid)
    group = seed_submission_analysis(sid, is_group=1)
    for member in (imported, edm_analysis):
        wb("INSERT INTO irp_analysis_group_member (group_analysis_id, member_analysis_id, "
           "inserted_at) VALUES (:g, :m, :now)", {"g": group, "m": member, "now": NOW})
    irp_job = seed_irp_job(submission_id=sid, analysis_id=imported)
    linking = seed_submission(user, name="Links_to_it", crm_ids=("CRM-3",))
    wb("UPDATE submission SET links_to_submission_id = :s WHERE id = :l",
       {"s": sid, "l": linking})
    finalize = seed_rwb_job("finalize_analysis", link=("submission", sid),
                            context=("irp_analysis", imported))
    wb("INSERT INTO rwb_job_heartbeat (rwb_job_id, worker_id, heartbeat_at) "
       "VALUES (:j, 'w1', :now)", {"j": finalize, "now": NOW})
    jobs = [finalize,
            seed_rwb_job("upload_edm", link=("edm", edm_id), context=("edm", edm_id),
                         input_data={"requested_from_submission_id": sid}),
            seed_rwb_job("execute_analysis_batch", link=("edm", edm_id), status="pending",
                         input_data={"submission_id": linking})]

    summary = delete_summary(sid)
    assert (summary.contract_count, summary.edm_count, summary.rdm_count,
            summary.imported_count, summary.group_count) == (2, 1, 1, 1, 1)
    assert [ref.name for ref in summary.linking] == ["Links_to_it"]
    assert summary.blocked is None

    delete_submission(submission_id=sid, actor_id=user)

    for table in ("contract", "submission_status_event", "submission_edm",
                  "submission_rdm", "irp_analysis"):
        assert not _exists(table, "submission_id", sid), table
    assert not _exists("submission", "id", sid)
    assert _count("SELECT COUNT(*) FROM irp_analysis_group_member", {}) == 0
    for table, row_id in (("irp_edm", edm_id), ("irp_rdm", rdm_id),
                          ("irp_analysis", edm_analysis), ("irp_job", irp_job)):
        assert _exists(table, "id", row_id), table
    assert _count("SELECT COUNT(*) FROM irp_job WHERE id = :j AND irp_analysis_id IS NULL "
                  "AND requested_from_submission_id IS NULL", {"j": irp_job}) == 1
    assert _count("SELECT COUNT(*) FROM submission WHERE id = :l "
                  "AND links_to_submission_id IS NULL", {"l": linking}) == 1
    assert all(_exists("rwb_job", "id", j) for j in jobs)
    assert _exists("rwb_job_heartbeat", "rwb_job_id", finalize)


@pytest.mark.parametrize("make_running, reason", [
    (lambda sid: seed_irp_job(submission_id=sid, status="RUNNING"), "1 Risk Modeler job"),
    (lambda sid: seed_irp_job(analysis_id=seed_submission_analysis(sid),
                              status="SUBMISSION RETRYING"),
     "1 Risk Modeler job"),
    (lambda sid: seed_rwb_job("submit_grouping", link=("submission", sid.upper()),
                              status="pending"),
     "1 Workbench job"),
    (lambda sid: seed_rwb_job("finalize_analysis", status="running",
                              context=("irp_analysis", seed_submission_analysis(sid))),
     "1 Workbench job"),
    (lambda sid: seed_rwb_job("execute_analysis_batch", status="pending",
                              input_data={"submission_id": sid}),
     "1 Workbench job"),
    (lambda sid: seed_rwb_job("upload_edm", link=("edm", None), status="running",
                              input_data={"requested_from_submission_id": sid}),
     "1 Workbench job"),
    (lambda sid: seed_manifest(submission_id=sid), "1 export is not loaded or closed"),
    (lambda sid: seed_manifest(submission_id=sid, irp_export_job_id="9001"),
     "1 export is not loaded or closed"),
    (lambda sid: seed_manifest(submission_id=sid, stage_status="failed"),
     "1 export is not loaded or closed"),
])
def test_unfinished_work_refuses_the_delete_and_deletes_nothing(
        iteration2_db, loss_db, make_running, reason):
    sid = seed_submission(iteration2_db.user_a)
    make_running(sid)
    assert reason in delete_summary(sid).blocked
    with pytest.raises(SubmissionDeleteBlocked, match=reason):
        delete_submission(submission_id=sid, actor_id=iteration2_db.user_a)
    assert _exists("submission", "id", sid)
    assert _exists("contract", "submission_id", sid)


@pytest.mark.parametrize("columns", [{"load_status": "loaded"}, {"closed_at": NOW}])
def test_a_loaded_or_closed_export_allows_the_delete_and_stays(iteration2_db, loss_db, columns):
    sid = seed_submission(iteration2_db.user_a)
    manifest = seed_manifest(submission_id=sid, **columns)
    assert delete_summary(sid).blocked is None
    delete_submission(submission_id=sid, actor_id=iteration2_db.user_a)
    assert execute_scalar(
        "SELECT COUNT(*) FROM stage.rwb_loss_result_manifest WHERE manifest_id = :m",
        {"m": manifest["manifest_id"]}, connection="LOSS") == 1


def test_an_unreachable_loss_repository_refuses_the_delete(iteration2_db, monkeypatch):
    def unreachable(*args, **kwargs):
        raise SQLAlchemyError("down")
    monkeypatch.setattr(submission_delete_service, "execute_scalar", unreachable)
    sid = seed_submission(iteration2_db.user_a)
    assert "loss repository is unreachable" in delete_summary(sid).blocked
    with pytest.raises(SubmissionDeleteBlocked):
        delete_submission(submission_id=sid, actor_id=iteration2_db.user_a)
    assert _exists("submission", "id", sid)


def test_delete_cancels_the_failed_jobs_that_name_the_submission(iteration2_db, loss_db):
    sid = seed_submission(iteration2_db.user_a)
    other = seed_submission(iteration2_db.user_a, name="Other", crm_ids=("CRM-9",))
    failed = seed_rwb_job("execute_analysis_batch", status="failed",
                          input_data={"submission_id": sid})
    kept = seed_rwb_job("execute_analysis_batch", status="failed",
                        input_data={"submission_id": other})

    summary = delete_summary(sid)
    assert (summary.failed_job_count, summary.blocked) == (1, None)
    delete_submission(submission_id=sid, actor_id=iteration2_db.user_a)

    status = "SELECT status_code FROM rwb_job WHERE id = :j"
    assert execute_scalar(status, {"j": failed}, connection="WORKBENCH") == "cancelled"
    assert execute_scalar(status, {"j": kept}, connection="WORKBENCH") == "failed"
