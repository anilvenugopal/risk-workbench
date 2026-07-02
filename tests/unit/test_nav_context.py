"""Unit tests for get_nav_context and nav package."""

from __future__ import annotations

from unittest.mock import MagicMock


def _user(role_codes):
    u = MagicMock()
    u.role_codes = role_codes
    return u


class TestGetNavContext:
    def test_home_active_section(self):
        from app.nav import get_nav_context
        ctx = get_nav_context(_user(["analyst"]), "home")
        assert ctx["active_key"] == "home"
        assert ctx["active_section"] == "home"

    def test_submissions_child_section_is_submissions(self):
        from app.nav import get_nav_context
        ctx = get_nav_context(_user(["analyst"]), "submissions.all")
        assert ctx["active_section"] == "submissions"
        assert ctx["active_key"] == "submissions.all"

    def test_rail_contains_home(self):
        from app.nav import get_nav_context
        ctx = get_nav_context(_user(["analyst"]), "home")
        rail_keys = [n["key"] for n in ctx["rail"]]
        assert "home" in rail_keys

    def test_analyst_rail_excludes_admin(self):
        from app.nav import get_nav_context
        ctx = get_nav_context(_user(["analyst"]), "home")
        rail_keys = [n["key"] for n in ctx["rail"]]
        assert "admin" not in rail_keys

    def test_admin_rail_includes_admin(self):
        from app.nav import get_nav_context
        ctx = get_nav_context(_user(["admin"]), "home")
        rail_keys = [n["key"] for n in ctx["rail"]]
        assert "admin" in rail_keys

    def test_rail_bottom_contains_account(self):
        from app.nav import get_nav_context
        ctx = get_nav_context(_user(["analyst"]), "home")
        bottom_keys = [n["key"] for n in ctx["rail_bottom"]]
        assert "account" in bottom_keys

    def test_home_has_no_sidebar(self):
        from app.nav import get_nav_context
        ctx = get_nav_context(_user(["analyst"]), "home")
        assert ctx["sidebar"] == []

    def test_submissions_has_sidebar(self):
        from app.nav import get_nav_context
        ctx = get_nav_context(_user(["analyst"]), "submissions.all")
        sidebar_keys = [n["key"] for n in ctx["sidebar"]]
        assert "submissions.all" in sidebar_keys
        assert "submissions.mine" in sidebar_keys

    def test_workflows_sidebar_has_5_items(self):
        from app.nav import get_nav_context
        ctx = get_nav_context(_user(["analyst"]), "workflows.active")
        assert len(ctx["sidebar"]) == 5

    def test_breadcrumb_for_child_has_two_items(self):
        from app.nav import get_nav_context
        ctx = get_nav_context(_user(["analyst"]), "submissions.all")
        keys = [c["key"] for c in ctx["breadcrumb"]]
        assert keys == ["submissions", "submissions.all"]

    def test_breadcrumb_for_root_has_one_item(self):
        from app.nav import get_nav_context
        ctx = get_nav_context(_user(["analyst"]), "home")
        assert len(ctx["breadcrumb"]) == 1

    def test_sidebar_title_set_for_section(self):
        from app.nav import get_nav_context
        ctx = get_nav_context(_user(["analyst"]), "submissions.all")
        assert ctx["sidebar_title"] is not None

    def test_no_roles_user_gets_reduced_rail(self):
        from app.nav import get_nav_context
        ctx = get_nav_context(_user([]), "home")
        rail_keys = [n["key"] for n in ctx["rail"]]
        assert "admin" not in rail_keys
        assert "home" in rail_keys
