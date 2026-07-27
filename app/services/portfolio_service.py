"""Portfolio service — per-portfolio detail: worker-side upsert + read model (US1).

``irp_portfolio`` is a thin identity/lineage record plus a **JSON snapshot cache**
column (``exposure_detail`` — research R2): the ``backfill_edm_detail`` worker
stores Risk Modeler's per-portfolio figures verbatim and stamps ``as_of`` (the
FR-052 trust signal); the web layer only ever reads the stored snapshot. The
upsert is **idempotent** on ``UNIQUE(edm_id, irp_id)`` with an ``(edm_id, name)``
match fallback (RM portfolio names are unique within an EDM) — a re-backfill
overwrites the snapshot in place, never inserting a duplicate (FR-004).

Read-only this iteration: no create/edit/split/filter (Iteration 4). No row
scoping anywhere (Article 6). Portability matches the sibling services: app-side
UUIDs bound as ``str``, app-supplied UTC timestamps, no dialect-only SQL.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services._common import _json, _snapshot_upsert, _txn, _uid, _utcnow
from db import execute

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


def _parse_snapshot(raw: Any) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("unparseable exposure_detail snapshot — rendering empty")
        return None
    return parsed if isinstance(parsed, dict) else None


@dataclass
class EdmAggregate:
    """The quick-orientation EDM rollup (US4 — FR-040/FR-041/FR-042), derived
    from the per-portfolio snapshots at read time (R4): SUM counts (record
    volume == locations, FR-013), UNION perils/sub-perils, COMBINE geography +
    the currency set. Never stored; never a request-path fetch."""
    portfolio_count: int
    with_snapshot: int                     # portfolios that contributed figures
    locations: int | None = None
    accounts: int | None = None
    policies: int | None = None
    perils: list[str] | None = None               # union, sorted
    sub_perils: list[str] | None = None
    states: list[str] | None = None
    countries: list[str] | None = None
    currencies: list[str] | None = None
    tiv_by_currency: dict[str, float] | None = None  # per-currency sums (never cross-summed)


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
    sub_perils: set[str] = set()
    states: set[str] = set()
    countries: set[str] = set()
    currencies: set[str] = set()
    tiv: dict[str, float] = {}
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
        sub_perils.update(v for v in (summary.get("sub_perils") or []) if v)
        states.update(v for v in (summary.get("states") or []) if v)
        countries.update(v for v in (summary.get("countries") or []) if v)
        currencies.update(v for v in (summary.get("currencies") or []) if v)
        for cur, amount in (summary.get("tiv_by_currency") or {}).items():
            if isinstance(amount, (int, float)):
                tiv[cur] = tiv.get(cur, 0.0) + float(amount)

    return EdmAggregate(
        portfolio_count=len(portfolios),
        with_snapshot=len(snaps),
        locations=counts["totalLocations"],
        accounts=counts["totalAccounts"],
        policies=counts["totalPolicies"],
        perils=sorted(perils),
        sub_perils=sorted(sub_perils),
        states=sorted(states),
        countries=sorted(countries),
        currencies=sorted(currencies),
        tiv_by_currency=tiv,
    )


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
        irp_id=r["irp_id"], exposure_detail=_parse_snapshot(r["exposure_detail"]),
        as_of=r["as_of"]) for r in rows]


__all__ = ["PortfolioRow", "EdmAggregate", "upsert_portfolio_detail",
           "list_portfolios", "aggregate_exposure"]
