"""SQL Server integration tests for the submission association schema.

Run with: pytest tests/sqlserver --run-sqlserver  (requires live SQL Server)

Covers:
  - the migration builds all nine Iteration-1 tables + the no-self-link CHECK,
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
    "treaty_type_kind", "submission_status_kind", "contract_status_kind", "submission",
    "contract", "submission_status_event", "irp_edm", "irp_rdm",
    "submission_edm", "submission_rdm",
]
# research.md R6 — CIC's eleven modeling treaty types (spec 017 FR-012).
TREATY_TYPE_CODES = {
    "aggregate_xol", "aggregate_cat_xol", "risk_aggregate_xol",
    "per_occurrence_xol", "per_occurrence_cat_xol", "per_risk_xol", "stop_loss",
    "reinstatement_premium_protection", "second_third_fourth_event_risk_exposed",
    "top_and_drop", "top_and_aggregate",
}
REMOVED_TABLES = [
    "customer", "program", "user_customer_access", "package", "submission_package",
]


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
        assert codes == TREATY_TYPE_CODES

    def test_contract_status_kind_seeds(self):
        codes = {r["code"] for r in execute(
            "SELECT code FROM contract_status_kind", {}, connection="WORKBENCH")}
        assert codes == {"OPEN", "WON", "LOST"}

    def test_contract_grain_columns(self):
        """Spec 017 as amended 2026-09-21: treaty type, the term and the deal
        status live on ``contract``; the submission keeps client and data
        vintage (data-model.md §2–§3)."""
        cols = {r["COLUMN_NAME"] for r in execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'submission'", {}, connection="WORKBENCH")}
        assert {"client_id", "data_vintage", "treaty_year"} <= cols
        assert not {"treaty_type_code", "inception_date", "expiration_date",
                    "deal_status_code"} & cols
        contract = {r["COLUMN_NAME"]: r["IS_NULLABLE"] for r in execute(
            "SELECT COLUMN_NAME, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'contract'", {}, connection="WORKBENCH")}
        for required in ("crm_id", "treaty_type_code", "inception_date",
                         "expiration_date", "contract_status_code", "updated_at"):
            assert contract[required] == "NO"
        fks = execute_scalar(
            "SELECT COUNT(*) FROM sys.foreign_keys "
            "WHERE parent_object_id = OBJECT_ID('dbo.contract')",
            {}, connection="WORKBENCH")
        assert fks == 5  # submission, treaty_type, contract_status, inserted/updated_by

    def test_v_contract_exists_with_date_columns(self):
        """The view is the FR-013 extract (T-04); its dates come back as DATE."""
        assert execute_scalar(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.VIEWS "
            "WHERE TABLE_NAME = 'v_contract'",
            {}, connection="WORKBENCH") == 1
        assert execute_scalar(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.VIEWS "
            "WHERE TABLE_NAME = 'v_submission_crm_id'",
            {}, connection="WORKBENCH") == 0
        types = {r["COLUMN_NAME"]: r["DATA_TYPE"] for r in execute(
            "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'v_contract'", {}, connection="WORKBENCH")}
        assert types["inception_date"] == "date"
        assert types["expiration_date"] == "date"
        assert types["data_vintage"] == "date"

    def test_submission_has_no_unique_name_and_no_customer_id(self):
        cols = {r["COLUMN_NAME"] for r in execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'submission'", {}, connection="WORKBENCH")}
        assert "customer_id" not in cols
        assert "assigned_analyst_id" in cols

    def test_no_self_link_check_exists(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.check_constraints "
            "WHERE name = 'ck_submission_no_self_link'",
            {}, connection="WORKBENCH")
        assert n == 1

    def test_submission_indexes(self):
        """The list's order is a contract aggregate since spec 017 T-11, so the
        inception-keyed covering index is gone; the two lookup indexes stay.
        ``uq_contract_crm_id`` (T-15) is unique and unfiltered: a CRM ID on a
        closed deal blocks like one on an active deal."""
        names = {r["name"] for r in execute(
            "SELECT name FROM sys.indexes "
            "WHERE object_id = OBJECT_ID('dbo.submission') AND name IS NOT NULL",
            {}, connection="WORKBENCH")}
        assert {"ix_submission_cedant_name", "ix_submission_assigned_analyst_id"} <= names
        assert not {"ix_submission_list_order", "ix_submission_treaty_type_code"} & names
        assert execute_scalar(
            "SELECT COUNT(*) FROM sys.indexes WHERE object_id = OBJECT_ID('dbo.contract') "
            "AND name = 'ix_contract_submission_id'", {}, connection="WORKBENCH") == 1
        assert execute(
            "SELECT is_unique, has_filter FROM sys.indexes "
            "WHERE object_id = OBJECT_ID('dbo.contract') AND name = 'uq_contract_crm_id'",
            {}, connection="WORKBENCH") == [{"is_unique": True, "has_filter": False}]

    def test_submission_foreign_keys_present(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.foreign_keys "
            "WHERE parent_object_id = OBJECT_ID('dbo.submission')",
            {}, connection="WORKBENCH")
        assert n == 5  # analyst, status, links_to, inserted_by, updated_by

    @pytest.mark.parametrize("table,entity_column,index_name", [
        ("submission_edm", "edm_id", "ix_submission_edm_edm_submission"),
        ("submission_rdm", "rdm_id", "ix_submission_rdm_rdm_submission"),
    ])
    def test_association_key_and_reverse_index(self, table, entity_column, index_name):
        keys = execute(
            "SELECT c.name FROM sys.indexes i "
            "JOIN sys.index_columns ic ON ic.object_id = i.object_id "
            "AND ic.index_id = i.index_id "
            "JOIN sys.columns c ON c.object_id = i.object_id "
            "AND c.column_id = ic.column_id "
            "WHERE i.object_id = OBJECT_ID(:table) AND i.is_primary_key = 1 "
            "ORDER BY ic.key_ordinal",
            {"table": f"dbo.{table}"}, connection="WORKBENCH")
        assert [row["name"] for row in keys] == ["submission_id", entity_column]
        assert execute_scalar(
            "SELECT COUNT(*) FROM sys.indexes WHERE object_id = OBJECT_ID(:table) "
            "AND name = :name",
            {"table": f"dbo.{table}", "name": index_name},
            connection="WORKBENCH",
        ) == 1


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
                "status_code, inserted_at, updated_at, inserted_by, updated_by) "
                "VALUES (:id, :uid, 'MigDeal', 'Mig Cedant', "
                "'ACTIVE', :now, :now, :uid, :uid)"
            ), {"id": sid, "uid": uid, "now": now})
            conn.execute(text(
                "INSERT INTO submission_status_event (id, submission_id, status_code, "
                "at, inserted_by) VALUES (:eid, :sid, 'ACTIVE', :now, :uid)"
            ), {"eid": str(uuid.uuid4()), "sid": sid, "now": now, "uid": uid})
    yield sid, uid
    # cleanup (children first)
    for tbl in ("submission_edm", "submission_rdm", "submission_status_event",
                "contract"):
        execute_command(f"DELETE FROM {tbl} WHERE submission_id = :sid",
                        {"sid": sid}, connection="WORKBENCH")
    execute_command("DELETE FROM submission WHERE id = :sid", {"sid": sid},
                    connection="WORKBENCH")
    execute_command("DELETE FROM app_user WHERE id = :uid", {"uid": uid},
                    connection="WORKBENCH")


# ── spec 017 T-06: the repository client read over LOSS ─────────────────────

def test_dbo_client_reads_over_loss_when_the_table_exists():
    """``client_service`` reads ``dbo.Client`` in ``rwb_loss``. Spec 014's
    bootstrap creates the table; without it the read fails open, which is
    FR-009's own scenario, so the test skips rather than asserts."""
    from app.services import client_service

    present = execute_scalar(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'Client'",
        {}, connection="LOSS")
    if not present:
        pytest.skip("dbo.Client is not in rwb_loss (spec 014 bootstrap not applied)")
    clients = client_service.list_clients()
    assert clients is not None
    assert clients == sorted(clients, key=lambda c: ((c.name or ""), c.id))


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
