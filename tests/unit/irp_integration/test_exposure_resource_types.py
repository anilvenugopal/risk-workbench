"""
Tests for the ``exposure_resource_type`` keyword on the analysis result reads.

``get_elt``, ``get_ep``, ``get_stats`` and ``get_plt`` send
``exposureResourceType=PORTFOLIO`` unless told otherwise; ``TREATY`` with a
treaty id from ``search_analysis_treaties`` scopes the result to that treaty.
``_validate_exposure_resource_type`` runs before any HTTP request, so the
assertions are on the recorded request, as in ``test_perspective_codes.py``.
"""

from types import SimpleNamespace

import pytest

from irp_integration.analysis import AnalysisManager
from irp_integration.constants import EXPOSURE_RESOURCE_TYPES
from irp_integration.exceptions import IRPValidationError

from conftest import FakeClient, FakeResponse

ANALYSIS_ID = 42
EXPOSURE_RESOURCE_ID = 7
PERSPECTIVE_CODE = 'TY'

RESULT_GETTERS = ['get_elt', 'get_ep', 'get_stats', 'get_plt']


def make_analysis_manager(responses=None):
    """Return (AnalysisManager, FakeClient) with no network and no credentials."""
    client = FakeClient(responses)
    return AnalysisManager(SimpleNamespace(client=client)), client


@pytest.mark.parametrize("getter_name", RESULT_GETTERS)
def test_result_getters_default_to_portfolio(getter_name):
    """Send ``exposureResourceType=PORTFOLIO`` when the keyword is omitted."""
    manager, client = make_analysis_manager([FakeResponse(200, json_body=[])])

    getattr(manager, getter_name)(ANALYSIS_ID, PERSPECTIVE_CODE, EXPOSURE_RESOURCE_ID)

    assert client.calls[0]['params']['exposureResourceType'] == 'PORTFOLIO'
    assert client.calls[0]['params']['exposureResourceId'] == EXPOSURE_RESOURCE_ID


@pytest.mark.parametrize("getter_name", RESULT_GETTERS)
def test_result_getters_send_treaty_when_asked(getter_name):
    """Send ``exposureResourceType=TREATY`` with the treaty id as the resource id."""
    manager, client = make_analysis_manager([FakeResponse(200, json_body=[])])

    getattr(manager, getter_name)(
        ANALYSIS_ID, PERSPECTIVE_CODE, EXPOSURE_RESOURCE_ID,
        exposure_resource_type='TREATY',
    )

    assert client.calls[0]['params']['exposureResourceType'] == 'TREATY'
    assert client.calls[0]['params']['exposureResourceId'] == EXPOSURE_RESOURCE_ID


@pytest.mark.parametrize("exposure_resource_type", ['treaty', 'ACCOUNT', ''])
@pytest.mark.parametrize("getter_name", RESULT_GETTERS)
def test_unknown_types_fail_before_the_request(getter_name, exposure_resource_type):
    """Raise on a type outside the vocabulary without issuing a request."""
    manager, client = make_analysis_manager()

    with pytest.raises(IRPValidationError) as raised:
        getattr(manager, getter_name)(
            ANALYSIS_ID, PERSPECTIVE_CODE, EXPOSURE_RESOURCE_ID,
            exposure_resource_type=exposure_resource_type,
        )

    assert f"Invalid exposure_resource_type '{exposure_resource_type}'" in str(raised.value)
    assert "see EXPOSURE_RESOURCE_TYPES in irp_integration/constants.py" in str(raised.value)
    assert client.calls == [], "validation runs before the request, so nothing is sent"


def test_constant_holds_the_two_types():
    assert EXPOSURE_RESOURCE_TYPES == ['PORTFOLIO', 'TREATY']
