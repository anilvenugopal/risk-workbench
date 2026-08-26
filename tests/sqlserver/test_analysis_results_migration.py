"""SQL Server migration assertions for spec 011 Phase 2.

Run with: make test-sql  (requires live SQL Server; the host shell cannot).

Covers the schema the retrieval worker writes into: the two NVARCHAR(MAX)
columns on ``irp_analysis``, the ``analysis_perspective_kind`` seeds in their
display order, the ``irp_analysis`` requestor-kind row the retrieval job is
keyed on, and a round-trip proving SQL Server reads the stored extract back as
JSON — the read models parse in Python (T-05), but a document the server itself
rejects would be a storage bug, not a parsing one.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest

from db import execute, execute_command, execute_scalar

pytestmark = pytest.mark.sqlserver


@pytest.mark.parametrize("column", ["loss_results", "submitted_settings"])
def test_extract_column_is_nullable_nvarchar_max(column):
    rows = execute(
        "SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'irp_analysis' "
        "AND COLUMN_NAME = :column",
        {"column": column}, connection="WORKBENCH")
    assert rows == [{"DATA_TYPE": "nvarchar",
                     "CHARACTER_MAXIMUM_LENGTH": -1,  # MAX
                     "IS_NULLABLE": "YES"}]


def test_perspective_seeds_are_gross_first():
    rows = execute(
        "SELECT code, label, sort_order FROM analysis_perspective_kind "
        "ORDER BY sort_order",
        connection="WORKBENCH")
    assert rows == [
        {"code": "GR", "label": "Gross", "sort_order": 10},
        {"code": "RL", "label": "Reinsurance Layer", "sort_order": 20},
        {"code": "WX", "label": "Working Excess", "sort_order": 30},
        {"code": "QS", "label": "Quota Share", "sort_order": 40},
        {"code": "GU", "label": "Ground Up", "sort_order": 50},
    ]


def test_irp_analysis_requestor_kind_is_seeded():
    rows = execute(
        "SELECT label, sort_order FROM rwb_job_requestor_type_kind "
        "WHERE code = 'irp_analysis'",
        connection="WORKBENCH")
    assert rows == [{"label": "IRP Analysis", "sort_order": 50}]


@pytest.fixture()
def scratch_analysis():
    """A throwaway own-executed irp_analysis row (its EDM satisfies the origin
    check constraint), cleaned up after."""
    edm_id = str(uuid.uuid4())
    analysis_id = str(uuid.uuid4())
    now = datetime.utcnow()
    execute_command(
        "INSERT INTO irp_edm (id, name, status, inserted_at, updated_at) "
        "VALUES (:i, :n, 'ready', :now, :now)",
        {"i": edm_id, "n": f"results-test-{edm_id[:8]}", "now": now},
        connection="WORKBENCH")
    execute_command(
        "INSERT INTO irp_analysis (id, edm_id, irp_id, name, status_code, "
        "inserted_at, updated_at) "
        "VALUES (:i, :e, '5572174', :n, 'ready', :now, :now)",
        {"i": analysis_id, "e": edm_id, "n": f"A {analysis_id[:8]}", "now": now},
        connection="WORKBENCH")
    yield analysis_id
    execute_command("DELETE FROM irp_analysis WHERE id = :i",
                    {"i": analysis_id}, connection="WORKBENCH")
    execute_command("DELETE FROM irp_edm WHERE id = :i",
                    {"i": edm_id}, connection="WORKBENCH")


def test_loss_results_round_trips_as_json(scratch_analysis):
    extract = {
        "engine_type": "RL", "engine_version": "23.0",
        "retrieved_at": "2026-08-26T14:03:22Z",
        "perspectives": {
            "GR": {"aal": 38270.5904752427, "std_dev": 2645726.187283731,
                   "oep": {"10000": 1234.5}, "aep": {"10000": 2345.6}},
            "RL": None, "WX": None, "QS": None, "GU": None,
        },
    }
    execute_command(
        "UPDATE irp_analysis SET loss_results = :doc WHERE id = :i",
        {"doc": json.dumps(extract), "i": scratch_analysis},
        connection="WORKBENCH")

    stored = execute_scalar(
        "SELECT loss_results FROM irp_analysis WHERE id = :i",
        {"i": scratch_analysis}, connection="WORKBENCH")
    assert json.loads(stored) == extract

    assert execute_scalar(
        "SELECT JSON_VALUE(loss_results, '$.perspectives.GR.aal') "
        "FROM irp_analysis WHERE id = :i",
        {"i": scratch_analysis}, connection="WORKBENCH",
    ) == "38270.5904752427"
