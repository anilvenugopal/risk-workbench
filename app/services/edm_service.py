"""EDM service — import an exposure file as an ``irp_edm`` and track it (US1).

Every Risk Modeler call is deferred to a worker (Article 11 / FR-042): ``import_edm``
creates the ``irp_edm`` (``status='pending_import'``) and enqueues one ``upload_edm``
head — **no gateway call on the request path**. The worker submits; the poller
mirrors status and flips the entity to ``ready``/``error`` (worker-poller.md).

Name collision **blocks the save** (FR-012 as amended by issue #17): ``import_edm``
raises ``NameCollisionError`` before persisting anything when the name already exists
in Risk Modeler. When the gateway can't answer, the check fails OPEN — the save
proceeds with ``ImportResult.collision_unchecked=True`` (the router warns) and the
worker-side submit validation is the backstop. No function applies row scoping —
every analyst sees every EDM (Article 6 / FR-037).

``list_adoptable_edms`` / ``adopt_edms`` take in an EDM that already exists in Risk
Modeler: the workbench never imported it, so there is nothing to submit. Listing is
a second permitted request-path *read*; the adopt is a plain insert plus one
``backfill_edm_detail`` head, which fetches the portfolios and treaties.

Portability matches ``submission_service``: app-side UUIDs bound
as ``str``, app-supplied UTC timestamps, no dialect-only SQL.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from app.services import (
    analysis_service,
    breakout_service,
    geohaz_service,
    irp_gateway,
    name_check,
    portfolio_service,
    rwb_job_service,
    treaty_service,
)
from app.services._common import (
    SubmissionRef,
    _attach_submissions,
    _import_entity,
    _mark_error,
    _mark_importing,
    _replace_source_file,
    _retry_import,
    _rm_ui_root,
    _submission_entity_context,
    _uid,
    _utcnow,
)
from app.services.analysis_service import BrokerAnalysisGroup, ExecutedAnalysis
from app.services.errors import EdmCatalogUnavailable
from app.services.name_check import CollisionCheck
from app.services.portfolio_service import PortfolioRow
from app.services.treaty_service import TreatyRow
from app.workers import dispatch
from db import execute, execute_command, execute_one, is_unique_violation

logger = logging.getLogger(__name__)

# Entity-status lifecycle (plain string — Article 3 carve-out).
PENDING = "pending_import"
IMPORTING = "importing"
READY = "ready"
ERROR = "error"
# Ordered status vocabulary offered as the library status filter (US7 / T058).
STATUSES = (PENDING, IMPORTING, READY, ERROR)
# Statuses a worker still moves on its own — the library list polls while any row
# sits in one of these and stops once every row is terminal.
TRANSIENT_STATUSES = (PENDING, IMPORTING)
# Rows per page on the "sync existing EDMs" list. Matches submission_service's
# PAGE_SIZE so the two paged lists step through at the same rate.
ADOPTABLE_PAGE_SIZE = 50


@dataclass
class ImportResult:
    """The id of the created entity. ``collision_unchecked=True`` means Risk Modeler
    was unreachable for the blocking name check — the save proceeded fail-open and
    the router should warn (a real collision then fails at the worker submit)."""
    entity_id: str
    collision_unchecked: bool = False


@dataclass
class EdmRow:
    id: str
    name: str
    status: str | None
    source_file_path: str | None
    irp_id: int | None
    inserted_at: Any
    updated_at: Any
    notes: str | None = None
    # Owning submissions (M:N), oldest-first — populated only by ``list_edms``;
    # defaulted so ``get_edm`` and every existing caller are unaffected (US7 / T058).
    submissions: list[SubmissionRef] = field(default_factory=list)


def check_name_collision(name: str) -> CollisionCheck:
    """Check ``name`` against Risk Modeler (empty = clear). A hit blocks the save
    (issue #17); ``checked=False`` means the gateway couldn't answer — the caller
    fails open with a warning. Cached briefly in-process (issue #11)."""
    return name_check.check_edm_name(name)


def import_edm(
    *, name: str, source_file_path: str, actor_id: Any,
    submission_id: Any | None = None,
) -> ImportResult:
    """Create an ``irp_edm`` (``pending_import``) and enqueue one ``upload_edm`` head
    (``requestor_type='analyst_request'``, ``requestor_id=irp_edm.id``). The worker
    performs the submit; the only Risk Modeler call here is the cached name-collision
    *read* (permitted, Article 11). Validates the source is within
    ``SHARED_DRIVE_ROOT`` and is a file (else ``InvalidSourceFile``). Raises
    ``NameCollisionError`` — before persisting anything — when the name already
    exists in Risk Modeler (issue #17)."""
    entity_id, collision_unchecked = _import_entity(
        "edm", name=name, source_file_path=source_file_path, actor_id=actor_id,
        submission_id=submission_id)
    return ImportResult(entity_id=entity_id, collision_unchecked=collision_unchecked)


# ── adopting EDMs that already live in Risk Modeler ──────────────────────────────

@dataclass
class AdoptableEdm:
    """One Risk Modeler EDM with no ``irp_edm`` row. Straight from RM's exposures
    list except ``rm_url``, which the workbench builds."""
    irp_id: int
    name: str
    status: str | None = None
    server_name: str | None = None
    portfolio_count: int | None = None
    treaty_count: int | None = None
    updated_at: str | None = None
    rm_url: str | None = None


@dataclass
class AdoptablePage:
    """One page of the adoptable list. ``total`` counts every EDM the diff and the
    name search left, not just this page."""
    rows: list[AdoptableEdm]
    page: int
    has_next: bool
    total: int


@dataclass
class AdoptResult:
    """``adopted`` is the new ``irp_edm.id`` per EDM taken in; ``skipped`` holds the
    exposureIds that were not taken in — the workbench already tracks them, or Risk
    Modeler no longer lists them."""
    adopted: list[str] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)


def _claimed_exposure_ids() -> set[str]:
    """Every RM exposureId a live ``irp_edm`` row already holds."""
    rows = execute(
        "SELECT irp_id FROM irp_edm "
        "WHERE deleted_at IS NULL AND irp_id IS NOT NULL",
        connection="WORKBENCH")
    return {str(r["irp_id"]) for r in rows}


def _unresolved_names() -> set[str]:
    """Names of live rows that hold no exposureId — still importing, or the poller's
    by-name resolution missed and the row reached ``ready`` with ``irp_id`` NULL.
    ``error`` is excluded because a failed import created nothing in Risk Modeler."""
    rows = execute(
        "SELECT name FROM irp_edm WHERE deleted_at IS NULL AND irp_id IS NULL "
        "AND status <> :e",
        {"e": ERROR}, connection="WORKBENCH")
    return {r["name"] for r in rows}


def list_adoptable_edms(*, page: int = 1,
                        name: str | None = None) -> AdoptablePage | None:
    """Risk Modeler's EDMs minus the ones the workbench already tracks, or None when
    Risk Modeler does not answer — the page then renders its unavailable state
    rather than a short list, which would read as "everything is already synced".

    This *read* is permitted on the request path (Article 11, same latitude
    ``name_check`` takes) and is not cached: a stale list would offer an EDM that is
    already gone. A soft-deleted ``irp_edm`` row hides nothing — an EDM deleted here
    but still in Risk Modeler is offered again.

    Paging and the ``name`` search are applied here, not passed to Risk Modeler:
    removing the already-tracked EDMs is what makes a page, and that subtraction
    needs the whole list. ``page`` is 1-based and clamped to the last page that has
    rows."""
    claimed = _claimed_exposure_ids()
    unresolved = _unresolved_names()
    try:
        catalog = irp_gateway.list_edms()
    except Exception:
        logger.exception("adoptable EDM list unavailable — Risk Modeler read failed")
        return None
    term = (name or "").strip().lower()
    adoptable = [
        AdoptableEdm(
            irp_id=int(e.irp_id), name=e.name, status=e.status,
            server_name=e.server_name, portfolio_count=e.portfolio_count,
            treaty_count=e.treaty_count, updated_at=e.updated_at,
            rm_url=_rm_datasource_url(e.name, "portfolios"))
        for e in catalog
        if e.irp_id not in claimed and e.name not in unresolved
        and (not term or term in e.name.lower())
    ]
    adoptable.sort(key=lambda a: a.name.lower())
    last_page = max(1, (len(adoptable) + ADOPTABLE_PAGE_SIZE - 1) // ADOPTABLE_PAGE_SIZE)
    page = min(max(1, int(page or 1)), last_page)
    start = (page - 1) * ADOPTABLE_PAGE_SIZE
    return AdoptablePage(rows=adoptable[start:start + ADOPTABLE_PAGE_SIZE],
                         page=page, has_next=len(adoptable) > start + ADOPTABLE_PAGE_SIZE,
                         total=len(adoptable))


def adopt_edms(*, irp_ids: list[int], actor_id: Any) -> AdoptResult:
    """Create an ``irp_edm`` for each Risk Modeler EDM the analyst selected and
    enqueue one ``backfill_edm_detail`` head each, which fills in the portfolios,
    their exposure figures, and the treaties.

    Not routed through ``import_edm``: that path demands a shared-drive
    ``source_file_path`` and raises ``NameCollisionError`` on exactly the
    already-in-Risk-Modeler name every adoption uses. An adopted row therefore has
    ``source_file_path`` NULL and ``status='ready'``.

    A filtered unique index on live ``irp_id`` values decides concurrent attempts;
    the losing request reports the EDM as skipped. The insert guard also repeats the
    unresolved-name arm of the ``list_adoptable_edms`` diff."""
    result = AdoptResult()
    wanted = {int(i) for i in irp_ids}
    if not wanted:
        return result

    try:
        by_irp_id = {e.irp_id: e for e in irp_gateway.list_edms()}
    except Exception as exc:
        logger.exception("Risk Modeler EDM catalog unavailable during sync")
        raise EdmCatalogUnavailable from exc
    now = _utcnow()
    actor = str(actor_id)
    for irp_id in sorted(wanted):
        entry = by_irp_id.get(str(irp_id))
        if entry is None:
            logger.warning("adopt_edms: exposureId %s is not in Risk Modeler's "
                           "list — skipped", irp_id)
            result.skipped.append(irp_id)
            continue
        edm_id = str(uuid.uuid4())
        try:
            rows = execute_command(
                """
                INSERT INTO irp_edm (id, name, irp_id, server_name, status,
                    inserted_at, updated_at, inserted_by, updated_by)
                SELECT :id, :n, :iid, :srv, :s, :now, :now, :by, :by
                WHERE NOT EXISTS (
                    SELECT 1 FROM irp_edm
                    WHERE deleted_at IS NULL
                      AND (irp_id = :iid
                           OR (irp_id IS NULL AND name = :n
                               AND status <> :err)))
                """,
                {"id": edm_id, "n": entry.name, "iid": irp_id,
                 "srv": entry.server_name, "s": READY, "now": now, "by": actor,
                 "err": ERROR},
                connection="WORKBENCH",
            )
        except Exception as exc:
            if not is_unique_violation(exc):
                raise
            rows = 0
        if rows == 0:
            result.skipped.append(irp_id)
            continue
        result.adopted.append(edm_id)

    for edm_id in result.adopted:
        sync_detail(edm_id=edm_id, actor_id=actor)
    logger.info("adopted %d EDM(s) from Risk Modeler by analyst %s (%d skipped)",
                len(result.adopted), actor, len(result.skipped))
    return result


def _to_row(row: dict) -> EdmRow:
    return EdmRow(
        id=_uid(row["id"]),
        name=row["name"],
        status=row["status"],
        source_file_path=row["source_file_path"],
        irp_id=row["irp_id"],
        inserted_at=row["inserted_at"],
        updated_at=row["updated_at"],
        notes=row["notes"],
    )


_ROW_SELECT = (
    "SELECT id, source_file_path, name, irp_id, status, "
    "inserted_at, updated_at, notes FROM irp_edm"
)


def list_edms(*, name: str | None = None,
              status: str | None = None) -> list[EdmRow]:
    """Every EDM in the library, optionally filtered. NO row scoping
    (FR-037 / Article 6) — all analysts see all EDMs. Soft-deleted rows excluded.

    ``name`` narrows by case-insensitive substring (``LIKE`` — case-insensitive on
    SQL Server's default collation); ``status`` narrows to the
    exact import status; both combine with AND; blank/``None`` are no-ops (US7 / T058).
    Each returned row's ``.submissions`` is set to its owning submissions (oldest-first)."""
    where = "WHERE deleted_at IS NULL"
    params: dict[str, Any] = {}
    if name:
        where += " AND name LIKE :q"
        params["q"] = f"%{name}%"
    if status:
        where += " AND status = :status"
        params["status"] = status
    rows = execute(f"{_ROW_SELECT} {where} ORDER BY inserted_at DESC, name",
                   params, connection="WORKBENCH")
    result = [_to_row(r) for r in rows]
    _attach_submissions("edm", result)
    return result


def get_edm(edm_id: Any) -> EdmRow | None:
    row = execute_one(f"{_ROW_SELECT} WHERE id = :id",
                      {"id": str(edm_id)}, connection="WORKBENCH")
    return _to_row(row) if row is not None else None


def latest_import_error(edm_id: Any) -> str | None:
    """The specific message behind an ``error`` EDM, when one was recorded: the
    failed ``upload_edm`` head's ``error_detail`` (at most one row per entity —
    the queue dedups on requestor + type), with the worker's ``"upload_edm
    submit failed: "`` framing stripped so the page shows the Risk Modeler
    message itself — e.g. the wheel's "already exist(s)" name backstop (issue
    #17). ``None`` when nothing was recorded: an RM-side terminal failure flips
    the entity via the poller while the head itself *succeeded*."""
    row = execute_one(
        "SELECT error_detail FROM rwb_job "
        "WHERE requestor_type = 'analyst_request' AND requestor_id = :e "
        "AND rwb_job_type = 'upload_edm' AND status_code = 'failed'",
        {"e": str(edm_id)}, connection="WORKBENCH")
    detail = row["error_detail"] if row is not None else None
    if not detail:
        return None
    prefix = "upload_edm submit failed: "
    return detail[len(prefix):] if detail.startswith(prefix) else detail


# ── the redesigned detail page's single read (spec 004 US1 — R6) ─────────────────

@dataclass
class EdmDetail:
    """The redesigned EDM detail page payload: a light header (FR-011 — MUST NOT
    include cedant or line of business) + the per-portfolio read model (US1's
    primary content) + the section state. US2/US3/US4 extend this with treaties,
    analyses, and the derived aggregate."""
    id: str
    name: str
    status: str | None
    as_of: Any                      # last-synced trust signal (FR-052)
    source_file_path: str | None
    irp_id: int | None              # RM exposureId (durable entity id)
    created_by_irp_job_irp_id: str | None
    inserted_at: Any
    updated_at: Any
    portfolio_count: int
    portfolios: list[PortfolioRow]
    # 'populated' | 'importing' | 'pending' | 'failed' | 'empty' | 'unavailable'
    detail_state: str
    notes: str | None = None
    # a backfill head (either key) is pending/running — drives the "Syncing…"
    # button state even when the table is already populated
    sync_running: bool = False
    # US2: the EDM-level treaty set (parsed attributes) for the expand/collapse
    # view + Excel export; empty list ⇒ the section renders its own state.
    treaties: list[TreatyRow] = field(default_factory=list)
    # US3 (FR-037): the RDM-grouped broker-analyses list. Listed here, never
    # attributed to a portfolio (8/4 D8).
    analyses: list[BrokerAnalysisGroup] = field(default_factory=list)
    # Spec 010 US2 (FR-013): every analysis the workbench itself submitted
    # against this EDM, live-status-derived. No RDM grouping.
    executed_analyses: list[ExecutedAnalysis] = field(default_factory=list)
    # Treaties polish (2026-07-24): the deep link into Risk Modeler's OWN
    # treaties screen for this datasource — None when RISK_MODELER_BASE_URL is
    # not configured (the template falls back to the plain read-only note).
    rm_treaties_url: str | None = None
    # Issue #17 backstop surfacing: the failed upload head's specific Risk
    # Modeler message (``latest_import_error``) — set only when status ==
    # 'error'; None when the failure recorded no submit detail.
    import_error: str | None = None
    # Spec 005 (FR-012): a ``run_breakout_*`` job on one of this EDM's
    # portfolios is pending|running — keeps the body's 3s self-poll alive so
    # generated rows appear as the worker upserts them.
    breakout_running: bool = False
    # The newest terminal breakout job's completion banner
    # (breakout_service.BreakoutBanner) — None when nothing warrants one.
    breakout_banner: Any = None


@dataclass
class ContextualEdmDetail:
    edm: EdmDetail
    submission: SubmissionRef
    edm_choices: list[SubmissionRef]
    rdms: list[BrokerAnalysisGroup]


@dataclass
class EdmAnalysesSection:
    """Exactly what ``partials/analyses_merged_section.html`` reads. ``submission``
    and ``rdms`` are populated on the submission-scoped read only — the plain
    library page has no submission context and renders no RDM group rows."""
    id: str
    executed_analyses: list[ExecutedAnalysis]
    submission: SubmissionRef | None = None
    rdms: list[BrokerAnalysisGroup] = field(default_factory=list)


def latest_backfill_status(edm_id: str) -> str | None:
    """The newest ``backfill_edm_detail`` job status for this EDM across its
    three enqueue keys — ``rwb_job_service.backfill_edm_detail_rows`` owns the
    membership predicate. Newest ``updated_at`` wins — a revived (re-synced)
    row keeps its ``inserted_at``, so insert order would lie. ``None`` when
    detail backfill never ran — the pre-capability / forward-only state."""
    rows = rwb_job_service.backfill_edm_detail_rows([edm_id])
    return rows[0]["status_code"] if rows else None


def latest_backfill_statuses(edm_ids: list[Any]) -> dict[str, str | None]:
    """``latest_backfill_status`` for a whole entity table in one query —
    newest ``updated_at`` per EDM reduced app-side. Every requested id gets a
    key; EDMs whose detail backfill never ran map to ``None``.

    Keyed by ``_uid``, not ``str``: the requested ids are lowercase and
    ``rwb_job.link_id`` reads back UPPERCASE, so raw keys would never meet and
    every requested EDM would report ``None``."""
    statuses: dict[str, str | None] = {_uid(e): None for e in edm_ids}
    for row in rwb_job_service.backfill_edm_detail_rows(list(statuses)):
        key = _uid(row["edm_id"])
        if statuses.get(key) is None:
            statuses[key] = row["status_code"]
    return statuses


def _rm_datasource_url(name: str, screen: str) -> str | None:
    """The Risk Modeler UI deep link for one of this EDM's datasource screens
    (``_rm_ui_root`` explains the tenant-subdomain origin). ``None`` when the
    UI root is not configured.

    RM addresses the datasource by *name*, not exposureId, and EDM names are not
    unique — for a duplicated name the link lands on whichever one RM picks."""
    root = _rm_ui_root()
    if root is None:
        return None
    return (f"{root}/riskmodeler/datasources/"
            f"{quote(str(name), safe='')}/{screen}")


def _detail_state(status: str | None, as_of: Any,
                  portfolios: list[PortfolioRow], job_status: str | None) -> str:
    """Which graceful section state the page renders (ui.md §5) — never an error.
    ``empty`` (a real zero-portfolio EDM, FR-015) is distinguished from
    ``unavailable`` by the ``as_of`` stamp: the worker stamps it only after a real
    enumeration, so a succeeded-as-skip run (no exposureId) stays unavailable."""
    if status in (PENDING, IMPORTING):
        return "importing"
    if portfolios:
        return "populated"
    if job_status in ("pending", "running"):
        return "pending"
    if job_status == "failed":
        return "failed"
    if job_status == "succeeded" and as_of is not None:
        return "empty"
    return "unavailable"


def get_edm_detail(edm_id: Any) -> EdmDetail | None:
    """The redesigned EDM detail page's single read (contracts/data-access.md):
    light header from the existing ``irp_edm`` columns + every portfolio with its
    parsed snapshot (graceful empty when none). ``None`` only if the EDM itself
    is missing (→ router 404). ``get_edm`` stays unchanged for the worker and
    recovery paths."""
    eid = str(edm_id)
    row = execute_one(
        "SELECT id, source_file_path, name, irp_id, "
        "created_by_irp_job_irp_id, as_of, status, inserted_at, updated_at, notes "
        "FROM irp_edm WHERE id = :id",
        {"id": eid}, connection="WORKBENCH")
    if row is None:
        return None
    portfolios = portfolio_service.list_portfolios(edm_id=eid)
    geohaz = geohaz_service.read(edm_id=eid)
    for portfolio in portfolios:
        entry = geohaz.get(portfolio.id)
        if entry is not None:
            portfolio.geohaz_state = entry.state
            portfolio.geohaz_latest = entry.latest
    treaties = treaty_service.list_treaties(edm_id=eid)
    analyses = analysis_service.list_edm_analyses(edm_id=eid)
    executed_analyses = analysis_service.list_executed_analyses(edm_id=eid)
    # Spec 005: in-flight indicator, completion banner, and durable per-row
    # error lines for the breakout fan-out (FR-012) — WORKBENCH reads only.
    breakout = breakout_service.page_state(eid)
    for p in portfolios:
        p.breakout_flight = breakout.flights.get(p.id)
        p.breakout_errors = breakout.errors.get(p.id, [])
    job_status = latest_backfill_status(eid)
    return EdmDetail(
        id=_uid(row["id"]),
        name=row["name"],
        status=row["status"],
        as_of=row["as_of"],
        source_file_path=row["source_file_path"],
        irp_id=row["irp_id"],
        created_by_irp_job_irp_id=row["created_by_irp_job_irp_id"],
        inserted_at=row["inserted_at"],
        updated_at=row["updated_at"],
        portfolio_count=len(portfolios),
        portfolios=portfolios,
        detail_state=_detail_state(row["status"], row["as_of"], portfolios,
                                   job_status),
        notes=row["notes"],
        sync_running=job_status in ("pending", "running"),
        treaties=treaties,
        analyses=analyses,
        executed_analyses=executed_analyses,
        rm_treaties_url=_rm_datasource_url(row["name"], "treaties"),
        import_error=(latest_import_error(eid) if row["status"] == ERROR
                      else None),
        breakout_running=breakout.running,
        breakout_banner=breakout.banner,
    )


def get_contextual_edm_detail(
    *, submission_id: Any, edm_id: Any,
) -> ContextualEdmDetail | None:
    """Return an EDM only when it belongs to the named submission."""
    sid = str(submission_id)
    eid = str(edm_id)
    ctx = _submission_entity_context("edm", submission_id=sid, entity_id=eid)
    if ctx is None:
        return None
    source, choices = ctx
    edm = get_edm_detail(eid)
    if edm is None:
        return None
    # Local import avoids the edm_service/rdm_service shared-DTO import cycle
    # (same reason sync_contextual_detail below imports it locally).
    from app.services import rdm_service
    rdms = analysis_service.list_submission_rdms(submission_id=sid)
    for rdm in rdms:
        rdm.sync_running = (
            rdm_service.latest_backfill_status(rdm.rdm_id) in ("pending", "running"))
    return ContextualEdmDetail(
        edm=edm, submission=source, edm_choices=choices, rdms=rdms,
    )


def get_edm_analyses(
    *, edm_id: Any, submission_id: Any | None = None,
) -> EdmAnalysesSection | None:
    """The Analyses section's own read (T-11). Its 3s self-poll re-renders that
    one fragment, so it must not pay for the whole detail page — portfolios,
    geohaz, treaties and breakout page state are all unread by the fragment.
    With ``submission_id`` it also reads the submission's RDMs, which the merged
    section renders as group rows (spec 011 FR-010). ``None`` when the EDM is
    gone, or (with ``submission_id``) no longer related to that submission."""
    eid = str(edm_id)
    source = None
    if submission_id is not None:
        ctx = _submission_entity_context("edm", submission_id=submission_id,
                                         entity_id=eid)
        if ctx is None:
            return None
        source, _choices = ctx
    row = execute_one("SELECT id FROM irp_edm WHERE id = :id",
                      {"id": eid}, connection="WORKBENCH")
    if row is None:
        return None
    rdms: list[BrokerAnalysisGroup] = []
    if submission_id is not None:
        # Local import avoids the edm_service/rdm_service shared-DTO import cycle
        # (same reason get_contextual_edm_detail above imports it locally).
        from app.services import rdm_service
        rdms = analysis_service.list_submission_rdms(
            submission_id=str(submission_id))
        for rdm in rdms:
            rdm.sync_running = (
                rdm_service.latest_backfill_status(rdm.rdm_id)
                in ("pending", "running"))
    return EdmAnalysesSection(
        id=_uid(row["id"]),
        executed_analyses=analysis_service.list_executed_analyses(edm_id=eid),
        submission=source, rdms=rdms)


def sync_detail(*, edm_id: Any, actor_id: Any) -> str | None:
    """Analyst-triggered re-run of ``backfill_edm_detail`` for one EDM (FR-003 as
    amended 2026-07-23) — the recovery path for pre-capability EDMs and failed
    fetches; its scope grows with the worker (portfolios now, treaties with US2).
    Keyed ``(analyst_request, edm_id)`` so it works for EVERY EDM, including those
    with no FINISHED import irp_job; ``ensure_pending_rwb_job`` revives a terminal
    head in place. Skips (→ ``None``) when the EDM is missing/deleted, the import
    is still in flight, or a backfill head under EITHER key is pending/running."""
    eid = str(edm_id)
    current = _current(eid)
    if current is None or current["status"] in (PENDING, IMPORTING):
        return None
    if latest_backfill_status(eid) in ("pending", "running"):
        return None
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=eid,
        rwb_job_type="backfill_edm_detail",
        link_type="edm", link_id=eid,
        context_type="edm", context_id=eid,
        input_data={"edm_id": eid},
        actor_id=str(actor_id),
    )
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="backfill_edm_detail")
    return job_id


def sync_contextual_detail(
    *, submission_id: Any, edm_id: Any, actor_id: Any,
) -> bool:
    """Queue stored EDM and submission-RDM refreshes for a valid context."""
    context = get_contextual_edm_detail(
        submission_id=submission_id, edm_id=edm_id)
    if context is None:
        return False
    sync_detail(edm_id=edm_id, actor_id=actor_id)
    # Local import avoids the edm_service/rdm_service shared-DTO import cycle.
    from app.services import rdm_service
    for rdm in context.rdms:
        rdm_service.sync_detail(rdm_id=rdm.rdm_id, actor_id=actor_id)
    return True


def _current(edm_id: str) -> dict | None:
    return execute_one(
        "SELECT status, updated_at FROM irp_edm WHERE id = :id AND deleted_at IS NULL",
        {"id": edm_id}, connection="WORKBENCH")


def retry_import(*, edm_id: Any, actor_id: Any) -> None:
    """Re-enqueue a single EDM's ``upload_edm`` head (FR-045). Idempotent: a no-op
    when the EDM is already ``ready`` or in flight (``importing``); otherwise resets an
    ``error`` entity back to ``pending_import`` **and** the head back to ``pending`` so
    the worker re-submits (the body only advances a ``pending_import`` row, so the
    entity reset is required for the resubmit to actually fire)."""
    _retry_import("edm", entity_id=edm_id, actor_id=actor_id)


def replace_source_file(
    *, edm_id: Any, new_source_file_path: str, expected_updated_at: Any,
    actor_id: Any,
) -> None:
    """Replace the source file of a failed/errored EDM and re-import (FR-046).
    Optimistic-concurrency checked on ``updated_at`` (FR-039). Validates the new path."""
    _replace_source_file(
        "edm", entity_id=edm_id, new_source_file_path=new_source_file_path,
        expected_updated_at=expected_updated_at, actor_id=actor_id)


# ── worker / poller status writers (Article 11 boundary) ─────────────────────────

def mark_importing(*, edm_id: Any, actor_id: Any | None = None) -> None:
    """Worker-side: the import submit succeeded — flip ``pending_import`` → ``importing``
    (FR-004). Left alone if the row was already advanced (idempotent re-run)."""
    _mark_importing("edm", entity_id=edm_id, actor_id=actor_id)


def mark_error(*, edm_id: Any, actor_id: Any | None = None) -> None:
    """Worker-side: a **submit-side** failure (never reached Risk Modeler) — flip an
    import-in-progress EDM to the visible, analyst-recoverable ``error`` state, the same
    state the poller uses for an RM-side terminal failure (worker-poller.md §3).
    Only touches ``pending_import``/``importing``; idempotent on re-run."""
    _mark_error("edm", entity_id=edm_id, actor_id=actor_id)


def backfill_on_terminal(conn, *, edm_id: Any, status: str,
                         irp_id: str | None,
                         created_by_irp_job_irp_id: str | None = None) -> None:
    """Poller-side: on the import job's terminal status, flip the entity to
    ``ready``/``error`` and (on ready) backfill two *distinct* identifiers (FR-006):
    ``irp_id`` = the durable RM **entity id** (the EDM's ``exposureId``), while
    ``created_by_irp_job_irp_id`` = the **import job's**
    ``irp_id`` (audit / lineage). Runs inside the poller's transaction (accepts ``conn``)."""
    from sqlalchemy import text  # noqa: PLC0415 — local: keep module import surface small
    ready = status == READY
    numeric = int(irp_id) if (ready and irp_id is not None) else None
    conn.execute(text(
        """
        UPDATE irp_edm
        SET status = :s, irp_id = :iid, created_by_irp_job_irp_id = :cid,
            updated_at = :now
        WHERE id = :id
        """
    ), {"s": status, "iid": numeric,
        "cid": (str(created_by_irp_job_irp_id)
                if ready and created_by_irp_job_irp_id is not None else None),
        "now": _utcnow(), "id": str(edm_id)})


__all__ = [
    "ImportResult", "EdmRow", "EdmDetail", "ContextualEdmDetail",
    "EdmAnalysesSection",
    "AdoptableEdm", "AdoptablePage",
    "AdoptResult",
    "PENDING", "IMPORTING", "READY", "ERROR",
    "STATUSES", "TRANSIENT_STATUSES",
    "check_name_collision", "import_edm", "list_edms", "get_edm",
    "list_adoptable_edms", "adopt_edms",
    "latest_import_error", "latest_backfill_status", "latest_backfill_statuses",
    "get_edm_detail",
    "get_contextual_edm_detail", "get_edm_analyses",
    "sync_detail", "sync_contextual_detail",
    "retry_import", "replace_source_file", "mark_importing", "mark_error",
    "backfill_on_terminal",
]
