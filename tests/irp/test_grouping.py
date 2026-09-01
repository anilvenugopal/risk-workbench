"""Sandbox round-trips for analysis grouping (spec 012, T020/T022 — quickstart §4).

Three cases, each driven by its own pair of environment variables naming
FINISHED sandbox analyses (comma-separated names) and their EDMs
(comma-separated, positionally aligned; leave an entry empty for a name-only
member). Each case skips — never fails — when its variables are unset: no
fixture name is safe to hardcode against someone else's sandbox tenant.

quickstart §4 lists the variable pairs; each skip message names its own.

T020 submits a grouping, polls ``get_grouping_job`` single-status to a terminal
state, and asserts the stats/EP getters serve the group's ``analysisId`` — the
T-11 assumption (group results retrieval reuses the analysis read path) is not
a validated claim until it passes. The two T022 cases stop at FINISHED: they
prove SC-002, that a mixed-scheme DLM pair and a DLM + HD pair each group with
zero manual pre-steps and no scheme parameter anywhere in the submitted
payload. Propagate detailed output verification stops at the
``propagateDetailedLosses`` payload flag until spec O-02 defines what it
retains.

Run: ``make shell`` then ``uv run pytest tests/irp --run-irp -k grouping``.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from app.services import irp_gateway

pytestmark = pytest.mark.irp

_POLL_TIMEOUT_SECS = 600
_POLL_INTERVAL_SECS = 15

_PERSPECTIVE = "GR"

_CURRENCY = {"code": "USD", "scheme": "RMS", "vintage": "RL25",
             "asOfDate": "2025-05-28"}


def _member_set(names_var: str, edms_var: str) -> tuple[list[str], dict[str, str]]:
    names = [n.strip() for n in os.environ.get(names_var, "").split(",")
             if n.strip()]
    edms = [e.strip() for e in os.environ.get(edms_var, "").split(",")]
    if len(names) < 2:
        pytest.skip(f"set {names_var} (comma-separated, ≥2 FINISHED sandbox "
                    f"analyses) and {edms_var} to run this case")
    edm_map = {name: edm
               for name, edm in zip(names, edms, strict=False) if edm}
    return names, edm_map


def _group_to_terminal(gateway, *, label: str, names: list[str],
                       edm_map: dict[str, str]) -> tuple[str, str]:
    """Submit one grouping and poll it to a terminal state.

    Returns the group name and the terminal status. Asserts SC-002 on the
    submitted payload: no event-rate-scheme parameter — the wheel auto-builds
    the region/peril simulation set.
    """
    group_name = f"RWB {label} {int(time.time())}"
    irp_id, request_body = gateway.submit_analysis_grouping(
        group_name=group_name, analysis_names=names, analysis_edm_map=edm_map,
        group_names=set(), currency=_CURRENCY,
        propagate_detailed_losses=True)
    assert irp_id
    assert "eventratescheme" not in json.dumps(request_body).lower()
    assert request_body["settings"]["propagateDetailedLosses"] is True

    deadline = time.time() + _POLL_TIMEOUT_SECS
    status = None
    while time.time() < deadline:
        status = gateway.get_grouping_job(irp_id).status  # single-status check
        if status in ("FINISHED", "FAILED", "CANCELLED"):
            break
        time.sleep(_POLL_INTERVAL_SECS)
    return group_name, status


def test_grouping_submit_poll_and_results_round_trip():
    gateway = irp_gateway._RealGateway()
    names, edm_map = _member_set("IRP_TEST_GROUP_MEMBER_NAMES",
                                 "IRP_TEST_GROUP_MEMBER_EDMS")
    group_name, status = _group_to_terminal(
        gateway, label="T020 Group", names=names, edm_map=edm_map)
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


def test_mixed_event_rate_scheme_dlm_members_group_and_finish():
    """US-2 acceptance 1 / SC-002 — two DLM analyses run under different
    event-rate schemes group with no scheme choice and no pre-step."""
    gateway = irp_gateway._RealGateway()
    names, edm_map = _member_set("IRP_TEST_GROUP_MIXED_SCHEME_NAMES",
                                 "IRP_TEST_GROUP_MIXED_SCHEME_EDMS")
    _, status = _group_to_terminal(
        gateway, label="T022 Mixed", names=names, edm_map=edm_map)
    assert status == "FINISHED"


def test_dlm_and_hd_members_group_and_finish():
    """US-2 acceptance 2 / SC-002 — a DLM + HD pair groups with no pre-step."""
    gateway = irp_gateway._RealGateway()
    names, edm_map = _member_set("IRP_TEST_GROUP_DLM_HD_NAMES",
                                 "IRP_TEST_GROUP_DLM_HD_EDMS")
    _, status = _group_to_terminal(
        gateway, label="T022 DlmHd", names=names, edm_map=edm_map)
    assert status == "FINISHED"
