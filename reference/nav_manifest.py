"""
nav_manifest.py — THE navigation manifest (mock of the real code manifest).

This single structure drives, with zero duplication:
  - the rail            (top-level nodes, in order)
  - the sidebar         (a section's children; first child is the default)
  - breadcrumbs         (walk `parent` upward from the current node)
  - active-state        (current route -> node -> top-level ancestor)
  - the search "Applications" group (nodes with searchable=True)

A page is added by adding ONE node here + a handler + a template. Rail
placement, sidebar entry, breadcrumb, active-state and search visibility are
all inherited. Detail pages (e.g. a specific submission) are NOT rail/sidebar
nodes — they declare a `parent` so the breadcrumb/active-state resolve to the
correct section even when reached from elsewhere (the context-switch rule).

In the real app this is a versioned code manifest; `roles` would gate RBAC.
"""

# Each node: key, label, parent, route, and optionally:
#   rail_icon  -> name of an SVG in static/icons (top-level nodes only)
#   searchable -> appears in global search "Applications" group
#   bottom     -> pinned to the bottom of the rail (the user/account node)
#   hidden     -> a real page but not shown in rail/sidebar (detail pages)
NODES = [
    # ---- Home (rail node with NO children -> no sidebar) ----
    {"key": "home", "label": "Home", "parent": None, "route": "/",
     "rail_icon": "home", "searchable": True},

    # ---- Submissions ----
    {"key": "submissions", "label": "Submissions", "parent": None,
     "route": "/submissions", "rail_icon": "submissions", "searchable": True,
     "sidebar_title": "Submissions",
     "actions": ["+ New submission", "+ Upload EDM", "+ Upload RDM"]},
    {"key": "submissions.all", "label": "All Submissions", "parent": "submissions", "route": "/submissions", "searchable": True},
    {"key": "submissions.mine", "label": "My Submissions", "parent": "submissions", "route": "/submissions?scope=mine"},
    {"key": "submissions.edms", "label": "EDMs", "parent": "submissions", "route": "/submissions/edms"},
    {"key": "submissions.rdms", "label": "RDMs", "parent": "submissions", "route": "/submissions/rdms"},
    {"key": "submissions.results", "label": "Results", "parent": "submissions", "route": "/submissions/results"},
    # detail page: lives UNDER All Submissions, hidden from nav
    {"key": "submissions.detail", "label": "Submission", "parent": "submissions.all",
     "route": "/submissions/{id}", "hidden": True},

    # ---- Workflows ----
    {"key": "workflows", "label": "Workflows", "parent": None,
     "route": "/workflows", "rail_icon": "workflows", "searchable": True,
     "sidebar_title": "Workflows", "actions": ["+ New workflow"]},
    {"key": "workflows.all", "label": "All Workflows", "parent": "workflows", "route": "/workflows", "searchable": True},
    {"key": "workflows.active", "label": "Active Workflows", "parent": "workflows", "route": "/workflows?scope=active"},
    {"key": "workflows.tasks", "label": "Tasks", "parent": "workflows", "route": "/workflows/tasks"},
    {"key": "workflows.review", "label": "Review", "parent": "workflows", "route": "/workflows/review"},
    {"key": "workflows.irpjobs", "label": "IRP Jobs", "parent": "workflows", "route": "/workflows/irp-jobs"},
    {"key": "workflows.exceptions", "label": "Exceptions", "parent": "workflows", "route": "/workflows/exceptions"},
    # detail page: lives UNDER All Workflows -> reached from a submission, still shows Workflows context
    {"key": "workflows.new", "label": "New workflow", "parent": "workflows.all",
     "route": "/workflows/new", "hidden": True},
    {"key": "workflows.detail", "label": "Workflow", "parent": "workflows.all",
     "route": "/workflows/{id}", "hidden": True},

    # ---- Results ----
    {"key": "results", "label": "Results", "parent": None,
     "route": "/results", "rail_icon": "results", "searchable": True,
     "sidebar_title": "Results", "actions": ["+ New report"]},
    {"key": "results.results", "label": "Results", "parent": "results", "route": "/results"},
    {"key": "results.reports", "label": "Reports", "parent": "results", "route": "/results/reports"},

    # ---- Templates ----
    {"key": "templates", "label": "Templates", "parent": None,
     "route": "/templates", "rail_icon": "templates", "searchable": True,
     "sidebar_title": "Templates", "actions": ["+ New template"]},
    {"key": "templates.templates", "label": "Templates", "parent": "templates", "route": "/templates", "searchable": True},
    {"key": "templates.parameters", "label": "Parameters", "parent": "templates", "route": "/templates/parameters"},
    {"key": "templates.reftables", "label": "Reference Tables", "parent": "templates", "route": "/templates/reference-tables"},

    # ---- Moody's IRP ----
    {"key": "irp", "label": "Moody's IRP", "parent": None,
     "route": "/irp", "rail_icon": "moodys", "searchable": True,
     "sidebar_title": "Moody's IRP", "actions": ["\u2193 Sync metadata"]},
    {"key": "irp.all", "label": "All IRP Jobs", "parent": "irp", "route": "/irp"},
    {"key": "irp.active", "label": "Active IRP Jobs", "parent": "irp", "route": "/irp/active"},
    {"key": "irp.treaties", "label": "Treaties", "parent": "irp", "route": "/irp/treaties"},

    # ---- Administration ----
    {"key": "admin", "label": "Administration", "parent": None,
     "route": "/admin", "rail_icon": "administration", "searchable": False,
     "sidebar_title": "Administration", "actions": ["+ New user"]},
    {"key": "admin.users", "label": "Users", "parent": "admin", "route": "/admin"},
    {"key": "admin.settings", "label": "Settings", "parent": "admin", "route": "/admin/settings"},

    # ---- Account (bottom of rail) ----
    {"key": "account", "label": "Account", "parent": None, "route": "/account",
     "rail_icon": "user", "bottom": True, "sidebar_title": "Account",
     "actions": []},
    {"key": "account.profile", "label": "Profile", "parent": "account", "route": "/account"},
    {"key": "account.signout", "label": "Sign out", "parent": "account", "route": "/signout"},
]

BY_KEY = {n["key"]: n for n in NODES}


def rail_nodes(bottom=False):
    return [n for n in NODES if n["parent"] is None and bool(n.get("bottom")) == bottom and n["key"] != "account" or
            (bottom and n["key"] == "account")] if False else \
        [n for n in NODES if n["parent"] is None and bool(n.get("bottom")) == bottom]


def top_ancestor(key):
    n = BY_KEY.get(key)
    while n and n["parent"] is not None:
        n = BY_KEY.get(n["parent"])
    return n


def children(key):
    return [n for n in NODES if n.get("parent") == key]


def breadcrumb(key):
    """Walk parent links from the node up to the root. Returns root-first list."""
    trail = []
    n = BY_KEY.get(key)
    while n is not None:
        trail.append(n)
        n = BY_KEY.get(n["parent"]) if n["parent"] else None
    return list(reversed(trail))


def default_child_key(section_key):
    kids = children(section_key)
    return kids[0]["key"] if kids else None


def searchable_nodes():
    return [n for n in NODES if n.get("searchable")]
