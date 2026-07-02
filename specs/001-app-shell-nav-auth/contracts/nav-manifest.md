# Contract: Navigation Manifest

**Date**: 2026-07-01  
**Feature**: [../spec.md](../spec.md)

The navigation manifest is the single source of truth for all navigation structure. It is a Python list of node dicts in `app/nav/manifest.py`.

---

## Node schema

Every node MUST have:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `key` | `str` | Yes | Unique identifier; dot-separated for hierarchy (`submissions.all`) |
| `label` | `str` | Yes | Display text in rail icon tooltip, sidebar, breadcrumb |
| `parent` | `str \| None` | Yes | `None` = rail root; dotted key of parent node |
| `route` | `str` | Yes | URL path; may contain `{id}` for detail pages |

Optional fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `rail_icon` | `str` | `None` | SVG name in `app/static/icons/`; top-level nodes only |
| `sidebar_title` | `str` | `label` | Section heading shown at top of sidebar |
| `searchable` | `bool` | `False` | Include in global nav search |
| `roles` | `list[str]` | `[]` | `role_kind.code` values; empty = all roles can see |
| `hidden` | `bool` | `False` | Real route but not rendered in rail/sidebar (detail pages) |
| `bottom` | `bool` | `False` | Pinned to bottom of rail (account node) |
| `actions` | `list[str]` | `[]` | Action button labels shown in sidebar header |

---

## Required helper functions

```python
def rail_nodes(bottom: bool = False) -> list[dict]
    """Return top-level nodes (parent=None) where bottom matches."""

def children(key: str) -> list[dict]
    """Return all nodes with parent == key, in NODES order."""

def breadcrumb(key: str) -> list[dict]
    """Return root-first list of ancestors including the node itself."""

def top_ancestor(key: str) -> dict | None
    """Return the rail root for this node (walk parent until parent=None)."""

def default_child_key(section_key: str) -> str | None
    """Return the key of the first child, or None."""

def searchable_nodes() -> list[dict]
    """Return all nodes where searchable=True."""

def visible_nodes(user_roles: list[str]) -> list[dict]
    """Return all nodes where roles is empty or user_roles intersects roles."""
```

---

## RBAC gate

When rendering rail or sidebar, call `visible_nodes(current_user.role_codes)`:

- A node with `roles: []` is visible to all authenticated users
- A node with `roles: ["admin"]` is visible only to users whose `role_kind.is_admin=True`
- The gate is checked at render time; no route-level enforcement is added in Iteration 0 (role gates on content pages come in later iterations)

---

## Iteration 0 nodes

Seven rail destinations (in order):

| key | label | rail_icon | has sidebar |
|-----|-------|-----------|-------------|
| `home` | Home | `home` | No (full-width main) |
| `submissions` | Submissions | `submissions` | Yes |
| `workflows` | Workflows | `workflows` | Yes |
| `results` | Results | `results` | Yes |
| `templates` | Templates | `templates` | Yes |
| `irp` | Moody's IRP | `moodys` | Yes |
| `admin` | Administration | `administration` | Yes; roles=["admin"] |

Sidebar children (stub in Iteration 0 — routes exist but pages are stubs):

**Submissions** (`parent=submissions`):

| key | label | route |
|-----|-------|-------|
| `submissions.all` | List | `/submissions` |
| `submissions.mine` | My Submissions | `/submissions/mine` |

**Workflows** (`parent=workflows`):

| key | label | route |
|-----|-------|-------|
| `workflows.active` | Active | `/workflows/active` |
| `workflows.review` | Review Queue | `/workflows/review` |
| `workflows.irp_jobs` | IRP Jobs | `/workflows/irp-jobs` |
| `workflows.rwb_jobs` | RWB Jobs | `/workflows/rwb-jobs` |
| `workflows.exceptions` | Exceptions | `/workflows/exceptions` |

**RWB Jobs** (`workflows.rwb_jobs`) is the queue-monitoring view for `rwb_job` rows — shows pending, running, succeeded, and failed jobs with their `work_type`, `origin`, `request_key`, and timestamps. It is a read-only audit/monitoring page in Iteration 0 (stub template); full filtering/detail is a later iteration.

**Templates**, **Results**, **Moody's IRP**, and **Administration** sidebar children are stubs defined when those features land. In Iteration 0 the sidebar renders with a single placeholder link for each.

Bottom rail:

| key | label | rail_icon |
|-----|-------|-----------|
| `account` | Account | `user` |
