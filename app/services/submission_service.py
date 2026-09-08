"""Submission service — the data-access spine for the deal domain (Iteration 1).

Functions (not classes), matching ``auth_service``. Every read goes through the
``db`` safe bound-parameter path; the two writes that must be atomic (create, and
each status transition) open ``get_connection("WORKBENCH")`` + an explicit
``conn.begin()`` and insert the event **and** stamp the cached column in one
transaction (Article 4 / R2).

Portability contract (unit tier = SQLite via ``register_engine``; integration
tier = SQL Server):
  - UUID PKs are generated app-side (``uuid4()``) and bound as ``str`` (R11) — no
    ``NEWID()`` on the hot path, and ids are known immediately for redirects.
  - Timestamps are app-supplied (``_utcnow()``) and bound as native ``datetime``;
    the ``updated_at`` optimistic-concurrency marker (R1) is bound **verbatim** in
    the ``WHERE`` so whatever type the caller read back round-trips unchanged.
  - No ``GETUTCDATE()``/``STRING_AGG``/``TOP`` in service SQL — those are not
    portable to SQLite. The migration keeps server defaults as a fallback only.
    A capped read appends ``db.row_limit(n)``, which emits the dialect's own
    clause, rather than spelling ``TOP``/``LIMIT`` here.

No row-level security anywhere: ``assigned_analyst_id`` is a plain predicate, never
a scope wrapper (Article 6 / R7).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

from sqlalchemy import text

from app.services._common import _rm_ui_root, _uid, _utcnow
from app.services.errors import (
    ConcurrencyConflict,
    SelfLinkError,
    SubmissionClosed,
    UnknownLinkError,
)
from db import (
    execute,
    execute_command,
    execute_one,
    execute_scalar,
    get_connection,
    row_limit,
)

ACTIVE = "ACTIVE"

# Rows per master-list request. The list is read newest-inception-first, so a
# page is what an analyst scans before narrowing; it also caps how many ids
# `_attach_crm_ids` binds, which SQL Server limits to 2,100 per statement.
PAGE_SIZE = 50

ENTITY_TABLE_SORTS = ("name", "status", "count")
ENTITY_TABLE_DEFAULT_SORT = "name"
ENTITY_TABLE_SORT_STARTS_DESCENDING = {
    "name": False,
    "status": False,
    "count": True,
}


# ── Result / row DTOs (contracts/data-access.md) ─────────────────────────────

@dataclass
class SubmissionRow:
    """One master-list / look-alike row. ``crm_ids`` is filled for the master list
    only (see ``_attach_crm_ids``); every other reader leaves it empty."""
    id: str
    name: str
    cedant_name: str
    treaty_type_code: str
    treaty_type_label: str | None
    inception_date: Any
    treaty_year: int | None
    status_code: str
    status_label: str | None
    assigned_analyst_id: str
    assigned_analyst_name: str | None
    updated_at: Any
    crm_ids: list[str] = field(default_factory=list)


@dataclass
class SubmissionPage:
    """One master-list page. ``has_next`` comes from reading one row past the page
    rather than a ``COUNT(*)``, which would scan everything the page cap avoids."""
    rows: list[SubmissionRow]
    page: int
    has_next: bool


@dataclass
class Submission:
    """Full detail view of a deal (cached status included)."""
    id: str
    name: str
    cedant_name: str
    treaty_type_code: str
    treaty_type_label: str | None
    inception_date: Any
    treaty_year: int | None
    links_to_submission_id: str | None
    directory_path: str | None
    status_code: str
    status_label: str | None
    assigned_analyst_id: str
    assigned_analyst_name: str | None
    inserted_at: Any
    updated_at: Any


@dataclass
class StatusEvent:
    id: str
    status_code: str
    status_label: str | None
    reason: str | None
    at: Any
    inserted_by: str | None
    inserted_by_name: str | None


@dataclass
class CrmTag:
    id: str
    submission_id: str
    crm_id: str
    inserted_at: Any


@dataclass
class CreateResult:
    created: bool
    submission_id: str | None = None
    warnings: list[SubmissionRow] = field(default_factory=list)


@dataclass
class UpdateResult:
    updated: bool
    warnings: list[SubmissionRow] = field(default_factory=list)


@dataclass(frozen=True)
class SubmissionEdm:
    id: str
    name: str
    status: str | None
    portfolio_count: int
    rm_url: str | None
    notes: str | None = None


@dataclass(frozen=True)
class SubmissionRdm:
    id: str
    name: str
    status: str | None
    analysis_count: int
    rm_url: str | None
    notes: str | None = None


@dataclass(frozen=True)
class EntityCandidate:
    id: str
    name: str
    status: str | None


@dataclass(frozen=True)
class CandidatePage:
    rows: list[EntityCandidate]
    page: int
    has_next: bool


@dataclass(frozen=True)
class AttachResult:
    attached_ids: list[str]
    stale_ids: list[str]


# ── Internal helpers ─────────────────────────────────────────────────────────

def _as_date(value: Any) -> Any:
    """Normalize a date-ish value to a ``date`` for binding.

    SQLite reads dates back as ISO strings; SQL Server as ``date``. Accept both
    (and ISO strings from HTML forms) so callers need not care."""
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value))


def _escape_like(value: str) -> str:
    """Neutralize LIKE wildcards in analyst input, so searching "A_B" matches a
    literal underscore rather than any character. Pair with ``ESCAPE '\\'`` on the
    predicate. Only ``%``, ``_`` and the escape character itself are handled —
    those are the three both SQL Server and SQLite agree on."""
    out = value.replace("\\", "\\\\")
    return out.replace("%", "\\%").replace("_", "\\_")


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


def _resolve_link_target(links_to: Any) -> str | None:
    """The id to write to ``submission.links_to_submission_id``, normalized to
    canonical lowercase, or ``None``. Raises ``UnknownLinkError`` when the value
    names no submission."""
    if links_to is None or not str(links_to).strip():
        return None
    target = _as_uuid(links_to)
    if target is None:
        raise UnknownLinkError("That linked submission was not found.")
    found = execute_scalar(
        "SELECT id FROM submission WHERE id = :id",
        {"id": target}, connection="WORKBENCH",
    )
    if found is None:
        raise UnknownLinkError("That linked submission was not found.")
    return target


def _default_treaty_year(treaty_year: int | None, inception_date: Any) -> int | None:
    """Fall back to the inception year when the analyst left treaty year blank
    (CR5). An entered year always wins (design note 08, D4)."""
    if treaty_year is not None:
        return treaty_year
    parsed = _as_date(inception_date)
    return parsed.year if parsed is not None else None


def _require_active(status_code: str | None) -> None:
    """Read-only gate (R3/FR-015): only ACTIVE submissions accept mutations."""
    if status_code != ACTIVE:
        raise SubmissionClosed(
            f"Submission is {status_code or 'missing'}; only ACTIVE deals are editable."
        )


def _load_status(submission_id: Any) -> str | None:
    return execute_scalar(
        "SELECT status_code FROM submission WHERE id = :id",
        {"id": str(submission_id)}, connection="WORKBENCH",
    )


_ROW_SELECT = """
    SELECT s.id, s.name, s.cedant_name, s.treaty_type_code,
           tk.label AS treaty_type_label,
           s.inception_date, s.treaty_year,
           s.status_code, sk.label AS status_label,
           s.assigned_analyst_id, u.display_name AS assigned_analyst_name,
           s.updated_at
    FROM submission s
    LEFT JOIN treaty_type_kind tk ON tk.code = s.treaty_type_code
    LEFT JOIN submission_status_kind sk ON sk.code = s.status_code
    LEFT JOIN app_user u ON u.id = s.assigned_analyst_id
"""


def _to_row(row: dict) -> SubmissionRow:
    return SubmissionRow(
        id=_uid(row["id"]),
        name=row["name"],
        cedant_name=row["cedant_name"],
        treaty_type_code=row["treaty_type_code"],
        treaty_type_label=row.get("treaty_type_label"),
        inception_date=row["inception_date"],
        treaty_year=row["treaty_year"],
        status_code=row["status_code"],
        status_label=row.get("status_label"),
        assigned_analyst_id=_uid(row["assigned_analyst_id"]),
        assigned_analyst_name=row.get("assigned_analyst_name"),
        updated_at=row["updated_at"],
    )


def _word_and_clauses(
    term: str, columns: tuple[str, ...], prefix: str,
) -> tuple[list[str], dict[str, Any]]:
    """One clause per whitespace-separated word in ``term``: the word has to appear in
    at least one of ``columns``, and every word has to match.

    Terms AND-combine (CR2): "american family" must not return every deal carrying
    "American". Substring per word rather than a similarity score — tolerant enough
    to find "American Family Mutual" from "american fam", and no fuzzier than that.

    ``prefix`` namespaces the bound parameters so two searched fields in one query
    cannot collide on ``:t0``."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for index, word in enumerate(term.split()):
        key = f"{prefix}{index}"
        match = " OR ".join(f"{col} LIKE :{key} ESCAPE '\\'" for col in columns)
        clauses.append(f"({match})")
        params[key] = f"%{_escape_like(word)}%"
    return clauses, params


def _submission_rows(
    clauses: list[str], params: dict[str, Any], *, exclude_id: Any = None,
    limit: int | None = None, offset: int = 0,
    order_by: str = "s.inception_date DESC, s.name",
) -> list[SubmissionRow]:
    """Run the shared row query: the master list, the look-alike check and the "links
    to" typeahead all select the same columns, and differ only in their predicates.

    ``order_by`` is interpolated SQL, never a bound value: pass ``SORT_COLUMNS``
    text, never a query-string value.

    ``exclude_id`` drops one submission from the results — the deal being renamed, or
    the one being edited so it cannot be offered as its own link. A value that is not
    a UUID excludes nothing rather than reaching the ``uniqueidentifier`` comparison
    (see ``_as_uuid``)."""
    excluded = _as_uuid(exclude_id) if exclude_id is not None else None
    if excluded is not None:
        clauses = [*clauses, "s.id <> :exclude"]
        params = {**params, "exclude": excluded}
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = _ROW_SELECT + where + f" ORDER BY {order_by}"
    if limit is not None:
        sql += " " + row_limit(limit, offset=offset)
    return [_to_row(row) for row in execute(sql, params, connection="WORKBENCH")]


def _in_clause(
    column: str, values: list[Any], prefix: str,
) -> tuple[str, dict[str, Any]]:
    """An ``IN (...)`` predicate over ``values``, one bound parameter each.
    ``prefix`` namespaces them so two filters in one query cannot collide."""
    params = {f"{prefix}{index}": value for index, value in enumerate(values)}
    placeholders = ", ".join(f":{key}" for key in params)
    return f"{column} IN ({placeholders})", params


def _attach_crm_ids(rows: list[SubmissionRow]) -> None:
    """Set each row's ``crm_ids``, oldest tag first, for the master list's CRM column
    (CR3). One query covers the page; a deal with no tags keeps the default ``[]``.

    One bound parameter per row, so the caller has to hand this a page rather than a
    whole table — SQL Server rejects a statement carrying more than 2,100."""
    ids = list(dict.fromkeys(str(row.id).lower() for row in rows))
    if not ids:
        return
    params = {f"s{i}": sid for i, sid in enumerate(ids)}
    placeholders = ", ".join(f":{key}" for key in params)
    tags = execute(
        "SELECT submission_id, crm_id FROM submission_crm_id "
        f"WHERE submission_id IN ({placeholders}) ORDER BY inserted_at, id",
        params, connection="WORKBENCH",
    )
    by_submission: dict[str, list[str]] = {}
    for tag in tags:
        by_submission.setdefault(
            str(tag["submission_id"]).lower(), []).append(tag["crm_id"])
    for row in rows:
        row.crm_ids = by_submission.get(str(row.id).lower(), [])


# ── Create / read / list ─────────────────────────────────────────────────────

def create_submission(
    *, name: str, cedant_name: str, treaty_type_code: str, inception_date: Any,
    treaty_year: int | None = None, links_to_submission_id: Any = None,
    directory_path: str | None = None, crm_ids: list[str] | None = None,
    actor_id: Any, confirmed: bool = False,
) -> CreateResult:
    """Create an ACTIVE submission owned by ``actor_id``.

    Runs the non-blocking duplicate check first: unconfirmed look-alikes short-
    circuit with ``created=False`` and warnings, writing nothing (FR-004). On the
    write path the submission row and its initial ACTIVE status event commit in
    one transaction (R2).

    ``treaty_year`` left as ``None`` is filled from the inception year (CR5).

    Each entry in ``crm_ids`` goes through ``add_crm_id`` after the submission
    commits, so create-time tags follow the same blank and repeat rules as tags
    added later on the detail page. Blank entries are skipped rather than
    rejected: the create form submits one comma-separated field, so "CRM-1,"
    is a stray comma, not a mistake worth a message.

    ``links_to_submission_id`` is checked before the duplicate check, so an id
    naming no deal is refused without first showing a look-alike warning."""
    link_target = _resolve_link_target(links_to_submission_id)
    matches = find_similar(
        name=name, cedant_name=cedant_name, treaty_type_code=treaty_type_code,
        inception_date=inception_date,
    )
    if matches and not confirmed:
        return CreateResult(created=False, warnings=matches)

    sid = str(uuid.uuid4())
    now = _utcnow()
    actor = str(actor_id)
    parsed_inception = _as_date(inception_date)
    params = {
        "id": sid,
        "owner": actor,
        "name": name,
        "cedant": cedant_name,
        "tt": treaty_type_code,
        "inc": parsed_inception,
        "ty": _default_treaty_year(treaty_year, parsed_inception),
        "lt": link_target,
        "dir": directory_path,
        "now": now,
        "actor": actor,
    }
    with get_connection("WORKBENCH") as conn, conn.begin():
        conn.execute(text(
            """
            INSERT INTO submission
                (id, assigned_analyst_id, name, cedant_name, treaty_type_code,
                 inception_date, treaty_year, links_to_submission_id,
                 directory_path, status_code, inserted_at, updated_at,
                 inserted_by, updated_by)
            VALUES
                (:id, :owner, :name, :cedant, :tt, :inc, :ty, :lt, :dir,
                 'ACTIVE', :now, :now, :actor, :actor)
            """
        ), params)
        conn.execute(text(
            """
            INSERT INTO submission_status_event
                (id, submission_id, status_code, reason, at, inserted_by)
            VALUES (:eid, :sid, 'ACTIVE', NULL, :now, :actor)
            """
        ), {"eid": str(uuid.uuid4()), "sid": sid, "now": now, "actor": actor})
    for crm_id in crm_ids or []:
        if crm_id.strip():
            add_crm_id(submission_id=sid, crm_id=crm_id, actor_id=actor)
    return CreateResult(created=True, submission_id=sid)


def get_submission(submission_id: Any) -> Submission | None:
    """Full detail incl. cached status_code. No access restriction (FR-019). An
    id that is not a UUID is "not found", not a query (see ``_as_uuid``)."""
    sid = _as_uuid(submission_id)
    if sid is None:
        return None
    row = execute_one(
        """
        SELECT s.id, s.name, s.cedant_name, s.treaty_type_code,
               tk.label AS treaty_type_label,
               s.inception_date, s.treaty_year, s.links_to_submission_id,
               s.directory_path, s.status_code, sk.label AS status_label,
               s.assigned_analyst_id, u.display_name AS assigned_analyst_name,
               s.inserted_at, s.updated_at
        FROM submission s
        LEFT JOIN treaty_type_kind tk ON tk.code = s.treaty_type_code
        LEFT JOIN submission_status_kind sk ON sk.code = s.status_code
        LEFT JOIN app_user u ON u.id = s.assigned_analyst_id
        WHERE s.id = :id
        """,
        {"id": sid}, connection="WORKBENCH",
    )
    if row is None:
        return None
    return Submission(
        id=_uid(row["id"]),
        name=row["name"],
        cedant_name=row["cedant_name"],
        treaty_type_code=row["treaty_type_code"],
        treaty_type_label=row.get("treaty_type_label"),
        inception_date=row["inception_date"],
        treaty_year=row["treaty_year"],
        links_to_submission_id=_uid(row["links_to_submission_id"]),
        directory_path=row["directory_path"],
        status_code=row["status_code"],
        status_label=row.get("status_label"),
        assigned_analyst_id=_uid(row["assigned_analyst_id"]),
        assigned_analyst_name=row.get("assigned_analyst_name"),
        inserted_at=row["inserted_at"],
        updated_at=row["updated_at"],
    )


def _risk_modeler_url(name: str, *, kind: str) -> str | None:
    root = _rm_ui_root()
    if root is None:
        return None
    if kind == "edm":
        return f"{root}/riskmodeler/datasources/{quote(str(name), safe='')}/portfolios"
    return f"{root}/riskmodeler/analyses?sourceRdmName={quote(str(name), safe='')}"


def _entity_table_order(
    sort: str, descending: bool, *, entity_alias: str, count_alias: str,
) -> str:
    columns = {
        "name": f"{entity_alias}.name",
        "status": f"{entity_alias}.status",
        "count": count_alias,
    }
    column = columns.get(sort, columns[ENTITY_TABLE_DEFAULT_SORT])
    direction = "DESC" if descending else "ASC"
    if column == f"{entity_alias}.name":
        return f"{column} {direction}, {entity_alias}.id ASC"
    return f"{column} {direction}, {entity_alias}.name ASC, {entity_alias}.id ASC"


def _list_submission_entities(
    submission_id: Any, *, kind: str, sort: str, descending: bool,
    entity_id: Any | None,
) -> list[SubmissionEdm] | list[SubmissionRdm]:
    edm = kind == "edm"
    entity_table = "irp_edm" if edm else "irp_rdm"
    association_table = "submission_edm" if edm else "submission_rdm"
    entity_column = "edm_id" if edm else "rdm_id"
    child_table = "irp_portfolio" if edm else "irp_analysis"
    count_alias = "portfolio_count" if edm else "analysis_count"
    dto = SubmissionEdm if edm else SubmissionRdm
    order_by = _entity_table_order(
        sort, descending, entity_alias="e", count_alias=count_alias)
    params: dict[str, Any] = {"id": str(submission_id)}
    entity_filter = ""
    if entity_id is not None:
        entity_filter = " AND e.id = :entity_id"
        params["entity_id"] = str(entity_id)
    rows = execute(
        f"SELECT e.id, e.name, e.status, e.notes, COUNT(c.id) AS {count_alias} "
        f"FROM {association_table} a JOIN {entity_table} e ON e.id = a.{entity_column} "
        f"LEFT JOIN {child_table} c ON c.{entity_column} = e.id AND c.deleted_at IS NULL "
        "WHERE a.submission_id = :id AND e.deleted_at IS NULL" + entity_filter + " "
        "GROUP BY e.id, e.name, e.status, e.notes, e.inserted_at "
        f"ORDER BY {order_by}",
        params, connection="WORKBENCH",
    )
    return [
        dto(
            id=_uid(row["id"]), name=row["name"], status=row["status"],
            rm_url=_risk_modeler_url(row["name"], kind=kind),
            notes=row["notes"],
            **{count_alias: int(row[count_alias] or 0)},
        )
        for row in rows
    ]


def list_submission_edms(
    submission_id: Any, *, sort: str = ENTITY_TABLE_DEFAULT_SORT,
    descending: bool = False, entity_id: Any | None = None,
) -> list[SubmissionEdm]:
    return _list_submission_entities(
        submission_id, kind="edm", sort=sort, descending=descending,
        entity_id=entity_id)


def list_submission_rdms(
    submission_id: Any, *, sort: str = ENTITY_TABLE_DEFAULT_SORT,
    descending: bool = False, entity_id: Any | None = None,
) -> list[SubmissionRdm]:
    return _list_submission_entities(
        submission_id, kind="rdm", sort=sort, descending=descending,
        entity_id=entity_id)


def _list_entity_candidates(
    *, submission_id: Any, query: str, page: int, kind: str,
) -> CandidatePage:
    page = max(1, page)
    entity_table = "irp_edm" if kind == "edm" else "irp_rdm"
    association_table = "submission_edm" if kind == "edm" else "submission_rdm"
    entity_column = "edm_id" if kind == "edm" else "rdm_id"
    params: dict[str, Any] = {"submission_id": str(submission_id)}
    where = (
        "e.deleted_at IS NULL AND NOT EXISTS ("
        f"SELECT 1 FROM {association_table} a "
        f"WHERE a.submission_id = :submission_id AND a.{entity_column} = e.id)"
    )
    cleaned_query = query.strip()
    if cleaned_query:
        where += " AND LOWER(e.name) LIKE :query ESCAPE '\\'"
        params["query"] = f"%{_escape_like(cleaned_query.lower())}%"
    sql = (
        f"SELECT e.id, e.name, e.status FROM {entity_table} e "
        f"WHERE {where} ORDER BY e.name, e.id "
        + row_limit(PAGE_SIZE + 1, offset=(page - 1) * PAGE_SIZE)
    )
    rows = execute(sql, params, connection="WORKBENCH")
    has_next = len(rows) > PAGE_SIZE
    return CandidatePage(
        rows=[EntityCandidate(id=_uid(row["id"]), name=row["name"],
                              status=row["status"])
              for row in rows[:PAGE_SIZE]],
        page=page,
        has_next=has_next,
    )


def list_edm_candidates(
    submission_id: Any, *, query: str = "", page: int = 1,
) -> CandidatePage:
    return _list_entity_candidates(
        submission_id=submission_id, query=query, page=page, kind="edm")


def list_rdm_candidates(
    submission_id: Any, *, query: str = "", page: int = 1,
) -> CandidatePage:
    return _list_entity_candidates(
        submission_id=submission_id, query=query, page=page, kind="rdm")


def _attach_entities(
    *, submission_id: Any, entity_ids: list[Any], actor_id: Any, kind: str,
) -> AttachResult:
    sid = str(submission_id)
    entity_table = "irp_edm" if kind == "edm" else "irp_rdm"
    association_table = "submission_edm" if kind == "edm" else "submission_rdm"
    entity_column = "edm_id" if kind == "edm" else "rdm_id"
    normalized: list[str] = []
    stale: list[str] = []
    seen: set[str] = set()
    for value in entity_ids:
        raw_value = str(value).strip()
        if raw_value in seen:
            continue
        seen.add(raw_value)
        entity_id = _as_uuid(value)
        if entity_id is None:
            stale.append(raw_value)
        elif entity_id not in normalized:
            normalized.append(entity_id)

    attached: list[str] = []
    with get_connection("WORKBENCH") as conn, conn.begin():
        status = conn.execute(
            text("SELECT status_code FROM submission WHERE id = :id"),
            {"id": sid},
        ).scalar()
        _require_active(status)
        for entity_id in normalized:
            eligible = conn.execute(text(
                f"SELECT e.id FROM {entity_table} e "
                "WHERE e.id = :entity_id AND e.deleted_at IS NULL "
                "AND NOT EXISTS ("
                f"SELECT 1 FROM {association_table} a "
                "WHERE a.submission_id = :submission_id "
                f"AND a.{entity_column} = e.id)"
            ), {"entity_id": entity_id, "submission_id": sid}).first()
            if eligible is None:
                stale.append(entity_id)
                continue
            conn.execute(text(
                f"INSERT INTO {association_table} "
                f"(submission_id, {entity_column}, inserted_at, inserted_by) "
                "VALUES (:submission_id, :entity_id, :now, :actor)"
            ), {"submission_id": sid, "entity_id": entity_id,
                "now": _utcnow(), "actor": str(actor_id)})
            attached.append(entity_id)
    return AttachResult(attached_ids=attached, stale_ids=stale)


def attach_edms(
    *, submission_id: Any, edm_ids: list[Any], actor_id: Any,
) -> AttachResult:
    return _attach_entities(
        submission_id=submission_id, entity_ids=edm_ids,
        actor_id=actor_id, kind="edm")


def attach_rdms(
    *, submission_id: Any, rdm_ids: list[Any], actor_id: Any,
) -> AttachResult:
    return _attach_entities(
        submission_id=submission_id, entity_ids=rdm_ids,
        actor_id=actor_id, kind="rdm")


def _detach_entity(
    *, submission_id: Any, entity_id: Any, kind: str,
) -> bool:
    sid = str(submission_id)
    association_table = "submission_edm" if kind == "edm" else "submission_rdm"
    entity_column = "edm_id" if kind == "edm" else "rdm_id"
    normalized_entity_id = _as_uuid(entity_id)
    with get_connection("WORKBENCH") as conn, conn.begin():
        status = conn.execute(
            text("SELECT status_code FROM submission WHERE id = :id"),
            {"id": sid},
        ).scalar()
        _require_active(status)
        if normalized_entity_id is None:
            return False
        result = conn.execute(text(
            f"DELETE FROM {association_table} "
            f"WHERE submission_id = :submission_id AND {entity_column} = :entity_id"
        ), {"submission_id": sid, "entity_id": normalized_entity_id})
    return result.rowcount > 0


def detach_edm(*, submission_id: Any, edm_id: Any) -> bool:
    return _detach_entity(
        submission_id=submission_id, entity_id=edm_id, kind="edm")


def detach_rdm(*, submission_id: Any, rdm_id: Any) -> bool:
    return _detach_entity(
        submission_id=submission_id, entity_id=rdm_id, kind="rdm")


# The columns the list header can sort on (D15). The request carries the key; the
# column text is looked up here and never taken from the query string. CRM ID does
# not sort — a deal carries several.
SORT_COLUMNS = {
    "name": "s.name",
    "cedant": "s.cedant_name",
    "inception": "s.inception_date",
    "year": "s.treaty_year",
}
DEFAULT_SORT = "inception"
# The direction a column starts in when the analyst first clicks it.
SORT_STARTS_DESCENDING = {"name": False, "cedant": False,
                          "inception": True, "year": True}


def _order_by(sort: str, descending: bool) -> str:
    """Name and id follow the sorted column so a page boundary falls in the same
    place every request when the sorted column ties."""
    column = SORT_COLUMNS[sort]
    tiebreakers = [c for c in ("s.name", "s.id") if c != column]
    return ", ".join([f"{column} {'DESC' if descending else 'ASC'}", *tiebreakers])


def list_submissions(
    *, owner_ids: list[Any] | None = None,
    name: str | None = None,
    cedant_name: str | None = None, crm_id: str | None = None,
    treaty_type_codes: list[str] | None = None, inception_date: Any = None,
    treaty_years: list[int] | None = None, status_codes: list[str] | None = None,
    page: int = 1, sort: str = DEFAULT_SORT, descending: bool = True,
) -> SubmissionPage:
    """One page of the master list. Filters AND-combine as bound predicates
    (FR-021). Every deal is visible to every analyst regardless of owner
    (Article 6) — ``owner_ids`` is a plain predicate, never an access gate (R7).

    The list filters OR within themselves and AND against the others (D16). An
    empty list turns that filter off: ``owner_ids=[]`` lists every owner's deals.

    ``name`` (CR1) and ``cedant_name`` match on words, every word required — see
    ``_word_and_clauses``. ``crm_id`` matches a substring of any CRM tag the deal
    carries (CR3). Owner, treaty type, inception date, treaty year and status are
    exact.

    ``page`` is 1-based; anything lower is page 1, so a hand-typed ``?page=0``
    reads the first page rather than a negative offset. ``sort`` is a key of
    ``SORT_COLUMNS``.

    No minimum term length: every read is capped at ``PAGE_SIZE``, so a
    one-character search costs no more than the page it narrows."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if owner_ids:
        # An owner id that is not a UUID binds NULL, which matches no row — the
        # hand-typed-URL case ``_as_uuid`` exists for.
        clause, owner_params = _in_clause(
            "s.assigned_analyst_id", [_as_uuid(o) for o in owner_ids], "owner")
        clauses.append(clause)
        params |= owner_params
    if name:
        name_clauses, name_params = _word_and_clauses(name, ("s.name",), "n")
        clauses += name_clauses
        params |= name_params
    if cedant_name:
        cedant_clauses, cedant_params = _word_and_clauses(
            cedant_name, ("s.cedant_name",), "c")
        clauses += cedant_clauses
        params |= cedant_params
    if crm_id:
        # EXISTS, not a join: a deal carrying three matching tags is still one row.
        clauses.append(
            "EXISTS (SELECT 1 FROM submission_crm_id c "
            "WHERE c.submission_id = s.id AND c.crm_id LIKE :crm ESCAPE '\\')")
        params["crm"] = f"%{_escape_like(crm_id.strip())}%"
    if treaty_type_codes:
        clause, treaty_type_params = _in_clause(
            "s.treaty_type_code", treaty_type_codes, "tt")
        clauses.append(clause)
        params |= treaty_type_params
    if inception_date is not None:
        clauses.append("s.inception_date = :inc")
        params["inc"] = _as_date(inception_date)
    if treaty_years:
        clause, treaty_year_params = _in_clause(
            "s.treaty_year", [int(year) for year in treaty_years], "ty")
        clauses.append(clause)
        params |= treaty_year_params
    if status_codes:
        clause, status_params = _in_clause("s.status_code", status_codes, "status")
        clauses.append(clause)
        params |= status_params
    page = max(1, int(page or 1))
    # One row past the page: its presence is what "there is a next page" means,
    # without a COUNT(*) over the same predicates.
    rows = _submission_rows(clauses, params, limit=PAGE_SIZE + 1,
                            offset=(page - 1) * PAGE_SIZE,
                            order_by=_order_by(sort, descending))
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    _attach_crm_ids(rows)
    return SubmissionPage(rows=rows, page=page, has_next=has_next)


def status_kinds() -> list[tuple[str, str]]:
    """Every submission status as (code, label) in display order, for the list's status
    filter. Read from the kind table (Article 4) rather than a literal, so the "Hold"
    status CIC asked for on 8/5 reaches the filter when its row is seeded."""
    rows = execute(
        "SELECT code, label FROM submission_status_kind ORDER BY sort_order, code",
        {}, connection="WORKBENCH",
    )
    return [(row["code"], row["label"]) for row in rows]


def find_similar(
    *, name: str, cedant_name: str, treaty_type_code: str, inception_date: Any,
    exclude_id: Any = None,
) -> list[SubmissionRow]:
    """Look-alikes: same ``name`` OR same (cedant + treaty_type + inception)
    (FR-004/R4). ``exclude_id`` skips the row being renamed. Never raises."""
    return _submission_rows(
        ["""(s.name = :name
             OR (s.cedant_name = :cedant AND s.treaty_type_code = :tt
                 AND s.inception_date = :inc))"""],
        {
            "name": name,
            "cedant": cedant_name,
            "tt": treaty_type_code,
            "inc": _as_date(inception_date),
        },
        exclude_id=exclude_id,
    )


# Both typeahead searches ignore a term this short. `%a%` matches most of the
# submission table, and a leading wildcard cannot seek ix_submission_cedant_name,
# so a one-character term buys a scan of every submission for a menu the analyst
# has not narrowed enough to read. The form applies the same minimum client-side
# so the request is not sent at all.
MIN_SUGGEST_TERM = 2


def cedant_suggestions(term: str, limit: int = 10) -> list[str]:
    """The first ``limit`` DISTINCT cedant names containing ``term`` (FR-006/R6).
    No cedant table.

    Contains, not prefix (CR7): typing "fam" has to find "American Family
    Mutual", which a ``LIKE 'fam%'`` match never returns."""
    trimmed = (term or "").strip()
    if len(trimmed) < MIN_SUGGEST_TERM:
        return []
    rows = execute(
        "SELECT DISTINCT cedant_name FROM submission "
        "WHERE cedant_name LIKE :term ESCAPE '\\' ORDER BY cedant_name "
        + row_limit(limit),
        {"term": f"%{_escape_like(trimmed)}%"}, connection="WORKBENCH",
    )
    return [row["cedant_name"] for row in rows]


def search_submissions_for_link(
    term: str, *, exclude_id: Any = None, limit: int = 10,
) -> list[SubmissionRow]:
    """Submissions matching every whitespace-separated term in ``term``, each
    matched against name or cedant (``_word_and_clauses``). Backs the "links to"
    picker (CR8).

    ``exclude_id`` drops the submission being edited so it cannot be offered as its
    own link — ``update_submission`` still raises ``SelfLinkError`` as the real
    check."""
    trimmed = (term or "").strip()
    if len(trimmed) < MIN_SUGGEST_TERM:
        return []
    clauses, params = _word_and_clauses(trimmed, ("s.name", "s.cedant_name"), "t")
    return _submission_rows(clauses, params, exclude_id=exclude_id, limit=limit)


def search_submissions_global(term: str, *, limit: int = 10) -> list[SubmissionRow]:
    """Submissions matching ``term`` as a single substring of name, cedant, or
    any tagged CRM id. Backs the Ctrl/Cmd-J submissions provider (PRD §19) —
    unlike ``search_submissions_for_link``, a single substring rather than
    AND-across-words, since global search has no "every word must match"
    expectation and needs to reach the CRM tag table the link picker does not."""
    trimmed = (term or "").strip()
    if len(trimmed) < MIN_SUGGEST_TERM:
        return []
    like = f"%{_escape_like(trimmed)}%"
    clauses = [
        "(s.name LIKE :q ESCAPE '\\' OR s.cedant_name LIKE :q ESCAPE '\\' "
        "OR EXISTS (SELECT 1 FROM submission_crm_id c "
        "WHERE c.submission_id = s.id AND c.crm_id LIKE :q ESCAPE '\\'))"
    ]
    return _submission_rows(clauses, {"q": like}, limit=limit)


# ── Edit / reassign (gated + concurrency-checked) ────────────────────────────

_MUTABLE_FIELDS = (
    "name", "cedant_name", "treaty_type_code", "inception_date",
    "treaty_year", "links_to_submission_id", "directory_path",
)


def update_submission(
    *, submission_id: Any, expected_updated_at: Any, actor_id: Any,
    confirmed: bool = False, **fields: Any,
) -> UpdateResult:
    """Edit mutable fields, gated by R3 (ACTIVE) + R1 (concurrency) + R9
    (self-link, and a ``links_to_submission_id`` naming no submission) + R4
    (non-blocking duplicate warning on rename).

    ``treaty_year`` is refilled from the inception year whenever the merged value
    is None (CR5) — the column does not record "no treaty year"."""
    sid = str(submission_id)
    current = execute_one(
        "SELECT status_code, name, cedant_name, treaty_type_code, inception_date, "
        "treaty_year, links_to_submission_id, directory_path "
        "FROM submission WHERE id = :id",
        {"id": sid}, connection="WORKBENCH",
    )
    if current is None:
        raise LookupError(f"submission {sid} not found")
    _require_active(current["status_code"])

    merged = {f: current[f] for f in _MUTABLE_FIELDS}
    for f in _MUTABLE_FIELDS:
        if f in fields:
            merged[f] = fields[f]
    merged["inception_date"] = _as_date(merged["inception_date"])
    merged["treaty_year"] = _default_treaty_year(
        merged["treaty_year"], merged["inception_date"]
    )

    # Resolve first, self-link second: a submission's own id always exists, so
    # linking to itself must report SelfLinkError, not "not found".
    links_to = _resolve_link_target(merged["links_to_submission_id"])
    if links_to is not None and links_to == _uid(sid):
        raise SelfLinkError("A submission cannot link to itself.")

    matches = find_similar(
        name=merged["name"], cedant_name=merged["cedant_name"],
        treaty_type_code=merged["treaty_type_code"],
        inception_date=merged["inception_date"], exclude_id=sid,
    )
    if matches and not confirmed:
        return UpdateResult(updated=False, warnings=matches)

    rows_affected = execute_command(
        """
        UPDATE submission
        SET name = :name, cedant_name = :cedant, treaty_type_code = :tt,
            inception_date = :inc, treaty_year = :ty,
            links_to_submission_id = :lt, directory_path = :dir,
            updated_at = :now, updated_by = :actor
        WHERE id = :id AND updated_at = :expected
        """,
        {
            "name": merged["name"],
            "cedant": merged["cedant_name"],
            "tt": merged["treaty_type_code"],
            "inc": merged["inception_date"],
            "ty": merged["treaty_year"],
            "lt": links_to,
            "dir": merged["directory_path"],
            "now": _utcnow(),
            "actor": str(actor_id),
            "id": sid,
            "expected": expected_updated_at,
        },
        connection="WORKBENCH",
    )
    if rows_affected == 0:
        raise ConcurrencyConflict(
            "This deal changed since you opened it — reload and re-apply."
        )
    return UpdateResult(updated=True)


def reassign_owner(
    *, submission_id: Any, new_owner_id: Any, expected_updated_at: Any,
    actor_id: Any,
) -> None:
    """Any analyst may reassign (FR-005a). Gated by R3 (ACTIVE) + R1 (concurrency).
    Moves My-view membership only; never visibility (SC-011)."""
    sid = str(submission_id)
    status = _load_status(sid)
    if status is None:
        raise LookupError(f"submission {sid} not found")
    _require_active(status)
    rows_affected = execute_command(
        """
        UPDATE submission
        SET assigned_analyst_id = :new, updated_at = :now, updated_by = :actor
        WHERE id = :id AND updated_at = :expected
        """,
        {
            "new": str(new_owner_id), "now": _utcnow(), "actor": str(actor_id),
            "id": sid, "expected": expected_updated_at,
        },
        connection="WORKBENCH",
    )
    if rows_affected == 0:
        raise ConcurrencyConflict(
            "This deal changed since you opened it — reload and re-apply."
        )


# ── Status lifecycle (event-sourced) ─────────────────────────────────────────

def set_status(
    *, submission_id: Any, to_status: str, reason: str | None,
    expected_updated_at: Any, actor_id: Any,
) -> None:
    """Transition to ACTIVE / COMPLETED / CANCELLED. No precondition (FR-012);
    reopen from either closed state is an ordinary transition (FR-011); a
    same-status set is a recorded no-op, never an error. One transaction (R2):
    UPDATE cached status_code (with the R1 concurrency check) + INSERT event.
    There is NO delete function (FR-014)."""
    sid = str(submission_id)
    now = _utcnow()
    actor = str(actor_id)
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            rows_affected = conn.execute(text(
                """
                UPDATE submission
                SET status_code = :s, updated_at = :now, updated_by = :actor
                WHERE id = :id AND updated_at = :expected
                """
            ), {
                "s": to_status, "now": now, "actor": actor,
                "id": sid, "expected": expected_updated_at,
            }).rowcount
            if rows_affected == 0:
                raise ConcurrencyConflict(
                    "This deal changed since you opened it — reload and re-apply."
                )
            conn.execute(text(
                """
                INSERT INTO submission_status_event
                    (id, submission_id, status_code, reason, at, inserted_by)
                VALUES (:eid, :sid, :s, :reason, :now, :actor)
                """
            ), {
                "eid": str(uuid.uuid4()), "sid": sid, "s": to_status,
                "reason": reason, "now": now, "actor": actor,
            })


def get_status_history(submission_id: Any) -> list[StatusEvent]:
    """Full immutable history, newest first (FR-013)."""
    rows = execute(
        """
        SELECT e.id, e.status_code, sk.label AS status_label, e.reason, e.at,
               e.inserted_by, u.display_name AS inserted_by_name
        FROM submission_status_event e
        LEFT JOIN submission_status_kind sk ON sk.code = e.status_code
        LEFT JOIN app_user u ON u.id = e.inserted_by
        WHERE e.submission_id = :id
        ORDER BY e.at DESC, e.id DESC
        """,
        {"id": str(submission_id)}, connection="WORKBENCH",
    )
    return [
        StatusEvent(
            id=_uid(row["id"]),
            status_code=row["status_code"],
            status_label=row.get("status_label"),
            reason=row["reason"],
            at=row["at"],
            inserted_by=_uid(row["inserted_by"]),
            inserted_by_name=row.get("inserted_by_name"),
        )
        for row in rows
    ]


# ── CRM tags (gated; append-only inserts) ────────────────────────────────────

def add_crm_id(*, submission_id: Any, crm_id: str, actor_id: Any) -> str:
    """Add a free-text CRM tag to an ACTIVE deal. Blank/whitespace is rejected
    (not stored); no format validation. Re-adding a tag the deal already carries
    (case-insensitive) is a silent no-op — the existing tag id comes back."""
    cleaned_crm_id = (crm_id or "").strip()
    if not cleaned_crm_id:
        raise ValueError("crm_id is blank")
    _require_active(_load_status(submission_id))
    for existing in list_crm_ids(submission_id):
        if existing.crm_id.casefold() == cleaned_crm_id.casefold():
            return str(existing.id)
    new_tag_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO submission_crm_id (id, submission_id, crm_id, inserted_at, "
        "inserted_by) VALUES (:id, :sid, :c, :now, :by)",
        {"id": new_tag_id, "sid": str(submission_id), "c": cleaned_crm_id,
         "now": _utcnow(), "by": str(actor_id)},
        connection="WORKBENCH",
    )
    return new_tag_id


def remove_crm_id(*, crm_tag_id: Any, actor_id: Any) -> None:
    row = execute_one(
        "SELECT submission_id FROM submission_crm_id WHERE id = :id",
        {"id": str(crm_tag_id)}, connection="WORKBENCH",
    )
    if row is None:
        return
    _require_active(_load_status(row["submission_id"]))
    execute_command(
        "DELETE FROM submission_crm_id WHERE id = :id",
        {"id": str(crm_tag_id)}, connection="WORKBENCH",
    )


def list_crm_ids(submission_id: Any) -> list[CrmTag]:
    rows = execute(
        "SELECT id, submission_id, crm_id, inserted_at FROM submission_crm_id "
        "WHERE submission_id = :id ORDER BY inserted_at, id",
        {"id": str(submission_id)}, connection="WORKBENCH",
    )
    return [
        CrmTag(
            id=_uid(row["id"]),
            submission_id=_uid(row["submission_id"]),
            crm_id=row["crm_id"],
            inserted_at=row["inserted_at"],
        )
        for row in rows
    ]
