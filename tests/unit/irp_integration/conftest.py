"""
Test doubles shared across the irp_integration test suite.

Every test runs offline. Nothing constructs a real ``Client``, so no environment
variables, no credentials and no network access are involved. ``FakeClient``
records each ``request()`` call, which is what lets the write tests assert on the
request body, method and path rather than only on the return value.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from irp_integration.analysis import AnalysisManager
from irp_integration.portfolio import PortfolioManager
from irp_integration.reference_data import ReferenceDataManager
from irp_integration.treaty import TreatyManager


class FakeResponse:
    """Stand-in for ``requests.Response`` carrying only what the managers read."""

    def __init__(self, status_code=200, json_body=None, headers=None, has_body=True):
        self.status_code = status_code
        self.headers = headers or {}
        self._json_body = json_body
        self._has_body = has_body

    def json(self) -> Any:
        """Return the configured body, or raise the way requests does for a body-less response."""
        if not self._has_body:
            raise ValueError("No JSON object could be decoded")
        return self._json_body


class FakeClient:
    """
    Stand-in for ``Client`` that returns queued responses and records calls.

    A queued entry that is an exception is raised instead of returned. Running
    out of queued responses is an assertion failure rather than a silent
    ``None``: a manager sending more requests than the test expected is worth
    failing on.
    """

    def __init__(self, responses: Optional[List[Any]] = None) -> None:
        self.responses = list(responses or [])
        self.calls: List[Dict[str, Any]] = []

    def request(self, method, path, *, params=None, json=None, **kwargs) -> FakeResponse:
        """Record the call and return (or raise) the next queued response."""
        self.calls.append({'method': method, 'path': path, 'params': params, 'json': json})
        if not self.responses:
            raise AssertionError(
                f"FakeClient received an unexpected {method} {path}; "
                f"no response was queued for it"
            )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeEDMManager:
    """Stand-in for ``EDMManager`` covering only the EDM-name-to-exposureId lookup."""

    def __init__(self, edms: Optional[List[Dict[str, Any]]] = None) -> None:
        self.edms = [] if edms is None else edms
        self.filters: List[str] = []

    def search_edms(self, filter: str = "", **kwargs: Any) -> List[Dict[str, Any]]:
        """Record the filter and return the configured EDMs."""
        self.filters.append(filter)
        return self.edms


@pytest.fixture
def make_portfolio_manager():
    """Return a factory building (PortfolioManager, FakeClient, FakeEDMManager)."""
    def build(responses=None, edms=None):
        client = FakeClient(responses)
        edm_manager = FakeEDMManager(edms)
        irp = SimpleNamespace(client=client, edm=edm_manager)
        return PortfolioManager(irp), client, edm_manager

    return build


@pytest.fixture
def make_reference_data_manager():
    """Return a factory building (ReferenceDataManager, FakeClient)."""
    def build(responses=None):
        client = FakeClient(responses)
        irp = SimpleNamespace(client=client)
        return ReferenceDataManager(irp), client

    return build


@pytest.fixture
def make_analysis_manager():
    """
    Return a factory building (AnalysisManager, FakeClient, FakeEDMManager).

    ``AnalysisManager`` reads its sibling managers off the owning client, so the
    real ``ReferenceDataManager``, ``TreatyManager`` and ``PortfolioManager`` are
    built over the same ``FakeClient``. One queue of responses then covers every
    request the submit path makes, in call order.
    """
    def build(responses=None, edms=None):
        client = FakeClient(responses)
        edm_manager = FakeEDMManager(edms)
        irp = SimpleNamespace(client=client, edm=edm_manager)
        irp.reference_data = ReferenceDataManager(irp)
        irp.treaty = TreatyManager(irp)
        irp.portfolio = PortfolioManager(irp)
        return AnalysisManager(irp), client, edm_manager

    return build


@pytest.fixture
def response():
    """Return the FakeResponse class for building queued responses."""
    return FakeResponse
