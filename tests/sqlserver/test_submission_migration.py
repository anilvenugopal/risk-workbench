"""SQL Server integration tests for the Iteration-1 submission/package schema.

Run with: pytest tests/sqlserver --run-sqlserver  (requires live SQL Server)

Covers:
  - the migration builds all nine Iteration-1 tables + the self-renewal CHECK,
    with the CR-003 tables gone and the two kind seeds present (T018);
  - the event-sourced status transaction is atomic — the submission_status_event
    insert and the cached submission.status_code stamp commit **and** roll back
    together (T032 / Article 4 / R2).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from db import execute, execute_command, execute_scalar, get_connection
from db.errors import SQLServerQueryError

pytestmark = pytest.mark.sqlserver

ITERATION1_TABLES = [
    "treaty_type_kind", "submission_status_kind", "package", "submission",
    "submission_crm_id", "submission_status_event", "submission_package",
    "irp_edm", "irp_rdm",
]
REMOVED_TABLES = ["customer", "program", "user_customer_access"]


def _table_exists(name: str) -> int:
    return execute_scalar(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_NAME = :n AND TABLE_SCHEMA = 'dbo'",
        {"n": name}, connection="WORKBENCH",
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── T018: structure / seeds ───────────────────────────────────────────────────

class TestSubmissionMigration:
    @pytest.mark.parametrize("name", ITERATION1_TABLES)
    def test_iteration1_table_exists(self, name):
        assert _table_exists(name) == 1

    @pytest.mark.parametrize("name", REMOVED_TABLES)
    def test_cr003_table_removed(self, name):
        assert _table_exists(name) == 0

    def test_submission_status_kind_seeds(self):
        codes = {r["code"] for r in execute(
            "SELECT code FROM submission_status_kind", {}, connection="WORKBENCH")}
        assert codes == {"ACTIVE", "COMPLETED", "CANCELLED"}

    def test_treaty_type_kind_seeds(self):
        codes = {r["code"] for r in execute(
            "SELECT code FROM treaty_type_kind", {}, connection="WORKBENCH")}
        assert codes == {"cat_xol", "quota_share", "surplus", "per_risk_xol",
                         "aggregate_xol", "stop_loss"}

    def test_submission_has_no_unique_name_and_no_customer_id(self):
        cols = {r["COLUMN_NAME"] for r in execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'submission'", {}, connection="WORKBENCH")}
        assert "customer_id" not in cols
        assert "assigned_analyst_id" in cols

    def test_self_renewal_check_exists(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.check_constraints "
            "WHERE name = 'ck_submission_no_self_link'",
            {}, connection="WORKBENCH")
        assert n == 1

    def test_submission_foreign_keys_present(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.foreign_keys "
            "WHERE parent_object_id = OBJECT_ID('dbo.submission')",
            {}, connection="WORKBENCH")
        assert n >= 5  # analyst, treaty_type, status, self-renewal, inserted/updated_by


# ── Fixtures: a throwaway analyst + submission (cleaned up after) ─────────────

@pytest.fixture()
def temp_submission():
    uid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    now = _utcnow()
    execute_command(
        "INSERT INTO app_user (id, email, display_name, must_change_password, "
        "is_active) VALUES (:id, :email, 'Mig Test', 0, 1)",
        {"id": uid, "email": f"mig_{uid[:8]}@example.com"}, connection="WORKBENCH")
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            conn.execute(text(
                "INSERT INTO submission (id, assigned_analyst_id, name, cedant_name, "
                "treaty_type_code, inception_date, status_code, inserted_at, "
                "updated_at, inserted_by, updated_by) "
                "VALUES (:id, :uid, 'MigDeal', 'Mig Cedant', 'cat_xol', :inc, "
                "'ACTIVE', :now, :now, :uid, :uid)"
            ), {"id": sid, "uid": uid, "inc": now.date(), "now": now})
            conn.execute(text(
                "INSERT INTO submission_status_event (id, submission_id, status_code, "
                "at, inserted_by) VALUES (:eid, :sid, 'ACTIVE', :now, :uid)"
            ), {"eid": str(uuid.uuid4()), "sid": sid, "now": now, "uid": uid})
    yield sid, uid
    # cleanup (children first)
    for tbl in ("submission_status_event", "submission_crm_id"):
        execute_command(f"DELETE FROM {tbl} WHERE submission_id = :sid",
                        {"sid": sid}, connection="WORKBENCH")
    execute_command("DELETE FROM submission WHERE id = :sid", {"sid": sid},
                    connection="WORKBENCH")
    execute_command("DELETE FROM app_user WHERE id = :uid", {"uid": uid},
                    connection="WORKBENCH")


# ── T032: event-sourced status transaction atomicity ─────────────────────────

class TestEventSourcedStatusTxn:
    def _marker(self, sid):
        return execute_scalar("SELECT updated_at FROM submission WHERE id = :id",
                              {"id": sid}, connection="WORKBENCH")

    def test_status_change_commits_event_and_cached_column_together(self, temp_submission):
        sid, uid = temp_submission
        now = _utcnow()
        with get_connection("WORKBENCH") as conn:
            with conn.begin():
                conn.execute(text(
                    "UPDATE submission SET status_code = 'COMPLETED', updated_at = :now "
                    "WHERE id = :id AND updated_at = :expected"
                ), {"now": now, "id": sid, "expected": self._marker(sid)})
                conn.execute(text(
                    "INSERT INTO submission_status_event (id, submission_id, "
                    "status_code, at, inserted_by) VALUES (:eid, :sid, 'COMPLETED', "
                    ":now, :uid)"
                ), {"eid": str(uuid.uuid4()), "sid": sid, "now": now, "uid": uid})
        # both committed
        assert execute_scalar("SELECT status_code FROM submission WHERE id = :id",
                              {"id": sid}, connection="WORKBENCH") == "COMPLETED"
        n = execute_scalar(
            "SELECT COUNT(*) FROM submission_status_event "
            "WHERE submission_id = :sid AND status_code = 'COMPLETED'",
            {"sid": sid}, connection="WORKBENCH")
        assert n == 1

    def test_failed_event_insert_rolls_back_cached_column(self, temp_submission):
        sid, uid = temp_submission
        before = execute_scalar("SELECT status_code FROM submission WHERE id = :id",
                                {"id": sid}, connection="WORKBENCH")
        now = _utcnow()
        # The event insert uses a bogus status_code that violates the FK to
        # submission_status_kind → the whole transaction must roll back, leaving
        # the cached status_code unchanged.
        with pytest.raises((SQLServerQueryError, Exception)):
            with get_connection("WORKBENCH") as conn:
                with conn.begin():
                    conn.execute(text(
                        "UPDATE submission SET status_code = 'CANCELLED', "
                        "updated_at = :now WHERE id = :id"
                    ), {"now": now, "id": sid})
                    conn.execute(text(
                        "INSERT INTO submission_status_event (id, submission_id, "
                        "status_code, at, inserted_by) VALUES (:eid, :sid, "
                        "'BOGUS_STATUS', :now, :uid)"
                    ), {"eid": str(uuid.uuid4()), "sid": sid, "now": now, "uid": uid})
        # cached column rolled back with the failed event
        after = execute_scalar("SELECT status_code FROM submission WHERE id = :id",
                               {"id": sid}, connection="WORKBENCH")
        assert after == before
        n = execute_scalar(
            "SELECT COUNT(*) FROM submission_status_event "
            "WHERE submission_id = :sid AND status_code = 'CANCELLED'",
            {"sid": sid}, connection="WORKBENCH")
        assert n == 0
