"""Sandbox round-trip for analysis grouping (spec 012, T020/T022 — quickstart §4).

Submits a real grouping of two finished sandbox analyses, polls
``get_grouping_job`` single-status to a terminal state, and asserts the
stats/EP getters serve the group's ``analysisId`` — the T-11 assumption
(group results retrieval reuses the analysis read path) is not a validated
claim until this passes.

Needs two FINISHED sandbox analyses named in ``IRP_TEST_GROUP_MEMBER_NAMES``
(comma-separated) with their EDM in ``IRP_TEST_GROUP_MEMBER_EDMS``
(comma-separated, aligned; leave an entry empty for a name-only member).
Skips (not fails) when unset — no fixture name is safe to hardcode against
someone else's sandbox tenant. Mixed-scheme / DLM+HD member pairs (T022,
US-2) are exercised by pointing these variables at such a pair; no scheme
parameter exists anywhere in this test to tune (SC-002).

Run: ``make shell`` then ``uv run pytest tests/irp --run-irp -k grouping``.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from app.services import irp_gateway

_MEMBER_NAMES = [n.strip() for n in
                 os.environ.get("IRP_TEST_GROUP_MEMBER_NAMES", "").split(",")
                 if n.strip()]
_MEMBER_EDMS = [e.strip() for e in
                os.environ.get("IRP_TEST_GROUP_MEMBER_EDMS", "").split(",")]

pytestmark = [
    pytest.mark.irp,
    pytest.mark.skipif(
        len(_MEMBER_NAMES) < 2,
        reason="set IRP_TEST_GROUP_MEMBER_NAMES (comma-separated, ≥2 FINISHED "
               "sandbox analyses) and IRP_TEST_GROUP_MEMBER_EDMS to run the "
               "grouping round-trip"),
]

_POLL_TIMEOUT_SECS = 600
_POLL_INTERVAL_SECS = 15

_PERSPECTIVE = "GR"


def test_grouping_submit_poll_and_results_round_trip():
    gateway = irp_gateway._RealGateway()
    edm_map = {name: edm
               for name, edm in zip(_MEMBER_NAMES, _MEMBER_EDMS, strict=False)
               if edm}
    group_name = f"RWB T020 Group {int(time.time())}"

    irp_id, request_body = gateway.submit_analysis_grouping(
        group_name=group_name, analysis_names=list(_MEMBER_NAMES),
        analysis_edm_map=edm_map, group_names=set(),
        currency={"code": "USD", "scheme": "RMS",
                  "vintage": "RL25", "asOfDate": "2025-05-28"},
        propagate_detailed_losses=True)
    assert irp_id
    # SC-002: the submitted payload carries no event-rate-scheme choice —
    # the wheel auto-builds the region/peril simulation set.
    assert "eventratescheme" not in json.dumps(request_body).lower()
    assert request_body["settings"]["propagateDetailedLosses"] is True

    deadline = time.time() + _POLL_TIMEOUT_SECS
    status = None
    while time.time() < deadline:
        status = gateway.get_grouping_job(irp_id).status  # single-status check
        if status in ("FINISHED", "FAILED", "CANCELLED"):
            break
        time.sleep(_POLL_INTERVAL_SECS)
    assert status == "FINISHED"

    hit = gateway.get_analysis_by_name_only(group_name)
    meta = gateway.get_analysis_metadata(analysis_id=int(hit.analysis_id))
    assert meta.payload
    pointer = meta.exposure_resource_id
    assert pointer is not None

    stats = gateway.get_analysis_stats(
        analysis_id=int(hit.analysis_id), perspective_code=_PERSPECTIVE,
        exposure_resource_id=int(pointer))
    ep = gateway.get_analysis_ep(
        analysis_id=int(hit.analysis_id), perspective_code=_PERSPECTIVE,
        exposure_resource_id=int(pointer))
    assert stats  # T-11: the analysis stats read serves a group id
    assert ep
