"""Sandbox check for the import-by-id lookup (issue #101).

The only unproven piece of the import: the ``appAnalysisId=`` search filter is
documented for the 0.8.0 wheel but had not been observed answering. Set
``IRP_TEST_APP_ANALYSIS_ID`` to the id the Risk Modeler UI shows on any
FINISHED sandbox analysis; the case skips — never fails — when it is unset.

Run: ``make shell`` then ``uv run pytest tests/irp --run-irp -k app_analysis_id``.
"""

from __future__ import annotations

import os

import pytest

from app.services import irp_gateway
from app.services.analysis_service import _to_display

pytestmark = pytest.mark.irp

_APP_ANALYSIS_ID = os.environ.get("IRP_TEST_APP_ANALYSIS_ID", "")


@pytest.mark.skipif(
    not _APP_ANALYSIS_ID.isdigit(),
    reason="set IRP_TEST_APP_ANALYSIS_ID to the id Risk Modeler shows on a "
           "sandbox analysis to run this check")
def test_app_analysis_id_resolves_and_round_trips_through_the_metadata():
    gateway = irp_gateway._RealGateway()

    analysis_id = gateway.resolve_app_analysis_id(
        app_analysis_id=int(_APP_ANALYSIS_ID))
    meta = gateway.get_analysis_metadata(analysis_id=int(analysis_id))

    assert analysis_id.isdigit()
    assert str(meta.payload.get("appAnalysisId")) == _APP_ANALYSIS_ID
    assert meta.payload.get("analysisName")
    # The import writes this into submitted_settings, and a row with no
    # currency there is unpairable in Compare. The only other evidence that
    # Risk Modeler reports it is a payload captured on 2026-08-26.
    assert _to_display(meta.payload).currency


@pytest.mark.skipif(
    not _APP_ANALYSIS_ID.isdigit(),
    reason="set IRP_TEST_APP_ANALYSIS_ID to run this check")
def test_unknown_app_analysis_id_is_a_lookup_error():
    with pytest.raises(LookupError):
        irp_gateway._RealGateway().resolve_app_analysis_id(
            app_analysis_id=999999999)
