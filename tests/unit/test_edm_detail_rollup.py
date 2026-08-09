"""Unit tests for the derived EDM-aggregate rollup (spec 004 US4, T044).

``portfolio_service.aggregate_exposure`` derives the quick-orientation rollup
from the stored per-portfolio snapshots (research R4): SUM the counts + record
volume + TIV, UNION perils/lines of business, COMBINE geography (states) + the
currency set, count portfolios. Pure function — no DB, no Risk Modeler, never
stored (FR-042). ``None`` when no portfolio carries a snapshot → the caller
renders the pending state (FR-043). ``edm_service.get_edm_detail`` surfacing
is covered in ``tests/sqlserver/test_edm_detail_rollup.py``.
"""

from __future__ import annotations

import uuid

from app.services import portfolio_service
from app.services._common import _utcnow

SNAP_A = {
    "metrics": {"totalLocations": 8240, "totalAccounts": 1120,
                "totalPolicies": 1180, "perilsExposed": "WS, EQ"},
    "summary": {"portfolio_name": "A", "total_tiv": 2.8e9,
                "currencies": ["USD"], "states": ["FL", "TX"],
                "lines_of_business": ["Commercial"]},
}
SNAP_B = {
    "metrics": {"totalLocations": 3900, "totalAccounts": 720,
                "totalPolicies": 760, "perilsExposed": "WS, FL"},
    "summary": {"portfolio_name": "B", "total_tiv": 1.3e9,
                "currencies": ["USD", "CAD"], "states": ["TX", "NY"],
                "lines_of_business": ["Commercial", "Residential"]},
}


def _row(name: str, snap: dict | None) -> portfolio_service.PortfolioRow:
    return portfolio_service.PortfolioRow(
        id=str(uuid.uuid4()), edm_id="e", name=name, irp_id=None,
        exposure_detail=snap, as_of=_utcnow() if snap else None)


def test_aggregate_sums_unions_and_combines():
    agg = portfolio_service.aggregate_exposure([_row("A", SNAP_A),
                                                _row("B", SNAP_B)])
    assert agg is not None
    assert agg.portfolio_count == 2
    assert agg.locations == 12140          # summed; record volume == locations
    assert agg.accounts == 1840
    assert agg.policies == 1940
    assert agg.perils == ["EQ", "FL", "WS"]          # union, sorted
    assert agg.lines_of_business == ["Commercial", "Residential"]
    assert agg.states == ["FL", "NY", "TX"]          # combined geography
    assert agg.currencies == ["CAD", "USD"]          # currency set
    assert agg.total_tiv == 4.1e9                    # summed across portfolios


def test_aggregate_none_when_no_snapshot():
    assert portfolio_service.aggregate_exposure([]) is None
    assert portfolio_service.aggregate_exposure(
        [_row("A", None), _row("B", None)]) is None  # rows but nothing backfilled


def test_aggregate_partial_snapshots_still_roll_up():
    # one portfolio backfilled, one not — the rollup uses what exists (FR-043)
    agg = portfolio_service.aggregate_exposure([_row("A", SNAP_A),
                                                _row("B", None)])
    assert agg is not None
    assert agg.portfolio_count == 2
    assert agg.with_snapshot == 1
    assert agg.locations == 8240


def test_aggregate_flat_precapability_shape_and_missing_summary():
    # pre-2026-07-23 rows hold the flat /metrics payload; summary may be null
    flat = {"totalLocations": 100, "totalAccounts": 10, "totalPolicies": 12,
            "perilsExposed": "EQ"}
    agg = portfolio_service.aggregate_exposure(
        [_row("A", flat), _row("B", {"metrics": {}, "summary": None})])
    assert agg is not None
    assert agg.locations == 100
    assert agg.perils == ["EQ"]
    assert agg.currencies == []            # graceful — no summary anywhere
    assert agg.total_tiv is None
