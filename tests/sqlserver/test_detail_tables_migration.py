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
    @pytest.mark.parametrize("table", ["irp_edm", "irp_rdm"])
    def test_entity_notes_are_nullable_nvarchar_250(self, table):
        rows = execute(
            "SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :table "
            "AND COLUMN_NAME = 'notes'",
            {"table": table}, connection="WORKBENCH")
        assert rows == [{"DATA_TYPE": "nvarchar",
                         "CHARACTER_MAXIMUM_LENGTH": 250,
                         "IS_NULLABLE": "YES"}]

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

    # ── spec 005 (T009): breakout lineage schema ────────────────────────────────

    def test_irp_portfolio_lineage_columns_present(self):
        cols = _columns("irp_portfolio")
        assert {"source_portfolio_id", "breakout_dimension_code",
                "breakout_value"} <= cols

    def test_irp_portfolio_self_fk_present(self):
        # source_portfolio_id → irp_portfolio.id, ondelete NO ACTION (SQL
        # Server rejects a cascading self-reference).
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.foreign_keys "
            "WHERE parent_object_id = OBJECT_ID('dbo.irp_portfolio') "
            "AND referenced_object_id = OBJECT_ID('dbo.irp_portfolio')",
            {}, connection="WORKBENCH")
        assert n == 1

    def test_breakout_dimension_kind_table_and_seeds(self):
        assert _table_exists("breakout_dimension_kind") == 1
        rows = execute(
            "SELECT code, label FROM breakout_dimension_kind ORDER BY sort_order",
            {}, connection="WORKBENCH")
        assert [(r["code"], r["label"]) for r in rows] == [
            ("lob", "Line of business"), ("state", "Geography - State"),
            ("country", "Geography - Country"), ("peril", "Peril"),
            ("custom", "Custom group")]

    def test_run_breakout_job_type_seeds_present(self):
        rows = execute(
            "SELECT code FROM rwb_job_type_kind "
            "WHERE code IN ('run_breakout_lob', 'run_breakout_state', "
            "'run_breakout_country', 'run_breakout_peril', "
            "'run_breakout_custom')",
            {}, connection="WORKBENCH")
        assert len(rows) == 5

    def test_breakout_group_requestor_type_seed_present(self):
        rows = execute(
            "SELECT code FROM rwb_job_requestor_type_kind "
            "WHERE code = 'breakout_group'", {}, connection="WORKBENCH")
        assert len(rows) == 1

    # ── spec 005 follow-on (T-12): the custom-group entity ─────────────────────

    def test_breakout_group_table_columns_and_constraints(self):
        assert _table_exists("breakout_group") == 1
        cols = _columns("breakout_group")
        assert {"id", "source_portfolio_id", "group_key", "label", "filters",
                "name", "number", "cart_id", "inserted_at", "updated_at",
                "inserted_by", "updated_by"} <= cols
        assert "customer_id" not in cols  # Article 6
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.key_constraints "
            "WHERE name = 'uq_breakout_group_source_key' "
            "AND parent_object_id = OBJECT_ID('dbo.breakout_group')",
            {}, connection="WORKBENCH")
        assert n == 1  # one row per (source, member set) — the job-dedup key
        fk_out = execute_scalar(
            "SELECT COUNT(*) FROM sys.foreign_keys "
            "WHERE parent_object_id = OBJECT_ID('dbo.breakout_group') "
            "AND referenced_object_id = OBJECT_ID('dbo.irp_portfolio')",
            {}, connection="WORKBENCH")
        assert fk_out == 1
        assert "breakout_group_id" in _columns("irp_portfolio")
        fk_back = execute_scalar(
            "SELECT COUNT(*) FROM sys.foreign_keys "
            "WHERE name = 'fk_irp_portfolio_breakout_group'",
            {}, connection="WORKBENCH")
        assert fk_back == 1

    def test_breakout_filtered_unique_index_present(self):
        row = execute(
            "SELECT is_unique, has_filter, filter_definition FROM sys.indexes "
            "WHERE name = 'uq_irp_portfolio_breakout' "
            "AND object_id = OBJECT_ID('dbo.irp_portfolio')",
            {}, connection="WORKBENCH")
        assert len(row) == 1
        assert row[0]["is_unique"] == 1
        assert row[0]["has_filter"] == 1
        definition = (row[0]["filter_definition"] or "").lower()
        assert "source_portfolio_id" in definition
        assert "deleted_at" in definition


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
    # generated rows first — the self-FK forbids removing a source portfolio
    # while a generated row still references it; group rows next, before their
    # source portfolio goes
    execute_command("DELETE FROM irp_portfolio WHERE edm_id = :e "
                    "AND source_portfolio_id IS NOT NULL",
                    {"e": edm_id}, connection="WORKBENCH")
    execute_command("DELETE FROM breakout_group WHERE source_portfolio_id IN "
                    "(SELECT id FROM irp_portfolio WHERE edm_id = :e)",
                    {"e": edm_id}, connection="WORKBENCH")
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


# ── behavioral: breakout lineage uniqueness under the real driver (spec 005) ──────

class TestBreakoutLineageBehavior:
    def _source(self, scratch_edm) -> str:
        portfolio_service.upsert_portfolio_detail(
            edm_id=scratch_edm, irp_id="9001", name="Source 2026",
            exposure_detail={"metrics": {}}, as_of=datetime.utcnow())
        return execute(
            "SELECT id FROM irp_portfolio WHERE edm_id = :e AND irp_id = '9001'",
            {"e": scratch_edm}, connection="WORKBENCH")[0]["id"]

    def test_second_live_generated_portfolio_rejected_as_skip(self, scratch_edm):
        source_id = self._source(scratch_edm)
        first = portfolio_service.save_generated_portfolio(
            scratch_edm, name="Source 2026 - TX", irp_id="9100",
            source_portfolio_id=source_id, dimension_code="state", value="TX",
            actor_id=None)
        assert first.created is True
        # the filtered unique index rejects a second LIVE row for the same
        # (source, dimension, value) — absorbed as created=False, never raised
        second = portfolio_service.save_generated_portfolio(
            scratch_edm, name="Source 2026 - TX (2)", irp_id="9101",
            source_portfolio_id=source_id, dimension_code="state", value="TX",
            actor_id=None)
        assert second.created is False
        assert str(second.portfolio_id).lower() == str(first.portfolio_id).lower()
        n = execute_scalar(
            "SELECT COUNT(*) FROM irp_portfolio WHERE source_portfolio_id = :s "
            "AND deleted_at IS NULL", {"s": source_id}, connection="WORKBENCH")
        assert n == 1

    def test_soft_deleted_generated_row_is_reclaimed_in_place(
            self, scratch_edm):
        # T-16: the re-run reuses the soft-deleted row — deleted_at cleared,
        # the new RM id stamped — never a ghost twin for the same triple.
        source_id = self._source(scratch_edm)
        first = portfolio_service.save_generated_portfolio(
            scratch_edm, name="Source 2026 - TX", irp_id="9100",
            source_portfolio_id=source_id, dimension_code="state", value="TX",
            actor_id=None)
        execute_command(
            "UPDATE irp_portfolio SET deleted_at = :now WHERE id = :i",
            {"now": datetime.utcnow(), "i": str(first.portfolio_id)},
            connection="WORKBENCH")
        second = portfolio_service.save_generated_portfolio(
            scratch_edm, name="Source 2026 - TX", irp_id="9102",
            source_portfolio_id=source_id, dimension_code="state", value="TX",
            actor_id=None)
        assert second.created is True
        assert str(second.portfolio_id).lower() == str(first.portfolio_id).lower()
        rows = execute(
            "SELECT irp_id, deleted_at FROM irp_portfolio "
            "WHERE source_portfolio_id = :s", {"s": source_id},
            connection="WORKBENCH")
        assert len(rows) == 1
        assert rows[0]["irp_id"] == "9102" and rows[0]["deleted_at"] is None

    def _group_row(self, source_id: str, key: str = "abc123def456") -> str:
        gid = str(uuid.uuid4())
        execute_command(
            "INSERT INTO breakout_group (id, source_portfolio_id, group_key, "
            "label, filters, name, number, cart_id, inserted_at, updated_at) "
            "VALUES (:i, :s, :k, 'Coastal', :f, 'Source 2026 - Coastal', "
            "'P9001-G-ABC', :c, :now, :now)",
            {"i": gid, "s": source_id, "k": key,
             "f": json.dumps({"state": ["TX"], "peril": ["2"]}),
             "c": str(uuid.uuid4()), "now": datetime.utcnow()},
            connection="WORKBENCH")
        return gid

    def test_duplicate_live_custom_triple_rejected_as_skip(self, scratch_edm):
        # The same filtered unique index guards custom rows: one LIVE
        # generated portfolio per (source, 'custom', group_key).
        source_id = self._source(scratch_edm)
        gid = self._group_row(source_id)
        first = portfolio_service.save_generated_portfolio(
            scratch_edm, name="Source 2026 - Coastal", irp_id="9200",
            source_portfolio_id=source_id, dimension_code="custom",
            value="abc123def456", actor_id=None, group_id=gid)
        assert first.created is True
        second = portfolio_service.save_generated_portfolio(
            scratch_edm, name="Source 2026 - Coastal (2)", irp_id="9201",
            source_portfolio_id=source_id, dimension_code="custom",
            value="abc123def456", actor_id=None, group_id=gid)
        assert second.created is False
        assert str(second.portfolio_id).lower() == str(first.portfolio_id).lower()

    def test_soft_deleted_custom_row_is_reclaimed(self, scratch_edm):
        source_id = self._source(scratch_edm)
        gid = self._group_row(source_id)
        first = portfolio_service.save_generated_portfolio(
            scratch_edm, name="Source 2026 - Coastal", irp_id="9200",
            source_portfolio_id=source_id, dimension_code="custom",
            value="abc123def456", actor_id=None, group_id=gid)
        execute_command(
            "UPDATE irp_portfolio SET deleted_at = :now WHERE id = :i",
            {"now": datetime.utcnow(), "i": str(first.portfolio_id)},
            connection="WORKBENCH")
        second = portfolio_service.save_generated_portfolio(
            scratch_edm, name="Source 2026 - Coastal", irp_id="9202",
            source_portfolio_id=source_id, dimension_code="custom",
            value="abc123def456", actor_id=None, group_id=gid)
        assert second.created is True
        assert str(second.portfolio_id).lower() == str(first.portfolio_id).lower()
        row = execute(
            "SELECT irp_id, deleted_at, breakout_group_id FROM irp_portfolio "
            "WHERE id = :i", {"i": str(first.portfolio_id)},
            connection="WORKBENCH")[0]
        assert row["irp_id"] == "9202" and row["deleted_at"] is None
        assert str(row["breakout_group_id"]).lower() == gid.lower()
