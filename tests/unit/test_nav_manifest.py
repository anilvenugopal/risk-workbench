"""Unit tests for the navigation manifest helper functions."""

from __future__ import annotations

import pytest


class TestRailNodes:
    def test_returns_7_top_rail_nodes(self):
        from app.nav.manifest import rail_nodes
        top = rail_nodes(bottom=False)
        assert len(top) == 7

    def test_returns_1_bottom_rail_node(self):
        from app.nav.manifest import rail_nodes
        bottom = rail_nodes(bottom=True)
        assert len(bottom) == 1
        assert bottom[0]["key"] == "account"

    def test_all_top_nodes_have_rail_icon(self):
        from app.nav.manifest import rail_nodes
        for node in rail_nodes(bottom=False):
            assert node["rail_icon"] is not None, f"{node['key']} missing rail_icon"


class TestChildren:
    def test_submissions_has_children(self):
        from app.nav.manifest import children
        kids = children("submissions")
        assert len(kids) == 2
        keys = [k["key"] for k in kids]
        assert "submissions.all" in keys
        assert "submissions.mine" in keys

    def test_workflows_has_5_children(self):
        from app.nav.manifest import children
        kids = children("workflows")
        assert len(kids) == 5

    def test_workflows_rwb_jobs_present(self):
        from app.nav.manifest import children
        kids = children("workflows")
        keys = [k["key"] for k in kids]
        assert "workflows.rwb_jobs" in keys

    def test_home_has_no_children(self):
        from app.nav.manifest import children
        assert children("home") == []


class TestBreadcrumb:
    def test_home_breadcrumb(self):
        from app.nav.manifest import breadcrumb
        crumb = breadcrumb("home")
        assert len(crumb) == 1
        assert crumb[0]["key"] == "home"

    def test_submissions_all_breadcrumb(self):
        from app.nav.manifest import breadcrumb
        crumb = breadcrumb("submissions.all")
        keys = [c["key"] for c in crumb]
        assert keys == ["submissions", "submissions.all"]

    def test_workflows_rwb_jobs_breadcrumb(self):
        from app.nav.manifest import breadcrumb
        crumb = breadcrumb("workflows.rwb_jobs")
        keys = [c["key"] for c in crumb]
        assert keys == ["workflows", "workflows.rwb_jobs"]


class TestTopAncestor:
    def test_rail_node_is_own_ancestor(self):
        from app.nav.manifest import top_ancestor
        anc = top_ancestor("home")
        assert anc["key"] == "home"

    def test_child_node_ancestor(self):
        from app.nav.manifest import top_ancestor
        anc = top_ancestor("submissions.all")
        assert anc["key"] == "submissions"

    def test_workflows_child_ancestor(self):
        from app.nav.manifest import top_ancestor
        anc = top_ancestor("workflows.rwb_jobs")
        assert anc["key"] == "workflows"


class TestVisibleNodes:
    def test_analyst_cannot_see_admin(self):
        from app.nav.manifest import visible_nodes
        visible = visible_nodes(["analyst"])
        keys = [n["key"] for n in visible]
        assert "admin" not in keys

    def test_admin_can_see_admin(self):
        from app.nav.manifest import visible_nodes
        visible = visible_nodes(["admin"])
        keys = [n["key"] for n in visible]
        assert "admin" in keys

    def test_no_roles_cannot_see_admin(self):
        from app.nav.manifest import visible_nodes
        visible = visible_nodes([])
        keys = [n["key"] for n in visible]
        assert "admin" not in keys

    def test_all_authenticated_can_see_home(self):
        from app.nav.manifest import visible_nodes
        for roles in [["analyst"], ["admin"], []]:
            keys = [n["key"] for n in visible_nodes(roles)]
            assert "home" in keys
