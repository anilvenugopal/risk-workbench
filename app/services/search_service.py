"""Global search (Ctrl/Cmd-J) — PRD §19. One provider per searchable type, each
returning a capped, independent result group; a miss in one provider never
drops the others.

Every provider does a case-insensitive substring match (``LIKE``) and caps its
own result count (``GROUP_LIMIT``) — start simple, move to Full-Text indexes if
volume ever demands it (§19). No row-level scoping: every authenticated analyst
searches every deal (Article 6).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.nav import searchable_nodes, top_ancestor
from app.services import edm_service, rdm_service, submission_service
from db import execute

MIN_TERM = 2
GROUP_LIMIT = 5


@dataclass
class SearchItem:
    label: str
    href: str
    meta: str | None = None
    rail_icon: str | None = None


@dataclass
class SearchGroup:
    type: str
    title: str
    items: list[SearchItem]


def _escape_like(value: str) -> str:
    out = value.replace("\\", "\\\\")
    return out.replace("%", "\\%").replace("_", "\\_")


def _pages(term: str, user_roles: list[str]) -> SearchGroup | None:
    needle = term.lower()
    matches = [
        n for n in searchable_nodes()
        if needle in n["label"].lower()
        and (not n.get("roles") or any(r in user_roles for r in n["roles"]))
    ]
    if not matches:
        return None
    items = []
    for node in matches[:GROUP_LIMIT]:
        ancestor = top_ancestor(node["key"])
        items.append(SearchItem(
            label=node["label"], href=node["route"], meta="Page",
            rail_icon=ancestor["rail_icon"] if ancestor else None,
        ))
    return SearchGroup(type="pages", title="Pages", items=items)


def _submissions(term: str) -> SearchGroup | None:
    rows = submission_service.search_submissions_global(term, limit=GROUP_LIMIT)
    if not rows:
        return None
    items = [
        SearchItem(label=row.name, href=f"/submissions/{row.id}",
                   meta=row.cedant_name)
        for row in rows
    ]
    return SearchGroup(type="submissions", title="Submissions", items=items)


def _edms(term: str) -> SearchGroup | None:
    rows = edm_service.list_edms(name=term)[:GROUP_LIMIT]
    if not rows:
        return None
    items = [SearchItem(label=row.name, href=f"/edms/{row.id}", meta="EDM")
             for row in rows]
    return SearchGroup(type="edms", title="EDMs", items=items)


def _rdms(term: str) -> SearchGroup | None:
    rows = rdm_service.list_rdms(name=term)[:GROUP_LIMIT]
    if not rows:
        return None
    items = [SearchItem(label=row.name, href=f"/rdms/{row.id}", meta="RDM")
             for row in rows]
    return SearchGroup(type="rdms", title="RDMs", items=items)


def _templates(term: str) -> SearchGroup | None:
    rows = execute(
        "SELECT id, name FROM analysis_template "
        "WHERE deleted_at IS NULL AND name LIKE :q ESCAPE '\\' "
        "ORDER BY name",
        {"q": f"%{_escape_like(term)}%"}, connection="WORKBENCH",
    )[:GROUP_LIMIT]
    if not rows:
        return None
    items = [
        SearchItem(label=row["name"],
                   href=f"/templates/analysis-templates/{row['id']}",
                   meta="Analysis template")
        for row in rows
    ]
    return SearchGroup(type="templates", title="Analysis Templates", items=items)


def _users(term: str) -> SearchGroup | None:
    rows = execute(
        "SELECT id, display_name, email FROM app_user "
        "WHERE is_active = 1 AND (display_name LIKE :q ESCAPE '\\' "
        "OR email LIKE :q ESCAPE '\\') "
        "ORDER BY display_name",
        {"q": f"%{_escape_like(term)}%"}, connection="WORKBENCH",
    )[:GROUP_LIMIT]
    if not rows:
        return None
    items = [
        SearchItem(label=row["display_name"], href=f"/admin/users/{row['id']}",
                   meta=row["email"])
        for row in rows
    ]
    return SearchGroup(type="users", title="Users", items=items)


_PROVIDERS = {
    "pages": lambda term, roles: _pages(term, roles),
    "submissions": lambda term, roles: _submissions(term),
    "edms": lambda term, roles: _edms(term),
    "rdms": lambda term, roles: _rdms(term),
    "templates": lambda term, roles: _templates(term),
    "users": lambda term, roles: _users(term),
}

# One (type, title) pair per provider, in fan-out order — the search modal's
# filter pill row (§19 extension) renders exactly this list, so a pill and a
# provider can never drift apart.
PROVIDER_TYPES: tuple[tuple[str, str], ...] = (
    ("pages", "Pages"), ("submissions", "Submissions"), ("edms", "EDMs"),
    ("rdms", "RDMs"), ("templates", "Analysis Templates"), ("users", "Users"),
)


def global_search(
    term: str, *, user_roles: list[str], type: str | None = None,
) -> list[SearchGroup]:
    """Fan out ``term`` across every provider; return only non-empty groups.

    Below ``MIN_TERM`` characters every provider would return its whole table
    through a bare ``%%`` substring match, so search does not run at all — the
    same floor ``cedant_suggestions``/``search_submissions_for_link`` use.

    ``type`` narrows the fan-out to one provider (the search modal's filter
    pill row) — an unknown key runs no provider rather than falling back to
    every one, so a stale pill value fails quiet, not wide."""
    trimmed = (term or "").strip()
    if len(trimmed) < MIN_TERM:
        return []
    keys = [type] if type else list(_PROVIDERS)
    groups = []
    for key in keys:
        provider = _PROVIDERS.get(key)
        if provider is None:
            continue
        group = provider(trimmed, user_roles)
        if group is not None:
            groups.append(group)
    return groups
