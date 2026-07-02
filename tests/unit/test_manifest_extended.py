"""Unit tests for app.nav.manifest — default_child_key, searchable_nodes, visible_nodes."""

from __future__ import annotations


class TestDefaultChildKey:
    def test_returns_first_child_key_for_section_with_children(self):
        from app.nav.manifest import default_child_key
        result = default_child_key("submissions")
        assert result == "submissions.all"

    def test_returns_first_child_key_for_workflows(self):
        from app.nav.manifest import default_child_key
        result = default_child_key("workflows")
        assert result == "workflows.active"

    def test_returns_none_for_leaf_node(self):
        from app.nav.manifest import default_child_key
        assert default_child_key("home") is None

    def test_returns_none_for_nonexistent_key(self):
        from app.nav.manifest import default_child_key
        assert default_child_key("no_such_section") is None

    def test_returns_none_for_section_with_no_children(self):
        from app.nav.manifest import default_child_key
        assert default_child_key("results") is None


class TestSearchableNodes:
    def test_returns_only_searchable_nodes(self):
        from app.nav.manifest import searchable_nodes
        results = searchable_nodes()
        assert all(n.get("searchable") for n in results)

    def test_excludes_admin_node(self):
        from app.nav.manifest import searchable_nodes
        keys = [n["key"] for n in searchable_nodes()]
        assert "admin" not in keys

    def test_excludes_account_node(self):
        from app.nav.manifest import searchable_nodes
        keys = [n["key"] for n in searchable_nodes()]
        assert "account" not in keys

    def test_includes_home(self):
        from app.nav.manifest import searchable_nodes
        keys = [n["key"] for n in searchable_nodes()]
        assert "home" in keys

    def test_includes_submissions_children(self):
        from app.nav.manifest import searchable_nodes
        keys = [n["key"] for n in searchable_nodes()]
        assert "submissions.all" in keys
        assert "submissions.mine" in keys

    def test_includes_workflows_children(self):
        from app.nav.manifest import searchable_nodes
        keys = [n["key"] for n in searchable_nodes()]
        assert "workflows.active" in keys

    def test_returns_list(self):
        from app.nav.manifest import searchable_nodes
        assert isinstance(searchable_nodes(), list)


class TestVisibleNodes:
    def test_non_role_gated_nodes_visible_to_empty_roles(self):
        from app.nav.manifest import visible_nodes
        nodes = visible_nodes([])
        keys = [n["key"] for n in nodes]
        assert "home" in keys
        assert "submissions" in keys

    def test_admin_node_hidden_from_empty_roles(self):
        from app.nav.manifest import visible_nodes
        nodes = visible_nodes([])
        keys = [n["key"] for n in nodes]
        assert "admin" not in keys

    def test_admin_node_visible_to_admin_role(self):
        from app.nav.manifest import visible_nodes
        nodes = visible_nodes(["admin"])
        keys = [n["key"] for n in nodes]
        assert "admin" in keys

    def test_analyst_role_does_not_unlock_admin_node(self):
        from app.nav.manifest import visible_nodes
        nodes = visible_nodes(["analyst"])
        keys = [n["key"] for n in nodes]
        assert "admin" not in keys


class TestBreadcrumb:
    def test_unknown_key_returns_empty_list(self):
        from app.nav.manifest import breadcrumb
        assert breadcrumb("no.such.key") == []

    def test_root_node_returns_itself(self):
        from app.nav.manifest import breadcrumb
        chain = breadcrumb("home")
        assert len(chain) == 1
        assert chain[0]["key"] == "home"

    def test_child_returns_parent_then_self(self):
        from app.nav.manifest import breadcrumb
        chain = breadcrumb("submissions.all")
        keys = [n["key"] for n in chain]
        assert "submissions" in keys
        assert keys[-1] == "submissions.all"


class TestTopAncestor:
    def test_unknown_key_returns_none(self):
        from app.nav.manifest import top_ancestor
        assert top_ancestor("no.such.key") is None

    def test_root_node_returns_itself(self):
        from app.nav.manifest import top_ancestor
        result = top_ancestor("home")
        assert result["key"] == "home"

    def test_child_returns_rail_root(self):
        from app.nav.manifest import top_ancestor
        result = top_ancestor("submissions.all")
        assert result["key"] == "submissions"
