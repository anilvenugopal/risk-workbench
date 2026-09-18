"""Sandbox check of the treaties endpoint the TY export relies on (spec 016
T-04): ``GET /platform/riskdata/v1/analyses/{id}/treaties`` through
``irp_gateway.list_analysis_treaties`` for a finished analysis run with
treaties. Set ``IRP_TEST_TREATY_ANALYSIS_ID`` to that analysis's Risk Modeler
analysis ID; run it once against a group analysis ID to close plan O-01.

Run: ``make shell`` then ``uv run pytest tests/irp --run-irp -k treaty``.
"""

from __future__ import annotations

import os

import pytest

from app.services import irp_gateway

_ANALYSIS_ID = os.environ.get("IRP_TEST_TREATY_ANALYSIS_ID", "")

pytestmark = [pytest.mark.irp]


@pytest.mark.skipif(
    not _ANALYSIS_ID,
    reason="set IRP_TEST_TREATY_ANALYSIS_ID to a finished sandbox analysis run with treaties")
def test_list_analysis_treaties_names_each_applied_treaty():
    rows = irp_gateway._RealGateway().list_analysis_treaties(analysis_id=int(_ANALYSIS_ID))

    assert rows, "the analysis reports no treaties; pick one run with treaties that take loss"
    for row in rows:
        assert set(row) >= {"treaty_id", "treaty_number", "treaty_name", "treaty_type",
                            "attachment_point", "occurrence_limit", "risk_limit"}
        assert row["treaty_id"] and row["treaty_number"]
