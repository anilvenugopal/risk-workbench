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
    # Spec 005 (FR-012), attached by edm_service.get_edm_detail: the live
    # breakout run on this portfolio (breakout_service.BreakoutFlight, None
    # when idle) and the durable failure lines from the latest terminal run
    # per dimension (list of breakout_service.BreakoutRowError).
    breakout_flight: Any = None
    breakout_errors: list = field(default_factory=list)
    # Spec 005 US3 (FR-014 as revised 2026-08-11): breakout lineage for the
    # expanded row's base-portfolio + criteria block. All None/absent for
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
    # (P-12 as revised 2026-08-05). For a custom-group row (T-12) it is the
    # group's label, read off the joined breakout_group row.
    breakout_value_label: str | None = None
    # Custom-group lineage (spec 005 follow-on T-12), joined from
    # breakout_group; None for quick-mode and broker-arrived rows. filters is
    # the parsed member-filter dict the criteria line renders.
    breakout_group_label: str | None = None
    breakout_group_filters: dict | None = None


# The two in-place overwrite paths of the idempotent upsert. The irp_id match is
# primary (UNIQUE(edm_id, irp_id)); the name match is the fallback for a row
# first written without its RM id (it backfills irp_id) — data-model §2. The
# name match is live-only (T-16): a soft-deleted sub-portfolio's name can be
# regenerated for a NEW RM portfolio, and stamping the dead row's irp_id would
# leave the enumerated portfolio without a live row.
_UPDATE_BY_IRP = """
    UPDATE irp_portfolio
    SET name = :name, exposure_detail = :snap, as_of = :asof, updated_at = :now
    WHERE edm_id = :edm AND irp_id = :irp
"""
_UPDATE_BY_NAME = """
    UPDATE irp_portfolio
    SET irp_id = :irp, exposure_detail = :snap, as_of = :asof, updated_at = :now
    WHERE edm_id = :edm AND name = :name AND deleted_at IS NULL
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
    read). Resurrect-by-name is restricted to non-generated rows (T-16): a
    re-breakout regenerates a deleted sub-portfolio's exact name for a NEW RM
    portfolio, and reviving the dead row by that name would put two live rows
    on one uq_irp_portfolio_breakout key. A dead generated row still
    resurrects on its irp_id match. Returns the rows pruned."""
    with _txn(conn) as working:
        return _snapshot_prune(working, table="irp_portfolio", edm_id=edm_id,
                               seen=seen, now=now,
                               name_resurrect_filter="AND source_portfolio_id IS NULL")


# ── Breakout lineage writes (spec 005 — R3/R7, contracts/data-access.md §2) ─────
# One write path enforces the integrity rule: the three lineage columns are set
# together, the source portfolio is live in the SAME EDM, and inserted_by is the
# confirming analyst (first population of that column on this table). The
# filtered unique index uq_irp_portfolio_breakout is the real idempotency
# guarantee — a race duplicate surfaces as a constraint violation and is
# reported as skipped (created=False), never raised (FR-011).

@dataclass(frozen=True)
class GeneratedWrite:
    """Outcome of ``save_generated_portfolio``. ``created=False``
    means a concurrent writer already owns the lineage key — the worker records
    that entry as ``skipped_existing``."""
    portfolio_id: str
    created: bool


_SELECT_BY_EDM_IRP = """
    SELECT id, deleted_at, source_portfolio_id, breakout_dimension_code,
        breakout_value
    FROM irp_portfolio
    WHERE edm_id = :edm AND irp_id = :irp
"""
# Stamps the full identity + lineage onto an existing row and clears its
# soft-delete (T-16). Serves both claim paths: a (edm_id, irp_id) match gets
# its lineage stamped (irp_id/deleted_at writes are no-ops there when the row
# is live), and a soft-deleted lineage-triple match gets the NEW RM identity
# and comes back to life on the same row.
_RECLAIM_GENERATED_BY_ID = """
    UPDATE irp_portfolio
    SET deleted_at = NULL, name = :name, irp_id = :irp,
        source_portfolio_id = :src, breakout_dimension_code = :dim,
        breakout_value = :val, breakout_group_id = :grp,
        inserted_by = COALESCE(inserted_by, :by),
        updated_at = :now, updated_by = :by
    WHERE id = :row_id
"""
_INSERT_GENERATED = """
    INSERT INTO irp_portfolio (id, edm_id, name, irp_id, source_portfolio_id,
        breakout_dimension_code, breakout_value, breakout_group_id,
        inserted_at, updated_at, inserted_by, updated_by)
    VALUES (:id, :edm, :name, :irp, :src, :dim, :val, :grp, :now, :now,
        :by, :by)
"""
_SELECT_GENERATED = """
    SELECT id, edm_id, name, irp_id, exposure_detail, as_of
    FROM irp_portfolio
    WHERE source_portfolio_id = :src AND breakout_dimension_code = :dim
      AND breakout_value = :val AND deleted_at IS NULL
"""
_SELECT_GENERATED_ANY = """
    SELECT id, deleted_at
    FROM irp_portfolio
    WHERE source_portfolio_id = :src AND breakout_dimension_code = :dim
      AND breakout_value = :val
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


def save_generated_portfolio(
        edm_id: Any, *, name: str, irp_id: str, source_portfolio_id: Any,
        dimension_code: str, value: str, actor_id: Any,
        group_id: Any = None) -> GeneratedWrite:
    """Persist a sub-portfolio with its lineage (FR-009) — the one write for
    both worker branches: a freshly created RM portfolio and an adoption of one
    Risk Modeler already holds (resolved by ``portfolioNumber`` — R7/T-07).
    Called immediately after the RM call returns — RM call first, row second
    (worker-poller.md ordering). Claims an existing (edm_id, irp_id) or
    lineage-triple row in place (``_claim_existing``). ``group_id`` links a
    custom-group portfolio to its ``breakout_group`` row (T-12); its
    ``breakout_value`` is the group_key."""
    if not (source_portfolio_id and dimension_code and value):
        raise ValueError(
            "breakout lineage integrity: source portfolio, dimension, and value "
            "must be set together")
    if (dimension_code == "custom") != (group_id is not None):
        raise ValueError(
            "breakout lineage integrity: dimension 'custom' and a "
            "breakout_group row go together, and only with each other")
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
        "dim": dimension_code, "val": value, "grp": _uid(group_id),
        "now": _utcnow(),
        "by": (_uid(actor_id) if actor_id is not None else None),
    }
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            try:
                with conn.begin_nested():
                    claimed = _claim_existing(conn, params)
                    if claimed is not None:
                        return claimed
                    conn.execute(text(_INSERT_GENERATED), params)
                    return GeneratedWrite(portfolio_id=params["id"], created=True)
            except Exception as exc:  # noqa: BLE001 — a UNIQUE race is a skip, not a failure
                if not is_unique_violation(exc):
                    raise
                # uq_irp_portfolio_breakout or uq_irp_portfolio_edm_irp says
                # another writer now holds one of this write's identities
                # (concurrent identical breakout / redelivered job / raced
                # backfill) — re-resolve against what the table holds now,
                # never an error for the healthy race (FR-011). Its own
                # savepoint: is_unique_violation is true for any
                # IntegrityError, so this arm also sees failures no re-resolve
                # can explain, and the claim may violate in its turn.
                with conn.begin_nested():
                    claimed = _claim_existing(conn, params)
                if claimed is None:
                    raise
                return claimed


def _claim_existing(conn, params: dict) -> GeneratedWrite | None:
    """Resolve the write onto a row the table already holds, live or
    soft-deleted — the (edm_id, irp_id) identity first, the lineage triple
    second. ``None`` means no row holds either identity: the caller inserts.

    A match carrying a different lineage raises rather than being reassigned,
    so a generated portfolio cannot move between breakout keys. A soft-deleted
    lineage-triple match is reclaimed in place (T-16) — a second row would be a
    ghost the next sync could revive into a duplicate live lineage key."""
    existing = conn.execute(text(_SELECT_BY_EDM_IRP), params).mappings().first()
    if existing is not None:
        held_source, held_dim, held_val = (
            _uid(existing["source_portfolio_id"]),
            existing["breakout_dimension_code"],
            existing["breakout_value"])
        if held_source is not None and (
                held_source, held_dim, held_val) != (
                params["src"], params["dim"], params["val"]):
            raise ValueError(
                "breakout lineage integrity: Risk Modeler portfolio "
                f"{params['irp']} is already the {held_dim}={held_val} "
                f"breakout of source portfolio {held_source}")
        conn.execute(text(_RECLAIM_GENERATED_BY_ID),
                     dict(params, row_id=existing["id"]))
        return GeneratedWrite(portfolio_id=_uid(existing["id"]), created=True)
    rows = conn.execute(text(_SELECT_GENERATED_ANY), params).mappings().all()
    live = next((r for r in rows if r["deleted_at"] is None), None)
    if live is not None:
        return GeneratedWrite(portfolio_id=_uid(live["id"]), created=False)
    if rows:
        conn.execute(text(_RECLAIM_GENERATED_BY_ID),
                     dict(params, row_id=rows[0]["id"]))
        return GeneratedWrite(portfolio_id=_uid(rows[0]["id"]), created=True)
    return None


def list_portfolios(*, edm_id: Any) -> list[PortfolioRow]:
    """Every portfolio of an EDM (read model), each with its parsed
    ``exposure_detail`` (``None`` → graceful empty) and its breakout lineage
    (FR-014): the immediate source portfolio's name and the dimension label
    joined in. Oldest ``inserted_at`` first, so a newly created sub-portfolio
    appears at the bottom of the Portfolios table; name breaks the tie between
    portfolios one backfill recorded together. No row scoping (Article 6)."""
    rows = execute(
        "SELECT p.id, p.edm_id, p.name, p.irp_id, p.exposure_detail, p.as_of, "
        "p.source_portfolio_id, s.name AS source_name, "
        "p.breakout_dimension_code, k.label AS breakout_dimension_label, "
        "p.breakout_value, "
        "bg.label AS breakout_group_label, bg.filters AS breakout_group_filters "
        "FROM irp_portfolio p "
        "LEFT JOIN irp_portfolio s ON p.source_portfolio_id = s.id "
        "LEFT JOIN breakout_dimension_kind k "
        "  ON p.breakout_dimension_code = k.code "
        "LEFT JOIN breakout_group bg ON p.breakout_group_id = bg.id "
        "WHERE p.edm_id = :e AND p.deleted_at IS NULL "
        "ORDER BY p.inserted_at, p.name",
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
        breakout_value=r["breakout_value"],
        breakout_group_label=r["breakout_group_label"],
        breakout_group_filters=_parse_json_dict(
            r["breakout_group_filters"], "breakout_group.filters")) for r in rows]
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
        # A custom-group row's display label IS the group label (one source of
        # truth — the joined breakout_group row); its breakout_value is the
        # group_key and never appears in any summary (T-12).
        if p.breakout_dimension_code == "custom":
            p.breakout_value_label = p.breakout_group_label
            continue
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


__all__ = ["PortfolioRow", "GeneratedWrite", "upsert_portfolio_detail",
           "prune_missing", "list_portfolios", "save_generated_portfolio",
           "find_generated"]
