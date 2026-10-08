"""
Tests for ``get_or_create_exposure_set``.

``submit_create_edm_job``, ``submit_edm_import_job`` and
``submit_rdm_import_job`` all resolve an exposure set through this one method,
so a change to what it returns moves three request bodies at once. Risk Modeler
permits duplicate exposure set names; the first match is used, and the test
below pins that rather than leaving it to whichever call site is read first.
"""

from types import SimpleNamespace

import pytest

from irp_integration.edm import EDMManager
from irp_integration.exceptions import IRPValidationError

from conftest import FakeClient, FakeResponse

# Invented name. Nothing here may name a real exposure set or tenant: this file
# ships in the sdist, so a name used here is published to PyPI.
EXPOSURE_SET_NAME = "example exposure set"


def make_manager(responses):
    """Build an EDMManager over a FakeClient with the given queued responses."""
    client = FakeClient(responses)
    return EDMManager(SimpleNamespace(client=client)), client


def test_an_existing_exposure_set_is_reused_without_a_post():
    manager, client = make_manager([
        FakeResponse(200, json_body=[{'exposureSetId': 7}]),
    ])

    assert manager.get_or_create_exposure_set(EXPOSURE_SET_NAME) == 7
    assert [call['method'] for call in client.calls] == ['GET']
    assert client.calls[0]['params'] == {
        'filter': f'exposureSetName="{EXPOSURE_SET_NAME}"'
    }, "the name is quoted, so a name containing a space still matches"


def test_duplicate_names_take_the_first_match():
    manager, client = make_manager([
        FakeResponse(200, json_body=[{'exposureSetId': 7}, {'exposureSetId': 8}]),
    ])

    assert manager.get_or_create_exposure_set(EXPOSURE_SET_NAME) == 7


def test_a_missing_exposure_set_is_created():
    manager, client = make_manager([
        FakeResponse(200, json_body=[]),
        FakeResponse(201, headers={'location': '/platform/riskdata/v1/exposuresets/9'}),
    ])

    assert manager.get_or_create_exposure_set(EXPOSURE_SET_NAME) == 9
    assert [call['method'] for call in client.calls] == ['GET', 'POST']
    assert client.calls[1]['json'] == {'exposureSetName': EXPOSURE_SET_NAME}


def test_an_empty_name_raises_before_the_search():
    manager, client = make_manager([])

    with pytest.raises(IRPValidationError):
        manager.get_or_create_exposure_set("")

    assert client.calls == []
