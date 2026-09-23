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
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote

from sqlalchemy import text

from app.services import client_service
from app.services._common import (
    _escape_like,
    _in_clause,
    _rm_ui_root,
    _uid,
    _utcnow,
    _word_and_clauses,
)
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
    is_unique_violation,
    row_limit,
)

ACTIVE = "ACTIVE"
# The one contract status a contract can be in force under (FR-018, P-09).
WON = "WON"
IN_PROCESS = "IN_PROCESS"

# Rows per master-list request. The list is read newest-inception-first, so a
# page is what an analyst scans before narrowing; it also caps how many ids
# `_attach_contracts` binds, which SQL Server limits to 2,100 per statement.
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
    """One master-list / look-alike row. The contract summary (``crm_ids``,
    ``treaty_type_labels``, ``latest_inception_date``)
    is filled for the master list only (see ``_attach_contracts``); every other
    reader leaves it empty."""
    id: str
    name: str
    cedant_name: str
    treaty_year: int | None
    status_code: str
    status_label: str | None
    assigned_analyst_id: str
    assigned_analyst_name: str | None
    updated_at: Any
    client_id: int | None
    data_vintage: Any = None
    client_name: str | None = None
    crm_ids: list[str] = field(default_factory=list)
    treaty_type_labels: list[str] = field(default_factory=list)
    latest_inception_date: Any = None

    @property
    def client_display(self) -> str | None:
        return client_service.display(self.client_id, self.client_name)


@dataclass
class SubmissionPage:
    """One master-list page. ``has_next`` comes from reading one row past the page
    rather than a ``COUNT(*)``, which would scan everything the page cap avoids."""
    rows: list[SubmissionRow]
    page: int
    has_next: bool


@dataclass(frozen=True)
class Contract:
    """One contract of a deal: a CRM ID with its treaty type, term and status
    (data-model.md §3). ``updated_at`` is the R1 marker for in-place edits."""
    id: str
    submission_id: str
    crm_id: str
    treaty_type_code: str
    treaty_type_label: str | None
    inception_date: Any
    expiration_date: Any
    contract_status_code: str
    contract_status_label: str | None
    inserted_at: Any
    updated_at: Any


@dataclass(frozen=True)
class ContractInput:
    """What a form posts for one contract row. ``expiration_date`` left ``None``
    is filled as inception plus one year minus one day (P-03)."""
    crm_id: str
    treaty_type_code: str
    inception_date: Any
    expiration_date: Any = None
    contract_status_code: str = IN_PROCESS


@dataclass(frozen=True)
class ContractOwner:
    """The submission that already holds a CRM ID (FR-003, note 33 D14)."""
    submission_id: str
    name: str


class ContractInvalid(ValueError):
    """A contract row the service will not write. ``index`` is the row's position
    in the posted list (``None`` for a single-row edit) so the form can mark it.
    ``owner`` is set when the CRM ID belongs to another submission; the message
    then ends "is already a contract on" and the form appends the linked name."""

    def __init__(
        self, message: str, *, index: int | None = None,
        owner: ContractOwner | None = None,
    ) -> None:
        super().__init__(message)
        self.index = index
        self.owner = owner


@dataclass
class Submission:
    """Full detail view of a deal (cached Modeling status included). Treaty type,
    the term and the deal status live on ``contracts``."""
    id: str
    name: str
    cedant_name: str
    treaty_year: int | None
    links_to_submission_id: str | None
    directory_path: str | None
    status_code: str
    status_label: str | None
    assigned_analyst_id: str
    assigned_analyst_name: str | None
    inserted_at: Any
    updated_at: Any
    client_id: int | None
    data_vintage: Any = None
    client_name: str | None = None
    contracts: list[Contract] = field(default_factory=list)

    @property
    def client_display(self) -> str | None:
        return client_service.display(self.client_id, self.client_name)


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


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


def _default_treaty_year(treaty_year: int | None, inceptions: Sequence[Any]) -> int | None:
    """Fall back to the earliest contract inception's year when the analyst left
    treaty year blank (P-20); ``None`` when the deal has no contract. An entered
    year always wins (design note 08, D4)."""
    if treaty_year is not None:
        return treaty_year
    parsed = [_as_date(value) for value in inceptions if value is not None]
    return min(parsed).year if parsed else None


def _default_expiration(inception: date) -> date:
    """Inception plus one year minus one day (P-03): 1/1 to 12/31. A February 29
    inception lands on February 28 of the next year before the day is taken."""
    try:
        anniversary = inception.replace(year=inception.year + 1)
    except ValueError:
        anniversary = inception.replace(year=inception.year + 1, day=28)
    return anniversary - timedelta(days=1)


def _taken_elsewhere(
    prepared: Sequence[dict[str, Any]], *, exclude_contract_id: str | None = None,
) -> None:
    """``ContractInvalid`` naming the owner for the first prepared row whose CRM
    ID is already a contract on another submission. One query for the whole
    form, normalised like the P-10 filter (lower-cased, trimmed); the contract
    being edited keeps its own CRM ID."""
    keys = sorted({row["crm_id"].lower() for row in prepared})
    if not keys:
        return
    clause, params = _in_clause("LOWER(TRIM(crm_id))", keys, "crm")
    owners = {
        row["crm_id"].strip().lower():
            ContractOwner(str(row["submission_id"]), row["submission_name"])
        for row in execute(
            f"""
            SELECT crm_id, submission_id, submission_name, contract_id
            FROM v_contract WHERE {clause}
            """,
            params, connection="WORKBENCH",
        )
        if exclude_contract_id is None or str(row["contract_id"]) != exclude_contract_id
    }
    for index, row in enumerate(prepared):
        owner = owners.get(row["crm_id"].lower())
        if owner is not None:
            raise ContractInvalid(
                f"{row['crm_id']} is already a contract on", index=index, owner=owner)


def _prepare_contracts(
    contracts: Sequence[ContractInput], *, existing: Sequence[Contract] = (),
    editing_id: str | None = None,
) -> list[dict[str, Any]]:
    """The bound parameters for each posted contract row, validated as one set
    (FR-003): a CRM ID is present and unique across the Workbench, case-insensitive
    and trimmed, across the posted rows, the deal's stored contracts (less the
    row being edited) and every other submission's contracts; the treaty type
    and status are rows of their kind tables; dates parse; a blank expiration is
    defaulted. ``ContractInvalid`` names the first bad row, with ``owner`` set
    when another submission holds the CRM ID."""
    treaty_types = {code for code, _ in treaty_type_kinds()}
    statuses = {code for code, _ in contract_status_kinds()}
    taken = {c.crm_id.lower(): c.crm_id for c in existing
             if editing_id is None or c.id != editing_id}
    prepared: list[dict[str, Any]] = []
    for index, row in enumerate(contracts):
        crm_id = (row.crm_id or "").strip()
        if not crm_id:
            raise ContractInvalid("Enter the CRM ID.", index=index)
        if crm_id.lower() in taken:
            raise ContractInvalid(
                f"{taken[crm_id.lower()]} is already a contract on this submission.",
                index=index)
        taken[crm_id.lower()] = crm_id
        if row.treaty_type_code not in treaty_types:
            raise ContractInvalid("Choose a treaty type from the list.", index=index)
        status = row.contract_status_code or IN_PROCESS
        if status not in statuses:
            raise ContractInvalid("Choose a contract status from the list.", index=index)
        try:
            inception = _as_date(row.inception_date)
            expiration = _as_date(row.expiration_date)
        except ValueError:
            raise ContractInvalid("Enter the dates as YYYY-MM-DD.", index=index) from None
        if inception is None:
            raise ContractInvalid("Enter the inception date.", index=index)
        prepared.append({
            "crm_id": crm_id, "tt": row.treaty_type_code, "inc": inception,
            "exp": expiration if expiration is not None else _default_expiration(inception),
            "status": status,
        })
    _taken_elsewhere(prepared, exclude_contract_id=editing_id)
    return prepared


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
    SELECT s.id, s.name, s.cedant_name, s.treaty_year, s.data_vintage,
           s.status_code, sk.label AS status_label, s.client_id,
           s.assigned_analyst_id, u.display_name AS assigned_analyst_name,
           s.updated_at
    FROM submission s
    LEFT JOIN submission_status_kind sk ON sk.code = s.status_code
    LEFT JOIN app_user u ON u.id = s.assigned_analyst_id
"""

# The list's inception: the latest contract inception, or the deal's creation
# date for a deal with no contract yet (P-17). SQL Server resolves the COALESCE
# to DATETIME2 and SQLite compares the ISO text; both order the same.
_LATEST_INCEPTION = ("COALESCE((SELECT MAX(c.inception_date) FROM contract c "
                     "WHERE c.submission_id = s.id), s.inserted_at)")


def _to_row(row: dict) -> SubmissionRow:
    return SubmissionRow(
        id=_uid(row["id"]),
        name=row["name"],
        cedant_name=row["cedant_name"],
        treaty_year=row["treaty_year"],
        status_code=row["status_code"],
        status_label=row.get("status_label"),
        assigned_analyst_id=_uid(row["assigned_analyst_id"]),
        assigned_analyst_name=row.get("assigned_analyst_name"),
        updated_at=row["updated_at"],
        client_id=row["client_id"],
        data_vintage=row.get("data_vintage"),
    )


def _submission_rows(
    clauses: list[str], params: dict[str, Any], *, exclude_id: Any = None,
    limit: int | None = None, offset: int = 0,
    order_by: str = f"{_LATEST_INCEPTION} DESC, s.name",
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


def _attach_contracts(rows: list[SubmissionRow]) -> None:
    """Set each row's contract summary for the master list (FR-007): CRM IDs in
    insertion order, the distinct treaty type and contract status labels in
    their kind tables' order, and the latest inception. One query covers the
    page; a deal with no contract keeps the defaults.

    One bound parameter per row, so the caller has to hand this a page rather than a
    whole table — SQL Server rejects a statement carrying more than 2,100."""
    ids = list(dict.fromkeys(str(row.id).lower() for row in rows))
    if not ids:
        return
    params = {f"s{i}": sid for i, sid in enumerate(ids)}
    placeholders = ", ".join(f":{key}" for key in params)
    contracts = execute(
        "SELECT c.submission_id, c.crm_id, c.inception_date, "
        "tk.label AS treaty_type_label, tk.sort_order AS treaty_type_order "
        "FROM contract c "
        "LEFT JOIN treaty_type_kind tk ON tk.code = c.treaty_type_code "
        f"WHERE c.submission_id IN ({placeholders}) ORDER BY c.inserted_at, c.id",
        params, connection="WORKBENCH",
    )
    by_submission: dict[str, list[dict]] = {}
    for contract in contracts:
        by_submission.setdefault(str(contract["submission_id"]).lower(), []).append(contract)

    for row in rows:
        items = by_submission.get(str(row.id).lower(), [])
        row.crm_ids = [item["crm_id"] for item in items]
        labels = {item["treaty_type_label"]: item["treaty_type_order"]
                  for item in items if item["treaty_type_label"] is not None}
        row.treaty_type_labels = sorted(labels, key=lambda v: (labels[v] or 0, v))
        inceptions = [_as_date(item["inception_date"]) for item in items]
        row.latest_inception_date = max(inceptions) if inceptions else None


def _attach_client_names(rows: list[SubmissionRow]) -> None:
    """One ``client_names`` read per page; a row keeps ``None`` when the
    repository cannot be read (FR-009)."""
    names = client_service.client_names(
        row.client_id for row in rows if row.client_id is not None)
    for row in rows:
        if row.client_id is not None:
            row.client_name = names.get(int(row.client_id))


# ── Create / read / list ─────────────────────────────────────────────────────

_CONTRACT_INSERT = """
    INSERT INTO contract
        (id, submission_id, crm_id, treaty_type_code, inception_date,
         expiration_date, contract_status_code, inserted_at, updated_at,
         inserted_by, updated_by)
    VALUES (:id, :sid, :crm_id, :tt, :inc, :exp, :status, :now, :now, :actor, :actor)
"""


def create_submission(
    *, name: str, cedant_name: str, treaty_year: int | None = None,
    links_to_submission_id: Any = None, directory_path: str | None = None,
    client_id: int | None = None, data_vintage: Any = None,
    contracts: Sequence[ContractInput] = (),
    actor_id: Any, confirmed: bool = False,
) -> CreateResult:
    """Create an ACTIVE submission owned by ``actor_id`` with zero or more
    contracts (FR-004, P-16).

    Every contract row is validated before anything is written
    (``_prepare_contracts``); then the duplicate check runs: unconfirmed
    look-alikes short-circuit with ``created=False`` and warnings, writing
    nothing (FR-004). On the write path the submission row, its initial ACTIVE
    status event and its contracts commit in one transaction (R2).

    ``treaty_year`` left as ``None`` is filled from the earliest contract
    inception (P-20). ``links_to_submission_id`` is checked before the duplicate
    check, so an id naming no deal is refused without first showing a look-alike
    warning."""
    link_target = _resolve_link_target(links_to_submission_id)
    prepared = _prepare_contracts(contracts)
    matches = find_similar(
        name=name, cedant_name=cedant_name,
        contract_terms=[(row["tt"], row["inc"]) for row in prepared],
    )
    if matches and not confirmed:
        return CreateResult(created=False, warnings=matches)

    sid = str(uuid.uuid4())
    now = _utcnow()
    actor = str(actor_id)
    params = {
        "id": sid,
        "owner": actor,
        "name": name,
        "cedant": cedant_name,
        "client": client_id,
        "vintage": _as_date(data_vintage),
        "ty": _default_treaty_year(treaty_year, [row["inc"] for row in prepared]),
        "lt": link_target,
        "dir": directory_path,
        "now": now,
        "actor": actor,
    }
    try:
        with get_connection("WORKBENCH") as conn, conn.begin():
            conn.execute(text(
                """
                INSERT INTO submission
                    (id, assigned_analyst_id, name, cedant_name, client_id,
                     data_vintage, treaty_year, links_to_submission_id,
                     directory_path, status_code, inserted_at, updated_at,
                     inserted_by, updated_by)
                VALUES
                    (:id, :owner, :name, :cedant, :client, :vintage, :ty, :lt,
                     :dir, 'ACTIVE', :now, :now, :actor, :actor)
                """
            ), params)
            conn.execute(text(
                """
                INSERT INTO submission_status_event
                    (id, submission_id, status_code, reason, at, inserted_by)
                VALUES (:eid, :sid, 'ACTIVE', NULL, :now, :actor)
                """
            ), {"eid": str(uuid.uuid4()), "sid": sid, "now": now, "actor": actor})
            # One microsecond apart, so the rows read back in the order the analyst
            # entered them (list_contracts orders by inserted_at).
            for index, row in enumerate(prepared):
                conn.execute(text(_CONTRACT_INSERT), {
                    **row, "id": str(uuid.uuid4()), "sid": sid,
                    "now": now + timedelta(microseconds=index), "actor": actor})
    except Exception as exc:
        _reraise_if_taken(exc, prepared)
    return CreateResult(created=True, submission_id=sid)


def _reraise_if_taken(
    exc: Exception, prepared: Sequence[dict[str, Any]], *,
    exclude_contract_id: str | None = None,
) -> None:
    """A write that lost the race to ``uq_contract_crm_id`` between the
    ``_prepare_contracts`` lookup and the commit is reported like the lookup
    would have; any other failure is re-raised unchanged."""
    if is_unique_violation(exc):
        try:
            _taken_elsewhere(prepared, exclude_contract_id=exclude_contract_id)
        except ContractInvalid as taken:
            raise taken from exc
    raise exc


def get_submission(submission_id: Any) -> Submission | None:
    """Full detail incl. cached status_code. No access restriction (FR-019). An
    id that is not a UUID is "not found", not a query (see ``_as_uuid``)."""
    sid = _as_uuid(submission_id)
    if sid is None:
        return None
    row = execute_one(
        """
        SELECT s.id, s.name, s.cedant_name, s.treaty_year, s.data_vintage,
               s.links_to_submission_id,
               s.directory_path, s.status_code, sk.label AS status_label,
               s.client_id,
               s.assigned_analyst_id, u.display_name AS assigned_analyst_name,
               s.inserted_at, s.updated_at
        FROM submission s
        LEFT JOIN submission_status_kind sk ON sk.code = s.status_code
        LEFT JOIN app_user u ON u.id = s.assigned_analyst_id
        WHERE s.id = :id
        """,
        {"id": sid}, connection="WORKBENCH",
    )
    if row is None:
        return None
    client_name = None
    if row["client_id"] is not None:
        client_name = client_service.client_names([row["client_id"]]).get(
            int(row["client_id"]))
    return Submission(
        id=_uid(row["id"]),
        name=row["name"],
        cedant_name=row["cedant_name"],
        treaty_year=row["treaty_year"],
        links_to_submission_id=_uid(row["links_to_submission_id"]),
        directory_path=row["directory_path"],
        status_code=row["status_code"],
        status_label=row.get("status_label"),
        assigned_analyst_id=_uid(row["assigned_analyst_id"]),
        assigned_analyst_name=row.get("assigned_analyst_name"),
        inserted_at=row["inserted_at"],
        updated_at=row["updated_at"],
        client_id=row["client_id"],
        data_vintage=row.get("data_vintage"),
        client_name=client_name,
        contracts=list_contracts(sid),
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
    "inception": _LATEST_INCEPTION,
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
    cedant_name: str | None = None, crm_ids: list[str] | None = None,
    treaty_type_codes: list[str] | None = None, inception_date: Any = None,
    treaty_years: list[int] | None = None, status_codes: list[str] | None = None,
    contract_status_codes: list[str] | None = None,
    client_ids: list[Any] | None = None, in_force_as_of: Any = None,
    page: int = 1, sort: str = DEFAULT_SORT, descending: bool = True,
) -> SubmissionPage:
    """One page of the master list. Filters AND-combine as bound predicates
    (FR-021). Every deal is visible to every analyst regardless of owner
    (Article 6) — ``owner_ids`` is a plain predicate, never an access gate (R7).

    The list filters OR within themselves and AND against the others (D16). An
    empty list turns that filter off: ``owner_ids=[]`` lists every owner's deals.

    ``name`` (CR1) and ``cedant_name`` match on words, every word required — see
    ``_word_and_clauses``. Contract-level filters (CRM IDs, treaty types,
    inception, contract status, in force) are satisfied by one contract row
    together (P-18) — see ``submission_filter_clauses``.

    ``page`` is 1-based; anything lower is page 1, so a hand-typed ``?page=0``
    reads the first page rather than a negative offset. ``sort`` is a key of
    ``SORT_COLUMNS``.

    No minimum term length: every read is capped at ``PAGE_SIZE``, so a
    one-character search costs no more than the page it narrows."""
    clauses, params = submission_filter_clauses({
        "owner_ids": owner_ids, "name": name, "cedant_name": cedant_name,
        "crm_ids": crm_ids, "treaty_type_codes": treaty_type_codes,
        "inception_date": inception_date, "treaty_years": treaty_years,
        "status_codes": status_codes, "contract_status_codes": contract_status_codes,
        "client_ids": client_ids, "in_force_as_of": in_force_as_of,
    })
    page = max(1, int(page or 1))
    # One row past the page: its presence is what "there is a next page" means,
    # without a COUNT(*) over the same predicates.
    rows = _submission_rows(clauses, params, limit=PAGE_SIZE + 1,
                            offset=(page - 1) * PAGE_SIZE,
                            order_by=_order_by(sort, descending))
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    _attach_contracts(rows)
    _attach_client_names(rows)
    return SubmissionPage(rows=rows, page=page, has_next=has_next)


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
    contract satisfies them together (P-18, research.md R4). ``name`` and
    ``cedant_name`` match on words, every word required (see
    ``_word_and_clauses``); each ``crm_ids`` value matches a whole CRM ID,
    case-insensitive and trimmed (P-10); ``in_force_as_of`` is a ``date`` and
    applies FR-018; the rest are exact."""
    s = alias
    clauses: list[str] = []
    params: dict[str, Any] = {}
    contract_clauses: list[str] = []
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
    if filters.get("cedant_name"):
        more_clauses, more = _word_and_clauses(
            filters["cedant_name"], (f"{s}.cedant_name",), "c")
        clauses += more_clauses
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
            f"{s}.client_id", [_as_int(c) for c in filters["client_ids"]], "cl")
        clauses.append(clause)
        params |= more
    if filters.get("crm_ids"):
        clause, more = _in_clause(
            "LOWER(TRIM(c.crm_id))",
            [str(value).strip().lower() for value in filters["crm_ids"]], "crm")
        contract_clauses.append(clause)
        params |= more
    if filters.get("treaty_type_codes"):
        clause, more = _in_clause("c.treaty_type_code", filters["treaty_type_codes"], "tt")
        contract_clauses.append(clause)
        params |= more
    if filters.get("inception_date") is not None:
        contract_clauses.append("c.inception_date = :inc")
        params["inc"] = _as_date(filters["inception_date"])
    if filters.get("contract_status_codes"):
        clause, more = _in_clause(
            "c.contract_status_code", filters["contract_status_codes"], "cs")
        contract_clauses.append(clause)
        params |= more
    if filters.get("in_force_as_of") is not None:
        contract_clauses.append(
            "c.contract_status_code = :won AND c.inception_date <= :asof "
            "AND c.expiration_date >= :asof")
        params["won"] = WON
        params["asof"] = _as_date(filters["in_force_as_of"])
    if contract_clauses:
        # One EXISTS, not a join: a deal with three matching contracts is still
        # one row, and every contract-level filter is met by the same contract.
        clauses.append(
            f"EXISTS (SELECT 1 FROM contract c WHERE c.submission_id = {s}.id AND "
            + " AND ".join(contract_clauses) + ")")
    return clauses, params


def has_submission_filters(filters: dict[str, Any] | None) -> bool:
    """Whether any submission-attribute filter is set, so a library adds its
    EXISTS only then and unlinked EDMs and RDMs stay listed otherwise (FR-016)."""
    return bool(filters) and any(
        value is not None and value != [] and value != ""
        for value in filters.values())


def _kinds(table: str) -> list[tuple[str, str]]:
    """Every row of a kind table as (code, label) in display order. ``table`` is
    one of the three literals below, never input."""
    rows = execute(
        f"SELECT code, label FROM {table} ORDER BY sort_order, code",
        {}, connection="WORKBENCH",
    )
    return [(row["code"], row["label"]) for row in rows]


def status_kinds() -> list[tuple[str, str]]:
    """Modeling statuses, for the list's status filter (Article 4)."""
    return _kinds("submission_status_kind")


def treaty_type_kinds() -> list[tuple[str, str]]:
    """The maintained treaty-type list, for the form and every treaty-type
    filter (FR-012)."""
    return _kinds("treaty_type_kind")


def contract_status_kinds() -> list[tuple[str, str]]:
    """Contract statuses — Won, Lost, In Process (T-01)."""
    return _kinds("contract_status_kind")


def find_similar(
    *, name: str, cedant_name: str,
    contract_terms: Sequence[tuple[str, Any]] = (), exclude_id: Any = None,
) -> list[SubmissionRow]:
    """Look-alikes: same ``name``, OR same cedant with a contract of the same
    treaty type and inception as one of ``contract_terms`` (FR-004/R4). A deal
    with no contract is compared on its name alone. ``exclude_id`` skips the row
    being renamed. Never raises."""
    clauses = ["s.name = :name"]
    params: dict[str, Any] = {"name": name, "cedant": cedant_name}
    for index, (treaty_type_code, inception_date) in enumerate(contract_terms):
        clauses.append(
            "(s.cedant_name = :cedant AND EXISTS (SELECT 1 FROM contract c "
            f"WHERE c.submission_id = s.id AND c.treaty_type_code = :tt{index} "
            f"AND c.inception_date = :inc{index}))")
        params[f"tt{index}"] = treaty_type_code
        params[f"inc{index}"] = _as_date(inception_date)
    return _submission_rows(
        ["(" + " OR ".join(clauses) + ")"], params, exclude_id=exclude_id)


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
        "OR EXISTS (SELECT 1 FROM contract c "
        "WHERE c.submission_id = s.id AND c.crm_id LIKE :q ESCAPE '\\'))"
    ]
    return _submission_rows(clauses, {"q": like}, limit=limit)


# ── Edit / reassign (gated + concurrency-checked) ────────────────────────────

_MUTABLE_FIELDS = (
    "name", "cedant_name", "client_id", "data_vintage", "treaty_year",
    "links_to_submission_id", "directory_path",
)


def update_submission(
    *, submission_id: Any, expected_updated_at: Any, actor_id: Any,
    confirmed: bool = False, **fields: Any,
) -> UpdateResult:
    """Edit mutable fields, gated by R3 (ACTIVE) + R1 (concurrency) + R9
    (self-link, and a ``links_to_submission_id`` naming no submission) + R4
    (non-blocking duplicate warning on rename). Contracts are edited through
    ``add_contract`` / ``update_contract`` / ``remove_contract``.

    ``treaty_year`` is refilled from the earliest contract inception whenever
    the merged value is None (P-20)."""
    sid = str(submission_id)
    current = execute_one(
        "SELECT status_code, name, cedant_name, client_id, data_vintage, "
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
    merged["data_vintage"] = _as_date(merged["data_vintage"])
    contracts = list_contracts(sid)
    merged["treaty_year"] = _default_treaty_year(
        merged["treaty_year"], [c.inception_date for c in contracts])

    # Resolve first, self-link second: a submission's own id always exists, so
    # linking to itself must report SelfLinkError, not "not found".
    links_to = _resolve_link_target(merged["links_to_submission_id"])
    if links_to is not None and links_to == _uid(sid):
        raise SelfLinkError("A submission cannot link to itself.")

    matches = find_similar(
        name=merged["name"], cedant_name=merged["cedant_name"],
        contract_terms=[(c.treaty_type_code, c.inception_date) for c in contracts],
        exclude_id=sid,
    )
    if matches and not confirmed:
        return UpdateResult(updated=False, warnings=matches)

    rows_affected = execute_command(
        """
        UPDATE submission
        SET name = :name, cedant_name = :cedant, client_id = :client,
            data_vintage = :vintage, treaty_year = :ty,
            links_to_submission_id = :lt, directory_path = :dir,
            updated_at = :now, updated_by = :actor
        WHERE id = :id AND updated_at = :expected
        """,
        {
            "name": merged["name"],
            "cedant": merged["cedant_name"],
            "client": merged["client_id"],
            "vintage": merged["data_vintage"],
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


# ── Modeling status (event-sourced) ─────────────────────────────────────────

def set_statuses(
    *, submission_id: Any, modeling_status: str, reason: str | None = None,
    expected_updated_at: Any, actor_id: Any,
) -> None:
    """The deal's Modeling status, event-sourced (R2): the cached ``status_code``
    and the ``submission_status_event`` row are written together under the R1
    marker. No transition is refused (FR-011, FR-012) and a same-status set is
    a recorded no-op; there is no delete (FR-014). ``ValueError`` on a code that
    is not in the kind table. Contract status is ``set_contract_status``."""
    if modeling_status not in {code for code, _ in status_kinds()}:
        raise ValueError(f"unknown Modeling status {modeling_status!r}")
    sid = str(submission_id)
    if _load_status(sid) is None:
        raise LookupError(f"submission {sid} not found")
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
            ), {"s": modeling_status, "now": now, "actor": actor, "id": sid,
                "expected": expected_updated_at}).rowcount
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
                "eid": str(uuid.uuid4()), "sid": sid, "s": modeling_status,
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


# ── Contracts (attributes gated on Active; status in place, ungated) ─────────

def list_contracts(submission_id: Any) -> list[Contract]:
    """The deal's contracts, oldest first (data-model.md §3)."""
    rows = execute(
        "SELECT c.id, c.submission_id, c.crm_id, c.treaty_type_code, "
        "tk.label AS treaty_type_label, c.inception_date, c.expiration_date, "
        "c.contract_status_code, ck.label AS contract_status_label, "
        "c.inserted_at, c.updated_at "
        "FROM contract c "
        "LEFT JOIN treaty_type_kind tk ON tk.code = c.treaty_type_code "
        "LEFT JOIN contract_status_kind ck ON ck.code = c.contract_status_code "
        "WHERE c.submission_id = :id ORDER BY c.inserted_at, c.id",
        {"id": str(submission_id)}, connection="WORKBENCH",
    )
    return [
        Contract(
            id=_uid(row["id"]),
            submission_id=_uid(row["submission_id"]),
            crm_id=row["crm_id"],
            treaty_type_code=row["treaty_type_code"],
            treaty_type_label=row.get("treaty_type_label"),
            inception_date=row["inception_date"],
            expiration_date=row["expiration_date"],
            contract_status_code=row["contract_status_code"],
            contract_status_label=row.get("contract_status_label"),
            inserted_at=row["inserted_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


def _contract_submission(contract_id: Any) -> str:
    sid = execute_scalar(
        "SELECT submission_id FROM contract WHERE id = :id",
        {"id": str(contract_id)}, connection="WORKBENCH",
    )
    if sid is None:
        raise LookupError(f"contract {contract_id} not found")
    return str(sid)


def add_contract(*, submission_id: Any, contract: ContractInput, actor_id: Any) -> str:
    """One more contract on an ACTIVE deal (FR-004), validated by
    ``_prepare_contracts`` against the deal's contracts and every other
    submission's; returns the new id."""
    sid = str(submission_id)
    _require_active(_load_status(sid))
    [row] = _prepare_contracts([contract], existing=list_contracts(sid))
    new_id = str(uuid.uuid4())
    try:
        execute_command(
            _CONTRACT_INSERT,
            {**row, "id": new_id, "sid": sid, "now": _utcnow(), "actor": str(actor_id)},
            connection="WORKBENCH",
        )
    except Exception as exc:
        _reraise_if_taken(exc, [row])
    return new_id


def update_contract(
    *, contract_id: Any, contract: ContractInput, expected_updated_at: Any,
    actor_id: Any,
) -> None:
    """A contract's CRM ID, treaty type and term, in place under its R1 marker;
    gated on the deal being Active. The status field of ``contract`` is ignored:
    ``set_contract_status`` owns it."""
    cid = _uid(contract_id)
    sid = _contract_submission(cid)
    _require_active(_load_status(sid))
    [row] = _prepare_contracts(
        [contract], existing=list_contracts(sid), editing_id=cid)
    try:
        rows_affected = execute_command(
            """
            UPDATE contract
            SET crm_id = :crm_id, treaty_type_code = :tt, inception_date = :inc,
                expiration_date = :exp, updated_at = :now, updated_by = :actor
            WHERE id = :id AND updated_at = :expected
            """,
            {"crm_id": row["crm_id"], "tt": row["tt"], "inc": row["inc"],
             "exp": row["exp"], "now": _utcnow(), "actor": str(actor_id),
             "id": cid, "expected": expected_updated_at},
            connection="WORKBENCH",
        )
    except Exception as exc:
        _reraise_if_taken(exc, [row], exclude_contract_id=cid)
    if rows_affected == 0:
        raise ConcurrencyConflict(
            "This contract changed since you opened it — reload and re-apply.")


def set_contract_status(
    *, contract_id: Any, to_status: str, expected_updated_at: Any, actor_id: Any,
) -> None:
    """Won / Lost / In Process on one contract, set in place in every Modeling
    status with no event and no reason (P-02, P-12; Article 4 "other status").
    ``ValueError`` on a code that is not in the kind table."""
    if to_status not in {code for code, _ in contract_status_kinds()}:
        raise ValueError(f"unknown contract status {to_status!r}")
    cid = _uid(contract_id)
    _contract_submission(cid)
    rows_affected = execute_command(
        """
        UPDATE contract
        SET contract_status_code = :status, updated_at = :now, updated_by = :actor
        WHERE id = :id AND updated_at = :expected
        """,
        {"status": to_status, "now": _utcnow(), "actor": str(actor_id),
         "id": cid, "expected": expected_updated_at},
        connection="WORKBENCH",
    )
    if rows_affected == 0:
        raise ConcurrencyConflict(
            "This contract changed since you opened it — reload and re-apply.")


def remove_contract(*, contract_id: Any, actor_id: Any) -> None:
    """Delete one contract of an ACTIVE deal; its status and dates go with it
    (FR-004). A contract that is already gone is a no-op."""
    try:
        sid = _contract_submission(contract_id)
    except LookupError:
        return
    _require_active(_load_status(sid))
    execute_command(
        "DELETE FROM contract WHERE id = :id",
        {"id": str(contract_id)}, connection="WORKBENCH",
    )
