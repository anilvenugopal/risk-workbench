"""Unit tests for db/elt.py — validation and pre-flight error paths only.

Both functions become SQL-Server-only past a certain point: upload_parquet's
`cursor.fast_executemany = True` is a pyodbc-only cursor attribute (raises
AttributeError on any other DBAPI, including sqlite3), and enrich()'s closing
statement is SQL Server's UPDATE ... FROM ... JOIN syntax, invalid on SQLite.
Neither can be driven end-to-end against a SQLite stand-in, so this file
covers only the pure-Python checks that run before either touches a cursor:
identifier validation, file/column/key/schema validation, and the
INFORMATION_SCHEMA introspection query itself (via a real ATTACHed
':memory:' database named INFORMATION_SCHEMA — not a stub, but genuinely
short of a working cursor or the SQL-Server-only statements).

The real load and update behavior — actual rows inserted/updated — is
covered end-to-end in tests/sqlserver/test_elt.py against a live SQL Server.
"""

from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import create_engine, text

from db.connection import register_engine
from db.elt import (
    _build_destination_columns,
    _check_column_mapping_matches_source,
    _drop_unmapped_source_columns,
    _validate_identifier,
    enrich,
    upload_parquet,
)
from db.errors import SQLServerConfigurationError, SQLServerQueryError


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


# ---- enrich ------------------------------------------------------------------

def _information_schema_engine():
    """Real SQLite engine with a genuine INFORMATION_SCHEMA.COLUMNS table
    (via ATTACH), seeded like SQL Server would report it. enrich() queries
    this table unmodified — nothing about the query itself is faked."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("ATTACH DATABASE ':memory:' AS INFORMATION_SCHEMA"))
        conn.execute(text("""
            CREATE TABLE INFORMATION_SCHEMA.COLUMNS (
                TABLE_SCHEMA TEXT, TABLE_NAME TEXT, COLUMN_NAME TEXT,
                DATA_TYPE TEXT, CHARACTER_MAXIMUM_LENGTH INTEGER,
                NUMERIC_PRECISION INTEGER, NUMERIC_SCALE INTEGER
            )
        """))
    return engine


def _seed_columns(engine, rows):
    with engine.begin() as conn:
        for row in rows:
            conn.execute(text("""
                INSERT INTO INFORMATION_SCHEMA.COLUMNS
                (TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE,
                 CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE)
                VALUES (:schema, :table, :col, :type, :maxlen, :prec, :scale)
            """), row)


@pytest.fixture()
def submission_engine():
    engine = _information_schema_engine()
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE submission (elt_data_key INTEGER, risk_score REAL, status TEXT)"
        ))
    _seed_columns(engine, [
        {"schema": "dbo", "table": "submission", "col": "elt_data_key", "type": "int",
         "maxlen": None, "prec": 10, "scale": 0},
        {"schema": "dbo", "table": "submission", "col": "risk_score", "type": "decimal",
         "maxlen": None, "prec": 18, "scale": 2},
        {"schema": "dbo", "table": "submission", "col": "status", "type": "varchar",
         "maxlen": 50, "prec": None, "scale": None},
    ])
    register_engine("ENRICH_TEST", engine)
    return engine


class TestEnrichValidation:
    def test_empty_dataframe_returns_zero_without_querying_db(self):
        df = pd.DataFrame({"elt_data_key": [], "risk_score": []})
        assert enrich(df, "submission", key_fields="elt_data_key", connection="ENRICH_TEST") == 0

    def test_missing_key_in_dataframe_raises(self, submission_engine):
        df = pd.DataFrame({"risk_score": [0.5]})
        with pytest.raises(SQLServerConfigurationError, match="not present in DataFrame"):
            enrich(df, "submission", key_fields="elt_data_key", connection="ENRICH_TEST")

    def test_nonexistent_target_table_raises(self, submission_engine):
        df = pd.DataFrame({"elt_data_key": [1], "risk_score": [0.5]})
        with pytest.raises(SQLServerQueryError, match="does not exist"):
            enrich(df, "no_such_table", key_fields="elt_data_key", connection="ENRICH_TEST")

    def test_key_not_in_target_table_raises(self, submission_engine):
        df = pd.DataFrame({"ghost_key": [1], "risk_score": [0.5]})
        with pytest.raises(SQLServerConfigurationError, match="not found in target table"):
            enrich(df, "submission", key_fields="ghost_key", connection="ENRICH_TEST")

    def test_no_matching_enrichment_columns_raises(self, submission_engine):
        df = pd.DataFrame({"elt_data_key": [1], "unrelated_field": ["x"]})
        with pytest.raises(SQLServerConfigurationError, match="No matching enrichment columns"):
            enrich(df, "submission", key_fields="elt_data_key", connection="ENRICH_TEST")

    def test_invalid_schema_name_raises(self, submission_engine):
        df = pd.DataFrame({"elt_data_key": [1], "risk_score": [0.5]})
        with pytest.raises(SQLServerQueryError, match="Invalid SQL"):
            enrich(df, "submission", key_fields="elt_data_key", schema="bad schema",
                   connection="ENRICH_TEST")


class TestEnrichColumnMapping:
    """column_mapping resolution happens entirely before the SQL-Server-only
    staging/UPDATE statements, so it's fully provable here: a mapped key or
    enrichment column that doesn't exist on the target table still raises the
    normal validation errors, using the *target* column name in the message."""

    def test_mapped_enrichment_column_not_on_target_raises(self, submission_engine):
        df = pd.DataFrame({"elt_data_key": [1], "src_score": [0.5]})
        with pytest.raises(SQLServerConfigurationError, match="No matching enrichment columns"):
            enrich(df, "submission", key_fields="elt_data_key",
                   column_mapping={"src_score": "no_such_column"}, connection="ENRICH_TEST")

    def test_mapped_key_not_on_target_raises(self, submission_engine):
        df = pd.DataFrame({"src_id": [1], "risk_score": [0.5]})
        with pytest.raises(SQLServerConfigurationError, match=r"\['no_such_key'\]"):
            enrich(df, "submission", key_fields="src_id",
                   column_mapping={"src_id": "no_such_key"}, connection="ENRICH_TEST")

    def test_mapped_key_resolves_against_dataframe_name(self, submission_engine):
        # key_fields names the DataFrame column; a missing DataFrame column
        # still raises "not present in DataFrame", even with column_mapping set.
        df = pd.DataFrame({"risk_score": [0.5]})
        with pytest.raises(SQLServerConfigurationError, match="not present in DataFrame"):
            enrich(df, "submission", key_fields="src_id",
                   column_mapping={"src_id": "elt_data_key"}, connection="ENRICH_TEST")

    def test_two_dataframe_columns_mapped_to_same_target_raises(self, submission_engine):
        df = pd.DataFrame({"elt_data_key": [1], "score_a": [0.1], "score_b": [0.2]})
        with pytest.raises(SQLServerConfigurationError, match="more than one DataFrame column"):
            enrich(df, "submission", key_fields="elt_data_key",
                   column_mapping={"score_a": "risk_score", "score_b": "risk_score"},
                   connection="ENRICH_TEST")
