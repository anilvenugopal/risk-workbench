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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
from db import execute

if TYPE_CHECKING:
    from app.services.geohaz_service import CellState, LatestLookup


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
    geohaz_eligible: bool = False
    geohaz_state: CellState | None = None
    geohaz_latest: LatestLookup | None = None


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


def update_exposure_metrics(conn, *, portfolio_id: Any, metrics: dict) -> None:
    """Replace Risk Modeler's portfolio metadata while retaining its summary."""
    row = conn.execute(text(
        "SELECT exposure_detail FROM irp_portfolio WHERE id = :id"
    ), {"id": str(portfolio_id)}).mappings().first()
    if row is None:
        return
    current = _parse_json_dict(row["exposure_detail"], "exposure_detail") or {}
    snapshot = {"metrics": metrics, "summary": current.get("summary")}
    conn.execute(text(
        "UPDATE irp_portfolio SET exposure_detail = :detail, updated_at = :now "
        "WHERE id = :id"
    ), {"detail": _json(snapshot), "now": _utcnow(), "id": str(portfolio_id)})


def list_portfolios(*, edm_id: Any) -> list[PortfolioRow]:
    """Every portfolio of an EDM (read model), each with its parsed
    ``exposure_detail`` (``None`` → graceful empty). No row scoping (Article 6);
    read-only — no create/edit/split (Iteration 4)."""
    rows = execute(
        "SELECT id, edm_id, name, irp_id, exposure_detail, as_of "
        "FROM irp_portfolio WHERE edm_id = :e AND deleted_at IS NULL "
        "ORDER BY name",
        {"e": str(edm_id)}, connection="WORKBENCH")
    return [PortfolioRow(
        id=_uid(r["id"]), edm_id=_uid(r["edm_id"]), name=r["name"],
        irp_id=r["irp_id"],
        exposure_detail=_parse_json_dict(r["exposure_detail"], "exposure_detail"),
        as_of=r["as_of"]) for r in rows]


__all__ = ["PortfolioRow", "upsert_portfolio_detail", "update_exposure_metrics",
           "prune_missing", "list_portfolios"]
