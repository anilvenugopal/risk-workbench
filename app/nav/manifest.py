"""Navigation manifest — single source of truth for all nav structure.

Adding a page = one entry in NODES + one route handler + one template.
Role gates, breadcrumbs, sidebar rendering, and search all derive from here.
"""

from __future__ import annotations

from typing import Any

# Each node: key, label, parent, route, plus optional fields.
# See specs/001-app-shell-nav-auth/contracts/nav-manifest.md for full schema.
NODES: list[dict[str, Any]] = [
    # ── Rail roots ──────────────────────────────────────────────────────────
    {
        "key": "home",
        "label": "Home",
        "parent": None,
        "route": "/",
        "rail_icon": "home",
        "sidebar_title": None,
        "searchable": True,
        "roles": [],
        "hidden": False,
        "bottom": False,
    },
    {
        "key": "submissions",
        "label": "Submissions",
        "parent": None,
        "route": "/submissions",
        "rail_icon": "submissions",
        "sidebar_title": "Submissions",
        "searchable": True,
        "roles": [],
        "hidden": False,
        "bottom": False,
    },
    {
        "key": "workflows",
        "label": "Workflows",
        "parent": None,
        "route": "/workflows",
        "rail_icon": "workflows",
        "sidebar_title": "Workflows",
        "searchable": True,
        "roles": [],
        "hidden": False,
        "bottom": False,
    },
    {
        "key": "results",
        "label": "Results",
        "parent": None,
        "route": "/results",
        "rail_icon": "results",
        "sidebar_title": "Results",
        "searchable": True,
        "roles": [],
        "hidden": False,
        "bottom": False,
    },
    {
        "key": "templates",
        "label": "Templates",
        "parent": None,
        "route": "/templates",
        "rail_icon": "templates",
        "sidebar_title": "Templates",
        "searchable": True,
        "roles": [],
        "hidden": False,
        "bottom": False,
    },
    {
        "key": "irp",
        "label": "Moody's IRP",
        "parent": None,
        "route": "/irp",
        "rail_icon": "moodys",
        "sidebar_title": "Moody's IRP",
        "searchable": True,
        "roles": [],
        "hidden": False,
        "bottom": False,
    },
    {
        "key": "admin",
        "label": "Administration",
        "parent": None,
        "route": "/admin/users",
        "rail_icon": "administration",
        "sidebar_title": "Administration",
        "searchable": False,
        "roles": ["admin"],
        "hidden": False,
        "bottom": False,
    },
    # ── Bottom rail ──────────────────────────────────────────────────────────
    {
        "key": "account",
        "label": "Account",
        "parent": None,
        "route": "/account",
        "rail_icon": "user",
        "sidebar_title": "Account",
        "searchable": False,
        "roles": [],
        "hidden": False,
        "bottom": True,
    },
    # ── Submissions sidebar ──────────────────────────────────────────────────
    {
        "key": "submissions.all",
        "label": "List",
        "parent": "submissions",
        "route": "/submissions",
        "rail_icon": None,
        "sidebar_title": None,
        "searchable": True,
        "roles": [],
        "hidden": False,
        "bottom": False,
    },
    {
        "key": "submissions.mine",
        "label": "My Submissions",
        "parent": "submissions",
        "route": "/submissions/mine",
        "rail_icon": None,
        "sidebar_title": None,
        "searchable": True,
        "roles": [],
        "hidden": False,
        "bottom": False,
    },
    # ── Workflows sidebar ────────────────────────────────────────────────────
    {
        "key": "workflows.active",
        "label": "Active",
        "parent": "workflows",
        "route": "/workflows/active",
        "rail_icon": None,
        "sidebar_title": None,
        "searchable": True,
        "roles": [],
        "hidden": False,
        "bottom": False,
    },
    {
        "key": "workflows.review",
        "label": "Review Queue",
        "parent": "workflows",
        "route": "/workflows/review",
        "rail_icon": None,
        "sidebar_title": None,
        "searchable": True,
        "roles": [],
        "hidden": False,
        "bottom": False,
    },
    {
        "key": "workflows.irp_jobs",
        "label": "IRP Jobs",
        "parent": "workflows",
        "route": "/workflows/irp-jobs",
        "rail_icon": None,
        "sidebar_title": None,
        "searchable": True,
        "roles": [],
        "hidden": False,
        "bottom": False,
    },
    {
        "key": "workflows.rwb_jobs",
        "label": "RWB Jobs",
        "parent": "workflows",
        "route": "/workflows/rwb-jobs",
        "rail_icon": None,
        "sidebar_title": None,
        "searchable": True,
        "roles": [],
        "hidden": False,
        "bottom": False,
    },
    {
        "key": "workflows.exceptions",
        "label": "Exceptions",
        "parent": "workflows",
        "route": "/workflows/exceptions",
        "rail_icon": None,
        "sidebar_title": None,
        "searchable": True,
        "roles": [],
        "hidden": False,
        "bottom": False,
    },
]

# Build index for O(1) lookup
_BY_KEY: dict[str, dict[str, Any]] = {n["key"]: n for n in NODES}


def rail_nodes(bottom: bool = False) -> list[dict[str, Any]]:
    """Return top-level nodes (parent=None) filtered by bottom flag."""
    return [n for n in NODES if n["parent"] is None and n["bottom"] == bottom]


def children(key: str) -> list[dict[str, Any]]:
    """Return all nodes whose parent == key, in NODES order."""
    return [n for n in NODES if n["parent"] == key]


def breadcrumb(key: str) -> list[dict[str, Any]]:
    """Return root-first ancestor chain including the node itself."""
    node = _BY_KEY.get(key)
    if node is None:
        return []
    chain = [node]
    current = node
    while current["parent"] is not None:
        current = _BY_KEY[current["parent"]]
        chain.insert(0, current)
    return chain


def top_ancestor(key: str) -> dict[str, Any] | None:
    """Return the rail root for this node (the ancestor with parent=None)."""
    node = _BY_KEY.get(key)
    if node is None:
        return None
    current = node
    while current["parent"] is not None:
        current = _BY_KEY[current["parent"]]
    return current


def default_child_key(section_key: str) -> str | None:
    """Return the key of the first child node, or None."""
    kids = children(section_key)
    return kids[0]["key"] if kids else None


def searchable_nodes() -> list[dict[str, Any]]:
    """Return all nodes where searchable=True."""
    return [n for n in NODES if n.get("searchable")]


def visible_nodes(user_roles: list[str]) -> list[dict[str, Any]]:
    """Return nodes where roles is empty OR user_roles intersects roles."""
    result = []
    for node in NODES:
        required = node.get("roles", [])
        if not required or any(r in user_roles for r in required):
            result.append(node)
    return result
