"""Sandbox round-trips for analysis grouping (spec 012, T020/T022 — quickstart §4).

Each case is driven by an environment variable naming FINISHED sandbox
analyses by **Platform analysis id** (comma-separated) and skips — never
fails — when it is unset: no fixture id is safe to hardcode against someone
else's sandbox tenant.

- ``IRP_TEST_GROUP_ELT_IDS``: two or more pure-ELT analyses sharing one
  event-rate scheme. Inspect → submit with ``num_of_simulations=1`` → poll
  ``get_grouping_job`` single-status to terminal → stats/EP for the group's
  ``analysisId`` (the T-11 assumption is not validated until this passes).
  The same ids submitted with a fabricated fingerprint must be rejected with
  ``inspection_changed`` before any POST.
- ``IRP_TEST_GROUP_CONFLICTING_ELT_IDS``: ELT analyses whose event-rate
  schemes differ in exactly one partition. One group per offered scheme; each
  request body carries the chosen scheme (SC-002).

Propagate detailed output verification stops at the ``propagateDetailedLosses``
flag until spec O-02 defines what it retains.

Run: ``make shell`` then ``uv run pytest tests/irp --run-irp -k grouping``.
"""

from __future__ import annotations

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


def _member_ids(var: str) -> list[int]:
    ids = [int(v) for v in os.environ.get(var, "").split(",") if v.strip()]
    if len(ids) < 2:
        pytest.skip(f"set {var} (comma-separated Platform analysis ids of ≥2 "
                    f"FINISHED sandbox analyses) to run this case")
    return ids


def _poll_to_terminal(gateway, irp_id: str) -> str | None:
    deadline = time.time() + _POLL_TIMEOUT_SECS
    status = None
    while time.time() < deadline:
        status = gateway.get_grouping_job(irp_id).status  # single-status check
        if status in ("FINISHED", "FAILED", "CANCELLED"):
            break
        time.sleep(_POLL_INTERVAL_SECS)
    return status


def _submit(gateway, *, label: str, ids: list[int], inspection,
            selections: list[dict], num_of_simulations: int) -> tuple[str, str, dict]:
    group_name = f"RWB {label} {int(time.time())}"
    irp_id, request_body = gateway.submit_grouping(
        analysis_ids=ids, group_name=group_name, currency=_CURRENCY,
        propagate_detailed_losses=True, num_of_simulations=num_of_simulations,
        event_rate_selections=selections,
        expected_inspection_fingerprint=inspection.fingerprint)
    assert irp_id
    assert request_body["resourceUris"] == [
        f"/platform/riskdata/v1/analyses/{i}" for i in ids]
    assert request_body["settings"]["analysisName"] == group_name
    assert request_body["settings"]["propagateDetailedLosses"] is True
    return irp_id, group_name, request_body


def test_pure_elt_group_inspect_submit_poll_and_results_round_trip():
    gateway = irp_gateway._RealGateway()
    ids = _member_ids("IRP_TEST_GROUP_ELT_IDS")

    inspection = gateway.inspect_grouping(analysis_ids=ids)
    assert inspection.blocking_problems == ()
    assert inspection.output_loss_table == "ELT"
    assert not any(p.event_rate_selection_required for p in inspection.partitions)

    irp_id, group_name, body = _submit(
        gateway, label="T020 ELT", ids=ids, inspection=inspection,
        selections=[], num_of_simulations=1)
    assert body["settings"]["numOfSimulations"] == 1
    assert body["settings"]["simulateToPLT"] is False

    assert _poll_to_terminal(gateway, irp_id) == "FINISHED"

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


def test_stale_fingerprint_is_rejected_before_any_post():
    """US-2 acceptance 4: the package re-inspects at submit and refuses a
    fingerprint that no longer matches — no job id is returned."""
    gateway = irp_gateway._RealGateway()
    ids = _member_ids("IRP_TEST_GROUP_ELT_IDS")

    with pytest.raises(irp_gateway.IRPGroupingValidationError) as exc:
        gateway.submit_grouping(
            analysis_ids=ids, group_name=f"RWB stale {int(time.time())}",
            currency=_CURRENCY, propagate_detailed_losses=True,
            num_of_simulations=1, event_rate_selections=[],
            expected_inspection_fingerprint="v1:" + "0" * 64)

    assert [str(p.code) for p in exc.value.problems] == ["inspection_changed"]


def test_conflicting_schemes_group_once_per_offered_scheme():
    """US-2 acceptance 1 / SC-002: the analyst's scheme choice is the only
    input a conflict needs; every offered scheme yields a finished group."""
    gateway = irp_gateway._RealGateway()
    ids = _member_ids("IRP_TEST_GROUP_CONFLICTING_ELT_IDS")

    inspection = gateway.inspect_grouping(analysis_ids=ids)
    assert inspection.blocking_problems == ()
    conflicting = [p for p in inspection.partitions if p.event_rate_selection_required]
    assert len(conflicting) == 1
    partition = conflicting[0]
    assert len(partition.event_rate_scheme_options) >= 2

    submitted = []
    for option in partition.event_rate_scheme_options:
        scheme_id = option.event_rate_scheme_id
        selection = {"peril_code": partition.key.peril_code,
                     "region_code": partition.key.region_code,
                     "model_version": partition.key.model_version,
                     "event_rate_scheme_id": scheme_id}
        irp_id, _, body = _submit(
            gateway, label=f"T022 scheme {scheme_id}", ids=ids,
            inspection=inspection, selections=[selection], num_of_simulations=1)
        entries = body["settings"]["regionPerilSimulationSet"]
        assert entries
        assert {e["eventRateSchemeId"] for e in entries} == {scheme_id}
        assert all(e["simulationSetId"] == 0 and e["simulationPeriods"] == 0
                   for e in entries)
        submitted.append(irp_id)

    for irp_id in submitted:
        assert _poll_to_terminal(gateway, irp_id) == "FINISHED"
