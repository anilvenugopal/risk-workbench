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
