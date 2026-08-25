"""SQL Server migration assertions for spec 009 Phase 2."""

from __future__ import annotations

import pytest

from db import execute, execute_scalar

pytestmark = pytest.mark.sqlserver

TABLES = (
    "irp_model_profile",
    "irp_output_profile",
    "irp_event_rate_scheme",
    "irp_currency",
    "irp_currency_scheme",
    "irp_currency_scheme_vintage",
    "analysis_template",
    "analysis_template_tag",
    "template_suite",
    "template_suite_item",
)


@pytest.mark.parametrize("table", TABLES)
def test_template_suite_table_exists(table):
    assert execute_scalar(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :table",
        {"table": table}, connection="WORKBENCH",
    ) == 1


@pytest.mark.parametrize("table,index", [
    ("irp_model_profile", "uq_irp_model_profile_irp_id"),
    ("irp_output_profile", "uq_irp_output_profile_irp_id"),
    ("irp_event_rate_scheme", "uq_irp_event_rate_scheme_irp_id"),
    ("irp_currency", "uq_irp_currency_code"),
    ("irp_currency_scheme", "uq_irp_currency_scheme_irp_id"),
])
def test_reference_cache_natural_key_index_is_unique(table, index):
    rows = execute(
        "SELECT is_unique, has_filter FROM sys.indexes "
        "WHERE object_id = OBJECT_ID(:table) AND name = :index",
        {"table": f"dbo.{table}", "index": index}, connection="WORKBENCH",
    )
    assert rows == [{"is_unique": True, "has_filter": False}]


@pytest.mark.parametrize("table,index", [
    ("analysis_template", "uq_analysis_template_live_name"),
    ("template_suite", "uq_template_suite_live_name"),
])
def test_live_name_index_is_unique_and_filtered(table, index):
    rows = execute(
        "SELECT is_unique, has_filter, filter_definition FROM sys.indexes "
        "WHERE object_id = OBJECT_ID(:table) AND name = :index",
        {"table": f"dbo.{table}", "index": index}, connection="WORKBENCH",
    )
    assert len(rows) == 1
    assert rows[0]["is_unique"] is True
    assert rows[0]["has_filter"] is True
    assert "[deleted_at] IS NULL" in rows[0]["filter_definition"]


def test_suite_item_has_one_template_per_suite_constraint():
    assert execute_scalar(
        "SELECT COUNT(*) FROM sys.key_constraints "
        "WHERE parent_object_id = OBJECT_ID('dbo.template_suite_item') "
        "AND name = 'uq_template_suite_item_template'",
        connection="WORKBENCH",
    ) == 1


def test_template_tag_primary_key_contains_template_and_tag_name():
    columns = execute(
        "SELECT c.name FROM sys.key_constraints kc "
        "JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id "
        "AND ic.index_id = kc.unique_index_id "
        "JOIN sys.columns c ON c.object_id = ic.object_id "
        "AND c.column_id = ic.column_id "
        "WHERE kc.parent_object_id = OBJECT_ID('dbo.analysis_template_tag') "
        "AND kc.type = 'PK' ORDER BY ic.key_ordinal",
        connection="WORKBENCH",
    )
    assert [row["name"] for row in columns] == ["template_id", "tag_name"]


def test_analysis_template_defaults():
    defaults = {row["COLUMN_NAME"]: row["COLUMN_DEFAULT"] for row in execute(
        "SELECT COLUMN_NAME, COLUMN_DEFAULT FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'analysis_template'",
        connection="WORKBENCH",
    )}
    assert defaults["min_loss_threshold"].strip("()'") == "1.00"
    assert defaults["num_max_loss_event"].strip("()'") == "1"
    assert defaults["franchise_deductible"].strip("()'") == "0"
    assert defaults["treat_construction_occupancy_as_unknown"].strip("()'") == "1"


def test_event_rate_scheme_workbench_is_active_column():
    rows = execute(
        "SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'irp_event_rate_scheme' "
        "AND COLUMN_NAME = 'workbench_is_active'",
        connection="WORKBENCH",
    )
    assert len(rows) == 1
    assert rows[0]["DATA_TYPE"] == "bit"
    assert rows[0]["IS_NULLABLE"] == "NO"
    assert rows[0]["COLUMN_DEFAULT"].strip("()'") == "1"


@pytest.mark.parametrize("table,column", [
    ("analysis_template", "treaty_name_pattern"),
    ("analysis_template", "currency_code"),
    ("analysis_template", "currency_scheme_code"),
    ("analysis_template", "currency_vintage"),
    ("template_suite_item", "position"),
    ("template_suite_item", "portfolio_name_override"),
])
def test_dropped_columns_are_absent(table, column):
    assert execute_scalar(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :table AND COLUMN_NAME = :column",
        {"table": table, "column": column}, connection="WORKBENCH",
    ) == 0


def test_sync_irp_metadata_kind_is_seeded():
    rows = execute(
        "SELECT label, sort_order FROM rwb_job_type_kind "
        "WHERE code = 'sync_irp_metadata'",
        connection="WORKBENCH",
    )
    assert rows == [{"label": "Sync IRP metadata", "sort_order": 120}]
