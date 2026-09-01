"""Unit tests for db/elt.py — validation and pre-flight error paths only.

This file covers only the checks that run before either function touches a
cursor: identifier validation, destination-column construction, and
column-mapping validation. No test here executes SQL (Article 12).

Everything that reads or writes the database — enrich()'s INFORMATION_SCHEMA
introspection and its validation errors, upload_parquet's real pyodbc cursor,
and the actual rows inserted or updated — lives in tests/sqlserver/test_elt.py.
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from db.elt import (
    _build_destination_columns,
    _check_column_mapping_matches_source,
    _drop_unmapped_source_columns,
    _validate_identifier,
    upload_parquet,
)
from db.errors import SQLServerQueryError


class TestValidateIdentifier:
    def test_accepts_simple_name(self):
        assert _validate_identifier("submission") == "[submission]"

    def test_accepts_leading_underscore(self):
        assert _validate_identifier("_col1") == "[_col1]"

    @pytest.mark.parametrize("bad", [
        "table; DROP TABLE x--",
        "table name",
        "table.name",
        "table-name",
        "1table",
        "",
    ])
    def test_rejects_unsafe_names(self, bad):
        with pytest.raises(SQLServerQueryError):
            _validate_identifier(bad)


# ---- upload_parquet ---------------------------------------------------------

class TestBuildDestinationColumns:
    def test_source_columns_pass_through_unmapped(self):
        assert _build_destination_columns(["id", "name"], {}, {}) == ["id", "name"]

    def test_source_columns_preserve_order(self):
        assert _build_destination_columns(["c", "a", "b"], {}, {}) == ["c", "a", "b"]

    def test_column_mapping_renames_source_columns(self):
        result = _build_destination_columns(
            ["id", "full_name"], {"full_name": "name"}, {},
        )
        assert result == ["id", "name"]

    def test_column_mapping_only_affects_listed_columns(self):
        result = _build_destination_columns(
            ["id", "name", "amount"], {"name": "full_name"}, {},
        )
        assert result == ["id", "full_name", "amount"]

    def test_extra_columns_appended_after_source_columns(self):
        result = _build_destination_columns(
            ["id", "name"], {}, {"batch_id": 42, "source_system": "CRM"},
        )
        assert result == ["id", "name", "batch_id", "source_system"]

    def test_extra_column_conflicting_with_source_column_raises(self):
        with pytest.raises(SQLServerQueryError, match="Extra column conflict"):
            _build_destination_columns(["id", "name"], {}, {"name": "should conflict"})

    def test_extra_column_conflicting_with_mapped_target_raises(self):
        # The mapped *target* name is what extra_columns collides against,
        # not the original source column name.
        with pytest.raises(SQLServerQueryError, match="Extra column conflict"):
            _build_destination_columns(
                ["full_name"], {"full_name": "name"}, {"name": "should conflict"},
            )

    def test_extra_column_not_conflicting_with_original_source_name_is_allowed(self):
        # full_name is renamed to "name", so "full_name" itself is free to use
        # as an extra column name.
        result = _build_destination_columns(
            ["full_name"], {"full_name": "name"}, {"full_name": 1},
        )
        assert result == ["name", "full_name"]

    def test_empty_source_and_extra_columns_returns_empty_list(self):
        assert _build_destination_columns([], {}, {}) == []


class TestCheckColumnMappingMatchesSource:
    def test_mapping_keys_present_in_source_passes(self):
        _check_column_mapping_matches_source(["id", "name"], {"name": "full_name"})

    def test_mapping_key_not_in_source_columns_raises(self):
        with pytest.raises(SQLServerQueryError, match="not present in the source columns"):
            _check_column_mapping_matches_source(["id", "name"], {"typo_column": "target_col"})

    def test_empty_mapping_always_passes(self):
        _check_column_mapping_matches_source(["id", "name"], {})


class TestDropUnmappedSourceColumns:
    def test_keeps_columns_matching_target_by_own_name(self):
        result = _drop_unmapped_source_columns(["id", "name"], {}, ["id", "name"])
        assert result == ["id", "name"]

    def test_drops_columns_not_on_target(self):
        result = _drop_unmapped_source_columns(
            ["id", "name", "extra_field"], {}, ["id", "name"],
        )
        assert result == ["id", "name"]

    def test_keeps_mapped_column_when_its_target_exists(self):
        result = _drop_unmapped_source_columns(
            ["id", "full_name"], {"full_name": "name"}, ["id", "name"],
        )
        assert result == ["id", "full_name"]

    def test_drops_mapped_column_when_its_target_does_not_exist(self):
        result = _drop_unmapped_source_columns(
            ["id", "extra_field"], {"extra_field": "no_such_column"}, ["id", "name"],
        )
        assert result == ["id"]

    def test_no_target_columns_drops_everything(self):
        assert _drop_unmapped_source_columns(["id", "name"], {}, []) == []


@pytest.fixture()
def parquet_file(tmp_path):
    table = pa.table({
        "id": [1, 2, 3],
        "name": ["a", "b", "c"],
    })
    path = tmp_path / "sample.parquet"
    pq.write_table(table, path)
    return path


class TestUploadParquetValidation:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(SQLServerQueryError, match="not found"):
            upload_parquet(tmp_path / "does_not_exist.parquet", "target_table")

    def test_extra_column_conflicting_with_parquet_column_raises(self, parquet_file):
        with pytest.raises(SQLServerQueryError, match="Extra column conflict"):
            upload_parquet(
                parquet_file,
                "target_table",
                extra_columns={"name": "should not be allowed"},
            )

    def test_invalid_table_name_raises(self, parquet_file):
        with pytest.raises(SQLServerQueryError, match="Invalid SQL"):
            upload_parquet(parquet_file, "bad table name")

    def test_invalid_column_mapping_target_raises(self, parquet_file):
        with pytest.raises(SQLServerQueryError, match="Invalid SQL"):
            upload_parquet(parquet_file, "target_table", column_mapping={"id": "bad col"})
