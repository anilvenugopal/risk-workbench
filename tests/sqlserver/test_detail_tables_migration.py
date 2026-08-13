"""SQL Server integration tests for the Iteration-3 detail schema (spec 004, T010).

Run with: pytest tests/sqlserver --run-sqlserver  (requires live SQL Server)

Covers:
  - the extended migration builds irp_portfolio / irp_treaty with their FKs, the
    UNIQUE(edm_id, irp_id) idempotent-upsert keys, and the (edm_id) indexes;
  - irp_analysis carries the three new detail columns (settings_metadata,
    is_group, exposure_resource_id) — and NOT the deferred group_parent_id;
  - the backfill_edm_detail rwb_job_type_kind seed row is present;
  - no scope/customer column anywhere (Article 6) and no status column on the
    detail entities (Article 4);
  - the idempotent portfolio-detail upsert overwrites exposure_detail/as_of in
    place under the real driver — no duplicate row on UNIQUE(edm_id, irp_id)
    (data-model §8).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest

from app.services import portfolio_service
from db import execute, execute_command, execute_scalar

pytestmark = pytest.mark.sqlserver

DETAIL_TABLES = ["irp_portfolio", "irp_treaty"]


def _table_exists(name: str) -> int:
    return execute_scalar(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_NAME = :n AND TABLE_SCHEMA = 'dbo'",
        {"n": name}, connection="WORKBENCH",
    )


def _columns(table: str) -> set[str]:
    return {r["COLUMN_NAME"] for r in execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME = :t", {"t": table}, connection="WORKBENCH")}


class TestDetailTablesMigration:
    @pytest.mark.parametrize("name", DETAIL_TABLES)
    def test_detail_table_exists(self, name):
        assert _table_exists(name) == 1

    def test_irp_portfolio_columns(self):
        cols = _columns("irp_portfolio")
        assert {"id", "edm_id", "name", "irp_id", "exposure_detail", "as_of",
                "deleted_at", "inserted_at", "updated_at", "inserted_by",
                "updated_by"} <= cols
        assert "status" not in cols       # Article 4 — no new status column
        assert "customer_id" not in cols  # Article 6

    def test_irp_treaty_columns(self):
        cols = _columns("irp_treaty")
        assert {"id", "edm_id", "name", "irp_id", "attributes", "as_of",
                "deleted_at", "inserted_at", "updated_at", "inserted_by",
                "updated_by"} <= cols
        assert "status" not in cols       # Article 4
        assert "customer_id" not in cols  # Article 6

    def test_irp_analysis_new_detail_columns(self):
        cols = _columns("irp_analysis")
        assert {"settings_metadata", "is_group", "exposure_resource_id"} <= cols
        # group_parent_id stays DEFERRED — RM exposes no group membership
        # (data-model §4/§6); it must not be silently added.
        assert "group_parent_id" not in cols
        assert "customer_id" not in cols  # Article 6
        assert "package_id" not in cols

    @pytest.mark.parametrize("table,constraint", [
        ("irp_portfolio", "uq_irp_portfolio_edm_irp"),
        ("irp_treaty", "uq_irp_treaty_edm_irp"),
    ])
    def test_unique_edm_irp_key_present(self, table, constraint):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.key_constraints "
            "WHERE name = :c AND parent_object_id = OBJECT_ID(:t)",
            {"c": constraint, "t": f"dbo.{table}"}, connection="WORKBENCH")
        assert n == 1  # the idempotent-upsert backbone (data-model §2/§3)

    @pytest.mark.parametrize("table", DETAIL_TABLES)
    def test_detail_table_foreign_keys_present(self, table):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.foreign_keys "
            "WHERE parent_object_id = OBJECT_ID(:t)",
            {"t": f"dbo.{table}"}, connection="WORKBENCH")
        assert n >= 3  # edm_id + inserted_by/updated_by user FKs

    @pytest.mark.parametrize("table,index", [
        ("irp_portfolio", "ix_irp_portfolio_edm_id"),
        ("irp_treaty", "ix_irp_treaty_edm_id"),
    ])
    def test_edm_id_index_present(self, table, index):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.indexes "
            "WHERE name = :i AND object_id = OBJECT_ID(:t)",
            {"i": index, "t": f"dbo.{table}"}, connection="WORKBENCH")
        assert n == 1

    def test_backfill_edm_detail_seed_present(self):
        row = execute(
            "SELECT code, label, sort_order FROM rwb_job_type_kind "
            "WHERE code = 'backfill_edm_detail'", {}, connection="WORKBENCH")
        assert len(row) == 1
        assert row[0]["label"] == "Backfill EDM Detail"
        assert row[0]["sort_order"] == 27

    def test_live_edm_irp_id_is_unique(self):
        row = execute(
            "SELECT is_unique, has_filter, filter_definition FROM sys.indexes "
            "WHERE name = 'uq_irp_edm_live_irp_id' "
            "AND object_id = OBJECT_ID('dbo.irp_edm')",
            connection="WORKBENCH")

        assert len(row) == 1
        assert row[0]["is_unique"] is True
        assert row[0]["has_filter"] is True
        assert "[irp_id] IS NOT NULL" in row[0]["filter_definition"]
        assert "[deleted_at] IS NULL" in row[0]["filter_definition"]


# ── behavioral: the idempotent detail upsert under the real driver ────────────

@pytest.fixture()
def scratch_edm():
    """A throwaway irp_edm row (FK target for irp_portfolio), cleaned up after."""
    edm_id = str(uuid.uuid4())
    now = datetime.utcnow()
    execute_command(
        "INSERT INTO irp_edm (id, name, status, inserted_at, updated_at) "
        "VALUES (:i, :n, 'ready', :now, :now)",
        {"i": edm_id, "n": f"upsert-test-{edm_id[:8]}", "now": now},
        connection="WORKBENCH")
    yield edm_id
    execute_command("DELETE FROM irp_portfolio WHERE edm_id = :e",
                    {"e": edm_id}, connection="WORKBENCH")
    execute_command("DELETE FROM irp_treaty WHERE edm_id = :e",
                    {"e": edm_id}, connection="WORKBENCH")
    execute_command("DELETE FROM irp_edm WHERE id = :i",
                    {"i": edm_id}, connection="WORKBENCH")


class TestDetailUpsertBehavior:
    def test_portfolio_upsert_overwrites_in_place_no_duplicate(self, scratch_edm):
        first = {"location_count": 100, "perils": ["EQ"]}
        second = {"location_count": 250, "perils": ["EQ", "WS"]}
        t1 = datetime.utcnow()
        portfolio_service.upsert_portfolio_detail(
            edm_id=scratch_edm, irp_id="9001", name="P1",
            exposure_detail=first, as_of=t1)
        portfolio_service.upsert_portfolio_detail(
            edm_id=scratch_edm, irp_id="9001", name="P1",
            exposure_detail=second, as_of=t1 + timedelta(minutes=5))

        rows = execute(
            "SELECT irp_id, name, exposure_detail, as_of FROM irp_portfolio "
            "WHERE edm_id = :e", {"e": scratch_edm}, connection="WORKBENCH")
        assert len(rows) == 1  # UNIQUE(edm_id, irp_id) — overwrite, not insert
        assert json.loads(rows[0]["exposure_detail"]) == second
        assert rows[0]["as_of"] > t1

    def test_treaty_upsert_overwrites_in_place_no_duplicate(self, scratch_edm):
        # US2 (data-model §8): the treaty half of the same idempotent contract —
        # a re-backfill overwrites attributes/as_of in place, never a duplicate.
        from app.services import treaty_service
        first = {"treatyType": "CATA", "occurrenceLimit": 100000000.0}
        second = {"treatyType": "CATA", "occurrenceLimit": 150000000.0}
        t1 = datetime.utcnow()
        treaty_service.upsert_treaty_detail(
            edm_id=scratch_edm, irp_id="7001", name="T1",
            attributes=first, as_of=t1)
        treaty_service.upsert_treaty_detail(
            edm_id=scratch_edm, irp_id="7001", name="T1",
            attributes=second, as_of=t1 + timedelta(minutes=5))

        rows = execute(
            "SELECT irp_id, attributes, as_of FROM irp_treaty "
            "WHERE edm_id = :e", {"e": scratch_edm}, connection="WORKBENCH")
        assert len(rows) == 1  # UNIQUE(edm_id, irp_id) — overwrite, not insert
        assert json.loads(rows[0]["attributes"]) == second
        assert rows[0]["as_of"] > t1

    def test_name_fallback_backfills_irp_id(self, scratch_edm):
        # First write arrives without the RM id (name-keyed), the re-backfill
        # carries it — the fallback match updates the SAME row and backfills it.
        portfolio_service.upsert_portfolio_detail(
            edm_id=scratch_edm, irp_id=None, name="P2",
            exposure_detail={"location_count": 1}, as_of=datetime.utcnow())
        portfolio_service.upsert_portfolio_detail(
            edm_id=scratch_edm, irp_id="9002", name="P2",
            exposure_detail={"location_count": 2}, as_of=datetime.utcnow())
        rows = execute(
            "SELECT irp_id, exposure_detail FROM irp_portfolio WHERE edm_id = :e",
            {"e": scratch_edm}, connection="WORKBENCH")
        assert len(rows) == 1
        assert rows[0]["irp_id"] == "9002"
        assert json.loads(rows[0]["exposure_detail"])["location_count"] == 2
