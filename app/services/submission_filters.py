"""The submission filter predicates (spec 017, contracts/routes.md §6).

``submission_filter_clauses`` turns one ``filters`` dict into ANDed SQL
predicates plus their bound parameters. ``submission_service.list_submissions``
applies them to the master list; ``_library_where`` wraps them in an EXISTS so
the EDM and RDM libraries narrow by the same deals (FR-016). Kept apart from
``submission_service`` so the libraries import the clauses without importing the
deal service.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from app.services._common import _ENTITY_ASSOC, _in_clause, _parse_int, _word_and_clauses

# The one contract status a contract can be in force under (FR-018, P-09).
WON = "WON"


def _as_date(value: Any) -> Any:
    """Normalize a date-ish value to a ``date`` for binding.

    SQLite reads dates back as ISO strings; SQL Server as ``date``. Accept both
    (and ISO strings from HTML forms) so callers need not care."""
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value))


def _as_uuid(value: Any) -> str | None:
    """``value`` as a canonical lowercase UUID string, or ``None`` when it is not
    a UUID at all.

    Every id column is ``uniqueidentifier``: SQL Server refuses to compare a
    non-UUID string against one and raises a conversion error, so an id that
    arrives from outside (a hand-typed URL, a hidden form input) has to be turned
    into "not found" before it is bound into a query."""
    try:
        return str(uuid.UUID(str(value).strip()))
    except (AttributeError, TypeError, ValueError):
        return None


def submission_filter_clauses(
    filters: dict[str, Any], alias: str = "s",
) -> tuple[list[str], dict[str, Any]]:
    """The ANDed predicates for one set of list filters, and their bound
    parameters (contracts/routes.md §6). Values OR within a key and AND across
    keys; a missing or empty key is no filter. ``alias`` is the ``submission``
    row the predicates read, so the libraries can wrap them in an EXISTS over
    their own join. Every parameter name carries its key's prefix (research.md
    R4), so a caller's own ``:q`` or ``:status`` never collides.

    Submission-level keys (owner, name, cedant, treaty year, Modeling status,
    client) become clauses on ``alias``. Contract-level keys (``crm_ids``,
    ``treaty_type_codes``, ``inception_date``, ``contract_status_codes``,
    ``in_force_as_of``) share one ``EXISTS`` over ``contract`` so a single
    contract satisfies them together (P-18, research.md R4). ``name`` matches
    on words, every word required (see ``_word_and_clauses``); each ``crm_ids``
    value matches a whole CRM ID, case-insensitive and trimmed (P-10);
    ``in_force_as_of`` is a ``date`` and applies FR-018; the rest are exact."""
    s = alias
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if filters.get("owner_ids"):
        # An owner id that is not a UUID binds NULL, which matches no row — the
        # hand-typed-URL case ``_as_uuid`` exists for.
        clause, more = _in_clause(
            f"{s}.assigned_analyst_id",
            [_as_uuid(o) for o in filters["owner_ids"]], "owner")
        clauses.append(clause)
        params |= more
    if filters.get("name"):
        more_clauses, more = _word_and_clauses(filters["name"], (f"{s}.name",), "n")
        clauses += more_clauses
        params |= more
    if filters.get("cedant_ids"):
        clause, more = _in_clause(
            f"{s}.cedant_id", [_as_uuid(c) for c in filters["cedant_ids"]], "ced")
        clauses.append(clause)
        params |= more
    if filters.get("treaty_years"):
        clause, more = _in_clause(
            f"{s}.treaty_year", [int(year) for year in filters["treaty_years"]], "ty")
        clauses.append(clause)
        params |= more
    if filters.get("status_codes"):
        clause, more = _in_clause(f"{s}.status_code", filters["status_codes"], "ms")
        clauses.append(clause)
        params |= more
    if filters.get("client_ids"):
        # A NULL client never matches (P-04); a value that is not an integer
        # binds NULL and matches nothing.
        clause, more = _in_clause(
            f"{s}.client_id", [_parse_int(c) for c in filters["client_ids"]], "cl")
        clauses.append(clause)
        params |= more
    contract_clauses, more = _contract_clauses(filters)
    params |= more
    if contract_clauses:
        # One EXISTS, not a join: a deal with three matching contracts is still
        # one row, and every contract-level filter is met by the same contract.
        clauses.append(
            f"EXISTS (SELECT 1 FROM contract c WHERE c.submission_id = {s}.id AND "
            + " AND ".join(contract_clauses) + ")")
    return clauses, params


def _contract_clauses(filters: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """The contract-level predicates on alias ``c``, shared by the list's
    EXISTS and by the row summary so both name the same contracts (P-18)."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if filters.get("crm_ids"):
        clause, more = _in_clause(
            "LOWER(TRIM(c.crm_id))",
            [str(value).strip().lower() for value in filters["crm_ids"]], "crm")
        clauses.append(clause)
        params |= more
    if filters.get("treaty_type_codes"):
        clause, more = _in_clause("c.treaty_type_code", filters["treaty_type_codes"], "tt")
        clauses.append(clause)
        params |= more
    if filters.get("inception_date") is not None:
        clauses.append("c.inception_date = :inc")
        params["inc"] = _as_date(filters["inception_date"])
    if filters.get("contract_status_codes"):
        clause, more = _in_clause(
            "c.contract_status_code", filters["contract_status_codes"], "cs")
        clauses.append(clause)
        params |= more
    if filters.get("in_force_as_of") is not None:
        clauses.append(
            "c.contract_status_code = :won AND c.inception_date <= :asof "
            "AND c.expiration_date >= :asof")
        params["won"] = WON
        params["asof"] = _as_date(filters["in_force_as_of"])
    return clauses, params


def has_submission_filters(filters: dict[str, Any] | None) -> bool:
    """Whether any submission-attribute filter is set, so a library adds its
    EXISTS only then and unlinked EDMs and RDMs stay listed otherwise (FR-016)."""
    return bool(filters) and any(
        value is not None and value != [] and value != ""
        for value in filters.values())


def _library_where(
    kind: str, *, name: str | None, status: str | None, match_words: bool,
    unattached: bool, submission_filters: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """The WHERE clause and bound parameters of ``edm_service.list_edms`` and
    ``rdm_service.list_rdms`` over the ``kind`` entity table. ``name`` narrows
    by case-insensitive substring, or with ``match_words`` by every word of
    the term in any order; ``status`` narrows to the exact import status;
    ``unattached`` keeps entities linked to no submission; ``submission_filters``
    keeps an entity when one linked submission satisfies every filter together
    (spec 017 FR-016): one EXISTS ANDing every clause against one submission at
    a time. Blank filters are no-ops."""
    cfg = _ENTITY_ASSOC[kind]
    where = "WHERE deleted_at IS NULL"
    params: dict[str, Any] = {}
    if name and match_words:
        words, more = _word_and_clauses(name, ("name",), "q")
        where += "".join(f" AND {clause}" for clause in words)
        params |= more
    elif name:
        where += " AND name LIKE :q"
        params["q"] = f"%{name}%"
    if status:
        where += " AND status = :status"
        params["status"] = status
    if unattached:
        where += (f" AND NOT EXISTS (SELECT 1 FROM {cfg['assoc']} a "
                  f"WHERE a.{cfg['id_col']} = {cfg['table']}.id)")
    if has_submission_filters(submission_filters):
        clauses, sub_params = submission_filter_clauses(
            submission_filters, alias="s")
        where += (
            f" AND EXISTS (SELECT 1 FROM {cfg['assoc']} a JOIN submission s "
            f"ON s.id = a.submission_id WHERE a.{cfg['id_col']} = {cfg['table']}.id AND "
            + " AND ".join(clauses) + ")")
        params |= sub_params
    return where, params
