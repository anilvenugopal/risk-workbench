"""Sandbox checks of the two Risk Modeler reads the TY export relies on (spec
016 T-04, T-18): ``GET /platform/riskdata/v1/analyses/{id}/treaties`` through
``irp_gateway.list_analysis_treaties`` for a finished analysis run with
treaties, and ``GET /platform/riskdata/v1/analyses/{id}/stats`` at ``TY``
scoped to one treaty through ``irp_gateway.get_analysis_stats``. Set
``IRP_TEST_TREATY_ANALYSIS_ID`` to that analysis's Risk Modeler analysis ID; run
it once against a group analysis ID as well.

Run: ``make shell`` then ``uv run pytest tests/irp --run-irp -k treaty``.
"""

from __future__ import annotations

import os

import pytest

from app.services import irp_gateway

_ANALYSIS_ID = os.environ.get("IRP_TEST_TREATY_ANALYSIS_ID", "")

pytestmark = [
    pytest.mark.irp,
    pytest.mark.skipif(
        not _ANALYSIS_ID,
        reason="set IRP_TEST_TREATY_ANALYSIS_ID to a finished sandbox analysis run with treaties"),
]


def test_list_analysis_treaties_names_each_applied_treaty():
    rows = irp_gateway._RealGateway().list_analysis_treaties(analysis_id=int(_ANALYSIS_ID))

    assert rows, "the analysis reports no treaties; pick one run with treaties that take loss"
    for row in rows:
        assert set(row) >= {"treaty_id", "treaty_number", "treaty_name", "treaty_type",
                            "attachment_point", "occurrence_limit", "risk_limit"}
        assert row["treaty_id"] and row["treaty_number"]


def test_treaty_scoped_ty_stats_answer_per_treaty():
    """Each applied treaty answers a list; a treaty that took loss answers a
    populated one (T-18). At least one treaty of the fixture analysis must take
    loss: a layer attaching at zero always does (note 32 §5.4)."""
    gateway = irp_gateway._RealGateway()
    treaties = gateway.list_analysis_treaties(analysis_id=int(_ANALYSIS_ID))
    assert treaties, "the analysis reports no treaties; pick one run with treaties that take loss"

    answers = {
        row["treaty_number"]: gateway.get_analysis_stats(
            analysis_id=int(_ANALYSIS_ID), perspective_code="TY",
            exposure_resource_id=int(row["treaty_id"]),
            exposure_resource_type="TREATY")
        for row in treaties}

    assert all(isinstance(rows, list) for rows in answers.values()), answers
    assert any(answers.values()), (
        f"no treaty of analysis {_ANALYSIS_ID} answered TY stats; the cart would offer "
        f"nothing to tick: {answers}")
