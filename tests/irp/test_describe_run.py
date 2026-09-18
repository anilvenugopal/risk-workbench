"""Sandbox checks for the describe-run read (spec 015, T010 — quickstart §3).

Re-checks live what `contracts/captures/` claims for three analyses: own DLM
5741781 collapses its 23 state region rows to one NA · WS partition on scheme
739; own HD 5733173 reads PET 12 "RMS 2020 Time-Dependent Rates" with 1,978,459
periods and no scheme; broker DLM 5689560 carries two treaties with their names.

Each id is overridable by environment variable, so the case runs against
another tenant's analyses without editing the file; an id that is not this
tenant's skips rather than fails.

Run: ``make shell`` then ``uv run pytest tests/irp -k describe --run-irp``.
"""

from __future__ import annotations

import os

import pytest

from app.services import irp_gateway

pytestmark = pytest.mark.irp


def _analysis_id(var: str, default: int) -> int:
    return int(os.environ.get(var) or default)


def _describe(analysis_id: int) -> irp_gateway.ResolvedRun:
    try:
        return irp_gateway._RealGateway().describe_analysis_run(
            analysis_id=analysis_id)
    except Exception as exc:  # noqa: BLE001 — another tenant's id, not a failure
        if "404" in str(exc) or "not found" in str(exc).lower():
            pytest.skip(f"analysis {analysis_id} is not in this tenant")
        raise


def test_own_dlm_names_its_event_rate_scheme():
    run = _describe(_analysis_id("IRP_TEST_DESCRIBE_OWN_DLM_ID", 5741781))

    [partition] = run.partitions
    assert (partition.region_code, partition.peril_code) == ("NA", "WS")
    assert partition.framework == "ELT"
    assert partition.event_rate_scheme_id == 739
    assert partition.event_rate_scheme_name == "RMS 2025 Stochastic Event Rates"


def test_own_hd_names_its_pet_and_periods():
    run = _describe(_analysis_id("IRP_TEST_DESCRIBE_OWN_HD_ID", 5733173))

    [partition] = run.partitions
    assert partition.framework == "PLT"
    assert partition.simulation_set_id == 12
    assert partition.simulation_set_name == "RMS 2020 Time-Dependent Rates"
    assert partition.periods == 1_978_459
    assert partition.event_rate_scheme_id is None


def test_broker_dlm_carries_its_applied_treaties_with_names():
    run = _describe(_analysis_id("IRP_TEST_DESCRIBE_BROKER_DLM_ID", 5689560))

    assert run.partitions[0].event_rate_scheme_id == 577
    assert [t.number for t in run.treaties] == ["XPR_1_100_Fld", "XPR_1_95_Fld"]
    assert all(t.name and t.currency for t in run.treaties)
