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

from sqlalchemy import text

from db import (execute, execute_one, execute_scalar, execute_command,
                get_connection, row_limit)
from app.services._common import _uid, _utcnow
from app.services.errors import (
    ConcurrencyConflict,
    SelfLinkError,
    SubmissionClosed,
)

ACTIVE = "ACTIVE"


# ── Result / row DTOs (contracts/data-access.md) ─────────────────────────────

@dataclass
class SubmissionRow:
    """One master-list / look-alike row."""
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


def _default_treaty_year(treaty_year: int | None, inception_date: Any) -> int | None:
    """Fall back to the inception year when the analyst left treaty year blank
    (CR5). An entered year always wins — a December inception is often written
    into the following treaty year."""
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


# ── Create / read / list ─────────────────────────────────────────────────────

def create_submission(
    *, name: str, cedant_name: str, treaty_type_code: str, inception_date: Any,
    treaty_year: int | None = None, links_to_submission_id: Any = None,
    directory_path: str | None = None, actor_id: Any, confirmed: bool = False,
) -> CreateResult:
    """Create an ACTIVE submission owned by ``actor_id``.

    Runs the non-blocking duplicate check first: unconfirmed look-alikes short-
    circuit with ``created=False`` and warnings, writing nothing (FR-004). On the
    write path the submission row and its initial ACTIVE status event commit in
    one transaction (R2).

    ``treaty_year`` left as ``None`` is filled from the inception year (CR5). The
    form fills the same value client-side; this is what makes the rule hold with
    JavaScript off."""
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
        "lt": str(links_to_submission_id) if links_to_submission_id else None,
        "dir": directory_path,
        "now": now,
        "actor": actor,
    }
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
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
    return CreateResult(created=True, submission_id=sid)


def get_submission(submission_id: Any) -> Submission | None:
    """Full detail incl. cached status_code. No access restriction (FR-019)."""
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
        {"id": str(submission_id)}, connection="WORKBENCH",
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


def list_submissions(
    *, owner_id: Any = None, cedant_name: str | None = None,
    treaty_type_code: str | None = None, inception_date: Any = None,
    treaty_year: int | None = None,
) -> list[SubmissionRow]:
    """Master list. ``owner_id`` set → "My Submissions" (plain predicate, R7);
    ``None`` → All. Filters AND-combine as bound predicates (FR-021). Every deal
    is visible to every analyst regardless of owner (Article 6)."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if owner_id is not None:
        clauses.append("s.assigned_analyst_id = :owner")
        params["owner"] = str(owner_id)
    if cedant_name:
        clauses.append("s.cedant_name = :cedant")
        params["cedant"] = cedant_name
    if treaty_type_code:
        clauses.append("s.treaty_type_code = :tt")
        params["tt"] = treaty_type_code
    if inception_date is not None:
        clauses.append("s.inception_date = :inc")
        params["inc"] = _as_date(inception_date)
    if treaty_year is not None:
        clauses.append("s.treaty_year = :ty")
        params["ty"] = int(treaty_year)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = execute(
        _ROW_SELECT + where + " ORDER BY s.inception_date DESC, s.name",
        params, connection="WORKBENCH",
    )
    return [_to_row(row) for row in rows]


def find_similar(
    *, name: str, cedant_name: str, treaty_type_code: str, inception_date: Any,
    exclude_id: Any = None,
) -> list[SubmissionRow]:
    """Look-alikes: same ``name`` OR same (cedant + treaty_type + inception)
    (FR-004/R4). ``exclude_id`` skips the row being renamed. Never raises."""
    params = {
        "name": name,
        "cedant": cedant_name,
        "tt": treaty_type_code,
        "inc": _as_date(inception_date),
        "exclude": str(exclude_id) if exclude_id else None,
    }
    rows = execute(
        _ROW_SELECT + """
        WHERE (s.name = :name
               OR (s.cedant_name = :cedant AND s.treaty_type_code = :tt
                   AND s.inception_date = :inc))
          AND (:exclude IS NULL OR s.id <> :exclude)
        ORDER BY s.inception_date DESC, s.name
        """,
        params, connection="WORKBENCH",
    )
    return [_to_row(row) for row in rows]


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
    Mutual", which a ``LIKE 'fam%'`` match never returns.

    ``limit`` is applied by the server. Reading every cedant matching "am" back
    into Python to keep ten of them is the entire cost of the query, and the
    ordered ``ix_submission_cedant_name`` scan stops at ten once the cap is in
    the SQL."""
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
    matched against name or cedant. Backs the "links to" picker (CR8).

    Terms AND-combine (CR2): "american fam" must not return every deal containing
    "American". ``exclude_id`` drops the submission being edited so it cannot be
    offered as its own link — ``update_submission`` still raises ``SelfLinkError``
    as the real check.

    ``limit`` is applied by the server. ``ORDER BY inception_date DESC`` walks
    ``ix_submission_inception_date`` and can stop once ten rows pass the LIKE
    predicates, instead of matching, joining, and sorting every deal that
    contains the term."""
    trimmed = (term or "").strip()
    if len(trimmed) < MIN_SUGGEST_TERM:
        return []
    terms = trimmed.split()
    clauses: list[str] = []
    params: dict[str, Any] = {"exclude": str(exclude_id) if exclude_id else None}
    for index, word in enumerate(terms):
        key = f"t{index}"
        clauses.append(
            f"(s.name LIKE :{key} ESCAPE '\\' OR s.cedant_name LIKE :{key} ESCAPE '\\')"
        )
        params[key] = f"%{_escape_like(word)}%"
    rows = execute(
        _ROW_SELECT
        + " WHERE " + " AND ".join(clauses)
        + " AND (:exclude IS NULL OR s.id <> :exclude)"
        + " ORDER BY s.inception_date DESC, s.name "
        + row_limit(limit),
        params, connection="WORKBENCH",
    )
    return [_to_row(row) for row in rows]


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
    (self-link) + R4 (non-blocking duplicate warning on rename)."""
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

    links_to = merged["links_to_submission_id"]
    if links_to is not None and _uid(links_to) == _uid(sid):
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
            "lt": str(links_to) if links_to else None,
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
