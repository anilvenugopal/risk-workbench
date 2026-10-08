"""
Tests for the errors ``Client.request()`` raises.

The ``client`` fixture runs in API-key mode so construction makes no network
call. The ``bearer_client`` fixture skips the login made during construction.
The session's ``request`` and ``post`` are replaced so every test runs offline.
"""

import pytest
import requests

from irp_integration.client import Client
from irp_integration.exceptions import IRPAPIError, IRPAuthenticationError


@pytest.fixture
def client(monkeypatch):
    """Return an API-key ``Client`` built from test environment variables."""
    monkeypatch.setenv('RISK_MODELER_BASE_URL', 'https://rm.example.invalid')
    monkeypatch.setenv('RISK_MODELER_RESOURCE_GROUP_ID', 'test-resource-group')
    monkeypatch.setenv('RISK_MODELER_API_KEY', 'test-key')
    return Client()


@pytest.fixture
def bearer_client(monkeypatch):
    """Return a bearer-mode ``Client`` whose construction makes no login request."""
    monkeypatch.setenv('RISK_MODELER_BASE_URL', 'https://rm.example.invalid')
    monkeypatch.setenv('RISK_MODELER_RESOURCE_GROUP_ID', 'test-resource-group')
    monkeypatch.delenv('RISK_MODELER_API_KEY', raising=False)
    monkeypatch.setenv('RISK_MODELER_TENANT_NAME', 'test-tenant')
    monkeypatch.setenv('RISK_MODELER_USERNAME', 'test-user')
    monkeypatch.setenv('RISK_MODELER_PASSWORD', 'test-password')
    with monkeypatch.context() as m:
        m.setattr(Client, '_login', lambda self: None)
        return Client()


def make_response(status_code, body=b'{}'):
    """Build a ``requests.Response`` with the given status and body."""
    response = requests.Response()
    response.status_code = status_code
    response._content = body
    return response


def test_http_error_carries_the_response_status(client, monkeypatch):
    response = requests.Response()
    response.status_code = 400
    response._content = b'{"message": "Invalid analysis name"}'
    monkeypatch.setattr(client.session, 'request', lambda **kwargs: response)

    with pytest.raises(IRPAPIError) as excinfo:
        client.request('POST', '/platform/riskdata/v1/analyses')

    assert excinfo.value.status_code == 400


def test_connection_error_has_no_status(client, monkeypatch):
    def refuse(**kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(client.session, 'request', refuse)

    with pytest.raises(IRPAPIError) as excinfo:
        client.request('GET', '/platform/riskdata/v1/analyses')

    assert excinfo.value.status_code is None


def test_a_401_that_survives_re_login_carries_the_status(bearer_client, monkeypatch):
    monkeypatch.setattr(bearer_client, '_login', lambda: None)
    monkeypatch.setattr(bearer_client.session, 'request', lambda **kwargs: make_response(401))

    with pytest.raises(IRPAuthenticationError) as excinfo:
        bearer_client.request('GET', '/platform/riskdata/v1/analyses')

    assert excinfo.value.status_code == 401


def test_a_rejected_re_login_carries_the_login_status(bearer_client, monkeypatch):
    monkeypatch.setattr(bearer_client.session, 'request', lambda **kwargs: make_response(401))
    monkeypatch.setattr(
        bearer_client.session, 'post',
        lambda *args, **kwargs: make_response(403, b'{"message": "Forbidden"}'),
    )

    with pytest.raises(IRPAuthenticationError) as excinfo:
        bearer_client.request('GET', '/platform/riskdata/v1/analyses')

    assert excinfo.value.status_code == 403
