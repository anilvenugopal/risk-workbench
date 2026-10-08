"""Delete a submission against a LIVE SQL Server (issue 206).

The SQLite mirror carries no foreign keys on most of the tables the delete
touches, so only this tier proves the delete order: every foreign key is NO
ACTION, and a child left behind makes the ``submission`` DELETE fail.

Run with:  make test-sql   (requires live SQL Server)
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.services.submission_delete_service import delete_submission
from db import execute_scalar
from tests.unit.export_rows import (
    NOW,
    seed_analysis,
    seed_edm_for,
    seed_irp_job,
    seed_rdm_for,
    seed_rwb_job,
    seed_submission,
    seed_submission_analysis,
    wb,
)

pytestmark = pytest.mark.sqlserver


@pytest.fixture(scope="module", autouse=True)
def loss_schema():
    """CI creates rwb_loss empty, and the delete reads its export manifest."""
    from db.scripts import execute_script_file  # noqa: PLC0415 — trusted DDL, test only
    bootstrap = Path(__file__).resolve().parents[2] / "db" / "bootstrap"
    execute_script_file(bootstrap / "loss_dev_mirror.sql", connection="LOSS")
    execute_script_file(bootstrap / "loss_schema.sql", connection="LOSS")


def test_delete_satisfies_every_foreign_key():
    user = str(uuid.uuid4())
    wb("INSERT INTO app_user (id, email, display_name, must_change_password, is_active) "
        "VALUES (:id, :email, 'Delete Test', 0, 1)",
        {"id": user, "email": f"del_{user[:8]}@example.com"})
    sid = seed_submission(user, name=f"Del_{user[:8]}", crm_ids=(f"D-{user[:8]}",))
    linking = seed_submission(user, name=f"Link_{user[:8]}", crm_ids=(f"L-{user[:8]}",))
    edm_id = rdm_id = edm_analysis = irp_job = None
    jobs: list[str] = []
    try:
        wb("UPDATE submission SET links_to_submission_id = :s WHERE id = :l",
            {"s": sid, "l": linking})
        wb("INSERT INTO submission_status_event (id, submission_id, status_code, at, "
            "inserted_by) VALUES (:id, :s, 'ACTIVE', :now, :u)",
            {"id": str(uuid.uuid4()), "s": sid, "now": NOW, "u": user})
        edm_id, rdm_id = seed_edm_for(sid), seed_rdm_for(sid)
        edm_analysis = seed_analysis(edm_id=edm_id, irp_id=None)
        imported = seed_submission_analysis(sid)
        group = seed_submission_analysis(sid, is_group=1)
        for member in (imported, edm_analysis):
            wb("INSERT INTO irp_analysis_group_member (group_analysis_id, "
                "member_analysis_id, inserted_at) VALUES (:g, :m, :now)",
                {"g": group, "m": member, "now": NOW})
        irp_job = seed_irp_job(submission_id=sid, analysis_id=group)
        jobs.append(seed_rwb_job("finalize_analysis", requestor=("irp_analysis", imported),
                                 link=("submission", sid), context=("irp_analysis", imported)))
        failed = seed_rwb_job("execute_analysis_batch", status="failed",
                              input_data={"submission_id": sid})
        jobs.append(failed)

        delete_submission(submission_id=sid, actor_id=user)

        assert execute_scalar("SELECT COUNT(*) FROM submission WHERE id = :s",
                              {"s": sid}, connection="WORKBENCH") == 0
        assert execute_scalar(
            "SELECT COUNT(*) FROM irp_job WHERE id = :j AND irp_analysis_id IS NULL "
            "AND requested_from_submission_id IS NULL",
            {"j": irp_job}, connection="WORKBENCH") == 1
        assert execute_scalar("SELECT status_code FROM rwb_job WHERE id = :j",
                              {"j": failed}, connection="WORKBENCH") == "cancelled"
    finally:
        for sql, value in (
                ("DELETE FROM irp_job WHERE id = :v", irp_job),
                ("DELETE FROM irp_analysis WHERE id = :v", edm_analysis),
                ("DELETE FROM submission_edm WHERE edm_id = :v", edm_id),
                ("DELETE FROM irp_edm WHERE id = :v", edm_id),
                ("DELETE FROM submission_rdm WHERE rdm_id = :v", rdm_id),
                ("DELETE FROM irp_rdm WHERE id = :v", rdm_id)):
            if value:
                wb(sql, {"v": value})
        for job in jobs:
            wb("DELETE FROM rwb_job WHERE id = :v", {"v": job})
        for submission_id in (sid, linking):
            wb("UPDATE submission SET links_to_submission_id = NULL WHERE id = :s",
                {"s": submission_id})
        for submission_id in (sid, linking):
            for table in ("contract", "submission_status_event"):
                wb(f"DELETE FROM {table} WHERE submission_id = :s", {"s": submission_id})
            wb("DELETE FROM submission WHERE id = :s", {"s": submission_id})
        wb("DELETE FROM app_user WHERE id = :u", {"u": user})
