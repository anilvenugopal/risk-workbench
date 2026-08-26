"""Sandbox round-trips for analysis-results retrieval (spec 011, T019/T025).

T019: one real submit → single-status poll → stats + EP for all five
perspectives against the FINISHED analysis. The run itself is what proves RM
serves WX and QS through the wheel (T-02), and that an unproduced perspective
returns an empty list rather than an error (T-08). Same tenant prerequisites
as ``test_analysis_execution_roundtrip.py``: set ``IRP_TEST_EDM_NAME`` /
``IRP_TEST_PORTFOLIO_NAME`` to a pre-imported sandbox EDM + portfolio.

T025: the broker pointer (T-03/O-02) — RM's own reported
``exposureResourceId`` on an RDM-imported analysis serves the same stats/EP
reads. Set ``IRP_TEST_RDM_NAME`` to a pre-imported sandbox RDM whose analyses
ran against a portfolio.

Run: ``make shell`` then ``uv run pytest tests/irp --run-irp``.
"""

from __future__ import annotations

import os
import time

import pytest

from app.services import irp_gateway
from app.workers.analysis_jobs import STORED_RETURN_PERIODS

_EDM_NAME = os.environ.get("IRP_TEST_EDM_NAME", "")
_PORTFOLIO_NAME = os.environ.get("IRP_TEST_PORTFOLIO_NAME", "")
_RDM_NAME = os.environ.get("IRP_TEST_RDM_NAME", "")

pytestmark = [pytest.mark.irp]

_PERSPECTIVES = ("GR", "RL", "WX", "QS", "GU")
_POLL_TIMEOUT_SECS = 300
_POLL_INTERVAL_SECS = 10


@pytest.mark.skipif(
    not (_EDM_NAME and _PORTFOLIO_NAME),
    reason="set IRP_TEST_EDM_NAME / IRP_TEST_PORTFOLIO_NAME to a pre-imported "
           "sandbox EDM + portfolio to run this round-trip")
def test_stats_and_ep_for_all_five_perspectives():
    gateway = irp_gateway._RealGateway()

    profile = gateway.list_model_profiles()[0]
    output = gateway.list_output_profiles()[0]
    job_name = f"RWB T019 Results {int(time.time())}"
    irp_id, request_body = gateway.submit_portfolio_analysis(
        edm_name=_EDM_NAME, portfolio_name=_PORTFOLIO_NAME, job_name=job_name,
        analysis_profile_name=profile.name, output_profile_name=output.name,
        event_rate_scheme_name=None, treaty_names=[], tag_names=[],
        currency={"code": "USD", "scheme": "RMS",
                 "vintage": "RL25", "asOfDate": "2025-05-28"},
        min_loss_threshold=1.0, num_max_loss_event=1,
        franchise_deductible=False, treat_construction_occupancy_as_unknown=True)
    assert irp_id

    deadline = time.monotonic() + _POLL_TIMEOUT_SECS
    status = gateway.get_analysis_job(irp_id)
    while status.status not in ("FINISHED", "FAILED", "CANCELLED"):
        assert time.monotonic() < deadline, (
            f"analysis {irp_id} did not reach a terminal status in "
            f"{_POLL_TIMEOUT_SECS}s (last: {status.status})")
        time.sleep(_POLL_INTERVAL_SECS)
        status = gateway.get_analysis_job(irp_id)  # single-status check only
    assert status.status == "FINISHED", (
        f"analysis {irp_id} finished with status {status.status}: {status.result}")

    hit = gateway.get_analysis_by_name(job_name, _EDM_NAME)
    meta = gateway.get_analysis_metadata(analysis_id=int(hit.analysis_id))
    assert meta.exposure_resource_id, "no exposure pointer on the metadata payload"
    analysis_id = int(hit.analysis_id)
    pointer = int(meta.exposure_resource_id)

    produced = 0
    for code in _PERSPECTIVES:
        # T-02: every code goes through the wheel's own perspective validation;
        # T-08: an unproduced perspective is an empty list, never an error.
        stats = gateway.get_analysis_stats(
            analysis_id=analysis_id, perspective_code=code,
            exposure_resource_id=pointer)
        ep = gateway.get_analysis_ep(
            analysis_id=analysis_id, perspective_code=code,
            exposure_resource_id=pointer)
        assert isinstance(stats, list) and isinstance(ep, list)
        if not stats and not ep:
            continue
        produced += 1
        oep = next((e for e in ep if e.get("epType") == "OEP"), None)
        assert oep is not None, f"{code}: EP response has no OEP element"
        periods = set(oep["value"]["returnPeriods"])
        missing = [rp for rp in STORED_RETURN_PERIODS if float(rp) not in periods]
        assert not missing, f"{code}: stored return periods absent: {missing}"
        assert any(r.get("epType") == "OEP" and r.get("purePremium") is not None
                   for r in stats), f"{code}: no OEP stats row with purePremium"

    # a plain-vanilla portfolio run must at least produce Gross
    assert produced >= 1


@pytest.mark.skipif(
    not _RDM_NAME,
    reason="set IRP_TEST_RDM_NAME to a pre-imported sandbox RDM whose analyses "
           "ran against a portfolio")
def test_broker_pointer_serves_stats_and_ep():
    """T025 (T-03/O-02): the ``exposureResourceId`` RM itself reports for an
    RDM-imported analysis — the value the backfill stores on the broker row —
    returns stats/EP rows through the same reads the retrieval worker makes."""
    gateway = irp_gateway._RealGateway()

    hits = gateway.search_analyses(source_rdm_name=_RDM_NAME)
    assert hits, f"RDM '{_RDM_NAME}' has no analyses in the sandbox"

    pointer = None
    analysis_id = None
    for hit in hits:
        meta = gateway.get_analysis_metadata(analysis_id=int(hit.analysis_id))
        if (meta.exposure_resource_id is not None
                and meta.exposure_resource_type == "PORTFOLIO"):
            analysis_id = int(hit.analysis_id)
            pointer = int(meta.exposure_resource_id)
            break
    assert pointer is not None, (
        f"no analysis of RDM '{_RDM_NAME}' carries a PORTFOLIO exposure pointer")

    stats = gateway.get_analysis_stats(
        analysis_id=analysis_id, perspective_code="GR",
        exposure_resource_id=pointer)
    ep = gateway.get_analysis_ep(
        analysis_id=analysis_id, perspective_code="GR",
        exposure_resource_id=pointer)
    assert stats, "broker pointer returned no GR stats rows"
    assert ep, "broker pointer returned no GR EP elements"
    oep = next((e for e in ep if e.get("epType") == "OEP"), None)
    assert oep is not None, "EP response has no OEP element"
    periods = set(oep["value"]["returnPeriods"])
    missing = [rp for rp in STORED_RETURN_PERIODS if float(rp) not in periods]
    assert not missing, f"stored return periods absent: {missing}"
