"""Sandbox round-trip for analysis execution (spec 010, T024).

One real submit → single-status poll → completion backfill, against the active
wheel (``make irp-status`` — signatures are pre-release and move). Needs a
pre-existing sandbox EDM with at least one portfolio already imported — this
tier assumes a populated tenant, not a bootstrap; set ``IRP_TEST_EDM_NAME`` /
``IRP_TEST_PORTFOLIO_NAME`` to point at it. Skips (not fails) when unset, since
no fixture name is safe to hardcode against someone else's sandbox tenant.

Run: ``make shell`` then ``uv run pytest tests/irp --run-irp``.
"""

from __future__ import annotations

import os
import time

import pytest

from app.services import irp_gateway

_EDM_NAME = os.environ.get("IRP_TEST_EDM_NAME", "")
_PORTFOLIO_NAME = os.environ.get("IRP_TEST_PORTFOLIO_NAME", "")

pytestmark = [
    pytest.mark.irp,
    pytest.mark.skipif(
        not (_EDM_NAME and _PORTFOLIO_NAME),
        reason="set IRP_TEST_EDM_NAME / IRP_TEST_PORTFOLIO_NAME to a pre-imported "
               "sandbox EDM + portfolio to run this round-trip"),
]

# Poll for at most this long before giving up — a single-status check per pass,
# never poll_*_to_completion (Article 11); this loop lives in the TEST, not the
# app, and stands in for the poller's own cadence during a manual sandbox run.
_POLL_TIMEOUT_SECS = 300
_POLL_INTERVAL_SECS = 10


def test_submit_poll_backfill_round_trip():
    gateway = irp_gateway._RealGateway()

    model_profiles = gateway.list_model_profiles()
    output_profiles = gateway.list_output_profiles()
    assert model_profiles and output_profiles
    profile = model_profiles[0]
    output = output_profiles[0]

    job_name = f"RWB T024 Roundtrip {int(time.time())}"
    irp_id, request_body = gateway.submit_portfolio_analysis(
        edm_name=_EDM_NAME, portfolio_name=_PORTFOLIO_NAME, job_name=job_name,
        analysis_profile_name=profile.name, output_profile_name=output.name,
        event_rate_scheme_name=None, treaty_names=[], tag_names=[],
        currency={"code": "USD", "scheme": "RMS",
                 "vintage": "RL25", "asOfDate": "2025-05-28"},
        min_loss_threshold=1.0, num_max_loss_event=1,
        franchise_deductible=False, treat_construction_occupancy_as_unknown=True)
    assert irp_id
    assert request_body.get("resourceUri")  # unrecoverable later — must be present

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

    analysis_id = None
    for task in (status.result or {}).get("tasks", []):
        value = ((task.get("output") or {}).get("log") or {}).get("analysisId")
        if value is not None and str(value).strip():
            analysis_id = str(value).strip()
            break
    assert analysis_id, (
        f"FINISHED job body carried no tasks[].output.log.analysisId — "
        f"shape drift? raw body: {status.result}")
    meta = gateway.get_analysis_metadata(analysis_id=int(analysis_id))
    assert meta.payload
    assert meta.payload.get("appAnalysisId"), (
        f"analysis-details payload carried no appAnalysisId: {meta.payload}")
