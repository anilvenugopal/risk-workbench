"""SQL Server integration tests for db/elt.py.

Run with: pytest tests/sqlserver --run-sqlserver  (requires live SQL Server)

Covers what the unit tier structurally cannot: upload_parquet's real pyodbc
cursor with fast_executemany, and enrich()'s closing UPDATE ... FROM ... JOIN
(SQL-Server-only syntax, invalid on SQLite). Each test creates its own scratch
table in setup and drops it in teardown, so a run leaves no litter — query the
table from inside the fixture (or comment out the DROP) to inspect rows by hand.
"""

from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from db import enrich, execute, execute_command, upload_parquet
from db.errors import SQLServerQueryError

pytestmark = pytest.mark.sqlserver


# ── upload_parquet ────────────────────────────────────────────────────────────

@pytest.fixture()
def upload_target():
    execute_command("""
        CREATE TABLE dbo.elt_test_upload (
            id INT,
            name VARCHAR(50),
            batch_id INT,
            loaded_at DATETIME2 DEFAULT SYSUTCDATETIME()
        )
    """, connection="WORKBENCH")
    yield "elt_test_upload"
    execute_command("DROP TABLE dbo.elt_test_upload", connection="WORKBENCH")


def _write_parquet(tmp_path, rows):
    table = pa.table(rows)
    path = tmp_path / "sample.parquet"
    pq.write_table(table, path)
    return path


class TestUploadParquet:
    def test_loads_all_rows(self, tmp_path, upload_target):
        path = _write_parquet(tmp_path, {"id": [1, 2, 3], "name": ["a", "b", "c"]})

        rows_inserted = upload_parquet(path, upload_target, batch_size=2, connection="WORKBENCH")

        assert rows_inserted == 3
        rows = execute(
            "SELECT id, name FROM dbo.elt_test_upload ORDER BY id", connection="WORKBENCH"
        )
        assert [(r["id"], r["name"]) for r in rows] == [(1, "a"), (2, "b"), (3, "c")]

    def test_default_column_populated_by_sql_server(self, tmp_path, upload_target):
        path = _write_parquet(tmp_path, {"id": [1], "name": ["a"]})

        upload_parquet(path, upload_target, connection="WORKBENCH")

        row = execute("SELECT loaded_at FROM dbo.elt_test_upload", connection="WORKBENCH")[0]
        assert row["loaded_at"] is not None

    def test_extra_columns_broadcast_to_every_row(self, tmp_path, upload_target):
        path = _write_parquet(tmp_path, {"id": [1, 2], "name": ["a", "b"]})

        upload_parquet(
            path, upload_target, extra_columns={"batch_id": 42}, connection="WORKBENCH",
        )

        rows = execute("SELECT batch_id FROM dbo.elt_test_upload", connection="WORKBENCH")
        assert [r["batch_id"] for r in rows] == [42, 42]

    def test_column_mapping_renames_parquet_column(self, tmp_path, upload_target):
        path = _write_parquet(tmp_path, {"id": [1], "full_name": ["Acme Corp"]})

        upload_parquet(
            path, upload_target,
            column_mapping={"full_name": "name"},
            connection="WORKBENCH",
        )

        row = execute("SELECT name FROM dbo.elt_test_upload", connection="WORKBENCH")[0]
        assert row["name"] == "Acme Corp"

    def test_missing_file_raises(self, upload_target, tmp_path):
        with pytest.raises(SQLServerQueryError, match="not found"):
            upload_parquet(tmp_path / "missing.parquet", upload_target, connection="WORKBENCH")

    def test_unrecognized_column_fails_by_default(self, tmp_path, upload_target):
        # A Parquet column the target table doesn't have is a hard error
        # unless the caller opts into drop_unmapped_columns.
        path = _write_parquet(tmp_path, {"id": [1], "name": ["a"], "extra_field": ["x"]})

        with pytest.raises(SQLServerQueryError):
            upload_parquet(path, upload_target, connection="WORKBENCH")

    def test_drop_unmapped_columns_skips_extra_parquet_columns(self, tmp_path, upload_target):
        path = _write_parquet(
            tmp_path, {"id": [1, 2], "name": ["a", "b"], "extra_field": ["x", "y"]},
        )

        rows_inserted = upload_parquet(
            path, upload_target, drop_unmapped_columns=True, connection="WORKBENCH",
        )

        assert rows_inserted == 2
        rows = execute(
            "SELECT id, name FROM dbo.elt_test_upload ORDER BY id", connection="WORKBENCH"
        )
        assert [(r["id"], r["name"]) for r in rows] == [(1, "a"), (2, "b")]

    def test_drop_unmapped_columns_keeps_mapped_column_whose_target_exists(
        self, tmp_path, upload_target,
    ):
        path = _write_parquet(tmp_path, {"id": [1], "full_name": ["Acme Corp"]})

        upload_parquet(
            path, upload_target,
            column_mapping={"full_name": "name"},
            drop_unmapped_columns=True,
            connection="WORKBENCH",
        )

        row = execute("SELECT name FROM dbo.elt_test_upload", connection="WORKBENCH")[0]
        assert row["name"] == "Acme Corp"

    def test_drop_unmapped_columns_drops_mapped_column_whose_target_does_not_exist(
        self, tmp_path, upload_target,
    ):
        # extra_field is mapped, but its target ("no_such_column") doesn't
        # exist on the table — it's still dropped, not treated as a typo.
        path = _write_parquet(tmp_path, {"id": [1], "name": ["a"], "extra_field": ["x"]})

        rows_inserted = upload_parquet(
            path, upload_target,
            column_mapping={"extra_field": "no_such_column"},
            drop_unmapped_columns=True,
            connection="WORKBENCH",
        )

        assert rows_inserted == 1
        row = execute("SELECT id, name FROM dbo.elt_test_upload", connection="WORKBENCH")[0]
        assert (row["id"], row["name"]) == (1, "a")


# ── enrich ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def enrich_single_key_target():
    execute_command("""
        CREATE TABLE dbo.elt_test_submission (
            elt_data_key INT PRIMARY KEY,
            risk_score DECIMAL(18, 2),
            status VARCHAR(20)
        )
    """, connection="WORKBENCH")
    execute_command("""
        INSERT INTO dbo.elt_test_submission (elt_data_key, risk_score, status) VALUES
        (101, 0.10, 'Pending'),
        (102, 0.40, 'Pending')
    """, connection="WORKBENCH")
    yield "elt_test_submission"
    execute_command("DROP TABLE dbo.elt_test_submission", connection="WORKBENCH")


@pytest.fixture()
def enrich_composite_key_target():
    execute_command("""
        CREATE TABLE dbo.elt_test_policy_coverage (
            region_id INT,
            coverage_code VARCHAR(10),
            credit_rating VARCHAR(10),
            exposure DECIMAL(18, 2),
            PRIMARY KEY (region_id, coverage_code)
        )
    """, connection="WORKBENCH")
    execute_command("""
        INSERT INTO dbo.elt_test_policy_coverage
            (region_id, coverage_code, credit_rating, exposure) VALUES
        (1, 'WIND', 'BBB', 10000.00),
        (1, 'FLOOD', 'A', 25000.00)
    """, connection="WORKBENCH")
    yield "elt_test_policy_coverage"
    execute_command("DROP TABLE dbo.elt_test_policy_coverage", connection="WORKBENCH")


class TestEnrichSingleKey:
    def test_updates_matching_rows_by_key(self, enrich_single_key_target):
        df = pd.DataFrame({
            "elt_data_key": [101, 102],
            "risk_score": [0.85, 0.95],
            "status": ["Approved", "Approved"],
        })

        rows_updated = enrich(
            df, enrich_single_key_target, key_fields="elt_data_key", connection="WORKBENCH",
        )

        assert rows_updated == 2
        rows = execute(
            "SELECT elt_data_key, risk_score, status FROM dbo.elt_test_submission ORDER BY elt_data_key",
            connection="WORKBENCH",
        )
        assert [float(r["risk_score"]) for r in rows] == [0.85, 0.95]
        assert [r["status"] for r in rows] == ["Approved", "Approved"]

    def test_decimal_precision_preserved(self, enrich_single_key_target):
        df = pd.DataFrame({"elt_data_key": [101], "risk_score": [0.87]})

        enrich(df, enrich_single_key_target, key_fields="elt_data_key", connection="WORKBENCH")

        row = execute(
            "SELECT risk_score FROM dbo.elt_test_submission WHERE elt_data_key = 101",
            connection="WORKBENCH",
        )[0]
        assert float(row["risk_score"]) == 0.87

    def test_unrelated_dataframe_columns_are_ignored(self, enrich_single_key_target):
        df = pd.DataFrame({
            "elt_data_key": [101],
            "status": ["Approved"],
            "unrelated_field": ["ignore me"],
        })

        rows_updated = enrich(
            df, enrich_single_key_target, key_fields="elt_data_key", connection="WORKBENCH",
        )

        assert rows_updated == 1

    def test_non_matching_keys_update_zero_rows(self, enrich_single_key_target):
        df = pd.DataFrame({"elt_data_key": [999], "status": ["Approved"]})

        rows_updated = enrich(
            df, enrich_single_key_target, key_fields="elt_data_key", connection="WORKBENCH",
        )

        assert rows_updated == 0


class TestEnrichColumnMapping:
    def test_mapped_key_and_enrichment_columns(self, enrich_single_key_target):
        df = pd.DataFrame({
            "src_id": [101, 102],
            "src_score": [0.85, 0.95],
        })

        rows_updated = enrich(
            df, enrich_single_key_target,
            key_fields="src_id",
            column_mapping={"src_id": "elt_data_key", "src_score": "risk_score"},
            connection="WORKBENCH",
        )

        assert rows_updated == 2
        rows = execute(
            "SELECT elt_data_key, risk_score FROM dbo.elt_test_submission ORDER BY elt_data_key",
            connection="WORKBENCH",
        )
        assert [float(r["risk_score"]) for r in rows] == [0.85, 0.95]

    def test_unmapped_columns_use_their_own_name(self, enrich_single_key_target):
        # Only src_score is mapped; elt_data_key (the key) matches the target
        # column name already, so it needs no entry in column_mapping.
        df = pd.DataFrame({
            "elt_data_key": [101],
            "src_score": [0.42],
        })

        rows_updated = enrich(
            df, enrich_single_key_target,
            key_fields="elt_data_key",
            column_mapping={"src_score": "risk_score"},
            connection="WORKBENCH",
        )

        assert rows_updated == 1
        row = execute(
            "SELECT risk_score FROM dbo.elt_test_submission WHERE elt_data_key = 101",
            connection="WORKBENCH",
        )[0]
        assert float(row["risk_score"]) == 0.42


class TestEnrichCompositeKey:
    def test_updates_matching_rows_by_composite_key(self, enrich_composite_key_target):
        df = pd.DataFrame({
            "region_id": [1, 1],
            "coverage_code": ["WIND", "FLOOD"],
            "credit_rating": ["AAA", "AA+"],
            "exposure": [15000.00, 32000.00],
        })

        rows_updated = enrich(
            df, enrich_composite_key_target,
            key_fields=["region_id", "coverage_code"],
            connection="WORKBENCH",
        )

        assert rows_updated == 2
        rows = execute(
            "SELECT coverage_code, credit_rating, exposure FROM dbo.elt_test_policy_coverage "
            "ORDER BY coverage_code",
            connection="WORKBENCH",
        )
        by_code = {r["coverage_code"]: r for r in rows}
        assert by_code["WIND"]["credit_rating"] == "AAA"
        assert float(by_code["WIND"]["exposure"]) == 15000.00
        assert by_code["FLOOD"]["credit_rating"] == "AA+"
