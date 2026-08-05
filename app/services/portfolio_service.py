"""Portfolio service — per-portfolio detail: worker-side upsert + read model (US1).

``irp_portfolio`` is a thin identity/lineage record plus a **JSON snapshot cache**
column (``exposure_detail`` — research R2): the ``backfill_edm_detail`` worker
stores Risk Modeler's per-portfolio figures verbatim and stamps ``as_of`` (the
FR-052 trust signal); the web layer only ever reads the stored snapshot. The
upsert is **idempotent** on ``UNIQUE(edm_id, irp_id)`` with an ``(edm_id, name)``
match fallback (RM portfolio names are unique within an EDM) — a re-backfill
overwrites the snapshot in place, never inserting a duplicate (FR-004), and
prunes (soft-deletes) rows RM's enumeration no longer returns.

Read-only this iteration: no create/edit/split/filter (Iteration 4). No row
scoping anywhere (Article 6). Portability matches the sibling services: app-side
UUIDs bound as ``str``, app-supplied UTC timestamps, no dialect-only SQL.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.services._common import (
    _json,
    _parse_json_dict,
    _snapshot_prune,
    _snapshot_upsert,
    _txn,
    _uid,
    _utcnow,
)
from db import execute, execute_one, get_connection, is_unique_violation

logger = logging.getLogger(__name__)


@dataclass
class PortfolioRow:
    """One portfolio of an EDM with its parsed snapshot (``None`` ⇒ not yet
    backfilled → the caller renders the graceful empty state)."""
    id: str
    edm_id: str
    name: str
    irp_id: str | None
    exposure_detail: dict | None
    as_of: Any
    # US3 (FR-037): the broker analyses LINKED to this portfolio (bucketed by
    # the R9 read-time resolution in edm_service.get_edm_detail) — the inline
    # panel; empty for group/unresolved analyses (standalone-only) and for
    # every caller that doesn't attach them.
    analyses: list = field(default_factory=list)
    # Spec 005 (FR-012), attached by edm_service.get_edm_detail: the live
    # breakout run on this portfolio (breakout_service.BreakoutFlight, None
    # when idle) and the durable failure lines from the latest terminal run
    # per dimension (list of breakout_service.BreakoutRowError).
    breakout_flight: Any = None
    breakout_errors: list = field(default_factory=list)
    # Spec 005 US3 (FR-014): breakout lineage for the row badge
    # "↳ from {source} · {dimension label}: {value}". All None/absent for
    # broker-arrived portfolios. source_name is the IMMEDIATE source only —
    # chained lineage is never rendered as a chain.
    source_portfolio_id: str | None = None
    source_name: str | None = None
    breakout_dimension_code: str | None = None
    breakout_dimension_label: str | None = None
    breakout_value: str | None = None
    # Display label for breakout_value (Admin1Name for a state code), resolved
    # at read time from the SOURCE portfolio's stored summary — never stored,
    # never a filter input; None falls back to the code in the template
    # (P-12 as revised 2026-08-05).
    breakout_value_label: str | None = None


# The two in-place overwrite paths of the idempotent upsert. The irp_id match is
# primary (UNIQUE(edm_id, irp_id)); the name match is the fallback for a row
# first written without its RM id (it backfills irp_id) — data-model §2.
_UPDATE_BY_IRP = """
    UPDATE irp_portfolio
    SET name = :name, exposure_detail = :snap, as_of = :asof, updated_at = :now
    WHERE edm_id = :edm AND irp_id = :irp
"""
_UPDATE_BY_NAME = """
    UPDATE irp_portfolio
    SET irp_id = :irp, exposure_detail = :snap, as_of = :asof, updated_at = :now
    WHERE edm_id = :edm AND name = :name
"""
_INSERT = """
    INSERT INTO irp_portfolio (id, edm_id, name, irp_id, exposure_detail, as_of,
        inserted_at, updated_at)
    VALUES (:id, :edm, :name, :irp, :snap, :asof, :now, :now)
"""


def upsert_portfolio_detail(*, edm_id: Any, irp_id: str | None, name: str,
                            exposure_detail: dict, as_of: Any,
                            conn=None) -> None:
    """Worker-side (``backfill_edm_detail``). Insert the ``irp_portfolio`` row or
    OVERWRITE ``exposure_detail`` (verbatim JSON) + ``as_of`` in place — never a
    duplicate (R2/FR-004). Runs in the caller's transaction when ``conn`` is
    given, else in its own short one (Article 7) — the caller must never hold a
    transaction across a gateway round-trip (Article 11)."""
    params = {
        "id": str(uuid.uuid4()), "edm": str(edm_id),
        "irp": (str(irp_id) if irp_id is not None else None), "name": name,
        "snap": _json(exposure_detail), "asof": as_of, "now": _utcnow(),
    }
    with _txn(conn) as working:
        _snapshot_upsert(working, params, update_by_irp=_UPDATE_BY_IRP,
                         update_by_name=_UPDATE_BY_NAME, insert=_INSERT)


def prune_missing(*, edm_id: Any, seen: list[tuple[str | None, str]],
                  now: Any, conn=None) -> int:
    """Worker-side (``backfill_edm_detail``), only after a SUCCESSFUL full
    portfolio enumeration: soft-delete this EDM's rows RM no longer returned
    (deleted RM-side) and resurrect pruned rows it returned again. ``seen`` is
    the enumerated (irp_id, name) set — including portfolios whose exposure
    read later fails (existence comes from the enumeration, not the detail
    read). Returns the rows pruned."""
    with _txn(conn) as working:
        return _snapshot_prune(working, table="irp_portfolio", edm_id=edm_id,
                               seen=seen, now=now)


@dataclass
class EdmAggregate:
    """The quick-orientation EDM rollup (US4 — FR-040/FR-041/FR-042), derived
    from the per-portfolio snapshots at read time (R4): SUM counts (record
    volume == locations, FR-013) + TIV, UNION perils/lines of business,
    COMBINE geography (states) + the currency set. Never stored; never a
    request-path fetch. The three counts and the TIV are nullable (no snapshot
    carried them); the collections are always present, possibly empty."""
    portfolio_count: int
    with_snapshot: int                     # portfolios that contributed figures
    locations: int | None
    accounts: int | None
    policies: int | None
    perils: list[str]                      # union, sorted
    lines_of_business: list[str]
    states: list[str]
    currencies: list[str]
    total_tiv: float | None                # sum of per-portfolio totals


def aggregate_exposure(portfolios: list[PortfolioRow]) -> EdmAggregate | None:
    """Derive the EDM-aggregate (R4) — a pure function over the already-fetched
    snapshots (no DB, no Risk Modeler). ``None`` when no portfolio carries a
    snapshot → the caller renders the pending/unavailable state (FR-042/FR-043).
    Reads both snapshot shapes defensively: the namespaced {"metrics","summary"}
    form and the flat pre-2026-07-23 /metrics payload."""
    snaps = [p.exposure_detail for p in portfolios if p.exposure_detail]
    if not snaps:
        return None

    counts: dict[str, int | None] = {"totalLocations": None,
                                     "totalAccounts": None,
                                     "totalPolicies": None}
    perils: set[str] = set()
    lines_of_business: set[str] = set()
    states: set[str] = set()
    currencies: set[str] = set()
    total_tiv: float | None = None
    for snap in snaps:
        metrics = snap.get("metrics") if isinstance(snap.get("metrics"), dict) \
            else snap  # flat fallback (pre-capability rows)
        for key in counts:
            value = metrics.get(key)
            if isinstance(value, (int, float)):
                counts[key] = int(value) + (counts[key] or 0)
        perils.update(p.strip() for p in str(metrics.get("perilsExposed") or "")
                      .split(",") if p.strip())
        summary = snap.get("summary") if isinstance(snap.get("summary"), dict) \
            else {}
        lines_of_business.update(
            v for v in (summary.get("lines_of_business") or []) if v)
        # Geography displays the state's label (Admin1Name) where the summary
        # carries one, falling back to the code — P-12 as revised 2026-08-05.
        # A pre-005 summary has no breakout_values: its states list renders
        # verbatim (the old name/code mix).
        container = summary.get("breakout_values")
        entries = (container.get("state")
                   if isinstance(container, dict) else None)
        if isinstance(entries, list) and entries:
            states.update(e.get("label") or e.get("value") for e in entries
                          if isinstance(e, dict)
                          and (e.get("label") or e.get("value")))
        else:
            states.update(v for v in (summary.get("states") or []) if v)
        currencies.update(v for v in (summary.get("currencies") or []) if v)
        tiv = summary.get("total_tiv")
        if isinstance(tiv, (int, float)):
            total_tiv = float(tiv) + (total_tiv or 0.0)

    return EdmAggregate(
        portfolio_count=len(portfolios),
        with_snapshot=len(snaps),
        locations=counts["totalLocations"],
        accounts=counts["totalAccounts"],
        policies=counts["totalPolicies"],
        perils=sorted(perils),
        lines_of_business=sorted(lines_of_business),
        states=sorted(states),
        currencies=sorted(currencies),
        total_tiv=total_tiv,
    )


# ── Breakout lineage writes (spec 005 — R3/R7, contracts/data-access.md §2) ─────
# One write path enforces the integrity rule: the three lineage columns are set
# together, the source portfolio is live in the SAME EDM, and inserted_by is the
# confirming analyst (first population of that column on this table). The
# filtered unique index uq_irp_portfolio_breakout is the real idempotency
# guarantee — a race duplicate surfaces as a constraint violation and is
# reported as skipped (created=False), never raised (FR-011).

@dataclass(frozen=True)
class GeneratedWrite:
    """Outcome of ``insert_generated``/``adopt_generated``. ``created=False``
    means a concurrent writer already owns the lineage key — the worker records
    that entry as ``skipped_existing``."""
    portfolio_id: str
    created: bool


_UPDATE_GENERATED_BY_EDM_IRP = """
    UPDATE irp_portfolio
    SET name = :name, source_portfolio_id = :src, breakout_dimension_code = :dim,
        breakout_value = :val, inserted_by = COALESCE(inserted_by, :by),
        updated_at = :now, updated_by = :by
    WHERE edm_id = :edm AND irp_id = :irp AND deleted_at IS NULL
"""
_INSERT_GENERATED = """
    INSERT INTO irp_portfolio (id, edm_id, name, irp_id, source_portfolio_id,
        breakout_dimension_code, breakout_value, inserted_at, updated_at,
        inserted_by, updated_by)
    VALUES (:id, :edm, :name, :irp, :src, :dim, :val, :now, :now, :by, :by)
"""
_SELECT_GENERATED = """
    SELECT id, edm_id, name, irp_id, exposure_detail, as_of
    FROM irp_portfolio
    WHERE source_portfolio_id = :src AND breakout_dimension_code = :dim
      AND breakout_value = :val AND deleted_at IS NULL
"""


def find_generated(source_portfolio_id: Any, dimension_code: str,
                   value: str) -> dict | None:
    """The live generated portfolio for (source, dimension, value), or ``None``.
    The worker's per-entry skip check (FR-011); soft-deleted rows never match."""
    row = execute_one(_SELECT_GENERATED,
                      {"src": _uid(source_portfolio_id), "dim": dimension_code,
                       "val": value}, connection="WORKBENCH")
    if row is not None:
        row = dict(row)
        row["id"] = _uid(row["id"])
    return row


def _write_generated(edm_id: Any, *, name: str, irp_id: str,
                     source_portfolio_id: Any, dimension_code: str, value: str,
                     actor_id: Any) -> GeneratedWrite:
    if not (source_portfolio_id and dimension_code and value):
        raise ValueError(
            "breakout lineage integrity: source portfolio, dimension, and value "
            "must be set together")
    source = execute_one(
        "SELECT edm_id, deleted_at FROM irp_portfolio WHERE id = :s",
        {"s": _uid(source_portfolio_id)}, connection="WORKBENCH")
    if source is None or source["deleted_at"] is not None:
        raise ValueError("breakout lineage integrity: source portfolio missing "
                         "or deleted")
    if _uid(source["edm_id"]) != _uid(edm_id):
        raise ValueError("breakout lineage integrity: source portfolio is not "
                         "in the same EDM")

    params = {
        "id": str(uuid.uuid4()), "edm": _uid(edm_id), "name": name,
        "irp": str(irp_id), "src": _uid(source_portfolio_id),
        "dim": dimension_code, "val": value, "now": _utcnow(),
        "by": (_uid(actor_id) if actor_id is not None else None),
    }
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            try:
                with conn.begin_nested():
                    # The row may already exist WITHOUT lineage — a backfill
                    # enumerated the RM portfolio before this run recorded it.
                    # Stamp the lineage in place rather than violating
                    # UNIQUE(edm_id, irp_id).
                    updated = conn.execute(
                        text(_UPDATE_GENERATED_BY_EDM_IRP), params).rowcount
                    if updated:
                        existing = conn.execute(text(
                            "SELECT id FROM irp_portfolio "
                            "WHERE edm_id = :edm AND irp_id = :irp"
                        ), params).scalar()
                        return GeneratedWrite(portfolio_id=_uid(existing),
                                              created=True)
                    conn.execute(text(_INSERT_GENERATED), params)
                    return GeneratedWrite(portfolio_id=params["id"], created=True)
            except Exception as exc:  # noqa: BLE001 — a UNIQUE race is a skip, not a failure
                if not is_unique_violation(exc):
                    raise
            # uq_irp_portfolio_breakout says a live row already owns this
            # lineage key (concurrent identical breakout / redelivered job) —
            # the skipped_existing outcome, never an error (FR-011).
            row = conn.execute(text(_SELECT_GENERATED), {
                "src": params["src"], "dim": params["dim"], "val": params["val"],
            }).mappings().first()
            if row is None:
                raise RuntimeError(
                    "breakout lineage write lost a UNIQUE race but no live row "
                    "matches the lineage key — refusing to guess")
            return GeneratedWrite(portfolio_id=_uid(row["id"]), created=False)


def insert_generated(edm_id: Any, *, name: str, irp_id: str,
                     source_portfolio_id: Any, dimension_code: str, value: str,
                     actor_id: Any) -> GeneratedWrite:
    """Persist a freshly created sub-portfolio with its lineage (FR-009). Called
    by the breakout worker immediately after ``create_sub_portfolio`` returns —
    RM call first, row second (worker-poller.md ordering)."""
    result = _write_generated(edm_id, name=name, irp_id=irp_id,
                              source_portfolio_id=source_portfolio_id,
                              dimension_code=dimension_code, value=value,
                              actor_id=actor_id)
    logger.info("generated portfolio %s (%s=%s) recorded for source %s%s",
                name, dimension_code, value, source_portfolio_id,
                "" if result.created else " (already present — skipped)")
    return result


def adopt_generated(edm_id: Any, *, name: str, irp_id: str,
                    source_portfolio_id: Any, dimension_code: str, value: str,
                    actor_id: Any) -> GeneratedWrite:
    """Same write as ``insert_generated`` for a sub-portfolio Risk Modeler
    already holds (resolved by ``portfolioNumber`` — R7/T-07): claims the
    existing (edm_id, irp_id) row in place when a backfill already captured it,
    inserts otherwise. Logged as an adoption."""
    result = _write_generated(edm_id, name=name, irp_id=irp_id,
                              source_portfolio_id=source_portfolio_id,
                              dimension_code=dimension_code, value=value,
                              actor_id=actor_id)
    logger.info("existing RM portfolio %s (irp_id=%s, %s=%s) adopted for "
                "source %s%s", name, irp_id, dimension_code, value,
                source_portfolio_id,
                "" if result.created else " (already present — skipped)")
    return result


def list_portfolios(*, edm_id: Any) -> list[PortfolioRow]:
    """Every portfolio of an EDM (read model), each with its parsed
    ``exposure_detail`` (``None`` → graceful empty) and its breakout lineage
    (FR-014): the immediate source portfolio's name and the dimension label
    joined in, ordering unchanged by name — grouping and indent stay display
    concerns. No row scoping (Article 6)."""
    rows = execute(
        "SELECT p.id, p.edm_id, p.name, p.irp_id, p.exposure_detail, p.as_of, "
        "p.source_portfolio_id, s.name AS source_name, "
        "p.breakout_dimension_code, k.label AS breakout_dimension_label, "
        "p.breakout_value "
        "FROM irp_portfolio p "
        "LEFT JOIN irp_portfolio s ON p.source_portfolio_id = s.id "
        "LEFT JOIN breakout_dimension_kind k "
        "  ON p.breakout_dimension_code = k.code "
        "WHERE p.edm_id = :e AND p.deleted_at IS NULL "
        "ORDER BY p.name",
        {"e": str(edm_id)}, connection="WORKBENCH")
    portfolios = [PortfolioRow(
        id=_uid(r["id"]), edm_id=_uid(r["edm_id"]), name=r["name"],
        irp_id=r["irp_id"],
        exposure_detail=_parse_json_dict(r["exposure_detail"], "exposure_detail"),
        as_of=r["as_of"],
        source_portfolio_id=(_uid(r["source_portfolio_id"])
                             if r["source_portfolio_id"] is not None else None),
        source_name=r["source_name"],
        breakout_dimension_code=r["breakout_dimension_code"],
        breakout_dimension_label=r["breakout_dimension_label"],
        breakout_value=r["breakout_value"]) for r in rows]
    _resolve_breakout_value_labels(portfolios)
    return portfolios


def _resolve_breakout_value_labels(portfolios: list[PortfolioRow]) -> None:
    """Attach each generated row's display label — the ``label`` stored beside
    its ``breakout_value`` in the SOURCE portfolio's summary (Admin1Name for a
    state code; P-12 as revised 2026-08-05). Generated portfolios live in the
    same EDM as their source, so the lookup stays inside the fetched list. Any
    miss (source pruned, summary rewritten without the value, no label yet)
    leaves ``None`` and the template falls back to the code."""
    by_id = {p.id: p for p in portfolios}
    for p in portfolios:
        source = by_id.get(p.source_portfolio_id or "")
        if source is None or not p.breakout_dimension_code or not p.breakout_value:
            continue
        summary = (source.exposure_detail or {}).get("summary")
        container = (summary.get("breakout_values")
                     if isinstance(summary, dict) else None)
        entries = (container.get(p.breakout_dimension_code)
                   if isinstance(container, dict) else None)
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("value") == p.breakout_value:
                label = entry.get("label")
                p.breakout_value_label = (label if isinstance(label, str)
                                          and label else None)
                break


__all__ = ["PortfolioRow", "EdmAggregate", "GeneratedWrite",
           "upsert_portfolio_detail", "prune_missing", "list_portfolios",
           "aggregate_exposure", "insert_generated", "adopt_generated",
           "find_generated"]
