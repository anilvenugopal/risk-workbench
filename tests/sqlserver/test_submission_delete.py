"""Delete a submission against a LIVE SQL Server (issue 206).

The SQLite mirror carries no foreign keys on most of the tables the delete
touches, so only this tier proves the delete order: every foreign key is NO
ACTION, and a child left behind makes the ``submission`` DELETE fail.

Run with:  make test-sql   (requires live SQL Server)
"""

from __future__ import annotations

import uuid

import pytest

from app.services import export_service
from app.services.submission_delete_service import delete_submission
from db import execute_command, execute_scalar
from tests.unit.export_rows import NOW, seed_analysis, seed_edm_for, seed_rdm_for, seed_submission
from tests.unit.test_submission_delete_service import _irp_job, _rwb_job, _submission_analysis

pytestmark = pytest.mark.sqlserver


def _wb(sql: str, params: dict) -> None:
    execute_command(sql, params, connection="WORKBENCH")


def test_delete_satisfies_every_foreign_key(monkeypatch):
    # The loss repository is not under test; no export is running.
    monkeypatch.setattr(export_service, "list_export_rows", lambda submission_id: [])
    user = str(uuid.uuid4())
    _wb("INSERT INTO app_user (id, email, display_name, must_change_password, is_active) "
        "VALUES (:id, :email, 'Delete Test', 0, 1)",
        {"id": user, "email": f"del_{user[:8]}@example.com"})
    sid = seed_submission(user, name=f"Del_{user[:8]}", crm_ids=(f"D-{user[:8]}",))
    linking = seed_submission(user, name=f"Link_{user[:8]}", crm_ids=(f"L-{user[:8]}",))
    edm_id = rdm_id = edm_analysis = irp_job = kept_job = None
    try:
        _wb("UPDATE submission SET links_to_submission_id = :s WHERE id = :l",
            {"s": sid, "l": linking})
        _wb("INSERT INTO submission_status_event (id, submission_id, status_code, at, "
            "inserted_by) VALUES (:id, :s, 'ACTIVE', :now, :u)",
            {"id": str(uuid.uuid4()), "s": sid, "now": NOW, "u": user})
        edm_id, rdm_id = seed_edm_for(sid), seed_rdm_for(sid)
        edm_analysis = seed_analysis(edm_id=edm_id, irp_id=None)
        imported = _submission_analysis(sid)
        group = _submission_analysis(sid, is_group=1)
        for member in (imported, edm_analysis):
            _wb("INSERT INTO irp_analysis_group_member (group_analysis_id, "
                "member_analysis_id, inserted_at) VALUES (:g, :m, :now)",
                {"g": group, "m": member, "now": NOW})
        irp_job = _irp_job(submission_id=sid, analysis_id=group)
        grouping = _rwb_job("submit_grouping", link=("submission", sid),
                            context=("irp_analysis", group),
                            input_data={"submission_id": sid})
        _wb("INSERT INTO rwb_job_heartbeat (rwb_job_id, worker_id, heartbeat_at) "
            "VALUES (:j, 'w1', :now)", {"j": grouping, "now": NOW})
        _rwb_job("finalize_analysis", requestor=("irp_analysis", imported),
                 link=("submission", sid), context=("irp_analysis", imported))
        kept_job = _rwb_job("upload_edm", link=("edm", edm_id), context=("edm", edm_id),
                            input_data={"requested_from_submission_id": sid})

        delete_submission(submission_id=sid, actor_id=user)

        assert execute_scalar("SELECT COUNT(*) FROM submission WHERE id = :s",
                              {"s": sid}, connection="WORKBENCH") == 0
        assert execute_scalar(
            "SELECT COUNT(*) FROM irp_job WHERE id = :j AND irp_analysis_id IS NULL "
            "AND requested_from_submission_id IS NULL",
            {"j": irp_job}, connection="WORKBENCH") == 1
    finally:
        for sql, value in (
                ("DELETE FROM rwb_job WHERE id = :v", kept_job),
                ("DELETE FROM irp_job WHERE id = :v", irp_job),
                ("DELETE FROM irp_analysis WHERE id = :v", edm_analysis),
                ("DELETE FROM submission_edm WHERE edm_id = :v", edm_id),
                ("DELETE FROM irp_edm WHERE id = :v", edm_id),
                ("DELETE FROM submission_rdm WHERE rdm_id = :v", rdm_id),
                ("DELETE FROM irp_rdm WHERE id = :v", rdm_id)):
            if value:
                _wb(sql, {"v": value})
        for submission_id in (sid, linking):
            _wb("UPDATE submission SET links_to_submission_id = NULL WHERE id = :s",
                {"s": submission_id})
        for submission_id in (sid, linking):
            for table in ("contract", "submission_status_event"):
                _wb(f"DELETE FROM {table} WHERE submission_id = :s", {"s": submission_id})
            _wb("DELETE FROM submission WHERE id = :s", {"s": submission_id})
        _wb("DELETE FROM app_user WHERE id = :u", {"u": user})
