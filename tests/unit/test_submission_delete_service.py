"""Deleting a submission (issue 206): which Workbench rows go, which stay, and
which unfinished work refuses the delete. The SQLite mirror carries no foreign
keys on most of these tables, so the delete order is proven only on the SQL
Server tier."""

from __future__ import annotations

import json
import uuid

import pytest

from app.services import export_service
from app.services.submission_delete_service import (
    SubmissionDeleteBlocked,
    delete_submission,
    delete_summary,
)
from db import execute_command, execute_scalar
from db.errors import SQLServerConnectionError
from tests.unit.export_rows import (
    NOW,
    seed_analysis,
    seed_edm_for,
    seed_manifest,
    seed_rdm_for,
    seed_submission,
)


def _wb(sql: str, params: dict) -> None:
    execute_command(sql, params, connection="WORKBENCH")


def _count(sql: str, params: dict) -> int:
    return execute_scalar(sql, params, connection="WORKBENCH")


def _submission_analysis(submission_id: str, *, is_group: int = 0) -> str:
    analysis_id = str(uuid.uuid4())
    _wb("INSERT INTO irp_analysis (id, submission_id, name, status_code, is_group, "
        "inserted_at, updated_at) VALUES (:id, :s, :n, 'ready', :g, :now, :now)",
        {"id": analysis_id, "s": submission_id, "n": f"A-{analysis_id[:6]}",
         "g": is_group, "now": NOW})
    return analysis_id


def _irp_job(*, submission_id: str | None = None, analysis_id: str | None = None,
             status: str = "FINISHED") -> str:
    job_id = str(uuid.uuid4())
    _wb("INSERT INTO irp_job (id, requested_from_submission_id, irp_analysis_id, "
        "irp_job_type, irp_id, status, submission_attempt_count, inserted_at, updated_at) "
        "VALUES (:id, :s, :a, 'analysis', :irp, :st, 1, :now, :now)",
        {"id": job_id, "s": submission_id, "a": analysis_id, "irp": job_id[:8],
         "st": status, "now": NOW})
    return job_id


def _rwb_job(job_type: str, *, requestor=("analyst_request", None),
             link=("not_applicable", None), context=(None, None),
             input_data: dict | None = None, status: str = "succeeded") -> str:
    job_id = str(uuid.uuid4())
    _wb("INSERT INTO rwb_job (id, requestor_type, requestor_id, link_type, link_id, "
        "context_type, context_id, rwb_job_type, status_code, input_data, attempt_count, "
        "inserted_at, updated_at) VALUES (:id, :rt, :rid, :lt, :lid, :ct, :cid, :jt, :st, "
        ":input, 1, :now, :now)",
        {"id": job_id, "rt": requestor[0], "rid": requestor[1] or str(uuid.uuid4()),
         "lt": link[0], "lid": link[1], "ct": context[0], "cid": context[1],
         "jt": job_type, "st": status,
         "input": json.dumps(input_data) if input_data else None, "now": NOW})
    return job_id


def _exists(table: str, column: str, value: str) -> bool:
    return _count(f"SELECT COUNT(*) FROM {table} WHERE {column} = :v", {"v": value}) > 0


def test_delete_removes_the_submission_and_keeps_the_edm_rdm_and_their_analyses(
        iteration2_db, loss_db):
    user = iteration2_db.user_a
    sid = seed_submission(user, name="To_delete", crm_ids=("CRM-1", "CRM-2"))
    _wb("INSERT INTO submission_status_event (id, submission_id, status_code, at, "
        "inserted_by) VALUES (:id, :s, 'ACTIVE', :now, :u)",
        {"id": str(uuid.uuid4()), "s": sid, "now": NOW, "u": user})
    edm_id, rdm_id = seed_edm_for(sid), seed_rdm_for(sid)
    edm_analysis = seed_analysis(edm_id=edm_id)
    imported = _submission_analysis(sid)
    group = _submission_analysis(sid, is_group=1)
    for member in (imported, edm_analysis):
        _wb("INSERT INTO irp_analysis_group_member (group_analysis_id, member_analysis_id, "
            "inserted_at) VALUES (:g, :m, :now)", {"g": group, "m": member, "now": NOW})
    irp_job = _irp_job(submission_id=sid, analysis_id=imported)
    linking = seed_submission(user, name="Links_to_it", crm_ids=("CRM-3",))
    _wb("UPDATE submission SET links_to_submission_id = :s WHERE id = :l",
        {"s": sid, "l": linking})
    finalize = _rwb_job("finalize_analysis", link=("submission", sid),
                        context=("irp_analysis", imported))
    _wb("INSERT INTO rwb_job_heartbeat (rwb_job_id, worker_id, heartbeat_at) "
        "VALUES (:j, 'w1', :now)", {"j": finalize, "now": NOW})
    gone = [finalize,
            _rwb_job("retrieve_analysis_results", requestor=("irp_analysis", group),
                     link=("submission", None), context=("irp_analysis", group)),
            _rwb_job("execute_analysis_batch", link=("edm", edm_id),
                     input_data={"submission_id": sid})]
    kept = [_rwb_job("upload_edm", link=("edm", edm_id), context=("edm", edm_id),
                     input_data={"requested_from_submission_id": sid}),
            _rwb_job("execute_analysis_batch", link=("edm", edm_id),
                     input_data={"submission_id": linking})]
    loaded = seed_manifest(submission_id=sid, load_status="loaded")

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
    assert not any(_exists("rwb_job", "id", j) for j in gone)
    assert all(_exists("rwb_job", "id", j) for j in kept)
    assert _count("SELECT COUNT(*) FROM rwb_job_heartbeat", {}) == 0
    assert execute_scalar(
        "SELECT COUNT(*) FROM stage.rwb_loss_result_manifest WHERE manifest_id = :m",
        {"m": loaded["manifest_id"]}, connection="LOSS") == 1


@pytest.mark.parametrize("make_running, reason", [
    (lambda sid: _irp_job(submission_id=sid, status="RUNNING"), "1 Risk Modeler job"),
    (lambda sid: _irp_job(analysis_id=_submission_analysis(sid), status="SUBMISSION RETRYING"),
     "1 Risk Modeler job"),
    (lambda sid: _rwb_job("submit_grouping", link=("submission", sid), status="pending"),
     "1 Workbench job"),
    (lambda sid: _rwb_job("upload_edm", link=("edm", None), status="running",
                          input_data={"requested_from_submission_id": sid}),
     "1 Workbench job"),
    (lambda sid: seed_manifest(submission_id=sid), "1 export"),
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


def test_an_unreachable_loss_repository_refuses_the_delete(iteration2_db, monkeypatch):
    def unreachable(submission_id):
        raise SQLServerConnectionError("down")
    monkeypatch.setattr(export_service, "list_export_rows", unreachable)
    sid = seed_submission(iteration2_db.user_a)
    assert "loss repository is unreachable" in delete_summary(sid).blocked
    with pytest.raises(SubmissionDeleteBlocked):
        delete_submission(submission_id=sid, actor_id=iteration2_db.user_a)
    assert _exists("submission", "id", sid)
