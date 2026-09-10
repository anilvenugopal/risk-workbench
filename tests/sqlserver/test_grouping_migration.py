"""SQL Server integration tests for the spec-012 grouping schema (T005).

Run with: pytest tests/sqlserver --run-sqlserver  (requires live SQL Server)

Covers:
  - irp_analysis.submission_id (nullable FK to submission) + its index;
  - the three-leg ck_irp_analysis_origin CHECK;
  - the filtered unique uq_irp_analysis_live_submission_name;
  - the irp_analysis_group_member table shape;
  - the submit_grouping rwb_job_type_kind seed row.
"""

from __future__ import annotations

import pytest

from db import execute, execute_scalar

pytestmark = pytest.mark.sqlserver


class TestGroupingMigration:
    def test_submission_id_column_nullable(self):
        row = execute(
            "SELECT IS_NULLABLE, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'irp_analysis' AND COLUMN_NAME = 'submission_id'",
            {}, connection="WORKBENCH")
        assert len(row) == 1
        assert row[0]["IS_NULLABLE"] == "YES"
        assert row[0]["DATA_TYPE"] == "uniqueidentifier"

    def test_submission_id_foreign_key_targets_submission(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.foreign_keys fk "
            "JOIN sys.foreign_key_columns fkc "
            "  ON fk.object_id = fkc.constraint_object_id "
            "WHERE fk.parent_object_id = OBJECT_ID('dbo.irp_analysis') "
            "AND fk.referenced_object_id = OBJECT_ID('dbo.submission') "
            "AND COL_NAME(fkc.parent_object_id, fkc.parent_column_id) "
            "    = 'submission_id'",
            {}, connection="WORKBENCH")
        assert n == 1

    def test_submission_id_index_present(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.indexes "
            "WHERE name = 'ix_irp_analysis_submission_id' "
            "AND object_id = OBJECT_ID('dbo.irp_analysis')",
            {}, connection="WORKBENCH")
        assert n == 1

    def test_origin_check_has_three_legs(self):
        definition = execute_scalar(
            "SELECT definition FROM sys.check_constraints "
            "WHERE name = 'ck_irp_analysis_origin' "
            "AND parent_object_id = OBJECT_ID('dbo.irp_analysis')",
            {}, connection="WORKBENCH")
        assert definition is not None
        for column in ("edm_id", "rdm_id", "submission_id"):
            assert column in definition

    def test_live_submission_name_unique_filtered(self):
        row = execute(
            "SELECT is_unique, has_filter, filter_definition FROM sys.indexes "
            "WHERE name = 'uq_irp_analysis_live_submission_name' "
            "AND object_id = OBJECT_ID('dbo.irp_analysis')",
            {}, connection="WORKBENCH")
        assert len(row) == 1
        assert row[0]["is_unique"] == 1
        assert row[0]["has_filter"] == 1
        assert "submission_id" in row[0]["filter_definition"]
        assert "deleted_at" in row[0]["filter_definition"]

    def test_group_member_table_shape(self):
        cols = {r["COLUMN_NAME"]: r["IS_NULLABLE"] for r in execute(
            "SELECT COLUMN_NAME, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'irp_analysis_group_member'",
            {}, connection="WORKBENCH")}
        assert cols == {"group_analysis_id": "NO", "member_analysis_id": "NO",
                        "inserted_at": "NO"}

    def test_group_member_primary_key_is_the_pair(self):
        pk_cols = [r["COLUMN_NAME"] for r in execute(
            "SELECT kcu.COLUMN_NAME "
            "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
            "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
            "  ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME "
            "WHERE tc.TABLE_NAME = 'irp_analysis_group_member' "
            "AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY' "
            "ORDER BY kcu.ORDINAL_POSITION",
            {}, connection="WORKBENCH")]
        assert pk_cols == ["group_analysis_id", "member_analysis_id"]

    def test_group_member_foreign_keys_target_irp_analysis(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.foreign_keys "
            "WHERE parent_object_id = "
            "  OBJECT_ID('dbo.irp_analysis_group_member') "
            "AND referenced_object_id = OBJECT_ID('dbo.irp_analysis')",
            {}, connection="WORKBENCH")
        assert n == 2

    def test_submit_grouping_kind_row(self):
        row = execute(
            "SELECT label, sort_order FROM rwb_job_type_kind "
            "WHERE code = 'submit_grouping'", {}, connection="WORKBENCH")
        assert len(row) == 1
        assert row[0]["label"] == "Submit grouping"
        assert row[0]["sort_order"] == 33
