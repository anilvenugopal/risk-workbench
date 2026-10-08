"""Tests for the ``RISK_MODELER_X509_STRICT`` setting on the client session."""

import ssl

import pytest

from irp_integration.client import Client


@pytest.fixture(autouse=True)
def api_key_env(monkeypatch):
    monkeypatch.setenv('RISK_MODELER_BASE_URL', 'https://rm.example.invalid')
    monkeypatch.setenv('RISK_MODELER_RESOURCE_GROUP_ID', 'test-resource-group')
    monkeypatch.setenv('RISK_MODELER_API_KEY', 'test-key')


def _pool_kw(client):
    return client.session.get_adapter('https://rm.example.invalid').poolmanager.connection_pool_kw


def test_x509_strict_false_clears_the_strict_flag_and_keeps_verification(monkeypatch):
    monkeypatch.setenv('RISK_MODELER_X509_STRICT', 'false')

    context = _pool_kw(Client())['ssl_context']

    assert not context.verify_flags & ssl.VERIFY_X509_STRICT
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_unset_x509_strict_keeps_urllib3s_default_context(monkeypatch):
    monkeypatch.delenv('RISK_MODELER_X509_STRICT', raising=False)

    assert 'ssl_context' not in _pool_kw(Client())
