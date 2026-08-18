"""Sandbox contract checks for analysis reference-data reads."""

from __future__ import annotations

import pytest

from app.services import irp_gateway

pytestmark = pytest.mark.irp


def test_reference_data_reads_return_the_cached_field_shapes():
    gateway = irp_gateway._RealGateway()

    model_profiles = gateway.list_model_profiles()
    output_profiles = gateway.list_output_profiles()
    event_rate_schemes = gateway.list_event_rate_schemes()
    currencies = gateway.list_currencies()

    assert model_profiles
    assert output_profiles
    assert event_rate_schemes
    assert currencies
    assert all(row.irp_id and row.name and row.software_version_code is not None
               for row in model_profiles)
    assert all(row.irp_id and row.name for row in output_profiles)
    assert all(row.irp_id and row.name for row in event_rate_schemes)
    assert all(row.code and row.name for row in currencies)
