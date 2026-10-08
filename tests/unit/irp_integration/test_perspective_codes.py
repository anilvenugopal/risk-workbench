"""
Tests for perspective-code validation on the analysis result reads.

``_validate_perspective_code`` runs client-side, before any HTTP request, in
``get_elt``, ``get_ep``, ``get_stats`` and ``get_plt``. A code missing from
``PERSPECTIVE_CODES`` is therefore unreachable no matter what Risk Modeler would
have answered, which is why the assertions below are on the recorded request
rather than only on the absence of an exception: passing validation and reaching
the endpoint as ``perspectiveCode`` are two different things.
"""

from types import SimpleNamespace

import pytest

from irp_integration.analysis import AnalysisManager
from irp_integration.constants import PERSPECTIVE_CODES
from irp_integration.exceptions import IRPValidationError

from conftest import FakeClient, FakeResponse

ANALYSIS_ID = 42
EXPOSURE_RESOURCE_ID = 7

# The two codes issue #28 was raised for. GR, GU and RL were already accepted
# before the constant widened; asserting them here pins that they still are.
REQUIRED_CODES = ['GR', 'GU', 'RL', 'WX', 'QS']

RESULT_GETTERS = ['get_elt', 'get_ep', 'get_stats', 'get_plt']


def make_analysis_manager(responses=None):
    """Return (AnalysisManager, FakeClient) with no network and no credentials."""
    client = FakeClient(responses)
    return AnalysisManager(SimpleNamespace(client=client)), client


@pytest.mark.parametrize("perspective_code", PERSPECTIVE_CODES)
def test_every_code_in_the_constant_passes_validation(perspective_code):
    """Accept every code in the Risk Modeler perspective vocabulary."""
    manager, _ = make_analysis_manager()

    assert manager._validate_perspective_code(perspective_code) is None


@pytest.mark.parametrize("perspective_code", REQUIRED_CODES)
@pytest.mark.parametrize("getter_name", RESULT_GETTERS)
def test_result_getters_send_the_perspective_code(getter_name, perspective_code):
    """Send the code to the endpoint as ``perspectiveCode`` rather than rejecting it."""
    manager, client = make_analysis_manager([FakeResponse(200, json_body=[])])

    getattr(manager, getter_name)(ANALYSIS_ID, perspective_code, EXPOSURE_RESOURCE_ID)

    assert client.calls[0]['params']['perspectiveCode'] == perspective_code, (
        f"{getter_name} must reach the platform with {perspective_code}; "
        "before issue #28 the client refused to send it"
    )


@pytest.mark.parametrize("perspective_code", ['ZZ', '', 'gr'])
@pytest.mark.parametrize("getter_name", RESULT_GETTERS)
def test_unknown_codes_fail_before_the_request(getter_name, perspective_code):
    """Raise on a code outside the vocabulary without issuing a request."""
    manager, client = make_analysis_manager()

    with pytest.raises(IRPValidationError) as raised:
        getattr(manager, getter_name)(ANALYSIS_ID, perspective_code, EXPOSURE_RESOURCE_ID)

    assert f"Invalid perspective_code '{perspective_code}'" in str(raised.value)
    assert "see PERSPECTIVE_CODES in irp_integration/constants.py" in str(raised.value), (
        "the message has to point at the valid codes, not just report a rejection"
    )
    assert client.calls == [], "validation runs before the request, so nothing is sent"


def test_constant_holds_the_full_vocabulary():
    """Pin the vocabulary itself: 64 distinct codes, GR/GU/RL/WX/QS among them."""
    assert len(PERSPECTIVE_CODES) == len(set(PERSPECTIVE_CODES)), "no duplicate codes"
    assert len(PERSPECTIVE_CODES) == 64
    for perspective_code in REQUIRED_CODES:
        assert perspective_code in PERSPECTIVE_CODES
