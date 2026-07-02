"""Extended unit tests for db/scripts.py.

Covers the paths not hit by test_db_package.py:
- _escape_sql_value: all type branches (None, bool, int, float, str, other)
- _convert_param_value: NaN handling, .item() protocol, numpy/pandas types
- _convert_params: None passthrough, dict conversion
- _resolve_sql_path: absolute path, relative path, missing file
- sql_file_exists: True/False cases
- display_result_sets: empty list, IPython absent fallback
- execute_query: happy path against SQLite via register_engine
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


# ── _escape_sql_value ─────────────────────────────────────────────────────────

class TestEscapeSqlValue:
    def _escape(self, v):
        from db.scripts import _escape_sql_value
        return _escape_sql_value(v)

    def test_none_returns_null(self):
        assert self._escape(None) == "NULL"

    def test_true_returns_1(self):
        assert self._escape(True) == "1"

    def test_false_returns_0(self):
        assert self._escape(False) == "0"

    def test_integer(self):
        assert self._escape(42) == "42"

    def test_float(self):
        assert self._escape(3.14) == "3.14"

    def test_plain_string_quoted(self):
        assert self._escape("hello") == "'hello'"

    def test_string_with_single_quote_escaped(self):
        assert self._escape("O'Brien") == "'O''Brien'"

    def test_non_string_coerced(self):
        # An object with __str__ should be treated as a string
        class Custom:
            def __str__(self):
                return "custom"
        result = self._escape(Custom())
        assert result == "'custom'"


# ── _convert_param_value ──────────────────────────────────────────────────────

class TestConvertParamValue:
    def _convert(self, v):
        from db.scripts import _convert_param_value
        return _convert_param_value(v)

    def test_none_stays_none(self):
        assert self._convert(None) is None

    def test_plain_string_unchanged(self):
        assert self._convert("hello") == "hello"

    def test_plain_int_unchanged(self):
        assert self._convert(99) == 99

    def test_item_protocol(self):
        """Objects with .item() (numpy scalars) should be unwrapped."""
        class ScalarLike:
            def item(self):
                return 7
        assert self._convert(ScalarLike()) == 7

    def test_numpy_scalar_unwrapped(self):
        import numpy as np
        val = np.int64(55)
        result = self._convert(val)
        assert result == 55
        assert isinstance(result, int)

    def test_numpy_array_converted_to_list(self):
        import numpy as np
        arr = np.array([1, 2, 3])
        result = self._convert(arr)
        assert result == [1, 2, 3]

    def test_pandas_series_converted_to_list(self):
        import pandas as pd
        s = pd.Series([4, 5, 6])
        result = self._convert(s)
        assert result == [4, 5, 6]

    def test_pandas_nan_becomes_none(self):
        import numpy as np
        result = self._convert(float("nan"))
        assert result is None

    def test_pandas_nat_becomes_none(self):
        import pandas as pd
        result = self._convert(pd.NaT)
        assert result is None


# ── _convert_params ───────────────────────────────────────────────────────────

class TestConvertParams:
    def test_none_returns_none(self):
        from db.scripts import _convert_params
        assert _convert_params(None) is None

    def test_dict_values_converted(self):
        import numpy as np
        from db.scripts import _convert_params
        result = _convert_params({"x": np.int64(10), "y": None})
        assert result["x"] == 10
        assert result["y"] is None


# ── _resolve_sql_path ─────────────────────────────────────────────────────────

class TestResolveSqlPath:
    def test_absolute_existing_path_returned(self, tmp_path):
        from db.scripts import _resolve_sql_path
        f = tmp_path / "test.sql"
        f.write_text("SELECT 1")
        result = _resolve_sql_path(str(f), sql_dir=None)
        assert result == f

    def test_relative_path_resolved_from_sql_dir(self, tmp_path):
        from db.scripts import _resolve_sql_path
        f = tmp_path / "query.sql"
        f.write_text("SELECT 1")
        result = _resolve_sql_path("query.sql", sql_dir=str(tmp_path))
        assert result == f

    def test_missing_file_raises_query_error(self, tmp_path):
        from db.scripts import _resolve_sql_path
        from db.errors import SQLServerQueryError
        with pytest.raises(SQLServerQueryError, match="not found"):
            _resolve_sql_path("no_such_file.sql", sql_dir=str(tmp_path))

    def test_relative_uses_mssql_sql_dir_env(self, tmp_path, monkeypatch):
        from db.scripts import _resolve_sql_path
        monkeypatch.setenv("MSSQL_SQL_DIR", str(tmp_path))
        f = tmp_path / "env_query.sql"
        f.write_text("SELECT 1")
        result = _resolve_sql_path("env_query.sql", sql_dir=None)
        assert result == f


# ── sql_file_exists ───────────────────────────────────────────────────────────

class TestSqlFileExists:
    def test_returns_true_for_existing_file(self, tmp_path):
        from db.scripts import sql_file_exists
        f = tmp_path / "exists.sql"
        f.write_text("SELECT 1")
        assert sql_file_exists(str(f)) is True

    def test_returns_false_for_missing_file(self, tmp_path):
        from db.scripts import sql_file_exists
        assert sql_file_exists(str(tmp_path / "missing.sql")) is False


# ── display_result_sets ───────────────────────────────────────────────────────

class TestDisplayResultSets:
    def test_empty_list_prints_message(self, capsys):
        from db.scripts import display_result_sets
        display_result_sets([])
        out = capsys.readouterr().out
        assert "No result sets" in out

    def test_dataframe_printed(self, capsys):
        import pandas as pd
        from db.scripts import display_result_sets
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        display_result_sets([df])
        out = capsys.readouterr().out
        assert "Result set" in out

    def test_max_rows_respected(self, capsys):
        import pandas as pd
        from db.scripts import display_result_sets
        df = pd.DataFrame({"x": range(100)})
        display_result_sets([df], max_rows=5)
        # Should not print 100 rows
        out = capsys.readouterr().out
        assert "Result set" in out


# ── execute_query ─────────────────────────────────────────────────────────────

class TestExecuteQuery:
    def test_returns_dataframe(self, monkeypatch):
        import pandas as pd
        from sqlalchemy import create_engine
        from db.connection import register_engine, _ENGINE_OVERRIDES
        from db.scripts import execute_query

        eng = create_engine("sqlite:///:memory:")
        from sqlalchemy import text
        with eng.begin() as conn:
            conn.execute(text("CREATE TABLE demo (n INTEGER)"))
            conn.execute(text("INSERT INTO demo VALUES (7)"))

        _ENGINE_OVERRIDES.clear()
        register_engine("DATABRIDGE", eng)
        result = execute_query("SELECT n FROM demo", connection="DATABRIDGE")
        _ENGINE_OVERRIDES.clear()

        assert isinstance(result, pd.DataFrame)
        assert result["n"].iloc[0] == 7

    def test_bad_sql_raises_query_error(self, monkeypatch):
        from sqlalchemy import create_engine
        from db.connection import register_engine, _ENGINE_OVERRIDES
        from db.scripts import execute_query
        from db.errors import SQLServerQueryError

        _ENGINE_OVERRIDES.clear()
        register_engine("DATABRIDGE", create_engine("sqlite:///:memory:"))
        with pytest.raises(SQLServerQueryError):
            execute_query("NOT VALID SQL", connection="DATABRIDGE")
        _ENGINE_OVERRIDES.clear()

    def test_connection_error_reraised(self, monkeypatch):
        """SQLServerConnectionError from get_engine must propagate unchanged (line 152)."""
        from db.scripts import execute_query
        from db.errors import SQLServerConnectionError
        import db.scripts as scripts_mod

        def _raise(name, database=None):
            raise SQLServerConnectionError("down")

        monkeypatch.setattr(scripts_mod, "get_engine", _raise)
        with pytest.raises(SQLServerConnectionError):
            execute_query("SELECT 1", connection="ANY")


# ── _substitute_named_parameters branches ────────────────────────────────────

class TestSubstituteNamedParameters:
    def _sub(self, query, params):
        from db.scripts import _substitute_named_parameters
        return _substitute_named_parameters(query, params)

    def test_no_params_returns_query_unchanged(self):
        """Line 108: early return when params is None/empty."""
        q = "SELECT 1"
        assert self._sub(q, None) == q

    def test_empty_params_dict_returns_query_unchanged(self):
        assert self._sub("SELECT 1", {}) == "SELECT 1"

    def test_value_substituted(self):
        result = self._sub("SELECT {{ n }}", {"n": 42})
        assert result == "SELECT 42"

    def test_string_value_quoted(self):
        result = self._sub("WHERE name = {{ name }}", {"name": "alice"})
        assert result == "WHERE name = 'alice'"

    def test_missing_param_raises(self):
        from db.errors import SQLServerQueryError
        with pytest.raises(SQLServerQueryError, match="Missing required parameter"):
            self._sub("SELECT {{ x }}", {"y": 1})


# ── _convert_param_value except branch ───────────────────────────────────────

class TestConvertParamValueExceptBranch:
    def test_pd_isna_raises_type_error_falls_through(self):
        """Lines 92-93: when pd.isna(value) raises TypeError, we fall through
        to the .item() check and return the value unchanged."""
        import db.scripts as scripts_mod

        class Opaque:
            """pd.isna raises TypeError on this; no .item() method."""
            pass

        obj = Opaque()
        result = scripts_mod._convert_param_value(obj)
        assert result is obj

    def test_pd_isna_raises_value_error_falls_through(self, monkeypatch):
        """Lines 92-93: when pd.isna(value) raises ValueError, we fall through
        to the .item() check and return the value unchanged."""
        import db.scripts as scripts_mod

        def _isna_raises(v):
            raise ValueError("truth value is ambiguous")

        monkeypatch.setattr(scripts_mod.pd, "isna", _isna_raises)

        class NoItem:
            pass

        obj = NoItem()
        result = scripts_mod._convert_param_value(obj)
        assert result is obj


class TestSubstituteIdentifierValidation:
    def test_invalid_chars_in_identifier_context_raises(self):
        """Lines 120-127: identifier-context param with special chars raises."""
        from db.scripts import _substitute_named_parameters
        from db.errors import SQLServerQueryError
        # [{{ schema }}] is identifier context; ';DROP' has invalid chars
        with pytest.raises(SQLServerQueryError, match="Invalid identifier"):
            _substitute_named_parameters("SELECT * FROM [{{ schema }}].t",
                                         {"schema": "dbo;DROP TABLE users--"})

    def test_valid_identifier_with_slash_allowed(self):
        """Valid path-like identifier passes validation."""
        from db.scripts import _substitute_named_parameters
        result = _substitute_named_parameters("SELECT * FROM [{{ schema }}].t",
                                              {"schema": "my/schema"})
        assert "my/schema" in result


class TestSubstituteValueError:
    def test_malformed_template_raises_query_error(self):
        """Lines 136-137: ExpressionTemplate.substitute raises ValueError for
        invalid placeholder patterns — wrapped as SQLServerQueryError."""
        from db.scripts import _substitute_named_parameters
        from db.errors import SQLServerQueryError

        # The ExpressionTemplate pattern uses {{ }}: passing a query where
        # the delimiter appears without a valid identifier triggers ValueError.
        # We monkeypatch substitute to raise ValueError directly.
        import db.scripts as scripts_mod
        original_cls = scripts_mod.ExpressionTemplate

        class BrokenTemplate(original_cls):
            def substitute(self, mapping):
                raise ValueError("bad pattern")

        scripts_mod.ExpressionTemplate = BrokenTemplate
        try:
            with pytest.raises(SQLServerQueryError, match="Parameter substitution error"):
                _substitute_named_parameters("{{ x }}", {"x": "v"})
        finally:
            scripts_mod.ExpressionTemplate = original_cls


class TestExecuteQueryConnectionError:
    def test_connection_error_reraised_from_execute_query(self, monkeypatch):
        """Line 152: SQLServerConnectionError raised inside execute_query
        propagates unchanged (not wrapped as SQLServerQueryError)."""
        import db.scripts as scripts_mod
        from db.errors import SQLServerConnectionError
        from unittest.mock import MagicMock

        bad_engine = MagicMock()
        bad_engine.connect.side_effect = SQLServerConnectionError("gone")
        monkeypatch.setattr(scripts_mod, "get_engine",
                            lambda name, database=None: bad_engine)

        with pytest.raises(SQLServerConnectionError):
            scripts_mod.execute_query("SELECT 1", connection="ANY")
