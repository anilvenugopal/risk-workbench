"""Unit tests for db.scope — apply_scope and scoped_execute."""

from __future__ import annotations


class TestApplyScope:
    def test_admin_bypass_returns_original(self):
        from db.scope import apply_scope
        sql = "SELECT * FROM submission"
        result_sql, result_params = apply_scope(sql, values=[], column="customer_id",
                                                params={}, is_admin=True)
        assert result_sql == sql
        assert result_params == {}

    def test_empty_values_returns_no_rows_query(self):
        from db.scope import apply_scope
        sql, params = apply_scope("SELECT * FROM submission", values=[],
                                  column="customer_id", is_admin=False)
        assert "1=0" in sql
        assert params == {}

    def test_single_value_adds_in_clause(self):
        from db.scope import apply_scope
        sql, params = apply_scope("SELECT * FROM submission", values=["cust-1"],
                                  column="customer_id", is_admin=False)
        assert "IN" in sql
        assert "customer_id" in sql
        assert "cust-1" in params.values()

    def test_multiple_values_all_bound(self):
        from db.scope import apply_scope
        vals = ["a", "b", "c"]
        sql, params = apply_scope("SELECT * FROM t", values=vals,
                                  column="col", is_admin=False)
        assert len([k for k in params if k.startswith("_scope_")]) == 3
        for v in vals:
            assert v in params.values()

    def test_original_params_preserved(self):
        from db.scope import apply_scope
        sql, params = apply_scope("SELECT * FROM t WHERE status = :s",
                                  values=["x"], column="col",
                                  params={"s": "open"}, is_admin=False)
        assert params["s"] == "open"

    def test_column_name_in_scoped_sql(self):
        from db.scope import apply_scope
        sql, _ = apply_scope("SELECT * FROM t", values=["v"],
                              column="owner_id", is_admin=False)
        assert "owner_id" in sql

    def test_wraps_original_as_subquery(self):
        from db.scope import apply_scope
        original = "SELECT id FROM submission"
        sql, _ = apply_scope(original, values=["x"], column="c", is_admin=False)
        assert original in sql

    def test_custom_param_prefix(self):
        from db.scope import apply_scope
        _, params = apply_scope("SELECT * FROM t", values=["v"],
                                column="c", is_admin=False, param_prefix="p_")
        assert any(k.startswith("p_") for k in params)

    def test_none_params_defaults_to_empty(self):
        from db.scope import apply_scope
        sql, params = apply_scope("SELECT * FROM t", values=["v"],
                                  column="c", params=None, is_admin=False)
        assert isinstance(params, dict)


class TestScopedExecute:
    def test_calls_execute_with_scoped_sql(self, monkeypatch):
        from db import scope

        captured = []
        monkeypatch.setattr(scope, "execute",
                            lambda sql, params, connection=None, database=None:
                            captured.append((sql, params)) or [])

        scope.scoped_execute("SELECT * FROM t", values=["v"], column="c",
                             is_admin=False, connection="WORKBENCH")
        assert len(captured) == 1
        assert "IN" in captured[0][0]

    def test_admin_bypass_passes_original_sql(self, monkeypatch):
        from db import scope

        captured = []
        monkeypatch.setattr(scope, "execute",
                            lambda sql, params, connection=None, database=None:
                            captured.append(sql) or [])

        scope.scoped_execute("SELECT * FROM t", values=[], column="c",
                             is_admin=True, connection="WORKBENCH")
        assert captured[0] == "SELECT * FROM t"

    def test_passes_connection_to_execute(self, monkeypatch):
        from db import scope

        captured = []
        monkeypatch.setattr(scope, "execute",
                            lambda sql, params, connection=None, database=None:
                            captured.append(connection) or [])

        scope.scoped_execute("SELECT 1", values=["x"], column="c",
                             is_admin=False, connection="WORKBENCH")
        assert captured[0] == "WORKBENCH"
