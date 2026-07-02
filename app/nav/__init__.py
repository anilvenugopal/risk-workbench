"""Navigation package — manifest + context builder."""

from .manifest import (
    NODES,
    rail_nodes,
    children,
    breadcrumb,
    top_ancestor,
    default_child_key,
    searchable_nodes,
    visible_nodes,
)


def get_nav_context(current_user: object, current_key: str) -> dict:
    """Build the nav context dict passed to every shell template.

    Returns rail, sidebar, breadcrumb, active_key, and active_section.
    """
    user_roles = getattr(current_user, "role_codes", [])
    visible = visible_nodes(user_roles)
    visible_keys = {n["key"] for n in visible}

    ancestor = top_ancestor(current_key)
    active_section_key = ancestor["key"] if ancestor else current_key

    rail = [n for n in rail_nodes(bottom=False) if n["key"] in visible_keys]
    rail_bottom = [n for n in rail_nodes(bottom=True) if n["key"] in visible_keys]
    sidebar = [n for n in children(active_section_key) if n["key"] in visible_keys]
    sidebar_title = None
    if ancestor:
        sidebar_title = ancestor.get("sidebar_title") or ancestor.get("label")

    crumb = breadcrumb(current_key)

    return {
        "rail": rail,
        "rail_bottom": rail_bottom,
        "sidebar": sidebar,
        "sidebar_title": sidebar_title,
        "breadcrumb": crumb,
        "active_key": current_key,
        "active_section": active_section_key,
    }


__all__ = [
    "NODES",
    "rail_nodes",
    "children",
    "breadcrumb",
    "top_ancestor",
    "default_child_key",
    "searchable_nodes",
    "visible_nodes",
    "get_nav_context",
]
