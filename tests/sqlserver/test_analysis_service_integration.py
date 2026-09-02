from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import analysis_service
from db import get_connection

pytestmark = pytest.mark.sqlserver


def test_executed_analyses_join_each_newest_job():
    edm_id = str(uuid.uuid4())
    analysis_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    older = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    newer = older + timedelta(minutes=1)
    expected: dict[str, str] = {}

    try:
        with get_connection("WORKBENCH") as conn, conn.begin():
            conn.execute(text(
                "INSERT INTO irp_edm (id, name, status) VALUES (:id, :name, 'ready')"
            ), {"id": edm_id, "name": f"Ranked query {edm_id}"})
            for index, analysis_id in enumerate(analysis_ids):
                conn.execute(text(
                    "INSERT INTO irp_analysis (id, edm_id, name, full_name, "
                    "status_code, execution_id, execution_item_no) "
                    "VALUES (:id, :edm, :name, :name, 'pending', :execution, 0)"
                ), {"id": analysis_id, "edm": edm_id,
                    "name": f"Ranked analysis {index}",
                    "execution": str(uuid.uuid4())})
                old_job_id = str(uuid.uuid4())
                new_job_id = str(uuid.uuid4())
                conn.execute(text(
                    "INSERT INTO irp_job (id, irp_analysis_id, irp_job_type, "
                    "status, submission_attempt_count, inserted_at, updated_at) "
                    "VALUES (:id, :analysis, 'analysis', :status, :attempts, "
                    ":inserted, :inserted)"
                ), [
                    {"id": old_job_id, "analysis": analysis_id,
                     "status": "QUEUED", "attempts": 1, "inserted": older},
                    {"id": new_job_id, "analysis": analysis_id,
                     "status": "RUNNING", "attempts": 2, "inserted": newer},
                ])
                expected[analysis_id] = new_job_id

        rows = analysis_service.list_executed_analyses(edm_id=edm_id)

        assert {row.id: row.irp_job_id for row in rows} == expected
        assert all(row.job_status == "RUNNING" for row in rows)
        assert all(row.submission_attempt_count == 2 for row in rows)
    finally:
        with get_connection("WORKBENCH") as conn, conn.begin():
            conn.execute(text(
                "DELETE FROM irp_job WHERE irp_analysis_id IN (:a1, :a2)"
            ), {"a1": analysis_ids[0], "a2": analysis_ids[1]})
            conn.execute(text(
                "DELETE FROM irp_analysis WHERE id IN (:a1, :a2)"
            ), {"a1": analysis_ids[0], "a2": analysis_ids[1]})
            conn.execute(text("DELETE FROM irp_edm WHERE id = :id"), {"id": edm_id})


def test_delete_by_submission_frees_the_name_of_an_analysis_and_a_group(
        fake_irp):
    """The submission-scoped delete against real SQL Server: the ``UNION ALL``
    candidate query resolves both an own analysis and a group row, and the
    filtered unique indexes (``uq_irp_analysis_live_edm_name``,
    ``uq_irp_analysis_live_submission_name``) let the freed names be reused.
    SQLite models neither the UNION's type coercion nor the filtered index the
    same way, so the unit tier cannot prove this."""
    edm_id = str(uuid.uuid4())
    submission_id = str(uuid.uuid4())
    analysis_id = str(uuid.uuid4())
    group_id = str(uuid.uuid4())
    reused = [str(uuid.uuid4()), str(uuid.uuid4())]

    try:
        with get_connection("WORKBENCH") as conn, conn.begin():
            conn.execute(text(
                "INSERT INTO irp_edm (id, name, status) VALUES (:id, :name, 'ready')"
            ), {"id": edm_id, "name": f"Delete scope {edm_id}"})
            conn.execute(text(
                "INSERT INTO submission (id, name, cedant_name, status_code) "
                "VALUES (:id, :name, 'Cedant', 'active')"
            ), {"id": submission_id, "name": f"Delete deal {submission_id}"})
            conn.execute(text(
                "INSERT INTO submission_edm (submission_id, edm_id) "
                "VALUES (:s, :e)"), {"s": submission_id, "e": edm_id})
            conn.execute(text(
                "INSERT INTO irp_analysis (id, edm_id, name, full_name, "
                "status_code, irp_id, execution_id, execution_item_no) "
                "VALUES (:id, :edm, 'CRE_Delete_v25', 'CRE_Delete_v25', "
                "'ready', '7101', :execution, 0)"
            ), {"id": analysis_id, "edm": edm_id,
                "execution": str(uuid.uuid4())})
            conn.execute(text(
                "INSERT INTO irp_analysis (id, submission_id, name, full_name, "
                "status_code, is_group, irp_id) "
                "VALUES (:id, :sub, 'CRE_Delete_Group', 'CRE_Delete_Group', "
                "'ready', 1, '7102')"
            ), {"id": group_id, "sub": submission_id})

        outcome = analysis_service.delete_submission_analyses(
            submission_id=submission_id,
            analysis_ids=[analysis_id, group_id], actor_id=None)

        assert outcome.deleted == 2
        assert sorted(fake_irp.deleted_analyses) == ["7101", "7102"]
        with get_connection("WORKBENCH") as conn:
            gone = conn.execute(text(
                "SELECT id FROM irp_analysis WHERE id IN (:a, :g) "
                "AND deleted_at IS NOT NULL"
            ), {"a": analysis_id, "g": group_id}).fetchall()
        assert len(gone) == 2

        # both names are free again — the indexes are filtered on deleted_at
        with get_connection("WORKBENCH") as conn, conn.begin():
            conn.execute(text(
                "INSERT INTO irp_analysis (id, edm_id, name, full_name, "
                "status_code, execution_id, execution_item_no) "
                "VALUES (:id, :edm, 'CRE_Delete_v25', 'CRE_Delete_v25', "
                "'pending', :execution, 0)"
            ), {"id": reused[0], "edm": edm_id, "execution": str(uuid.uuid4())})
            conn.execute(text(
                "INSERT INTO irp_analysis (id, submission_id, name, full_name, "
                "status_code, is_group) "
                "VALUES (:id, :sub, 'CRE_Delete_Group', 'CRE_Delete_Group', "
                "'pending', 1)"), {"id": reused[1], "sub": submission_id})
    finally:
        with get_connection("WORKBENCH") as conn, conn.begin():
            conn.execute(text(
                "DELETE FROM irp_analysis WHERE id IN (:a, :g, :r1, :r2)"
            ), {"a": analysis_id, "g": group_id,
                "r1": reused[0], "r2": reused[1]})
            conn.execute(text(
                "DELETE FROM submission_edm WHERE submission_id = :s"),
                {"s": submission_id})
            conn.execute(text("DELETE FROM submission WHERE id = :s"),
                         {"s": submission_id})
            conn.execute(text("DELETE FROM irp_edm WHERE id = :id"),
                         {"id": edm_id})
